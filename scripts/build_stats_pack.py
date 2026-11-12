"""Build the bundled stats pack (kings_picks/bundled/stats_pack.json).

Run this on a machine where the free stats feeds answer
(Neko's PC / Shadow's box — NOT Streamlit Cloud, where the
NBA feed is blocked; that block is the whole reason the pack
exists). It reads the app's own disk caches
(data/cache/nba_gamelogs.json, nba_positions.json, and the
nflverse cache), trims every player's game values to the
last 15 per stat, adds each league's player->team map
(PlayerIndex for the NBA, the nflverse CSV for the NFL —
fetched fresh here, where fetching works), and writes one
lean JSON the app ships and reads as its backup stats
source (kings_picks/stats_pack.py).

The pack's "stats through" stamp is derived from the
source caches' fetch stamps: the newest game inside the
pack was played on or before that date. Re-run and re-push
to refresh the pack; the app never writes it.

Usage:
    python scripts/build_stats_pack.py [--cache-dir DIR]
        [--out FILE] [--max-games 15] [--no-teams]
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

MAX_GAMES_DEFAULT = 15


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _trim(logs: dict, max_games: int) -> dict:
    """Per player, per stat: the newest `max_games` values
    (the caches store newest first), values as plain floats."""
    return {
        player: {
            label: [float(v) for v in (values or [])[:max_games]]
            for label, values in (stats or {}).items()
            if values
        }
        for player, stats in (logs or {}).items()
        if stats
    }


def _find_nfl_cache(cache_dir: Path):
    """The newest nflverse cache payload in the dir (v3 name
    preferred, then v2), or None."""
    candidates = sorted(
        list(cache_dir.glob("nflverse_*_v3.json"))
        + list(cache_dir.glob("nflverse_*_v2.json")),
        reverse=True,
    )
    for path in candidates:
        payload = _read_json(path)
        if payload and payload.get("logs"):
            return payload
    return None


def _fetch_nba_teams(season: str) -> dict:
    """PlayerIndex -> {normalized name: team abbreviation},
    via game_logs' own parser. {} on any failure — the pack
    still ships; the roster guard just stands down for the
    NBA until a pack with teams is built."""
    try:
        from nba_api.stats.endpoints import PlayerIndex

        from kings_picks.game_logs import parse_nba_player_index_teams

        result = PlayerIndex(season=season, timeout=60)
        rows = result.get_data_frames()[0].to_dict("records")
        return parse_nba_player_index_teams(rows)
    except Exception as exc:  # noqa: BLE001 — build tool, report and move on
        print(f"  NBA teams: fetch failed ({exc}); shipping without them.")
        return {}


def _fetch_nfl_teams(season: int) -> dict:
    """The nflverse weekly CSV -> {normalized name: team
    abbreviation}, via game_logs' own parser. {} on failure."""
    try:
        import requests

        from kings_picks.game_logs import NFLVERSE_URL, parse_nflverse_teams

        resp = requests.get(NFLVERSE_URL.format(season=season), timeout=60)
        if resp.status_code != 200:
            print(f"  NFL teams: feed answered {resp.status_code}; shipping without them.")
            return {}
        return parse_nflverse_teams(resp.text)
    except Exception as exc:  # noqa: BLE001
        print(f"  NFL teams: fetch failed ({exc}); shipping without them.")
        return {}


def build_pack(cache_dir, out_path, max_games: int = MAX_GAMES_DEFAULT,
               fetch_teams: bool = True) -> dict:
    """Assemble the pack payload from the local caches and
    write it. Returns the payload (also the summary source
    for the CLI's printout)."""
    cache_dir = Path(cache_dir)
    out_path = Path(out_path)
    sports: dict[str, dict] = {}
    fetched_stamps: list[float] = []

    nba_cache = _read_json(cache_dir / "nba_gamelogs.json")
    if nba_cache and nba_cache.get("logs"):
        positions_payload = _read_json(cache_dir / "nba_positions.json") or {}
        teams_payload = _read_json(cache_dir / "nba_teams.json") or {}
        teams = teams_payload.get("teams") or {}
        if not teams and fetch_teams:
            teams = _fetch_nba_teams(str(nba_cache.get("season", "")))
        sports["NBA"] = {
            "season": nba_cache.get("season", ""),
            "logs": _trim(nba_cache["logs"], max_games),
            "positions": positions_payload.get("positions", {}) or {},
            "teams": teams,
        }
        fetched_stamps.append(float(nba_cache.get("fetched_at", 0) or 0))

    nfl_cache = _find_nfl_cache(cache_dir)
    if nfl_cache and nfl_cache.get("logs"):
        teams = nfl_cache.get("teams") or {}
        if not teams and fetch_teams:
            try:
                season = int(nfl_cache.get("season") or 0)
            except (TypeError, ValueError):
                season = 0
            if season:
                teams = _fetch_nfl_teams(season)
        sports["NFL"] = {
            "season": nfl_cache.get("season", ""),
            "logs": _trim(nfl_cache["logs"], max_games),
            "positions": nfl_cache.get("positions", {}) or {},
            "teams": teams,
        }
        fetched_stamps.append(float(nfl_cache.get("fetched_at", 0) or 0))

    stats_through = ""
    if fetched_stamps:
        newest = max(fetched_stamps)
        if newest > 0:
            stats_through = date.fromtimestamp(newest).isoformat()

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stats_through": stats_through,
        "max_games_per_stat": max_games,
        "sports": sports,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, separators=(",", ":")))
    return payload


def _summary(payload: dict, out_path: Path) -> str:
    lines = [
        f"Stats pack written: {out_path} "
        f"({out_path.stat().st_size / 1024:.0f} KB)",
        f"Stats through: {payload.get('stats_through') or '(unknown)'}",
    ]
    for sport, data in sorted((payload.get("sports") or {}).items()):
        lines.append(
            f"  {sport}: {len(data.get('logs') or {})} players, "
            f"{len(data.get('positions') or {})} positions, "
            f"{len(data.get('teams') or {})} teams, "
            f"season {data.get('season')}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cache_dir = REPO_ROOT / "data" / "cache"
    out_path = REPO_ROOT / "kings_picks" / "bundled" / "stats_pack.json"
    max_games = MAX_GAMES_DEFAULT
    fetch_teams = True
    it = iter(range(len(argv)))
    for i in it:
        arg = argv[i]
        if arg == "--cache-dir":
            cache_dir = Path(argv[i + 1])
            next(it)
        elif arg == "--out":
            out_path = Path(argv[i + 1])
            next(it)
        elif arg == "--max-games":
            max_games = int(argv[i + 1])
            next(it)
        elif arg == "--no-teams":
            fetch_teams = False
    payload = build_pack(cache_dir, out_path, max_games, fetch_teams)
    print(_summary(payload, out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
