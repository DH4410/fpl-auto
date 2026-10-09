# FPL Season Plan — GW6

*Generated 2026-10-09 16:48 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW6

**Captain:** Groß (10.49 xPts → 20.98 effective with double)  
**Vice:** Belloumi (7.65 xPts)  
**Transfer:** OUT Ødegaard (£6.7m) → IN Schade (£6.2m)  
**Transfer:** OUT B.Fernandes (£11.9m) → IN Groß (£5.9m)  
**Transfer:** OUT M.Sangaré (£5.6m) → IN Belloumi (£5.1m)  
**Hits:** 2 (−8 pts)  
**Bank after:** £10.6m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Trafford** (GKP, £5.0m): 4.11 xPts for GW6, ranked #8/13 among GKPs in candidate pool. Leeds fixture. P(start) 90%.
- **Bogle** (DEF, £4.6m): 5.75 xPts for GW6, ranked #9/47 among DEFs in candidate pool. Leeds fixture. P(start) 90%.
- **Calafiori** (DEF, £5.9m): 3.52 xPts for GW6, ranked #34/47 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Gvardiol** (DEF, £5.7m): 5.30 xPts for GW6, ranked #14/47 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Tarkowski** (DEF, £6.2m): 7.28 xPts for GW6, ranked #2/47 among DEFs in candidate pool. Everton fixture. P(start) 90%.
- **Belloumi** (MID, £5.1m) [**Vice**]: 7.65 xPts for GW6, ranked #2/75 among MIDs in candidate pool. Hull City fixture. P(start) 90%.
- **Cherki** (MID, £7.8m): 4.44 xPts for GW6, ranked #8/75 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £5.9m) [**CAPTAIN**]: Highest projected return in the squad: 10.49 xPts (20.97 effective with captain double). Ranked #1/75 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Saka** (MID, £9.6m): 4.17 xPts for GW6, ranked #10/75 among MIDs in candidate pool. Arsenal fixture. P(start) 90%.
- **Schade** (MID, £6.2m): 7.60 xPts for GW6, ranked #3/75 among MIDs in candidate pool. Brentford fixture. P(start) 90%.
- **Emersonn** (FWD, £5.5m): 6.30 xPts for GW6, ranked #2/15 among FWDs in candidate pool. Ipswich Town fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **João Pedro** (FWD, £7.7m): 3.47 xPts. Budget saved here funds the premium XI picks.
- **Ajayi** (DEF, £4.2m): 1.58 xPts. Budget saved here funds the premium XI picks.
- **Wissa** (FWD, £6.2m): 1.34 xPts. Budget saved here funds the premium XI picks.
- **Tzolakis** (GKP, £4.7m): 3.60 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Ødegaard (£6.7m, 2.27 xPts GW6)
**IN:** Schade (£6.2m, 7.60 xPts GW6)
**Net this week:** +5.32 xPts

Schade projects 7.60 xPts vs Ødegaard's 2.27 — a 5.32 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** B.Fernandes (£11.9m, 3.45 xPts GW6)
**IN:** Groß (£5.9m, 10.49 xPts GW6)
**Net this week:** +7.03 xPts

Groß projects 10.49 xPts vs B.Fernandes's 3.45 — a 7.03 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** M.Sangaré (£5.6m, 1.80 xPts GW6)
**IN:** Belloumi (£5.1m, 7.65 xPts GW6)
**Net this week:** +5.85 xPts

Belloumi projects 7.65 xPts vs M.Sangaré's 1.80 — a 5.85 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

⚠️ **2 hit(s) required (−8 pts).** The planner only recommends hits when the projected 6-GW gain exceeds the penalty. Skip and roll if you want to avoid the risk — the bot will adapt next week.

---

## Players We Considered But Didn't Pick

