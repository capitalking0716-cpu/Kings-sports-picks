"""The bundled stats pack — Lilly's backup copy of the game
logs, shipped WITH the app.

Why this exists (2026-11-12): the live NBA game-log feed
(stats.nba.com via nba_api) answers from Neko's PC but
returns NOTHING from Streamlit Cloud, so a Cloud pull ran
entirely odds-only — no form data, no form grades, no
dropped lines. The fix is not a different live feed; it is
a copy of the logs bundled into the repo, built from the
local caches by scripts/build_stats_pack.py, refreshed
whenever that script is re-run and pushed.

Fallback order everywhere stats are read:
  live feed -> this pack -> NO DATA (honest, as always).
The pack never outranks a live feed that answered; it only
fills a league the live feed didn't answer for.

The pack is LEAN on purpose: per player, per stat, only the
last 15 games (the projection reads the full list for its
season average, so a packed player's season average is
really a last-15 average — a smaller evidence base, stated
plainly here; the form grade and the last-5/last-10 reads
are exact). It carries positions and teams too, so the
Players page and the roster guard work from it as well.

`stats_through` is the pack's freshness stamp: the date the
source caches were fetched (the newest game inside was
played on or before that date). The app shows it wherever
the pack is in play, so a stale pack never poses as fresh.
"""

from __future__ import annotations

import json
from pathlib import Path

from kings_picks.game_logs import GameLogs

PACK_PATH = Path(__file__).resolve().parent / "bundled" / "stats_pack.json"
SOURCE_LABEL = "bundled stats pack"

_cache: tuple | None = None


def format_stamp(iso_date: str) -> str:
    """"2026-11-12" -> "Nov 12, 2026" ("" stays "")."""
    if not iso_date:
        return ""
    try:
        from datetime import date

        parsed = date.fromisoformat(str(iso_date)[:10])
    except ValueError:
        return str(iso_date)
    return parsed.strftime("%b %d, %Y").replace(" 0", " ")


def _logs_from(payload_sport: dict, sport: str, stamp: str) -> GameLogs | None:
    logs = (payload_sport or {}).get("logs") or {}
    if not logs:
        return None
    return GameLogs(
        sport=sport,
        by_player=logs,
        source_label=SOURCE_LABEL,
        season_label=str((payload_sport or {}).get("season", "") or ""),
        positions=(payload_sport or {}).get("positions", {}) or {},
        teams=(payload_sport or {}).get("teams", {}) or {},
    )


def load_pack(path=None):
    """(nfl GameLogs | None, nba GameLogs | None, stamp).

    Fail-soft like every stats source in this app: a missing
    or unreadable pack is simply no pack — (None, None, "") —
    and the app runs on the live feeds alone. The stamp is
    the human "stats through ..." line, "" when the pack
    carries none. Loaded once per process; the file is small
    and static between deploys.
    """
    global _cache
    pack_file = Path(path) if path is not None else PACK_PATH
    if path is None and _cache is not None:
        return _cache
    result: tuple = (None, None, "")
    try:
        payload = json.loads(pack_file.read_text())
        stamp = format_stamp(payload.get("stats_through", ""))
        sports = payload.get("sports", {}) or {}
        result = (
            _logs_from(sports.get("NFL"), "NFL", stamp),
            _logs_from(sports.get("NBA"), "NBA", stamp),
            stamp,
        )
    except Exception:
        result = (None, None, "")
    if path is None:
        _cache = result
    return result


def merge_logs(live_nfl, live_nba):
    """The fallback order, in one place: per league, the live
    feed's logs when they answered, else the bundled pack's,
    else None. Returns (nfl, nba, stamp) — the stamp is the
    pack's whenever either league is riding on the pack, so
    the app can label what it is showing."""
    pack_nfl, pack_nba, stamp = load_pack()
    nfl = live_nfl if live_nfl is not None else pack_nfl
    nba = live_nba if live_nba is not None else pack_nba
    used_pack = (live_nfl is None and nfl is not None) or (
        live_nba is None and nba is not None
    )
    return nfl, nba, (stamp if used_pack else "")
