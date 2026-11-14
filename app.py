"""Kings Sports Books Picks — Neko's own sports picks platform.

Local only. Paper tracking only. No real bets, no money moved.
Lilly never promises wins.
"""

import csv
import html
import io
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo

    _DETROIT = ZoneInfo("America/Detroit")
except Exception:  # pragma: no cover — zoneinfo is stdlib on supported Pythons
    _DETROIT = None

import streamlit as st

from kings_picks import breakdown, game_logs, king_style, learning, lilly, picks_export, players, profiles, projection, roster, self_test, slate_store, slip, stats_pack
from kings_picks.ledger import Ledger
from kings_picks.model import SCORING_VERSION, decimal_to_american, score_prop
from kings_picks.odds_api import OddsApiClient, market_key_for_label
from kings_picks.parlays import build_parlays
from kings_picks.pick_log import PickLog, entry_stamp, now_stamp

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
    .live-banner {
        background-color: #12351f; border: 2px solid #2ecc71;
        color: #b6f5cd; padding: 14px; border-radius: 12px;
        font-size: 20px; font-weight: bold; text-align: center;
    }
    .warn-box {
        background-color: #2a0a0a; border: 2px solid #e74c3c;
        color: #ffd2cc; padding: 14px; border-radius: 12px; font-size: 18px;
    }
    .gameday {
        background: linear-gradient(135deg, #14091f, #2a1152);
        border: 2px solid #d4af37; border-radius: 12px;
        padding: 14px 16px; margin-bottom: 14px; font-size: 16px;
    }
    .gameday .gd-title {
        color: #d4af37; font-size: 21px; font-weight: bold;
        letter-spacing: 1px; text-align: center; margin-bottom: 6px;
    }
    .gameday .gd-sec {
        color: #d4af37; font-weight: bold; letter-spacing: 1px;
        margin-top: 8px; font-size: 15px;
    }
    .gameday .gd-game { margin: 3px 0; font-size: 16px; }
    .gameday .gd-lock { margin-top: 8px; font-size: 17px; font-weight: bold; }
    .gameday .gd-line { margin-top: 6px; font-size: 15px; }
    .pick-card {
        background-color: #1c1026; border: 1px solid #7b2fbe;
        border-radius: 12px; padding: 12px; margin-bottom: 10px;
    }
    .bet-ticket {
        background-color: #f6f1e3; color: #1a1426;
        border: 2px solid #d4af37; border-radius: 10px;
        padding: 14px 16px; margin-bottom: 14px;
        font-family: "Courier New", Courier, monospace;
    }
    .bet-ticket .ticket-head {
        font-size: 20px; font-weight: bold; text-align: center;
        letter-spacing: 1px; color: #1a1426 !important;
    }
    .bet-ticket .ticket-sub { text-align: center; font-size: 14px; }
    .bet-ticket .ticket-perf { border-top: 2px dashed #8a7f5c; margin: 10px 0; }
    .bet-ticket .ticket-leg { margin: 9px 0; font-size: 15px; }
    .bet-ticket .ticket-foot { font-size: 15px; margin: 3px 0; }
    .bet-ticket .ticket-terms { font-size: 12.5px; font-style: italic; margin-top: 8px; }
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
SLIP_BOOK = slip.SlipBook(f"{DATA_DIR}/slips.json")
SLATE_STORE = slate_store.SlateStore(f"{DATA_DIR}/current_slate.json")


def get_client() -> OddsApiClient:
    return OddsApiClient(cache_dir=f"{DATA_DIR}/cache")


# The whole no-key state, in one plain sentence. There is
# no demo mode and there are no sample picks: without a key
# the app simply has no props to show.
SETUP_NOTE = "Add your Odds API key in Streamlit Secrets to pull live props."


def load_props(force_refresh: bool = False):
    """Live props, or an honest empty result.

    Returns (props, mode, note) with mode "live", "no_key"
    or "failed". No key -> no props and the setup note. A
    failed fetch -> no props and the failure note. Lilly
    never falls back to invented/sample data.
    """
    client = get_client()
    if not client.has_key:
        return [], "no_key", f"No Odds API key set. {SETUP_NOTE}"
    result = client.fetch_props("NBA", force_refresh=force_refresh)
    props = list(result.props)
    nfl = client.fetch_props("NFL", force_refresh=force_refresh)
    props.extend(nfl.props)
    if not props:
        message = result.message if not result.ok else nfl.message
        return [], "failed", (
            f"LIVE DATA FAILED: {message} "
            "No picks were built from that pull."
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


def _load_logs():
    """(nfl logs, nba logs, pack stamp) for this session.

    The stats fallback order, in one place: per league, the
    live free feed first (through the shared store); a league
    the live feed didn't answer for falls back to the bundled
    stats pack (kings_picks/stats_pack.py) — the copy of the
    logs shipped with the app, which is what keeps form data
    alive where a feed is blocked (stats.nba.com answers
    nothing from Streamlit Cloud). A league neither source
    covers stays None, and its picks stay odds-only. The
    stamp is the pack's "stats through" date whenever either
    league is riding on the pack, else "".
    """
    try:
        store = _get_stats_store()
        live_nfl = store.nfl_logs()
        live_nba = store.nba_logs()
    except Exception:
        live_nfl, live_nba = None, None
    return stats_pack.merge_logs(live_nfl, live_nba)


def _form_lookup_for(mode: str):
    """The game-log lookup for one run, or None.

    Only LIVE runs ever touch the stats sources (the free
    feeds, with the bundled stats pack as their fallback) —
    a run that isn't live must never acquire form data, and
    any failure just means this run stays odds-only. The
    lookup hands score_all the raw log values each
    projection is built from. The loaded logs are stashed in
    session state so the Props page breakdown can show the
    same form the scoring used. The feeds come through the
    shared store (_get_stats_store) — the exact same data
    the Players page browses.
    """
    if mode != "live":
        return None
    nfl, nba, stamp = _load_logs()
    st.session_state["nfl_logs"] = nfl
    st.session_state["nba_logs"] = nba
    st.session_state["stats_stamp"] = stamp
    if nfl is None and nba is None:
        return None
    return game_logs.make_form_lookup(nfl, nba)


def _learning_lookup_for(mode: str):
    """Lilly's learned corrections for one run, or None.

    Only LIVE runs learn or get corrected: the corrections
    are derived from settled live picks, and a run that
    isn't live must never be blended with her real record
    in either direction. Refreshing first folds in every
    pick settled since the last run — this reads the
    on-disk pick log only; the Odds API is never touched
    by learning.
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


def _render_drop_options(pick, active_profile):
    """The dropped-lines expander for one My Slip board row.

    Neko's style: play the Over at a lower line — less
    payout, bigger chance (Unders mirror: a raised line).
    FETCH DISCIPLINE: alternate markets are fetched ONLY
    here, only after the user taps THIS pick open, only for
    this pick's event + market, through the disk-cached
    client — so an expansion costs 1 request the first time
    and nothing while the 6-hour cache is warm. Nothing is
    fetched for picks nobody expands.
    """
    open_key = f"slip_drop_open_{active_profile}_{pick.key}"
    if not st.session_state.get(open_key):
        if st.button(
            "📉 Dropped lines — play it safer",
            key=f"slip-drop-btn-{active_profile}-{pick.key}",
        ):
            st.session_state[open_key] = True
            st.rerun()
        return
    if st.button(
        "📉 Hide dropped lines",
        key=f"slip-drop-btn-{active_profile}-{pick.key}",
    ):
        st.session_state[open_key] = False
        st.rerun()
        return

    st.caption(slip.DROP_NOTE)
    mode = st.session_state["mode"]
    market_key = market_key_for_label(pick.prop)
    alt_prices = []
    if market_key is None:
        st.info(
            "No alternate market exists for this prop, so there are "
            "no dropped lines to price for it."
        )
    else:
        client = get_client()
        event_id = getattr(pick, "event_id", "") or client.resolve_event_id(
            pick.sport, pick.event, getattr(pick, "commence_time", "") or ""
        )
        result = client.fetch_alternates(pick.sport, event_id or "", market_key)
        if not result.ok:
            st.info(result.message)
        else:
            note = result.message
            if result.requests_remaining and not result.from_cache:
                note += f" | {result.requests_remaining}"
            st.caption(note)
            alt_prices = result.prices
            if not any(p.player == pick.player for p in alt_prices):
                st.info(
                    f"No book offers a dropped line for {pick.player} — "
                    f"{pick.prop} in this game."
                )
    form_lookup = _form_lookup_for(mode)
    values = form_lookup(pick) if form_lookup is not None else None
    learning_lookup = _learning_lookup_for(mode)
    learned = (
        learning_lookup(pick.sport, pick.prop, pick.side)
        if learning_lookup is not None
        else None
    )
    options = slip.build_drop_options(
        pick, alt_prices, form_values=values, learning=learned
    )
    keys_now = SLIP_BOOK.keys(active_profile)
    families_now = SLIP_BOOK.families(active_profile)
    for option in options:
        if option.playable:
            leg = option.to_leg()
            st.markdown(
                f"<div class='pick-card'>"
                f"<b style='font-size:18px'>{pick.player} — {pick.prop} {option.label}</b><br>"
                f"Odds: {option.odds:+d} &nbsp;|&nbsp; Lilly's chance: "
                f"<b>{option.probability:.0%}</b> (standard {option.base_line:g}: "
                f"{option.standard_probability:.0%}) &nbsp;|&nbsp; {option.bookmaker}"
                f"</div>",
                unsafe_allow_html=True,
            )
            if leg is not None and leg.key in keys_now:
                st.caption("✅ On your slip")
            elif leg is not None and leg.family in families_now:
                st.caption(
                    "Another version of this pick is on your slip — "
                    "remove it from the slip above to switch versions."
                )
            elif st.button(
                "➕ Add dropped line to slip",
                key=f"slip-drop-add-{active_profile}-{pick.key}-{option.line:g}",
            ):
                SLIP_BOOK.add(active_profile, leg)
                st.rerun()
        elif option.probability is not None:
            st.markdown(
                f"**{pick.player} — {pick.prop} {option.label}** — Lilly's "
                f"chance: **{option.probability:.0%}** (standard "
                f"{option.base_line:g}: {option.standard_probability:.0%})"
                f"<br><i>{option.note}</i>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f"**{pick.player} — {pick.prop} {option.label}**"
                f"<br><i>{option.note}</i>",
                unsafe_allow_html=True,
            )


def _ticket_number(seed_text: str) -> str:
    """A stable ticket number from the ticket's own content:
    the same picks always print the same number — after a
    reload from the stored slate included — and different
    picks print a different one."""
    import hashlib

    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    return f"KSB-{digest[:8].upper()}"


def _form_test_html(pick) -> str:
    """The per-leg form-test line on a bet ticket: the grade
    the pull's test-before-picking pass gave this leg at
    selection time (kings_picks/self_test.py), with its
    last-10 record. Legs that were never graded (hand-built
    slip legs, slates stored before the test existed) print
    nothing — the ticket never invents a grade."""
    verdict = getattr(pick, "form_verdict", "") or ""
    if not verdict:
        return ""
    display = self_test.VERDICT_DISPLAY.get(verdict, verdict)
    if verdict == self_test.NO_DATA:
        detail = "no game log covers this pick"
    elif getattr(pick, "form_record", ""):
        detail = f"last-10 form {pick.form_record}"
    else:
        detail = ""
    text = f"🧪 Form test: <b>{display}</b>"
    if detail:
        text += f" — {detail}"
    return text + "<br>"


def render_parlay(parlay, mode: str):
    """One parlay as an authentic bet ticket.

    Laid out like a sportsbook slip: shop header, ticket
    number, the pull's timestamp, one row per leg (with the
    King legs' dropped/raised notes), dashed perforations,
    and a footer with the totals and Lilly's honest line as
    the terms. The legs themselves are SIMPLE (Neko's order,
    2026-11-12): the pick, its odds and chance, the odds-only
    flag when it applies, the form-test line, the King note —
    no explanation paragraph and no verdict paragraph. He
    set the parameters; the ticket doesn't re-teach them.
    (The why/verdict prose still lives on the Props page,
    where he goes to dig in.)
    """
    style_tag = (
        "👑 KING'S STYLE"
        if getattr(parlay, "style", "standard") == "king"
        else "STANDARD LINES"
    )
    pulled = slate_store.format_pulled_at(st.session_state.get("pulled_at", ""))
    pulled_line = f"Pulled {pulled}" if pulled else "Pulled this session"
    number = _ticket_number(
        "|".join(
            [parlay.label, getattr(parlay, "style", "standard")]
            + [f"{p.key}:{p.odds}" for p in parlay.picks]
        )
    )
    legs_html = ""
    for pick in parlay.picks:
        # The odds-only flag prints only when it applies —
        # a form-backed leg needs no flag line at all.
        odds_only = (
            "⚠️ Odds-only — no recent-form data<br>"
            if not pick.has_form_data
            else ""
        )
        # King-style annotations (kings_picks/king_style.py):
        # a dropped leg names the line it came from, and a
        # kept leg says plainly why it is still standard.
        # Standard picks carry neither attribute and render
        # exactly as they always have.
        title = pick.label
        base_line = getattr(pick, "base_line", None)
        if base_line is not None and float(base_line) != float(pick.line):
            verb = "dropped" if pick.side == "Over" else "raised"
            title = f"{pick.label} ({verb} from {float(base_line):g})"
        king_note = getattr(pick, "king_note", "") or ""
        king_html = f"<i>👑 {king_note}</i><br>" if king_note else ""
        legs_html += (
            f"<div class='ticket-leg'>"
            f"<b>{title}</b><br>"
            f"Odds: {pick.odds:+d} &nbsp;|&nbsp; Chance: <b>{pick.probability:.0%}</b> &nbsp;|&nbsp; "
            f"{pick.bookmaker or ''} &nbsp;|&nbsp; {pick.event}<br>"
            f"{odds_only}"
            f"{_form_test_html(pick)}"
            f"{king_html}"
            f"</div>"
        )
    try:
        combined_american = f"{decimal_to_american(parlay.decimal_payout):+d}"
    except ValueError:
        combined_american = "—"
    st.markdown(
        f"<div class='bet-ticket'>"
        f"<div class='ticket-head'>👑 KINGS SPORTS BOOKS PICKS</div>"
        f"<div class='ticket-sub'><b>{parlay.label}</b> — wins about "
        f"{parlay.overall_probability:.1%} of the time</div>"
        f"<div class='ticket-sub'>Ticket #{number} &nbsp;·&nbsp; {pulled_line}</div>"
        f"<div class='ticket-sub'>{style_tag} &nbsp;·&nbsp; Model {SCORING_VERSION}</div>"
        f"<div class='ticket-perf'></div>"
        f"{legs_html}"
        f"<div class='ticket-perf'></div>"
        f"<div class='ticket-foot'><b>True overall chance: "
        f"{parlay.overall_probability:.2%}</b> (about 1 in {parlay.one_in:.0f})</div>"
        f"<div class='ticket-foot'>Combined odds: <b>{combined_american}</b> "
        f"(decimal {parlay.decimal_payout:.2f}) &nbsp;|&nbsp; "
        f"If all legs win, $1 staked returns ${parlay.decimal_payout:.2f}</div>"
        f"<div class='ticket-foot'>Expected value per $1: "
        f"<b>${parlay.expected_value_per_dollar:+.2f}</b></div>"
        f"<div class='ticket-terms'>{parlay.plain_summary()}</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _render_slip_ticket(legs, summary, profile):
    """A hand-built My Slip slip, printed as a bet ticket —
    the same ticket language as Lilly's parlay tickets, and
    every number the old metric row showed: combined odds
    (American + decimal), the true overall chance with its
    1-in-X, the stake, the payout, the profit, and the
    expected value (Lilly's chance × the payout − the stake).
    """
    number = _ticket_number(
        "slip|" + profile + "|" + "|".join(f"{leg.key}:{leg.odds}" for leg in legs)
    )
    pulled = slate_store.format_pulled_at(st.session_state.get("pulled_at", ""))
    pulled_line = f"Board pulled {pulled}" if pulled else "Built from today's board"
    legs_html = ""
    for leg in legs:
        form_tag = "✅ Recent form included" if leg.has_form_data else "⚠️ Odds-only — no recent-form data"
        legs_html += (
            f"<div class='ticket-leg'>"
            f"<b>{leg.label}</b> — {leg.sport}<br>"
            f"Odds: {leg.odds:+d} &nbsp;|&nbsp; Lilly's chance: <b>{leg.probability:.0%}</b> &nbsp;|&nbsp; "
            f"{leg.bookmaker or ''} &nbsp;|&nbsp; {leg.event}<br>"
            f"{form_tag}<br>"
            f"{_form_test_html(leg)}"
            f"</div>"
        )
    american = (
        f"{summary['american_odds']:+d}"
        if summary["american_odds"] is not None
        else "—"
    )
    one_in = f"{summary['one_in']:.0f}" if summary["one_in"] else "—"
    st.markdown(
        f"<div class='bet-ticket'>"
        f"<div class='ticket-head'>👑 KINGS SPORTS BOOKS PICKS</div>"
        f"<div class='ticket-sub'><b>MY SLIP</b> — "
        f"{profiles.PROFILE_LABELS[profile]}'s ticket</div>"
        f"<div class='ticket-sub'>Ticket #{number} &nbsp;·&nbsp; {pulled_line}</div>"
        f"<div class='ticket-sub'>HAND-BUILT &nbsp;·&nbsp; Model {SCORING_VERSION}</div>"
        f"<div class='ticket-perf'></div>"
        f"{legs_html}"
        f"<div class='ticket-perf'></div>"
        f"<div class='ticket-foot'>Combined odds: <b>{american}</b> "
        f"(decimal {summary['decimal_odds']:.2f} — what $1 returns if every leg wins)</div>"
        f"<div class='ticket-foot'><b>True overall chance: "
        f"{summary['overall_probability']:.1%}</b> (about 1 in {one_in}) — "
        f"Lilly's estimate: her chance for each leg, multiplied together</div>"
        f"<div class='ticket-foot'>Bet amount: <b>${summary['stake']:.2f}</b> &nbsp;|&nbsp; "
        f"If it hits, you get back <b>${summary['payout']:.2f}</b> &nbsp;|&nbsp; "
        f"Profit: <b>${summary['profit']:.2f}</b></div>"
        f"<div class='ticket-foot'>Expected value at this amount: "
        f"<b>${summary['expected_value']:+.2f}</b> "
        f"(her chance × the payout − your stake)</div>"
        f"<div class='ticket-terms'>Paper tracking only — Lilly doesn't place "
        f"bets. Odds are as shown when each leg was added; the real book's "
        f"price at bet time is the one that counts.</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _picks_download_teams() -> dict:
    """{sport: {name key: team text}} for the plain-English
    download's player (Team) labels — from the same loaded
    logs the self-test reads (the live feeds when they
    answered, the bundled stats pack as fallback; zero Odds
    API quota either way)."""
    logs = _self_test_logs()
    return {
        sport: (getattr(logs.get(sport), "teams", None) or {})
        for sport in ("NFL", "NBA")
    }


def _render_picks_download():
    """⬇️ Download my picks (plain English) — the current
    stored slate as one text file for Neko's phone
    (kings_picks/picks_export.py). Built from the slate the
    app already holds: no new pull, no rescore, zero quota,
    and the same for both profiles — the picks are shared.
    With no slate yet, a short note stands where the button
    would be — never an empty file."""
    no_slate_note = (
        "📥 Once Lilly has built your picks, a button appears here "
        "to download the whole pull as a plain-English file you "
        "can keep on your phone."
    )
    parlays = st.session_state.get("parlays", [])
    king_parlays = st.session_state.get("king_parlays", [])
    if not parlays and not king_parlays:
        st.caption(no_slate_note)
        return
    text = picks_export.build_picks_text(
        pulled_at=st.session_state.get("pulled_at", ""),
        parlays=parlays,
        king_parlays=king_parlays,
        teams=_picks_download_teams(),
    )
    if not text:
        st.caption(no_slate_note)
        return
    pulled_at = st.session_state.get("pulled_at", "")
    date_part = (
        pulled_at[:10] if pulled_at else datetime.now().date().isoformat()
    )
    st.download_button(
        "⬇️ Download my picks (plain English)",
        data=text,
        file_name=f"lillys-picks-{date_part}.txt",
        mime="text/plain",
        key="dl-picks-plain",
    )


def _self_test_logs() -> dict:
    """The game logs the self-test grades against: the ones the
    last live run already loaded (stashed in session state by
    _form_lookup_for), else the shared stats store — the same
    free feeds, the same disk cache, ZERO Odds API quota.
    Fail-soft like everything form-shaped: any problem means
    no logs, and every grade simply reads NO DATA."""
    nfl = st.session_state.get("nfl_logs")
    nba = st.session_state.get("nba_logs")
    if nfl is None and nba is None:
        try:
            # Read-only: unlike _form_lookup_for this does NOT
            # stash what it loaded — the self-test is a report,
            # and a fallback load must never shadow the logs a
            # later run (or a later pull) brings in.
            nfl, nba, _stamp = _load_logs()
        except Exception:
            return {}
    return {"NFL": nfl, "NBA": nba}


def render_self_test():
    """🧪 Lilly tests her picks — the Dashboard self-test.

    Grades the CURRENT slate from the pick log (her latest
    logged generation: standard and 👑 King-style legs at the
    lines they played) against each player's recent game-log
    form. Read-only reporting: it never changes a pick, a
    chance, or the learning loop — she tests herself and shows
    the marks alongside her picks (kings_picks/self_test.py).
    """
    slate = self_test.latest_slate(PICK_LOG.entries)
    with st.expander("🧪 Lilly tests her picks", expanded=False):
        st.caption(
            "Lilly grades her own picks against each player's recent "
            "game-log form: in his last 10 games, how often would this "
            "exact pick have hit? This checks past-game form only — it "
            "is not a promise about tonight, and it never changes her "
            "picks. It reports alongside them."
        )
        if not slate:
            st.info(
                "No picks logged yet — run the algorithm and Lilly "
                "will grade her picks here."
            )
            return
        grades = self_test.grade_slate(PICK_LOG.entries, _self_test_logs())
        counts = self_test.verdict_counts(grades)
        summary = " · ".join(
            f"{counts[tag]} {tag}"
            for tag in (
                self_test.STRONG,
                self_test.LEAN,
                self_test.MIXED,
                self_test.SHAKY,
                self_test.NO_DATA,
            )
            if tag in counts
        )
        st.write(
            f"**{len(grades)} picks graded** (logged {slate[0].date}) — {summary}"
        )
        for grade in grades:
            king_tag = " &nbsp;👑 King's style" if grade.style == "king" else ""
            chance = (
                f"{grade.stated_chance:.0%}"
                if grade.stated_chance is not None
                else "—"
            )
            full = (
                f"{grade.full_rate:.0%} over the full log"
                if grade.full_rate is not None
                else "full log: no decided games"
            )
            st.markdown(
                f"<div class='pick-card'>"
                f"<b style='font-size:18px'>{grade.label}</b>{king_tag}<br>"
                f"Lilly's chance: <b>{chance}</b> &nbsp;|&nbsp; "
                f"Last-10 form: <b>{grade.form_label}</b> &nbsp;|&nbsp; "
                f"{full} &nbsp;|&nbsp; <b>{grade.verdict_display}</b>"
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
    ["Dashboard", "Props", "🎟️ My Slip", "Players", "💜 Lilly", "Results / Ledger", "Setup"],
    index=0,
)
st.sidebar.markdown("---")
st.sidebar.markdown(
    "Lilly never promises wins. A pick is a chance, never a sure thing. "
    "Sports betting in Michigan is 21+."
)

# ---- Shared state: the stored slate ----
# A pull PERSISTS (kings_picks/slate_store.py): the slate —
# board, Lilly's parlays and the 👑 King parlays, the picks
# exactly as pulled — is saved to data/current_slate.json,
# and a fresh app load renders the STORED slate with no
# Odds API call and no rescore. It stays until Neko pulls
# fresh odds; only a pull replaces it.
def _apply_stored_slate(payload) -> bool:
    """Put a stored slate into session state for rendering.
    False (nothing changed) when there is no usable slate."""
    restored = slate_store.restore_slate(payload)
    if restored is None:
        return False
    st.session_state["parlays"] = restored["parlays"]
    st.session_state["king_parlays"] = restored["king_parlays"]
    st.session_state["king_info"] = restored["king_info"]
    st.session_state["scored"] = restored["scored"]
    st.session_state["raw_props"] = restored["raw_props"]
    st.session_state["mode"] = "live"
    st.session_state["note"] = restored["note"]
    st.session_state["pulled_at"] = restored["pulled_at"]
    return True


if "parlays" not in st.session_state:
    st.session_state["parlays"] = []
    st.session_state["mode"] = "no_key"
    st.session_state["note"] = ""
if "king_parlays" not in st.session_state:
    st.session_state["king_parlays"] = []
    st.session_state["king_info"] = {}
if "scored" not in st.session_state:
    st.session_state["scored"] = []
if "raw_props" not in st.session_state:
    st.session_state["raw_props"] = []
if "pulled_at" not in st.session_state:
    # First run of a fresh session: render the stored slate,
    # if one was ever pulled. No fetch, no scoring — the
    # store IS the slate until the next pull.
    st.session_state["pulled_at"] = ""
    _apply_stored_slate(SLATE_STORE.load())


def _grade_scored_legs(scored) -> dict:
    """Test before picking (Neko's order, 2026-11-12).

    Grade every scored candidate leg at the line it would
    play, against the player's game-log form
    (kings_picks/self_test.py — zero Odds API quota, the
    logs are already loaded for the run). The grade is
    stamped on the pick (it is the grade the bet ticket
    shows) and the {pick key: verdict} map is returned for
    the parlay builder, which ranks form-backed legs first
    and keeps SHAKY legs as a last resort
    (kings_picks/parlays.py). Fail-soft: no logs means
    every grade reads NO DATA, which the builder treats as
    neutral — a pull never dies because a feed didn't load.
    """
    logs_by_sport = _self_test_logs()
    verdicts: dict = {}
    for pick in scored:
        grade = self_test.grade_pick(
            pick.player,
            pick.prop,
            pick.side,
            pick.line,
            logs_by_sport.get(pick.sport),
        )
        pick.form_verdict = grade.verdict
        pick.form_record = grade.form_label
        verdicts[pick.key] = grade.verdict
    return verdicts


def _grade_king_legs(king_parlays) -> None:
    """Grade the 👑 King legs at the lines they PLAY.

    A King leg's own line IS its played line — the dropped
    line for a dropped leg, the standard line for a kept
    one — so grading each finished King leg at leg.line is
    grading it exactly where it plays. The grades stamp the
    King tickets the same way the standard tickets are
    stamped. Selection itself already happened in the
    standard build (the King set mirrors it leg for leg);
    these grades are the record of how each dropped leg's
    form reads at its dropped line.
    """
    logs_by_sport = _self_test_logs()
    for parlay in king_parlays:
        for leg in parlay.picks:
            grade = self_test.grade_pick(
                leg.player,
                leg.prop,
                leg.side,
                leg.line,
                logs_by_sport.get(leg.sport),
            )
            leg.form_verdict = grade.verdict
            leg.form_record = grade.form_label


def _roster_index():
    """The roster index for this run's logs: who plays for
    whom, from the loaded game logs (live feeds or the
    bundled pack). NBA logs loaded live carry no teams —
    those ride a separate PlayerIndex side-feed with its own
    daily cache — so attach them when the store can provide
    them; logs that already carry teams (the pack's) are
    left alone."""
    nfl = st.session_state.get("nfl_logs")
    nba = st.session_state.get("nba_logs")
    if nba is not None and not getattr(nba, "teams", None):
        try:
            teams = _get_stats_store().nba_teams()
        except Exception:
            teams = {}
        if teams:
            nba.teams = dict(teams)
    return roster.build_index(nfl, nba)


def _apply_roster_guard(props):
    """Validate a fresh pull's legs against the rosters
    (kings_picks/roster.py) BEFORE anything is scored.

    Returns (props, note_fragment). A leg naming a player
    who isn't in the listed game is dropped here — never
    scored, never picked, never logged — and the fragment
    reports how many. When the index didn't load, everything
    is kept and the fragment says the check didn't run,
    rather than the slate silently going unchecked.
    """
    try:
        kept, dropped, active = roster.guard_props(props, _roster_index())
    except Exception:
        return props, ""
    if not active:
        return props, (
            "🛡️ Roster check unavailable this pull — the player "
            "index didn't load, so legs weren't checked against rosters."
        )
    if dropped:
        return kept, (
            f"🛡️ {dropped} glitched leg(s) thrown out — player not "
            "in the listed game."
        )
    return kept, ""


def run_algorithm(force_refresh: bool = False):
    props, mode, note = load_props(force_refresh=force_refresh)
    if mode != "live":
        # No key, or the live fetch failed: Lilly never
        # invents picks. A stored slate, if one exists, stays
        # on screen exactly as pulled — this pull replaces
        # nothing, records nothing, stores nothing.
        if _apply_stored_slate(SLATE_STORE.load()):
            st.session_state["note"] += (
                f" | ⚠️ That pull didn't come back live — {note} "
                "Your stored slate above is still the one showing, "
                "exactly as pulled."
            )
            return st.session_state["parlays"], "live", st.session_state["note"]
        st.session_state["parlays"] = []
        st.session_state["king_parlays"] = []
        st.session_state["king_info"] = {}
        st.session_state["scored"] = []
        st.session_state["raw_props"] = []
        st.session_state["mode"] = mode
        st.session_state["note"] = note
        st.session_state["pulled_at"] = ""
        return [], mode, note

    form_lookup = _form_lookup_for(mode)
    learning_lookup = _learning_lookup_for(mode)
    # Roster guard: a glitched leg — a player listed under a
    # game he isn't in — dies here, before scoring. It is
    # never picked and never logged.
    props, guard_note = _apply_roster_guard(props)
    if guard_note:
        note = f"{note} | {guard_note}"
    scored = score_all(
        props, form_lookup=form_lookup, learning_lookup=learning_lookup
    )
    st.session_state["raw_props"] = props
    # Test before picking: grade every candidate leg
    # against its game-log form FIRST, then build — the
    # builder ranks form-backed legs ahead and keeps
    # form-weak legs as a last resort.
    form_verdicts = _grade_scored_legs(scored)
    parlays, build_notes = build_parlays(scored, form_verdicts=form_verdicts)
    st.session_state["parlays"] = parlays
    st.session_state["scored"] = scored
    st.session_state["mode"] = mode
    st.session_state["note"] = note + (" | " + " | ".join(build_notes) if build_notes else "")

    # 👑 King's style: the same parlays rebuilt at Neko's
    # dropped lines (kings_picks/king_style.py). Live pulls
    # only — dropped lines are priced from the books' real
    # alternate markets, and Lilly does not invent them.
    # QUOTA: alternates are fetched ONLY for the unique
    # (event, market) pairs inside the final parlays,
    # through the disk-cached client — never slate-wide.
    king_parlays: list = []
    king_info: dict = {}
    if mode == "live" and parlays:
        client = get_client()

        def _king_fetch(sport, event_id, market_key, event="", commence_time=""):
            eid = event_id or client.resolve_event_id(sport, event, commence_time) or ""
            if not eid:
                return []
            result = client.fetch_alternates(sport, eid, market_key)
            return list(result.prices) if result.ok else []

        king_parlays, king_info = king_style.build_king_parlays(
            parlays,
            _king_fetch,
            form_lookup=form_lookup,
            learning_lookup=learning_lookup,
        )
        # King legs graded at the lines they play (the
        # dropped lines), stamped for their tickets.
        _grade_king_legs(king_parlays)
        st.session_state["note"] += (
            f" | 👑 King style: {king_info['legs_dropped']} legs dropped, "
            f"{king_info['legs_kept_standard']} kept standard — dropped lines "
            f"priced for {king_info['pairs_fetched']} event/market pair(s) "
            f"(cached, so re-runs are free)."
        )
    st.session_state["king_parlays"] = king_parlays
    st.session_state["king_info"] = king_info

    # One instant stamps the whole pull: the pick-log
    # entries and the stored slate carry the same time, so
    # the record and the screen always agree on when these
    # picks were pulled.
    pulled_at = now_stamp()
    if parlays:
        LEDGER.record_run(parlays, mode, profile=active_profile, model=SCORING_VERSION)
        # Standard legs first: a King leg kept at the standard
        # line IS the standard pick (same log key), so it is
        # logged once, as standard; dropped King legs log
        # separately, tagged style="king", source="lilly".
        PICK_LOG.record_run(
            parlays + king_parlays, mode, model=SCORING_VERSION,
            created_at=pulled_at,
        )

    # Persist the pulled slate (board + both parlay sets,
    # exactly as rendered) — later loads render THIS, with
    # no API call and no rescore, until the next pull.
    SLATE_STORE.save(
        slate_store.build_payload(
            pulled_at=pulled_at,
            note=st.session_state["note"],
            raw_props=props,
            scored=scored,
            parlays=parlays,
            king_parlays=king_parlays,
            king_info=king_info,
        )
    )
    st.session_state["pulled_at"] = pulled_at
    return parlays, mode, note


# ================= GAME-DAY HEADER =================
# Presentation only: everything below reads the stored
# slate / session as it already stands — no API calls, no
# rescore, nothing recomputed but the clock.

def _parse_commence(value):
    """A book commence_time (ISO) -> aware Detroit datetime,
    or None when the slate carries no usable time."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_DETROIT) if _DETROIT else parsed
    return parsed.astimezone(_DETROIT) if _DETROIT else parsed


def _gameday_games():
    """Tonight's games from the stored slate's board: unique
    (sport, event) pairs with their real start times,
    earliest first (games without a listed time go last)."""
    seen: dict = {}
    for prop in st.session_state.get("raw_props", []):
        event = getattr(prop, "event", "")
        if event:
            seen.setdefault(
                (prop.sport, event), getattr(prop, "commence_time", "") or ""
            )
    games = [
        {"sport": sport, "event": event, "starts": _parse_commence(commence)}
        for (sport, event), commence in seen.items()
    ]
    games.sort(
        key=lambda g: (
            g["starts"].timestamp() if g["starts"] else float("inf"),
            g["event"],
        )
    )
    return games


def _render_gameday_header():
    """The sportsbook game-day banner at the top of the
    Dashboard: today's date, tonight's games with start
    times, the countdown to the first lock, the stored-slate
    stamp, and the Odds API quota when the last pull's note
    reported it."""
    now = datetime.now(_DETROIT) if _DETROIT else datetime.now()
    lines = [
        "<div class='gd-title'>🏟️ GAME DAY — "
        f"{now.strftime('%A, %B')} {now.day}, {now.year}</div>"
    ]
    games = _gameday_games()
    if games:
        lines.append("<div class='gd-sec'>TONIGHT'S GAMES</div>")
        for game in games:
            when = (
                game["starts"].strftime("%-I:%M %p ET")
                if game["starts"]
                else "start time not listed"
            )
            lines.append(
                f"<div class='gd-game'>{game['sport']} · "
                f"{html.escape(game['event'])} — {when}</div>"
            )
        future = [g["starts"] for g in games if g["starts"] and g["starts"] > now]
        if future:
            total_minutes = int((future[0] - now).total_seconds() // 60)
            hours, minutes = divmod(total_minutes, 60)
            clock = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
            lines.append(
                f"<div class='gd-lock'>⏰ First lock in {clock} — "
                f"{future[0].strftime('%-I:%M %p ET')}</div>"
            )
        elif any(g["starts"] for g in games):
            lines.append(
                "<div class='gd-lock'>🔴 First lock has passed — "
                "games are underway or done.</div>"
            )
    pulled_at = st.session_state.get("pulled_at", "")
    if pulled_at:
        when = slate_store.format_pulled_at(pulled_at)
        if when:
            lines.append(
                f"<div class='gd-line'>📌 Stored slate — pulled {when}</div>"
            )
    note = st.session_state.get("note", "") or ""
    quota = re.search(r"\d+ of 500 requests left[^|]*", note)
    if quota:
        lines.append(
            f"<div class='gd-line'>🎟️ Odds API quota: {quota.group(0).strip()}</div>"
        )
    st.markdown(
        "<div class='gameday'>" + "".join(lines) + "</div>",
        unsafe_allow_html=True,
    )


# ================= DASHBOARD =================
if page == "Dashboard":
    st.title("👑 Kings Sports Books Picks")
    st.subheader("Today's parlays — built by Lilly")
    _render_gameday_header()
    _pack_stamp = stats_pack.load_pack()[2]
    if _pack_stamp:
        st.caption(
            f"📦 Backup stats pack aboard — player stats through "
            f"{_pack_stamp}. Lilly falls back to it whenever a live "
            "stats feed can't be reached."
        )

    if st.session_state["parlays"]:
        st.markdown(
            "<div class='live-banner'>✅ LIVE PROPS — from The Odds API</div>",
            unsafe_allow_html=True,
        )
    if not get_client().has_key:
        st.info(f"🔑 {SETUP_NOTE}")
    pulled_at = st.session_state.get("pulled_at", "")
    if pulled_at:
        pulled_when = slate_store.format_pulled_at(pulled_at)
        pulled_text = f" — pulled {pulled_when}" if pulled_when else ""
        st.caption(
            f"📌 **Stored slate{pulled_text}.** These picks are saved in "
            "the app: they stay exactly as pulled until you tap "
            "**🔄 Pull fresh odds**, and only a fresh pull replaces them."
        )
    if st.session_state["note"]:
        st.write(st.session_state["note"])

    if st.button("▶ Run the algorithm — build today's parlays"):
        with st.spinner("Lilly is building your parlays..."):
            run_algorithm(force_refresh=False)
        st.success("Done. Parlays saved to the ledger.")

    if st.button("🔄 Pull fresh odds (uses API quota — tap only when you want fresh lines)"):
        with st.spinner("Fetching fresh live props..."):
            run_algorithm(force_refresh=True)
        st.success("Refreshed.")

    if not st.session_state["parlays"]:
        st.markdown(
            "<div class='warn-box'>No parlays built yet. Tap <b>Run the algorithm</b> above "
            "to pull today's live props. The pull is stored — the picks stay on this "
            "screen until you pull fresh odds.</div>",
            unsafe_allow_html=True,
        )
    else:
        # 👑 King's style leads: Neko's dropped-line rebuild
        # of the same parlays, priced from the books' real
        # alternate markets on the live pull that built them.
        st.subheader("👑 King's style — dropped lines")
        king_parlays = st.session_state.get("king_parlays", [])
        if king_parlays:
            st.write(
                "Built King's way: take the number he's known for, drop it down "
                "a few, play the safer side of it — small money adds up. Every "
                "leg is priced at the books' real alternate price. A leg whose "
                "dropped line no book offers keeps its standard line, and the "
                "card says so plainly."
            )
            for king_parlay in king_parlays:
                render_parlay(king_parlay, st.session_state["mode"])
                st.markdown("---")
        st.subheader("🎟️ Tonight's Slate — Lilly's standard lines")
        for parlay in st.session_state["parlays"]:
            render_parlay(parlay, st.session_state["mode"])
            st.markdown("---")

    # ⬇️ The whole pull — odds and picks — as one plain-
    # English file for his phone. Sits under the tickets it
    # downloads; with no slate it is a short note instead.
    _render_picks_download()

    # 🧪 Lilly tests her picks: her latest logged slate,
    # graded against recent game-log form. Rendered from the
    # pick log (not session state), so the grades are here
    # even on a fresh app load — and an empty log just shows
    # the "no picks yet" line inside the expander.
    render_self_test()

# ================= PROPS =================
elif page == "Props":
    st.title("📋 Props")
    st.write("Every prop Lilly can see right now, with its chance and why it was picked.")
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

# ================= MY SLIP =================
elif page == "🎟️ My Slip":
    st.title("🎟️ My Slip — build your own picks")
    st.write(
        "Your picks, your call. Add selections from today's board and the "
        "slip does the maths out loud: the combined odds, Lilly's true "
        "overall chance for the whole slip, and what your amount would "
        "pay if every leg hits."
    )
    st.info(slip.CALCULATOR_NOTE)
    if not get_client().has_key:
        st.info(f"🔑 {SETUP_NOTE}")
    flash = st.session_state.pop("slip_flash", None)
    if flash:
        st.success(flash)

    # ----- Your slip so far (this profile's own slip) -----
    st.subheader(f"🧾 {profiles.PROFILE_LABELS[active_profile]}'s slip")
    my_legs = SLIP_BOOK.legs(active_profile)
    stake_key = f"slip_stake_{active_profile}"
    if stake_key not in st.session_state:
        st.session_state[stake_key] = 10.0
    if not my_legs:
        st.write(
            "Your slip is empty. Add selections from the board below — "
            "each profile keeps its own slip."
        )
    else:
        # Quick amounts first (they set the stake the number
        # input below then shows), then the adjustable amount.
        st.markdown("**Bet amount** — tap a quick amount or type your own:")
        chip_cols = st.columns(5)
        for chip_col, amount in zip(chip_cols, (1, 5, 10, 25, 50)):
            if chip_col.button(f"${amount}", key=f"slip-chip-{active_profile}-{amount}"):
                st.session_state[stake_key] = float(amount)
                st.rerun()
        stake = st.number_input(
            "Bet amount ($)",
            min_value=0.0,
            step=1.0,
            format="%.2f",
            key=stake_key,
        )
        summary = slip.slip_summary(my_legs, float(stake))
        # The slip itself, printed as a bet ticket — every
        # number the old metrics showed is in its footer.
        _render_slip_ticket(my_legs, summary, active_profile)
        for leg in my_legs:
            if st.button(
                f"❌ Remove — {leg.label}",
                key=f"slip-remove-{active_profile}-{leg.key}",
            ):
                SLIP_BOOK.remove(active_profile, leg.key)
                st.rerun()
        save_col, clear_col = st.columns(2)
        if clear_col.button("🧹 Clear slip", key="slip-clear"):
            SLIP_BOOK.clear(active_profile)
            st.rerun()
        if save_col.button("💾 Save slip to my record", key="slip-save"):
            added, already = PICK_LOG.record_manual(
                my_legs,
                mode="live",
                profile=active_profile,
                created_at=now_stamp(),
            )
            SLIP_BOOK.clear(active_profile)
            note = (
                f"Saved {len(added)} new pick(s) to your record"
                + (f" — {len(already)} leg(s) were already on it (Lilly logged them too)." if already else ".")
                + " Settle them Won / Lost on Results / Ledger after the games."
            )
            st.session_state["slip_flash"] = note
            st.rerun()

    # ----- Today's board: the same props the Props page shows -----
    st.markdown("---")
    st.subheader("📋 Today's board")
    scored = st.session_state["scored"]
    if not scored:
        st.write(
            "No board loaded yet. Loading it uses the same cached slate "
            "fetch as the Dashboard — no extra quota beyond that fetch, "
            "and none at all while the cache is warm."
        )
        if st.button("📥 Load today's board"):
            with st.spinner("Loading today's board..."):
                run_algorithm(force_refresh=False)
            st.rerun()
    else:
        if st.session_state["note"]:
            st.caption(st.session_state["note"])
        search = st.text_input("Search a player", key="slip_search", placeholder="Start typing a name…")
        sport_choice = st.radio(
            "League", ["All", "NBA", "NFL"], horizontal=True, key="slip_sport"
        )
        on_slip = SLIP_BOOK.keys(active_profile)
        families_on_slip = SLIP_BOOK.families(active_profile)
        board = [
            p for p in scored
            if (sport_choice == "All" or p.sport == sport_choice)
            and (not search or search.lower() in p.player.lower())
        ]
        st.write(f"{len(board)} selections on the board — best chance first, the way Lilly ranks them.")
        for pick in board:
            form_tag = "✅ Recent form included" if pick.has_form_data else "⚠️ Odds-only — no recent-form data"
            st.markdown(
                f"<div class='pick-card'>"
                f"<b style='font-size:18px'>{pick.label}</b> — {pick.sport}<br>"
                f"Odds: {pick.odds:+d} &nbsp;|&nbsp; Lilly's chance: <b>{pick.probability:.0%}</b> &nbsp;|&nbsp; "
                f"{pick.bookmaker or ''} &nbsp;|&nbsp; {pick.event}<br>"
                f"{form_tag}<br>"
                f"<i>{pick.why}</i>"
                f"</div>",
                unsafe_allow_html=True,
            )
            if pick.key in on_slip:
                if st.button("✅ On your slip — tap to remove", key=f"slip-toggle-{active_profile}-{pick.key}"):
                    SLIP_BOOK.remove(active_profile, pick.key)
                    st.rerun()
            elif (pick.player, pick.sport, pick.prop, float(pick.line)) in families_on_slip:
                st.caption(
                    "📉 A dropped-line version of this pick is on your slip — "
                    "remove it from the slip above to change versions."
                )
            else:
                if st.button("➕ Add to slip", key=f"slip-toggle-{active_profile}-{pick.key}"):
                    SLIP_BOOK.add(active_profile, slip.SlipLeg.from_prop(pick))
                    st.rerun()
            if slip.drop_steps_for(pick.prop):
                _render_drop_options(pick, active_profile)

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
    pack_stamp = ""
    if logs is None:
        # The live feed didn't answer — the bundled stats
        # pack is this page's backup copy, the same fallback
        # the scoring path uses.
        _pack_nfl, _pack_nba, pack_stamp = stats_pack.load_pack()
        logs = _pack_nfl if league == "NFL" else _pack_nba
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
        if logs.source_label == stats_pack.SOURCE_LABEL and pack_stamp:
            st.caption(
                f"📦 The live feed didn't answer, so this is the bundled "
                f"stats pack — stats through {pack_stamp}."
            )
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
        "stats, so after the games you tap Won or Lost here. Lilly never guesses a result. "
        "Picks built by hand on **My Slip** land here too, tagged ✋ Your pick — settle "
        "them the same way. Legs from the 👑 King's style parlays are tagged "
        "**👑 King's style** — her picks at dropped lines; they are her record, "
        "they count and teach exactly like her standard-line picks."
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
    if pstats["manual"]["total"]:
        st.caption(
            f"Picks built by hand on My Slip are tagged ✋ Your pick and kept out of "
            f"Lilly's record above: {pstats['manual']['total']} logged, "
            f"{pstats['manual']['settled']} settled. They are the builder's own record — "
            f"she does not learn from them. They do count in a profile's played record."
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
                manual_tag = " · ✋ Your pick" if getattr(entry, "source", "lilly") == "manual" else ""
                king_tag = " · 👑 King's style" if getattr(entry, "style", "standard") == "king" else ""
                i_played = active_profile in entry.played_by
                st.markdown(
                    f"<div class='pick-card'>"
                    f"<b style='font-size:17px'>{entry.label}</b> — {entry.sport}{manual_tag}{king_tag} · {entry.model}<br>"
                    f"{entry.event} &nbsp;|&nbsp; Odds: {entry.odds:+d} &nbsp;|&nbsp; "
                    f"Chance: <b>{entry.probability:.0%}</b> &nbsp;|&nbsp; "
                    f"Pulled {entry_stamp(entry)}"
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
                manual_tag = " · ✋ Your pick" if getattr(entry, "source", "lilly") == "manual" else ""
                king_tag = " · 👑 King's style" if getattr(entry, "style", "standard") == "king" else ""
                played_note = (
                    f" · played by {', '.join(entry.played_by)}" if entry.played_by else ""
                )
                learned_note = f" · 📚 learned: {entry.learning}" if entry.learning else ""
                st.write(
                    f"{mark} — **{entry.label}** — {entry.event} · {entry.date}{manual_tag}{king_tag} · {entry.model}{played_note}{learned_note} · pulled {entry_stamp(entry)}"
                )
            if len(settled_entries) > 30:
                st.caption(f"Showing the newest 30 of {len(settled_entries)} settled picks.")

    # ---------- What Lilly has learned ----------
    st.markdown("---")
    st.subheader("🧠 What Lilly has learned")
    st.write(
        "Lilly studies her own settled record — her live picks only — "
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
            "from them."
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
        st.info(f"🔑 No key set yet. {SETUP_NOTE}")
    st.subheader("How to get the free key — 5 steps")
    st.markdown(
        """
        1. Go to **https://the-odds-api.com** in your browser.
        2. Tap **Sign Up** and make a free account (email + password). No card needed.
        3. Open your dashboard — your API key is shown there. Copy it.
        4. In this app's **Streamlit Cloud** page, open **Settings → Secrets** and add:
           `LILLY_ODDS_API_KEY = "paste-your-key-here"`
           (on the same screen as your `KING_PIN` / `KOHEN_PIN` lines).
        5. Reboot the app. The banner on the Dashboard will turn green and say LIVE PROPS.
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
