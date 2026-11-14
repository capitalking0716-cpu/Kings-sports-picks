"""Tests for dropped lines on My Slip — Neko's betting
style (2026-11-12): "whatever the player is known for,
take that number and drop it down a few and play that."

Covered: the per-market step table, Lilly's chance rising
as the line drops, alternate-market parsing from a fixture
payload, the on-demand + cached alt fetch (quota
discipline), slip maths with a dropped leg, standard-vs-
dropped uniqueness, and a saved dropped leg settling
against the PLAYED line while staying out of Lilly's
learning.

Run: pytest -q

The PINs below ("1111" / "2222") are FAKE test values. The
real PINs exist only in Neko's Streamlit secrets — never
in this repo.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import learning, projection
from kings_picks.model import Prop, score_prop
from kings_picks.odds_api import (
    AltPrice,
    OddsApiClient,
    _parse_event_alternates,
    alternate_market_key,
    market_key_for_label,
)
from kings_picks.pick_log import PickLog
from kings_picks.slip import (
    DROP_STEPS,
    SlipBook,
    SlipLeg,
    adjusted_lines,
    build_drop_options,
    drop_steps_for,
    slip_summary,
)

FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
TODAY = "2026-11-12"

# A steady scorer's log: 10 games, newest first.
VALUES = [30.0, 26.0, 24.0, 28.0, 22.0, 27.0, 25.0, 29.0, 23.0, 26.0]


# ---------- the step table ----------


def test_step_table_scales_with_the_market():
    assert drop_steps_for("Points") == (2.0, 4.0, 6.0)
    assert drop_steps_for("Rebounds") == (2.0, 4.0, 6.0)
    assert drop_steps_for("Assists") == (2.0, 4.0, 6.0)
    assert drop_steps_for("Points + Rebounds + Assists") == (3.0, 6.0, 9.0)
    assert drop_steps_for("Three-Pointers Made") == (1.0, 2.0)
    assert drop_steps_for("Passing Yards") == (25.0, 50.0, 75.0)
    assert drop_steps_for("Rushing Yards") == (10.0, 20.0, 30.0)
    assert drop_steps_for("Receiving Yards") == (10.0, 20.0, 30.0)
    assert drop_steps_for("Receptions") == (1.0, 2.0, 3.0)
    assert drop_steps_for("Passing Touchdowns") == (1.0,)
    # A yes/no market has no line to drop, and an unknown
    # market gets no invented steps.
    assert drop_steps_for("Anytime Touchdown") == ()
    assert drop_steps_for("Some Future Market") == ()
    assert set(DROP_STEPS) >= {
        "Points", "Rebounds", "Assists", "Passing Yards",
        "Rushing Yards", "Receiving Yards",
    }


def test_adjusted_lines_drop_overs_and_raise_unders():
    assert adjusted_lines("Points", "Over", 24.5) == [
        (22.5, 2.0), (20.5, 4.0), (18.5, 6.0),
    ]
    assert adjusted_lines("Points", "Under", 24.5) == [
        (26.5, 2.0), (28.5, 4.0), (30.5, 6.0),
    ]
    assert adjusted_lines("Passing Yards", "Over", 249.5) == [
        (224.5, 25.0), (199.5, 50.0), (174.5, 75.0),
    ]


def test_adjusted_lines_keep_convention_and_stay_positive():
    # A whole-number base line stays whole (its pushes are
    # real — the projection handles them).
    assert adjusted_lines("Points", "Over", 24.0) == [
        (22.0, 2.0), (20.0, 4.0), (18.0, 6.0),
    ]
    # A small line runs out of room: candidates at or below
    # zero are dropped, never offered.
    assert adjusted_lines("Rebounds", "Over", 4.5) == [(2.5, 2.0), (0.5, 4.0)]
    assert adjusted_lines("Three-Pointers Made", "Over", 0.5) == []
    for label in DROP_STEPS:
        for side in ("Over", "Under"):
            for new_line, _step in adjusted_lines(label, side, 12.5):
                assert new_line > 0
                # The .5 of the base line survives every step.
                assert new_line % 1 == 0.5


# ---------- Lilly's chance at a dropped line ----------


def _board_pick(values=None, line=24.5, side="Over"):
    p = Prop(player="Star", sport="NBA", prop="Points", side=side,
             line=line, odds=-110, bookmaker="Test Book", event="A vs B")
    if values:
        proj = projection.project_stat(values)
        return score_prop(
            p, pair_odds=-110,
            form_probability=projection.over_probability(values, line, side),
            projected_value=proj.expected, projection_games=proj.games,
        )
    return score_prop(p, pair_odds=-110)


def _alt(side, line, odds, player="Star", book="Alt Book", prop="Points"):
    return AltPrice(
        player=player, sport="NBA", prop=prop,
        market_key=market_key_for_label(prop), side=side, line=line,
        odds=odds, bookmaker=book, event="A vs B",
    )


def _priced_alts():
    rows = []
    for line, over, under in ((22.5, -150, 130), (20.5, -260, 220), (18.5, -450, 340)):
        rows.append(_alt("Over", line, over))
        rows.append(_alt("Under", line, under))
    return rows


def test_chance_rises_as_the_line_drops():
    # The raw projection is monotone in the line...
    chances = [
        projection.over_probability(VALUES, line, "Over")
        for line in (24.5, 22.5, 20.5, 18.5)
    ]
    assert chances == sorted(chances)
    assert len(set(chances)) == 4  # strictly, on this log
    # ...and so is every priced option Lilly shows.
    pick = _board_pick(values=VALUES)
    options = build_drop_options(pick, _priced_alts(), form_values=VALUES)
    assert [o.line for o in options] == [22.5, 20.5, 18.5]
    shown = [o.probability for o in options]
    assert shown == sorted(shown) and len(set(shown)) == 3
    assert all(o.probability > pick.probability for o in options)
    assert all(o.playable for o in options)


def test_priced_option_is_scored_through_the_board_path():
    pick = _board_pick(values=VALUES)
    learned = {
        "delta": -0.03,
        "tag": "nba_points_over:-3",
        "note": "Learning: trimmed in the fixture.",
    }
    options = build_drop_options(
        pick, _priced_alts(), form_values=VALUES, learning=learned
    )
    option = options[0]  # Over 22.5 at -150, pair +130
    proj = projection.project_stat(VALUES)
    expected = score_prop(
        Prop(player="Star", sport="NBA", prop="Points", side="Over",
             line=22.5, odds=-150, bookmaker="Alt Book", event="A vs B"),
        pair_odds=130,
        form_probability=projection.over_probability(VALUES, 22.5, "Over"),
        projected_value=proj.expected, projection_games=proj.games,
        learning_delta=-0.03, learning_tag="nba_points_over:-3",
        learning_note="Learning: trimmed in the fixture.",
    )
    assert option.probability == pytest.approx(expected.probability)
    assert option.scored.why == expected.why
    assert option.scored.learning == "nba_points_over:-3"
    assert option.odds == -150 and option.bookmaker == "Alt Book"


def test_odds_only_option_devigs_when_both_alt_sides_priced():
    pick = _board_pick(values=None)
    options = build_drop_options(pick, _priced_alts(), form_values=None)
    option = options[0]
    expected = score_prop(
        Prop(player="Star", sport="NBA", prop="Points", side="Over",
             line=22.5, odds=-150, bookmaker="Alt Book", event="A vs B"),
        pair_odds=130,
    )
    assert option.playable
    assert option.probability == pytest.approx(expected.probability)
    assert not option.scored.has_form_data


def test_odds_only_option_one_side_keeps_the_cut_in():
    pick = _board_pick(values=None)
    options = build_drop_options(
        pick, [_alt("Over", 22.5, -150)], form_values=None
    )
    option = options[0]
    expected = score_prop(
        Prop(player="Star", sport="NBA", prop="Points", side="Over",
             line=22.5, odds=-150, bookmaker="Alt Book", event="A vs B"),
        pair_odds=None,
    )
    assert option.playable
    assert option.probability == pytest.approx(expected.probability)
    assert "bookmaker's cut is still in" in option.scored.why


def test_unpriced_option_with_form_is_chance_only():
    pick = _board_pick(values=VALUES)
    options = build_drop_options(pick, [], form_values=VALUES)
    assert len(options) == 3
    for option in options:
        assert not option.playable
        assert option.odds is None
        assert option.to_leg() is None  # never joins payout maths
        assert option.probability == pytest.approx(
            projection.over_probability(VALUES, option.line, "Over")
        )
        assert "Projection only" in option.note


def test_unpriced_option_without_form_shows_nothing_invented():
    pick = _board_pick(values=None)
    options = build_drop_options(pick, [], form_values=None)
    for option in options:
        assert not option.playable
        assert option.probability is None
        assert "No book offers this line" in option.note


def test_alt_rows_for_other_players_are_ignored():
    pick = _board_pick(values=None)
    strangers = [_alt("Over", 22.5, -150, player="Someone Else"),
                 _alt("Under", 22.5, 130, player="Someone Else")]
    options = build_drop_options(pick, strangers, form_values=None)
    assert all(not o.playable and o.probability is None for o in options)


# ---------- alternate-market parsing ----------


def _alt_event_payload():
    return {
        "id": "evt123",
        "home_team": "Lakers", "away_team": "Celtics",
        "commence_time": "2026-11-13T00:00:00Z",
        "bookmakers": [
            {"key": "betmgm", "title": "BetMGM", "markets": [
                # The standard market rides along in the
                # payload and must NOT be read as alternates.
                {"key": "player_points", "outcomes": [
                    {"description": "LeBron James", "name": "Over", "point": 24.5, "price": -110},
                    {"description": "LeBron James", "name": "Under", "point": 24.5, "price": -110},
                ]},
                {"key": "player_points_alternate", "outcomes": [
                    {"description": "LeBron James", "name": "Over", "point": 22.5, "price": -200},
                    {"description": "LeBron James", "name": "Under", "point": 22.5, "price": 165},
                    {"description": "LeBron James", "name": "Over", "point": 20.5, "price": -320},
                    {"description": "LeBron James", "name": "Under", "point": 20.5, "price": 250},
                    {"description": "LeBron James", "name": "Over", "point": 18.5, "price": -450},
                    {"description": "LeBron James", "name": "Under", "point": 18.5, "price": 340},
                    {"description": "Other Guy", "name": "Over", "point": 22.5, "price": 100},
                ]},
            ]},
            {"key": "bet365", "title": "bet365", "markets": [
                {"key": "player_points_alternate", "outcomes": [
                    # A better Over 22.5 price than BetMGM's.
                    {"description": "LeBron James", "name": "Over", "point": 22.5, "price": -195},
                ]},
            ]},
            # A book Lilly doesn't ask for: ignored even at a
            # too-good price.
            {"key": "draftkings", "title": "DraftKings", "markets": [
                {"key": "player_points_alternate", "outcomes": [
                    {"description": "LeBron James", "name": "Over", "point": 22.5, "price": 500},
                ]},
            ]},
        ],
    }


def test_parse_event_alternates_fixture():
    assert alternate_market_key("player_points") == "player_points_alternate"
    assert market_key_for_label("Points") == "player_points"
    rows = _parse_event_alternates(
        _alt_event_payload(), "NBA", "player_points",
        ("betmgm", "bet365"),
    )
    assert len(rows) == 7
    by = {(r.player, r.side, r.line): r for r in rows}
    best = by[("LeBron James", "Over", 22.5)]
    assert best.odds == -195 and best.bookmaker == "bet365"  # best price wins
    assert by[("LeBron James", "Under", 22.5)].odds == 165
    assert by[("LeBron James", "Over", 20.5)].odds == -320
    assert by[("LeBron James", "Under", 18.5)].odds == 340
    assert by[("Other Guy", "Over", 22.5)].odds == 100
    # Nothing from the standard market, nothing from DraftKings.
    assert all(r.line != 24.5 for r in rows)
    assert all(r.odds != 500 for r in rows)
    assert all(r.prop == "Points" and r.market_key == "player_points" for r in rows)
    assert all(r.event == "Celtics vs Lakers" for r in rows)


# ---------- the on-demand fetch: quota discipline ----------


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {
            "x-requests-used": "41",
            "x-requests-remaining": "459",
        }
        self.text = ""

    def json(self):
        return self._payload


class _FakeSession:
    """A requests-shaped stand-in that counts every call."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.handler(url, params or {})


