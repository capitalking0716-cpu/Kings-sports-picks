"""Lilly tests her own picks — a form check, reported alongside.

Built 2026-11-12 at Neko's word: "have her do testing now that
I ran the system and she has fresh numbers." After a run, every
pick Lilly logged (standard legs and 👑 King-style legs, at the
lines they PLAY) is graded against the player's own recent
game-log form: in his last 10 logged games, how many times
would this exact pick — this side of this line — have hit?

WHAT THIS IS, AND WHAT IT IS NOT:

- It is a DESCRIPTIVE check of past-game form, the same honest
  counting the Players page shows. It is not a promise about
  tonight, and it is not a probability.
- It NEVER changes a pick's stated chance. Her chances come
  from the scoring model (kings_picks/model.py) and her
  learning loop; this grade never flows back into score_prop
  and never reprices a leg. What the grade DOES steer is
  selection order: since the test-before-picking build
  (2026-11-12, Neko's order), a live pull grades every
  candidate leg BEFORE the parlays are built, and the builder
  ranks form-backed legs first (STRONG, then LEAN), treating
  SHAKY legs as a last-resort reserve. Same legs, same
  chances — the grade decides which legs get picked, never
  what a picked leg claims.
- The raw hit-rate was RETIRED as a scoring ingredient by the
  walk-forward calibration proof (validation/projection-
  calibration-2026-11-12.md): as a chance it was worse than
  always saying 50% in the NBA. That is precisely why it lives
  here, as a labelled form check, and never flows back into
  score_prop. Do not wire it back in.
- ZERO Odds API quota: the grades read only the free game-log
  feeds (nflverse / stats.nba.com via kings_picks/game_logs.py)
  that the app already loads. A player or stat the logs don't
  cover grades NO DATA — never a guess, never a crash.

Pushes are counted honestly: a game landing exactly on the
line (only possible on whole-number lines) is neither a hit
nor a miss, and hit rates are computed over decided games only.
"""

from __future__ import annotations

from dataclasses import dataclass

# Verdict tags. STRONG needs both the rate AND enough decided
# games behind it — 7-for-7 is encouraging, not strong proof.
STRONG = "STRONG"
LEAN = "LEAN"
MIXED = "MIXED"
SHAKY = "SHAKY"
NO_DATA = "NO DATA"

STRONG_MIN_RATE = 0.75
STRONG_MIN_DECIDED = 8
LEAN_MIN_RATE = 0.60
SHAKY_MAX_RATE = 0.40

VERDICT_DISPLAY = {
    STRONG: "💪 STRONG",
    LEAN: "👍 LEAN",
    MIXED: "😐 MIXED",
    SHAKY: "⚠️ SHAKY",
    NO_DATA: "❔ NO DATA",
}

_VERDICT_RANK = {STRONG: 0, LEAN: 1, MIXED: 2, SHAKY: 3, NO_DATA: 4}

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_player_name(name: str) -> str:
    """One key per player across the pick log and the feeds.

    The pick log carries the books' spellings ("Paul Reed Jr",
    "R.J. Barrett"); the game logs were keyed when the feeds
    were parsed. Lowercase, hyphens become spaces, apostrophes
    drop, trailing suffixes (Jr / Sr / II / III / IV / V) are
    stripped — and periods are dropped OUTRIGHT, not spaced:
    that is what makes "R.J. Barrett" land on the feed's
    "rj barrett" instead of a stranded "r j barrett". The
    lookup (values_for) also tries the game-log parser's own
    key form, so a feed that spelled the name with spaced
    initials is still found.
    """
    text = (name or "").lower().replace("-", " ").replace(".", "").replace("'", "")
    parts = [p for p in text.split() if p]
    while parts and parts[-1] in _NAME_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def _squash_initials(key: str) -> str:
    """"r j barrett" -> "rj barrett": merge runs of single
    letters. One more candidate key form for the lookup —
    harmless when nothing matches it."""
    out: list[str] = []
    for part in key.split():
        if out and len(out[-1]) == 1 and len(part) == 1:
            out[-1] += part
        else:
            out.append(part)
    return " ".join(out)


