# FPL-Auto — Agent Reference

> **Read this first.** This file is the single authoritative reference for any AI agent (or developer) working on this codebase. It covers the FPL game rules encoded in the project, the project's goal, module responsibilities, data flows, CI automation, and hard constraints.

---

## 1. Project Goal

Build the best possible Fantasy Premier League squad for the **2026/27 season** by combining:

1. **Historical testing** — backtest ML forecasts and MILP strategies against the complete 2025/26 Vaastav dataset to calibrate model performance before the season starts.
2. **Fixture analysis** — use the fully published 2026/27 fixture list (all 38 GWs + FDR ratings available now, deadline GW1 = ~21 Aug 2026) to identify early-season hauls, rotation threats, and optimal chip windows.
3. **Rolling-horizon live management** — each gameweek, the bot re-forecasts, re-optimises, and writes advisory transfer + captain recommendations; the user reviews and the action is executed (or automated via GitHub Actions).

**Objective:** maximise total FPL points across the 38-GW season. Constraint: the project is wired to a live FPL account — do not execute write endpoints without explicit user approval.

---

## 2. Hard Constraints — What NOT to Touch

| File / Area | Why hands-off |
|---|---|
| `fpl_auth.py` | OAuth login + Playwright cookie capture. Regression here breaks live auth. |
| `fpl_api.py` write calls (`transfer`, `update_picks`) | Direct POSTs to the live FPL account. |
| `app.py` routes `/api/transfer`, `/api/picks` | Trigger live squad changes. |
| `scripts/apply_team.py` | Posts GW1 squad to live account. Do not execute without `--dry-run`. |
| `scripts/post_gw.py` `--auto` flag | Auto-submits transfers without confirmation. |
| `.env`, `.session.json` | Credentials and live session cookies. Never commit, never print. |
| `bot/cache/` | ~600 cached API JSONs. Do not delete — refetching costs rate-limit budget. |

**Model change constraint:** The user requested using `claude-opus-5` for reasoning. This project does not currently call the Claude API — all text generation is rule-based (see `bot/reporter.py`). If you want to use Opus for analysis, use Claude Code's `/model opus` command in your session, or spawn a subagent with `model: "opus"`.

---

## 3. FPL Game Rules (encoded in `bot/fpl_rules.py`)

### Squad Structure
- **15 players total**: 2 GKP, 5 DEF, 5 MID, 3 FWD
- **Starting XI**: 11 players, 4 on bench (ordered bench priority 1–4)
- **Formation constraints**: min 1 GKP, 3 DEF, 2 MID, 1 FWD in the XI; max 1 GKP, 5 DEF, 5 MID, 3 FWD
- **Budget**: £100.0m (stored as `1000` integer tenths internally)
- **Club cap**: max 3 players from the same club

### Scoring Matrix

| Event | GKP | DEF | MID | FWD |
|---|---|---|---|---|
| Playing 1–59 min | +1 | +1 | +1 | +1 |
| Playing 60+ min | +2 | +2 | +2 | +2 |
| Goal scored | +6 | +6 | +5 | +4 |
| Assist | +3 | +3 | +3 | +3 |
| Clean sheet (must play 60+) | +6 | +6 | +1 | — |
| 3 saves (GKP only) | +1 | — | — | — |
| Penalty save | +5 | — | — | — |
| Penalty miss | −2 | −2 | −2 | −2 |
| Yellow card | −1 | −1 | −1 | −1 |
| Red card | −3 | −3 | −3 | −3 |
| Own goal | −2 | −2 | −2 | −2 |
| BPS bonus (top 3 per match) | +1/+2/+3 | (same) | (same) | (same) |
| **Defensive contribution (DC)** | +2 | +2 | — | — | 

**DC rule (2025/26+):** A GKP or DEF earns +2 pts for each clearance, block, or interception that directly prevents a goal-scoring opportunity. Introduced 2025/26; the DC sub-model in `bot/models.py` is therefore trained on 2025/26 data only.

### Transfers
- **1 free transfer (FT)** per gameweek, rolls over to max 2
- **Hit (additional transfer)**: −4 pts per transfer beyond free transfers
- **Selling price rule**: if you paid £5.0m and the player is now £5.5m, you sell for £5.2m (original price + 50% of profit, rounded down). Stored in integer tenths: `selling_price = buy_price + (now_cost - buy_price) // 2`.

