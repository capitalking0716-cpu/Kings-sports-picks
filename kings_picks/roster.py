"""Roster guard — no leg survives on a player who isn't in
the game it's listed under.

Built 2026-11-12, after a live Cloud pull put Anthony Davis
and Trae Young legs on a Detroit-vs-Washington ticket and
two college players (AJ Dybantsa, Morez Johnson Jr.) on NBA
tickets: the books' prop feed occasionally attaches a player
to an event he isn't playing in, and nothing checked. Now
every candidate leg is validated BEFORE scoring:

- the player must be in the player index (the free feeds'
  own data — NBA PlayerIndex teams, nflverse teams, or the
  bundled stats pack's copy of them), and
- his team must be one of the two teams in the leg's event.

A leg that fails is dropped before scoring — never picked,
never logged. FAIL-SOFT, like everything stats-shaped in
this app: if the index can't be loaded, or doesn't cover
this board at all, the guard stands down and the pull
behaves exactly as it did before the guard existed (the app
says so in the pull note). A player whose index team can't
be read, or an event whose teams can't be read, is kept —
the guard only drops what it can prove is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kings_picks.self_test import _key_candidates

# ---------- team tables ----------
# (abbreviation, full name as The Odds API spells it). The
# full names are how events arrive ("Detroit Pistons vs
# Washington Wizards"); the abbreviations are how the feeds
# name a player's team (PlayerIndex TEAM_ABBREVIATION,
# nflverse recent_team). Nicknames (the last word, or last
# two words for "Trail Blazers") are derived for tolerant
# matching: "LA Clippers" and "Los Angeles Clippers" are
# the same team, and both spellings show up in the wild.

NBA_TEAMS = [
    ("ATL", "Atlanta Hawks"),
    ("BOS", "Boston Celtics"),
    ("BKN", "Brooklyn Nets"),
    ("CHA", "Charlotte Hornets"),
    ("CHI", "Chicago Bulls"),
    ("CLE", "Cleveland Cavaliers"),
    ("DAL", "Dallas Mavericks"),
    ("DEN", "Denver Nuggets"),
    ("DET", "Detroit Pistons"),
    ("GSW", "Golden State Warriors"),
    ("HOU", "Houston Rockets"),
    ("IND", "Indiana Pacers"),
    ("LAC", "LA Clippers"),
    ("LAL", "Los Angeles Lakers"),
    ("MEM", "Memphis Grizzlies"),
    ("MIA", "Miami Heat"),
    ("MIL", "Milwaukee Bucks"),
    ("MIN", "Minnesota Timberwolves"),
    ("NOP", "New Orleans Pelicans"),
    ("NYK", "New York Knicks"),
    ("OKC", "Oklahoma City Thunder"),
    ("ORL", "Orlando Magic"),
    ("PHI", "Philadelphia 76ers"),
    ("PHX", "Phoenix Suns"),
    ("POR", "Portland Trail Blazers"),
    ("SAC", "Sacramento Kings"),
    ("SAS", "San Antonio Spurs"),
    ("TOR", "Toronto Raptors"),
    ("UTA", "Utah Jazz"),
    ("WAS", "Washington Wizards"),
]

NFL_TEAMS = [
    ("ARI", "Arizona Cardinals"),
    ("ATL", "Atlanta Falcons"),
    ("BAL", "Baltimore Ravens"),
    ("BUF", "Buffalo Bills"),
    ("CAR", "Carolina Panthers"),
    ("CHI", "Chicago Bears"),
    ("CIN", "Cincinnati Bengals"),
    ("CLE", "Cleveland Browns"),
    ("DAL", "Dallas Cowboys"),
    ("DEN", "Denver Broncos"),
    ("DET", "Detroit Lions"),
    ("GB", "Green Bay Packers"),
    ("HOU", "Houston Texans"),
    ("IND", "Indianapolis Colts"),
    ("JAX", "Jacksonville Jaguars"),
    ("KC", "Kansas City Chiefs"),
    ("LV", "Las Vegas Raiders"),
    ("LAC", "Los Angeles Chargers"),
    ("LAR", "Los Angeles Rams"),
    ("MIA", "Miami Dolphins"),
    ("MIN", "Minnesota Vikings"),
    ("NE", "New England Patriots"),
    ("NO", "New Orleans Saints"),
    ("NYG", "New York Giants"),
    ("NYJ", "New York Jets"),
    ("PHI", "Philadelphia Eagles"),
    ("PIT", "Pittsburgh Steelers"),
    ("SF", "San Francisco 49ers"),
    ("SEA", "Seattle Seahawks"),
    ("TB", "Tampa Bay Buccaneers"),
    ("TEN", "Tennessee Titans"),
    ("WAS", "Washington Commanders"),
]

_TEAM_TABLES = {"NBA": NBA_TEAMS, "NFL": NFL_TEAMS}

# Abbreviation spellings that mean the same team, from the
# feeds' own habits (nflverse has used LA and STL for the
# Rams, SD for the Chargers, OAK for the Raiders, WSH for
# Washington; stats.nba.com has used PHO for Phoenix).
_ABBR_ALIASES = {
    "NBA": {"PHO": "PHX", "WSH": "WAS"},
    "NFL": {
        "LA": "LAR",
        "STL": "LAR",
        "SD": "LAC",
        "OAK": "LV",
        "WSH": "WAS",
        "JAC": "JAX",
    },
}

# Full-name spellings that mean the same team.
_NAME_ALIASES = {
    "NBA": {"los angeles clippers": "LAC"},
    "NFL": {},
}


def _norm_text(text: str) -> str:
    return " ".join((text or "").lower().replace(".", " ").replace("-", " ").split())


def _build_resolution(sport: str):
    """(by_abbr, by_name, by_nickname) for one league, built
    once from its table. Nicknames map to exactly one team
    per league — checked when the tables were written."""
    table = _TEAM_TABLES.get(sport, [])
    by_name: dict[str, str] = {}
    by_nickname: dict[str, str] = {}
    for abbr, full in table:
        norm = _norm_text(full)
        by_name[norm] = abbr
        words = norm.split()
        if words:
            by_nickname[words[-1]] = abbr
        if len(words) >= 2:
            by_nickname[" ".join(words[-2:])] = abbr
    for alias, abbr in _NAME_ALIASES.get(sport, {}).items():
        by_name[_norm_text(alias)] = abbr
    by_abbr = {abbr for abbr, _full in table}
    return by_abbr, by_name, by_nickname


_RESOLUTION = {sport: _build_resolution(sport) for sport in _TEAM_TABLES}


def resolve_team(sport: str, text) -> str | None:
    """Any spelling of a team -> its canonical abbreviation,
    or None when nothing matches. Accepts the abbreviation
    itself ("DET", "kc"), the full name ("Detroit Pistons"),
    or a name whose trailing nickname identifies the team
    ("LA Clippers" via the Clippers nickname)."""
    if not text:
        return None
    built = _RESOLUTION.get(sport)
    if built is None:
        return None
    by_abbr, by_name, by_nickname = built
    raw = str(text).strip()
    upper = raw.upper()
    aliases = _ABBR_ALIASES.get(sport, {})
    if upper in by_abbr:
        return upper
    if upper in aliases:
        return aliases[upper]
    norm = _norm_text(raw)
    if norm in by_name:
        return by_name[norm]
    words = norm.split()
    for cut in range(1, len(words)):
        suffix = " ".join(words[cut:])
        if suffix in by_nickname:
            return by_nickname[suffix]
    return None


def event_team_abbrs(sport: str, event: str) -> tuple[str, ...]:
    """The teams in an event label ("Away vs Home") as
    canonical abbreviations. () when the label can't be
    read as two resolvable teams — the guard then falls
    back to index membership alone."""
    parts = (event or "").split(" vs ")
    if len(parts) != 2:
        return ()
    abbrs = []
    for part in parts:
        abbr = resolve_team(sport, part)
        if abbr is None:
            return ()
        abbrs.append(abbr)
    return tuple(abbrs)


@dataclass
class RosterIndex:
    """Who plays for whom, per sport: normalized player name
    -> team text (an abbreviation, as the feeds state it).
    Built from loaded GameLogs' `teams` mappings."""

    teams: dict = field(default_factory=dict)  # sport -> {name key: team text}

    def sports(self) -> list[str]:
        return [sport for sport, mapping in self.teams.items() if mapping]

    def team_abbr(self, sport: str, player: str) -> str | None:
        """The player's team abbreviation. None = the player
        is not in the index at all. "" = the player IS in the
        index but his team text couldn't be resolved — kept
        by the guard (fail-soft), never treated as a mismatch."""
        mapping = self.teams.get(sport) or {}
        if not mapping:
            return None
        for key in _key_candidates(player):
            if key in mapping:
                return resolve_team(sport, mapping[key]) or ""
        return None

    def validate(self, prop, active_sports) -> bool:
        """One leg against the index. Only called for sports
        the guard is active for; other sports pass untouched."""
        if prop.sport not in active_sports:
            return True
        abbr = self.team_abbr(prop.sport, prop.player)
        if abbr is None:
            return False  # not in the index — not a player in this league's data
        if not abbr:
            return True  # in the index, team unreadable — fail soft
        teams = event_team_abbrs(prop.sport, prop.event)
        if not teams:
            return True  # event unreadable — membership is all we can prove
        return abbr in teams


