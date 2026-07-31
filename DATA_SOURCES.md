# FPL Data Sources Audit

Audit date: 2026-07-31

This file records the data sources checked for the FPL Auto project, whether they work, what calls to use, and how reliable they look for building a player/stat collection pipeline.

## Short Verdict

Use the official FPL REST API as the primary source. `bootstrap-static` should be the first ingest because it already returns all players, teams, gameweeks, rules/settings, prices, ownership, status/news, ICT, xG/xA, xGC, starts, defensive contribution, transfers, and ranking fields.

For an AI team builder, the best near-term stack is:

1. Official FPL `bootstrap-static` for current player pool, prices, positions, teams, gameweek metadata, FPL scoring, player availability, and core season stats.
2. Official FPL `fixtures` and `element-summary/{player_id}` for fixture difficulty and player-specific upcoming/history data.
3. Official FPL `event/{gw}/live` after matches start for live points and per-gameweek scoring.
4. FPL-Core-Insights as a secondary CSV warehouse, especially for 2026/27 CSV snapshots and cross-competition/Elo enrichment.
5. SoccerData and Understat only as optional enrichment for non-FPL football stats. They need entity matching and are less direct than official FPL IDs.

Do not depend on Vaastav for 2026/27 as the main source right now. The repo works and has historical files through `data/2025-26`, but `data/2026-27` was not present when checked. Its README also warns weekly updates were stopped after the 2024/25 season.

## Current Official FPL State

The current official FPL API is live for the 2026/27 preseason.

- `https://fantasy.premierleague.com/api/bootstrap-static/`: HTTP 200, JSON, 1.3 MB.
- `events`: 38 gameweeks.
- `teams`: 20 teams.
- `elements`: 564 players.
- GW1 is marked as next, with deadline `2026-08-21T17:30:00Z`.
- `https://fantasy.premierleague.com/api/event/1/live/`: HTTP 200, returns `{"elements":[]}` because no matches have started.
- Some player season stats are already populated in `bootstrap-static` even though GW1 has not started. Treat these as carried/historical FPL-provided season reference values until actual 2026/27 scoring begins.

## Official FPL REST Endpoints

Base path: `https://fantasy.premierleague.com/api`

The base URL itself returns HTTP 404. That is normal. Use concrete endpoint paths below.

| Endpoint | Status checked | Auth | Use |
| --- | --- | --- | --- |
| `/bootstrap-static/` | 200 | No | Best primary source. All players, teams, rules/settings, chips, phases, gameweeks, element stat definitions. |
| `/elements/` | 200 | No | Players only. Same player records as `bootstrap-static.elements`; useful for lighter refreshes. |
| `/events/` | 200 | No | Gameweeks only. Same as `bootstrap-static.events`; useful for lighter refreshes. |
| `/fixtures/` | 200 | No | All 380 league fixtures, kickoff times, team IDs, FDR, scores, match status, fixture-level stats once played. |
| `/fixtures/?event={gw}` | 200 | No | Fixtures for one gameweek. Checked GW1: 10 fixtures. |
| `/element-summary/{player_id}/` | 200 | No | Player upcoming fixtures, current-season gameweek history, and previous-season history. Checked player ID 1. |
| `/event/{gw}/live/` | 200 | No | Live gameweek player stats/points. Checked GW1: alive but empty preseason. |
| `/entry/{manager_id}/` | 200 for sample manager 1 | No | Public manager profile, leagues, team name, overall/event summary fields. |
| `/entry/{manager_id}/history/` | 200 for sample manager 1 | No | Manager current GW history, past seasons, chips. Current list empty preseason. |
| `/entry/{manager_id}/transfers/` | 200 for sample manager 1 | No | Public transfer history. Empty preseason for sample. |
| `/entry/{manager_id}/event/{gw}/picks/` | 404 for sample manager 1/GW1 | No | Correct known route for public picks after data is available. Preseason can return 404/not found. Keep endpoint but handle 404. |
| `/leagues-classic/{league_id}/standings/` | 200 for league 314 | No/possibly session for some leagues | Classic league metadata and standings. Preseason standings can be empty. |
| `/team/set-piece-notes/` | 200 | No | Set-piece notes per team, currently last updated `2026-07-22T18:37:17Z`. Useful for penalties/corners/free kicks notes. |
| `/me/` | 200 unauth but `player:null` | Auth for useful user data | Current logged-in user data. Without auth returns no player. |
| `/my-team/{entry_id}/` | Not public | Auth required | Current user's squad, picks, chips, bank. Already used in this project. |
| `/transfers/` POST | Not public | Auth required | Make transfers. Already used in this project. |
| `/my-team/{entry_id}/` POST | Not public | Auth required | Update picks/captain/vice/bench. Already used in this project. |

