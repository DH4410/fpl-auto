import unittest

from bot.reflection_insights import build_reflection_messages


class ReflectionTests(unittest.TestCase):
    def test_underexposed_easy_fixture_team_generates_lesson(self):
        bootstrap = {
            "teams": [
                {"id": 1, "name": "Arsenal", "short_name": "ARS"},
                {"id": 2, "name": "Other", "short_name": "OTH"},
            ],
            "elements": [
                {"id": 1, "team": 1, "web_name": "A1"},
                {"id": 2, "team": 1, "web_name": "A2"},
                {"id": 3, "team": 1, "web_name": "A3"},
                {"id": 4, "team": 1, "web_name": "A4"},
                {"id": 5, "team": 1, "web_name": "A5"},
                {"id": 20, "team": 2, "web_name": "Mine"},
            ],
        }
        live = {
            "elements": [
                {"id": 1, "stats": {"total_points": 9}},
                {"id": 2, "stats": {"total_points": 8}},
                {"id": 3, "stats": {"total_points": 7}},
                {"id": 4, "stats": {"total_points": 6}},
                {"id": 5, "stats": {"total_points": 5}},
                {"id": 20, "stats": {"total_points": 2}},
            ]
        }
        fixtures = [
            {"event": 2, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 4},
            {"event": 3, "team_h": 2, "team_a": 1, "team_h_difficulty": 4, "team_a_difficulty": 2},
            {"event": 4, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 4},
        ]
        picks = [{"element": 20, "position": 1, "is_captain": True, "multiplier": 2}]

        messages = build_reflection_messages(
            gw=1,
            live_data=live,
            bootstrap=bootstrap,
            my_picks=picks,
            fixtures=fixtures,
        )
        self.assertTrue(any("underweighted Arsenal" in message for message in messages))
        self.assertTrue(any("FDR 2.0" in message for message in messages))

    def test_bad_fixture_run_does_not_trigger_hindsight_chasing(self):
        bootstrap = {
            "teams": [{"id": 1, "name": "Arsenal"}, {"id": 2, "name": "Other"}],
            "elements": [
                {"id": i, "team": 1, "web_name": f"A{i}"} for i in range(1, 6)
            ] + [{"id": 20, "team": 2, "web_name": "Mine"}],
        }
        live = {"elements": [{"id": i, "stats": {"total_points": 12}} for i in range(1, 6)]}
        fixtures = [
            {"event": 2, "team_h": 1, "team_a": 2, "team_h_difficulty": 5, "team_a_difficulty": 2},
            {"event": 3, "team_h": 2, "team_a": 1, "team_h_difficulty": 2, "team_a_difficulty": 5},
            {"event": 4, "team_h": 1, "team_a": 2, "team_h_difficulty": 5, "team_a_difficulty": 2},
        ]
        messages = build_reflection_messages(
            gw=1,
            live_data=live,
            bootstrap=bootstrap,
            my_picks=[{"element": 20, "position": 1, "is_captain": True, "multiplier": 2}],
            fixtures=fixtures,
        )
        self.assertFalse(any("underweighted Arsenal" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
