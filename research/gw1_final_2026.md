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

## 5. How to Apply the Squad

```bash
# Set your FPL password
export FPL_PASSWORD=your_password_here

# Preview (no API calls)
python scripts/apply_team.py --dry-run

# Apply to your FPL account (will ask for confirmation)
python scripts/apply_team.py

# Apply without confirmation prompt
python scripts/apply_team.py --auto
```

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
