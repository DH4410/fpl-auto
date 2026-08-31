from __future__ import annotations

import unittest

from bot.updater import build_current_state


class UpdaterCurrentStateTests(unittest.TestCase):
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
