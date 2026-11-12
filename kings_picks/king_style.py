"""King's style parlays — Lilly's parlays, rebuilt Neko's way.

Neko's order (2026-11-12, after the dropped-line simulation
he asked Lilly to run 5 times): "These are the pics I want —
small money adds up." His method: take the number a player
is known for, drop it down a few, and play the safer side of
it. So on every LIVE run, each parlay Lilly builds gets a
King-style counterpart: the same picks, leg by leg, played
at the MIDDLE drop step of the market's table
(kings_picks/slip.py DROP_STEPS — Points -4, Rushing Yards
-20, Passing Yards -50), priced with the book's REAL
alternate price and scored through the exact board path
(slip.build_drop_options: v2 projection at the new line,
blended with the real alt price, learning corrections on
top). Combined odds, TRUE overall probability and EV are
recomputed from the dropped legs with the same honest maths
as the standard set (kings_picks/parlays.py) — a King parlay
is safer per leg and pays less, and its card says both.

FALLBACK, PER LEG, NEVER SILENT: if no book offers a leg's
dropped line — or the market has no drop at all (Anytime
Touchdown) — the King parlay keeps the STANDARD leg and the
leg carries a plain note saying so ("no dropped line
offered — standard leg kept"). A King parlay never dresses
a standard leg up as a dropped one.

QUOTA GUARD (hard rule): alternates are fetched ONLY for
the unique (event, market) pairs that appear in the FINAL
generated parlays — never slate-wide, never for a leg whose
market has no drop — and only through the caller's fetcher,
which is the disk-cached odds_api.fetch_alternates in the
app. One slate generation can therefore spend at most one
request per distinct (event, market) pair across all its
parlays' legs (44 legs at most, so 44 in the pathological
case; real slates share legs heavily and land far lower),
and repeats within the 6-hour cache are free. DEMO runs
never call this module at all — the app shows one explainer
line instead, because dropped lines need live prices and
Lilly does not invent them.

PICK ANNOTATIONS: a dropped King leg is the fresh ScoredProp
from the board's scoring path, annotated with two plain
attributes (read via getattr everywhere, so standard picks
simply don't have them):

- `base_line`: the standard line it was dropped (Over) or
  raised (Under) from — renders as "(dropped from 24.5)"
  and rides into the pick-log label.
- `king_note`: on a KEPT standard leg, why it was kept.

A kept leg is a copy — the standard set's own pick objects
are never mutated.

LOGGING / LEARNING: King parlays are LILLY's picks in King's
style, so the app logs them exactly like the standard set
(source="lilly") with the parlay's style tag ("king")
stamped on each entry, and settled King entries TEACH her
learning loop like any of her own picks. Only hand-built
manual picks stay excluded from teaching.
"""

from __future__ import annotations

import copy

from . import slip as _slip
from .odds_api import market_key_for_label
from .parlays import Parlay, combined_decimal_payout, overall_probability

# The plain labels a kept leg carries. Wording is part of
# the honesty contract — a kept leg must SAY it was kept.
KEPT_NO_OFFER = "no dropped line offered — standard leg kept"
KEPT_NO_DROP = "no dropped line for this market — standard leg kept"


