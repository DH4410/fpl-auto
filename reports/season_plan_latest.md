# FPL Season Plan — GW3

*Generated 2026-09-03 22:16 UTC — advisory only, no transfers executed*

---

## How the Bot Works

All predictions run locally — no external AI APIs are called. GitHub Actions fetches fresh FPL data every run, re-scores all players, and solves the MILP. Nothing is hardcoded between gameweeks.

**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted points-per-game — raw current-season PPG regressed toward a position / `ep_next` prior so a one- or two-game sample can't inflate the projection — then scaled by FDR. The result is a 6-GW forward projection per player, which the MILP uses to find the optimal squad and transfer.

**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 onward. Players with consistently high defensive activity score higher on the DC model sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also includes the DEFCON bonus in its expected-points calculation (60% weight here), so it's partially accounted for. The bot does not predict threshold-crossing probability explicitly — that would require match-level simulation.

---

## Immediate Action — GW3

**Chip:** Wildcard  
**Captain:** Tzolakis (9.00 xPts → 18.00 effective with double)  
**Vice:** B.Fernandes (8.92 xPts)  
**Transfer:** OUT Martinez (£5.0m) → IN Trafford (£5.0m)  
**Transfer:** OUT Pickford (£5.5m) → IN Tzolakis (£4.6m)  
**Transfer:** OUT Gabriel (£8.0m) → IN Calafiori (£5.6m)  
**Transfer:** OUT James (£5.5m) → IN White (£5.5m)  
**Transfer:** OUT Lacroix (£6.0m) → IN Tarkowski (£6.0m)  
**Transfer:** OUT O'Reilly (£6.5m) → IN Ajayi (£4.1m)  
**Transfer:** OUT Guéhi (£6.0m) → IN Mendy (£4.0m)  
**Transfer:** OUT Lewis-Potter (£5.5m) → IN Saka (£9.5m)  
**Transfer:** OUT Groß (£5.5m) → IN Palmer (£9.6m)  
**Transfer:** OUT Szoboszlai (£7.0m) → IN M.Sangaré (£5.7m)  
**Transfer:** OUT Thiago (£8.0m) → IN João Pedro (£7.7m)  
**Transfer:** OUT Calvert-Lewin (£6.0m) → IN Emersonn (£5.5m)  
**Transfer:** OUT Woltemade (£5.9m) → IN Wissa (£6.1m)  
**Bank after:** £1.5m  
**FT next GW:** 2  

---

## Why the Bot Chose This Squad

### Starting XI

- **Tzolakis** (GKP, £4.6m) [**CAPTAIN**]: Highest projected return in the squad: 9.00 xPts (18.00 effective with captain double). Ranked #1/16 among GKPs in the 150-player candidate pool. Captain is always the highest-xPts player in the XI.
- **Ajayi** (DEF, £4.1m): 7.12 xPts for GW3, ranked #2/42 among DEFs in candidate pool. Hull City fixture. P(start) 90%.
- **Calafiori** (DEF, £5.6m): 6.48 xPts for GW3, ranked #3/42 among DEFs in candidate pool. Arsenal fixture. P(start) 90%.
- **Mendy** (DEF, £4.0m): 7.20 xPts for GW3, ranked #1/42 among DEFs in candidate pool. Hull City fixture. P(start) 90%.
- **Tarkowski** (DEF, £6.0m): 5.98 xPts for GW3, ranked #4/42 among DEFs in candidate pool. Everton fixture. P(start) 90%.
- **B.Fernandes** (MID, £12.0m) [**Vice**]: 8.92 xPts for GW3, ranked #1/72 among MIDs in candidate pool. Man Utd fixture. P(start) 90%.
- **Cherki** (MID, £7.7m): 7.84 xPts for GW3, ranked #3/72 among MIDs in candidate pool. Man City fixture. P(start) 90%.
- **M.Sangaré** (MID, £5.7m): 8.10 xPts for GW3, ranked #2/72 among MIDs in candidate pool. Brentford fixture. P(start) 90%.
- **Palmer** (MID, £9.6m): 6.65 xPts for GW3, ranked #5/72 among MIDs in candidate pool. Chelsea fixture. P(start) 90%.
- **Saka** (MID, £9.5m): 7.57 xPts for GW3, ranked #4/72 among MIDs in candidate pool. Arsenal fixture. P(start) 90%.
- **João Pedro** (FWD, £7.7m): 6.41 xPts for GW3, ranked #1/20 among FWDs in candidate pool. Chelsea fixture. P(start) 90%.

### Bench

*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally spends budget on the starting XI and uses bench slots for legal squad shape.*

- **White** (DEF, £5.5m): 5.87 xPts. Budget saved here funds the premium XI picks.
- **Emersonn** (FWD, £5.5m): 4.50 xPts. Budget saved here funds the premium XI picks.
- **Trafford** (GKP, £5.0m): 4.67 xPts. Budget saved here funds the premium XI picks.
- **Wissa** (FWD, £6.1m): 4.08 xPts. Budget saved here funds the premium XI picks.

