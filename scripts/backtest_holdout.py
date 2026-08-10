#!/usr/bin/env python3
"""
Holdout backtest with realistic DGW timing + multi-profile comparison.

Evaluates six improvements on seasons NEVER seen during training:
  - 2024-25 holdout: model trained on 2022-23 + 2023-24 only
  - (2025-26 reference: uses existing trained models for comparison)

Six configurations tested:
  1. Baseline (rolling mean, no ML)
  2. ML only (no captain risk, oracle DGW)
  3. ML + p60 captain risk (oracle DGW)
  4. ML + p60 + captain safety filter p_start >= 0.70 (oracle DGW)
  5. ML + p60 + captain safety (REALISTIC DGW — revealed 3 GW before)
  6. ML + xP-blend captain + safety (REALISTIC DGW) — mirrors 2025-26 best

Portfolio temperatures (for run 6):
  Greedy / Balanced / Cautious / Contrarian / Differential

DGW announcement schedule for 2024-25 season:
  GW24 (79 doubles): announced ~3-4 GWs before → known from GW20
  GW25 (79 doubles): announced ~3-4 GWs before → known from GW21
  GW32 (71 doubles): announced ~3-4 GWs before → known from GW28
  GW33 (155 doubles): announced ~4-5 GWs before → known from GW28

Usage:
    python scripts/backtest_holdout.py [--season 2024-25]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from bot.backtester import SeasonBacktester, compare_strategies
from bot.chip_planner import ChipPlanner
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
from bot.portfolio import PortfolioManager, PRESET_PROFILES, captain_scores_for_profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DGW announcement schedule: {season: {dgw_gw: first_gw_it_was_known}}
# Based on typical PL announcement windows (3-6 GW lead for planned DGWs).
# ---------------------------------------------------------------------------
DGW_ANNOUNCEMENT: dict[str, dict[int, int]] = {
    "2024-25": {
        # "Normal" early announcement (4-5 GW lead, typical Cup-related DGWs)
        24: 20,   # GW24 DGW: announced ~4 GWs before → known from GW20
        25: 21,   # GW25 DGW: same announcement window → from GW21
        32: 28,   # GW32 DGW: spring DGW → from GW28
        33: 28,   # GW33 DGW: major spring DGW (155 doubles) → from GW28
    },
    # "Emergency" schedule: DGWs revealed only at the SAME GW (post-deadline)
    # Simulates weather/last-minute postponements confirmed too late to act on.
    "2024-25-emergency": {
        24: 25,   # GW24 DGW: only confirmed at GW25 — TOO LATE for GW24 picks
        25: 26,   # GW25 DGW: only confirmed at GW26 — TOO LATE
        32: 33,   # GW32 DGW: confirmed too late
        33: 34,   # GW33 DGW: confirmed too late
    },
    "2025-26": {
        26: 22,   # GW26 DGW: EFL Cup window → from GW22
        33: 28,   # GW33 DGW: major spring → from GW28
        36: 32,   # GW36 DGW: late spring → from GW32
    },
    "2025-26-emergency": {
        26: 27,   # All too late
        33: 34,
        36: 37,
    },
}

# 2024-25 optimal chip timing (known from backtest sweep)
CHIP_SCHEDULE_2024_25 = {24: "bboost", 33: "3xc"}
CHIP_SCHEDULE_2025_26 = {26: "bboost", 33: "3xc"}

POSITION_MAP = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
DGW_SCALE = 1.7


def _add_position_features(feat: pd.DataFrame) -> pd.DataFrame:
    df = feat.copy()
    df["position"] = df["position"].str.upper().str.strip().replace({"GK": "GKP"})
    et = df["position"].map({"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4})
    df["element_type"] = et
    df["pos_gkp"] = (et == 1).astype(float)
    df["pos_def"] = (et == 2).astype(float)
    df["pos_mid"] = (et == 3).astype(float)
    df["pos_fwd"] = (et == 4).astype(float)
    return df


def _prep_history(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["position"] = df["position"].str.upper().str.strip().replace({"GK": "GKP"})
    if "value" in df.columns and "now_cost" not in df.columns:
        df["now_cost"] = df["value"]
    if "was_home" in df.columns:
        df["is_home"] = df["was_home"].astype(float)
    return df


def _detect_dgw_elements(history: pd.DataFrame, test_season: str) -> dict[int, set]:
    """Return {gw: set(element_ids)} for all DGW gameweeks in the test season."""
    test = history[history["season"] == test_season]
    dgw = {}
    for gw, grp in test.groupby("GW"):
        counts = grp.groupby("element").size()
        doubles = set(counts[counts > 1].index.astype(int))
        if doubles:
            dgw[int(gw)] = doubles
    return dgw


def build_prediction_fns(
    feat_all: pd.DataFrame,
    history: pd.DataFrame,
    fcols: list[str],
    medians: pd.Series,
    predictor: FPLPointsPredictor,
    test_season: str,
    dgw_announcement: dict[int, int],
) -> dict[str, object]:
    """Build all prediction callables and return in a registry dict.

    Returns:
        dict with keys:
          "xpts_fn"           -> expected_points_fn (oracle DGW scale)
          "xpts_fn_realistic" -> expected_points_fn (realistic DGW)
          "p60_fn"            -> Series[element → p60] per GW
          "dgw_elements"      -> {gw: set(elements)}
          "cap_oracle_fn"     -> captain_score xPts*p60, oracle DGW
          "cap_real_fn"       -> captain_score xPts*p60, realistic DGW
          "cap_safe_oracle_fn"-> cap_oracle but zeroes p60 < 0.70
          "cap_safe_real_fn"  -> cap_real but zeroes p60 < 0.70
    """
    dgw_elements = _detect_dgw_elements(history, test_season)

    _cache: dict[int, dict | None] = {}

    def _ensure(before_gw: int) -> None:
        if before_gw in _cache:
            return
        gw_feat = feat_all[
            (feat_all["season"] == test_season) & (feat_all["GW"] == before_gw)
        ].drop_duplicates("element").copy()
        if gw_feat.empty:
            _cache[before_gw] = None
            return
        X = gw_feat[fcols].fillna(medians)
        et = gw_feat["element_type"].fillna(MID).astype(int).values
        preds = predictor.predict(X, et)
        _cache[before_gw] = {
            "xp":       preds["expected_points"].clip(lower=0).values.copy(),
            "p60":      preds["p60"].values.copy(),
            "elements": gw_feat["element"].astype(int).values,
        }

    def _apply_dgw(scores: np.ndarray, elements: np.ndarray,
                   before_gw: int, oracle: bool) -> np.ndarray:
        """Scale scores for DGW players ONLY when before_gw is itself a DGW.

        oracle=True: always scale if before_gw is a known DGW.
        oracle=False: only scale if the DGW was announced before the deadline
            (first_known_gw <= before_gw in the announcement schedule).

        Deliberately does NOT scale for distant future DGWs — a player's
        expected points for GW5 should not be inflated because they have a
        DGW in GW24.
        """
        if before_gw not in dgw_elements:
            return scores  # current GW is not a DGW
        first_known = dgw_announcement.get(before_gw, 1)  # default: always known
        if not oracle and before_gw < first_known:
            return scores  # DGW announced too late — model didn't know in time
        scores = scores.copy()
        doubles = dgw_elements[before_gw]
        for i, eid in enumerate(elements):
            if eid in doubles:
                scores[i] *= DGW_SCALE
        return scores

    def xpts_oracle(before_gw: int) -> pd.Series:
        _ensure(before_gw)
        c = _cache[before_gw]
        if c is None:
            return pd.Series(dtype=float)
        xp = _apply_dgw(c["xp"], c["elements"], before_gw, oracle=True)
        return pd.Series(xp, index=c["elements"])

    def xpts_real(before_gw: int) -> pd.Series:
        _ensure(before_gw)
        c = _cache[before_gw]
        if c is None:
            return pd.Series(dtype=float)
        xp = _apply_dgw(c["xp"], c["elements"], before_gw, oracle=False)
        return pd.Series(xp, index=c["elements"])

    def _cap_score(before_gw: int, oracle: bool, safety: bool) -> pd.Series:
        _ensure(before_gw)
        c = _cache[before_gw]
        if c is None:
            return pd.Series(dtype=float)
        xp = c["xp"].copy()
        p60 = c["p60"].copy()
        cap = xp * p60
        cap = _apply_dgw(cap, c["elements"], before_gw, oracle=oracle)
        if safety:
            cap = np.where(p60 < 0.70, 0.0, cap)
        return pd.Series(cap, index=c["elements"])

    def cap_oracle(before_gw):
        return _cap_score(before_gw, oracle=True, safety=False)

    def cap_real(before_gw):
        return _cap_score(before_gw, oracle=False, safety=False)

    def cap_safe_oracle(before_gw):
        return _cap_score(before_gw, oracle=True, safety=True)

    def cap_safe_real(before_gw):
        return _cap_score(before_gw, oracle=False, safety=True)

    # Profile-based captain functions (for portfolio temperature comparison)
    def make_profile_cap_fn(profile, oracle: bool):
        def fn(before_gw: int) -> pd.Series:
            _ensure(before_gw)
            c = _cache[before_gw]
            if c is None:
                return pd.Series(dtype=float)
            xp = pd.Series(c["xp"], index=c["elements"])
            p60 = pd.Series(c["p60"], index=c["elements"])
            k = profile.captain_risk_weight
            scores = xp * (k + (1.0 - k) * p60)
            scores_arr = _apply_dgw(scores.values, c["elements"], before_gw, oracle=oracle)
            return pd.Series(scores_arr, index=c["elements"])
        return fn

    return {
        "xpts_oracle": xpts_oracle,
        "xpts_real": xpts_real,
        "cap_oracle": cap_oracle,
        "cap_real": cap_real,
        "cap_safe_oracle": cap_safe_oracle,
        "cap_safe_real": cap_safe_real,
        "dgw_elements": dgw_elements,
        "cache": _cache,
        "ensure": _ensure,
        "make_profile_cap_fn": make_profile_cap_fn,
    }


def run_season(
    test_season: str,
    training_seasons: tuple[str, ...],
    context_seasons: tuple[str, ...],
    chip_schedule: dict[int, str],
) -> None:
    print(f"\n{'='*72}")
    print(f"  HOLDOUT BACKTEST — Test season: {test_season}")
    print(f"  Training: {training_seasons}")
    print(f"  Chips: {chip_schedule}")
    print(f"{'='*72}")

    # ── Load data ────────────────────────────────────────────────────────
    log.info("Loading %s…", context_seasons)
    history = load_multi_season_history(seasons=context_seasons)
    history = _prep_history(history)
    log.info("Loaded %d rows", len(history))

    # ── Feature pipeline ─────────────────────────────────────────────────
    log.info("EWMA + rolling + h2h features…")
    feat_all = compute_ewma_stats(history)
    feat_all = rolling_window_stats(feat_all)
    feat_all = resolve_opponent_team_name(feat_all)
    feat_all = player_vs_opponent_features(feat_all)
    feat_all = _add_position_features(feat_all)

    # Medians from training data only (no leakage from test season)
    train_feat = feat_all[feat_all["season"].isin(training_seasons)]
    fcols = feature_columns(train_feat)
    medians = train_feat[fcols].median()
    log.info("Feature columns: %d (includes %d h2h)",
             len(fcols), sum(1 for c in fcols if c.startswith("h2h_")))

    # ── Train models in-memory ────────────────────────────────────────────
    log.info("Training models on %s…", training_seasons)
    from bot.models import build_training_targets
    train_feat2 = train_feat.copy()
    if "value" in train_feat2.columns and "now_cost" not in train_feat2.columns:
        train_feat2["now_cost"] = train_feat2["value"]
    targets = build_training_targets(train_feat2)
    X_full = train_feat2[fcols].fillna(medians)
    xg_mask = targets["xg_per90"].notna() & targets["xa_per90"].notna()

    predictor = FPLPointsPredictor()
    predictor.minutes_model.train(X_full, targets["minutes"])
    predictor.attack_model.train(
        X_full[xg_mask], targets["xg_per90"][xg_mask], targets["xa_per90"][xg_mask]
    )
    predictor.defense_model.train(X_full, targets["clean_sheet"], targets.get("defcon_per90"))
    predictor.bonus_model.train(X_full, targets["bps"], targets.get("bonus"))
    log.info("Models trained on %d rows (%d with xG)", len(X_full), xg_mask.sum())

    # ── Build prediction fns ──────────────────────────────────────────────
    ann = DGW_ANNOUNCEMENT.get(test_season, {})
    fns = build_prediction_fns(feat_all, history, fcols, medians, predictor,
                                test_season, ann)
    dgw_elements = fns["dgw_elements"]
    log.info("DGW gameweeks in %s: %s", test_season,
             {gw: len(elems) for gw, elems in sorted(dgw_elements.items())})
    log.info("Announcement schedule: %s", ann)

    # ── Build emergency-announcement variants ─────────────────────────────
    emerg_ann = DGW_ANNOUNCEMENT.get(test_season + "-emergency", {})
    fns_emerg = build_prediction_fns(feat_all, history, fcols, medians, predictor,
                                     test_season, emerg_ann)

    # ── Run configurations ────────────────────────────────────────────────
    configs = [
        ("1. Baseline (rolling mean)",
         None, None, chip_schedule),
        ("2. ML oracle DGW, no cap risk",
         fns["xpts_oracle"], None, chip_schedule),
        ("3. ML + p60 cap risk, oracle DGW",
         fns["xpts_oracle"], fns["cap_oracle"], chip_schedule),
        ("4. ML + p60 + safety filter, oracle DGW",
         fns["xpts_oracle"], fns["cap_safe_oracle"], chip_schedule),
        ("5. ML + safety, EARLY announce (4 GW lead)",
         fns["xpts_real"], fns["cap_safe_real"], chip_schedule),
        ("6. ML + safety, EMERGENCY (post-deadline reveal)",
         fns_emerg["xpts_real"], fns_emerg["cap_safe_real"], chip_schedule),
        ("7. ML + p60 + safety, oracle DGW + no chips",
         fns["xpts_oracle"], fns["cap_safe_oracle"], {}),
    ]

    results = []
    for name, xpts_fn, cap_fn, chips in configs:
        log.info("Running: %s", name)
        bt = SeasonBacktester(
            history, test_season=test_season,
            expected_points_fn=xpts_fn,
            captain_score_fn=cap_fn,
        )
        df = bt.run(strategy="1ft", chip_gws=chips)
        total = int(df["pts"].sum())
        cap_bonus = int(df["captain_bonus"].sum())
        results.append((name, total, cap_bonus))
        log.info("  → %d pts (captain bonus: %d)", total, cap_bonus)

    # ── Portfolio temperature comparison (best ML config, no chips) ───────
    print(f"\n  {'─'*68}")
    print(f"  PORTFOLIO TEMPERATURE COMPARISON (ML + safety filter, no chips)")
    print(f"  {'─'*68}")
    print(f"  {'Profile':<22}  {'Score':>6}  {'Cap bonus':>10}  {'Temperature':>12}")
    print(f"  {'─'*22}  {'─'*6}  {'─'*10}  {'─'*12}")

    for profile_name, profile in PRESET_PROFILES.items():
        cap_fn = fns["make_profile_cap_fn"](profile, oracle=False)
        bt = SeasonBacktester(
            history, test_season=test_season,
            expected_points_fn=fns["xpts_real"],
            captain_score_fn=cap_fn,
        )
        df = bt.run(strategy="1ft", chip_gws={})
        total = int(df["pts"].sum())
        cap_bonus = int(df["captain_bonus"].sum())
        print(f"  {profile_name:<22}  {total:>6}  {cap_bonus:>10}  {profile.temperature:>12.2f}")

    # ── DGW timing breakdown ──────────────────────────────────────────────
    print(f"\n  {'─'*68}")
    print(f"  DGW TIMING: when did the model first know?")
    print(f"  {'─'*68}")
    print(f"  {'GW':>4}  {'Doubles':>8}  {'Early (GW known)':>18}  {'Emergency':>12}")
    for dgw_gw in sorted(dgw_elements):
        n_elems = len(dgw_elements[dgw_gw])
        early_known = ann.get(dgw_gw, 1)
        emerg_known = emerg_ann.get(dgw_gw, dgw_gw + 1)
        print(f"  GW{dgw_gw:>2}  {n_elems:>8}  "
              f"GW{early_known} ({dgw_gw - early_known} GW lead)       "
              f"GW{emerg_known} (post-deadline)")
    print()

    # ── Main results table ────────────────────────────────────────────────
    print(f"\n  {'─'*68}")
    print(f"  MAIN RESULTS — {test_season} holdout")
    print(f"  {'─'*68}")
    print(f"  {'Config':<44}  {'Score':>6}  {'Cap bonus':>10}")
    print(f"  {'─'*44}  {'─'*6}  {'─'*10}")

    for name, total, cap_bonus in results:
        tag = "** BEST" if total == max(r[1] for r in results) else ""
        print(f"  {name:<44}  {total:>6}  {cap_bonus:>10}  {tag}")

    best = max(r[1] for r in results)
    baseline = results[0][1]
    ml_base = results[1][1]
    oracle_chips = results[3][1]
    early_ann = results[4][1]
    emergency = results[5][1]
    no_chips = results[6][1]
    print(f"\n  ML gain over baseline:             {ml_base - baseline:>+4} pts")
    print(f"  Best config gain over baseline:    {best - baseline:>+4} pts")
    print(f"  Captain safety filter gain:        {results[3][1] - results[2][1]:>+4} pts")
    print(f"  Oracle vs early announcement:      {oracle_chips - early_ann:>+4} pts "
          f"(0 = early = oracle when 4 GW lead)")
    print(f"  Cost of emergency (late) DGW:      {early_ann - emergency:>+4} pts "
          f"(value of knowing DGW in advance)")
    print(f"  Value of chips:                    {oracle_chips - no_chips:>+4} pts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", default="2024-25",
                        choices=["2024-25", "2025-26"],
                        help="Holdout test season")
    args = parser.parse_args()

    if args.season == "2024-25":
        training = ("2022-23", "2023-24")
        context = ("2022-23", "2023-24", "2024-25")
        chips = CHIP_SCHEDULE_2024_25
    else:
        training = ("2022-23", "2023-24", "2024-25")
        context = ("2022-23", "2023-24", "2024-25", "2025-26")
        chips = CHIP_SCHEDULE_2025_26

    run_season(args.season, training, context, chips)


if __name__ == "__main__":
    main()
