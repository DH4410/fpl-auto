# FPL Season Plan — GW8

*Generated 2026-10-22 09:10 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW8

**Captain:** Henderson (6.67 xPts → 13.34 effective with double)  
**Vice:** De Cuyper (6.93 xPts)  
**Transfer:** OUT Dasilva (£4.0m) → IN De Cuyper (£5.3m)  
**Transfer:** OUT Gvardiol (£5.7m) → IN Vuskovic (£5.1m)  
**Transfer:** OUT Wissa (£6.1m) → IN Haaland (£15.7m)  
**Hits:** 2 (−8 pts)  
**Bank after:** £5.3m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Trafford** (GKP, £5.0m): 2.60 xPts for GW8, ranked #8/12 among GKPs in candidate pool. Leeds fixture. P(start) 90%.
- **Calafiori** (DEF, £6.0m): 5.20 xPts for GW8, ranked #6/47 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **De Cuyper** (DEF, £5.3m) [**Vice**]: 6.93 xPts for GW8, ranked #1/47 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Vuskovic** (DEF, £5.1m): 6.15 xPts for GW8, ranked #2/47 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Belloumi** (MID, £5.2m): 3.60 xPts for GW8, ranked #28/73 among MIDs in candidate pool. Hull City fixture. P(start) 90%.
- **Cherki** (MID, £8.0m): 4.33 xPts for GW8, ranked #13/73 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £6.2m): 4.55 xPts for GW8, ranked #10/73 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Henderson** (MID, £4.9m) [**CAPTAIN**]: Highest projected return in the squad: 6.67 xPts (13.33 effective with captain double). Ranked #1/73 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Manzambi** (MID, £6.0m): 2.52 xPts for GW8, ranked #53/73 among MIDs in candidate pool. Aston Villa fixture. P(start) 70%.
- **Haaland** (FWD, £15.7m): 6.34 xPts for GW8, ranked #1/18 among FWDs in candidate pool. Man City fixture. P(start) 90%.
- **João Pedro** (FWD, £7.9m): 5.29 xPts for GW8, ranked #4/18 among FWDs in candidate pool. Chelsea fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Tarkowski** (DEF, £6.2m): 1.16 xPts. Budget saved here funds the premium XI picks.
- **Emersonn** (FWD, £5.6m): 1.80 xPts. Budget saved here funds the premium XI picks.
- **Bogle** (DEF, £4.7m): 2.41 xPts. Budget saved here funds the premium XI picks.
- **Tzolakis** (GKP, £4.7m): 2.25 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Dasilva (£4.0m, 2.25 xPts GW8)
**IN:** De Cuyper (£5.3m, 6.93 xPts GW8)
**Net this week:** +4.68 xPts

De Cuyper projects 6.93 xPts vs Dasilva's 2.25 — a 4.68 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Gvardiol (£5.7m, 2.13 xPts GW8)
**IN:** Vuskovic (£5.1m, 6.15 xPts GW8)
**Net this week:** +4.02 xPts

Vuskovic projects 6.15 xPts vs Gvardiol's 2.13 — a 4.02 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Wissa (£6.1m, 1.48 xPts GW8)
**IN:** Haaland (£15.7m, 6.34 xPts GW8)
**Net this week:** +4.86 xPts

Haaland projects 6.34 xPts vs Wissa's 1.48 — a 4.86 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

⚠️ **2 hit(s) required (−8 pts).** The planner only recommends hits when the projected 6-GW gain exceeds the penalty. Skip and roll if you want to avoid the risk — the bot will adapt next week.

---

## Players We Considered But Didn't Pick

