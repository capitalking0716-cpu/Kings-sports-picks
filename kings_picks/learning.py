"""Lilly's self-learning loop — she recalibrates from her
own settled record. Neko ordered it 2026-11-12: "Have Lilly
learn from the losses and re-educate herself to make better
picks every time."

THE HONEST SHAPE OF IT (same pattern as Ava's lessons in
VaultTec, adapted to a picks app):

- She learns ONLY from settled, REAL picks. An entry teaches
  her only when it is mode="live" AND a human settled it
  Won / Lost on the Results page. Demo picks never teach —
  they are sample data. Open picks never teach — nobody
  knows how they ended yet. MANUAL picks never teach either:
  a pick a profile built by hand on the My Slip page
  (source="manual") is the user's selection, not Lilly's —
  grading her probabilities against picks she did not make
  would contaminate her calibration.
- What she learns is CALIBRATION, nothing more: in each
  bucket (sport x market x side, e.g. NBA Points overs, and
  a coarser sport x side level), compare the chance she
  STATED against how often those picks actually won. If she
  runs hot — says 52%, wins 41% — future picks in that
  bucket get trimmed. If she runs cold, they get bumped.
- A bucket needs at least 30 settled picks before any
  correction switches on. Below that she says so plainly:
  not enough settled picks yet. The correction is shrunk
  toward zero by sample size (full trust only at 100
  settled) and capped at +/-10 percentage points, so a
  small or noisy sample can never swing her numbers hard.
- Corrections apply in the live scoring path, AFTER the
  v2 projection / odds blend (model.score_prop), and every
  pick they touch is tagged in the pick log
  (learning="nba_points_over:-7" — bucket, points moved).
- She is MEASURED, not believed: her Brier score on settled
  picks (lower is better; always saying 50% scores 0.25)
  is tracked as-shown and as the uncorrected model would
  have said it (reconstructed from the tags), so whether
  the learning actually helps is itself on the record.

Learning NEVER promises profit or accuracy. She
recalibrates her numbers against her own record; a
recalibrated chance is still a chance.

Efficiency (Neko's standing rule for Lilly): this module
reads the on-disk pick log and local state files only.
ZERO Odds API calls, no external fetches, ever.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .pick_log import LOST, WON

# A bucket earns a voice only with this many settled picks
# behind it. Fewer is anecdote, not evidence.
MIN_SETTLED = 30
# The shrink factor reaches full trust at this many settled
# picks: factor = min(1, n / FULL_TRUST_SETTLED).
FULL_TRUST_SETTLED = 100
# No single lesson may move a pick by more than this.
MAX_CORRECTION = 0.10
# A correction moving by at least this many points counts as
# a material change — worth a new lesson line.
MATERIAL_CHANGE = 0.02

_SIDES = ("Over", "Under")

HONEST_NOTE = (
    "Learning only recalibrates Lilly's numbers against her own "
    "settled record. It is not a promise of profit or accuracy — "
    "a recalibrated chance is still a chance, and she still "
    "never guarantees a pick."
)


def _slug(text: str) -> str:
    """'Points + Rebounds' -> 'points_rebounds'."""
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def parse_market(market: str) -> tuple[str, str]:
    """Pick-log market ('Points Over') -> (prop, side).

    The log stores market as f"{prop} {side}"; the prop itself
    may contain spaces, so split off only the last word, and
    only when it really is a side.
    """
    text = (market or "").strip()
    prop, _, last = text.rpartition(" ")
    if last in _SIDES and prop:
        return prop, last
    return text, ""


def market_bucket_key(sport: str, prop: str, side: str) -> str:
    """'NBA', 'Points', 'Over' -> 'nba_points_over'."""
    return "_".join(p for p in (_slug(sport), _slug(prop), _slug(side)) if p)


def side_bucket_key(sport: str, side: str) -> str:
    """'NBA', 'Over' -> 'nba_over'."""
    return "_".join(p for p in (_slug(sport), _slug(side)) if p)


def tag_delta(tag: str) -> float:
    """'nba_points_over:-7' -> -0.07. No tag / junk -> 0.0.

    The tag stores whole percentage points (what a human
    reads); the reconstruction of an uncorrected probability
    from it is therefore approximate to within half a point,
    which the Brier bookkeeping discloses.
    """
    if not tag or ":" not in tag:
        return 0.0
    try:
        return float(tag.rsplit(":", 1)[1]) / 100.0
    except ValueError:
        return 0.0


def make_tag(bucket_key: str, delta: float) -> str:
    """Inverse of tag_delta: bucket + signed whole points."""
    return f"{bucket_key}:{delta * 100:+.0f}"


def shrink_factor(n: int) -> float:
    """How much of the raw calibration gap to trust at n
    settled picks: 0 below the activation threshold, then a
    straight line up to full trust at FULL_TRUST_SETTLED."""
    if n < MIN_SETTLED:
        return 0.0
    return min(1.0, n / FULL_TRUST_SETTLED)


def correction_for(n: int, mean_stated: float, hit_rate: float) -> float:
    """The correction one bucket applies, in probability
    points: raw gap (actual minus stated), shrunk by sample
    size, capped at +/-MAX_CORRECTION. 0.0 below MIN_SETTLED.
    """
    if n < MIN_SETTLED:
        return 0.0
    raw = (hit_rate - mean_stated) * shrink_factor(n)
    return max(-MAX_CORRECTION, min(MAX_CORRECTION, raw))


@dataclass
class Bucket:
    """One bucket's settled record and the correction it earns."""

    key: str
    level: str  # "market" (sport x prop x side) or "side" (sport x side)
    sport: str
    prop: str  # "" for side-level buckets
    side: str
    settled: int = 0
    wins: int = 0
    mean_stated: float = 0.0
    correction: float = 0.0

    @property
    def hit_rate(self) -> float | None:
        return (self.wins / self.settled) if self.settled else None

    @property
    def active(self) -> bool:
        return self.correction != 0.0

    @property
    def needed(self) -> int:
        """Settled picks still missing before this bucket can
        switch a correction on."""
        return max(0, MIN_SETTLED - self.settled)

    @property
    def label(self) -> str:
        """'NBA Points overs' / 'NFL Overs' — plural side word,
        the way Lilly talks about a class of picks."""
        side_word = f"{self.side.lower()}s" if self.side else ""
        parts = [self.sport, self.prop, side_word]
        return " ".join(p for p in parts if p)


