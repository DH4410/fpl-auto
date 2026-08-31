from __future__ import annotations

import unittest

import pandas as pd

from bot.chip_planner import ChipPlanner
from bot.fpl_rules import (
    CHIP_BENCH_BOOST,
    CHIP_FREE_HIT,
    CHIP_TRIPLE_CAPTAIN,
    CHIP_WILDCARD,
)
from bot.pre_deadline_simulator import PreDeadlineSimulator


class FixedGainPlanner(ChipPlanner):
    def __init__(self, fixed_gains: dict[tuple[str, int], float], **kwargs):
        super().__init__(**kwargs)
        self.fixed_gains = fixed_gains

    def _estimate_gain(self, chip: str, gw_dict: dict, forecasts: pd.DataFrame, gw: int) -> float:
        return float(self.fixed_gains.get((chip, gw), 0.0))


class ChipExpiryPolicyTests(unittest.TestCase):
    def test_no_expiry_pressure_early_in_half(self):
        planner = ChipPlanner()
        chips = [
            CHIP_WILDCARD,
            CHIP_FREE_HIT,
            CHIP_TRIPLE_CAPTAIN,
            CHIP_BENCH_BOOST,
        ]
        self.assertEqual(
            planner._effective_min_gain(
                CHIP_TRIPLE_CAPTAIN, gw=3, current_half=1, chips_remaining=len(chips)
            ),
            planner.min_tc_gain,
        )
        self.assertEqual(
            planner._effective_min_gain(
                CHIP_BENCH_BOOST, gw=3, current_half=1, chips_remaining=len(chips)
            ),
            planner.min_bb_gain,
        )

    def test_expiry_softens_but_never_panics(self):
        planner = ChipPlanner()
        self.assertEqual(
            planner._effective_min_gain(
                CHIP_TRIPLE_CAPTAIN, gw=19, current_half=1, chips_remaining=4
            ),
            5.0,
        )
        self.assertEqual(
            planner._effective_min_gain(
                CHIP_BENCH_BOOST, gw=19, current_half=1, chips_remaining=4
            ),
            9.0,
        )
        self.assertEqual(
            planner._effective_min_gain(
                CHIP_WILDCARD, gw=19, current_half=1, chips_remaining=4
            ),
            4.0,
        )

    def test_bad_final_week_can_still_hold_and_let_chips_expire(self):
        fixed = {
            (CHIP_WILDCARD, 19): 3.9,
            (CHIP_FREE_HIT, 19): 3.9,
            (CHIP_TRIPLE_CAPTAIN, 19): 4.9,
            (CHIP_BENCH_BOOST, 19): 8.9,
        }
        planner = FixedGainPlanner(fixed)
        result = planner.evaluate(
            forecasts=pd.DataFrame(),
            gw_plan=[{"gw": 19}],
            chips_available=[
                CHIP_WILDCARD,
                CHIP_FREE_HIT,
                CHIP_TRIPLE_CAPTAIN,
                CHIP_BENCH_BOOST,
            ],
            current_gw=19,
            current_half=1,
        )
        self.assertIsNone(result["recommendation"])
        self.assertEqual(result["chip_plan"], [])
        self.assertTrue(result["expiry_context"]["chip_loss_risk"])
        self.assertIn("never forces a chip", result["reason"])

    def test_close_call_can_prefer_wildcard_then_bench_boost_combo(self):
        fixed = {
            (CHIP_WILDCARD, 16): 10.0,
            (CHIP_WILDCARD, 17): 9.4,
            (CHIP_WILDCARD, 18): 5.0,
            (CHIP_BENCH_BOOST, 16): 11.5,
            (CHIP_BENCH_BOOST, 17): 12.0,
            (CHIP_BENCH_BOOST, 18): 14.0,
        }
        planner = FixedGainPlanner(fixed, wc_bb_combo_bonus=1.5)
        result = planner.evaluate(
            forecasts=pd.DataFrame(),
            gw_plan=[{"gw": 16}, {"gw": 17}, {"gw": 18}],
            chips_available=[CHIP_WILDCARD, CHIP_BENCH_BOOST],
            current_gw=16,
            current_half=1,
        )
        by_chip = {row["chip"]: row for row in result["chip_plan"]}
        self.assertEqual(by_chip[CHIP_WILDCARD]["gw"], 17)
        self.assertEqual(by_chip[CHIP_BENCH_BOOST]["gw"], 18)
        self.assertEqual(
            result["combo_plan"],
            [{
                "sequence": "Wildcard -> Bench Boost",
                "wildcard_gw": 17,
                "bench_boost_gw": 18,
            }],
        )

    def test_below_threshold_chip_cannot_block_valid_chip_same_gw(self):
        # BB has the larger raw gain but misses its 11-point floor; TC clears
        # its 6-point floor. The LP must choose TC rather than letting invalid
        # BB occupy the only chip slot and disappear after solving.
        planner = FixedGainPlanner({
            (CHIP_BENCH_BOOST, 8): 10.9,
            (CHIP_TRIPLE_CAPTAIN, 8): 6.1,
        })
        result = planner.evaluate(
            forecasts=pd.DataFrame(),
            gw_plan=[{"gw": 8}],
            chips_available=[CHIP_BENCH_BOOST, CHIP_TRIPLE_CAPTAIN],
            current_gw=8,
            current_half=1,
        )
        self.assertEqual(result["recommendation"], CHIP_TRIPLE_CAPTAIN)
        self.assertEqual(
            [(row["chip"], row["gw"]) for row in result["chip_plan"]],
            [(CHIP_TRIPLE_CAPTAIN, 8)],
        )

    def test_execution_gate_uses_expiry_aware_threshold_from_plan(self):
        chip, reason = PreDeadlineSimulator._check_chip(
            {
                "chip_plan": [{
                    "chip": CHIP_TRIPLE_CAPTAIN,
                    "gw": 19,
                    "expected_gain": 5.2,
                    "required_gain": 5.0,
                }],
                "chip_reason": "One GW remains, but the opportunity still clears the floor.",
            },
            CHIP_TRIPLE_CAPTAIN,
            19,
        )
        self.assertEqual(chip, CHIP_TRIPLE_CAPTAIN)
        self.assertIn("threshold 5.0", reason)

    def test_expiry_context_tracks_calendar_capacity(self):
        planner = ChipPlanner()
        context = planner._expiry_context(
            current_gw=18,
            current_half=1,
            chips_available=[
                CHIP_WILDCARD,
                CHIP_TRIPLE_CAPTAIN,
                CHIP_BENCH_BOOST,
            ],
        )
        self.assertEqual(context["gws_remaining"], 2)
        self.assertEqual(context["chips_remaining"], 3)
        self.assertEqual(context["calendar_slack"], -1)
        self.assertTrue(context["chip_loss_risk"])


if __name__ == "__main__":
    unittest.main()
