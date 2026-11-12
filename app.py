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

from kings_picks import breakdown, demo_data, game_logs, learning, lilly, players, profiles, projection
from kings_picks.ledger import Ledger
from kings_picks.model import SCORING_VERSION, score_prop
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
LEARNING = learning.LearningEngine(
    f"{DATA_DIR}/learned.json", f"{DATA_DIR}/lilly-lessons.jsonl"
)


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


def _get_stats_store():
    """The ONE stats store for the whole app, held in session
    state.

    Browsing (the Players page) and the algorithm both read
    player stats through this single store — same disk cache
    (data/cache, shared with the odds cache), same parsed logs —
    so the two can never disagree about a player's form, and
    the free feeds download at most once per app session no
    matter how often the page reruns.
    """
    store = st.session_state.get("stats_store")
    if store is None:
        store = game_logs.StatsStore(f"{DATA_DIR}/cache")
        st.session_state["stats_store"] = store
    return store


def _form_lookup_for(mode: str):
    """The game-log lookup for one run, or None.

    Only LIVE runs ever touch the free stats feeds (nflverse
    for the NFL, stats.nba.com for the NBA) — demo runs must
    never acquire form data, and any feed failure just means
    this run stays odds-only. The lookup hands score_all the
    raw log values each projection is built from. The loaded
    logs are stashed in session state so the Props page
    breakdown can show the same form the scoring used. The
    feeds come through the shared store (_get_stats_store) —
    the exact same data the Players page browses.
    """
    if mode != "live":
        return None
    try:
        store = _get_stats_store()
        nfl = store.nfl_logs()
        nba = store.nba_logs()
    except Exception:
        return None
    st.session_state["nfl_logs"] = nfl
    st.session_state["nba_logs"] = nba
    if nfl is None and nba is None:
        return None
    return game_logs.make_form_lookup(nfl, nba)


def _learning_lookup_for(mode: str):
    """Lilly's learned corrections for one run, or None.

    Only LIVE runs learn or get corrected: the corrections are
    derived from settled live picks, and demo picks (sample
    data, invented probabilities) must never be blended with
    her real record in either direction. Refreshing first
    folds in every pick settled since the last run — this
    reads the on-disk pick log only; the Odds API is never
    touched by learning.
    """
    if mode != "live":
        return None
    LEARNING.refresh(PICK_LOG)
    return LEARNING.lookup


def score_all(props, form_lookup=None, learning_lookup=None):
    """Score props. Opposite-side odds are paired where both sides exist,
    so the bookmaker's cut is taken out. Where a real game-log feed
    matched the player, the projection model (kings_picks/projection.py)
    turns his log into a chance and the model blends it half-and-half
    with the odds, and the pick is labelled as having form data;
    everywhere else the pick is honestly labelled odds-only.

    A matched log with fewer games than the projection model needs
    (5) yields NO projection and the pick stays odds-only —
    deliberately NO fallback to the old raw hit-rate: the
    calibration proof (validation/projection-calibration-
    2026-11-12.md) showed that ingredient was worse than always
    saying 50% in the NBA, so it was retired, not kept as a spare.

    On live runs, Lilly's learning loop (kings_picks/learning.py)
    then applies any calibration correction her settled record
    has earned for the pick's bucket — after the blend, in the
    open, tagged on the pick.
    """
    by_line: dict = {}
    for p in props:
        by_line.setdefault((p.player, p.sport, p.prop, p.line), {})[p.side] = p.odds
    scored = []
    for p in props:
        pair = by_line.get((p.player, p.sport, p.prop, p.line), {})
        other_side = "Under" if p.side == "Over" else "Over"
        values = form_lookup(p) if form_lookup is not None else None
        learned = (
            learning_lookup(p.sport, p.prop, p.side)
            if learning_lookup is not None
            else None
        )
        learn_kwargs = (
            {
                "learning_delta": learned["delta"],
                "learning_tag": learned["tag"],
                "learning_note": learned["note"],
            }
            if learned
            else {}
        )
        form_probability = None
        proj = None
        if values:
            form_probability = projection.over_probability(values, p.line, p.side)
            if form_probability is not None:
                proj = projection.project_stat(values)
        if form_probability is not None and proj is not None:
            scored.append(
                score_prop(p, pair_odds=pair.get(other_side),
                           form_probability=form_probability,
                           projected_value=proj.expected,
                           projection_games=proj.games,
                           **learn_kwargs)
            )
        else:
            scored.append(score_prop(p, pair_odds=pair.get(other_side), **learn_kwargs))
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
    ["Dashboard", "Props", "Players", "💜 Lilly", "Results / Ledger", "Setup"],
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
if "raw_props" not in st.session_state:
    st.session_state["raw_props"] = []


