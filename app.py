"""Kings Sports Books Picks — Neko's own sports picks platform.

Local only. Paper tracking only. No real bets, no money moved.
Lilly never promises wins.
"""

import csv
import io
import json
from dataclasses import asdict
from datetime import datetime, timedelta

import streamlit as st

from kings_picks import demo_data, lilly, profiles
from kings_picks.ledger import Ledger
from kings_picks.model import score_prop
from kings_picks.odds_api import OddsApiClient
from kings_picks.parlays import build_parlays
from kings_picks.pick_log import PickLog

st.set_page_config(
    page_title="Kings Sports Books Picks",
    page_icon="👑",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---- Black / purple / gold theme, phone-first ----
st.markdown(
    """
    <style>
    .stApp { background-color: #0b0b12; color: #f5f0ff; }
    section[data-testid="stSidebar"] { background-color: #14091f; }
    h1, h2, h3 { color: #d4af37 !important; }
    .stButton > button {
        background-color: #7b2fbe !important; color: white !important;
        font-size: 20px !important; font-weight: bold !important;
        border-radius: 14px !important; min-height: 56px !important;
        border: 2px solid #d4af37 !important; width: 100%;
    }
    .stButton > button:hover { background-color: #9b4fd8 !important; }
    div[data-testid="stMetricValue"] { color: #d4af37; }
    .demo-banner {
        background-color: #3d2b00; border: 2px solid #d4af37;
        color: #ffe08a; padding: 14px; border-radius: 12px;
        font-size: 20px; font-weight: bold; text-align: center;
    }
    .live-banner {
        background-color: #12351f; border: 2px solid #2ecc71;
        color: #b6f5cd; padding: 14px; border-radius: 12px;
        font-size: 20px; font-weight: bold; text-align: center;
    }
    .warn-box {
        background-color: #2a0a0a; border: 2px solid #e74c3c;
        color: #ffd2cc; padding: 14px; border-radius: 12px; font-size: 18px;
    }
    .pick-card {
        background-color: #1c1026; border: 1px solid #7b2fbe;
        border-radius: 12px; padding: 12px; margin-bottom: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---- Profiles gate: King & Kohen, 4-digit PIN login ----
# Nothing in the app renders until a profile is signed in. PINs are
# read ONLY from Streamlit secrets (KING_PIN / KOHEN_PIN), set by
# Neko himself — they are never in this code.

def _secrets_snapshot() -> dict:
    """The PIN secrets as a plain dict. st.secrets raises when no
    secrets exist at all, so any failure here means "not set up"."""
    snapshot = {}
    try:
        for key in profiles.SECRET_KEYS.values():
            snapshot[key] = st.secrets.get(key)
    except Exception:
        return {}
    return snapshot


SECRETS = _secrets_snapshot()

if not profiles.profiles_configured(SECRETS):
    st.title("👑 Kings Sports Books Picks")
    st.markdown(
        "<div class='warn-box'><b>Profiles not set up yet.</b><br>"
        "This app has two profiles — King and Kohen — and each one needs "
        "a 4-digit PIN before anyone can get in.<br><br>"
        "Owner: open this app's <b>Settings → Secrets</b> and add two lines "
        "(choose your own PINs — they are never stored in the app itself):<br>"
        "<code>KING_PIN = \"....\"</code><br>"
        "<code>KOHEN_PIN = \"....\"</code><br><br>"
        "Then reboot the app.</div>",
        unsafe_allow_html=True,
    )
    st.stop()

# ---- Remember this phone (browser cookie) ----
# After a successful PIN login, this phone may store ONE cookie
# whose value is only the profile name ("King" / "Kohen") — never
# the PIN. A phone with that cookie reopens straight into its
# profile. Logging out deletes the cookie: the phone forgets, and
# the next visit needs a PIN again. Switching to the OTHER
# profile always needs that profile's PIN.
#
# The cookie component is guarded: if extra-streamlit-components
# is unavailable or anything about the cookie fails, remember-me
# silently turns off and the PIN gate works exactly as before.
try:
    import extra_streamlit_components as _stx
except ImportError:
    _stx = None


def _make_cookie_manager():
    if _stx is None:
        return None
    try:
        return _stx.CookieManager(key="ksp_cookie_manager")
    except Exception:
        return None


COOKIE_MANAGER = _make_cookie_manager()


def _remembered_profile():
    """The profile this phone remembers, or None.

    Cookie reads on Streamlit reruns are finicky — a missing or
    failed read simply means "show the PIN gate", never an error.
    """
    if COOKIE_MANAGER is None:
        return None
    try:
        getter = getattr(COOKIE_MANAGER, "get_all", None) or getattr(
            COOKIE_MANAGER, "getAll", None
        )
        cookies = getter() or {}
    except Exception:
        return None
    if not isinstance(cookies, dict):
        return None
    return profiles.profile_from_cookie(cookies.get(profiles.COOKIE_NAME), SECRETS)


def _remember_this_phone(profile: str) -> None:
    """Store the profile-name cookie on this phone for a year."""
    if COOKIE_MANAGER is None:
        return
    try:
        COOKIE_MANAGER.set(
            profiles.COOKIE_NAME,
            profile,
            expires_at=datetime.now() + timedelta(days=365),
            key="ksp_set_profile_cookie",
        )
        st.session_state["via_cookie"] = True
    except Exception:
        pass


def _forget_this_phone() -> None:
    """Delete the remember-me cookie — this phone forgets."""
    if COOKIE_MANAGER is None:
        return
    try:
        COOKIE_MANAGER.delete(profiles.COOKIE_NAME, key="ksp_delete_profile_cookie")
    except Exception:
        pass


active_profile = st.session_state.get("active_profile")
if active_profile not in profiles.PROFILES:
    active_profile = None

if active_profile is None:
    remembered = _remembered_profile()
    if remembered is not None:
        st.session_state["active_profile"] = remembered
        st.session_state["via_cookie"] = True
        active_profile = remembered

if active_profile is None:
    st.title("👑 Kings Sports Books Picks")
    st.subheader("Who's playing? Pick your profile.")
    pending_profile = st.session_state.get("pending_profile")
    if pending_profile not in profiles.PROFILES:
        cols = st.columns(len(profiles.PROFILES))
        for col, name in zip(cols, profiles.PROFILES):
            if col.button(profiles.PROFILE_LABELS[name], key=f"login-{name.lower()}"):
                st.session_state["pending_profile"] = name
                st.rerun()
    else:
        st.markdown(f"### PIN for {profiles.PROFILE_LABELS[pending_profile]}")
        with st.form("pin_form", clear_on_submit=True):
            pin_entry = st.text_input(
                "4-digit PIN",
                type="password",
                max_chars=4,
                key="pin_entry",
                placeholder="••••",
            )
            submitted = st.form_submit_button("Log in")
        if submitted:
            if profiles.verify_pin(pending_profile, pin_entry, SECRETS):
                st.session_state["active_profile"] = pending_profile
                st.session_state.pop("pending_profile", None)
                _remember_this_phone(pending_profile)
                st.rerun()
            else:
                st.error("❌ Wrong PIN — it has to be exactly the 4 digits. Try again.")
        if st.button("← Back to profiles", key="login-back"):
            st.session_state.pop("pending_profile", None)
            st.rerun()
    st.stop()

DATA_DIR = "data"
LEDGER = Ledger(f"{DATA_DIR}/ledger.json")
PICK_LOG = PickLog(f"{DATA_DIR}/pick_log.json")


def get_client() -> OddsApiClient:
    return OddsApiClient(cache_dir=f"{DATA_DIR}/cache")


def load_props(force_refresh: bool = False):
    """Live props if a key is set, else clearly-labelled demo data."""
    client = get_client()
    if not client.has_key:
        return demo_data.demo_props(), "demo", (
            "DEMO MODE — no API key set. These are SAMPLE picks, not live picks. "
            "Add the free key in Setup to get live props."
        )
    result = client.fetch_props("NBA", force_refresh=force_refresh)
    props = list(result.props)
    nfl = client.fetch_props("NFL", force_refresh=force_refresh)
    props.extend(nfl.props)
    if not result.ok and not props:
        return demo_data.demo_props(), "demo", (
            f"LIVE DATA FAILED: {result.message} Showing clearly-labelled DEMO picks instead — "
            "these are NOT live picks."
        )
    note = result.message
    if nfl.requests_remaining:
        note += f" | {nfl.requests_remaining}"
    return props, "live", note


def score_all(props):
    """Score props. Opposite-side odds are paired where both sides exist,
    so the bookmaker's cut is taken out. No form data exists on the
    free tier, so every pick is honestly labelled odds-only."""
    by_line: dict = {}
    for p in props:
        by_line.setdefault((p.player, p.sport, p.prop, p.line), {})[p.side] = p.odds
    scored = []
    for p in props:
        pair = by_line.get((p.player, p.sport, p.prop, p.line), {})
        other_side = "Under" if p.side == "Over" else "Over"
        scored.append(score_prop(p, pair_odds=pair.get(other_side)))
    # Keep only Over picks + the best of each line to avoid mirror duplicates
    # in the pool: for each player/prop/line keep the higher-chance side.
    best: dict = {}
    for s in scored:
        k = s.key
        if k not in best or s.probability > best[k].probability:
            best[k] = s
    return sorted(best.values(), key=lambda x: x.probability, reverse=True)


def render_parlay(parlay, mode: str):
    st.markdown(f"### 👑 {parlay.label} — wins about {parlay.overall_probability:.1%} of the time")
    st.markdown(
        f"**True overall chance: {parlay.overall_probability:.2%}** "
        f"(about 1 in {parlay.one_in:.0f}) &nbsp;|&nbsp; "
        f"If all legs win, $1 staked returns ${parlay.decimal_payout:.2f} &nbsp;|&nbsp; "
        f"Expected value per $1: **${parlay.expected_value_per_dollar:+.2f}**"
    )
    st.info(parlay.plain_summary())
    for pick in parlay.picks:
        verdict, reason = lilly.verdict_for_pick(pick.probability, pick.has_form_data, pick.edge)
        odds_only = "⚠️ Odds-only — no recent-form data" if not pick.has_form_data else "✅ Recent form included"
        st.markdown(
            f"<div class='pick-card'>"
            f"<b style='font-size:18px'>{pick.label}</b><br>"
            f"Odds: {pick.odds:+d} &nbsp;|&nbsp; Chance: <b>{pick.probability:.0%}</b> &nbsp;|&nbsp; "
            f"{pick.bookmaker or ''} &nbsp;|&nbsp; {pick.event}<br>"
            f"{odds_only}<br>"
            f"<i>{pick.why}</i><br>"
            f"<b>Lilly's verdict: {verdict}</b> — {reason}"
            f"</div>",
            unsafe_allow_html=True,
        )


# ---- Sidebar ----
st.sidebar.title("👑 Kings Picks")
st.sidebar.markdown("**Neko's own picks platform.** Paper tracking only — no real bets, no money moved.")
st.sidebar.markdown(f"### Signed in as {profiles.PROFILE_LABELS[active_profile]}")
if st.session_state.get("via_cookie"):
    st.sidebar.markdown(
        "📱 **This phone remembers you** — no PIN needed on this phone. "
        "Log out and this phone forgets you."
    )
if st.sidebar.button("Log out / switch profile", key="logout"):
    _forget_this_phone()
    st.session_state.pop("active_profile", None)
    st.session_state.pop("pending_profile", None)
    st.session_state.pop("via_cookie", None)
    st.rerun()
page = st.sidebar.radio(
    "Go to",
    ["Dashboard", "Props", "💜 Lilly", "Results / Ledger", "Setup"],
    index=0,
)
st.sidebar.markdown("---")
st.sidebar.markdown(
    "Lilly never promises wins. A pick is a chance, never a sure thing. "
    "Sports betting in Michigan is 21+."
)

# ---- Shared state: today's parlays ----
if "parlays" not in st.session_state:
    st.session_state["parlays"] = []
    st.session_state["mode"] = "demo"
    st.session_state["note"] = ""
if "scored" not in st.session_state:
    st.session_state["scored"] = []


def run_algorithm(force_refresh: bool = False):
    props, mode, note = load_props(force_refresh=force_refresh)
    scored = score_all(props)
    parlays, build_notes = build_parlays(scored)
    st.session_state["parlays"] = parlays
    st.session_state["scored"] = scored
    st.session_state["mode"] = mode
    st.session_state["note"] = note + (" | " + " | ".join(build_notes) if build_notes else "")
    if parlays:
        LEDGER.record_run(parlays, mode, profile=active_profile)
        PICK_LOG.record_run(parlays, mode)
    return parlays, mode, note


# ================= DASHBOARD =================
if page == "Dashboard":
    st.title("👑 Kings Sports Books Picks")
    st.subheader("Today's parlays — built by Lilly")

    if st.session_state["mode"] == "demo":
        st.markdown(
            "<div class='demo-banner'>⚠️ DEMO MODE — SAMPLE PICKS ONLY, NOT LIVE</div>",
            unsafe_allow_html=True,
        )
    elif st.session_state["parlays"]:
        st.markdown(
            "<div class='live-banner'>✅ LIVE PROPS — from The Odds API</div>",
            unsafe_allow_html=True,
        )
    if st.session_state["note"]:
        st.write(st.session_state["note"])

    if st.button("▶ Run the algorithm — build today's parlays"):
        with st.spinner("Lilly is building your parlays..."):
            run_algorithm(force_refresh=False)
        st.success("Done. Parlays saved to the ledger.")

    if st.button("↻ Refresh live props (uses API quota — tap only when you want fresh lines)"):
        with st.spinner("Fetching fresh live props..."):
            run_algorithm(force_refresh=True)
        st.success("Refreshed.")

    if not st.session_state["parlays"]:
        st.markdown(
            "<div class='warn-box'>No parlays built yet. Tap <b>Run the algorithm</b> above. "
            "Without an API key this builds DEMO parlays from sample data.</div>",
            unsafe_allow_html=True,
        )
    else:
        for parlay in st.session_state["parlays"]:
            render_parlay(parlay, st.session_state["mode"])
            st.markdown("---")

# ================= PROPS =================
elif page == "Props":
    st.title("📋 Props")
    st.write("Every prop Lilly can see right now, with its chance and why it was picked.")
    if st.session_state["mode"] == "demo" and st.session_state["scored"]:
        st.markdown(
            "<div class='demo-banner'>⚠️ DEMO MODE — SAMPLE PROPS, NOT LIVE</div>",
            unsafe_allow_html=True,
        )
    if not st.session_state["scored"]:
        st.write("No props loaded yet. Go to the Dashboard and tap **Run the algorithm**.")
    else:
        for pick in st.session_state["scored"]:
            verdict, _ = lilly.verdict_for_pick(pick.probability, pick.has_form_data, pick.edge)
            st.markdown(
                f"<div class='pick-card'>"
                f"<b style='font-size:18px'>{pick.label}</b> — {pick.sport}<br>"
                f"Odds: {pick.odds:+d} &nbsp;|&nbsp; Chance: <b>{pick.probability:.0%}</b> &nbsp;|&nbsp; "
                f"Verdict: <b>{verdict}</b><br>"
                f"<i>{pick.why}</i>"
                f"</div>",
                unsafe_allow_html=True,
            )

# ================= LILLY =================
elif page == "💜 Lilly":
    st.title("💜 Lilly")
    st.markdown(lilly.BRIEF)
    if st.button("▶ Run the algorithm"):
        with st.spinner("Lilly is building your parlays..."):
            run_algorithm(force_refresh=False)
        st.success(f"Built {len(st.session_state['parlays'])} parlays. See the Dashboard.")
    st.subheader("Chat with Lilly — always on, works offline")
    st.write("No key needed. She answers from her rules, never from a guess.")
    if "lilly_chat" not in st.session_state:
        st.session_state["lilly_chat"] = []
    for who, msg in st.session_state["lilly_chat"]:
        st.markdown(f"**{who}:** {msg}")
    user_msg = st.text_input("Ask Lilly something...", key="lilly_input")
    if st.button("Send to Lilly"):
        if user_msg.strip():
            reply = lilly.chat_reply(user_msg)
            st.session_state["lilly_chat"].append(("You", user_msg))
            st.session_state["lilly_chat"].append(("💜 Lilly", reply))
            st.rerun()

# ================= RESULTS / LEDGER =================
elif page == "Results / Ledger":
    st.title("📊 Results / Ledger")
    st.write(
        "Paper tracking only. Mark each pick won or lost after the games. "
        "This record is what proves the picks work — or do not — over time."
    )
    st.write(
        f"Showing **{profiles.PROFILE_LABELS[active_profile]}'s record only** — "
        "parlays recorded while signed in under the other profile are not counted here."
    )
    stats = LEDGER.stats(profile=active_profile)
    col1, col2, col3 = st.columns(3)
    col1.metric("Parlays tracked", stats["total_parlays"])
    col2.metric("Parlays settled", stats["settled_parlays"])
    col3.metric("Pick hit rate", f"{stats['pick_hit_rate']:.0%}" if stats["pick_hit_rate"] is not None else "—")
    st.subheader("Hit rate by parlay size")
    for size in (3, 5, 10):
        row = stats["by_size"][size]
        rate = f"{row['hit_rate']:.0%}" if row["hit_rate"] is not None else "no settled parlays yet"
        st.write(f"**{size}-pick:** {row['won']} won of {row['parlays']} settled — {rate}")

    st.subheader("Settle results")
    for lp in reversed(LEDGER.parlays_for_profile(active_profile)[-10:]):
        with st.expander(f"{lp.date} — {lp.label} ({lp.mode}) — {lp.picks_won} won / {lp.picks_lost} lost / {lp.picks_pending} pending"):
            for pick in lp.picks:
                c1, c2, c3 = st.columns([5, 1, 1])
                c1.write(f"{pick['label']} — chance {pick['probability']:.0%} — {pick['result']}")
                if c2.button("✅ Won", key=f"w-{lp.id}-{pick['key']}"):
                    LEDGER.settle_pick(lp.id, pick["key"], True)
                    st.rerun()
                if c3.button("❌ Lost", key=f"l-{lp.id}-{pick['key']}"):
                    LEDGER.settle_pick(lp.id, pick["key"], False)
                    st.rerun()

    # ---------- Pick log: every pick, played or not ----------
    st.markdown("---")
    st.subheader("🎯 Pick log — every pick Lilly makes")
    st.write(
        "Every leg of every parlay, logged on its own and tracked **whether anyone "
        "played it or not**. Settlement is by hand — no free feed has final player "
        "stats, so after the games you tap Won or Lost here. Lilly never guesses a result."
    )
    pstats = PICK_LOG.stats(profile=active_profile)
    pc1, pc2, pc3, pc4 = st.columns(4)
    pc1.metric("Picks logged", pstats["total"])
    pc2.metric("Settled", pstats["settled"])
    pc3.metric("Pick hit rate", f"{pstats['hit_rate']:.0%}" if pstats["hit_rate"] is not None else "—")
    played = pstats["played"]
    pc4.metric(
        "Your played record",
        f"{played['won']} of {played['settled']}" if played["settled"] else "—",
    )
    if played["hit_rate"] is not None:
        st.caption(
            f"Your played picks hit rate: {played['hit_rate']:.0%} "
            f"({played['settled']} played picks settled)."
        )
    if pstats["demo"]["total"]:
        st.caption(
            f"Demo picks are kept out of the record above: {pstats['demo']['total']} demo "
            f"picks logged, {pstats['demo']['settled']} settled. Demo never counts as a real record."
        )

    open_entries = PICK_LOG.open_picks()
    st.markdown("#### Open picks — settle them after the games")
    if not open_entries:
        st.write("No open picks. Run the algorithm and every pick she makes lands here.")
    open_dates = sorted({e.date for e in open_entries}, reverse=True)
    for group_date in open_dates:
        group = [e for e in open_entries if e.date == group_date]
        with st.expander(f"{group_date} — {len(group)} open picks", expanded=(group_date == open_dates[0])):
            for entry in group:
                demo_tag = " · DEMO" if entry.mode == "demo" else ""
                i_played = active_profile in entry.played_by
                st.markdown(
                    f"<div class='pick-card'>"
                    f"<b style='font-size:17px'>{entry.label}</b> — {entry.sport}{demo_tag}<br>"
                    f"{entry.event} &nbsp;|&nbsp; Odds: {entry.odds:+d} &nbsp;|&nbsp; "
                    f"Chance: <b>{entry.probability:.0%}</b>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                b1, b2, b3 = st.columns(3)
                if b1.button("✅ Won", key=f"pl-won-{entry.key}"):
                    PICK_LOG.settle(entry.key, True)
                    st.rerun()
                if b2.button("❌ Lost", key=f"pl-lost-{entry.key}"):
                    PICK_LOG.settle(entry.key, False)
                    st.rerun()
                played_label = "✅ You played this — tap to undo" if i_played else "🎟️ I played this"
                if b3.button(played_label, key=f"pl-played-{entry.key}"):
                    PICK_LOG.toggle_played(entry.key, active_profile)
                    st.rerun()

    settled_entries = PICK_LOG.settled_picks()
    if settled_entries:
        with st.expander(f"Settled picks — {len(settled_entries)} done"):
            for entry in settled_entries[:30]:
                mark = "✅ Won" if entry.status == "won" else "❌ Lost"
                demo_tag = " · DEMO" if entry.mode == "demo" else ""
                played_note = (
                    f" · played by {', '.join(entry.played_by)}" if entry.played_by else ""
                )
                st.write(
                    f"{mark} — **{entry.label}** — {entry.event} · {entry.date}{demo_tag}{played_note}"
                )
            if len(settled_entries) > 30:
                st.caption(f"Showing the newest 30 of {len(settled_entries)} settled picks.")

    # ---------- Backup export ----------
    st.markdown("---")
    st.subheader("⬇️ Back up your records")
    st.write(
        "The app's files can be reset when the app rebuilds. Download a copy of your "
        "records now and then — these buttons are the safety net."
    )
    today_str = datetime.now().date().isoformat()
    pick_rows = PICK_LOG.rows()
    pick_csv = ""
    if pick_rows:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(pick_rows[0].keys()))
        writer.writeheader()
        writer.writerows(pick_rows)
        pick_csv = buf.getvalue()
    ledger_rows = [
        {
            "id": lp.id, "date": lp.date, "size": lp.size, "label": lp.label,
            "overall_probability": lp.overall_probability, "mode": lp.mode,
            "profile": lp.profile, "settled": lp.settled,
            "picks_won": lp.picks_won, "picks_lost": lp.picks_lost,
            "picks_pending": lp.picks_pending,
        }
        for lp in LEDGER.parlays
    ]
    ledger_csv = ""
    if ledger_rows:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(ledger_rows[0].keys()))
        writer.writeheader()
        writer.writerows(ledger_rows)
        ledger_csv = buf.getvalue()
    dl1, dl2 = st.columns(2)
    dl1.download_button(
        "⬇️ Pick log — JSON", data=PICK_LOG.to_json(),
        file_name=f"pick-log-{today_str}.json", mime="application/json",
        key="dl-picklog-json",
    )
    dl2.download_button(
        "⬇️ Pick log — CSV", data=pick_csv,
        file_name=f"pick-log-{today_str}.csv", mime="text/csv",
        key="dl-picklog-csv",
    )
    dl3, dl4 = st.columns(2)
    dl3.download_button(
        "⬇️ Parlay ledger — JSON",
        data=json.dumps([asdict(lp) for lp in LEDGER.parlays], indent=2),
        file_name=f"parlay-ledger-{today_str}.json", mime="application/json",
        key="dl-ledger-json",
    )
    dl4.download_button(
        "⬇️ Parlay ledger — CSV", data=ledger_csv,
        file_name=f"parlay-ledger-{today_str}.csv", mime="text/csv",
        key="dl-ledger-csv",
    )

# ================= SETUP =================
elif page == "Setup":
    st.title("⚙️ Setup")
    client = get_client()
    if client.has_key:
        st.success("✅ Live key is set (LILLY_ODDS_API_KEY). Lilly will fetch live props.")
    else:
        st.markdown(
            "<div class='demo-banner'>⚠️ NO KEY SET — APP IS IN DEMO MODE WITH SAMPLE DATA</div>",
            unsafe_allow_html=True,
        )
    st.subheader("How to get the free key — 5 steps")
    st.markdown(
        """
        1. Go to **https://the-odds-api.com** in your browser.
        2. Tap **Sign Up** and make a free account (email + password). No card needed.
        3. Open your dashboard — your API key is shown there. Copy it.
        4. On this PC, open the file `.streamlit/secrets.toml` in this folder and paste:
           `LILLY_ODDS_API_KEY = "paste-your-key-here"`
           (Or set it as a Windows environment variable with the same name.)
        5. Restart the app. The banner on the Dashboard will turn green and say LIVE PROPS.
        """
    )
    st.subheader("What the free key gives you")
    st.markdown(
        """
        - **500 requests a month, free.** It resets on the 1st of the month.
        - Live player props for NBA and NFL (NHL later) from US sportsbooks.
        - **Player props cost quota per game** — that is why this app caches them for 6 hours and only refreshes when you tap Refresh. Do not tap Refresh over and over.
        - **What it does NOT give you:** player game logs. The free game-log source (balldontlie) keeps those behind a paid tier, so until that is added, picks are **odds-only** and labelled that way. Lilly never invents a probability.
        """
    )
    if st.button("Check my key and quota"):
        result = client.list_events("NBA")
        if result.ok:
            st.success(f"Key works. {result.message} {result.requests_remaining}")
        else:
            st.error(result.message)
    st.subheader("Membership plan")
    st.write(
        "Plan only, not built yet: sell this as a $25/month membership ONLY after "
        "consistent picks are demonstrated in the ledger above. No paywall, no Stripe, "
        "no payments in this app."
    )
