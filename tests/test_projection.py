"""Tests for kings_picks/projection.py — fixture logs only,
no network. Every expected number below is worked out from the
constants documented in projection.py, so a silent change to
the model's recipe fails loudly here.
"""

import math
import random

import pytest

from kings_picks import projection
from kings_picks.projection import (
    MIN_GAMES,
    PROB_CEIL,
    PROB_FLOOR,
    WEIGHT_LAST5,
    WEIGHT_LAST10,
    WEIGHT_SEASON,
    over_probability,
    project_stat,
)


# ------------------------------------------------------------------
# Projection weighting
# ------------------------------------------------------------------

def test_weights_sum_to_one():
    assert WEIGHT_SEASON + WEIGHT_LAST10 + WEIGHT_LAST5 == pytest.approx(1.0)


def test_projection_combines_season_last10_last5():
    # Newest first: five 30s (hot recent form), then six 10s.
    values = [30.0] * 5 + [10.0] * 6
    proj = project_stat(values)
    assert proj is not None
    season_avg = (5 * 30 + 6 * 10) / 11
    last10_avg = (5 * 30 + 5 * 10) / 10
    last5_avg = 30.0
    expected = (
        WEIGHT_SEASON * season_avg
        + WEIGHT_LAST10 * last10_avg
        + WEIGHT_LAST5 * last5_avg
    )
    assert proj.expected == pytest.approx(expected)
    assert proj.games == 11
    # Recency-aware: the projection leans toward the hot last 5,
    # landing above the last-10 average but below the last-5 average.
    assert last10_avg < proj.expected < last5_avg


def test_projection_flat_log_projects_the_flat_value():
    proj = project_stat([12.0] * 7)
    assert proj is not None
    assert proj.expected == pytest.approx(12.0)
    assert proj.games == 7


# ------------------------------------------------------------------
# Minimum sample
# ------------------------------------------------------------------

def test_too_few_games_returns_none():
    assert project_stat([10.0, 12.0, 9.0, 11.0]) is None  # 4 < MIN_GAMES
    assert over_probability([10.0, 12.0, 9.0, 11.0], 10.5) is None
    assert project_stat([]) is None
    assert over_probability([], 10.5) is None


def test_exactly_min_games_works():
    values = [10.0] * MIN_GAMES
    assert project_stat(values) is not None
    assert over_probability(values, 10.5) is not None


# ------------------------------------------------------------------
# Spread: shrinkage and floors
# ------------------------------------------------------------------

def test_constant_log_spread_comes_from_shrinkage_not_zero():
    # A metronome log has raw sd 0; the spread must still be the
    # shrunken typical-CV spread, never 0.
    proj = project_stat([10.0] * 8)
    assert proj is not None
    shrunk_cv = (8 * 0.0 + projection.CV_PRIOR_GAMES * projection.TYPICAL_CV) / (
        8 + projection.CV_PRIOR_GAMES
    )
    assert proj.spread == pytest.approx(shrunk_cv * 10.0)
    assert proj.spread > 0


def test_spread_absolute_floor_for_zero_log():
    # Never recorded the stat: projection 0, spread is the floor.
    proj = project_stat([0.0] * 6)
    assert proj is not None
    assert proj.expected == 0.0
    assert proj.spread == pytest.approx(projection.MIN_SPREAD_ABS)


def test_spread_grows_with_volatility():
    steady = project_stat([20.0, 21.0, 19.0, 20.0, 21.0, 19.0, 20.0, 20.0])
    wild = project_stat([35.0, 5.0, 30.0, 10.0, 38.0, 2.0, 28.0, 12.0])
    assert steady is not None and wild is not None
    assert wild.spread > steady.spread


# ------------------------------------------------------------------
# Over / Under symmetry
# ------------------------------------------------------------------

@pytest.mark.parametrize(
    "values, line",
    [
        ([22.0, 18.0, 25.0, 20.0, 24.0, 19.0, 21.0, 23.0], 21.5),
        ([22.0, 18.0, 25.0, 20.0, 24.0, 19.0, 21.0, 23.0], 21.0),  # whole line
        ([300.0, 250.0, 280.0, 310.0, 265.0, 290.0], 275.5),
        ([1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0], 0.5),  # binary
    ],
)
def test_over_and_under_add_to_one(values, line):
    over = over_probability(values, line, "Over")
    under = over_probability(values, line, "Under")
    assert over is not None and under is not None
    assert over + under == pytest.approx(1.0)


def test_bad_side_rejected():
    with pytest.raises(ValueError):
        over_probability([10.0] * 6, 10.5, "over")


# ------------------------------------------------------------------
# Push handling (whole-number line, integer stat)
# ------------------------------------------------------------------

def test_landing_exactly_on_the_line_is_a_coin_flip():
    # Projection sits exactly on a whole-number line: the push
    # slice is split half/half, so Over must be exactly 50%.
    p = over_probability([10.0] * 8, 10.0, "Over")
    assert p == pytest.approx(0.5)