def build_index(nfl_logs=None, nba_logs=None) -> RosterIndex:
    """Assemble the index from loaded game logs (live feeds
    or the bundled stats pack — anything with a `teams`
    mapping). Leagues whose logs carry no teams simply
    contribute nothing."""
    teams: dict[str, dict] = {}
    for logs in (nfl_logs, nba_logs):
        if logs is None:
            continue
        mapping = getattr(logs, "teams", None) or {}
        if mapping:
            teams[logs.sport] = dict(mapping)
    return RosterIndex(teams=teams)


def guard_props(props, index: RosterIndex | None):
    """Validate a pulled board against the roster index.

    Returns (kept_props, dropped_count, active). The guard is
    ACTIVE for a sport only when that sport's index is
    non-empty AND at least one pulled player of that sport is
    found in it — an index that knows nobody on the board is
    an index that didn't really load (wrong season, empty
    feed), and standing down beats nuking a real slate.
    When no sport is active the whole guard is inactive:
    everything is kept, and the caller reports that the
    roster check didn't run.
    """
    props = list(props)
    if index is None:
        return props, 0, False
    active: set[str] = set()
    for sport in index.sports():
        for prop in props:
            if prop.sport == sport and index.team_abbr(sport, prop.player) is not None:
                active.add(sport)
                break
    if not active:
        return props, 0, False
    kept = []
    dropped = 0
    for prop in props:
        if index.validate(prop, active):
            kept.append(prop)
        else:
            dropped += 1
    return kept, dropped, True
