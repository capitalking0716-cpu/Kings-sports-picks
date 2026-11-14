"""Tests for the King & Kohen profile PIN login and per-profile ledger.

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

from kings_picks import profiles
from kings_picks.ledger import Ledger
from kings_picks.model import Prop, score_prop
from kings_picks.parlays import build_parlays

FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}
APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def make_scored(n, base=0.60):
    """n scored props with descending probabilities (mirrors the
    helper in test_kings_picks.py)."""
    out = []
    for i in range(n):
        p = Prop(player=f"Player{i}", sport="NBA", prop="Points",
                 side="Over", line=10.5 + i, odds=-110)
        s = score_prop(p, pair_odds=-110)
        s.probability = max(0.51, base - i * 0.01)
        out.append(s)
    return out


def one_parlay():
    parlays, _ = build_parlays(make_scored(12), plan=[(3, 1)])
    return parlays


# ---------- profiles: configuration ----------

def test_profiles_are_exactly_king_and_kohen():
    assert profiles.PROFILES == ["King", "Kohen"]
    assert profiles.DEFAULT_PROFILE == "King"


def test_configured_only_when_both_pins_set():
    assert profiles.profiles_configured(FAKE_SECRETS) is True
    assert profiles.profiles_configured({"KING_PIN": "1111"}) is False
    assert profiles.profiles_configured({"KOHEN_PIN": "2222"}) is False
    assert profiles.profiles_configured({}) is False
    assert profiles.profiles_configured(None) is False
    assert profiles.profiles_configured({"KING_PIN": "", "KOHEN_PIN": "2222"}) is False
    assert profiles.profiles_configured({"KING_PIN": "   ", "KOHEN_PIN": "2222"}) is False


def test_get_expected_pin():
    assert profiles.get_expected_pin("King", FAKE_SECRETS) == "1111"
    assert profiles.get_expected_pin("Kohen", FAKE_SECRETS) == "2222"
    assert profiles.get_expected_pin("King", {}) is None
    assert profiles.get_expected_pin("King", None) is None
    assert profiles.get_expected_pin("Nobody", FAKE_SECRETS) is None
    # A numeric TOML value (no quotes) still works as a PIN.
    assert profiles.get_expected_pin("King", {"KING_PIN": 1111}) == "1111"
    # Surrounding whitespace in the stored secret is ignored.
    assert profiles.get_expected_pin("King", {"KING_PIN": " 1111 "}) == "1111"


# ---------- profiles: PIN verification ----------

def test_verify_pin_correct():
    assert profiles.verify_pin("King", "1111", FAKE_SECRETS) is True
    assert profiles.verify_pin("Kohen", "2222", FAKE_SECRETS) is True


def test_verify_pin_wrong():
    assert profiles.verify_pin("King", "9999", FAKE_SECRETS) is False
    assert profiles.verify_pin("Kohen", "0000", FAKE_SECRETS) is False


def test_verify_pin_other_profiles_pin_does_not_work():
    # Kohen's PIN must not open King's profile, and the reverse.
    assert profiles.verify_pin("King", "2222", FAKE_SECRETS) is False
    assert profiles.verify_pin("Kohen", "1111", FAKE_SECRETS) is False


def test_verify_pin_strips_whitespace():
    assert profiles.verify_pin("King", " 1111 ", FAKE_SECRETS) is True
    assert profiles.verify_pin("King", "\t1111\n", FAKE_SECRETS) is True


@pytest.mark.parametrize("bad", ["111", "11111", "11a1", "abcd", "", "    ", "1 11", None])
def test_verify_pin_rejects_non_4_digit(bad):
    assert profiles.verify_pin("King", bad, FAKE_SECRETS) is False
    assert profiles.is_valid_pin_format(bad) is False


def test_pin_format_accepts_exactly_four_digits():
    assert profiles.is_valid_pin_format("0123") is True
    assert profiles.is_valid_pin_format(" 0123 ") is True


def test_verify_pin_missing_secret_never_passes():
    # No configured PIN -> no login, even with a "nice" guess.
    assert profiles.verify_pin("King", "1111", {}) is False
    assert profiles.verify_pin("King", "1111", None) is False
    assert profiles.verify_pin("King", "1111", {"KOHEN_PIN": "2222"}) is False
    assert profiles.verify_pin("Nobody", "1111", FAKE_SECRETS) is False


# ---------- ledger: per-profile records ----------

def test_record_run_tags_the_active_profile(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record_run(one_parlay(), "demo", profile="King")
    ledger.record_run(one_parlay(), "demo", profile="Kohen")
    assert ledger.parlays[0].profile == "King"
    assert ledger.parlays[1].profile == "Kohen"


def test_record_run_defaults_to_king_for_older_callers(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record_run(one_parlay(), "demo")  # no profile argument
    assert ledger.parlays[0].profile == "King"
    ledger.record_run(one_parlay(), "demo", profile="Somebody Else")
    assert ledger.parlays[1].profile == "King"  # unknown names fall back


def test_stats_are_per_profile(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record_run(one_parlay(), "demo", profile="King")
    ledger.record_run(one_parlay(), "demo", profile="Kohen")
    king_entry, kohen_entry = ledger.parlays
    for pick in king_entry.picks:
        ledger.settle_pick(king_entry.id, pick["key"], True)   # King: all won
    for pick in kohen_entry.picks:
        ledger.settle_pick(kohen_entry.id, pick["key"], False)  # Kohen: all lost

    king = ledger.stats(profile="King")
    assert king["total_parlays"] == 1
    assert king["pick_hit_rate"] == pytest.approx(1.0)
    assert king["parlay_hit_rate"] == pytest.approx(1.0)

    kohen = ledger.stats(profile="Kohen")
    assert kohen["total_parlays"] == 1
    assert kohen["pick_hit_rate"] == pytest.approx(0.0)
    assert kohen["parlay_hit_rate"] == pytest.approx(0.0)

    overall = ledger.stats()  # no profile -> everything, as before
    assert overall["total_parlays"] == 2
    assert overall["pick_hit_rate"] == pytest.approx(0.5)


def test_parlays_for_profile_filters(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json")
    ledger.record_run(one_parlay(), "demo", profile="King")
    ledger.record_run(one_parlay(), "demo", profile="Kohen")
    assert len(ledger.parlays_for_profile("King")) == 1
    assert len(ledger.parlays_for_profile("Kohen")) == 1
    assert ledger.parlays_for_profile("Kohen")[0].profile == "Kohen"


def test_legacy_entries_without_profile_count_as_king(tmp_path):
    path = tmp_path / "ledger.json"
    legacy = [{
        "id": "legacy01", "date": "2026-11-10", "size": 3,
        "label": "3-pick parlay", "overall_probability": 0.2,
        "mode": "demo", "settled": True,
        "picks": [{"key": "k1", "label": "P Over 10.5",
                   "probability": 0.6, "odds": -110, "result": "won"}],
        # note: no "profile" field — written before profiles existed
    }]
    path.write_text(json.dumps(legacy))
    ledger = Ledger(path)
    assert ledger.parlays[0].profile == "King"
    assert len(ledger.parlays_for_profile("King")) == 1
    assert ledger.parlays_for_profile("Kohen") == []
    assert ledger.stats(profile="King")["total_parlays"] == 1
    assert ledger.stats(profile="Kohen")["total_parlays"] == 0


def test_entry_profile_helper():
    assert profiles.entry_profile({}) == "King"
    assert profiles.entry_profile({"profile": "Kohen"}) == "Kohen"
    assert profiles.entry_profile({"profile": "bogus"}) == "King"
    assert profiles.normalize_profile(None) == "King"


# ---------- app render checks (Streamlit AppTest) ----------

def _run_app(secrets=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=30)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    return at


def _click_submit(at, label):
    matches = [b for b in at.button if b.label == label]
    assert matches, f"no button labelled {label!r}; have {[b.label for b in at.button]}"
    matches[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    return at


def test_app_shows_setup_screen_when_pin_secrets_missing():
    at = _run_app()  # no KING_PIN / KOHEN_PIN at all
    text = "\n".join(m.value for m in at.markdown)
    assert "Profiles not set up yet" in text
    assert "KING_PIN" in text and "KOHEN_PIN" in text
    # No way in: no profile buttons, no app pages.
    assert [b for b in at.button if b.key in ("login-king", "login-kohen")] == []
    assert list(at.radio) == []


def test_app_login_screen_when_logged_out():
    at = _run_app(FAKE_SECRETS)
    assert [t.value for t in at.title] == ["👑 Kings Sports Books Picks"]
    keys = {b.key for b in at.button}
    assert "login-king" in keys and "login-kohen" in keys
    # The app itself stays shut until a PIN is entered.
    assert list(at.radio) == []
    assert "active_profile" not in at.session_state


def test_app_wrong_pin_is_denied():
    at = _run_app(FAKE_SECRETS)
    at.button(key="login-kohen").click().run()
    at.text_input(key="pin_entry").set_value("9999")
    _click_submit(at, "Log in")
    assert any("Wrong PIN" in e.value for e in at.error)
    assert "active_profile" not in at.session_state
    assert list(at.radio) == []  # still on the login gate


def test_app_king_login_opens_app_and_logout_returns():
    at = _run_app(FAKE_SECRETS)
    at.button(key="login-king").click().run()
    at.text_input(key="pin_entry").set_value("1111")
    _click_submit(at, "Log in")
    assert at.session_state["active_profile"] == "King"
    topbar_text = "\n".join(m.value for m in at.markdown)
    assert "Signed in as 👑 King" in topbar_text
    # The real app is open now (page radio + Dashboard run button).
    assert list(at.radio) != []
    assert any("Run the algorithm" in b.label for b in at.button)
    # Log out -> straight back to the profile picker.
    at.button(key="logout").click().run()
    assert "active_profile" not in at.session_state
    keys = {b.key for b in at.button}
    assert "login-king" in keys and "login-kohen" in keys


def test_app_kohen_login_uses_kohen_profile():
    at = _run_app(FAKE_SECRETS)
    at.button(key="login-kohen").click().run()
    # King's PIN must not open Kohen's profile.
    at.text_input(key="pin_entry").set_value("1111")
    _click_submit(at, "Log in")
    assert "active_profile" not in at.session_state
    # Kohen's own PIN does.
    at.text_input(key="pin_entry").set_value("2222")
    _click_submit(at, "Log in")
    assert at.session_state["active_profile"] == "Kohen"
    topbar_text = "\n".join(m.value for m in at.markdown)
    assert "Signed in as Kohen" in topbar_text


# ---------- remember-me cookie: profile_from_cookie ----------

def test_cookie_name_is_ksp_profile():
    # The cookie holds ONLY a profile name — never a PIN.
    assert profiles.COOKIE_NAME == "ksp_profile"


def test_profile_from_cookie_valid():
    assert profiles.profile_from_cookie("King", FAKE_SECRETS) == "King"
    assert profiles.profile_from_cookie("Kohen", FAKE_SECRETS) == "Kohen"
    # Stray whitespace from the browser is tolerated.
    assert profiles.profile_from_cookie("  King  ", FAKE_SECRETS) == "King"


@pytest.mark.parametrize("junk", [
    "Neko", "king", "KING", "kohen", "", "   ", "King Kohen",
    "King|Kohen", "1111", "👑 King", None, 0, 1234, ["King"], {"p": "King"},
])
def test_profile_from_cookie_rejects_junk(junk):
    assert profiles.profile_from_cookie(junk, FAKE_SECRETS) is None


def test_profile_from_cookie_needs_configured_profiles():
    # A cookie is never a way around an unconfigured app: with
    # either PIN missing from secrets, the cookie grants nothing.
    assert profiles.profile_from_cookie("King", {}) is None
    assert profiles.profile_from_cookie("King", None) is None
    assert profiles.profile_from_cookie("King", {"KING_PIN": "1111"}) is None
    assert profiles.profile_from_cookie("Kohen", {"KOHEN_PIN": "2222"}) is None
    assert profiles.profile_from_cookie("King", {"KING_PIN": "", "KOHEN_PIN": "2222"}) is None
