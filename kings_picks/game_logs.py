"""Real recent-form game logs from FREE feeds.

Built 2026-11-12, after probing what actually works:

- NFL — nflverse. The old `player_stats` GitHub release stops
  at 2024 CSVs; the living feed is the `stats_player` release,
  file `stats_player_week_{season}.csv` (one row per player per
  game). Probed from this machine 2026-11-12: HTTP 200, ~4.2 MB
  for 2026, downloads in seconds. Updated weekly by nflverse,
  so a 7-day disk cache is plenty.
- NBA — stats.nba.com via the nba_api package. Probed
  2026-11-12: ScoreboardV2 OK, PlayerGameLog OK 4/4,
  LeagueGameLog failed once with a proxy error then succeeded
  on retry (3,843 rows, 2026-27). Reachable — but the bulk call
  is flaky, so it gets ONE retry, a 1-day disk cache, and any
  failure simply means NBA picks stay odds-only that day.
  Honest caveat: reachability was proven from Shadow's machine;
  the deployed app runs on Streamlit Cloud, whose network is
  different. If the feed fails there, NBA form silently stays
  off — the app never breaks and never fakes a number.

POSITIONS (browsing only — the scoring path never reads them):
- NFL: the same nflverse CSV's own `position` column, taken
  from each player's most recent regular-season row.
- NBA: LeagueGameLog rows carry no position, so positions come
  from the nba_api PlayerIndex endpoint (probed 2026-11-12:
  526 rows, every player has a POSITION code — 'G', 'F', 'C'
  and combos like 'G-F'). The codes are expanded to the words
  they stand for (G -> Guard, ...): a translation of the feed's
  own label, never an assignment. Any failure leaves NBA
  positions empty and the Players page simply shows one list.
A position is NEVER guessed from a player's stats.

FAIL-SOFT IS THE RULE: every fetch/parse error returns None.
No feed = odds-only labels, exactly like before this module
existed. These feeds NEVER touch The Odds API (quota!).

Form is only ever attached to LIVE props matched by name to a
real feed row. Demo props never get form data.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests

NFLVERSE_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/"
    "stats_player/stats_player_week_{season}.csv"
)
NFL_SOURCE = "nflverse (free, updated weekly)"
NBA_SOURCE = "stats.nba.com (free, via nba_api)"

NFL_TTL_SECONDS = 7 * 24 * 60 * 60  # nflverse updates weekly
NBA_TTL_SECONDS = 24 * 60 * 60  # NBA plays most days

# Our prop label -> nflverse column(s). Values are summed.
NFL_STAT_COLUMNS = {
    "Passing Yards": ("passing_yards",),
    "Passing Touchdowns": ("passing_tds",),
    "Rushing Yards": ("rushing_yards",),
    "Receiving Yards": ("receiving_yards",),
    "Receptions": ("receptions",),
}
NFL_ANYTIME_TD = "Anytime Touchdown"  # derived: rushing_tds + receiving_tds >= 1

# Our prop label -> stats.nba.com column(s). Values are summed.
NBA_STAT_COLUMNS = {
    "Points": ("PTS",),
    "Rebounds": ("REB",),
    "Assists": ("AST",),
    "Three-Pointers Made": ("FG3M",),
    "Points + Rebounds + Assists": ("PTS", "REB", "AST"),
}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """Match the same player across feeds: 'Michael Penix Jr.',
    'Luther Burden III' and 'Amon-Ra St. Brown' must land on the
    same key in every feed. Lowercase, drop suffixes and
    punctuation, squeeze spaces."""
    text = (name or "").lower().replace("-", " ").replace(".", " ").replace("'", "")
    parts = [p for p in text.split() if p]
    while parts and parts[-1] in _SUFFIXES:
        parts.pop()
    return " ".join(parts)


def nfl_season_for(today: date) -> int:
    """The NFL season that owns `today` (seasons run Sep-Feb)."""
    return today.year if today.month >= 3 else today.year - 1


def nba_season_for(today: date) -> str:
    """The NBA season label stats.nba.com wants, e.g. '2026-27'."""
    if today.month >= 10:
        return f"{today.year}-{str(today.year + 1)[-2:]}"
    return f"{today.year - 1}-{str(today.year)[-2:]}"


def _num(cell) -> float:
    """CSV/API cell -> float. Blank or junk counts as 0 for a
    counting stat (the player was in the game; he just did none
    of that)."""
    try:
        if cell is None or cell == "":
            return 0.0
        return float(cell)
    except (TypeError, ValueError):
        return 0.0


def summarize(values_newest_first: list, side: str, line: float, n: int = 10):
    """FormSummary for one player + prop, or None if no games.

    Hits are counted for the PICK's side of the line — the same
    counting form_data.fetch_recent_form has always used:
    Over hits when value > line, Under when value < line. A
    value exactly on the line is a push — it counts for neither
    side, which is the honest read for half-point lines anyway.
    """
    if not values_newest_first:
        return None
    last = list(values_newest_first[:n])
    if side == "Over":
        hits = sum(1 for v in last if v > line)
    else:
        hits = sum(1 for v in last if v < line)
    last5 = list(values_newest_first[:5])
    return FormSummary(
        values=last,
        hits=hits,
        games=len(last),
        last5_avg=sum(last5) / len(last5),
        season_avg=sum(values_newest_first) / len(values_newest_first),
    )


@dataclass
class FormSummary:
    values: list  # last N game values, newest first
    hits: int  # of `games`, how many went the pick's side of the line
    games: int
    last5_avg: float
    season_avg: float
    source_label: str = ""


@dataclass
class GameLogs:
    """One feed, parsed: normalized player name -> prop label ->
    game values, newest game first. `positions` maps the same
    normalized names to the position label the feed stated for
    the player (empty when the feed didn't provide one) — used
    only by the Players page, never by scoring."""

    sport: str
    by_player: dict = field(default_factory=dict)
    source_label: str = ""
    season_label: str = ""
    positions: dict = field(default_factory=dict)

    def position_of(self, player: str):
        """The player's position as the feed stated it, or None
        when unknown. Never guessed."""
        return self.positions.get(normalize_name(player)) or None

    def values(self, player: str, prop_label: str):
        """The player's game values for this prop, newest first,
        or None if the feed has no such player/prop."""
        player_logs = self.by_player.get(normalize_name(player))
        if not player_logs:
            return None
        vals = player_logs.get(prop_label)
        return list(vals) if vals else None

    def form(self, player: str, prop_label: str, side: str, line: float, n: int = 10):
        vals = self.values(player, prop_label)
        if not vals:
            return None
        summary = summarize(vals, side, line, n=n)
        if summary is not None:
            summary.source_label = self.source_label
        return summary


def parse_nflverse_csv(text: str) -> dict:
    """nflverse weekly CSV text -> {normalized name: {prop label:
    [values newest first]}}. Regular season rows only."""
    reader = csv.DictReader(io.StringIO(text))
    per_player: dict[str, dict[str, list]] = {}
    for row in reader:
        season_type = (row.get("season_type") or "REG").strip()
        if season_type != "REG":
            continue
        name = (row.get("player_display_name") or "").strip()
        if not name:
            continue
        try:
            week = int(float(row.get("week") or 0))
        except (TypeError, ValueError):
            week = 0
        stats = per_player.setdefault(normalize_name(name), {})
        for label, columns in NFL_STAT_COLUMNS.items():
            value = sum(_num(row.get(col)) for col in columns)
            stats.setdefault(label, []).append((week, value))
        anytime = 1.0 if (_num(row.get("rushing_tds")) + _num(row.get("receiving_tds"))) >= 1 else 0.0
        stats.setdefault(NFL_ANYTIME_TD, []).append((week, anytime))
    return {
        player: {
            label: [v for _week, v in sorted(games, key=lambda t: t[0], reverse=True)]
            for label, games in stats.items()
        }
        for player, stats in per_player.items()
    }


def parse_nflverse_positions(text: str) -> dict:
    """nflverse weekly CSV text -> {normalized name: position}.

    The position is the feed's own `position` column from the
    player's MOST RECENT regular-season row that states one —
    positions can drift week to week in the source data, and a
    blank cell never erases a position the feed did state.
    Nothing is inferred from stats: a player whose rows never
    name a position simply isn't in the result.
    """
    reader = csv.DictReader(io.StringIO(text))
    latest: dict[str, tuple[int, str]] = {}
    for row in reader:
        season_type = (row.get("season_type") or "REG").strip()
        if season_type != "REG":
            continue
        name = (row.get("player_display_name") or "").strip()
        position = (row.get("position") or "").strip()
        if not name or not position:
            continue
        try:
            week = int(float(row.get("week") or 0))
        except (TypeError, ValueError):
            week = 0
        key = normalize_name(name)
        if key not in latest or week >= latest[key][0]:
            latest[key] = (week, position)
    return {key: position for key, (_week, position) in latest.items()}


def parse_nba_game_log_rows(rows: list) -> dict:
    """LeagueGameLog records (list of dicts) -> same shape as the
    nflverse parser. Rows with no stat line (did-not-play) are
    skipped: a DNP is not a game the player went under in."""
    per_player: dict[str, dict[str, list]] = {}
    for row in rows:
        name = (row.get("PLAYER_NAME") or "").strip()
        game_date = str(row.get("GAME_DATE") or "")
        if not name or not game_date:
            continue
        if row.get("PTS") is None:
            continue  # did not play — no stat line at all
        stats = per_player.setdefault(normalize_name(name), {})
        for label, columns in NBA_STAT_COLUMNS.items():
            value = sum(_num(row.get(col)) for col in columns)
            stats.setdefault(label, []).append((game_date, value))
    return {
        player: {
            label: [v for _d, v in sorted(games, key=lambda t: t[0], reverse=True)]
            for label, games in stats.items()
        }
        for player, stats in per_player.items()
    }


# stats.nba.com's PlayerIndex sends position CODES, not words.
NBA_POSITION_WORDS = {"G": "Guard", "F": "Forward", "C": "Center"}


def expand_nba_position(code) -> str:
    """'G' -> 'Guard', 'G-F' -> 'Guard-Forward', 'F-C' ->
    'Forward-Center'. Unknown code parts pass through as-is —
    the label always comes from the feed, this only spells it
    out. Blank/junk -> '' (position unknown)."""
    if code is None:
        return ""
    text = str(code).strip()
    if not text or text.lower() in ("nan", "none"):
        return ""
    parts = []
    for part in text.split("-"):
        part = part.strip()
        if part:
            parts.append(NBA_POSITION_WORDS.get(part, part))
    return "-".join(parts)


def parse_nba_player_index_rows(rows: list) -> dict:
    """PlayerIndex records (list of dicts) -> {normalized name:
    position label}. Rows with no name or no position are
    skipped — an unstated position stays unknown, never filled
    in from anything else."""
    positions: dict[str, str] = {}
    for row in rows:
        first = (row.get("PLAYER_FIRST_NAME") or "").strip()
        last = (row.get("PLAYER_LAST_NAME") or "").strip()
        label = expand_nba_position(row.get("POSITION"))
        if not label or (not first and not last):
            continue
        name = " ".join(part for part in (first, last) if part)
        positions[normalize_name(name)] = label
    return positions


class StatsStore:
    """Disk-cached access to both feeds. Every failure returns
    None — the app then runs odds-only, exactly as before."""

    def __init__(
        self,
        cache_dir,
        session: requests.Session | None = None,
        today: date | None = None,
    ):
        self.cache_dir = Path(cache_dir)
        self.session = session or requests.Session()
        self.today = today or date.today()
        self._nfl: GameLogs | None = None
        self._nba: GameLogs | None = None
        self._nfl_tried = False
        self._nba_tried = False

    # ---------- cache plumbing ----------

    def _read_cache(self, name: str, ttl: float):
        path = self.cache_dir / name
        try:
            data = json.loads(path.read_text())
            if time.time() - data.get("fetched_at", 0) > ttl:
                return None
            return data
        except Exception:
            return None

    def _write_cache(self, name: str, payload: dict) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / name).write_text(json.dumps(payload))
        except Exception:
            pass  # a failed cache write must never break the app

    # ---------- NFL (nflverse) ----------

    # The cache filename carries a version: v1 files hold logs
    # only (no positions). Reading only v2 names means a stale
    # pre-positions cache is ignored outright instead of silently
    # serving a week of position-less data under the 7-day TTL.
    @staticmethod
    def _nfl_cache_name(season: int) -> str:
        return f"nflverse_{season}_v2.json"

    def _fetch_nflverse(self, season: int):
        """(logs, positions) parsed from one CSV download, or
        None on any failure."""
        try:
            resp = self.session.get(
                NFLVERSE_URL.format(season=season), timeout=60
            )
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        try:
            return (
                parse_nflverse_csv(resp.text),
                parse_nflverse_positions(resp.text),
            )
        except Exception:
            return None

    def nfl_logs(self) -> GameLogs | None:
        if self._nfl_tried:
            return self._nfl
        self._nfl_tried = True
        season = nfl_season_for(self.today)
        cached = self._read_cache(self._nfl_cache_name(season), NFL_TTL_SECONDS)
        if cached is None and season - 1 >= 2020:
            # Offseason / new-season gap: last season is the cache
            # that might exist; freshness still enforced by TTL.
            cached = self._read_cache(
                self._nfl_cache_name(season - 1), NFL_TTL_SECONDS
            )
        if cached is not None:
            self._nfl = GameLogs(
                sport="NFL",
                by_player=cached.get("logs", {}),
                source_label=NFL_SOURCE,
                season_label=str(cached.get("season", "")),
                positions=cached.get("positions", {}) or {},
            )
            return self._nfl
        for candidate in (season, season - 1):
            fetched = self._fetch_nflverse(candidate)
            if fetched and fetched[0]:
                logs, positions = fetched
                self._write_cache(
                    self._nfl_cache_name(candidate),
                    {
                        "fetched_at": time.time(),
                        "season": candidate,
                        "logs": logs,
                        "positions": positions,
                    },
                )
                self._nfl = GameLogs(
                    sport="NFL",
                    by_player=logs,
                    source_label=NFL_SOURCE,
                    season_label=str(candidate),
                    positions=positions,
                )
                return self._nfl
        return None

    # ---------- NBA (stats.nba.com via nba_api) ----------

    def _fetch_nba(self, season: str) -> dict | None:
        try:
            from nba_api.stats.endpoints import LeagueGameLog
        except Exception:
            return None
        for _attempt in range(2):  # the bulk call is flaky — one retry
            try:
                result = LeagueGameLog(
                    season=season,
                    player_or_team_abbreviation="P",
                    timeout=60,
                )
                rows = result.get_data_frames()[0].to_dict("records")
                logs = parse_nba_game_log_rows(rows)
                if logs:
                    return logs
            except Exception:
                continue
        return None

    def nba_logs(self) -> GameLogs | None:
        if self._nba_tried:
            return self._nba
        self._nba_tried = True
        season = nba_season_for(self.today)
        cached = self._read_cache("nba_gamelogs.json", NBA_TTL_SECONDS)
        if cached is not None:
            self._nba = GameLogs(
                sport="NBA",
                by_player=cached.get("logs", {}),
                source_label=NBA_SOURCE,
                season_label=str(cached.get("season", "")),
                positions=self._nba_positions(season),
            )
            return self._nba
        logs = self._fetch_nba(season)
        if logs:
            self._write_cache(
                "nba_gamelogs.json",
                {"fetched_at": time.time(), "season": season, "logs": logs},
            )
            self._nba = GameLogs(
                sport="NBA",
                by_player=logs,
                source_label=NBA_SOURCE,
                season_label=season,
                positions=self._nba_positions(season),
            )
            return self._nba
        return None

    # ---------- NBA positions (PlayerIndex) ----------
    # LeagueGameLog rows state no position, so positions come
    # from a second endpoint with its OWN cache and lifecycle.
    # Positions failing (or the endpoint being unreachable) only
    # empties the mapping — the game logs above are unaffected.

    def _fetch_nba_positions(self, season: str) -> dict | None:
        try:
            from nba_api.stats.endpoints import PlayerIndex
        except Exception:
            return None
        for _attempt in range(2):  # same flaky feed — one retry
            try:
                result = PlayerIndex(season=season, timeout=60)
                rows = result.get_data_frames()[0].to_dict("records")
                positions = parse_nba_player_index_rows(rows)
                if positions:
                    return positions
            except Exception:
                continue
        return None

    def _nba_positions(self, season: str) -> dict:
        """{normalized name: position label} for the NBA, disk-
        cached daily; {} whenever the feed won't provide them."""
        cached = self._read_cache("nba_positions.json", NBA_TTL_SECONDS)
        if cached is not None:
            return cached.get("positions", {}) or {}
        positions = self._fetch_nba_positions(season)
        if positions:
            self._write_cache(
                "nba_positions.json",
                {
                    "fetched_at": time.time(),
                    "season": season,
                    "positions": positions,
                },
            )
            return positions
        return {}


def make_form_lookup(nfl: GameLogs | None, nba: GameLogs | None):
    """Build the callable score_all consumes:
    lookup(prop) -> (hits, games) | None.

    Demo props NEVER get form. Unmatched players stay odds-only.
    The hits are counted for the prop's own side of its line.
    """

    def lookup(prop) -> tuple[int, int] | None:
        if getattr(prop, "source", "live") == "demo":
            return None
        logs = nfl if prop.sport == "NFL" else nba if prop.sport == "NBA" else None
        if logs is None:
            return None
        summary = logs.form(prop.player, prop.prop, prop.side, prop.line)
        if summary is None or summary.games == 0:
            return None
        return summary.hits, summary.games

    return lookup
