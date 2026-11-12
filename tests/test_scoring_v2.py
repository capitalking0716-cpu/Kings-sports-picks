"""Tests for the v2 wiring (2026-11-12): the projection model is
Lilly's form ingredient now.

What is being pinned down here:

- Scoring follows the PROJECTION where the projection and the
  old raw hit-rate disagree (the hit-rate is retired as an
  ingredient — see validation/projection-calibration-2026-11-12.md).
- A matched log too short to project from (< 5 games) makes the
  pick odds-only. There is NO hit-rate fallback, even when the
  short log's hit-rate is a perfect 4-for-4.
- The Anytime Touchdown (binary) scoring path runs on the
  projection model's smoothed scoring rate.
- Mid-range Over projections carry Lilly's calibration caution.
- Pick-log and ledger entries are tagged with the scoring era:
  new runs "v2", entries saved before the tag existed read "v1".

The scoring composition tested here mirrors app.score_all's
glue exactly: values from make_form_lookup -> projection
.over_probability / project_stat -> score_prop. (app.py itself
is Streamlit script code; its seams are what can be unit-tested,
the same pattern the rest of the suite uses.)

Run: pytest -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import game_logs, projection
from kings_picks.game_logs import GameLogs, make_form_lookup, summarize
from kings_picks.ledger import Ledger
from kings_picks.model import SCORING_VERSION, Prop, score_prop
from kings_picks.parlays import build_parlays
from kings_picks.pick_log import PickLog

TODAY = "2026-11-12"


def score_as_app_does(prop, lookup, pair_odds=-110):
    """app.score_all's per-prop composition, line for line in
    behaviour: projection ingredient when the model can project,
    plain odds-only scoring otherwise."""
    values = lookup(prop) if lookup is not None else None
    form_probability = None
    proj = None
    if values:
        form_probability = projection.over_probability(values, prop.line, prop.side)
        if form_probability is not None:
            proj = projection.project_stat(values)
    if form_probability is not None and proj is not None:
        return score_prop(
            prop, pair_odds=pair_odds,
            form_probability=form_probability,
            projected_value=proj.expected,
            projection_games=proj.games,
        )
    return score_prop(prop, pair_odds=pair_odds)


# ---------- the ingredient swap ----------

def test_scoring_follows_projection_where_hit_rate_disagrees():
    # The log is newest-first: the four most recent games are
    # cold 5s, the six before them were 26s. The descriptive
    # hit-rate over the last 10 is HIGH (6 of 10 over the line),
    # but the projection — which weights the recent cold games
    # biggest and anchors on the low season average — lands far
    # below it. The two methods genuinely disagree, and scoring
    # must follow the projection.
    values = [5.0, 5.0, 5.0, 5.0, 26.0, 26.0, 26.0, 26.0, 26.0, 26.0]
    logs = GameLogs(
        sport="NBA",
        by_player={game_logs.normalize_name("Swing Man"): {"Points": values}},
        source_label="test feed",
    )
    lookup = make_form_lookup(None, logs)
    prop = Prop(player="Swing Man", sport="NBA", prop="Points", side="Over",
                line=24.5, odds=-110, source="live")

    # The descriptive hit-rate really is 6 of 10 — the retired
    # v1 ingredient would have blended 0.5*0.5 + 0.5*0.6 = 0.55.
    summary = summarize(values, "Over", 24.5)
    assert (summary.hits, summary.games) == (6, 10)

    p_form = projection.over_probability(values, 24.5, "Over")
    assert p_form is not None
    assert p_form < 0.45  # the projection strongly disagrees

    scored = score_as_app_does(prop, lookup)
    assert scored.has_form_data is True
    # The blend followed the projection, not the hit-rate.
    assert scored.probability == pytest.approx(0.5 * 0.5 + 0.5 * p_form)
    assert scored.probability != pytest.approx(0.55)
    assert scored.probability < 0.5  # pulled BELOW the pure odds
    assert scored.form_probability == pytest.approx(p_form)
    assert "Projection:" in scored.why


def test_short_log_no_projection_and_no_hit_rate_fallback():
    # Four games, ALL over the line: the retired hit-rate would
    # call this 4-for-4 (100%). The projection model needs 5+
    # games, so the pick must stay odds-only — the perfect
    # hit-rate does not rescue it. That is the whole point of
    # the v2 wiring.
    values = [40.0, 40.0, 40.0, 40.0]
    logs = GameLogs(
        sport="NBA",
        by_player={game_logs.normalize_name("Short Log"): {"Points": values}},
        source_label="test feed",
    )
    lookup = make_form_lookup(None, logs)
    prop = Prop(player="Short Log", sport="NBA", prop="Points", side="Over",
                line=24.5, odds=-110, source="live")

    assert lookup(prop) == values  # the log IS matched...
    assert summarize(values, "Over", 24.5).hits == 4  # ...and perfect
    assert projection.over_probability(values, 24.5, "Over") is None

    scored = score_as_app_does(prop, lookup)
    assert scored.has_form_data is False
    assert scored.probability == pytest.approx(0.5)  # pure de-vigged odds
    assert scored.form_probability is None
    assert "odds-only" in scored.why.lower()


def test_anytime_td_scores_on_projection_scoring_rate():
    values = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0]  # newest first
    logs = GameLogs(
        sport="NFL",
        by_player={game_logs.normalize_name("TD Man"): {"Anytime Touchdown": values}},
        source_label="test feed",
    )
    lookup = make_form_lookup(logs, None)
    prop = Prop(player="TD Man", sport="NFL", prop="Anytime Touchdown",
                side="Over", line=0.5, odds=-110, source="live")

    # Binary path: 5 scores in 8 games, smoothed (5 + 1) / (8 + 2).
    p_form = projection.over_probability(values, 0.5, "Over")
    assert p_form == pytest.approx(0.6)

    scored = score_as_app_does(prop, lookup)
    assert scored.has_form_data is True
    assert scored.probability == pytest.approx(0.5 * 0.5 + 0.5 * 0.6)
    # The wording speaks in scoring rates, not fake yardage.
    assert "end zone in about 62%" in scored.why
    assert "at about 60%" in scored.why


# ---------- the calibration caution ----------

def _scored_with_form(p_form, side="Over"):
    prop = Prop(player="P", sport="NBA", prop="Points", side=side,
                line=24.5, odds=-110, source="live")
    return score_prop(prop, pair_odds=-110, form_probability=p_form,
                      projected_value=26.0, projection_games=9)


def test_mid_range_over_carries_the_calibration_caution():
    scored = _scored_with_form(0.57, side="Over")
    assert "too confidently" in scored.why
    assert "lean, not a lock" in scored.why


def test_strong_over_and_unders_carry_no_caution():
    assert "too confidently" not in _scored_with_form(0.85, side="Over").why
    # The known bias is an OVER bias — Unders in the same range
    # do not get the warning.
    assert "too confidently" not in _scored_with_form(0.57, side="Under").why


# ---------- breakdown attribution ----------

def test_breakdown_attributes_the_move_to_the_projection():
    from kings_picks import breakdown
    from kings_picks.game_logs import FormSummary

    prop = Prop(player="Patrick Mahomes", sport="NFL", prop="Passing Yards",
                side="Over", line=249.5, odds=-110, bookmaker="DraftKings",
                event="Chiefs vs Bills", source="live")
    pick = score_prop(prop, pair_odds=-110, form_probability=0.57,
                      projected_value=262.0, projection_games=8)
    form = FormSummary(values=[250.0, 300.0, 200.0], hits=2, games=3,
                       last5_avg=250.0, season_avg=250.0,
                       source_label="nflverse (free, updated weekly)")
    card = breakdown.build_breakdown(pick, [pick], [], pair_odds=-110, form=form)
    text = " ".join(card.reasons)
    # The descriptive hit-rate display stays...
    assert "2 of his last 3 games" in text
    # ...and the ingredient attribution names the projection.
    assert "projection model" in text
    assert "about 262 passing yards" in text
    assert "57%" in text


# ---------- era tagging ----------

def _parlays(n=12):
    scored = []
    for i in range(n):
        p = Prop(player=f"Player{i}", sport="NBA", prop="Points",
                 side="Over", line=10.5 + i, odds=-110,
                 event=f"Team{i} vs Team{i + 1}")
        s = score_prop(p, pair_odds=-110)
        s.probability = max(0.51, 0.60 - i * 0.01)
        scored.append(s)
    parlays, _ = build_parlays(scored, plan=[(3, 1)])
    return parlays


def test_scoring_version_is_v2():
    assert SCORING_VERSION == "v2"


def test_pick_log_new_entries_tagged_v2_by_default(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(_parlays(), "live", created=TODAY)
    assert log.entries
    assert all(e.model == "v2" for e in log.entries)
    # ...and the tag survives the round trip to disk.
    reloaded = PickLog(tmp_path / "pick_log.json")
    assert all(e.model == "v2" for e in reloaded.entries)


def test_pick_log_record_run_accepts_an_explicit_era(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(_parlays(), "live", created=TODAY, model="v1")
    assert all(e.model == "v1" for e in log.entries)


def test_pick_log_legacy_entries_read_as_v1(tmp_path):
    path = tmp_path / "pick_log.json"
    legacy = [{
        "id": "k", "key": "k", "date": TODAY, "sport": "NBA",
        "event": "A vs B", "player": "Old Timer", "market": "Points Over",
        "line": 24.5, "odds": -110, "probability": 0.55, "mode": "live",
        "label": "Old Timer — Points Over 24.5", "status": "open",
        "played_by": [],
    }]
    path.write_text(json.dumps(legacy))
    log = PickLog(path)
    assert log.entries[0].model == "v1"


def test_ledger_new_entries_tagged_v2_by_default(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record_run(_parlays(), "live")
    assert ledger.parlays[0].model == "v2"
    reloaded = Ledger(tmp_path / "ledger.json")
    assert reloaded.parlays[0].model == "v2"


def test_ledger_legacy_entries_read_as_v1(tmp_path):
    path = tmp_path / "ledger.json"
    legacy = [{
        "id": "abc12345", "date": TODAY, "size": 3, "label": "3-pick",
        "overall_probability": 0.2, "mode": "live", "picks": [],
        "settled": False, "profile": "King",
    }]
    path.write_text(json.dumps(legacy))
    ledger = Ledger(path)
    assert ledger.parlays[0].model == "v1"
