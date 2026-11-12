"""Lilly's projection model (v2) — true chances from a
player's own games.

The recipe, in plain words:

1. PROJECT the player's next game. Look at his games this
   season three ways — his whole-season average, his last-10
   average, and his last-5 average — and mix them. Recent
   games get the biggest vote because players change (roles,
   health, teams), but the full season still gets a real vote
   so one hot or cold week can't drag the number all the way.
2. Measure his WIGGLE. Every player bounces around his own
   average: a steady player barely moves, a boom-or-bust
   player swings wildly. We measure his own game-to-game
   spread (standard deviation). Two players with the same
   average but different wiggle are NOT the same bet, and
   this is where the model learns that.
3. Turn projection + wiggle into a CHANCE. If a player
   projects at 24 points, wiggles by about 6, and the line
   is 21.5, the chance he goes over is: how much of his
   normal bell curve sits above 21.5? That is the whole
   trick — the same bell-curve math used everywhere from
   weather forecasts to factory quality control.

What this module does NOT do: it never looks at bookmaker
odds, and it never claims an "edge". It estimates a true
probability from the player's own recorded games. Comparing
this number against the bookmaker's price is a separate
step (the picking step), not this module's job. Lilly never
promises wins — a 60% chance still loses 4 times in 10.

Special cases, handled honestly:

- PUSHES. Most of these stats are whole numbers (you can't
  score 24.3 points). If the line is a whole number too, the
  player can land exactly on it — a push, stake back. We
  count landing exactly on the line as half a win, half a
  loss, instead of pretending it can't happen. Half-point
  lines (21.5) can't push, so no adjustment there.
- ANYTIME TOUCHDOWN is a yes/no stat (he scores or he
  doesn't — the log is all 0s and 1s). A bell curve makes no
  sense for that, so we use his smoothed scoring rate
  instead: his touchdowns per game, pulled slightly toward
  "we're not sure yet" so a tiny hot streak can't produce a
  silly number.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

# ------------------------------------------------------------------
# The constants. Every one of these was picked BEFORE testing the
# model against real data (one documented scheme, chosen a priori)
# and each is explained so the math can be defended in plain words.
# ------------------------------------------------------------------

# A projection needs a real history behind it. Fewer games than
# this and we honestly don't know the player yet — return None.
MIN_GAMES = 5

# The projection mix (must add up to 1.0):
# - the last 5 games say the most about the player RIGHT NOW
#   (current role, current health), so they get the biggest vote;
# - the last 10 soften that — one weird week shows up less;
# - the whole season anchors it, so a short streak can't take over.
WEIGHT_LAST5 = 0.40
WEIGHT_LAST10 = 0.30
WEIGHT_SEASON = 0.30

# How much a "typical" player's numbers wiggle, as a share of his
# own average: about 40%. (Steady scorers sit near 25-30%, volatile
# players run 50%+; 40% is the middle of the road for the counting
# stats Lilly prices.) Used only to steady SMALL samples: a spread
# measured from 5 games is jumpy, so we blend it toward this
# typical value as if we had also seen a few typical games.
TYPICAL_CV = 0.40
CV_PRIOR_GAMES = 5  # the "few typical games" in that blend

# Floors on the measured wiggle, so the model never claims a player
# is a robot: the spread is at least half a unit, and at least 10%
# of the projection, no matter how metronome-steady his log looks.
MIN_SPREAD_ABS = 0.5
MIN_SPREAD_FRAC = 0.10

# The model never says 0% or 100%. Even the surest-looking pick
# keeps a sliver of doubt, and the longest shot keeps a sliver of
# hope — that is simply true of real games.
PROB_FLOOR = 0.01
PROB_CEIL = 0.99


@dataclass
class Projection:
    """One player's projected next game for one stat."""

    expected: float  # projected value (e.g. 24.3 points)
    spread: float  # his game-to-game wiggle around it (std dev)
    games: int  # how many real games this is built from


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def project_stat(values_newest_first) -> Projection | None:
    """Project a player's next game from his log.

    values_newest_first: his game values for one stat, most
    recent game first (the shape game_logs.py stores).
    Returns None when there are fewer than MIN_GAMES games —
    not enough history to say anything honest.
    """
    values = [float(v) for v in values_newest_first]
    n = len(values)
    if n < MIN_GAMES:
        return None

    season_avg = _mean(values)
    last10_avg = _mean(values[:10])
    last5_avg = _mean(values[:5])
    expected = (
        WEIGHT_SEASON * season_avg
        + WEIGHT_LAST10 * last10_avg
        + WEIGHT_LAST5 * last5_avg
    )

    # ---- his wiggle (spread) ----
    # Raw spread from his full log. With 2+ games statistics.stdev
    # is the sample standard deviation; MIN_GAMES >= 5 guarantees
    # we always have enough points for it here.
    raw_sd = statistics.stdev(values) if n >= 2 else 0.0
    if season_avg > 0:
        raw_cv = raw_sd / season_avg
    else:
        # He has never recorded this stat at all — there is no
        # wiggle to measure, so fall back to the typical one.
        raw_cv = TYPICAL_CV
    # Steady the small-sample wiggle: pretend we also watched
    # CV_PRIOR_GAMES extra games of a perfectly typical player.
    shrunk_cv = (n * raw_cv + CV_PRIOR_GAMES * TYPICAL_CV) / (n + CV_PRIOR_GAMES)
    spread = shrunk_cv * expected
    # Floors: never a robot, never a zero-width guess.
    spread = max(spread, MIN_SPREAD_ABS, MIN_SPREAD_FRAC * abs(expected))

    return Projection(expected=expected, spread=spread, games=n)