def _std_event_payload():
    return {
        "id": "evt123",
        "home_team": "Lakers", "away_team": "Celtics",
        "commence_time": "2026-11-13T00:00:00Z",
        "bookmakers": [
            {"key": "betmgm", "title": "BetMGM", "markets": [
                {"key": "player_points", "outcomes": [
                    {"description": "LeBron James", "name": "Over", "point": 24.5, "price": -110},
                    {"description": "LeBron James", "name": "Under", "point": 24.5, "price": -110},
                ]},
            ]},
            {"key": "bet365", "title": "bet365", "markets": [
                {"key": "player_points", "outcomes": [
                    # A better Over price than BetMGM's -110.
                    {"description": "LeBron James", "name": "Over", "point": 24.5, "price": -105},
                ]},
            ]},
        ],
    }


def test_default_client_asks_for_betmgm_and_bet365(tmp_path):
    # Neko plays on bet365 and BetMGM — NOT DraftKings/FanDuel.
    # A default-constructed client must ask The Odds API for
    # exactly his two books, on BOTH the standard props pull
    # and the on-demand alternates fetch. No silent fallback.
    def handler(url, params):
        if url.endswith("/events"):
            return _Resp([{"id": "evt123",
                           "commence_time": "2026-11-13T00:00:00Z",
                           "home_team": "Lakers", "away_team": "Celtics"}])
        if "alternate" in (params.get("markets") or ""):
            return _Resp(_alt_event_payload())
        return _Resp(_std_event_payload())

    session = _FakeSession(handler)
    client = OddsApiClient(api_key="TESTKEY", cache_dir=tmp_path, session=session)
    assert client.bookmakers == ("betmgm", "bet365")

    pulled = client.fetch_props("NBA", force_refresh=True)
    assert pulled.ok and len(pulled.props) == 2  # the Over/Under pair
    over = next(p for p in pulled.props if p.side == "Over")
    assert over.bookmaker == "bet365"  # -105 beat BetMGM's -110

    alt = client.fetch_alternates("NBA", "evt123", "player_points")
    assert alt.ok and len(alt.prices) == 7

    odds_calls = [p for u, p in session.calls if "/odds" in u]
    assert len(odds_calls) == 2  # one standard pull + one alternates fetch
    assert all(p["bookmakers"] == "betmgm,bet365" for p in odds_calls)


