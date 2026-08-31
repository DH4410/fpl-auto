from __future__ import annotations

import unittest

from scripts.weekly_orchestrator_core import (
    _build_planning_news_guard,
    _expected_live_squad,
    _guard_changed,
    _picks_readback_errors,
    _chip_readback_error,
    _candidate_watch_ids,
    build_picks_payload,
)


class ExecutionGuardTests(unittest.TestCase):
    def test_news_guard_detects_late_official_change(self):
        bootstrap = {
            "elements": [
                {
                    "id": 10,
                    "status": "a",
                    "chance_of_playing_next_round": 100,
                    "news": "",
                }
            ]
        }
        frozen = _build_planning_news_guard(bootstrap, [10])
        changed, _ = _guard_changed(frozen, bootstrap)
        self.assertFalse(changed)

        updated = {
            "elements": [
                {
                    "id": 10,
                    "status": "d",
                    "chance_of_playing_next_round": 50,
                    "news": "Knock - 50% chance of playing",
                }
            ]
        }
        changed, detail = _guard_changed(frozen, updated)
        self.assertTrue(changed)
        self.assertIn("status a→d", detail)

    def test_expected_squad_accounts_for_checkpointed_transfer(self):
        decision = {
            "source_squad_signature": [1, 2, 3],
            "approved_transfers": [
                {"element_out": 1, "element_in": 4},
                {"element_out": 2, "element_in": 5},
            ],
        }
        self.assertEqual(
            _expected_live_squad(decision, {"1->4"}),
            {2, 3, 4},
        )

    def test_chip_readback_confirms_target_gameweek(self):
        live = {
            "chips": [
                {"name": "3xc", "status_for_entry": "played", "event": 9},
                {"name": "bboost", "status_for_entry": "available", "event": None},
            ]
        }
        self.assertIsNone(_chip_readback_error("3xc", 9, live))
        self.assertIsNotNone(_chip_readback_error("3xc", 10, live))
        self.assertIsNotNone(_chip_readback_error("bboost", 9, live))

    def test_watchlist_collects_only_transfer_in_targets(self):
        state = {
            "signing_ideas": [
                {"action": "transfer_in", "element": 101},
                {"action": "transfer_out", "element": 202},
            ],
            "research_ideas": [
                {"action": "transfer_in", "element": 303},
            ],
            "idea_list": [
                {"action": "hold", "element": 404},
            ],
        }
        self.assertEqual(_candidate_watch_ids(state), [101, 303])

    def test_picks_payload_orders_outfield_bench_by_current_xpts(self):
        starters = [
            {"element": i, "position": 2 if i <= 4 else 3, "cost": 5.0}
            for i in range(1, 12)
        ]
        bench = [
            {"element": 12, "position": 1, "cost": 4.0, "xpts": 3.0},
            {"element": 13, "position": 2, "cost": 7.0, "xpts": 1.5},
            {"element": 14, "position": 3, "cost": 4.5, "xpts": 4.0},
            {"element": 15, "position": 4, "cost": 5.5, "xpts": 2.5},
        ]
        payload = build_picks_payload({
            "captain": {"element": 5},
            "gw_plan": [{
                "starting_xi": starters,
                "bench": bench,
                "vice": {"element": 6},
            }],
        })
        by_position = {row["position"]: row["element"] for row in payload}
        self.assertEqual(by_position[12], 12)
        self.assertEqual(
            [by_position[13], by_position[14], by_position[15]],
            [14, 15, 13],
        )

    def test_exact_picks_readback_checks_positions_captain_and_vice(self):
        expected = [
            {
                "element": 10,
                "position": 1,
                "is_captain": False,
                "is_vice_captain": False,
            },
            {
                "element": 20,
                "position": 2,
                "is_captain": True,
                "is_vice_captain": False,
            },
            {
                "element": 30,
                "position": 3,
                "is_captain": False,
                "is_vice_captain": True,
            },
        ]
        live = {"picks": [dict(row) for row in expected]}
        self.assertEqual(_picks_readback_errors(expected, live), [])

        live["picks"][1]["is_captain"] = False
        errors = _picks_readback_errors(expected, live)
        self.assertTrue(errors)
        self.assertIn("position 2", errors[0])


if __name__ == "__main__":
    unittest.main()