### Official FPL Calls

```bash
curl "https://fantasy.premierleague.com/api/bootstrap-static/"
curl "https://fantasy.premierleague.com/api/elements/"
curl "https://fantasy.premierleague.com/api/events/"
curl "https://fantasy.premierleague.com/api/fixtures/"
curl "https://fantasy.premierleague.com/api/fixtures/?event=1"
curl "https://fantasy.premierleague.com/api/element-summary/1/"
curl "https://fantasy.premierleague.com/api/event/1/live/"
curl "https://fantasy.premierleague.com/api/entry/1/"
curl "https://fantasy.premierleague.com/api/entry/1/history/"
curl "https://fantasy.premierleague.com/api/entry/1/transfers/"
curl "https://fantasy.premierleague.com/api/entry/1/event/1/picks/"
curl "https://fantasy.premierleague.com/api/leagues-classic/314/standings/"
curl "https://fantasy.premierleague.com/api/team/set-piece-notes/"
```

### Official Bootstrap Top-Level Keys

`bootstrap-static` currently returns:

- `chips`
- `events`
- `game_settings`
- `game_config`
- `phases`
- `teams`
- `total_players`
- `element_stats`
- `element_types`
- `elements`

Current counts:

| Key | Count |
| --- | ---: |
| `chips` | 8 |
| `events` | 38 |
| `phases` | 11 |
| `teams` | 20 |
| `element_stats` | 26 |
| `element_types` | 4 |
| `elements` | 564 |

### Main Player Stat Fields in `elements`

Important fields for AI selection:

- Identity/team/position: `id`, `code`, `first_name`, `second_name`, `web_name`, `known_name`, `element_type`, `team`, `team_code`, `photo`, `region`, `opta_code`.
- Selection/eligibility: `can_transact`, `can_select`, `status`, `news`, `news_added`, `chance_of_playing_this_round`, `chance_of_playing_next_round`, `removed`, `special`, `scout_risks`.
- Price/value: `now_cost`, `cost_change_event`, `cost_change_start`, `value_form`, `value_season`, `now_cost_rank`, `now_cost_rank_type`.
- Ownership/transfers: `selected_by_percent`, `selected_rank`, `transfers_in`, `transfers_in_event`, `transfers_out`, `transfers_out_event`.
- Points/form: `total_points`, `event_points`, `points_per_game`, `form`, `ep_this`, `ep_next`.
- Core scoring: `minutes`, `starts`, `goals_scored`, `assists`, `clean_sheets`, `goals_conceded`, `own_goals`, `penalties_saved`, `penalties_missed`, `yellow_cards`, `red_cards`, `saves`, `bonus`, `bps`.
- FPL analytics: `influence`, `creativity`, `threat`, `ict_index`, plus rank/type fields for each.
- Expected stats: `expected_goals`, `expected_assists`, `expected_goal_involvements`, `expected_goals_conceded`, and per-90 versions.
- Defensive contribution: `clearances_blocks_interceptions`, `recoveries`, `tackles`, `defensive_contribution`, `defensive_contribution_per_90`.
- Set-piece order hints: `corners_and_indirect_freekicks_order`, `direct_freekicks_order`, `penalties_order`, plus text fields.

The `element_stats` definition list currently includes: minutes, goals, assists, clean sheets, goals conceded, own goals, penalties saved/missed, yellow/red cards, saves, bonus, BPS, influence, creativity, threat, ICT, clearances/blocks/interceptions, recoveries, tackles, defensive contribution, starts, expected goals, expected assists, expected goal involvements, expected goals conceded.

