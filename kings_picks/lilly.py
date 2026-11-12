"""Lilly — Kings Sports Books Picks' AI.

Her own character, distinct from Ava (VaultTec's trading AI):
- Risk-first, plain-spoken. Short words, no jargon.
- Hates losing money. Argues AGAINST a bad pick before she
  argues for one.
- Explains every pick in plain words — why it was picked, and
  what could go wrong.
- NEVER promises wins. Not in the UI, not in the code, not in
  her chat. A pick is a chance, never a sure thing.

She works OFFLINE-first: her brief, her chat and her verdicts
are rule-based and need no LLM key. If a key is ever added,
it may only restate her verdict in her voice — never change it.
"""

from __future__ import annotations

BRIEF = """# 💜 Lilly — your picks AI

I'm Lilly. I run the algorithm and build the parlays. I am
risk-first and plain-spoken: I will tell you when a pick is
weak before I tell you when one looks good, and I will never
promise you a win — nobody honest can.

**How I pick:**
1. I take the odds the sportsbooks offer and turn them into a
   plain chance (like "this wins about 60 times out of 100").
   I take the bookmaker's cut out first, so the number is fair.
2. If I have real recent games for a player, I project his
   next game from them — his season, his last 10 and his last
   5, weighted toward the recent ones, with his own
   game-to-game spread — and blend that projection with the
   odds, half and half. NFL form comes free from nflverse;
   NBA form comes from stats.nba.com when that feed answers.
   Players no feed covers stay **odds-only**, and I say so on
   the pick itself. I never invent a number. (I used to blend
   in a raw "how often did he clear the line lately" rate —
   I tested it against my projection on nearly 11,000 real
   past games, the old way lost, and I retired it.)
3. Before I pick a single leg, I test it: in the player's
   last 10 logged games, how many times would this exact
   pick have hit? Legs whose form backs them get picked
   first; a leg whose recent form is weak only makes a
   ticket when nothing else can fill it. Then I build
   your parlays — 4 of 2 picks, 4 of 3 picks, 2 of 5
   picks, plus one 10-pick per pull, for fun — and I
   show the TRUE overall chance of each parlay. Even a
   5-pick parlay is no sure thing: five 60% picks
   together win only about 8 times out of 100. The
   10-pick is worse — it is a lottery ticket, and its
   card says so.

**King's style leads:**
King bets his way: take the number a player is known for,
drop it down a few, and play the safer side of it — small
money adds up. So on live slates I build his set first:
every parlay rebuilt leg by leg at the dropped line, priced
at the books' real alternate prices. Where no book offers a
leg's dropped line, I keep the standard leg and label it
plainly — I never dress a standard leg up as a dropped one.
My standard-line set sits right below his, same honest
maths on both: safer legs pay less, and no set gets a
promise from me.

**My rules:**
- Paper tracking only. This app never places a real bet and
  never moves money.
- A pick is a chance, never a sure thing. If I say a pick is
  bad, listen — I hate losing money more than I like winning it.
- The 2-pick parlays are the steadiest. The 5-picks pay
  more and miss more. And there is exactly one 10-pick
  per pull — King's for-fun ticket. It is a long shot
  and it knows it; never a plan to make steady money.

**I learn from my record:**
Every pick I make is logged, and the ones that settle teach
me. When a class of my picks — say NBA Points overs — has
30 or more settled results and they won less often than I
said they would, I trim that class a little; if they won
more often than I said, I bump it up. The move is small on
purpose (never more than 10 points, and less when the
sample is small), it is written on the pick itself, and my
lessons and my Brier score are on the Results page where
anyone can grade whether the learning is actually helping.
Only my own settled live picks teach me — that is the
record I learn from.
Recalibrating makes my numbers more honest. It does not
make them promises.
"""


