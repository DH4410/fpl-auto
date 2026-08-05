"""
Chip evaluation and timing — Module 7.

Computes the marginal expected-points gain of each chip in each gameweek and
recommends when to play them, subject to the 2026/27 rules:

  * One chip per gameweek.
  * Each manager has one Wildcard, Free Hit, Triple Captain and Bench Boost
    per *half* of the season (GW1-19 and GW20-38 — 8 chips in total).
  * First-half chips that are unused at the GW19 deadline do not carry over;
    second-half chips unlock at GW20.

This module is deterministic: it scores each chip against expected values from
the forecast table and solves a small LP to assign chips to gameweeks. For a
stochastic chip policy (useful for Bench Boost / Triple Captain around uncertain
double/blank gameweeks), the rl_agent module handles that after the fixture
schedule is clearer.

Integration
-----------
:meth:`ChipPlanner.evaluate` is called by :mod:`updater` after
:mod:`season_planner` has run. The chip recommendations are appended to the
planner's ``gw_plan`` output and to the markdown report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd
import pulp

from .fpl_rules import (
    CHIP_BENCH_BOOST, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN, CHIP_WILDCARD,
    CHIP_LABELS, chip_half, FIRST_HALF_GWS, SECOND_HALF_GWS,
    MAX_CHIPS_PER_GW, HIT_COST,
)
from .optimizer import get_solver

log = logging.getLogger(__name__)


@dataclass
class ChipPlanner:
    """Evaluate and schedule chips over a planning horizon.

    Parameters
    ----------
    captain_multiplier_base:
        Normal captain multiplier (2). Triple Captain raises it to 3, so
        the marginal gain is the captain's xPts × 1.
    bench_weight_boost:
        Fraction of bench xPts credited when Bench Boost is active. Set to
        1.0 because Bench Boost makes all four bench players score fully.
    wildcard_gain_premium:
        Multiplier applied to the best-squad improvement estimate for Wildcard.
        Accounts for the fact that a Wildcard lets you restructure fully; the
        estimate here is conservative.
    """

    captain_multiplier_base: int = 2
    bench_weight_boost: float = 1.0
    wildcard_gain_premium: float = 1.2
    solver_msg: bool = False
    time_limit: int = 30

    def evaluate(
        self,
        forecasts: pd.DataFrame,
        gw_plan: list[dict],
        chips_available: list[str],
        current_gw: int,
        current_half: int,
    ) -> dict[str, Any]:
        """Recommend chip timing over the planning horizon.

        Parameters
        ----------
        forecasts:
            Full forecast DataFrame from :mod:`season_forecaster`.
        gw_plan:
            Per-GW plan from :mod:`season_planner`.
        chips_available:
            List of chip codes (e.g. ``["wildcard", "3xc"]``) not yet used
            this half.
        current_gw:
            First GW in the planning horizon.
        current_half:
            1 (GW1-19) or 2 (GW20-38).

        Returns
        -------
        dict
            ``chip_plan``     list of {chip, gw, expected_gain} sorted by GW
            ``recommendation`` chip code for current_gw, or None
            ``reason``        one-sentence explanation
            ``advisory_only`` True
        """
        if not chips_available:
            return {
                "chip_plan": [],
                "recommendation": None,
                "reason": "No chips available in this half of the season.",
                "advisory_only": True,
            }

        valid_gws = list(FIRST_HALF_GWS if current_half == 1 else SECOND_HALF_GWS)
        plan_gws = [g["gw"] for g in gw_plan]
        # Restrict to GWs within both the plan horizon and the valid chip window.
        candidate_gws = [gw for gw in plan_gws if gw in valid_gws]
        if not candidate_gws:
            return {
                "chip_plan": [],
                "recommendation": None,
                "reason": "No valid chip gameweeks within planning horizon.",
                "advisory_only": True,
            }

        # Build gain estimates per chip per GW.
        gains: dict[str, dict[int, float]] = {}
        for chip in chips_available:
            chip_gains: dict[int, float] = {}
            for gw_dict in gw_plan:
                gw = gw_dict["gw"]
                if gw not in candidate_gws:
                    continue
                chip_gains[gw] = self._estimate_gain(chip, gw_dict, forecasts, gw)
            if chip_gains:
                gains[chip] = chip_gains

        if not gains:
            return {
                "chip_plan": [],
                "recommendation": None,
                "reason": "Could not estimate chip gains for any valid gameweek.",
                "advisory_only": True,
            }

        # Small LP: assign at most 1 chip per GW, at most 1 per chip type.
        prob = pulp.LpProblem("fpl_chips", pulp.LpMaximize)
        play = {
            (chip, gw): pulp.LpVariable(f"p_{chip}_{gw}", cat="Binary")
            for chip, gw_gains in gains.items()
            for gw in gw_gains
        }
        prob += pulp.lpSum(
            gains[chip][gw] * play[(chip, gw)]
            for (chip, gw) in play
        )
        for chip in gains:
            prob += pulp.lpSum(play[(chip, gw)] for gw in gains[chip]) <= 1
        for gw in candidate_gws:
            prob += pulp.lpSum(
                play[(chip, gw)] for chip in gains if gw in gains[chip]
            ) <= MAX_CHIPS_PER_GW

        solver = get_solver(msg=self.solver_msg, time_limit=self.time_limit)
        status = prob.solve(solver)
        if pulp.LpStatus[status] != "Optimal":
            log.warning("chip LP did not solve optimally: %s", pulp.LpStatus[status])

        chip_plan = sorted(
            [
                {"chip": chip, "gw": gw,
                 "chip_label": CHIP_LABELS.get(chip, chip),
                 "expected_gain": round(gains[chip].get(gw, 0.0), 2)}
                for (chip, gw), var in play.items()
                if (pulp.value(var) or 0.0) > 0.5
            ],
            key=lambda d: d["gw"],
        )

        now = [p for p in chip_plan if p["gw"] == current_gw]
        rec = now[0]["chip"] if now else None
        reason = (
            f"Play {CHIP_LABELS.get(rec, rec)} this GW "
            f"(est. +{now[0]['expected_gain']:.1f} pts)." if rec
            else "Hold all chips — a later gameweek scores higher."
        )
        return {
            "chip_plan": chip_plan,
            "recommendation": rec,
            "reason": reason,
            "advisory_only": True,
        }

    # ------------------------------------------------------------------
    # Gain estimates (deterministic, single-GW)
    # ------------------------------------------------------------------

    def _estimate_gain(
        self, chip: str, gw_dict: dict, forecasts: pd.DataFrame, gw: int
    ) -> float:
        if chip == CHIP_TRIPLE_CAPTAIN:
            return self._tc_gain(gw_dict)
        if chip == CHIP_BENCH_BOOST:
            return self._bb_gain(gw_dict)
        if chip == CHIP_FREE_HIT:
            return self._fh_gain(gw_dict, forecasts, gw)
        if chip == CHIP_WILDCARD:
            return self._wc_gain(gw_dict)
        return 0.0

    @staticmethod
    def _tc_gain(gw_dict: dict) -> float:
        """Extra points from Triple Captain = captain's xPts × 1 (gains 1× extra)."""
        cap_xpts = gw_dict.get("captain", {}).get("xpts", 0.0)
        return float(cap_xpts)

    @staticmethod
    def _bb_gain(gw_dict: dict) -> float:
        """Bench Boost gain = full bench xPts (they normally score 0 in objective)."""
        return float(gw_dict.get("bench_xpts", 0.0))

    def _fh_gain(self, gw_dict: dict, forecasts: pd.DataFrame, gw: int) -> float:
        """Free Hit gain: difference between best possible XI xPts and current plan.

        Best possible XI for this GW = top 11 by xPts (unrestricted squad).
        This ignores budget constraints, making it an upper bound. It still
        usefully identifies blank gameweeks where a Free Hit adds >10 points.
        """
        gw_fc = forecasts[forecasts["gw"] == gw].copy()
        if gw_fc.empty:
            return 0.0
        gw_fc_sorted = gw_fc.nlargest(11, "xpts")
        best_xi = gw_fc_sorted["xpts"].sum()
        current_xi = float(gw_dict.get("xi_xpts", 0.0))
        return max(0.0, best_xi - current_xi)

    def _wc_gain(self, gw_dict: dict) -> float:
        """Wildcard gain: roughly the squad restructuring benefit.

        Approximated as the proportion of the plan horizon's value that the
        Wildcard unlocks by allowing unlimited free transfers. For the first
        implementation, this is a heuristic based on hit cost savings.
        """
        hits = float(gw_dict.get("hits", 0))
        xi_xpts = float(gw_dict.get("xi_xpts", 0.0))
        # Wildcard value ≈ hit savings + a small structural premium.
        hit_saving = hits * HIT_COST
        return (hit_saving + xi_xpts * 0.05) * self.wildcard_gain_premium
