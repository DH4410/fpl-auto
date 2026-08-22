import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from bot.top100_cohort_scout import CohortLockedTopManagerScout


class CohortScoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.scout = CohortLockedTopManagerScout(data_dir=Path(self.tmp.name), sample_size=2)
        self.bootstrap = {
            "events": [{
                "id": 2,
                "deadline_time": "2026-08-28T17:30:00Z",
                "finished": False,
            }]
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_freezes_standings_only_before_deadline(self):
        rows = [
            {"entry": 101, "rank": 1, "total": 100},
            {"entry": 202, "rank": 2, "total": 99},
        ]
        self.scout.tracker.fetch_standings = lambda _session: rows
        result = self.scout.capture_pre_deadline_cohort(
            gw=2,
            bootstrap=self.bootstrap,
            session=object(),
            persist=True,
            now_utc=datetime(2026, 8, 28, 17, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(result["captured"])
        cohort = self.scout.load_cohort(2)
        self.assertTrue(cohort["standings_only"])
        self.assertEqual([r["entry"] for r in cohort["standings"]], [101, 202])
        self.assertNotIn("managers", cohort)

    def test_refuses_to_select_live_leaders_without_predeadline_cohort(self):
        # If this callback were used after lock, the old bug would be back.
        def fail_if_live_standings_are_queried(_session):
            raise AssertionError("live Overall standings must not select the cohort")

        self.scout.tracker.fetch_standings = fail_if_live_standings_are_queried
        result = self.scout.snapshot_locked_teams(
            gw=2,
            bootstrap=self.bootstrap,
            session=object(),
            persist=False,
            now_utc=datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(result["no_predeadline_cohort"])
        self.assertFalse(result["created"])

    def test_cannot_freeze_cohort_after_deadline(self):
        self.scout.tracker.fetch_standings = lambda _session: [{"entry": 999}]
        result = self.scout.capture_pre_deadline_cohort(
            gw=2,
            bootstrap=self.bootstrap,
            session=object(),
            persist=False,
            now_utc=datetime(2026, 8, 28, 18, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(result["captured"])
        self.assertIn("already locked", result["reason"])


if __name__ == "__main__":
    unittest.main()