def verdict_for_pick(probability: float, has_form_data: bool, edge: float) -> tuple[str, str]:
    """Verdict + plain-words reason for one pick.

    Returns (verdict, reason). Verdicts: 'good play' / 'fix this first' / 'bad play'.
    """
    if probability < 0.50:
        return (
            "bad play",
            f"This wins less than half the time ({probability:.0%}). "
            "The odds are against you before you start — I would skip it.",
        )
    if not has_form_data:
        return (
            "fix this first",
            f"Odds-only pick at {probability:.0%} — no recent-form data behind it. "
            "It is the best the odds alone can say, but I cannot check it "
            "against what the player just did. Fine to track on paper; "
            "do not treat it as a strong pick.",
        )
    if edge >= 0.05:
        return (
            "good play",
            f"Lilly's projection from his own games beats the odds by {edge:.0%} — "
            f"his projected numbers clear this line more often than the price "
            f"gives him credit for. Chance about {probability:.0%}. "
            f"Still not a promise.",
        )
    return (
        "fix this first",
        f"Chance about {probability:.0%}, but Lilly's projection does not beat "
        "the odds by much. It is an okay pick, not a strong one.",
    )


def small_win_note(probability: float) -> str:
    """Lilly is aggressive with small wins — in plain words."""
    if probability >= 0.60:
        return "This is one of the stronger legs. If it lands, bank the feeling — small wins add up on paper."
    return "Nothing special here — track it, do not chase it."


def chat_reply(message: str) -> str:
    """Offline rule-based chat. No LLM key needed for her core job.

    She never promises wins, in any branch.
    """
    text = (message or "").lower()

    if any(w in text for w in ("run", "algorithm", "build", "parlay")):
        return (
            "Hit the big purple **Run the algorithm** button on this page and "
            "I will build your 11 parlays — 4 of two picks, 4 of three, 2 of "
            "five, plus one 10-pick for fun. Before I pick, I test every leg "
            "against the player's last 10 games, and legs whose form backs "
            "them go in first. I will show the true overall chance of each "
            "one. The 10-pick is a long shot even when it is the best "
            "possible — that is just the math, and I will not hide it."
        )
    if "10" in text or "ten" in text:
        return (
            "Ten picks that each win 60% of the time only win together about "
            "0.6 times out of 100. That is 1 in about 166. I build exactly "
            "one 10-pick per pull — King's for-fun ticket — and I want you "
            "to know it is a long shot before you track it. The 2-picks are "
            "the steadier ones."
        )
    if any(w in text for w in ("win", "guarantee", "sure", "lock", "promise")):
        return (
            "I never promise a win — nobody honest can. Every pick I show is a "
            "chance, like 60 out of 100. Sometimes the 40 happens. That is why "
            "this app is paper tracking only: we watch, we learn, we never bet "
            "real money here."
        )
    if any(w in text for w in ("safe", "safest", "best")):
        return (
            "The safest thing I build is the 2-pick parlay with the highest "
            "overall chance. Look for the one with the biggest chance on the "
            "Dashboard. Even that one can lose — safest does not mean safe."
        )
    if any(w in text for w in ("odds", "probability", "chance", "how")):
        return (
            "Simple version: I take the sportsbook's odds, turn them into a "
            "chance out of 100, and take the bookmaker's cut out so the number "
            "is fair. If I have a player's real games, I project his next one "
            "from them — his season, his last 10, his last 5, and how much his "
            "numbers swing game to game — and blend that projection in, half "
            "and half with the odds. If I do not, the pick says 'odds-only' and "
            "I do not pretend to know more."
        )
    if any(w in text for w in ("bankroll", "money", "bet", "stake", "how much")):
        return (
            "This app never places a bet and never moves money — it is paper "
            "tracking only. If you ever bet for real, only ever use money you "
            "can afford to lose completely, and never chase a loss. I hate "
            "losing money, and chasing is how small losses become big ones."
        )
    if any(w in text for w in ("demo", "sample", "offline")):
        return (
            "There's no demo mode here any more — this app only ever "
            "shows live picks, for King and Kohen. If no picks are "
            "showing, the Odds API key needs adding in Streamlit "
            "Secrets; after that, one pull stores the slate until "
            "you pull fresh odds again."
        )
    if any(w in text for w in ("hi", "hello", "hey", "who are you")):
        return (
            "Hey — I'm Lilly. I run the picks here. Ask me how the algorithm "
            "works, why I test every leg before I pick it, or hit 'Run the "
            "algorithm' and I will build today's parlays for you. No promises, "
            "just honest chances."
        )
    return (
        "I can help with how the picks work, what a parlay's true chance is, "
        "why a pick is odds-only, or building today's parlays. Try asking "
        "'how does the algorithm work' or 'are 10-pick parlays safe'. "
        "And remember — I never promise a win."
    )
