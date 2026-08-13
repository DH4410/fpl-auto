# FPL Season Plan — GW1

*Generated 2026-08-13 14:56 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs, points-per-game × FDR is used as a stable proxy. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (40% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW1

**Captain:** B.Fernandes (3.59 xPts → 7.18 effective with double)  
**Vice:** Haaland (3.59 xPts)  
**Bank after:** £0.5m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Pickford** (GKP, £5.5m): 4.02 xPts for GW1, ranked #1/17 among GKPs in candidate pool. Everton fixture. P(start) 90%.
- **Aït-Nouri** (DEF, £5.5m): 2.97 xPts for GW1, ranked #5/62 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Cash** (DEF, £4.5m): 2.81 xPts for GW1, ranked #11/62 among DEFs in candidate pool. Aston Villa fixture. P(start) 90%.
- **James** (DEF, £5.5m): 3.03 xPts for GW1, ranked #3/62 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **Kayode** (DEF, £4.5m): 2.73 xPts for GW1, ranked #14/62 among DEFs in candidate pool. Brentford fixture. P(start) 90%.
- **Richards** (DEF, £5.0m): 2.83 xPts for GW1, ranked #10/62 among DEFs in candidate pool. Crystal Palace fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 3.59 xPts (7.18 effective with captain double). Ranked #1/58 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Cherki** (MID, £7.5m): 2.80 xPts for GW1, ranked #3/58 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £5.5m): 2.37 xPts for GW1, ranked #18/58 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Schade** (MID, £6.0m): 2.42 xPts for GW1, ranked #13/58 among MIDs in candidate pool. Brentford fixture. P(start) 90%.
- **Haaland** (FWD, £15.5m) [**Vice**]: 3.59 xPts for GW1, ranked #1/13 among FWDs in candidate pool. Man City fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Martinez** (GKP, £5.0m): 3.16 xPts. Budget saved here funds the premium XI picks.
- **Lewis-Potter** (MID, £5.5m): 2.34 xPts. Budget saved here funds the premium XI picks.
- **Woltemade** (FWD, £6.0m): 2.06 xPts. Budget saved here funds the premium XI picks.
- **Igor Jesus** (FWD, £6.0m): 2.01 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision


---

## Players We Considered But Didn't Pick

### Highest-Projected Players Not In Your Squad

- **Donnarumma** (GKP, Man City, £5.5m, 3.53 xPts): ranked #4 overall. 3-player Man City cap is maxed.
- **Raya** (GKP, Arsenal, £6.0m, 3.44 xPts): ranked #5 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Gabriel** (DEF, Arsenal, £8.0m, 3.27 xPts): ranked #6 overall. £8.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Lammens** (GKP, Man Utd, £5.0m, 3.22 xPts): ranked #7 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Sels** (GKP, Nott'm Forest, £5.0m, 3.19 xPts): ranked #8 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Roefs** (GKP, Sunderland, £5.0m, 3.17 xPts): ranked #9 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Sánchez** (GKP, Chelsea, £5.0m, 3.15 xPts): ranked #11 overall. Edged out by selected GKPs with better 6-GW projections.
- **Kelleher** (GKP, Brentford, £5.0m, 3.13 xPts): ranked #12 overall. 3-player Brentford cap is maxed.

---

## Chip Schedule

| GW | Chip | Est. Gain |
|---:|---|---:|
| 2 | Triple Captain | +4.1 pts |
| 3 | Bench Boost | +9.7 pts |

*Hold all chips — no chip this GW offers ≥4 pts; save for a double/blank gameweek.*

---

## GW-by-GW Plan

|   GW | Transfers           | Chip           | Captain     | Vice        |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:--------------------|:---------------|:------------|:------------|----------:|-------------:|-------:|------:|:-------|
|    1 | Roll                | —              | B.Fernandes | Haaland     |     36.76 |         9.58 |      0 |     1 | £0.5m  |
|    2 | Roll                | Triple Captain | B.Fernandes | Haaland     |     37.32 |         8.51 |      0 |     2 | £0.5m  |
|    3 | Roll                | Bench Boost    | Haaland     | B.Fernandes |     39.42 |         9.67 |      0 |     3 | £0.5m  |
|    4 | Roll                | —              | James       | Richards    |     35.94 |         9.28 |      0 |     4 | £0.5m  |
|    5 | N.Williams ← Kayode | —              | Haaland     | B.Fernandes |     39.67 |         9.27 |      0 |     4 | £0.0m  |
|    6 | Roll                | —              | B.Fernandes | Haaland     |     36.06 |         9.23 |      0 |     5 | £0.0m  |

---

## Starting XI — GW1

**GKP:** **Pickford**  
**DEF:** **Cash** | **Kayode** | **James** | **Richards** | **Aït-Nouri**  
**MID:** **Schade** | **Groß** | **Cherki** | **B.Fernandes**(C)  
**FWD:** **Haaland**  

**Bench:** Martinez | Lewis-Potter | Woltemade | Igor Jesus