"""End-to-end regression tests for SeasonForecaster.forecast().

Needs pandas + numpy (the forecaster is DataFrame-based). The CI `tests` job
installs them; when run somewhere without them the module skips cleanly.
"""

import unittest

try:
    import pandas  # noqa: F401
    HAVE_PANDAS = True
except ImportError:  # pragma: no cover
    HAVE_PANDAS = False

if HAVE_PANDAS:
    from bot.season_forecaster import SeasonForecaster

GKP, DEF, MID, FWD = 1, 2, 3, 4


def _player(pid, team, pos, *, ppg, ep_next, minutes=90, starts=1,
            now_cost=50, name=None):
    return {
        "id": pid, "team": team, "element_type": pos,
        "web_name": name or f"P{pid}",
        "now_cost": now_cost, "points_per_game": ppg, "ep_next": ep_next,
        "minutes": minutes, "starts": starts, "status": "a",
        "can_select": True, "chance_of_playing_next_round": None,
        "selected_by_percent": "1.0",
    }


def _fx(event, home, away, h_diff=3, a_diff=3):
    return {"event": event, "team_h": home, "team_a": away,
            "team_h_difficulty": h_diff, "team_a_difficulty": a_diff}


def _league_fixtures():
    """One fixture per team per GW1-6: (1 v 2) and (3 v 4)."""
    fx = []
    for gw in range(1, 7):
        fx.append(_fx(gw, 1, 2))
        fx.append(_fx(gw, 3, 4))
    return fx


@unittest.skipUnless(HAVE_PANDAS, "pandas not installed")
class ForecastTests(unittest.TestCase):

    def setUp(self):
        fillers = [_player(100 + i, (i % 4) + 1, MID, ppg=3.0, ep_next=2.5,
                           name=f"Filler{i}") for i in range(16)]
        self.bootstrap = {
            "teams": [{"id": t} for t in (1, 2, 3, 4)],
            "elements": [
                _player(1, 1, DEF, ppg=17.0, ep_next=2.0, name="Hero"),       # 1 huge game
                _player(2, 2, MID, ppg=2.0, ep_next=5.0, now_cost=120, name="Premium"),
                _player(3, 2, MID, ppg=5.0, ep_next=4.0, name="Steady"),
                _player(4, 3, FWD, ppg=6.0, ep_next=3.0, name="Swinger"),
                _player(5, 4, DEF, ppg=0.0, ep_next=0.0, minutes=0, starts=0, name="Junk"),
            ] + fillers,
        }
        self.fixtures = _league_fixtures()

    def _run(self, current_gw=1, owned=None, ml=None, fixtures=None):
        fc = SeasonForecaster(horizon=6, max_candidates=50)
        return fc.forecast(self.bootstrap, fixtures or self.fixtures, current_gw,
                           owned_ids=owned or [], ml_xpts=ml)

    def test_hero_future_gw_not_near_raw_ppg(self):
        df = self._run()
        future = df[(df["element"] == 1) & (df["gw"] >= 2)]
        self.assertGreater(len(future), 0)
        self.assertTrue((future["xpts"] < 7.0).all(),
                        f"hero future xpts still inflated:\n{future}")

    def test_hero_immediate_gw_uses_ep_next(self):
        df = self._run()
        gw1 = df[(df["element"] == 1) & (df["gw"] == 1)]["xpts"].iloc[0]
        self.assertAlmostEqual(gw1, 1.8, places=2)  # ep_next 2.0 * p_start 0.9

    def test_hero_does_not_dominate_candidate_pool(self):
        """With raw PPG the hero's horizon value dwarfed the premium's (~8x);
        after shrinkage it must be a narrow band."""
        df = self._run()
        hero = df[df["element"] == 1]["xpts"].sum()
        premium = df[df["element"] == 2]["xpts"].sum()
        self.assertGreater(premium, 0)
        self.assertLess(hero / premium, 1.8)

    def test_double_gameweek_scales_by_n_fixtures(self):
        fx = _league_fixtures()
        fx.append(_fx(4, 3, 4))  # team 3 (and 4) get a second GW4 fixture
        df = self._run(fixtures=fx)
        s = df[df["element"] == 4].set_index("gw")["xpts"]
        self.assertGreater(s[4], 1.7 * s[3])
        self.assertGreater(s[4], 1.7 * s[6])

    def test_blank_gameweek_is_zero(self):
        fx = [f for f in _league_fixtures() if not (f["event"] == 5 and f["team_h"] == 3)]
        df = self._run(fixtures=fx)
        gw5 = df[(df["element"] == 4) & (df["gw"] == 5)]["xpts"].iloc[0]
        self.assertEqual(gw5, 0.0)

    def test_fdr_affects_future_projection(self):
        fx = [f for f in _league_fixtures() if f["event"] not in (2, 3)]
        fx += [_fx(2, 1, 2, h_diff=1), _fx(3, 1, 2, h_diff=5),
               _fx(2, 3, 4), _fx(3, 3, 4)]
        df = self._run(fixtures=fx)
        hero = df[df["element"] == 1].set_index("gw")["xpts"]
        self.assertGreater(hero[2], hero[3], "easy GW2 should beat hard GW3")

    def test_owned_player_always_retained(self):
        df = self._run(owned=[5])
        self.assertIn(5, set(df["element"]), "owned player dropped from forecast")

    def test_ml_future_gw_stays_bounded(self):
        df = self._run(ml={1: 3.0, 2: 4.0})
        future = df[(df["element"].isin([1, 2])) & (df["gw"] >= 2)]
        self.assertTrue((future["xpts"] < 10.0).all())


if __name__ == "__main__":
    unittest.main()
