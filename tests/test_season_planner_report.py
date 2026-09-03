from __future__ import annotations

import unittest

import pandas as pd

from bot.fpl_rules import DEF, FWD, GKP, MID
from bot.season_planner import SeasonPlanner


class SeasonPlannerReportTests(unittest.TestCase):
    @staticmethod
    def _squad_forecasts(include_replacement: bool = False) -> pd.DataFrame:
        players = []
        element = 1
        for pos, count in [(GKP, 2), (DEF, 5), (MID, 5), (FWD, 3)]:
            for _ in range(count):
                players.append({
                    "element": element,
                    "gw": 3,
                    "xpts": 3.0,
                    "position": pos,
                    "team": ((element - 1) % 8) + 1,
                    "now_cost": 50,
                    "name": f"P{element}",
                })
                element += 1
        if include_replacement:
            players.append({
                "element": 16, "gw": 3, "xpts": 2.0, "position": FWD,
                "team": 8, "now_cost": 50, "name": "Replacement",
            })
        return pd.DataFrame(players)

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

    def test_wildcard_rebuild_excludes_unavailable_and_preserves_free_transfers(self):
        squad = list(range(1, 16))
        current = self._squad_forecasts(include_replacement=True)
        forecasts = pd.concat([current, current.assign(gw=4)], ignore_index=True)
        result = SeasonPlanner(horizon=2, time_limit=15).plan(
            forecasts,
            {
                "gameweek": 3,
                "squad": squad,
                "selling_prices": {element: 50 for element in squad},
                "bank": 0,
                "ft": 2,
                "chips_available": ["wildcard"],
            },
            unlimited_transfers_current_gw=True,
            forbidden_current_ids={15},
        )

        targets = [
            {row["element"] for row in gameweek["squad"]}
            for gameweek in result["gw_plan"]
        ]
        self.assertTrue(all(15 not in target for target in targets))
        self.assertIn(16, targets[0])
        self.assertEqual(result["hits"], 0)
        self.assertEqual(result["gw_plan"][0]["ft_banked_next"], 2)
        self.assertEqual(result["transfer_plan_kind"], "wildcard_rebuild")


if __name__ == "__main__":
    unittest.main()
