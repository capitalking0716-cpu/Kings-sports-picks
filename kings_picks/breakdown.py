"""Player breakdown — the Props Edge-style "why this pick"
math, kept pure so it can be unit-tested without Streamlit.

Everything here comes from data the app already has: the odds
feed (best price per side across the books Lilly polls), the
de-vig math in model.py, today's scored slate, the built
parlays, and — when a real game-log feed matched the player —
the form summary from game_logs.py.

Honesty rules, same as everywhere in this app:
- The "book's chance" is the book's STICKER chance: the raw
  implied chance in the price, bookmaker's cut still in.
- "Lilly's chance" is the scored probability (cut removed, or
  blended with real form when form exists).
- The gap between them is explained for what it mostly is —
  the cut coming out — never dressed up as secret knowledge.
- Weak signals are called weak, in writing, on the card.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kings_picks.model import american_to_implied, remove_vig

# (label, low, high) — a prop lands in the first band whose
# high end it is under.
BANDS = [
    ("Under 50%", 0.0, 0.50),
    ("50–55%", 0.50, 0.55),
    ("55–60%", 0.55, 0.60),
    ("60% or more", 0.60, 1.01),
]


def book_chance(odds: int) -> float:
    """The book's sticker chance for one price (cut still in)."""
    return american_to_implied(odds)


def fair_chance(odds: int, side: str, pair_odds: int | None) -> float:
    """Lilly's cleaned-up chance from the odds alone: the pair's
    cut removed. With no opposite side priced, the sticker
    chance is all there is — returned as-is."""
    if pair_odds is None:
        return american_to_implied(odds)
    if side == "Over":
        over_p, _ = remove_vig(odds, pair_odds)
        return over_p
    _, under_p = remove_vig(pair_odds, odds)
    return under_p


def pair_vig(odds: int, pair_odds: int | None) -> float | None:
    """The bookmaker's cut in an Over/Under pair, as a fraction
    (0.0476 = 4.76 points over 100%). None when only one side
    was priced and the cut cannot be measured."""
    if pair_odds is None:
        return None
    return american_to_implied(odds) + american_to_implied(pair_odds) - 1.0


def pair_map(props) -> dict:
    """(player, sport, prop, line) -> {side: odds} from raw props,
    so a scored pick can find its opposite side's price."""
    out: dict[tuple, dict] = {}
    for p in props:
        out.setdefault((p.player, p.sport, p.prop, p.line), {})[p.side] = p.odds
    return out


def chance_bands(scored) -> list:
    """[(band label, count)] for the whole slate, in band order."""
    counts = {label: 0 for label, _lo, _hi in BANDS}
    for pick in scored:
        for label, _lo, hi in BANDS:
            if pick.probability < hi:
                counts[label] += 1
                break
    return [(label, counts[label]) for label, _lo, _hi in BANDS]


def slate_rank(pick, scored) -> int:
    """1-based rank of this pick on today's slate by Lilly's
    chance (ties share the better rank)."""
    return 1 + sum(1 for other in scored if other.probability > pick.probability)


def parlays_containing(pick_key: str, parlays) -> list:
    """Labels of the built parlays that include this pick."""
    return [
        parlay.label
        for parlay in parlays
        if any(leg.key == pick_key for leg in parlay.picks)
    ]


def chance_chart_rows(lilly_chance: float, book: float) -> list:
    """Rows for the Lilly-vs-book bar chart, as percents."""
    return [
        {"who": "Lilly's chance", "chance_pct": round(lilly_chance * 100, 1)},
        {"who": "Book's sticker chance", "chance_pct": round(book * 100, 1)},
    ]


def form_chart_rows(values_newest_first: list) -> list:
    """Rows for the recent-form bar chart, oldest game first so
    the chart reads left-to-right like a season unfolding."""
    rows = []
    oldest_first = list(reversed(values_newest_first))
    for i, value in enumerate(oldest_first):
        label = "Last game" if i == len(oldest_first) - 1 else f"{len(oldest_first) - i} games ago"
        rows.append({"game": label, "value": float(value)})
    return rows


@dataclass
class PropBreakdown:
    pick: object
    lilly_chance: float
    book_chance: float
    fair_chance: float
    edge_points: float  # lilly minus book, in percentage points
    vig: float | None
    pair_odds: int | None
    rank: int
    slate_size: int
    parlay_labels: list = field(default_factory=list)
    strength: str = "mixed"  # "strong" / "mixed" / "weak"
    reasons: list = field(default_factory=list)


def _strength(pick, form) -> str:
    if pick.probability < 0.50:
        return "weak"
    if form is not None and form.games > 0:
        if pick.probability >= 0.55 and form.hits / form.games >= 0.5:
            return "strong"
        return "mixed"
    if pick.probability >= 0.60:
        return "mixed"  # a big odds-only number is still just the book talking
    return "weak" if pick.probability < 0.53 else "mixed"


