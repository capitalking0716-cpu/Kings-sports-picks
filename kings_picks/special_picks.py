"""Special picks — the anytime-TD set (2026-11-14).

Neko's order: on a day with 6 or more NFL games, a SPECIAL
PICKS button lights up; tapping it builds THE SET — for
EVERY game on the day's slate, one 2-pick parlay of
anytime TDs: the best TD scorer from EACH team in the
game, one leg per team.

How "best" is picked (Neko's refinement, 2026-11-14, and
the rule for anytime-TD selection generally): "specifically
picking players known for 1 TD in a game." Rank by
CONSISTENCY — the share of his games in which he scored at
least one touchdown (his games-with-a-TD rate) — NOT by
raw TD total, which a couple of multi-TD explosion games
can inflate. The steady "he gets his one TD" player beats
the boom-or-bust player. Total TDs are only the
tie-break, and Lilly's chance for the leg breaks any tie
left after that.

Injury exclusion comes FIRST, before any ranking: a
player carrying any designation (Out / Doubtful /
Questionable / IR / protocol...) is never used. When the
flagged player would have won his team's spot, the swap
is noted in plain English; when every TD option on a team
is flagged, that game sits out and the note says why.

Data, all of it already in the app — this module makes NO
Odds API calls and reads NO network:
- the selected date's STORED slate (raw props for who is
  offered, scored props for Lilly's chances);
- the NFL game logs the app already loads (the live feeds
  with the bundled stats pack as fallback): each player's
  "Anytime Touchdown" game log (TDs per game, newest
  first) for the consistency maths, and the logs' player
  -> team map for which team in the game he plays for
  (the same map the roster guard trusts — a player whose
  team can't be placed in the game is never used);
- injury designations as an INPUT mapping (normalized
  player name -> designation text). The app passes
  whatever its sources carry; an empty mapping means no
  player is flagged. A designation is never invented here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kings_picks.game_logs import normalize_name
from kings_picks.model import score_prop
from kings_picks.parlays import Parlay, combined_decimal_payout, overall_probability
from kings_picks.roster import resolve_team

# The button lights up at this many NFL games on the day's
# slate — Neko's number, in his words: "more than 6 games"
# (a 6-game setup or more is valid).
MIN_NFL_GAMES = 6

TD_PROP = "Anytime Touchdown"


@dataclass
class SpecialSet:
    """The built set: one 2-pick Parlay per game (style
    "special", so the pick log tags every leg as a special
    pick), the plain-English notes (injury swaps, games
    sitting out), and each leg's TD form line keyed by the
    leg's pick key for the section display."""

    parlays: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    form_lines: dict = field(default_factory=dict)
    games_seen: int = 0


def nfl_game_count(raw_props) -> int:
    """How many NFL games the slate holds — distinct game
    labels among its NFL props. This is the count the
    SPECIAL PICKS button lights up on."""
    return len(
        {
            p.event
            for p in raw_props or []
            if p.sport == "NFL" and p.event
        }
    )


def td_consistency(values) -> dict:
    """One player's TD consistency from his "Anytime
    Touchdown" game log (TDs per game, newest first).

    The headline number is the games-with-a-TD rate: in
    what share of his games did he score at least once?
    A 3-TD explosion counts ONCE — that is the whole point
    of Neko's rule. Total TDs ride along as the tie-break,
    and the last-5 window is what the form line quotes."""
    vals = [float(v) for v in (values or [])]
    games = len(vals)
    with_td = sum(1 for v in vals if v >= 1)
    last5 = vals[:5]
    last5_with = sum(1 for v in last5 if v >= 1)
    return {
        "games": games,
        "games_with_td": with_td,
        "season_rate": (with_td / games) if games else None,
        "last5_games": len(last5),
        "last5_with_td": last5_with,
        "last5_rate": (last5_with / len(last5)) if last5 else None,
        "total_tds": int(sum(vals)),
    }


def td_form_line(values) -> str:
    """The TD form sentence in plain words — the
    consistency story, quoted on the last 5 games:
    "Scores a touchdown in most games — got one in 4 of
    his last 5 games." No log, no sentence invented: the
    line says there's no form to show."""
    stats = td_consistency(values)
    if not stats["games"]:
        return "No game log covers him — Lilly can't show his TD form."
    x, n = stats["last5_with_td"], stats["last5_games"]
    if x == 0:
        return f"Hasn't scored in his last {n} games — a cold pick right now."
    rate = stats["last5_rate"]
    if rate >= 0.8:
        head = "Scores a touchdown in most games"
    elif rate >= 0.5:
        head = "Scores a touchdown in more games than not"
    elif rate >= 0.25:
        head = "Scores a touchdown in some games"
    else:
        head = "Rarely scores a touchdown"
    season = ""
    if stats["games"] > n:
        season = (
            f" Season: got one in {stats['games_with_td']} "
            f"of {stats['games']} games."
        )
    return f"{head} — got one in {x} of his last {n} games.{season}"


