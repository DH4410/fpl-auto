# FPL Bot — Research and Architecture

Starting-point research document for the Fantasy Premier League advisory bot in
[`bot/`](../../bot). Written 2026-07-31, before the 2026/27 season begins.

> **This bot never executes transfers.** Every module produces recommendations
> for a human to review and act on. No code in `bot/` calls an FPL write
> endpoint.

> **No API keys, anywhere.** Every data source is a public, unauthenticated HTTP
> GET. No accounts, no paid feeds, no subscriptions. The bot runs on a fresh
> machine with nothing but `pip install -r requirements_bot.txt`.

---

## 1. Project goal

Recommend, each gameweek:

1. which 15 players to own,
2. which 11 to start, and who to captain,
3. which transfers to make, and whether a −4 hit is worth taking,
4. when to play each of the eight chips.

The target is a decision-support tool whose reasoning is auditable. Every
projection can be decomposed into the components that produced it, so when a
recommendation looks wrong you can see whether the error came from minutes,
attack, defence or bonus.

---

## 2. Architecture

Six modules, each with a single responsibility.

```
bot/
├── fpl_rules.py           Exact 2026/27 ruleset — scoring, squad legality,
│                          chips, transfers, selling price, auto-subs
├── data_collector.py      Cached ingest: FPL API, Vaastav CSVs,
│                          football-data.co.uk odds, FPL-Core-Insights
├── feature_engineering.py  Module 1 — EWMA form, Dixon-Coles ratings,
│                          de-vigged odds, promoted priors, news sentiment
├── models.py              Module 2 — four ML sub-models + points predictor
├── simulator.py           Module 3 — bivariate Poisson + player attribution
├── optimizer.py           Module 4 — MIP squad/XI/transfer/chip optimisation
└── rl_agent.py            Module 5 — PPO chip-timing agent
```

`notebooks/fpl_bot_v1.ipynb` is a thin driver: it imports these modules and
displays results. It contains no algorithms.

### Why components rather than direct points regression

The obvious approach — regress `total_points` on features — throws away the
structure FPL gives you for free. A defender's score is a *deterministic*
function of minutes, goals, assists, clean sheet, defensive contribution, cards
and bonus. Each of those has different drivers, different noise, and different
predictability. Modelling them separately and composing through the exact rules
means:

* the scoring rules are never approximated, only the inputs are;
* a rule change (as happened with defensive contribution in 2025/26) is a
  constant edit, not a retraining problem;
* projections are auditable component by component.

### Data flow

```
FPL API ─┐
Vaastav ─┼─► data_collector ─► feature_engineering ─► models ─┐
odds CSV─┘                              │                     │
                                        └──► simulator ◄──────┘
                                                  │
                                                  ▼
                                             optimizer ──► recommendation
                                                  ▲
                                             rl_agent (chips)
```

---

## 3. Data sources

| Source | Used for | Auth | Verified |
|---|---|---|---|
| FPL `bootstrap-static` | Current pool, prices, teams, availability | None | 564 players, 20 teams, 38 GWs |
| FPL `fixtures` | Fixture calendar, FPL's own difficulty ratings | None | 380 fixtures |
| FPL `element-summary/{id}` | Per-player history and upcoming fixtures | None | Yes |
| FPL `event/{gw}/live` | Live gameweek scoring | None | Returns empty preseason — expected |
| FPL `team/set-piece-notes` | Penalty and set-piece duty | None | Yes |
| Vaastav `merged_gw.csv` | Training labels, 2024-25 + 2025-26 | None | 27,605 + 29,757 rows |
| football-data.co.uk | Historical closing odds, shots, cards | None | 380 rows/season, Pinnacle closing prices |
| FPL-Core-Insights | Secondary CSV warehouse | None | Best-effort, path fallbacks |

### The endpoint path that is easy to get wrong

Set-piece notes live at **`/api/team/set-piece-notes/`**. The shorter
`/api/set-piece-notes/` returns HTTP 404. Verified both.

### The data finding that shaped Module 2

**2024/25 Vaastav data contains no defensive-contribution columns at all.**
Verified directly against the CSV headers:

| Season | `clearances_blocks_interceptions` | `recoveries` | `tackles` | `defensive_contribution` |
|---|---|---|---|---|
| 2024-25 | absent | absent | absent | absent |
| 2025-26 | present | present | present | present |

The DC rule did not exist in 2024/25, so the statistic was never recorded. This
matters because the original project spec proposed falling back to "computing
CBIT from `clearances_blocks_interceptions`" — **that fallback is impossible**,
because the column it depends on is itself absent from that season.

What was done instead:

* `fpl_rules.py` encodes the DC scoring rule exactly (DEF +2 at CBIT ≥ 10,
  MID/FWD +2 at CBIRT ≥ 12) regardless of training data, since it is the
  2026/27 rule.
* `DefenseModel` trains its DC head **only on rows that actually carry a DC
  label** — 9,591 of them, all from 2025/26 — and falls back to positional rate
  priors for anyone else.
* Missing DC values are **never zero-filled**. Zero would assert "this defender
  made no clearances", which is false rather than unknown, and would bias every
  defender's projection downward.

`data_collector.load_multi_season_history` reports per-season column coverage in
`df.attrs["coverage"]` so this stays visible rather than silent.

### A second trap: FPL team ids are not stable

FPL reassigns team ids alphabetically **every season** over that year's 20
clubs. Team id 9 in 2024/25 is a different club from team id 9 in 2026/27.
Fitting ratings on historical data keyed by FPL id and joining them to the
current season silently attributes one club's strength to another — with no
error and no obvious symptom.

`feature_engineering.fit_team_strength_by_name` is the season-safe path: it fits
on club *names* (stable across seasons) and maps to current ids explicitly,
dropping clubs that have since been relegated. On 2024/25 data it maps 16 of 20
clubs; the 4 unmapped are Leicester, Southampton, West Ham and Wolves, none of
which are in the 2026/27 Premier League. The 4 promoted clubs that appear in
their place (Coventry, Hull, Leeds, Sunderland) are seeded by
`promoted_team_priors`.

---

## 4. Techniques implemented

### Module 1 — Feature engineering

| Technique | Notes |
|---|---|
| **EWMA form** (α = 0.25, ≈7-GW window) | Shifted one gameweek within each player. Verified: 100% of first-appearance rows have NaN form, so no label leaks in. |
| **Rolling windows** (3, 6 GW) | Given alongside EWMA — after an injury layoff the window mean collapses while the EWMA decays slowly, and the difference is informative. |
| **Dixon-Coles ratings** | MLE with the low-score τ correction and exponential time decay (ξ = 0.0018). Attack constrained to sum to zero; defence left free so its mean sets the goal level. |
| **Shin odds de-vigging** | Strips the bookmaker overround, correctly shifting mass toward favourites relative to proportional normalisation. |
| **Market-implied goal rates** | Poisson inversion of de-vigged 1X2 + over/under 2.5 into (λ_home, λ_away), feedable straight into the simulator. |
| **Promoted-team priors** | Worst-quartile seeding, not league-average. |
| **Keyword news sentiment** | Domain-specific word list with negation handling, scoring FPL's free `news` field. |

**Validation of the Dixon-Coles fit.** The fitted home advantage on 2024/25 was
0.066. The empirical log-ratio of home to away goals that season was 0.063 — the
fit recovers the data. (2024/25 genuinely had unusually weak home advantage; the
low number is correct, not a bug.)

### Module 2 — ML models

| Model | Target | Algorithm | Design note |
|---|---|---|---|
| `MinutesModel` | Minutes 0–90, plus P(60+) | XGBoost | Custom asymmetric objective, over-prediction penalised 3× |
| `AttackModel` | xG/90, xA/90 | LightGBM ×2 | Per-90 rates decouple attack from minutes, so they compose multiplicatively |
| `DefenseModel` | Clean-sheet prob, DC/90 | XGBoost | DC head trains on 2025/26 only; positional priors otherwise |
| `BonusModel` | BPS, then bonus | XGBoost + empirical curve | Bonus is a within-match *ranking*, so BPS alone is half the job |

**The asymmetric minutes loss works as designed.** Over-predicting minutes is
worse than under-predicting: a player you expect to start who is benched
delivers zero, *and* you already spent budget on him. Measured mean bias across
walk-forward periods is **−6.8 minutes** — the model is systematically
conservative, which is the intent.

### Module 3 — Simulation

Bivariate Poisson scorelines; team goals split among players by a **vectorised
multinomial** that conserves the team total exactly (independent per-player
Poisson draws would not, and would break the team/player correlation that
captaincy variance depends on).

There are no Python loops over simulation draws anywhere. The one loop in the
attribution walks the *goal index* — at most a handful of goals per match — so
it runs 0–8 times regardless of whether you draw 1,000 or 100,000 simulations.

Measured runtime: 100,000 fixture simulations in under 1 ms; a full 564-player
gameweek at 50,000 sims in ≈8 s using a 113 MB float32 array.

