"""
Rolling-horizon FPL season planner — Module 6.

Takes the per-player, per-GW expected-points table from
:mod:`season_forecaster` and solves a multi-period mixed-integer programme to
find the best squad, transfers, captains and chip timing over the next
:attr:`SeasonPlanner.horizon` gameweeks.

Only the **first gameweek action is committed**. Re-run after each gameweek
with updated information to get the next action (rolling-horizon principle).

MILP design
-----------
Variables (per candidate *i*, per gameweek *t* in the horizon):

  squad[i,t]   binary  player i in 15-man squad at GW t
  start[i,t]   binary  player i in starting XI at GW t
  capt[i,t]    binary  player i is captain at GW t
  buy[i,t]     binary  player i transferred in at GW t
  sell[i,t]    binary  player i transferred out at GW t

State variables (per gameweek):

  ft_var[t]    integer ∈ [1, MAX_BANKED_FT]  free transfers available
  hit_var[t]   integer ≥ 0                   transfers beyond FT (hits)
  bank[t]      continuous ≥ 0                money in bank (tenths £m)

Objective:

  Σ_t  decay^(t-1) * [
      Σ_i xpts[i,t] * start[i,t]
    + Σ_i xpts[i,t] * capt[i,t]        (captain double counts twice)
    + bench_weight * Σ_i xpts[i,t] * (squad[i,t] - start[i,t])
    - hit_cost * hit_var[t]
    + ft_value * ft_var[t+1]            (value of banked FTs)
  ]

Selling-price rule
------------------
Players already owned carry their actual selling price (50%-profit rule).
Players bought within the horizon are re-sold at their purchase price (no
intra-season price modelling in v1, per the research recommendation).

Solver
------
Uses the same :func:`get_solver` probe from :mod:`optimizer` (HiGHS → CBC
fallback). The candidate pool is pre-filtered to ≤ 150 players, keeping the
model small enough to solve in seconds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd
import pulp

from .fpl_rules import (
    DEF, FWD, GKP, MAX_BANKED_FT, MAX_PLAYERS_PER_CLUB, MID,
    FORMATION_MINIMUMS, HIT_COST, RULES, SQUAD_COMPOSITION, SQUAD_SIZE,
    STARTING_XI_SIZE, to_millions, to_tenths,
)
from .optimizer import get_solver

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_HORIZON = 6
DEFAULT_DECAY = 0.85
DEFAULT_BENCH_WEIGHT = 0.10
DEFAULT_FT_VALUE = 1.5   # extra expected points credited per banked FT
CAPTAIN_EXTRA = 1.0      # (cap multiplier - 1) = 1; captain earns xpts twice


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

@dataclass
class SeasonPlanner:
    """Rolling-horizon MILP season planner.

    Parameters
    ----------
    horizon:
        Number of gameweeks to plan ahead. Only GW 1 of the plan is acted on.
    decay:
        Discount factor per gameweek. GW k's xPts is weighted decay^(k-1).
    bench_weight:
        Fraction of expected points credited to bench players in the objective.
    ft_value:
        Expected-points credit per additional banked free transfer at end of
        horizon. Prevents the solver from wasting FTs on sideways transfers.
    time_limit:
        Solver time limit in seconds.
    """

    horizon: int = DEFAULT_HORIZON
    decay: float = DEFAULT_DECAY
    bench_weight: float = DEFAULT_BENCH_WEIGHT
    ft_value: float = DEFAULT_FT_VALUE
    solver_msg: bool = False
    time_limit: int = 120

    def plan(
        self,
        forecasts: pd.DataFrame,
        current_state: dict[str, Any],
        *,
        unlimited_transfers_current_gw: bool = False,
        max_current_gw_hits: int | None = None,
        forbidden_current_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        """Solve the multi-GW MILP and return a full season plan.

        Parameters
        ----------
        forecasts:
            Output of :meth:`SeasonForecaster.forecast`. Must have columns
            ``element``, ``gw``, ``xpts``, ``position``, ``team``,
            ``now_cost``, ``name``.
        current_state:
            Dict with keys:
              ``gameweek``       int   first GW to plan from
              ``squad``          list[int]   element IDs currently owned
              ``selling_prices`` dict[int, int]  {element_id: sell_price_tenths}
              ``bank``           int   bank balance in tenths of £m
              ``ft``             int   free transfers available this GW
              ``chips_available``  list[str]   chips not yet used this half

        Returns
        -------
        dict
            ``gw_plan``       list of per-GW dicts (transfers, captain, XI)
            ``transfers_in``  list for GW 1 (immediate action)
            ``transfers_out`` list for GW 1 (immediate action)
            ``chip``          chip recommendation for GW 1 (or None)
            ``report_table``  pd.DataFrame  human-readable GW summary
            ``advisory_only`` True
        """
        gw0 = int(current_state["gameweek"])
        squad_ids = set(current_state.get("squad", []))
        sell_prices: dict[int, int] = {
            int(k): int(v) for k, v in current_state.get("selling_prices", {}).items()
        }
        bank0 = int(current_state.get("bank", 0))
        ft0 = min(MAX_BANKED_FT, max(1, int(current_state.get("ft", 1))))
        forbidden_current_ids = {
            int(element) for element in (forbidden_current_ids or set())
        }

        # Initial squad selection: unlimited free transfers at GW1 (no hits).
        initial_squad = len(squad_ids) == 0

        gws = list(range(gw0, gw0 + self.horizon))
        # Build pivot: rows=element, cols=gw, values=xpts.
        pivot = (
            forecasts[forecasts["gw"].isin(gws)]
            .pivot_table(index="element", columns="gw", values="xpts", fill_value=0.0)
        )
        # Player metadata (one row per element, take first occurrence).
        meta = (
            forecasts[forecasts["gw"] == gw0]
            .drop_duplicates("element")
            .set_index("element")
        )
        # Align pivot and meta to the same elements.
        elements = pivot.index.tolist()
        meta = meta.reindex(elements)

        n = len(elements)
        T = len(gws)

        if n == 0:
            raise ValueError("forecast table is empty after filtering")

        # ---------------------------------------------------------------
        # Build parameter arrays (indexed by player position in `elements`).
        # ---------------------------------------------------------------
        xpts = np.zeros((n, T), dtype=float)
        for ti, gw in enumerate(gws):
            if gw in pivot.columns:
                xpts[:, ti] = pivot[gw].to_numpy()

        costs = meta["now_cost"].fillna(0).astype(int).to_numpy()  # tenths
        pos = meta["position"].fillna(3).astype(int).to_numpy()
        club = meta["team"].fillna(0).astype(int).to_numpy()
        names = meta["name"].fillna("?").tolist()

        # Selling prices: use known sell price for currently owned, else buy price.
        sell_price_arr = np.array([
            sell_prices.get(eid, costs[i]) for i, eid in enumerate(elements)
        ], dtype=int)

        # Binary mask: 1 if player is currently in squad.
        owned = np.array([1 if eid in squad_ids else 0 for eid in elements], dtype=int)

        # ---------------------------------------------------------------
        # Build MILP.
        # ---------------------------------------------------------------
        prob = pulp.LpProblem("fpl_season_plan", pulp.LpMaximize)

        # Binary variables.
        sq = [[pulp.LpVariable(f"sq_{i}_{t}", cat="Binary")
               for t in range(T)] for i in range(n)]
        st = [[pulp.LpVariable(f"st_{i}_{t}", cat="Binary")
               for t in range(T)] for i in range(n)]
        cp = [[pulp.LpVariable(f"cp_{i}_{t}", cat="Binary")
               for t in range(T)] for i in range(n)]
        buy = [[pulp.LpVariable(f"buy_{i}_{t}", cat="Binary")
                for t in range(T)] for i in range(n)]
        sell = [[pulp.LpVariable(f"sell_{i}_{t}", cat="Binary")
                 for t in range(T)] for i in range(n)]

        # State variables.
        ft_var = [pulp.LpVariable(f"ft_{t}", lowBound=1, upBound=MAX_BANKED_FT,
                                   cat="Integer") for t in range(T + 1)]
        hit_var = [pulp.LpVariable(f"hit_{t}", lowBound=0, cat="Integer")
                   for t in range(T)]
        bank_var = [pulp.LpVariable(f"bank_{t}", lowBound=0, cat="Continuous")
                    for t in range(T + 1)]

        # Initial state.
        prob += ft_var[0] == ft0
        prob += bank_var[0] == bank0

        # ---------------------------------------------------------------
        # Objective.
        # ---------------------------------------------------------------
        decay_weights = [self.decay ** t for t in range(T)]
        obj = pulp.lpSum(
            decay_weights[t] * (
                pulp.lpSum(xpts[i, t] * st[i][t] for i in range(n))
                + pulp.lpSum(xpts[i, t] * cp[i][t] for i in range(n))  # captain extra
                + self.bench_weight * pulp.lpSum(
                    xpts[i, t] * (sq[i][t] - st[i][t]) for i in range(n))
                - HIT_COST * hit_var[t]
            )
            for t in range(T)
        )
        # Bonus: value of banked FTs at end of horizon.
        obj += self.ft_value * ft_var[T]
        prob += obj

        # ---------------------------------------------------------------
        # Constraints.
        # ---------------------------------------------------------------
        for t in range(T):
            n_trans = pulp.lpSum(buy[i][t] for i in range(n))

            # --- Squad composition ---
            prob += pulp.lpSum(sq[i][t] for i in range(n)) == SQUAD_SIZE
            for et, required in SQUAD_COMPOSITION.items():
                prob += pulp.lpSum(sq[i][t] for i in range(n) if pos[i] == et) == required

            # --- Club limit ---
            for c in np.unique(club):
                prob += (pulp.lpSum(sq[i][t] for i in range(n) if club[i] == c)
                         <= MAX_PLAYERS_PER_CLUB)

            # --- Starting XI ---
            prob += pulp.lpSum(st[i][t] for i in range(n)) == STARTING_XI_SIZE
            prob += pulp.lpSum(st[i][t] for i in range(n) if pos[i] == GKP) == 1
            for et, minimum in FORMATION_MINIMUMS.items():
                if et == GKP:
                    continue
                prob += pulp.lpSum(st[i][t] for i in range(n) if pos[i] == et) >= minimum

            # --- Captain: exactly 1 starter. FPL permits goalkeepers too. ---
            prob += pulp.lpSum(cp[i][t] for i in range(n)) == 1
            for i in range(n):
                prob += st[i][t] <= sq[i][t]
                prob += cp[i][t] <= st[i][t]

            # --- Transfer continuity ---
            for i in range(n):
                prev = owned[i] if t == 0 else sq[i][t - 1]
                prob += sq[i][t] == prev + buy[i][t] - sell[i][t]
                # Cannot buy and sell the same player in the same GW.
                prob += buy[i][t] + sell[i][t] <= 1
                if elements[i] in forbidden_current_ids:
                    prob += sq[i][t] == 0

            # --- Free transfers and hits ---
            # hit_var[t] counts transfers beyond the FT allowance.
            # Initial squad selection is free (unlimited FTs for GW1).
            if t == 0 and (initial_squad or unlimited_transfers_current_gw):
                prob += hit_var[t] == 0
            else:
                prob += hit_var[t] >= n_trans - ft_var[t]
                if t == 0 and max_current_gw_hits is not None:
                    prob += hit_var[t] <= max(0, int(max_current_gw_hits))

            # --- FT rolling: ft_var[t+1] = min(5, ft_var[t] - n_trans + hit_var[t] + 1)
            # For the initial squad GW (no existing squad), all 15 buys are free;
            # FT carryover into GW2 is simply 1 (fresh start).
            if t == 0 and initial_squad:
                prob += ft_var[t + 1] == 1
            elif t == 0 and unlimited_transfers_current_gw:
                # Current FPL chip rules preserve banked free transfers when a
                # Wildcard is played; the unlimited rebuild does not spend or
                # add one.
                prob += ft_var[t + 1] == ft_var[t]
            else:
                prob += ft_var[t + 1] <= ft_var[t] - n_trans + hit_var[t] + 1
                prob += ft_var[t + 1] >= 1  # redundant with variable bound, explicit for clarity

            # --- Budget ---
            spend = pulp.lpSum(costs[i] * buy[i][t] for i in range(n))
            raise_ = pulp.lpSum(sell_price_arr[i] * sell[i][t] for i in range(n))
            prob += bank_var[t + 1] == bank_var[t] - spend + raise_

        # ---------------------------------------------------------------
        # Solve.
        # ---------------------------------------------------------------
        solver = get_solver(msg=self.solver_msg, time_limit=self.time_limit)
        status = prob.solve(solver)
        if pulp.LpStatus[status] != "Optimal":
            raise RuntimeError(f"season planner: solver returned {pulp.LpStatus[status]}")

        # ---------------------------------------------------------------
        # Extract results.
        # ---------------------------------------------------------------
        result = self._extract(
            prob, elements, names, pos, club, costs, sell_price_arr,
            xpts, sq, st, cp, buy, sell, ft_var, hit_var, bank_var,
            gws, owned,
        )
        result["transfer_plan_kind"] = (
            "wildcard_rebuild" if unlimited_transfers_current_gw else "ordinary"
        )
        result["max_current_gw_hits"] = max_current_gw_hits
        return result

    # ------------------------------------------------------------------
    # Result extraction
    # ------------------------------------------------------------------

    def _extract(
        self, prob, elements, names, pos, club, costs, sell_price_arr,
        xpts, sq, st, cp, buy, sell, ft_var, hit_var, bank_var,
        gws, owned,
    ) -> dict[str, Any]:
        n = len(elements)
        T = len(gws)

        gw_plan: list[dict] = []
        for t, gw in enumerate(gws):
            squad_idx = [i for i in range(n) if _val(sq[i][t]) > 0.5]
            start_idx = [i for i in range(n) if _val(st[i][t]) > 0.5]
            capt_idx = next(i for i in range(n) if _val(cp[i][t]) > 0.5)
            # Stable position-first ordering makes each displayed/API transfer
            # pair like-for-like instead of zipping unrelated GK/DEF/MID/FWD
            # changes that merely balance at whole-squad level.
            buy_idx = sorted(
                [i for i in range(n) if _val(buy[i][t]) > 0.5],
                key=lambda i: (pos[i], elements[i]),
            )
            sell_idx = sorted(
                [i for i in range(n) if _val(sell[i][t]) > 0.5],
                key=lambda i: (pos[i], elements[i]),
            )
            bench_idx = [i for i in squad_idx if i not in start_idx]

            vice_candidates = [i for i in start_idx if i != capt_idx and pos[i] != GKP]
            if not vice_candidates:
                vice_candidates = [i for i in start_idx if i != capt_idx]
            vice_idx = max(vice_candidates, key=lambda i: xpts[i, t], default=capt_idx)

            xi_xpts = sum(xpts[i, t] for i in start_idx)
            bench_xpts = sum(xpts[i, t] for i in bench_idx)
            cap_extra = xpts[capt_idx, t]  # extra from captain doubling

            gw_plan.append({
                "gw": gw,
                "squad": [_player(elements, names, costs, pos, i) for i in squad_idx],
                "starting_xi": sorted(
                    [_player(elements, names, costs, pos, i) for i in start_idx],
                    key=lambda p: (p["position"], -xpts[start_idx[0], t] if start_idx else 0),
                ),
                "bench": [
                    {
                        **_player(elements, names, costs, pos, i),
                        "xpts": round(float(xpts[i, t]), 2),
                    }
                    for i in bench_idx
                ],
                "captain": {"element": elements[capt_idx], "name": names[capt_idx],
                            "xpts": round(xpts[capt_idx, t], 2)},
                "vice": {"element": elements[vice_idx], "name": names[vice_idx],
                         "xpts": round(xpts[vice_idx, t], 2)},
                "transfers_in": [{"element": elements[i], "name": names[i],
                                   "cost": to_millions(costs[i]),
                                   "position": int(pos[i])} for i in buy_idx],
                "transfers_out": [{"element": elements[i], "name": names[i],
                                    "selling_price": to_millions(sell_price_arr[i]),
                                    "position": int(pos[i])}
                                   for i in sell_idx],
                "n_transfers": len(buy_idx),
                "hits": int(round(_val(hit_var[t]))),
                "hit_cost": int(round(_val(hit_var[t]))) * HIT_COST,
                "ft_available": int(round(_val(ft_var[t]))),
                "ft_banked_next": int(round(_val(ft_var[t + 1]))),
                "bank_after": to_millions(int(round(_val(bank_var[t + 1])))),
                "xi_xpts": round(xi_xpts + cap_extra, 2),
                "bench_xpts": round(bench_xpts, 2),
            })

        # Build the report table. "Initial squad" is only correct when
        # the planner truly started from no owned players; an established team
        # with zero GW1 transfers is a Roll, not an initial-selection event.
        report_rows = []
        was_initial_squad = int(sum(owned)) == 0
        for g in gw_plan:
            trans_str = (
                "Initial squad" if g["gw"] == gws[0] and was_initial_squad
                else (", ".join(
                    f"{t['name']} ← {o['name']}"
                    for t, o in zip(g["transfers_in"], g["transfers_out"])
                ) or "Roll")
            )
            chip_str = g.get("chip", "—")
            report_rows.append({
                "GW": g["gw"],
                "Transfers": trans_str,
                "Chip": chip_str if chip_str else "—",
                "Captain": g["captain"]["name"],
                "Vice": g["vice"]["name"],
                "XI xPts": g["xi_xpts"],
                "Bench xPts": g["bench_xpts"],
                "Hits": g["hits"],
                "FT→": g["ft_banked_next"],
                "Bank": f"£{g['bank_after']}m",
            })

        report_table = pd.DataFrame(report_rows)

        # First GW action (the committed recommendation).
        first = gw_plan[0]
        return {
            "gw_plan": gw_plan,
            "transfers_in": first["transfers_in"],
            "transfers_out": first["transfers_out"],
            "chip": first.get("chip"),
            "captain": first["captain"],
            "vice": first["vice"],
            "hits": first["hits"],
            "hit_cost": first["hit_cost"],
            "ft_banked_next": first["ft_banked_next"],
            "bank_after": first["bank_after"],
            "report_table": report_table,
            "advisory_only": True,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _val(v) -> float:
    """Extract PuLP variable value safely."""
    return pulp.value(v) or 0.0


def _player(elements, names, costs, pos, i) -> dict:
    return {
        "element": elements[i],
        "name": names[i],
        "cost": to_millions(costs[i]),
        "position": pos[i],
    }
