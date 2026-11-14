"""Tests for the bookmaker data fix (2026-11-14 UI rework).

The parser always knew which sportsbook priced each leg —
the pick log and the parlay ledger simply never kept it.
Now, when NEW records are written:

- PickLog.record_run copies the book off each scored prop;
- PickLog.record_manual copies it off each slip leg;
- Ledger.record_run copies it into each ledger pick dict.

Backward-compatible by design: records saved before the
field existed load with no book ("" / missing key), exports
show the book only when one is recorded, and no code path
ever invents one.

Run: pytest -q
"""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import log_export
from kings_picks.ledger import Ledger
from kings_picks.model import Prop, score_prop
from kings_picks.parlays import Parlay
from kings_picks.pick_log import PickLog
from kings_picks.slip import SlipLeg

TODAY = "2026-11-14"


def _scored(bookmaker="BetMGM", player="Book Star"):
    raw = Prop(
        player=player, sport="NBA", prop="Points", side="Over",
        line=24.5, odds=-110, bookmaker=bookmaker,
        event="Alpha vs Beta", source="live",
    )
    return score_prop(raw, pair_odds=-110)


def _parlay(picks):
    return Parlay(
        size=len(picks), picks=picks, overall_probability=0.5,
        decimal_payout=2.0, expected_value_per_dollar=0.0,
        label="2-pick parlay #1",
    )


# ---------- pick log: record_run / record_manual ----------


def test_record_run_persists_the_book_that_priced_each_leg(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    added = log.record_run([_parlay([_scored("bet365")])], "live", created=TODAY)
    assert len(added) == 1
    assert added[0].bookmaker == "bet365"
    # And it survives the round trip to disk.
    reloaded = PickLog(tmp_path / "pick_log.json")
    assert reloaded.entries[0].bookmaker == "bet365"
    # The flat export rows carry it too (CSV/JSON backups).
    assert reloaded.rows()[0]["bookmaker"] == "bet365"


def test_record_manual_persists_the_book_off_the_slip_leg(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    leg = SlipLeg(
        player="Hand Pick", sport="NBA", prop="Points", side="Over",
        line=10.5, odds=-105, probability=0.6, bookmaker="BetMGM",
        event="A vs B",
    )
    added, _already = log.record_manual(
        [leg], "live", profile="King", created=TODAY
    )
    assert len(added) == 1
    assert added[0].bookmaker == "BetMGM"


def test_old_pick_log_records_load_with_no_book(tmp_path):
    # A record in the pre-fix shape: no "bookmaker" key at all.
    old = [{
        "id": "k", "key": "k", "date": TODAY, "sport": "NBA",
        "event": "A vs B", "player": "Old Timer", "market": "Points Over",
        "line": 10.5, "odds": -110, "probability": 0.55, "mode": "live",
        "label": "Old Timer — Points Over 10.5",
    }]
    path = tmp_path / "pick_log.json"
    path.write_text(json.dumps(old))
    log = PickLog(path)
    assert len(log.entries) == 1
    assert log.entries[0].bookmaker == ""


# ---------- parlay ledger ----------


def test_ledger_record_run_persists_the_book(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record_run([_parlay([_scored("BetMGM")])], "live", profile="King")
    pick = ledger.parlays[0].picks[0]
    assert pick["bookmaker"] == "BetMGM"
    reloaded = Ledger(tmp_path / "ledger.json")
    assert reloaded.parlays[0].picks[0]["bookmaker"] == "BetMGM"


def test_old_ledger_records_have_no_book_and_none_is_invented(tmp_path):
    old = [{
        "id": "abc", "date": TODAY, "size": 1, "label": "old",
        "overall_probability": 0.5, "mode": "live",
        "picks": [{
            "key": "k", "label": "Old — Points Over 10.5",
            "probability": 0.55, "odds": -110, "result": "pending",
        }],
        "settled": False, "profile": "King", "model": "v1",
    }]
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(old))
    ledger = Ledger(path)
    assert "bookmaker" not in ledger.parlays[0].picks[0]


# ---------- Word log export shows the book only when present ----------


def test_log_docx_names_the_book_only_when_the_record_has_one(tmp_path):
    docx = pytest.importorskip("docx")
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record_run(
        [_parlay([_scored("BetMGM")]), _parlay([_scored("")])],
        "live", profile="King",
    )
    data = log_export.build_log_docx(
        ledger.parlays_for_profile("King"), "👑 King"
    )
    document = docx.Document(io.BytesIO(data))
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Book: BetMGM" in text
    # Exactly one leg carries a book — the other names none.
    assert text.count("Book:") == 1