def teaching_entries(entries) -> list:
    """The only entries allowed to teach: Lilly's OWN picks,
    live AND settled. Demo never teaches; open picks never
    teach; manual (hand-built) picks never teach — they are
    the user's selections, not hers. Entries saved before
    the source tag existed have no "manual" marker and are
    hers by default."""
    return [
        e
        for e in entries
        if e.mode == "live"
        and e.status in (WON, LOST)
        and getattr(e, "source", "lilly") != "manual"
    ]


def compute_buckets(entries) -> dict[str, Bucket]:
    """Settled-live entries -> every bucket's record.

    Each entry feeds TWO buckets: its market bucket
    (sport x prop x side) and its side bucket (sport x side).
    Application (LearningEngine.lookup) uses the market
    bucket first and only falls back to the side level, so
    no pick is ever corrected twice.
    """
    buckets: dict[str, Bucket] = {}
    sums: dict[str, float] = {}

    def feed(key: str, level: str, sport: str, prop: str, side: str, entry) -> None:
        bucket = buckets.get(key)
        if bucket is None:
            bucket = buckets[key] = Bucket(
                key=key, level=level, sport=sport, prop=prop, side=side
            )
        bucket.settled += 1
        if entry.status == WON:
            bucket.wins += 1
        sums[key] = sums.get(key, 0.0) + float(entry.probability)

    for entry in teaching_entries(entries):
        prop, side = parse_market(entry.market)
        feed(
            market_bucket_key(entry.sport, prop, side),
            "market", entry.sport, prop, side, entry,
        )
        if side:
            feed(
                side_bucket_key(entry.sport, side),
                "side", entry.sport, "", side, entry,
            )
    for key, bucket in buckets.items():
        bucket.mean_stated = sums[key] / bucket.settled
        bucket.correction = correction_for(
            bucket.settled, bucket.mean_stated, bucket.hit_rate or 0.0
        )
    return buckets