def _phi(z: float) -> float:
    """The standard normal bell curve's cumulative chance:
    the share of the curve below z."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _is_binary(values: list[float]) -> bool:
    """A yes/no stat (Anytime Touchdown) has a log of only 0s
    and 1s. Detected from the data itself, not from a name."""
    return all(v in (0.0, 1.0) for v in values)


def _binary_over_probability(values: list[float], line: float) -> float:
    """Smoothed scoring rate for a yes/no stat.

    Counts how often he beat the line, with pushes (landing
    exactly on the line — only possible at line 0 or 1 here)
    counted as half, then smooths with one phantom make and
    one phantom miss (a Beta(1,1) prior) so 3-for-3 to start a
    season reads as 4/5 = 80%, not a reckless 100%.
    """
    above = sum(1 for v in values if v > line)
    equal = sum(1 for v in values if v == line)
    return (above + 0.5 * equal + 1.0) / (len(values) + 2.0)


def _normal_over_probability(proj: Projection, line: float) -> float:
    """Chance the player's next game lands OVER `line`, from
    the bell curve centred on his projection with his wiggle."""
    mu, sd = proj.expected, proj.spread
    line_is_whole = abs(line - round(line)) < 1e-9
    if line_is_whole:
        # Whole-number stat + whole-number line = pushes are
        # real. A whole-number outcome k "lives" in the slice
        # [k - 0.5, k + 0.5) of the curve (continuity
        # correction). Over wins above line + 0.5; landing
        # exactly on the line takes half its slice.
        p_above = 1.0 - _phi((line + 0.5 - mu) / sd)
        p_push = _phi((line + 0.5 - mu) / sd) - _phi((line - 0.5 - mu) / sd)
        return p_above + 0.5 * p_push
    # Half-point lines can't push — the plain curve at the line.
    # (Any other fractional line also lands here; real lines are
    # whole or half, so this is the honest general fallback.)
    return 1.0 - _phi((line - mu) / sd)


def over_probability(values_newest_first, line: float, side: str = "Over") -> float | None:
    """Lilly's v2 chance that the player's next game goes
    `side` of `line` for one stat, from his own game log.

    Returns None when the log has fewer than MIN_GAMES games.
    Under is exactly 1 - Over: the push slice, when there is
    one, is already split half-and-half inside the Over number,
    so the two sides always add up to 100%.
    """
    if side not in ("Over", "Under"):
        raise ValueError(f"side must be 'Over' or 'Under', got {side!r}")
    values = [float(v) for v in values_newest_first]
    if len(values) < MIN_GAMES:
        return None

    if _is_binary(values):
        p_over = _binary_over_probability(values, float(line))
    else:
        proj = project_stat(values)
        if proj is None:  # guarded by the length check; kept for safety
            return None
        p_over = _normal_over_probability(proj, float(line))

    p_over = min(max(p_over, PROB_FLOOR), PROB_CEIL)
    return p_over if side == "Over" else 1.0 - p_over
