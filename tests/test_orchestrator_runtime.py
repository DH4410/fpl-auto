import unittest
from datetime import datetime, timezone

from bot.orchestrator_runtime import (
    hours_until_next_deadline,
    is_international_break,
    latest_started_gw,
    next_future_event,
)


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)
        self.bootstrap = {
            "events": [
                {
                    "id": 1,
                    "deadline_time": "2026-08-21T17:30:00Z",
                    "finished": False,
                },
                {
                    "id": 2,
                    "deadline_time": "2026-08-28T17:30:00Z",
                    "finished": False,
                },
            ]
        }

    def test_live_gw_is_not_mistaken_for_next_deadline(self):
        self.assertEqual(next_future_event(self.bootstrap, self.now)["id"], 2)
        self.assertEqual(latest_started_gw(self.bootstrap, self.now), 1)

    def test_hours_until_deadline_is_positive_during_live_gw(self):
        hours = hours_until_next_deadline(self.bootstrap, self.now)
        self.assertGreater(hours, 0)
        self.assertAlmostEqual(hours, 151.5, places=1)

    def test_break_uses_future_deadline(self):
        self.assertFalse(is_international_break(self.bootstrap, 14, self.now))
        far = {"events": [{"id": 2, "deadline_time": "2026-09-10T17:30:00Z"}]}
        self.assertTrue(is_international_break(far, 14, self.now))


if __name__ == "__main__":
    unittest.main()