### GW7 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Neto** (MID, Chelsea): **15 pts in GW7** (2 goals, clean sheet). Not transferred in — Forward projection 6.53 xPts (ranked #3 overall) is competitive, but bringing them in at £6.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Ayari** (MID, Brighton): **13 pts in GW7** (1 goal, 1 assist, clean sheet). Not transferred in — The 3-player Brighton cap is already maxed in the squad. Bringing them in would mean dropping another Brighton player, which the MILP found to be a worse 6-GW outcome.
- **Evanilson** (FWD, Bournemouth): **12 pts in GW7** (1 goal, 1 assist, clean sheet). Not transferred in — Forward projection 5.67 xPts (ranked #12 overall) is competitive, but bringing them in at £6.1m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Kostoulas** (FWD, Brighton): **12 pts in GW7** (1 goal, 1 assist, clean sheet). Not transferred in — The 3-player Brighton cap is already maxed in the squad. Bringing them in would mean dropping another Brighton player, which the MILP found to be a worse 6-GW outcome.
- **Hato** (DEF, Chelsea): **11 pts in GW7** (1 assist, clean sheet). Not transferred in — Forward projection 4.69 xPts (ranked #22 overall) is competitive, but bringing them in at £4.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Iwobi** (MID, Fulham): **11 pts in GW7** (1 goal, clean sheet). Not transferred in — Forward projection 5.49 xPts (ranked #14 overall) is competitive, but bringing them in at £5.4m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Tavernier** (MID, Bournemouth): **10 pts in GW7** (1 goal, clean sheet). Not transferred in — Forward projection 4.37 xPts (ranked #30 overall) is competitive, but bringing them in at £6.2m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Boscagli** (DEF, Brighton): **10 pts in GW7** (1 assist, clean sheet). Not transferred in — The 3-player Brighton cap is already maxed in the squad. Bringing them in would mean dropping another Brighton player, which the MILP found to be a worse 6-GW outcome.
- **Raya** (GKP, Arsenal): **9 pts in GW7** (clean sheet). Not transferred in — Forward projection 4.79 xPts (ranked #20 overall) is competitive, but bringing them in at £6.1m would require dropping a player the MILP values more over the full 6-GW horizon.

### Highest-Projected Players Not In Your Squad

- **Neto** (MID, Chelsea, £6.5m, 6.53 xPts): ranked #3 overall. £6.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Rudoni** (MID, Coventry City, £4.9m, 6.28 xPts): ranked #5 overall. £4.9m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Rice** (MID, Arsenal, £7.3m, 5.90 xPts): ranked #7 overall. £7.3m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Cherif** (FWD, Coventry City, £5.0m, 5.85 xPts): ranked #9 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Ouattara** (DEF, Ipswich Town, £4.0m, 5.85 xPts): ranked #8 overall. £4.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Doku** (MID, Man City, £7.5m, 5.85 xPts): ranked #10 overall. £7.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Mbeumo** (MID, Man Utd, £7.8m, 5.80 xPts): ranked #11 overall. £7.8m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Evanilson** (FWD, Bournemouth, £6.1m, 5.67 xPts): ranked #12 overall. £6.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers                                                 | Chip   | Captain   | Vice      |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:----------------------------------------------------------|:-------|:----------|:----------|----------:|-------------:|-------:|------:|:-------|
|    8 | De Cuyper ← Dasilva, Vuskovic ← Gvardiol, Haaland ← Wissa | —      | Henderson | De Cuyper |     60.82 |         7.62 |      2 |     1 | £5.3m  |
|    9 | Dewsbury-Hall ← Belloumi, Evanilson ← Emersonn            | —      | Haaland   | Evanilson |     61.88 |        14.86 |      1 |     1 | £3.2m  |
|   10 | Raya ← Trafford                                           | —      | Haaland   | De Cuyper |     73.69 |        14.32 |      0 |     1 | £2.1m  |
|   11 | Roll                                                      | —      | Haaland   | De Cuyper |     76.05 |        14.34 |      0 |     2 | £2.1m  |
|   12 | Roll                                                      | —      | Groß      | De Cuyper |     65.49 |        14.54 |      0 |     3 | £2.1m  |
|   13 | Roll                                                      | —      | Henderson | De Cuyper |     72.28 |        13.81 |      0 |     4 | £2.1m  |

---

## Starting XI — GW8

**GKP:** **Trafford**  
**DEF:** **Calafiori** | **De Cuyper** | **Vuskovic**  
**MID:** **Manzambi** | **Henderson**(C) | **Groß** | **Belloumi** | **Cherki**  
**FWD:** **João Pedro** | **Haaland**  

**Bench:** Tarkowski | Emersonn | Bogle | Tzolakis