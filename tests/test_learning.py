"""Tests for Lilly's self-learning loop (kings_picks/learning.py).

What is being pinned down here:

- She learns ONLY from settled, live picks. Demo entries and
  open entries never teach, however many there are.
- A bucket needs 30 settled picks before any correction
  switches on; below that the lookup honestly returns None.
- The correction math: raw gap (actual hit rate minus the
  average stated chance), shrunk by min(1, n/100), capped at
  +/-10 percentage points.
- A correction actually moves a scored pick, is explained in
  the why-text, and is tagged onto the pick-log entry.
- Lessons are written when a correction switches on (and
  when it switches back off), exactly once per change.
- The learned state round-trips through disk, and Brier is
  tracked as-shown vs uncorrected (reconstructed from tags).

Run: pytest -q
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import learning
from kings_picks.learning import (
    LearningEngine,
    brier_scores,
    compute_buckets,
    correction_for,
    teaching_entries,
)
from kings_picks.model import Prop, score_prop
from kings_picks.pick_log import PickEntry, PickLog

TODAY = "2026-11-12"


def entry(
    n: int,
    sport: str = "NBA",
    market: str = "Points Over",
    probability: float = 0.52,
    status: str = "won",
    mode: str = "live",
    tag: str = "",
) -> PickEntry:
    key = f"2026-11-0{1 + n % 9}|Game {n}|Player {n}|{market}|24.5"
    return PickEntry(
        id=key,
        key=key,
        date="2026-11-01",
        sport=sport,
        event=f"Game {n}",
        player=f"Player {n}",
        market=market,
        line=24.5,
        odds=-110,
        probability=probability,
        mode=mode,
        label=f"Player {n} — {market} 24.5",
        status=status,
        model="v2",
        learning=tag,
    )


def settled(n: int, wins: int, **kwargs) -> list[PickEntry]:
    """n settled entries, the first `wins` of them won."""
    return [
        entry(i, status="won" if i < wins else "lost", **kwargs)
        for i in range(n)
    ]


def engine(tmp_path) -> LearningEngine:
    return LearningEngine(tmp_path / "learned.json", tmp_path / "lessons.jsonl")


# ---------- who is allowed to teach ----------


def test_teaching_entries_exclude_demo_and_open():
    entries = (
        settled(5, 3)  # live + settled: teach
        + settled(5, 3, mode="demo")  # demo: never
        + [entry(90 + i, status="open") for i in range(5)]  # open: never
    )
    teaching = teaching_entries(entries)
    assert len(teaching) == 5
    assert all(e.mode == "live" and e.status in ("won", "lost") for e in teaching)


def test_demo_record_teaches_nothing(tmp_path):
    eng = engine(tmp_path)
    state = eng.refresh(settled(100, 40, mode="demo"), today=TODAY)
    assert state["settled_live"] == 0
    assert state["buckets"] == {}
    assert state["active_corrections"] == {}
    assert eng.lessons() == []


# ---------- the correction math ----------


def test_correction_math_shrink_and_cap():
    # Below the threshold: no voice at all.
    assert correction_for(29, 0.90, 0.0) == 0.0
    # n=30: gap -0.20, shrink 0.30 -> -0.06.
    assert correction_for(30, 0.60, 0.40) == pytest.approx(-0.06)
    # n=50, running cold: gap +0.10, shrink 0.50 -> +0.05.
    assert correction_for(50, 0.50, 0.60) == pytest.approx(0.05)
    # Full trust at 100, gap -0.11 -> capped at -0.10.
    assert correction_for(100, 0.52, 0.41) == pytest.approx(-0.10)
    assert correction_for(200, 0.40, 0.90) == pytest.approx(0.10)
    # Perfectly calibrated: no correction however big the sample.
    assert correction_for(100, 0.55, 0.55) == 0.0


def test_bucket_below_threshold_stays_silent(tmp_path):
    eng = engine(tmp_path)
    state = eng.refresh(settled(29, 0), today=TODAY)  # all lost, still silent
    bucket = state["buckets"]["nba_points_over"]
    assert bucket["settled"] == 29
    assert bucket["needed"] == 1
    assert bucket["active"] is False
    assert eng.lookup("NBA", "Points", "Over") is None


def test_bucket_stats_and_correction_from_entries():
    # 63 settled, said 52%, won 26 of 63 (41.3%).
    buckets = compute_buckets(settled(63, 26, probability=0.52))
    bucket = buckets["nba_points_over"]
    assert bucket.settled == 63
    assert bucket.mean_stated == pytest.approx(0.52)
    assert bucket.hit_rate == pytest.approx(26 / 63)
    # gap (0.4127 - 0.52) x shrink 0.63
    assert bucket.correction == pytest.approx((26 / 63 - 0.52) * 0.63)
    assert bucket.active


# ---------- lookup: market bucket first, side bucket fallback ----------


def test_lookup_prefers_market_bucket_and_falls_back_to_side(tmp_path):
    entries = (
        settled(30, 12, market="Points Over", probability=0.70)  # corr -0.09
        + settled(30, 18, market="Rebounds Over", probability=0.50)  # corr +0.03
    )
    eng = engine(tmp_path)
    eng.refresh(entries, today=TODAY)
    points = eng.lookup("NBA", "Points", "Over")
    assert points is not None
    assert points["bucket"] == "nba_points_over"
    assert points["delta"] == pytest.approx(-0.09)
    # Assists has no market bucket of its own: the NBA Over
    # side bucket (60 settled, said 60%, won 50% -> -0.06)
    # answers instead.
    assists = eng.lookup("NBA", "Assists", "Over")
    assert assists is not None
    assert assists["bucket"] == "nba_over"
    assert assists["delta"] == pytest.approx(-0.06)
    # Nothing learned about Unders at all.
    assert eng.lookup("NBA", "Points", "Under") is None


# ---------- applied in scoring, tagged in the log ----------


def test_correction_moves_the_pick_and_tags_the_log(tmp_path):
    eng = engine(tmp_path)
    eng.refresh(settled(63, 26, probability=0.52), today=TODAY)
    learned = eng.lookup("NBA", "Points", "Over")
    assert learned is not None

    prop = Prop(player="Some Guard", sport="NBA", prop="Points",
                side="Over", line=24.5, odds=-110, event="A vs B")
    plain = score_prop(prop, pair_odds=-110)
    moved = score_prop(
        prop, pair_odds=-110,
        learning_delta=learned["delta"],
        learning_tag=learned["tag"],
        learning_note=learned["note"],
    )
    assert moved.probability == pytest.approx(plain.probability + learned["delta"])
    assert moved.learning == learned["tag"]
    assert moved.learning.startswith("nba_points_over:-")
    assert "run hot" in moved.why
    assert "trimmed" in moved.why
    # No correction passed -> no tag, no note, same number.
    assert plain.learning == ""
    assert "Learning:" not in plain.why

    log = PickLog(tmp_path / "pick_log.json")
    parlay = SimpleNamespace(picks=[moved])
    added = log.record_run([parlay], mode="live", created=TODAY)
    assert len(added) == 1
    assert added[0].learning == learned["tag"]
    assert added[0].probability == pytest.approx(moved.probability)
    # The tag rides along in the backup export rows too.
    assert log.rows()[0]["learning"] == learned["tag"]


def test_old_pick_log_json_loads_without_learning_field(tmp_path):
    path = tmp_path / "pick_log.json"
    raw = [
        {
            "id": "k", "key": "k", "date": "2026-11-01", "sport": "NBA",
            "event": "A vs B", "player": "P", "market": "Points Over",
            "line": 24.5, "odds": -110, "probability": 0.55,
            "mode": "live", "label": "P — Points Over 24.5",
            "status": "won", "played_by": [], "model": "v1",
        }
    ]
    path.write_text(json.dumps(raw))
    log = PickLog(path)
    assert log.entries[0].learning == ""


# ---------- lessons ----------


def test_lesson_written_once_on_activation(tmp_path):
    eng = engine(tmp_path)
    fixture = settled(63, 26, probability=0.52)
    eng.refresh(fixture, today=TODAY)
    lessons = eng.lessons()
    # One lesson per activated bucket: the market bucket AND
    # the NBA Over side bucket both switch on from this data.
    kinds = {(l["bucket"], l["kind"]) for l in lessons}
    assert ("nba_points_over", "activated") in kinds
    market_lesson = next(l for l in lessons if l["bucket"] == "nba_points_over")
    assert "won 41%" in market_lesson["text"]
    assert "said 52%" in market_lesson["text"]
    assert "63 settled picks" in market_lesson["text"]
    assert "trim" in market_lesson["text"]
    # Refreshing the same record again writes nothing new.
    eng.refresh(fixture, today=TODAY)
    assert len(eng.lessons()) == len(lessons)


def test_lesson_written_when_correction_clears(tmp_path):
    eng = engine(tmp_path)
    eng.refresh(settled(30, 12, probability=0.60), today=TODAY)
    assert any(l["kind"] == "activated" for l in eng.lessons())
    # Add a balancing batch: totals become said 50%, won 50%.
    more = settled(30, 18, probability=0.40)
    more = [entry(100 + i, status=e.status, probability=0.40) for i, e in enumerate(more)]
    eng.refresh(settled(30, 12, probability=0.60) + more, today=TODAY)
    cleared = [l for l in eng.lessons() if l["kind"] == "cleared"]
    market_cleared = [l for l in cleared if l["bucket"] == "nba_points_over"]
    assert market_cleared
    assert "stopped adjusting" in market_cleared[0]["text"]


# ---------- measured, not claimed: Brier + state ----------


def test_brier_as_shown_vs_uncorrected_from_tags():
    entries = [
        # Shown 57% after a -7 trim; uncorrected model said 64%.
        entry(1, probability=0.57, status="won", tag="nba_points_over:-7"),
        entry(2, probability=0.40, status="lost"),
    ]
    scores = brier_scores(entries)
    assert scores["settled"] == 2
    assert scores["as_shown"] == pytest.approx(((0.57 - 1) ** 2 + 0.40**2) / 2)
    assert scores["uncorrected"] == pytest.approx(((0.64 - 1) ** 2 + 0.40**2) / 2)


def test_state_round_trips_and_history_tracks(tmp_path):
    eng = engine(tmp_path)
    state = eng.refresh(settled(40, 20, probability=0.55), today=TODAY)
    assert state["brier_history"][-1]["settled"] == 40
    assert json.loads(eng.state_json()) == state
    # A fresh engine over the same files sees the same state.
    again = engine(tmp_path)
    assert again.state == state
    # More settled picks -> history grows, state updates.
    state2 = again.refresh(settled(45, 22, probability=0.55), today=TODAY)
    assert state2["settled_live"] == 45
    assert state2["brier_history"][-1]["settled"] == 45
    # Exports: the lessons export is the raw file the app offers.
    assert again.lessons_text() == (tmp_path / "lessons.jsonl").read_text()


def test_refresh_with_no_settled_live_picks_is_quiet(tmp_path):
    eng = engine(tmp_path)
    state = eng.refresh([], today=TODAY)
    assert state["settled_live"] == 0
    assert state["brier"]["as_shown"] is None
    assert eng.lookup("NBA", "Points", "Over") is None
    assert eng.lessons() == []
    # Nothing changed, so no learned.json gets churned out.
    assert not (tmp_path / "learned.json").exists()
