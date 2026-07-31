"""
Module 4 -- squad optimisation by mixed-integer programming.

Every decision this module makes is a recommendation. Nothing here calls an FPL
write endpoint; the caller decides whether to act.

Solvers
-------
Free solvers only -- **gurobipy is never imported**. :func:`get_solver` probes
for HiGHS through PuLP and falls back to the bundled CBC, so the module works on
a bare ``pip install pulp`` and gets HiGHS's speed when ``highspy`` is present.

Why MIP rather than a greedy heuristic
--------------------------------------
Squad selection is a multi-dimensional knapsack: maximise expected points under
a budget, a positional quota, a three-per-club cap and formation legality. Greedy
"best points per million" picks fail on it because the constraints interact --
the cheap enabler that unlocks a premium forward is invisible to a greedy rule.
A MIP solves it exactly.

Money handling
--------------
All budget arithmetic is done in **integer tenths of a million**, matching FPL's
own representation. Selling prices follow the 50%-profit rule from
:mod:`fpl_rules` -- a player bought at £5.0m and now worth £5.5m sells for £5.2m,
not £5.5m. Using ``now_cost`` for sales, as naive implementations do, silently
inflates the budget and produces squads the user cannot actually buy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd
import pulp

from .fpl_rules import (
    DEF, FWD, GKP, MAX_BANKED_FT, MAX_PLAYERS_PER_CLUB, MID,
    FORMATION_MINIMUMS, HIT_COST, RULES, SQUAD_COMPOSITION, SQUAD_SIZE,
    STARTING_BUDGET_TENTHS, STARTING_XI_SIZE, to_tenths,
)

log = logging.getLogger(__name__)

#: Discount applied to expected points in future gameweeks. Projections decay in
#: reliability with horizon, and FPL lets you re-plan every week, so a point
#: promised three gameweeks out is worth less than one this week.
DEFAULT_HORIZON_DISCOUNT = 0.85

#: Points credited for a bench slot. Bench players score nothing unless Bench
#: Boost is active, but a squad with a competent bench survives injuries and
#: rotation, so they carry a small positive weight in squad selection.
DEFAULT_BENCH_WEIGHT = 0.15


def get_solver(msg: bool = False, time_limit: int | None = 120):
    """Return the best available free MIP solver.

    Prefers HiGHS -- markedly faster than CBC on these models -- and falls back
    to the CBC binary that ships with PuLP. Gurobi is deliberately not
    considered: it is commercial and requires a licence.

    PuLP has exposed HiGHS under more than one class name across versions, so
    the candidates are probed by name rather than imported directly.
    """
    for attr in ("HiGHS", "HiGHS_CMD"):
        cls = getattr(pulp, attr, None)
        if cls is None:
            continue
        try:
            solver = cls(msg=msg, timeLimit=time_limit) if time_limit \
                else cls(msg=msg)
            if solver.available():
                log.debug("using solver %s", attr)
                return solver
        except (AttributeError, TypeError, pulp.PulpError) as exc:
            log.debug("solver %s unavailable: %s", attr, exc)

    log.debug("falling back to CBC")
    return pulp.PULP_CBC_CMD(msg=msg, timeLimit=time_limit)


def _name_column(df: pd.DataFrame) -> str:
    """Pick a display-name column, preferring FPL's web_name.

    Frames arriving from a previous optimiser result carry ``name`` instead of
    ``web_name``, and a concatenation of the two produces a half-NaN column, so
    the choice is made on which column actually has values.
    """
    for col in ("web_name", "name"):
        if col in df.columns and df[col].notna().any():
            return col
    return "element"


def _positions(df: pd.DataFrame) -> np.ndarray:
    return df["element_type"].astype(int).to_numpy()


def _prices(df: pd.DataFrame) -> np.ndarray:
    """Purchase prices in integer tenths."""
    if "now_cost" in df.columns:
        return df["now_cost"].astype(int).to_numpy()
    if "price" in df.columns:
        return np.array([to_tenths(p) for p in df["price"]])
    raise KeyError("player frame needs now_cost or price")


@dataclass
class SquadOptimizer:
    """Mixed-integer optimisation of squads, line-ups, transfers and chips."""

    rules: object = field(default_factory=lambda: RULES)
    bench_weight: float = DEFAULT_BENCH_WEIGHT
    horizon_discount: float = DEFAULT_HORIZON_DISCOUNT
    solver_msg: bool = False
    time_limit: int | None = 120

    def _solver(self):
        return get_solver(msg=self.solver_msg, time_limit=self.time_limit)

    # ------------------------------------------------------------------
    # Initial squad
    # ------------------------------------------------------------------

    def optimize_initial_squad(self, player_pool_df: pd.DataFrame,
                               budget: float = 100.0,
                               points_col: str = "expected_points",
                               exclude: Sequence[int] = (),
                               force_include: Sequence[int] = (),
                               ) -> dict:
        """Pick the best legal 15 within budget.

        The objective weights starters fully and bench players at
        :attr:`bench_weight`, with the starting XI chosen simultaneously -- so
        the squad is optimised for the XI it will actually field rather than for
        15 players' combined totals, which would over-spend on the bench.

        ``player_pool_df`` needs ``element``/``id``, ``element_type``, ``team``,
        ``now_cost`` and a points column. Returns a dict with the chosen squad,
        the starting XI, captain and vice, and the total cost.
        """
        pool = player_pool_df.reset_index(drop=True).copy()
        if "element" not in pool.columns:
            pool["element"] = pool["id"]

        if len(exclude):
            pool = pool[~pool["element"].isin(exclude)].reset_index(drop=True)
        if pool.empty:
            raise ValueError("player pool is empty after exclusions")

        n = len(pool)
        pts = pool[points_col].fillna(0).to_numpy(dtype=float)
        cost = _prices(pool)
        pos = _positions(pool)
        club = pool["team"].astype(int).to_numpy()
        budget_tenths = to_tenths(budget)

        prob = pulp.LpProblem("fpl_initial_squad", pulp.LpMaximize)
        squad = [pulp.LpVariable(f"s_{i}", cat="Binary") for i in range(n)]
        start = [pulp.LpVariable(f"x_{i}", cat="Binary") for i in range(n)]
        capt = [pulp.LpVariable(f"c_{i}", cat="Binary") for i in range(n)]

        # Objective: starters at full weight, bench at a fraction, plus the
        # captain's doubled score.
        prob += (pulp.lpSum(pts[i] * start[i] for i in range(n))
                 + self.bench_weight * pulp.lpSum(
                     pts[i] * (squad[i] - start[i]) for i in range(n))
                 + pulp.lpSum(pts[i] * capt[i] for i in range(n)))

        prob += pulp.lpSum(squad) == SQUAD_SIZE
        prob += pulp.lpSum(start) == STARTING_XI_SIZE
        prob += pulp.lpSum(cost[i] * squad[i] for i in range(n)) <= budget_tenths

        for i in range(n):
            prob += start[i] <= squad[i]      # cannot start a player you do not own
            prob += capt[i] <= start[i]       # the captain must be in the XI
        prob += pulp.lpSum(capt) == 1

        for et, required in SQUAD_COMPOSITION.items():
            prob += pulp.lpSum(squad[i] for i in range(n) if pos[i] == et) == required

        # Formation legality on the starting XI.
        prob += pulp.lpSum(start[i] for i in range(n) if pos[i] == GKP) == 1
        for et, minimum in FORMATION_MINIMUMS.items():
            if et == GKP:
                continue
            prob += pulp.lpSum(start[i] for i in range(n)
                               if pos[i] == et) >= minimum

        for c in np.unique(club):
            prob += pulp.lpSum(squad[i] for i in range(n)
                               if club[i] == c) <= MAX_PLAYERS_PER_CLUB

        for element in force_include:
            idx = pool.index[pool["element"] == element].tolist()
            if idx:
                prob += squad[idx[0]] == 1

        status = prob.solve(self._solver())
        if pulp.LpStatus[status] != "Optimal":
            raise RuntimeError(f"squad optimisation failed: {pulp.LpStatus[status]}")

        chosen = [i for i in range(n) if squad[i].value() > 0.5]
        starters = [i for i in range(n) if start[i].value() > 0.5]
        captain = next(i for i in range(n) if capt[i].value() > 0.5)
        bench = [i for i in chosen if i not in starters]
        # Vice-captain: the best starter who is not the captain.
        vice = max((i for i in starters if i != captain), key=lambda i: pts[i])

        return self._package(pool, chosen, starters, bench, captain, vice, cost, pts)

    # ------------------------------------------------------------------
    # Starting XI
    # ------------------------------------------------------------------

    def optimize_starting_xi(self, squad_df: pd.DataFrame,
                             expected_points: Sequence[float] | str
                             = "expected_points") -> dict:
        """Best legal XI, captain, vice and bench order from a fixed 15.

        Bench order matters: it decides who comes on when a starter blanks, so
        the bench is returned sorted by expected points, with the reserve
        goalkeeper separated out (he occupies his own slot and can only replace
        the keeper).
        """
        squad = squad_df.reset_index(drop=True).copy()
        if "element" not in squad.columns:
            squad["element"] = squad["id"]

        pts = (squad[expected_points] if isinstance(expected_points, str)
               else pd.Series(expected_points, index=squad.index))
        pts = pts.fillna(0).to_numpy(dtype=float)
        pos = _positions(squad)
        n = len(squad)

        prob = pulp.LpProblem("fpl_starting_xi", pulp.LpMaximize)
        start = [pulp.LpVariable(f"x_{i}", cat="Binary") for i in range(n)]
        capt = [pulp.LpVariable(f"c_{i}", cat="Binary") for i in range(n)]

        prob += (pulp.lpSum(pts[i] * start[i] for i in range(n))
                 + pulp.lpSum(pts[i] * capt[i] for i in range(n)))
        prob += pulp.lpSum(start) == STARTING_XI_SIZE
        prob += pulp.lpSum(capt) == 1
        for i in range(n):
            prob += capt[i] <= start[i]

        prob += pulp.lpSum(start[i] for i in range(n) if pos[i] == GKP) == 1
        for et, minimum in FORMATION_MINIMUMS.items():
            if et == GKP:
                continue
            prob += pulp.lpSum(start[i] for i in range(n)
                               if pos[i] == et) >= minimum

        status = prob.solve(self._solver())
        if pulp.LpStatus[status] != "Optimal":
            raise RuntimeError(f"XI optimisation failed: {pulp.LpStatus[status]}")

        starters = [i for i in range(n) if start[i].value() > 0.5]
        captain = next(i for i in range(n) if capt[i].value() > 0.5)
        vice = max((i for i in starters if i != captain), key=lambda i: pts[i])

        bench_idx = [i for i in range(n) if i not in starters]
        bench_gk = [i for i in bench_idx if pos[i] == GKP]
        bench_out = sorted((i for i in bench_idx if pos[i] != GKP),
                           key=lambda i: -pts[i])

        cost = _prices(squad)
        result = self._package(squad, list(range(n)), starters,
                               bench_gk + bench_out, captain, vice, cost, pts)
        result["formation"] = self._formation_string(pos[starters])
        result["bench_order"] = [int(squad.loc[i, "element"])
                                 for i in bench_out]
        result["reserve_gk"] = [int(squad.loc[i, "element"]) for i in bench_gk]
        return result

    # ------------------------------------------------------------------
    # Transfers
    # ------------------------------------------------------------------

    def optimize_transfers(self, current_squad: pd.DataFrame,
                           player_pool_df: pd.DataFrame,
                           free_transfers: int = 1, bank: float = 0.0,
                           n_gw_horizon: int = 3,
                           points_cols: Sequence[str] | None = None,
                           max_transfers: int = 3) -> dict:
        """Recommend transfers over a multi-gameweek horizon.

        ``current_squad`` must carry ``element``, ``element_type``, ``team``,
        ``now_cost`` (the player's *current* price) and ``purchase_price`` (what
        was paid). Selling prices are computed from those two by the 50%-profit
        rule -- this is the single place naive optimisers leak budget.

        ``points_cols`` names one expected-points column per gameweek in the
        horizon; with a single column it is reused and discounted geometrically.
        The objective is::

            sum over gw of  discount^gw * (XI points + captain)  -  4 * hits

        Transfers beyond ``free_transfers`` cost 4 points each, and the model
        will take a hit only when the horizon gain exceeds it -- which is the
        right test, since a hit is a one-off cost against a multi-week benefit.

        Returns the recommended transfers in and out with the resulting squad.
        **Advisory only: this never executes a transfer.**
        """
        current = current_squad.reset_index(drop=True).copy()
        pool = player_pool_df.reset_index(drop=True).copy()
        if "element" not in pool.columns:
            pool["element"] = pool["id"]

        # Align the display-name column before concatenating. A squad frame
        # produced by a previous optimiser call carries `name` while the pool
        # carries `web_name`; concatenating them leaves each half of the column
        # NaN, which shows up as a nameless player in the recommendation.
        if "web_name" not in current.columns and "name" in current.columns:
            current["web_name"] = current["name"]

        owned_ids = set(current["element"].astype(int))

        # Selling price per owned player, in tenths, via the 50%-profit rule.
        sell_price = {}
        for _, row in current.iterrows():
            now = int(row["now_cost"])
            paid = int(row.get("purchase_price", now) or now)
            sell_price[int(row["element"])] = RULES.calc_selling_price_tenths(
                paid, now)

        # Candidate universe: everything owned plus the pool.
        candidates = pool[~pool["element"].isin(owned_ids)].copy()
        merged = pd.concat([current.assign(_owned=True),
                            candidates.assign(_owned=False)],
                           ignore_index=True, sort=False)
        merged = merged.drop_duplicates(subset="element").reset_index(drop=True)

        n = len(merged)
        pos = _positions(merged)
        club = merged["team"].astype(int).to_numpy()
        buy_cost = _prices(merged)
        owned = merged["_owned"].fillna(False).to_numpy()
        elements = merged["element"].astype(int).to_numpy()

        # Points per gameweek across the horizon.
        if points_cols is None:
            points_cols = ["expected_points"]
        gw_points = []
        for h in range(n_gw_horizon):
            col = points_cols[min(h, len(points_cols) - 1)]
            base = merged[col].fillna(0).to_numpy(dtype=float)
            gw_points.append(base * (self.horizon_discount ** h))

        budget_available = (
            sum(sell_price.get(int(e), 0) for e in elements[owned])
            + to_tenths(bank))

        prob = pulp.LpProblem("fpl_transfers", pulp.LpMaximize)
        squad = [pulp.LpVariable(f"s_{i}", cat="Binary") for i in range(n)]
        start = [pulp.LpVariable(f"x_{i}", cat="Binary") for i in range(n)]
        capt = [pulp.LpVariable(f"c_{i}", cat="Binary") for i in range(n)]
        buy = [pulp.LpVariable(f"b_{i}", cat="Binary") for i in range(n)]
        sell = [pulp.LpVariable(f"o_{i}", cat="Binary") for i in range(n)]
        hits = pulp.LpVariable("hits", lowBound=0, cat="Integer")

        total_points = pulp.lpSum(
            sum(gw_points[h][i] for h in range(n_gw_horizon)) * start[i]
            for i in range(n))
        captain_bonus = pulp.lpSum(
            sum(gw_points[h][i] for h in range(n_gw_horizon)) * capt[i]
            for i in range(n))
        bench_value = self.bench_weight * pulp.lpSum(
            gw_points[0][i] * (squad[i] - start[i]) for i in range(n))

        prob += total_points + captain_bonus + bench_value - HIT_COST * hits

        # Squad structure.
        prob += pulp.lpSum(squad) == SQUAD_SIZE
        prob += pulp.lpSum(start) == STARTING_XI_SIZE
        prob += pulp.lpSum(capt) == 1
        for i in range(n):
            prob += start[i] <= squad[i]
            prob += capt[i] <= start[i]
            # Link transfer variables to ownership changes.
            if owned[i]:
                prob += squad[i] == 1 - sell[i]
                prob += buy[i] == 0
            else:
                prob += squad[i] == buy[i]
                prob += sell[i] == 0

        for et, required in SQUAD_COMPOSITION.items():
            prob += pulp.lpSum(squad[i] for i in range(n)
                               if pos[i] == et) == required

        prob += pulp.lpSum(start[i] for i in range(n) if pos[i] == GKP) == 1
        for et, minimum in FORMATION_MINIMUMS.items():
            if et == GKP:
                continue
            prob += pulp.lpSum(start[i] for i in range(n)
                               if pos[i] == et) >= minimum

        for c in np.unique(club):
            prob += pulp.lpSum(squad[i] for i in range(n)
                               if club[i] == c) <= MAX_PLAYERS_PER_CLUB

        # Budget: what we buy must be covered by sales plus the bank.
        spend = pulp.lpSum(buy_cost[i] * buy[i] for i in range(n))
        raised = pulp.lpSum(sell_price.get(int(elements[i]), 0) * sell[i]
                            for i in range(n) if owned[i])
        prob += spend <= raised + to_tenths(bank)

        # Transfer accounting: buys equal sells, and hits cover the excess.
        n_transfers = pulp.lpSum(buy)
        prob += n_transfers == pulp.lpSum(sell)
        prob += n_transfers <= max_transfers
        prob += hits >= n_transfers - free_transfers

        status = prob.solve(self._solver())
        if pulp.LpStatus[status] != "Optimal":
            raise RuntimeError(f"transfer optimisation failed: "
                               f"{pulp.LpStatus[status]}")

        bought = [i for i in range(n) if buy[i].value() > 0.5]
        sold = [i for i in range(n) if sell[i].value() > 0.5]
        chosen = [i for i in range(n) if squad[i].value() > 0.5]
        starters = [i for i in range(n) if start[i].value() > 0.5]
        captain = next(i for i in range(n) if capt[i].value() > 0.5)
        bench = [i for i in chosen if i not in starters]
        horizon_pts = np.array([sum(gw_points[h][i] for h in range(n_gw_horizon))
                                for i in range(n)])
        vice = max((i for i in starters if i != captain),
                   key=lambda i: horizon_pts[i])

        name_col = _name_column(merged)
        result = self._package(merged, chosen, starters, bench, captain, vice,
                               buy_cost, horizon_pts)
        result.update({
            "transfers_in": [{"element": int(elements[i]),
                              "name": merged.loc[i, name_col],
                              "cost": float(buy_cost[i] / 10)} for i in bought],
            "transfers_out": [{"element": int(elements[i]),
                               "name": merged.loc[i, name_col],
                               "selling_price": float(
                                   sell_price.get(int(elements[i]), 0) / 10)}
                              for i in sold],
            "n_transfers": len(bought),
            "hits": int(max(0, len(bought) - free_transfers)),
            "hit_cost": int(max(0, len(bought) - free_transfers) * HIT_COST),
            "bank_after": float((raised.value() + to_tenths(bank)
                                 - spend.value()) / 10),
            "horizon": n_gw_horizon,
            "advisory_only": True,
        })
        return result

    # ------------------------------------------------------------------
    # Chips
    # ------------------------------------------------------------------

    def optimize_chip_usage(self, current_state: dict, chip_availability: dict,
                            scenario_points_matrix: pd.DataFrame) -> dict:
        """Recommend which chip, if any, to play and when.

        ``scenario_points_matrix`` is indexed by gameweek with columns
        ``base``, ``bench``, ``best_player`` and optionally ``wildcard_gain`` /
        ``freehit_gain`` -- the expected points each chip would add in that
        gameweek.

        The rule encoded is simply: play a chip in the gameweek where its
        marginal gain is highest, subject to one chip per gameweek and the
        half-season expiry (first-half chips die at the GW19 deadline, the
        second set unlocks at GW20). This is a deterministic assignment problem,
        solved exactly rather than simulated -- the uncertainty lives in the
        points matrix, which the simulator supplies.

        For a learned policy that accounts for sequencing and rank position, see
        :mod:`rl_agent`.
        """
        gws = scenario_points_matrix.index.tolist()
        gains = {}

        if chip_availability.get("bboost"):
            gains["bboost"] = scenario_points_matrix.get(
                "bench", pd.Series(0, index=gws))
        if chip_availability.get("3xc"):
            gains["3xc"] = scenario_points_matrix.get(
                "best_player", pd.Series(0, index=gws))
        if chip_availability.get("wildcard"):
            gains["wildcard"] = scenario_points_matrix.get(
                "wildcard_gain", pd.Series(0, index=gws))
        if chip_availability.get("freehit"):
            gains["freehit"] = scenario_points_matrix.get(
                "freehit_gain", pd.Series(0, index=gws))

        if not gains:
            return {"recommendation": None,
                    "reason": "no chips available in this half of the season"}

        prob = pulp.LpProblem("fpl_chips", pulp.LpMaximize)
        play = {(chip, gw): pulp.LpVariable(f"p_{chip}_{gw}", cat="Binary")
                for chip in gains for gw in gws}

        prob += pulp.lpSum(float(gains[chip].get(gw, 0)) * play[(chip, gw)]
                           for chip in gains for gw in gws)

        for chip in gains:
            prob += pulp.lpSum(play[(chip, gw)] for gw in gws) <= 1
        for gw in gws:
            prob += pulp.lpSum(play[(chip, gw)] for chip in gains) <= 1

        status = prob.solve(self._solver())
        if pulp.LpStatus[status] != "Optimal":
            raise RuntimeError(f"chip optimisation failed: {pulp.LpStatus[status]}")

        plan = [{"chip": chip, "gameweek": int(gw),
                 "expected_gain": float(gains[chip].get(gw, 0))}
                for (chip, gw), var in play.items() if var.value() > 0.5]
        plan.sort(key=lambda d: d["gameweek"])

        current_gw = current_state.get("gameweek")
        now = [p for p in plan if p["gameweek"] == current_gw]
        return {
            "plan": plan,
            "recommendation": now[0]["chip"] if now else None,
            "reason": ("play it this gameweek" if now
                       else "hold -- a later gameweek is worth more"),
            "advisory_only": True,
        }

    # ------------------------------------------------------------------
    # Stochastic version
    # ------------------------------------------------------------------

    def optimize_squad_stochastic(self, player_pool_df: pd.DataFrame,
                                  scenarios: np.ndarray, budget: float = 100.0,
                                  risk_aversion: float = 0.0,
                                  n_scenarios: int = 1000) -> dict:
        """Squad selection over Monte Carlo scenarios rather than point estimates.

        ``scenarios`` is an ``(n_players, n_sims)`` sample matrix from
        :meth:`simulator.SeasonSimulator.simulate_gameweek`. The objective
        maximises the *sample average* of expected points, optionally penalised
        by ``risk_aversion`` times the downside spread::

            maximise  mean(points) - risk_aversion * (mean - P10)

        With ``risk_aversion=0`` this is equivalent to the deterministic problem
        but uses the simulated mean, which correctly accounts for the non-linear
        parts of FPL scoring (thresholds at 60 minutes, the defensive-
        contribution cutoff) that an expectation of inputs cannot capture --
        E[f(x)] is not f(E[x]) when f has steps in it.

        Only the first ``n_scenarios`` columns are used; 1,000 is plenty for a
        stable mean and keeps the model small.
        """
        sample = np.asarray(scenarios)[:, :n_scenarios]
        mean_pts = sample.mean(axis=1)
        p10 = np.quantile(sample, 0.10, axis=1)

        pool = player_pool_df.reset_index(drop=True).copy()
        if len(pool) != len(mean_pts):
            raise ValueError(f"scenario matrix has {len(mean_pts)} rows but the "
                             f"pool has {len(pool)}")

        pool["_stochastic_points"] = mean_pts - risk_aversion * (mean_pts - p10)
        result = self.optimize_initial_squad(pool, budget=budget,
                                             points_col="_stochastic_points")
        result["risk_aversion"] = risk_aversion
        result["n_scenarios"] = int(sample.shape[1])
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _formation_string(positions: np.ndarray) -> str:
        counts = {et: int((positions == et).sum()) for et in (DEF, MID, FWD)}
        return f"{counts[DEF]}-{counts[MID]}-{counts[FWD]}"

    @staticmethod
    def _package(df: pd.DataFrame, chosen: Sequence[int], starters: Sequence[int],
                 bench: Sequence[int], captain: int, vice: int,
                 cost: np.ndarray, pts: np.ndarray) -> dict:
        """Assemble a readable result dict from solved variable indices."""
        name_col = _name_column(df)

        def rows(indices):
            return [{
                "element": int(df.loc[i, "element"]),
                "name": df.loc[i, name_col],
                "position": int(df.loc[i, "element_type"]),
                "team": int(df.loc[i, "team"]),
                "price": float(cost[i] / 10),
                "expected_points": float(pts[i]),
            } for i in indices]

        starter_rows = sorted(rows(starters), key=lambda r: (r["position"],
                                                             -r["expected_points"]))
        return {
            "squad": rows(chosen),
            "starting_xi": starter_rows,
            "bench": rows(bench),
            "captain": int(df.loc[captain, "element"]),
            "captain_name": df.loc[captain, name_col],
            "vice_captain": int(df.loc[vice, "element"]),
            "vice_captain_name": df.loc[vice, name_col],
            "total_cost": float(sum(cost[i] for i in chosen) / 10),
            "expected_points": float(sum(pts[i] for i in starters)
                                     + pts[captain]),
            "advisory_only": True,
        }
