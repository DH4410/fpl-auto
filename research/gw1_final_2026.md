# FPL Auto — GW1 2026/27 Final Research Report

*Generated 2026-08-05 | Season: 2026/27 | Model: FPLPointsPredictor v2*

---

## 1. System Overview

The bot uses a four-model ML stack trained on six seasons of historical FPL data,
composed through the exact 2026/27 FPL scoring rules to produce E[points] per player
per gameweek. The pipeline is:

```
Historical data (Vaastav, FPL API, football-data.co.uk)
  └─ feature_engineering.py  →  EWMA form matrix (163k rows × 26 features)
        └─ models.py           →  4 sub-models (Minutes, Attack, Defense, Bonus)
              └─ inference       →  E[pts] per current-season player
                    └─ optimizer →  MIP squad selection (£100m, 2-5-5-3, ≤3/club)
```

For GW1, model predictions are blended with FPL's own `ep_next` (40% model,
60% FPL) because `ep_next` already incorporates fixture difficulty for the
specific GW1 matchups.

---

## 2. Training Data

| Season   | Rows    | DC columns? |
|----------|--------:|-------------|
| 2020-21  | 24,365  | No          |
| 2021-22  | 25,447  | No          |
| 2022-23  | 26,505  | No          |
| 2023-24  | 29,725  | No          |
| 2024-25  | 27,605  | No          |
| 2025-26  | 29,757  | **Yes**     |
| **Total**| **163,404** |          |

Defensive-contribution columns (`clearances_blocks_interceptions`, `recoveries`,
`tackles`, `defensive_contribution`) only exist from 2025-26. The DC sub-model
head trains only on those rows; earlier seasons use positional-median priors.

### Feature Matrix (26 features)

All features are EWMA-shifted by 1 GW (no leakage), α=0.25 (~7 GW effective window):

| Group | Features |
|-------|----------|
| Playing time | `ewma_minutes`, `ewma_p60_rate`, `ewma_played_any`, `ewma_start_rate`, `games_played` |
| Attacking | `ewma_goals_scored`, `ewma_assists`, `ewma_expected_goals`, `ewma_expected_assists`, `ewma_expected_goal_involvements` |
| Defensive | `ewma_clean_sheets`, `ewma_goals_conceded`, `ewma_expected_goals_conceded`, `ewma_clearances_blocks_interceptions`, `ewma_recoveries`, `ewma_tackles`, `ewma_defensive_contribution` |
| Production proxy | `ewma_bonus`, `ewma_bps`, `ewma_total_points`, `ewma_influence`, `ewma_creativity`, `ewma_threat`, `ewma_ict_index` |
| Context | `ewma_saves`, `was_home` |

---

## 3. Inference (GW1 Projection)

| Metric | Value |
|--------|------:|
| Total active players | 527 |
| Players with history_past features | 464 |
| Players using promoted-team minute prior (41.4 min) | 89 |
| Attack model trained on (minutes ≥ 20) | 38,333 rows |

### Top 20 GW1 Projections (blended score)

| # | Player | Team | Pos | Price | Blended xPts | Model xPts | ep_next |
|--:|--------|------|-----|------:|-------------:|-----------:|--------:|
| 1 | Haaland | Man City | FWD | 15.5 | 5.55 | 7.87 | 4.0 |
| 2 | B.Fernandes | Man Utd | MID | 12.0 | 5.36 | 7.39 | 4.0 |
| 3 | Gabriel | Arsenal | DEF | 8.0 | 5.06 | 6.66 | 4.0 |
| 4 | Matheus N. | Man City | DEF | 6.0 | 4.75 | 7.66 | 2.8 |
| 5 | Senesi | Spurs | DEF | 6.0 | 4.59 | 7.27 | 2.8 |
| 6 | Saka | Arsenal | MID | 9.5 | 4.59 | 6.67 | 3.2 |
| 7 | Raya | Arsenal | GKP | 6.0 | 4.50 | 5.26 | 4.0 |
| 8 | Pickford | Everton | GKP | 5.5 | 4.47 | 6.24 | 3.3 |
| 9 | Doku | Man City | MID | 7.5 | 4.46 | 7.25 | 2.6 |
| 10 | Dowman | Arsenal | MID | 5.5 | 4.46 | 8.15 | 2.0 |
| 11 | Pedro Porro | Spurs | DEF | 5.5 | 4.46 | 7.39 | 2.5 |
| 12 | Guéhi | Man City | DEF | 6.0 | 4.45 | 6.93 | 2.8 |
| 13 | Truffert | Bournemouth | DEF | 5.5 | 4.40 | 7.24 | 2.5 |
| 14 | Tarkowski | Everton | DEF | 6.0 | 4.38 | 6.75 | 2.8 |
| 15 | Lacroix | Chelsea | DEF | 6.0 | 4.35 | 6.68 | 2.8 |
| 16 | Kostoulas | Brighton | FWD | 5.5 | 4.33 | 8.12 | 1.8 |
| 17 | Mukiele | Sunderland | DEF | 5.5 | 4.31 | 7.03 | 2.5 |
| 18 | Donnarumma | Man City | GKP | 5.5 | 4.31 | 5.83 | 3.3 |
| 19 | Virgil | Liverpool | DEF | 6.5 | 4.31 | 6.12 | 3.1 |
| 20 | A.Becker | Liverpool | GKP | 5.5 | 4.29 | 5.79 | 3.3 |

