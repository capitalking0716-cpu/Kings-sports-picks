"""The Odds API client (the-odds-api.com).

What the free tier actually gives (checked 2026-11-10 against the
docs and live reports — do not assume more):

- 500 requests a month, no credit card. Resets on the 1st.
- GET /v4/sports and GET /v4/sports/{sport}/events are FREE
  (no quota used). Use them to find event IDs first.
- Player props live ONLY on the per-event endpoint:
  GET /v4/sports/{sport}/events/{eventId}/odds
  The sport-wide /odds endpoint returns 422 for prop markets.
- Cost of a priced call = (number of markets) x (number of
  regions). We ask for regions=us only, and only the prop
  markets we need, to make the 500 last.
- Historical odds are a paid add-on — the free tier cannot
  fetch them. We never try.

Because props cost quota per event, results are CACHED on disk
(data/cache/) and only refreshed when Neko taps Refresh.
If the API blocks or fails, the error is returned plainly —
never hidden, never faked.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

BASE_URL = "https://api.the-odds-api.com/v4"
ENV_KEY = "LILLY_ODDS_API_KEY"

SPORT_KEYS = {
    "NBA": "basketball_nba",
    "NFL": "americanfootball_nfl",
    # NHL later — same pattern, sport key "icehockey_nhl".
}

# Prop markets per sport (The Odds API market keys).
PROP_MARKETS = {
    "NBA": [
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
        "player_points_rebounds_assists",
    ],
    "NFL": [
        "player_pass_yds",
        "player_pass_tds",
        "player_rush_yds",
        "player_receiving_yds",
        "player_receptions",
        "player_anytime_td",
    ],
}

MARKET_LABELS = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "Three-Pointers Made",
    "player_points_rebounds_assists": "Points + Rebounds + Assists",
    "player_pass_yds": "Passing Yards",
    "player_pass_tds": "Passing Touchdowns",
    "player_rush_yds": "Rushing Yards",
    "player_receiving_yds": "Receiving Yards",
    "player_receptions": "Receptions",
    "player_anytime_td": "Anytime Touchdown",
}

CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours; refresh on demand

# Prop label -> Odds API market key (the reverse of
# MARKET_LABELS; labels are unique, so this is lossless).
LABEL_TO_MARKET = {label: key for key, label in MARKET_LABELS.items()}


def market_key_for_label(label: str) -> str | None:
    """The Odds API market key for a prop label, or None."""
    return LABEL_TO_MARKET.get(label)


def alternate_market_key(market_key: str) -> str:
    """The alternate-lines market for a standard market key:
    the standard key + "_alternate" (e.g. player_points ->
    player_points_alternate). Alternate markets carry the
    SAME prop at a RANGE of lines — the dropped (safer) and
    raised lines Neko's betting style plays."""
    return f"{market_key}_alternate"


@dataclass
class ApiResult:
    ok: bool
    message: str
    props: list = None
    requests_remaining: str = ""
    from_cache: bool = False

    def __post_init__(self):
        if self.props is None:
            self.props = []


@dataclass
class AltPrice:
    """One book's price for one side of one ALTERNATE line.

    Same honesty rules as Prop: this is a real price a real
    book showed in the API response, never an estimate.
    """

    player: str
    sport: str
    prop: str  # the STANDARD market's label, e.g. "Points"
    market_key: str  # the standard market key, e.g. "player_points"
    side: str  # "Over" or "Under"
    line: float
    odds: int  # American odds
    bookmaker: str = ""
    event: str = ""  # e.g. "Lakers vs Celtics"
    commence_time: str = ""


@dataclass
class AltResult:
    ok: bool
    message: str
    prices: list = None
    requests_remaining: str = ""
    from_cache: bool = False

    def __post_init__(self):
        if self.prices is None:
            self.prices = []


def get_api_key() -> str:
    """Key from env var, or Streamlit secrets if the app set them there."""
    key = os.environ.get(ENV_KEY, "").strip()
    if key:
        return key
    try:
        import streamlit as st

        return str(st.secrets.get(ENV_KEY, "")).strip()
    except Exception:
        return ""


def cache_path(cache_dir: Path, sport: str) -> Path:
    return cache_dir / f"{sport.lower()}_props.json"


