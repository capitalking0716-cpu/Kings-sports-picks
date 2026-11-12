"""Transparent probability model. No black box.

Every number Lilly shows comes from one of two honest places:

1. The sportsbook odds themselves (always real, when a key is set).
   American odds -> implied chance, with the bookmaker's cut
   (the "vig") taken out of each Over/Under pair so the two sides
   add up to 100%.
2. A projection built from the player's own game log — ONLY when
   a real game-log feed matched him (nflverse for the NFL,
   stats.nba.com for the NBA — see game_logs.py). The projection
   model (kings_picks/projection.py) projects his next game from
   his season / last-10 / last-5 averages and his own game-to-game
   spread, and turns that into a chance for this side of the
   line. Lilly blends it half-and-half with the vig-free odds
   chance above.

The projection ingredient (scoring era "v2") replaced a raw
"share of his recent games over the line" hit-rate on
2026-11-12: a 10,969-game walk-forward calibration test
(validation/projection-calibration-2026-11-12.md) showed the
hit-rate was worse than always saying 50% in the NBA, while the
projection beat or tied it in both leagues. The hit-rate still
EXISTS as a display ("over in X of last 10" on the Players page
and the breakdown) — it is honest descriptive stats — but it is
no longer a scoring ingredient, by design.

A matched log with fewer than 5 games yields NO projection and
the pick stays odds-only. There is deliberately no fallback to
the hit-rate: a short log is not evidence, and the proof showed
the fallback itself was the liability.

A probability is NEVER made up. If there is no data for it,
Lilly says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The scoring era stamped onto every pick-log / ledger entry.
# "v1" = the retired hit-rate blend; "v2" = the projection blend.
SCORING_VERSION = "v2"


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


def decimal_to_american(decimal: float) -> int:
    """Decimal payout -> American odds (the inverse conversion).

    decimal >= 2.0 is a plus price: (decimal - 1) x 100.
    1.0 < decimal < 2.0 is a minus price: -100 / (decimal - 1).
    Rounded to the nearest whole number, the way books quote
    combined parlay prices. A decimal at or below 1.0 is not
    a real price (it would mean no possible profit) and raises.
    """
    if decimal <= 1.0:
        raise ValueError("Decimal odds must be above 1.0.")
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100.0))
    return -int(round(100.0 / (decimal - 1.0)))


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
    # The Odds API's own id for the game, filled in at parse
    # time. The My Slip dropped-lines feature needs it to ask
    # for THAT event's alternate markets on demand. Props
    # loaded from caches saved before this field existed
    # carry "" — the app then resolves the id from the free
    # events endpoint instead (odds_api.resolve_event_id).
    event_id: str = ""
    # Filled in by the model:
    probability: float = 0.0
    has_form_data: bool = False
    # The v2 form ingredient, when a real game log matched:
    # the projection model's chance for this side, the projected
    # stat value (a scoring RATE for Anytime Touchdown), and how
    # many logged games the projection was built from.
    form_probability: float | None = None
    projected_value: float | None = None
    projection_games: int = 0
    edge: float = 0.0
    source: str = "live"  # "live" or "demo"
    form_note: str = ""
    # Filled in when Lilly's learning loop (kings_picks/
    # learning.py) adjusted this pick: the bucket tag that
    # rides into the pick log, e.g. "nba_points_over:-7",
    # and the signed probability move behind it. Empty / 0
    # means no learned correction touched this pick.
    learning: str = ""
    learning_delta: float = 0.0

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
    # Filled in by the live pull's test-before-picking step:
    # before the parlays are built, every candidate leg is
    # graded against the player's game-log form
    # (kings_picks/self_test.py) at the line it would play,
    # and the parlay builder ranks by this verdict. The
    # verdict that ranked the leg at selection time
    # ("STRONG" / "LEAN" / "MIXED" / "SHAKY" / "NO DATA")
    # and its last-10 record ("7/10", pushes named) ride on
    # the pick so the bet ticket can show them. "" when the
    # leg was never graded (hand-built slip legs, slates
    # stored before this step existed).
    form_verdict: str = ""
    form_record: str = ""


def _projection_note(
    prop: Prop,
    form_probability: float,
    projected_value: float | None,
    projection_games: int,
) -> str:
    """The projection ingredient in plain words — the actual
    projected value and the chance it produced, never just
    "form says so"."""
    if projected_value is None:
        return (
            f"Projection: the model puts the {prop.side.lower()} "
            f"{prop.line:g} at about {form_probability:.0%}, from "
            f"{projection_games} logged games."
        )
    if prop.prop == "Anytime Touchdown":
        # The projected value for a yes/no stat is his scoring
        # rate — a share of games, not a yardage-style number.
        return (
            f"Projection: he gets into the end zone in about "
            f"{projected_value:.0%} of his games ({projection_games} "
            f"logged), and the model puts the {prop.side.lower()} "
            f"{prop.line:g} at about {form_probability:.0%}."
        )
    return (
        f"Projection: about {projected_value:.0f} {prop.prop.lower()} "
        f"expected, from {projection_games} logged games; his own "
        f"game-to-game spread puts the {prop.side.lower()} {prop.line:g} "
        f"at about {form_probability:.0%}."
    )


# The calibration proof's known weakness, carried on the pick
# itself: against average-set test lines, v2's Over chances in
# the middle range landed a few points below what it called
# (NBA 52% -> 46%, 57% -> 51.5%; the NFL gap was bigger). When
# an Over's projection ingredient sits in that range, Lilly
# says so instead of letting the number sound sure of itself.
OVER_CAUTION_LOW = 0.50
OVER_CAUTION_HIGH = 0.70
OVER_CAUTION = (
    " One caution from Lilly's own testing: her projection model "
    "has called mid-range Overs a few points too confidently, so "
    "read this as a lean, not a lock."
)


def score_prop(
    prop: Prop,
    pair_odds: int | None = None,
    form_probability: float | None = None,
    projected_value: float | None = None,
    projection_games: int = 0,
    learning_delta: float = 0.0,
    learning_tag: str = "",
    learning_note: str = "",
) -> ScoredProp:
    """Score one prop and explain it in plain words.

    pair_odds: the American odds of the OTHER side of the same
    Over/Under line, so the vig can be removed. If it is not
    given, the raw implied chance is used and the pick is flagged
    as less exact.

    form_probability / projected_value / projection_games: the
    v2 form ingredient — the projection model's chance for this
    side of the line, from kings_picks/projection.py, computed
    from the player's real game log. Only pass numbers a real
    feed produced. If form_probability is None the pick is
    odds-only: the probability comes from the odds alone and is
    labelled that way. There is NO hit-rate parameter any more —
    the raw "share of recent games over the line" was retired as
    an ingredient by the calibration proof and must not come
    back through this function.

    learning_delta / learning_tag / learning_note: Lilly's
    self-learning correction (kings_picks/learning.py), derived
    ONLY from her settled live record. Applied after the blend,
    explained in the why-text, tagged onto the pick. The caller
    (the app's live scoring path) passes it only for live
    picks — demo picks are never adjusted.
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
    scored.has_form_data = form_probability is not None

    if scored.has_form_data:
        assert form_probability is not None
        # Transparent blend, written out in the why-text:
        # half what the odds say, half what the projection says.
        probability = 0.5 * implied + 0.5 * form_probability
        scored.probability = min(max(probability, 0.05), 0.95)
        scored.form_probability = form_probability
        scored.projected_value = projected_value
        scored.projection_games = projection_games
        scored.edge = scored.probability - implied
        scored.form_note = _projection_note(
            prop, form_probability, projected_value, projection_games
        )
        scored.why = (
            f"{scored.form_note} The odds say about {implied:.0%} "
            f"({vig_note}). Blending the two half and half, Lilly puts "
            f"this at {scored.probability:.0%}."
        )
        if prop.side == "Over" and OVER_CAUTION_LOW <= form_probability < OVER_CAUTION_HIGH:
            scored.why += OVER_CAUTION
    else:
        scored.probability = min(max(implied, 0.05), 0.95)
        scored.edge = 0.0
        scored.form_note = (
            "Odds-only pick — no recent-form data. "
            "No usable game log covers this pick, so no projection either."
        )
        scored.why = (
            f"The odds say about {implied:.0%} ({vig_note}). "
            "There is no recent-form data for this pick, so Lilly does "
            "not pretend to know more than the odds say. "
            "Odds-only, no recent-form data."
        )

    # Lilly's learning loop (kings_picks/learning.py) applies
    # LAST, on top of the blend above: a small calibration
    # correction earned by her own settled record in this
    # pick's bucket. It is capped and shrunk by the learning
    # module, the move is explained in her why-text, and the
    # tag rides into the pick log so the correction itself
    # stays on the record. No correction -> nothing changes.
    if learning_delta:
        scored.probability = min(max(scored.probability + learning_delta, 0.05), 0.95)
        scored.edge = scored.probability - implied
        scored.learning = learning_tag
        scored.learning_delta = learning_delta
        if learning_note:
            scored.why += f" {learning_note}"
    return scored


def pct(p: float) -> str:
    """0.623 -> '62%'."""
    return f"{p:.0%}"
