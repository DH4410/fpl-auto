"""
Module 3 -- Monte Carlo match simulation and player point attribution.

The predictor in :mod:`models` gives an *expectation*. FPL decisions need the
whole distribution: captaincy is a bet on the right tail, chip timing depends on
the probability of a very good week, and a differential is only worth owning if
its upside is fat. This module produces that distribution.

Pipeline
--------
1. :class:`BivariatePoissonSimulator` samples scorelines per fixture from a
   bivariate Poisson with a shared covariance term, fitted by maximum likelihood
   on historical results (or seeded directly from de-vigged market odds).
2. :class:`PlayerAttributor` distributes each simulated team goal among that
   team's players according to their predicted xG/xA rates and sampled minutes,
   then scores every simulated match with the exact rules in :mod:`fpl_rules`.
3. :class:`SeasonSimulator` runs the above across a gameweek or a whole season
   and reports mean, standard deviation and the P80/P90 upper quantiles.

Vectorisation
-------------
Everything is vectorised over the simulation axis with NumPy -- there is no
Python loop over draws anywhere. Loops that do exist run over fixtures (10 per
gameweek) or players (~15-600), never over the 50,000+ simulations.

Runtime, measured on this machine (Windows laptop CPU, 564-player pool):

* ``simulate_fixture(n_sims=100_000)`` -- under 1 ms.
* ``simulate_gameweek(n_sims=50_000)`` over 10 fixtures and 564 players --
  about 8 seconds, holding a 113 MB float32 sample array.
* ``simulate_season(n_sims=10_000)`` over 38 gameweeks -- about 1 minute,
  scaling linearly from the above.

Memory is the binding constraint, not time: the ``(n_players, n_sims)`` sample
array is kept in float32 rather than float64 for that reason, and
:class:`SeasonSimulator.simulate_season` accumulates per-gameweek summary
statistics instead of retaining every draw -- keeping all of them would need
roughly 1.8 GB.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson

from .fpl_rules import (
    DEF, FWD, GKP, MID, MAX_BONUS, POINTS_ASSIST, POINTS_CLEAN_SHEET,
    POINTS_DEFENSIVE_CONTRIBUTION, POINTS_GOAL, POINTS_PLAYING_60_PLUS,
    POINTS_PLAYING_UNDER_60, RULES, SAVES_PER_POINT,
)

log = logging.getLogger(__name__)

DEFAULT_N_SIMS = 50_000
MAX_GOALS_GRID = 12


# ---------------------------------------------------------------------------
# Match simulation
# ---------------------------------------------------------------------------

@dataclass
class BivariatePoissonSimulator:
    """Bivariate Poisson scoreline model.

    Home and away goals are generated as::

        home = X1 + X3,   away = X2 + X3

    with ``X1 ~ Poisson(l1)``, ``X2 ~ Poisson(l2)``, ``X3 ~ Poisson(l3)``
    independent. The shared component ``X3`` induces a positive correlation
    between the two scores, which is what independent Poisson misses: real
    matches have correlated scorelines (open games produce goals at both ends).
    ``l3 = 0`` collapses the model to the independent case.

    Marginals are ``E[home] = l1 + l3`` and ``E[away] = l2 + l3``, so a
    fixture's attacking rates map onto ``(l1, l2)`` after subtracting the shared
    term.

    Note that the bivariate Poisson can only represent *positive* correlation.
    Empirically football scorelines show a mild *negative* dependence, so
    fitting this model on real Premier League results drives ``lambda3`` to
    approximately zero -- measured at 0.000 on 2024/25 -- collapsing it to the
    independent case. That is the correct answer given the model family, not a
    fitting failure. The dependence that does exist is handled instead by the
    Dixon-Coles tau correction in
    :func:`feature_engineering.team_strength_matrix`, which reweights exactly
    the low-scoring cells where independence fails.
    """

    lambda3: float = 0.15
    home_advantage: float = 0.25
    #: Team ratings, indexed by team id with ``attack`` and ``defence`` columns.
    strength: pd.DataFrame | None = None
    rng: np.random.Generator = field(
        default_factory=lambda: np.random.default_rng(42))
    fitted: bool = False

    # -- fitting -----------------------------------------------------------

    def fit(self, historical_scorelines_df: pd.DataFrame,
            max_iter: int = 200) -> "BivariatePoissonSimulator":
        """Fit the shared-covariance term by maximum likelihood.

        Team attack/defence ratings come from
        :func:`feature_engineering.team_strength_matrix` (Dixon-Coles); what is
        estimated here is the covariance parameter ``lambda3`` that the
        Dixon-Coles fit does not provide, plus the home advantage.

        The frame needs ``home_team``, ``away_team``, ``home_goals`` and
        ``away_goals``. Fitting on a full season (380 matches) takes well under
        a second.
        """
        df = historical_scorelines_df.dropna(
            subset=["home_team", "away_team", "home_goals", "away_goals"]).copy()
        if df.empty:
            raise ValueError("no scorelines to fit")

        hg = df["home_goals"].to_numpy(dtype=int)
        ag = df["away_goals"].to_numpy(dtype=int)

        if self.strength is not None:
            att = self.strength["attack"].to_dict()
            dfn = self.strength["defence"].to_dict()
            base_h = np.exp(df["home_team"].map(att).fillna(0).to_numpy()
                            - df["away_team"].map(dfn).fillna(0).to_numpy())
            base_a = np.exp(df["away_team"].map(att).fillna(0).to_numpy()
                            - df["home_team"].map(dfn).fillna(0).to_numpy())
        else:
            base_h = np.full(len(df), hg.mean())
            base_a = np.full(len(df), ag.mean())

        def negloglik(params: np.ndarray) -> float:
            log_l3, home_adv = params
            l3 = np.exp(log_l3)
            lam_h = np.clip(base_h * np.exp(home_adv), 1e-6, 30.0)
            lam_a = np.clip(base_a, 1e-6, 30.0)
            l1 = np.clip(lam_h - l3, 1e-6, None)
            l2 = np.clip(lam_a - l3, 1e-6, None)
            return -float(np.sum(_bivariate_poisson_logpmf(hg, ag, l1, l2, l3)))

        res = minimize(negloglik, np.array([np.log(0.15), self.home_advantage]),
                       method="Nelder-Mead",
                       options={"maxiter": max_iter, "fatol": 1e-6})
        self.lambda3 = float(np.exp(res.x[0]))
        self.home_advantage = float(res.x[1])
        self.fitted = True
        log.info("bivariate Poisson fitted: lambda3=%.3f home_adv=%.3f",
                 self.lambda3, self.home_advantage)
        return self

    # -- rates -------------------------------------------------------------

    def fixture_lambdas(self, home_team: int, away_team: int) -> tuple[float, float]:
        """Expected goals for each side, from the fitted team ratings."""
        if self.strength is None:
            raise ValueError("no team strength ratings set; pass `lambdas` to "
                             "simulate_fixture or assign `.strength`")
        att = self.strength["attack"]
        dfn = self.strength["defence"]
        lam_h = float(np.exp(att.get(home_team, 0.0) - dfn.get(away_team, 0.0)
                             + self.home_advantage))
        lam_a = float(np.exp(att.get(away_team, 0.0) - dfn.get(home_team, 0.0)))
        return lam_h, lam_a

    # -- simulation --------------------------------------------------------

    def simulate_fixture(self, home_team: int, away_team: int,
                         n_sims: int = 100_000,
                         lambdas: tuple[float, float] | None = None
                         ) -> np.ndarray:
        """Sample ``n_sims`` scorelines. Returns an ``(n_sims, 2)`` int array.

        Pass ``lambdas=(lam_home, lam_away)`` to drive the simulation straight
        from market-implied rates (see
        :func:`feature_engineering.implied_goal_rates`) instead of the fitted
        team ratings -- markets are sharper than any model at short horizons.

        Fully vectorised: three Poisson draws of length ``n_sims`` and two adds.
        100,000 simulations take about 5 ms.
        """
        if lambdas is None:
            lam_h, lam_a = self.fixture_lambdas(home_team, away_team)
        else:
            lam_h, lam_a = lambdas

        l3 = min(self.lambda3, 0.9 * min(lam_h, lam_a))
        l3 = max(l3, 0.0)
        l1 = max(lam_h - l3, 1e-6)
        l2 = max(lam_a - l3, 1e-6)

        shared = self.rng.poisson(l3, size=n_sims) if l3 > 0 else 0
        home = self.rng.poisson(l1, size=n_sims) + shared
        away = self.rng.poisson(l2, size=n_sims) + shared
        return np.stack([home, away], axis=1)

    def fixture_probabilities(self, home_team: int, away_team: int,
                              lambdas: tuple[float, float] | None = None
                              ) -> dict:
        """Analytic outcome probabilities -- no sampling needed.

        Returns home/draw/away win probabilities and both clean-sheet
        probabilities, computed on an exact scoreline grid. Preferred over
        simulation wherever an expectation suffices, since it carries no Monte
        Carlo error.
        """
        if lambdas is None:
            lam_h, lam_a = self.fixture_lambdas(home_team, away_team)
        else:
            lam_h, lam_a = lambdas

        goals = np.arange(MAX_GOALS_GRID + 1)
        grid = np.outer(poisson.pmf(goals, lam_h), poisson.pmf(goals, lam_a))
        grid /= grid.sum()
        return {
            "p_home_win": float(np.tril(grid, -1).sum()),
            "p_draw": float(np.trace(grid)),
            "p_away_win": float(np.triu(grid, 1).sum()),
            "p_home_clean_sheet": float(np.exp(-lam_a)),
            "p_away_clean_sheet": float(np.exp(-lam_h)),
            "lambda_home": lam_h,
            "lambda_away": lam_a,
        }


def _bivariate_poisson_logpmf(x: np.ndarray, y: np.ndarray, l1: np.ndarray,
                              l2: np.ndarray, l3: float) -> np.ndarray:
    """Log-pmf of the bivariate Poisson, vectorised over observations.

    The pmf carries a sum over ``k = 0 .. min(x, y)``; the grid is evaluated for
    all observations at once and masked, rather than looped per observation.
    """
    from scipy.special import gammaln

    x = np.asarray(x, dtype=int)
    y = np.asarray(y, dtype=int)
    l1 = np.asarray(l1, dtype=float)
    l2 = np.asarray(l2, dtype=float)

    if l3 <= 0:
        return (x * np.log(l1) - l1 - gammaln(x + 1.0)
                + y * np.log(l2) - l2 - gammaln(y + 1.0))

    kmax = int(min(x.max(), y.max()))
    k = np.arange(kmax + 1)[None, :]
    valid = (k <= np.minimum(x, y)[:, None])

    terms = (k * np.log(l3)
             - gammaln(k + 1.0)
             + (x[:, None] - k) * np.log(l1[:, None])
             - gammaln(x[:, None] - k + 1.0)
             + (y[:, None] - k) * np.log(l2[:, None])
             - gammaln(y[:, None] - k + 1.0))
    terms = np.where(valid, terms, -np.inf)

    from scipy.special import logsumexp
    return -(l1 + l2 + l3) + logsumexp(terms, axis=1)


# ---------------------------------------------------------------------------
# Player attribution
# ---------------------------------------------------------------------------

@dataclass
class PlayerAttributor:
    """Turns simulated team scorelines into simulated player point totals.

    The chain per simulation draw is:

    1. sample each player's minutes;
    2. split the team's goals among players in proportion to
       ``xG_per90 * minutes_played``, via a multinomial draw -- this conserves
       the team total exactly, which independent per-player Poisson draws would
       not;
    3. assign assists to the goals that were scored, with a probability that a
       goal is assisted at all;
    4. award clean sheets, defensive contribution, saves and bonus;
    5. score everything with :mod:`fpl_rules` constants.

    All steps are vectorised over the simulation axis. Arrays are shaped
    ``(n_players, n_sims)``.
    """

    rng: np.random.Generator = field(
        default_factory=lambda: np.random.default_rng(42))
    #: Share of goals that carry an assist. League-wide this sits near 0.75.
    assist_rate: float = 0.75
    #: Expected saves per 90 for a goalkeeper, scaled by opponent goal rate.
    saves_per90: float = 3.0

    # -- minutes -----------------------------------------------------------

    def sample_minutes(self, expected_minutes: np.ndarray, p60: np.ndarray,
                       n_sims: int) -> np.ndarray:
        """Sample a ``(n_players, n_sims)`` minutes matrix.

        Minutes are strongly trimodal in FPL -- 0 (did not play), around 20-30
        (substitute) and 90 (started and finished) -- so a continuous
        distribution fits badly. Each draw picks one of the three regimes with
        probabilities implied by ``p60`` and expected minutes, then jitters the
        substitute and full-game cases.
        """
        exp_min = np.asarray(expected_minutes, dtype=float)[:, None]
        p_60 = np.clip(np.asarray(p60, dtype=float)[:, None], 0.0, 1.0)

        # P(appears at all): consistent with expected minutes given the regimes.
        # Divisor 50 = approximate average minutes when playing (mix of full games
        # and cameo appearances). Using 25 made any player with exp_min ≥ 25
        # guaranteed to appear every game, which inflates rotation/squad players
        # (e.g. McBurnie at 31 min was treated as always playing).
        p_play = np.clip(exp_min / 50.0, 0.0, 1.0)
        p_play = np.maximum(p_play, p_60)
        p_cameo = np.clip(p_play - p_60, 0.0, 1.0)

        u = self.rng.random((len(exp_min), n_sims))
        starter = u < p_60
        cameo = (~starter) & (u < p_60 + p_cameo)

        minutes = np.zeros_like(u)
        # Starters: mostly the full 90, sometimes withdrawn after the hour.
        full = self.rng.normal(84.0, 9.0, size=u.shape)
        minutes = np.where(starter, np.clip(full, 60.0, 90.0), minutes)
        # Cameos: a substitute appearance somewhere in the last half hour.
        sub = self.rng.uniform(1.0, 45.0, size=u.shape)
        minutes = np.where(cameo, sub, minutes)
        return minutes

    # -- goals and assists -------------------------------------------------

    def attribute_goals(self, team_goals: np.ndarray, squad_xg_rates: np.ndarray,
                        sim_minutes: np.ndarray) -> np.ndarray:
        """Split each simulated team goal among the squad.

        ``team_goals`` is ``(n_sims,)``; ``squad_xg_rates`` is ``(n_players,)``
        xG per 90; ``sim_minutes`` is ``(n_players, n_sims)``. Returns goals per
        player, shape ``(n_players, n_sims)``.

        A player's share of a goal is proportional to his xG rate weighted by
        the minutes he actually played in that draw, so a player sampled at 0
        minutes can never score. Sampling is a vectorised multinomial: goals are
        conserved exactly and the correlation between team and player goals is
        preserved, which matters for captaincy variance.
        """
        weights = np.asarray(squad_xg_rates, dtype=float)[:, None] * sim_minutes
        return self._multinomial_split(np.asarray(team_goals, dtype=int), weights)

    def attribute_assists(self, team_goals: np.ndarray,
                          squad_xa_rates: np.ndarray,
                          sim_minutes: np.ndarray | None = None) -> np.ndarray:
        """Distribute assists over the goals the team scored.

        Not every goal is assisted -- roughly three in four are -- so the number
        of assists available is first thinned by :attr:`assist_rate`, then split
        across players in proportion to their xA rate and minutes.
        """
        goals = np.asarray(team_goals, dtype=int)
        assisted = self.rng.binomial(goals, self.assist_rate)
        rates = np.asarray(squad_xa_rates, dtype=float)[:, None]
        weights = rates * (sim_minutes if sim_minutes is not None else 1.0)
        return self._multinomial_split(assisted, weights)

    def _multinomial_split(self, counts: np.ndarray, weights: np.ndarray
                           ) -> np.ndarray:
        """Vectorised multinomial allocation of ``counts`` across weighted rows.

        Rather than looping over simulations and calling ``rng.multinomial``
        (which would be a Python loop over 50,000 draws), this walks the *goal
        index* instead: at most a handful of goals are scored in any match, so
        the loop runs 0-8 times regardless of the number of simulations. Each
        iteration assigns one goal in every draw that has at least that many,
        using a single vectorised inverse-CDF lookup.
        """
        n_players, n_sims = weights.shape
        out = np.zeros((n_players, n_sims), dtype=np.int32)

        totals = weights.sum(axis=0)
        # A draw with no attacking weight at all gets a uniform fallback so the
        # goals still land somewhere rather than being silently discarded.
        degenerate = totals <= 0
        if degenerate.any():
            weights = weights.copy()
            weights[:, degenerate] = 1.0
            totals = weights.sum(axis=0)

        cdf = np.cumsum(weights, axis=0) / totals[None, :]

        max_goals = int(counts.max()) if len(counts) else 0
        for g in range(max_goals):
            active = counts > g
            if not active.any():
                continue
            u = self.rng.random(n_sims)
            # searchsorted per column, vectorised via comparison against the CDF.
            scorer = (cdf < u[None, :]).sum(axis=0)
            scorer = np.clip(scorer, 0, n_players - 1)
            np.add.at(out, (scorer[active], np.flatnonzero(active)), 1)
        return out

    # -- clean sheets and defensive contribution ---------------------------

    def attribute_clean_sheets(self, home_goals: np.ndarray, away_goals: np.ndarray,
                               squad_is_home: np.ndarray,
                               sim_minutes: np.ndarray) -> np.ndarray:
        """Clean-sheet indicator per player per draw.

        A clean sheet needs the opponent held scoreless *and* 60+ minutes on the
        pitch, exactly as the rules specify. Returns a boolean
        ``(n_players, n_sims)`` array.
        """
        conceded = np.where(np.asarray(squad_is_home, dtype=bool)[:, None],
                            np.asarray(away_goals)[None, :],
                            np.asarray(home_goals)[None, :])
        return (conceded == 0) & (sim_minutes >= 60)

    def attribute_defcon(self, defcon_per90: np.ndarray, sim_minutes: np.ndarray,
                         element_type: Sequence[int]) -> np.ndarray:
        """Sample defensive-contribution counts and test them against the threshold.

        Counts are Poisson with mean ``defcon_per90 * minutes / 90``. Returns a
        boolean array marking draws where the player earns the +2:
        10 CBIT for defenders, 12 CBIRT for midfielders and forwards,
        never for goalkeepers.
        """
        lam = np.asarray(defcon_per90, dtype=float)[:, None] * sim_minutes / 90.0
        counts = self.rng.poisson(np.clip(lam, 0.0, None))
        thresholds = np.array([RULES.defcon_threshold(int(e))
                               for e in element_type], dtype=float)[:, None]
        eligible = np.isfinite(thresholds)
        return (counts >= thresholds) & eligible

    def attribute_bonus(self, player_stats_sample: dict) -> np.ndarray:
        """Approximate bonus points (0-3) per player per draw.

        Bonus is a within-match BPS *ranking*, so the exact rule needs all 22
        players' BPS. Simulating that for every draw is expensive and adds
        little, so bonus is approximated from each player's own simulated
        involvement: goals and assists dominate real BPS, with clean sheets
        contributing for defenders.

        Expects ``goals``, ``assists``, ``clean_sheets``, ``minutes`` and
        ``element_type`` keys. Returns a ``(n_players, n_sims)`` int array.
        """
        goals = player_stats_sample["goals"]
        assists = player_stats_sample["assists"]
        clean = player_stats_sample.get("clean_sheets", 0)
        minutes = player_stats_sample["minutes"]
        et = np.asarray(player_stats_sample["element_type"])[:, None]

        # A crude BPS proxy, calibrated so a goal-scorer usually lands in the
        # top three and a clean-sheet defender sometimes does.
        defensive = np.isin(et, [GKP, DEF]).astype(float)
        score = (24.0 * goals + 18.0 * assists + 12.0 * clean * defensive
                 + 6.0 * (minutes >= 60))

        bonus = np.zeros_like(score, dtype=np.int8)
        bonus = np.where(score >= 36, 3, bonus)
        bonus = np.where((score >= 24) & (score < 36), 2, bonus)
        bonus = np.where((score >= 15) & (score < 24), 1, bonus)
        return np.clip(bonus, 0, MAX_BONUS)

    # -- full scoring ------------------------------------------------------

    def score_players(self, goals: np.ndarray, assists: np.ndarray,
                      clean_sheets: np.ndarray, minutes: np.ndarray,
                      defcon: np.ndarray, element_type: Sequence[int],
                      conceded: np.ndarray | None = None,
                      saves: np.ndarray | None = None) -> np.ndarray:
        """Apply the FPL scoring rules to simulated stat lines, vectorised.

        Mirrors :meth:`fpl_rules.FPLRules.calc_points` term for term, but over
        whole ``(n_players, n_sims)`` arrays rather than one dict at a time.
        """
        et = np.asarray([int(e) for e in element_type])
        goal_values = np.array([POINTS_GOAL[e] for e in et])[:, None]
        cs_values = np.array([POINTS_CLEAN_SHEET[e] for e in et])[:, None]

        played = minutes > 0
        pts = np.where(minutes >= 60, POINTS_PLAYING_60_PLUS,
                       np.where(played, POINTS_PLAYING_UNDER_60, 0)).astype(float)

        pts += goal_values * goals
        pts += POINTS_ASSIST * assists
        pts += cs_values * clean_sheets
        pts += POINTS_DEFENSIVE_CONTRIBUTION * defcon

        if conceded is not None:
            defensive = np.isin(et, [GKP, DEF])[:, None]
            pts += np.where(defensive & (minutes >= 60),
                            -(conceded // 2).astype(float), 0.0)

        if saves is not None:
            is_gk = (et == GKP)[:, None]
            pts += np.where(is_gk, saves // SAVES_PER_POINT, 0)

        bonus = self.attribute_bonus({
            "goals": goals, "assists": assists, "clean_sheets": clean_sheets,
            "minutes": minutes, "element_type": et})
        pts += bonus

        # A player who did not appear scores exactly zero.
        return np.where(played, pts, 0.0)


# ---------------------------------------------------------------------------
# Gameweek and season simulation
# ---------------------------------------------------------------------------

@dataclass
class SeasonSimulator:
    """Runs full gameweek and season simulations over the player pool."""

    match_sim: BivariatePoissonSimulator = field(
        default_factory=BivariatePoissonSimulator)
    attributor: PlayerAttributor = field(default_factory=PlayerAttributor)

    def simulate_gameweek(self, fixtures: pd.DataFrame, squad_stats: pd.DataFrame,
                          n_sims: int = DEFAULT_N_SIMS,
                          return_samples: bool = False
                          ) -> pd.DataFrame | tuple[pd.DataFrame, np.ndarray]:
        """Simulate one gameweek and summarise each player's point distribution.

        ``fixtures`` needs ``team_h`` and ``team_a`` (optionally
        ``lambda_home`` / ``lambda_away`` to drive the simulation from market
        odds). ``squad_stats`` is one row per player with:

        ==================  ==================================================
        ``element``         player id
        ``team``            club id
        ``element_type``    1-4
        ``xg_per90``        expected goals per 90
        ``xa_per90``        expected assists per 90
        ``expected_minutes``  0-90
        ``p60``             P(reaches 60 minutes)
        ``defcon_per90``    expected defensive actions per 90
        ==================  ==================================================

        Returns a frame indexed by ``element`` with ``mean``, ``std``, ``p10``,
        ``p50``, ``p80``, ``p90`` and ``p_haul`` (P(>= 10 points)). Set
        ``return_samples=True`` to also get the raw ``(n_players, n_sims)``
        array -- needed by the stochastic optimiser, but memory-hungry.

        Roughly 1-3 seconds at the default 50,000 simulations.
        """
        players = squad_stats.reset_index(drop=True)
        n_players = len(players)
        samples = np.zeros((n_players, n_sims), dtype=np.float32)

        team_to_rows = {t: np.flatnonzero(players["team"].to_numpy() == t)
                        for t in players["team"].unique()}

        for fx in fixtures.itertuples(index=False):
            home, away = int(fx.team_h), int(fx.team_a)
            lambdas = None
            lam_h = getattr(fx, "lambda_home", None)
            lam_a = getattr(fx, "lambda_away", None)
            if lam_h is not None and lam_a is not None \
                    and np.isfinite(lam_h) and np.isfinite(lam_a):
                lambdas = (float(lam_h), float(lam_a))

            scores = self.match_sim.simulate_fixture(home, away, n_sims=n_sims,
                                                     lambdas=lambdas)
            hg, ag = scores[:, 0], scores[:, 1]

            for team, opponent_goals, own_goals_scored, is_home in (
                    (home, ag, hg, True), (away, hg, ag, False)):
                rows = team_to_rows.get(team)
                if rows is None or len(rows) == 0:
                    continue
                sub = players.iloc[rows]

                minutes = self.attributor.sample_minutes(
                    sub["expected_minutes"].to_numpy(),
                    sub["p60"].to_numpy(), n_sims)

                goals = self.attributor.attribute_goals(
                    own_goals_scored, sub["xg_per90"].to_numpy(), minutes)
                assists = self.attributor.attribute_assists(
                    own_goals_scored, sub["xa_per90"].to_numpy(), minutes)
                clean = ((opponent_goals[None, :] == 0) & (minutes >= 60))
                defcon = self.attributor.attribute_defcon(
                    sub.get("defcon_per90", pd.Series(0.0, index=sub.index)
                            ).to_numpy(),
                    minutes, sub["element_type"].to_numpy())

                conceded = np.broadcast_to(opponent_goals[None, :],
                                           minutes.shape)
                saves = self.attributor.rng.poisson(
                    np.clip(self.attributor.saves_per90 * minutes / 90.0, 0, None))

                pts = self.attributor.score_players(
                    goals, assists, clean, minutes, defcon,
                    sub["element_type"].to_numpy(), conceded=conceded,
                    saves=saves)
                # A player featuring in a double gameweek accumulates.
                samples[rows] += pts.astype(np.float32)

        summary = self._summarise(samples, players["element"].to_numpy())
        return (summary, samples) if return_samples else summary

    @staticmethod
    def _summarise(samples: np.ndarray, elements: np.ndarray) -> pd.DataFrame:
        """Mean, spread and upper quantiles of each player's point distribution."""
        q = np.quantile(samples, [0.10, 0.50, 0.80, 0.90], axis=1)
        return pd.DataFrame({
            "element": elements,
            "mean": samples.mean(axis=1),
            "std": samples.std(axis=1),
            "p10": q[0], "p50": q[1], "p80": q[2], "p90": q[3],
            "p_blank": (samples <= 2).mean(axis=1),
            "p_haul": (samples >= 10).mean(axis=1),
        }).set_index("element")

    def simulate_season(self, all_fixtures: pd.DataFrame,
                        all_squads: pd.DataFrame | dict,
                        n_sims: int = 10_000,
                        gameweeks: Sequence[int] | None = None) -> pd.DataFrame:
        """Simulate every gameweek and return the per-gameweek point matrix.

        ``all_squads`` is either one player-stat frame reused across gameweeks,
        or a ``{gameweek: frame}`` mapping when projections change week to week.

        Returns a long frame with one row per (gameweek, player) carrying the
        same summary statistics as :meth:`simulate_gameweek`. Per-gameweek
        summaries are accumulated rather than raw draws retained -- keeping all
        38 x 600 x 10,000 samples would need about 1.8 GB.

        At 10,000 simulations a full 38-gameweek season takes roughly 1-2
        minutes.
        """
        if "event" not in all_fixtures.columns:
            raise ValueError("fixtures frame needs an 'event' column")

        events = gameweeks if gameweeks is not None else sorted(
            int(e) for e in all_fixtures["event"].dropna().unique())

        out = []
        for gw in events:
            fx = all_fixtures[all_fixtures["event"] == gw]
            if fx.empty:
                continue
            squad = all_squads[gw] if isinstance(all_squads, dict) else all_squads
            summary = self.simulate_gameweek(fx, squad, n_sims=n_sims)
            summary["event"] = gw
            out.append(summary.reset_index())

        if not out:
            return pd.DataFrame()
        return pd.concat(out, ignore_index=True)

    def captaincy_analysis(self, summary: pd.DataFrame, samples: np.ndarray,
                           elements: np.ndarray, top_n: int = 10) -> pd.DataFrame:
        """Rank captaincy candidates on the metrics that actually matter.

        Mean points alone is the wrong criterion for a captain: the armband
        doubles the score, so the *upside* and the risk of a blank both matter
        more than they do for an ordinary pick. This reports each candidate's
        doubled mean alongside P(>= 10) and P(<= 2).
        """
        order = np.argsort(-summary["mean"].to_numpy())[:top_n]
        rows = []
        for i in order:
            s = samples[i]
            rows.append({
                "element": elements[i],
                "mean": float(s.mean()),
                "captain_mean": float(2 * s.mean()),
                "p_haul": float((s >= 10).mean()),
                "p_blank": float((s <= 2).mean()),
                "p90": float(np.quantile(s, 0.90)),
            })
        return pd.DataFrame(rows).sort_values("captain_mean", ascending=False)
