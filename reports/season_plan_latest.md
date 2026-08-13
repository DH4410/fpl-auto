# FPL Season Plan — GW1

*Generated 2026-08-13 14:39 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs, points-per-game × FDR is used as a stable proxy. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW1

**Chip:** Bench Boost  
**Captain:** Haaland (3.55 xPts → 7.10 effective with double)  
**Vice:** B.Fernandes (3.47 xPts)  
**Bank after:** £0.5m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Pickford** (GKP, £5.5m): 4.19 xPts for GW1, ranked #1/17 among GKPs in candidate pool. Everton fixture. P(start) 90%.
- **Aït-Nouri** (DEF, £5.5m): 3.10 xPts for GW1, ranked #3/62 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Cash** (DEF, £4.5m): 2.97 xPts for GW1, ranked #7/62 among DEFs in candidate pool. Aston Villa fixture. P(start) 90%.
- **James** (DEF, £5.5m): 3.09 xPts for GW1, ranked #4/62 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **Kayode** (DEF, £4.5m): 2.93 xPts for GW1, ranked #10/62 among DEFs in candidate pool. Brentford fixture. P(start) 90%.
- **Richards** (DEF, £5.0m): 2.97 xPts for GW1, ranked #8/62 among DEFs in candidate pool. Crystal Palace fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**Vice**]: 3.47 xPts for GW1, ranked #1/58 among MIDs in candidate pool. Man Utd fixture. P(start) 90%.
- **Cherki** (MID, £7.5m): 2.81 xPts for GW1, ranked #2/58 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Lewis-Potter** (MID, £5.5m): 2.41 xPts for GW1, ranked #15/58 among MIDs in candidate pool. Brentford fixture. P(start) 90%.
- **Schade** (MID, £6.0m): 2.51 xPts for GW1, ranked #10/58 among MIDs in candidate pool. Brentford fixture. P(start) 90%.
- **Haaland** (FWD, £15.5m) [**CAPTAIN**]: Highest projected return in the squad: 3.55 xPts (7.10 effective with captain double). Ranked #1/13 among FWDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Martinez** (GKP, £5.0m): 3.30 xPts. Budget saved here funds the premium XI picks.
- **Groß** (MID, £5.5m): 2.38 xPts. Budget saved here funds the premium XI picks.
- **Woltemade** (FWD, £6.0m): 2.07 xPts. Budget saved here funds the premium XI picks.
- **Igor Jesus** (FWD, £6.0m): 2.05 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision


---

## Players We Considered But Didn't Pick

### Highest-Projected Players Not In Your Squad

- **Donnarumma** (GKP, Man City, £5.5m, 3.62 xPts): ranked #2 overall. 3-player Man City cap is maxed.
- **Raya** (GKP, Arsenal, £6.0m, 3.42 xPts): ranked #5 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Lammens** (GKP, Man Utd, £5.0m, 3.37 xPts): ranked #6 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Sels** (GKP, Nott'm Forest, £5.0m, 3.34 xPts): ranked #7 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Roefs** (GKP, Sunderland, £5.0m, 3.31 xPts): ranked #8 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Sánchez** (GKP, Chelsea, £5.0m, 3.29 xPts): ranked #10 overall. Edged out by selected GKPs with better 6-GW projections.
- **Kelleher** (GKP, Brentford, £5.0m, 3.26 xPts): ranked #11 overall. 3-player Brentford cap is maxed.
- **Gabriel** (DEF, Arsenal, £8.0m, 3.22 xPts): ranked #12 overall. £8.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## Chip Schedule

| GW | Chip | Est. Gain |
|---:|---|---:|
| 1 | Bench Boost | +9.8 pts |
| 3 | Triple Captain | +4.1 pts |

*Play Bench Boost this GW (est. +9.8 pts).*

---

## GW-by-GW Plan

|   GW | Transfers           | Chip           | Captain     | Vice        |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:--------------------|:---------------|:------------|:------------|----------:|-------------:|-------:|------:|:-------|
|    1 | Roll                | Bench Boost    | Haaland     | B.Fernandes |     37.55 |         9.79 |      0 |     1 | £0.5m  |
|    2 | Roll                | —              | B.Fernandes | Haaland     |     38.01 |         8.66 |      0 |     2 | £0.5m  |
|    3 | Roll                | Triple Captain | Haaland     | Aït-Nouri   |     40.28 |         9.88 |      0 |     3 | £0.5m  |
|    4 | Roll                | —              | James       | Richards    |     36.84 |         9.51 |      0 |     4 | £0.5m  |
|    5 | N.Williams ← Kayode | —              | Haaland     | Aït-Nouri   |     40.35 |         9.53 |      0 |     4 | £0.0m  |
|    6 | Roll                | —              | B.Fernandes | James       |     36.56 |         9.54 |      0 |     5 | £0.0m  |

---

## Starting XI — GW1

**GKP:** **Pickford**  
**DEF:** **Cash** | **Kayode** | **James** | **Richards** | **Aït-Nouri**  
**MID:** **Lewis-Potter** | **Schade** | **Cherki** | **B.Fernandes**  
**FWD:** **Haaland**(C)  

**Bench:** Martinez | Groß | Woltemade | Igor Jesus