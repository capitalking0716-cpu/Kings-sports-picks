"""Tests for King's style parlays — Lilly's parlays rebuilt
at Neko's dropped lines (2026-11-12: "These are the pics I
want — small money adds up").

Covered: the middle-step counterpart construction (dropped
leg price / chance / parlay maths hand-checked against the
board's own drop path), the per-leg fallbacks (unpriced
middle line; a market with no drop at all), the quota guard
(alternates fetched once per unique (event, market) pair in
the final parlays — never slate-wide, never for no-drop
markets), demo building nothing, logging with the style tag,
settled King entries teaching Lilly's learning loop while
manual picks stay excluded, and the standard set coming out
of the build untouched.

Run: pytest -q

The PINs below ("1111" / "2222") are FAKE test values. The
real PINs exist only in Neko's Streamlit secrets — never
in this repo.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import king_style, learning, projection
from kings_picks.model import Prop, american_to_decimal, score_prop
from kings_picks.odds_api import AltPrice, market_key_for_label
from kings_picks.parlays import Parlay, build_parlays
from kings_picks.pick_log import PickLog
from kings_picks.slip import SlipLeg, build_drop_options

FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
TODAY = "2026-11-12"

# A steady scorer's log: 10 games, newest first.
VALUES = [30.0, 26.0, 24.0, 28.0, 22.0, 27.0, 25.0, 29.0, 23.0, 26.0]


# ---------- fixtures ----------


def _points_leg():
    p = Prop(player="Star", sport="NBA", prop="Points", side="Over",
             line=24.5, odds=-110, bookmaker="Test Book",
             event="A vs B", event_id="ev1")
    proj = projection.project_stat(VALUES)
    return score_prop(
        p, pair_odds=-110,
        form_probability=projection.over_probability(VALUES, 24.5, "Over"),
        projected_value=proj.expected, projection_games=proj.games,
    )


def _rush_leg():
    p = Prop(player="Runner", sport="NFL", prop="Rushing Yards", side="Over",
             line=69.5, odds=-110, bookmaker="Test Book",
             event="C vs D", event_id="ev2")
    return score_prop(p, pair_odds=-110)


def _td_leg():
    p = Prop(player="Star", sport="NBA", prop="Anytime Touchdown", side="Over",
             line=0.5, odds=150, bookmaker="Test Book",
             event="A vs B", event_id="ev1")
    return score_prop(p)


def _alt(player, sport, prop, side, line, odds, event):
    return AltPrice(
        player=player, sport=sport, prop=prop,
        market_key=market_key_for_label(prop), side=side, line=line,
        odds=odds, bookmaker="Alt Book", event=event,
    )


def _points_alts():
    rows = []
    for line, over, under in ((22.5, -150, 130), (20.5, -260, 220), (18.5, -450, 340)):
        rows.append(_alt("Star", "NBA", "Points", "Over", line, over, "A vs B"))
        rows.append(_alt("Star", "NBA", "Points", "Under", line, under, "A vs B"))
    return rows


def _rush_alts():
    rows = []
    for line, over, under in ((59.5, -140, 120), (49.5, -230, 195), (39.5, -380, 300)):
        rows.append(_alt("Runner", "NFL", "Rushing Yards", "Over", line, over, "C vs D"))
        rows.append(_alt("Runner", "NFL", "Rushing Yards", "Under", line, under, "C vs D"))
    return rows


class FakeFetcher:
    """Counts every call; serves a fixed (sport, event,
    market) -> prices table."""

    def __init__(self, table=None):
        self.table = table or {}
        self.calls = []

    def __call__(self, sport, event_id, market_key, event="", commence_time=""):
        self.calls.append((sport, event_id, market_key))
        return list(self.table.get((sport, event_id, market_key), []))


def _full_fetcher():
    return FakeFetcher({
        ("NBA", "ev1", "player_points"): _points_alts(),
        ("NFL", "ev2", "player_rush_yds"): _rush_alts(),
    })


def _standard_parlay():
    legs = [_points_leg(), _rush_leg(), _td_leg()]
    prob = 1.0
    payout = 1.0
    for leg in legs:
        prob *= leg.probability
        payout *= american_to_decimal(leg.odds)
    return Parlay(
        size=3, picks=legs, overall_probability=prob,
        decimal_payout=payout, expected_value_per_dollar=prob * payout - 1.0,
        label="3-pick parlay #1",
    )


def _form_lookup(leg):
    if leg.player == "Star" and leg.prop == "Points":
        return VALUES
    return None


# ---------- the middle step ----------


def test_middle_step_is_the_tables_middle():
    assert king_style.middle_step("Points") == 4.0
    assert king_style.middle_step("Rushing Yards") == 20.0
    assert king_style.middle_step("Passing Yards") == 50.0
    assert king_style.middle_step("Passing Touchdowns") == 1.0
    # No table entry, no drop — and no invention.
    assert king_style.middle_step("Anytime Touchdown") is None
    assert king_style.middle_step("Some Market Lilly Never Sees") is None


# ---------- counterpart construction ----------


def test_king_counterpart_drops_each_leg_at_the_middle_step():
    standard = _standard_parlay()
    fetcher = _full_fetcher()
    king, info = king_style.build_king_parlays(
        [standard], fetcher, form_lookup=_form_lookup
    )
    assert len(king) == 1
    parlay = king[0]
    assert parlay.style == "king"
    assert parlay.size == 3 and parlay.label == standard.label

    # Leg 1: Points 24.5 dropped by the middle step (4) to
    # 20.5, at the book's real alternate price.
    leg1 = parlay.picks[0]
    assert leg1.line == 20.5 and leg1.odds == -260
    assert leg1.base_line == 24.5
    # Its chance is the board's own drop-path number at 20.5.
    options = build_drop_options(
        standard.picks[0], _points_alts(), form_values=VALUES
    )
    middle = [o for o in options if o.step == 4.0][0]
    assert middle.playable
    assert leg1.probability == pytest.approx(middle.probability)
    assert leg1.probability > standard.picks[0].probability

    # Leg 2: Rushing Yards 69.5 dropped by 20 to 49.5,
    # odds-only, de-vigged from the priced alt pair.
    leg2 = parlay.picks[1]
    assert leg2.line == 49.5 and leg2.odds == -230
    assert leg2.base_line == 69.5
    expected2 = score_prop(
        Prop(player="Runner", sport="NFL", prop="Rushing Yards",
             side="Over", line=49.5, odds=-230, bookmaker="Alt Book",
             event="C vs D", event_id="ev2"),
        pair_odds=195,
    )
    assert leg2.probability == pytest.approx(expected2.probability)

    # Leg 3: Anytime Touchdown has no line to drop — the
    # standard leg is kept, copied, and labelled plainly.
    leg3 = parlay.picks[2]
    assert leg3.line == 0.5 and leg3.odds == 150
    assert leg3.base_line is None
    assert leg3.king_note == king_style.KEPT_NO_DROP
    assert leg3 is not standard.picks[2]
    assert leg3.probability == pytest.approx(standard.picks[2].probability)

    # The parlay maths is recomputed from the King legs with
    # the same honest products as the standard set.
    prob = 1.0
    payout = 1.0
    for leg in parlay.picks:
        prob *= leg.probability
        payout *= american_to_decimal(leg.odds)
    assert parlay.overall_probability == pytest.approx(prob)
    assert parlay.decimal_payout == pytest.approx(payout)
    assert parlay.expected_value_per_dollar == pytest.approx(prob * payout - 1.0)
    # Dropped legs are safer: the King parlay out-chances
    # the standard one and pays less for it.
    assert parlay.overall_probability > standard.overall_probability
    assert parlay.decimal_payout < standard.decimal_payout

    assert info["pairs_fetched"] == 2
    assert info["legs_dropped"] == 2
    assert info["legs_kept_standard"] == 1


def test_standard_set_is_untouched_by_the_king_build():
    standard = _standard_parlay()
    before = [(leg.line, leg.odds, leg.probability) for leg in standard.picks]
    king_style.build_king_parlays([standard], _full_fetcher(), form_lookup=_form_lookup)
    after = [(leg.line, leg.odds, leg.probability) for leg in standard.picks]
    assert before == after
    assert standard.style == "standard"
    for leg in standard.picks:
        # No King annotation leaks onto Lilly's own picks.
        assert getattr(leg, "base_line", None) is None
        assert getattr(leg, "king_note", "") == ""


# ---------- fallbacks ----------


def test_unpriced_middle_line_keeps_the_standard_leg_labelled():
    # Only the smallest drop (22.5) is priced; the middle
    # step's line (20.5) is offered by no book.
    partial = [r for r in _points_alts() if r.line == 22.5]
    fetcher = FakeFetcher({
        ("NBA", "ev1", "player_points"): partial,
        ("NFL", "ev2", "player_rush_yds"): _rush_alts(),
    })
    standard = _standard_parlay()
    king, info = king_style.build_king_parlays(
        [standard], fetcher, form_lookup=_form_lookup
    )
    leg1 = king[0].picks[0]
    assert leg1.line == 24.5 and leg1.odds == -110
    assert leg1.base_line is None
    assert leg1.king_note == king_style.KEPT_NO_OFFER
    assert leg1 is not standard.picks[0]
    assert info["legs_dropped"] == 1  # only the rush leg dropped
    assert info["legs_kept_standard"] == 2


def test_no_alternates_at_all_keeps_every_droppable_leg():
    standard = _standard_parlay()
    king, info = king_style.build_king_parlays(
        [standard], FakeFetcher({}), form_lookup=_form_lookup
    )
    parlay = king[0]
    assert [leg.line for leg in parlay.picks] == [24.5, 69.5, 0.5]
    assert parlay.picks[0].king_note == king_style.KEPT_NO_OFFER
    assert parlay.picks[1].king_note == king_style.KEPT_NO_OFFER
    assert parlay.picks[2].king_note == king_style.KEPT_NO_DROP
    # Nothing dropped, nothing dressed up: same numbers as
    # the standard parlay, honestly arrived at.
    assert parlay.overall_probability == pytest.approx(standard.overall_probability)
    assert parlay.decimal_payout == pytest.approx(standard.decimal_payout)
    assert info["legs_dropped"] == 0


# ---------- quota guard ----------


def test_alternates_fetched_once_per_unique_event_market_pair():
    # A realistic generation: build_parlays shares the top
    # legs across parlays, and two players share one event's
    # Points market. The fetcher must still fire exactly
    # once per unique (event, market) in the final parlays.
    wing = score_prop(
        Prop(player="Wing", sport="NBA", prop="Points", side="Over",
             line=18.5, odds=-110, bookmaker="Test Book",
             event="A vs B", event_id="ev1"),
        pair_odds=-110,
    )
    boards = score_prop(
        Prop(player="Big", sport="NBA", prop="Rebounds", side="Over",
             line=10.5, odds=-110, bookmaker="Test Book",
             event="A vs B", event_id="ev1"),
        pair_odds=-110,
    )
    pool = [_points_leg(), wing, boards, _rush_leg(), _td_leg()]
    parlays, notes = build_parlays(pool, plan=((3, 2),))
    assert len(parlays) == 2

    wing_alts = []
    for line, over, under in ((16.5, -150, 130), (14.5, -260, 220), (12.5, -450, 340)):
        wing_alts.append(_alt("Wing", "NBA", "Points", "Over", line, over, "A vs B"))
        wing_alts.append(_alt("Wing", "NBA", "Points", "Under", line, under, "A vs B"))
    board_alts = []
    for line, over, under in ((8.5, -150, 130), (6.5, -260, 220), (4.5, -450, 340)):
        board_alts.append(_alt("Big", "NBA", "Rebounds", "Over", line, over, "A vs B"))
        board_alts.append(_alt("Big", "NBA", "Rebounds", "Under", line, under, "A vs B"))
    fetcher = FakeFetcher({
        ("NBA", "ev1", "player_points"): _points_alts() + wing_alts,
        ("NBA", "ev1", "player_rebounds"): board_alts,
        ("NFL", "ev2", "player_rush_yds"): _rush_alts(),
    })
    king_style.build_king_parlays(parlays, fetcher, form_lookup=_form_lookup)
    assert len(fetcher.calls) == 3
    assert len(set(fetcher.calls)) == 3  # no pair fetched twice
    assert set(fetcher.calls) == {
        ("NBA", "ev1", "player_points"),
        ("NBA", "ev1", "player_rebounds"),
        ("NFL", "ev2", "player_rush_yds"),
    }
    # The Anytime Touchdown leg never triggers a fetch: its
    # market has no drop.
    assert ("NBA", "ev1", "player_anytime_td") not in fetcher.calls


# ---------- logging + learning ----------


def _built_pair(fetcher=None, learning_lookup=None):
    standard = _standard_parlay()
    king, _info = king_style.build_king_parlays(
        [standard], fetcher or _full_fetcher(),
        form_lookup=_form_lookup, learning_lookup=learning_lookup,
    )
    return standard, king[0]


def test_king_entries_logged_with_style_tag_and_dropped_label(tmp_path):
    standard, king_parlay = _built_pair()
    log = PickLog(tmp_path / "pick_log.json")
    added = log.record_run([standard, king_parlay], "live", created=TODAY)
    # 3 standard legs + 2 dropped King legs; the kept TD leg
    # IS the standard pick (same key), logged once.
    assert len(added) == 5
    assert len(log.entries) == 5

    dropped = [e for e in log.entries if e.line == 20.5]
    assert len(dropped) == 1
    entry = dropped[0]
    assert entry.style == "king"
    assert entry.source == "lilly"
    assert entry.market == "Points Over"
    assert "(dropped from 24.5)" in entry.label

    standard_points = [e for e in log.entries if e.line == 24.5]
    assert len(standard_points) == 1
    assert standard_points[0].style == "standard"

    td = [e for e in log.entries if e.line == 0.5]
    assert len(td) == 1 and td[0].style == "standard"


def test_old_entries_load_as_standard_style(tmp_path):
    path = tmp_path / "pick_log.json"
    path.write_text(json.dumps([{
        "id": "k", "key": "k", "date": TODAY, "sport": "NBA",
        "event": "A vs B", "player": "Old", "market": "Points Over",
        "line": 20.5, "odds": -110, "probability": 0.5,
        "mode": "live", "status": "won",
    }]))
    log = PickLog(path)
    assert len(log.entries) == 1
    assert log.entries[0].style == "standard"
    assert log.entries[0].source == "lilly"


def test_settled_king_entries_teach_and_manual_stay_excluded(tmp_path):
    standard, king_parlay = _built_pair()
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run([standard, king_parlay], "live", created=TODAY)
    dropped = [e for e in log.entries if e.style == "king"][0]
    log.settle(dropped.key, True)

    manual_leg = SlipLeg(
        player="Hand", sport="NBA", prop="Points", side="Over",
        line=10.5, odds=-110, probability=0.6, event="A vs B",
    )
    log.record_manual([manual_leg], "live", profile="King", created=TODAY)
    manual = [e for e in log.entries if e.source == "manual"][0]
    log.settle(manual.key, False)

    teaching = learning.teaching_entries(log.entries)
    # Her King-style pick teaches: it is her number at the
    # dropped line. The hand-built pick never does.
    assert dropped in teaching
    assert manual not in teaching
    buckets = learning.compute_buckets(log.entries)
    assert buckets  # the settled King pick fed a real bucket


def test_learning_correction_rides_onto_king_legs_and_the_log(tmp_path):
    learned = {
        "delta": -0.03,
        "tag": "nba_points_over:-3",
        "note": "Learning: trimmed in the fixture.",
    }

    def learning_lookup(sport, prop, side):
        if (sport, prop, side) == ("NBA", "Points", "Over"):
            return learned
        return None

    standard, king_parlay = _built_pair(learning_lookup=learning_lookup)
    leg1 = king_parlay.picks[0]
    assert leg1.learning == "nba_points_over:-3"
    options = build_drop_options(
        standard.picks[0], _points_alts(), form_values=VALUES, learning=learned
    )
    middle = [o for o in options if o.step == 4.0][0]
    assert leg1.probability == pytest.approx(middle.probability)

    log = PickLog(tmp_path / "pick_log.json")
    log.record_run([king_parlay], "live", created=TODAY)
    entry = [e for e in log.entries if e.line == 20.5][0]
    assert entry.style == "king"
    assert entry.learning == "nba_points_over:-3"


# ---------- demo: nothing built, one explainer (AppTest) ----------


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


def test_app_demo_builds_no_king_parlays_and_explains_why():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    run = [b for b in at.button if "Run the algorithm" in b.label]
    assert run, "no Run the algorithm button on the Dashboard"
    run[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    # Demo built the standard set but NO King set — the
    # builder is never invoked without live prices, so no
    # alternate fetch can fire (the client has no key in
    # demo anyway) and nothing is invented.
    assert at.session_state["parlays"], "demo should still build standard parlays"
    assert at.session_state["king_parlays"] == []
    subheaders = "\n".join(s.value for s in at.subheader)
    assert "King's style — dropped lines" in subheaders
    assert "Lilly's standard lines" in subheaders
    infos = "\n".join(i.value for i in at.info)
    assert "won't invent dropped parlays" in infos
