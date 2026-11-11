"""Sample data for DEMO / OFFLINE mode.

These are made-up sample players, lines and odds so Neko can
see how the app works with no key set. They are NEVER live
picks. The app labels every screen that uses them DEMO in big
letters, and the ledger records demo runs as mode="demo" so
demo results can never be mistaken for a real track record.
"""

from __future__ import annotations

from kings_picks.model import Prop

# (player, sport, prop, side, line, odds, event)
_SAMPLE = [
    ("Sample Star", "NBA", "Points", "Over", 24.5, -110, "Sample Team vs Demo Team"),
    ("Sample Star", "NBA", "Points", "Under", 24.5, -110, "Sample Team vs Demo Team"),
    ("Demo Guard", "NBA", "Points", "Over", 19.5, -115, "Demo Team vs Sample Team"),
    ("Demo Guard", "NBA", "Assists", "Over", 6.5, -120, "Demo Team vs Sample Team"),
    ("Demo Guard", "NBA", "Assists", "Under", 6.5, -105, "Demo Team vs Sample Team"),
    ("Sample Big", "NBA", "Rebounds", "Over", 10.5, -110, "Sample Team vs Demo Team"),
    ("Sample Big", "NBA", "Rebounds", "Under", 10.5, -110, "Sample Team vs Demo Team"),
    ("Sample Big", "NBA", "Points + Rebounds + Assists", "Over", 34.5, -115, "Sample Team vs Demo Team"),
    ("Demo Wing", "NBA", "Three-Pointers Made", "Over", 2.5, -110, "Demo Team vs Sample Team"),
    ("Demo Wing", "NBA", "Points", "Over", 17.5, -105, "Demo Team vs Sample Team"),
    ("Demo Wing", "NBA", "Points", "Under", 17.5, -115, "Demo Team vs Sample Team"),
    ("Sample Shooter", "NBA", "Points", "Over", 21.5, -120, "Sample Team vs Demo Team"),
    ("Sample Shooter", "NBA", "Points", "Under", 21.5, +100, "Sample Team vs Demo Team"),
    ("Sample Shooter", "NBA", "Three-Pointers Made", "Over", 3.5, +120, "Sample Team vs Demo Team"),
    ("Demo Forward", "NBA", "Rebounds", "Over", 7.5, -110, "Demo Team vs Sample Team"),
    ("Demo Forward", "NBA", "Points", "Over", 15.5, -110, "Demo Team vs Sample Team"),
    ("Demo Forward", "NBA", "Assists", "Over", 3.5, -115, "Demo Team vs Sample Team"),
    ("Sample QB", "NFL", "Passing Yards", "Over", 249.5, -110, "Sample FC vs Demo FC"),
    ("Sample QB", "NFL", "Passing Yards", "Under", 249.5, -110, "Sample FC vs Demo FC"),
    ("Sample QB", "NFL", "Passing Touchdowns", "Over", 1.5, -120, "Sample FC vs Demo FC"),
    ("Sample RB", "NFL", "Rushing Yards", "Over", 74.5, -110, "Demo FC vs Sample FC"),
    ("Sample RB", "NFL", "Rushing Yards", "Under", 74.5, -110, "Demo FC vs Sample FC"),
    ("Sample WR", "NFL", "Receiving Yards", "Over", 64.5, -115, "Demo FC vs Sample FC"),
    ("Sample WR", "NFL", "Receptions", "Over", 4.5, -110, "Demo FC vs Sample FC"),
    ("Sample WR", "NFL", "Anytime Touchdown", "Over", 0.5, +140, "Demo FC vs Sample FC"),
    ("Demo TE", "NFL", "Receiving Yards", "Over", 44.5, -110, "Demo FC vs Sample FC"),
    ("Demo TE", "NFL", "Receptions", "Over", 3.5, -120, "Demo FC vs Sample FC"),
    ("Sample Kicker", "NFL", "Anytime Touchdown", "Over", 0.5, +900, "Sample FC vs Demo FC"),
]


def demo_props() -> list[Prop]:
    return [
        Prop(
            player=player,
            sport=sport,
            prop=prop,
            side=side,
            line=line,
            odds=odds,
            bookmaker="Demo Book",
            event=event,
            source="demo",
        )
        for player, sport, prop, side, line, odds, event in _SAMPLE
    ]