### GW5 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Semenyo** (MID, Man City): **17 pts in GW5** (2 goals, 1 assist). Not transferred in — Forward projection: 2.40 xPts for GW6 (ranked #120 overall). This is below our lowest-ranked MID in the squad (4.17 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Brobbey** (FWD, Sunderland): **17 pts in GW5** (3 goals). Not transferred in — Forward projection: 0.00 xPts for GW6 (ranked #149 overall). This is below our lowest-ranked FWD in the squad (1.34 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Dasilva** (DEF, Coventry City): **15 pts in GW5** (1 goal, clean sheet). Not transferred in — Forward projection 6.75 xPts (ranked #7 overall) is competitive, but bringing them in at £4.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Schuster** (DEF, Brentford): **14 pts in GW5** (1 assist, clean sheet, DEFCON bonus (15 CBIT)). Not transferred in — Forward projection 7.65 xPts (ranked #3 overall) is competitive, but bringing them in at £4.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Manzambi** (MID, Aston Villa): **13 pts in GW5** (1 goal, 1 assist, clean sheet). Not transferred in — Forward projection 6.30 xPts (ranked #9 overall) is competitive, but bringing them in at £5.9m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Hall** (DEF, Newcastle): **13 pts in GW5** (1 goal). Not transferred in — Forward projection 5.33 xPts (ranked #25 overall) is competitive, but bringing them in at £5.3m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Buendía** (MID, Aston Villa): **12 pts in GW5** (1 goal). Not transferred in — Forward projection 5.52 xPts (ranked #21 overall) is competitive, but bringing them in at £5.9m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Rushworth** (GKP, Coventry City): **11 pts in GW5** (1 assist, clean sheet). Not transferred in — Forward projection 4.79 xPts (ranked #32 overall) is competitive, but bringing them in at £4.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Anthony** (MID, Brentford): **10 pts in GW5** (1 goal, clean sheet). Not transferred in — Forward projection: 4.03 xPts for GW6 (ranked #53 overall). This is below our lowest-ranked MID in the squad (4.17 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Kostoulas** (FWD, Brighton): **10 pts in GW5** (1 goal, 1 assist, clean sheet). Not transferred in — Forward projection 6.32 xPts (ranked #8 overall) is competitive, but bringing them in at £5.6m would require dropping a player the MILP values more over the full 6-GW horizon.

### Highest-Projected Players Not In Your Squad

- **Schuster** (DEF, Brentford, £4.5m, 7.65 xPts): ranked #3 overall. £4.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Jacquet** (DEF, Liverpool, £5.0m, 6.75 xPts): ranked #6 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Dasilva** (DEF, Coventry City, £4.0m, 6.75 xPts): ranked #7 overall. £4.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Kostoulas** (FWD, Brighton, £5.6m, 6.32 xPts): ranked #8 overall. £5.6m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Manzambi** (MID, Aston Villa, £5.9m, 6.30 xPts): ranked #9 overall. £5.9m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **A.Becker** (GKP, Liverpool, £5.5m, 6.10 xPts): ranked #11 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **De Cuyper** (DEF, Brighton, £5.0m, 5.95 xPts): ranked #12 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Araujo** (DEF, Liverpool, £5.4m, 5.85 xPts): ranked #14 overall. £5.4m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers                                                   | Chip   | Captain   | Vice     |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:------------------------------------------------------------|:-------|:----------|:---------|----------:|-------------:|-------:|------:|:-------|
|    6 | Schade ← Ødegaard, Groß ← B.Fernandes, Belloumi ← M.Sangaré | —      | Groß      | Belloumi |     77.08 |         9.98 |      2 |     1 | £10.6m |
|    7 | Haaland ← Wissa                                             | —      | Groß      | Haaland  |     77.57 |        13.69 |      0 |     1 | £1.1m  |
|    8 | Schuster ← Ajayi                                            | —      | Schade    | Groß     |     75.77 |        16.62 |      0 |     1 | £0.7m  |
|    9 | Roll                                                        | —      | Schade    | Bogle    |     73.28 |        16.22 |      0 |     2 | £0.7m  |
|   10 | Roll                                                        | —      | Groß      | Bogle    |     78.33 |        15.25 |      0 |     3 | £0.7m  |
|   11 | Roll                                                        | —      | Groß      | Haaland  |     80.31 |        16.85 |      0 |     4 | £0.7m  |

---

## Starting XI — GW6

**GKP:** **Trafford**  
**DEF:** **Calafiori** | **Tarkowski** | **Bogle** | **Gvardiol**  
**MID:** **Saka** | **Schade** | **Groß**(C) | **Belloumi** | **Cherki**  
**FWD:** **Emersonn**  

**Bench:** João Pedro | Ajayi | Wissa | Tzolakis