def build_breakdown(pick, scored, parlays, pair_odds: int | None = None, form=None) -> PropBreakdown:
    """Assemble one prop's breakdown card data + plain-English
    reasons from REAL signals only.

    `form` is a game_logs.FormSummary for this pick's side of
    the line, or None when no feed matched the player.
    """
    book = book_chance(pick.odds)
    fair = fair_chance(pick.odds, pick.side, pair_odds)
    vig = pair_vig(pick.odds, pair_odds)
    rank = slate_rank(pick, scored)
    labels = parlays_containing(pick.key, parlays)
    lilly = pick.probability
    edge_points = (lilly - book) * 100.0

    reasons: list[str] = []

    # --- the price ---
    book_note = f" at {pick.bookmaker}" if pick.bookmaker else ""
    reasons.append(
        f"Best price Lilly found for this side: {pick.odds:+d}{book_note} — "
        "the better of the books she checks."
    )

    # --- the bookmaker's cut ---
    if vig is not None:
        total = (book_chance(pick.odds) + book_chance(pair_odds)) * 100
        reasons.append(
            f"The two sides' prices together imply {total:.0f}% — the extra "
            f"{vig * 100:.1f} points over 100% is the bookmaker's cut. "
            f"Lilly's {lilly:.0%} already has that cut taken out; the book's "
            f"sticker chance for this side alone is {book:.0%}."
        )
    else:
        reasons.append(
            "Only this side's price was listed, so the bookmaker's cut "
            "could not be measured or removed — treat this number as rougher "
            "than most."
        )

    # --- the other side of the line ---
    if pair_odds is not None:
        other_side = "Under" if pick.side == "Over" else "Over"
        gap = abs(book - book_chance(pair_odds)) * 100
        if gap < 3.0:
            reasons.append(
                f"The other side ({other_side} {pick.line:g}) is priced "
                f"{pair_odds:+d} — almost the same as this side. The books "
                "see this one as close to a coin flip."
            )
        elif book > book_chance(pair_odds):
            reasons.append(
                f"The other side ({other_side} {pick.line:g}) is priced "
                f"{pair_odds:+d}, a longer price — the books lean toward "
                "this side of the line."
            )
        else:
            reasons.append(
                f"The other side ({other_side} {pick.line:g}) is priced "
                f"{pair_odds:+d}, a SHORTER price than this side — the "
                "books actually lean the other way. That is a mark against "
                "this pick."
            )

    # --- rank on today's board ---
    if rank == 1:
        reasons.append(
            f"This is the #1 prop on today's whole board by Lilly's chance "
            f"(of {len(scored)} props)."
        )
    elif rank > len(scored) / 2:
        reasons.append(
            f"This sits at #{rank} of {len(scored)} props on today's board — "
            "the bottom half. Lilly only builds parlays from the top of the "
            "board, so this is not one of her favourites."
        )
    elif rank <= 10:
        reasons.append(
            f"This sits at #{rank} of {len(scored)} props on today's board — "
            "near the top of what Lilly can see today."
        )
    else:
        reasons.append(
            f"This sits at #{rank} of {len(scored)} props on today's board — "
            "middle of the pack, nothing special either way."
        )

    # --- parlay membership ---
    if labels:
        reasons.append(f"Lilly put this pick in: {', '.join(labels)}.")
    else:
        reasons.append(
            "This pick did not make any of today's parlays — the parlays "
            "take the highest-chance picks first."
        )

    # --- form, or the honest lack of it ---
    if form is not None and form.games > 0:
        move = (lilly - fair) * 100
        direction = (
            f"moved Lilly's number up by {move:.0f} points from the cleaned-up "
            f"odds ({fair:.0%})" if move >= 0.5 else
            f"pulled Lilly's number down by {abs(move):.0f} points from the "
            f"cleaned-up odds ({fair:.0%})" if move <= -0.5 else
            f"left Lilly's number about where the cleaned-up odds put it ({fair:.0%})"
        )
        # The hit-rate above is descriptive — what the scoring
        # actually blended in is the projection model's chance,
        # so say so, with its numbers, whenever the pick carries
        # them. The move in `direction` is measured from the real
        # blended number, so it is the projection ingredient's
        # move, not the hit-rate's.
        projection_sentence = ""
        if pick.form_probability is not None:
            if pick.prop == "Anytime Touchdown":
                projection_sentence = (
                    f" What her scoring blended in is her projection model's "
                    f"number: it puts this side at {pick.form_probability:.0%} "
                    f"from his scoring log."
                )
            elif pick.projected_value is not None:
                projection_sentence = (
                    f" What her scoring blended in is her projection model's "
                    f"number: about {pick.projected_value:.0f} "
                    f"{pick.prop.lower()} projected, putting this side at "
                    f"{pick.form_probability:.0%}."
                )
            else:
                projection_sentence = (
                    f" What her scoring blended in is her projection model's "
                    f"number: {pick.form_probability:.0%} for this side."
                )
        reasons.append(
            f"Recent form ({form.source_label}): this player went "
            f"{pick.side.lower()} {pick.line:g} in {form.hits} of his last "
            f"{form.games} games.{projection_sentence} That {direction}. "
            "Form is supporting "
            "evidence only — the chance still starts from the odds, and "
            "form never turns a pick into a promise."
        )
    else:
        reasons.append(
            "No recent-form data behind this pick — Lilly's number is the "
            "book's number with the cut removed, nothing more. She has NOT "
            "checked it against the player's recent games, so do not read "
            "the gap above as an edge she found."
        )

    # --- Lilly's risk-first cautions ---
    if pick.probability < 0.50:
        reasons.append(
            "⚠️ Weak pick: even after cleaning up the odds, this side wins "
            "less than half the time. Lilly is risk-first — she would skip "
            "this one."
        )
    if form is not None and form.games > 0 and form.hits / form.games < 0.4:
        reasons.append(
            "⚠️ The recent games argue AGAINST this side — it only landed "
            f"{form.hits} of the last {form.games} times."
        )

    return PropBreakdown(
        pick=pick,
        lilly_chance=lilly,
        book_chance=book,
        fair_chance=fair,
        edge_points=edge_points,
        vig=vig,
        pair_odds=pair_odds,
        rank=rank,
        slate_size=len(scored),
        parlay_labels=labels,
        strength=_strength(pick, form),
        reasons=reasons,
    )
