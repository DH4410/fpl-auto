# FPL Season Plan — GW3

*Generated 2026-09-03 08:32 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW3

**Chip:** Wildcard  
**Captain:** B.Fernandes (11.96 xPts → 23.92 effective with double)  
**Vice:** Cherki (10.32 xPts)  
**Transfer:** OUT Gabriel (£8.0m) → IN Calafiori (£5.6m)  
**Transfer:** OUT Martinez (£5.0m) → IN White (£5.5m)  
**Transfer:** OUT Lewis-Potter (£5.5m) → IN Saka (£9.5m)  
**Transfer:** OUT Thiago (£8.0m) → IN Palmer (£9.6m)  
**Transfer:** OUT James (£5.5m) → IN João Pedro (£7.7m)  
**Transfer:** OUT Lacroix (£6.0m) → IN Egan (£4.0m)  
**Transfer:** OUT Szoboszlai (£7.0m) → IN Ajayi (£4.1m)  
**Transfer:** OUT O'Reilly (£6.5m) → IN Tzolakis (£4.6m)  
**Hits:** 6 (−24 pts)  
**Bank after:** £0.9m  
**FT next GW:** 1  

---

## Why the Bot Chose This Squad

### Starting XI

- **Tzolakis** (GKP, £4.6m): 9.00 xPts for GW3, ranked #1/16 among GKPs in candidate pool. Hull City fixture. P(start) 90%.
- **Ajayi** (DEF, £4.1m): 9.00 xPts for GW3, ranked #1/42 among DEFs in candidate pool. Hull City fixture. P(start) 90%.
- **Calafiori** (DEF, £5.6m): 9.00 xPts for GW3, ranked #2/42 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Egan** (DEF, £4.0m): 7.65 xPts for GW3, ranked #5/42 among DEFs in candidate pool. Hull City fixture. P(start) 90%.
- **White** (DEF, £5.5m): 8.10 xPts for GW3, ranked #4/42 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**CAPTAIN**]: Highest projected return in the squad: 11.96 xPts (23.92 effective with captain double). Ranked #1/74 among MIDs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Cherki** (MID, £7.7m) [**Vice**]: 10.32 xPts for GW3, ranked #2/74 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **Groß** (MID, £5.5m): 7.46 xPts for GW3, ranked #9/74 among MIDs in candidate pool. Brighton fixture. P(start) 90%.
- **Palmer** (MID, £9.6m): 9.29 xPts for GW3, ranked #3/74 among MIDs in candidate pool. Chelsea fixture. P(start) 90%.
- **Saka** (MID, £9.5m): 9.29 xPts for GW3, ranked #4/74 among MIDs in candidate pool. Arsenal fixture. P(start) 90%.
- **João Pedro** (FWD, £7.7m): 9.00 xPts for GW3, ranked #1/18 among FWDs in candidate pool. Chelsea fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **Pickford** (GKP, £5.5m): 4.50 xPts. Budget saved here funds the premium XI picks.
- **Calvert-Lewin** (FWD, £6.0m): 4.28 xPts. Budget saved here funds the premium XI picks.
- **Guéhi** (DEF, £6.0m): 5.40 xPts. Budget saved here funds the premium XI picks.
- **Woltemade** (FWD, £5.9m): 0.00 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Gabriel (£8.0m, 5.85 xPts GW3)
**IN:** Calafiori (£5.6m, 9.00 xPts GW3)
**Net this week:** +3.15 xPts

Calafiori projects 9.00 xPts vs Gabriel's 5.85 — a 3.15 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Martinez (£5.0m, 0.90 xPts GW3)
**IN:** White (£5.5m, 8.10 xPts GW3)
**Net this week:** +7.20 xPts

White projects 8.10 xPts vs Martinez's 0.90 — a 7.20 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Lewis-Potter (£5.5m, 7.34 xPts GW3)
**IN:** Saka (£9.5m, 9.29 xPts GW3)
**Net this week:** +1.94 xPts

Saka projects 9.29 xPts vs Lewis-Potter's 7.34 — a 1.94 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Thiago (£8.0m, 1.13 xPts GW3)
**IN:** Palmer (£9.6m, 9.29 xPts GW3)
**Net this week:** +8.16 xPts

Palmer projects 9.29 xPts vs Thiago's 1.13 — a 8.16 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** James (£5.5m, 0.90 xPts GW3)
**IN:** João Pedro (£7.7m, 9.00 xPts GW3)
**Net this week:** +8.10 xPts

João Pedro projects 9.00 xPts vs James's 0.90 — a 8.10 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Lacroix (£6.0m, 3.15 xPts GW3)
**IN:** Egan (£4.0m, 7.65 xPts GW3)
**Net this week:** +4.50 xPts

Egan projects 7.65 xPts vs Lacroix's 3.15 — a 4.50 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Szoboszlai (£7.0m, 6.11 xPts GW3)
**IN:** Ajayi (£4.1m, 9.00 xPts GW3)
**Net this week:** +2.89 xPts

Ajayi projects 9.00 xPts vs Szoboszlai's 6.11 — a 2.89 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** O'Reilly (£6.5m, 1.80 xPts GW3)
**IN:** Tzolakis (£4.6m, 9.00 xPts GW3)
**Net this week:** +7.20 xPts

Tzolakis projects 9.00 xPts vs O'Reilly's 1.80 — a 7.20 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

