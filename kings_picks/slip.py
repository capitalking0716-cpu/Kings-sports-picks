"""My Slip — the manual pick-slip builder Neko ordered
2026-11-12: "Give me the ability to make my own picks, show
me the odds on each player selection and the overall pick
slip, make bet amounts adjustable, altering the resulting
[payout]."

This is a CALCULATOR, not a bookmaker. Lilly never places
bets, holds no money, and moves no money. The slip shows
what a parlay of the user's own hand-picked selections
WOULD pay at the books' shown prices, what Lilly's model
thinks of its true overall chance, and what those two
numbers imply — the user bets (or doesn't) at his own book,
with his own money, on his own call.

THE MATHS — identical to Lilly's generated parlays
(kings_picks/parlays.py), so a hand-built slip and a Lilly
parlay are always scored the same way:

- Combined decimal odds = the PRODUCT of each leg's
  decimal odds (model.american_to_decimal per leg).
- TRUE overall probability = the PRODUCT of Lilly's
  per-leg probabilities — her estimate for each leg from
  the v2 scoring path, learning corrections included,
  exactly as the Props breakdown shows them. It is an
  ESTIMATE, labelled that way everywhere it appears.
- Payout = stake x combined decimal. Profit = payout -
  stake. Expected value = overall probability x payout -
  stake. Changing the stake changes ONLY the money lines:
  the odds and the probability are functions of the legs
  alone and never move with the amount.

SLIP RULES:
- One slip per profile (King and Kohen each keep their
  own), persisted to data/slips.json so a slip survives
  the app rerunning — same per-profile pattern as the
  ledger. Unknown profile names fall back to King's,
  matching the rest of the app.
- A selection can't repeat on a slip: uniqueness is the
  Prop.key rule (player + sport + prop + line), the same
  rule Lilly's parlays use — both sides of one line can't
  sit on one slip either.
- A leg keeps the odds and Lilly's chance AS SHOWN when
  it was added. Books move prices; the slip's honest copy
  says the real book's price at bet time is the one that
  counts.

SAVING: saving a slip logs its legs to the pick log
(PickLog.record_manual) tagged source="manual" — the
user's own record, settle-able Won / Lost on Results like
everything else. Manual picks NEVER teach Lilly's
learning loop: she recalibrates only on picks SHE made.

DROPPED LINES (added 2026-11-12, Neko's own betting
style): "whatever the player is known for, take that
number and drop it down a few and play that" — play the
Over at a LOWER line: a smaller payout for a bigger
chance. The drop expander on the board offers the dropped
versions of an Over (and, by the same code path, raised
versions of an Under — the mirror). Two honesty rules:

- Lilly's chance at a dropped line is recomputed through
  the SAME scoring path as the board (the v2 projection at
  the new line, blended with the book's real price at that
  line, learning corrections included) — never a guess.
- The price at a dropped line is a REAL book price from
  the event's alternate market, fetched on demand only
  (odds_api.fetch_alternates). A dropped line no book
  prices can be shown for its projected chance but can
  NEVER join the slip, because the slip's payout maths
  runs on real prices only.

Paper tracking only. No real bets, no money moved.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import profiles as _profiles
from . import projection as _projection
from .model import Prop, american_to_decimal, decimal_to_american, score_prop

# The one calm honesty line, in Lilly's voice, shown on the
# slip. Kept here so the app and any future surface quote
# her exactly.
CALCULATOR_NOTE = (
    "This slip is a calculator — Lilly doesn't place bets. "
    "The odds are the books' prices as fetched; they can move "
    "at the real book before you bet. The overall chance is "
    "Lilly's estimate, and every leg you add multiplies it "
    "down — multi-leg slips are long shots by nature."
)


# ---------- dropped lines: Neko's style, as a step table ----------
#
# How far below the standard line each drop option sits,
# per market. All steps are whole numbers, so a half-point
# base line (24.5) drops to half-point lines (22.5, 20.5,
# 18.5) and a whole base line stays whole — the .5
# convention of the base line is always preserved. Every
# candidate is still filtered to stay above zero when the
# options are built (a 4.5 line can't drop 6). Sized by the
# market's own scale: counting stats move in ones and twos,
# yardage in tens, passing yards — the biggest numbers on
# the board — in twenty-fives.
_COUNTING_STEPS = (2.0, 4.0, 6.0)
_YARDAGE_STEPS = (10.0, 20.0, 30.0)

DROP_STEPS: dict[str, tuple[float, ...]] = {
    # NBA counting stats
    "Points": _COUNTING_STEPS,
    "Rebounds": _COUNTING_STEPS,
    "Assists": _COUNTING_STEPS,
    # The combined stat runs bigger, so its steps run bigger.
    "Points + Rebounds + Assists": (3.0, 6.0, 9.0),
    # Threes live at 0.5-4.5: unit steps are the useful drops.
    "Three-Pointers Made": (1.0, 2.0),
    # NFL
    "Passing Yards": (25.0, 50.0, 75.0),
    "Rushing Yards": _YARDAGE_STEPS,
    "Receiving Yards": _YARDAGE_STEPS,
    "Receptions": (1.0, 2.0, 3.0),
    # 1.5 -> 0.5 ("at least one TD pass") is the only
    # sensible drop a passing-TD line has.
    "Passing Touchdowns": (1.0,),
    # "Anytime Touchdown" is deliberately absent: its line
    # is a fixed 0.5 (score or don't) — there is nothing
    # below it to drop to.
}

# The one calm honesty line for the drop UI, in Lilly's
# voice, next to CALCULATOR_NOTE.
DROP_NOTE = (
    "Dropping the line buys safety, and the book charges "
    "for it — the payout drops too. The slip's expected-"
    "value line is the honest check on whether a dropped "
    "line is worth its price."
)


def drop_steps_for(prop_label: str) -> tuple[float, ...]:
    """The drop steps for one market, or () when the market
    has no droppable line (Anytime Touchdown, or a market
    the table doesn't know — no table entry, no invention).
    """
    return DROP_STEPS.get(prop_label, ())


def adjusted_lines(prop_label: str, side: str, base_line: float) -> list[tuple[float, float]]:
    """(new line, step) pairs for one selection, nearest
    first. Overs DROP the line (base - step); Unders RAISE
    it (base + step) — the mirror, from the same table.
    Every candidate stays above zero.
    """
    out: list[tuple[float, float]] = []
    for step in drop_steps_for(prop_label):
        new_line = base_line - step if side == "Over" else base_line + step
        if new_line > 0:
            out.append((new_line, step))
    return out


@dataclass
class SlipLeg:
    """One hand-picked selection on a slip.

    A snapshot of the prop as shown when it was added: the
    book's price and Lilly's chance at that moment.
    """

    player: str
    sport: str  # "NBA" or "NFL"
    prop: str  # e.g. "Points"
    side: str  # "Over" or "Under"
    line: float
    odds: int  # American odds, as shown
    probability: float  # Lilly's chance for this side (0..1)
    bookmaker: str = ""
    event: str = ""  # e.g. "Lakers vs Celtics"
    has_form_data: bool = False
    # The standard line this leg was dropped (Over) or
    # raised (Under) FROM, when it was added as a dropped
    # version. None = played at the standard line. Legs
    # saved before dropped lines existed load with None.
    base_line: float | None = None

    @property
    def key(self) -> str:
        """Same uniqueness rule as Prop.key / Lilly's parlays."""
        return f"{self.player}|{self.sport}|{self.prop}|{self.line}"

    @property
    def family(self) -> tuple:
        """The selection this leg is a version OF:
        (player, sport, prop, standard line). A standard leg
        is its own family; every dropped/raised version
        shares the standard line it came from. The standard
        version and any dropped version of one selection are
        ONE slot on a slip (SlipBook.add enforces it) — you
        play one version of a pick, not two.
        """
        standard = self.base_line if self.base_line is not None else self.line
        return (self.player, self.sport, self.prop, standard)

    @property
    def line_note(self) -> str:
        """'dropped from 24.5' / 'raised from 24.5', or ""
        for a leg played at the standard line."""
        if self.base_line is None or self.base_line == self.line:
            return ""
        verb = "dropped" if self.side == "Over" else "raised"
        return f"{verb} from {self.base_line:g}"

    @property
    def label(self) -> str:
        text = f"{self.player} — {self.prop} {self.side} {self.line:g}"
        note = self.line_note
        return f"{text} ({note})" if note else text

    @classmethod
    def from_prop(cls, prop) -> "SlipLeg":
        """Snapshot a scored Prop / ScoredProp as a slip leg."""
        return cls(
            player=prop.player,
            sport=prop.sport,
            prop=prop.prop,
            side=prop.side,
            line=float(prop.line),
            odds=int(prop.odds),
            probability=float(prop.probability),
            bookmaker=getattr(prop, "bookmaker", "") or "",
            event=getattr(prop, "event", "") or "",
            has_form_data=bool(getattr(prop, "has_form_data", False)),
        )


# ---------- slip maths (pure — no state, no I/O) ----------


def combined_decimal(legs) -> float:
    """Combined decimal odds: the product of the legs'
    decimal odds. An empty slip's neutral product is 1.0."""
    payout = 1.0
    for leg in legs:
        payout *= american_to_decimal(leg.odds)
    return payout


def overall_probability(legs) -> float:
    """TRUE overall chance: the product of Lilly's per-leg
    probabilities. An empty slip wins nothing: 0.0."""
    if not legs:
        return 0.0
    p = 1.0
    for leg in legs:
        p *= leg.probability
    return p


def slip_summary(legs, stake: float) -> dict:
    """Every number the slip shows, from the legs + stake.

    Odds and probability depend ONLY on the legs; payout,
    profit and expected value scale with the stake. A
    negative stake is treated as 0 (no bet). An empty slip
    pays nothing and risks nothing.
    """
    stake = max(0.0, float(stake))
    decimal = combined_decimal(legs)
    probability = overall_probability(legs)
    if not legs:
        return {
            "legs": 0,
            "decimal_odds": decimal,
            "american_odds": None,
            "overall_probability": 0.0,
            "one_in": None,
            "stake": stake,
            "payout": 0.0,
            "profit": 0.0,
            "expected_value": 0.0,
        }
    payout = stake * decimal
    return {
        "legs": len(legs),
        "decimal_odds": decimal,
        "american_odds": decimal_to_american(decimal),
        "overall_probability": probability,
        "one_in": (1.0 / probability) if probability > 0 else None,
        "stake": stake,
        "payout": payout,
        "profit": payout - stake,
        "expected_value": probability * payout - stake,
    }


# ---------- per-profile persistence ----------


class SlipBook:
    """One in-progress slip per profile, saved to disk.

    data/slips.json shape: {"King": [leg, ...], "Kohen":
    [leg, ...]} — plain leg dicts, so the file doubles as a
    readable record of what each profile is building.
    """

    def __init__(self, path: Path | str = "data/slips.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._slips: dict[str, list[SlipLeg]] = self._load()

    def _load(self) -> dict[str, list[SlipLeg]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict):
                return {}
            return {
                _profiles.normalize_profile(name): [
                    SlipLeg(**leg) for leg in legs if isinstance(leg, dict)
                ]
                for name, legs in raw.items()
                if isinstance(legs, list)
            }
        except Exception:
            return {}

    def _save(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    name: [asdict(leg) for leg in legs]
                    for name, legs in self._slips.items()
                },
                indent=2,
            )
        )

    # ---------- views ----------

    def legs(self, profile: str) -> list[SlipLeg]:
        """The profile's current slip legs, in add order."""
        return list(self._slips.get(_profiles.normalize_profile(profile), []))

    def keys(self, profile: str) -> set[str]:
        """The Prop.key set of the profile's slip — for
        marking board rows already on the slip."""
        return {leg.key for leg in self.legs(profile)}

    def families(self, profile: str) -> set[tuple]:
        """The selection families on the profile's slip
        (SlipLeg.family) — for marking board rows whose
        dropped/raised version is already on the slip."""
        return {leg.family for leg in self.legs(profile)}

    def contains(self, profile: str, key: str) -> bool:
        return key in self.keys(profile)

    # ---------- changes ----------

    def add(self, profile: str, leg: SlipLeg) -> bool:
        """Add a leg. False (no change) when the slip refuses:

        - the same selection is already on it (same key —
          both sides of one line included), or
        - a DIFFERENT VERSION of the same selection is on
          it: the standard line and a dropped/raised version
          of one pick share a family (SlipLeg.family), and a
          family takes one slot. Two unrelated standard
          lines for the same player/prop (two books post
          different main lines) are different families and
          may still sit together, as before.
        """
        profile = _profiles.normalize_profile(profile)
        legs = self._slips.setdefault(profile, [])
        for existing in legs:
            if existing.key == leg.key:
                return False
            if existing.family == leg.family and (
                existing.base_line is not None or leg.base_line is not None
            ):
                return False
        legs.append(leg)
        self._save()
        return True

    def remove(self, profile: str, key: str) -> bool:
        """Remove the leg with this key. False if absent."""
        profile = _profiles.normalize_profile(profile)
        legs = self._slips.get(profile, [])
        kept = [leg for leg in legs if leg.key != key]
        if len(kept) == len(legs):
            return False
        self._slips[profile] = kept
        self._save()
        return True

    def clear(self, profile: str) -> None:
        """Empty the profile's slip (the other profile's
        slip is untouched)."""
        profile = _profiles.normalize_profile(profile)
        if self._slips.get(profile):
            self._slips[profile] = []
            self._save()


