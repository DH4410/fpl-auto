# FPL Season Plan — GW9

*Generated 2026-10-30 01:32 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW9

**Captain:** Haaland (6.45 xPts → 12.90 effective with double)  
**Vice:** Gabriel (6.29 xPts)  
**Transfer:** OUT Calafiori (£5.8m) → IN Gabriel (£7.9m)  
**Transfer:** OUT Tarkowski (£6.1m) → IN Konsa (£4.7m)  
**Transfer:** OUT João Pedro (£7.7m) → IN Evanilson (£6.3m)  
**Hits:** 2 (−8 pts)  
**Bank after:** £6.0m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Trafford** (GKP, £5.0m): 4.19 xPts for GW9, ranked #4/17 among GKPs in candidate pool. Leeds fixture. P(start) 90%.
- **De Cuyper** (DEF, £5.3m): 5.18 xPts for GW9, ranked #4/50 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Gabriel** (DEF, £7.9m) [**Vice**]: 6.29 xPts for GW9, ranked #1/50 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Konsa** (DEF, £4.7m): 6.21 xPts for GW9, ranked #2/50 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Vuskovic** (DEF, £5.2m): 4.38 xPts for GW9, ranked #11/50 among DEFs in candidate pool. Brighton fixture. P(start) 90%.
- **Belloumi** (MID, £5.2m): 3.60 xPts for GW9, ranked #17/67 among MIDs in candidate pool. Hull City fixture. P(start) 90%.
- **Groß** (MID, £6.2m): 3.54 xPts for GW9, ranked #19/67 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Henderson** (MID, £4.9m): 5.28 xPts for GW9, ranked #2/67 among MIDs in candidate pool. Chelsea fixture. P(start) 90%.
- **Manzambi** (MID, £6.1m): 3.85 xPts for GW9, ranked #11/67 among MIDs in candidate pool. Aston Villa fixture. P(start) 90%.
- **Evanilson** (FWD, £6.3m): 5.43 xPts for GW9, ranked #2/16 among FWDs in candidate pool. Bournemouth fixture. P(start) 90%.
- **Haaland** (FWD, £15.8m) [**CAPTAIN**]: Highest projected return in the squad: 6.45 xPts (12.90 effective with captain double). Ranked #1/16 among FWDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Emersonn** (FWD, £5.5m): 1.53 xPts. Budget saved here funds the premium XI picks.
- **Bogle** (DEF, £4.8m): 2.42 xPts. Budget saved here funds the premium XI picks.
- **Cherki** (MID, £8.0m): 3.22 xPts. Budget saved here funds the premium XI picks.
- **Tzolakis** (GKP, £4.7m): 2.43 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Calafiori (£5.8m, 1.66 xPts GW9)
**IN:** Gabriel (£7.9m, 6.29 xPts GW9)
**Net this week:** +4.63 xPts

Gabriel projects 6.29 xPts vs Calafiori's 1.66 — a 4.63 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Tarkowski (£6.1m, 1.94 xPts GW9)
**IN:** Konsa (£4.7m, 6.21 xPts GW9)
**Net this week:** +4.27 xPts

Konsa projects 6.21 xPts vs Tarkowski's 1.94 — a 4.27 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** João Pedro (£7.7m, 0.00 xPts GW9)
**IN:** Evanilson (£6.3m, 5.43 xPts GW9)
**Net this week:** +5.43 xPts

Evanilson projects 5.43 xPts vs João Pedro's 0.00 — a 5.43 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

⚠️ **2 hit(s) required (−8 pts).** The planner only recommends hits when the projected 6-GW gain exceeds the penalty. Skip and roll if you want to avoid the risk — the bot will adapt next week.

---

## Players We Considered But Didn't Pick

