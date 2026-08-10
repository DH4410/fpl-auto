"""
Module 2 -- the four ML sub-models and the points predictor that orchestrates them.

Rather than regressing FPL points directly, the pipeline predicts the *components*
of the scoring system and then composes them through the exact rules in
:mod:`fpl_rules`. Direct points regression wastes the enormous amount of
structure FPL hands you for free: a defender's points are a deterministic
function of minutes, goals, assists, clean sheets, defensive contribution, cards
and bonus, and each of those has different drivers and different noise.

The four sub-models
-------------------
==================  ====================================================
:class:`MinutesModel`  expected minutes (0-90), asymmetric loss
:class:`AttackModel`   xG per 90 and xA per 90, two regressors
:class:`DefenseModel`  clean-sheet probability and defensive-contribution rate
:class:`BonusModel`    BPS per appearance
==================  ====================================================

:class:`FPLPointsPredictor` combines them into E[points] per player per
gameweek, applying the scoring constants from :mod:`fpl_rules`.

Two design choices worth stating
--------------------------------
**Asymmetric minutes loss.** Over-predicting minutes is far more costly than
under-predicting: a player you expect to start and who is benched delivers zero
and, worse, the optimiser has already spent budget on him. :class:`MinutesModel`
uses a custom XGBoost objective that weights over-prediction 3x, which shifts
predictions down for rotation risks.

**Defensive contribution is trained on 2025/26 only.** Verified against the
source data: ``data/2024-25/gws/merged_gw.csv`` has no
``clearances_blocks_interceptions``, ``recoveries``, ``tackles`` or
``defensive_contribution`` columns -- the DC rule did not exist that season, so
the statistic was never recorded there. Those columns first appear in 2025/26.
The task spec's suggested fallback ("compute CBIT from
``clearances_blocks_interceptions``") is therefore not possible on 2024/25 data,
because that column is itself absent. :class:`DefenseModel` handles this by
training the DC head only on rows from seasons that actually carry the columns,
and falling back to a positional rate prior for everyone else. It never
zero-fills: zero would assert "this defender made no clearances", which is
false rather than unknown.
"""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .fpl_rules import (
    DEF, FWD, GKP, MID, POINTS_ASSIST, POINTS_CLEAN_SHEET, POINTS_GOAL,
    POINTS_DEFENSIVE_CONTRIBUTION, POINTS_PLAYING_60_PLUS,
    POINTS_PLAYING_UNDER_60, RULES, SAVES_PER_POINT,
)

log = logging.getLogger(__name__)

MAX_MINUTES = 90.0

#: How much more heavily over-prediction is penalised in the minutes model.
MINUTES_OVERPREDICTION_PENALTY = 3.0

#: Fallback defensive-contribution rates per 90 by position, used when a player
#: has no DC history at all. Anchored on 2025/26 league-wide medians; refresh
#: with :meth:`DefenseModel.calibrate_position_priors` once data is loaded.
DEFAULT_DEFCON_PER90 = {GKP: 0.0, DEF: 7.5, MID: 5.5, FWD: 2.5}

#: Typical BPS per appearance by position, used as a cold-start prior.
DEFAULT_BPS_PER_APPEARANCE = {GKP: 16.0, DEF: 14.0, MID: 12.0, FWD: 11.0}


def _require_xgboost():
    try:
        import xgboost as xgb  # noqa: PLC0415
        return xgb
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "xgboost is required for the ML models: pip install xgboost"
        ) from exc