# ---------- dropped-line options ----------


@dataclass
class DropOption:
    """One dropped (Over) or raised (Under) version of a
    board selection, as the drop UI shows it.

    playable=True means a real book priced this exact line:
    `scored` is the pick scored at that line through the
    board's own path, ready to snapshot onto the slip.
    playable=False options are shown for their chance only
    (probability set, from the projection alone) or as an
    honest nothing (probability None — no price and no form,
    so there is nothing Lilly can truthfully show).
    """

    line: float  # the line this option plays
    base_line: float  # the standard line it derives from
    step: float  # how far it moved from the standard line
    side: str
    probability: float | None  # Lilly's chance at this line
    standard_probability: float  # her chance at the standard line
    odds: int | None = None  # the book's real price, if any
    bookmaker: str = ""
    playable: bool = False
    note: str = ""
    scored: object = None  # the ScoredProp at this line, when priced

    @property
    def label(self) -> str:
        verb = "dropped" if self.side == "Over" else "raised"
        return f"{self.side} {self.line:g} ({verb} from {self.base_line:g})"

    def to_leg(self) -> SlipLeg | None:
        """The slip leg for this option, or None when it has
        no real price — an unpriced option can never join
        the slip's payout maths."""
        if not self.playable or self.scored is None:
            return None
        leg = SlipLeg.from_prop(self.scored)
        leg.base_line = self.base_line
        return leg


