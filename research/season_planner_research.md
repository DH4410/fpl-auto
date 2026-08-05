# Season-Long FPL Planner Research

Date: 2026-08-05

Goal: turn the current GW1 picker into a season-long planner that chooses the
best starting squad, weekly transfers, captains, bench order, and chip windows
using the known 2026/27 fixture schedule.

## Sources Read

- OpenFPL-Scout-AI: https://github.com/elcaiseri/OpenFPL-Scout-AI
- OpenFPL API on RapidAPI: https://rapidapi.com/elcaiseri-elcaiseri-default/api/openfpl-api
- OpenFPL forecasting method: https://github.com/daniegr/OpenFPL
- OpenFPL paper: https://arxiv.org/abs/2508.09992
- Open FPL Solver / FPL Optimization Tools: https://github.com/solioanalytics/open-fpl-solver
- Open FPL Solver settings: https://raw.githubusercontent.com/solioanalytics/open-fpl-solver/main/data/comprehensive_settings.json
- Open FPL Solver setting docs: https://raw.githubusercontent.com/solioanalytics/open-fpl-solver/main/data/README.md
- FPL Review solver introduction: https://docs.fplreview.com/the-model/solvers/into-to-solvers/
- Andysimcoe FPL optimization tutorials: https://github.com/Andysimcoe/FPL-Optimization-Tools
- Data-Driven Team Selection in FPL using Integer Programming: https://arxiv.org/abs/2505.02170
- Venter and van Vuuren, ORiON optimization approach: https://journals.co.za/doi/10.5784/40-1-004
- Vaastav historical FPL dataset: https://github.com/vaastav/Fantasy-Premier-League
- FPL Python wrapper docs: https://fpl.readthedocs.io/en/latest/index.html
- Official chip update for 2026/27: https://www.premierleague.com/en/news/4679879/whats-happening-with-fpl-chips-in-202627
- Official/free-transfer help: https://fantasy.premierleague.com/en/help
- Reddit: FPL Optimization Tool updated for 23/24: https://www.reddit.com/r/FantasyPL/comments/15mybp4/fpl_optimization_tool_updated_for_2324_season/
- Reddit: FPL Bot attempt to solve the game: https://www.reddit.com/r/FantasyPL/comments/1ceda33/fpl_bot_my_attempt_to_solve_the_game/

## Key Research Takeaways

OpenFPL-Scout-AI is useful as a benchmark and possible external forecast
source. It uses an ensemble of Linear Regression, XGBoost, and CatBoost to
predict player points, exposes RapidAPI endpoints such as `/api/gw/scout` and
`/api/gw/playerpoints`, and focuses on single-gameweek optimal team selection.

Daniel Groos's OpenFPL is more interesting for forecasting quality. It is an
open-source FPL forecasting method using public FPL and Understat data. The
paper reports position-specific ensembles trained on 2020-21 through 2023-24
and tested prospectively on 2024-25. It explicitly evaluates one-, two-, and
three-gameweek forecast horizons, which is exactly the sort of signal needed for
transfer planning.

Open FPL Solver is the strongest architecture reference for optimization. It is
a deterministic multi-period FPL optimizer using pandas and HiGHS. Its settings
include an 8-gameweek horizon, decay factor, free-transfer value, bench weights,
transfer hit cost, chip constraints, forced chip weeks, allowed chip weeks, and
transfer/chip summaries.

FPL Review's solver docs frame the core problem well: projections become useful
only when a solver searches team selections and transfer sequences across
multiple gameweeks. The docs explicitly call out transfer planning, projection
uncertainty, scenario planning, and constraints for chips/planned transfers.

The Andysimcoe/Sertalp tutorial repository is useful because it decomposes the
problem into learnable pieces: single-period squad/lineup/captain optimization,
multi-period optimization, bench decisions, noise in expected values,
sensitivity analysis, data collection from the FPL API, and wildcard
optimization.

The 2025 integer-programming paper is useful as academic support for combining
predictive modeling with deterministic/robust integer programming and Monte
Carlo simulation. It is mostly about selecting the starting XI and captain, so
it is not enough by itself for full-season FPL, but it reinforces the modeling
choice: use forecasts to build objective coefficients, then enforce FPL
constraints through integer programming.

Venter and van Vuuren's ORiON paper is relevant because it reports a
combinatorial optimization approach that forecasted future FPL performances and
retrospectively placed within the top 4% worldwide in 2020/21. That is strong
evidence that "forecast plus optimizer" is a realistic approach, even if it
does not guarantee winning.

