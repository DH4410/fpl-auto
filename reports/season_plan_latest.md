# FPL Season Plan — GW1

*Generated 2026-08-21 12:18 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs, points-per-game × FDR is used as a stable proxy. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW1

**Captain:** B.Fernandes (4.31 xPts → 8.62 effective with double)  
**Vice:** Gabriel (3.60 xPts)  
**Transfer:** OUT Matheus N. (£6.0m) → IN Guéhi (£6.0m)  
**Bank after:** £0.0m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Pickford** (GKP, £5.5m): 2.97 xPts for GW1, ranked #4/17 among GKPs in candidate pool. Everton fixture. P(start) 90%.
- **Gabriel** (DEF, £8.0m) [**Vice**]: 3.60 xPts for GW1, ranked #1/61 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Guéhi** (DEF, £6.0m): 2.52 xPts for GW1, ranked #7/61 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **James** (DEF, £5.5m): 2.70 xPts for GW1, ranked #5/61 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **Lacroix** (DEF, £6.0m): 2.52 xPts for GW1, ranked #9/61 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **O'Reilly** (DEF, £6.5m): 2.79 xPts for GW1, ranked #2/61 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 4.31 xPts (8.62 effective with captain double). Ranked #1/59 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Cherki** (MID, £7.5m): 2.76 xPts for GW1, ranked #4/59 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £5.5m): 2.51 xPts for GW1, ranked #11/59 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Szoboszlai** (MID, £7.0m): 2.67 xPts for GW1, ranked #6/59 among MIDs in candidate pool. Liverpool fixture. P(start) 90%.
- **Thiago** (FWD, £8.0m): 2.48 xPts for GW1, ranked #2/13 among FWDs in candidate pool. Brentford fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Martinez** (GKP, £5.0m): 2.34 xPts. Budget saved here funds the premium XI picks.
- **Lewis-Potter** (MID, £5.5m): 1.94 xPts. Budget saved here funds the premium XI picks.
- **Calvert-Lewin** (FWD, £6.0m): 2.03 xPts. Budget saved here funds the premium XI picks.
- **Woltemade** (FWD, £6.0m): 2.03 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Matheus N. (£6.0m, 0.88 xPts GW1)
**IN:** Guéhi (£6.0m, 2.52 xPts GW1)
**Net this week:** +1.64 xPts

Guéhi projects 2.52 xPts vs Matheus N.'s 0.88 — a 1.64 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

---

## Players We Considered But Didn't Pick

### Highest-Projected Players Not In Your Squad

- **Haaland** (FWD, Man City, £15.5m, 3.83 xPts): ranked #2 overall. 3-player Man City cap is maxed.
- **Raya** (GKP, Arsenal, £6.0m, 3.60 xPts): ranked #4 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Saka** (MID, Arsenal, £9.5m, 3.17 xPts): ranked #5 overall. £9.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Palmer** (MID, Chelsea, £9.5m, 3.17 xPts): ranked #6 overall. £9.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Donnarumma** (GKP, Man City, £5.5m, 2.97 xPts): ranked #7 overall. 3-player Man City cap is maxed.
- **A.Becker** (GKP, Liverpool, £5.5m, 2.97 xPts): ranked #9 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Virgil** (DEF, Liverpool, £6.5m, 2.79 xPts): ranked #10 overall. £6.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Rice** (MID, Arsenal, £7.5m, 2.76 xPts): ranked #12 overall. £7.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## Chip Schedule

| GW | Chip | Est. Gain |
|---:|---|---:|
| 2 | Triple Captain | +7.7 pts |
| 5 | Bench Boost | +14.8 pts |

*Hold all chips — no chip this GW offers ≥4 pts; save for a double/blank gameweek.*

---

## GW-by-GW Plan

|   GW | Transfers            | Chip           | Captain     | Vice        |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:---------------------|:---------------|:------------|:------------|----------:|-------------:|-------:|------:|:-------|
|    1 | Guéhi ← Matheus N.   | —              | B.Fernandes | Gabriel     |     36.15 |         8.35 |      0 |     1 | £0.0m  |
|    2 | Benitez ← Pickford   | Triple Captain | B.Fernandes | Gabriel     |     60.55 |        12.98 |      0 |     1 | £1.0m  |
|    3 | Stach ← Lewis-Potter | —              | B.Fernandes | Guéhi       |     63.27 |        12.88 |      0 |     1 | £0.5m  |
|    4 | Rice ← Cherki        | —              | Gabriel     | B.Fernandes |     63.19 |        14.12 |      0 |     1 | £0.5m  |
|    5 | Roll                 | Bench Boost    | B.Fernandes | Gabriel     |     61.76 |        14.82 |      0 |     2 | £0.5m  |
|    6 | Roll                 | —              | B.Fernandes | Gabriel     |     59.87 |        13.21 |      0 |     3 | £0.5m  |

---

## Starting XI — GW1

**GKP:** **Pickford**  
**DEF:** **Gabriel** | **James** | **Lacroix** | **O'Reilly** | **Guéhi**  
**MID:** **Groß** | **Szoboszlai** | **Cherki** | **B.Fernandes**(C)  
**FWD:** **Thiago**  

**Bench:** Martinez | Lewis-Potter | Calvert-Lewin | Woltemade