### Player Detail Endpoint

`/element-summary/{player_id}/` returns:

- `fixtures`: upcoming fixtures for that player. Checked sample had 38.
- `history`: current season per-gameweek player history. Empty before GW1.
- `history_past`: previous season totals. Checked sample had 5 seasons.

Use this endpoint lazily per candidate/player, not for every refresh unless caching. For all 564 players it is many requests.

### Gameweek Live Endpoint

`/event/{gw}/live/` returns:

- `elements`: live player records for the gameweek.

Preseason response for GW1 is valid but empty. After matches start, expect each element to include `id`, `stats`, and point/explain data. Do not treat an empty list as failure before kickoff.

### Known Stale/Removed FPL Paths

These were probed and should not be used:

- `/bootstrap-dynamic/`: 404
- `/game-settings/`: 404 as standalone; use `bootstrap-static.game_settings`
- `/teams/`: 404 as standalone; use `bootstrap-static.teams`
- `/leagues-classic-standings/{league_id}/`: 404; use `/leagues-classic/{league_id}/standings/`

## Documentation and Wrapper Sources

| Source | Status | Reliability | Notes |
| --- | --- | --- | --- |
| FPL Assist Postman docs | 200 | Reference only | Loads as Postman web app. Useful endpoint descriptions, not an ingest dependency. |
| Cyberpug Postman docs | 200 | Reference only | Same as above. |
| Microsoft Fantasy Premier League connector | 200 | Reference only | Power Platform connector, 100 calls per 60 seconds per connection. Documents General Info, Managers Basic Info, Managers History, Players Detailed Data. |
| `fpl` Python wrapper docs | 200 | Medium | Async wrapper around FPL API. Docs list useful API concepts, but PyPI latest upload was 2023-08-14 and repo last push was 2024-07-26. Current project already has direct `requests` wrappers, which is simpler. |
| `fplscrapR` | 200 | Low | GitHub repo is archived/read-only. Avoid for new work. |
| `flavnat/footy-api` and `fpl-api-tau.vercel.app` | 200 | Medium/secondary | Unofficial Fastify/GraphQL/PostgreSQL mirror. Public GraphQL works and returns current FPL-shaped data, but it adds third-party dependency risk. |

### Unofficial GraphQL Mirror

Docs:

- `https://fpl-api-tau.vercel.app/`
- Markdown docs exist at `https://fpl-api-tau.vercel.app/v1/README.md`
- GraphQL endpoint: `https://fpl-api-6h0d.onrender.com/graphql`
- No API key required for public data retrieval according to its docs.

Checked GraphQL query:

```graphql
query {
  elements(first: 3, orderBy: { field: total_points, direction: DESC }) {
    items {
      id
      web_name
      now_cost
      total_points
      selected_by_percent
      expected_goals
      expected_assists
    }
  }
}
```

This returned HTTP 200. It is useful for ad hoc filtered queries, but the official API is still the source of truth.

## Open Datasets and Repositories

| Source | Status | Reliability | Use |
| --- | --- | --- | --- |
| Vaastav Fantasy-Premier-League | 200 | Historical only for this project | Raw historical CSVs work. `data/2025-26/gws/merged_gw.csv` exists. `data/2026-27` did not exist when checked. README says weekly updates stopped after 2024/25. |
| FPL-Core-Insights | 200 | High secondary | Active 2026/27 CSV dataset. Repo pushed 2026-07-31. `data/2026-2027/players.csv` and `playerstats.csv` returned HTTP 200. Claims twice-daily refresh. Good enrichment warehouse. |
| OpenFootball England | 200 | Medium | Public-domain fixtures/results archive. 2026/27 Premier League text file exists. Not FPL player data, but useful for fixture archive cross-checking. |

### Raw Dataset Calls

```bash
curl "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data/2026-2027/players.csv"
curl "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data/2026-2027/playerstats.csv"
curl "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/2025-26/gws/merged_gw.csv"
curl "https://raw.githubusercontent.com/openfootball/england/master/2026-27/1-premierleague.txt"
```