The Reddit/community sources are not authoritative, but they are practically
useful. The recurring pattern from serious tool builders is an 8-week planning
horizon, expected-points inputs from multiple sources, mixed-integer linear
programming, bench/captain/transfer/chip outputs, time decay, sub weights, and
free-transfer/budget values. One high-performing FPL bot write-up splits the
problem exactly into two areas: expected points for each player over the next N
gameweeks, then optimization of transfer and chip strategy over those weeks.

Vaastav remains the best public historical data source, but its README now says
weekly updates stopped after 2024-25. It is still useful for old training data,
but current-season live data needs FPL API, OpenFPL API, direct bootstrap,
fixture endpoints, odds, injury/news sources, or our local warehouse.

The official 2026/27 rules matter for the planner:

- Managers have a Wildcard, Free Hit, Triple Captain, and Bench Boost in each
  half of the season, so 8 chips total.
- The first chip set must be used before the GW19 deadline and does not carry
  into the second half.
- Only one chip can be played in a single Gameweek.
- Free transfers can be banked up to 5.

The official chip article also gives strategic hints that should be encoded as
priors or scenario checks, not hard-coded truth: Free Hit is commonly useful for
blank gameweeks, Bench Boost and Triple Captain are commonly useful for doubles,
Wildcard should be used with a longer-term outlook, and early Triple Captain
opportunities can come from premium attackers at home to promoted teams.

## What These Sources Imply

The project should not try to "know" the entire season's real results. It should
know the fixture calendar and uncertainty. A winning-grade system needs to
compare policies under many simulated futures:

- base projection
- optimistic/pessimistic minutes
- injury/rotation shocks
- fixture postponement/double-gameweek scenarios
- price-change stress tests
- high-ownership versus differential strategy modes

The planner should avoid a single static 38-GW answer. The right behavior is a
rolling plan that is re-solved weekly while preserving the current best known
future route. This is both computationally easier and strategically sound:
future information changes every week.

## Design Principles From The Literature And Tools

1. Forecast first, optimize second.
   Do not mix model training code into the optimizer. The optimizer should
   accept a table of future expected points and risk metrics.

2. Optimize a horizon, not only the next gameweek.
   Sources repeatedly converge around 4-8 gameweek planning, with Solio's
   default set to 8 and community tools commonly supporting up to 8 weeks.

3. Discount future gameweeks.
   Solio's default `decay_base` is 0.9. That is a sensible starting point
   because GW8 is less certain than GW1, but it still matters.

4. Give value to free transfers and money in the bank.
   Open FPL Solver includes `ft_value`, `ft_value_list`, `ft_use_penalty`, and
   `itb_value`. This prevents the model from overspending or forcing low-value
   sideways transfers.

5. Model bench value.
   Bench players are not zero-value assets. Solio's default bench weights are
   small but nonzero and position-dependent by bench order.

6. Treat chips as constraints and marginal-value decisions.
   Chips should be available, forbidden, forced, or allowed by GW. The planner
   should compute their expected marginal gain, then obey one-chip-per-GW and
   first-half expiry constraints.

7. Produce alternatives and sensitivity analysis.
   Exact optimal plans can be fragile when two players differ by 0.1 expected
   points. The implementation should support alternative solutions and scenario
   runs before presenting a recommendation.

## Recommended Architecture

Build this as two separate engines:

1. Forecast engine
2. Season optimization engine

The forecast engine answers:

> For every player, in every future gameweek, what is the expected point
> distribution, probability of starting, minutes, haul probability, blank
> probability, and injury/rotation risk?

The optimization engine answers:

> Given those forecasts and FPL rules, what squad/transfer/captain/chip path
> maximizes expected season points or rank-aware upside?

Keep these separate. Forecasting mistakes should be debuggable without touching
the FPL rules solver.

## Forecast Engine Strategy

Use an ensemble rather than one model:

- Existing local model from `fpl_bot_v2.ipynb`
- OpenFPL-style position-specific model
- Market/team-goal simulation model
- Optional OpenFPL API/RapidAPI predictions as an external comparison source

For each future fixture, produce player-level forecast components:

- expected minutes
- probability of appearance
- probability of 60+ minutes
- goals/xG
- assists/xA
- clean-sheet probability
- save/bonus/defensive-contribution expectation
- cards and goals conceded where relevant
- uncertainty distribution, not only mean xPts

For far-future weeks, increase uncertainty and decay confidence. The exact
future fixture schedule is known, but lineups, injuries, transfers, postponements,
price changes, and double/blank gameweeks are not fully known. So the bot should
"look ahead" probabilistically, not pretend it knows actual results.

## Optimization Strategy

Use rolling-horizon mixed-integer optimization instead of one giant 38-GW solve.

Recommended default:

- Horizon: 6 to 8 gameweeks
- Re-solve every gameweek
- Lock only the immediate next action
- Simulate 20,000 to 50,000 season paths for robustness checks

