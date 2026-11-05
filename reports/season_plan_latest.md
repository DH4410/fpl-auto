# FPL Season Plan — GW10

*Generated 2026-11-05 13:57 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW10

**Captain:** Rudoni (6.11 xPts → 12.22 effective with double)  
**Vice:** Konsa (6.19 xPts)  
**Transfer:** OUT Henderson (£4.9m) → IN Rudoni (£4.9m)  
**Bank after:** £5.9m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Trafford** (GKP, £5.0m): 3.84 xPts for GW10, ranked #6/15 among GKPs in candidate pool. Leeds fixture. P(start) 90%.
- **De Cuyper** (DEF, £5.4m): 5.24 xPts for GW10, ranked #4/47 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Gabriel** (DEF, £8.0m): 5.89 xPts for GW10, ranked #2/47 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Konsa** (DEF, £4.8m) [**Vice**]: 6.19 xPts for GW10, ranked #1/47 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Vuskovic** (DEF, £5.2m): 4.75 xPts for GW10, ranked #7/47 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Belloumi** (MID, £5.3m): 4.20 xPts for GW10, ranked #6/69 among MIDs in candidate pool. Hull City fixture. P(start) 90%.
- **Groß** (MID, £6.2m): 3.52 xPts for GW10, ranked #17/69 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Manzambi** (MID, £6.1m): 5.40 xPts for GW10, ranked #2/69 among MIDs in candidate pool. Aston Villa fixture. P(start) 90%.
- **Rudoni** (MID, £4.9m) [**CAPTAIN**]: Highest projected return in the squad: 6.11 xPts (12.22 effective with captain double). Ranked #1/69 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Evanilson** (FWD, £6.4m): 5.34 xPts for GW10, ranked #2/19 among FWDs in candidate pool. Bournemouth fixture. P(start) 90%.
- **Haaland** (FWD, £15.8m): 5.14 xPts for GW10, ranked #3/19 among FWDs in candidate pool. Man City fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Emersonn** (FWD, £5.5m): 1.62 xPts. Budget saved here funds the premium XI picks.
- **Bogle** (DEF, £4.8m): 2.26 xPts. Budget saved here funds the premium XI picks.
- **Cherki** (MID, £7.9m): 3.01 xPts. Budget saved here funds the premium XI picks.
- **Tzolakis** (GKP, £4.7m): 1.98 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Henderson (£4.9m, 1.70 xPts GW10)
**IN:** Rudoni (£4.9m, 6.11 xPts GW10)
**Net this week:** +4.41 xPts

Rudoni projects 6.11 xPts vs Henderson's 1.70 — a 4.41 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

---

## Players We Considered But Didn't Pick

### GW9 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Van Hecke** (DEF, Spurs): **15 pts in GW9** (1 goal, clean sheet). Not transferred in — Forward projection 4.80 xPts (ranked #14 overall) is competitive, but bringing them in at £5.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **McBurnie** (FWD, Hull City): **12 pts in GW9** (1 goal, 1 assist). Not transferred in — Forward projection 4.26 xPts (ranked #21 overall) is competitive, but bringing them in at £5.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Verbruggen** (GKP, Brighton): **11 pts in GW9** (clean sheet, 7 saves). Not transferred in — The 3-player Brighton cap is already maxed in the squad. Bringing them in would mean dropping another Brighton player, which the MILP found to be a worse 6-GW outcome.
- **Fofana** (DEF, Chelsea): **11 pts in GW9** (1 assist, clean sheet). Not transferred in — Forward projection 3.38 xPts (ranked #58 overall) is competitive, but bringing them in at £5.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Van den Berg** (DEF, Brentford): **10 pts in GW9** (clean sheet). Not transferred in — Forward projection 3.10 xPts (ranked #76 overall) is competitive, but bringing them in at £4.9m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Ayari** (MID, Brighton): **10 pts in GW9** (1 goal, clean sheet). Not transferred in — The 3-player Brighton cap is already maxed in the squad. Bringing them in would mean dropping another Brighton player, which the MILP found to be a worse 6-GW outcome.
- **Havertz** (FWD, Arsenal): **9 pts in GW9** (1 goal, clean sheet). Not transferred in — Forward projection 3.00 xPts (ranked #84 overall) is competitive, but bringing them in at £7.4m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Rogers** (MID, Chelsea): **9 pts in GW9** (1 goal, clean sheet). Not transferred in — Forward projection 3.81 xPts (ranked #38 overall) is competitive, but bringing them in at £8.2m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Thiago** (FWD, Brentford): **9 pts in GW9** (1 goal, clean sheet). Not transferred in — Forward projection 4.10 xPts (ranked #27 overall) is competitive, but bringing them in at £7.8m would require dropping a player the MILP values more over the full 6-GW horizon.

### Highest-Projected Players Not In Your Squad

- **Silva** (DEF, Bournemouth, £5.1m, 5.40 xPts): ranked #5 overall. £5.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **N.Jackson** (FWD, Aston Villa, £7.0m, 5.39 xPts): ranked #6 overall. £7.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Verbruggen** (GKP, Brighton, £4.6m, 5.31 xPts): ranked #8 overall. 3-player Brighton cap is maxed.
- **Hill** (DEF, Bournemouth, £5.5m, 4.96 xPts): ranked #11 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Raya** (GKP, Arsenal, £6.2m, 4.94 xPts): ranked #12 overall. £6.2m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Ayari** (MID, Brighton, £5.4m, 4.86 xPts): ranked #13 overall. 3-player Brighton cap is maxed.
- **Van Hecke** (DEF, Spurs, £5.0m, 4.80 xPts): ranked #14 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Tavernier** (MID, Bournemouth, £6.6m, 4.76 xPts): ranked #15 overall. £6.6m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers            | Chip   | Captain   | Vice      |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:---------------------|:-------|:----------|:----------|----------:|-------------:|-------:|------:|:-------|
|   10 | Rudoni ← Henderson   | —      | Rudoni    | Konsa     |     61.73 |         8.87 |      0 |     1 | £5.9m  |
|   11 | N.Jackson ← Emersonn | —      | Haaland   | De Cuyper |     66.07 |        14.69 |      0 |     1 | £4.4m  |
|   12 | Van Hecke ← Bogle    | —      | N.Jackson | De Cuyper |     61.55 |        15.31 |      0 |     1 | £4.1m  |
|   13 | Roll                 | —      | Haaland   | Van Hecke |     58.49 |        14.99 |      0 |     2 | £4.1m  |
|   14 | Roll                 | —      | N.Jackson | De Cuyper |     64.93 |        14.72 |      0 |     3 | £4.1m  |
|   15 | Roll                 | —      | N.Jackson | De Cuyper |     61.98 |        14.7  |      0 |     4 | £4.1m  |

---

## Starting XI — GW10

**GKP:** **Trafford**  
**DEF:** **Gabriel** | **Konsa** | **De Cuyper** | **Vuskovic**  
**MID:** **Manzambi** | **Groß** | **Rudoni**(C) | **Belloumi**  
**FWD:** **Evanilson** | **Haaland**  

**Bench:** Emersonn | Bogle | Cherki | Tzolakis