def test_fetch_alternates_fires_once_then_serves_cache(tmp_path):
    session = _FakeSession(lambda url, params: _Resp(_alt_event_payload()))
    client = OddsApiClient(
        api_key="TESTKEY", cache_dir=tmp_path, session=session
    )
    first = client.fetch_alternates("NBA", "evt123", "player_points")
    assert first.ok and not first.from_cache
    assert len(first.prices) == 7
    assert "459" in first.requests_remaining
    assert len(session.calls) == 1
    url, params = session.calls[0]
    assert url.endswith("/sports/basketball_nba/events/evt123/odds")
    # Exactly ONE alternate market, one region — one request.
    assert params["markets"] == "player_points_alternate"
    assert params["regions"] == "us"
    # A repeat view is served from the disk cache: no call.
    second = client.fetch_alternates("NBA", "evt123", "player_points")
    assert second.ok and second.from_cache
    assert len(second.prices) == 7
    assert len(session.calls) == 1
    # A different market is a different cache key — and only
    # that market is fetched.
    third = client.fetch_alternates("NBA", "evt123", "player_rebounds")
    assert third.ok
    assert len(session.calls) == 2
    assert session.calls[1][1]["markets"] == "player_rebounds_alternate"


def test_fetch_alternates_422_is_an_honest_cached_empty(tmp_path):
    session = _FakeSession(lambda url, params: _Resp({}, status=422))
    client = OddsApiClient(
        api_key="TESTKEY", cache_dir=tmp_path, session=session
    )
    result = client.fetch_alternates("NBA", "evt999", "player_points")
    assert result.ok and result.prices == []
    assert "No book offers alternate lines" in result.message
    again = client.fetch_alternates("NBA", "evt999", "player_points")
    assert again.from_cache and len(session.calls) == 1


