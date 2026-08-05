# FPL Bot v2 Training Result Summary

Source checked: `C:\Users\dimah\Downloads\scratchpad.ipynb`

This result came from a Colab scratchpad run of `notebooks/fpl_bot_v2.ipynb`.
The exact recommended team from the screenshot is present in the scratchpad's
printed output.

## Bottom Line

- The model was trained on historical seasons, not just one gameweek.
- The displayed squad recommendation is for 2026/27 Gameweek 1 only.
- The GW1 projection used 50,000 Monte Carlo simulations across 10 GW1 fixtures.
- Walk-forward validation cells were deliberately skipped/stubbed in this run.
- Transfer and chip code exists in the project, but this visible result is not a
  season-long transfer/chip plan.

## Training Data

The notebook reports 163,404 training rows across 6 historical seasons:

| Season | Rows | Defensive contribution columns |
|---|---:|---|
| 2020-21 | 24,365 | No |
| 2021-22 | 25,447 | No |
| 2022-23 | 26,505 | No |
| 2023-24 | 29,725 | No |
| 2024-25 | 27,605 | No |
| 2025-26 | 29,757 | Yes |

Feature matrix:

| Metric | Value |
|---|---:|
| Training rows | 163,404 |
| Features | 37 |
| Defensive-contribution labelled rows | 9,591 |

The notebook states that 2026/27 results are not used in training, avoiding
look-ahead bias for the GW1 recommendation.

## GW1 Simulation Inputs

The completed scratchpad output reports:

| Item | Value |
|---|---:|
| Available players | 527 |
| FPL history-past features | 464 players |
| Bootstrap fallback players | 63 players |
| Promoted-team players given default minutes | 40 |
| GW1 fixtures simulated | 10 |
| Simulations | 50,000 |

Promoted-team corrections were active:

- Promoted-team xG/xA discounts were applied.
- Position-level xG caps/floors were active.
- New promoted-team players with zero reported minutes received a 41.4 minute
  default.

## Top GW1 Projections

| Rank | Player | Team | Pos | Price | Mean | P80 | P90 | Haul % | Blank % |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | E.Le Fee | SUN | MID | 6.0 | 5.58 | 9.0 | 13.0 | 19 | 42 |
| 2 | Watkins | AVL | FWD | 8.0 | 5.23 | 8.0 | 13.0 | 14 | 56 |
| 3 | McBurnie | HUL | FWD | 5.5 | 5.04 | 11.0 | 15.0 | 26 | 58 |
| 4 | Gakpo | LIV | MID | 7.0 | 5.04 | 9.0 | 13.0 | 15 | 54 |
| 5 | Gibbs-White | NFO | MID | 8.0 | 5.02 | 9.0 | 10.0 | 15 | 48 |
| 6 | Schade | BRE | MID | 6.0 | 4.95 | 9.0 | 10.0 | 14 | 55 |
| 7 | O.Dango | BRE | MID | 6.5 | 4.93 | 9.0 | 13.0 | 15 | 56 |
| 8 | Szoboszlai | LIV | MID | 7.0 | 4.89 | 9.0 | 11.0 | 13 | 51 |
| 9 | Wirtz | LIV | MID | 7.5 | 4.85 | 9.0 | 13.0 | 14 | 55 |
| 10 | Hume | SUN | DEF | 4.5 | 4.63 | 7.0 | 10.0 | 12 | 56 |
| 11 | Saka | ARS | MID | 9.5 | 4.62 | 8.0 | 11.0 | 15 | 51 |
| 12 | Thiago | BRE | FWD | 8.0 | 4.53 | 8.0 | 12.0 | 11 | 63 |
| 13 | Mukiele | SUN | DEF | 5.5 | 4.52 | 7.0 | 10.0 | 12 | 55 |
| 14 | Raya | ARS | GKP | 6.0 | 4.47 | 8.0 | 8.0 | 0 | 38 |
| 15 | Tarkowski | EVE | DEF | 6.0 | 4.39 | 7.0 | 9.0 | 7 | 54 |