### GW8 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **N.Jackson** (FWD, Aston Villa): **13 pts in GW8** (2 goals). Not transferred in — Forward projection 5.10 xPts (ranked #11 overall) is competitive, but bringing them in at £6.8m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Hill** (DEF, Bournemouth): **11 pts in GW8** (clean sheet, DEFCON bonus (15 CBIT)). Not transferred in — Forward projection 5.12 xPts (ranked #10 overall) is competitive, but bringing them in at £5.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Bobb** (MID, Fulham): **11 pts in GW8** (1 goal, clean sheet). Not transferred in — Forward projection 4.14 xPts (ranked #30 overall) is competitive, but bringing them in at £5.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Tel** (MID, Spurs): **11 pts in GW8** (1 goal, clean sheet). Not transferred in — Forward projection 3.41 xPts (ranked #56 overall) is competitive, but bringing them in at £5.6m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Dedić** (DEF, Newcastle): **11 pts in GW8** (1 assist, clean sheet). Not transferred in — Forward projection 3.33 xPts (ranked #63 overall) is competitive, but bringing them in at £4.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **White** (DEF, Arsenal): **10 pts in GW8** (1 assist, clean sheet). Not transferred in — Forward projection 5.72 xPts (ranked #4 overall) is competitive, but bringing them in at £5.4m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Truffert** (DEF, Bournemouth): **10 pts in GW8** (1 assist, clean sheet). Not transferred in — Forward projection 3.94 xPts (ranked #37 overall) is competitive, but bringing them in at £5.4m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Rayan** (MID, Bournemouth): **10 pts in GW8** (1 goal, clean sheet). Not transferred in — Forward projection 3.71 xPts (ranked #45 overall) is competitive, but bringing them in at £6.3m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Tavernier** (MID, Bournemouth): **10 pts in GW8** (1 goal, clean sheet). Not transferred in — Forward projection 5.48 xPts (ranked #5 overall) is competitive, but bringing them in at £6.4m would require dropping a player the MILP values more over the full 6-GW horizon.
- **O.Dango** (MID, Brentford): **10 pts in GW8** (1 goal). Not transferred in — Their 6-GW projected return sits outside the top-150 candidate pool. The EWMA model hasn't built enough forward-looking form from this performance to move them into contention yet.

### Highest-Projected Players Not In Your Squad

- **White** (DEF, Arsenal, £5.4m, 5.72 xPts): ranked #4 overall. £5.4m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Tavernier** (MID, Bournemouth, £6.4m, 5.48 xPts): ranked #5 overall. £6.4m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Raya** (GKP, Arsenal, £6.2m, 5.18 xPts): ranked #8 overall. £6.2m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Hill** (DEF, Bournemouth, £5.5m, 5.12 xPts): ranked #10 overall. £5.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **N.Jackson** (FWD, Aston Villa, £6.8m, 5.10 xPts): ranked #11 overall. £6.8m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Doku** (MID, Man City, £7.5m, 5.01 xPts): ranked #12 overall. £7.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Rudoni** (MID, Coventry City, £4.9m, 4.93 xPts): ranked #13 overall. £4.9m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Silva** (DEF, Bournemouth, £5.0m, 4.77 xPts): ranked #15 overall. £5.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.

---

## GW-by-GW Plan

|   GW | Transfers                                                      | Chip   | Captain   | Vice      |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:---------------------------------------------------------------|:-------|:----------|:----------|----------:|-------------:|-------:|------:|:-------|
|    9 | Gabriel ← Calafiori, Konsa ← Tarkowski, Evanilson ← João Pedro | —      | Haaland   | Gabriel   |     60.84 |         9.59 |      2 |     1 | £6.0m  |
|   10 | Tavernier ← Belloumi                                           | —      | Haaland   | Gabriel   |     71.44 |        13.08 |      0 |     1 | £4.7m  |
|   11 | Raya ← Tzolakis                                                | —      | Haaland   | De Cuyper |     73.54 |        13.67 |      0 |     1 | £3.1m  |
|   12 | Roll                                                           | —      | Henderson | De Cuyper |     64.11 |        14.7  |      0 |     2 | £3.1m  |
|   13 | Roll                                                           | —      | Henderson | Haaland   |     69.39 |        12.89 |      0 |     3 | £3.1m  |
|   14 | Roll                                                           | —      | Haaland   | De Cuyper |     69.65 |        16.2  |      0 |     4 | £3.1m  |

---

## Starting XI — GW9

**GKP:** **Trafford**  
**DEF:** **Gabriel** | **Konsa** | **De Cuyper** | **Vuskovic**  
**MID:** **Manzambi** | **Henderson** | **Groß** | **Belloumi**  
**FWD:** **Evanilson** | **Haaland**(C)  

**Bench:** Emersonn | Bogle | Cherki | Tzolakis