"""Tests for the 2026-11-14 UI rework — layout only.

Neko's words: "Go through the app, make it look
professional. The dashboard should have all the tools,
players, pick options on display. Rework the whole site
to mirror bet365 with my features of Lilly."

Covered here (presentation, not picking logic):
- the app opens on a top bar — brand + signed-in profile
  chip — with the pages as one tab row, in the sportsbook
  order: Dashboard · Games & Props · Players · My Slip ·
  My Record · Lilly · Setup. No sidebar navigation.
- EVERY page renders without exceptions for BOTH signed-in
  profiles (the walk below), each showing its own marker
  content.
- the Dashboard is the control center, its sections in
  the set order: day/pull/status, the games board, my
  slip in short, downloads, the record at a glance.

The PINs below ("1111" / "2222") are FAKE test values.

Run: pytest -q
"""

from pathlib import Path

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
FAKE_SECRETS = {"KING_PIN": "1111", "KOHEN_PIN": "2222"}

PAGES = [
    "Dashboard",
    "Games & Props",
    "Players",
    "My Slip",
    "My Record",
    "💜 Lilly",
    "⚙️ Setup",
]


class _NullStore:
    """Stats store that answers nothing — the Players page
    then rides its bundled-stats-pack fallback, exactly as
    it does where a live feed is blocked."""

    def nfl_logs(self):
        return None

    def nba_logs(self):
        return None

    def nba_teams(self):
        return {}


def _run_app(secrets=None):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=60)
    if secrets:
        for key, value in secrets.items():
            at.secrets[key] = value
    at.run()
    assert not at.exception, f"app raised: {at.exception}"
    at.session_state["stats_store"] = _NullStore()
    return at


def _login(at, profile, pin):
    at.button(key=f"login-{profile.lower()}").click().run()
    at.text_input(key="pin_entry").set_value(pin)
    matches = [b for b in at.button if b.label == "Log in"]
    assert matches, "no Log in button"
    matches[0].click().run()
    assert not at.exception, f"app raised: {at.exception}"
    assert at.session_state["active_profile"] == profile
    return at


def _all_text(at):
    parts = []
    for accessor in (
        "title", "header", "subheader", "markdown", "caption",
        "info", "success", "write", "metric",
    ):
        try:
            parts += [str(el.value) for el in getattr(at, accessor)]
        except Exception:
            pass
    for accessor in ("selectbox", "button", "radio"):
        try:
            parts += [str(el.label) for el in getattr(at, accessor)]
        except Exception:
            pass
    return "\n".join(parts)


def _nav(at):
    radios = list(at.radio)
    assert radios, "the tab navigation is missing"
    return radios[0]


# ---------- the chrome: top bar + tabs, no sidebar nav ----------


def test_top_bar_and_tab_order(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    at = _login(_run_app(FAKE_SECRETS), "King", "1111")
    text = _all_text(at)
    # The brand bar and the signed-in chip, up top.
    assert "KINGS SPORTS BOOKS PICKS" in text
    assert "Signed in as 👑 King" in text
    # The tabs, in the sportsbook order — and nothing is
    # navigated from a sidebar any more.
    assert list(_nav(at).options) == PAGES
    assert not [r for r in at.sidebar.radio]


# ---------- the walk: every page, both profiles ----------


PAGE_MARKERS = {
    "Dashboard": ["⬇️ Downloads", "📊 Your record", "🏟️ Today's games"],
    "Games & Props": ["Games & Props", "No props loaded yet"],
    "Players": ["Players", "Pick a player"],
    "My Slip": ["My Slip", "calculator"],
    "My Record": ["My Record", "Pick log", "Back up your records"],
    "💜 Lilly": ["Chat with Lilly"],
    "⚙️ Setup": ["Setup", "How to get the free key"],
}


def test_every_page_renders_for_both_profiles(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    for profile, pin in (("King", "1111"), ("Kohen", "2222")):
        at = _login(_run_app(FAKE_SECRETS), profile, pin)
        for target in PAGES:
            _nav(at).set_value(target).run()
            assert not at.exception, (
                f"{profile} on {target}: app raised {at.exception}"
            )
            text = _all_text(at)
            for marker in PAGE_MARKERS[target]:
                assert marker in text, (
                    f"{profile} on {target}: missing {marker!r}"
                )
        # And back out cleanly.
        at.button(key="logout").click().run()
        assert "active_profile" not in at.session_state


# ---------- the Dashboard is the control center, in order ----------


def test_dashboard_sections_in_order(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    at = _login(_run_app(FAKE_SECRETS), "King", "1111")
    _nav(at).set_value("Dashboard").run()
    assert not at.exception, f"app raised: {at.exception}"
    subheads = [s.value for s in at.subheader]
    # The set order: special picks ride above the games
    # board section; tickets (none pulled here) sit between
    # the board and the slip; downloads precede the record.
    def _index(fragment):
        hits = [i for i, s in enumerate(subheads) if fragment in s]
        assert hits, f"no subheader containing {fragment!r}: {subheads}"
        return hits[0]

    assert _index("Today's games") < _index("My slip")
    assert _index("My slip") < _index("Downloads")
    assert _index("Downloads") < _index("Your record")
    # The tools are all on display: pull buttons, the
    # special-picks button with its rule, the slip shortcut
    # and the record shortcut.
    labels = [b.label for b in at.button]
    assert any("Run the algorithm" in label for label in labels)
    assert any("Pull fresh odds" in label for label in labels)
    assert any("SPECIAL PICKS" in label for label in labels)
    assert any("Open My Slip" in label for label in labels)
    assert any("Open my full record" in label for label in labels)
    assert "Lights up on days with 6 or more NFL games" in _all_text(at)
