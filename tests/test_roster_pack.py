"""Tests for the 2026-11-12 evening build, Neko's three orders:

1. ROSTER GUARD (kings_picks/roster.py) — a leg naming a
   player who isn't in the listed game (a star under the
   wrong event, a college player on an NBA board) is dropped
   BEFORE scoring: never picked, never logged. Fail-soft:
   an index that didn't load stands the guard down.
2. BUNDLED STATS PACK (kings_picks/stats_pack.py,
   scripts/build_stats_pack.py) — with the live feed
   answering nothing, a known player still gets real form
   data, a stats-based chance, and priced drop options from
   the pack shipped with the app; a player the pack doesn't
   know still reads NO DATA.
3. SIMPLER TICKETS — rendered tickets carry no "Lilly's
   verdict" and no "The odds say about" paragraph, while the
   header, the per-leg form line, the dropped-from note and
   the footer all stay.

Run: pytest -q

The PINs below ("1111" / "2222") are FAKE test values. The
real PINs exist only in Neko's Streamlit secrets.
"""

import importlib.util
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import game_logs, players as players_mod, roster, stats_pack
from kings_picks.model import Prop, score_prop
from kings_picks.odds_api import AltPrice, AltResult, ApiResult, OddsApiClient
from kings_picks.parlays import Parlay, build_parlays
from kings_picks.pick_log import PickLog
from kings_picks.slip import build_drop_options

FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
PULLED = "2026-11-12T21:37:00+00:00"

_BUILDER_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "build_stats_pack.py"
)


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_stats_pack", _BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prop(player, sport="NBA", event="Detroit Pistons vs Washington Wizards",
          prop="Assists", side="Over", line=1.5, odds=-183):
    return Prop(
        player=player, sport=sport, prop=prop, side=side, line=line,
        odds=odds, bookmaker="FixtureBook", event=event, source="live",
    )


def _index():
    return roster.RosterIndex(teams={
        "NBA": {
            "anthony davis": "DAL",
            "cade cunningham": "DET",
            "alex sarr": "WAS",
            "jayson tatum": "BOS",
        },
        "NFL": {
            "patrick mahomes": "KC",
            "josh allen": "BUF",
        },
    })


# ---------- roster: resolution ----------


def test_resolve_team_accepts_abbr_full_name_and_nickname_forms():
    assert roster.resolve_team("NBA", "DET") == "DET"
    assert roster.resolve_team("NBA", "Detroit Pistons") == "DET"
    assert roster.resolve_team("NBA", "LA Clippers") == "LAC"
    assert roster.resolve_team("NBA", "Los Angeles Clippers") == "LAC"
    assert roster.resolve_team("NFL", "kc") == "KC"
    assert roster.resolve_team("NFL", "Kansas City Chiefs") == "KC"
    # Feed spelling variants land on the canonical team.
    assert roster.resolve_team("NFL", "LA") == "LAR"
    assert roster.resolve_team("NBA", "PHO") == "PHX"
    assert roster.resolve_team("NBA", "Not A Team") is None
    assert roster.resolve_team("NBA", "") is None


def test_event_team_abbrs_reads_both_sides():
    assert roster.event_team_abbrs(
        "NBA", "Detroit Pistons vs Washington Wizards"
    ) == ("DET", "WAS")
    assert roster.event_team_abbrs("NBA", "Team A vs Team B") == ()
    assert roster.event_team_abbrs("NBA", "") == ()


# ---------- roster: the guard ----------


def test_guard_drops_college_and_unknown_names():
    props = [
        _prop("AJ Dybantsa"),       # college player — not in the index
        _prop("Morez Johnson Jr."),  # college player — not in the index
        _prop("Cade Cunningham"),   # DET, in the DET vs WAS game — fine
    ]
    kept, dropped, active = roster.guard_props(props, _index())
    assert active
    assert dropped == 2
    assert [p.player for p in kept] == ["Cade Cunningham"]


