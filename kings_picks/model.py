"""Transparent probability model. No black box.

Every number Lilly shows comes from one of two honest places:

1. The sportsbook odds themselves (always real, when a key is set).
   American odds -> implied chance, with the bookmaker's cut
   (the "vig") taken out of each Over/Under pair so the two sides
   add up to 100%.
2. The player's recent games vs the same line — ONLY when a real
   game-log source is available. Free feeds exist now (nflverse
   for the NFL, stats.nba.com for the NBA — see game_logs.py),
   but coverage is per-player: any pick no feed matched is
   labelled "odds-only, no recent-form data" and its chance is
   the vig-free implied chance from the odds — never invented.

A probability is NEVER made up. If there is no data for it,
Lilly says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def american_to_implied(odds: int) -> float:
    """American odds -> raw implied chance (0..1), vig still in."""
    if odds == 0:
        raise ValueError("Odds of 0 are not valid American odds.")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def american_to_decimal(odds: int) -> float:
    """American odds -> decimal payout (what $1 staked returns if it wins)."""
    if odds > 0:
        return 1.0 + odds / 100.0
    return 1.0 + 100.0 / abs(odds)


def remove_vig(over_odds: int, under_odds: int) -> tuple[float, float]:
    """Take the bookmaker's cut out of an Over/Under pair.

    Raw implied chances add up to more than 100% — that extra is
    the bookmaker's cut. Dividing each side by the total makes
    the pair add up to exactly 100%. Returns (over_chance, under_chance).
    """
    over = american_to_implied(over_odds)
    under = american_to_implied(under_odds)
    total = over + under
    if total <= 0:
        raise ValueError("Odds pair has no total implied chance.")
    return over / total, under / total


@dataclass
class Prop:
    """One player prop, as it comes from the odds feed (or demo data)."""

    player: str
    sport: str  # "NBA" or "NFL"
    prop: str  # e.g. "Points", "Passing Yards"
    side: str  # "Over" or "Under"
    line: float
    odds: int  # American odds, e.g. -110 or +150
    bookmaker: str = ""
    event: str = ""  # e.g. "Lakers vs Celtics"
    commence_time: str = ""
    # Filled in by the model:
    probability: float = 0.0
    has_form_data: bool = False
    recent_hits: int = 0
    recent_games: int = 0
    edge: float = 0.0
    source: str = "live"  # "live" or "demo"
    form_note: str = ""

    @property
    def key(self) -> str:
        """Unique pick inside a parlay: same player + same prop can't repeat."""
        return f"{self.player}|{self.sport}|{self.prop}|{self.line}"

    @property
    def label(self) -> str:
        return f"{self.player} — {self.prop} {self.side} {self.line:g}"


@dataclass
class ScoredProp(Prop):
    """A Prop with its probability and plain-words reason attached."""

    why: str = ""


def score_prop(
    prop: Prop,
    pair_odds: int | None = None,
    recent_hits: int | None = None,
    recent_games: int | None = None,
) -> ScoredProp:
    """Score one prop and explain it in plain words.

    pair_odds: the American odds of the OTHER side of the same
    Over/Under line, so the vig can be removed. If it is not
    given, the raw implied chance is used and the pick is flagged
    as less exact.

    recent_hits / recent_games: how many of the player's last
    N games went OVER this line (for an Over pick; the flip side
    for Under). Only pass real game-log numbers. If these are
    None, the pick is odds-only — probability comes from the
    odds alone and is labelled that way.
    """
    if pair_odds is not None:
        if prop.side == "Over":
            over_p, under_p = remove_vig(prop.odds, pair_odds)
        else:
            over_p, under_p = remove_vig(pair_odds, prop.odds)
        implied = over_p if prop.side == "Over" else under_p
        vig_note = "bookmaker's cut taken out"
    else:
        implied = american_to_implied(prop.odds)
        vig_note = "no opposite side shown, so the bookmaker's cut is still in"

    scored = ScoredProp(**{**prop.__dict__})
    scored.has_form_data = recent_hits is not None and recent_games not in (None, 0)

    if scored.has_form_data:
        assert recent_games is not None and recent_games > 0
        assert recent_hits is not None
        hit_rate = recent_hits / recent_games
        # Transparent blend, written out in the why-text:
        # half what the odds say, half what the player just did.
        probability = 0.5 * implied + 0.5 * hit_rate
        scored.probability = min(max(probability, 0.05), 0.95)
        scored.recent_hits = recent_hits
        scored.recent_games = recent_games
        scored.edge = scored.probability - implied
        scored.form_note = (
            f"Recent form: {recent_hits} of last {recent_games} games "
            f"went {prop.side.lower()} {prop.line:g} ({hit_rate:.0%})."
        )
        scored.why = (
            f"{scored.form_note} The odds say about {implied:.0%} "
            f"({vig_note}). Blending the two, Lilly puts this at "
            f"{scored.probability:.0%}."
        )
    else:
        scored.probability = min(max(implied, 0.05), 0.95)
        scored.edge = 0.0
        scored.form_note = (
            "Odds-only pick — no recent-form data. "
            "No free game-log source covers this right now."
        )
        scored.why = (
            f"The odds say about {implied:.0%} ({vig_note}). "
            "There is no recent-form data for this pick, so Lilly does "
            "not pretend to know more than the odds say. "
            "Odds-only, no recent-form data."
        )
    return scored


def pct(p: float) -> str:
    """0.623 -> '62%'."""
    return f"{p:.0%}"