⚠️ **6 hit(s) required (−24 pts).** The planner only recommends hits when the projected 6-GW gain exceeds the penalty. Skip and roll if you want to avoid the risk — the bot will adapt next week.

---

## Players We Considered But Didn't Pick

### GW2 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Haaland** (FWD, Man City): **13 pts in GW2** (2 goals). Not transferred in — Forward projection 6.98 xPts (ranked #21 overall) is competitive, but bringing them in at £15.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Gibbs-White** (MID, Nott'm Forest): **13 pts in GW2** (1 goal, 1 assist). Not transferred in — Forward projection: 7.18 xPts for GW3 (ranked #20 overall). This is below our lowest-ranked MID in the squad (7.46 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Scott** (MID, Bournemouth): **12 pts in GW2** (1 goal, clean sheet). Not transferred in — Forward projection: 6.30 xPts for GW3 (ranked #27 overall). This is below our lowest-ranked MID in the squad (7.46 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Tarkowski** (DEF, Everton): **12 pts in GW2** (1 goal). Not transferred in — Forward projection 8.10 xPts (ranked #9 overall) is competitive, but bringing them in at £6.0m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Mbeumo** (MID, Man Utd): **11 pts in GW2** (1 goal, 1 assist). Not transferred in — Forward projection: 5.85 xPts for GW3 (ranked #30 overall). This is below our lowest-ranked MID in the squad (7.46 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Hall** (DEF, Newcastle): **11 pts in GW2** (clean sheet, DEFCON bonus (11 CBIT)). Not transferred in — Forward projection 6.75 xPts (ranked #23 overall) is competitive, but bringing them in at £5.1m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Dedić** (DEF, Newcastle): **11 pts in GW2** (1 assist, clean sheet). Not transferred in — Forward projection 5.40 xPts (ranked #41 overall) is competitive, but bringing them in at £4.5m would require dropping a player the MILP values more over the full 6-GW horizon.

### Highest-Projected Players Not In Your Squad

- **M.Sangaré** (MID, Brentford, £5.7m, 8.10 xPts): ranked #10 overall. £5.7m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Tarkowski** (DEF, Everton, £6.0m, 8.10 xPts): ranked #9 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Stach** (MID, Leeds, £6.0m, 8.07 xPts): ranked #12 overall. £6.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **De Cuyper** (DEF, Brighton, £4.7m, 7.65 xPts): ranked #15 overall. £4.7m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Gakpo** (MID, Liverpool, £7.0m, 7.65 xPts): ranked #14 overall. £7.0m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Elanga** (MID, Newcastle, £6.1m, 7.65 xPts): ranked #13 overall. £6.1m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Lewis-Potter** (MID, Brentford, £5.5m, 7.34 xPts): ranked #18 overall. Edged out by selected MIDs with better 6-GW projections.
- **Mendy** (DEF, Hull City, £4.0m, 7.20 xPts): ranked #19 overall. 3-player Hull City cap is maxed.

---

## Chip Schedule

| GW | Chip | Est. Gain |
|---:|---|---:|
| 3 | Wildcard | +35.5 pts |
| 4 | Triple Captain | +9.6 pts |
| 5 | Bench Boost | +14.1 pts |

*Play Wildcard this GW (est. +35.5 pts; needs 4.0). Preferred sequence: Wildcard GW3 -> Bench Boost GW5. 17 GW(s) remain before this chip set expires with 4 chip(s) unused; expiry only softens thresholds and never forces a chip.*

---

## GW-by-GW Plan

|   GW | Transfers                                                                                                                                                | Chip           | Captain     | Vice       |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:---------------------------------------------------------------------------------------------------------------------------------------------------------|:---------------|:------------|:-----------|----------:|-------------:|-------:|------:|:-------|
|    3 | Calafiori ← Gabriel, White ← Martinez, Saka ← Lewis-Potter, Palmer ← Thiago, João Pedro ← James, Egan ← Lacroix, Ajayi ← Szoboszlai, Tzolakis ← O'Reilly | Wildcard       | B.Fernandes | Cherki     |    112.04 |        14.18 |      6 |     1 | £0.9m  |
|    4 | Tarkowski ← Guéhi                                                                                                                                        | Triple Captain | Palmer      | João Pedro |     85.74 |        13.16 |      0 |     1 | £0.9m  |
|    5 | Elanga ← Groß                                                                                                                                            | Bench Boost    | B.Fernandes | Palmer     |     88.45 |        14.07 |      0 |     1 | £0.3m  |
|    6 | Roll                                                                                                                                                     | —              | B.Fernandes | Saka       |     89.68 |        13.01 |      0 |     2 | £0.3m  |
|    7 | Roll                                                                                                                                                     | —              | B.Fernandes | Palmer     |     85.78 |        12.21 |      0 |     3 | £0.3m  |
|    8 | Roll                                                                                                                                                     | —              | B.Fernandes | Palmer     |     83.92 |        11.16 |      0 |     4 | £0.3m  |

---

## Starting XI — GW3

**GKP:** **Tzolakis**  
**DEF:** **Calafiori** | **White** | **Egan** | **Ajayi**  
**MID:** **Saka** | **Groß** | **Palmer** | **Cherki** | **B.Fernandes**(C)  
**FWD:** **João Pedro**  

**Bench:** Pickford | Calvert-Lewin | Guéhi | Woltemade