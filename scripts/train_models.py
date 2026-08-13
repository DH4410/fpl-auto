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

import json
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


def _safe_col(df: pd.DataFrame, col: str) -> pd.Series:
    """Return df[col] if present, else an all-NaN Series with df's index."""
    return df[col] if col in df.columns else pd.Series(float("nan"), index=df.index)



# Maps football-data.co.uk team names to the Vaastav/FPL-history short names
# used in the training feat frame. Most names are identical; only these two
# differ. NOTE: this is NOT the same as FOOTBALL_DATA_NAME_ALIASES, which
# targets FPL bootstrap full names ('Nottingham Forest') rather than the
# abbreviated names Vaastav uses ("Nott'm Forest").
_FD_TO_VAASTAV: dict[str, str] = {
    "Man United": "Man Utd",
    "Tottenham": "Spurs",
    "Sheffield United": "Sheffield Utd",
}


def _add_odds_features(feat: pd.DataFrame, seasons: tuple[str, ...]) -> pd.DataFrame:
    """Left-join football-data.co.uk betting odds onto the training frame.

    For each historical fixture, maps football-data team names to Vaastav
    team names (the short names in the training feat frame), then joins on
    (team, kickoff_date). Rows with no odds match receive NaN and are handled
    by the existing median-fill step downstream.

    Features added (perspective-adjusted per team):
        mkt_p_win, mkt_p_draw, mkt_p_lose — de-vigged Shin probabilities
        mkt_lambda_for, mkt_lambda_against — Poisson-implied goal rates
        mkt_clean_sheet — market P(opponent scores 0)
        mkt_p_over25 — de-vigged over-2.5-goals probability
    """
    from bot.data_collector import fetch_football_data_odds
    from bot.feature_engineering import odds_features as _odds_features

    def _vaastav(name) -> str:
        if pd.isna(name):
            return ""
        return _FD_TO_VAASTAV.get(str(name), str(name))

    all_parts: list[pd.DataFrame] = []
    for season in seasons:
        try:
            raw = fetch_football_data_odds(season=season)
            odf = _odds_features(raw).reset_index(drop=True)
        except Exception as exc:
            log.warning("Odds unavailable for %s: %s", season, exc)
            continue
        if "date" not in odf.columns:
            continue

        dates = pd.to_datetime(odf["date"]).dt.date

        home = pd.DataFrame({
            "kickoff_date": dates,
            "team": odf["HomeTeam"].map(_vaastav),
            "mkt_p_win": _safe_col(odf, "p_home"),
            "mkt_p_draw": _safe_col(odf, "p_draw"),
            "mkt_p_lose": _safe_col(odf, "p_away"),
            "mkt_lambda_for": _safe_col(odf, "lambda_home"),
            "mkt_lambda_against": _safe_col(odf, "lambda_away"),
            "mkt_clean_sheet": _safe_col(odf, "mkt_clean_sheet_home"),
            "mkt_p_over25": _safe_col(odf, "p_over25"),
        })
        away = pd.DataFrame({
            "kickoff_date": dates,
            "team": odf["AwayTeam"].map(_vaastav),
            "mkt_p_win": _safe_col(odf, "p_away"),
            "mkt_p_draw": _safe_col(odf, "p_draw"),
            "mkt_p_lose": _safe_col(odf, "p_home"),
            "mkt_lambda_for": _safe_col(odf, "lambda_away"),
            "mkt_lambda_against": _safe_col(odf, "lambda_home"),
            "mkt_clean_sheet": _safe_col(odf, "mkt_clean_sheet_away"),
            "mkt_p_over25": _safe_col(odf, "p_over25"),
        })
        all_parts.append(pd.concat([home, away], ignore_index=True))

    if not all_parts:
        log.warning("No odds data loaded for any season — skipping mkt_ features")
        return feat

    lookup = (pd.concat(all_parts, ignore_index=True)
              .drop_duplicates(subset=["team", "kickoff_date"]))

    n_before = len(feat)
    feat = feat.copy()
    feat["_kickoff_date"] = pd.to_datetime(feat["kickoff_time"]).dt.date
    feat = feat.merge(lookup, left_on=["team", "_kickoff_date"],
                      right_on=["team", "kickoff_date"], how="left")
    feat = feat.drop(columns=["_kickoff_date", "kickoff_date"])
    if len(feat) != n_before:
        raise AssertionError(
            f"Odds merge duplicated rows: {n_before} → {len(feat)}; "
            "check for duplicate (team, date) in lookup"
        )

    n_matched = feat["mkt_p_win"].notna().sum()
    log.info("Odds join: %d/%d rows matched (%.0f%%)",
             n_matched, n_before, 100 * n_matched / n_before)
    return feat


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

    log.info("Joining betting odds features…")
    feat = _add_odds_features(feat, TRAINING_SEASONS)

    fcols = feature_columns(feat)
    log.info("Feature columns (%d): %s", len(fcols), fcols)

    targets = build_training_targets(feat)

    # Median fill from the full feature matrix (no target leakage -- targets
    # describe the CURRENT row, features are all lagged by the shift in
    # compute_ewma_stats).
    medians = feat[fcols].median()
    X_full = feat[fcols].fillna(medians)

    # Persist mkt_* medians so inference can fill those features with the
    # population centre rather than NaN (live odds are not fetched at inference
    # time; training medians give a neutral, consistent imputation).
    mkt_cols = [c for c in fcols if c.startswith("mkt_")]
    if mkt_cols:
        mkt_medians = {c: float(medians[c]) for c in mkt_cols if pd.notna(medians[c])}
        (MODELS_DIR / "mkt_medians.json").write_text(
            json.dumps(mkt_medians, indent=2), encoding="utf-8"
        )
        log.info("Saved mkt medians for %d odds features to mkt_medians.json",
                 len(mkt_medians))

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
