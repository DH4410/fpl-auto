#!/usr/bin/env python3
"""
Systematic chip schedule sweep for FPL 2025-26.

Sweeps 11 chip combinations (with/without Free Hit) and 4 DGW_SCALE values
for the best chip schedule to find the highest total season score.

Captain scoring: k=0.5 blend  ->  cap_score = xPts * (0.5 + 0.5 * p60)
Wildcard: GW14 for all runs.

Usage:
    python scripts/chip_sweep.py

Pre-requisite: trained models in bot/models/  (run train_models.py first).
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from bot.backtester import SeasonBacktester
from bot.data_collector import load_multi_season_history
from bot.feature_engineering import compute_ewma_stats, feature_columns, rolling_window_stats
from bot.fpl_rules import MID
from bot.models import FPLPointsPredictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parents[1] / "bot" / "models"
TEST_SEASON = "2025-26"
CONTEXT_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")
DEFAULT_DGW_SCALE = 1.7


# ---------------------------------------------------------------------------
# History prep helpers  (same as backtest_25_26.py)
# ---------------------------------------------------------------------------

def _prep_history(history: pd.DataFrame) -> pd.DataFrame:
    df = history.copy()
    df["position"] = df["position"].str.upper().str.strip().replace({"GK": "GKP"})
    if "value" in df.columns and "now_cost" not in df.columns:
        df["now_cost"] = df["value"]
    if "was_home" in df.columns:
        df["is_home"] = df["was_home"].astype(float)
    return df


def _add_position_features(feat: pd.DataFrame) -> pd.DataFrame:
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


# ---------------------------------------------------------------------------
# Predictor factory — shared cache, per-call scale
# ---------------------------------------------------------------------------

def build_predictor_fns(
    feat_all: pd.DataFrame,
    history: pd.DataFrame,
    fcols: list,
    medians: pd.Series,
    predictor: "FPLPointsPredictor",
):
    """Return (make_expected_points_fn, make_captain_fn) sharing a single prediction cache.

    Both factories close over _pred_cache so predictor.predict() is called at
    most once per GW regardless of how many scale/k variants are evaluated.

    make_expected_points_fn(dgw_scale)          -> expected_points_fn(before_gw)
    make_captain_fn(k, cap_dgw_scale)           -> captain_score_fn(before_gw)
    """
    # Pre-compute which elements have 2 fixtures in each test GW.
    test_hist = history[history["season"] == TEST_SEASON]
    dgw_elements: dict[int, set] = {}
    for gw, grp in test_hist.groupby("GW"):
        counts = grp.groupby("element").size()
        doubles = set(counts[counts > 1].index.astype(int))
        if doubles:
            dgw_elements[int(gw)] = doubles

    # Shared cache: before_gw -> {"xp": ndarray, "p60": ndarray, "elements": ndarray}
    _pred_cache: dict = {}

    def _ensure_cached(before_gw: int) -> None:
        if before_gw in _pred_cache:
            return
        gw_feat = feat_all[
            (feat_all["season"] == TEST_SEASON) & (feat_all["GW"] == before_gw)
        ].drop_duplicates(subset=["element"]).copy()
        if gw_feat.empty:
            _pred_cache[before_gw] = None
            return
        X = gw_feat[fcols].fillna(medians)
        et = gw_feat["element_type"].fillna(MID).astype(int).values
        preds = predictor.predict(X, et)
        _pred_cache[before_gw] = {
            "xp":       preds["expected_points"].clip(lower=0).values.copy(),
            "p60":      preds["p60"].values.copy(),
            "elements": gw_feat["element"].astype(int).values,
        }

    def make_expected_points_fn(dgw_scale: float = DEFAULT_DGW_SCALE):
        def fn(before_gw: int) -> pd.Series:
            _ensure_cached(before_gw)
            cached = _pred_cache[before_gw]
            if cached is None:
                return pd.Series(dtype=float)
            xp = cached["xp"].copy()
            if before_gw in dgw_elements:
                doubles = dgw_elements[before_gw]
                for idx, eid in enumerate(cached["elements"]):
                    if eid in doubles:
                        xp[idx] *= dgw_scale
            return pd.Series(xp, index=cached["elements"])
        return fn

    def make_captain_fn(k: float = 0.5, cap_dgw_scale: float = DEFAULT_DGW_SCALE):
        def fn(before_gw: int) -> pd.Series:
            _ensure_cached(before_gw)
            cached = _pred_cache[before_gw]
            if cached is None:
                return pd.Series(dtype=float)
            cap_score = (cached["xp"] * (k + (1.0 - k) * cached["p60"])).copy()
            if before_gw in dgw_elements:
                doubles = dgw_elements[before_gw]
                for idx, eid in enumerate(cached["elements"]):
                    if eid in doubles:
                        cap_score[idx] *= cap_dgw_scale
            return pd.Series(cap_score, index=cached["elements"])
        return fn

    return make_expected_points_fn, make_captain_fn


# ---------------------------------------------------------------------------
# Chip schedules to sweep
# ---------------------------------------------------------------------------

CHIP_SCHEDULES = [
    # No Free Hit — 6 schedules
    ({26: "bboost", 33: "3xc"},          "BB@26+TC@33"),
    ({26: "bboost", 36: "3xc"},          "BB@26+TC@36"),
    ({33: "bboost", 36: "3xc"},          "BB@33+TC@36"),
    ({36: "bboost", 33: "3xc"},          "BB@36+TC@33"),
    ({26: "3xc",    33: "bboost"},        "TC@26+BB@33"),
    ({26: "3xc",    36: "bboost"},        "TC@26+BB@36"),
    # Free Hit at GW31 (blank GW) — 3 schedules
    ({31: "freehit", 26: "bboost", 33: "3xc"},  "FH@31+BB@26+TC@33"),
    ({31: "freehit", 26: "bboost", 36: "3xc"},  "FH@31+BB@26+TC@36"),
    ({31: "freehit", 33: "bboost", 36: "3xc"},  "FH@31+BB@33+TC@36"),
    # Free Hit at GW34 (blank GW) — 2 schedules
    ({34: "freehit", 26: "bboost", 33: "3xc"},  "FH@34+BB@26+TC@33"),
    ({34: "freehit", 33: "bboost", 36: "3xc"},  "FH@34+BB@33+TC@36"),
]


def run_one(
    history: pd.DataFrame,
    ml_fn,
    cap_fn,
    chip_gws: dict,
    label: str,
    dgw_scale: float,
) -> tuple[int, pd.DataFrame, list]:
    """Run a single backtest and return (total_score, per_gw_df, missing_chips)."""
    bt = SeasonBacktester(
        history,
        test_season=TEST_SEASON,
        expected_points_fn=ml_fn,
        captain_score_fn=cap_fn,
    )
    df = bt.run(strategy="wildcard14", chip_gws=chip_gws)
    total = int(df["pts"].sum())

    # Verify chips were actually applied
    applied = set(df.loc[df["chip"].notna() & (df["chip"] != ""), "chip"].tolist())
    expected_chips = set(chip_gws.values())
    # Wildcard is consumed by the strategy, not chip_gws
    missing = expected_chips - applied - {"wildcard"}

    return total, df, sorted(missing)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # ── 0. Sanity check ──────────────────────────────────────────────────
    if not MODELS_DIR.exists() or not (MODELS_DIR / "minutes.pkl").exists():
        print(f"ERROR: No trained models found in {MODELS_DIR}")
        print("Run:  python scripts/train_models.py  first.")
        sys.exit(1)

    # ── 1. Load models + data ────────────────────────────────────────────
    log.info("Loading trained ML models from %s ...", MODELS_DIR)
    predictor = FPLPointsPredictor.load(MODELS_DIR)

    log.info("Loading history for seasons %s ...", CONTEXT_SEASONS)
    history = load_multi_season_history(seasons=CONTEXT_SEASONS)
    log.info("Loaded %d rows", len(history))
    history = _prep_history(history)

    # ── 2. Feature engineering ───────────────────────────────────────────
    log.info("Computing EWMA + rolling features ...")
    feat_all = compute_ewma_stats(history)
    feat_all = rolling_window_stats(feat_all)
    feat_all = _add_position_features(feat_all)

    train_feat = feat_all[feat_all["season"] != TEST_SEASON]
    fcols = feature_columns(train_feat)
    medians = train_feat[fcols].median()
    log.info("Feature columns: %d", len(fcols))

    # ── 3. Build shared prediction cache ────────────────────────────────
    make_xpts_fn, make_cap_fn = build_predictor_fns(
        feat_all, history, fcols, medians, predictor
    )

    # Default fns for the main sweep (DGW_SCALE = 1.7, captain k = 0.5)
    ml_fn      = make_xpts_fn(dgw_scale=DEFAULT_DGW_SCALE)
    cap_fn_k05 = make_cap_fn(k=0.5, cap_dgw_scale=DEFAULT_DGW_SCALE)

    # ── 4. DGW data summary ──────────────────────────────────────────────
    t = history[history["season"] == TEST_SEASON]
    print("\n" + "=" * 70)
    print("  DGW / BGW VERIFICATION (2025-26)")
    print("=" * 70)
    for gw in [26, 31, 33, 34, 36]:
        grp = t[t["GW"] == gw]
        counts = grp.groupby("element").size()
        n_double = int((counts > 1).sum())
        n_zero_m = int((grp.groupby("element")["minutes"].sum() == 0).sum())
        tag = "DGW" if n_double > 0 else "BGW"
        print(f"  GW{gw:2d} [{tag}]: {len(grp):>4} rows, "
              f"{grp['element'].nunique():>4} players, "
              f"{n_double:>3} with 2 fixtures, "
              f"{n_zero_m:>3} with 0 mins")
    print("  Note: BGW data includes all players (blanked ones score 0).")
    print("  FH optimizer uses ML xPts which may not know who blanked.")
    print("=" * 70)

    # ── 5. Main chip schedule sweep ──────────────────────────────────────
    print(f"\n  Running {len(CHIP_SCHEDULES)} chip schedules "
          f"(WC@GW14, captain k=0.5, DGW_SCALE={DEFAULT_DGW_SCALE}) ...")
    print()

    results = []
    best_score = 0
    best_df: pd.DataFrame | None = None
    best_label = ""
    best_chip_gws: dict | None = None

    for chip_gws, label in CHIP_SCHEDULES:
        log.info("  Running: %s ...", label)
        total, df, missing = run_one(
            history, ml_fn, cap_fn_k05, chip_gws, label, DEFAULT_DGW_SCALE
        )
        miss_str = f"MISSING:{missing}" if missing else "ok"
        results.append({
            "label":     label,
            "score":     total,
            "dgw_scale": DEFAULT_DGW_SCALE,
            "chips_ok":  miss_str,
        })
        log.info("  %s -> %d pts  chips=%s", label, total, miss_str)

        if total > best_score:
            best_score = total
            best_df = df.copy()
            best_label = label
            best_chip_gws = chip_gws

    # ── 6. DGW_SCALE sweep for the best chip schedule ────────────────────
    DGW_SCALES = [1.5, 1.7, 2.0, 2.5]
    print(f"\n  DGW_SCALE sweep for best schedule ({best_label}) ...")

    for scale in DGW_SCALES:
        if scale == DEFAULT_DGW_SCALE:
            # Already ran this; record under a scaled label
            log.info("  DGW_SCALE=%.1f: already in main sweep, skipping re-run", scale)
            continue
        log.info("  DGW_SCALE=%.1f ...", scale)
        ml_fn_s  = make_xpts_fn(dgw_scale=scale)
        cap_fn_s = make_cap_fn(k=0.5, cap_dgw_scale=scale)
        total, df, missing = run_one(
            history, ml_fn_s, cap_fn_s, best_chip_gws,
            f"{best_label}_dgw{scale}", scale,
        )
        miss_str = f"MISSING:{missing}" if missing else "ok"
        results.append({
            "label":     f"{best_label} [dgw_scale={scale}]",
            "score":     total,
            "dgw_scale": scale,
            "chips_ok":  miss_str,
        })
        log.info("  DGW_SCALE=%.1f -> %d pts  chips=%s", scale, total, miss_str)

        if total > best_score:
            best_score = total
            best_df = df.copy()
            best_label = f"{best_label} [dgw_scale={scale}]"

    # ── 7. Ranked results table ──────────────────────────────────────────
    results_df = pd.DataFrame(results).sort_values("score", ascending=False).reset_index(drop=True)

    print("\n" + "=" * 70)
    print("  CHIP SWEEP RESULTS — ALL RUNS (wildcard@GW14, captain k=0.5)")
    print("=" * 70)
    print(f"  {'Rank':>4}  {'Score':>6}  {'DGW':>5}  {'Chips':>4}  Label")
    for rank, row in enumerate(results_df.itertuples(), start=1):
        marker = " <-- BEST" if rank == 1 else ""
        print(f"  {rank:>4}  {int(row.score):>6}  "
              f"{float(row.dgw_scale):>5.1f}  {row.chips_ok:>4}  "
              f"{row.label}{marker}")
    print("=" * 70)

    print(f"\n  BEST SCORE:    {best_score} pts  ({best_label})")
    print(f"  CURRENT BEST:  2130 pts  (WC@14 + BB@GW26 + TC@GW33, k=0)")
    print(f"  TARGET:        2200 pts")
    if best_score >= 2200:
        print("  STATUS: PASS — sub-100k threshold met.")
    elif best_score > 2130:
        print(f"  STATUS: IMPROVEMENT — +{best_score - 2130} pts over current best, "
              f"but {2200 - best_score} pts short of target.")
    else:
        print(f"  STATUS: NO IMPROVEMENT — gap to target = {2200 - best_score} pts.")

    # ── 8. Per-GW breakdown for best strategy ───────────────────────────
    print(f"\n  Per-GW breakdown — best strategy ({best_label}):")
    print(f"  {'GW':>4}  {'Pts':>5}  {'Cumul':>7}  {'CapBonus':>9}  {'Chip':>10}  {'FTs':>3}  {'Bank':>5}")
    for row in best_df.sort_values("gw").itertuples():
        chip_str = str(row.chip) if row.chip else ""
        print(f"  GW{int(row.gw):2d}  {int(row.pts):>5}  "
              f"{int(row.cumulative):>7}  {int(row.captain_bonus):>9}  "
              f"{chip_str:>10}  {int(row.free_transfers):>3}  {float(row.bank):>5.1f}")

    # ── 9. Save outputs ──────────────────────────────────────────────────
    out_dir = Path(__file__).resolve().parents[1] / "reports"
    out_dir.mkdir(exist_ok=True)

    # Best strategy per-GW
    best_path = out_dir / "chip_sweep_best.csv"
    best_df["strategy"] = best_label
    best_df.to_csv(best_path, index=False)

    # Full sweep summary
    sweep_path = out_dir / "chip_sweep_results.csv"
    results_df.to_csv(sweep_path, index=False)

    print(f"\n  Best strategy GW breakdown saved to {best_path}")
    print(f"  Full sweep summary saved to {sweep_path}")


if __name__ == "__main__":
    main()
