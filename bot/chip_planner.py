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
    # Per-chip minimum expected gain before the planner recommends playing:
    # Normal-GW captain xPts ≈ 3.5-5. DGW captain ≈ 7-12. Require 6+ for TC.
    # Normal-GW bench xPts ≈ 9-10. Standout-fixture GW ≈ 11-12. DGW ≈ 15-22.
    # 11.0 allows BB in genuinely great fixture weeks (not just DGWs), while
    # blocking ordinary weeks where bench typically scores 9-10 pts.
    min_tc_gain: float = 6.0
    min_bb_gain: float = 11.0
    min_fh_gain: float = float(HIT_COST)
    min_wc_gain: float = float(HIT_COST)
    # Soft expiry policy: thresholds may ease as a half closes, but never by
    # more than 30% and never below a sensible per-chip floor. This prevents
    # "panic chip" behaviour while still avoiding pointless hoarding.
    expiry_max_discount: float = 0.30
    # Small synergy preference for a prepared Wildcard -> Bench Boost sequence.
    # It can break a close tie, but it cannot outweigh a clearly better
    # standalone chip week.
    wc_bb_combo_bonus: float = 1.5

    @staticmethod
    def _half_end(current_half: int) -> int:
        return 19 if int(current_half) == 1 else 38

    def _base_min_gain(self, chip: str) -> float:
        if chip == CHIP_TRIPLE_CAPTAIN:
            return float(self.min_tc_gain)
        if chip == CHIP_BENCH_BOOST:
            return float(self.min_bb_gain)
        if chip == CHIP_FREE_HIT:
            return float(self.min_fh_gain)
        if chip == CHIP_WILDCARD:
            return float(self.min_wc_gain)
        return float(HIT_COST)

    def _minimum_floor(self, chip: str) -> float:
        # Expiry should make the bot less fussy, not reckless.
        if chip == CHIP_TRIPLE_CAPTAIN:
            return max(5.0, self.min_tc_gain * 0.75)
        if chip == CHIP_BENCH_BOOST:
            return max(9.0, self.min_bb_gain * 0.75)
        # WC/FH should still need to beat roughly one paid transfer's value.
        return float(HIT_COST)

    def _effective_min_gain(
        self,
        chip: str,
        gw: int,
        current_half: int,
        chips_remaining: int,
    ) -> float:
        """Softly lower a chip's reservation value as its half expires.

        The model still chooses the best projected week in its horizon. This
        function only answers "how good must that opportunity be?" It never
        forces a chip merely because time is running out.
        """
        base = self._base_min_gain(chip)
        half_end = self._half_end(current_half)
        gws_left = max(1, half_end - int(gw) + 1)
        slack = gws_left - max(1, int(chips_remaining))

        # Plenty of calendar room: no expiry pressure at all.
        if slack >= 7:
            discount = 0.0
        elif slack >= 4:
            discount = 0.05
        elif slack >= 2:
            discount = 0.10
        elif slack == 1:
            discount = 0.15
        elif slack == 0:
            discount = 0.20
        else:
            discount = min(self.expiry_max_discount, 0.20 + 0.05 * abs(slack))

        # A lone remaining chip also gets a small timing nudge in the final
        # couple of weeks, but never enough to cross the hard floor.
        if gws_left <= 2:
            discount = max(discount, 0.15)
        discount = min(self.expiry_max_discount, discount)
        return round(max(self._minimum_floor(chip), base * (1.0 - discount)), 3)

    def _expiry_context(
        self,
        current_gw: int,
        current_half: int,
        chips_available: list[str],
    ) -> dict[str, int | bool]:
        half_end = self._half_end(current_half)
        gws_left = max(0, half_end - int(current_gw) + 1)
        chips_left = len(chips_available)
        return {
            "half_end_gw": half_end,
            "gws_remaining": gws_left,
            "chips_remaining": chips_left,
            "calendar_slack": gws_left - chips_left,
            "chip_loss_risk": chips_left > gws_left,
        }

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

        # Build gain estimates and expiry-aware reservation thresholds per chip/GW.
        gains: dict[str, dict[int, float]] = {}
        thresholds: dict[str, dict[int, float]] = {}
        chips_remaining = len(chips_available)
        for chip in chips_available:
            chip_gains: dict[int, float] = {}
            chip_thresholds: dict[int, float] = {}
            for gw_dict in gw_plan:
                gw = gw_dict["gw"]
                if gw not in candidate_gws:
                    continue
                chip_gains[gw] = self._estimate_gain(chip, gw_dict, forecasts, gw)
                chip_thresholds[gw] = self._effective_min_gain(
                    chip, gw, current_half, chips_remaining
                )
            if chip_gains:
                gains[chip] = chip_gains
                thresholds[chip] = chip_thresholds

        if not gains:
            return {
                "chip_plan": [],
                "recommendation": None,
                "reason": "Could not estimate chip gains for any valid gameweek.",
                "advisory_only": True,
            }

        # Small LP: assign at most 1 chip per GW, at most 1 per chip type.
        # Raw expected gain still dominates week selection. A tiny WC->BB
        # sequence bonus only breaks close calls when both opportunities already
        # clear their own thresholds.
        prob = pulp.LpProblem("fpl_chips", pulp.LpMaximize)
        play = {
            (chip, gw): pulp.LpVariable(f"p_{chip}_{gw}", cat="Binary")
            for chip, gw_gains in gains.items()
            for gw in gw_gains
        }
        objective_terms = [
            gains[chip][gw] * play[(chip, gw)]
            for (chip, gw) in play
        ]

        # A below-threshold chip is not merely hidden after solving: it must be
        # impossible inside the LP. Otherwise a high raw-but-insufficient BB/FH
        # could occupy a GW, block a genuinely valid TC/WC through the one-chip
        # constraint, then disappear during post-solve filtering.
        for (chip, gw), var in play.items():
            if gains[chip][gw] < thresholds[chip][gw]:
                prob += var == 0

        combo_vars: dict[tuple[int, int], pulp.LpVariable] = {}
        if CHIP_WILDCARD in gains and CHIP_BENCH_BOOST in gains:
            for wc_gw in gains[CHIP_WILDCARD]:
                for bb_gw in gains[CHIP_BENCH_BOOST]:
                    gap = int(bb_gw) - int(wc_gw)
                    if gap not in (1, 2):
                        continue
                    if gains[CHIP_WILDCARD][wc_gw] < thresholds[CHIP_WILDCARD][wc_gw]:
                        continue
                    if gains[CHIP_BENCH_BOOST][bb_gw] < thresholds[CHIP_BENCH_BOOST][bb_gw]:
                        continue
                    var = pulp.LpVariable(f"combo_wc_bb_{wc_gw}_{bb_gw}", cat="Binary")
                    combo_vars[(wc_gw, bb_gw)] = var
                    prob += var <= play[(CHIP_WILDCARD, wc_gw)]
                    prob += var <= play[(CHIP_BENCH_BOOST, bb_gw)]
                    prob += var >= (
                        play[(CHIP_WILDCARD, wc_gw)]
                        + play[(CHIP_BENCH_BOOST, bb_gw)]
                        - 1
                    )
                    gap_weight = 1.0 if gap == 1 else 0.4
                    objective_terms.append(self.wc_bb_combo_bonus * gap_weight * var)

        prob += pulp.lpSum(objective_terms)
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

        # Only include assignments that clear their effective reservation value.
        # Expiry can soften that value, but the hard per-chip floors above mean
        # the planner is still allowed to let a chip expire rather than panic.
        chip_plan = sorted(
            [
                {
                    "chip": chip,
                    "gw": gw,
                    "chip_label": CHIP_LABELS.get(chip, chip),
                    "expected_gain": round(gains[chip].get(gw, 0.0), 2),
                    "required_gain": round(thresholds[chip][gw], 2),
                    "base_required_gain": round(self._base_min_gain(chip), 2),
                }
                for (chip, gw), var in play.items()
                if (pulp.value(var) or 0.0) > 0.5
                and gains[chip].get(gw, 0.0) >= thresholds[chip][gw]
            ],
            key=lambda d: d["gw"],
        )

        combo_plan = []
        for (wc_gw, bb_gw), var in combo_vars.items():
            if (pulp.value(var) or 0.0) > 0.5:
                combo_plan.append(
                    {
                        "sequence": "Wildcard -> Bench Boost",
                        "wildcard_gw": int(wc_gw),
                        "bench_boost_gw": int(bb_gw),
                    }
                )

        expiry = self._expiry_context(current_gw, current_half, chips_available)
        now = [p for p in chip_plan if p["gw"] == current_gw]
        rec = now[0]["chip"] if now else None
        if rec:
            entry = now[0]
            reason = (
                f"Play {CHIP_LABELS.get(rec, rec)} this GW "
                f"(est. +{entry['expected_gain']:.1f} pts; "
                f"needs {entry['required_gain']:.1f})."
            )
            current_combo = next(
                (
                    combo for combo in combo_plan
                    if combo["wildcard_gw"] == current_gw
                    or combo["bench_boost_gw"] == current_gw
                ),
                None,
            )
            if current_combo:
                reason += (
                    f" Preferred sequence: Wildcard GW{current_combo['wildcard_gw']} "
                    f"-> Bench Boost GW{current_combo['bench_boost_gw']}."
                )
        else:
            future = next((p for p in chip_plan if p["gw"] > current_gw), None)
            if future:
                reason = (
                    f"Hold all chips this GW — the model currently prefers "
                    f"{future['chip_label']} in GW{future['gw']} "
                    f"(+{future['expected_gain']:.1f} pts)."
                )
            else:
                reason = "Hold all chips this GW — no modeled opportunity clears its reservation value."
            if combo_plan:
                combo = combo_plan[0]
                reason += (
                    f" Best combo currently: Wildcard GW{combo['wildcard_gw']} "
                    f"-> Bench Boost GW{combo['bench_boost_gw']}."
                )

        reason += (
            f" {expiry['gws_remaining']} GW(s) remain before this chip set expires "
            f"with {expiry['chips_remaining']} chip(s) unused; expiry only softens "
            f"thresholds and never forces a chip."
        )
        return {
            "chip_plan": chip_plan,
            "combo_plan": combo_plan,
            "expiry_context": expiry,
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

    # ------------------------------------------------------------------
    # Backtester chip scheduling
    # ------------------------------------------------------------------

    def plan(
        self,
        expected_points_fn,
        history_df: pd.DataFrame,
        test_season: str,
        wildcard_h1_gw: int | None = None,
        wildcard_h2_gw: int | None = None,
    ) -> dict[int, str]:
        """Return chip schedule {gw: chip_name} for the full season.

        Assigns up to 8 chips (H1 + H2 sets). FH is only assigned if a BGW
        exists in that half. WC timing defaults to 35% through H1 and 25%
        through H2 if not supplied explicitly.
        """
        test_hist = history_df[history_df["season"] == test_season]
        all_gws = sorted(test_hist["GW"].unique().astype(int))
        if not all_gws:
            return {}

        h1_gws = [g for g in all_gws if g < 20]
        h2_gws = [g for g in all_gws if g >= 20]

        dgw_set, bgw_set, gw_n_unique = self._detect_gw_types(test_hist)
        median_n = float(np.median(list(gw_n_unique.values()))) if gw_n_unique else 800.0

        if wildcard_h1_gw is None:
            wildcard_h1_gw = h1_gws[int(0.35 * len(h1_gws))] if h1_gws else 7
        if wildcard_h2_gw is None:
            wildcard_h2_gw = h2_gws[int(0.25 * len(h2_gws))] if h2_gws else 25

        schedule: dict[int, str] = {}
        for gws, wc_gw in [(h1_gws, wildcard_h1_gw), (h2_gws, wildcard_h2_gw)]:
            half_bgws = {g for g in bgw_set if g in set(gws)}
            schedule.update(
                self._plan_half(gws, wc_gw, half_bgws, expected_points_fn,
                                test_hist, gw_n_unique, median_n)
            )
        return schedule

    def _detect_gw_types(
        self, test_hist: pd.DataFrame
    ) -> tuple[set[int], set[int], dict[int, int]]:
        """Return (dgw_gws, bgw_gws, gw_n_unique) detected from player counts."""
        gw_stats: dict[int, dict] = {}
        for gw, grp in test_hist.groupby("GW"):
            counts = grp.groupby("element").size()
            gw_stats[int(gw)] = {
                "n_unique": int(grp["element"].nunique()),
                "n_doubles": int((counts > 1).sum()),
            }
        dgw_set = {g for g, s in gw_stats.items() if s["n_doubles"] > 0}
        median_n = float(np.median([s["n_unique"] for s in gw_stats.values()]))
        bgw_set = {g for g, s in gw_stats.items() if s["n_unique"] < 0.85 * median_n}
        gw_n_unique = {g: s["n_unique"] for g, s in gw_stats.items()}
        return dgw_set, bgw_set, gw_n_unique

    def _plan_half(
        self,
        gws: list[int],
        wc_gw: int,
        bgw_set: set[int],
        expected_points_fn,
        test_hist: pd.DataFrame,
        gw_n_unique: dict[int, int],
        median_n: float,
    ) -> dict[int, str]:
        """Assign at most 4 chips (WC, FH, TC, BB) to GWs in one half.

        Assignment order: WC (fixed) → FH (best BGW) → BB (best top-15 sum,
        prefers DGWs) → TC (best max player). BB is assigned before TC because
        Bench Boost extracts more total value from double gameweeks than Triple
        Captain does.
        """
        gw_set = set(gws)
        assigned: dict[int, str] = {}
        taken: set[int] = set()

        # 1. Wildcard — fixed GW
        if wc_gw in gw_set:
            assigned[wc_gw] = CHIP_WILDCARD
            taken.add(wc_gw)

        # 2. Free Hit — best BGW only (skip if none).
        # FH score = predicted top-15 xPts among active players, weighted by
        # sqrt(n_unique / median_n) so BGWs with more fixtures rank higher when
        # ML predictions are similar.
        def _fh_score(g: int) -> float:
            n = gw_n_unique.get(g, median_n)
            return self._fh_value(g, expected_points_fn, test_hist) * (n / median_n) ** 0.5

        bgw_candidates = sorted(
            [g for g in bgw_set if g in gw_set and g not in taken],
            key=lambda g: -_fh_score(g),
        )
        if bgw_candidates:
            fh_gw = bgw_candidates[0]
            assigned[fh_gw] = CHIP_FREE_HIT
            taken.add(fh_gw)

        # 3. Bench Boost — GW with highest top-15 sum (assigned before TC because
        # BB gains more total value in DGWs — all 15 players benefit from doubles).
        bb_candidates = [g for g in gws if g not in taken]
        if bb_candidates:
            bb_gw = max(bb_candidates,
                        key=lambda g: self._bb_value(g, expected_points_fn))
            assigned[bb_gw] = CHIP_BENCH_BOOST
            taken.add(bb_gw)

        # 4. Triple Captain — GW with highest single-player prediction.
        tc_candidates = [g for g in gws if g not in taken]
        if tc_candidates:
            tc_gw = max(tc_candidates,
                        key=lambda g: self._tc_value(g, expected_points_fn))
            assigned[tc_gw] = CHIP_TRIPLE_CAPTAIN

        return assigned

    def _tc_value(self, gw: int, expected_points_fn) -> float:
        s = expected_points_fn(gw)
        return float(s.max()) if not s.empty else 0.0

    def _bb_value(self, gw: int, expected_points_fn) -> float:
        s = expected_points_fn(gw)
        return float(s.nlargest(15).sum()) if not s.empty else 0.0

    def _fh_value(
        self, gw: int, expected_points_fn, test_hist: pd.DataFrame
    ) -> float:
        """Top-15 xPts among players with an actual fixture this GW."""
        gw_elements = set(test_hist[test_hist["GW"] == gw]["element"].astype(int))
        s = expected_points_fn(gw)
        s = s[s.index.isin(gw_elements)]
        return float(s.nlargest(15).sum()) if not s.empty else 0.0
