#!/usr/bin/env python
"""Calibration proof for Lilly's projection model (v2).

Run OUTSIDE pytest (it uses the live free feeds via
game_logs.StatsStore):

    ../vaulttec/.venv/bin/python validation/run_calibration.py

WHAT IT DOES
Walk every player-game in the real game logs (NFL nflverse,
NBA stats.nba.com). For each game, build a prediction using
ONLY the games before it (strictly no look-ahead), for both:

  v2 — the new projection model (kings_picks/projection.py)
  v1 — the old ingredient: the empirical hit-rate of the
       player's prior games against the line (hits in his
       last 10 prior games, the same counting production
       model.py blends from today)

THE TEST LINE IS SYNTHETIC. Historical bookmaker lines are
paid data Lilly does not have, so each game is tested against
a line built from the player's own prior-games average,
rounded to the nearest 0.5. (For the binary Anytime
Touchdown log the only real-world line is 0.5, so it is
fixed at 0.5.) This validates the probability MATH against
reality — whether "60%" predictions really land about 60%
of the time. It does NOT validate anything against
bookmakers, and nothing here claims an edge over them.

FAIRNESS RULES
- Both methods are scored on exactly the same games.
- A game is only tested once the player has at least
  projection.MIN_GAMES prior games for that stat, and only
  when he has actually recorded the stat before (prior sum
  > 0) — no free credit for predicting a player won't do
  something he never does.
- Actual outcome: 1 = went over, 0 = stayed under,
  0.5 = landed exactly on the line (a push).
- Accuracy score: the Brier score — the average squared
  distance between the predicted chance and the outcome.
  Lower is better. A forecaster who always says 50% scores
  0.25 before pushes; that baseline is printed too.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from kings_picks import projection  # noqa: E402
from kings_picks.game_logs import StatsStore  # noqa: E402

MIN_PRIOR = projection.MIN_GAMES
BUCKETS = [
    (0.00, 0.40, "under 40%"),
    (0.40, 0.50, "40-50%"),
    (0.50, 0.55, "50-55%"),
    (0.55, 0.60, "55-60%"),
    (0.60, 0.70, "60-70%"),
    (0.70, 1.01, "70% +"),
]


def round_to_half(x: float) -> float:
    """Round to the nearest 0.5, halves rounding up — the grid
    real prop lines live on."""
    return math.floor(x * 2 + 0.5) / 2


def old_method_probability(prior_newest_first: list[float], line: float) -> float:
    """v1's ingredient, exactly as production counts it today:
    of the player's last (up to) 10 prior games, the share that
    went OVER the line. Landing on the line counts for neither
    side — same as game_logs.summarize."""
    last = prior_newest_first[:10]
    hits = sum(1 for v in last if v > line)
    return hits / len(last)


def evaluate(logs) -> list[tuple[float, float, float]]:
    """Walk one league's logs. Returns (p_v2, p_v1, outcome)
    for every testable player-game."""
    records: list[tuple[float, float, float]] = []
    for _player, stats in logs.by_player.items():
        for _label, values in stats.items():
            chrono = list(reversed(values))  # oldest first
            is_binary = all(v in (0.0, 1.0) for v in chrono)
            for i in range(MIN_PRIOR, len(chrono)):
                prior = chrono[:i]
                if sum(prior) <= 0:
                    continue  # he has never recorded this stat
                actual = chrono[i]
                if is_binary:
                    line = 0.5
                else:
                    line = round_to_half(sum(prior) / len(prior))
                prior_newest_first = list(reversed(prior))
                p_v2 = projection.over_probability(prior_newest_first, line, "Over")
                if p_v2 is None:
                    continue
                p_v1 = old_method_probability(prior_newest_first, line)
                if actual > line:
                    outcome = 1.0
                elif actual < line:
                    outcome = 0.0
                else:
                    outcome = 0.5  # push — exactly on the line
                records.append((p_v2, p_v1, outcome))
    return records


def brier(records, idx: int) -> float:
    return sum((r[idx] - r[2]) ** 2 for r in records) / len(records)


def bucket_rows(records, idx: int):
    rows = []
    for lo, hi, label in BUCKETS:
        cell = [r for r in records if lo <= r[idx] < hi]
        if not cell:
            rows.append((label, 0, None, None))
        else:
            mean_pred = sum(r[idx] for r in cell) / len(cell)
            actual = sum(r[2] for r in cell) / len(cell)
            rows.append((label, len(cell), mean_pred, actual))
    return rows


def report_block(title: str, records) -> str:
    lines = [f"### {title} — {len(records):,} player-games", ""]
    if not records:
        lines.append("No testable player-games (feed missing?).")
        return "\n".join(lines)
    b_v2 = brier(records, 0)
    b_v1 = brier(records, 1)
    b_5050 = sum((0.5 - r[2]) ** 2 for r in records) / len(records)
    lines.append(
        f"Brier score (lower is better): **v2 {b_v2:.4f}** · "
        f"v1 {b_v1:.4f} · always-50% {b_5050:.4f}"
    )
    lines.append("")
    lines.append("| Predicted | Method | Games | Avg predicted | Actually went over |")
    lines.append("|---|---|---|---|---|")
    for idx, name in ((0, "v2"), (1, "v1")):
        for label, n, mean_pred, actual in bucket_rows(records, idx):
            if n == 0:
                lines.append(f"| {label} | {name} | 0 | — | — |")
            else:
                lines.append(
                    f"| {label} | {name} | {n:,} | {mean_pred:.1%} | {actual:.1%} |"
                )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    store = StatsStore(cache_dir=APP_ROOT / "data" / "cache")
    all_records: list[tuple[float, float, float]] = []
    blocks = []
    for league, fetch in (("NFL", store.nfl_logs), ("NBA", store.nba_logs)):
        logs = fetch()
        if logs is None:
            blocks.append(f"### {league}\n\nFeed unavailable — no games tested.")
            continue
        records = evaluate(logs)
        all_records.extend(records)
        blocks.append(
            report_block(f"{league} ({logs.source_label}, season {logs.season_label})", records)
        )
    blocks.append(report_block("BOTH LEAGUES", all_records))
    print("\n".join(blocks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
