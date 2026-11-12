"""Tests for the stored slate, the demo purge, pick
timestamps, and the bet-ticket restyle (all 2026-11-12).

What is being pinned down:

- A pull PERSISTS: the slate store round-trips the board,
  Lilly's parlays and the 👑 King parlays (dropped-leg
  annotations included), and a fresh app load renders the
  stored slate with ZERO Odds API calls, byte-identical
  tickets, ticket numbers included.
- Only a fresh pull overwrites the stored slate.
- No key -> the plain setup note, no picks, ever. Demo mode
  is gone: demo payloads/entries are purged, never shown.
- Every pick carries a created_at stamp, shown in
  America/Detroit; legacy entries fall back to their date.
- Parlay cards and the My Slip summary are bet tickets:
  shop header, ticket number, pull timestamp, one row per
  leg, and a footer with every total the old cards showed.

Run: pytest -q

The PINs below ("1111" / "2222") are FAKE test values. The
real PINs exist only in Neko's Streamlit secrets.
"""

import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import slate_store
from kings_picks.model import Prop, score_prop
from kings_picks.odds_api import AltPrice, AltResult, ApiResult, OddsApiClient
from kings_picks.parlays import Parlay, build_parlays
from kings_picks.pick_log import PickEntry, PickLog, entry_stamp

FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
PULLED = "2026-11-12T21:37:00+00:00"  # = 04:37 PM in Detroit (EST)


# ---------- fixtures ----------


def _scored(player="Fixture0", line=10.5, odds=-110, **kw):
    prop = Prop(
        player=player, sport="NBA", prop="Points", side="Over",
        line=line, odds=odds, bookmaker="FixtureBook",
        event="Team A vs Team B", source="live", **kw,
    )
    return score_prop(prop, pair_odds=-110)


def _payload(**overrides):
    scored = [_scored(f"Fixture{i}", line=10.5 + i) for i in range(12)]
    parlays, _ = build_parlays(scored, plan=[(3, 1)])
    king_leg = _scored("Fixture0", line=6.5, odds=-170)
    king_leg.base_line = 10.5
    king_leg.king_note = ""
    king = Parlay(
        size=1, picks=[king_leg], overall_probability=king_leg.probability,
        decimal_payout=1.59, expected_value_per_dollar=-0.1,
        label="3-pick parlay #1", style="king",
    )
    payload = slate_store.build_payload(
        pulled_at=PULLED, note="fixture note", raw_props=scored,
        scored=scored, parlays=parlays, king_parlays=[king],
        king_info={"legs_dropped": 1},
    )
    payload.update(overrides)
    return payload


# ---------- store: round-trip & tolerance ----------


def test_store_round_trip_preserves_the_whole_slate(tmp_path):
    store = slate_store.SlateStore(tmp_path / "current_slate.json")
    payload = _payload()
    store.save(payload)
    assert store.exists()
    restored = slate_store.restore_slate(store.load())
    assert restored is not None
    assert restored["pulled_at"] == PULLED
    assert restored["note"] == "fixture note"
    assert restored["king_info"] == {"legs_dropped": 1}
    assert len(restored["scored"]) == 12
    # Parlays come back identical, serialised form included.
    assert [slate_store.parlay_to_dict(p) for p in restored["parlays"]] == payload["parlays"]
    assert [slate_store.parlay_to_dict(p) for p in restored["king_parlays"]] == payload["king_parlays"]
    # The King leg's dropped-line annotation survives: it is
    # what prints "(dropped from 10.5)" on the ticket.
    leg = restored["king_parlays"][0].picks[0]
    assert leg.base_line == 10.5
    assert leg.line == 6.5


def test_store_missing_and_corrupt_are_no_slate(tmp_path):
    store = slate_store.SlateStore(tmp_path / "current_slate.json")
    assert store.load() is None and not store.exists()
    store.path.write_text("this is not json")
    assert store.load() is None
    store.path.write_text('["a", "list"]')
    assert store.load() is None
    assert slate_store.restore_slate(None) is None
    assert slate_store.restore_slate("nope") is None