def _form_stamp(values) -> tuple[str, str]:
    """(form_verdict, form_record) for a TD leg, from the
    same last-5 consistency the form line quotes — so the
    plain-English download grades the leg exactly the way
    the special section showed it ("4/5")."""
    stats = td_consistency(values)
    if not stats["games"]:
        return "NO DATA", ""
    rate = stats["last5_rate"]
    if rate >= 0.75:
        verdict = "STRONG"
    elif rate >= 0.5:
        verdict = "LEAN"
    elif rate >= 0.25:
        verdict = "MIXED"
    else:
        verdict = "SHAKY"
    return verdict, f"{stats['last5_with_td']}/{stats['last5_games']}"


def _rank_key(candidate) -> tuple:
    """Consistency first (Neko's rule): games-with-a-TD
    rate, then total TDs as the tie-break, then Lilly's
    chance for the leg. A player with no game log rates
    below every logged player — his consistency is
    unknown, not zero, and unknown never beats shown."""
    stats = candidate["stats"]
    rate = stats["season_rate"]
    return (
        rate if rate is not None else -1.0,
        stats["total_tds"],
        float(candidate["pick"].probability),
    )


def build_special_set(raw_props, scored, nfl_logs, injuries=None) -> SpecialSet:
    """Build the set from one date's stored slate.

    raw_props / scored: the slate's props, exactly as the
    pull stored them. nfl_logs: the loaded NFL GameLogs
    (or None — then no player can be placed on a team or
    graded, and the set comes back empty with the reason
    in the notes). injuries: {normalized name: designation}
    — excluded FIRST, before any ranking."""
    result = SpecialSet()
    injuries = injuries or {}
    raw_props = list(raw_props or [])
    games: dict[str, list] = {}
    for prop in raw_props:
        if prop.sport == "NFL" and prop.event:
            games.setdefault(prop.event, []).append(prop)
    result.games_seen = len(games)
    if not games:
        return result
    if nfl_logs is None:
        result.notes.append(
            "The NFL game logs didn't load, so Lilly can't check "
            "TD form or place players on their teams — no special "
            "picks this time."
        )
        return result

    scored_by_pick = {
        (p.player, p.prop, float(p.line)): p for p in scored or []
    }
    pair_odds: dict[tuple, dict] = {}
    for prop in raw_props:
        pair_odds.setdefault(
            (prop.player, prop.prop, float(prop.line)), {}
        )[prop.side] = prop.odds

    def leg_pick(prop):
        """The scored TD pick for this player — Lilly's own
        board version when she kept one, else the raw prop
        scored through the board's odds-only path."""
        kept = scored_by_pick.get(
            (prop.player, prop.prop, float(prop.line))
        )
        if kept is not None and kept.side == "Over":
            return kept
        pair = pair_odds.get((prop.player, prop.prop, float(prop.line)), {})
        return score_prop(prop, pair_odds=pair.get("Under"))

    ordered_games = sorted(
        games.items(),
        key=lambda kv: (
            min((p.commence_time or "9999") for p in kv[1]),
            kv[0],
        ),
    )
    for event, props in ordered_games:
        teams = [t.strip() for t in event.split(" vs ") if t.strip()]
        if len(teams) != 2:
            result.notes.append(
                f"{event}: Lilly can't split this game into its two "
                "teams, so it sits out of the special set."
            )
            continue
        legs = []
        game_ok = True
        for team in teams:
            team_abbr = resolve_team("NFL", team)
            candidates = []
            for prop in props:
                if prop.prop != TD_PROP or prop.side != "Over":
                    continue
                if team_abbr is None:
                    continue
                if nfl_logs.team_of(prop.player) != team_abbr:
                    continue
                values = nfl_logs.values(prop.player, TD_PROP)
                candidates.append(
                    {
                        "player": prop.player,
                        "team": team,
                        "pick": leg_pick(prop),
                        "stats": td_consistency(values),
                        "values": values,
                        "designation": injuries.get(
                            normalize_name(prop.player)
                        ),
                    }
                )
            if not candidates:
                result.notes.append(
                    f"{event}: no anytime TD props for {team} on this "
                    "slate — this game sits out of the special set."
                )
                game_ok = False
                break
            ranked = sorted(candidates, key=_rank_key, reverse=True)
            clean = [c for c in ranked if not c["designation"]]
            if not clean:
                flagged = ", ".join(
                    f"{c['player']} ({c['designation']})" for c in ranked
                )
                result.notes.append(
                    f"{event}: every TD option for {team} is carrying "
                    f"an injury tag — {flagged} — so this game sits "
                    "out of the special set."
                )
                game_ok = False
                break
            chosen = clean[0]
            if ranked[0] is not chosen:
                top = ranked[0]
                result.notes.append(
                    f"{event}: {top['player']} ({top['designation']}) "
                    f"would have the {team} spot, but no tagged player "
                    f"is used — {chosen['player']} takes it."
                )
            leg = chosen["pick"]
            verdict, record = _form_stamp(chosen["values"])
            leg.form_verdict = verdict
            leg.form_record = record
            result.form_lines[leg.key] = td_form_line(chosen["values"])
            legs.append(leg)
        if not game_ok or len(legs) != 2:
            continue
        prob = overall_probability(legs)
        payout = combined_decimal_payout(legs)
        result.parlays.append(
            Parlay(
                size=2,
                picks=legs,
                overall_probability=prob,
                decimal_payout=payout,
                expected_value_per_dollar=prob * payout - 1.0,
                label=f"{event} — anytime TD pair",
                style="special",
            )
        )
    return result
