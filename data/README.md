# FPL Bot — Data Sources

This directory documents all data sources used by the bot.
**All primary data is fetched automatically at runtime** — nothing here needs to be downloaded manually.

---

## Auto-fetched sources (zero setup)

| Source | What | URL pattern | Cache |
|---|---|---|---|
| FPL API | Current 564 players, prices, teams, GWs, injuries | `fantasy.premierleague.com/api/bootstrap-static/` | 6h |
| FPL API | All 380 fixtures + FDR | `/api/fixtures/` | 6h |
| FPL API | Per-player history + upcoming fixtures | `/api/element-summary/{id}/` | 24h |
| FPL API | Set-piece notes (penalty, corner takers) | `/api/team/set-piece-notes/` | 6h |
| Vaastav GitHub | Per-GW training data 2020-21 → 2025-26 | `raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/{season}/gws/merged_gw.csv` | 7d |
| Vaastav GitHub | Season-level understat xG/xA | `.../data/{season}/understat/understat_plus.csv` | 7d |
| Vaastav GitHub | Season totals (cleaned_players.csv) | `.../data/{season}/cleaned_players.csv` | 7d |
| football-data.co.uk | Pinnacle closing odds (Dixon-Coles) | `football-data.co.uk/mmz4281/{code}/E0.csv` | 7d |
| ESPN (public) | PL news headlines | `site.api.espn.com/apis/site/v2/sports/soccer/eng.1/news` | 1h |
| FPL-Core-Insights | 2026-27 CSV snapshot | `raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data/2026-2027/players.csv` | 6h |

Cache files live in `bot/cache/` (git-ignored).

---

## What each source contributes

### FPL API (official, primary)
The single best source for anything FPL-specific:
- **Prices** (`now_cost`): integer tenths, e.g. 80 = £8.0m
- **Injury status** (`status`, `news`, `chance_of_playing_*`): most reliable injury signal
- **Form & selection** (`form`, `selected_by_percent`, `points_per_game`)
- **Expected stats** (`expected_goals`, `expected_assists`, `expected_goal_involvements`, `expected_goals_conceded`)
- **ICT index** (`influence`, `creativity`, `threat`) — FPL's own composite
- **Defensive contribution** (`clearances_blocks_interceptions`, `recoveries`, `tackles`) — from 2025-26 only
- **Set-piece order** (`penalties_order`, `corners_and_indirect_freekicks_order`)

### Vaastav Fantasy-Premier-League (training data)
Historical per-gameweek CSVs for all 10 seasons back to 2016-17.
We use 2020-21 → 2025-26 (6 seasons, ~180,000 rows) to avoid COVID-disrupted 2019-20.

**Important caveats:**
- `clearances_blocks_interceptions`, `recoveries`, `tackles`, `defensive_contribution` only exist from **2025-26** (DC rule introduced that season)
- `xP` (expected points) from Vaastav is scraped post-gameweek and must be shifted or excluded to avoid look-ahead bias
- Weekly Vaastav updates stopped after 2024-25; 2025-26 is the last complete season

Available cross-season file: `data/cleaned_merged_seasons.csv` (all seasons combined)

### Vaastav understat
Shot-level xG from understat.com mirrored per season:
- `xG`, `xA`, `npxG` (non-penalty xG), `xGChain`, `xGBuildup`
- `time` (minutes played) — used to compute per-90 rates
- File: `data/{season}/understat/understat_plus.csv` (or `understat.csv` for older seasons)

### football-data.co.uk
Free historical match CSVs with bookmaker closing odds:
- Pinnacle closing odds (sharpest public price): `PSCH`, `PSCD`, `PSCA`
- Over/under 2.5: `PC>2.5`, `PC<2.5`
- Full-time scores (`FTHG`, `FTAG`), shots, corners, cards
- Used for Dixon-Coles ratings + de-vigged market goal rates

**Note:** bookmaker column sets change between seasons. The `data_collector` normalises
all seasons to stable `odds_home`, `odds_draw`, `odds_away`, `odds_over25` columns.

### ESPN (unofficial public API)
No authentication, no rate limit published. Used for supplementary news only:
- **Endpoint:** `https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/news`
- Returns headlines and descriptions; player injury endpoints return empty for soccer (verified)
- Use the FPL `news` field as the primary injury signal; ESPN gives context headlines only
- League ID for Premier League: `eng.1`

---

## Optional data (not auto-fetched)

### Kaggle: FPL 2025-26 Dataset
`https://www.kaggle.com/datasets/calvinrostanto/fantasy-premier-league-2025-2026`

Requires Kaggle credentials. Vaastav already has 2025-26 data — this is not needed.
Download with: `kaggle datasets download calvinrostanto/fantasy-premier-league-2025-2026`

### soccerdata (FBref/Understat)
`pip install soccerdata` — scrapes public pages, no key.
Provides shot coordinates, passing networks, advanced defensive metrics.
Not in the default pipeline (heavy download, self-rate-limits); enable via `data_collector.fetch_soccerdata_fbref`.

---

## NOT used and why

| Source | Why excluded |
|---|---|
| fplform.com/fpl-predicted-points | No public API (verified); 10 MB HTML page; proprietary model |
| Paid odds APIs (The Odds API, Betfair) | Require accounts/keys; replaced by free football-data.co.uk Pinnacle closing odds |
| LLM news sentiment (OpenAI etc.) | Requires API keys; domain-specific keyword scorer is more accurate for FPL injury text |
| Transfermarkt squad arrivals/departures | Scrapeable but not yet wired; `promoted_team_priors` covers the promoted-club case |

---

## Training vs inference data split

**Training (never use 2026/27 data here):**
- Vaastav 2020-21 through 2025-26
- football-data.co.uk odds through 2024-25
- Used to fit: MinutesModel, AttackModel, DefenseModel, BonusModel, Dixon-Coles ratings

**Inference (current season, never in training):**
- FPL API bootstrap-static (2026/27 player pool, prices, preseason stats)
- FPL fixtures (2026/27 schedule + FDR)
- FPL-Core-Insights 2026-27 CSV
- ESPN news

Using 2026/27 gameweek results in training would be cheating — those results aren't known
at the time the bot makes its weekly recommendations.
