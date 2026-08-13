# FPL Season Plan — GW1

*Generated 2026-08-13 13:45 UTC — advisory only, no transfers executed*

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

- **Pickford** (GKP, £5.5m): 3.53 xPts for GW1, ranked #1/17 among GKPs in candidate pool. Everton fixture. P(start) 90%.
- **Gabriel** (DEF, £8.0m) [**Vice**]: 3.38 xPts for GW1, ranked #1/62 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **James** (DEF, £5.5m): 2.93 xPts for GW1, ranked #3/62 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **Matheus N.** (DEF, £6.0m): 2.83 xPts for GW1, ranked #5/62 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **O'Reilly** (DEF, £6.5m): 2.94 xPts for GW1, ranked #2/62 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Pedro Porro** (DEF, £5.5m): 2.83 xPts for GW1, ranked #4/62 among DEFs in candidate pool. Spurs fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 3.83 xPts (7.66 effective with captain double). Ranked #1/58 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Cherki** (MID, £7.5m): 2.66 xPts for GW1, ranked #5/58 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Gakpo** (MID, £7.0m): 2.44 xPts for GW1, ranked #12/58 among MIDs in candidate pool. Liverpool fixture. P(start) 90%.
- **Szoboszlai** (MID, £7.0m): 2.59 xPts for GW1, ranked #8/58 among MIDs in candidate pool. Liverpool fixture. P(start) 90%.
- **Mateta** (FWD, £6.5m): 2.18 xPts for GW1, ranked #5/13 among FWDs in candidate pool. Crystal Palace fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Groß** (MID, £5.5m): 2.36 xPts. Budget saved here funds the premium XI picks.
- **Welbeck** (FWD, £6.0m): 1.96 xPts. Budget saved here funds the premium XI picks.
- **A.Becker** (GKP, £5.5m): 3.24 xPts. Budget saved here funds the premium XI picks.
- **Woltemade** (FWD, £6.0m): 2.01 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision


---

## Players We Considered But Didn't Pick

### Highest-Projected Players Not In Your Squad

- **Raya** (GKP, Arsenal, £6.0m, 3.50 xPts): ranked #3 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Donnarumma** (GKP, Man City, £5.5m, 3.38 xPts): ranked #5 overall. 3-player Man City cap is maxed.
- **Haaland** (FWD, Man City, £15.5m, 3.32 xPts): ranked #6 overall. 3-player Man City cap is maxed.
- **Palmer** (MID, Chelsea, £9.5m, 2.94 xPts): ranked #9 overall. £9.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Saka** (MID, Arsenal, £9.5m, 2.93 xPts): ranked #11 overall. £9.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Lammens** (GKP, Man Utd, £5.0m, 2.93 xPts): ranked #12 overall. Edged out by selected GKPs with better 6-GW projections.
- **Roefs** (GKP, Sunderland, £5.0m, 2.89 xPts): ranked #13 overall. Edged out by selected GKPs with better 6-GW projections.
- **Sánchez** (GKP, Chelsea, £5.0m, 2.86 xPts): ranked #14 overall. Edged out by selected GKPs with better 6-GW projections.

---

## Chip Schedule

| GW | Chip | Est. Gain |
|---:|---|---:|
| 2 | Triple Captain | +4.3 pts |
| 4 | Bench Boost | +10.1 pts |

*Hold all chips — no chip this GW offers ≥4 pts; save for a double/blank gameweek.*

---

## GW-by-GW Plan

|   GW | Transfers     | Chip           | Captain     | Vice        |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:--------------|:---------------|:------------|:------------|----------:|-------------:|-------:|------:|:-------|
|    1 | Roll          | —              | B.Fernandes | Gabriel     |     35.98 |         9.56 |      0 |     1 | £0.0m  |
|    2 | Roll          | Triple Captain | B.Fernandes | James       |     37.34 |         9.18 |      0 |     2 | £0.0m  |
|    3 | Roll          | —              | B.Fernandes | O'Reilly    |     37.36 |         8.5  |      0 |     3 | £0.0m  |
|    4 | Roll          | Bench Boost    | Gabriel     | James       |     35.78 |        10.08 |      0 |     4 | £0.0m  |
|    5 | Roll          | —              | B.Fernandes | O'Reilly    |     37.84 |         9.44 |      0 |     5 | £0.0m  |
|    6 | Rice ← Cherki | —              | Gabriel     | B.Fernandes |     35.8  |         8.96 |      0 |     5 | £0.0m  |

---

## Starting XI — GW1

**GKP:** **Pickford**  
**DEF:** **Gabriel** | **James** | **O'Reilly** | **Matheus N.** | **Pedro Porro**  
**MID:** **Gakpo** | **Szoboszlai** | **Cherki** | **B.Fernandes**(C)  
**FWD:** **Mateta**  

**Bench:** Groß | Welbeck | A.Becker | Woltemade