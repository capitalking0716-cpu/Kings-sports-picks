"""Recent-form data (player game logs) — honest status.

UPDATE 2026-11-12: free game-log feeds DO exist now and are
wired in kings_picks/game_logs.py — nflverse for the NFL and
stats.nba.com (via nba_api) for the NBA. That module is the
live path. THIS module remains the older paid-balldontlie
option, kept for if a paid key is ever added.

Checked 2026-11-10 (about balldontlie specifically):

- balldontlie.io DOES have per-game player stats, but on the
  CURRENT API they are a PAID endpoint (ALL-STAR tier and up).
  The free key only opens /teams, /players and /games — no
  game logs, no season averages. There is no free game-log
  source that works right now. (Old blog posts saying it is
  keyless are out of date — the live API returns 401.)
- At the time there was no free NFL game-log API in use either
  (nflverse, found later, is a file feed, not an API — see
  game_logs.py).

So this module does ONE honest job: if Neko adds a paid
balldontlie key (BALLDONTLIE_API_KEY), it fetches a player's
last games and counts how many went over/under a line. If
there is no key, or the endpoint answers 401/403, it returns
None and says why — and the model then labels the pick
"odds-only, no recent-form data" instead of inventing a
probability.

Never invent form numbers. No key / no paid tier = no form data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

BASE_URL = "https://api.balldontlie.io/nba/v1"
ENV_KEY = "BALLDONTLIE_API_KEY"


@dataclass
class FormResult:
    player: str
    hits: int
    games: int
    values: list  # the stat value in each recent game, newest first


def get_balldontlie_key() -> str:
    key = os.environ.get(ENV_KEY, "").strip()
    if key:
        return key
    try:
        import streamlit as st

        return str(st.secrets.get(ENV_KEY, "")).strip()
    except Exception:
        return ""


# Which balldontlie stat field matches each of our prop labels (NBA only).
STAT_FIELD = {
    "Points": "pts",
    "Rebounds": "reb",
    "Assists": "ast",
    "Three-Pointers Made": "fg3m",
    "Points + Rebounds + Assists": None,  # sum of three fields
}


def fetch_recent_form(
    player_name: str,
    prop_label: str,
    side: str,
    line: float,
    n_games: int = 8,
    api_key: str = "",
    session: requests.Session | None = None,
) -> tuple[FormResult | None, str]:
    """Return (FormResult, note) or (None, reason-why-not).

    Only NBA, only when a balldontlie key is set. A 401 means the
    key is free-tier and the stats endpoint is paid — reported
    plainly, not treated as a bug.
    """
    key = api_key or get_balldontlie_key()
    if not key:
        return None, "No balldontlie key set — form data is off. Picks are odds-only."
    if prop_label not in STAT_FIELD:
        return None, f"No game-log mapping for the '{prop_label}' prop."

    session = session or requests.Session()
    headers = {"Authorization": key}
    try:
        resp = session.get(
            f"{BASE_URL}/players",
            params={"search": player_name, "per_page": 5},
            headers=headers,
            timeout=20,
        )
    except requests.RequestException as exc:
        return None, f"Could not reach balldontlie: {exc}"
    if resp.status_code in (401, 403):
        return None, (
            "balldontlie said this key cannot read player stats (401/403). "
            "The free key does not include game logs — that endpoint is paid. "
            "Picks stay odds-only."
        )
    if resp.status_code != 200:
        return None, f"balldontlie answered {resp.status_code}."
    players = resp.json().get("data", [])
    if not players:
        return None, f"Could not find '{player_name}' in balldontlie."
    player_id = players[0]["id"]

    try:
        resp = session.get(
            f"{BASE_URL}/stats",
            params={"player_ids[]": player_id, "per_page": n_games},
            headers=headers,
            timeout=20,
        )
    except requests.RequestException as exc:
        return None, f"Could not reach balldontlie: {exc}"
    if resp.status_code in (401, 403):
        return None, (
            "balldontlie said this key cannot read player stats (401/403). "
            "The free key does not include game logs — that endpoint is paid."
        )
    if resp.status_code != 200:
        return None, f"balldontlie answered {resp.status_code}."

    values = []
    for row in resp.json().get("data", []):
        field = STAT_FIELD[prop_label]
        if field is None:
            value = (row.get("pts") or 0) + (row.get("reb") or 0) + (row.get("ast") or 0)
        else:
            value = row.get(field)
        if value is not None:
            values.append(float(value))
    if not values:
        return None, f"No recent games found for {player_name}."
    hits = sum(1 for v in values if (v > line if side == "Over" else v < line))
    return FormResult(player=player_name, hits=hits, games=len(values), values=values), (
        f"{hits} of last {len(values)} games went {side.lower()} {line:g}."
    )
