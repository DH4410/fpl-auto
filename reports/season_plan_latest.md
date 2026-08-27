# FPL Season Plan — GW2

*Generated 2026-08-27 09:49 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW2

**Captain:** B.Fernandes (4.31 xPts → 8.62 effective with double)  
**Vice:** Gabriel (3.60 xPts)  
**Transfer:** Roll (banking free transfer)  
**Bank after:** £0.0m  
**FT next GW:** 2  

---

## Why the Bot Chose This Squad

### Starting XI

- **Pickford** (GKP, £5.5m): 2.97 xPts for GW2, ranked #4/19 among GKPs in candidate pool. Everton fixture. P(start) 90%.
- **Gabriel** (DEF, £8.0m) [**Vice**]: 3.60 xPts for GW2, ranked #1/46 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Guéhi** (DEF, £6.0m): 2.52 xPts for GW2, ranked #7/46 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **James** (DEF, £5.5m): 2.70 xPts for GW2, ranked #4/46 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **Lacroix** (DEF, £6.0m): 2.52 xPts for GW2, ranked #9/46 among DEFs in candidate pool. Chelsea fixture. P(start) 90%.
- **O'Reilly** (DEF, £6.5m): 2.79 xPts for GW2, ranked #2/46 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 4.31 xPts (8.62 effective with captain double). Ranked #1/66 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Cherki** (MID, £7.5m): 2.76 xPts for GW2, ranked #5/66 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £5.5m): 2.51 xPts for GW2, ranked #10/66 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Szoboszlai** (MID, £7.0m): 2.96 xPts for GW2, ranked #4/66 among MIDs in candidate pool. Liverpool fixture. P(start) 90%.
- **Thiago** (FWD, £8.0m): 2.48 xPts for GW2, ranked #2/19 among FWDs in candidate pool. Brentford fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Martinez** (GKP, £5.0m): 2.34 xPts. Budget saved here funds the premium XI picks.
- **Lewis-Potter** (MID, £5.5m): 1.94 xPts. Budget saved here funds the premium XI picks.
- **Calvert-Lewin** (FWD, £6.0m): 2.03 xPts. Budget saved here funds the premium XI picks.
- **Woltemade** (FWD, £6.0m): 2.03 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**Rolling the free transfer.** No single swap improves the 6-GW projected total enough to justify spending the FT now. Banking gives 2 FT(s) next GW (valued at ~3 expected pts in the planner). Rolling is often optimal mid-season when the squad is healthy.

---

## Players We Considered But Didn't Pick

