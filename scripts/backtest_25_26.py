#!/usr/bin/env python3
"""
Backtest FPLPointsPredictor on the completed 2025-26 season.

Runs two strategies side by side:
  baseline  -- rolling historical mean points (the existing backtester default)
  ml        -- trained FPLPointsPredictor predictions

Target: ML strategy scores > 2,059 pts (user's actual 2025-26 score, ~3M rank)
and beats the baseline, demonstrating the model adds real value.

Sub-100k rank is roughly 2,200+ pts based on the 2025-26 distribution.

Usage:
    python scripts/backtest_25_26.py

The script expects trained models in bot/models/ (run train_models.py first).
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from bot.backtester import SeasonBacktester, compare_strategies
from bot.data_collector import load_multi_season_history
from bot.feature_engineering import (
    compute_ewma_stats,
    feature_columns,
    rolling_window_stats,
)
from bot.fpl_rules import MID
from bot.models import FPLPointsPredictor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parents[1] / "bot" / "models"
TEST_SEASON = "2025-26"

# Load 2022-23 onward: 2022-24 gives EWMA context for early 2025-26 GWs;
# without it, GW1 features are all NaN and the model is blind for the first
# few weeks. Because element IDs reset each season, the EWMA chains within
# each season independently -- there is no cross-season leakage.
CONTEXT_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")

POSITION_MAP = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}


def _prep_history(history: pd.DataFrame) -> pd.DataFrame:
    """Normalise for the backtester: fix GK→GKP position string, add now_cost alias."""
    df = history.copy()
    # Backtester maps position strings via _POS_MAP which uses "GKP" not "GK".
    df["position"] = df["position"].str.upper().str.strip().replace({"GK": "GKP"})
    # Keep 'value' for backtester; add 'now_cost' alias for feature_columns().
    if "value" in df.columns and "now_cost" not in df.columns:
        df["now_cost"] = df["value"]
    if "was_home" in df.columns:
        df["is_home"] = df["was_home"].astype(float)
    return df


def _add_position_features(feat: pd.DataFrame) -> pd.DataFrame:
    """Add element_type int and pos_* one-hots to the EWMA feature frame."""
    df = feat.copy()
    et = df["position"].str.upper().str.strip().map(
        {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
    )
    df["element_type"] = et
    df["pos_gkp"] = (et == 1).astype(float)
    df["pos_def"] = (et == 2).astype(float)
    df["pos_mid"] = (et == 3).astype(float)
    df["pos_fwd"] = (et == 4).astype(float)
    return df


DGW_SCALE = 1.7  # scale factor for players with 2 fixtures in a GW


def build_ml_predictor_fn(
    feat_all: pd.DataFrame,
    history: pd.DataFrame,
    fcols: list[str],
    medians: pd.Series,
    predictor: FPLPointsPredictor,
) -> callable:
    """Return an expected_points_fn(before_gw) -> pd.Series for SeasonBacktester."""
    # Pre-compute which elements have double fixtures in each test GW.
    test_hist = history[history["season"] == TEST_SEASON]
    dgw_elements: dict[int, set[int]] = {}
    for gw, grp in test_hist.groupby("GW"):
        counts = grp.groupby("element").size()
        doubles = set(counts[counts > 1].index.astype(int))
        if doubles:
            dgw_elements[int(gw)] = doubles

    def expected_points_fn(before_gw: int) -> pd.Series:
        # Row at GW==before_gw has EWMA features from GW 1..(before_gw-1),
        # because compute_ewma_stats shifts by 1. This is lookahead-free.
        gw_feat = feat_all[
            (feat_all["season"] == TEST_SEASON)
            & (feat_all["GW"] == before_gw)
        ].copy()
        # Drop any duplicate element rows (can arise from data concatenation).
        gw_feat = gw_feat.drop_duplicates(subset=["element"])

        if gw_feat.empty:
            return pd.Series(dtype=float)

        X = gw_feat[fcols].fillna(medians)
        et = gw_feat["element_type"].fillna(MID).astype(int).values

        preds = predictor.predict(X, et)
        xp = preds["expected_points"].clip(lower=0).values.copy()

        # Scale up players with a double fixture this GW.
        if before_gw in dgw_elements:
            doubles = dgw_elements[before_gw]
            elems = gw_feat["element"].astype(int).values
            for idx, eid in enumerate(elems):
                if eid in doubles:
                    xp[idx] *= DGW_SCALE

        return pd.Series(xp, index=gw_feat["element"].astype(int).values)

    return expected_points_fn


def main() -> None:
    # ── 0. Load trained models ───────────────────────────────────────────
    if not MODELS_DIR.exists() or not (MODELS_DIR / "minutes.pkl").exists():
        print(f"ERROR: No trained models found in {MODELS_DIR}")
        print("Run:  python scripts/train_models.py  first.")
        sys.exit(1)

    log.info("Loading trained ML models from %s…", MODELS_DIR)
    predictor = FPLPointsPredictor.load(MODELS_DIR)
    log.info("Models loaded OK.")

    # ── 1. Load historical data ──────────────────────────────────────────
    log.info("Loading history for seasons: %s…", CONTEXT_SEASONS)
    history = load_multi_season_history(seasons=CONTEXT_SEASONS)
    log.info("Loaded %d rows", len(history))

    history = _prep_history(history)

    # ── 2. Pre-compute EWMA feature matrix for ALL seasons ───────────────
    log.info("Computing EWMA + rolling features (once for all seasons)…")
    feat_all = compute_ewma_stats(history)
    feat_all = rolling_window_stats(feat_all)
    feat_all = _add_position_features(feat_all)

    # Derive feature column list from the training sub-set to avoid any
    # accidental leakage from 2025-26 test rows changing the median.
    train_feat = feat_all[feat_all["season"] != TEST_SEASON]
    fcols = feature_columns(train_feat)
    medians = train_feat[fcols].median()
    log.info("Feature columns for prediction: %d", len(fcols))

    # ── 3. Build the expected_points callable ────────────────────────────
    ml_fn = build_ml_predictor_fn(feat_all, history, fcols, medians, predictor)

    # ── 4. Run baseline (rolling mean) ──────────────────────────────────
    # For a fair A/B, both strategies see ALL historical data (2022-24) for
    # the rolling mean too. The backtester's _expected_pts already excludes
    # the test season, so this is clean.
    log.info("Running BASELINE backtester (rolling mean)…")
    bt_base = SeasonBacktester(history, test_season=TEST_SEASON)
    df_base = bt_base.run(strategy="1ft")

    # ── 5. Run ML strategy ───────────────────────────────────────────────
    log.info("Running ML backtester…")
    bt_ml = SeasonBacktester(history, test_season=TEST_SEASON,
                             expected_points_fn=ml_fn)
    df_ml = bt_ml.run(strategy="1ft")

    # ── 6. Run ML + wildcard at GW20 ────────────────────────────────────
    log.info("Running ML + wildcard@GW20 backtester…")
    bt_wc = SeasonBacktester(history, test_season=TEST_SEASON,
                             expected_points_fn=ml_fn)
    df_wc = bt_wc.run(strategy="wildcard20")

    # ── 7. Run ML + chips (BB@GW26, TC@GW33) ───────────────────────────
    # 2025-26 DGWs: GW26, GW33, GW36. BGWs: GW31, GW34.
    # With the all-FTs strategy, the squad is well-maintained so FH actually
    # hurts (our squad outscores the FH squad in blank GWs). Best schedule:
    # BB in DGW26 (bench gets 2× fixture) and TC in DGW33 (biggest DGW,
    # captain scores 3×). No FH needed.
    CHIP_SCHEDULE_25_26 = {26: "bboost", 33: "3xc"}
    log.info("Running ML + chips backtester (BB@GW26, TC@GW33)…")
    bt_chips = SeasonBacktester(history, test_season=TEST_SEASON,
                                expected_points_fn=ml_fn)
    df_chips = bt_chips.run(strategy="1ft", chip_gws=CHIP_SCHEDULE_25_26)

    # ── 8. Report ────────────────────────────────────────────────────────
    base_total  = int(df_base["pts"].sum())
    ml_total    = int(df_ml["pts"].sum())
    wc_total    = int(df_wc["pts"].sum())
    chips_total = int(df_chips["pts"].sum())

    print("\n" + "=" * 65)
    print("  FPL 2025-26 BACKTEST RESULTS")
    print("=" * 65)
    print(f"  Baseline (rolling mean, 1 FT/GW):         {base_total:>5} pts")
    print(f"  ML model (FPLPointsPredictor, 1 FT):      {ml_total:>5} pts")
    print(f"  ML model + Wildcard at GW20:               {wc_total:>5} pts")
    print(f"  ML model + Chips (BB26/TC33):              {chips_total:>5} pts")
    print()
    print(f"  User's actual 2025-26 score:               2,059 pts  (~3M rank)")
    print(f"  Sub-100k target:                          ~2,200 pts")
    print(f"  ML gain over baseline:                    {ml_total - base_total:>+5} pts")
    print(f"  ML+Chips (BB+TC) gain over baseline:      {chips_total - base_total:>+5} pts")
    print(f"  ML+Chips gain over user's actual:         {chips_total - 2059:>+5} pts")
    print("=" * 65)

    best = max(ml_total, chips_total)
    if best >= 2200:
        print("\n  PASS: ML strategy meets sub-100k threshold.")
        print("  Ready to integrate into the live 26/27 pipeline.")
    elif best >= 2059:
        print("\n  PARTIAL: ML beats user's actual score but not sub-100k.")
    else:
        print("\n  MISS: ML strategy underperforms. Check model diagnostics.")

    # Chip breakdown
    print("\n  Chips GW breakdown:")
    print(f"  {'GW':>4}  {'ML':>5}  {'Chips':>6}  {'Chip':>8}  {'Capt bonus':>10}")
    for gw in [26, 33]:
        mpts  = int(df_ml.loc[df_ml["gw"] == gw, "pts"].sum())
        cpts  = int(df_chips.loc[df_chips["gw"] == gw, "pts"].sum())
        chip  = df_chips.loc[df_chips["gw"] == gw, "chip"].iloc[0] if len(df_chips[df_chips["gw"] == gw]) else ""
        cbonus = int(df_chips.loc[df_chips["gw"] == gw, "captain_bonus"].sum())
        print(f"  GW{gw:2d}  {mpts:>5}  {cpts:>6}  {str(chip):>8}  {cbonus:>10}")

    # ── 9. GW-by-GW breakdown ────────────────────────────────────────────
    print("\n  GW-by-GW (ML vs baseline, first 10 GWs):")
    print(f"  {'GW':>4}  {'Base':>5}  {'ML':>5}  {'Diff':>5}  {'ML cum':>8}")
    for gw in range(1, min(11, df_ml["gw"].max() + 1)):
        bpts = int(df_base.loc[df_base["gw"] == gw, "pts"].sum())
        mpts = int(df_ml.loc[df_ml["gw"] == gw, "pts"].sum())
        mcum = int(df_ml.loc[df_ml["gw"] <= gw, "pts"].sum())
        print(f"  {gw:>4}  {bpts:>5}  {mpts:>5}  {mpts - bpts:>+5}  {mcum:>8}")

    # Save per-GW results
    out_dir = Path(__file__).resolve().parents[1] / "reports"
    out_dir.mkdir(exist_ok=True)
    df_ml["strategy"] = "ml_1ft"
    df_base["strategy"] = "baseline_1ft"
    df_wc["strategy"] = "ml_wildcard20"
    df_chips["strategy"] = "ml_chips"
    combined = pd.concat([df_base, df_ml, df_wc, df_chips], ignore_index=True)
    out_path = out_dir / "backtest_2025_26.csv"
    combined.to_csv(out_path, index=False)
    print(f"\n  Full results saved to {out_path}")


if __name__ == "__main__":
    main()
