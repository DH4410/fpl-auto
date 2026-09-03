from __future__ import annotations

import unittest

from bot import data_collector
from bot.updater import _blend_ml_xpts, build_current_state, unavailable_for_rebuild


class UpdaterCurrentStateTests(unittest.TestCase):
    def test_ml_outlier_is_capped_before_official_estimate_blend(self):
        # 100 raw xPts used to overwhelm ep_next and produced the implausible
        # GW3 8-11 xPts cluster. The model channel is now capped at 12.
        self.assertAlmostEqual(_blend_ml_xpts(100.0, 4.0, 3), 7.2)
        self.assertAlmostEqual(_blend_ml_xpts(100.0, 0.0, 3), 6.0)

    def test_woltemade_style_unavailable_player_is_blocked_from_rebuild(self):
        blocked = unavailable_for_rebuild({
            "elements": [
                {"id": 99, "status": "u", "web_name": "Woltemade"},
                {"id": 100, "status": "a", "web_name": "Available"},
                {"id": 101, "status": "a", "can_select": False},
            ]
        })
        self.assertEqual(blocked, {99, 101})

    def test_live_my_team_bank_overrides_last_deadline_snapshot(self):
        my_team = {
            "picks": [
                {"element": 1, "selling_price": 50},
            ],
            "transfers": {
                "bank": 7,
                "limit": 2,
            },
            "chips": [],
        }
        state = build_current_state(
            bootstrap={},
            my_team=my_team,
            current_gw=3,
            entry_info={"last_deadline_bank": 25},
        )
        self.assertEqual(state["bank"], 7)
        self.assertEqual(state["ft"], 2)

    def test_player_pool_can_use_exact_supplied_bootstrap_snapshot(self):
        bootstrap = {
            "elements": [
                {
                    "id": 99,
                    "team": 1,
                    "now_cost": 73,
                    "status": "a",
                    "element_type": 3,
                }
            ],
            "teams": [
                {"id": 1, "name": "Live FC", "short_name": "LIV"},
            ],
        }
        pool = data_collector.build_player_pool(bootstrap=bootstrap)
        self.assertEqual(pool.iloc[0]["id"], 99)
        self.assertEqual(pool.iloc[0]["team_name"], "Live FC")
        self.assertEqual(pool.iloc[0]["price"], 7.3)

    def test_last_deadline_bank_is_only_fallback_when_live_bank_missing(self):
        my_team = {
            "picks": [
                {"element": 1, "selling_price": 50},
            ],
            "transfers": {
                "limit": 1,
            },
            "chips": [],
        }
        state = build_current_state(
            bootstrap={},
            my_team=my_team,
            current_gw=3,
            entry_info={"last_deadline_bank": 14},
        )
        self.assertEqual(state["bank"], 14)


if __name__ == "__main__":
    unittest.main()
