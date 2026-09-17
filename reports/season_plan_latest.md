# FPL Season Plan — GW5

*Generated 2026-09-17 09:14 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW5

**Captain:** B.Fernandes (5.74 xPts → 11.48 effective with double)  
**Vice:** Bogle (6.68 xPts)  
**Transfer:** OUT Mendy (£4.0m) → IN Bogle (£4.5m)  
**Bank after:** £3.7m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Tzolakis** (GKP, £4.6m): 6.30 xPts for GW5, ranked #1/16 among GKPs in candidate pool. Hull City fixture. P(start) 90%.
- **Bogle** (DEF, £4.5m) [**Vice**]: 6.68 xPts for GW5, ranked #1/45 among DEFs in candidate pool. Leeds fixture. P(start) 90%.
- **Calafiori** (DEF, £5.8m): 5.38 xPts for GW5, ranked #4/45 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Gvardiol** (DEF, £5.7m): 5.92 xPts for GW5, ranked #2/45 among DEFs in candidate pool. Man City fixture. P(start) 90%.
- **Tarkowski** (DEF, £6.1m): 5.19 xPts for GW5, ranked #5/45 among DEFs in candidate pool. Everton fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 5.74 xPts (11.48 effective with captain double). Ranked #3/74 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Cherki** (MID, £7.8m): 4.93 xPts for GW5, ranked #9/74 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **M.Sangaré** (MID, £5.7m): 4.95 xPts for GW5, ranked #8/74 among MIDs in candidate pool. Brentford fixture. P(start) 90%.
- **Saka** (MID, £9.5m): 5.46 xPts for GW5, ranked #4/74 among MIDs in candidate pool. Arsenal fixture. P(start) 90%.
- **Ødegaard** (MID, £6.7m): 4.81 xPts for GW5, ranked #11/74 among MIDs in candidate pool. Arsenal fixture. P(start) 90%.
- **Emersonn** (FWD, £5.5m): 5.40 xPts for GW5, ranked #2/15 among FWDs in candidate pool. Ipswich Town fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **João Pedro** (FWD, £7.8m): 1.99 xPts. Budget saved here funds the premium XI picks.
- **Ajayi** (DEF, £4.2m): 4.63 xPts. Budget saved here funds the premium XI picks.
- **Trafford** (GKP, £5.0m): 4.23 xPts. Budget saved here funds the premium XI picks.
- **Wissa** (FWD, £6.2m): 2.98 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Mendy (£4.0m, 1.02 xPts GW5)
**IN:** Bogle (£4.5m, 6.68 xPts GW5)
**Net this week:** +5.66 xPts

Bogle projects 6.68 xPts vs Mendy's 1.02 — a 5.66 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

---

## Players We Considered But Didn't Pick

### GW4 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Groß** (MID, Brighton): **17 pts in GW4** (1 goal, 2 assists, clean sheet). Not transferred in — Forward projection 6.29 xPts (ranked #3 overall) is competitive, but bringing them in at £5.7m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Schade** (MID, Brentford): **15 pts in GW4** (2 goals). Not transferred in — Forward projection 5.32 xPts (ranked #13 overall) is competitive, but bringing them in at £6.1m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Raya** (GKP, Arsenal): **14 pts in GW4** (clean sheet). Not transferred in — The 3-player Arsenal cap is already maxed in the squad. Bringing them in would mean dropping another Arsenal player, which the MILP found to be a worse 6-GW outcome.
- **Davis** (DEF, Ipswich Town): **14 pts in GW4** (1 goal, 2 assists). Not transferred in — Forward projection: 3.96 xPts for GW5 (ranked #47 overall). This is below our lowest-ranked DEF in the squad (4.63 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Belloumi** (MID, Hull City): **13 pts in GW4** (2 goals). Not transferred in — Forward projection 6.12 xPts (ranked #4 overall) is competitive, but bringing them in at £5.1m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Dunk** (DEF, Brighton): **12 pts in GW4** (1 goal, clean sheet). Not transferred in — Forward projection: 4.42 xPts for GW5 (ranked #30 overall). This is below our lowest-ranked DEF in the squad (4.63 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **De Cuyper** (DEF, Brighton): **11 pts in GW4** (1 assist, clean sheet). Not transferred in — Forward projection 5.86 xPts (ranked #6 overall) is competitive, but bringing them in at £4.9m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Mykolenko** (DEF, Everton): **11 pts in GW4** (clean sheet). Not transferred in — Forward projection: 4.44 xPts for GW5 (ranked #29 overall). This is below our lowest-ranked DEF in the squad (4.63 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.

### Highest-Projected Players Not In Your Squad

- **Groß** (MID, Brighton, £5.7m, 6.29 xPts): ranked #3 overall. £5.7m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Belloumi** (MID, Hull City, £5.1m, 6.12 xPts): ranked #4 overall. £5.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **De Cuyper** (DEF, Brighton, £4.9m, 5.86 xPts): ranked #6 overall. £4.9m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Haaland** (FWD, Man City, £15.5m, 5.61 xPts): ranked #8 overall. £15.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Raya** (GKP, Arsenal, £6.0m, 5.53 xPts): ranked #9 overall. 3-player Arsenal cap is maxed.
- **Schade** (MID, Brentford, £6.1m, 5.32 xPts): ranked #13 overall. £6.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Gibbs-White** (MID, Nott'm Forest, £8.0m, 5.14 xPts): ranked #15 overall. £8.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Tavernier** (MID, Bournemouth, £6.1m, 5.12 xPts): ranked #16 overall. £6.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers            | Chip   | Captain     | Vice      |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:---------------------|:-------|:------------|:----------|----------:|-------------:|-------:|------:|:-------|
|    5 | Bogle ← Mendy        | —      | B.Fernandes | Bogle     |     66.5  |        13.83 |      0 |     1 | £3.7m  |
|    6 | Groß ← M.Sangaré     | —      | Groß        | Saka      |     67.17 |        14.17 |      0 |     1 | £3.7m  |
|    7 | Tavernier ← Ødegaard | —      | Groß        | Tavernier |     68.06 |        13.35 |      0 |     1 | £4.3m  |
|    8 | Schade ← Cherki      | —      | Schade      | Bogle     |     66.19 |        13.98 |      0 |     1 | £5.8m  |
|    9 | Roll                 | —      | Tavernier   | Bogle     |     68.24 |        14.27 |      0 |     2 | £5.8m  |
|   10 | Roll                 | —      | Groß        | Tavernier |     69.97 |        13.85 |      0 |     3 | £5.8m  |

---

## Starting XI — GW5

**GKP:** **Tzolakis**  
**DEF:** **Calafiori** | **Tarkowski** | **Bogle** | **Gvardiol**  
**MID:** **Saka** | **Ødegaard** | **Cherki** | **B.Fernandes**(C) | **M.Sangaré**  
**FWD:** **Emersonn**  

**Bench:** João Pedro | Ajayi | Trafford | Wissa