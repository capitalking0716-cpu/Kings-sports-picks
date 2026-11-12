"""Results / ledger. Paper tracking only.

Every parlay Lilly builds is saved to a local JSON file in
data/ledger.json, with its picks and the date it was built.
Neko (or Lilly) marks each pick won/lost after the games, and
the ledger tracks hit rates over time:
  - per parlay size (3 / 5 / 10)
  - per pick
  - overall

No real bets are placed by this app. No money moves. The
ledger is a record of how good the picks were — that record is
what earns the right to sell a membership later, only after
consistent picks are demonstrated.

Each entry is tagged with the profile that recorded it ("King"
or "Kohen"), and each profile has its own record and hit rates.
Entries saved before profiles existed have no tag and count as
King's.

Each entry also carries a `model` tag — the scoring era that
built it ("v2" = the projection-model blend, wired 2026-11-12;
"v1" = the retired hit-rate blend). Entries saved before the
tag existed load as "v1".
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from . import profiles as _profiles
from .model import SCORING_VERSION


@dataclass
class LedgerPick:
    key: str
    label: str
    probability: float
    odds: int
    result: str = "pending"  # pending | won | lost


@dataclass
class LedgerParlay:
    id: str
    date: str
    size: int
    label: str
    overall_probability: float
    mode: str  # "live" or "demo"
    picks: list = field(default_factory=list)
    settled: bool = False
    # Who recorded this parlay: "King" or "Kohen". Entries saved
    # before profiles existed have no profile in the JSON and load
    # with this default — legacy / demo data counts as King's.
    profile: str = _profiles.DEFAULT_PROFILE
    # Scoring era that built this parlay ("v2" projection blend /
    # "v1" retired hit-rate blend). Same legacy rule: entries
    # saved before the field existed load as "v1".
    model: str = "v1"

    @property
    def picks_won(self) -> int:
        return sum(1 for p in self.picks if p["result"] == "won")

    @property
    def picks_lost(self) -> int:
        return sum(1 for p in self.picks if p["result"] == "lost")

    @property
    def picks_pending(self) -> int:
        return sum(1 for p in self.picks if p["result"] == "pending")

    @property
    def won_all(self) -> bool | None:
        if self.picks_pending:
            return None
        return self.picks_lost == 0


class Ledger:
    def __init__(self, path: Path | str = "data/ledger.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.parlays: list[LedgerParlay] = self._load()

    def _load(self) -> list[LedgerParlay]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text())
            return [LedgerParlay(**item) for item in raw]
        except Exception:
            return []

    def _save(self) -> None:
        self.path.write_text(json.dumps([asdict(p) for p in self.parlays], indent=2))

    def record_run(
        self,
        parlays,
        mode: str,
        profile: str = _profiles.DEFAULT_PROFILE,
        model: str = SCORING_VERSION,
    ) -> list[str]:
        """Save a fresh set of built parlays under `profile`'s record.

        Returns their IDs. Unknown profile names fall back to King's,
        matching how legacy entries (no profile field) are treated.
        `model` is the scoring era of the run and defaults to the
        current scoring version.
        """
        profile = _profiles.normalize_profile(profile)
        ids: list[str] = []
        today = date.today().isoformat()
        for parlay in parlays:
            entry = LedgerParlay(
                id=uuid.uuid4().hex[:8],
                date=today,
                size=parlay.size,
                label=parlay.label,
                overall_probability=parlay.overall_probability,
                mode=mode,
                profile=profile,
                model=model,
                picks=[
                    asdict(LedgerPick(key=p.key, label=p.label, probability=p.probability, odds=p.odds))
                    for p in parlay.picks
                ],
            )
            self.parlays.append(entry)
            ids.append(entry.id)
        self._save()
        return ids

    def settle_pick(self, parlay_id: str, pick_key: str, won: bool) -> bool:
        """Mark one pick won/lost. Returns False if not found."""
        for parlay in self.parlays:
            if parlay.id == parlay_id:
                for pick in parlay.picks:
                    if pick["key"] == pick_key:
                        pick["result"] = "won" if won else "lost"
                        parlay.settled = all(p["result"] != "pending" for p in parlay.picks)
                        self._save()
                        return True
        return False

    def parlays_for_profile(self, profile: str) -> list[LedgerParlay]:
        """Only the parlays that belong to `profile`'s record.

        Entries with no profile (legacy / demo data) count as King's,
        so they show up for King and never for Kohen.
        """
        profile = _profiles.normalize_profile(profile)
        return [lp for lp in self.parlays if _profiles.entry_profile(lp) == profile]

    def stats(self, profile: str | None = None) -> dict:
        """Hit rates: overall, per parlay size, per pick.

        With `profile` given, the numbers cover only that profile's
        record. With no profile, they cover every entry (the
        original behaviour).
        """
        entries = self.parlays if profile is None else self.parlays_for_profile(profile)
        settled_picks = [p for lp in entries for p in lp.picks if p["result"] != "pending"]
        won_picks = sum(1 for p in settled_picks if p["result"] == "won")
        settled_parlays = [lp for lp in entries if lp.settled]
        won_parlays = sum(1 for lp in settled_parlays if lp.won_all)

        by_size: dict[int, dict] = {}
        for size in (3, 5, 10):
            subset = [lp for lp in settled_parlays if lp.size == size]
            by_size[size] = {
                "parlays": len(subset),
                "won": sum(1 for lp in subset if lp.won_all),
                "hit_rate": (sum(1 for lp in subset if lp.won_all) / len(subset)) if subset else None,
            }
        return {
            "total_parlays": len(entries),
            "settled_parlays": len(settled_parlays),
            "parlays_won": won_parlays,
            "parlay_hit_rate": (won_parlays / len(settled_parlays)) if settled_parlays else None,
            "settled_picks": len(settled_picks),
            "picks_won": won_picks,
            "pick_hit_rate": (won_picks / len(settled_picks)) if settled_picks else None,
            "by_size": by_size,
        }
