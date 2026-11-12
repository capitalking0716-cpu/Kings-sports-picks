# Lilly's Projection Model (v2) — Calibration Proof

**Date:** 2026-11-12 · **Module:** `kings_picks/projection.py` ·
**Harness:** `validation/run_calibration.py` (re-runnable, uses the live free feeds)

## The short version

The new projection model was tested against **10,969 real player-games**
(NFL 2,553 · NBA 8,416), predicting each game using only the games
before it. It **beat the old method overall and clearly in the NBA,
and tied it in the NFL** — and the old method turned out to be worse
than a forecaster who always says "50%". But the new model is **not
perfectly calibrated either**: it calls Overs a few points too
confidently in the middle range, because real player stats are
lopsided (a few huge games pull a player's average above his typical
game). Full numbers and the honest verdict are below. **No constants
were tuned after seeing these results** — the recipe below is the
one written down before the test ran.

## The recipe being tested (v2)

1. **Project** the next game from three averages of the player's own
   log: whole season × 0.30, last 10 games × 0.30, last 5 games × 0.40
   (recent games vote biggest, the season anchors).
2. **Measure his wiggle**: his game-to-game standard deviation,
   steadied for small samples by blending toward a typical wiggle
   (coefficient of variation 0.40, as if 5 extra typical games had
   been seen), with floors so no player is ever treated as a robot
   (spread at least 0.5, and at least 10% of the projection).
3. **Convert to a chance** with a bell curve over (projection,
   wiggle). Whole-number lines get honest push handling (landing
   exactly on the line counts as half). The binary Anytime
   Touchdown log uses a smoothed scoring rate instead of the curve.
   Probabilities are clamped to 1%–99% — never certainty.
   Fewer than 5 prior games → no prediction at all.

**v1 (the old ingredient)** is what production blends from today:
of the player's last 10 prior games, the share that went over the
line — a raw hit-rate.

## How the test worked

- **Walk-forward, no look-ahead.** Every game was predicted from
  strictly earlier games of the same player and stat.
- **The test line is synthetic — read this before the numbers.**
  Historical bookmaker lines are paid data Lilly doesn't have, so
  each game was tested against a line built from the player's own
  prior-games average, rounded to the nearest 0.5 (Anytime
  Touchdown: fixed at 0.5, the only real line that stat has). This
  validates the probability **math against reality**. It does not
  validate anything against bookmakers, and nothing in this report
  claims an edge over them. Real bookmaker lines are set nearer a
  player's *typical* (median) game than his average, so the exact
  biases below would not transfer one-for-one to real lines.
- **Same games for both methods.** A game counts only once the
  player has ≥ 5 prior games at that stat and has actually recorded
  the stat before — no credit for predicting a player won't do
  something he never does.
- **Outcome:** 1 = went over, 0 = stayed under, 0.5 = push
  (pushes were 3.8% of NFL tests, 7.9% of NBA tests — the push
  handling is not theoretical).
- **Score:** the Brier score — average squared distance between
  the predicted chance and the outcome. Lower is better; always
  saying 50% scores 0.25 before pushes.

## Results

### NFL (nflverse, season 2026) — 2,553 player-games

Brier: **v2 0.2311** · v1 0.2315 · always-50% 0.2405

| Predicted | Method | Games | Avg predicted | Actually went over |
|---|---|---|---|---|
| under 40% | v2 | 487 | 29.3% | 25.6% |
| 40-50% | v2 | 834 | 46.2% | 36.6% |
| 50-55% | v2 | 872 | 51.7% | 38.2% |
| 55-60% | v2 | 273 | 56.7% | 41.9% |
| 60-70% | v2 | 53 | 62.5% | 52.8% |
| 70% + | v2 | 34 | 75.5% | 38.2% |
| under 40% | v1 | 1,280 | 24.1% | 32.5% |
| 40-50% | v1 | 588 | 41.0% | 37.5% |
| 50-55% | v1 | 281 | 50.0% | 42.3% |
| 55-60% | v1 | 97 | 57.1% | 36.6% |
| 60-70% | v1 | 244 | 61.7% | 39.5% |
| 70% + | v1 | 63 | 80.0% | 49.2% |

### NBA (stats.nba.com, season 2026-27) — 8,416 player-games

Brier: **v2 0.2301** · v1 0.2509 · always-50% 0.2303