---

## Transfer Decision

**OUT:** Martinez (£5.0m, 1.36 xPts GW3)
**IN:** Trafford (£5.0m, 4.67 xPts GW3)
**Net this week:** +3.30 xPts

Trafford projects 4.67 xPts vs Martinez's 1.36 — a 3.30 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Pickford (£5.5m, 4.16 xPts GW3)
**IN:** Tzolakis (£4.6m, 9.00 xPts GW3)
**Net this week:** +4.84 xPts

Tzolakis projects 9.00 xPts vs Pickford's 4.16 — a 4.84 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Gabriel (£8.0m, 4.28 xPts GW3)
**IN:** Calafiori (£5.6m, 6.48 xPts GW3)
**Net this week:** +2.20 xPts

Calafiori projects 6.48 xPts vs Gabriel's 4.28 — a 2.20 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** James (£5.5m, 1.31 xPts GW3)
**IN:** White (£5.5m, 5.87 xPts GW3)
**Net this week:** +4.56 xPts

White projects 5.87 xPts vs James's 1.31 — a 4.56 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Lacroix (£6.0m, 2.49 xPts GW3)
**IN:** Tarkowski (£6.0m, 5.98 xPts GW3)
**Net this week:** +3.49 xPts

Tarkowski projects 5.98 xPts vs Lacroix's 2.49 — a 3.49 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** O'Reilly (£6.5m, 3.00 xPts GW3)
**IN:** Ajayi (£4.1m, 7.12 xPts GW3)
**Net this week:** +4.12 xPts

Ajayi projects 7.12 xPts vs O'Reilly's 3.00 — a 4.12 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Guéhi (£6.0m, 4.48 xPts GW3)
**IN:** Mendy (£4.0m, 7.20 xPts GW3)
**Net this week:** +2.72 xPts

Mendy projects 7.20 xPts vs Guéhi's 4.48 — a 2.72 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Lewis-Potter (£5.5m, 5.30 xPts GW3)
**IN:** Saka (£9.5m, 7.57 xPts GW3)
**Net this week:** +2.27 xPts

Saka projects 7.57 xPts vs Lewis-Potter's 5.30 — a 2.27 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Groß (£5.5m, 6.09 xPts GW3)
**IN:** Palmer (£9.6m, 6.65 xPts GW3)
**Net this week:** +0.56 xPts

Palmer projects 6.65 xPts vs Groß's 6.09 — a 0.56 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Szoboszlai (£7.0m, 5.02 xPts GW3)
**IN:** M.Sangaré (£5.7m, 8.10 xPts GW3)
**Net this week:** +3.08 xPts

M.Sangaré projects 8.10 xPts vs Szoboszlai's 5.02 — a 3.08 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Thiago (£8.0m, 1.94 xPts GW3)
**IN:** João Pedro (£7.7m, 6.41 xPts GW3)
**Net this week:** +4.47 xPts

João Pedro projects 6.41 xPts vs Thiago's 1.94 — a 4.47 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Calvert-Lewin (£6.0m, 3.45 xPts GW3)
**IN:** Emersonn (£5.5m, 4.50 xPts GW3)
**Net this week:** +1.05 xPts

Emersonn projects 4.50 xPts vs Calvert-Lewin's 3.45 — a 1.05 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.
**OUT:** Woltemade (£5.9m, 0.00 xPts GW3)
**IN:** Wissa (£6.1m, 4.08 xPts GW3)
**Net this week:** +4.08 xPts

Wissa projects 4.08 xPts vs Woltemade's 0.00 — a 4.08 xPts improvement this GW alone. The MILP confirmed this swap also improves the full 6-GW plan after accounting for future fixtures and the value of free transfers.

---

## Players We Considered But Didn't Pick

### GW2 Standout Performers — Why They're Not In Your Squad

*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW forward model uses EWMA form features that smooth out single-match spikes. One hot game shifts the model's view less than you'd expect.)*

