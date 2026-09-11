# FPL Season Plan — GW4

*Generated 2026-09-11 02:55 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW4

**Chip:** Bench Boost  
**Captain:** B.Fernandes (6.68 xPts → 13.36 effective with double)  
**Vice:** Mendy (7.20 xPts)  
**Transfer:** OUT White (£5.5m) → IN Gvardiol (£5.6m)  
**Transfer:** OUT Palmer (£9.6m) → IN Ødegaard (£6.7m)  
**Bank after:** £4.2m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Tzolakis** (GKP, £4.6m): 7.83 xPts for GW4, ranked #1/16 among GKPs in candidate pool. Hull City fixture. P(start) 90%.
- **Ajayi** (DEF, £4.1m): 5.88 xPts for GW4, ranked #2/44 among DEFs in candidate pool. Hull City fixture. P(start) 90%.
- **Calafiori** (DEF, £5.8m): 5.04 xPts for GW4, ranked #6/44 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Gvardiol** (DEF, £5.6m): 5.14 xPts for GW4, ranked #4/44 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Mendy** (DEF, £4.1m) [**Vice**]: 7.20 xPts for GW4, ranked #1/44 among DEFs in candidate pool. Hull City fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 6.68 xPts (13.37 effective with captain double). Ranked #1/76 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Cherki** (MID, £7.8m): 5.94 xPts for GW4, ranked #2/76 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **M.Sangaré** (MID, £5.7m): 5.40 xPts for GW4, ranked #3/76 among MIDs in candidate pool. Brentford fixture. P(start) 90%.
- **Saka** (MID, £9.5m): 5.37 xPts for GW4, ranked #4/76 among MIDs in candidate pool. Arsenal fixture. P(start) 90%.
- **Ødegaard** (MID, £6.7m): 5.31 xPts for GW4, ranked #5/76 among MIDs in candidate pool. Arsenal fixture. P(start) 90%.
- **João Pedro** (FWD, £7.7m): 4.67 xPts for GW4, ranked #3/14 among FWDs in candidate pool. Chelsea fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Tarkowski** (DEF, £6.0m): 4.93 xPts. Budget saved here funds the premium XI picks.
- **Emersonn** (FWD, £5.5m): 3.60 xPts. Budget saved here funds the premium XI picks.
- **Trafford** (GKP, £5.0m): 4.05 xPts. Budget saved here funds the premium XI picks.
- **Wissa** (FWD, £6.2m): 3.18 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** White (£5.5m, 4.91 xPts GW4)
**IN:** Gvardiol (£5.6m, 5.14 xPts GW4)
**Net this week:** +0.24 xPts

Gvardiol projects 5.14 xPts vs White's 4.91 — a 0.24 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Palmer (£9.6m, 5.10 xPts GW4)
**IN:** Ødegaard (£6.7m, 5.31 xPts GW4)
**Net this week:** +0.21 xPts

Ødegaard projects 5.31 xPts vs Palmer's 5.10 — a 0.21 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

---

## Players We Considered But Didn't Pick

### GW3 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Mitchell** (DEF, Crystal Palace): **15 pts in GW3** (2 goals). Not transferred in — Forward projection: 3.26 xPts for GW4 (ranked #78 overall). This is below our lowest-ranked DEF in the squad (4.93 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Bogle** (DEF, Leeds): **14 pts in GW3** (1 goal, clean sheet). Not transferred in — Forward projection 5.00 xPts (ranked #18 overall) is competitive, but bringing them in at £4.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Isak** (FWD, Liverpool): **13 pts in GW3** (2 goals, clean sheet). Not transferred in — Forward projection 5.48 xPts (ranked #8 overall) is competitive, but bringing them in at £9.1m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Barnes** (MID, Newcastle): **12 pts in GW3** (1 goal, 1 assist). Not transferred in — Forward projection: 4.11 xPts for GW4 (ranked #39 overall). This is below our lowest-ranked MID in the squad (5.31 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Vuskovic** (DEF, Brighton): **12 pts in GW3** (1 goal). Not transferred in — Forward projection: 4.88 xPts for GW4 (ranked #21 overall). This is below our lowest-ranked DEF in the squad (4.93 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Janelt** (MID, Brentford): **11 pts in GW3** (1 goal). Not transferred in — Forward projection: 4.75 xPts for GW4 (ranked #27 overall). This is below our lowest-ranked MID in the squad (5.31 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Gakpo** (MID, Liverpool): **11 pts in GW3** (2 assists, clean sheet). Not transferred in — Forward projection: 2.52 xPts for GW4 (ranked #109 overall). This is below our lowest-ranked MID in the squad (5.31 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Tavernier** (MID, Bournemouth): **10 pts in GW3** (1 goal). Not transferred in — Forward projection: 5.25 xPts for GW4 (ranked #12 overall). This is below our lowest-ranked MID in the squad (5.31 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Scott** (MID, Bournemouth): **10 pts in GW3** (2 assists). Not transferred in — Forward projection: 5.13 xPts for GW4 (ranked #14 overall). This is below our lowest-ranked MID in the squad (5.31 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **George** (MID, Everton): **10 pts in GW3** (1 goal). Not transferred in — Forward projection: 4.00 xPts for GW4 (ranked #42 overall). This is below our lowest-ranked MID in the squad (5.31 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **King** (MID, Fulham): **10 pts in GW3** (1 goal). Not transferred in — Forward projection: 4.40 xPts for GW4 (ranked #32 overall). This is below our lowest-ranked MID in the squad (5.31 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.

