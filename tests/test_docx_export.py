"""Tests for the Word (.docx) downloads (Neko's order
2026-11-14: "My logs should all be word files I can Read
back"):

- "⬇️ Download my picks (Word)" — the SAME plain-English
  content as the text download, rendered from the same
  blocks (kings_picks/picks_export.py build_picks_docx):
  a real .docx that opens with python-docx, carrying the
  KSB- ticket numbers, the leg lines, the dropped-line
  notes, the pre-built flag and the special-picks section.
- "⬇️ My pick log — Word" — the profile's ledger record
  (kings_picks/log_export.py build_log_docx): date, ticket,
  legs with lines, and each result in plain words.
- Neither file holds keys, PINs or raw JSON — picks only,
  same promise as the text file.
- Empty means empty: no picks / no record -> b"".

The whole module skips cleanly when python-docx is not
installed (it is a deployed-app dependency, in
requirements.txt; the text export never depends on it).

Run: pytest -q
"""

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

docx = pytest.importorskip("docx")

from kings_picks import log_export, picks_export
from kings_picks.ledger import LedgerParlay
from kings_picks.model import Prop, score_prop
from kings_picks.parlays import Parlay

PULLED = "2026-11-12T21:37:00+00:00"  # = 04:37 PM in Detroit (EST)
TEAMS = {
    "NBA": {"josh giddey": "CHI", "fixture0": "DET"},
    "NFL": {"jahmyr gibbs": "DET"},
}


def _scored(player="Josh Giddey", sport="NBA", prop="Assists", side="Over",
            line=4.5, odds=-110, form=True,
            event="Atlanta Hawks vs Chicago Bulls", **kw):
    raw = Prop(
        player=player, sport=sport, prop=prop, side=side, line=line,
        odds=odds, bookmaker="BetMGM", event=event,
        source="live", **kw,
    )
    if form:
        return score_prop(
            raw, pair_odds=-110, form_probability=0.7,
            projected_value=6.0, projection_games=10,
        )
    return score_prop(raw, pair_odds=-110)


def _parlay(picks, probability=0.70, style="standard", label="3-pick parlay #1"):
    return Parlay(
        size=len(picks), picks=picks, overall_probability=probability,
        decimal_payout=2.4, expected_value_per_dollar=0.1,
        label=label, style=style,
    )


def _docx_text(data: bytes) -> str:
    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs)


# ---------- the picks Word file ----------


def test_picks_docx_is_a_valid_word_file_with_the_ticket():
    leg = _scored()
    leg.form_verdict = "STRONG"
    leg.form_record = "9/10"
    legs = [
        leg,
        _scored(player="Fixture1", prop="Points", line=12.5),
        _scored(player="Fixture2", prop="Rebounds", line=6.5),
    ]
    parlay = _parlay(legs)
    data = picks_export.build_picks_docx(
        pulled_at=PULLED, parlays=[parlay], teams=TEAMS,
    )
    assert data[:2] == b"PK"  # a real .docx (zip) file
    text = _docx_text(data)
    assert "KINGS SPORTS BOOKS PICKS" in text
    assert "Pulled: Thu Nov 12, 2026" in text
    # Same ticket number on the Word file as on screen / in text.
    assert f"TICKET {picks_export.ticket_number(parlay)}" in text
    assert "3-pick parlay (standard lines)" in text
    assert "Overall chance: about 7 in 10 (70%)." in text
    # A known leg line, and its plain-English sentence.
    assert "1. Josh Giddey (Chicago Bulls) — Over 4.5 assists" in text
    assert "What has to happen: Giddey gets 5 or more assists." in text
    assert (
        "Form grade: Strong — he hit this line in 9 of his last 10 games."
        in text
    )


def test_picks_docx_matches_the_text_download():
    leg = _scored()
    args = dict(pulled_at=PULLED, parlays=[_parlay([leg])], teams=TEAMS)
    word_text = _docx_text(picks_export.build_picks_docx(**args))
    plain_text = picks_export.build_picks_text(**args)
    for line in plain_text.splitlines():
        line = line.strip()
        if line and set(line) != {"-"}:
            assert line in word_text


