"""Tests for the ⭐ SPECIAL PICKS anytime-TD set (2026-11-14).

Neko's order: on a day with 6 or more NFL games, a special
button lights up; tapping it builds, for every game, one
2-pick parlay of anytime TDs — the best TD scorer from
EACH team.

And his refinement, the ranking rule: "specifically
picking players known for 1 TD in a game" — rank by
CONSISTENCY (the share of games where he scored at least
one TD), not by raw TD totals that a couple of explosion
games inflate. Total TDs are only the tie-break. Injury
exclusion comes first: a tagged player is never used, and
the swap is noted in plain English.

Covered here:
- consistency maths and the form line in plain words;
- the ranking: steady beats boom-or-bust even when the
  boom player has MORE total TDs; tie on rate -> totals;
- flagged players excluded, swaps noted, an all-flagged
  team sits the game out with the reason;
- the set: exactly one 2-pick per game, one leg per team,
  combined chance = the two legs multiplied;
- the button's count: distinct NFL games only, lights at 6;
- the download carries the set;
- the app flow: 6 NFL games -> button enabled, tap builds
  the section, save tags the legs "special" in the log;
  5 NFL games -> button disabled.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kings_picks import picks_export, special_picks
from kings_picks.game_logs import GameLogs
from kings_picks.model import Prop
from kings_picks.odds_api import AltResult, ApiResult, OddsApiClient

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}

KC_GAME = "Atlanta Falcons vs Kansas City Chiefs"

# The steady man: a TD in 8 of 10 games, 8 TDs total.
STEADY = [1.0, 1.0, 0.0, 1.0, 1.0, 1.0, 0.0, 1.0, 1.0, 1.0]
# The boom-or-bust man: 10 TDs total — MORE than Steady —
# but they came in just 3 of 10 games (and only 1 of his
# last 5).
BOOM = [4.0, 0.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 3.0, 0.0]
# A regular scorer for the other team.
REGULAR = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0]


def _td_prop(player, event=KC_GAME, odds=150):
    return Prop(
        player=player, sport="NFL", prop="Anytime Touchdown",
        side="Over", line=0.5, odds=odds, bookmaker="bet365",
        event=event, event_id="evt-1", source="live",
    )


def _logs(by_player, teams):
    return GameLogs(
        sport="NFL", by_player=by_player, teams=teams,
        source_label="fixture",
    )


def _kc_logs():
    return _logs(
        {
            "steady man": {"Anytime Touchdown": list(STEADY)},
            "boom man": {"Anytime Touchdown": list(BOOM)},
            "regular scorer": {"Anytime Touchdown": list(REGULAR)},
        },
        {
            "steady man": "KC",
            "boom man": "KC",
            "regular scorer": "ATL",
        },
    )


def _kc_props():
    return [
        _td_prop("Steady Man", odds=120),
        _td_prop("Boom Man", odds=110),
        _td_prop("Regular Scorer", odds=180),
    ]


# ---------- consistency maths + the plain-words form line ----------


def test_consistency_counts_games_with_a_td_not_raw_totals():
    steady = special_picks.td_consistency(STEADY)
    boom = special_picks.td_consistency(BOOM)
    assert steady["games_with_td"] == 8
    assert steady["season_rate"] == pytest.approx(0.8)
    assert steady["total_tds"] == 8
    # Boom has MORE touchdowns but in far fewer games — the
    # explosion games count once each.
    assert boom["total_tds"] == 10
    assert boom["games_with_td"] == 3
    assert boom["season_rate"] == pytest.approx(0.3)
    assert boom["last5_with_td"] == 1  # newest five: 4,0,0,0,0


def test_td_form_line_says_the_consistency_story():
    line = special_picks.td_form_line(STEADY)
    assert "most games" in line
    assert "got one in 4 of his last 5 games" in line
    cold = special_picks.td_form_line([0.0] * 6)
    assert "Hasn't scored" in cold
    assert "No game log" in special_picks.td_form_line(None)
    assert "No game log" in special_picks.td_form_line([])


# ---------- the ranking rule ----------


def test_steady_beats_boom_despite_fewer_total_tds():
    built = special_picks.build_special_set(_kc_props(), [], _kc_logs())
    assert len(built.parlays) == 1
    parlay = built.parlays[0]
    players = [leg.player for leg in parlay.picks]
    # One leg per team: KC's spot goes to the steady man,
    # NOT the boom-or-bust man with more total TDs.
    assert "Steady Man" in players
    assert "Boom Man" not in players
    assert "Regular Scorer" in players
    assert parlay.style == "special"
    assert parlay.size == 2
    # The form line the section shows for the steady leg.
    steady_leg = next(
        leg for leg in parlay.picks if leg.player == "Steady Man"
    )
    assert "got one in 4 of his last 5 games" in built.form_lines[steady_leg.key]
    # The combined chance is the two legs multiplied.
    assert parlay.overall_probability == pytest.approx(
        parlay.picks[0].probability * parlay.picks[1].probability
    )


def test_total_tds_only_break_a_consistency_tie():
    logs = _logs(
        {
            "even a": {"Anytime Touchdown": [1.0, 0.0, 1.0, 0.0, 1.0]},  # rate .6, 3 TDs
            "even b": {"Anytime Touchdown": [2.0, 0.0, 1.0, 0.0, 2.0]},  # rate .6, 5 TDs
            "regular scorer": {"Anytime Touchdown": list(REGULAR)},
        },
        {"even a": "KC", "even b": "KC", "regular scorer": "ATL"},
    )
    props = [
        _td_prop("Even A"), _td_prop("Even B"), _td_prop("Regular Scorer"),
    ]
    built = special_picks.build_special_set(props, [], logs)
    players = [leg.player for leg in built.parlays[0].picks]
    assert "Even B" in players  # same rate, more TDs
    assert "Even A" not in players


# ---------- injury exclusion first ----------


def test_flagged_favourite_is_excluded_and_the_swap_is_noted():
    built = special_picks.build_special_set(
        _kc_props(), [], _kc_logs(),
        injuries={"steady man": "Questionable (groin)"},
    )
    players = [leg.player for leg in built.parlays[0].picks]
    assert "Steady Man" not in players
    assert "Boom Man" in players  # the best CLEAN scorer left
    notes = "\n".join(built.notes)
    assert "Steady Man" in notes
    assert "Questionable" in notes


def test_all_flagged_team_sits_the_game_out():
    built = special_picks.build_special_set(
        _kc_props(), [], _kc_logs(),
        injuries={"steady man": "Out", "boom man": "Doubtful"},
    )
    assert built.parlays == []
    notes = "\n".join(built.notes)
    assert "every TD option" in notes
    assert "Kansas City Chiefs" in notes


def test_no_logs_means_no_set_and_a_plain_reason():
    built = special_picks.build_special_set(_kc_props(), [], None)
    assert built.parlays == []
    assert "game logs didn't load" in "\n".join(built.notes)


# ---------- the button's game count ----------


def test_nfl_game_count_counts_distinct_nfl_games_only():
    props = _kc_props() + [
        Prop(
            player="Hooper", sport="NBA", prop="Points", side="Over",
            line=10.5, odds=-110, event="A vs B", source="live",
        ),
        _td_prop("Other One", event="Buffalo Bills vs New York Jets"),
    ]
    assert special_picks.nfl_game_count(props) == 2
    assert special_picks.nfl_game_count([]) == 0
    assert special_picks.MIN_NFL_GAMES == 6


# ---------- the download carries the set ----------


def test_download_includes_the_special_set():
    built = special_picks.build_special_set(_kc_props(), [], _kc_logs())
    text = picks_export.build_picks_text(
        pulled_at="2026-11-14T12:00:00+00:00",
        special_parlays=built.parlays,
    )
    assert "SPECIAL PICKS" in text
    assert "Steady Man" in text
    assert "scores a touchdown at any point in the game" in text
    # The leg's form, graded on the same last-5 consistency:
    # 4 of his last 5.
    assert "hit this line in 4 of his last 5 games" in text


# ---------- the app flow ----------

NFL_GAMES = [
    ("Atlanta Falcons", "Kansas City Chiefs"),
    ("New York Jets", "Buffalo Bills"),
    ("San Francisco 49ers", "Dallas Cowboys"),
    ("Green Bay Packers", "Detroit Lions"),
    ("Indianapolis Colts", "Miami Dolphins"),
    ("Seattle Seahawks", "Las Vegas Raiders"),
]


def _app_fixture(n_games):
    """(props, nfl_logs) for n NFL games: each team has a
    steady scorer (a TD in 8 of 10) and a boom-or-bust man
    (more TDs, far fewer games). KC's steady man is named
    King Steady so the pick is easy to recognise."""
    commence = (
        datetime.now(timezone.utc) + timedelta(hours=3)
    ).replace(microsecond=0).isoformat()
    props = []
    by_player, teams = {}, {}

    def add_td(player, team_abbr, event, values, odds):
        props.append(Prop(
            player=player, sport="NFL", prop="Anytime Touchdown",
            side="Over", line=0.5, odds=odds, bookmaker="bet365",
            event=event, event_id=f"evt-{team_abbr}",
            commence_time=commence, source="live",
        ))
        by_player[player.lower()] = {"Anytime Touchdown": list(values)}
        teams[player.lower()] = team_abbr

    abbrs = {
        "Atlanta Falcons": "ATL", "Kansas City Chiefs": "KC",
        "New York Jets": "NYJ", "Buffalo Bills": "BUF",
        "San Francisco 49ers": "SF", "Dallas Cowboys": "DAL",
        "Green Bay Packers": "GB", "Detroit Lions": "DET",
        "Indianapolis Colts": "IND", "Miami Dolphins": "MIA",
        "Seattle Seahawks": "SEA", "Las Vegas Raiders": "LV",
    }
    for away, home in NFL_GAMES[:n_games]:
        event = f"{away} vs {home}"
        for team in (away, home):
            abbr = abbrs[team]
            steady_name = (
                "King Steady" if team == "Kansas City Chiefs"
                else f"Steady {abbr}"
            )
            add_td(steady_name, abbr, event, STEADY, 120)
            add_td(f"Boom {abbr}", abbr, event, BOOM, 110)
    # A rich NBA board too, so the pull's standard plan has
    # plenty to build with, like the other app fixtures.
    for i in range(14):
        for side in ("Over", "Under"):
            props.append(Prop(
                player=f"Fixture{i}", sport="NBA", prop="Points",
                side=side, line=10.5 + i, odds=-110,
                bookmaker="FixtureBook", event=f"Team{i} vs Team{i + 1}",
                event_id="evt-fixture", commence_time=commence,
                source="live",
            ))
    logs = GameLogs(
        sport="NFL", by_player=by_player, teams=teams,
        source_label="fixture",
    )
    return props, logs


class _AppStore:
    def __init__(self, nfl_logs):
        self._nfl = nfl_logs

    def nfl_logs(self):
        return self._nfl

    def nba_logs(self):
        return None


def _install_app_client(monkeypatch, props):
    def fetch_props(self, sport, force_refresh=False, max_events=12):
        return ApiResult(
            True, "fixture props",
            props=[p for p in props if p.sport == sport],
            requests_remaining="499 of 500 requests left this month (1 used)",
        )

    def fetch_alternates(self, sport, event_id, market_key, force_refresh=False):
        return AltResult(True, "fixture alternates", prices=[])

    monkeypatch.setattr(OddsApiClient, "has_key", property(lambda self: True))
    monkeypatch.setattr(OddsApiClient, "fetch_props", fetch_props)
    monkeypatch.setattr(OddsApiClient, "fetch_alternates", fetch_alternates)
    monkeypatch.setattr(
        OddsApiClient, "resolve_event_id",
        lambda self, sport, event_label, commence_time="": "evt-1",
    )


def _run_app(secrets, store):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    at.session_state["stats_store"] = store
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
    for accessor in ("title", "subheader", "markdown", "caption", "info", "success"):
        try:
            parts += [el.value for el in getattr(at, accessor)]
        except Exception:
            pass
    return "\n".join(parts)


def _pull(at):
    run = [b for b in at.button if "Run the algorithm" in b.label]
    assert run, "run button missing"
    run[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"


def test_app_special_button_lights_at_six_games_and_builds_the_set(
    tmp_path, monkeypatch
):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    props, logs = _app_fixture(6)
    _install_app_client(monkeypatch, props)
    at = _login_as_king(_run_app(FAKE_SECRETS, _AppStore(logs)))
    _pull(at)

    button = at.button(key="special_picks_btn")
    assert not button.disabled, "button should be lit at 6 NFL games"
    assert "Lights up on days with 6 or more NFL games" in _all_text(at)

    button.click().run()
    assert not at.exception, f"app raised: {at.exception}"
    text = _all_text(at)
    assert "Special picks" in text
    special = at.session_state["special_parlays"]
    assert len(special) == 6  # one 2-pick per game
    assert all(len(p.picks) == 2 for p in special)
    kc = next(p for p in special if "Kansas City Chiefs" in p.label)
    kc_players = [leg.player for leg in kc.picks]
    assert "King Steady" in kc_players  # consistency won
    assert "Boom KC" not in kc_players
    assert "got one in 4 of his last 5 games" in text


def test_record_run_tags_special_legs(tmp_path):
    """The pick-log tag itself: legs saved from a special
    parlay land with style "special" — the TD-set tag his
    record is split by."""
    from kings_picks.pick_log import PickLog

    built = special_picks.build_special_set(_kc_props(), [], _kc_logs())
    log = PickLog(tmp_path / "pick_log.json")
    added = log.record_run(built.parlays, mode="live")
    assert len(added) == 2
    assert all(entry.style == "special" for entry in added)
    assert {entry.player for entry in added} == {
        "Steady Man", "Regular Scorer",
    }


def test_app_special_save_writes_special_tagged_rows(tmp_path, monkeypatch):
    """End to end, without a pull first (a pull's own
    tickets may already hold some of these legs — the log
    never double-logs a pick): the slate is placed in
    session the way a stored slate lands there, the button
    builds the set, and saving writes all 12 legs with the
    special tag."""
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    props, logs = _app_fixture(6)
    _install_app_client(monkeypatch, props)
    at = _login_as_king(_run_app(FAKE_SECRETS, _AppStore(logs)))
    from datetime import date as _date

    at.session_state["raw_props"] = props
    at.session_state["scored"] = []
    at.session_state["parlays"] = []
    at.session_state["mode"] = "live"
    at.session_state["slate_date"] = _date.today().isoformat()
    at.run()
    assert not at.exception, f"app raised: {at.exception}"

    button = at.button(key="special_picks_btn")
    assert not button.disabled
    button.click().run()
    assert not at.exception, f"app raised: {at.exception}"
    at.button(key="special_save").click().run()
    assert not at.exception, f"app raised: {at.exception}"
    assert "tagged" in _all_text(at)

    logged = json.loads((tmp_path / "data" / "pick_log.json").read_text())
    special_rows = [r for r in logged if r.get("style") == "special"]
    assert len(special_rows) == 12  # 6 games x 2 legs
    assert all(
        r["market"] == "Anytime Touchdown Over" for r in special_rows
    )
    assert any(r["player"] == "King Steady" for r in special_rows)


def test_app_special_button_stays_greyed_out_below_six_games(
    tmp_path, monkeypatch
):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    props, logs = _app_fixture(5)
    _install_app_client(monkeypatch, props)
    at = _login_as_king(_run_app(FAKE_SECRETS, _AppStore(logs)))
    _pull(at)

    button = at.button(key="special_picks_btn")
    assert button.disabled, "button must stay disabled at 5 NFL games"
    assert "5 NFL games" in _all_text(at)
    assert at.session_state["special_parlays"] == []
