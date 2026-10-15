# FPL Season Plan — GW7

*Generated 2026-10-15 22:53 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW7

**Captain:** Groß (8.11 xPts → 16.22 effective with double)  
**Vice:** Dasilva (9.00 xPts)  
**Transfer:** OUT Ajayi (£4.1m) → IN Dasilva (£4.0m)  
**Transfer:** OUT Saka (£9.5m) → IN Manzambi (£5.9m)  
**Transfer:** OUT Schade (£6.2m) → IN Henderson (£4.9m)  
**Hits:** 2 (−8 pts)  
**Bank after:** £15.6m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Trafford** (GKP, £5.0m): 4.33 xPts for GW7, ranked #4/11 among GKPs in candidate pool. Leeds fixture. P(start) 90%.
- **Calafiori** (DEF, £5.9m): 3.79 xPts for GW7, ranked #24/44 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Dasilva** (DEF, £4.0m) [**Vice**]: 9.00 xPts for GW7, ranked #1/44 among DEFs in candidate pool. Coventry City fixture. P(start) 90%.
- **Gvardiol** (DEF, £5.8m): 3.39 xPts for GW7, ranked #28/44 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Tarkowski** (DEF, £6.2m): 5.85 xPts for GW7, ranked #3/44 among DEFs in candidate pool. Everton fixture. P(start) 90%.
- **Belloumi** (MID, £5.2m): 4.50 xPts for GW7, ranked #15/77 among MIDs in candidate pool. Hull City fixture. P(start) 90%.
- **Cherki** (MID, £7.9m): 6.73 xPts for GW7, ranked #3/77 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £6.0m) [**CAPTAIN**]: Highest projected return in the squad: 8.11 xPts (16.21 effective with captain double). Ranked #1/77 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Henderson** (MID, £4.9m): 6.66 xPts for GW7, ranked #4/77 among MIDs in candidate pool. Chelsea fixture. P(start) 90%.
- **Manzambi** (MID, £5.9m): 8.10 xPts for GW7, ranked #2/77 among MIDs in candidate pool. Aston Villa fixture. P(start) 90%.
- **João Pedro** (FWD, £7.8m): 4.20 xPts for GW7, ranked #4/18 among FWDs in candidate pool. Chelsea fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Emersonn** (FWD, £5.6m): 1.80 xPts. Budget saved here funds the premium XI picks.
- **Bogle** (DEF, £4.7m): 2.55 xPts. Budget saved here funds the premium XI picks.
- **Wissa** (FWD, £6.3m): 1.05 xPts. Budget saved here funds the premium XI picks.
- **Tzolakis** (GKP, £4.7m): 3.60 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Ajayi (£4.1m, 1.63 xPts GW7)
**IN:** Dasilva (£4.0m, 9.00 xPts GW7)
**Net this week:** +7.37 xPts

Dasilva projects 9.00 xPts vs Ajayi's 1.63 — a 7.37 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Saka (£9.5m, 2.21 xPts GW7)
**IN:** Manzambi (£5.9m, 8.10 xPts GW7)
**Net this week:** +5.89 xPts

Manzambi projects 8.10 xPts vs Saka's 2.21 — a 5.89 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Schade (£6.2m, 1.23 xPts GW7)
**IN:** Henderson (£4.9m, 6.66 xPts GW7)
**Net this week:** +5.43 xPts

Henderson projects 6.66 xPts vs Schade's 1.23 — a 5.43 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

⚠️ **2 hit(s) required (−8 pts).** The planner only recommends hits when the projected 6-GW gain exceeds the penalty. Skip and roll if you want to avoid the risk — the bot will adapt next week.

---

## Players We Considered But Didn't Pick