def _load_cache(path: Path) -> ApiResult | None:
    try:
        data = json.loads(path.read_text())
        age = time.time() - data.get("saved_at", 0)
        if age > CACHE_TTL_SECONDS:
            return None
        from kings_picks.model import Prop

        props = [Prop(**p) for p in data.get("props", [])]
        return ApiResult(
            ok=True,
            message=f"Loaded {len(props)} props from cache (saved {int(age // 60)} min ago).",
            props=props,
            from_cache=True,
        )
    except Exception:
        return None


def _save_cache(path: Path, props: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": time.time(),
        "props": [p.__dict__ for p in props],
    }
    path.write_text(json.dumps(payload))


def _parse_event_odds(
    event: dict, sport: str, bookmakers: tuple[str, ...]
) -> list:
    """Turn one event's odds JSON into Prop rows.

    Player name is in the outcome's `description`, not `name`.
    We keep the BEST price (highest payout) for each side across
    the bookmakers asked for, and record which book offered it.
    """
    from kings_picks.model import Prop

    home = event.get("home_team", "")
    away = event.get("away_team", "")
    event_label = f"{away} vs {home}" if away and home else ""
    commence = str(event.get("commence_time", ""))

    # (player, market, side, line) -> best (odds, bookmaker)
    best: dict[tuple, tuple[int, str]] = {}
    for book in event.get("bookmakers", []):
        if bookmakers and book.get("key") not in bookmakers:
            continue
        for market in book.get("markets", []):
            mkey = market.get("key", "")
            label = MARKET_LABELS.get(mkey, mkey)
            for outcome in market.get("outcomes", []):
                player = (outcome.get("description") or "").strip()
                side = (outcome.get("name") or "").strip()
                point = outcome.get("point")
                price = outcome.get("price")
                if not player or side not in ("Over", "Under"):
                    continue
                if point is None or price is None:
                    continue
                # Anytime TD has no line — treat the line as 0.5 (score at least 1).
                line = 0.5 if mkey == "player_anytime_td" else float(point)
                k = (player, label, side, line)
                odds = int(price)
                if k not in best or odds > best[k][0]:
                    best[k] = (odds, book.get("title", book.get("key", "")))

    props = []
    for (player, label, side, line), (odds, book) in best.items():
        props.append(
            Prop(
                player=player,
                sport=sport,
                prop=label,
                side=side,
                line=line,
                odds=odds,
                bookmaker=book,
                event=event_label,
                commence_time=commence,
                source="live",
                event_id=str(event.get("id", "") or ""),
            )
        )
    return props


def _parse_event_alternates(
    event: dict,
    sport: str,
    market_key: str,
    bookmakers: tuple[str, ...],
) -> list:
    """Turn one event's odds JSON into AltPrice rows for ONE
    alternate market (the standard key + "_alternate").

    Same parsing rules as _parse_event_odds: the player is
    the outcome's `description`, and we keep the BEST price
    (highest payout) per (player, side, line) across the
    bookmakers asked for. Books that don't offer the market
    simply contribute nothing — no row is ever invented for
    a line no book priced.
    """
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    event_label = f"{away} vs {home}" if away and home else ""
    commence = str(event.get("commence_time", ""))
    label = MARKET_LABELS.get(market_key, market_key)
    alt_key = alternate_market_key(market_key)

    # (player, side, line) -> best (odds, bookmaker)
    best: dict[tuple, tuple[int, str]] = {}
    for book in event.get("bookmakers", []):
        if bookmakers and book.get("key") not in bookmakers:
            continue
        for market in book.get("markets", []):
            if market.get("key", "") != alt_key:
                continue
            for outcome in market.get("outcomes", []):
                player = (outcome.get("description") or "").strip()
                side = (outcome.get("name") or "").strip()
                point = outcome.get("point")
                price = outcome.get("price")
                if not player or side not in ("Over", "Under"):
                    continue
                if point is None or price is None:
                    continue
                k = (player, side, float(point))
                odds = int(price)
                if k not in best or odds > best[k][0]:
                    best[k] = (odds, book.get("title", book.get("key", "")))

    return [
        AltPrice(
            player=player,
            sport=sport,
            prop=label,
            market_key=market_key,
            side=side,
            line=line,
            odds=odds,
            bookmaker=book,
            event=event_label,
            commence_time=commence,
        )
        for (player, side, line), (odds, book) in best.items()
    ]


