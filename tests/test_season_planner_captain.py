from __future__ import annotations

import unittest

import pandas as pd

from bot.fpl_rules import DEF, FWD, GKP, MID
from bot.season_planner import SeasonPlanner


def _squad(xpts_by_element: dict[int, float]) -> pd.DataFrame:
    """A legal 15-man squad; per-element xPts supplied by the caller."""
    players = []
    element = 1
    for pos, count in [(GKP, 2), (DEF, 5), (MID, 5), (FWD, 3)]:
        for _ in range(count):
            players.append({
                "element": element,
                "gw": 3,
                "xpts": xpts_by_element.get(element, 3.0),
                "position": pos,
                "team": ((element - 1) % 8) + 1,
                "now_cost": 50,
                "name": f"P{element}",
            })
            element += 1
    return pd.DataFrame(players)


def _plan(forecasts: pd.DataFrame) -> dict:
    squad = list(range(1, 16))
    return SeasonPlanner(horizon=1, time_limit=15).plan(
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


class SeasonPlannerCaptainTests(unittest.TestCase):
    def test_goalkeeper_is_not_captained_even_when_top_projected(self):
        # Element 1 is a GKP with a runaway projection; every outfield starter
        # is well behind. The position weighting must still keep the armband
        # off the keeper.
        result = _plan(_squad({1: 12.0}))
        captain = result["gw_plan"][0]["captain"]["element"]
        vice = result["gw_plan"][0]["vice"]["element"]
        self.assertNotIn(captain, (1, 2))
        self.assertNotIn(vice, (1, 2))

    def test_defender_needs_to_clearly_beat_attackers_for_the_armband(self):
        # A DEF (element 3) projected above the attackers but not 2x them:
        # the 0.5 weight should hand the armband to the best MID/FWD instead.
        result = _plan(_squad({3: 6.0, 13: 4.5, 14: 4.5, 15: 4.5}))
        captain_pos = next(
            p["position"] for p in result["gw_plan"][0]["starting_xi"]
            if p["element"] == result["gw_plan"][0]["captain"]["element"]
        )
        self.assertIn(captain_pos, (MID, FWD))

    def test_dominant_attacker_still_wins_the_armband(self):
        result = _plan(_squad({13: 9.0}))
        self.assertEqual(result["gw_plan"][0]["captain"]["element"], 13)


if __name__ == "__main__":
    unittest.main()
