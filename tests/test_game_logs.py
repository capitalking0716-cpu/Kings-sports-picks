"""Tests for the free game-log feeds (Part 2) — parsers run on
small FIXTURE data only. No network in tests, ever.

Run: pytest -q
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import game_logs
from kings_picks.game_logs import GameLogs, StatsStore, summarize
from kings_picks.model import Prop

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}

# ---------- nflverse fixture ----------
# Columns the parser needs, in the real feed's names. Weeks are
# deliberately out of order in the file; the parser must sort.
NFL_HEADER = (
    "player_id,player_name,player_display_name,position,season,week,season_type,"
    "passing_yards,passing_tds,rushing_yards,rushing_tds,receiving_yards,"
    "receiving_tds,receptions"
)
NFL_CSV = "\n".join([
    NFL_HEADER,
    # Mahomes: weeks 1..3 passing 200/300/250; a POST row (999) must be dropped.
    "00-1,P.Mahomes,Patrick Mahomes,QB,2026,2,REG,300,3,20,0,0,0,0",
    "00-1,P.Mahomes,Patrick Mahomes,QB,2026,1,REG,200,1,10,0,0,0,0",
    "00-1,P.Mahomes,Patrick Mahomes,QB,2026,3,REG,250,2,15,1,0,0,0",
    "00-1,P.Mahomes,Patrick Mahomes,QB,2026,19,POST,999,9,99,9,99,9,9",
    # Burden: feed name has NO suffix; lookups will use "Luther Burden III".
    "00-2,L.Burden,Luther Burden,WR,2026,1,REG,0,0,5,0,80,1,6",
    "00-2,L.Burden,Luther Burden,WR,2026,2,REG,0,0,0,0,40,0,3",
    # Penix: feed name HAS the suffix.
    "00-3,M.Penix,Michael Penix Jr.,QB,2026,1,REG,280,2,12,0,0,0,0",
    "00-3,M.Penix,Michael Penix Jr.,QB,2026,2,REG,190,0,8,0,0,0,0",
    # Blank stat cell counts as 0 (he played; he just did none of it).
    "00-4,J.Blank,Joe Blank,WR,2026,1,REG,0,0,0,0,,0,2",
])


def test_normalize_name_suffixes_and_punctuation():
    assert game_logs.normalize_name("Michael Penix Jr.") == "michael penix"
    assert game_logs.normalize_name("Luther Burden III") == "luther burden"
    assert game_logs.normalize_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert game_logs.normalize_name("Patrick Mahomes") == "patrick mahomes"
    assert game_logs.normalize_name("") == ""


def test_nflverse_parse_sorts_newest_first_and_drops_postseason():
    logs = game_logs.parse_nflverse_csv(NFL_CSV)
    mahomes = logs[game_logs.normalize_name("Patrick Mahomes")]
    assert mahomes["Passing Yards"] == [250.0, 300.0, 200.0]  # weeks 3,2,1
    assert 999.0 not in mahomes["Passing Yards"]
    assert mahomes["Passing Touchdowns"] == [2.0, 3.0, 1.0]


def test_nflverse_anytime_td_derived_from_rush_plus_rec():
    logs = game_logs.parse_nflverse_csv(NFL_CSV)
    burden = logs[game_logs.normalize_name("Luther Burden")]
    # Week 1: 1 receiving TD -> 1.0. Week 2: none -> 0.0. Newest first:
    assert burden["Anytime Touchdown"] == [0.0, 1.0]
    assert burden["Receiving Yards"] == [40.0, 80.0]
    assert burden["Receptions"] == [3.0, 6.0]


def test_nflverse_blank_cell_is_zero_not_a_crash():
    logs = game_logs.parse_nflverse_csv(NFL_CSV)
    blank = logs[game_logs.normalize_name("Joe Blank")]
    assert blank["Receiving Yards"] == [0.0]


def test_gamelogs_lookup_matches_across_suffix_styles():
    logs = GameLogs(sport="NFL", by_player=game_logs.parse_nflverse_csv(NFL_CSV),
                    source_label=game_logs.NFL_SOURCE, season_label="2026")
    # Book-style name WITH suffix finds the feed name without one, and back.
    assert logs.values("Luther Burden III", "Receiving Yards") == [40.0, 80.0]
    assert logs.values("Michael Penix", "Passing Yards") == [190.0, 280.0]
    assert logs.values("Michael Penix Jr.", "Passing Yards") == [190.0, 280.0]
    assert logs.values("Nobody Here", "Passing Yards") is None


# ---------- NBA fixture ----------

NBA_ROWS = [
    {"PLAYER_NAME": "LeBron James", "GAME_DATE": "2026-11-06", "PTS": 20, "REB": 5, "AST": 6, "FG3M": 2},
    {"PLAYER_NAME": "LeBron James", "GAME_DATE": "2026-11-10", "PTS": 30, "REB": 8, "AST": 9, "FG3M": 3},
    {"PLAYER_NAME": "LeBron James", "GAME_DATE": "2026-11-08", "PTS": 25, "REB": 7, "AST": 7, "FG3M": 1},
    {"PLAYER_NAME": "LeBron James", "GAME_DATE": "2026-11-04", "PTS": None, "REB": None, "AST": None, "FG3M": None},  # DNP
]


def test_nba_parse_newest_first_pra_sum_and_dnp_skipped():
    logs = game_logs.parse_nba_game_log_rows(NBA_ROWS)
    lebron = logs[game_logs.normalize_name("LeBron James")]
    assert lebron["Points"] == [30.0, 25.0, 20.0]  # DNP row gone, dates desc
    assert lebron["Points + Rebounds + Assists"] == [47.0, 39.0, 31.0]
    assert lebron["Three-Pointers Made"] == [3.0, 1.0, 2.0]


# ---------- summarize / hit counting ----------

def test_summarize_counts_hits_for_the_picks_side():
    values = [300.0, 200.0, 250.0]  # newest first
    over = summarize(values, "Over", 249.5)
    assert (over.hits, over.games) == (2, 3)
    under = summarize(values, "Under", 249.5)
    assert (under.hits, under.games) == (1, 3)


def test_summarize_push_counts_for_neither_side():
    values = [250.0, 240.0]
    assert summarize(values, "Over", 250.0).hits == 0
    assert summarize(values, "Under", 250.0).hits == 1


def test_summarize_averages_and_last10_cap():
    values = [float(v) for v in range(20, 0, -1)]  # 20 games
    s = summarize(values, "Over", 10.5)
    assert s.games == 10  # capped at last 10
    assert s.values == values[:10]
    assert abs(s.last5_avg - sum(values[:5]) / 5) < 1e-9
    assert abs(s.season_avg - sum(values) / 20) < 1e-9
    assert summarize([], "Over", 10.5) is None


def test_form_carries_source_label():
    logs = GameLogs(sport="NFL", by_player=game_logs.parse_nflverse_csv(NFL_CSV),
                    source_label=game_logs.NFL_SOURCE, season_label="2026")
    form = logs.form("Patrick Mahomes", "Passing Yards", "Over", 249.5)
    assert form is not None
    assert (form.hits, form.games) == (2, 3)
    assert form.source_label == game_logs.NFL_SOURCE


# ---------- seasons ----------

def test_season_helpers():
    assert game_logs.nfl_season_for(date(2026, 11, 12)) == 2026
    assert game_logs.nfl_season_for(date(2027, 2, 5)) == 2026  # Super Bowl month
    assert game_logs.nba_season_for(date(2026, 11, 12)) == "2026-27"
    assert game_logs.nba_season_for(date(2027, 3, 1)) == "2026-27"
    assert game_logs.nba_season_for(date(2026, 5, 1)) == "2025-26"


# ---------- form lookup (what score_all consumes) ----------

def test_make_form_lookup_rules():
    nfl = GameLogs(sport="NFL", by_player=game_logs.parse_nflverse_csv(NFL_CSV),
                   source_label=game_logs.NFL_SOURCE, season_label="2026")
    lookup = game_logs.make_form_lookup(nfl, None)
    live = Prop(player="Patrick Mahomes", sport="NFL", prop="Passing Yards",
                side="Over", line=249.5, odds=-110, source="live")
    # v2: the lookup hands scoring the RAW LOG VALUES (newest
    # first) for the projection model — not a hit-rate count.
    assert lookup(live) == [250.0, 300.0, 200.0]
    unknown = Prop(player="Ghost Player", sport="NFL", prop="Passing Yards",
                   side="Over", line=249.5, odds=-110, source="live")
    assert lookup(unknown) is None
    nba_prop = Prop(player="LeBron James", sport="NBA", prop="Points",
                    side="Over", line=24.5, odds=-110, source="live")
    assert lookup(nba_prop) is None  # no NBA logs loaded -> odds-only


# ---------- StatsStore: fail-soft + cache ----------

class _BoomSession:
    def get(self, *args, **kwargs):
        raise game_logs.requests.ConnectionError("no network")


def test_store_fails_soft_when_feed_is_down(tmp_path):
    store = StatsStore(tmp_path, session=_BoomSession(), today=date(2026, 11, 12))
    assert store.nfl_logs() is None  # no exception, just no form


def test_store_caches_to_disk_and_reuses(tmp_path):
    calls = {"n": 0}

    class _OkSession:
        def get(self, url, timeout=None):
            calls["n"] += 1

            class R:
                status_code = 200
                text = NFL_CSV

            return R()

    first = StatsStore(tmp_path, session=_OkSession(), today=date(2026, 11, 12))
    logs = first.nfl_logs()
    assert logs is not None and logs.season_label == "2026"
    assert calls["n"] == 1
    # A fresh store (new app session) reads the disk cache — no new fetch.
    second = StatsStore(tmp_path, session=_BoomSession(), today=date(2026, 11, 12))
    cached = second.nfl_logs()
    assert cached is not None
    assert cached.values("Patrick Mahomes", "Passing Yards") == [250.0, 300.0, 200.0]
    assert calls["n"] == 1


# ---------- positions (browsing only — scoring never reads them) ----------

def test_nflverse_positions_from_the_feeds_own_column():
    positions = game_logs.parse_nflverse_positions(NFL_CSV)
    assert positions == {
        "patrick mahomes": "QB",
        "luther burden": "WR",
        "michael penix": "QB",
        "joe blank": "WR",
    }


# A player the feed lists at different positions across weeks;
# rows deliberately out of order, plus a POST row that must not
# count and a blank cell on the latest week.
NFL_POSITIONS_CSV = "\n".join([
    NFL_HEADER,
    "00-9,H.Hybrid,Hybrid Player,WR,2026,3,REG,0,0,0,0,0,0,0",
    "00-9,H.Hybrid,Hybrid Player,RB,2026,1,REG,0,0,0,0,0,0,0",
    "00-9,H.Hybrid,Hybrid Player,TE,2026,19,POST,0,0,0,0,0,0,0",
    "00-8,B.Blank,Blank Latest,QB,2026,1,REG,0,0,0,0,0,0,0",
    "00-8,B.Blank,Blank Latest,,2026,4,REG,0,0,0,0,0,0,0",
    "00-7,N.Never,Never Stated,,2026,2,REG,0,0,0,0,0,0,0",
])


def test_nflverse_positions_most_recent_stated_week_wins():
    positions = game_logs.parse_nflverse_positions(NFL_POSITIONS_CSV)
    # Week 3 WR beats week 1 RB; the POST week's TE is ignored.
    assert positions["hybrid player"] == "WR"
    # A blank on the latest week never erases a stated position.
    assert positions["blank latest"] == "QB"
    # Never stated anywhere -> absent, not invented.
    assert "never stated" not in positions


def test_gamelogs_position_of_lookup():
    logs = GameLogs(
        sport="NFL",
        by_player=game_logs.parse_nflverse_csv(NFL_CSV),
        source_label=game_logs.NFL_SOURCE,
        season_label="2026",
        positions=game_logs.parse_nflverse_positions(NFL_CSV),
    )
    assert logs.position_of("Patrick Mahomes") == "QB"
    assert logs.position_of("Michael Penix Jr.") == "QB"  # suffix-safe
    assert logs.position_of("Nobody Here") is None


def test_expand_nba_position_codes_to_words():
    assert game_logs.expand_nba_position("G") == "Guard"
    assert game_logs.expand_nba_position("F") == "Forward"
    assert game_logs.expand_nba_position("C") == "Center"
    assert game_logs.expand_nba_position("G-F") == "Guard-Forward"
    assert game_logs.expand_nba_position("F-C") == "Forward-Center"
    assert game_logs.expand_nba_position("C-F") == "Center-Forward"
    assert game_logs.expand_nba_position("") == ""
    assert game_logs.expand_nba_position(None) == ""


NBA_INDEX_ROWS = [
    {"PLAYER_FIRST_NAME": "LeBron", "PLAYER_LAST_NAME": "James", "POSITION": "F"},
    {"PLAYER_FIRST_NAME": "Bam", "PLAYER_LAST_NAME": "Adebayo", "POSITION": "C-F"},
    {"PLAYER_FIRST_NAME": "Darius", "PLAYER_LAST_NAME": "Acuff Jr.", "POSITION": "G"},
    {"PLAYER_FIRST_NAME": "No", "PLAYER_LAST_NAME": "Position", "POSITION": ""},
    {"PLAYER_FIRST_NAME": "", "PLAYER_LAST_NAME": "", "POSITION": "G"},
]


def test_nba_player_index_rows_parse_positions():
    positions = game_logs.parse_nba_player_index_rows(NBA_INDEX_ROWS)
    assert positions == {
        "lebron james": "Forward",
        "bam adebayo": "Center-Forward",
        "darius acuff": "Guard",  # suffix in the last name normalizes away
    }


def test_store_positions_survive_the_disk_cache(tmp_path):
    class _OkSession:
        def get(self, url, timeout=None):
            class R:
                status_code = 200
                text = NFL_CSV

            return R()

    first = StatsStore(tmp_path, session=_OkSession(), today=date(2026, 11, 12))
    assert first.nfl_logs().position_of("Patrick Mahomes") == "QB"
    # Fresh store, dead network: positions ride the disk cache too.
    second = StatsStore(tmp_path, session=_BoomSession(), today=date(2026, 11, 12))
    cached = second.nfl_logs()
    assert cached is not None
    assert cached.position_of("Luther Burden") == "WR"


def test_store_ignores_pre_positions_v1_cache(tmp_path):
    import json
    import time as _time

    # A fresh, unexpired v1 cache file (logs only, written by the
    # pre-positions build) must NOT be served — the store has to
    # hit the (dead) network instead of showing a position-less
    # week from a stale file.
    (tmp_path / "nflverse_2026.json").write_text(json.dumps({
        "fetched_at": _time.time(),
        "season": 2026,
        "logs": {"patrick mahomes": {"Passing Yards": [1.0]}},
    }))
    store = StatsStore(tmp_path, session=_BoomSession(), today=date(2026, 11, 12))
    assert store.nfl_logs() is None


def _write_nba_caches(tmp_path, with_positions=True):
    import json
    import time as _time

    (tmp_path / "nba_gamelogs.json").write_text(json.dumps({
        "fetched_at": _time.time(),
        "season": "2026-27",
        "logs": game_logs.parse_nba_game_log_rows(NBA_ROWS),
    }))
    if with_positions:
        (tmp_path / "nba_positions.json").write_text(json.dumps({
            "fetched_at": _time.time(),
            "season": "2026-27",
            "positions": {"lebron james": "Forward"},
        }))


def test_store_nba_positions_come_from_their_own_cache(tmp_path):
    _write_nba_caches(tmp_path, with_positions=True)
    # BoomSession proves no network is touched on the cache path.
    store = StatsStore(tmp_path, session=_BoomSession(), today=date(2026, 11, 12))
    logs = store.nba_logs()
    assert logs is not None
    assert logs.position_of("LeBron James") == "Forward"


def test_store_nba_logs_survive_when_positions_unavailable(tmp_path):
    _write_nba_caches(tmp_path, with_positions=False)

    class _NoPositionsStore(StatsStore):
        def _fetch_nba_positions(self, season):
            return None

    store = _NoPositionsStore(
        tmp_path, session=_BoomSession(), today=date(2026, 11, 12)
    )
    logs = store.nba_logs()
    assert logs is not None  # the game logs themselves still load
    assert logs.positions == {}  # positions degrade to unknown, honestly
    assert logs.position_of("LeBron James") is None


# ---------- app render: Props page breakdown (AppTest) ----------

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


def test_app_props_page_shows_board_and_breakdown_after_live_run():
    # A live-scored board injected the way the sibling test
    # below does it (demo mode was removed 2026-11-12, so
    # there is no sample run to click through any more).
    from kings_picks.model import score_prop

    at = _login_as_king(_run_app(FAKE_SECRETS))
    raw = [
        Prop(player="Fixture Star", sport="NBA", prop="Points",
             side=side, line=24.5, odds=-110, bookmaker="FixtureBook",
             event="Team A vs Team B", source="live")
        for side in ("Over", "Under")
    ]
    at.session_state["scored"] = [score_prop(raw[0], pair_odds=-110)]
    at.session_state["raw_props"] = raw
    at.session_state["parlays"] = []
    at.session_state["mode"] = "live"
    list(at.radio)[0].set_value("Props").run()
    assert not at.exception, f"app raised: {at.exception}"
    subheads = [s.value for s in at.subheader]
    assert any("board at a glance" in s for s in subheads)
    assert any("Player breakdown" in s for s in subheads)
    boxes = list(at.selectbox)
    assert boxes, "player picker missing"
    assert "Fixture Star" in boxes[0].options


def test_app_props_breakdown_shows_recent_form_when_feed_matched():
    from kings_picks.model import score_prop

    at = _login_as_king(_run_app(FAKE_SECRETS))
    prop = Prop(player="Patrick Mahomes", sport="NFL", prop="Passing Yards",
                side="Over", line=249.5, odds=-110, bookmaker="DraftKings",
                event="Chiefs vs Bills", source="live")
    scored_pick = score_prop(prop, pair_odds=-110, form_probability=0.55,
                             projected_value=262.0, projection_games=8)
    at.session_state["scored"] = [scored_pick]
    at.session_state["raw_props"] = [
        prop,
        Prop(player="Patrick Mahomes", sport="NFL", prop="Passing Yards",
             side="Under", line=249.5, odds=-110, source="live"),
    ]
    at.session_state["parlays"] = []
    at.session_state["mode"] = "live"
    at.session_state["nfl_logs"] = GameLogs(
        sport="NFL", by_player=game_logs.parse_nflverse_csv(NFL_CSV),
        source_label=game_logs.NFL_SOURCE, season_label="2026")
    list(at.radio)[0].set_value("Props").run()
    assert not at.exception, f"app raised: {at.exception}"
    all_text = "\n".join(
        [m.value for m in at.markdown]
        + [s.value for s in at.subheader]
        + [c.value for c in at.caption]
    )
    assert "Recent form" in all_text
    assert "nflverse" in all_text