def _require_lightgbm():
    try:
        import lightgbm as lgb  # noqa: PLC0415
        return lgb
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "lightgbm is required for this model: pip install lightgbm"
        ) from exc


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseModel:
    """Shared fit/predict/save/load plumbing for the sub-models.

    Persistence uses pickle for the estimator plus a JSON sidecar for the
    feature list, so a loaded model cannot silently be fed columns in a
    different order -- a failure mode that produces plausible-looking garbage
    rather than an error.
    """

    name = "base"

    def __init__(self) -> None:
        self.features: list[str] = []
        self.fitted = False

    # -- helpers -----------------------------------------------------------

    def _prepare(self, X: pd.DataFrame, fit: bool = False) -> np.ndarray:
        if fit:
            self.features = [c for c in X.columns
                             if pd.api.types.is_numeric_dtype(X[c])]
            if not self.features:
                raise ValueError("no numeric feature columns supplied")
            return X[self.features].to_numpy(dtype=float)

        missing = [c for c in self.features if c not in X.columns]
        if missing:
            raise ValueError(f"{self.name}: missing feature columns {missing}")
        return X[self.features].to_numpy(dtype=float)

    def _check_fitted(self) -> None:
        if not self.fitted:
            raise RuntimeError(f"{self.name} has not been trained yet")

    # -- persistence -------------------------------------------------------

    def save(self, path: str | Path) -> Path:
        """Pickle the model and write a JSON sidecar of the feature order."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(self, fh)
        meta = {"name": self.name, "features": self.features,
                "fitted": self.fitted}
        path.with_suffix(path.suffix + ".json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: str | Path):
        """Load a model previously written by :meth:`save`."""
        with Path(path).open("rb") as fh:
            obj = pickle.load(fh)
        if not isinstance(obj, cls):
            log.warning("loaded a %s from %s, expected %s",
                        type(obj).__name__, path, cls.__name__)
        return obj

    def feature_importances(self, top: int = 20) -> pd.DataFrame:
        """Gain-based importances for the primary estimator, most important first."""
        self._check_fitted()
        est = getattr(self, "model", None) or getattr(self, "_primary", None)
        if est is None or not hasattr(est, "feature_importances_"):
            return pd.DataFrame(columns=["feature", "importance"])
        imp = pd.DataFrame({"feature": self.features,
                            "importance": est.feature_importances_})
        return imp.sort_values("importance", ascending=False).head(top).reset_index(
            drop=True)


# ---------------------------------------------------------------------------
# Model A -- minutes
# ---------------------------------------------------------------------------

class AsymmetricMinutesObjective:
    """Custom XGBoost objective penalising over-prediction ``penalty`` times more.

    Squared error, reweighted by which side of the truth the prediction falls::

        loss = w * (pred - true)^2,   w = penalty if pred > true else 1

    Gradient and hessian follow directly. In FPL terms this makes the model
    conservative about minutes: it would rather tell you a rotation risk plays
    55 minutes than 80, because the optimiser's downside from believing an
    inflated minutes figure is much worse than from missing a haul.

    Implemented as a callable class rather than a closure so that a trained
    :class:`MinutesModel` can be pickled -- a nested function cannot be.
    """

    def __init__(self, penalty: float = MINUTES_OVERPREDICTION_PENALTY) -> None:
        self.penalty = float(penalty)

    def __call__(self, y_true: np.ndarray, y_pred: np.ndarray):
        residual = y_pred - y_true
        weight = np.where(residual > 0, self.penalty, 1.0)
        return 2.0 * weight * residual, 2.0 * weight


def asymmetric_minutes_objective(penalty: float = MINUTES_OVERPREDICTION_PENALTY
                                 ) -> AsymmetricMinutesObjective:
    """Build the asymmetric minutes objective. See :class:`AsymmetricMinutesObjective`."""
    return AsymmetricMinutesObjective(penalty)


class MinutesModel(BaseModel):
    """Expected minutes played, 0-90.

    Implements the asymmetric-loss minutes model: an XGBoost regressor with the
    custom objective from :func:`asymmetric_minutes_objective`, plus a companion
    classifier for P(plays 60+ minutes), which is what the clean-sheet and
    appearance-point calculations actually need. Predicting the probability
    directly is better calibrated than thresholding the regressed minutes.

    Predictions are clipped to [0, 90].
    """

    name = "minutes"

    def __init__(self, penalty: float = MINUTES_OVERPREDICTION_PENALTY,
                 n_estimators: int = 400, max_depth: int = 5,
                 learning_rate: float = 0.05, random_state: int = 42) -> None:
        super().__init__()
        self.penalty = penalty
        self.params = dict(n_estimators=n_estimators, max_depth=max_depth,
                           learning_rate=learning_rate, subsample=0.8,
                           colsample_bytree=0.8, random_state=random_state,
                           n_jobs=-1)
        self.model = None
        self.p60_model = None

    def train(self, X: pd.DataFrame, y: pd.Series | np.ndarray,
              y_p60: pd.Series | np.ndarray | None = None) -> "MinutesModel":
        """Fit both heads. ``y`` is minutes; ``y_p60`` defaults to ``y >= 60``."""
        xgb = _require_xgboost()
        Xn = self._prepare(X, fit=True)
        y = np.asarray(y, dtype=float)

        self.model = xgb.XGBRegressor(objective=asymmetric_minutes_objective(
            self.penalty), **self.params)
        self.model.fit(Xn, y)

        p60 = np.asarray(y_p60 if y_p60 is not None else (y >= 60), dtype=int)
        # scale_pos_weight balances the starter/non-starter imbalance, which is
        # roughly 60/40 across a full squad list but far more skewed on bench
        # players.
        n_pos = max(1, int(p60.sum()))
        n_neg = max(1, len(p60) - n_pos)
        self.p60_model = xgb.XGBClassifier(
            eval_metric="logloss", scale_pos_weight=n_neg / n_pos, **self.params)
        self.p60_model.fit(Xn, p60)

        self.fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Expected minutes, clipped to [0, 90]."""
        self._check_fitted()
        return np.clip(self.model.predict(self._prepare(X)), 0.0, MAX_MINUTES)

    def predict_p60(self, X: pd.DataFrame) -> np.ndarray:
        """P(player reaches 60 minutes) -- drives clean-sheet and appearance points."""
        self._check_fitted()
        return self.p60_model.predict_proba(self._prepare(X))[:, 1]

    def predict_pstart(self, X: pd.DataFrame) -> np.ndarray:
        """P(player appears at all), approximated from expected minutes.

        A separate head would be better; this monotone transform is a reasonable
        stand-in and avoids a third model. Players with an expected 0 minutes map
        to ~0, and anyone above ~20 expected minutes maps close to 1.
        """
        mins = self.predict(X)
        return np.clip(mins / 25.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Model B -- attack
# ---------------------------------------------------------------------------

class AttackModel(BaseModel):
    """Expected goals and assists per 90, as two separate regressors.

    Goals and assists are modelled apart because their drivers differ: xG is
    dominated by shot volume, position and penalty duty, while xA depends on
    chance creation and set-piece responsibility. Training on *per 90* rates
    rather than raw totals decouples the attacking model from the minutes model,
    so the two can be composed multiplicatively without double-counting playing
    time.

    LightGBM is used here -- it handles the many low-signal, heavily zero-
    inflated rate features slightly better than XGBoost in this setting and
    trains faster on the wide feature matrix.
    """

    name = "attack"

    def __init__(self, n_estimators: int = 500, learning_rate: float = 0.05,
                 num_leaves: int = 31, random_state: int = 42) -> None:
        super().__init__()
        self.params = dict(n_estimators=n_estimators, learning_rate=learning_rate,
                           num_leaves=num_leaves, subsample=0.8,
                           colsample_bytree=0.8, random_state=random_state,
                           n_jobs=-1, verbose=-1)
        self.xg_model = None
        self.xa_model = None

    @property
    def _primary(self):
        return self.xg_model

    def train(self, X: pd.DataFrame, y_xg90: pd.Series | np.ndarray,
              y_xa90: pd.Series | np.ndarray) -> "AttackModel":
        """Fit the xG/90 and xA/90 heads."""
        lgb = _require_lightgbm()
        Xn = self._prepare(X, fit=True)

        self.xg_model = lgb.LGBMRegressor(**self.params)
        self.xg_model.fit(Xn, np.asarray(y_xg90, dtype=float))

        self.xa_model = lgb.LGBMRegressor(**self.params)
        self.xa_model.fit(Xn, np.asarray(y_xa90, dtype=float))

        self.fitted = True
        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        """Return a frame with ``xg_per90`` and ``xa_per90``, both non-negative."""
        self._check_fitted()
        Xn = self._prepare(X)
        return pd.DataFrame({
            "xg_per90": np.clip(self.xg_model.predict(Xn), 0.0, None),
            "xa_per90": np.clip(self.xa_model.predict(Xn), 0.0, None),
        }, index=X.index)


# ---------------------------------------------------------------------------
# Model C -- defense
# ---------------------------------------------------------------------------

class DefenseModel(BaseModel):
    """Clean-sheet probability (classifier) and defensive contribution per 90.

    **Data availability drives the design here.** The defensive-contribution
    columns (``clearances_blocks_interceptions``, ``recoveries``, ``tackles``,
    ``defensive_contribution``) exist only from 2025/26 onward -- 2024/25's
    ``merged_gw.csv`` does not contain them in any form, so they cannot be
    reconstructed from that season. Consequently:

    * the clean-sheet head trains on **all** available seasons;
    * the DC head trains only on rows where a DC target is actually present;
    * when no DC training data exists at all, :meth:`predict_defcon_per90` falls
      back to the positional rate priors in :data:`DEFAULT_DEFCON_PER90`, and
      :attr:`defcon_is_rule_based` is set so callers can widen uncertainty.

    Missing DC values are never imputed as zero.
    """

    name = "defense"

    def __init__(self, n_estimators: int = 400, max_depth: int = 5,
                 learning_rate: float = 0.05, random_state: int = 42) -> None:
        super().__init__()
        self.params = dict(n_estimators=n_estimators, max_depth=max_depth,
                           learning_rate=learning_rate, subsample=0.8,
                           colsample_bytree=0.8, random_state=random_state,
                           n_jobs=-1)
        self.cs_model = None
        self.defcon_model = None
        self.defcon_is_rule_based = True
        self.position_priors = dict(DEFAULT_DEFCON_PER90)

    @property
    def _primary(self):
        return self.cs_model

    def train(self, X: pd.DataFrame, y_clean_sheet: pd.Series | np.ndarray,
              y_defcon_per90: pd.Series | np.ndarray | None = None
              ) -> "DefenseModel":
        """Fit the clean-sheet classifier and, where data allows, the DC head.

        ``y_defcon_per90`` may contain NaN for seasons without DC recording --
        those rows are dropped from the DC head rather than filled.
        """
        xgb = _require_xgboost()
        Xn = self._prepare(X, fit=True)
        cs = np.asarray(y_clean_sheet, dtype=int)

        n_pos = max(1, int(cs.sum()))
        n_neg = max(1, len(cs) - n_pos)
        self.cs_model = xgb.XGBClassifier(
            eval_metric="logloss", scale_pos_weight=n_neg / n_pos, **self.params)
        self.cs_model.fit(Xn, cs)

        if y_defcon_per90 is not None:
            dc = pd.Series(np.asarray(y_defcon_per90, dtype=float))
            usable = dc.notna().to_numpy()
            n_usable = int(usable.sum())
            if n_usable >= 100:
                self.defcon_model = xgb.XGBRegressor(**self.params)
                self.defcon_model.fit(Xn[usable], dc[usable].to_numpy())
                self.defcon_is_rule_based = False
                log.info("DefenseModel: DC head trained on %d rows", n_usable)
            else:
                log.warning(
                    "DefenseModel: only %d rows carry defensive-contribution "
                    "data (need >=100); falling back to positional rate priors. "
                    "This is expected when training on 2024-25 alone, which "
                    "predates the DC statistic.", n_usable)

        self.fitted = True
        return self

    def calibrate_position_priors(self, history_df: pd.DataFrame) -> dict:
        """Recompute positional DC/90 priors from any season that records them.

        Uses the median rather than the mean -- DC per 90 is right-skewed, and a
        handful of ball-winning midfielders would otherwise drag the prior up
        for everyone.
        """
        if "defensive_contribution" not in history_df.columns:
            log.info("no defensive_contribution column; keeping default priors")
            return self.position_priors

        df = history_df[history_df["minutes"] > 0].copy()
        df["dc90"] = (pd.to_numeric(df["defensive_contribution"], errors="coerce")
                      / df["minutes"] * 90.0)
        et = (df["element_type"] if "element_type" in df.columns
              else df["position"].map({"GK": GKP, "GKP": GKP, "DEF": DEF,
                                       "MID": MID, "FWD": FWD}))
        priors = df.assign(_et=et).groupby("_et")["dc90"].median().to_dict()
        for pos, value in priors.items():
            if pd.notna(pos) and pd.notna(value):
                self.position_priors[int(pos)] = float(value)
        log.info("calibrated DC/90 priors: %s", self.position_priors)
        return self.position_priors

    def predict_clean_sheet(self, X: pd.DataFrame) -> np.ndarray:
        """P(clean sheet), given the player is on the pitch for 60+ minutes."""
        self._check_fitted()
        return self.cs_model.predict_proba(self._prepare(X))[:, 1]

    def predict_defcon_per90(self, X: pd.DataFrame,
                             element_type: Sequence[int] | None = None
                             ) -> np.ndarray:
        """Expected defensive-contribution actions per 90.

        Uses the trained head when one exists, otherwise the positional priors.
        """
        self._check_fitted()
        if self.defcon_model is not None:
            return np.clip(self.defcon_model.predict(self._prepare(X)), 0.0, None)

        if element_type is None:
            raise ValueError(
                "no DC model was trained, so element_type is required to apply "
                "the positional fallback priors")
        return np.array([self.position_priors.get(int(et), 0.0)
                         for et in element_type], dtype=float)

    def predict_defcon_probability(self, defcon_per90: np.ndarray,
                                   expected_minutes: np.ndarray,
                                   element_type: Sequence[int]) -> np.ndarray:
        """P(player hits the DC threshold) for the +2 points.

        The count of defensive actions in a match is modelled as Poisson with
        mean ``defcon_per90 * minutes / 90``. The threshold is 10 CBIT for
        defenders and 12 CBIRT for midfielders and forwards; goalkeepers are not
        eligible and always get 0.
        """
        from scipy.stats import poisson

        lam = np.asarray(defcon_per90, dtype=float) * np.asarray(
            expected_minutes, dtype=float) / 90.0
        out = np.zeros(len(lam))
        for i, et in enumerate(element_type):
            threshold = RULES.defcon_threshold(int(et))
            if not np.isfinite(threshold):
                continue  # goalkeeper
            out[i] = float(poisson.sf(threshold - 1, max(lam[i], 1e-9)))
        return out

    def predict(self, X: pd.DataFrame,
                element_type: Sequence[int] | None = None) -> pd.DataFrame:
        """Clean-sheet probability and DC rate together."""
        return pd.DataFrame({
            "clean_sheet_prob": self.predict_clean_sheet(X),
            "defcon_per90": self.predict_defcon_per90(X, element_type),
        }, index=X.index)


# ---------------------------------------------------------------------------
# Model D -- bonus
# ---------------------------------------------------------------------------

class BonusModel(BaseModel):
    """Expected BPS per appearance, converted to expected bonus points.

    Bonus is awarded on a *within-match ranking* of BPS, not on an absolute
    threshold, so a regression on BPS is only half the job. The conversion from
    predicted BPS to expected bonus is handled by
    :meth:`expected_bonus_from_bps`, which uses an empirical mapping fitted on
    historical (BPS, bonus) pairs -- far more robust than trying to simulate all
    22 players' BPS in every fixture.
    """

    name = "bonus"

    def __init__(self, n_estimators: int = 400, max_depth: int = 5,
                 learning_rate: float = 0.05, random_state: int = 42) -> None:
        super().__init__()
        self.params = dict(n_estimators=n_estimators, max_depth=max_depth,
                           learning_rate=learning_rate, subsample=0.8,
                           colsample_bytree=0.8, random_state=random_state,
                           n_jobs=-1)
        self.model = None
        #: Bin edges and mean bonus per bin, fitted in :meth:`fit_bonus_curve`.
        self.bps_bins: np.ndarray | None = None
        self.bonus_curve: np.ndarray | None = None

    def train(self, X: pd.DataFrame, y_bps: pd.Series | np.ndarray,
              y_bonus: pd.Series | np.ndarray | None = None) -> "BonusModel":
        """Fit the BPS regressor and, if bonus labels are given, the BPS->bonus curve."""
        xgb = _require_xgboost()
        Xn = self._prepare(X, fit=True)
        self.model = xgb.XGBRegressor(**self.params)
        self.model.fit(Xn, np.asarray(y_bps, dtype=float))
        self.fitted = True

        if y_bonus is not None:
            self.fit_bonus_curve(np.asarray(y_bps, dtype=float),
                                 np.asarray(y_bonus, dtype=float))
        return self

    def fit_bonus_curve(self, bps: np.ndarray, bonus: np.ndarray,
                        n_bins: int = 30) -> None:
        """Empirical mapping from BPS to mean bonus points actually awarded."""
        mask = np.isfinite(bps) & np.isfinite(bonus)
        bps, bonus = bps[mask], bonus[mask]
        if len(bps) < 50:
            log.warning("too few rows (%d) to fit a BPS->bonus curve", len(bps))
            return
        edges = np.unique(np.quantile(bps, np.linspace(0, 1, n_bins + 1)))
        idx = np.clip(np.digitize(bps, edges[1:-1]), 0, len(edges) - 2)
        curve = np.zeros(len(edges) - 1)
        for b in range(len(curve)):
            sel = idx == b
            curve[b] = float(bonus[sel].mean()) if sel.any() else 0.0
        # Bonus must be non-decreasing in BPS; enforce it to remove bin noise.
        self.bps_bins = edges
        self.bonus_curve = np.maximum.accumulate(curve)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Expected BPS per appearance."""
        self._check_fitted()
        return np.clip(self.model.predict(self._prepare(X)), 0.0, None)

    def expected_bonus_from_bps(self, bps: np.ndarray) -> np.ndarray:
        """Convert predicted BPS into expected bonus points (0-3).

        Falls back to a smooth heuristic when no empirical curve was fitted:
        bonus is negligible below ~20 BPS and saturates near 3 around ~40, which
        matches the observed distribution closely enough for cold starts.
        """
        bps = np.asarray(bps, dtype=float)
        if self.bonus_curve is None or self.bps_bins is None:
            return np.clip((bps - 18.0) / 8.0, 0.0, 3.0)
        idx = np.clip(np.digitize(bps, self.bps_bins[1:-1]), 0,
                      len(self.bonus_curve) - 1)
        return np.clip(self.bonus_curve[idx], 0.0, 3.0)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class FPLPointsPredictor:
    """Composes the four sub-models into E[points] via the exact FPL rules.

    The expectation is assembled component by component, each term mirroring a
    constant in :mod:`fpl_rules` rather than a learned coefficient::

        E[pts] = appearance + goals + assists + clean sheet + defcon
                 + saves + bonus - expected cards - expected concessions

    where the goal, assist and clean-sheet terms are the sub-model rates scaled
    by expected minutes and multiplied by the position-specific point values.

    Deliberately an *expectation*, not a simulation: the full distribution
    (needed for captaincy risk and chip timing) comes from
    :mod:`simulator`, which consumes the same rate predictions.
    """

    minutes_model: MinutesModel = field(default_factory=MinutesModel)
    attack_model: AttackModel = field(default_factory=AttackModel)
    defense_model: DefenseModel = field(default_factory=DefenseModel)
    bonus_model: BonusModel = field(default_factory=BonusModel)
    rules: Any = field(default_factory=lambda: RULES)

    #: Expected cards and own goals per 90 -- too rare and too weakly predictable
    #: to justify their own model; league-average rates by position are used.
    yellow_per90: dict = field(default_factory=lambda: {
        GKP: 0.05, DEF: 0.18, MID: 0.16, FWD: 0.12})
    saves_per90_prior: float = 3.0

    def train(self, X: pd.DataFrame, targets: dict) -> "FPLPointsPredictor":
        """Train all four sub-models.

        ``targets`` supplies the label columns::

            minutes, xg_per90, xa_per90, clean_sheet, bps
            defcon_per90 (optional -- NaN where a season predates the DC rule)
            bonus        (optional -- enables the BPS->bonus curve)
        """
        self.minutes_model.train(X, targets["minutes"])
        self.attack_model.train(X, targets["xg_per90"], targets["xa_per90"])
        self.defense_model.train(X, targets["clean_sheet"],
                                 targets.get("defcon_per90"))
        self.bonus_model.train(X, targets["bps"], targets.get("bonus"))
        return self

    def predict(self, X: pd.DataFrame, element_type: Sequence[int],
                team_goals_conceded: Sequence[float] | None = None
                ) -> pd.DataFrame:
        """Expected points per player, with the component breakdown retained.

        ``element_type`` must align row-for-row with ``X``. The returned frame
        keeps every component column, which makes the predictions auditable --
        when a player's projection looks wrong you can see whether it came from
        minutes, attack or bonus.
        """
        et = np.asarray([int(e) for e in element_type])
        n = len(et)

        exp_minutes = self.minutes_model.predict(X)
        p60 = self.minutes_model.predict_p60(X)
        p_play = self.minutes_model.predict_pstart(X)

        attack = self.attack_model.predict(X)
        xg90 = attack["xg_per90"].to_numpy()
        xa90 = attack["xa_per90"].to_numpy()

        cs_prob = self.defense_model.predict_clean_sheet(X)
        dc90 = self.defense_model.predict_defcon_per90(X, et)
        p_defcon = self.defense_model.predict_defcon_probability(dc90, exp_minutes, et)

        bps = self.bonus_model.predict(X)
        exp_bonus = self.bonus_model.expected_bonus_from_bps(bps)

        minutes_share = exp_minutes / MAX_MINUTES

        # Appearance points: 1 for showing up, 2 once past the hour.
        appearance = (POINTS_PLAYING_UNDER_60 * np.clip(p_play - p60, 0.0, 1.0)
                      + POINTS_PLAYING_60_PLUS * p60)

        goal_values = np.array([POINTS_GOAL[e] for e in et], dtype=float)
        cs_values = np.array([POINTS_CLEAN_SHEET[e] for e in et], dtype=float)

        exp_goals = xg90 * minutes_share
        exp_assists = xa90 * minutes_share

        goals_pts = goal_values * exp_goals
        assist_pts = POINTS_ASSIST * exp_assists
        # A clean sheet requires 60+ minutes, so gate it on p60 not on p_play.
        cs_pts = cs_values * cs_prob * p60
        defcon_pts = POINTS_DEFENSIVE_CONTRIBUTION * p_defcon

        # Goalkeeper save points: 1 per 3 saves.
        saves_pts = np.where(et == GKP,
                             self.saves_per90_prior * minutes_share / SAVES_PER_POINT,
                             0.0)

        # Goals conceded: GKP/DEF lose 1 per 2 conceded while on the pitch.
        if team_goals_conceded is None:
            conceded = np.where(cs_prob > 0, -np.log(np.clip(cs_prob, 1e-6, 1.0)),
                                1.3)
        else:
            conceded = np.asarray(team_goals_conceded, dtype=float)
        concede_pts = np.where(np.isin(et, [GKP, DEF]),
                               -0.5 * conceded * minutes_share, 0.0)

        # Per-player yellow card rate when available; positional average otherwise.
        pos_yellow = np.array([self.yellow_per90.get(int(e), 0.15) for e in et])
        if "ewma_yellow_cards" in X.columns:
            player_rate = pd.to_numeric(X["ewma_yellow_cards"], errors="coerce").values
            valid = np.isfinite(player_rate) & (player_rate >= 0)
            yellow_rate = np.where(valid, player_rate, pos_yellow)
        else:
            yellow_rate = pos_yellow
        card_pts = -yellow_rate * minutes_share

        total = (appearance + goals_pts + assist_pts + cs_pts + defcon_pts
                 + saves_pts + concede_pts + card_pts + exp_bonus)

        return pd.DataFrame({
            "expected_points": total,
            "expected_minutes": exp_minutes,
            "p60": p60,
            "xg_per90": xg90,
            "xa_per90": xa90,
            "clean_sheet_prob": cs_prob,
            "defcon_per90": dc90,
            "p_defcon": p_defcon,
            "expected_bps": bps,
            "expected_bonus": exp_bonus,
            "pts_appearance": appearance,
            "pts_goals": goals_pts,
            "pts_assists": assist_pts,
            "pts_clean_sheet": cs_pts,
            "pts_defcon": defcon_pts,
            "pts_saves": saves_pts,
            "pts_conceded": concede_pts,
            "pts_cards": card_pts,
        }, index=X.index)

    # -- persistence -------------------------------------------------------

    def save(self, directory: str | Path) -> Path:
        """Write all four sub-models into ``directory``."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.minutes_model.save(directory / "minutes.pkl")
        self.attack_model.save(directory / "attack.pkl")
        self.defense_model.save(directory / "defense.pkl")
        self.bonus_model.save(directory / "bonus.pkl")
        return directory

    @classmethod
    def load(cls, directory: str | Path) -> "FPLPointsPredictor":
        """Load a predictor previously written by :meth:`save`."""
        directory = Path(directory)
        return cls(
            minutes_model=MinutesModel.load(directory / "minutes.pkl"),
            attack_model=AttackModel.load(directory / "attack.pkl"),
            defense_model=DefenseModel.load(directory / "defense.pkl"),
            bonus_model=BonusModel.load(directory / "bonus.pkl"),
        )


# ---------------------------------------------------------------------------
# Walk-forward validation
# ---------------------------------------------------------------------------

def walk_forward_validation(df: pd.DataFrame, feature_cols: Sequence[str],
                            target_col: str, model_factory,
                            time_col: str = "round",
                            min_train_periods: int = 5,
                            season_col: str | None = "season",
                            step: int = 1) -> pd.DataFrame:
    """Expanding-window backtest that respects temporal ordering.

    At each gameweek ``t``, the model is trained on everything strictly before
    ``t`` and evaluated on ``t`` alone -- never on data it could not have had.
    Ordinary k-fold cross-validation is invalid here: shuffling rows lets the
    model learn from gameweek 30 to predict gameweek 12, which inflates every
    metric and is the single most common way FPL model backtests lie.

    ``model_factory`` is a zero-argument callable returning a fresh, untrained
    model exposing ``train(X, y)`` and ``predict(X)``.

    Returns one row per evaluated period with MAE, RMSE and Spearman rank
    correlation. Rank correlation matters more than MAE for FPL: the optimiser
    only needs players ordered correctly, not their point totals nailed.
    """
    from scipy.stats import spearmanr

    data = df.dropna(subset=[target_col]).copy()
    if season_col and season_col in data.columns:
        data["_period"] = (data[season_col].astype(str) + "_"
                           + data[time_col].astype(int).astype(str).str.zfill(2))
    else:
        data["_period"] = data[time_col].astype(int).astype(str).str.zfill(3)

    periods = sorted(data["_period"].unique())
    rows = []

    for i in range(min_train_periods, len(periods), step):
        train_periods = periods[:i]
        test_period = periods[i]

        train = data[data["_period"].isin(train_periods)]
        test = data[data["_period"] == test_period]
        if train.empty or test.empty:
            continue

        X_train = train[list(feature_cols)].astype(float)
        X_test = test[list(feature_cols)].astype(float)
        # Features are lagged, so early rows carry NaN; fill with the training
        # median, which is computed on training data only (no leakage).
        medians = X_train.median()
        X_train = X_train.fillna(medians)
        X_test = X_test.fillna(medians)

        model = model_factory()
        model.train(X_train, train[target_col].to_numpy(dtype=float))
        pred = np.asarray(model.predict(X_test), dtype=float).ravel()
        actual = test[target_col].to_numpy(dtype=float)

        err = pred - actual
        rho = np.nan
        if len(actual) > 2 and np.std(pred) > 0:
            rho = float(spearmanr(pred, actual).statistic)

        rows.append({
            "period": test_period,
            "n_train": len(train),
            "n_test": len(test),
            "mae": float(np.mean(np.abs(err))),
            "rmse": float(np.sqrt(np.mean(err ** 2))),
            "bias": float(np.mean(err)),
            "spearman": rho,
        })

    return pd.DataFrame(rows)


def build_training_targets(history_df: pd.DataFrame) -> dict:
    """Derive the sub-model label columns from a Vaastav gameweek frame.

    Per-90 rates are computed only for rows with meaningful minutes; below the
    threshold the rate is NaN (undefined) rather than 0, since one goal in three
    minutes is not a 30-goals-per-90 striker.

    ``defcon_per90`` is NaN for every row of a season that predates the
    defensive-contribution statistic. That is deliberate -- see the module
    docstring.
    """
    df = history_df.copy()
    minutes = pd.to_numeric(df["minutes"], errors="coerce")
    enough = minutes >= 20

    def per90(col: str) -> pd.Series:
        if col not in df.columns:
            return pd.Series(np.nan, index=df.index)
        raw = pd.to_numeric(df[col], errors="coerce")
        return (raw / minutes * 90.0).where(enough)

    targets = {
        "minutes": minutes,
        "xg_per90": per90("expected_goals"),
        "xa_per90": per90("expected_assists"),
        "clean_sheet": pd.to_numeric(df.get("clean_sheets", 0),
                                     errors="coerce").fillna(0).clip(0, 1),
        "bps": pd.to_numeric(df.get("bps", 0), errors="coerce"),
        "bonus": pd.to_numeric(df.get("bonus", 0), errors="coerce"),
    }

    if "defensive_contribution" in df.columns:
        targets["defcon_per90"] = per90("defensive_contribution")
    else:
        targets["defcon_per90"] = pd.Series(np.nan, index=df.index)
        log.info("no defensive_contribution column in this frame; the DC head "
                 "will fall back to positional priors")

    return targets