**An honest negative result.** Fitting λ₃ (the shared covariance term) on real
Premier League results drives it to **0.000** — the model collapses to
independent Poisson. This is correct, not a fitting failure: real scorelines are
mildly *negatively* dependent, and a bivariate Poisson can only express
*positive* correlation. The dependence that does exist is captured instead by the
Dixon-Coles τ correction in the ratings fit. Keeping the bivariate structure
costs nothing and makes the model ready for competitions where the positive
correlation does appear.

### Module 4 — Optimisation

Mixed-integer programming via PuLP. `get_solver()` probes for HiGHS and falls
back to CBC. **`gurobipy` is never imported** — it is commercial.

Squad selection is a multi-dimensional knapsack: budget, positional quota,
three-per-club cap and formation legality all interact. Greedy "points per
million" fails on it because the cheap enabler that unlocks a premium striker is
invisible to a greedy rule.

**The selling-price rule is implemented correctly**, which is where naive
optimisers leak budget. A player keeps his purchase price plus 50% of profit,
rounded *down* to £0.1m; price falls are absorbed in full:

| Paid | Now | Sells for | |
|---|---|---|---|
| £5.0m | £5.5m | **£5.2m** | not £5.5m |
| £5.0m | £5.4m | £5.2m | rounds down |
| £5.0m | £4.7m | £4.7m | loss absorbed fully |

All money arithmetic is done in **integer tenths**, matching FPL's own
representation, because float arithmetic on £m values produces off-by-£0.1
errors on exactly this rule.

### Module 5 — RL chip agent

PPO over a Gymnasium environment where one episode is a 38-gameweek season.
Chip timing is the one decision a myopic optimiser handles badly: spending Bench
Boost in GW7 forecloses the GW34 double, and the choice must be made before the
fixture swings are known — a Markov decision process.

`baseline_chip_policy` is a hand-written heuristic (hold chips for doubles,
never let one expire) included deliberately as a benchmark: **an RL agent that
cannot beat it is not worth deploying.** Measured over 200 simulated seasons,
the heuristic beats never-playing-chips by ≈51 points/season.

---

## 5. Backtest results

Walk-forward validation with an expanding window: at gameweek *t*, train on
everything strictly before *t*, evaluate on *t* alone. Ordinary k-fold
cross-validation is invalid here — shuffling rows lets the model learn from
GW30 to predict GW12, which inflates every metric. This is the single most
common way FPL model backtests lie.

**Setup:** 57,362 player-gameweek rows (2024-25 + 2025-26), 37 features,
24 evaluation periods.

| Target | MAE | Spearman ρ | Bias |
|---|---|---|---|
| Minutes | 14.72 min | **0.755** | −6.8 min (conservative, by design) |
| Total points | 1.75 pts | **0.680** | — |

**Spearman matters more than MAE here.** The optimiser only needs players
*ordered* correctly; it never consumes the absolute point total. A ρ of 0.68 on
single-gameweek points is a reasonable result against a target dominated by
irreducible noise — a striker's return in one match is close to a coin flip, and
no model recovers that.

The negative minutes bias is the asymmetric loss doing its job, not an error.

### End-to-end verification

The full notebook was executed top to bottom on real data. All 39 cells run.
Independently re-validating the optimiser's output against `fpl_rules`:

```
squad legal: True | ok
formation legal: True
max players from one club: 3
squad composition: {'GKP': 2, 'DEF': 5, 'MID': 5, 'FWD': 3}
```

---

## 6. Techniques deferred, and why

| Technique | Status | Reasoning |
|---|---|---|
| **Paid odds APIs** (The Odds API, Betfair, Pinnacle) | **Replaced, not deferred** | All require accounts and keys. football-data.co.uk publishes *Pinnacle closing odds* as free CSVs — the sharpest public price, at no cost. Nothing was lost. |
| **LLM press-conference sentiment** | **Replaced** | Needs an API key. A domain-specific keyword scorer replaces it, and is arguably *better*: general-purpose sentiment models misread football injury text ("returns to full training after surgery" is positive for FPL, negative to SST-2). |
| **Local transformer sentiment** | Deferred | `distilbert-base-uncased-finetuned-sst-2-english` needs no key, but adds a ~250 MB torch/transformers dependency to refine an already-weak signal, and has the domain-mismatch problem above. TODO documented at the plug point. |
| **Effective ownership (EO)** | Deferred | EO drives *rank* strategy, not raw points, so it belongs in the optimiser's objective rather than the feature matrix. Obtainable free by sampling `/leagues-classic/314/standings/` + `/entry/{id}/event/{gw}/picks/` — many polite requests, no key. |
| **Shot-level xG / spatial models** | Deferred | The free `understat` package provides shot coordinates with no key, but needs an asyncio entry point and fuzzy name matching. Aggregate xG from the FPL API covers the current use. |
| **Transfermarkt squad churn** | Deferred | Preseason arrivals/departures appear in no FPL feed, so a rebuilt squad looks identical to a stable one. Scrapeable without a key. `promoted_team_priors` partially covers the worst case. |
| **Survival analysis for injuries** | Deferred | `lifelines` is in requirements but unused. Would model return-date hazard rather than treating `chance_of_playing` as static. |
| **Full BPS simulation** | Deferred | Exact bonus needs all 22 players' BPS simulated per fixture. Approximated from each player's own involvement — a real accuracy cost, and the most defensible next upgrade. |