### Chips (8 total — 2 sets of 4, one per half-season)
- **Half 1**: GW1–19 → Wildcard 1, Free Hit 1, Triple Captain 1, Bench Boost 1
- **Half 2**: GW20–38 → Wildcard 2, Free Hit 2, Triple Captain 2, Bench Boost 2

| Chip | Effect |
|---|---|
| Wildcard | Unlimited free transfers that GW; squad locked in after activation |
| Free Hit | Unlimited free transfers for ONE GW only; squad reverts afterward |
| Triple Captain | Captain scores 3× instead of 2× |
| Bench Boost | All 15 players score (bench included) |

**BGW/DGW strategy**: canonical 3-week chip combo — Wildcard into a DGW (prepare 15 DGW assets), Bench Boost the DGW (all 15 score), Free Hit a BGW (cover blanks). Chip planner (`bot/chip_planner.py`) evaluates timing via MILP over the horizon.

---

## 4. Repository Structure

```
fpl-auto/
├── app.py                  # Flask web UI (port 5000) — advisory only
├── fpl_api.py              # FPL REST API wrappers (read + write)
├── fpl_auth.py             # Auth: email/password + Playwright cookie capture
├── pyproject.toml          # Python project metadata
├── requirements.txt        # Minimal runtime deps
├── requirements_bot.txt    # Full bot deps (xgboost, lightgbm, pulp, etc.)
│
├── docs/                   # Reference documentation
│   ├── FPLrules.md         # Human-readable FPL rules reference
│   └── DATA_SOURCES.md     # Full audit of all data sources (dated 2026-07-31)
│
├── rulesagents.md          # ← YOU ARE HERE
│
├── bot/                    # Core ML + optimisation library
│   ├── fpl_rules.py        # Exact rule arithmetic (scoring, chips, pricing)
│   ├── models.py           # 4 XGBoost sub-models (Minutes/Attack/Defense/Bonus)
│   ├── season_forecaster.py# Lightweight per-player per-GW xPts engine
│   ├── season_planner.py   # Rolling-horizon MILP squad planner
│   ├── optimizer.py        # Single-GW MILP squad optimizer (HiGHS/CBC)
│   ├── chip_planner.py     # Chip timing evaluator
│   ├── updater.py          # SeasonUpdater orchestrator (main pipeline)
│   ├── data_collector.py   # FPL API + Vaastav + FPL-Core-Insights fetchers
│   ├── feature_engineering.py # Feature pipeline for ML training
│   ├── reporter.py         # Narrative Markdown report generator (no AI calls)
│   ├── backtester.py       # Historical season simulation
│   ├── simulator.py        # Monte Carlo / scenario simulator
│   ├── news_collector.py   # Injury/availability news enrichment
│   ├── live_monitor.py     # Real-time GW live-score monitor
│   ├── prediction_adjustments.py # Manual overrides on xPts
│   ├── model_variants.py   # Experimental model variants
│   ├── portfolio.py        # Portfolio/risk analysis tools
│   ├── rl_agent.py         # Experimental RL agent (advisory)
│   ├── cache/              # Cached API JSON files (gitignored)
│   └── models/             # Trained model artifacts (.pkl + .json metadata)
│
├── scripts/                # Standalone entry-point scripts (CLI)
│   ├── train_models.py     # Train/retrain the 4 ML sub-models
│   ├── generate_gw1_squad.py # Generate optimal GW1 squad → research/
│   ├── apply_team.py       # POST GW1 squad to live FPL (use --dry-run first)
│   ├── post_gw.py          # Post-GW transfer pipeline (auth required)
│   ├── analyze_gw_public.py# Post-GW analysis, no auth needed
│   ├── live_check.py       # Live GW score monitor
│   ├── backtest_25_26.py   # Backtest over 2025/26 season
│   ├── backtest_comprehensive.py # Multi-season comprehensive backtest
│   ├── backtest_holdout.py # Holdout-set backtest
│   └── chip_sweep.py       # Sweep chip timing combinations
│
├── research/               # Pre-season research outputs
│   ├── gw1_squad_2026.json # Pre-computed optimal GW1 squad (read by app.py)
│   ├── gw1_final_2026.md   # GW1 research report
│   ├── season_planner_research.md
│   ├── training_results_summary.md
│   └── training_meta_2026.json
│
├── reports/                # Bot-generated reports (committed by CI)
│   ├── season_plan_latest.md
│   ├── reflect_latest.md
│   ├── backtest_2025_26.csv
│   └── comprehensive_backtest_report.html
│
├── notebooks/              # Jupyter exploration notebooks
│   ├── fpl_bot_v1.ipynb
│   ├── fpl_bot_v2.ipynb
│   └── mc_season_2025_26.ipynb
│
├── data/                   # Data warehouse (mostly gitignored)
│   ├── warehouse.duckdb    # gitignored (38MB)
│   └── parquet/            # gitignored raw/staging layers
│
├── templates/              # Flask HTML templates
├── static/                 # Flask static assets
│
└── .github/workflows/
    ├── post_gw.yml         # Runs every 4h: analyze + auto-transfer (needs FPL_REFRESH_TOKEN secret)
    ├── apply_gw1.yml       # One-shot: apply GW1 squad
    └── token_health.yml    # Monthly refresh token health check
```

