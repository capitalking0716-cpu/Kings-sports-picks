"""Tests for the Players page — standalone player-stats
browsing. Pure helpers run on fixture data; the AppTest render
tests inject a FIXTURE-BACKED store into session state, so no
network, no Odds API key, and no algorithm run are involved.

Run: pytest -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import game_logs, players
from kings_picks.game_logs import GameLogs

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}

# ---------- fixtures (same shapes as test_game_logs) ----------

NFL_HEADER = (
    "player_id,player_name,player_display_name,position,season,week,season_type,"
    "passing_yards,passing_tds,rushing_yards,rushing_tds,receiving_yards,"
    "receiving_tds,receptions"
)
NFL_CSV = "\n".join([
    NFL_HEADER,
    "00-1,P.Mahomes,Patrick Mahomes,QB,2026,2,REG,300,3,20,0,0,0,0",
    "00-1,P.Mahomes,Patrick Mahomes,QB,2026,1,REG,200,1,10,0,0,0,0",
    "00-1,P.Mahomes,Patrick Mahomes,QB,2026,3,REG,250,2,15,1,0,0,0",
    "00-2,L.Burden,Luther Burden,WR,2026,1,REG,0,0,5,0,80,1,6",
    "00-2,L.Burden,Luther Burden,WR,2026,2,REG,0,0,0,0,40,0,3",
    "00-3,M.Penix,Michael Penix Jr.,QB,2026,1,REG,280,2,12,0,0,0,0",
    "00-3,M.Penix,Michael Penix Jr.,QB,2026,2,REG,190,0,8,0,0,0,0",
    "00-4,J.Blank,Joe Blank,WR,2026,1,REG,0,0,0,0,,0,2",
])

NBA_ROWS = [
    {"PLAYER_NAME": "LeBron James", "GAME_DATE": "2026-11-06", "PTS": 20, "REB": 5, "AST": 6, "FG3M": 2},
    {"PLAYER_NAME": "LeBron James", "GAME_DATE": "2026-11-10", "PTS": 30, "REB": 8, "AST": 9, "FG3M": 3},
    {"PLAYER_NAME": "LeBron James", "GAME_DATE": "2026-11-08", "PTS": 25, "REB": 7, "AST": 7, "FG3M": 1},
]


def _nfl_logs():
    return GameLogs(sport="NFL", by_player=game_logs.parse_nflverse_csv(NFL_CSV),
                    source_label=game_logs.NFL_SOURCE, season_label="2026")


def _nba_logs():
    return GameLogs(sport="NBA", by_player=game_logs.parse_nba_game_log_rows(NBA_ROWS),
                    source_label=game_logs.NBA_SOURCE, season_label="2026-27")


def _nfl_logs_with_positions():
    logs = _nfl_logs()
    logs.positions = {
        "patrick mahomes": "QB",
        "michael penix": "QB",
        "luther burden": "WR",
        # Joe Blank deliberately unstated -> Other / unknown.
    }
    return logs


class _FakeStore:
    """Stands in for StatsStore — same two methods the app calls,
    fixture data only."""

    def __init__(self, nfl=None, nba=None):
        self._nfl = nfl
        self._nba = nba

    def nfl_logs(self):
        return self._nfl

    def nba_logs(self):
        return self._nba


# ---------- display names / picker list ----------

def test_display_name_round_trips_to_feed_key():
    for key in _nfl_logs().by_player:
        assert game_logs.normalize_name(players.display_name(key)) == key
    assert players.display_name("amon ra st brown") == "Amon Ra St Brown"
    assert players.display_name("") == ""


def test_player_names_lists_every_player_alphabetical():
    assert players.player_names(_nfl_logs()) == [
        "Joe Blank", "Luther Burden", "Michael Penix", "Patrick Mahomes",
    ]
    assert players.player_names(_nba_logs()) == ["Lebron James"]


# ---------- default line ----------

def test_default_line_is_season_avg_rounded_to_nearest_half():
    assert players.default_line(254.6) == 254.5
    assert players.default_line(25.25) == 25.5  # half rounds up
    assert players.default_line(25.75) == 26.0
    assert players.default_line(25.2) == 25.0
    assert players.default_line(0.0) == 0.0
    assert players.default_line(1 / 3) == 0.5  # an anytime-TD rate
    assert players.default_line(-3.0) == 0.0  # never negative


# ---------- stat sections ----------

def test_stat_sections_nfl_order_and_math():
    sections = players.stat_sections(_nfl_logs(), "Patrick Mahomes")
    assert [s.label for s in sections] == [
        "Passing Yards", "Passing Touchdowns", "Rushing Yards",
        "Receiving Yards", "Receptions", "Anytime Touchdown",
    ]
    passing = sections[0]
    assert passing.values == [250.0, 300.0, 200.0]  # weeks 3,2,1
    assert passing.last10 == passing.values
    assert passing.games == 3
    assert passing.season_avg == 250.0
    assert passing.last5_avg == 250.0
    assert passing.default_line == 250.0
    anytime = sections[-1]
    assert anytime.values == [1.0, 0.0, 0.0]  # rushing TD in week 3 only
    assert anytime.default_line == 0.5


def test_stat_sections_nba_labels_and_pra():
    sections = players.stat_sections(_nba_logs(), "LeBron James")
    assert [s.label for s in sections] == list(game_logs.NBA_STAT_COLUMNS)
    points = sections[0]
    assert points.values == [30.0, 25.0, 20.0]
    assert points.season_avg == 25.0
    assert points.default_line == 25.0
    pra = sections[-1]
    assert pra.values == [47.0, 39.0, 31.0]


def test_stat_sections_match_by_normalized_name():
    logs = _nfl_logs()
    # Suffix styles differ between the book and the feed; both resolve.
    assert players.stat_sections(logs, "Michael Penix Jr.")[0].label == "Passing Yards"
    assert players.stat_sections(logs, "michael penix")


def test_stat_sections_skip_empty_stats_and_unknown_players():
    logs = GameLogs(sport="NFL", by_player={
        "some guy": {"Passing Yards": [], "Rushing Yards": [12.0]},
        "no games": {},
    })
    sections = players.stat_sections(logs, "Some Guy")
    assert [s.label for s in sections] == ["Rushing Yards"]
    assert players.stat_sections(logs, "No Games") == []
    assert players.stat_sections(logs, "Ghost Player") == []


def test_display_name_mc_surnames_keep_second_capital():
    assert players.display_name("christian mccaffrey") == "Christian McCaffrey"
    assert players.display_name("terry mclaurin") == "Terry McLaurin"
    # Documented limit: other mid-word capitals can't be
    # recovered from a normalized key.
    assert players.display_name("lebron james") == "Lebron James"


def test_stat_sections_last10_caps_chart_but_averages_use_all_games():
    values = [float(v) for v in range(24, 12, -1)]  # 12 games: 24..13
    logs = GameLogs(sport="NBA", by_player={"test player": {"Points": values}})
    (section,) = players.stat_sections(logs, "Test Player")
    assert section.games == 12
    assert section.last10 == values[:10]
    assert section.season_avg == sum(values) / 12
    assert section.last5_avg == sum(values[:5]) / 5


def test_stat_sections_unknown_stat_still_shows_after_known_ones():
    logs = GameLogs(sport="NFL", by_player={
        "some guy": {"Blocked Kicks": [1.0], "Passing Yards": [100.0]},
    })
    sections = players.stat_sections(logs, "Some Guy")
    assert [s.label for s in sections] == ["Passing Yards", "Blocked Kicks"]


# ---------- position groups ----------

def _group_logs(sport, keys, positions):
    return GameLogs(
        sport=sport,
        by_player={key: {"Points": [1.0]} for key in keys},
        positions=positions,
    )


def test_position_groups_nfl_order_counts_and_unknown_last():
    keys = ["q bone", "r bone", "w rone", "t eone", "f bone",
            "l bone", "k icker", "m ystery"]
    positions = {
        "q bone": "QB", "r bone": "RB", "w rone": "WR", "t eone": "TE",
        "f bone": "FB", "l bone": "LB", "k icker": "K",
        # "m ystery" unstated -> Other / unknown
    }
    groups = players.position_groups(_group_logs("NFL", keys, positions))
    # Task order first (QB, RB, WR, TE, FB), other feed labels
    # alphabetically (K before LB), unknown group last.
    assert [label for label, _members in groups] == [
        "QB", "RB", "WR", "TE", "FB", "K", "LB", "Other / unknown",
    ]
    by_label = dict(groups)
    assert by_label["QB"] == ["Q Bone"]
    assert by_label["Other / unknown"] == ["M Ystery"]
    assert sum(len(m) for _l, m in groups) == len(keys)  # nobody dropped


def test_position_groups_nba_order_then_extras_alphabetical():
    keys = ["a g", "b gf", "c f", "d fc", "e c", "f fg", "g cf"]
    positions = {
        "a g": "Guard", "b gf": "Guard-Forward", "c f": "Forward",
        "d fc": "Forward-Center", "e c": "Center",
        "f fg": "Forward-Guard", "g cf": "Center-Forward",
    }
    groups = players.position_groups(_group_logs("NBA", keys, positions))
    assert [label for label, _members in groups] == [
        "Guard", "Guard-Forward", "Forward", "Forward-Center", "Center",
        "Center-Forward", "Forward-Guard",  # extras, alphabetical
    ]


def test_position_groups_members_sorted_alphabetical():
    logs = _group_logs(
        "NFL",
        ["z ed", "a aron"],
        {"z ed": "QB", "a aron": "QB"},
    )
    assert players.position_groups(logs) == [("QB", ["A Aron", "Z Ed"])]


def test_position_groups_empty_when_feed_states_no_positions():
    logs = _group_logs("NFL", ["some guy"], {})
    assert players.position_groups(logs) == []
    # A mapping that names nobody in the player list is, for this
    # page, no position data at all.
    logs = _group_logs("NFL", ["some guy"], {"other player": "QB"})
    assert players.position_groups(logs) == []


def test_names_for_group_filters_and_falls_back():
    logs = _nfl_logs_with_positions()
    groups = players.position_groups(logs)
    assert players.names_for_group(logs, groups, players.ALL_POSITIONS) == \
        players.player_names(logs)
    assert players.names_for_group(logs, groups, "QB") == [
        "Michael Penix", "Patrick Mahomes",
    ]
    assert players.names_for_group(logs, groups, players.UNKNOWN_POSITION_GROUP) == [
        "Joe Blank",
    ]
    # A stale choice after a feed refresh falls back to everyone.
    assert players.names_for_group(logs, groups, "Punter") == \
        players.player_names(logs)


# ---------- app render: Players page (AppTest) ----------

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
    return at


def _all_text(at):
    return "\n".join(
        [t.value for t in at.title]
        + [s.value for s in at.subheader]
        + [m.value for m in at.markdown]
        + [c.value for c in at.caption]
    )


def _goto_players(at):
    list(at.radio)[0].set_value("Players").run()
    assert not at.exception, f"app raised: {at.exception}"
    return at


def test_app_players_page_renders_stats_without_run_or_key():
    # Only the fake PINs are in secrets — no Odds API key anywhere,
    # and the algorithm is never run. The page must still serve
    # the full stats from the fixture-backed store.
    at = _login_as_king(_run_app(FAKE_SECRETS))
    at.session_state["stats_store"] = _FakeStore(nfl=_nfl_logs(), nba=_nba_logs())
    _goto_players(at)

    assert at.session_state["parlays"] == []  # algorithm never ran
    assert at.session_state["mode"] == "no_key"  # no key, nothing pulled — still works

    text = _all_text(at)
    assert "Player stats: nflverse" in text
    assert "independent of the odds" in text

    boxes = list(at.selectbox)
    assert boxes and "Patrick Mahomes" in boxes[0].options
    # Alphabetical default: Joe Blank. Switch to Mahomes.
    boxes[0].set_value("Patrick Mahomes").run()
    assert not at.exception, f"app raised: {at.exception}"

    text = _all_text(at)
    assert "Patrick Mahomes" in text
    assert any(s.value == "Passing Yards" for s in at.subheader)
    assert any(
        m.label == "Season average" and m.value == "250.0" for m in at.metric
    )
    assert any(
        m.label == "Games logged" and m.value == "3" for m in at.metric
    )
    assert "Over 250 in 1 of his last 3 games" in text
    line_boxes = [
        n for n in at.number_input if n.label == "Test a line — Passing Yards"
    ]
    assert line_boxes and line_boxes[0].value == 250.0


def test_app_players_page_nba_league():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    at.session_state["stats_store"] = _FakeStore(nfl=_nfl_logs(), nba=_nba_logs())
    _goto_players(at)
    league = [r for r in at.radio if r.label == "League"]
    assert league, "no League switch"
    league[0].set_value("NBA").run()
    assert not at.exception, f"app raised: {at.exception}"

    text = _all_text(at)
    assert "Player stats: stats.nba.com" in text
    boxes = list(at.selectbox)
    assert boxes and boxes[0].options == ["Lebron James"]
    assert any(s.value == "Points" for s in at.subheader)
    assert any(
        m.label == "Season average" and m.value == "25.0" for m in at.metric
    )
    assert "Over 25 in 1 of his last 3 games" in text


def test_app_players_page_feed_failure_is_honest_and_other_league_serves():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    at.session_state["stats_store"] = _FakeStore(nfl=_nfl_logs(), nba=None)
    _goto_players(at)
    # NFL (default league) is served fine.
    assert "Player stats: nflverse" in _all_text(at)
    # NBA feed down: honest message, no crash, no invented stats.
    league = [r for r in at.radio if r.label == "League"]
    league[0].set_value("NBA").run()
    assert not at.exception, f"app raised: {at.exception}"
    text = _all_text(at)
    assert "didn't answer" in text
    assert "Lebron James" not in text
    # Back to NFL — still fine.
    league = [r for r in at.radio if r.label == "League"]
    league[0].set_value("NFL").run()
    assert not at.exception, f"app raised: {at.exception}"
    assert "Patrick Mahomes" in list(at.selectbox)[0].options


# ---------- app render: position filter ----------

def _position_box(at):
    matches = [b for b in at.selectbox if b.label == "Position"]
    return matches[0] if matches else None


def _picker_box(at):
    matches = [b for b in at.selectbox if b.label.startswith("Pick a player")]
    assert matches, "no player picker"
    return matches[0]


def test_app_players_page_position_filter_narrows_picker():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    at.session_state["stats_store"] = _FakeStore(
        nfl=_nfl_logs_with_positions(), nba=_nba_logs()
    )
    _goto_players(at)

    box = _position_box(at)
    assert box is not None, "position filter missing"
    # AppTest exposes the formatted labels — counts included.
    assert box.options == [
        "All positions (4)", "QB (2)", "WR (1)", "Other / unknown (1)",
    ]

    # Narrow to QBs: the picker keeps only the two quarterbacks.
    box.set_value("QB (2)").run()
    assert not at.exception, f"app raised: {at.exception}"
    picker = _picker_box(at)
    assert picker.options == ["Michael Penix", "Patrick Mahomes"]

    # The chosen player's position shows in his header.
    picker.set_value("Patrick Mahomes").run()
    assert not at.exception, f"app raised: {at.exception}"
    assert "Patrick Mahomes · QB" in _all_text(at)

    # The unknown group holds the player the feed never placed.
    _position_box(at).set_value("Other / unknown (1)").run()
    assert not at.exception, f"app raised: {at.exception}"
    assert _picker_box(at).options == ["Joe Blank"]


def test_app_players_page_without_positions_renders_as_before():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    at.session_state["stats_store"] = _FakeStore(nfl=_nfl_logs(), nba=_nba_logs())
    _goto_players(at)

    # No filter, one honest caption, the full single list.
    assert _position_box(at) is None
    assert "Position info isn't available" in _all_text(at)
    assert _picker_box(at).options == [
        "Joe Blank", "Luther Burden", "Michael Penix", "Patrick Mahomes",
    ]
