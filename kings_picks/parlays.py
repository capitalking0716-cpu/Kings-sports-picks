"""Parlay builder — the core output.

Each run builds:
  - 2 parlays of 10 picks
  - 3 parlays of 5 picks
  - 3 parlays of 3 picks

Rule: the combination with the HIGHEST overall win chance wins.
For a fixed size, the overall chance is the product of the
individual pick chances, so the best parlay of N picks is simply
the N highest-chance picks — as long as no pick appears twice
in the same parlay. That is exactly what this builder does, and
it is why the result is honest: it never hides a weak leg.

The honest fact, shown on every parlay: even the BEST 10-pick
parlay is a long shot. Ten picks that each win 60% of the time
multiply to 0.6^10 = about 0.6% overall. Lilly says that out
loud on every 10-pick card. A parlay is not a safe bet — it is
a lottery ticket with better odds than most.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from kings_picks.model import ScoredProp, american_to_decimal

# (size, how many) — the shape Neko asked for.
PARLAY_PLAN = [(10, 2), (5, 3), (3, 3)]


@dataclass
class Parlay:
    size: int
    picks: list  # list[ScoredProp]
    overall_probability: float = 0.0
    decimal_payout: float = 0.0
    expected_value_per_dollar: float = 0.0
    label: str = ""

    @property
    def one_in(self) -> float:
        """'1 in X' — easier to feel than 0.6%."""
        if self.overall_probability <= 0:
            return float("inf")
        return 1.0 / self.overall_probability

    def plain_summary(self) -> str:
        """One plain-words line about what this parlay really is."""
        if self.size >= 10:
            return (
                f"Long shot. Even the best 10 picks only win about "
                f"{self.overall_probability:.1%} of the time — roughly "
                f"1 in {self.one_in:.0f}. Fun to track, not a way to make "
                f"steady money."
            )
        if self.size >= 5:
            return (
                f"Tough. Wins about {self.overall_probability:.1%} of the "
                f"time — roughly 1 in {self.one_in:.0f}."
            )
        return (
            f"The steadiest of the three sizes. Wins about "
            f"{self.overall_probability:.1%} of the time — roughly "
            f"1 in {self.one_in:.0f}."
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


def build_parlays(scored: list, plan: list = None) -> tuple[list, list]:
    """Build the parlays. Returns (parlays, notes).

    Picks are ranked by probability, best first. Each parlay
    takes the best picks not already used in THAT parlay — so a
    pick never appears twice in one parlay, but strong picks can
    appear in more than one parlay (different tickets can share
    a leg; that is normal).

    If there are not enough picks for the full plan, fewer
    parlays are built and a note says so plainly.
    """
    plan = plan or PARLAY_PLAN
    notes: list[str] = []
    ranked = sorted(scored, key=lambda p: p.probability, reverse=True)
    # No duplicate pick inside a parlay: dedupe the pool by key,
    # keeping the best-chance version of each pick.
    seen: dict[str, ScoredProp] = {}
    for pick in ranked:
        if pick.key not in seen:
            seen[pick.key] = pick
    pool = sorted(seen.values(), key=lambda p: p.probability, reverse=True)

    if not pool:
        return [], ["No picks to build from. Fetch live props or use demo mode."]
    if len(pool) < 3:
        return [], [f"Only {len(pool)} pick(s) available — need at least 3 to build a parlay."]

    parlays: list[Parlay] = []
    cursor = 0
    for size, count in plan:
        for i in range(count):
            # Take the next `size` best picks. Wrap around the pool
            # if we run out, so later parlays still get built — but
            # never repeat a pick inside one parlay.
            legs: list[ScoredProp] = []
            guard = 0
            while len(legs) < size and guard < len(pool) * 2:
                candidate = pool[cursor % len(pool)]
                cursor += 1
                guard += 1
                if candidate.key not in {p.key for p in legs}:
                    legs.append(candidate)
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
    if len(pool) < 10:
        notes.append(
            f"Only {len(pool)} picks in the pool — 10-pick parlays need at least 10 different picks."
        )
    return parlays, notes
