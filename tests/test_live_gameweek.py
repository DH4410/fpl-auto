import unittest

from bot.live_gameweek import build_live_summary, score_picks


class LiveGameweekScoringTests(unittest.TestCase):
    def setUp(self):
        self.picks = [
            {"element": 1, "position": 1, "multiplier": 1, "is_captain": False},
            {"element": 2, "position": 2, "multiplier": 2, "is_captain": True},
            {"element": 3, "position": 12, "multiplier": 0, "is_captain": False},
        ]
        self.live = {
            "elements": [
                {"id": 1, "stats": {"total_points": 3, "minutes": 90}},
                {"id": 2, "stats": {"total_points": 1, "minutes": 30}},
                {"id": 3, "stats": {"total_points": 8, "minutes": 90}},
            ]
        }
        self.bootstrap = {
            "elements": [
                {"id": 1, "web_name": "Starter", "team": 1},
                {"id": 2, "web_name": "Captain", "team": 2},
                {"id": 3, "web_name": "Bench", "team": 3},
            ]
        }

    def test_live_score_uses_locked_multipliers(self):
        result = score_picks(self.picks, self.live)
        self.assertEqual(result["net"], 5.0)
        self.assertEqual(result["bench_raw"], 8.0)

    def test_transfer_hit_is_subtracted(self):
        result = score_picks(self.picks, self.live, transfer_cost=4)
        self.assertEqual(result["gross"], 5.0)
        self.assertEqual(result["net"], 1.0)

    def test_summary_names_captain(self):
        result = build_live_summary(
            gw=1,
            picks=self.picks,
            live_data=self.live,
            bootstrap=self.bootstrap,
        )
        self.assertEqual(result["captain"]["name"], "Captain")
        self.assertEqual(result["captain"]["counted_points"], 2.0)


if __name__ == "__main__":
    unittest.main()