FPL-Core-Insights useful files:

- `/data/2026-2027/players.csv`
- `/data/2026-2027/teams.csv`
- `/data/2026-2027/playerstats.csv`
- `/data/2026-2027/gameweek_summaries.csv`
- `/data/2026-2027/By Gameweek/GW{x}/...`
- `/data/2026-2027/By Tournament/{tournament_name}/GW{x}/...`

## Advanced Football Analytics

| Source | Status | Reliability | Notes |
| --- | --- | --- | --- |
| soccerdata docs/PyPI | 200 | Medium/high for enrichment | Active package. PyPI version `1.9.1`, uploaded 2026-07-24. Scrapes FBref, ESPN, Football-Data.co.uk, Sofascore, SoFIFA, Understat, WhoScored, ClubElo. Returns pandas DataFrames. |
| understat Python package | 200 | Medium | PyPI version `0.1.14`, uploaded 2025-12-16. Useful xG/xA/xGChain/xGBuildup enrichment. Needs player/team name matching to FPL IDs. |
| OpenFootball England | 200 | Medium | Fixtures/results only, no FPL stats. |

### soccerdata Example

```python
import soccerdata as sd

fbref = sd.FBref("ENG-Premier League", "2026-2027")
schedule = fbref.read_schedule()
player_stats = fbref.read_player_season_stats(stat_type="standard")
team_passing = fbref.read_team_season_stats(stat_type="passing")
```

### understat Example

```python
import aiohttp
import asyncio
from understat import Understat

async def main():
    async with aiohttp.ClientSession() as session:
        understat = Understat(session)
        players = await understat.get_players("epl", 2026)
        return players

players = asyncio.run(main())
```

## General Football APIs

These are alive but should be secondary because they either require keys, have plan restrictions, or are less FPL-specific.

| Source | Docs status | Sample/API status | Auth | Use |
| --- | --- | --- | --- | --- |
| API-Football/API-Sports | Docs blocked scripted fetch with 403 Cloudflare | API `/status` returned 403 missing key | API key header | Broad football stats, fixtures, odds, injuries depending plan. Not needed for first FPL model. |
| football-data.org | 200 | `/v4/competitions` works anonymously; PL matches returned 403 without permissions | `X-Auth-Token` | Fixtures, standings, scorers. Need token/subscription for useful PL match data. |
| TheSportsDB | 200 | Free key `3` sample calls worked | Public/free key or premium key | Teams, league events, artwork. Good for display/enrichment, not FPL scoring. |
| Sportmonks | 200 docs | API returned 401 no token | `api_token` query or auth header | Rich paid football data. Secondary only. |
| FootyStats | 200 docs | API returned 401 with placeholder key | API key | Team/league stats. Secondary only. |
| SoccerDataAPI.com | 200 docs | API returned 401 invalid token with placeholder | `auth_token` and gzip header | Live scores, league stats, transfers, injuries, previews. Secondary only. |

### General API Calls

```bash
# football-data.org
curl "https://api.football-data.org/v4/competitions"
curl -H "X-Auth-Token: YOUR_TOKEN" "https://api.football-data.org/v4/competitions/PL/matches"

# TheSportsDB public examples
curl "https://www.thesportsdb.com/api/v1/json/3/search_all_teams.php?l=English%20Premier%20League"
curl "https://www.thesportsdb.com/api/v1/json/3/eventsnextleague.php?id=4328"

# API-Football / API-Sports
curl -H "x-apisports-key: YOUR_KEY" "https://v3.football.api-sports.io/fixtures?league=39&season=2026"

# Sportmonks
curl "https://api.sportmonks.com/v3/football/leagues?api_token=YOUR_TOKEN"

# FootyStats
curl "https://api.footystats.org/league-list?key=YOUR_API_KEY"

# SoccerDataAPI.com
curl -H "Accept-Encoding: gzip" "https://api.soccerdataapi.com/livescores/?auth_token=YOUR_TOKEN"
```

## Articles and Other Links