def test_guard_drops_a_star_under_the_wrong_event():
    # Anthony Davis is a real NBA player in the index (DAL) —
    # but this leg lists him under Detroit vs Washington.
    props = [_prop("Anthony Davis"), _prop("Alex Sarr")]
    kept, dropped, active = roster.guard_props(props, _index())
    assert active and dropped == 1
    assert [p.player for p in kept] == ["Alex Sarr"]


def test_guard_keeps_a_star_under_his_own_event():
    props = [
        _prop("Anthony Davis", event="Washington Wizards vs Dallas Mavericks"),
        _prop("Jayson Tatum", event="Boston Celtics vs Detroit Pistons"),
    ]
    kept, dropped, active = roster.guard_props(props, _index())
    assert active and dropped == 0 and len(kept) == 2


def test_guard_covers_the_nfl_path():
    good = _prop(
        "Patrick Mahomes", sport="NFL", prop="Passing Yards",
        side="Over", line=249.5, odds=-110,
        event="Buffalo Bills vs Kansas City Chiefs",
    )
    bad = _prop(
        "Patrick Mahomes", sport="NFL", prop="Passing Yards",
        side="Over", line=249.5, odds=-110,
        event="Buffalo Bills vs Miami Dolphins",
    )
    kept, dropped, active = roster.guard_props([good, bad], _index())
    assert active and dropped == 1 and kept == [good]


def test_guard_stands_down_when_the_index_is_empty_or_unrelated():
    props = [_prop("Cade Cunningham")]
    kept, dropped, active = roster.guard_props(props, roster.RosterIndex())
    assert (kept, dropped, active) == (props, 0, False)
    # An index that knows nobody on this board didn't really
    # load — it must not nuke a real slate.
    other = roster.RosterIndex(teams={"NBA": {"someone else": "DET"}})
    kept, dropped, active = roster.guard_props(props, other)
    assert (kept, dropped, active) == (props, 0, False)
    kept, dropped, active = roster.guard_props(props, None)
    assert (kept, dropped, active) == (props, 0, False)


def test_guard_keys_match_across_name_spellings():
    # The books' spelling (suffix, initials) must find the
    # index entry — the self-test normaliser's candidates.
    index = roster.RosterIndex(teams={"NBA": {"rj barrett": "TOR"}})
    props = [_prop("R.J. Barrett", event="Toronto Raptors vs Boston Celtics")]
    kept, dropped, active = roster.guard_props(props, index)
    assert active and dropped == 0 and kept == props


def test_rejected_legs_never_reach_parlays_or_the_pick_log(tmp_path):
    board = []
    for i in range(12):
        team_event = "Detroit Pistons vs Washington Wizards"
        board.append(_prop(f"Guard Fixture{i}", event=team_event))
    index = roster.RosterIndex(teams={
        "NBA": {f"guard fixture{i}": ("DET" if i % 2 == 0 else "WAS")
                for i in range(12)},
    })
    board.append(_prop("Anthony Davis"))  # DAL under DET vs WAS
    board.append(_prop("AJ Dybantsa"))    # not in the index at all
    kept, dropped, active = roster.guard_props(board, index)
    assert active and dropped == 2

    scored = [score_prop(p, pair_odds=-110) for p in kept]
    parlays, _ = build_parlays(scored, plan=[(3, 1)])
    assert parlays
    for parlay in parlays:
        names = {p.player for p in parlay.picks}
        assert "Anthony Davis" not in names and "AJ Dybantsa" not in names

    log = PickLog(tmp_path / "pick_log.json")
    log.record_run(parlays, "live", created="2026-11-12", created_at=PULLED)
    logged_players = {e.player for e in log.entries}
    assert "Anthony Davis" not in logged_players
    assert "AJ Dybantsa" not in logged_players
    assert logged_players  # the real legs did get logged


# ---------- game_logs: team parsers ----------


