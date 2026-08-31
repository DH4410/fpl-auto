from __future__ import annotations

import unittest

from scripts.weekly_orchestrator_core import (
    _build_planning_news_guard,
    _expected_live_squad,
    _guard_changed,
    _picks_readback_errors,
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