def brier_scores(entries) -> dict:
    """Calibration measured, on settled live picks only.

    as_shown: the probabilities Lilly actually showed (the
    ones in the log — post-correction where a correction
    applied). uncorrected: what the same picks' probabilities
    would have been with NO learning — reconstructed from
    the learning tags (whole-point rounding, so approximate
    within half a point per tagged pick). Both are None
    until at least one live pick has settled.
    """
    settled = teaching_entries(entries)
    if not settled:
        return {"settled": 0, "as_shown": None, "uncorrected": None}
    shown = 0.0
    plain = 0.0
    for entry in settled:
        y = 1.0 if entry.status == WON else 0.0
        p = float(entry.probability)
        shown += (p - y) ** 2
        plain += (p - tag_delta(getattr(entry, "learning", "")) - y) ** 2
    n = len(settled)
    return {"settled": n, "as_shown": shown / n, "uncorrected": plain / n}


def _lesson_text(bucket: Bucket, kind: str) -> str:
    """One plain-language lesson, in Lilly's voice, with the
    numbers in it — the same discipline as Ava's lessons."""
    said = bucket.mean_stated
    won = bucket.hit_rate or 0.0
    points = abs(bucket.correction) * 100
    if kind == "cleared":
        return (
            f"Lesson: my {bucket.label} are back in line — they won "
            f"{won:.0%} against the {said:.0%} I said across "
            f"{bucket.settled} settled picks, so I stopped adjusting them."
        )
    if bucket.correction < 0:
        move = f"I now trim those picks by about {points:.0f} points"
    else:
        move = f"I now bump those picks up by about {points:.0f} points"
    return (
        f"Lesson: my {bucket.label} won {won:.0%} when I said "
        f"{said:.0%} across {bucket.settled} settled picks — {move}."
    )