- **Groß** (MID, Brighton): **13 pts in GW2** (1 goal, 1 assist). Not transferred in — Forward projection: 6.09 xPts for GW3 (ranked #11 overall). This is below our lowest-ranked MID in the squad (6.65 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Haaland** (FWD, Man City): **13 pts in GW2** (2 goals). Not transferred in — Forward projection 5.52 xPts (ranked #19 overall) is competitive, but bringing them in at £15.5m would require dropping a player the MILP values more over the full 6-GW horizon.
- **Gibbs-White** (MID, Nott'm Forest): **13 pts in GW2** (1 goal, 1 assist). Not transferred in — Forward projection: 5.91 xPts for GW3 (ranked #14 overall). This is below our lowest-ranked MID in the squad (6.65 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Scott** (MID, Bournemouth): **12 pts in GW2** (1 goal, clean sheet). Not transferred in — Forward projection: 4.54 xPts for GW3 (ranked #32 overall). This is below our lowest-ranked MID in the squad (6.65 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Mbeumo** (MID, Man Utd): **11 pts in GW2** (1 goal, 1 assist). Not transferred in — Forward projection: 4.84 xPts for GW3 (ranked #29 overall). This is below our lowest-ranked MID in the squad (6.65 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Hall** (DEF, Newcastle): **11 pts in GW2** (clean sheet, DEFCON bonus (11 CBIT)). Not transferred in — Forward projection: 5.12 xPts for GW3 (ranked #23 overall). This is below our lowest-ranked DEF in the squad (5.87 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.
- **Dedić** (DEF, Newcastle): **11 pts in GW2** (1 assist, clean sheet). Not transferred in — Forward projection: 5.40 xPts for GW3 (ranked #21 overall). This is below our lowest-ranked DEF in the squad (5.87 xPts). The EWMA form model smooths over single-match spikes — a big GW shifts the average less than the raw score suggests.

### Highest-Projected Players Not In Your Squad

- **Groß** (MID, Brighton, £5.5m, 6.09 xPts): ranked #11 overall. Edged out by selected MIDs with better 6-GW projections.
- **Stach** (MID, Leeds, £6.0m, 5.94 xPts): ranked #13 overall. Edged out by selected MIDs with better 6-GW projections.
- **Gibbs-White** (MID, Nott'm Forest, £7.9m, 5.91 xPts): ranked #14 overall. Edged out by selected MIDs with better 6-GW projections.
- **Egan** (DEF, Hull City, £4.0m, 5.77 xPts): ranked #16 overall. 3-player Hull City cap is maxed.
- **Elanga** (MID, Newcastle, £6.1m, 5.66 xPts): ranked #17 overall. Edged out by selected MIDs with better 6-GW projections.
- **De Cuyper** (DEF, Brighton, £4.7m, 5.64 xPts): ranked #18 overall. Edged out by selected DEFs with better 6-GW projections.
- **Haaland** (FWD, Man City, £15.5m, 5.52 xPts): ranked #19 overall. £15.5m is hard to fit within £100m without dropping a player the MILP values more over 6 GWs.
- **Gakpo** (MID, Liverpool, £7.0m, 5.50 xPts): ranked #20 overall. Edged out by selected MIDs with better 6-GW projections.

---

## Chip Schedule

| GW | Chip | Est. Gain |
|---:|---|---:|
| 3 | Wildcard | +26.6 pts |
| 4 | Triple Captain | +9.6 pts |

*Play Wildcard this GW: dedicated legal rebuild beats the best ordinary plan by +26.63 discounted xPts (needs 12.00). Future chips were recalculated from the rebuilt squad.*

---

## GW-by-GW Plan

|   GW | Transfers                                                                                                                                                                                                                                                        | Chip           | Captain     | Vice        |   XI xPts |   Bench xPts |   Hits |   FT→ | Bank   |
|-----:|:-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|:---------------|:------------|:------------|----------:|-------------:|-------:|------:|:-------|
|    3 | Trafford ← Martinez, Tzolakis ← Pickford, Calafiori ← Gabriel, White ← James, Tarkowski ← Lacroix, Ajayi ← O'Reilly, Mendy ← Guéhi, Saka ← Lewis-Potter, Palmer ← Groß, M.Sangaré ← Szoboszlai, João Pedro ← Thiago, Emersonn ← Calvert-Lewin, Wissa ← Woltemade | Wildcard       | Tzolakis    | B.Fernandes |     90.28 |        19.11 |      0 |     2 | £1.5m  |
|    4 | Stach ← M.Sangaré                                                                                                                                                                                                                                                | Triple Captain | Palmer      | João Pedro  |     86.47 |        17.95 |      0 |     2 | £1.2m  |
|    5 | Elanga ← Stach                                                                                                                                                                                                                                                   | —              | B.Fernandes | Palmer      |     88.45 |        18.76 |      0 |     2 | £1.1m  |
|    6 | Roll                                                                                                                                                                                                                                                             | —              | B.Fernandes | Saka        |     89.68 |        18.1  |      0 |     3 | £1.1m  |
|    7 | Roll                                                                                                                                                                                                                                                             | —              | B.Fernandes | Palmer      |     85.67 |        16.22 |      0 |     4 | £1.1m  |
|    8 | Roll                                                                                                                                                                                                                                                             | —              | B.Fernandes | Palmer      |     83.66 |        17.29 |      0 |     5 | £1.1m  |

---

## Starting XI — GW3

**GKP:** **Tzolakis**(C)  
**DEF:** **Calafiori** | **Tarkowski** | **Ajayi** | **Mendy**  
**MID:** **Saka** | **Palmer** | **Cherki** | **B.Fernandes** | **M.Sangaré**  
**FWD:** **João Pedro**  

**Bench:** White | Emersonn | Trafford | Wissa