## Recommended 15-Man Squad

Squad cost: GBP 97.0m

Expected XI points: 59.4

Captain: E.Le Fee

Vice-captain: Watkins

| Pos | Player | Price | GW1 expected pts | Role |
|---|---|---:|---:|---|
| GKP | Raya | 6.0 | 4.465 | XI |
| GKP | Pickford | 5.5 | 4.189 | Bench |
| DEF | Hume | 4.5 | 4.628 | XI |
| DEF | Mukiele | 5.5 | 4.521 | XI |
| DEF | Tarkowski | 6.0 | 4.390 | XI |
| DEF | Gabriel | 8.0 | 4.216 | Bench |
| DEF | Virgil | 6.5 | 4.010 | Bench |
| MID | E.Le Fee | 6.0 | 5.581 | XI, Captain |
| MID | Gakpo | 7.0 | 5.039 | XI |
| MID | Gibbs-White | 8.0 | 5.024 | XI |
| MID | Schade | 6.0 | 4.953 | XI |
| MID | O.Dango | 6.5 | 4.925 | XI |
| FWD | Watkins | 8.0 | 5.226 | XI, Vice-captain |
| FWD | McBurnie | 5.5 | 5.040 | XI |
| FWD | Thiago | 8.0 | 4.527 | Bench |

Legality checks:

| Check | Result |
|---|---|
| Squad legal | True, ok |
| Formation legal | True |
| Max players per club | 3, limit 3 |
| Composition | 2 GKP, 5 DEF, 5 MID, 3 FWD |

## Captain Reasoning

| Player | Fixture | Mean | Haul % | Blank % | xG/90 | Note |
|---|---|---:|---:|---:|---:|---|
| E.Le Fee | vs 12 away | 5.58 | 19 | 42 | 0.104 | Captain |
| Watkins | vs 5 away | 5.23 | 14 | 56 | 0.500 | Vice-captain |
| McBurnie | vs 16 home | 5.04 | 26 | 58 | 0.221 |  |
| Gakpo | vs 17 away | 5.04 | 15 | 54 | 0.292 |  |
| Gibbs-White | vs 13 home | 5.02 | 15 | 48 | 0.280 |  |
| Schade | vs 19 home | 4.95 | 14 | 55 | 0.320 |  |
| O.Dango | vs 19 home | 4.93 | 15 | 56 | 0.320 |  |
| Szoboszlai | vs 17 away | 4.89 | 13 | 51 | 0.168 |  |

## Did It Train Only GW1 Or The Whole Year?

It trained on historical full-season player/gameweek rows from 2020-21 through
2025-26.

The final displayed squad, however, is optimized for GW1 only. The output says
`GW1: 10 fixtures, 50k sims`, and the expected points shown are GW1 expected
points, not full-season expected points.

## Did This Run Make Transfers Through The Year?

Not for the displayed recommendation.

The project has transfer/backtest machinery:

- `bot/optimizer.py` has an `optimize_transfers` method.
- `bot/backtester.py` supports `passive`, `1ft`, and `wildcard20` strategies.
- The notebook has season backtest cells for 2024-25 comparing those strategies.

But the scratchpad's visible reported result is a GW1 squad pick. It does not
show a season-long transfer log for the recommended 2026/27 team.

## Did This Run Use Chips?

Not for the displayed squad recommendation.

The codebase has chip logic:

- `bot/optimizer.py` can recommend chip usage from scenario gains.
- `bot/rl_agent.py` defines a chip-timing PPO environment.
- `notebooks/fpl_bot_v2.ipynb` has a chip strategy section and RL chip agent
  cells.

The visible scratchpad result does not show an actual chip being played for GW1
or a full-season chip schedule tied to this recommended squad.

## Important Caveat

The scratchpad deliberately skipped/stubbed walk-forward validation cells:

- `5a. Walk-forward validation`
- `5b. Walk-forward plot`
- `5c. Human manager comparison`

That means the saved scratchpad result is useful as a GW1 recommendation report,
but it is not a complete validation report.
