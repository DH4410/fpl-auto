"""Regression tests for early-season PPG stabilisation (_stabilized_ppg).

These run without pandas/numpy: _stabilized_ppg accepts any mapping, so a plain
dict stands in for a DataFrame row. Keep them dependency-free so the lightweight
`unittest discover` CI job covers the core of the fix.
"""

import unittest

from bot.season_forecaster import (
    MIN_PPG,
    POS_BASELINE_PPG,
    PRIOR_MATCHES,
    _stabilized_ppg,
)

GKP, DEF, MID, FWD = 1, 2, 3, 4


def row(ppg=0.0, minutes=0.0, starts=0.0, ep_next=0.0, pos=MID):
    return {
        "points_per_game": ppg,
        "minutes": minutes,
        "starts": starts,
        "ep_next": ep_next,
        "element_type": pos,
    }


class StabilizedPpgTests(unittest.TestCase):

    def test_single_huge_gw_is_heavily_regressed(self):
        """A 17-point GW1 haul must not become a ~17 per-game base rate."""
        de_cuyper = row(ppg=17.0, minutes=77, starts=1, ep_next=1.9, pos=DEF)
        stab = _stabilized_ppg(de_cuyper, immediate_fdr_mult=1.0)
        self.assertLess(stab, 5.0, "one 17-pt game still dominates the estimate")
        self.assertGreater(stab, 2.5, "shouldn't collapse below the prior either")
        # far closer to the prior (~2.5) than to the raw 17.
        self.assertLess(abs(stab - 3.0), abs(stab - 17.0))

    def test_blank_gw1_with_strong_prior_not_destroyed(self):
        """A premium who blanked GW1 keeps a respectable base rate."""
        haaland = row(ppg=2.0, minutes=90, starts=1, ep_next=4.0, pos=FWD)
        stab = _stabilized_ppg(haaland, immediate_fdr_mult=1.0)
        self.assertGreater(stab, 3.0, "one blank shouldn't drag a premium to 2.0")

    def test_gw1_hero_lands_in_same_band_as_premium_not_multiples_above(self):
        hero = _stabilized_ppg(row(ppg=17.0, minutes=77, starts=1, ep_next=1.9, pos=DEF))
        premium = _stabilized_ppg(row(ppg=2.0, minutes=90, starts=1, ep_next=4.0, pos=FWD))
        # Before the fix this ratio was ~8x; it must now be a narrow band.
        self.assertLess(abs(hero - premium), 1.5)

    def test_hero_with_weak_underlying_ranks_below_premium(self):
        """15 pts off one goal + clean sheet (ep_next 1.0) < a real premium."""
        mendy = _stabilized_ppg(row(ppg=15.0, minutes=63, starts=1, ep_next=1.0, pos=DEF))
        haaland = _stabilized_ppg(row(ppg=2.0, minutes=90, starts=1, ep_next=4.0, pos=FWD))
        self.assertLess(mendy, haaland)

    def test_sample_size_ramps_toward_raw_ppg(self):
        r = lambda g: row(ppg=7.0, minutes=90 * g, starts=g, ep_next=3.0, pos=MID)
        one = _stabilized_ppg(r(1))
        ten = _stabilized_ppg(r(10))
        twenty = _stabilized_ppg(r(20))
        self.assertLess(one, ten)
        self.assertLess(ten, twenty)
        # after ~1 game the prior wins; by ~20 games real form is past halfway.
        prior = 0.5 * 3.0 + 0.5 * POS_BASELINE_PPG[MID]
        halfway = prior + 0.5 * (7.0 - prior)
        self.assertLess(one, halfway)
        self.assertGreater(twenty, halfway)

    def test_genuinely_strong_player_becomes_ppg_driven_with_enough_matches(self):
        """A season of elite scoring should eventually read close to raw PPG."""
        salah = row(ppg=8.5, minutes=90 * 30, starts=30, ep_next=6.0, pos=MID)
        stab = _stabilized_ppg(salah)
        self.assertGreater(stab, 7.0)

    def test_ep_next_zero_falls_back_to_position_baseline(self):
        for pos in (GKP, DEF, MID, FWD):
            stab = _stabilized_ppg(row(ppg=0.0, ep_next=0.0, pos=pos))
            self.assertAlmostEqual(stab, POS_BASELINE_PPG[pos], places=6)

    def test_ep_next_is_fixture_neutralised(self):
        """Dividing ep_next by the immediate FDR multiplier means an easy next
        fixture yields a *lower* neutral base rate than a hard one."""
        base = row(ppg=4.0, minutes=90, starts=1, ep_next=3.0, pos=MID)
        easy_fixture = _stabilized_ppg(base, immediate_fdr_mult=1.30)
        hard_fixture = _stabilized_ppg(base, immediate_fdr_mult=0.70)
        self.assertLess(easy_fixture, hard_fixture)

    def test_never_below_min_ppg(self):
        stab = _stabilized_ppg(row(ppg=0.0, ep_next=0.01, pos=MID), immediate_fdr_mult=5.0)
        self.assertGreaterEqual(stab, MIN_PPG)

    def test_prior_matches_constant_is_a_strong_early_prior(self):
        self.assertGreaterEqual(PRIOR_MATCHES, 8.0)


if __name__ == "__main__":
    unittest.main()
