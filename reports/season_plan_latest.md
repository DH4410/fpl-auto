# FPL Season Plan — GW1

*Generated 2026-08-13 11:48 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs, points-per-game × FDR is used as a stable proxy. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW1

**Chip:** Bench Boost  
**Captain:** B.Fernandes (3.58 xPts → 7.16 effective with double)  
**Vice:** Gabriel (3.25 xPts)  
**Bank after:** £0.0m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Donnarumma** (GKP, £5.5m): 3.21 xPts for GW1, ranked #2/17 among GKPs in candidate pool. Man City fixture. P(start) 90%.
- **Gabriel** (DEF, £8.0m) [**Vice**]: 3.25 xPts for GW1, ranked #1/62 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Guéhi** (DEF, £6.0m): 2.76 xPts for GW1, ranked #3/62 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Matheus N.** (DEF, £6.0m): 2.71 xPts for GW1, ranked #5/62 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Pedro Porro** (DEF, £5.5m): 2.76 xPts for GW1, ranked #2/62 among DEFs in candidate pool. Spurs fixture. P(start) 90%.
- **Senesi** (DEF, £6.0m): 2.70 xPts for GW1, ranked #6/62 among DEFs in candidate pool. Spurs fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 3.58 xPts (7.16 effective with captain double). Ranked #1/58 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Groß** (MID, £5.5m): 2.23 xPts for GW1, ranked #16/58 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Palmer** (MID, £9.5m): 3.00 xPts for GW1, ranked #3/58 among MIDs in candidate pool. Chelsea fixture. P(start) 90%.
- **Szoboszlai** (MID, £7.0m): 2.47 xPts for GW1, ranked #9/58 among MIDs in candidate pool. Liverpool fixture. P(start) 90%.
- **Calvert-Lewin** (FWD, £6.0m): 2.07 xPts for GW1, ranked #7/13 among FWDs in candidate pool. Leeds fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Welbeck** (FWD, £6.0m): 1.93 xPts. Budget saved here funds the premium XI picks.
- **Pickford** (GKP, £5.5m): 3.08 xPts. Budget saved here funds the premium XI picks.
- **Woltemade** (FWD, £6.0m): 2.00 xPts. Budget saved here funds the premium XI picks.
- **Diarra** (MID, £5.5m): 2.04 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision


---

## Players We Considered But Didn't Pick

### Highest-Projected Players Not In Your Squad

- **Haaland** (FWD, Man City, £15.5m, 3.43 xPts): ranked #2 overall. 3-player Man City cap is maxed.
- **Raya** (GKP, Arsenal, £6.0m, 3.33 xPts): ranked #3 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Saka** (MID, Arsenal, £9.5m, 3.00 xPts): ranked #7 overall. £9.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **A.Becker** (GKP, Liverpool, £5.5m, 2.91 xPts): ranked #9 overall. Edged out by selected GKPs with better 6-GW projections.
- **O'Reilly** (DEF, Man City, £6.5m, 2.71 xPts): ranked #12 overall. 3-player Man City cap is maxed.
- **Gibbs-White** (MID, Nott'm Forest, £8.0m, 2.65 xPts): ranked #15 overall. £8.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **James** (DEF, Chelsea, £5.5m, 2.60 xPts): ranked #16 overall. Edged out by selected DEFs with better 6-GW projections.
- **Pope** (GKP, Newcastle, £5.0m, 2.59 xPts): ranked #17 overall. Edged out by selected GKPs with better 6-GW projections.

---

## Chip Schedule

| GW | Chip | Est. Gain |
|---:|---|---:|
| 1 | Bench Boost | +9.1 pts |
| 2 | Triple Captain | +4.0 pts |

*Play Bench Boost this GW (est. +9.1 pts).*

---

## GW-by-GW Plan

|   GW | Transfers          | Chip           | Captain     | Vice        |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:-------------------|:---------------|:------------|:------------|----------:|-------------:|-------:|------:|:-------|
|    1 | Roll               | Bench Boost    | B.Fernandes | Gabriel     |     34.31 |         9.05 |      0 |     1 | £0.0m  |
|    2 | Roll               | Triple Captain | B.Fernandes | Palmer      |     36.2  |         9.1  |      0 |     2 | £0.0m  |
|    3 | Roll               | —              | B.Fernandes | Guéhi       |     34.89 |         8.01 |      0 |     3 | £0.0m  |
|    4 | James ← Matheus N. | —              | Palmer      | Gabriel     |     34.69 |         8.7  |      0 |     3 | £0.5m  |
|    5 | Roll               | —              | B.Fernandes | Gabriel     |     35.14 |         8.89 |      0 |     4 | £0.5m  |
|    6 | Roll               | —              | Gabriel     | B.Fernandes |     34.03 |         8.28 |      0 |     5 | £0.5m  |

---

## Starting XI — GW1

**GKP:** **Donnarumma**  
**DEF:** **Gabriel** | **Guéhi** | **Matheus N.** | **Senesi** | **Pedro Porro**  
**MID:** **Groß** | **Palmer** | **Szoboszlai** | **B.Fernandes**(C)  
**FWD:** **Calvert-Lewin**  

**Bench:** Welbeck | Pickford | Woltemade | Diarra