def _key_candidates(name: str) -> list[str]:
    """Every key form a player's log might be filed under:
    this module's normal form, the game-log parser's own
    normal form (kings_picks.game_logs.normalize_name, which
    spaces periods instead of dropping them), and the
    squashed-initials variant of each. Deduped, order kept."""
    from .game_logs import normalize_name as _feed_normalize_name

    candidates: list[str] = []
    for base in (normalize_player_name(name), _feed_normalize_name(name)):
        for variant in (base, _squash_initials(base)):
            if variant and variant not in candidates:
                candidates.append(variant)
    return candidates


def split_market(market: str) -> tuple[str, str]:
    """A pick-log market ("Points Over", "Anytime Touchdown
    Over") -> (prop label, side). A market with no Over/Under
    suffix comes back with side "" — ungradeable, and the
    grading path turns it into NO DATA rather than guessing a
    direction."""
    text = (market or "").strip()
    for side in ("Over", "Under"):
        suffix = f" {side}"
        if text.endswith(suffix):
            return text[: -len(suffix)], side
    return text, ""


def count_outcomes(values, side: str, line: float) -> tuple[int, int, int]:
    """(hits, misses, pushes) for one side of one line.

    Over hits when value > line, Under when value < line. A
    value exactly on the line is a push — counted separately,
    never as a hit or a miss.
    """
    hits = misses = pushes = 0
    for value in values:
        if value == line:
            pushes += 1
        elif side == "Over":
            if value > line:
                hits += 1
            else:
                misses += 1
        else:  # Under
            if value < line:
                hits += 1
            else:
                misses += 1
    return hits, misses, pushes


def _rate(hits: int, misses: int):
    """Hit rate over decided games only (pushes excluded).
    None when nothing was decided."""
    decided = hits + misses
    if decided == 0:
        return None
    return hits / decided


def verdict_for(last10_rate, last10_decided: int, games_found: int) -> str:
    """The verdict tag for one grade.

    NO DATA is only for a player/stat the logs don't cover at
    all. A covered player whose last 10 all pushed has no
    decided rate — that is MIXED (no evidence either way),
    never NO DATA and never SHAKY.
    """
    if games_found == 0:
        return NO_DATA
    if last10_rate is None:
        return MIXED
    if last10_rate >= STRONG_MIN_RATE and last10_decided >= STRONG_MIN_DECIDED:
        return STRONG
    if last10_rate >= LEAN_MIN_RATE:
        return LEAN
    if last10_rate <= SHAKY_MAX_RATE:
        return SHAKY
    return MIXED


@dataclass
class PickGrade:
    """One pick, graded against the player's game-log form."""

    player: str
    prop: str  # prop label, e.g. "Points", "Passing Yards"
    side: str  # "Over" / "Under" ("" when the market had none)
    line: float
    games_found: int = 0  # games in the log for this player + prop
    last10_games: int = 0  # games inside the last-10 window
    last10_hits: int = 0
    last10_misses: int = 0
    last10_pushes: int = 0
    last10_rate: float | None = None  # pushes excluded
    full_hits: int = 0
    full_misses: int = 0
    full_pushes: int = 0
    full_rate: float | None = None  # pushes excluded
    verdict: str = NO_DATA
    # Display fields, filled in by grade_entry from the log row:
    label: str = ""  # the pick's own log label
    stated_chance: float | None = None  # Lilly's chance, as logged
    style: str = "standard"  # "standard" / "king"
    mode: str = "live"  # entries are live-only since demo mode was removed
    sport: str = ""
    date: str = ""  # the slate date the pick was logged on

    @property
    def last10_decided(self) -> int:
        return self.last10_hits + self.last10_misses

    @property
    def form_label(self) -> str:
        """"7/10" for the last-10 window, with pushes named —
        "7/10 (1 push)". NO DATA grades read plainly."""
        if self.games_found == 0:
            return "no game log"
        text = f"{self.last10_hits}/{self.last10_games}"
        if self.last10_pushes:
            plural = "es" if self.last10_pushes != 1 else ""
            text += f" ({self.last10_pushes} push{plural})"
        return text

    @property
    def verdict_display(self) -> str:
        return VERDICT_DISPLAY.get(self.verdict, self.verdict)


