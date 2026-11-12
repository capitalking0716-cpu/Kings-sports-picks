# 👑 Kings Sports Books Picks

Neko's own sports betting picks platform — like Props Edge, but his.
Built by Lilly, his picks AI. **Runs as a private Streamlit Cloud app (owner and
invited viewers only). Paper tracking only: this app never places a real bet
and never moves money.**

- **Sports:** NBA + NFL to start (both in season in November). NHL later.
- **What it builds, every run:** 2 parlays of 10 picks, 3 of 5 picks, 3 of 3 picks —
  always the combination with the **highest overall win chance**.
- **Lilly never promises wins.** Not in the app, not in the code, not in her chat.

## Honest limits — read this first

1. **No live data until the key is set.** Without a free Odds API key the app runs in
   clearly-labelled **DEMO MODE** with sample players and sample odds. Demo picks are
   NOT live picks, and the ledger records them as `mode="demo"` so they can never be
   mistaken for a real track record.
2. **10-pick parlays are long shots, even optimized.** Ten picks that each win 60% of
   the time only win together about **0.6% of the time** (1 in about 166). Lilly shows
   the true overall chance on every parlay card. The 3-pick parlays are the steadiest.
3. **Most live picks are odds-only.** The probability comes from the sportsbook odds
   (with the bookmaker's cut taken out). There is **no free game-log source** right now:
   balldontlie's free key does not include per-game player stats — that endpoint is
   paid. So the app does NOT invent recent-form numbers. A pick with no form data is
   labelled **"odds-only, no recent-form data"** and Lilly's verdict for it is never
   "good play". If a paid balldontlie key is added later, form data blends in (half
   odds, half recent games).
4. **Player props cost API quota per game.** The free tier is 500 requests/month.
   Props live only on the per-event endpoint, so the app caches results for 6 hours
   and only refreshes when you tap Refresh. Do not tap Refresh over and over.
5. **This is not a way to make steady money.** Even the best picks lose sometimes.
   Never bet money you cannot afford to lose completely, and never chase a loss.

## How to run it on your PC

Phone-first, big buttons. You only need Python on the PC.

```bash
cd kings-sports-picks
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Mac / Linux
pip install -r requirements.txt
streamlit run app.py
```

Then open the address Streamlit prints (usually http://localhost:8501) in your browser.

## Profiles (King & Kohen)

The app opens with a login screen. There are exactly two profiles:

- **👑 King** — Neko
- **Kohen** — his son

Tap your profile, type your **4-digit PIN**, and you're in. The picks pages
(Dashboard, Props) and Lilly's chat are shared — both profiles see the same
slates. **Results / Ledger is per-profile:** parlays recorded while King is
signed in count only in King's record, same for Kohen, and each profile's hit
rates are figured from its own record. Entries saved before profiles existed
have no tag and count as King's.

**PINs live only in Streamlit Secrets** — never in the code, never in this
repo, never in chat. The owner sets them in the app's **Settings → Secrets**
(on Streamlit Cloud) or in the local `.streamlit/secrets.toml`, as two lines:

```toml
KING_PIN = "...."
KOHEN_PIN = "...."
```

(replace the `....` with each person's own 4 digits, and keep the quotes).

If those two lines are missing, the app shows a "Profiles not set up yet"
screen and lets nobody in — there is no default PIN and no way around the
login. Use **Log out / switch profile** in the sidebar to change who is
signed in.

## How to get the free Odds API key — 5 steps

1. Go to **https://the-odds-api.com** in your browser.
2. Tap **Sign Up** and make a free account (email + password). **No credit card needed.**
3. Open your dashboard — your API key is shown there. Copy it.
4. In this folder, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
   and paste your key in place of the sample text:
   `LILLY_ODDS_API_KEY = "paste-your-key-here"`
5. Restart the app. The Dashboard banner turns green and says **LIVE PROPS**.

What the free key gives you: **500 requests a month** (resets on the 1st), live player
props for NBA and NFL from US sportsbooks. What it does **not** give you: player game
logs, or historical odds (paid add-on).

## How the model works (plain words)

1. Take the odds the sportsbook offers (like -110) and turn them into a plain chance
   (like "wins about 52 times out of 100"). Take the bookmaker's cut out first, using
   both sides of the Over/Under line, so the two sides add up to 100%.
2. If real recent games exist for a player, blend them in: half what the odds say,
   half what the player just did. Right now that data does not exist on the free tier,
   so step 2 is skipped and the pick is labelled odds-only.
3. Rank every pick by chance, best first. A parlay of N picks is the N best picks
   (never the same pick twice in one parlay). Its overall chance is the picks'
   chances multiplied together — that is why long parlays get tiny so fast.

Every pick shows: player, prop, line, odds, estimated chance, and WHY it was picked
in plain words, plus Lilly's verdict (good play / fix this first / bad play).

## Lilly

Risk-first, plain-spoken, hates losing money. She argues against a bad pick before
she argues for one. Her chat works **offline** — no LLM key needed for her core job.
Her brief, her chat, and her verdicts are rule-based. If an LLM key is ever added, it
may only restate her verdict in her voice — never change it.

## Pages

- **Dashboard** — today's parlays, with true overall chance and expected value.
- **Props** — every prop Lilly can see, with its chance and why.
- **💜 Lilly** — her brief, always-on chat, and the "Run the algorithm" button.
- **Results / Ledger** — mark picks won/lost after the games; hit rates per parlay
  size, per pick, and overall. Saved locally in `data/ledger.json`.
- **Setup** — key status, how to get the free key, and the membership plan note.

## Membership plan (NOT built yet)

Neko's plan: sell this as a **$25/month membership ONLY after consistent picks are
demonstrated** in the ledger. No paywall, no Stripe, no payments are built into this
app. The ledger record is what earns the right to sell it later.

## A note about Kohen

Kohen is Neko's son. He is **21 (confirmed 2026-11-10)** — of legal betting age
in Michigan. Neko gifts him access through his own **Kohen profile** (4-digit
PIN) in this app, plus a viewer invite to the private Streamlit app so he can
open it on his own phone. His Results/Ledger record is separate from King's.

## Files

```
app.py                    Streamlit app (phone-first, black / purple / gold)
kings_picks/
  odds_api.py             The Odds API client (cached, quota-safe)
  form_data.py            Optional balldontlie recent-form (paid tier only; honest when absent)
  model.py                Transparent probability model (odds -> chance, vig removed)
  parlays.py              Parlay builder (highest overall chance, no duplicates)
  lilly.py                Lilly — brief, verdicts, offline chat
  ledger.py               Results ledger (local JSON, per-profile records)
  profiles.py             King & Kohen PIN login (PINs live only in Streamlit secrets)
  demo_data.py            Clearly-labelled sample data for demo/offline mode
tests/                    pytest suite (must stay green)
data/                     Local cache + ledger (gitignored, never committed)
.streamlit/
  secrets.toml.example    Template — copy to secrets.toml, paste your real key
```

## Tests

```bash
pytest -q
```

The suite covers: odds math, vig removal, odds-only vs form scoring, parlay math
(including ten 60% picks ≈ 0.6%), max-chance selection, the no-duplicate rule,
Lilly's verdicts and her never promising wins, the ledger, demo labelling, and
the odds-feed parser.