def test_push_slice_gets_half_credit():
    # Centred on 10: Over 10 (with half the push slice) must beat
    # Over 10.5 (no slice at all) and lose to Over 9.5.
    values = [10.0] * 8
    p10 = over_probability(values, 10.0)
    p105 = over_probability(values, 10.5)
    p95 = over_probability(values, 9.5)
    assert p95 > p10 > p105


def test_half_point_line_has_no_push_effect():
    # A half-point line splits the distribution cleanly: centred
    # on 10, Over 9.5 and Under 9.5 mirror around the mass above
    # and below 9.5 — Over must exceed 50% by exactly the slice
    # between 9.5 and 10.
    p_over = over_probability([10.0] * 8, 9.5, "Over")
    assert p_over is not None and p_over > 0.5


# ------------------------------------------------------------------
# Anytime Touchdown (binary log)
# ------------------------------------------------------------------

def test_anytime_td_uses_smoothed_rate():
    # 6 touchdowns in 10 games, line 0.5: (6 + 1) / (10 + 2).
    values = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0]
    p = over_probability(values, 0.5, "Over")
    assert p == pytest.approx(7 / 12)
    assert over_probability(values, 0.5, "Under") == pytest.approx(5 / 12)


def test_anytime_td_perfect_start_is_not_100_percent():
    p = over_probability([1.0] * 5, 0.5, "Over")
    assert p == pytest.approx(6 / 7)  # smoothed — never certainty
    assert p < 1.0


def test_binary_line_zero_counts_pushes_as_half():
    # Line 0: scoring beats it, not scoring lands ON it (push).
    # above=3, equal=2 -> (3 + 0.5*2 + 1) / (5 + 2) = 5/7.
    values = [1.0, 1.0, 0.0, 0.0, 1.0]
    assert over_probability(values, 0.0, "Over") == pytest.approx(5 / 7)


# ------------------------------------------------------------------
# Probability clamps
# ------------------------------------------------------------------

def test_probabilities_never_reach_zero_or_one():
    assert over_probability([40.0] * 10, 10.0) == pytest.approx(PROB_CEIL)
    assert over_probability([2.0] * 10, 30.0) == pytest.approx(PROB_FLOOR)


# ------------------------------------------------------------------
# Synthetic calibration: probabilities must match reality
# ------------------------------------------------------------------

def _walk_forward(games, start):
    """Predict each game from `start` on using only earlier
    games, against a line set at the prior-games average
    rounded to the nearest 0.5 (the calibration harness's
    synthetic line). Returns (predicted, outcomes)."""
    predicted, outcomes = [], []
    for i in range(start, len(games)):
        prior = games[:i]
        line = math.floor((sum(prior) / len(prior)) * 2 + 0.5) / 2
        p = over_probability(list(reversed(prior)), line, "Over")
        assert p is not None
        actual = games[i]
        outcomes.append(1.0 if actual > line else 0.0 if actual < line else 0.5)
        predicted.append(p)
    return predicted, outcomes


def test_synthetic_stationary_player_is_roughly_calibrated():
    """A player whose true game never changes, Normal(20, 5).
    There is almost no signal to find here — the line sits at
    his own average — so the honest check is narrower: his
    predicted chances must average out near what actually
    happened, and the Brier score must stay in a sane band
    (a model spouting garbage probabilities would blow past
    it). Beating the always-50% forecaster in a no-signal
    scenario is NOT claimed — nothing should."""
    rng = random.Random(20261112)
    games = [max(0, round(rng.gauss(20, 5))) for _ in range(120)]
    predicted, outcomes = _walk_forward(games, 30)

    assert len(predicted) == 90
    mean_pred = sum(predicted) / len(predicted)
    mean_outcome = sum(outcomes) / len(outcomes)
    assert abs(mean_pred - mean_outcome) < 0.08
    brier = sum((p - o) ** 2 for p, o in zip(predicted, outcomes)) / len(predicted)
    assert brier < 0.28


def test_synthetic_role_change_is_noticed():
    """A player whose role grows mid-season: 60 games around
    16, then 60 around 26. The line (his full prior average)
    lags behind his new level, so a recency-aware model must
    call the Over well above 50% in the new-role stretch and
    score far better than the always-50% forecaster. It stays
    conservative — the season anchor keeps some of the old
    role in the projection — so it may under-call, but never
    by more than 20 points on average."""
    rng = random.Random(7)
    games = [max(0, round(rng.gauss(16, 4))) for _ in range(60)] + [
        max(0, round(rng.gauss(26, 4))) for _ in range(60)
    ]
    predicted, outcomes = _walk_forward(games, 70)

    mean_pred = sum(predicted) / len(predicted)
    mean_outcome = sum(outcomes) / len(outcomes)
    assert mean_pred > 0.60
    assert abs(mean_pred - mean_outcome) < 0.20
    brier = sum((p - o) ** 2 for p, o in zip(predicted, outcomes)) / len(predicted)
    brier_5050 = sum((0.5 - o) ** 2 for o in outcomes) / len(outcomes)
    assert brier < 0.75 * brier_5050