def grade_values(player: str, prop: str, side: str, line: float, values) -> PickGrade:
    """Grade one pick against a raw game-value list (newest
    first), or None when the logs don't cover the player/stat.

    Pure: no I/O, no feeds, no odds. This is the function the
    unit tests drive with synthetic logs.
    """
    grade = PickGrade(player=player, prop=prop, side=side, line=float(line))
    if not values or side not in ("Over", "Under"):
        return grade
    values = list(values)
    grade.games_found = len(values)

    last10 = values[:10]
    hits, misses, pushes = count_outcomes(last10, side, grade.line)
    grade.last10_games = len(last10)
    grade.last10_hits = hits
    grade.last10_misses = misses
    grade.last10_pushes = pushes
    grade.last10_rate = _rate(hits, misses)

    f_hits, f_misses, f_pushes = count_outcomes(values, side, grade.line)
    grade.full_hits = f_hits
    grade.full_misses = f_misses
    grade.full_pushes = f_pushes
    grade.full_rate = _rate(f_hits, f_misses)

    grade.verdict = verdict_for(
        grade.last10_rate, grade.last10_decided, grade.games_found
    )
    return grade


def values_for(logs, player: str, prop: str):
    """The player's game values for one prop from a loaded
    GameLogs (anything with a `by_player` dict keyed by
    normalized name), or None when uncovered. Every plausible
    key form of the name is tried (see _key_candidates); name
    variants that match nothing degrade to None — NO DATA,
    never a crash."""
    if logs is None:
        return None
    by_player = getattr(logs, "by_player", None) or {}
    for key in _key_candidates(player):
        stats = by_player.get(key)
        if stats:
            values = stats.get(prop)
            if values:
                return list(values)
    return None


def grade_pick(player: str, prop: str, side: str, line: float, logs) -> PickGrade:
    """Grade one pick against the loaded game logs for its
    sport (a GameLogs, or None when the feed didn't load)."""
    return grade_values(player, prop, side, line, values_for(logs, player, prop))


def grade_entry(entry, logs_by_sport: dict) -> PickGrade:
    """Grade one pick-log entry. `logs_by_sport` maps sport
    ("NBA" / "NFL") to its GameLogs or None. The grade carries
    the entry's own label, stated chance, style, mode and date
    so the UI can show her number next to the form check."""
    prop, side = split_market(getattr(entry, "market", ""))
    sport = getattr(entry, "sport", "")
    logs = (logs_by_sport or {}).get(sport)
    grade = grade_pick(
        getattr(entry, "player", ""), prop, side, getattr(entry, "line", 0.0), logs
    )
    grade.label = getattr(entry, "label", "") or (
        f"{grade.player} — {prop} {side} {grade.line:g}".strip()
    )
    try:
        grade.stated_chance = float(getattr(entry, "probability"))
    except (TypeError, ValueError):
        grade.stated_chance = None
    grade.style = getattr(entry, "style", "standard") or "standard"
    grade.mode = getattr(entry, "mode", "live") or "live"
    grade.sport = sport
    grade.date = getattr(entry, "date", "") or ""
    return grade


def latest_slate(entries) -> list:
    """The current slate: Lilly's OWN logged picks (source
    "lilly" — standard and King-style legs; manual My Slip
    picks are the user's record, not hers to grade) from the
    most recent date in the log.

    The log keeps a creation DATE per pick, not a generation
    id — runs on the same day dedupe into one day-slate by
    pick key — so "the latest generation" is the latest date
    present. Older days are her history, not tonight's slate.
    """
    own = [e for e in entries if getattr(e, "source", "lilly") == "lilly"]
    if not own:
        return []
    latest = max(getattr(e, "date", "") for e in own)
    return [e for e in own if getattr(e, "date", "") == latest]


def _grade_sort_key(grade: PickGrade):
    rate = grade.last10_rate if grade.last10_rate is not None else -1.0
    return (
        _VERDICT_RANK.get(grade.verdict, len(_VERDICT_RANK)),
        -rate,
        -grade.last10_decided,
        grade.player,
    )


def grade_slate(entries, logs_by_sport: dict) -> list[PickGrade]:
    """Grade the current slate (see latest_slate), strongest
    first: verdict band, then last-10 rate, then decided
    games. NO DATA always sorts last — absence of evidence is
    not a bad grade, it just isn't a good one either."""
    return sorted(
        (grade_entry(e, logs_by_sport) for e in latest_slate(entries)),
        key=_grade_sort_key,
    )


def verdict_counts(grades) -> dict:
    """{verdict tag: count} over a graded slate, for the
    section's one-line summary."""
    counts: dict[str, int] = {}
    for grade in grades:
        counts[grade.verdict] = counts.get(grade.verdict, 0) + 1
    return counts