def test_store_purges_a_demo_payload(tmp_path):
    # A leftover demo slate (from the short-lived demo-aware
    # build) must never render: load purges it outright.
    store = slate_store.SlateStore(tmp_path / "current_slate.json")
    payload = _payload(mode="demo")
    store.save(payload)
    assert store.load() is None
    assert not store.path.exists()
    assert slate_store.restore_slate(payload) is None


def test_format_pulled_at_is_detroit_time():
    assert slate_store.format_pulled_at(PULLED) == "Thu Nov 12, 2026 · 04:37 PM ET"
    assert slate_store.format_pulled_at("") == ""
    assert slate_store.format_pulled_at("garbage") == ""


# ---------- pick log: stamps & the demo purge ----------


def test_record_run_stamps_every_entry_with_the_pull_time(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    scored = [_scored(f"Fixture{i}", line=10.5 + i) for i in range(12)]
    parlays, _ = build_parlays(scored, plan=[(3, 1)])
    log.record_run(parlays, "live", created="2026-11-12", created_at=PULLED)
    assert log.entries
    assert all(e.created_at == PULLED for e in log.entries)
    # 21:37 UTC is 4:37 PM in Detroit in November (EST).
    assert entry_stamp(log.entries[0]) == "Nov 12, 2026 · 04:37 PM ET"


def test_entry_stamp_falls_back_to_date_for_legacy_entries():
    legacy = PickEntry(
        id="k", key="k", date="2026-11-10", sport="NBA", event="A vs B",
        player="Old Timer", market="Points Over", line=10.5, odds=-110,
        probability=0.5, mode="live",
    )
    assert legacy.created_at == ""
    assert entry_stamp(legacy) == "Nov 10, 2026"


def test_manual_save_is_live_and_timestamped(tmp_path):
    # The My Slip save path: hand-built picks are recorded
    # live (demo is gone) and carry the save-time stamp.
    from kings_picks.slip import SlipLeg

    log = PickLog(tmp_path / "pick_log.json")
    leg = SlipLeg(
        player="Hand Pick", sport="NBA", prop="Points", side="Over",
        line=9.5, odds=-120, probability=0.55, event="A vs B",
    )
    added, _ = log.record_manual(
        [leg], mode="live", profile="King", created="2026-11-12"
    )
    assert len(added) == 1
    assert added[0].mode == "live" and added[0].source == "manual"
    assert added[0].created_at  # stamped even when not passed in
    assert "·" in entry_stamp(added[0])  # date AND time, Detroit


def test_pick_log_purges_demo_entries_on_load(tmp_path):
    path = tmp_path / "pick_log.json"
    live = PickEntry(
        id="live1", key="live1", date="2026-11-12", sport="NBA",
        event="A vs B", player="Real Player", market="Points Over",
        line=10.5, odds=-110, probability=0.5, mode="live",
        created_at=PULLED,
    )
    demo = PickEntry(
        id="demo1", key="demo1", date="2026-11-12", sport="NBA",
        event="Sample vs Demo", player="Sample Star",
        market="Points Over", line=24.5, odds=-110, probability=0.5,
        mode="demo",
    )
    path.write_text(json.dumps([asdict(live), asdict(demo)], indent=2))
    log = PickLog(path)
    assert [e.player for e in log.entries] == ["Real Player"]
    # The purge is permanent: the file on disk is cleaned too.
    on_disk = json.loads(path.read_text())
    assert [row["mode"] for row in on_disk] == ["live"]


# ---------- app: fake live client ----------


def _fixture_props(prefix="Fixture", n=14):
    # Tonight's games start ~3 hours from whenever the test
    # runs, so the game-day header has a real first lock to
    # count down to.
    commence = (
        datetime.now(timezone.utc) + timedelta(hours=3)
    ).replace(microsecond=0).isoformat()
    props = []
    for i in range(n):
        for side in ("Over", "Under"):
            props.append(Prop(
                player=f"{prefix}{i}", sport="NBA", prop="Points",
                side=side, line=10.5 + i, odds=-110 - (i % 5) * 10,
                bookmaker="FixtureBook", event=f"Team{i} vs Team{i + 1}",
                event_id="evt-fixture", commence_time=commence,
                source="live",
            ))
    return props


def _fixture_alts(prefix="Fixture", n=14):
    # Every fixture player's middle drop (Points steps are
    # 2/4/6, so the middle is -4), priced: every King leg drops.
    return [
        AltPrice(
            player=f"{prefix}{i}", sport="NBA", prop="Points",
            market_key="player_points", side="Over", line=(10.5 + i) - 4,
            odds=-170, bookmaker="FixtureBook",
        )
        for i in range(n)
    ]


def _install_fake_client(monkeypatch, holder, calls):
    """A counting fake OddsApiClient. `holder["props"]` is
    read at fetch time, so a test can swap the slate between
    pulls."""

    def fetch_props(self, sport, force_refresh=False, max_events=12):
        calls.append(("props", sport))
        return ApiResult(
            True, "fixture props",
            props=[p for p in holder["props"] if p.sport == sport],
            requests_remaining="499 of 500 requests left this month (1 used)",
        )

    def fetch_alternates(self, sport, event_id, market_key, force_refresh=False):
        calls.append(("alts", sport, market_key))
        return AltResult(
            True, "fixture alternates", prices=list(holder.get("alts") or []),
            requests_remaining="499 of 500 requests left this month (1 used)",
        )

    monkeypatch.setattr(OddsApiClient, "has_key", property(lambda self: True))
    monkeypatch.setattr(OddsApiClient, "fetch_props", fetch_props)
    monkeypatch.setattr(OddsApiClient, "fetch_alternates", fetch_alternates)
    monkeypatch.setattr(
        OddsApiClient, "resolve_event_id",
        lambda self, sport, event_label, commence_time="": "evt-fixture",
    )


class _FakeStore:
    """StatsStore stand-in: no feeds, so scoring is odds-only
    and nothing tries the network."""

    def nfl_logs(self):
        return None

    def nba_logs(self):
        return None


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
    assert at.session_state["active_profile"] == "King"
    return at


def _ticket_texts(at):
    # The page CSS also mentions .bet-ticket — match the
    # rendered ticket div itself, not the style block.
    return sorted(
        m.value for m in at.markdown if "bet-ticket'>" in m.value
    )


def _all_text(at):
    parts = []
    for accessor in ("title", "subheader", "markdown", "caption", "info", "success"):
        try:
            parts += [el.value for el in getattr(at, accessor)]
        except Exception:
            pass
    for expander in at.expander:
        parts.append(expander.label)
        for accessor in ("markdown", "caption", "info", "write"):
            try:
                parts += [el.value for el in getattr(expander, accessor)]
            except Exception:
                pass
    return "\n".join(parts)


def test_app_pull_stores_slate_and_reload_renders_it_with_zero_api_calls(
    tmp_path, monkeypatch
):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    holder = {"props": _fixture_props(), "alts": _fixture_alts()}
    calls: list = []
    _install_fake_client(monkeypatch, holder, calls)

    at = _login_as_king(_run_app(FAKE_SECRETS))
    run = [b for b in at.button if "Run the algorithm" in b.label]
    run[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    assert calls, "the pull must call the client"
    assert (tmp_path / "data" / "current_slate.json").exists()
    tickets_first = _ticket_texts(at)
    assert tickets_first, "no tickets rendered after the pull"
    parlays_first = [
        slate_store.parlay_to_dict(p) for p in at.session_state["parlays"]
    ]
    king_first = [
        slate_store.parlay_to_dict(p) for p in at.session_state["king_parlays"]
    ]

    # The stored slate and the pick log agree: every leg on
    # the tickets is in the log, stamped with the pull time.
    stored = slate_store.SlateStore(tmp_path / "data" / "current_slate.json").load()
    logged = json.loads((tmp_path / "data" / "pick_log.json").read_text())
    logged_keys = {
        (row["player"], row["market"], row["line"]) for row in logged
    }
    for parlay in stored["parlays"] + stored["king_parlays"]:
        for leg in parlay["picks"]:
            market = f"{leg['prop']} {leg['side']}"
            assert (leg["player"], market, leg["line"]) in logged_keys
    assert logged and all(
        row["created_at"] == stored["pulled_at"] for row in logged
    )

    # --- simulated reload: a brand-new app session ---
    calls.clear()
    at2 = _login_as_king(_run_app(FAKE_SECRETS))
    assert calls == [], f"stored render made API calls: {calls}"
    # The picks display is byte-identical: same tickets
    # (same ticket numbers, same legs, same totals).
    assert _ticket_texts(at2) == tickets_first
    assert [
        slate_store.parlay_to_dict(p) for p in at2.session_state["parlays"]
    ] == parlays_first
    assert [
        slate_store.parlay_to_dict(p) for p in at2.session_state["king_parlays"]
    ] == king_first
    assert "Stored slate" in _all_text(at2)


def test_app_pull_fresh_odds_overwrites_the_stored_slate(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    holder = {"props": _fixture_props("Alpha"), "alts": _fixture_alts("Alpha")}
    calls: list = []
    _install_fake_client(monkeypatch, holder, calls)
    at = _login_as_king(_run_app(FAKE_SECRETS))
    [b for b in at.button if "Run the algorithm" in b.label][0].click().run()
    assert "Alpha0" in _all_text(at)

    holder["props"] = _fixture_props("Bravo")
    holder["alts"] = _fixture_alts("Bravo")
    fresh = [b for b in at.button if "Pull fresh odds" in b.label]
    assert fresh, "no Pull fresh odds control"
    fresh[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    # The tickets are the new slate. (The pick log keeps both
    # pulls — that history is the record, and the self-test
    # section legitimately still names the first pull's legs.)
    tickets = "\n".join(_ticket_texts(at))
    assert "Bravo" in tickets and "Alpha" not in tickets
    stored = slate_store.SlateStore(tmp_path / "data" / "current_slate.json").load()
    assert stored["scored"] and all(
        row["player"].startswith("Bravo") for row in stored["scored"]
    )


def test_app_tickets_carry_every_field_standard_and_king(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    holder = {"props": _fixture_props(), "alts": _fixture_alts()}
    _install_fake_client(monkeypatch, holder, [])
    at = _login_as_king(_run_app(FAKE_SECRETS))
    [b for b in at.button if "Run the algorithm" in b.label][0].click().run()
    tickets = "\n".join(_ticket_texts(at))
    assert tickets, "no tickets rendered"
    # Header: shop, ticket number, style + model tags.
    assert "KINGS SPORTS BOOKS PICKS" in tickets
    assert "Ticket #KSB-" in tickets
    assert "STANDARD LINES" in tickets
    assert "KING'S STYLE" in tickets
    assert "Model v2" in tickets
    # King legs show where they were dropped from.
    assert "(dropped from" in tickets
    # Leg rows: odds, chance, bookmaker, event, why, verdict.
    assert "Odds:" in tickets and "Chance:" in tickets
    assert "FixtureBook" in tickets
    assert "The odds say about" in tickets
    assert "Lilly's verdict:" in tickets
    # Footer: every total the old cards showed.
    assert "True overall chance:" in tickets
    assert "about 1 in" in tickets
    assert "Combined odds:" in tickets
    assert "If all legs win, $1 staked returns $" in tickets
    assert "Expected value per $1:" in tickets
    assert "Long shot." in tickets  # the 10-pick honest line
    # Every leg carries its form-test grade from the pull's
    # test-before-picking pass (NO DATA here — the fixture
    # stats store has no logs, which grades neutral).
    assert "Form test:" in tickets
    assert "NO DATA" in tickets


def test_app_no_key_shows_the_setup_note_and_no_picks(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    at = _login_as_king(_run_app(FAKE_SECRETS))
    text = _all_text(at)
    assert (
        "Add your Odds API key in Streamlit Secrets to pull live props."
        in text
    )
    assert at.session_state["parlays"] == []
    assert _ticket_texts(at) == []
    [b for b in at.button if "Run the algorithm" in b.label][0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    assert at.session_state["parlays"] == []
    assert _ticket_texts(at) == []
    assert not (tmp_path / "data" / "current_slate.json").exists()
    # The game-day banner still shows the date, but with no
    # slate it invents no games and no lock countdown.
    banner = "\n".join(m.value for m in at.markdown if "gameday" in m.value)
    assert "GAME DAY" in banner
    assert "TONIGHT'S GAMES" not in banner
    assert "First lock" not in banner


def test_app_gameday_header_shows_games_lock_quota_and_stamp(
    tmp_path, monkeypatch
):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    holder = {"props": _fixture_props(), "alts": _fixture_alts()}
    _install_fake_client(monkeypatch, holder, [])
    at = _login_as_king(_run_app(FAKE_SECRETS))
    [b for b in at.button if "Run the algorithm" in b.label][0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    # The banner sits at the very top of the page, so on the
    # pull's own run it still shows the pre-pull state; the
    # next load — every stored-slate visit — renders it from
    # the stored slate. Simulate that load.
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    banner = "\n".join(m.value for m in at.markdown if "gameday" in m.value)
    assert "GAME DAY" in banner
    assert "TONIGHT'S GAMES" in banner
    assert "Team0 vs Team1" in banner  # from the stored slate
    assert "ET" in banner  # real start time, Detroit
    assert "First lock in" in banner
    assert "Stored slate — pulled" in banner
    assert "Odds API quota: 499 of 500 requests left" in banner
    # The sportsbook section structure is on the page.
    subheaders = "\n".join(s.value for s in at.subheader)
    assert "Tonight's Slate" in subheaders
    assert "King's style — dropped lines" in subheaders
    # 👑 King's style LEADS the Dashboard; the standard
    # slate sits below it (Neko's order, kept in the
    # stored-slate flow: the store holds both sets and the
    # Dashboard re-renders them in this order).
    headers = [s.value for s in at.subheader]
    king_at = next(i for i, h in enumerate(headers) if "King's style" in h)
    slate_at = next(i for i, h in enumerate(headers) if "Tonight's Slate" in h)
    assert king_at < slate_at


def test_app_results_show_stamps_and_demo_entries_are_gone(
    tmp_path, monkeypatch
):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    stamped = PickEntry(
        id="s1", key="s1", date="2026-11-12", sport="NBA", event="A vs B",
        player="Stamped Player", market="Points Over", line=10.5,
        odds=-110, probability=0.55, mode="live", label="Stamped Player — Points Over 10.5",
        created_at=PULLED,
    )
    legacy = PickEntry(
        id="l1", key="l1", date="2026-11-10", sport="NBA", event="C vs D",
        player="Legacy Player", market="Points Over", line=12.5,
        odds=-110, probability=0.5, mode="live", label="Legacy Player — Points Over 12.5",
    )
    demo = PickEntry(
        id="d1", key="d1", date="2026-11-12", sport="NBA",
        event="Sample vs Demo", player="Sample Star",
        market="Points Over", line=24.5, odds=-110, probability=0.5,
        mode="demo", label="Sample Star — Points Over 24.5",
    )
    (tmp_path / "data" / "pick_log.json").write_text(
        json.dumps([asdict(stamped), asdict(legacy), asdict(demo)], indent=2)
    )
    at = _login_as_king(_run_app(FAKE_SECRETS))
    # The purge ran at app load: the file holds live only.
    on_disk = json.loads((tmp_path / "data" / "pick_log.json").read_text())
    assert {row["mode"] for row in on_disk} == {"live"}
    list(at.radio)[0].set_value("Results / Ledger").run()
    assert not at.exception, f"app raised: {at.exception}"
    text = _all_text(at)
    assert "Stamped Player" in text
    assert "Pulled Nov 12, 2026 · 04:37 PM ET" in text
    assert "Legacy Player" in text
    assert "Pulled Nov 10, 2026" in text  # date fallback, no invented time
    assert "Sample Star" not in text


def test_app_my_slip_summary_is_a_ticket(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    holder = {"props": _fixture_props(), "alts": _fixture_alts()}
    _install_fake_client(monkeypatch, holder, [])
    at = _login_as_king(_run_app(FAKE_SECRETS))
    [b for b in at.button if "Run the algorithm" in b.label][0].click().run()
    list(at.radio)[0].set_value("🎟️ My Slip").run()
    assert not at.exception, f"app raised: {at.exception}"
    add = [b for b in at.button if b.label == "➕ Add to slip"]
    assert add, "no Add to slip buttons on the board"
    add[-1].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    tickets = "\n".join(_ticket_texts(at))
    assert "MY SLIP" in tickets
    assert "Ticket #KSB-" in tickets
    assert "Combined odds:" in tickets
    assert "True overall chance:" in tickets
    assert "Bet amount:" in tickets
    assert "If it hits, you get back" in tickets
    assert "Profit:" in tickets
    assert "Expected value at this amount:" in tickets
    # Saving runs the record path end-to-end. (Every board
    # row is also a leg in Lilly's logged parlays, and the
    # log dedupes by pick, so her honest answer here is that
    # the legs were already on the record — the live,
    # timestamped manual write itself is pinned by
    # test_manual_save_is_live_and_timestamped above.)
    save = [b for b in at.button if "Save slip" in b.label]
    save[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    feedback = "\n".join(s.value for s in at.success)
    assert "Saved" in feedback and "already on it" in feedback


# ---------- the new plan in the store (2026-11-12) ----------


def test_store_round_trip_with_the_new_plan(tmp_path):
    from collections import Counter

    store = slate_store.SlateStore(tmp_path / "current_slate.json")
    scored = [_scored(f"Fixture{i}", line=10.5 + i) for i in range(14)]
    parlays, _ = build_parlays(scored)  # the default plan
    assert Counter(p.size for p in parlays) == Counter({2: 4, 3: 4, 5: 2, 10: 1})
    payload = slate_store.build_payload(
        pulled_at=PULLED, note="new plan", raw_props=scored,
        scored=scored, parlays=parlays, king_parlays=[], king_info={},
    )
    store.save(payload)
    restored = slate_store.restore_slate(store.load())
    assert restored is not None
    assert [slate_store.parlay_to_dict(p) for p in restored["parlays"]] == payload["parlays"]
    assert Counter(p.size for p in restored["parlays"]) == Counter(
        {2: 4, 3: 4, 5: 2, 10: 1}
    )
    # The for-fun 10-pick keeps its honest footer line.
    ten = next(p for p in restored["parlays"] if p.size == 10)
    assert "Long shot" in ten.plain_summary()


def test_app_renders_a_legacy_stored_slate_with_a_10_pick(tmp_path, monkeypatch):
    # A slate stored by an older build — one holding a
    # 10-pick parlay — must still load and render as-is:
    # no crash, no silent rebuild, the ticket prints with
    # its honest long-shot terms.
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    scored = [_scored(f"Fixture{i}", line=10.5 + i) for i in range(12)]
    parlays, _ = build_parlays(scored, plan=[(10, 1), (3, 1)])
    payload = slate_store.build_payload(
        pulled_at=PULLED, note="legacy slate", raw_props=scored,
        scored=scored, parlays=parlays, king_parlays=[], king_info={},
    )
    slate_store.SlateStore(tmp_path / "data" / "current_slate.json").save(payload)

    at = _login_as_king(_run_app(FAKE_SECRETS))
    tickets = "\n".join(_ticket_texts(at))
    assert "10-pick parlay" in tickets
    assert "Long shot." in tickets
    assert "3-pick parlay" in tickets
    assert [p.size for p in at.session_state["parlays"]] == [10, 3]