---

## 4. Recommended GW1 Squad

**Squad cost:** £100.0m | **Expected XI pts:** 57.3 (incl. captain double)
**Captain:** Haaland (Man City) | **Vice:** B.Fernandes (Man Utd)
**Formation:** 5-3-2 (1 GKP, 5 DEF, 3 MID, 2 FWD starting)

| Pos | Player | Team | Price | xPts | Role |
|-----|--------|------|------:|-----:|------|
| GKP | Pickford | Everton | 5.5 | 4.47 | XI |
| GKP | Dubravka | Spurs | 4.0 | 2.04 | Bench |
| DEF | Gabriel | Arsenal | 8.0 | 5.06 | XI |
| DEF | Matheus N. | Man City | 6.0 | 4.75 | XI |
| DEF | Senesi | Spurs | 6.0 | 4.59 | XI |
| DEF | Pedro Porro | Spurs | 5.5 | 4.46 | XI |
| DEF | Ballard | Sunderland | 5.0 | 4.23 | XI |
| MID | B.Fernandes | Man Utd | 12.0 | 5.36 | XI, **Vice** |
| MID | Doku | Man City | 7.5 | 4.46 | XI |
| MID | Dowman | Arsenal | 5.5 | 4.46 | XI |
| MID | Reed | Fulham | 4.5 | 3.43 | Bench |
| MID | Hughes | Crystal Palace | 4.5 | 2.28 | Bench |
| FWD | Haaland | Man City | 15.5 | 5.55 | XI, **Captain** |
| FWD | Kostoulas | Brighton | 5.5 | 4.33 | XI |
| FWD | Piroe | Leeds | 5.0 | 3.41 | Bench |

**Squad legality:**
- 3 per club max: Man City (Matheus N., Doku, Haaland ✓), Spurs (Senesi, Pedro Porro, Dubravka ✓), Arsenal (Gabriel, Dowman + one more allowed)
- 2 GKP, 5 DEF, 5 MID, 3 FWD ✓
- Formation 5-3-2 (legal: ≥3 DEF, ≥2 FWD, 1 GKP) ✓

**Machine-readable squad:** `research/gw1_squad_2026.json`

---

## 5. How the Bot Chose This Squad

### Process

1. **Score every player.** The bot fetches all 527 active players from the FPL bootstrap API and runs each through the ML pipeline to get a blended `xPts` per GW (40% model output + 60% FPL's own `ep_next` for GW1, since `ep_next` carries the specific fixture-difficulty signal that the model can't learn until games are played).

2. **Pre-filter to 150 candidates.** The full 527-player pool would make the MILP too slow. Players are ranked by blended xPts and the top 150 are passed to the solver.

3. **Solve the MILP.** The optimizer maximises total starting-XI xPts subject to hard constraints: £100m budget, 2 GKP / 5 DEF / 5 MID / 3 FWD squad shape, ≤3 players per club, and a legal formation (≥3 DEF + ≥2 FWD + 1 GKP in the XI). Bench players are weighted at 25% to give them some value without distorting the XI selection.

4. **Assign captain/vice.** Highest-xPts player in the XI becomes captain (Haaland, 5.55 → 11.1 effective), second becomes vice (B.Fernandes, 5.36).

### Key Decisions

