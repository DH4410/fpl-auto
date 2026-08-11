"""
RandomForest counterparts to the four sub-models, plus a model-averaging wrapper.

The gradient-boosted models in :mod:`models` are the production pipeline; these
variants exist to answer a different question -- how much of the backtest score
is driven by the *decomposition* (minutes x rates x FPL scoring rules) versus by
the particular learner underneath it. Swapping XGBoost/LightGBM for random
forests changes the learner while holding the decomposition fixed.

Each RF sub-model subclasses its gradient-boosted counterpart and overrides
``train`` only, storing the fitted estimator under the same attribute name the
parent's ``predict`` reads (``model``, ``xg_model``/``xa_model``,
``cs_model``/``defcon_model``). Everything downstream -- the Poisson defensive-
contribution threshold, the empirical BPS->bonus curve, the positional priors
and the whole points composition in
:meth:`~bot.models.FPLPointsPredictor.predict` -- is therefore inherited rather
than copied, so the two families cannot silently drift apart. A copy-pasted
points formula that fell one term behind would make every family comparison
meaningless.

The one genuine gap is P(60+ minutes). :class:`~bot.models.MinutesModel` trains
a dedicated XGBoost classifier for it; the RF variant has no such head and
derives a monotone proxy from expected minutes instead. That is a real
difference between the families, not an incidental one, and it is called out in
the backtest report.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .models import (
    MAX_MINUTES, AttackModel, BonusModel, DefenseModel, FPLPointsPredictor,
    MinutesModel,
)

log = logging.getLogger(__name__)


def _require_sklearn():
    try:
        from sklearn.ensemble import (  # noqa: PLC0415
            RandomForestClassifier, RandomForestRegressor,
        )
        return RandomForestClassifier, RandomForestRegressor
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "scikit-learn is required for the RF model variants: "
            "pip install scikit-learn"
        ) from exc


# ---------------------------------------------------------------------------
# Model A -- minutes
# ---------------------------------------------------------------------------

class MinutesRFModel(MinutesModel):
    """Expected minutes from a random forest, 0-90.

    Loses the asymmetric objective -- a forest averages leaf means and cannot
    carry a custom gradient -- so unlike the XGBoost version this model has no
    built-in bias against over-predicting a rotation risk's minutes.
    """

    name = "minutes_rf"

    def __init__(self, n_estimators: int = 300, max_depth: int = 8,
                 random_state: int = 42) -> None:
        super().__init__(random_state=random_state)
        self.params = dict(n_estimators=n_estimators, max_depth=max_depth,
                           random_state=random_state, n_jobs=-1)

    def train(self, X: pd.DataFrame, y: pd.Series | np.ndarray,
              y_p60: pd.Series | np.ndarray | None = None) -> "MinutesRFModel":
        """Fit the minutes regressor. ``y_p60`` is accepted but unused."""
        _, RandomForestRegressor = _require_sklearn()
        Xn = self._prepare(X, fit=True)
        self.model = RandomForestRegressor(**self.params)
        self.model.fit(Xn, np.asarray(y, dtype=float))
        self.p60_model = None
        self.fitted = True
        return self

    def predict_p60(self, X: pd.DataFrame) -> np.ndarray:
        """P(60+ minutes) proxied from expected minutes.

        No classifier head exists here, so this is a monotone transform of the
        regressed minutes rather than a calibrated probability. The 0.9 ceiling
        keeps even a nailed-on 90-minute player short of certainty.
        """
        mins = self.predict(X)
        return np.clip(mins / MAX_MINUTES, 0.0, 1.0) * 0.9


# ---------------------------------------------------------------------------
# Model B -- attack
# ---------------------------------------------------------------------------

class AttackRFModel(AttackModel):
    """xG/90 and xA/90 from two random forests."""

    name = "attack_rf"

    def __init__(self, n_estimators: int = 300, max_depth: int = 8,
                 random_state: int = 42) -> None:
        super().__init__(random_state=random_state)
        self.params = dict(n_estimators=n_estimators, max_depth=max_depth,
                           random_state=random_state, n_jobs=-1)

    def train(self, X: pd.DataFrame, y_xg90: pd.Series | np.ndarray,
              y_xa90: pd.Series | np.ndarray) -> "AttackRFModel":
        """Fit both heads on rows where each target is present.

        Callers already mask to rows with both rates defined; the forests cannot
        accept a NaN label at all, so the guard is repeated here.
        """
        _, RandomForestRegressor = _require_sklearn()
        Xn = self._prepare(X, fit=True)
        xg = np.asarray(y_xg90, dtype=float)
        xa = np.asarray(y_xa90, dtype=float)
        usable = np.isfinite(xg) & np.isfinite(xa)

        self.xg_model = RandomForestRegressor(**self.params)
        self.xg_model.fit(Xn[usable], xg[usable])

        self.xa_model = RandomForestRegressor(**self.params)
        self.xa_model.fit(Xn[usable], xa[usable])

        self.fitted = True
        return self


# ---------------------------------------------------------------------------
# Model C -- defense
# ---------------------------------------------------------------------------

class DefenseRFModel(DefenseModel):
    """Clean-sheet probability (forest classifier) and DC rate (forest regressor).

    Keeps the parent's data-availability behaviour: the DC head is fitted only
    when enough rows actually carry the statistic, and falls back to the
    positional rate priors otherwise. Training on 2022-23 + 2023-24 that
    fallback is always taken, since those seasons predate the DC rule.
    """

    name = "defense_rf"

    def __init__(self, n_estimators: int = 300, max_depth: int = 6,
                 random_state: int = 42) -> None:
        super().__init__(random_state=random_state)
        self.params = dict(n_estimators=n_estimators, max_depth=max_depth,
                           random_state=random_state, n_jobs=-1)

    def train(self, X: pd.DataFrame, y_clean_sheet: pd.Series | np.ndarray,
              y_defcon_per90: pd.Series | np.ndarray | None = None
              ) -> "DefenseRFModel":
        RandomForestClassifier, RandomForestRegressor = _require_sklearn()
        Xn = self._prepare(X, fit=True)
        cs = np.asarray(y_clean_sheet, dtype=int)

        # class_weight is the forest's analogue of scale_pos_weight: clean
        # sheets are the minority class in every season.
        self.cs_model = RandomForestClassifier(class_weight="balanced",
                                               **self.params)
        self.cs_model.fit(Xn, cs)

        if y_defcon_per90 is not None:
            dc = pd.Series(np.asarray(y_defcon_per90, dtype=float))
            usable = dc.notna().to_numpy()
            n_usable = int(usable.sum())
            if n_usable >= 100:
                self.defcon_model = RandomForestRegressor(**self.params)
                self.defcon_model.fit(Xn[usable], dc[usable].to_numpy())
                self.defcon_is_rule_based = False
                log.info("DefenseRFModel: DC head trained on %d rows", n_usable)
            else:
                log.warning(
                    "DefenseRFModel: only %d rows carry defensive-contribution "
                    "data (need >=100); falling back to positional rate priors.",
                    n_usable)

        self.fitted = True
        return self


# ---------------------------------------------------------------------------
# Model D -- bonus
# ---------------------------------------------------------------------------

class BonusRFModel(BonusModel):
    """Expected BPS per appearance from a random forest.

    Fits the same empirical BPS->bonus curve as the parent when bonus labels are
    supplied -- without it this family would fall back to the crude linear
    heuristic while the boosted family used the fitted curve, putting an
    uncontrolled difference into the comparison.
    """

    name = "bonus_rf"

    def __init__(self, n_estimators: int = 200, max_depth: int = 6,
                 random_state: int = 42) -> None:
        super().__init__(random_state=random_state)
        self.params = dict(n_estimators=n_estimators, max_depth=max_depth,
                           random_state=random_state, n_jobs=-1)

    def train(self, X: pd.DataFrame, y_bps: pd.Series | np.ndarray,
              y_bonus: pd.Series | np.ndarray | None = None) -> "BonusRFModel":
        _, RandomForestRegressor = _require_sklearn()
        Xn = self._prepare(X, fit=True)
        bps = np.asarray(y_bps, dtype=float)
        self.model = RandomForestRegressor(**self.params)
        self.model.fit(Xn, bps)
        self.fitted = True

        if y_bonus is not None:
            self.fit_bonus_curve(bps, np.asarray(y_bonus, dtype=float))
        return self


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------

@dataclass
class FPLRandomForestPredictor(FPLPointsPredictor):
    """The four RF sub-models behind the inherited points composition.

    ``predict`` is deliberately *not* overridden: the expected-points formula,
    the appearance/clean-sheet gating on p60 and every scoring constant come
    straight from :class:`~bot.models.FPLPointsPredictor`, so the two families
    differ only in the learners. Sub-models are rebuilt in ``__post_init__``
    from ``random_state``, which is what the multi-seed backtest varies.
    """

    random_state: int = 42

    def __post_init__(self) -> None:
        self.minutes_model = MinutesRFModel(random_state=self.random_state)
        self.attack_model = AttackRFModel(random_state=self.random_state)
        self.defense_model = DefenseRFModel(random_state=self.random_state)
        self.bonus_model = BonusRFModel(random_state=self.random_state)


@dataclass
class EnsembleFPLPredictor:
    """Averages expected points and p60 across several trained predictors.

    Only the two columns the backtester consumes are averaged --
    ``expected_points`` drives transfers and XI selection, ``p60`` drives the
    captain risk filter. The remaining component columns are passed through from
    the first predictor so the frame stays inspectable; averaging them would
    imply a coherence across families that does not exist.
    """

    predictors: list = field(default_factory=list)

    def predict(self, X: pd.DataFrame, element_type, **kwargs) -> pd.DataFrame:
        if not self.predictors:
            raise ValueError("EnsembleFPLPredictor has no predictors")

        frames = [p.predict(X, element_type, **kwargs) for p in self.predictors]
        out = frames[0].copy()
        out["expected_points"] = np.mean(
            [f["expected_points"].to_numpy(dtype=float) for f in frames], axis=0)
        # p60 comes from the first predictor only — it should be the calibrated
        # XGBoost classifier. Averaging with the RF's monotone proxy would pull
        # the probability down and shrink the captain-eligible pool.
        out["p60"] = frames[0]["p60"].to_numpy(dtype=float)
        return out