def test_fetch_alternates_without_key_never_calls(tmp_path):
    session = _FakeSession(lambda url, params: _Resp(_alt_event_payload()))
    client = OddsApiClient(api_key="", cache_dir=tmp_path, session=session)
    result = client.fetch_alternates("NBA", "evt123", "player_points")
    assert not result.ok
    assert session.calls == []


def test_resolve_event_id_uses_the_free_events_endpoint(tmp_path):
    events = [
        {"id": "evt123", "commence_time": "2026-11-13T00:00:00Z",
         "home_team": "Lakers", "away_team": "Celtics"},
        {"id": "evt456", "commence_time": "2026-11-13T01:00:00Z",
         "home_team": "Bulls", "away_team": "Heat"},
    ]
    session = _FakeSession(lambda url, params: _Resp(events))
    client = OddsApiClient(
        api_key="TESTKEY", cache_dir=tmp_path, session=session
    )
    assert client.resolve_event_id(
        "NBA", "Celtics vs Lakers", "2026-11-13T00:00:00Z"
    ) == "evt123"
    assert client.resolve_event_id("NBA", "Nobody vs Lakers") is None
    # Every call hit the plain events endpoint — no priced
    # /odds call, so no quota was spent resolving.
    assert all(url.endswith("/events") for url, _ in session.calls)