class LearningEngine:
    """Owns data/learned.json (the current learned state)
    and data/lilly-lessons.jsonl (the lesson diary).

    refresh() recomputes everything from the pick log, saves
    the state when it changed, and appends a lesson whenever
    a correction switches on, switches off, or moves by at
    least MATERIAL_CHANGE. lookup() is what the scoring path
    asks, per prop. Everything here reads disk only — zero
    Odds API calls.
    """

    def __init__(
        self,
        state_path: Path | str = "data/learned.json",
        lessons_path: Path | str = "data/lilly-lessons.jsonl",
    ):
        self.state_path = Path(state_path)
        self.lessons_path = Path(lessons_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.lessons_path.parent.mkdir(parents=True, exist_ok=True)
        self.state: dict = self._load()

    # ---------- persistence ----------

    def _load(self) -> dict:
        if self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text())
                if isinstance(state, dict) and "buckets" in state:
                    return state
            except Exception:
                pass
        return self._empty_state()

    @staticmethod
    def _empty_state() -> dict:
        return {
            "version": 1,
            "updated": "",
            "settled_live": 0,
            "buckets": {},
            "active_corrections": {},
            "brier": {"settled": 0, "as_shown": None, "uncorrected": None},
            "brier_history": [],
            "honest_note": HONEST_NOTE,
        }

    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def state_json(self) -> str:
        """The learned state as a JSON string (backup export)."""
        return json.dumps(self.state, indent=2)

    def lessons(self) -> list[dict]:
        """Every lesson she has written herself, oldest first."""
        if not self.lessons_path.exists():
            return []
        out = []
        for line in self.lessons_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out

    def lessons_text(self) -> str:
        """The raw lessons file (backup export)."""
        if self.lessons_path.exists():
            return self.lessons_path.read_text()
        return ""

    def _append_lesson(self, bucket: Bucket, kind: str, today: str) -> dict:
        lesson = {
            "date": today,
            "bucket": bucket.key,
            "kind": kind,
            "text": _lesson_text(bucket, kind),
            "settled": bucket.settled,
            "mean_stated": round(bucket.mean_stated, 4),
            "hit_rate": round(bucket.hit_rate or 0.0, 4),
            "correction": round(bucket.correction, 4),
        }
        with self.lessons_path.open("a") as fh:
            fh.write(json.dumps(lesson) + "\n")
        return lesson

    # ---------- the loop ----------

    def refresh(self, source, today: str | None = None) -> dict:
        """Recompute the learned state from the pick log.

        `source` is a PickLog or a bare list of its entries.
        Saves state and appends lessons ONLY when something
        actually changed, so the app can call this on every
        page render without churning files. Returns the state.
        """
        today = today or date.today().isoformat()
        entries = getattr(source, "entries", source)
        buckets = compute_buckets(entries)
        brier = brier_scores(entries)

        new_corrections = {
            key: round(b.correction, 4) for key, b in buckets.items() if b.active
        }
        old_corrections = self.state.get("active_corrections", {})

        new_state = {
            "version": 1,
            "updated": self.state.get("updated", ""),
            "settled_live": len(teaching_entries(entries)),
            "buckets": {
                key: {
                    **asdict(b),
                    "hit_rate": (round(b.hit_rate, 4) if b.hit_rate is not None else None),
                    "active": b.active,
                    "needed": b.needed,
                    "label": b.label,
                }
                for key, b in sorted(buckets.items())
            },
            "active_corrections": new_corrections,
            "brier": {
                "settled": brier["settled"],
                "as_shown": (round(brier["as_shown"], 4) if brier["as_shown"] is not None else None),
                "uncorrected": (round(brier["uncorrected"], 4) if brier["uncorrected"] is not None else None),
            },
            "brier_history": list(self.state.get("brier_history", [])),
            "honest_note": HONEST_NOTE,
        }

        comparable_old = {k: v for k, v in self.state.items() if k != "updated"}
        comparable_new = {k: v for k, v in new_state.items() if k != "updated"}
        if comparable_old == comparable_new:
            return self.state

        # Changes worth a lesson: a correction switching on,
        # switching off, or moving materially.
        for key in sorted(set(new_corrections) | set(old_corrections)):
            old = old_corrections.get(key, 0.0)
            new = new_corrections.get(key, 0.0)
            if old == new:
                continue
            bucket = buckets.get(key)
            if bucket is None:
                continue  # bucket vanished entirely — nothing to teach from
            if old == 0.0:
                kind = "activated"
            elif new == 0.0:
                kind = "cleared"
            elif abs(new - old) >= MATERIAL_CHANGE:
                kind = "changed"
            else:
                continue
            self._append_lesson(bucket, kind, today)

        history = new_state["brier_history"]
        if not history or history[-1]["settled"] != new_state["settled_live"]:
            history.append(
                {
                    "date": today,
                    "settled": new_state["settled_live"],
                    "as_shown": new_state["brier"]["as_shown"],
                    "uncorrected": new_state["brier"]["uncorrected"],
                }
            )
        new_state["updated"] = today
        self.state = new_state
        self._save()
        return self.state

    # ---------- scoring side ----------

    def lookup(self, sport: str, prop: str, side: str) -> dict | None:
        """The correction for one prop, or None.

        Market bucket first; the sport x side bucket only as
        a fallback, so a pick is never corrected twice.
        Returns {bucket, delta, tag, note} — everything
        model.score_prop needs to apply and explain it.
        """
        buckets = self.state.get("buckets", {})
        for key in (
            market_bucket_key(sport, prop, side),
            side_bucket_key(sport, side),
        ):
            record = buckets.get(key)
            if not record or not record.get("active"):
                continue
            delta = float(record["correction"])
            return {
                "bucket": key,
                "delta": delta,
                "tag": make_tag(key, delta),
                "note": self._note_for(record, delta),
            }
        return None

    @staticmethod
    def _note_for(record: dict, delta: float) -> str:
        """The why-text sentence, in Lilly's plain words,
        whenever a correction moves a pick's number."""
        label = record.get("label") or record["key"]
        said = record.get("mean_stated") or 0.0
        won = record.get("hit_rate") or 0.0
        n = record.get("settled") or 0
        points = abs(delta) * 100
        if delta < 0:
            return (
                f"Learning: Lilly has learned her {label} run hot "
                f"lately — across {n} settled picks she said about "
                f"{said:.0%} and they won {won:.0%}, so she trimmed "
                f"this one by about {points:.0f} points."
            )
        return (
            f"Learning: Lilly has learned her {label} run cold "
            f"lately — across {n} settled picks she said about "
            f"{said:.0%} and they won {won:.0%}, so she bumped "
            f"this one up by about {points:.0f} points."
        )