def run_algorithm(force_refresh: bool = False):
    props, mode, note = load_props(force_refresh=force_refresh)
    form_lookup = _form_lookup_for(mode)
    scored = score_all(
        props, form_lookup=form_lookup, learning_lookup=_learning_lookup_for(mode)
    )
    st.session_state["raw_props"] = props
    parlays, build_notes = build_parlays(scored)
    st.session_state["parlays"] = parlays
    st.session_state["scored"] = scored
    st.session_state["mode"] = mode
    st.session_state["note"] = note + (" | " + " | ".join(build_notes) if build_notes else "")
    if parlays:
        LEDGER.record_run(parlays, mode, profile=active_profile, model=SCORING_VERSION)
        PICK_LOG.record_run(parlays, mode, model=SCORING_VERSION)
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
        scored = st.session_state["scored"]
        parlays = st.session_state["parlays"]

        # ----- Today's board at a glance -----
        st.subheader("📊 Today's board at a glance")
        st.write(
            f"{len(scored)} props on the board, grouped by Lilly's chance. "
            "Most props on any board live in the middle bands — that is normal."
        )
        band_rows = [
            {"Chance band": label, "Props": count}
            for label, count in breakdown.chance_bands(scored)
        ]
        st.bar_chart(band_rows, x="Chance band", y="Props")

        # ----- Player breakdown -----
        st.markdown("---")
        st.subheader("🔍 Player breakdown")
        st.write(
            "Pick a player and Lilly breaks each of his props down: the price, "
            "the bookmaker's cut, where it ranks today, and — where a real "
            "game-log feed covers him — his recent form against the line."
        )
        players = sorted({p.player for p in scored})
        chosen_player = st.selectbox("Pick a player", players, key="bd_player")
        pairs = breakdown.pair_map(st.session_state["raw_props"] or [])
        player_props = sorted(
            (p for p in scored if p.player == chosen_player),
            key=lambda p: p.probability,
            reverse=True,
        )
        for pick in player_props:
            logs = None
            if pick.sport == "NFL":
                logs = st.session_state.get("nfl_logs")
            elif pick.sport == "NBA":
                logs = st.session_state.get("nba_logs")
            form = logs.form(pick.player, pick.prop, pick.side, pick.line) if logs else None
            pair = pairs.get((pick.player, pick.sport, pick.prop, pick.line), {})
            other_side = "Under" if pick.side == "Over" else "Over"
            card = breakdown.build_breakdown(
                pick, scored, parlays,
                pair_odds=pair.get(other_side), form=form,
            )
            verdict, verdict_reason = lilly.verdict_for_pick(
                pick.probability, pick.has_form_data, pick.edge
            )
            with st.expander(
                f"{pick.label} — Lilly {card.lilly_chance:.0%} · {card.strength}",
                expanded=(pick is player_props[0]),
            ):
                st.markdown(
                    f"**{pick.event}** &nbsp;|&nbsp; {pick.sport} &nbsp;|&nbsp; "
                    f"Line: **{pick.prop} {pick.side} {pick.line:g}**"
                )
                m1, m2, m3 = st.columns(3)
                m1.metric("Best odds", f"{pick.odds:+d}", help=pick.bookmaker or None)
                m2.metric("Lilly's chance", f"{card.lilly_chance:.0%}")
                m3.metric(
                    "Gap vs book sticker",
                    f"{card.edge_points:+.1f} pts",
                    help="Lilly's chance minus the book's sticker chance. "
                         "Mostly the bookmaker's cut coming out — see below.",
                )
                if pick.bookmaker:
                    st.caption(f"Best price at {pick.bookmaker}.")
                st.bar_chart(
                    breakdown.chance_chart_rows(card.lilly_chance, card.book_chance),
                    x="who", y="chance_pct",
                )
                st.markdown("**Why Lilly sees it this way:**")
                for reason in card.reasons:
                    st.markdown(f"- {reason}")
                st.markdown(f"**Lilly's verdict: {verdict}** — {verdict_reason}")

                # ----- Recent form (only when a real feed matched) -----
                if form is not None and form.games > 0:
                    st.markdown(f"#### Recent form — {form.source_label}")
                    st.write(
                        f"Went **{pick.side.lower()} {pick.line:g}** in "
                        f"**{form.hits} of his last {form.games} games** · "
                        f"Last 5 average: **{form.last5_avg:.1f}** · "
                        f"Season average: **{form.season_avg:.1f}** · "
                        f"The line: **{pick.line:g}**"
                    )
                    form_rows = breakdown.form_chart_rows(form.values)
                    try:
                        import altair as _alt
                        import pandas as _pd

                        _df = _pd.DataFrame(form_rows)
                        _bars = _alt.Chart(_df).mark_bar(color="#7b2fbe").encode(
                            x=_alt.X("game:N", sort=None, title="Recent games"),
                            y=_alt.Y("value:Q", title=pick.prop),
                        )
                        _line = _alt.Chart(
                            _pd.DataFrame({"line": [pick.line]})
                        ).mark_rule(color="#e74c3c", strokeDash=[6, 4]).encode(y="line:Q")
                        st.altair_chart(_bars + _line, use_container_width=True)
                        st.caption("Red dashed line = the line this pick has to beat.")
                    except Exception:
                        st.bar_chart(form_rows, x="game", y="value")
                        st.caption(f"The line this pick has to beat: {pick.line:g}.")
                elif pick.source == "live":
                    st.caption(
                        "No recent-form feed matched this player yet — this pick "
                        "is odds-only, and Lilly labels it that way rather than "
                        "inventing stats."
                    )

        # ----- Full list -----
        st.markdown("---")
        st.subheader("All props")
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

