"""Tests for the player breakdown (Part 1) — pure math and
plain-English reasons from signals the app already has.

Run: pytest -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import breakdown
from kings_picks.game_logs import FormSummary
from kings_picks.model import Prop, score_prop
from kings_picks.parlays import build_parlays


def scored(player="Star", sport="NBA", prop="Points", side="Over",
           line=24.5, odds=-110, pair=-110, prob=None, bookmaker="DraftKings"):
    p = Prop(player=player, sport=sport, prop=prop, side=side, line=line,
             odds=odds, bookmaker=bookmaker, event="A vs B")
    s = score_prop(p, pair_odds=pair)
    if prob is not None:
        s.probability = prob
    return s


# ---------- vig / fair chance / book chance ----------

def test_book_chance_is_raw_implied():
    assert abs(breakdown.book_chance(-110) - 110 / 210) < 1e-9
    assert abs(breakdown.book_chance(150) - 100 / 250) < 1e-9


def test_pair_vig_even_pair():
    # -110/-110: both sides imply 52.38%, total 104.76% -> 4.76 pts of cut.
    vig = breakdown.pair_vig(-110, -110)
    assert abs(vig - (2 * 110 / 210 - 1)) < 1e-9


def test_pair_vig_none_without_pair():
    assert breakdown.pair_vig(-110, None) is None


def test_fair_chance_removes_vig():
    assert abs(breakdown.fair_chance(-110, "Over", -110) - 0.5) < 1e-9
    # Without a pair, fair chance falls back to the sticker chance.
    assert breakdown.fair_chance(-110, "Over", None) == breakdown.book_chance(-110)


def test_fair_chance_under_side():
    # Over -150 / Under +130: fair Under = (100/230) / (150/250 + 100/230).
    fair = breakdown.fair_chance(130, "Under", -150)
    expected = (100 / 230) / (150 / 250 + 100 / 230)
    assert abs(fair - expected) < 1e-9


# ---------- pair map ----------

def test_pair_map_collects_both_sides():
    props = [
        Prop(player="Star", sport="NBA", prop="Points", side="Over", line=24.5, odds=-110),
        Prop(player="Star", sport="NBA", prop="Points", side="Under", line=24.5, odds=-120),
        Prop(player="Star", sport="NBA", prop="Assists", side="Over", line=6.5, odds=-105),
    ]
    pairs = breakdown.pair_map(props)
    assert pairs[("Star", "NBA", "Points", 24.5)] == {"Over": -110, "Under": -120}
    assert pairs[("Star", "NBA", "Assists", 6.5)] == {"Over": -105}


# ---------- slate bands + rank ----------

def test_chance_bands_count_and_boundaries():
    slate = [
        scored(player="A", prob=0.49),
        scored(player="B", prob=0.50),   # boundary: lands in 50-55
        scored(player="C", prob=0.54),
        scored(player="D", prob=0.55),   # boundary: lands in 55-60
        scored(player="E", prob=0.61),
        scored(player="F", prob=0.75),
    ]
    bands = dict(breakdown.chance_bands(slate))
    assert bands == {"Under 50%": 1, "50–55%": 2, "55–60%": 1, "60% or more": 2}


def test_chance_bands_empty_slate():
    assert dict(breakdown.chance_bands([])) == {
        "Under 50%": 0, "50–55%": 0, "55–60%": 0, "60% or more": 0,
    }


def test_slate_rank():
    slate = [scored(player="A", prob=0.70), scored(player="B", prob=0.60),
             scored(player="C", prob=0.55)]
    assert breakdown.slate_rank(slate[0], slate) == 1
    assert breakdown.slate_rank(slate[2], slate) == 3


# ---------- chart data ----------

def test_chance_chart_rows_are_percents():
    rows = breakdown.chance_chart_rows(0.58, 0.61)
    assert rows[0] == {"who": "Lilly's chance", "chance_pct": 58.0}
    assert rows[1] == {"who": "Book's sticker chance", "chance_pct": 61.0}


def test_form_chart_rows_oldest_first():
    rows = breakdown.form_chart_rows([30.0, 20.0, 10.0])  # newest first in
    assert [r["value"] for r in rows] == [10.0, 20.0, 30.0]
    assert rows[-1]["game"] == "Last game"


# ---------- parlays containing ----------

def test_parlays_containing():
    slate = [scored(player=f"P{i}", line=10.5 + i, prob=0.70 - i * 0.01) for i in range(6)]
    parlays, _ = build_parlays(slate, plan=[(3, 1)])
    assert len(parlays) == 1
    top_key = slate[0].key
    assert breakdown.parlays_containing(top_key, parlays) == [parlays[0].label]
    assert breakdown.parlays_containing("nobody|NBA|Points|1.5", parlays) == []


# ---------- build_breakdown reasons ----------

def test_breakdown_odds_only_is_honest_about_the_gap():
    pick = scored(prob=0.58)
    card = breakdown.build_breakdown(pick, [pick], [], pair_odds=-110)
    text = " ".join(card.reasons)
    assert "bookmaker's cut" in text
    assert "No recent-form data behind this pick" in text
    assert "not read" in text or "do not read" in text
    assert abs(card.book_chance - 110 / 210) < 1e-9
    assert card.lilly_chance == 0.58
    # Lilly 58% vs sticker 52.4% -> +5.6 points.
    assert abs(card.edge_points - (0.58 - 110 / 210) * 100) < 0.01
    assert card.strength == "mixed"


def test_breakdown_no_pair_says_cut_unmeasured():
    pick = scored(pair=None)
    card = breakdown.build_breakdown(pick, [pick], [], pair_odds=None)
    text = " ".join(card.reasons)
    assert "could not be measured" in text
    assert card.vig is None


def test_breakdown_coin_flip_pair_called_out():
    pick = scored(prob=0.52)
    card = breakdown.build_breakdown(pick, [pick], [], pair_odds=-110)
    assert any("coin flip" in r for r in card.reasons)


def test_breakdown_books_lean_the_other_way():
    # Pick the Over at +150 while the Under is -170: books favour Under.
    pick = scored(odds=150, pair=-170, prob=0.42)
    card = breakdown.build_breakdown(pick, [pick], [], pair_odds=-170)
    assert any("lean the other way" in r for r in card.reasons)


def test_breakdown_weak_pick_warns_plainly():
    pick = scored(prob=0.44)
    card = breakdown.build_breakdown(pick, [pick], [], pair_odds=-110)
    assert card.strength == "weak"
    assert any("less than half the time" in r for r in card.reasons)


def test_breakdown_rank_and_parlay_lines():
    slate = [scored(player=f"P{i}", line=10.5 + i, prob=0.70 - i * 0.02) for i in range(6)]
    parlays, _ = build_parlays(slate, plan=[(3, 1)])
    top = breakdown.build_breakdown(slate[0], slate, parlays, pair_odds=-110)
    assert top.rank == 1
    assert any("#1 prop" in r for r in top.reasons)
    assert any("put this pick in" in r for r in top.reasons)
    last = breakdown.build_breakdown(slate[-1], slate, parlays, pair_odds=-110)
    assert any("did not make any of today's parlays" in r for r in last.reasons)
    assert any("bottom half" in r for r in last.reasons)


def test_breakdown_with_form_explains_the_move():
    pick = scored(prob=0.64)
    form = FormSummary(values=[30.0, 28.0, 12.0, 26.0, 31.0], hits=4, games=5,
                       last5_avg=25.4, season_avg=24.0,
                       source_label="nflverse (free, updated weekly)")
    card = breakdown.build_breakdown(pick, [pick], [], pair_odds=-110, form=form)
    text = " ".join(card.reasons)
    assert "nflverse" in text
    assert "4 of his last 5 games" in text
    assert "supporting" in text and "evidence only" in text
    assert "No recent-form data" not in text
    assert card.strength == "strong"


def test_breakdown_form_against_the_pick_warns():
    pick = scored(prob=0.55)
    form = FormSummary(values=[10.0, 12.0, 30.0, 8.0, 9.0, 11.0, 14.0, 7.0, 13.0, 6.0],
                       hits=1, games=10, last5_avg=13.8, season_avg=12.0,
                       source_label="nflverse (free, updated weekly)")
    card = breakdown.build_breakdown(pick, [pick], [], pair_odds=-110, form=form)
    assert any("argue AGAINST" in r for r in card.reasons)
    assert card.strength == "mixed"