At each gameweek:

1. Load current squad, bank, free transfers, chip availability, prices, injuries.
2. Forecast all players for the next 6-8 GWs.
3. Solve for squad, transfers, XI, captain, vice-captain, bench, and possible chip.
4. Execute/report only the current GW decision.
5. Advance one GW and re-run with updated information.

This avoids the brittle fantasy of "solve August through May perfectly", while
still choosing a GW1 squad that is good beyond GW1.

## Initial Squad Objective

The current notebook maximizes GW1 expected points. Replace that with:

```text
maximize:
  GW1 XI points
  + decayed expected value from GW2-GW8
  + value of banked transfers
  + bench cover value
  + future captaincy value
  + chip setup value
  - transfer hit risk
  - forced-transfer/injury/rotation risk
```

This will stop the model from over-picking one-week punts unless they also help
the next several weeks or enable a planned transfer path.

## Transfer Model

State variables:

- owned player in each GW
- bought/sold indicators
- bank/free-transfer count
- selling price and purchase price approximation
- hit count
- max 3 players per club
- squad composition
- valid XI formation

Rules:

- 1 new free transfer per GW
- roll unused free transfers up to 5
- hits cost 4 points each
- Wildcard allows unlimited permanent transfers for one GW
- Free Hit creates a one-week temporary squad and restores the original squad
  after the GW

For a first implementation, ignore price rises/falls except current sell prices.
Add price-change simulation later.

## Chip Model

Do not hard-code chip weeks only. Score each chip's marginal value:

- Wildcard: value of restructuring squad over future horizon
- Free Hit: one-week delta versus current squad, especially blanks/doubles
- Bench Boost: expected bench points in that GW
- Triple Captain: extra captain expected points and haul upside

Constraints:

- one Wildcard, Free Hit, Bench Boost, Triple Captain before GW19
- one refreshed set after GW19
- one chip max per GW
- first-half chips expire if unused

The first implementation can use deterministic expected-value chip planning.
The second implementation should run scenario analysis over uncertain future
double/blank gameweeks.

## "Winning FPL" Strategy Layer

Pure expected points is the right foundation, but top overall rank usually also
needs controlled upside.

Add rank-aware mode later:

- Early season: maximize expected points and squad flexibility.
- Mid season: use ownership only as a tiebreaker.
- Late season: if chasing, overweight high-upside/low-owned captains and
  players; if leading, reduce downside and protect high-owned essentials.

This requires effective ownership estimates. Possible public approximations:

- overall selected-by percentage from FPL bootstrap
- top-manager sample scraping if available
- league/entry pick sampling through public FPL endpoints

## Implementation Plan For This Repo

1. Add `bot/season_forecaster.py`
   - builds player xPts by GW for all remaining fixtures
   - stores mean, p10, p50, p90, p_start, p60, p_haul, p_blank

2. Add `bot/season_planner.py`
   - rolling-horizon optimizer
   - takes current squad or preseason empty state
   - outputs GW-by-GW decisions

3. Add `bot/chip_planner.py`
   - computes marginal chip value by GW
   - enforces half-season chip expiry

4. Add `notebooks/fpl_season_planner.ipynb`
   - clear report: starting squad, transfer path, chip plan, expected points,
     scenario robustness

5. Add exports:
   - `reports/season_plan_latest.md`
   - `reports/season_plan_latest.csv`
   - optional JSON for app integration

## Output The Planner Should Produce

Minimum useful report:

| GW | Transfers | Chip | Captain | Vice | XI xPts | Bench xPts | Bank | FT |
|---:|---|---|---|---|---:|---:|---:|---:|
| 1 | Initial squad | None | Player A | Player B | 58.4 | 6.2 | 1.5 | 1 |
| 2 | Roll | None | Player C | Player A | 55.0 | 5.1 | 1.5 | 2 |
| 3 | Player X -> Player Y | None | Player C | Player B | 61.2 | 4.8 | 0.7 | 1 |

Also include:

- starting 15-man squad
- total expected points over horizon
- best chip windows
- player transfer watchlist
- scenario sensitivity: optimistic/base/pessimistic
- top 10 players by 6-GW value
- top 10 players by full-season value

## Bottom Line

Yes, we can build this. The most credible path is not "train 50k for a full
season"; it is:

1. train forecasting models on historical seasons,
2. generate future GW-by-GW player projections from 2026/27 fixtures,
3. use 20k-50k simulated season paths to measure uncertainty,
4. solve transfers/chips with a rolling 6-8 GW optimizer,
5. re-run every week as real information arrives.

That is much closer to how serious FPL optimization tools work.
