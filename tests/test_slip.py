"""Tests for My Slip — the manual pick-slip builder:
slip storage (per profile, uniqueness), the slip maths
(hand-computed odds / payout / probability), saving a slip
to the pick log as MANUAL picks, and the guarantee that
manual picks never teach Lilly's learning loop.

Run: pytest -q

The PINs below ("1111" / "2222") are FAKE test values. The
real PINs exist only in Neko's Streamlit secrets — never in
this repo.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kings_picks import learning
from kings_picks.learning import LearningEngine
from kings_picks.model import Prop, decimal_to_american, score_prop
from kings_picks.pick_log import PickEntry, PickLog
from kings_picks.slip import SlipBook, SlipLeg, overall_probability, slip_summary

FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
TODAY = "2026-11-12"


def leg(player="Star", sport="NBA", prop="Points", side="Over",
        line=24.5, odds=-110, probability=0.60, **kw) -> SlipLeg:
    return SlipLeg(
        player=player, sport=sport, prop=prop, side=side, line=line,
        odds=odds, probability=probability,
        bookmaker=kw.get("bookmaker", "Test Book"),
        event=kw.get("event", "A vs B"),
        has_form_data=kw.get("has_form_data", False),
    )


# ---------- leg identity ----------


def test_leg_key_and_label_match_prop_rules():
    p = Prop(player="Star", sport="NBA", prop="Points", side="Over",
             line=24.5, odds=-110)
    snapped = SlipLeg.from_prop(score_prop(p, pair_odds=-110))
    assert snapped.key == p.key == "Star|NBA|Points|24.5"
    assert snapped.label == "Star — Points Over 24.5"
    assert snapped.odds == -110 and snapped.bookmaker == ""


# ---------- slip book: add / remove / clear / uniqueness ----------


def test_add_remove_clear(tmp_path):
    book = SlipBook(tmp_path / "slips.json")
    assert book.add("King", leg())
    assert book.add("King", leg(player="Other", line=10.5))
    assert len(book.legs("King")) == 2
    assert book.contains("King", leg().key)
    assert book.remove("King", leg().key)
    assert not book.remove("King", leg().key)  # already gone
    assert len(book.legs("King")) == 1
    book.clear("King")
    assert book.legs("King") == []


def test_same_selection_cannot_repeat_even_other_side(tmp_path):
    book = SlipBook(tmp_path / "slips.json")
    assert book.add("King", leg(side="Over"))
    # Same player + prop + line, other side: same Prop.key,
    # so the slip refuses it — both sides of one line can't
    # sit on one slip, exactly like Lilly's parlays.
    assert not book.add("King", leg(side="Under"))
    assert len(book.legs("King")) == 1
    # A different line for the same player/prop is a different
    # selection and is fine.
    assert book.add("King", leg(line=25.5))


def test_slips_are_per_profile_and_survive_reload(tmp_path):
    path = tmp_path / "slips.json"
    book = SlipBook(path)
    book.add("King", leg())
    book.add("Kohen", leg(player="Son Pick", line=5.5))
    assert [l.player for l in book.legs("King")] == ["Star"]
    assert [l.player for l in book.legs("Kohen")] == ["Son Pick"]
    book.clear("King")
    assert book.legs("King") == []
    assert len(book.legs("Kohen")) == 1  # untouched
    reloaded = SlipBook(path)
    assert [l.player for l in reloaded.legs("Kohen")] == ["Son Pick"]
    assert reloaded.legs("Kohen")[0].odds == -110


# ---------- conversions ----------


def test_decimal_to_american_hand_computed():
    assert decimal_to_american(2.0) == 100
    assert decimal_to_american(3.0) == 200
    assert decimal_to_american(1.5) == -200
    assert decimal_to_american(21 / 11) == -110  # a -110 leg round-trips
    with pytest.raises(ValueError):
        decimal_to_american(1.0)


# ---------- slip maths (hand-computed) ----------


def test_two_leg_maths():
    legs = [leg(odds=-110, probability=0.60),
            leg(player="B", line=5.5, odds=150, probability=0.50)]
    s = slip_summary(legs, 10.0)
    # decimal: (21/11) * 2.5 = 4.7727... -> American +377
    assert s["decimal_odds"] == pytest.approx(21 / 11 * 2.5)
    assert s["american_odds"] == 377
    # true overall chance: 0.60 * 0.50
    assert s["overall_probability"] == pytest.approx(0.30)
    assert s["one_in"] == pytest.approx(1 / 0.30)
    assert s["payout"] == pytest.approx(10.0 * 21 / 11 * 2.5)
    assert s["profit"] == pytest.approx(s["payout"] - 10.0)
    assert s["expected_value"] == pytest.approx(0.30 * s["payout"] - 10.0)


def test_three_leg_maths():
    legs = [leg(odds=-110, probability=0.60),
            leg(player="B", line=5.5, odds=-110, probability=0.50),
            leg(player="C", line=7.5, odds=100, probability=0.50)]
    s = slip_summary(legs, 20.0)
    # decimal: (21/11)^2 * 2.0 = 7.2892... -> American +629
    assert s["decimal_odds"] == pytest.approx((21 / 11) ** 2 * 2.0)
    assert s["american_odds"] == 629
    assert s["overall_probability"] == pytest.approx(0.15)
    assert s["payout"] == pytest.approx(20.0 * (21 / 11) ** 2 * 2.0)
    assert overall_probability(legs) == pytest.approx(0.15)


def test_stake_changes_money_never_odds_or_chance():
    legs = [leg(odds=-110, probability=0.60),
            leg(player="B", line=5.5, odds=150, probability=0.50)]
    small = slip_summary(legs, 5.0)
    big = slip_summary(legs, 50.0)
    assert small["decimal_odds"] == big["decimal_odds"]
    assert small["american_odds"] == big["american_odds"]
    assert small["overall_probability"] == big["overall_probability"]
    assert big["payout"] == pytest.approx(small["payout"] * 10)
    assert big["profit"] == pytest.approx(big["payout"] - 50.0)


def test_empty_slip_summary_is_zeroed():
    s = slip_summary([], 25.0)
    assert s["legs"] == 0
    assert s["overall_probability"] == 0.0
    assert s["payout"] == 0.0 and s["profit"] == 0.0
    assert s["american_odds"] is None and s["one_in"] is None


# ---------- saving a slip to the pick log ----------


def test_record_manual_tags_entries_and_marks_played(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    legs = [leg(), leg(player="B", line=5.5, odds=150, probability=0.5)]
    added, already = log.record_manual(legs, mode="live", profile="King",
                                       created=TODAY)
    assert len(added) == 2 and already == []
    for entry in added:
        assert entry.source == "manual"
        assert entry.mode == "live"
        assert entry.played_by == ["King"]
        assert entry.status == "open"
    assert added[0].market == "Points Over"
    assert added[0].label == "Star — Points Over 24.5"
    # Saving the same slip again the same day adds nothing.
    added2, already2 = log.record_manual(legs, mode="live", profile="King",
                                         created=TODAY)
    assert added2 == [] and len(already2) == 2
    assert len(log.entries) == 2


def test_record_manual_keeps_lilly_entry_first_write_wins(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    # Lilly's algorithm logs the pick first (a one-pick parlay).
    pick = score_prop(Prop(player="Star", sport="NBA", prop="Points",
                           side="Over", line=24.5, odds=-110,
                           event="A vs B"), pair_odds=-110)
    parlay = SimpleNamespace(picks=[pick])
    log.record_run([parlay], "live", created=TODAY)
    # King hand-builds the same selection on My Slip and saves.
    added, already = log.record_manual([SlipLeg.from_prop(pick)],
                                       mode="live", profile="King",
                                       created=TODAY)
    assert added == [] and len(already) == 1
    entry = log.entries[0]
    assert entry.source == "lilly"  # first write wins — still hers
    assert entry.played_by == ["King"]  # but now on his played record


def test_manual_picks_in_stats_are_separate_from_lilly(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    added, _ = log.record_manual([leg(), leg(player="B", line=5.5)],
                                 mode="live", profile="Kohen", created=TODAY)
    log.settle(added[0].key, True)
    stats = log.stats(profile="Kohen")
    # Not in Lilly's headline record...
    assert stats["total"] == 0 and stats["settled"] == 0
    # ...counted in their own block...
    assert stats["manual"]["total"] == 2
    assert stats["manual"]["settled"] == 1
    assert stats["manual"]["hit_rate"] == pytest.approx(1.0)
    # ...and in the builder's own played record.
    assert stats["played"]["total"] == 2
    assert stats["played"]["settled"] == 1


def test_manual_demo_save_stays_demo(tmp_path):
    log = PickLog(tmp_path / "pick_log.json")
    added, _ = log.record_manual([leg()], mode="demo", profile="King",
                                 created=TODAY)
    assert added[0].mode == "demo" and added[0].source == "manual"
    stats = log.stats(profile="King")
    assert stats["total"] == 0 and stats["manual"]["total"] == 0
    assert stats["demo"]["total"] == 1  # demo block, never the record


# ---------- manual picks never teach Lilly ----------


def _entry(n, source="lilly", status="won", mode="live", probability=0.52):
    key = f"2026-11-01|Game {n}|Player {n}|Points Over|24.5"
    return PickEntry(
        id=key, key=key, date="2026-11-01", sport="NBA",
        event=f"Game {n}", player=f"Player {n}", market="Points Over",
        line=24.5, odds=-110, probability=probability, mode=mode,
        label=f"Player {n} — Points Over 24.5", status=status,
        model="v2", source=source,
    )


def test_teaching_entries_exclude_manual():
    entries = (
        [_entry(i) for i in range(5)]  # hers, settled: teach
        + [_entry(50 + i, source="manual") for i in range(5)]  # manual: never
        + [_entry(90 + i, source="manual", status="lost") for i in range(5)]
    )
    teaching = learning.teaching_entries(entries)
    assert len(teaching) == 5
    assert all(getattr(e, "source", "lilly") != "manual" for e in teaching)


def test_manual_record_alone_teaches_lilly_nothing(tmp_path):
    # 40 settled manual picks, all lost against a stated 52%:
    # a screaming calibration signal IF she (wrongly) read it.
    entries = [_entry(i, source="manual", status="lost") for i in range(40)]
    assert learning.compute_buckets(entries) == {}
    assert learning.brier_scores(entries)["settled"] == 0
    eng = LearningEngine(tmp_path / "learned.json", tmp_path / "lessons.jsonl")
    state = eng.refresh(entries, today=TODAY)
    assert state["settled_live"] == 0
    assert state["active_corrections"] == {}
    assert eng.lessons() == []


def test_manual_picks_do_not_move_lillys_buckets(tmp_path):
    hers = [_entry(i, status="won" if i < 15 else "lost") for i in range(40)]
    manual = [_entry(100 + i, source="manual", status="lost") for i in range(40)]
    base = learning.compute_buckets(hers)
    mixed = learning.compute_buckets(hers + manual)
    assert set(mixed) == set(base)
    for key, bucket in base.items():
        assert mixed[key].settled == bucket.settled
        assert mixed[key].correction == pytest.approx(bucket.correction)
    # Sanity: her own 40 settled picks DO form a bucket that
    # teaches (the exclusion is about manual, not about her).
    assert base["nba_points_over"].settled == 40


# ---------- app render: My Slip page (AppTest) ----------


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


def test_app_my_slip_page_builds_and_saves_demo_slip():
    at = _login_as_king(_run_app(FAKE_SECRETS))
    radios = list(at.radio)
    assert radios, "app page radio missing after login"
    radios[0].set_value("🎟️ My Slip").run()
    assert not at.exception, f"app raised: {at.exception}"
    text = "\n".join(
        [m.value for m in at.markdown]
        + [t.value for t in at.title]
        + [s.value for s in at.subheader]
        + [i.value for i in at.info]
    )
    assert "My Slip" in text
    assert "calculator" in text  # the honest note is on the page
    # No key in tests -> demo mode: the board loads on demand.
    load = [b for b in at.button if "Load today's board" in b.label]
    assert load, "board load button missing"
    load[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    add = [b for b in at.button if b.label == "➕ Add to slip"]
    assert add, "no Add to slip buttons on the demo board"
    add[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    # The slip now shows the combined-odds maths and a stake box.
    metric_labels = {m.label for m in at.metric}
    assert "Combined odds" in metric_labels
    assert "True overall chance" in metric_labels
    assert any(ni.key == "slip_stake_King" for ni in at.number_input)
    save = [b for b in at.button if "Save slip" in b.label]
    assert save, "save button missing"
    save[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
