from __future__ import annotations

import unittest

import pandas as pd

from bot.fpl_rules import DEF, FWD, GKP, MID
from bot.season_planner import SeasonPlanner


class SeasonPlannerReportTests(unittest.TestCase):
    def test_established_team_zero_transfer_is_roll_not_initial_squad(self):
        players = []
        spec = [
            (GKP, 2),
            (DEF, 5),
            (MID, 5),
            (FWD, 3),
        ]
        element = 1
        for pos, count in spec:
            for _ in range(count):
                players.append(
                    {
                        "element": element,
                        "gw": 3,
                        "xpts": 3.0,
                        "position": pos,
                        "team": ((element - 1) % 5) + 1,
                        "now_cost": 50,
                        "name": f"P{element}",
                    }
                )
                element += 1

        forecasts = pd.DataFrame(players)
        squad = list(range(1, 16))
        planner = SeasonPlanner(horizon=1, time_limit=15)
        result = planner.plan(
            forecasts,
            {
                "gameweek": 3,
                "squad": squad,
                "selling_prices": {element: 50 for element in squad},
                "bank": 0,
                "ft": 1,
                "chips_available": [],
            },
        )

        self.assertEqual(result["transfers_in"], [])
        self.assertEqual(result["transfers_out"], [])
        self.assertEqual(result["report_table"].iloc[0]["Transfers"], "Roll")


if __name__ == "__main__":
    unittest.main()
