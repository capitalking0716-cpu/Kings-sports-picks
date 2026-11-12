"""Players page helpers — pure prep for browsing raw player
stats, kept separate from Streamlit so it can be unit-tested.

The Players page shows the SAME game logs Lilly blends into
her picks (game_logs.py), for every player in the free feeds —
no algorithm run, no odds, no API key needed. These helpers
turn a parsed GameLogs into what the page renders: the picker
list and one section per tracked stat.

Honesty rules, same as everywhere in this app: only stats the
feed really has, averages computed from the real values, and
nothing invented for players or stats the feed doesn't cover.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from kings_picks.game_logs import (
    NBA_STAT_COLUMNS,
    NFL_ANYTIME_TD,
    NFL_STAT_COLUMNS,
    normalize_name,
)

# Canonical on-page order for each league's stat sections.
STAT_ORDER = {
    "NFL": list(NFL_STAT_COLUMNS) + [NFL_ANYTIME_TD],
    "NBA": list(NBA_STAT_COLUMNS),
}


def _title_part(part: str) -> str:
    titled = part.capitalize()
    # 'Mc' surnames keep their second capital (McCaffrey,
    # McLaurin) — the one systematic pattern in these feeds.
    if titled.startswith("Mc") and len(titled) > 2:
        return "Mc" + titled[2:].capitalize()
    return titled


def display_name(normalized: str) -> str:
    """A feed key back into something a human picks from a list.

    by_player keys are normalized ('patrick mahomes',
    'amon ra st brown'). Title-casing loses a little fidelity
    ('St.' shows as 'St', 'LeBron' as 'Lebron' — mid-word
    capitals can't be recovered from a normalized key), but
    every display name normalizes back to its exact key, so
    lookups round-trip safely.
    """
    return " ".join(_title_part(part) for part in (normalized or "").split())


def player_names(logs) -> list[str]:
    """Every player in the feed, as display names, A-Z — the
    Players page picker list."""
    return sorted(display_name(key) for key in logs.by_player if key)


def default_line(season_avg: float) -> float:
    """The line the 'test a line' box starts on: the season
    average rounded to the nearest 0.5 (half up), never below 0.
    Mirrors how book lines are actually quoted — always a .0 or
    a .5."""
    return max(0.0, math.floor(season_avg * 2 + 0.5) / 2)


@dataclass
class StatSection:
    """One tracked stat for one player, ready to render."""

    label: str
    values: list  # every logged game value, newest first
    last10: list  # values[:10] — what the chart shows
    games: int  # games logged for this stat
    season_avg: float
    last5_avg: float
    default_line: float


def stat_sections(logs, player: str) -> list[StatSection]:
    """The stat sections for one player, in the league's
    canonical order. Stats the feed has no games for are left
    out entirely — the page never shows an empty or invented
    section."""
    player_logs = logs.by_player.get(normalize_name(player)) or {}
    order = STAT_ORDER.get(logs.sport, [])
    labels = [label for label in order if player_logs.get(label)]
    # Any stat outside the canonical order (shouldn't happen)
    # still shows, after the known ones, rather than vanishing.
    labels += sorted(
        label for label in player_logs if label not in order and player_logs[label]
    )
    sections = []
    for label in labels:
        values = [float(v) for v in player_logs[label]]
        last5 = values[:5]
        season_avg = sum(values) / len(values)
        sections.append(
            StatSection(
                label=label,
                values=values,
                last10=values[:10],
                games=len(values),
                season_avg=season_avg,
                last5_avg=sum(last5) / len(last5),
                default_line=default_line(season_avg),
            )
        )
    return sections
