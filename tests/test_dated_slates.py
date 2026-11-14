"""Tests for pick-the-date, pre-built picks, and test-my-picks
(2026-11-14).

Neko's order: "I also want that ability to pick the dates and
pre build pick and test my picks as well."

Covered here:
- per-date slate storage (data/slates/<date>.json): two dates
  keep two slates, bad date strings never become filenames,
  demo payloads are purged;
- the game-date helpers: a payload's day (recorded, derived
  from its games, or from the pull time), plain date names;
- date filtering in the Odds API client: a dated pull prices
  ONLY that Detroit day's games, caches per date, and a day
  with no games says so in plain English;
- test-my-picks maths: a typed line (priced / projection-only /
  not offered), and the slip's TRUE combined chance;
- the plain-English download for a chosen date: the day is
  named, a future day is labelled Pre-built, and his own
  tested picks ride in it with the dropped lines spelled out;
- the app flow: pull tomorrow's games from the Dashboard date
  picker, the slate is stored under tomorrow (today's mirror
  untouched), tickets say PRE-BUILT, and switching dates back
  and forth re-renders from the store with ZERO new API calls.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kings_picks import picks_export, slate_store, slip
from kings_picks.game_logs import GameLogs
from kings_picks.model import Prop, score_prop
from kings_picks.odds_api import (
    AltPrice,
    AltResult,
    ApiResult,
    OddsApiClient,
    cache_path,
    event_date_detroit,
)

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
DETROIT = ZoneInfo("America/Detroit")


# ---------- per-date slate storage ----------


def _payload(pulled_at="2026-11-14T12:00:00+00:00", game_date=""):
    return slate_store.build_payload(
        pulled_at=pulled_at,
        note="fixture",
        raw_props=[],
        scored=[],
        parlays=[],
        king_parlays=[],
        king_info={},
        game_date=game_date,
    )


def test_dated_store_keeps_one_slate_per_date(tmp_path):
    store = slate_store.DatedSlateStore(tmp_path / "slates")
    today = _payload(game_date="2026-11-14")
    sunday = _payload(
        pulled_at="2026-11-14T12:05:00+00:00", game_date="2026-11-15"
    )
    assert store.save("2026-11-14", today)
    assert store.save("2026-11-15", sunday)
    # Pulling Sunday never wiped today, and each date reads
    # back its own slate.
    assert store.load("2026-11-14")["pulled_at"] == today["pulled_at"]
    assert store.load("2026-11-15")["pulled_at"] == sunday["pulled_at"]
    assert store.dates() == ["2026-11-14", "2026-11-15"]
    assert store.exists("2026-11-15")
    assert not store.exists("2026-11-16")
    assert store.load("2026-11-16") is None


def test_dated_store_rejects_bad_dates_and_purges_demo(tmp_path):
    store = slate_store.DatedSlateStore(tmp_path / "slates")
    # A date is a filename here: anything that isn't a real
    # YYYY-MM-DD day is refused, never written.
    assert store.save("not-a-date", _payload()) is False
    assert store.save("2026-13-40", _payload()) is False
    assert store.save("../escape", _payload()) is False
    assert store.load("not-a-date") is None
    assert list((tmp_path / "slates").glob("*.json")) == []
    # A leftover demo payload is purged on sight, like the
    # single store does.
    demo = _payload()
    demo["mode"] = "demo"
    assert store.save("2026-11-14", demo)
    assert store.load("2026-11-14") is None
    assert not (tmp_path / "slates" / "2026-11-14.json").exists()


def test_build_payload_records_game_date_and_restore_reads_it():
    payload = _payload(game_date="2026-11-16")
    assert payload["game_date"] == "2026-11-16"
    restored = slate_store.restore_slate(payload)
    assert restored is not None
    assert restored["game_date"] == "2026-11-16"


def test_payload_game_date_derivations():
    # Recorded at pull time wins.
    assert (
        slate_store.payload_game_date(_payload(game_date="2026-11-16"))
        == "2026-11-16"
    )
    # Older payloads are dated by their games: the Detroit
    # date of the earliest commence time (00:30 UTC is still
    # the evening before in Detroit).
    legacy = slate_store.build_payload(
        pulled_at="2026-11-14T12:00:00+00:00",
        note="",
        raw_props=[
            Prop(
                player="P", sport="NBA", prop="Points", side="Over",
                line=10.5, odds=-110, event="A vs B",
                commence_time="2026-11-16T00:30:00Z",
            )
        ],
        scored=[], parlays=[], king_parlays=[], king_info={},
    )
    assert slate_store.payload_game_date(legacy) == "2026-11-15"
    # No games to date it by: the pull's own Detroit date.
    assert (
        slate_store.payload_game_date(
            _payload(pulled_at="2026-11-14T12:00:00+00:00")
        )
        == "2026-11-14"
    )
    # Nothing says: "" — never a guess presented as fact.
    assert slate_store.payload_game_date(_payload(pulled_at="")) == ""
    assert slate_store.payload_game_date(None) == ""
    assert slate_store.payload_game_date("junk") == ""


def test_format_game_date_names_the_day_plainly():
    assert (
        slate_store.format_game_date("2026-11-16")
        == "Monday, November 16, 2026"
    )
    assert slate_store.format_game_date("") == ""
    assert slate_store.format_game_date("16/11/2026") == ""


# ---------- date filtering in the Odds API client ----------


def test_event_date_detroit_reads_the_detroit_day():
    # 00:30 UTC is 7:30 PM the evening before in Detroit.
    assert event_date_detroit("2026-11-16T00:30:00Z") == "2026-11-15"
    # 18:00 UTC is 1:00 PM the same day in Detroit.
    assert event_date_detroit("2026-11-16T18:00:00Z") == "2026-11-16"
    assert event_date_detroit("") == ""
    assert event_date_detroit("not a time") == ""


def test_dated_cache_path_is_per_date(tmp_path):
    assert cache_path(tmp_path, "NBA").name == "nba_props.json"
    assert (
        cache_path(tmp_path, "NBA", "2026-11-16").name
        == "nba_props_2026-11-16.json"
    )


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {
            "x-requests-used": "1",
            "x-requests-remaining": "499",
        }
        self.text = ""

    def json(self):
        return self._payload


def _event(event_id, away, home, commence, player):
    return {
        "id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "bookmakers": [
            {
                "key": "betmgm",
                "title": "BetMGM",
                "markets": [
                    {
                        "key": "player_points",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": player,
                                "price": -110,
                                "point": 20.5,
                            },
                            {
                                "name": "Under",
                                "description": player,
                                "price": -110,
                                "point": 20.5,
                            },
                        ],
                    }
                ],
            }
        ],
    }


class _Session:
    """A fake Odds API transport: both events are always
    listed (the dated bounds are the API's hint, the
    client-side filter is Lilly's guarantee); per-event odds
    calls are recorded so tests can prove only the picked
    day's games were ever priced."""

    def __init__(self, events):
        self.events = events
        self.calls = []

    def get(self, url, params=None, timeout=30):
        self.calls.append((url, dict(params or {})))
        if url.endswith("/events"):
            return _Resp(self.events)
        for event in self.events:
            if f"/events/{event['id']}/odds" in url:
                return _Resp(event)
        raise AssertionError(f"unexpected URL: {url}")


def _two_day_events():
    today = datetime.now(DETROIT).date()
    tomorrow = today + timedelta(days=1)
    day_after = tomorrow + timedelta(days=1)
    return today, tomorrow, [
        # 23:30 UTC is the same evening in Detroit.
        _event("evt-today", "Alpha", "Beta", f"{today}T23:30:00Z", "Today Star"),
        # 00:30 UTC the day after tomorrow is tomorrow
        # evening in Detroit.
        _event(
            "evt-tomorrow", "Gamma", "Delta",
            f"{day_after}T00:30:00Z", "Future Star",
        ),
    ]


def test_fetch_props_filters_to_the_picked_date(tmp_path):
    today, tomorrow, events = _two_day_events()
    session = _Session(events)
    client = OddsApiClient(
        api_key="fixture-key", cache_dir=tmp_path / "cache", session=session
    )
    result = client.fetch_props("NBA", game_date=tomorrow.isoformat())
    assert result.ok
    assert {p.player for p in result.props} == {"Future Star"}
    # The events call asked for just that day...
    events_calls = [c for c in session.calls if c[0].endswith("/events")]
    assert events_calls and "commenceTimeFrom" in events_calls[0][1]
    # ...and only that day's game was ever priced.
    odds_calls = [c for c in session.calls if "/odds" in c[0]]
    assert len(odds_calls) == 1
    assert "evt-tomorrow" in odds_calls[0][0]
    # The cache is per date: the dated file exists, the
    # undated one was never written.
    assert (tmp_path / "cache" / f"nba_props_{tomorrow.isoformat()}.json").exists()
    assert not (tmp_path / "cache" / "nba_props.json").exists()
    # A second pull for the same date is served from that
    # cache — zero new transport calls.
    calls_before = len(session.calls)
    again = client.fetch_props("NBA", game_date=tomorrow.isoformat())
    assert again.ok and {p.player for p in again.props} == {"Future Star"}
    assert len(session.calls) == calls_before
    # An undated pull still sees both days' games.
    both = client.fetch_props("NBA")
    assert {p.player for p in both.props} == {"Today Star", "Future Star"}


def test_fetch_props_for_a_day_with_no_games_says_so_plainly(tmp_path):
    today, _tomorrow, events = _two_day_events()
    session = _Session(events)
    client = OddsApiClient(
        api_key="fixture-key", cache_dir=tmp_path / "cache", session=session
    )
    empty_day = (today + timedelta(days=4)).isoformat()
    result = client.fetch_props("NBA", game_date=empty_day)
    assert result.ok
    assert result.props == []
    assert "No NBA games on" in result.message
    assert "haven't posted lines" in result.message
    # Nothing was priced for a day with no games.
    assert not [c for c in session.calls if "/odds" in c[0]]


# ---------- test my picks: typed lines + combined chance ----------


def _scored_pick():
    prop = Prop(
        player="Star", sport="NBA", prop="Points", side="Over",
        line=24.5, odds=-110, bookmaker="BetMGM", event="A vs B",
        event_id="evt-1",
    )
    return score_prop(prop, pair_odds=-110)


def test_custom_line_option_priced_projection_and_standard():
    pick = _scored_pick()
    alts = [
        AltPrice(
            player="Star", sport="NBA", prop="Points",
            market_key="player_points", side="Over", line=22.5,
            odds=-155, bookmaker="bet365",
        )
    ]
    # A typed line a book prices: playable at the book's
    # real price, and the slip leg carries the DROPPED line
    # with the standard line remembered.
    priced = slip.custom_line_option(pick, alts, 22.5)
    assert priced is not None and priced.playable
    assert priced.odds == -155 and priced.bookmaker == "bet365"
    leg = priced.to_leg()
    assert leg is not None
    assert leg.line == 22.5 and leg.base_line == 24.5
    # A typed line no book prices, with form behind it:
    # a chance to read, never a leg.
    values = [28.0, 26.0, 30.0, 24.0, 27.0, 29.0, 25.0, 31.0, 26.0, 28.0]
    projection_only = slip.custom_line_option(
        pick, alts, 19.5, form_values=values
    )
    assert projection_only is not None
    assert not projection_only.playable
    assert projection_only.probability is not None
    assert projection_only.to_leg() is None
    # No price and no form: an honest nothing.
    nothing = slip.custom_line_option(pick, alts, 19.5)
    assert nothing is not None
    assert not nothing.playable and nothing.probability is None
    # The standard line itself, and nonsense, are not tests.
    assert slip.custom_line_option(pick, alts, 24.5) is None
    assert slip.custom_line_option(pick, alts, 0) is None
    assert slip.custom_line_option(pick, alts, -3) is None


def test_slip_combined_chance_multiplies_the_legs():
    def leg(probability):
        return slip.SlipLeg(
            player="P", sport="NBA", prop="Points", side="Over",
            line=10.5, odds=-110, probability=probability,
        )

    summary = slip.slip_summary([leg(0.8), leg(0.5)], 10.0)
    assert summary["overall_probability"] == pytest.approx(0.4)
    # ...and the words the app and the download say for it.
    assert picks_export.chance_words(0.4) == "about 4 in 10 (40%)"
    assert picks_export.chance_words(0.8) == "about 8 in 10 (80%)"


# ---------- the download for a chosen date ----------


def _own_leg():
    leg = slip.SlipLeg(
        player="Browser Star", sport="NBA", prop="Points", side="Over",
        line=22.5, odds=-155, probability=0.8, bookmaker="bet365",
        event="Alpha vs Beta", base_line=24.5,
    )
    leg.form_verdict = "STRONG"
    leg.form_record = "10/10"
    return leg


def test_export_names_the_date_labels_prebuilt_and_carries_own_picks():
    text = picks_export.build_picks_text(
        pulled_at="2026-11-14T12:00:00+00:00",
        parlays=[],
        king_parlays=[],
        game_date="2026-11-16",
        today="2026-11-14",
        slip_legs=[_own_leg()],
        slip_profile="👑 King",
    )
    assert "Games for: Monday, November 16, 2026" in text
    assert "🔮 Pre-built — an early look at that day's games." in text
    assert "YOUR OWN PICKS" in text
    assert "Over 22.5 points" in text
    assert "dropped down from 24.5 to 22.5" in text
    assert "hit this line in 10 of his last 10 games" in text
    assert "Overall chance if played together: about 8 in 10 (80%)" in text


def test_export_for_today_is_not_called_prebuilt():
    text = picks_export.build_picks_text(
        pulled_at="2026-11-14T12:00:00+00:00",
        parlays=[],
        king_parlays=[],
        game_date="2026-11-14",
        today="2026-11-14",
        slip_legs=[_own_leg()],
        slip_profile="👑 King",
    )
    assert "Games for:" in text
    assert "Pre-built" not in text


def test_export_with_nothing_is_empty():
    assert picks_export.build_picks_text() == ""
    assert (
        picks_export.build_picks_text(game_date="2026-11-16", today="2026-11-14")
        == ""
    )


# ---------- the app: pre-build tomorrow, switch for free ----------


class _FakeStore:
    """StatsStore stand-in: no feeds, so scoring is odds-only
    and nothing tries the network."""

    def nfl_logs(self):
        return None

    def nba_logs(self):
        return None


def _run_app(secrets=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    at.session_state["stats_store"] = _FakeStore()
    return at


def _login_as_king(at):
    at.button(key="login-king").click().run()
    at.text_input(key="pin_entry").set_value("1111")
    matches = [b for b in at.button if b.label == "Log in"]
    assert matches, "no Log in button"
    matches[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    assert at.session_state["active_profile"] == "King"
    return at


def _all_text(at):
    parts = []
    for accessor in ("title", "subheader", "markdown", "caption", "info", "success"):
        try:
            parts += [el.value for el in getattr(at, accessor)]
        except Exception:
            pass
    return "\n".join(parts)


def _ticket_texts(at):
    return sorted(
        m.value for m in at.markdown if "bet-ticket'>" in m.value
    )


def _future_props(tomorrow: date):
    commence = datetime.combine(
        tomorrow, time(19, 0), tzinfo=DETROIT
    ).isoformat()
    props = []
    for i in range(14):
        for side in ("Over", "Under"):
            props.append(Prop(
                player=f"Future{i}", sport="NBA", prop="Points",
                side=side, line=10.5 + i, odds=-110 - (i % 5) * 10,
                bookmaker="FixtureBook", event=f"Team{i} vs Team{i + 1}",
                event_id="evt-future", commence_time=commence,
                source="live",
            ))
    return props


def test_app_prebuild_for_tomorrow_stores_by_date_and_switching_is_free(
    tmp_path, monkeypatch
):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    today = datetime.now(DETROIT).date()
    tomorrow = today + timedelta(days=1)
    tomorrow_iso = tomorrow.isoformat()
    calls: list = []

    def fetch_props(self, sport, force_refresh=False, max_events=12, game_date=None):
        calls.append(("props", sport, game_date))
        props = _future_props(tomorrow) if game_date == tomorrow_iso else []
        return ApiResult(
            True, "fixture props",
            props=[p for p in props if p.sport == sport],
            requests_remaining="499 of 500 requests left this month (1 used)",
        )

    def fetch_alternates(self, sport, event_id, market_key, force_refresh=False):
        calls.append(("alts", sport, market_key))
        return AltResult(True, "fixture alternates", prices=[])

    monkeypatch.setattr(OddsApiClient, "has_key", property(lambda self: True))
    monkeypatch.setattr(OddsApiClient, "fetch_props", fetch_props)
    monkeypatch.setattr(OddsApiClient, "fetch_alternates", fetch_alternates)
    monkeypatch.setattr(
        OddsApiClient, "resolve_event_id",
        lambda self, sport, event_label, commence_time="": "evt-future",
    )

    at = _login_as_king(_run_app(FAKE_SECRETS))
    # Pick tomorrow in the Dashboard date picker: an honest
    # empty day, and picking it cost no pull at all.
    at.selectbox(key="date_pick_dash").set_value(tomorrow_iso).run()
    assert not at.exception, f"app raised: {at.exception}"
    assert at.session_state["game_date"] == tomorrow_iso
    assert at.session_state["parlays"] == []
    assert calls == []

    # Pre-build tomorrow's picks.
    run = [b for b in at.button if "Run the algorithm" in b.label]
    assert run, "run button missing for the future date"
    run[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    assert ("props", "NBA", tomorrow_iso) in calls
    # Stored under TOMORROW's date — today's mirror untouched.
    assert (tmp_path / "data" / "slates" / f"{tomorrow_iso}.json").exists()
    assert not (tmp_path / "data" / "current_slate.json").exists()
    tickets = _ticket_texts(at)
    assert tickets, "no pre-built tickets rendered"
    assert all("PRE-BUILT" in t for t in tickets)
    assert "Pre-built" in _all_text(at)

    # Switch back to today: empty again, zero new calls.
    calls_after_pull = len(calls)
    at.selectbox(key="date_pick_dash").set_value(today.isoformat()).run()
    assert not at.exception, f"app raised: {at.exception}"
    assert at.session_state["parlays"] == []
    assert len(calls) == calls_after_pull

    # Switch to tomorrow again: the same tickets, still free.
    at.selectbox(key="date_pick_dash").set_value(tomorrow_iso).run()
    assert not at.exception, f"app raised: {at.exception}"
    assert _ticket_texts(at) == tickets
    assert len(calls) == calls_after_pull
