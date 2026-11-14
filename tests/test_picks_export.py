"""Tests for the plain-English picks download
(kings_picks/picks_export.py, Neko's order 2026-11-14:
"download the odds pull with her picks — plain English
understandable information").

What is being pinned down:

- The file is built from the parlays the app already holds
  (restored stored slate included) — no pull, no rescore.
- Every ticket reads in plain English: pull time, KSB-
  ticket number (the same recipe as the on-screen ticket),
  each leg as "Player (Team) — what has to happen", the
  form grade in words with its last-10 record, and the
  overall chance in words ("about 7 in 10").
- A King-style leg names the line it was dropped from.
- No parlays -> no file (""); the app shows a note instead.
- The file holds picks only: no JSON, no keys, no PINs.

Run: pytest -q
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import picks_export, slate_store
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


# ---------- empty slate ----------


def test_no_parlays_means_no_file():
    assert picks_export.build_picks_text() == ""
    assert picks_export.build_picks_text(parlays=[], king_parlays=[]) == ""
    assert picks_export.build_picks_text(pulled_at=PULLED) == ""


# ---------- the plain-English ticket ----------


def test_ticket_reads_in_plain_english():
    leg = _scored()
    leg.form_verdict = "STRONG"
    leg.form_record = "9/10"
    legs = [
        leg,
        _scored(player="Fixture1", prop="Points", line=12.5),
        _scored(player="Fixture2", prop="Rebounds", line=6.5),
    ]
    text = picks_export.build_picks_text(
        pulled_at=PULLED, parlays=[_parlay(legs)], teams=TEAMS,
    )
    # When the pull happened, in Detroit time.
    assert "Pulled: Thu Nov 12, 2026" in text
    assert "04:37 PM ET" in text
    # Ticket heading and the honest overall chance, in words.
    assert "3-pick parlay (standard lines)" in text
    assert "Overall chance: about 7 in 10 (70%)" in text
    # The leg, exactly the promised shape.
    assert "Josh Giddey (Chicago Bulls) — Over 4.5 assists" in text
    assert "What has to happen: Giddey gets 5 or more assists." in text
    assert (
        "Form grade: Strong — he hit this line in 9 of his last 10 games."
        in text
    )
    assert "Lilly's chance for this pick:" in text
    assert "Game: Atlanta Hawks vs Chicago Bulls" in text
    assert "Book: BetMGM" in text


def test_ticket_number_matches_the_on_screen_recipe():
    leg = _scored()
    parlay = _parlay([leg])
    seed = "|".join(
        [parlay.label, "standard"] + [f"{p.key}:{p.odds}" for p in parlay.picks]
    )
    expected = "KSB-" + hashlib.sha256(seed.encode()).hexdigest()[:8].upper()
    assert picks_export.ticket_number(parlay) == expected
    text = picks_export.build_picks_text(parlays=[parlay], teams=TEAMS)
    assert f"TICKET {expected}" in text


def test_shaky_and_lean_grades_in_words():
    shaky = _scored(player="Fixture0", prop="Points", line=10.5)
    shaky.form_verdict = "SHAKY"
    shaky.form_record = "3/10 (1 push)"
    lean = _scored(player="Fixture1", prop="Points", line=12.5)
    lean.form_verdict = "LEAN"
    lean.form_record = "6/10"
    text = picks_export.build_picks_text(
        parlays=[_parlay([shaky, lean], label="2-pick parlay #1")], teams=TEAMS,
    )
    assert "Form grade: Shaky — he hit this line in 3 of his last 10 games." in text
    assert "Form grade: Lean — he hit this line in 6 of his last 10 games." in text


def test_ungraded_leg_prints_no_form_line():
    text = picks_export.build_picks_text(
        parlays=[_parlay([_scored()])], teams=TEAMS,
    )
    assert "Form grade" not in text


def test_odds_only_leg_is_flagged_plainly():
    leg = _scored(form=False)
    assert not leg.has_form_data
    text = picks_export.build_picks_text(parlays=[_parlay([leg])], teams=TEAMS)
    assert "odds-only pick" in text


def test_king_ticket_names_the_dropped_line():
    leg = _scored(line=4.5)
    leg.base_line = 8.5
    leg.king_note = ""
    text = picks_export.build_picks_text(
        king_parlays=[_parlay([leg], style="king")], teams=TEAMS,
    )
    assert "KING'S STYLE TICKETS" in text
    assert "1-pick parlay (King's style — dropped lines)" in text
    assert "this line was dropped down from 8.5 to 4.5" in text


def test_anytime_touchdown_leg():
    leg = _scored(
        player="Jahmyr Gibbs", sport="NFL", prop="Anytime Touchdown",
        line=0.5, event="Detroit Lions vs Green Bay Packers",
    )
    text = picks_export.build_picks_text(parlays=[_parlay([leg])], teams=TEAMS)
    assert "Jahmyr Gibbs (Detroit Lions) — Anytime Touchdown" in text
    assert "Gibbs scores a touchdown at any point in the game." in text


def test_unknown_player_prints_without_a_team():
    leg = _scored(player="Mystery Man")
    text = picks_export.build_picks_text(parlays=[_parlay([leg])], teams=TEAMS)
    assert "Mystery Man — Over 4.5 assists" in text
    assert "Mystery Man (" not in text


# ---------- sentences and chance words ----------


def test_what_has_to_happen_sentences():
    over = _scored(prop="Rebounds", line=6.5)
    assert picks_export.what_has_to_happen(over) == (
        "Giddey gets 7 or more rebounds."
    )
    under = _scored(prop="Points", side="Under", line=24.5)
    assert picks_export.what_has_to_happen(under) == (
        "Giddey gets 24 or fewer points."
    )
    whole = _scored(prop="Passing Yards", side="Over", line=250)
    sentence = picks_export.what_has_to_happen(whole)
    assert "more than 250 passing yards (at least 251)" in sentence
    assert "tie" in sentence
    suffix = _scored(player="Luther Burden III", prop="Receptions", line=3.5)
    assert picks_export.what_has_to_happen(suffix) == (
        "Burden gets 4 or more receptions."
    )


def test_chance_words():
    assert picks_export.chance_words(0.70) == "about 7 in 10 (70%)"
    assert picks_export.chance_words(0.62) == "about 6 in 10 (62%)"
    assert picks_export.chance_words(0.004) == "about 1 in 250 (0.4%)"
    assert picks_export.chance_words(0.0) == "no chance worked out"


def test_long_shot_ticket_says_so_in_words():
    leg = _scored()
    text = picks_export.build_picks_text(
        parlays=[_parlay([leg], probability=0.004, label="10-pick parlay #1")],
        teams=TEAMS,
    )
    assert "Overall chance: about 1 in 250 (0.4%)" in text


# ---------- the real path: restored stored slate ----------


def test_restored_slate_exports():
    scored = [
        _scored(f"Fixture{i}", prop="Points", line=10.5 + i) for i in range(3)
    ]
    payload = slate_store.build_payload(
        pulled_at=PULLED, note="", raw_props=scored, scored=scored,
        parlays=[_parlay(scored)], king_parlays=[],
    )
    restored = slate_store.restore_slate(payload)
    assert restored is not None
    text = picks_export.build_picks_text(
        pulled_at=restored["pulled_at"],
        parlays=restored["parlays"],
        king_parlays=restored["king_parlays"],
        teams=TEAMS,
    )
    assert "Fixture0 (Detroit Pistons) — Over 10.5 points" in text
    assert "Pulled: Thu Nov 12, 2026" in text


def test_file_holds_picks_only():
    leg = _scored()
    leg.form_verdict = "STRONG"
    leg.form_record = "9/10"
    text = picks_export.build_picks_text(
        pulled_at=PULLED,
        parlays=[_parlay([leg])],
        king_parlays=[_parlay([leg], style="king")],
        teams=TEAMS,
    )
    assert "{" not in text and "}" not in text  # no raw JSON
    assert "LILLY_ODDS_API_KEY" not in text
    assert "KING_PIN" not in text and "KOHEN_PIN" not in text