# ---------- slip integration ----------


def _standard_leg(**kw):
    base = dict(player="Star", sport="NBA", prop="Points", side="Over",
                line=24.5, odds=-110, probability=0.60)
    base.update(kw)
    return SlipLeg(**base)


def _dropped_leg(**kw):
    base = dict(player="Star", sport="NBA", prop="Points", side="Over",
                line=19.5, base_line=24.5, odds=-320, probability=0.78)
    base.update(kw)
    return SlipLeg(**base)


def test_slip_maths_with_one_dropped_leg():
    legs = [
        _standard_leg(player="Other", line=5.5),  # -110, 0.60
        _dropped_leg(),  # -320, 0.78, played at 19.5
    ]
    s = slip_summary(legs, 10.0)
    # decimal: (21/11) x (1 + 100/320) = 1.90909 x 1.3125
    assert s["decimal_odds"] == pytest.approx(21 / 11 * 1.3125)
    assert s["overall_probability"] == pytest.approx(0.60 * 0.78)
    assert s["payout"] == pytest.approx(10.0 * 21 / 11 * 1.3125)
    assert s["profit"] == pytest.approx(s["payout"] - 10.0)
    assert s["expected_value"] == pytest.approx(0.468 * s["payout"] - 10.0)


def test_family_uniqueness_standard_vs_dropped(tmp_path):
    book = SlipBook(tmp_path / "slips.json")
    assert book.add("King", _standard_leg())
    # The dropped version of the SAME selection is refused:
    # one version of a pick per slip.
    assert not book.add("King", _dropped_leg())
    assert not book.add("King", _dropped_leg(line=22.5, odds=-150))
    # An UNRELATED standard line for the same player/prop
    # (a different book's main line) is a different family
    # and still fits, as it always could.
    assert book.add("King", _standard_leg(line=25.5))
    assert len(book.legs("King")) == 2

    fresh = SlipBook(tmp_path / "slips2.json")
    assert fresh.add("King", _dropped_leg())
    # ...and the refusal runs the other way too.
    assert not fresh.add("King", _standard_leg())
    assert len(fresh.legs("King")) == 1


def test_option_to_leg_carries_the_base_line(tmp_path):
    pick = _board_pick(values=VALUES)
    options = build_drop_options(pick, _priced_alts(), form_values=VALUES)
    leg = options[1].to_leg()  # Over 20.5
    assert leg is not None
    assert leg.line == 20.5 and leg.base_line == 24.5
    assert leg.family == ("Star", "NBA", "Points", 24.5)
    book = SlipBook(tmp_path / "slips.json")
    assert book.add("King", leg)
    reloaded = SlipBook(tmp_path / "slips.json")
    assert reloaded.legs("King")[0].base_line == 24.5  # survives the save