---

## 5. Core Data Flow

```
FPL API (bootstrap-static, fixtures, my-team, event/live)
    │
    └─► data_collector.py
            │
            ├─► bot/cache/          (6-hour TTL JSON cache)
            │
            └─► SeasonForecaster.forecast()
                    │   Inputs: bootstrap + fixtures
                    │   Output: DataFrame[element, gw, xpts, p_start, p_60, uncertainty]
                    │   GW1: uses ep_next; future GWs: PPG × FDR_multiplier
                    │
                    └─► SeasonPlanner.plan()   (rolling-horizon MILP, 6-GW horizon)
                            │   Decision vars per candidate per GW:
                            │   squad, start, capt, buy, sell, ft_var, hit_var, bank
                            │   Objective: Σ decay^t × (xPts × start + capt_bonus + bench_weight × bench_xPts)
                            │             - hit_cost + ft_value
                            │   Solver: HiGHS → CBC fallback (PuLP)
                            │
                            └─► ChipPlanner.evaluate()
                                    │   Evaluates Wildcard/Free Hit/Triple Captain/Bench Boost timing
                                    │   Returns chip_plan list + per-chip reasoning
                                    │
                                    └─► reporter.build_report()
                                            │   Rule-based Markdown narrative (no AI)
                                            │
                                            └─► reports/season_plan_latest.md
```

---

## 6. ML Models (`bot/models.py`)

Four XGBoost sub-models compose expected FPL points using exact scoring rules:

| Model | Predicts | Training target |
|---|---|---|
| `MinutesModel` | P(minutes ≥ 1), P(minutes ≥ 60) | Binary/regression on Vaastav minutes |
| `AttackModel` | Goals + assists by position | Vaastav offensive stats |
| `DefenseModel` | Clean sheets + DC contributions | 2025/26 only (DC rule new) |
| `BonusModel` | Bonus point probability | Vaastav BPS/bonus history |

**Training data**: Vaastav `fantasy-premier-league` dataset through 2025/26. Stored at `bot/cache/vaastav_*_merged_gw.json`. Retrain with: `python scripts/train_models.py`.

**Asymmetric minute loss**: over-predicting minutes (model says 90, player plays 45) is penalised 3× harder than under-predicting, because missing a key player's blank destroys GW score.

**Trained artifacts**: `bot/models/minutes.pkl`, `attack.pkl`, `defense.pkl`, `bonus.pkl` (+ `.json` metadata).

---

## 7. Key Forecasting Parameters

| Constant | Value | Location | Notes |
|---|---|---|---|
| `DEFAULT_HORIZON` | 6 GWs | `season_forecaster.py` | Re-optimised each GW |
| `DEFAULT_DECAY` | 0.85 per GW | `season_forecaster.py` | Future xPts discounted |
| `FDR_MULTIPLIER` | 1→1.30×, 3→1.00×, 5→0.70× | `season_forecaster.py` | ±30% range |
| `DEFAULT_BENCH_WEIGHT` | 0.15 | `optimizer.py` | Bench contribution to objective |
| `MAX_BANKED_FT` | 2 | `fpl_rules.py` | FT cap |
| `MIN_PPG` | 1.0 | `season_forecaster.py` | Floor prevents zero projections |
| Hit cost | 4 pts | `fpl_rules.py` | Per transfer beyond free |