### Highest-Projected Players Not In Your Squad

- **Haaland** (FWD, Man City, £15.5m, 5.62 xPts): ranked #6 overall. £15.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Egan** (DEF, Hull City, £4.1m, 5.56 xPts): ranked #7 overall. 3-player Hull City cap is maxed.
- **Isak** (FWD, Liverpool, £9.1m, 5.48 xPts): ranked #8 overall. £9.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Tavernier** (MID, Bournemouth, £6.0m, 5.25 xPts): ranked #12 overall. Edged out by selected MIDs with better 6-GW projections.
- **Scott** (MID, Bournemouth, £6.1m, 5.13 xPts): ranked #14 overall. Edged out by selected MIDs with better 6-GW projections.
- **Palmer** (MID, Chelsea, £9.7m, 5.10 xPts): ranked #15 overall. Edged out by selected MIDs with better 6-GW projections.
- **De Cuyper** (DEF, Brighton, £4.8m, 5.10 xPts): ranked #16 overall. £4.8m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Bogle** (DEF, Leeds, £4.5m, 5.00 xPts): ranked #18 overall. £4.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## Chip Schedule

| GW | Chip | Est. Gain |
|---:|---|---:|
| 4 | Bench Boost | +15.8 pts |
| 9 | Triple Captain | +7.5 pts |

*Play Bench Boost this GW (est. +15.8 pts; needs 11.0). 16 GW(s) remain before this chip set expires with 2 chip(s) unused; expiry only softens thresholds and never forces a chip.*

---

## GW-by-GW Plan

|   GW | Transfers                           | Chip           | Captain     | Vice        |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:------------------------------------|:---------------|:------------|:------------|----------:|-------------:|-------:|------:|:-------|
|    4 | Gvardiol ← White, Ødegaard ← Palmer | Bench Boost    | B.Fernandes | Mendy       |     71.14 |        15.76 |      0 |     1 | £4.2m  |
|    5 | King ← M.Sangaré                    | —              | Cherki      | B.Fernandes |     73.47 |        15.15 |      0 |     1 | £4.4m  |
|    6 | Roll                                | —              | B.Fernandes | Ødegaard    |     73.29 |        15.04 |      0 |     2 | £4.4m  |
|    7 | Scott ← Saka, Haaland ← João Pedro  | —              | Cherki      | B.Fernandes |     76.97 |        13.04 |      0 |     1 | £0.0m  |
|    8 | Roll                                | —              | B.Fernandes | Ajayi       |     69.92 |        13.84 |      0 |     2 | £0.0m  |
|    9 | Roll                                | Triple Captain | Cherki      | Haaland     |     76.34 |        15.13 |      0 |     3 | £0.0m  |

---

## Starting XI — GW4

**GKP:** **Tzolakis**  
**DEF:** **Calafiori** | **Ajayi** | **Gvardiol** | **Mendy**  
**MID:** **Saka** | **Ødegaard** | **Cherki** | **B.Fernandes**(C) | **M.Sangaré**  
**FWD:** **João Pedro**  

**Bench:** Tarkowski | Emersonn | Trafford | Wissa