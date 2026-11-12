"""Tests for Lilly's self-test — she grades her own picks
against recent game-log form (kings_picks/self_test.py), plus
the Dashboard section that shows the grades.

The grading is a descriptive form check ONLY: it never feeds
scoring (the hit-rate was retired as an ingredient by the
calibration proof). These tests pin the counting — hits,
misses, pushes counted separately and excluded from rates —
the verdict bands, the name normaliser that lets book
spellings find feed rows, and the slate rule (her latest
logged date, her own picks only).

Run: pytest -q

The PINs below ("1111" / "2222") are FAKE test values, injected
through plain dicts that stand in for Streamlit secrets. The real
PINs exist only in Neko's Streamlit secrets — never in this repo.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import self_test
from kings_picks.game_logs import GameLogs
from kings_picks.model import Prop, score_prop
from kings_picks.parlays import build_parlays
from kings_picks.pick_log import PickEntry, PickLog

FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
TODAY = "2026-11-12"
YESTERDAY = "2026-11-11"


# ---------- name normaliser ----------

def test_normalize_strips_suffixes():
    assert self_test.normalize_player_name("Paul Reed Jr") == \
        self_test.normalize_player_name("Paul Reed")
    assert self_test.normalize_player_name("Paul Reed Jr.") == "paul reed"
    assert self_test.normalize_player_name("Luther Burden III") == "luther burden"
    assert self_test.normalize_player_name("John Smith Sr") == "john smith"


def test_normalize_removes_periods_and_punctuation():
    assert self_test.normalize_player_name("R.J. Barrett") == \
        self_test.normalize_player_name("RJ Barrett")
    assert self_test.normalize_player_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert self_test.normalize_player_name("De'Andre Hunter") == "deandre hunter"


def test_normalize_plain_and_empty():
    assert self_test.normalize_player_name("LeBron James") == "lebron james"
    assert self_test.normalize_player_name("") == ""
    assert self_test.normalize_player_name(None) == ""


# ---------- market splitting ----------

def test_split_market():
    assert self_test.split_market("Points Over") == ("Points", "Over")
    assert self_test.split_market("Points + Rebounds + Assists Under") == (
        "Points + Rebounds + Assists", "Under")
    assert self_test.split_market("Anytime Touchdown Over") == (
        "Anytime Touchdown", "Over")
    assert self_test.split_market("Passing Yards") == ("Passing Yards", "")


# ---------- hit / push counting ----------

def test_count_outcomes_over_and_under():
    values = [30.0, 20.0, 25.0, 10.0]
    assert self_test.count_outcomes(values, "Over", 24.5) == (2, 2, 0)
    assert self_test.count_outcomes(values, "Under", 24.5) == (2, 2, 0)


def test_count_outcomes_push_is_neither_hit_nor_miss():
    # Whole-number line: landing exactly on it pushes, both ways.
    values = [5.0, 5.0, 7.0, 3.0]
    assert self_test.count_outcomes(values, "Over", 5.0) == (1, 1, 2)
    assert self_test.count_outcomes(values, "Under", 5.0) == (1, 1, 2)


# ---------- grading on synthetic logs ----------

def test_grade_strong():
    values = [30.0] * 9 + [10.0]  # 9 of last 10 over 24.5
    grade = self_test.grade_values("A Player", "Points", "Over", 24.5, values)
    assert grade.games_found == 10
    assert grade.last10_hits == 9 and grade.last10_misses == 1
    assert grade.last10_rate == 0.9
    assert grade.verdict == self_test.STRONG
    assert grade.form_label == "9/10"


def test_grade_strong_needs_eight_decided_games():
    # Perfect but thin: 7 games is a LEAN, not STRONG proof.
    grade = self_test.grade_values("A Player", "Points", "Over", 24.5, [30.0] * 7)
    assert grade.last10_rate == 1.0
    assert grade.verdict == self_test.LEAN


def test_grade_lean_mixed_shaky_bands():
    six = [30.0] * 6 + [10.0] * 4
    assert self_test.grade_values("P", "Points", "Over", 24.5, six).verdict \
        == self_test.LEAN
    five = [30.0] * 5 + [10.0] * 5
    assert self_test.grade_values("P", "Points", "Over", 24.5, five).verdict \
        == self_test.MIXED
    four = [30.0] * 4 + [10.0] * 6
    assert self_test.grade_values("P", "Points", "Over", 24.5, four).verdict \
        == self_test.SHAKY
    none = [10.0] * 10
    assert self_test.grade_values("P", "Points", "Over", 24.5, none).verdict \
        == self_test.SHAKY


def test_grade_pushes_excluded_from_rate_and_named_in_label():
    # Line 25 (whole number): 25s push. 6 over, 2 under, 2 pushes
    # -> rate over decided games is 6/8 = 0.75, decided = 8 -> STRONG.
    values = [30.0] * 6 + [20.0] * 2 + [25.0] * 2
    grade = self_test.grade_values("P", "Points", "Over", 25.0, values)
    assert grade.last10_pushes == 2
    assert grade.last10_decided == 8
    assert grade.last10_rate == 0.75
    assert grade.verdict == self_test.STRONG
    assert grade.form_label == "6/10 (2 pushes)"


def test_grade_all_pushes_is_mixed_not_no_data():
    grade = self_test.grade_values("P", "Rebounds", "Over", 5.0, [5.0] * 10)
    assert grade.games_found == 10
    assert grade.last10_rate is None
    assert grade.verdict == self_test.MIXED


def test_grade_last10_window_and_full_log_rate():
    # 15 games: the newest 10 are all under, the oldest 5 all over.
    values = [10.0] * 10 + [30.0] * 5
    grade = self_test.grade_values("P", "Points", "Over", 24.5, values)
    assert grade.games_found == 15
    assert grade.last10_games == 10
    assert grade.last10_rate == 0.0
    assert grade.verdict == self_test.SHAKY
    assert grade.full_hits == 5 and grade.full_misses == 10
    assert grade.full_rate == 5 / 15


def test_grade_no_data_for_missing_values():
    for values in (None, []):
        grade = self_test.grade_values("Ghost", "Points", "Over", 24.5, values)
        assert grade.games_found == 0
        assert grade.last10_rate is None and grade.full_rate is None
        assert grade.verdict == self_test.NO_DATA
        assert grade.form_label == "no game log"


def test_grade_under_side():
    values = [3.0] * 8 + [9.0] * 2  # under 5.5 in 8 of 10
    grade = self_test.grade_values("P", "Assists", "Under", 5.5, values)
    assert grade.last10_hits == 8
    assert grade.verdict == self_test.STRONG


# ---------- grading through loaded game logs ----------

def _nba_logs():
    return GameLogs(
        sport="NBA",
        by_player={
            "paul reed": {"Assists": [3.0, 2.0, 4.0, 1.0, 3.0,
                                      2.0, 5.0, 3.0, 2.0, 4.0]},
            "rj barrett": {"Points": [22.0] * 10},
        },
        source_label="fixture",
    )


def test_grade_pick_finds_name_variants():
    logs = _nba_logs()
    # The books' spelling carries the suffix; the feed key doesn't.
    grade = self_test.grade_pick("Paul Reed Jr", "Assists", "Over", 1.5, logs)
    assert grade.games_found == 10
    assert grade.last10_hits == 9  # only the 1.0 game went under 1.5
    # Periods in the books' spelling, none in the feed key.
    grade = self_test.grade_pick("R.J. Barrett", "Points", "Over", 19.5, logs)
    assert grade.games_found == 10
    assert grade.verdict == self_test.STRONG


def test_grade_pick_missing_player_or_stat_is_no_data():
    logs = _nba_logs()
    assert self_test.grade_pick("Nobody", "Points", "Over", 9.5, logs).verdict \
        == self_test.NO_DATA
    assert self_test.grade_pick("Paul Reed Jr", "Blocks", "Over", 0.5, logs).verdict \
        == self_test.NO_DATA
    assert self_test.grade_pick("Paul Reed Jr", "Assists", "Over", 1.5, None).verdict \
        == self_test.NO_DATA


# ---------- slates from the pick log ----------

def _entry(date, player, source="lilly", style="standard", mode="live",
           market="Points Over", line=24.5, probability=0.6):
    return PickEntry(
        id=f"{date}|{player}", key=f"{date}|{player}", date=date,
        sport="NBA", event="A vs B", player=player, market=market,
        line=line, odds=-110, probability=probability, mode=mode,
        label=f"{player} — {market} {line:g}", source=source, style=style,
    )


def test_latest_slate_is_latest_date_and_her_picks_only():
    entries = [
        _entry(YESTERDAY, "Old Pick"),
        _entry(TODAY, "Today Standard"),
        _entry(TODAY, "Today King", style="king"),
        _entry(TODAY, "Hand Built", source="manual"),  # the user's, not hers
    ]
    slate = self_test.latest_slate(entries)
    assert [e.player for e in slate] == ["Today Standard", "Today King"]
    assert self_test.latest_slate([]) == []


def test_grade_entry_carries_display_fields():
    entry = _entry(TODAY, "Paul Reed Jr", style="king", mode="live",
                   market="Assists Over", line=1.5, probability=0.62)
    grade = self_test.grade_entry(entry, {"NBA": _nba_logs()})
    assert grade.prop == "Assists" and grade.side == "Over"
    assert grade.stated_chance == 0.62
    assert grade.style == "king" and grade.mode == "live"
    assert grade.date == TODAY
    assert grade.last10_hits == 9


def test_grade_slate_sorts_strongest_first_no_data_last():
    entries = [
        _entry(TODAY, "Ghost Player"),  # no log -> NO DATA
        _entry(TODAY, "R.J. Barrett", market="Points Over", line=19.5),
        _entry(TODAY, "Paul Reed Jr", market="Assists Over", line=1.5),
    ]
    grades = self_test.grade_slate(entries, {"NBA": _nba_logs()})
    assert grades[-1].verdict == self_test.NO_DATA
    assert grades[0].verdict == self_test.STRONG
    counts = self_test.verdict_counts(grades)
    assert counts[self_test.NO_DATA] == 1
    assert sum(counts.values()) == 3


# ---------- app render: Dashboard self-test section (AppTest) ----------

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


def _all_text(at):
    parts = []
    for accessor in ("title", "subheader", "markdown", "caption", "info"):
        try:
            parts += [el.value for el in getattr(at, accessor)]
        except Exception:
            pass
    for expander in at.expander:
        parts.append(expander.label)
        for accessor in ("markdown", "caption", "info"):
            try:
                parts += [el.value for el in getattr(expander, accessor)]
            except Exception:
                pass
    return "\n".join(parts)


class _FakeStore:
    """Stands in for StatsStore — same two methods the app calls,
    fixture data only (mirrors tests/test_players.py)."""

    def __init__(self, nfl=None, nba=None):
        self._nfl = nfl
        self._nba = nba

    def nfl_logs(self):
        return self._nfl

    def nba_logs(self):
        return self._nba


def test_app_dashboard_self_test_with_empty_pick_log(tmp_path, monkeypatch):
    # A fresh data dir: no pick_log.json at all. The section
    # must render its "no picks yet" state without touching
    # the stats feeds or raising.
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    at = _login_as_king(_run_app(FAKE_SECRETS))
    text = _all_text(at)
    assert "🧪 Lilly tests her picks" in text
    assert "No picks logged yet" in text
    assert "never changes her" in text  # the honest caption


def test_app_dashboard_self_test_grades_the_slate(tmp_path, monkeypatch):
    # A live slate on disk + fixture logs through the fake
    # store: Player0's Points Over 10.5 hit in all 10 fixture
    # games -> STRONG, 10/10.
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    scored = []
    for i in range(12):
        p = Prop(player=f"Player{i}", sport="NBA", prop="Points",
                 side="Over", line=10.5 + i, odds=-110,
                 event=f"Team{i} vs Team{i + 1}")
        s = score_prop(p, pair_odds=-110)
        s.probability = max(0.51, 0.60 - i * 0.01)
        scored.append(s)
    parlays, _ = build_parlays(scored, plan=[(10, 1), (5, 1), (3, 1)])
    log = PickLog(tmp_path / "data" / "pick_log.json")
    log.record_run(parlays, "live", created=TODAY)

    at = _login_as_king(_run_app(FAKE_SECRETS))
    fixture = GameLogs(
        sport="NBA",
        by_player={"player0": {"Points": [25.0] * 12}},
        source_label="fixture",
    )
    at.session_state["stats_store"] = _FakeStore(nba=fixture)
    at.run()
    assert not at.exception, f"app raised: {at.exception}"

    text = _all_text(at)
    assert "🧪 Lilly tests her picks" in text
    assert "12 picks graded" in text
    assert "10/10" in text
    assert "STRONG" in text
    assert "NO DATA" in text  # Players 1-11 have no fixture log
