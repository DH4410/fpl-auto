# FPL Season Plan — GW1

*Generated 2026-08-14 07:44 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs, points-per-game × FDR is used as a stable proxy. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW1

**Captain:** B.Fernandes (3.83 xPts → 7.66 effective with double)  
**Vice:** Gabriel (3.38 xPts)  
**Bank after:** £0.0m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Pickford** (GKP, £5.5m): 3.67 xPts for GW1, ranked #1/17 among GKPs in candidate pool. Everton fixture. P(start) 90%.
- **Gabriel** (DEF, £8.0m) [**Vice**]: 3.38 xPts for GW1, ranked #1/62 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **James** (DEF, £5.5m): 2.92 xPts for GW1, ranked #3/62 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **Lacroix** (DEF, £6.0m): 2.67 xPts for GW1, ranked #8/62 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **Matheus N.** (DEF, £6.0m): 2.84 xPts for GW1, ranked #4/62 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **O'Reilly** (DEF, £6.5m): 3.00 xPts for GW1, ranked #2/62 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 3.83 xPts (7.66 effective with captain double). Ranked #1/58 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Cherki** (MID, £7.5m): 2.79 xPts for GW1, ranked #4/58 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £5.5m): 2.37 xPts for GW1, ranked #14/58 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Szoboszlai** (MID, £7.0m): 2.50 xPts for GW1, ranked #10/58 among MIDs in candidate pool. Liverpool fixture. P(start) 90%.
- **Thiago** (FWD, £8.0m): 2.52 xPts for GW1, ranked #2/13 among FWDs in candidate pool. Brentford fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Martinez** (GKP, £5.0m): 2.89 xPts. Budget saved here funds the premium XI picks.
- **Lewis-Potter** (MID, £5.5m): 2.21 xPts. Budget saved here funds the premium XI picks.
- **Calvert-Lewin** (FWD, £6.0m): 2.02 xPts. Budget saved here funds the premium XI picks.
- **Woltemade** (FWD, £6.0m): 2.05 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision


---

## Players We Considered But Didn't Pick

### Highest-Projected Players Not In Your Squad

- **Haaland** (FWD, Man City, £15.5m, 3.67 xPts): ranked #2 overall. 3-player Man City cap is maxed.
- **Raya** (GKP, Arsenal, £6.0m, 3.50 xPts): ranked #4 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Donnarumma** (GKP, Man City, £5.5m, 3.34 xPts): ranked #6 overall. 3-player Man City cap is maxed.
- **A.Becker** (GKP, Liverpool, £5.5m, 2.95 xPts): ranked #8 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Saka** (MID, Arsenal, £9.5m, 2.93 xPts): ranked #9 overall. £9.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Lammens** (GKP, Man Utd, £5.0m, 2.93 xPts): ranked #10 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Sels** (GKP, Nott'm Forest, £5.0m, 2.91 xPts): ranked #12 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Roefs** (GKP, Sunderland, £5.0m, 2.89 xPts): ranked #13 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers           | Chip   | Captain     | Vice        |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:--------------------|:-------|:------------|:------------|----------:|-------------:|-------:|------:|:-------|
|    1 | Roll                | —      | B.Fernandes | Gabriel     |     36.33 |         9.17 |      0 |     1 | £0.0m  |
|    2 | Roll                | —      | B.Fernandes | James       |     37.48 |         8.59 |      0 |     2 | £0.0m  |
|    3 | Roll                | —      | B.Fernandes | O'Reilly    |     36.79 |         9.06 |      0 |     3 | £0.0m  |
|    4 | Roll                | —      | Gabriel     | B.Fernandes |     35.56 |         9.45 |      0 |     4 | £0.0m  |
|    5 | Tarkowski ← Lacroix | —      | B.Fernandes | O'Reilly    |     38.01 |         8.86 |      0 |     4 | £0.0m  |
|    6 | Roll                | —      | Gabriel     | B.Fernandes |     35.94 |         8.39 |      0 |     5 | £0.0m  |

---

## Starting XI — GW1

**GKP:** **Pickford**  
**DEF:** **Gabriel** | **James** | **Lacroix** | **O'Reilly** | **Matheus N.**  
**MID:** **Groß** | **Szoboszlai** | **Cherki** | **B.Fernandes**(C)  
**FWD:** **Thiago**  

**Bench:** Martinez | Lewis-Potter | Calvert-Lewin | Woltemade