---

## 8. GitHub Actions CI

| Workflow | Trigger | What it does |
|---|---|---|
| `post_gw.yml` | Every 4h (cron) + manual | Detects finished GW → analyze → random delay → submit transfer → commit reports/ |
| `apply_gw1.yml` | Manual only | Posts GW1 squad from `research/gw1_squad_2026.json` |
| `token_health.yml` | Monthly | Checks refresh token validity, opens GitHub issue if expired |

**Required GitHub Secrets**: `FPL_REFRESH_TOKEN`, `FPL_EMAIL`, `FPL_PASSWORD`, `FPL_ENTRY_ID`, `GH_SECRETS_PAT`.

The CI commits `reports/` back to master after each run with message `auto: GW{N} analysis + transfer report [skip ci]`.

**Important**: `scripts/` paths are hardcoded in all workflows. Never move or rename scripts without updating the workflow YAML files.

---

## 9. Data Sources

Full audit: `docs/DATA_SOURCES.md`. Summary:

| Source | Type | Auth | Refresh |
|---|---|---|---|
| FPL `bootstrap-static` | Squad/player master | None | 6h cache |
| FPL `fixtures` | All 380 fixtures + FDR | None | 6h cache |
| FPL `my-team/{entry_id}` | Live squad, prices, chips | Session cookie | Per request |
| FPL `event/{gw}/live` | Live GW scores | None | Per request |
| Vaastav `fantasy-premier-league` | Historical GW data through 2025/26 | None | Git pull |
| FPL-Core-Insights | Secondary player stats | None | Twice-daily |
| FPL `element-summary/{id}` | Per-player fixture history | None | Cached per player |

**Stale paths to avoid**: The old FPL `transfers` endpoint (`/api/transfers/`) is deprecated for live use — use `my-team` instead.

---

## 10. Running Things Locally

```bash
# Web UI (Flask, port 5000)
python app.py

# Retrain ML models
python scripts/train_models.py

# Generate pre-season GW1 squad
python scripts/generate_gw1_squad.py

# Apply GW1 squad (DRY RUN FIRST)
python scripts/apply_team.py --dry-run
python scripts/apply_team.py          # live — only if user explicitly confirms

# Post-GW analysis (no auth)
python scripts/analyze_gw_public.py --gw 1

# Post-GW transfer pipeline (auth required)
python scripts/post_gw.py --gw 1 --dry-run

# Backtesting
python scripts/backtest_25_26.py
python scripts/backtest_comprehensive.py

# Live GW monitor
python scripts/live_check.py --gw 1

# Chip sweep
python scripts/chip_sweep.py
```

---

## 11. 26/27 Season Context (as of 2026-08-13)

- **GW1 deadline**: ~21 August 2026
- **All 38 GW fixtures published** — FDR ratings are available now, enabling pre-season fixture analysis
- **What can be done now**: rolling-FDR analysis, GW1 differentials, early-season haul identification, chip window planning (Wildcard/BB/FH targets based on fixture swings)
- **What cannot be done yet**: BGW/DGW detection (announced mid-season), confirmed player valuations beyond bootstrap prices
- **Current GW1 squad**: `research/gw1_squad_2026.json` + `research/gw1_final_2026.md`
- **Training data cutoff**: Vaastav through 2025/26 end. Models are trained. No retraining needed until mid-season data accumulates.

---

## 12. Experiment Ideas for 26/27

These are safe to test in backtests/notebooks without touching live endpoints:

1. **FDR-weighted captain picks**: run `chip_sweep.py` variants targeting GW clusters with FDR ≤ 2 for premium attackers
2. **Asymmetric bench ordering**: test bench weight sensitivity (currently 0.15) against 2025/26 historical outcomes
3. **Wildcard timing sweep**: `scripts/chip_sweep.py` over the full fixture calendar to find optimal WC GW
4. **Differential analysis**: low-ownership (< 10%) players with FDR ≤ 2 in GW1–4 — highest-leverage picks
5. **Model ablation**: compare 4-model composite vs. `ep_next` baseline over 2025/26 using `backtest_holdout.py`

All experiments write to `reports/`. Commit results with `git add reports/` using descriptive messages.