---

## 7. Known limitations

1. **No 2026/27 gameweek data exists.** The season has not started;
   `event/1/live` returns an empty list. Models are trained on 2024-25 and
   2025-26 and applied out of sample to a new season with 4 promoted clubs.
   Early-season projections deserve wide error bars.
2. **Defensive contribution has one season of history.** The DC rule began in
   2025/26, so the DC head has 9,591 training rows from a single season and
   cannot yet distinguish a stable role from a one-year anomaly.
3. **Bonus is approximated.** See above — a genuine accuracy cost.
4. **Preseason `bootstrap-static` stats are carried reference values,** not
   2026/27 scoring. Treating them as current-season form would be wrong.
5. **The RL agent trains on simulated seasons** and inherits every bias in the
   simulator. Its output should be compared against
   `optimize_chip_usage` and the hand-written heuristic, not trusted blindly.
6. **Wildcard and Free Hit values in the RL environment are crude** — modelled
   as a percentage lift rather than by re-optimising the squad, which is what
   they actually do.
7. **No opponent modelling.** The bot optimises expected points, not rank
   against a specific mini-league. Without EO these differ substantially.

---

## 8. Upgrade path

In descending order of expected value per unit of work:

1. **Full BPS simulation** — removes the largest known approximation.
2. **Effective ownership** — switches the objective from points to rank, which
   is what actually determines whether a season feels successful. Free to
   obtain, just slow.
3. **Blend market odds with fitted ratings** — markets dominate at short
   horizons, fitted ratings extrapolate better past ~2 gameweeks. The
   de-vigging and Poisson inversion already exist
   (`feature_engineering.odds_features`); only the blending weight is missing.
4. **Injury hazard model** — `lifelines` survival analysis on return dates,
   replacing the static availability percentage.
5. **Multi-period transfer planning** with explicit price-change modelling —
   team value compounds over a season.
6. **Shot-level xG** via the free `understat` package.
7. **A proper wildcard model in the RL environment** — re-optimise the squad
   inside the step function rather than applying a flat lift.

---

## 9. What this approach gets right, and what could be improved

| Area | Gets right | Could be improved |
|---|---|---|
| **Rules** | Encoded exactly, including auto-subs with a closed goalkeeper slot and the selling-price floor in integer tenths | Chip-stacking interactions (BB in a double gameweek with an already-boosted bench) are not modelled jointly |
| **Data honesty** | Missing DC data reported, never zero-filled; team-id instability handled by name-based fitting | Only two seasons of training data; no cross-league transfer |
| **Leakage** | EWMA features shifted; walk-forward validation with expanding windows; verified 100% NaN on first appearances | Feature selection was done once over the whole set, a mild form of selection leakage |
| **Uncertainty** | Full simulated distributions, not just means; P80/P90 and haul/blank probabilities reported | Model parameter uncertainty is ignored — the sim treats predicted rates as known |
| **Optimisation** | Exact MIP; correct budget accounting; free solvers only | Single-period objective with a discount, rather than true multi-period lookahead over transfers |
| **Captaincy** | Judged on upside and blank risk, not just mean | No explicit consideration of what rivals are captaining |
| **Cost** | Runs entirely on free public data — no keys, accounts or subscriptions | Some free sources need fuzzy name matching, which is a silent failure mode |
| **Safety** | Advisory only; nothing auto-executes | — |

---

## 10. Reproducing

```bash
pip install -r requirements.txt -r requirements_bot.txt
jupyter notebook notebooks/fpl_bot_v1.ipynb
```

Or open the notebook in Google Colab — cell 0 clones the repo and installs
dependencies. First run downloads ~10 MB of CSVs and caches them in `bot/cache/`
(git-ignored); later runs are served from cache for 6 hours.

Section 7 (RL training) needs `stable-baselines3`, which pulls in torch. It is
wrapped in a try/except and skipped cleanly if absent — every other section runs
without it.