class OddsApiClient:
    """Thin client. One instance per run; key is never logged."""

    def __init__(
        self,
        api_key: str = "",
        cache_dir: Path | str = "data/cache",
        bookmakers: tuple[str, ...] = ("draftkings", "fanduel"),
        session: requests.Session | None = None,
    ):
        self.api_key = api_key or get_api_key()
        self.cache_dir = Path(cache_dir)
        self.bookmakers = bookmakers
        self.session = session or requests.Session()

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, params: dict) -> requests.Response:
        params = {**params, "apiKey": self.api_key}
        return self.session.get(f"{BASE_URL}{path}", params=params, timeout=30)

    def quota_from(self, resp: requests.Response) -> str:
        used = resp.headers.get("x-requests-used", "?")
        remaining = resp.headers.get("x-requests-remaining", "?")
        return f"{remaining} of 500 requests left this month ({used} used)"

    def list_events(self, sport: str) -> ApiResult:
        """Free endpoint: upcoming events for a sport (no quota)."""
        if not self.has_key:
            return ApiResult(False, "No API key set. Add LILLY_ODDS_API_KEY in Setup.")
        try:
            resp = self._get(f"/sports/{SPORT_KEYS[sport]}/events", {})
        except requests.RequestException as exc:
            return ApiResult(False, f"Could not reach The Odds API: {exc}")
        if resp.status_code == 401:
            return ApiResult(False, "The Odds API said the key is not valid (401). Check the key in Setup.")
        if resp.status_code != 200:
            return ApiResult(False, f"The Odds API answered {resp.status_code}: {resp.text[:200]}")
        return ApiResult(True, f"Found {len(resp.json())} upcoming {sport} events.",
                         props=[], requests_remaining=self.quota_from(resp))

    def fetch_props(
        self, sport: str, force_refresh: bool = False, max_events: int = 12
    ) -> ApiResult:
        """Player props for a sport. Cached unless force_refresh.

        Costs quota: one priced call per event, markets x regions.
        We keep it to regions=us and only that sport's prop markets.
        """
        cache = cache_path(self.cache_dir, sport)
        if not force_refresh:
            hit = _load_cache(cache)
            if hit is not None:
                return hit
        if not self.has_key:
            return ApiResult(
                False,
                "No API key set. Add your Odds API key in "
                "Streamlit Secrets to pull live props.",
            )
        try:
            events_resp = self._get(f"/sports/{SPORT_KEYS[sport]}/events", {})
        except requests.RequestException as exc:
            return ApiResult(False, f"Could not reach The Odds API: {exc}")
        if events_resp.status_code == 401:
            return ApiResult(False, "The Odds API said the key is not valid (401). Check the key in Setup.")
        if events_resp.status_code != 200:
            return ApiResult(False, f"The Odds API answered {events_resp.status_code}: {events_resp.text[:200]}")

        events = events_resp.json()
        if not events:
            return ApiResult(True, f"No upcoming {sport} games right now.", props=[])

        props: list = []
        errors: list[str] = []
        remaining = ""
        for event in events[:max_events]:
            markets = ",".join(PROP_MARKETS[sport])
            try:
                resp = self._get(
                    f"/sports/{SPORT_KEYS[sport]}/events/{event['id']}/odds",
                    {
                        "regions": "us",
                        "markets": markets,
                        "oddsFormat": "american",
                        "bookmakers": ",".join(self.bookmakers),
                    },
                )
            except requests.RequestException as exc:
                errors.append(f"{event.get('id')}: {exc}")
                continue
            remaining = self.quota_from(resp)
            if resp.status_code == 422:
                errors.append(
                    f"{event.get('id')}: props not offered for this game (422)."
                )
                continue
            if resp.status_code == 401:
                return ApiResult(False, "The Odds API said the key is not valid (401). Check the key in Setup.")
            if resp.status_code == 429:
                return ApiResult(False, "The Odds API rate limit was hit (429). Wait a bit and try again.",
                                 props=props, requests_remaining=remaining)
            if resp.status_code != 200:
                errors.append(f"{event.get('id')}: HTTP {resp.status_code}")
                continue
            props.extend(_parse_event_odds(resp.json(), sport, self.bookmakers))

        if props:
            _save_cache(cache, props)
        msg = f"Fetched {len(props)} live {sport} props from {min(len(events), max_events)} games."
        if errors:
            msg += f" {len(errors)} game(s) had no props: " + "; ".join(errors[:3])
        return ApiResult(True, msg, props=props, requests_remaining=remaining)

    # ---------- alternate lines (dropped / raised), on demand ----------

    def alt_cache_path(self, sport: str, event_id: str, market_key: str) -> Path:
        """One cache file per (sport, event, market): an
        expansion fetches exactly one event's one alternate
        market, and is cached under exactly that key."""
        return self.cache_dir / f"alt_{sport.lower()}_{event_id}_{market_key}.json"

    def _load_alt_cache(self, path: Path) -> AltResult | None:
        try:
            data = json.loads(path.read_text())
            age = time.time() - data.get("saved_at", 0)
            if age > CACHE_TTL_SECONDS:
                return None
            prices = [AltPrice(**p) for p in data.get("prices", [])]
            return AltResult(
                ok=True,
                message=f"Loaded {len(prices)} alternate prices from cache (saved {int(age // 60)} min ago).",
                prices=prices,
                from_cache=True,
            )
        except Exception:
            return None

    def _save_alt_cache(self, path: Path, prices: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            "prices": [p.__dict__ for p in prices],
        }
        path.write_text(json.dumps(payload))

    def resolve_event_id(
        self, sport: str, event_label: str, commence_time: str = ""
    ) -> str | None:
        """Find an event's Odds API id from its label
        ("Away vs Home") via the FREE events endpoint — no
        quota. For board props loaded from an older cache
        that predates Prop.event_id. Matches the label, and
        the commence time too when both sides know it.
        Returns None when nothing matches — the caller then
        honestly can't fetch that event's alternates.
        """
        if not self.has_key:
            return None
        try:
            resp = self._get(f"/sports/{SPORT_KEYS[sport]}/events", {})
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        for event in resp.json():
            away = event.get("away_team", "")
            home = event.get("home_team", "")
            label = f"{away} vs {home}" if away and home else ""
            if label != event_label:
                continue
            if commence_time and str(event.get("commence_time", "")) != commence_time:
                continue
            return str(event.get("id", "") or "") or None
        return None

    def fetch_alternates(
        self,
        sport: str,
        event_id: str,
        market_key: str,
        force_refresh: bool = False,
    ) -> AltResult:
        """Alternate lines for ONE market of ONE event — the
        dropped / raised lines behind the My Slip expander.

        QUOTA: this is called ONLY when the user taps to
        expand dropped lines for a specific pick. It asks for
        exactly one alternate market in one region, so one
        priced call costs 1 market x 1 region = 1 request.
        The result is disk-cached under the (sport, event,
        market) key with the same 6-hour TTL as the slate, so
        re-opening the same expansion is free.
        """
        cache = self.alt_cache_path(sport, event_id, market_key)
        if not force_refresh:
            hit = self._load_alt_cache(cache)
            if hit is not None:
                return hit
        if not self.has_key:
            return AltResult(
                False,
                "No API key set — dropped lines come from the books' real "
                "alternate markets, so they need the live key in Setup.",
            )
        if not event_id:
            return AltResult(False, "That game couldn't be matched to the odds feed, so its alternate lines can't be fetched.")
        try:
            resp = self._get(
                f"/sports/{SPORT_KEYS[sport]}/events/{event_id}/odds",
                {
                    "regions": "us",
                    "markets": alternate_market_key(market_key),
                    "oddsFormat": "american",
                    "bookmakers": ",".join(self.bookmakers),
                },
            )
        except requests.RequestException as exc:
            return AltResult(False, f"Could not reach The Odds API: {exc}")
        remaining = self.quota_from(resp)
        if resp.status_code == 401:
            return AltResult(False, "The Odds API said the key is not valid (401). Check the key in Setup.")
        if resp.status_code == 429:
            return AltResult(False, "The Odds API rate limit was hit (429). Wait a bit and try again.",
                             requests_remaining=remaining)
        if resp.status_code == 422:
            # The event doesn't offer this alternate market at
            # all — an honest empty, cached like any result so
            # re-taps don't re-ask.
            result = AltResult(
                True,
                "No book offers alternate lines for this market in this game.",
                prices=[],
                requests_remaining=remaining,
            )
            self._save_alt_cache(cache, [])
            return result
        if resp.status_code != 200:
            return AltResult(False, f"The Odds API answered {resp.status_code}: {resp.text[:200]}",
                             requests_remaining=remaining)
        prices = _parse_event_alternates(resp.json(), sport, market_key, self.bookmakers)
        self._save_alt_cache(cache, prices)
        return AltResult(
            True,
            f"Fetched {len(prices)} alternate prices for this game.",
            prices=prices,
            requests_remaining=remaining,
        )