def test_parse_nba_player_index_teams():
    rows = [
        {"PLAYER_FIRST_NAME": "Anthony", "PLAYER_LAST_NAME": "Davis",
         "TEAM_ABBREVIATION": "DAL", "IS_DEFUNCT": 0},
        {"PLAYER_FIRST_NAME": "Ghost", "PLAYER_LAST_NAME": "Oldtimer",
         "TEAM_ABBREVIATION": "", "IS_DEFUNCT": 0},
        {"PLAYER_FIRST_NAME": "Nan", "PLAYER_LAST_NAME": "Cells",
         "TEAM_ABBREVIATION": "DET", "IS_DEFUNCT": float("nan")},
    ]
    teams = game_logs.parse_nba_player_index_teams(rows)
    assert teams["anthony davis"] == "DAL"
    assert "ghost oldtimer" not in teams
    assert teams["nan cells"] == "DET"  # NaN cells never crash the parse


def test_parse_nflverse_teams_takes_the_most_recent_row():
    header = (
        "player_display_name,position,recent_team,season,week,season_type,"
        "passing_yards,passing_tds,rushing_yards,receiving_yards,receptions,"
        "rushing_tds,receiving_tds\n"
    )
    rows = (
        "Patrick Mahomes,QB,KC,2026,1,REG,300,2,10,0,0,0,0\n"
        "Traded Player,WR,BUF,2026,1,REG,0,0,0,50,4,0,1\n"
        "Traded Player,WR,MIA,2026,9,REG,0,0,0,60,5,0,0\n"
    )
    teams = game_logs.parse_nflverse_teams(header + rows)
    assert teams["patrick mahomes"] == "KC"
    assert teams["traded player"] == "MIA"


# ---------- stats pack: build, load, merge ----------

_PACK_VALUES = [30.0, 26.0, 24.0, 28.0, 22.0, 27.0, 25.0, 29.0, 23.0, 26.0,
                31.0, 21.0, 27.0, 25.0, 29.0, 24.0, 28.0, 26.0, 30.0, 22.0]
_PACK_FETCHED = time.mktime((2026, 11, 12, 12, 0, 0, 0, 0, -1))


def _write_synthetic_caches(cache_dir: Path):
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "nba_gamelogs.json").write_text(json.dumps({
        "fetched_at": _PACK_FETCHED,
        "season": "2026-27",
        "logs": {"test star": {"Points": list(_PACK_VALUES)}},
    }))
    (cache_dir / "nba_positions.json").write_text(json.dumps({
        "fetched_at": _PACK_FETCHED,
        "season": "2026-27",
        "positions": {"test star": "Guard"},
    }))


def test_pack_builder_trims_and_stamps_from_the_caches(tmp_path):
    builder = _load_builder()
    cache_dir = tmp_path / "cache"
    _write_synthetic_caches(cache_dir)
    out = tmp_path / "bundled" / "stats_pack.json"
    payload = builder.build_pack(cache_dir, out, fetch_teams=False)
    assert payload["stats_through"] == "2026-11-12"
    nba = payload["sports"]["NBA"]
    assert nba["season"] == "2026-27"
    assert nba["logs"]["test star"]["Points"] == _PACK_VALUES[:15]
    assert nba["positions"] == {"test star": "Guard"}
    assert "NFL" not in payload["sports"]  # no NFL cache written
    assert json.loads(out.read_text()) == payload