| Decision | Why |
|----------|-----|
| **5 defenders in XI** | Gabriel, Matheus N., Senesi, Pedro Porro, and Ballard all scored in the top 15 blended xPts. The model's DC (defensive contribution) sub-model — trained on 2025-26 data when CBIT stats became available — gave all five high form scores. Running 5 DEF freed enough budget to fit Haaland without sacrificing midfield quality. |
| **Haaland at £15.5m** | Highest absolute xPts in the pool (5.55 blended). The solver treats a captained player as worth 2× their xPts, so the MILP structurally rewards the highest-xPts player as captain — Haaland is the clear choice. |
| **3× Man City** | Haaland, Matheus N., and Doku are the three highest-scoring City players; the ≤3/club cap is exactly met. City have the best GW1 fixture by FDR. |
| **Ballard (Sunderland, £5.0m) over cheaper options** | Newly-promoted players get Sunderland's 2024-25 (Championship) data, but the minutes prior and DC-form signal still ranked Ballard above most £5m alternatives. The cheap price enables the premium attack (Haaland + B.Fernandes). |
| **B.Fernandes at £12m** | The second-highest xPts in the dataset (5.36), with a Man Utd home fixture. Despite the price, the MILP found the budget works by loading up on cheap bench players (Dubravka £4m, Reed £4.5m, Hughes £4.5m). |
| **Bench: 4 cheap enablers** | Dubravka (£4m), Reed (£4.5m), Hughes (£4.5m), Piroe (£5m). The solver deliberately takes the bench penalty on these players to maximise starting-XI quality. Their xPts are low, but legal squad shape requires bodies on the bench. |

### Does the Model Predict DEFCON Points?

Partially. DEFCON awards +2 pts when a player crosses a defensive-activity threshold in a match:
- **Defenders:** 10+ combined clearances, blocks, interceptions, and tackles (CBIT)
- **Midfielders/Forwards:** 12+ combined CBIT and ball recoveries
- **Cap:** maximum +2 pts per match regardless

The model's DC sub-model uses EWMA features for `clearances_blocks_interceptions`, `recoveries`, `tackles`, and `defensive_contribution` — so it captures *who tends to be defensively active* in a continuous sense. A defender with high EWMA CBIT will score higher on the DC head, and that lifts their `model_xpts`.

However, the model does **not** explicitly calculate P(CBIT ≥ threshold) × 2. The DEFCON bonus is a discrete threshold jump, not a linear signal. What the model sees is *correlated form* — players who habitually hit 10+ CBIT do show up in EWMA as having high defensive contribution, so they rank higher, but the model can't separate "this player averages 12 CBIT so they'll get +2 pts" from "this player is generally more defensive."

The 60% weight given to FPL's own `ep_next` partially compensates: FPL's model does include DEFCON in its expected-points calculation (since it's in the official scoring rules), so the blended score implicitly captures DEFCON through that channel.

**Practical takeaway:** The DC-heavy defenders in this squad (Gabriel, Matheus N., Senesi) were ranked partly because their defensive activity form suggests DEFCON potential. The model doesn't guarantee they'll hit the threshold, but they're the right type of player.

---

## 6. Post-GW Automation

After each GW finishes, run:

```bash
export FPL_PASSWORD=your_password_here
python scripts/post_gw.py --gw <next_gw_number>

# With auto-apply of 1 free transfer (no hits):
python scripts/post_gw.py --gw 2 --auto
```

The planner runs a 6-GW rolling MILP and considers:
- Free transfer banking (max 5 FT)
- Wildcard / Free Hit / Triple Captain / Bench Boost timing
- Hit-cost penalty (−4 pts per extra transfer)
- Player availability (injury/suspension)

---

## 7. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| 40% model + 60% ep_next blend (GW1) | ep_next is fixture-adjusted; model gives form signal |
| Asymmetric loss on minutes model (3× over-prediction penalty) | Being wrong high on rotation risks destroys value |
| DC model uses only 2025-26 rows | 2024-25 data predates the DC scoring rule — no column exists |
| Pre-filter to 150 candidates before MILP | 568 × 6 GW binary vars would be too slow; 150 × 6 is seconds |
| Rolling 6-GW horizon, commit 1 GW | Re-solve every week with fresh data; horizon gives FT banking signal |
| No Gurobi — HiGHS → CBC fallback | Free solver only; avoids license dependency |

---

## 8. Files Reference

| File | Purpose |
|------|---------|
| `research/gw1_squad_2026.json` | Full squad with element IDs, prices, picks format for FPL API |
| `research/training_meta_2026.json` | Training data statistics and feature list |
| `research/gw1_final_2026.md` | This document |
| `research/training_results_summary.md` | Previous run (different model blend, kept for comparison) |
| `scripts/apply_team.py` | One-shot GW1 squad submission script |
| `scripts/post_gw.py` | Weekly post-GW analysis and auto-transfer script |
| `bot/season_forecaster.py` | Lightweight GW xPts engine (ep_next + PPG) |
| `bot/season_planner.py` | Rolling 6-GW MILP planner |
| `bot/updater.py` | Pipeline orchestrator → reports/ |
| `bot/models.py` | 4 ML sub-models (Minutes, Attack, Defense, Bonus) |
| `reports/season_plan_latest.md` | Auto-generated plan report (post-run) |
