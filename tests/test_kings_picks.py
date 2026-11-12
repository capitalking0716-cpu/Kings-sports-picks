"""Tests for Kings Sports Books Picks. Run: pytest -q"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import lilly
from kings_picks.ledger import Ledger
from kings_picks.model import (
    Prop,
    american_to_decimal,
    american_to_implied,
    remove_vig,
    score_prop,
)
from kings_picks.parlays import PARLAY_PLAN, build_parlays, overall_probability
from kings_picks.odds_api import _parse_event_odds


def make_prop(player="P1", odds=-110, side="Over", line=20.5, prop="Points", sport="NBA"):
    return Prop(player=player, sport=sport, prop=prop, side=side, line=line, odds=odds)


def make_scored(n, base=0.60):
    """n scored props with descending probabilities 0.60, 0.59, ..."""
    out = []
    for i in range(n):
        p = make_prop(player=f"Player{i}", odds=-110, line=10.5 + i)
        s = score_prop(p, pair_odds=-110)
        s.probability = max(0.51, base - i * 0.01)
        out.append(s)
    return out


# ---------- model math ----------

def test_american_to_implied_negative():
    assert american_to_implied(-110) == pytest.approx(110 / 210, rel=1e-6)


def test_american_to_implied_positive():
    assert american_to_implied(150) == pytest.approx(100 / 250, rel=1e-6)


def test_american_to_implied_zero_rejected():
    with pytest.raises(ValueError):
        american_to_implied(0)


def test_american_to_decimal():
    assert american_to_decimal(-110) == pytest.approx(1 + 100 / 110, rel=1e-6)
    assert american_to_decimal(150) == pytest.approx(2.5, rel=1e-6)


def test_remove_vig_sums_to_one():
    over, under = remove_vig(-110, -110)
    assert over + under == pytest.approx(1.0)
    assert over == pytest.approx(0.5)


def test_remove_vig_favours_shorter_odds():
    over, under = remove_vig(-200, 150)
    assert over > under
    assert over + under == pytest.approx(1.0)


def test_score_odds_only_never_invents_form():
    s = score_prop(make_prop(), pair_odds=-110)
    assert s.has_form_data is False
    assert s.probability == pytest.approx(0.5)
    assert s.edge == 0.0
    assert "odds-only" in s.why.lower()
    assert "no recent-form data" in s.why.lower()


def test_score_with_real_form_blends_half_and_half():
    # v2 ingredient: a projection probability (from projection.py,
    # computed upstream from the player's real game log), blended
    # half-and-half with the de-vigged odds — the same blend shape
    # as v1, a different (proven) ingredient.
    s = score_prop(make_prop(), pair_odds=-110, form_probability=0.75,
                   projected_value=28.0, projection_games=9)
    # implied 0.5, projection 0.75 -> 0.625
    assert s.has_form_data is True
    assert s.probability == pytest.approx(0.625)
    assert s.edge == pytest.approx(0.125)
    assert s.form_probability == pytest.approx(0.75)
    assert "Projection: about 28 points" in s.why


def test_score_under_uses_opposite_side():
    s = score_prop(make_prop(side="Under", odds=150), pair_odds=-200)
    over, under = remove_vig(-200, 150)
    assert s.probability == pytest.approx(under)


# ---------- parlay math ----------

def test_overall_probability_is_product():
    picks = make_scored(3)
    for p in picks:
        p.probability = 0.6
    assert overall_probability(picks) == pytest.approx(0.6 ** 3)


def test_ten_60_percent_picks_is_about_0_6_percent():
    # The honest fact Lilly shows on every 10-pick card.
    picks = make_scored(10)
    for p in picks:
        p.probability = 0.60
    assert overall_probability(picks) == pytest.approx(0.0060466176, rel=1e-6)
    assert overall_probability(picks) < 0.01  # tiny, even with the best picks


def test_plan_is_the_new_shape():
    # Neko's plan (2026-11-12): 4 of 2, 4 of 3, 2 of 5 —
    # plus exactly ONE 10-pick per pull, for fun.
    assert PARLAY_PLAN == [(2, 4), (3, 4), (5, 2), (10, 1)]


def test_build_plan_counts():
    parlays, notes = build_parlays(make_scored(40))
    sizes = sorted(p.size for p in parlays)
    assert sizes == [2, 2, 2, 2, 3, 3, 3, 3, 5, 5, 10]
    assert sum(1 for p in parlays if p.size == 2) == 4
    assert sum(1 for p in parlays if p.size == 3) == 4
    assert sum(1 for p in parlays if p.size == 5) == 2
    assert sum(1 for p in parlays if p.size == 10) == 1
    # The single 10-pick is the for-fun ticket, and its
    # card keeps the honest long-shot line.
    ten = next(p for p in parlays if p.size == 10)
    assert "Long shot" in ten.plain_summary()


def test_build_picks_max_probability_combination():
    scored = make_scored(12)
    parlays, _ = build_parlays(scored, plan=[(3, 1)])
    best3 = sorted(scored, key=lambda p: p.probability, reverse=True)[:3]
    assert {p.key for p in parlays[0].picks} == {p.key for p in best3}
    assert parlays[0].overall_probability == pytest.approx(
        overall_probability(best3)
    )


def test_no_duplicate_pick_inside_a_parlay():
    scored = make_scored(30)
    # Duplicate every pick object so the pool has repeats of the same key.
    doubled = scored + [score_prop(make_prop(player=f"Player{i}", line=10.5 + i), pair_odds=-110)
                        for i in range(30)]
    parlays, _ = build_parlays(doubled)
    for parlay in parlays:
        keys = [p.key for p in parlay.picks]
        assert len(keys) == len(set(keys)), f"duplicate in {parlay.label}"
        assert len(parlay.picks) == parlay.size


def test_parlay_ev_math():
    scored = make_scored(3)
    for p in scored:
        p.probability = 0.5
        p.odds = 100  # decimal 2.0 each
    parlays, _ = build_parlays(scored, plan=[(3, 1)])
    pl = parlays[0]
    assert pl.decimal_payout == pytest.approx(8.0)
    assert pl.expected_value_per_dollar == pytest.approx(0.125 * 8.0 - 1.0)


def test_too_few_picks_builds_nothing_and_says_so():
    # The smallest ticket on the new plan is a 2-pick, so a
    # one-pick pool builds nothing — and says so.
    parlays, notes = build_parlays(make_scored(1))
    assert parlays == []
    assert notes  # a plain-words reason is returned


def test_parlay_plain_summary_warns_on_ten():
    # The plan's one for-fun 10-pick still carries the
    # honest long-shot line on its card.
    parlays, _ = build_parlays(make_scored(12), plan=[(10, 1)])
    assert "long shot" in parlays[0].plain_summary().lower()


def test_plain_summary_wording_for_the_new_sizes():
    two, _ = build_parlays(make_scored(12), plan=[(2, 1)])
    three, _ = build_parlays(make_scored(12), plan=[(3, 1)])
    five, _ = build_parlays(make_scored(12), plan=[(5, 1)])
    for parlay in (two[0], three[0], five[0]):
        assert "steadiest of the three sizes" not in parlay.plain_summary()
    assert "both picks have to land" in two[0].plain_summary()
    assert "All three picks have to land" in three[0].plain_summary()
    assert "All five picks have to land" in five[0].plain_summary()


# ---------- test before picking (form-graded selection) ----------

def _scored_with(player, prob):
    s = score_prop(make_prop(player=player), pair_odds=-110)
    s.probability = prob
    return s


def test_shaky_high_chance_leg_is_passed_over_for_lean():
    # The highest-chance leg in the pool is form-weak
    # (SHAKY); two lower-chance legs have their recent form
    # behind them (LEAN). Test-before-picking: the 2-pick
    # is built from the LEAN legs, not the SHAKY favourite.
    hot = _scored_with("HotButShaky", 0.90)
    lean_a = _scored_with("LeanA", 0.60)
    lean_b = _scored_with("LeanB", 0.59)
    verdicts = {hot.key: "SHAKY", lean_a.key: "LEAN", lean_b.key: "LEAN"}
    parlays, notes = build_parlays(
        [hot, lean_a, lean_b], plan=[(2, 1)], form_verdicts=verdicts
    )
    assert len(parlays) == 1
    assert {p.key for p in parlays[0].picks} == {lean_a.key, lean_b.key}
    # And no note claims a SHAKY leg had to be used.
    assert not any("SHAKY" in n for n in notes)


def test_strong_ranks_ahead_of_lean_ahead_of_mixed():
    strong = _scored_with("Strong", 0.70)
    lean = _scored_with("Lean", 0.70)
    mixed = _scored_with("Mixed", 0.70)
    verdicts = {strong.key: "STRONG", lean.key: "LEAN", mixed.key: "MIXED"}
    parlays, _ = build_parlays(
        [mixed, lean, strong], plan=[(2, 1)], form_verdicts=verdicts
    )
    assert {p.key for p in parlays[0].picks} == {strong.key, lean.key}


def test_no_data_is_neutral_never_punished():
    # A leg the logs don't cover (NO DATA, the best chance
    # in the pool) is picked ahead of a MIXED leg on chance
    # alone — same band — and is never treated like a
    # form-weak leg: the 0.99 SHAKY leg is still passed over.
    nodata = _scored_with("Unknown", 0.62)
    mixed = _scored_with("Mixed", 0.61)
    shaky = _scored_with("Shaky", 0.99)
    verdicts = {nodata.key: "NO DATA", mixed.key: "MIXED", shaky.key: "SHAKY"}
    parlays, _ = build_parlays(
        [shaky, mixed, nodata], plan=[(2, 1)], form_verdicts=verdicts
    )
    assert {p.key for p in parlays[0].picks} == {nodata.key, mixed.key}


def test_shaky_leg_is_used_when_the_pool_cannot_fill_without_it():
    # Only two form-backed legs exist and the ticket needs
    # three: the SHAKY leg tops it up — as a last resort,
    # and the build notes say so out loud.
    lean_a = _scored_with("LeanA", 0.60)
    lean_b = _scored_with("LeanB", 0.59)
    shaky = _scored_with("Shaky", 0.95)
    verdicts = {lean_a.key: "LEAN", lean_b.key: "LEAN", shaky.key: "SHAKY"}
    parlays, notes = build_parlays(
        [lean_a, lean_b, shaky], plan=[(3, 1)], form_verdicts=verdicts
    )
    assert len(parlays) == 1
    assert {p.key for p in parlays[0].picks} == {
        lean_a.key, lean_b.key, shaky.key
    }
    assert any("SHAKY" in n for n in notes)


def test_ungraded_pool_ranks_by_chance_as_before():
    # No verdicts at all (the pre-test behaviour): pure
    # probability order, the top of the pool fills first.
    scored = make_scored(12)
    parlays, _ = build_parlays(scored, plan=[(3, 1)])
    best3 = sorted(scored, key=lambda p: p.probability, reverse=True)[:3]
    assert {p.key for p in parlays[0].picks} == {p.key for p in best3}


# ---------- Lilly ----------

def test_lilly_verdict_bad_play_below_half():
    verdict, _ = lilly.verdict_for_pick(0.40, False, 0.0)
    assert verdict == "bad play"


def test_lilly_verdict_odds_only_is_never_good_play():
    verdict, _ = lilly.verdict_for_pick(0.60, False, 0.0)
    assert verdict == "fix this first"


def test_lilly_verdict_good_play_needs_form_edge():
    verdict, _ = lilly.verdict_for_pick(0.65, True, 0.08)
    assert verdict == "good play"


@pytest.mark.parametrize("msg", [
    "run the algorithm", "are 10 pick parlays safe", "will I win",
    "guarantee me a win", "how do the odds work", "hi", "demo mode",
    "how much should I bet", "what is the safest pick", "blah blah",
])
def test_lilly_chat_never_promises_a_win(msg):
    reply = lilly.chat_reply(msg)
    lowered = reply.lower()
    assert "guarantee" not in lowered or "never" in lowered
    assert "will win" not in lowered
    assert "sure thing" not in lowered
    assert "can't lose" not in lowered


def test_lilly_brief_never_promises_wins():
    assert "never" in lilly.BRIEF.lower()
    assert "promise" in lilly.BRIEF.lower()


def test_lilly_chat_offline_no_key_needed():
    assert lilly.chat_reply("hello")
    assert lilly.chat_reply("")


# ---------- ledger ----------

def test_ledger_records_and_settles(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    parlays, _ = build_parlays(make_scored(12), plan=[(3, 1)])
    ids = ledger.record_run(parlays, "live")
    assert len(ids) == 1
    assert ledger.stats()["total_parlays"] == 1
    assert ledger.stats()["pick_hit_rate"] is None  # nothing settled yet

    pick_key = ledger.parlays[0].picks[0]["key"]
    assert ledger.settle_pick(ids[0], pick_key, True) is True
    assert ledger.settle_pick(ids[0], "no-such-key", True) is False
    stats = ledger.stats()
    assert stats["settled_picks"] == 1
    assert stats["picks_won"] == 1
    assert stats["pick_hit_rate"] == pytest.approx(1.0)


def test_ledger_hit_rate_by_size(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    parlays, _ = build_parlays(make_scored(20), plan=[(3, 1), (5, 1)])
    ledger.record_run(parlays, "live")
    for lp in ledger.parlays:
        for pick in lp.picks:
            ledger.settle_pick(lp.id, pick["key"], True)
    stats = ledger.stats()
    assert stats["parlay_hit_rate"] == pytest.approx(1.0)
    assert stats["by_size"][3]["hit_rate"] == pytest.approx(1.0)
    assert stats["by_size"][5]["hit_rate"] == pytest.approx(1.0)
    assert stats["by_size"][10]["hit_rate"] is None  # none settled


def test_ledger_persists_across_reload(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = Ledger(path)
    parlays, _ = build_parlays(make_scored(12), plan=[(3, 1)])
    ledger.record_run(parlays, "live")
    ledger2 = Ledger(path)
    assert ledger2.stats()["total_parlays"] == 1


# ---------- full plan builds live ----------

def test_full_plan_builds_and_records_live(tmp_path):
    # The full 11-parlay plan from a live-scored board, and
    # the ledger records the run as live. (Demo mode was
    # removed 2026-11-12 — the app only ever builds live.)
    scored = make_scored(30)
    parlays, _ = build_parlays(scored)
    assert len(parlays) == 11
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record_run(parlays, "live")
    assert ledger.parlays[0].mode == "live"


# ---------- odds api parsing ----------

def test_parse_event_odds_player_in_description():
    event = {
        "home_team": "Lakers", "away_team": "Celtics",
        "commence_time": "2026-11-10T19:00:00Z",
        "bookmakers": [{
            "key": "draftkings", "title": "DraftKings",
            "markets": [{
                "key": "player_points",
                "outcomes": [
                    {"description": "LeBron James", "name": "Over", "point": 24.5, "price": -110},
                    {"description": "LeBron James", "name": "Under", "point": 24.5, "price": -110},
                ],
            }],
        }],
    }
    props = _parse_event_odds(event, "NBA", ("draftkings",))
    assert len(props) == 2
    assert props[0].player == "LeBron James"
    assert props[0].line == 24.5
    assert props[0].source == "live"


def test_parse_event_odds_keeps_best_price():
    event = {
        "home_team": "A", "away_team": "B", "commence_time": "",
        "bookmakers": [
            {"key": "draftkings", "title": "DraftKings", "markets": [
                {"key": "player_points", "outcomes": [
                    {"description": "Star", "name": "Over", "point": 20.5, "price": -120}]}]},
            {"key": "fanduel", "title": "FanDuel", "markets": [
                {"key": "player_points", "outcomes": [
                    {"description": "Star", "name": "Over", "point": 20.5, "price": -105}]}]},
        ],
    }
    props = _parse_event_odds(event, "NBA", ("draftkings", "fanduel"))
    assert len(props) == 1
    assert props[0].odds == -105  # best payout wins
    assert props[0].bookmaker == "FanDuel"
