"""Tests for the props browser and the Drop-it-down stepper
(2026-11-14).

Neko's words: "Her to show me her pick's actual player props
and then allow me to drop them down as needed."

Covered here:
- the browser (Props page) lists the ACTUAL pulled props —
  per game, per player, per stat — at the real standard line
  and real price, with the book that posted each one named
  (a prop only bet365 carries says bet365);
- the Drop-it-down stepper walks the line down one step at
  a time, and each step shows the book's REAL alternate
  price, the book offering it, and Lilly's form grade at
  that line with the hit count in plain words;
- a dropped leg added from the stepper lands on his slip at
  the DROPPED line (the standard line remembered as its
  base), the slip page grades the whole slip, saving puts
  the dropped line in the pick log tagged as his own
  (source "manual"), and the plain-English download carries
  the dropped line spelled out.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kings_picks import picks_export, slip
from kings_picks.game_logs import GameLogs
from kings_picks.model import Prop
from kings_picks.odds_api import AltPrice, AltResult, ApiResult, OddsApiClient

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}

STAR_POINTS = [28.0, 26.0, 30.0, 24.0, 27.0, 29.0, 25.0, 31.0, 26.0, 28.0]
# Over 24.5 hit in 9 of these 10; Over 22.5 hit in all 10.


def _browser_props():
    commence = (
        datetime.now(timezone.utc) + timedelta(hours=3)
    ).replace(microsecond=0).isoformat()
    props = []

    def add(player, prop, side, line, odds, book, event="Alpha vs Beta"):
        props.append(Prop(
            player=player, sport="NBA", prop=prop, side=side,
            line=line, odds=odds, bookmaker=book, event=event,
            event_id="evt-alpha", commence_time=commence, source="live",
        ))

    # The star's Points live at BetMGM; his Assists exist
    # only at bet365 — the browser must say which book
    # posted each real line.
    add("Browser Star", "Points", "Over", 24.5, -110, "BetMGM")
    add("Browser Star", "Points", "Under", 24.5, -110, "BetMGM")
    add("Browser Star", "Assists", "Over", 7.5, -115, "bet365")
    add("Browser Star", "Assists", "Under", 7.5, -105, "bet365")
    for i in range(11):
        for side in ("Over", "Under"):
            add(
                f"Filler{i}", "Points", side, 10.5 + i, -110,
                "FixtureBook", event=f"Filler Game {i}",
            )
    return props


def _browser_alts():
    return [
        AltPrice(
            player="Browser Star", sport="NBA", prop="Points",
            market_key="player_points", side="Over", line=22.5,
            odds=-155, bookmaker="bet365",
        ),
        AltPrice(
            player="Browser Star", sport="NBA", prop="Points",
            market_key="player_points", side="Over", line=20.5,
            odds=-210, bookmaker="BetMGM",
        ),
        AltPrice(
            player="Browser Star", sport="NBA", prop="Points",
            market_key="player_points", side="Over", line=18.5,
            odds=-260, bookmaker="BetMGM",
        ),
    ]


class _BrowserStore:
    """StatsStore stand-in with the star's real game logs,
    so form grades are real grades, not NO DATA."""

    def nfl_logs(self):
        return None

    def nba_logs(self):
        return GameLogs(
            sport="NBA",
            by_player={
                "browser star": {
                    "Points": list(STAR_POINTS),
                    "Assists": [8.0] * 10,
                }
            },
            source_label="fixture",
        )


def _install_fake_client(monkeypatch):
    def fetch_props(self, sport, force_refresh=False, max_events=12):
        return ApiResult(
            True, "fixture props",
            props=[p for p in _browser_props() if p.sport == sport],
            requests_remaining="499 of 500 requests left this month (1 used)",
        )

    def fetch_alternates(self, sport, event_id, market_key, force_refresh=False):
        return AltResult(
            True, "fixture alternates", prices=list(_browser_alts()),
            requests_remaining="499 of 500 requests left this month (1 used)",
        )

    monkeypatch.setattr(OddsApiClient, "has_key", property(lambda self: True))
    monkeypatch.setattr(OddsApiClient, "fetch_props", fetch_props)
    monkeypatch.setattr(OddsApiClient, "fetch_alternates", fetch_alternates)
    monkeypatch.setattr(
        OddsApiClient, "resolve_event_id",
        lambda self, sport, event_label, commence_time="": "evt-alpha",
    )


def _run_app(secrets=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    at.session_state["stats_store"] = _BrowserStore()
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


def _pull_and_open_props(at):
    run = [b for b in at.button if "Run the algorithm" in b.label]
    assert run, "run button missing"
    run[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    radios = list(at.radio)
    assert radios, "app page radio missing after login"
    radios[0].set_value("Games & Props").run()
    assert not at.exception, f"app raised: {at.exception}"


def test_props_browser_lists_real_lines_with_their_books(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    _install_fake_client(monkeypatch)
    at = _login_as_king(_run_app(FAKE_SECRETS))
    _pull_and_open_props(at)

    text = _all_text(at)
    assert "His actual props" in text
    assert "Browser Star" in text
    # His real Points line, at its real price, at the book
    # that posted it.
    assert "Points — Over 24.5" in text
    assert "-110 at **BetMGM**" in text
    # The Assists exist only at bet365 — and the row says so.
    assert "Assists — Over 7.5" in text
    assert "-115 at **bet365**" in text
    # The drop control names his real line, in his words.
    toggles = [
        b for b in at.button
        if "Drop it down" in b.label and "24.5" in b.label
    ]
    assert toggles, "no Drop it down control on the star's Points"


def test_drop_stepper_shows_real_price_and_grade_then_adds_the_dropped_leg(
    tmp_path, monkeypatch
):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    _install_fake_client(monkeypatch)
    at = _login_as_king(_run_app(FAKE_SECRETS))
    _pull_and_open_props(at)

    # Open the dropper on the star's Points.
    toggle = [
        b for b in at.button
        if "Drop it down" in b.label and "24.5" in b.label
    ]
    assert toggle, "dropper toggle missing"
    toggle[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    text = _all_text(at)
    assert "His real line is 24.5" in text
    # The standard line itself is graded too: 9 of his last 10.
    assert "hit this line in 9 of his last 10 games" in text

    # One tap down: the book's real alternate price, the book
    # offering it, and the grade AT the dropped line.
    down = [b for b in at.button if b.label == "📉 Drop it down"]
    assert down, "step-down button missing"
    down[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    text = _all_text(at)
    assert "Over 22.5 (dropped from 24.5)" in text
    assert "-155" in text and "bet365" in text
    assert "STRONG" in text
    assert "hit this line in 10 of his last 10 games" in text

    # Add it: the slip on disk carries the DROPPED line, with
    # the standard line remembered as where it came from.
    add = [b for b in at.button if b.label == "➕ Add this dropped line to my slip"]
    assert add, "add-dropped-line button missing"
    add[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    slips = json.loads((tmp_path / "data" / "slips.json").read_text())
    star_legs = [
        leg for leg in slips["King"] if leg["player"] == "Browser Star"
    ]
    assert len(star_legs) == 1
    assert star_legs[0]["line"] == 22.5
    assert star_legs[0]["base_line"] == 24.5

    # The download button is offered while the slip holds the
    # leg — and the file it serves carries the dropped line.
    radios = list(at.radio)
    radios[0].set_value("Dashboard").run()
    assert not at.exception, f"app raised: {at.exception}"
    assert any(
        "Download my picks" in b.label for b in at.download_button
    ), "download button missing with a slip on board"
    leg = slip.SlipLeg(
        player="Browser Star", sport="NBA", prop="Points", side="Over",
        line=22.5, odds=-155, probability=0.8, bookmaker="bet365",
        event="Alpha vs Beta", base_line=24.5,
    )
    text_file = picks_export.build_picks_text(
        parlays=at.session_state["parlays"],
        king_parlays=at.session_state["king_parlays"],
        slip_legs=[leg],
        slip_profile="👑 King",
    )
    assert "Over 22.5 points" in text_file
    assert "dropped down from 24.5 to 22.5" in text_file

    # On My Slip the whole slip is graded as one, and saving
    # writes the DROPPED line into his record as his own pick.
    radios = list(at.radio)
    radios[0].set_value("My Slip").run()
    assert not at.exception, f"app raised: {at.exception}"
    text = _all_text(at)
    assert "Test my picks" in text
    assert "dropped from 24.5" in text
    assert "True overall chance for the whole slip" in text
    at.button(key="slip-save").click().run()
    assert not at.exception, f"app raised: {at.exception}"
    logged = json.loads((tmp_path / "data" / "pick_log.json").read_text())
    star_rows = [row for row in logged if row["player"] == "Browser Star"]
    assert star_rows, "the saved slip never reached the pick log"
    assert any(
        row["line"] == 22.5 and row.get("source") == "manual"
        for row in star_rows
    ), f"no manual log row at the dropped line: {star_rows}"
