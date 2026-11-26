# FPL Season Plan — GW12

*Generated 2026-11-26 13:31 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW12

**Captain:** Rudoni (7.01 xPts → 14.02 effective with double)  
**Vice:** Wirtz (6.08 xPts)  
**Transfer:** OUT Manzambi (£6.0m) → IN Wirtz (£7.4m)  
**Bank after:** £2.7m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Tzolakis** (GKP, £4.7m): 5.13 xPts for GW12, ranked #2/11 among GKPs in candidate pool. Hull City fixture. P(start) 90%.
- **De Cuyper** (DEF, £5.5m): 4.43 xPts for GW12, ranked #9/46 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Gabriel** (DEF, £8.1m): 3.98 xPts for GW12, ranked #15/46 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Konsa** (DEF, £4.9m): 4.47 xPts for GW12, ranked #8/46 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Vuskovic** (DEF, £5.3m): 4.23 xPts for GW12, ranked #12/46 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Belloumi** (MID, £5.2m): 4.02 xPts for GW12, ranked #15/75 among MIDs in candidate pool. Hull City fixture. P(start) 90%.
- **Groß** (MID, £6.3m): 4.57 xPts for GW12, ranked #8/75 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Rudoni** (MID, £5.0m) [**CAPTAIN**]: Highest projected return in the squad: 7.01 xPts (14.02 effective with captain double). Ranked #1/75 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Wirtz** (MID, £7.4m) [**Vice**]: 6.08 xPts for GW12, ranked #2/75 among MIDs in candidate pool. Liverpool fixture. P(start) 90%.
- **Evanilson** (FWD, £6.6m): 5.18 xPts for GW12, ranked #3/18 among FWDs in candidate pool. Bournemouth fixture. P(start) 90%.
- **N.Jackson** (FWD, £7.3m): 4.72 xPts for GW12, ranked #4/18 among FWDs in candidate pool. Aston Villa fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Bogle** (DEF, £4.8m): 2.83 xPts. Budget saved here funds the premium XI picks.
- **Trafford** (GKP, £5.1m): 4.09 xPts. Budget saved here funds the premium XI picks.
- **Cherki** (MID, £7.8m): 3.29 xPts. Budget saved here funds the premium XI picks.
- **Haaland** (FWD, £15.8m): 1.91 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Manzambi (£6.0m, 2.97 xPts GW12)
**IN:** Wirtz (£7.4m, 6.08 xPts GW12)
**Net this week:** +3.11 xPts

Wirtz projects 6.08 xPts vs Manzambi's 2.97 — a 3.11 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

---

## Players We Considered But Didn't Pick

### GW11 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Lewis-Potter** (MID, Brentford): **16 pts in GW11** (2 goals, clean sheet). Not transferred in — Forward projection 4.63 xPts (ranked #20 overall) is competitive, but bringing them in at £5.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Okafor** (MID, Leeds): **15 pts in GW11** (2 goals). Not transferred in — Forward projection 4.54 xPts (ranked #22 overall) is competitive, but bringing them in at £5.7m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Hill** (DEF, Bournemouth): **14 pts in GW11** (1 assist, clean sheet, DEFCON bonus (12 CBIT)). Not transferred in — Forward projection 5.48 xPts (ranked #6 overall) is competitive, but bringing them in at £5.7m would require dropping a player the MILP values more over the full 6-GW horizon.
- **McBurnie** (FWD, Hull City): **13 pts in GW11** (2 goals, clean sheet). Not transferred in — Forward projection 5.99 xPts (ranked #3 overall) is competitive, but bringing them in at £5.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Ngumoha** (MID, Liverpool): **13 pts in GW11** (1 goal, 1 assist, clean sheet). Not transferred in — Forward projection 3.50 xPts (ranked #64 overall) is competitive, but bringing them in at £5.7m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Williams-Barnett** (MID, Spurs): **13 pts in GW11** (1 goal, 1 assist, clean sheet). Not transferred in — Forward projection 3.52 xPts (ranked #63 overall) is competitive, but bringing them in at £4.4m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Yeremy** (MID, Crystal Palace): **12 pts in GW11** (1 goal, 1 assist). Not transferred in — Their 6-GW projected return sits outside the top-150 candidate pool. The EWMA model hasn't built enough forward-looking form from this performance to move them into contention yet.
- **Pedro Porro** (DEF, Spurs): **11 pts in GW11** (1 assist, clean sheet). Not transferred in — Forward projection 5.11 xPts (ranked #13 overall) is competitive, but bringing them in at £5.3m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Roefs** (GKP, Sunderland): **11 pts in GW11** (clean sheet, 8 saves). Not transferred in — Forward projection 5.47 xPts (ranked #7 overall) is competitive, but bringing them in at £4.8m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Eze** (MID, Arsenal): **10 pts in GW11** (1 goal). Not transferred in — Forward projection: 3.12 xPts for GW12 (ranked #84 overall). This is below our lowest-ranked MID in the squad (3.29 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Janelt** (MID, Brentford): **10 pts in GW11** (1 goal, clean sheet). Not transferred in — Forward projection 3.75 xPts (ranked #51 overall) is competitive, but bringing them in at £5.0m would require dropping a player the MILP values more over the full 6-GW horizon.

### Highest-Projected Players Not In Your Squad

- **McBurnie** (FWD, Hull City, £5.5m, 5.99 xPts): ranked #3 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Barry** (FWD, Everton, £5.7m, 5.89 xPts): ranked #4 overall. £5.7m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Silva** (DEF, Bournemouth, £5.1m, 5.67 xPts): ranked #5 overall. £5.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Hill** (DEF, Bournemouth, £5.7m, 5.48 xPts): ranked #6 overall. £5.7m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Roefs** (GKP, Sunderland, £4.8m, 5.47 xPts): ranked #7 overall. £4.8m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **E.Le Fée** (MID, Sunderland, £5.7m, 5.46 xPts): ranked #8 overall. £5.7m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Van Hecke** (DEF, Spurs, £5.2m, 5.42 xPts): ranked #9 overall. £5.2m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Araujo** (DEF, Liverpool, £5.5m, 5.13 xPts): ranked #12 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers         | Chip   | Captain   | Vice      |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:------------------|:-------|:----------|:----------|----------:|-------------:|-------:|------:|:-------|
|   12 | Wirtz ← Manzambi  | —      | Rudoni    | Wirtz     |     60.83 |        12.13 |      0 |     1 | £2.7m  |
|   13 | Van Hecke ← Bogle | —      | Wirtz     | Van Hecke |     60.01 |        15.36 |      0 |     1 | £2.2m  |
|   14 | Barry ← Haaland   | —      | Barry     | Groß      |     66.06 |        15.78 |      0 |     1 | £12.2m |
|   15 | Roll              | —      | Groß      | Van Hecke |     61.79 |        15.44 |      0 |     2 | £12.2m |
|   16 | Roll              | —      | Barry     | Groß      |     66.2  |        14.46 |      0 |     3 | £12.2m |
|   17 | Roll              | —      | Barry     | Wirtz     |     64.92 |        16.42 |      0 |     4 | £12.2m |

---

## Starting XI — GW12

**GKP:** **Tzolakis**  
**DEF:** **Gabriel** | **Konsa** | **De Cuyper** | **Vuskovic**  
**MID:** **Groß** | **Rudoni**(C) | **Belloumi** | **Wirtz**  
**FWD:** **Evanilson** | **N.Jackson**  

**Bench:** Bogle | Trafford | Cherki | Haaland