| Link | Status | Notes |
| --- | --- | --- |
| Medium article: Getting started with FPL data | 429 via scripted fetch | Search index confirms it is a basic FPL API/Pandas guide. Not needed because direct endpoints were validated. |
| Postman FPL collection link | 200 | Reference only. |
| FPL bootstrap link | 200 | Primary. |

## Recommended Ingest Design

1. Fetch `bootstrap-static` on startup and cache it with `fetched_at`.
2. Normalize tables:
   - `players` from `elements`
   - `teams` from `teams`
   - `events` from `events`
   - `element_types`
   - `element_stats`
   - `game_settings` and `game_config`
3. Fetch `fixtures` daily or on app start. During active match windows, refresh more often.
4. Fetch `element-summary/{player_id}` lazily:
   - on player detail view
   - for shortlisted players
   - before optimizer runs
5. Fetch `event/{gw}/live` only for current/active gameweeks. Empty list is valid before kickoff.
6. For authenticated user/team actions, continue using the existing app flow:
   - `/me/`
   - `/my-team/{entry_id}/`
   - POST `/transfers/`
   - POST `/my-team/{entry_id}/`
7. Add FPL-Core-Insights as optional enrichment with explicit source labels. Do not silently mix it with official current data.

## Data Quality Rules

- Treat prices as tenths in official FPL (`now_cost=80` means 8.0).
- Treat many numeric-looking FPL fields as strings until parsed: examples include `form`, `points_per_game`, `selected_by_percent`, `influence`, `creativity`, `threat`, `ict_index`, expected stat fields.
- Keep `element`/`id` as the primary FPL player ID for current season joins.
- Keep `code`/`element_code` for cross-season player matching.
- Handle preseason:
  - `history` is empty.
  - `event live` is empty.
  - picks can 404.
  - fixtures exist.
  - gameweek aggregate stats are zero/null.
- Do not train a model on same-gameweek `xP` from scraped post-GW data without shifting it. Vaastav's README specifically warns about lookahead bias in `xP`.
- Cache and rate-limit politely. There are no official published FPL API limits, but avoid hitting per-player summary endpoints in tight loops without cache.

## Project Fit

Current project file `fpl_api.py` already uses the right official base URL and has wrappers for:

- `bootstrap`
- `me`
- `my_team`
- `transfer`
- `update_picks`
- `entry_info`

Suggested next wrappers for data gathering:

```python
def fixtures(session, event=None): ...
def elements(session): ...
def events(session): ...
def player_summary(session, player_id): ...
def event_live(session, event): ...
def entry_history(session, entry_id): ...
def entry_transfers(session, entry_id): ...
def set_piece_notes(session): ...
```

## Source Links Checked

- https://fantasy.premierleague.com/api/bootstrap-static/
- https://fantasy.premierleague.com/api/fixtures/
- https://www.postman.com/fplassist/fpl-assist/documentation/zqlmv01/fantasy-premier-league-api
- https://www.postman.com/cyberpug/fpl-workspace/request/ftluun3/general-information
- https://fpl-api-tau.vercel.app/
- https://fpl-api-tau.vercel.app/v1/README.md
- https://fpl-api-6h0d.onrender.com/graphql
- https://fpl.readthedocs.io/en/latest/index.html
- https://pypi.org/project/fpl/
- https://github.com/amosbastian/fpl
- https://github.com/wiscostret/fplscrapR
- https://github.com/vaastav/Fantasy-Premier-League
- https://github.com/olbauday/FPL-Core-Insights
- https://soccerdata.readthedocs.io/
- https://pypi.org/project/soccerdata/
- https://github.com/amosbastian/understat
- https://understat.readthedocs.io/en/latest/
- https://github.com/openfootball/england
- https://api-sports.io/documentation/football/v3
- https://www.football-data.org/documentation/api
- https://www.thesportsdb.com/free_sports_api
- https://docs.sportmonks.com/v3/definitions/definition-of-common-football-terms
- https://footystats.org/api/
- https://soccerdataapi.com/docs/
- https://medium.com/analytics-vidhya/getting-started-with-fantasy-premier-league-data-56d3b9be8c32
- https://github.com/flavnat/footy-api
- https://learn.microsoft.com/en-us/connectors/fantasypremierleagueip/
