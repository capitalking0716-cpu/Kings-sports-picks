"""Parlay builder — the core output.

Each run builds (Neko's plan, set 2026-11-12):
  - 4 parlays of 2 picks
  - 4 parlays of 3 picks
  - 2 parlays of 5 picks
  - 1 parlay of 10 picks — the for-fun ticket

The 10-pick is back by King's own word ("one 10 pic each
pull for fun, u never know"), exactly one per pull, and
its card keeps the honest long-shot line it always had:
it is a lottery ticket and it says so. The working set is
the 2s, 3s and 5s.

TEST BEFORE PICKING (Neko's order, 2026-11-12: "have her
test them before picking"): on a live pull, every candidate
leg is graded BEFORE the parlays are built, against the
player's own recent game-log form — in his last 10 logged
games, how many times would this exact side of this exact
line have hit (kings_picks/self_test.py; pushes count
neither way, rates are over decided games only). The
builder ranks by that grade, in plain words:

  - Legs whose form backs them pick first: STRONG, then
    LEAN. Within the same band, the higher-chance leg goes
    first, exactly as before.
  - Legs with middling form (MIXED) come next, on level
    footing with legs the logs don't cover at all (NO
    DATA): a missing game log is never held against a pick.
  - A leg whose recent form is weak — SHAKY, it hit 40%
    or less of its decided last-10 games — is used ONLY
    when the rest of the pool cannot fill the plan without
    it. It is a last resort, never a regular.

A pick never appears twice in the same parlay, but the
same pick can ride on more than one parlay (different
tickets can share a leg; that is normal).

The honest fact, shown on every parlay: even the BEST
5-pick parlay is no sure thing. Five picks that each win
60% of the time multiply to 0.6^5 = about 8% overall —
and the one 10-pick multiplies ten of them, down under
1%. Shorter tickets win more often and pay less; Lilly
says the true chance out loud on every card. A parlay is
not a safe bet — it is a lottery ticket with better odds
than most.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from kings_picks.model import ScoredProp, american_to_decimal

# (size, how many) — the shape Neko asked for (2026-11-12:
# 4 of 2 picks, 4 of 3 picks, 2 of 5 picks, plus exactly
# ONE 10-pick per pull, for fun).
PARLAY_PLAN = [(2, 4), (3, 4), (5, 2), (10, 1)]

# Form bands for test-before-picking, best first. The
# verdict strings are kings_picks/self_test.py's — the
# builder keys off the plain strings so it never has to
# import the grading module. MIXED and NO DATA share the
# neutral band on purpose: missing logs are not bad form.
_FORM_TIERS = {"STRONG": 0, "LEAN": 1, "MIXED": 2, "NO DATA": 2, "SHAKY": 3}
_NEUTRAL_TIER = 2
_SHAKY_TIER = 3


def form_tier(verdict) -> int:
    """The selection band for one leg's form verdict.

    "" / None / an unknown tag reads as the neutral band —
    a leg that was never graded is treated exactly like a
    leg the logs don't cover: no boost, no punishment.
    """
    return _FORM_TIERS.get(verdict or "", _NEUTRAL_TIER)


@dataclass
class Parlay:
    size: int
    picks: list  # list[ScoredProp]
    overall_probability: float = 0.0
    decimal_payout: float = 0.0
    expected_value_per_dollar: float = 0.0
    label: str = ""
    # Which line style the legs are played at: "standard"
    # (the books' main lines — everything this builder makes)
    # or "king" (Neko's dropped-line style, built by
    # kings_picks/king_style.py as counterparts of these).
    # The pick log stamps the tag onto every logged leg, so
    # Lilly's record can be split by style later.
    style: str = "standard"

    @property
    def one_in(self) -> float:
        """'1 in X' — easier to feel than 0.6%."""
        if self.overall_probability <= 0:
            return float("inf")
        return 1.0 / self.overall_probability

    def plain_summary(self) -> str:
        """One plain-words line about what this parlay really is."""
        if self.size >= 10:
            # The one for-fun ticket (and any 10-pick still
            # sitting in an older stored slate) keeps the
            # long-shot line it always had — plainly a
            # lottery ticket, never dressed up as a plan.
            return (
                f"Long shot. Even the best 10 picks only win about "
                f"{self.overall_probability:.1%} of the time — roughly "
                f"1 in {self.one_in:.0f}. Fun to track, not a way to make "
                f"steady money."
            )
        if self.size >= 5:
            return (
                f"Tough. All five picks have to land — wins about "
                f"{self.overall_probability:.1%} of the time — roughly "
                f"1 in {self.one_in:.0f}."
            )
        if self.size == 3:
            return (
                f"All three picks have to land. Wins about "
                f"{self.overall_probability:.1%} of the time — roughly "
                f"1 in {self.one_in:.0f}."
            )
        return (
            f"The shortest ticket Lilly builds — both picks have to "
            f"land. Wins about {self.overall_probability:.1%} of the "
            f"time — roughly 1 in {self.one_in:.0f}."
        )


def overall_probability(picks: list) -> float:
    """Product of the individual pick chances."""
    p = 1.0
    for pick in picks:
        p *= pick.probability
    return p


def combined_decimal_payout(picks: list) -> float:
    """What $1 staked returns if every leg wins (decimal odds)."""
    payout = 1.0
    for pick in picks:
        payout *= american_to_decimal(pick.odds)
    return payout


def build_parlays(scored: list, plan: list = None, form_verdicts: dict = None) -> tuple[list, list]:
    """Build the parlays. Returns (parlays, notes).

    form_verdicts: {pick key: self-test verdict} from the
    pull's test-before-picking pass (kings_picks/
    self_test.py). The pool is ranked by form band first
    (see the module docstring), then by probability inside
    a band. SHAKY legs sit in a reserve pool and only top
    up a parlay the form-backed pool cannot fill on its
    own. With no verdicts at all, every leg reads neutral
    and the ranking is pure probability, as it was before
    the form test existed.

    Each parlay takes the best picks not already used in
    THAT parlay — so a pick never appears twice in one
    parlay, but strong picks can appear in more than one
    parlay (different tickets can share a leg; that is
    normal).

    If there are not enough picks for the full plan, fewer
    parlays are built and a note says so plainly.
    """
    plan = plan or PARLAY_PLAN
    notes: list[str] = []
    verdicts = form_verdicts or {}

    def tier_of(pick) -> int:
        return form_tier(verdicts.get(pick.key, getattr(pick, "form_verdict", "")))

    ranked = sorted(scored, key=lambda p: p.probability, reverse=True)
    # No duplicate pick inside a parlay: dedupe the pool by key,
    # keeping the best-chance version of each pick.
    seen: dict[str, ScoredProp] = {}
    for pick in ranked:
        if pick.key not in seen:
            seen[pick.key] = pick
    pool = sorted(seen.values(), key=lambda p: (tier_of(p), -p.probability))
    backing = [p for p in pool if tier_of(p) < _SHAKY_TIER]
    reserve = [p for p in pool if tier_of(p) >= _SHAKY_TIER]

    if not pool:
        return [], ["No picks to build from. Pull live props first."]
    smallest = min(size for size, _ in plan)
    if len(pool) < smallest:
        return [], [f"Only {len(pool)} pick(s) available — need at least {smallest} to build a parlay."]

    def _fill(source, cursor, count, used):
        """Take up to `count` picks from `source`, in pool
        order from `cursor` (wrapping), skipping keys already
        in `used`. Returns (legs, new_cursor)."""
        legs: list = []
        if not source or count <= 0:
            return legs, cursor
        guard = 0
        while len(legs) < count and guard < len(source) * 2:
            candidate = source[cursor % len(source)]
            cursor += 1
            guard += 1
            if candidate.key not in used:
                legs.append(candidate)
                used.add(candidate.key)
        return legs, cursor

    parlays: list[Parlay] = []
    main_cursor = 0
    reserve_cursor = 0
    reserve_used = 0
    for size, count in plan:
        for i in range(count):
            used: set = set()
            legs, main_cursor = _fill(backing, main_cursor, size, used)
            if len(legs) < size:
                # The form-backed pool cannot fill this
                # parlay alone — only now may form-weak
                # (SHAKY) legs top it up.
                extra, reserve_cursor = _fill(
                    reserve, reserve_cursor, size - len(legs), used
                )
                legs += extra
                reserve_used += len(extra)
            if len(legs) < size:
                notes.append(
                    f"Only {len(legs)} legs available for a {size}-pick parlay — skipped it."
                )
                continue
            prob = overall_probability(legs)
            payout = combined_decimal_payout(legs)
            parlays.append(
                Parlay(
                    size=size,
                    picks=legs,
                    overall_probability=prob,
                    decimal_payout=payout,
                    expected_value_per_dollar=prob * payout - 1.0,
                    label=f"{size}-pick parlay #{i + 1}",
                )
            )
    if reserve_used:
        notes.append(
            f"{reserve_used} form-weak (SHAKY) leg(s) were used only "
            "because the pool could not fill the plan without them."
        )
    largest = max(size for size, _ in plan)
    if len(pool) < largest:
        notes.append(
            f"Only {len(pool)} picks in the pool — the {largest}-pick parlays "
            f"need at least {largest} different picks."
        )
    return parlays, notes