def test_dropped_leg_label_names_the_standard_line():
    assert _dropped_leg().label == "Star — Points Over 19.5 (dropped from 24.5)"
    raised = SlipLeg(player="Star", sport="NBA", prop="Points", side="Under",
                     line=29.5, base_line=24.5, odds=-320, probability=0.78)
    assert raised.label == "Star — Points Under 29.5 (raised from 24.5)"
    assert _standard_leg().label == "Star — Points Over 24.5"


def test_saved_dropped_leg_settles_on_the_played_line_not_lillys(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    added, already = log.record_manual(
        [_dropped_leg()], mode="live", profile="King", created=TODAY
    )
    assert len(added) == 1 and already == []
    entry = added[0]
    # The record is the PLAYED line: settlement judges 19.5,
    # not the 24.5 the line was dropped from.
    assert entry.line == 19.5
    assert entry.source == "manual"
    assert "dropped from 24.5" in entry.label
    assert log.settle(entry.key, True)
    # And as a manual pick it never teaches Lilly — even
    # settled, even won.
    assert learning.teaching_entries(log.entries) == []
    stats = log.stats(profile="King")
    assert stats["manual"]["settled"] == 1
    assert stats["settled"] == 0  # not in Lilly's headline record


# ---------- app render: the drop expander (AppTest) ----------


def _run_app(secrets=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
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


def _install_fake_client(monkeypatch):
    """A fake live OddsApiClient — a fixture board whose
    alternate markets come back empty (no book offers a
    dropped line), so the expander must say so honestly."""
    from kings_picks.model import Prop
    from kings_picks.odds_api import AltResult, ApiResult, OddsApiClient

    props = []
    for i in range(14):
        for side in ("Over", "Under"):
            props.append(Prop(
                player=f"Fixture{i}", sport="NBA", prop="Points",
                side=side, line=10.5 + i, odds=-110 - (i % 5) * 10,
                bookmaker="FixtureBook", event=f"Team{i} vs Team{i + 1}",
                event_id="evt-fixture", source="live",
            ))

    def fetch_props(self, sport, force_refresh=False, max_events=12):
        return ApiResult(
            True, "fixture props",
            props=[p for p in props if p.sport == sport],
            requests_remaining="499 of 500 requests left this month (1 used)",
        )

    monkeypatch.setattr(OddsApiClient, "has_key", property(lambda self: True))
    monkeypatch.setattr(OddsApiClient, "fetch_props", fetch_props)
    monkeypatch.setattr(
        OddsApiClient, "fetch_alternates",
        lambda self, sport, event_id, market_key, force_refresh=False:
            AltResult(True, "fixture alternates", prices=[]),
    )
    monkeypatch.setattr(
        OddsApiClient, "resolve_event_id",
        lambda self, sport, event_label, commence_time="": "evt-fixture",
    )


class _FakeStore:
    def nfl_logs(self):
        return None

    def nba_logs(self):
        return None


def test_app_drop_expander_renders_honestly_on_a_live_board(monkeypatch):
    at = _login_as_king(_run_app(FAKE_SECRETS))
    _install_fake_client(monkeypatch)
    at.session_state["stats_store"] = _FakeStore()
    radios = list(at.radio)
    radios[0].set_value("My Slip").run()
    assert not at.exception, f"app raised: {at.exception}"
    load = [b for b in at.button if "Load today's board" in b.label]
    load[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    toggles = [b for b in at.button if "Dropped lines" in b.label]
    assert toggles, "no dropped-lines expander on the board"
    toggles[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    infos = "\n".join(i.value for i in at.info)
    captions = "\n".join(c.value for c in at.caption)
    markdown = "\n".join(m.value for m in at.markdown)
    # No book offers this player a dropped line: the page
    # says so, shows the honest drop note, and invents no price.
    assert "No book offers a dropped line" in infos
    assert "payout drops too" in captions
    assert "No book offers this line" in markdown
