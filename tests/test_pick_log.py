"""Tests for the pick log — every pick tracked, played or not —
plus the cookie tests' companion AppTest check for the Results
page Pick log section.

Run: pytest -q

The PINs below ("1111" / "2222") are FAKE test values, injected
through plain dicts that stand in for Streamlit secrets. The real
PINs exist only in Neko's Streamlit secrets — never in this repo.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import pick_log
from kings_picks.model import Prop, score_prop
from kings_picks.parlays import build_parlays
from kings_picks.pick_log import PickLog, pick_key

FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
TODAY = "2026-11-12"


def make_scored(n, base=0.60):
    """n scored props with descending probabilities (mirrors the
    helper in the other test files)."""
    out = []
    for i in range(n):
        p = Prop(player=f"Player{i}", sport="NBA", prop="Points",
                 side="Over", line=10.5 + i, odds=-110,
                 event=f"Team{i} vs Team{i + 1}")
        s = score_prop(p, pair_odds=-110)
        s.probability = max(0.51, base - i * 0.01)
        out.append(s)
    return out


def built_parlays(n=12, plan=((10, 1), (5, 1), (3, 1))):
    parlays, _ = build_parlays(make_scored(n), plan=list(plan))
    return parlays


# ---------- pick key ----------

def test_pick_key_shape_and_stability():
    k1 = pick_key(TODAY, "Lakers vs Celtics", "Star", "Points Over", 24.5)
    k2 = pick_key(TODAY, "Lakers vs Celtics", "Star", "Points Over", 24.5)
    assert k1 == k2 == f"{TODAY}|Lakers vs Celtics|Star|Points Over|24.5"


def test_pick_key_changes_with_each_part():
    base = pick_key(TODAY, "A vs B", "Star", "Points Over", 24.5)
    assert pick_key("2026-11-13", "A vs B", "Star", "Points Over", 24.5) != base
    assert pick_key(TODAY, "A vs C", "Star", "Points Over", 24.5) != base
    assert pick_key(TODAY, "A vs B", "Other", "Points Over", 24.5) != base
    assert pick_key(TODAY, "A vs B", "Star", "Points Under", 24.5) != base
    assert pick_key(TODAY, "A vs B", "Star", "Assists Over", 24.5) != base
    assert pick_key(TODAY, "A vs B", "Star", "Points Over", 25.5) != base


# ---------- recording + dedupe ----------

def test_record_run_logs_every_leg_of_every_parlay(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    parlays = built_parlays()
    added = log.record_run(parlays, "live", created=TODAY)
    leg_keys = {
        pick_key(TODAY, p.event, p.player, f"{p.prop} {p.side}", p.line)
        for parlay in parlays for p in parlay.picks
    }
    # Parlays share legs, so the log holds each DISTINCT pick once.
    total_legs = sum(len(parlay.picks) for parlay in parlays)
    assert len(leg_keys) < total_legs  # legs really were shared
    assert {e.key for e in added} == leg_keys
    assert len(log.entries) == len(leg_keys)


def test_record_run_entry_fields(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(built_parlays(plan=((3, 1),)), "demo", created=TODAY)
    entry = log.entries[0]
    assert entry.id == entry.key
    assert entry.date == TODAY
    assert entry.sport == "NBA"
    assert entry.event and entry.player
    assert entry.market.endswith("Over")
    assert isinstance(entry.line, float)
    assert entry.odds == -110
    assert 0 < entry.probability <= 1
    assert entry.mode == "demo"
    assert entry.status == "open"
    assert entry.played_by == []
    assert entry.label  # display label carried through


def test_record_run_dedupes_rerun_same_day(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    parlays = built_parlays()
    first = log.record_run(parlays, "live", created=TODAY)
    assert first  # something was logged
    count = len(log.entries)
    second = log.record_run(parlays, "live", created=TODAY)
    assert second == []  # nothing new
    assert len(log.entries) == count


def test_record_run_next_day_logs_again(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    parlays = built_parlays()
    log.record_run(parlays, "live", created=TODAY)
    added = log.record_run(parlays, "live", created="2026-11-13")
    assert added  # same picks, new date -> new entries
    assert len(log.entries) == 2 * len(added)


def test_same_pick_different_line_is_a_different_pick(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    scored = make_scored(3)
    twin = score_prop(Prop(player="Player0", sport="NBA", prop="Points",
                           side="Over", line=99.5, odds=-110,
                           event="Team0 vs Team1"), pair_odds=-110)
    parlays, _ = build_parlays(scored + [twin], plan=[(3, 1), (3, 1)])
    log.record_run(parlays, "live", created=TODAY)
    player0 = [e for e in log.entries if e.player == "Player0"]
    assert len(player0) == 2
    assert {e.line for e in player0} == {10.5, 99.5}


def test_pick_log_persists_to_disk(tmp_path):
    path = tmp_path / "pick_log.json"
    log = PickLog(path)
    log.record_run(built_parlays(plan=((3, 1),)), "live", created=TODAY)
    key = log.entries[0].key
    log.settle(key, True)
    log.toggle_played(key, "Kohen")

    reloaded = PickLog(path)
    assert len(reloaded.entries) == len(log.entries)
    entry = reloaded.entries[0]
    assert entry.status == "won"
    assert entry.played_by == ["Kohen"]
    raw = json.loads(path.read_text())
    assert raw[0]["key"] == key


# ---------- settle flow ----------

def test_settle_marks_won_and_lost(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(built_parlays(plan=((3, 1),)), "live", created=TODAY)
    k_won, k_lost = log.entries[0].key, log.entries[1].key
    assert log.settle(k_won, True) is True
    assert log.settle(k_lost, False) is True
    by_key = {e.key: e for e in log.entries}
    assert by_key[k_won].status == "won"
    assert by_key[k_lost].status == "lost"
    assert log.settle("no|such|key", True) is False
    assert [e.key for e in log.open_picks()] == [log.entries[2].key]
    assert {e.key for e in log.settled_picks()} == {k_won, k_lost}


def test_settled_picks_newest_first(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(built_parlays(plan=((3, 1),)), "live", created=TODAY)
    for entry in log.entries:
        log.settle(entry.key, True)
    settled = log.settled_picks()
    assert [e.key for e in settled] == [e.key for e in reversed(log.entries)]


# ---------- played flow ----------

def test_toggle_played_marks_and_unmarks_per_profile(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(built_parlays(plan=((3, 1),)), "live", created=TODAY)
    key = log.entries[0].key
    assert log.toggle_played(key, "King") is True
    assert log.toggle_played(key, "Kohen") is True
    entry = log.entries[0]
    assert entry.played_by == ["King", "Kohen"]
    assert log.played_by_profile(key, "King") is True
    # Toggling again unmarks only that profile.
    assert log.toggle_played(key, "King") is False
    assert entry.played_by == ["Kohen"]
    assert log.played_by_profile(key, "King") is False
    assert log.played_by_profile(key, "Kohen") is True
    assert log.toggle_played("no|such|key", "King") is None


def test_toggle_played_unknown_profile_counts_as_king(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(built_parlays(plan=((3, 1),)), "live", created=TODAY)
    key = log.entries[0].key
    assert log.toggle_played(key, "Somebody Else") is True
    assert log.entries[0].played_by == ["King"]


# ---------- stats math ----------

def _seeded_log(tmp_path):
    """4 live picks (2 won, 1 lost, 1 open) + demo picks (all won).

    Demo props use distinct sample players — exactly like the real
    demo data — so demo picks are different picks from live ones,
    not the same keys re-tagged.
    """
    log = PickLog(tmp_path / "pick_log.json")
    live = built_parlays(n=6, plan=((3, 1), (3, 1)))  # up to 4 distinct legs
    log.record_run(live, "live", created=TODAY)
    demo_scored = []
    for i in range(4):
        p = Prop(player=f"Sample{i}", sport="NBA", prop="Points",
                 side="Over", line=30.5 + i, odds=-110,
                 event="Sample Team vs Demo Team")
        s = score_prop(p, pair_odds=-110)
        s.probability = 0.60 - i * 0.01
        demo_scored.append(s)
    demo, _ = build_parlays(demo_scored, plan=[(3, 1)])
    log.record_run(demo, "demo", created=TODAY)
    live_entries = [e for e in log.entries if e.mode == "live"]
    demo_entries = [e for e in log.entries if e.mode == "demo"]
    assert len(demo_entries) == 3
    log.settle(live_entries[0].key, True)
    log.settle(live_entries[1].key, True)
    log.settle(live_entries[2].key, False)
    for entry in demo_entries:
        log.settle(entry.key, True)
    return log, live_entries, demo_entries


def test_stats_exclude_demo_from_headline(tmp_path):
    log, live_entries, demo_entries = _seeded_log(tmp_path)
    stats = log.stats(profile="King")
    assert stats["total"] == len(live_entries)
    assert stats["settled"] == 3
    assert stats["won"] == 2
    assert stats["lost"] == 1
    assert stats["open"] == len(live_entries) - 3
    assert stats["hit_rate"] == pytest.approx(2 / 3)
    # Demo lives in its own block, never in the headline numbers.
    assert stats["demo"]["total"] == len(demo_entries)
    assert stats["demo"]["settled"] == len(demo_entries)
    assert stats["demo"]["hit_rate"] == pytest.approx(1.0)


def test_stats_empty_log(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    stats = log.stats(profile="King")
    assert stats["total"] == 0
    assert stats["settled"] == 0
    assert stats["hit_rate"] is None
    assert stats["played"]["hit_rate"] is None


def test_stats_played_record_is_per_profile_and_live_only(tmp_path):
    log, live_entries, demo_entries = _seeded_log(tmp_path)
    # King played two live picks: one won, one lost.
    log.toggle_played(live_entries[0].key, "King")   # won
    log.toggle_played(live_entries[2].key, "King")   # lost
    # King also "played" a demo pick and an open live pick — the
    # open one is not settled yet, the demo one never counts.
    log.toggle_played(demo_entries[0].key, "King")
    open_live = [e for e in live_entries if e.status == "open"]
    if open_live:
        log.toggle_played(open_live[0].key, "King")
    # Kohen played only the other won live pick.
    log.toggle_played(live_entries[1].key, "Kohen")

    king = log.stats(profile="King")["played"]
    assert king["settled"] == 2
    assert king["won"] == 1
    assert king["hit_rate"] == pytest.approx(0.5)

    kohen = log.stats(profile="Kohen")["played"]
    assert kohen["settled"] == 1
    assert kohen["won"] == 1
    assert kohen["hit_rate"] == pytest.approx(1.0)


# ---------- backup export ----------

def test_export_rows_and_json(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(built_parlays(plan=((3, 1),)), "live", created=TODAY)
    key = log.entries[0].key
    log.toggle_played(key, "King")
    log.toggle_played(key, "Kohen")

    rows = log.rows()
    assert len(rows) == len(log.entries)
    assert rows[0]["key"] == key
    assert rows[0]["played_by"] == "King;Kohen"  # flat for CSV

    parsed = json.loads(log.to_json())
    assert parsed[0]["key"] == key
    assert parsed[0]["played_by"] == ["King", "Kohen"]  # list stays a list


# ---------- app render: Results page pick log (AppTest) ----------

def _run_app(secrets=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    return at


def _login_as_king(at):
    at.button(key="login-king").click().run()
    at.text_input(key="pin_entry").set_value("1111")
    matches = [b for b in at.button if b.label == "Log in"]
    assert matches, "no Log in button"
    matches[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    assert at.session_state["active_profile"] == "King"
    return at


def test_app_results_page_shows_pick_log_and_exports():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    radios = list(at.radio)
    assert radios, "app page radio missing after login"
    radios[0].set_value("Results / Ledger").run()
    assert not at.exception, f"app raised: {at.exception}"
    text = "\n".join(
        [m.value for m in at.markdown]
        + [s.value for s in at.subheader]
        + [c.value for c in at.caption]
    )
    assert "Pick log" in text
    assert "Back up your records" in text
    labels = {b.label for b in at.download_button}
    assert any("Pick log — JSON" in label for label in labels)
    assert any("Pick log — CSV" in label for label in labels)
    assert any("Parlay ledger — JSON" in label for label in labels)
    assert any("Parlay ledger — CSV" in label for label in labels)


def test_app_runs_without_cookie_component(monkeypatch):
    # Simulate extra-streamlit-components being unavailable: the
    # guarded import path must fall back to the plain PIN gate.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("extra_streamlit_components"):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    at = _run_app(FAKE_SECRETS)
    keys = {b.key for b in at.button}
    assert "login-king" in keys and "login-kohen" in keys
    _login_as_king(at)  # full PIN login still works, no cookie needed