def test_pack_loader_and_merge_give_form_when_the_live_feed_is_empty(tmp_path):
    builder = _load_builder()
    cache_dir = tmp_path / "cache"
    _write_synthetic_caches(cache_dir)
    out = tmp_path / "stats_pack.json"
    builder.build_pack(cache_dir, out, fetch_teams=False)

    nfl, nba, stamp = stats_pack.load_pack(path=out)
    assert nfl is None and nba is not None
    assert stamp == "Nov 12, 2026"
    assert nba.source_label == stats_pack.SOURCE_LABEL
    assert nba.values("Test Star", "Points") == _PACK_VALUES[:15]
    assert nba.values("Nobody Packed", "Points") is None

    # The fallback order itself: live None -> pack; a live
    # answer always outranks the pack.
    import kings_picks.stats_pack as pack_mod

    saved_path, saved_cache = pack_mod.PACK_PATH, pack_mod._cache
    try:
        pack_mod.PACK_PATH, pack_mod._cache = out, None
        m_nfl, m_nba, m_stamp = pack_mod.merge_logs(None, None)
        assert m_nba is not None and m_stamp == "Nov 12, 2026"
        live = game_logs.GameLogs(sport="NBA", by_player={"live guy": {}})
        m_nfl, m_nba, m_stamp = pack_mod.merge_logs(None, live)
        # A live answer outranks the pack, and with nothing
        # riding on the pack (this pack has no NFL) the stamp
        # goes quiet.
        assert m_nba is live and m_stamp == ""
    finally:
        pack_mod.PACK_PATH, pack_mod._cache = saved_path, saved_cache


def test_stats_pack_shipped_in_the_repo_is_real():
    # The pack that ships with the app (built from the real
    # local caches) loads and covers both leagues.
    nfl, nba, stamp = stats_pack.load_pack()
    assert nfl is not None and nba is not None and stamp
    assert len(nba.by_player) > 100
    assert len(nfl.by_player) > 100
    assert nba.teams and nfl.teams


# ---------- stats pack: scoring and drops from the pack ----------


def _pack_nba_logs():
    return game_logs.GameLogs(
        sport="NBA",
        by_player={"test star": {"Points": list(_PACK_VALUES[:15])}},
        source_label=stats_pack.SOURCE_LABEL,
        season_label="2026-27",
        teams={"test star": "DET"},
    )


def test_packed_player_gets_a_stats_based_chance_with_live_feed_down():
    # The live feed is stubbed empty by construction: the
    # only logs in the room are the pack's.
    lookup = game_logs.make_form_lookup(None, _pack_nba_logs())
    prop = _prop("Test Star", prop="Points", side="Over", line=24.5, odds=-110,
                 event="Detroit Pistons vs Washington Wizards")
    values = lookup(prop)
    assert values == _PACK_VALUES[:15]

    from kings_picks import projection

    form_probability = projection.over_probability(values, prop.line, prop.side)
    assert form_probability is not None
    scored = score_prop(prop, pair_odds=-110, form_probability=form_probability,
                        projected_value=projection.project_stat(values).expected,
                        projection_games=len(values))
    assert scored.has_form_data
    odds_only = score_prop(prop, pair_odds=-110)
    assert not odds_only.has_form_data
    assert scored.probability != odds_only.probability


def test_packed_player_gets_drop_options_and_unpacked_stays_no_data():
    lookup = game_logs.make_form_lookup(None, _pack_nba_logs())
    prop = _prop("Test Star", prop="Points", side="Over", line=24.5, odds=-110,
                 event="Detroit Pistons vs Washington Wizards")
    scored = score_prop(prop, pair_odds=-110)
    alts = [
        AltPrice(player="Test Star", sport="NBA", prop="Points",
                 market_key="player_points", side="Over", line=20.5,
                 odds=-250, bookmaker="FixtureBook"),
        AltPrice(player="Test Star", sport="NBA", prop="Points",
                 market_key="player_points", side="Under", line=20.5,
                 odds=180, bookmaker="FixtureBook"),
    ]
    options = build_drop_options(scored, alts, form_values=lookup(prop))
    playable = [o for o in options if o.playable]
    assert playable, "a packed player must get priced drop options"
    assert all(o.probability is not None for o in playable)

    # A player absent from the pack: no values, NO DATA —
    # the lookup is honestly empty, never invented.
    ghost = _prop("Ghost Player", prop="Points", side="Over", line=24.5,
                  odds=-110, event="Detroit Pistons vs Washington Wizards")
    assert lookup(ghost) is None


# ---------- app: fakes shared by the AppTests ----------