def build_drop_options(
    pick,
    alt_prices,
    form_values=None,
    learning=None,
) -> list[DropOption]:
    """Dropped/raised versions of one board selection.

    pick: the scored board prop (a ScoredProp). Its own
    probability is the standard-line chance every option is
    compared against.

    alt_prices: AltPrice rows (kings_picks/odds_api.py) for
    this pick's event + market, as fetched on demand. Only
    THIS player's rows are read, and only lines the step
    table names: a price for some other line is never bent
    into service as a price for this one.

    form_values / learning: exactly what the board's scoring
    path consumed — the player's raw game-log values for
    this stat (or None) and Lilly's learning dict for this
    bucket (or None). Each priced option is scored with
    model.score_prop at the new line: the v2 projection
    recomputed AT THAT LINE, blended with the real alt
    price (vig removed when both alt sides are priced, the
    "cut still in" convention when only this side is), and
    the learning correction applied on top — the board's
    path, line for line.
    """
    # Best price per (side, line) for THIS player. The alt
    # parser already keeps the best across books; re-index
    # defensively so a stray row can never leak in.
    prices: dict[tuple, tuple[int, str]] = {}
    for row in alt_prices or []:
        if row.player != pick.player or row.prop != pick.prop:
            continue
        k = (row.side, float(row.line))
        if k not in prices or row.odds > prices[k][0]:
            prices[k] = (int(row.odds), row.bookmaker or "")

    learn_kwargs = (
        {
            "learning_delta": learning["delta"],
            "learning_tag": learning["tag"],
            "learning_note": learning["note"],
        }
        if learning
        else {}
    )
    proj = _projection.project_stat(form_values) if form_values else None

    options: list[DropOption] = []
    other_side = "Under" if pick.side == "Over" else "Over"
    for new_line, step in adjusted_lines(pick.prop, pick.side, float(pick.line)):
        form_probability = (
            _projection.over_probability(form_values, new_line, pick.side)
            if form_values
            else None
        )
        own = prices.get((pick.side, new_line))
        if own is not None:
            odds, book = own
            pair = prices.get((other_side, new_line))
            prop = Prop(
                player=pick.player,
                sport=pick.sport,
                prop=pick.prop,
                side=pick.side,
                line=new_line,
                odds=odds,
                bookmaker=book,
                event=getattr(pick, "event", "") or "",
                commence_time=getattr(pick, "commence_time", "") or "",
                source=getattr(pick, "source", "live") or "live",
                event_id=getattr(pick, "event_id", "") or "",
            )
            if form_probability is not None and proj is not None:
                scored = score_prop(
                    prop,
                    pair_odds=pair[0] if pair else None,
                    form_probability=form_probability,
                    projected_value=proj.expected,
                    projection_games=proj.games,
                    **learn_kwargs,
                )
            else:
                scored = score_prop(
                    prop,
                    pair_odds=pair[0] if pair else None,
                    **learn_kwargs,
                )
            options.append(
                DropOption(
                    line=new_line,
                    base_line=float(pick.line),
                    step=step,
                    side=pick.side,
                    probability=float(scored.probability),
                    standard_probability=float(pick.probability),
                    odds=odds,
                    bookmaker=book,
                    playable=True,
                    note=scored.why,
                    scored=scored,
                )
            )
        elif form_probability is not None:
            # No book prices this line, but his game log does
            # speak: show the projection's chance at the line
            # (learning correction included, the same delta
            # the board applies), labelled as projection-only.
            # It can NOT join the slip — payout maths needs a
            # real price.
            probability = form_probability
            if learning:
                probability = min(max(probability + learning["delta"], 0.05), 0.95)
            options.append(
                DropOption(
                    line=new_line,
                    base_line=float(pick.line),
                    step=step,
                    side=pick.side,
                    probability=float(probability),
                    standard_probability=float(pick.probability),
                    playable=False,
                    note=(
                        "Projection only — no book offers this line, so "
                        "there's no price and it can't go on the slip."
                    ),
                )
            )
        else:
            options.append(
                DropOption(
                    line=new_line,
                    base_line=float(pick.line),
                    step=step,
                    side=pick.side,
                    probability=None,
                    standard_probability=float(pick.probability),
                    playable=False,
                    note="No book offers this line.",
                )
            )
    return options
