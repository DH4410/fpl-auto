"""Tests for the advisory projection sanity guard (season_forecaster.projection_warnings)
and the report banner it drives (reporter._projection_warning_block).
"""

import unittest

from bot.season_forecaster import projection_warnings
from bot.reporter import _projection_warning_block


def _gw(gw, xi, chip=None):
    return {"gw": gw, "xi_xpts": xi, "chip": chip}


class ProjectionGuardTests(unittest.TestCase):

    def test_flags_uncapped_future_gw_towering_over_immediate(self):
        plan = [_gw(2, 37.0), _gw(3, 150.0), _gw(4, 40.0)]
        warns = projection_warnings(plan)
        self.assertEqual(len(warns), 1)
        self.assertIn("GW3", warns[0])

    def test_silent_when_projections_are_flat(self):
        plan = [_gw(2, 37.0), _gw(3, 39.0), _gw(4, 38.0), _gw(5, 41.0)]
        self.assertEqual(projection_warnings(plan), [])

    def test_chip_gameweeks_are_exempt(self):
        plan = [_gw(2, 37.0), _gw(3, 80.0, chip="bboost"), _gw(4, 90.0, chip="3xc")]
        self.assertEqual(projection_warnings(plan), [])

    def test_empty_or_zero_base_is_safe(self):
        self.assertEqual(projection_warnings([]), [])
        self.assertEqual(projection_warnings([_gw(2, 0.0), _gw(3, 100.0)]), [])

    def test_reporter_banner_hidden_when_clean(self):
        self.assertEqual(_projection_warning_block({}), "")
        self.assertEqual(_projection_warning_block({"projection_warnings": []}), "")

    def test_reporter_banner_shown_when_flagged(self):
        block = _projection_warning_block({"projection_warnings": ["GW3 XI ... inflated"]})
        self.assertIn("Projection sanity warning", block)
        self.assertIn("GW3 XI ... inflated", block)


if __name__ == "__main__":
    unittest.main()