class _FakeStore:
    """StatsStore stand-in: the live feeds answer nothing,
    so everything stats-shaped must come from the pack."""

    def nfl_logs(self):
        return None

    def nba_logs(self):
        return None

    def nba_teams(self):
        return {}


def _run_app(secrets=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    at.session_state["stats_store"] = _FakeStore()
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
    parts = []
    for accessor in ("title", "subheader", "markdown", "caption", "info", "success"):
        try:
            parts += [el.value for el in getattr(at, accessor)]
        except Exception:
            pass
    return "\n".join(parts)


def _ticket_texts(at):
    return sorted(m.value for m in at.markdown if "bet-ticket'>" in m.value)


# ---------- app: the stamp renders ----------


def test_app_dashboard_shows_the_pack_stamp():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    stamp = stats_pack.load_pack()[2]
    assert stamp  # the shipped pack carries a stamp
    text = _all_text(at)
    assert "Backup stats pack aboard" in text
    assert stamp in text


def test_app_players_page_falls_back_to_the_pack():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    list(at.radio)[0].set_value("Players").run()
    assert not at.exception, f"app raised: {at.exception}"
    text = _all_text(at)
    assert "bundled stats pack" in text
    stamp = stats_pack.load_pack()[2]
    assert f"stats through {stamp}" in text
    # Real packed players are browsable (NFL is the default
    # league on the page).
    nfl, _nba, _stamp = stats_pack.load_pack()
    expected = players_mod.player_names(nfl)[0]
    boxes = list(at.selectbox)
    picker = [b for b in boxes if expected in (b.options or [])]
    assert picker, f"{expected} not offered on the Players page"


# ---------- app: simpler tickets from a stored slate ----------


def test_app_stored_slate_tickets_are_simple(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    from kings_picks import slate_store

    def scored(player="Fixture0", line=10.5, odds=-110, **kw):
        prop = Prop(
            player=player, sport="NBA", prop="Points", side="Over",
            line=line, odds=odds, bookmaker="FixtureBook",
            event="Team A vs Team B", source="live", **kw,
        )
        return score_prop(prop, pair_odds=-110)

    board = [scored(f"Fixture{i}", line=10.5 + i) for i in range(12)]
    for pick in board:
        pick.form_verdict = "NO DATA"
        pick.form_record = "no game log"
    parlays, _ = build_parlays(board, plan=[(3, 1)])
    king_leg = scored("Fixture0", line=6.5, odds=-170)
    king_leg.base_line = 10.5
    king_leg.king_note = "no dropped line offered — standard leg kept"
    king_leg.form_verdict = "NO DATA"
    king_leg.form_record = "no game log"
    king = Parlay(
        size=1, picks=[king_leg], overall_probability=king_leg.probability,
        decimal_payout=1.59, expected_value_per_dollar=-0.1,
        label="3-pick parlay #1", style="king",
    )
    payload = slate_store.build_payload(
        pulled_at=PULLED, note="fixture note", raw_props=board,
        scored=board, parlays=parlays, king_parlays=[king],
        king_info={"legs_dropped": 1},
    )
    slate_store.SlateStore(tmp_path / "data" / "current_slate.json").save(payload)

    at = _login_as_king(_run_app(FAKE_SECRETS))
    tickets = "\n".join(_ticket_texts(at))
    assert tickets, "no tickets rendered from the stored slate"
    # The paragraphs are gone.
    assert "Lilly's verdict" not in tickets
    assert "The odds say about" not in tickets
    assert "Recent form included" not in tickets
    # Header, leg rows, form line, King note and footer stay.
    assert "KINGS SPORTS BOOKS PICKS" in tickets
    assert "Ticket #KSB-" in tickets
    assert "Model v2" in tickets
    assert "Odds:" in tickets and "Chance:" in tickets
    assert "FixtureBook" in tickets and "Team A vs Team B" in tickets
    assert "⚠️ Odds-only — no recent-form data" in tickets
    assert "Form test:" in tickets
    assert "(dropped from 10.5)" in tickets
    assert "True overall chance:" in tickets
    assert "Combined odds:" in tickets
    assert "If all legs win, $1 staked returns $" in tickets
    assert "Expected value per $1:" in tickets


# ---------- app: the guard end to end ----------


def _pack_event_and_players():
    """Two real packed teams with enough Points logs, from
    the shipped pack — the board the guard test pulls."""
    _nfl, nba, _stamp = stats_pack.load_pack()
    known = dict(roster.NBA_TEAMS)
    by_team: dict[str, list[str]] = {}
    for key, abbr in (nba.teams or {}).items():
        if abbr not in known:
            continue
        stats = (nba.by_player.get(key) or {}).get("Points") or []
        if len(stats) >= 5:
            by_team.setdefault(abbr, []).append(key)
    (abbr_a, keys_a), (abbr_b, keys_b) = sorted(
        by_team.items(), key=lambda kv: -len(kv[1])
    )[:2]
    names = {abbr: dict(roster.NBA_TEAMS)[abbr] for abbr in (abbr_a, abbr_b)}
    event = f"{names[abbr_a]} vs {names[abbr_b]}"
    players = [players_mod.display_name(k) for k in keys_a[:6] + keys_b[:6]]
    third = next(
        abbr for abbr, keys in by_team.items()
        if abbr not in (abbr_a, abbr_b) and keys
    )
    glitch = players_mod.display_name(by_team[third][0])
    return event, players, glitch


def test_app_pull_drops_glitched_legs_and_scores_from_the_pack(
    tmp_path, monkeypatch
):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    event, players, glitch = _pack_event_and_players()
    commence = (
        datetime.now(timezone.utc) + timedelta(hours=3)
    ).replace(microsecond=0).isoformat()
    props = []
    for name in players:
        for side in ("Over", "Under"):
            props.append(Prop(
                player=name, sport="NBA", prop="Points", side=side,
                line=10.5, odds=-110, bookmaker="FixtureBook",
                event=event, event_id="evt-fixture",
                commence_time=commence, source="live",
            ))
    for name in (glitch, "AJ Dybantsa"):
        props.append(Prop(
            player=name, sport="NBA", prop="Points", side="Over",
            line=10.5, odds=-110, bookmaker="FixtureBook",
            event=event, event_id="evt-fixture",
            commence_time=commence, source="live",
        ))

    def fetch_props(self, sport, force_refresh=False, max_events=12):
        return ApiResult(
            True, "fixture props",
            props=[p for p in props if p.sport == sport],
            requests_remaining="499 of 500 requests left this month (1 used)",
        )

    monkeypatch.setattr(OddsApiClient, "has_key", property(lambda self: True))
    monkeypatch.setattr(OddsApiClient, "fetch_props", fetch_props)
    monkeypatch.setattr(
        OddsApiClient, "fetch_alternates",
        lambda self, sport, event_id, market_key, force_refresh=False:
            AltResult(True, "no alternates", prices=[]),
    )
    monkeypatch.setattr(
        OddsApiClient, "resolve_event_id",
        lambda self, sport, event_label, commence_time="": "evt-fixture",
    )

    at = _login_as_king(_run_app(FAKE_SECRETS))
    [b for b in at.button if "Run the algorithm" in b.label][0].click().run()
    assert not at.exception, f"app raised: {at.exception}"

    note = at.session_state["note"]
    assert "2 glitched leg(s) thrown out" in note

    picked = {p.player for p in at.session_state["scored"]}
    assert glitch not in picked and "AJ Dybantsa" not in picked
    assert picked  # the real board survived
    # The pack did the scoring: at least one kept leg carries
    # real form data even though the live feed answered
    # nothing (the fake store returns None for both leagues).
    assert any(p.has_form_data for p in at.session_state["scored"])

    logged = json.loads((tmp_path / "data" / "pick_log.json").read_text())
    logged_players = {row["player"] for row in logged}
    assert glitch not in logged_players and "AJ Dybantsa" not in logged_players
    assert logged_players