# ================= PLAYERS =================
elif page == "Players":
    st.title("🏃 Players")
    st.write(
        "Every player in the stats feeds, with his real game logs — "
        "**no algorithm run needed, no odds, no API key**. These are the "
        "exact same numbers Lilly blends into her picks; here you can "
        "check any player against any line yourself, any time."
    )
    league = st.radio("League", ["NFL", "NBA"], horizontal=True, key="players_league")
    logs = None
    try:
        with st.spinner(
            f"Loading {league} player stats… the first load downloads the "
            "feed (a few seconds); after that it's cached."
        ):
            store = _get_stats_store()
            logs = store.nfl_logs() if league == "NFL" else store.nba_logs()
    except Exception:
        logs = None
    if logs is None:
        st.markdown(
            f"<div class='warn-box'><b>The {league} stats feed didn't answer "
            "this time.</b><br>Nothing is broken — the feed is free and "
            "sometimes slow or down. Try again in a bit, or check the other "
            "league above. Lilly never fills a gap with invented stats, so "
            "this page shows nothing rather than something fake.</div>",
            unsafe_allow_html=True,
        )
    else:
        season_note = f" · {logs.season_label} season" if logs.season_label else ""
        st.caption(f"Player stats: {logs.source_label}{season_note}")
        st.caption(
            "Stats are independent of the odds and the algorithm — the "
            "algorithm reads from this same feed when it scores picks."
        )
        names_all = players.player_names(logs)
        groups = players.position_groups(logs)
        if groups:
            counts = {label: len(members) for label, members in groups}
            group_options = [players.ALL_POSITIONS] + [
                label for label, _members in groups
            ]
            group_choice = st.selectbox(
                "Position",
                group_options,
                format_func=lambda label: (
                    f"{label} ({len(names_all)})"
                    if label == players.ALL_POSITIONS
                    else f"{label} ({counts[label]})"
                ),
                key=f"players_position_{league}",
            )
            names = players.names_for_group(logs, groups, group_choice)
        else:
            st.caption(
                f"Position info isn't available from the {league} feed "
                "right now, so every player is in one list."
            )
            names = names_all
        chosen = st.selectbox(
            f"Pick a player — {len(names)} in the {league} feed",
            names,
            key=f"players_pick_{league}",
        )
        position = logs.position_of(chosen)
        if position:
            st.markdown(f"## {chosen} · {position}")
        else:
            st.markdown(f"## {chosen}")
        sections = players.stat_sections(logs, chosen)
        if not sections:
            st.write("This feed has no game stats logged for him yet.")
        for section in sections:
            st.markdown("---")
            st.subheader(section.label)
            s1, s2, s3 = st.columns(3)
            s1.metric("Games logged", section.games)
            s2.metric("Season average", f"{section.season_avg:.1f}")
            s3.metric("Last 5 average", f"{section.last5_avg:.1f}")
            line = st.number_input(
                f"Test a line — {section.label}",
                min_value=0.0,
                step=0.5,
                value=float(section.default_line),
                key=(
                    f"line-{league}-"
                    f"{game_logs.normalize_name(chosen)}-{section.label}"
                ),
            )
            check = game_logs.summarize(section.values, "Over", float(line))
            if check is not None:
                st.write(
                    f"**Over {line:g} in {check.hits} of his last "
                    f"{check.games} games.**"
                )
            chart_rows = breakdown.form_chart_rows(section.last10)
            try:
                import altair as _alt
                import pandas as _pd

                _df = _pd.DataFrame(chart_rows)
                _bars = _alt.Chart(_df).mark_bar(color="#7b2fbe").encode(
                    x=_alt.X("game:N", sort=None, title="Recent games"),
                    y=_alt.Y("value:Q", title=section.label),
                )
                _rule = _alt.Chart(
                    _pd.DataFrame({"line": [float(line)]})
                ).mark_rule(color="#e74c3c", strokeDash=[6, 4]).encode(y="line:Q")
                st.altair_chart(_bars + _rule, use_container_width=True)
                st.caption(
                    "Last 10 games, oldest to newest. Red dashed line = "
                    "the line you're testing."
                )
            except Exception:
                st.bar_chart(chart_rows, x="game", y="value")
                st.caption(
                    f"Last 10 games, oldest to newest. The line you're "
                    f"testing: {line:g}."
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
    st.caption(
        "Scoring eras: picks tagged **v2** were scored with Lilly's projection "
        "model; older **v1** picks used the retired hit-rate blend. The tag is "
        "on every pick below and rides along in the backup exports."
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
                    f"<b style='font-size:17px'>{entry.label}</b> — {entry.sport}{demo_tag} · {entry.model}<br>"
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
                learned_note = f" · 📚 learned: {entry.learning}" if entry.learning else ""
                st.write(
                    f"{mark} — **{entry.label}** — {entry.event} · {entry.date}{demo_tag} · {entry.model}{played_note}{learned_note}"
                )
            if len(settled_entries) > 30:
                st.caption(f"Showing the newest 30 of {len(settled_entries)} settled picks.")

    # ---------- What Lilly has learned ----------
    st.markdown("---")
    st.subheader("🧠 What Lilly has learned")
    st.write(
        "Lilly studies her own settled record — real picks only, never demo — "
        "and recalibrates where her stated chances have been running hot or "
        "cold. A bucket needs **30 settled picks** before any correction "
        "switches on, corrections are shrunk by sample size and capped at "
        "10 points, and every corrected pick is tagged in the log. This is "
        "recalibration, not a promise: she still never guarantees a pick."
    )
    learn_state = LEARNING.refresh(PICK_LOG)
    learn_buckets = learn_state.get("buckets", {})
    active = learn_state.get("active_corrections", {})
    settled_live = learn_state.get("settled_live", 0)
    if not settled_live:
        st.write(
            "📭 **Nothing learned yet — no live picks have settled.** "
            "Settle real picks above after the games and she starts learning "
            "from them. Demo picks never teach her anything."
        )
    else:
        if active:
            st.write(f"**{len(active)} correction(s) switched on right now:**")
            for key in sorted(active):
                delta = active[key]
                arrow = "trims" if delta < 0 else "bumps up"
                st.write(
                    f"- **{learn_buckets[key]['label']}** — Lilly {arrow} these "
                    f"by about {abs(delta) * 100:.0f} points "
                    f"({learn_buckets[key]['settled']} settled picks)."
                )
        else:
            closest = max(
                learn_buckets.values(), key=lambda b: b["settled"], default=None
            )
            if closest is not None:
                st.write(
                    "📭 **No corrections switched on yet** — not enough settled "
                    f"picks in any one bucket. Her fullest bucket is "
                    f"**{closest['label']}** with {closest['settled']} of "
                    f"{learning.MIN_SETTLED} settled picks needed — "
                    f"{closest['needed']} more to go there."
                )
        brier = learn_state.get("brier", {})
        if brier.get("as_shown") is not None:
            st.caption(
                f"Her settled-pick Brier score: **{brier['as_shown']:.3f}** "
                f"as shown vs {brier['uncorrected']:.3f} without learning "
                "(lower is better — always saying 50% scores 0.250). "
                "That gap is how the learning itself gets graded."
            )
        with st.expander(f"Bucket by bucket — {len(learn_buckets)} buckets tracked"):
            for key in sorted(learn_buckets):
                b = learn_buckets[key]
                corr = (
                    f"{b['correction'] * 100:+.0f} points"
                    if b["active"]
                    else f"off — needs {b['needed']} more settled"
                )
                st.write(
                    f"**{b['label']}** — {b['settled']} settled · she said "
                    f"{b['mean_stated']:.0%} on average · won "
                    f"{(b['hit_rate'] or 0):.0%} · correction: {corr}"
                )
    lilly_lessons = LEARNING.lessons()
    if lilly_lessons:
        st.markdown("#### 📖 Lilly's lessons")
        for lesson in reversed(lilly_lessons[-10:]):
            st.write(f"- {lesson['text']} *({lesson['date']})*")

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
            "profile": lp.profile, "model": lp.model, "settled": lp.settled,
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
    dl5, dl6 = st.columns(2)
    dl5.download_button(
        "⬇️ What Lilly has learned — JSON", data=LEARNING.state_json(),
        file_name=f"lilly-learned-{today_str}.json", mime="application/json",
        key="dl-learned-json",
    )
    dl6.download_button(
        "⬇️ Lilly's lessons — JSONL", data=LEARNING.lessons_text(),
        file_name=f"lilly-lessons-{today_str}.jsonl", mime="application/json",
        key="dl-lessons-jsonl",
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
        - **What the key does NOT give you:** player game logs — the API is odds only. Lilly gets recent form separately, from free feeds: nflverse for the NFL, and stats.nba.com for the NBA when that feed answers. Players no feed covers stay **odds-only** and are labelled that way. Lilly never invents a probability.
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