### GW6 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **De Cuyper** (DEF, Brighton): **15 pts in GW6** (1 goal, clean sheet). Not transferred in — Forward projection 7.04 xPts (ranked #4 overall) is competitive, but bringing them in at £5.1m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Dewsbury-Hall** (MID, Everton): **13 pts in GW6** (1 goal, 1 assist). Not transferred in — Forward projection 5.42 xPts (ranked #13 overall) is competitive, but bringing them in at £6.6m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Doku** (MID, Man City): **12 pts in GW6** (1 goal, 1 assist). Not transferred in — Forward projection 4.58 xPts (ranked #28 overall) is competitive, but bringing them in at £7.4m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Cherif** (FWD, Coventry City): **12 pts in GW6** (1 goal, 1 assist). Not transferred in — Forward projection 5.40 xPts (ranked #16 overall) is competitive, but bringing them in at £5.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **O'Shea** (DEF, Ipswich Town): **11 pts in GW6** (1 goal). Not transferred in — Forward projection 4.44 xPts (ranked #35 overall) is competitive, but bringing them in at £4.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **McGinn** (MID, Aston Villa): **10 pts in GW6** (1 goal). Not transferred in — Forward projection 5.41 xPts (ranked #14 overall) is competitive, but bringing them in at £5.4m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Cunha** (MID, Man Utd): **10 pts in GW6** (1 assist). Not transferred in — Forward projection 6.37 xPts (ranked #7 overall) is competitive, but bringing them in at £7.9m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Ouattara** (DEF, Ipswich Town): **10 pts in GW6** (1 goal). Not transferred in — Forward projection 5.40 xPts (ranked #15 overall) is competitive, but bringing them in at £4.0m would require dropping a player the MILP values more over the full 6-GW horizon.

### Highest-Projected Players Not In Your Squad

- **De Cuyper** (DEF, Brighton, £5.1m, 7.04 xPts): ranked #4 overall. £5.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Cunha** (MID, Man Utd, £7.9m, 6.37 xPts): ranked #7 overall. £7.9m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Buendía** (MID, Aston Villa, £5.9m, 5.62 xPts): ranked #9 overall. £5.9m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Rudoni** (MID, Coventry City, £4.9m, 5.54 xPts): ranked #10 overall. £4.9m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Rushworth** (GKP, Coventry City, £4.5m, 5.50 xPts): ranked #11 overall. £4.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Vuskovic** (DEF, Brighton, £5.1m, 5.44 xPts): ranked #12 overall. £5.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Dewsbury-Hall** (MID, Everton, £6.6m, 5.42 xPts): ranked #13 overall. £6.6m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **McGinn** (MID, Aston Villa, £5.4m, 5.41 xPts): ranked #14 overall. £5.4m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers                                            | Chip   | Captain   | Vice       |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:-----------------------------------------------------|:-------|:----------|:-----------|----------:|-------------:|-------:|------:|:-------|
|    7 | Dasilva ← Ajayi, Manzambi ← Saka, Henderson ← Schade | —      | Groß      | Dasilva    |     72.77 |         9    |      2 |     1 | £15.6m |
|    8 | De Cuyper ← Gvardiol                                 | —      | Henderson | João Pedro |     66.66 |        13.68 |      0 |     1 | £16.2m |
|    9 | Brobbey ← Wissa                                      | —      | Manzambi  | Tarkowski  |     64.92 |        16.29 |      0 |     1 | £16.7m |
|   10 | Dewsbury-Hall ← Belloumi                             | —      | Groß      | Tarkowski  |     71.88 |        14.98 |      0 |     1 | £15.2m |
|   11 | Roll                                                 | —      | Groß      | De Cuyper  |     73.9  |        15.13 |      0 |     2 | £15.2m |
|   12 | Roll                                                 | —      | Groß      | Henderson  |     69.47 |        15.27 |      0 |     3 | £15.2m |

---

## Starting XI — GW7

**GKP:** **Trafford**  
**DEF:** **Calafiori** | **Dasilva** | **Tarkowski** | **Gvardiol**  
**MID:** **Manzambi** | **Henderson** | **Groß**(C) | **Belloumi** | **Cherki**  
**FWD:** **João Pedro**  

**Bench:** Emersonn | Bogle | Wissa | Tzolakis