def middle_step(prop_label: str) -> float | None:
    """The middle drop step for one market, or None when the
    market has no droppable line. For a 3-step table this is
    the second step (Points 4, Rushing Yards 20, Passing
    Yards 50); a 1-step table's only step is its middle.
    """
    steps = _slip.drop_steps_for(prop_label)
    if not steps:
        return None
    return steps[len(steps) // 2]


def _pair_key(leg) -> tuple | None:
    """The (sport, event id, event, market) fetch identity of
    one leg, or None when the leg can never need a fetch:
    no Odds API market for its prop, or its market has no
    drop steps (Anytime Touchdown). The event label rides
    along so legs whose cached prop predates event ids still
    dedupe against each other — the app resolves the id for
    free when it fetches.
    """
    market_key = market_key_for_label(leg.prop)
    if market_key is None or not _slip.drop_steps_for(leg.prop):
        return None
    return (
        leg.sport,
        getattr(leg, "event_id", "") or "",
        getattr(leg, "event", "") or "",
        market_key,
    )


def _kept(leg, note: str):
    """The standard leg, copied, plainly labelled as kept.
    The original pick object is never mutated — the standard
    set keeps rendering exactly as it did.
    """
    kept = copy.copy(leg)
    kept.base_line = None
    kept.king_note = note
    return kept


def _king_leg(leg, alt_prices, form_lookup, learning_lookup):
    """One leg's King version: (pick, was_dropped).

    alt_prices: the AltPrice rows for this leg's event +
    market (already fetched, once, by build_king_parlays) —
    build_drop_options reads only this player's rows.
    form_lookup / learning_lookup: exactly what the board's
    scoring path consumes, so the dropped chance is the
    board's own number at the new line.
    """
    step = middle_step(leg.prop)
    if step is None:
        return _kept(leg, KEPT_NO_DROP), False
    values = form_lookup(leg) if form_lookup is not None else None
    learned = (
        learning_lookup(leg.sport, leg.prop, leg.side)
        if learning_lookup is not None
        else None
    )
    options = _slip.build_drop_options(
        leg, alt_prices, form_values=values, learning=learned
    )
    for option in options:
        if option.step == step and option.playable and option.scored is not None:
            scored = option.scored
            scored.base_line = float(leg.line)
            scored.king_note = ""
            return scored, True
    return _kept(leg, KEPT_NO_OFFER), False


def build_king_parlays(parlays, alt_fetcher, form_lookup=None, learning_lookup=None):
    """Build the King-style counterpart of every parlay.

    parlays: Lilly's standard parlays (kings_picks/parlays.py
    Parlay objects), in build order. Returns (king_parlays,
    info) — one counterpart per input parlay, same sizes,
    same labels, style="king"; info reports what the build
    cost and did:

      pairs_fetched — distinct (event, market) pairs the
        fetcher was called for (the worst-case quota of this
        generation, before the disk cache serves any of them
        for free);
      legs_dropped / legs_kept_standard — the leg counts.

    alt_fetcher(sport, event_id, market_key, event=...,
    commence_time=...) -> list[AltPrice]: called at most
    ONCE per unique pair appearing in the final parlays, and
    never for a leg whose market has no drop. In the app it
    wraps the cached OddsApiClient.fetch_alternates (with
    free event-id resolution when a cached prop predates
    event ids); in tests it is a counting fake.
    """
    # Pass 1: the unique fetch set, from the FINAL parlays
    # only — nothing slate-wide is ever fetched.
    fetch_legs: dict[tuple, object] = {}
    for parlay in parlays:
        for leg in parlay.picks:
            key = _pair_key(leg)
            if key is not None and key not in fetch_legs:
                fetch_legs[key] = leg
    prices: dict[tuple, list] = {}
    for key, leg in fetch_legs.items():
        sport, event_id, _event, market_key = key
        prices[key] = (
            alt_fetcher(
                sport,
                event_id,
                market_key,
                event=getattr(leg, "event", "") or "",
                commence_time=getattr(leg, "commence_time", "") or "",
            )
            or []
        )

    # Pass 2: rebuild each parlay leg by leg.
    king_parlays: list[Parlay] = []
    legs_dropped = 0
    legs_kept = 0
    for parlay in parlays:
        legs = []
        for leg in parlay.picks:
            key = _pair_key(leg)
            king_leg, was_dropped = _king_leg(
                leg,
                prices.get(key, []) if key is not None else [],
                form_lookup,
                learning_lookup,
            )
            legs.append(king_leg)
            if was_dropped:
                legs_dropped += 1
            else:
                legs_kept += 1
        prob = overall_probability(legs)
        payout = combined_decimal_payout(legs)
        king_parlays.append(
            Parlay(
                size=parlay.size,
                picks=legs,
                overall_probability=prob,
                decimal_payout=payout,
                expected_value_per_dollar=prob * payout - 1.0,
                label=parlay.label,
                style="king",
            )
        )
    info = {
        "pairs_fetched": len(fetch_legs),
        "pairs": sorted(
            (sport, market_key)
            for (sport, _eid, _event, market_key) in fetch_legs
        ),
        "legs_dropped": legs_dropped,
        "legs_kept_standard": legs_kept,
    }
    return king_parlays, info
