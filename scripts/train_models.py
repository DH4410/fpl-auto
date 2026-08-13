#!/usr/bin/env python3
"""
Train FPLPointsPredictor on all available Vaastav seasons.

Saves four sub-model pickles to bot/models/:
    minutes.pkl  attack.pkl  defense.pkl  bonus.pkl

All six seasons are loaded; the xg_mask in build_training_targets handles
2020-21/2021-22 automatically (those rows have NaN xg_per90/xa_per90 and are
excluded from AttackModel only). MinutesModel, DefenseModel and BonusModel
benefit from the full dataset.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from bot.data_collector import load_multi_season_history
from bot.feature_engineering import (
    compute_ewma_stats,
    feature_columns,
    player_vs_opponent_features,
    resolve_opponent_team_name,
    rolling_window_stats,
)
from bot.fpl_rules import MID
from bot.models import FPLPointsPredictor, build_training_targets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

TRAINING_SEASONS = ("2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26")
MODELS_DIR = Path(__file__).resolve().parents[1] / "bot" / "models"

POSITION_MAP = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}


def _add_position_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add element_type int and pos_* one-hots from the 'position' string."""
    df = df.copy()
    pos_str = df["position"].str.upper().str.strip().replace({"GK": "GKP"})
    df["element_type"] = pos_str.map({"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4})
    df["pos_gkp"] = (df["element_type"] == 1).astype(float)
    df["pos_def"] = (df["element_type"] == 2).astype(float)
    df["pos_mid"] = (df["element_type"] == 3).astype(float)
    df["pos_fwd"] = (df["element_type"] == 4).astype(float)
    return df


def main() -> None:
    log.info("Loading training data for seasons: %s", TRAINING_SEASONS)
    history = load_multi_season_history(seasons=TRAINING_SEASONS)
    log.info("Loaded %d rows across %d seasons", len(history), history["season"].nunique())

    history = _add_position_features(history)

    # Rename 'value' → 'now_cost' so feature_columns() picks it up.
    if "value" in history.columns and "now_cost" not in history.columns:
        history = history.rename(columns={"value": "now_cost"})

    if "was_home" in history.columns:
        history["is_home"] = history["was_home"].astype(float)

    log.info("Computing EWMA + rolling features…")
    feat = compute_ewma_stats(history)
    feat = rolling_window_stats(feat)

    log.info("Resolving opponent team names + h2h features…")
    feat = resolve_opponent_team_name(feat)
    feat = player_vs_opponent_features(feat)

    fcols = feature_columns(feat)
    log.info("Feature columns (%d): %s", len(fcols), fcols)

    targets = build_training_targets(feat)

    # Median fill from the full feature matrix (no target leakage -- targets
    # describe the CURRENT row, features are all lagged by the shift in
    # compute_ewma_stats).
    medians = feat[fcols].median()
    X_full = feat[fcols].fillna(medians)

    # AttackModel needs non-NaN xG/xA targets (only 2022-23+ has xG/xA columns).
    xg_mask = targets["xg_per90"].notna() & targets["xa_per90"].notna()
    X_attack = X_full[xg_mask]
    log.info(
        "Training split: %d rows total, %d with xG/xA for AttackModel",
        len(X_full),
        xg_mask.sum(),
    )

    predictor = FPLPointsPredictor()

    sample_weight = targets.get("sample_weight")

    log.info("Training MinutesModel on %d rows…", len(X_full))
    predictor.minutes_model.train(X_full, targets["minutes"])

    log.info("Training AttackModel on %d rows (xG/xA present)…", xg_mask.sum())
    predictor.attack_model.train(
        X_attack,
        targets["xg_per90"][xg_mask],
        targets["xa_per90"][xg_mask],
        sample_weight=sample_weight[xg_mask] if sample_weight is not None else None,
    )

    log.info("Training DefenseModel on %d rows…", len(X_full))
    predictor.defense_model.train(
        X_full,
        targets["clean_sheet"],
        targets.get("defcon_per90"),
        sample_weight=sample_weight,
    )

    log.info("Training BonusModel on %d rows…", len(X_full))
    predictor.bonus_model.train(
        X_full,
        targets["bps"],
        targets.get("bonus"),
        sample_weight=sample_weight,
    )

    log.info("All sub-models trained. Saving to %s…", MODELS_DIR)
    predictor.save(MODELS_DIR)

    # Quick in-sample Spearman for sanity check (not a true holdout).
    et = feat["element_type"].fillna(MID).astype(int).values
    preds = predictor.predict(X_full, et)
    actual_pts = pd.to_numeric(feat.get("total_points", pd.Series(dtype=float)),
                                errors="coerce").fillna(0)
    from scipy.stats import spearmanr
    rho, _ = spearmanr(
        preds["expected_points"].fillna(0),
        actual_pts,
    )

    print("\n" + "=" * 60)
    print(f"Training complete — models saved to {MODELS_DIR}")
    print(f"Seasons: {TRAINING_SEASONS}")
    print(f"Rows trained (minutes/defense/bonus): {len(X_full):,}")
    print(f"Rows trained (attack, xG/xA rows):    {xg_mask.sum():,}")
    print(f"Features: {len(fcols)}")
    print(f"In-sample Spearman (xPts vs actual):  {rho:+.3f}")
    print()
    print("NOTE: DC head falls back to positional priors for seasons without DC data.")
    print("This is expected and documented in models.py.")
    print("NOTE: xG/xA attack targets only cover 2022-23+ rows (earlier seasons have NaN).")
    print("=" * 60)


if __name__ == "__main__":
    main()
