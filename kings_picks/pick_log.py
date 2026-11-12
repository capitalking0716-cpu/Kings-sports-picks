"""Pick log — EVERY pick Lilly makes, tracked to a result.

The parlay ledger (ledger.py) records the parlays themselves,
per profile. This log is different: every single LEG of every
parlay is saved here as its own pick, and each pick is tracked
won / lost whether or not anyone actually played it. Neko asked
for this plainly: "keep logs of all picks and track win/loss on
picks chosen regardless if played."

There is NO free source for final player-prop stats (the known
project limit), so settlement is MANUAL by design: a human taps
Won / Lost on each pick after the games. Lilly never guesses a
result.

The log is global (shared): Lilly's picks are the same for both
profiles. The per-profile part is `played_by` — the profiles who
marked "I played this" on a pick — so each profile also gets its
own played record.

Honesty rules, same as the ledger: demo picks are logged with
mode="demo" and are NEVER mixed into the headline hit-rate
stats. Demo numbers are reported separately, or not at all.

Every entry also carries a `model` tag — the scoring era that
produced the pick ("v2" = the projection-model blend, wired
2026-11-12; "v1" = the retired hit-rate blend). Entries saved
before the tag existed have no model in the JSON and load as
"v1". The tag rides along in the JSON/CSV backup exports, so
Lilly's record can be split by era later and v2 has to prove
itself against v1 on settled picks, not on promises.

Paper tracking only. No real bets, no money moved.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from . import profiles as _profiles
from .model import SCORING_VERSION

OPEN = "open"
WON = "won"
LOST = "lost"


def pick_key(created: str, event: str, player: str, market: str, line) -> str:
    """The stable identity of one pick on one day.

    date + event + player + market + line. Re-running the
    algorithm the same day produces the same key for the same
    pick, which is what makes re-runs dedupe instead of
    double-logging.
    """
    return f"{created}|{event}|{player}|{market}|{float(line):g}"


@dataclass
class PickEntry:
    id: str  # same value as key — the stable identity
    key: str
    date: str  # created date, ISO (YYYY-MM-DD)
    sport: str
    event: str  # teams, e.g. "Lakers vs Celtics"
    player: str
    market: str  # e.g. "Points Over"
    line: float
    odds: int  # American odds
    probability: float  # implied chance, as a fraction (0..1)
    mode: str  # "live" or "demo"
    label: str = ""  # display label, e.g. "Player — Points Over 24.5"
    status: str = OPEN  # open | won | lost
    played_by: list = field(default_factory=list)  # profile names
    # Scoring era that produced this pick: "v2" (projection
    # blend) or "v1" (retired hit-rate blend). Entries saved
    # before this field existed load with the "v1" default —
    # everything scored before the v2 wiring IS v1.
    model: str = "v1"


class PickLog:
    def __init__(self, path: Path | str = "data/pick_log.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.entries: list[PickEntry] = self._load()

    def _load(self) -> list[PickEntry]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text())
            return [PickEntry(**item) for item in raw]
        except Exception:
            return []

    def _save(self) -> None:
        self.path.write_text(json.dumps([asdict(e) for e in self.entries], indent=2))

    # ---------- recording ----------

    def record_run(
        self,
        parlays,
        mode: str,
        created: str | None = None,
        model: str = SCORING_VERSION,
    ) -> list[PickEntry]:
        """Log every leg of every parlay as an individual pick.

        Deduped by pick key: a pick that is already logged for
        that date (same event / player / market / line) is not
        logged twice, however many parlays share the leg or
        however often the algorithm re-runs that day.

        `model` is the scoring era of the RUN (the run's scoring
        version — every pick in the run gets it, odds-only picks
        included; an odds-only pick in a v2 run is still a v2-era
        pick). It defaults to the current scoring version.

        Returns only the entries that were newly added.
        """
        created = created or date.today().isoformat()
        known = {e.key for e in self.entries}
        added: list[PickEntry] = []
        for parlay in parlays:
            for pick in parlay.picks:
                market = f"{pick.prop} {pick.side}"
                key = pick_key(created, pick.event, pick.player, market, pick.line)
                if key in known:
                    continue
                known.add(key)
                entry = PickEntry(
                    id=key,
                    key=key,
                    date=created,
                    sport=pick.sport,
                    event=pick.event,
                    player=pick.player,
                    market=market,
                    line=float(pick.line),
                    odds=int(pick.odds),
                    probability=float(pick.probability),
                    mode=mode,
                    label=pick.label,
                    model=model,
                )
                self.entries.append(entry)
                added.append(entry)
        if added:
            self._save()
        return added

    # ---------- settling / played ----------

    def _find(self, key: str) -> PickEntry | None:
        for entry in self.entries:
            if entry.key == key:
                return entry
        return None

    def settle(self, key: str, won: bool) -> bool:
        """Mark one pick won / lost by hand. False if not found."""
        entry = self._find(key)
        if entry is None:
            return False
        entry.status = WON if won else LOST
        self._save()
        return True

    def toggle_played(self, key: str, profile: str) -> bool | None:
        """Mark / unmark that `profile` played this pick.

        Returns the new played state for that profile (True =
        now marked played), or None if the pick is not found.
        Unknown profile names fall back to King's, matching how
        the rest of the app treats stray profile values.
        """
        entry = self._find(key)
        if entry is None:
            return None
        profile = _profiles.normalize_profile(profile)
        if profile in entry.played_by:
            entry.played_by.remove(profile)
            now_played = False
        else:
            entry.played_by.append(profile)
            now_played = True
        self._save()
        return now_played

    def played_by_profile(self, key: str, profile: str) -> bool:
        entry = self._find(key)
        if entry is None:
            return False
        return _profiles.normalize_profile(profile) in entry.played_by

    # ---------- views ----------

    def open_picks(self) -> list[PickEntry]:
        """Unsettled picks, newest first."""
        return [e for e in reversed(self.entries) if e.status == OPEN]

    def settled_picks(self) -> list[PickEntry]:
        """Settled picks, newest first."""
        return [e for e in reversed(self.entries) if e.status != OPEN]

    # ---------- stats ----------

    @staticmethod
    def _block(entries: list[PickEntry]) -> dict:
        settled = [e for e in entries if e.status != OPEN]
        won = sum(1 for e in settled if e.status == WON)
        return {
            "total": len(entries),
            "open": len(entries) - len(settled),
            "settled": len(settled),
            "won": won,
            "lost": len(settled) - won,
            "hit_rate": (won / len(settled)) if settled else None,
        }

    def stats(self, profile: str | None = None) -> dict:
        """Hit-rate numbers for the pick log.

        The headline block covers LIVE picks only. Demo picks are
        counted in their own separate block and never mixed in.
        The "played" block is `profile`'s own played record —
        live picks they marked "I played this" — again with demo
        excluded. With no profile given, "played" covers picks
        played by anyone.
        """
        live = [e for e in self.entries if e.mode == "live"]
        demo = [e for e in self.entries if e.mode != "live"]
        if profile is None:
            played = [e for e in live if e.played_by]
        else:
            profile = _profiles.normalize_profile(profile)
            played = [e for e in live if profile in e.played_by]
        headline = self._block(live)
        headline["demo"] = self._block(demo)
        headline["played"] = self._block(played)
        return headline

    # ---------- backup export ----------

    def to_json(self) -> str:
        """The whole log as a JSON string (played_by stays a list)."""
        return json.dumps([asdict(e) for e in self.entries], indent=2)

    def rows(self) -> list[dict]:
        """Flat dicts for CSV export (played_by joined with ';')."""
        out = []
        for entry in self.entries:
            row = asdict(entry)
            row["played_by"] = ";".join(entry.played_by)
            out.append(row)
        return out