def test_picks_docx_king_special_and_prebuilt_sections():
    king_leg = _scored(line=4.5)
    king_leg.base_line = 8.5
    td_leg = _scored(
        player="Jahmyr Gibbs", sport="NFL", prop="Anytime Touchdown",
        line=0.5, event="Detroit Lions vs Green Bay Packers",
    )
    data = picks_export.build_picks_docx(
        pulled_at=PULLED,
        king_parlays=[_parlay([king_leg], style="king", label="1-pick parlay #1")],
        special_parlays=[
            _parlay([td_leg], style="special", label="2-pick parlay #1")
        ],
        teams=TEAMS,
        game_date="2026-11-16",
        today="2026-11-14",
    )
    text = _docx_text(data)
    assert "KING'S STYLE TICKETS" in text
    assert "this line was dropped down from 8.5 to 4.5" in text
    assert "SPECIAL PICKS — ONE ANYTIME TD FROM EACH TEAM" in text
    assert "Gibbs scores a touchdown at any point in the game." in text
    assert "Games for: Monday, November 16, 2026" in text
    assert "Pre-built — an early look at that day's games." in text


def test_picks_docx_empty_means_no_file():
    assert picks_export.build_picks_docx() == b""
    assert picks_export.build_picks_docx(parlays=[], king_parlays=[]) == b""


def test_picks_docx_holds_picks_only():
    leg = _scored()
    data = picks_export.build_picks_docx(
        pulled_at=PULLED, parlays=[_parlay([leg])], teams=TEAMS,
    )
    text = _docx_text(data)
    assert "LILLY_ODDS_API_KEY" not in text
    assert "KING_PIN" not in text and "KOHEN_PIN" not in text


# ---------- the pick-log Word file ----------


def _ledger_parlay(picks, label="3-pick parlay #1", date="2026-11-13",
                   probability=0.42, settled=False):
    return LedgerParlay(
        id="test1234", date=date, size=len(picks), label=label,
        overall_probability=probability, mode="live", picks=picks,
        settled=settled, profile="King", model="v2",
    )


def test_log_docx_contains_a_known_entry():
    open_parlay = _ledger_parlay([
        {"key": "k1", "label": "Josh Giddey — Assists Over 4.5",
         "probability": 0.7, "odds": -110, "result": "won"},
        {"key": "k2", "label": "Fixture1 — Points Over 12.5",
         "probability": 0.6, "odds": -105, "result": "pending"},
    ])
    won_parlay = _ledger_parlay(
        [
            {"key": "k3", "label": "Jahmyr Gibbs — Anytime Touchdown Over 0.5",
             "probability": 0.8, "odds": -150, "result": "won"},
        ],
        label="1-pick parlay #1", date="2026-11-12", settled=True,
    )
    data = log_export.build_log_docx([open_parlay, won_parlay], "👑 King")
    assert data[:2] == b"PK"
    text = _docx_text(data)
    assert "👑 King's pick log" in text
    assert "2 tickets on this record · 1 settled · 1 won." in text
    # The known entry: date, ticket, leg with its line, result.
    assert "Fri Nov 13, 2026 — 3-pick parlay #1" in text
    assert "1. Josh Giddey — Assists Over 4.5" in text
    assert "Result: Won" in text
    assert "Result: Still open" in text
    assert "Ticket result: Still open — 1 won, 0 lost, 1 still to play." in text
    assert "Thu Nov 12, 2026 — 1-pick parlay #1" in text
    assert "Ticket result: ✅ Won — every pick on the ticket won." in text


def test_log_docx_lost_ticket_says_so():
    lost = _ledger_parlay(
        [
            {"key": "k1", "label": "Josh Giddey — Assists Over 4.5",
             "probability": 0.7, "odds": -110, "result": "lost"},
        ],
        label="1-pick parlay #1", settled=True,
    )
    text = _docx_text(log_export.build_log_docx([lost], "👑 King"))
    assert "Result: Lost" in text
    assert "Ticket result: ❌ Lost." in text


def test_log_docx_empty_record_means_no_file():
    assert log_export.build_log_docx([], "👑 King") == b""
    assert log_export.build_log_docx(None, "👑 King") == b""


def test_log_docx_holds_no_secrets():
    parlay = _ledger_parlay([
        {"key": "k1", "label": "Josh Giddey — Assists Over 4.5",
         "probability": 0.7, "odds": -110, "result": "won"},
    ])
    text = _docx_text(log_export.build_log_docx([parlay], "👑 King"))
    assert "LILLY_ODDS_API_KEY" not in text
    assert "KING_PIN" not in text and "KOHEN_PIN" not in text
