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

Paper tracking only. No real bets, no money moved.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import profiles as _profiles
from .model import american_to_decimal, decimal_to_american

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

    @property
    def key(self) -> str:
        """Same uniqueness rule as Prop.key / Lilly's parlays."""
        return f"{self.player}|{self.sport}|{self.prop}|{self.line}"

    @property
    def label(self) -> str:
        return f"{self.player} — {self.prop} {self.side} {self.line:g}"

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

    def contains(self, profile: str, key: str) -> bool:
        return key in self.keys(profile)

    # ---------- changes ----------

    def add(self, profile: str, leg: SlipLeg) -> bool:
        """Add a leg. False (no change) if its key is already
        on this profile's slip — a selection can't repeat."""
        profile = _profiles.normalize_profile(profile)
        legs = self._slips.setdefault(profile, [])
        if any(existing.key == leg.key for existing in legs):
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
