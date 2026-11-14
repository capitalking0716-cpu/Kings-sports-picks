"""Plain-English download of the current pull — the odds pull
and Lilly's picks as one text file Neko can keep on his phone.

Neko's order (2026-11-14): "Update Lilly to allow me to
download the odds pull with her picks — plain English
understandable information."

What this module is:

- A pure text builder over the slate the app ALREADY holds —
  the standard parlays and the 👑 King-style parlays from the
  last pull, exactly as restored from the stored slate
  (kings_picks/slate_store.py). Building the file makes NO
  Odds API call and NO rescore: a download costs zero quota
  and can never change the picks it describes.
- Plain English a beginner can read cold: when the pull
  happened, one block per ticket with the same KSB- ticket
  number the app prints on screen (same content recipe as
  app.py's _ticket_number, so the file and the ticket agree),
  each leg as "Player (Team) — what has to happen", the
  leg's form grade in words with its last-10 record, Lilly's
  chance per leg, and the ticket's overall chance in words
  ("about 7 in 10"). No raw JSON, no unexplained
  abbreviations, and — by construction — no keys, PINs or
  anything else that is not a pick: the builder only ever
  reads parlay objects.
- Empty means empty: with no parlays at all the builder
  returns "", and the app shows a short note where the
  button would be instead of offering a hollow file.

Team names come from the feeds' own team tables
(kings_picks/roster.py) via the loaded game logs' player ->
team maps — the same source the roster guard uses. A player
the maps don't cover simply prints without a team; the
game line on his leg still names both teams.
"""

from __future__ import annotations

import hashlib
import math
import re

from kings_picks import slate_store
from kings_picks import slip as _slip
from kings_picks.game_logs import normalize_name
from kings_picks.roster import NBA_TEAMS, NFL_TEAMS, resolve_team

_TEAM_FULL = {"NBA": dict(NBA_TEAMS), "NFL": dict(NFL_TEAMS)}

# Form verdicts (kings_picks/self_test.py) in plain words.
_GRADE_WORDS = {
    "STRONG": "Strong",
    "LEAN": "Lean",
    "MIXED": "Mixed — up and down",
    "SHAKY": "Shaky",
    "NO DATA": "No grade",
}

# Prop labels whose plain-words unit differs from a plain
# lower-casing of the feed label.
_UNIT_WORDS = {
    "Three-Pointers Made": "three-pointers",
}

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

_RECORD_RE = re.compile(r"^(\d+)\s*/\s*(\d+)")

_RULE = "-" * 60