### GW1 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **De Cuyper** (DEF, Brighton): **17 pts in GW1** (1 goal, 1 assist, clean sheet). Not transferred in — Forward projection: 1.71 xPts for GW2 (ranked #134 overall). This is below our lowest-ranked DEF in the squad (2.52 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Hinshelwood** (MID, Brighton): **16 pts in GW1** (2 goals, clean sheet). Not transferred in — Forward projection 1.98 xPts (ranked #86 overall) is competitive, but bringing them in at £6.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Mendy** (DEF, Hull City): **15 pts in GW1** (1 goal, clean sheet, DEFCON bonus (13 CBIT)). Not transferred in — Forward projection: 0.90 xPts for GW2 (ranked #149 overall). This is below our lowest-ranked DEF in the squad (2.52 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Ajayi** (DEF, Hull City): **14 pts in GW1** (1 goal, clean sheet). Not transferred in — Their 6-GW projected return sits outside the top-150 candidate pool. The EWMA model hasn't built enough forward-looking form from this performance to move them into contention yet.
- **M.Sangaré** (MID, Brentford): **14 pts in GW1** (2 assists, clean sheet). Not transferred in — Forward projection: 1.80 xPts for GW2 (ranked #114 overall). This is below our lowest-ranked MID in the squad (1.94 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Kayode** (DEF, Brentford): **13 pts in GW1** (1 goal, clean sheet). Not transferred in — Forward projection: 1.71 xPts for GW2 (ranked #136 overall). This is below our lowest-ranked DEF in the squad (2.52 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Palmer** (MID, Chelsea): **13 pts in GW1** (1 goal, 1 assist). Not transferred in — Forward projection 3.17 xPts (ranked #6 overall) is competitive, but bringing them in at £9.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Stach** (MID, Leeds): **13 pts in GW1** (1 goal, clean sheet). Not transferred in — Forward projection 2.40 xPts (ranked #30 overall) is competitive, but bringing them in at £6.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Gakpo** (MID, Liverpool): **12 pts in GW1** (1 goal). Not transferred in — Forward projection 2.25 xPts (ranked #53 overall) is competitive, but bringing them in at £7.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **White** (DEF, Arsenal): **11 pts in GW1** (1 assist, clean sheet). Not transferred in — Forward projection: 2.25 xPts for GW2 (ranked #59 overall). This is below our lowest-ranked DEF in the squad (2.52 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Ødegaard** (MID, Arsenal): **11 pts in GW1** (1 goal, clean sheet). Not transferred in — Forward projection 2.07 xPts (ranked #78 overall) is competitive, but bringing them in at £6.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **João Pedro** (FWD, Chelsea): **11 pts in GW1** (1 goal, 1 assist). Not transferred in — Forward projection 2.16 xPts (ranked #65 overall) is competitive, but bringing them in at £7.6m would require dropping a player the MILP values more over the full 6-GW horizon.

### Highest-Projected Players Not In Your Squad

- **Haaland** (FWD, Man City, £15.5m, 3.83 xPts): ranked #2 overall. 3-player Man City cap is maxed.
- **Raya** (GKP, Arsenal, £6.0m, 3.60 xPts): ranked #3 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Saka** (MID, Arsenal, £9.5m, 3.17 xPts): ranked #5 overall. £9.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Palmer** (MID, Chelsea, £9.5m, 3.17 xPts): ranked #6 overall. £9.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Donnarumma** (GKP, Man City, £5.5m, 2.97 xPts): ranked #7 overall. 3-player Man City cap is maxed.
- **A.Becker** (GKP, Liverpool, £5.5m, 2.97 xPts): ranked #8 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Virgil** (DEF, Liverpool, £6.5m, 2.79 xPts): ranked #11 overall. £6.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Rice** (MID, Arsenal, £7.5m, 2.76 xPts): ranked #14 overall. £7.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers                                     | Chip   | Captain     | Vice       |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:----------------------------------------------|:-------|:------------|:-----------|----------:|-------------:|-------:|------:|:-------|
|    2 | Initial squad                                 | —      | B.Fernandes | Gabriel    |     36.44 |         8.35 |      0 |     2 | £0.0m  |
|    3 | De Cuyper ← Lewis-Potter, Hinshelwood ← James | —      | Hinshelwood | Szoboszlai |     43.17 |         9.59 |      0 |     1 | £0.4m  |
|    4 | Saka ← B.Fernandes                            | —      | Hinshelwood | Szoboszlai |     42.13 |        10.12 |      0 |     1 | £2.9m  |
|    5 | Roll                                          | —      | Saka        | Szoboszlai |     40.77 |        10.09 |      0 |     2 | £2.9m  |
|    6 | Roll                                          | —      | Saka        | Gabriel    |     41.25 |         8.98 |      0 |     3 | £2.9m  |
|    7 | Roll                                          | —      | Saka        | Szoboszlai |     40.97 |         9.72 |      0 |     4 | £2.9m  |

---

## Starting XI — GW2

**GKP:** **Pickford**  
**DEF:** **Gabriel** | **James** | **Lacroix** | **O'Reilly** | **Guéhi**  
**MID:** **Groß** | **Szoboszlai** | **Cherki** | **B.Fernandes**(C)  
**FWD:** **Thiago**  

**Bench:** Martinez | Lewis-Potter | Calvert-Lewin | Woltemade