| Predicted | Method | Games | Avg predicted | Actually went over |
|---|---|---|---|---|
| under 40% | v2 | 579 | 33.9% | 39.4% |
| 40-50% | v2 | 3,263 | 46.1% | 45.1% |
| 50-55% | v2 | 2,995 | 52.0% | 46.2% |
| 55-60% | v2 | 1,273 | 57.0% | 51.5% |
| 60-70% | v2 | 298 | 62.4% | 53.5% |
| 70% + | v2 | 8 | 73.6% | 50.0% |
| under 40% | v1 | 3,340 | 28.0% | 47.1% |
| 40-50% | v1 | 2,261 | 41.6% | 45.7% |
| 50-55% | v1 | 1,301 | 50.0% | 47.0% |
| 55-60% | v1 | 569 | 56.5% | 43.4% |
| 60-70% | v1 | 840 | 61.8% | 47.1% |
| 70% + | v1 | 105 | 74.5% | 42.9% |

### Both leagues — 10,969 player-games

Brier: **v2 0.2303** · v1 0.2464 · always-50% 0.2327

Weighted bucket calibration error (average distance between the
bucket's predicted and actual rates): NBA — v2 **0.040** vs v1
0.119. NFL — v2 0.108 vs v1 0.095.

## Reading the results honestly

1. **The old method is noise in a suit.** A 10-game hit-rate is
   mostly luck: v1's "under 40%" NBA calls (average prediction
   28%) actually went over **47.1%** of the time, and its "70%+"
   calls landed **42.9%**. Overall and in the NBA, v1 scored
   **worse than always saying 50%**. The ingredient production
   blends from today is, on this evidence, a liability.
2. **Averages are a high bar.** Only **46.4%** of NBA games and
   **36.0%** of NFL games beat the player's own prior average —
   player stats are right-skewed: a few spike games drag the
   average above the typical game, and the NFL feed's long tail
   of part-time players makes it worse. Any symmetric model
   tested against average-set lines fights this current.
3. **v2 ranks sensibly but leans optimistic in the middle.**
   Its actual over-rates rise with its predictions (NBA:
   39% → 45% → 46% → 52% → 54%), so the ordering is real — but
   a "52%" call landed 46%, a "57%" call landed 51.5% (NBA);
   the NFL mid-range gap is bigger (51.7% called, 38.2% landed).
   That is the skew from point 2 showing up in a symmetric bell
   curve, and it is the model's known weakness, recorded here —
   not tuned away after the fact.
4. **Small buckets are decoration.** The 70%+ rows rest on
   8–63 games. Ignore them.
5. **Sample caveats.** Both seasons are young (NFL ~10 weeks,
   NBA ~3 weeks in), so most series contribute only a handful of
   testable games. The picture can shift as the logs grow.

## Verdict

- **NBA: v2 wins, clearly.** Better Brier (0.2301 vs 0.2509),
  one-third of v1's calibration error, monotone buckets — and
  v1 was worse than a shrug.
- **NFL: a tie on Brier (0.2311 vs 0.2315), no winner on points.**
  v2's ordering is more sensible and v1's extremes are wilder,
  but neither method is well calibrated against average-set
  lines in a league where only 36% of games clear them. v1 is
  marginally better on the secondary bucket-error metric
  (0.095 vs 0.108); call the NFL what it is — **unproven
  either way, v2 not worse**.
- **Overall: v2 beats v1** (0.2303 vs 0.2464), and it is the
  only method of the two that never loses to the always-50%
  baseline in any league.

**Recommendation: v2 earned wiring into live picking as the
replacement for the raw hit-rate ingredient** — it beat or tied
the old method everywhere on identical games, and its mistakes
are smooth and explainable instead of wild. It should go in
**with its warning label attached**: its probabilities run a
few points optimistic on Overs (more in the NFL), so a v2
"57%" should be read as "a real edge over a coin flip, not a
promise". The principled next improvement — a skew-aware
distribution (e.g. modelling on a log scale or a
negative-binomial count model) and/or centring on the median
game — is recorded here as future work. It was deliberately
**not** bolted on after seeing these numbers: tuning against
the same test data until the report turns green would make
the proof worthless.

## Reproduce

```
cd ~/workspace/goals/5-ai-operated-companies-at-10k-month-each/files/kings-sports-picks
../vaulttec/.venv/bin/python validation/run_calibration.py
```