def ticket_number(parlay) -> str:
    """The KSB- number for a parlay — the same recipe app.py's
    _ticket_number uses for the on-screen ticket (label, style,
    and each leg's key and price), so a downloaded ticket and
    the ticket on screen always carry the same number."""
    seed = "|".join(
        [parlay.label, getattr(parlay, "style", "standard") or "standard"]
        + [f"{p.key}:{p.odds}" for p in parlay.picks]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"KSB-{digest[:8].upper()}"


def team_name(sport: str, player: str, teams=None) -> str:
    """'Chicago Bulls' for a player, from the loaded logs'
    player -> team maps (teams: {sport: {name key: team
    text}}). "" when the maps don't cover him or the team
    text can't be resolved — never an invented team."""
    mapping = (teams or {}).get(sport) or {}
    text = mapping.get(normalize_name(player))
    if not text:
        return ""
    abbr = resolve_team(sport, text)
    if abbr is None:
        return ""
    return _TEAM_FULL.get(sport, {}).get(abbr, "")


def _surname(player: str) -> str:
    """The name a sentence uses: 'Giddey gets 5 or more
    assists.' Suffixes (Jr., III) step aside for the family
    name in front of them."""
    parts = [p for p in (player or "").split() if p]
    if not parts:
        return "He"
    if len(parts) > 1 and parts[-1].lower().strip(".") in _NAME_SUFFIXES:
        parts = parts[:-1]
    return parts[-1]


def _unit_words(prop: str) -> str:
    return _UNIT_WORDS.get(prop, (prop or "").lower())


def what_has_to_happen(pick) -> str:
    """One plain sentence: the result this leg needs.

    Half-point lines (the normal case) are said as the whole
    number they really mean — Over 4.5 assists is "5 or more
    assists". A whole-number line can tie (a push), so it is
    said as "more than" / "fewer than" with the whole-number
    meaning spelled out next to it."""
    name = _surname(pick.player)
    if pick.prop == "Anytime Touchdown":
        if pick.side == "Over":
            return f"{name} scores a touchdown at any point in the game."
        return f"{name} does not score a touchdown in the game."
    unit = _unit_words(pick.prop)
    line = float(pick.line)
    if line == int(line):
        whole = int(line)
        if pick.side == "Over":
            return (
                f"{name} gets more than {whole} {unit} "
                f"(at least {whole + 1}). A total of exactly "
                f"{whole} is a tie — the stake comes back."
            )
        return (
            f"{name} gets fewer than {whole} {unit} "
            f"({whole - 1} or less). A total of exactly "
            f"{whole} is a tie — the stake comes back."
        )
    if pick.side == "Over":
        return f"{name} gets {math.floor(line) + 1} or more {unit}."
    return f"{name} gets {math.floor(line)} or fewer {unit}."


def form_line(pick) -> str:
    """The leg's form grade in words, with its last-10
    record when the pull graded it ("7/10" / "7/10 (1
    push)"). "" for a leg that was never graded — the file
    never invents a grade."""
    verdict = getattr(pick, "form_verdict", "") or ""
    if not verdict:
        return ""
    word = _GRADE_WORDS.get(verdict, verdict.title())
    if verdict == "NO DATA":
        return (
            "Form grade: No grade — no game log covers this "
            "pick, so Lilly will not pretend his form says "
            "anything."
        )
    record = getattr(pick, "form_record", "") or ""
    match = _RECORD_RE.match(record)
    if match:
        hits, decided = match.group(1), match.group(2)
        return (
            f"Form grade: {word} — he hit this line in {hits} "
            f"of his last {decided} games."
        )
    return f"Form grade: {word}."


def chance_words(probability) -> str:
    """An overall chance in words a beginner feels: "about 7
    in 10" for a real chance, "about 1 in 250" for a long
    shot. The exact percent rides along in parentheses."""
    p = float(probability or 0.0)
    if p <= 0:
        return "no chance worked out"
    if p >= 0.095:
        return f"about {round(p * 10)} in 10 ({p:.0%})"
    return f"about 1 in {1.0 / p:.0f} ({p:.1%})"


def _leg_lines(pick, index: int, teams) -> list[str]:
    team = team_name(pick.sport, pick.player, teams)
    who = f"{pick.player} ({team})" if team else pick.player
    if pick.prop == "Anytime Touchdown":
        head = f"{index}. {who} — Anytime Touchdown"
    else:
        head = (
            f"{index}. {who} — {pick.side} {float(pick.line):g} "
            f"{_unit_words(pick.prop)}"
        )
    lines = [head, f"   What has to happen: {what_has_to_happen(pick)}"]
    # King-style annotation (kings_picks/king_style.py): a
    # leg played at a moved line says where it came from.
    base_line = getattr(pick, "base_line", None)
    if base_line is not None and float(base_line) != float(pick.line):
        verb = "dropped down" if pick.side == "Over" else "raised up"
        lines.append(
            f"   King's style: this line was {verb} from "
            f"{float(base_line):g} to {float(pick.line):g} to "
            "give the pick more room."
        )
    grade = form_line(pick)
    if grade:
        lines.append(f"   {grade}")
    lines.append(f"   Lilly's chance for this pick: {pick.probability:.0%}.")
    meta = []
    if pick.event:
        meta.append(f"Game: {pick.event}")
    if pick.bookmaker:
        meta.append(f"Book: {pick.bookmaker}")
    meta.append(f"Price: {int(pick.odds):+d}")
    lines.append("   " + " · ".join(meta))
    if not pick.has_form_data:
        lines.append(
            "   Note: this is an odds-only pick — Lilly has no "
            "recent-form data for it, so it rests on the "
            "book's price alone."
        )
    lines.append("")
    return lines


def _ticket_lines(parlay, teams) -> list[str]:
    style = getattr(parlay, "style", "standard") or "standard"
    style_words = (
        "King's style — dropped lines"
        if style == "king"
        else "special pick — anytime TD pair"
        if style == "special"
        else "standard lines"
    )
    count = len(parlay.picks)
    lines = [
        _RULE,
        f"TICKET {ticket_number(parlay)} — {count}-pick parlay ({style_words})",
        "Every pick on this ticket has to win, or the ticket loses.",
        f"Overall chance: {chance_words(parlay.overall_probability)}.",
        "",
    ]
    for index, pick in enumerate(parlay.picks, 1):
        lines.extend(_leg_lines(pick, index, teams))
    return lines


def build_picks_text(
    *,
    pulled_at: str = "",
    parlays=(),
    king_parlays=(),
    teams=None,
    game_date: str = "",
    today: str = "",
    slip_legs=(),
    slip_profile: str = "",
    special_parlays=(),
) -> str:
    """The whole download: the pull's time, then every ticket
    — 👑 King's style first, as the Dashboard shows them,
    then Lilly's standard lines.

    parlays / king_parlays are Parlay objects (restored from
    the stored slate or straight from a pull); teams is the
    loaded logs' {sport: {name key: team text}} maps, used
    only to name each player's team. Returns "" when there
    are no parlays at all — no slate, no file.

    game_date names the day the slate's games are played
    ("Games for: Sunday, November 16, 2026"); when it is
    still ahead of `today`, the tickets are a pre-build and
    the file says so in one plain line. slip_legs are the
    user's own hand-built, form-tested picks (My Slip):
    they ride at the end, under their own heading, with
    the slip's true combined chance — the same legs the
    app grades on the My Slip page."""
    standard = list(parlays or [])
    king = list(king_parlays or [])
    own = list(slip_legs or [])
    special = list(special_parlays or [])
    if not standard and not king and not own and not special:
        return ""
    out = [
        "👑 KINGS SPORTS BOOKS PICKS",
        "Lilly's picks from the latest odds pull — in plain English",
        "",
    ]
    pulled = slate_store.format_pulled_at(pulled_at)
    out.append(f"Pulled: {pulled}" if pulled else "Pulled: this session")
    games_day = slate_store.format_game_date(game_date)
    if games_day:
        out.append(f"Games for: {games_day}")
        if today and game_date > today:
            out.append(
                "🔮 Pre-built — an early look at that day's games. "
                "Lines and who's playing can change by game day."
            )
    out += [
        "",
        "How to read this file: each ticket is a parlay — a bundle of",
        "picks that ALL have to win. Shorter tickets win more often",
        "and pay less; longer tickets are long shots. No parlay is a",
        "sure thing, and Lilly never promises a win.",
        "",
    ]
    if king:
        out.append("👑 KING'S STYLE TICKETS")
        out.append(
            "Built King's way: take the number the player is known for,"
        )
        out.append("drop it down a few, and play the safer side of it.")
        out.append("")
        for parlay in king:
            out.extend(_ticket_lines(parlay, teams))
    if special:
        out.append("⭐ SPECIAL PICKS — ONE ANYTIME TD FROM EACH TEAM")
        out.append(
            "The steadiest TD scorer from each team — the man known "
            "for getting his one touchdown — paired game by game."
        )
        out.append("")
        for parlay in special:
            out.extend(_ticket_lines(parlay, teams))
    if standard:
        out.append("🎟️ LILLY'S STANDARD-LINE TICKETS")
        out.append("")
        for parlay in standard:
            out.extend(_ticket_lines(parlay, teams))
    if own:
        heading = "🧪 YOUR OWN PICKS — built by you, tested by Lilly"
        if slip_profile:
            heading += f" ({slip_profile}'s slip)"
        out.append(heading)
        out.append(
            "Your hand-built picks from My Slip, each one graded "
            "against the player's last 10 games."
        )
        out.append("")
        for index, leg in enumerate(own, 1):
            out.extend(_leg_lines(leg, index, teams))
        out.append(
            "Overall chance if played together: "
            f"{chance_words(_slip.overall_probability(own))} — "
            "Lilly's chance for each pick, multiplied together."
        )
        out.append(
            "Save the slip in the app to keep these picks in your "
            "record and settle them after the games."
        )
        out.append("")
    out += [
        _RULE,
        "These are the picks exactly as pulled at the time above.",
        "If you pull fresh odds in the app, download this file again",
        "so your copy matches what Lilly is showing.",
    ]
    return "\n".join(out).strip() + "\n"
