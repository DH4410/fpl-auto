import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot.top100_post_deadline import PostDeadlineTopManagerScout


class _NeverCallSession:
    def get(self, *args, **kwargs):
        raise AssertionError("manager endpoints must not be called before deadline")


class Top100PostDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = {
            "events": [
                {"id": 1, "deadline_time": "2026-08-21T17:30:00Z", "finished": False},
                {"id": 2, "deadline_time": "2026-08-28T17:30:00Z", "finished": False},
            ],
            "teams": [
                {"id": 1, "name": "Arsenal"},
                {"id": 2, "name": "Other"},
            ],
            "elements": [
                {"id": 10, "web_name": "A1", "team": 1, "element_type": 2},
                {"id": 11, "web_name": "A2", "team": 1, "element_type": 3},
                {"id": 12, "web_name": "O1", "team": 2, "element_type": 4},
            ],
        }

    def test_refuses_to_fetch_before_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            scout = PostDeadlineTopManagerScout(data_dir=Path(tmp))
            result = scout.snapshot_locked_teams(
                gw=1,
                bootstrap=self.bootstrap,
                session=_NeverCallSession(),
                persist=False,
                now_utc=datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc),
            )
        self.assertTrue(result["blocked_pre_deadline"])
        self.assertIsNone(result["snapshot"])

    def test_deadline_passed_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            scout = PostDeadlineTopManagerScout(data_dir=Path(tmp))
            self.assertTrue(
                scout.deadline_has_passed(
                    self.bootstrap,
                    1,
                    now_utc=datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc),
                )
            )

    def test_strategy_detects_club_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            scout = PostDeadlineTopManagerScout(data_dir=Path(tmp))
            managers = [
                {
                    "captain": 11,
                    "picks": [
                        {"element": 10, "position": 2, "multiplier": 1},
                        {"element": 11, "position": 6, "multiplier": 2},
                        {"element": 12, "position": 10, "multiplier": 1},
                    ],
                },
                {
                    "captain": 11,
                    "picks": [
                        {"element": 10, "position": 2, "multiplier": 1},
                        {"element": 11, "position": 7, "multiplier": 2},
                    ],
                },
            ]
            strategy = scout._strategy_summary(managers, self.bootstrap)
        arsenal = next(x for x in strategy["club_exposure"] if x["name"] == "Arsenal")
        self.assertEqual(arsenal["avg_players_per_manager"], 2.0)
        self.assertEqual(strategy["captaincy"][0]["name"], "A2")


if __name__ == "__main__":
    unittest.main()
