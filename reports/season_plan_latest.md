# FPL Season Plan — GW11

*Generated 2026-11-20 18:18 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW11

**Captain:** N.Jackson (7.04 xPts → 14.08 effective with double)  
**Vice:** Gabriel (7.18 xPts)  
**Transfer:** OUT Emersonn (£5.4m) → IN N.Jackson (£7.2m)  
**Bank after:** £4.1m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Trafford** (GKP, £5.0m): 5.31 xPts for GW11, ranked #1/13 among GKPs in candidate pool. Leeds fixture. P(start) 90%.
- **De Cuyper** (DEF, £5.5m): 4.21 xPts for GW11, ranked #15/49 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Gabriel** (DEF, £8.1m) [**Vice**]: 7.18 xPts for GW11, ranked #1/49 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Konsa** (DEF, £4.9m): 6.99 xPts for GW11, ranked #2/49 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Vuskovic** (DEF, £5.3m): 4.73 xPts for GW11, ranked #9/49 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Cherki** (MID, £7.9m): 3.81 xPts for GW11, ranked #15/69 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £6.3m): 3.88 xPts for GW11, ranked #13/69 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Rudoni** (MID, £4.9m): 4.58 xPts for GW11, ranked #3/69 among MIDs in candidate pool. Coventry City fixture. P(start) 90%.
- **Evanilson** (FWD, £6.5m): 5.42 xPts for GW11, ranked #3/19 among FWDs in candidate pool. Bournemouth fixture. P(start) 90%.
- **Haaland** (FWD, £15.8m): 3.63 xPts for GW11, ranked #7/19 among FWDs in candidate pool. Man City fixture. P(start) 90%.
- **N.Jackson** (FWD, £7.2m) [**CAPTAIN**]: Highest projected return in the squad: 7.04 xPts (14.09 effective with captain double). Ranked #1/19 among FWDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Manzambi** (MID, £6.2m): 2.97 xPts. Budget saved here funds the premium XI picks.
- **Belloumi** (MID, £5.3m): 3.12 xPts. Budget saved here funds the premium XI picks.
- **Bogle** (DEF, £4.8m): 3.24 xPts. Budget saved here funds the premium XI picks.
- **Tzolakis** (GKP, £4.7m): 2.97 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Emersonn (£5.4m, 1.17 xPts GW11)
**IN:** N.Jackson (£7.2m, 7.04 xPts GW11)
**Net this week:** +5.87 xPts

N.Jackson projects 7.04 xPts vs Emersonn's 1.17 — a 5.87 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

---

## Players We Considered But Didn't Pick

### GW10 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Alysson** (MID, Aston Villa): **16 pts in GW10** (1 goal, 2 assists). Not transferred in — Forward projection 4.31 xPts (ranked #28 overall) is competitive, but bringing them in at £4.9m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Barry** (FWD, Everton): **16 pts in GW10** (2 goals, 1 assist). Not transferred in — Forward projection 5.84 xPts (ranked #7 overall) is competitive, but bringing them in at £5.7m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Delap** (FWD, Nott'm Forest): **13 pts in GW10** (2 goals). Not transferred in — Forward projection 3.98 xPts (ranked #39 overall) is competitive, but bringing them in at £5.3m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Wirtz** (MID, Liverpool): **13 pts in GW10** (1 goal, 1 assist). Not transferred in — Forward projection 4.82 xPts (ranked #16 overall) is competitive, but bringing them in at £7.2m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Ødegaard** (MID, Arsenal): **11 pts in GW10** (1 goal, clean sheet). Not transferred in — Forward projection 4.11 xPts (ranked #37 overall) is competitive, but bringing them in at £6.7m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Dorgu** (MID, Man Utd): **11 pts in GW10** (1 goal, 1 assist). Not transferred in — Their 6-GW projected return sits outside the top-150 candidate pool. The EWMA model hasn't built enough forward-looking form from this performance to move them into contention yet.
- **E.Le Fée** (MID, Sunderland): **11 pts in GW10** (1 goal, clean sheet). Not transferred in — Forward projection 5.37 xPts (ranked #10 overall) is competitive, but bringing them in at £5.7m would require dropping a player the MILP values more over the full 6-GW horizon.
- **McGinn** (MID, Aston Villa): **10 pts in GW10** (1 goal, 1 assist). Not transferred in — Forward projection 4.26 xPts (ranked #30 overall) is competitive, but bringing them in at £5.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Muharemović** (DEF, Leeds): **10 pts in GW10** (clean sheet, DEFCON bonus (13 CBIT)). Not transferred in — Forward projection 6.57 xPts (ranked #4 overall) is competitive, but bringing them in at £5.1m would require dropping a player the MILP values more over the full 6-GW horizon.

### Highest-Projected Players Not In Your Squad

- **Muharemović** (DEF, Leeds, £5.1m, 6.57 xPts): ranked #4 overall. £5.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Araujo** (DEF, Liverpool, £5.5m, 6.30 xPts): ranked #5 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Van Hecke** (DEF, Spurs, £5.1m, 5.94 xPts): ranked #6 overall. £5.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Barry** (FWD, Everton, £5.7m, 5.84 xPts): ranked #7 overall. £5.7m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Silva** (DEF, Bournemouth, £5.1m, 5.67 xPts): ranked #8 overall. £5.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **E.Le Fée** (MID, Sunderland, £5.7m, 5.37 xPts): ranked #10 overall. £5.7m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Raya** (GKP, Arsenal, £6.2m, 5.05 xPts): ranked #12 overall. £6.2m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Hill** (DEF, Bournemouth, £5.6m, 5.03 xPts): ranked #13 overall. £5.6m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers              | Chip   | Captain   | Vice      |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:-----------------------|:-------|:----------|:----------|----------:|-------------:|-------:|------:|:-------|
|   11 | N.Jackson ← Emersonn   | —      | N.Jackson | Gabriel   |     63.84 |        12.3  |      0 |     1 | £4.1m  |
|   12 | Barry ← Haaland        | —      | N.Jackson | Groß      |     64.38 |        15.61 |      0 |     1 | £14.1m |
|   13 | B.Fernandes ← Manzambi | —      | Barry     | N.Jackson |     58.07 |        15.1  |      0 |     1 | £8.5m  |
|   14 | Roll                   | —      | Barry     | N.Jackson |     71.6  |        14.54 |      0 |     2 | £8.5m  |
|   15 | Roll                   | —      | N.Jackson | Gabriel   |     63.07 |        14.88 |      0 |     3 | £8.5m  |
|   16 | Roll                   | —      | Barry     | Groß      |     69.43 |        12.75 |      0 |     4 | £8.5m  |

---

## Starting XI — GW11

**GKP:** **Trafford**  
**DEF:** **Gabriel** | **Konsa** | **De Cuyper** | **Vuskovic**  
**MID:** **Groß** | **Rudoni** | **Cherki**  
**FWD:** **Evanilson** | **N.Jackson**(C) | **Haaland**  

**Bench:** Manzambi | Belloumi | Bogle | Tzolakis