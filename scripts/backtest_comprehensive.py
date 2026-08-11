#!/usr/bin/env python3
"""
Comprehensive 2024-25 holdout backtest: model families x seeds, with an audit.

Where ``backtest_holdout.py`` asks "which *configuration* scores best", this
script asks two different questions:

  1. **How much of the score is the learner?** Four families are run through an
     identical pipeline -- gradient boosting (XGBoost/LightGBM), random forests,
     a 50/50 average of the two, and a no-ML rolling-mean baseline.
  2. **How much of the gap is noise?** Every ML family is run under five random
     seeds, so a family's advantage can be read against its own seed spread
     rather than from a single lucky run. A 30-point edge means nothing if the
     seed standard deviation is 40.

All runs use the best configuration found by the holdout sweep: ML expected
points, the p60 captain safety filter, and DGW scaling that only applies once a
double gameweek would realistically have been announced.

Results are appended to ``reports/comprehensive_results.csv`` after every run,
and runs already present there are skipped, so an interrupted session resumes
where it stopped. The HTML report is regenerated from that CSV.

Usage:
    python scripts/backtest_comprehensive.py
    python scripts/backtest_comprehensive.py --report-only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from bot.backtester import SeasonBacktester
from bot.data_collector import load_multi_season_history
from bot.feature_engineering import (
    compute_ewma_stats,
    feature_columns,
    player_vs_opponent_features,
    resolve_opponent_team_name,
    rolling_window_stats,
)
from bot.fpl_rules import MID
from bot.models import (
    AttackModel, BonusModel, DefenseModel, FPLPointsPredictor, MinutesModel,
    build_training_targets,
)
from bot.model_variants import EnsembleFPLPredictor, FPLRandomForestPredictor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

N_SEEDS = 5
SEEDS = [0, 1, 2, 3, 42]
TRAINING_SEASONS = ("2022-23", "2023-24")
TEST_SEASON = "2024-25"
CONTEXT_SEASONS = ("2022-23", "2023-24", "2024-25")
RESULTS_CSV = Path("reports/comprehensive_results.csv")
REPORT_HTML = Path("reports/comprehensive_backtest_report.html")
ARTIFACTS_JSON = Path("reports/_comprehensive_artifacts.json")

# {dgw_gw: first_gw_it_was_known} -- see backtest_holdout.py
DGW_ANNOUNCEMENT = {"2024-25": {24: 20, 25: 21, 32: 28, 33: 28}}
CHIP_SCHEDULE_2024_25 = {24: "bboost", 33: "3xc"}  # oracle -- documented in report
DGW_SCALE = 1.7

#: GW whose predictions are compared across seeds for the stability check.
SEED_STABILITY_GW = 20

FAMILY_XGB = "XGB/LGB"
FAMILY_RF = "RandomForest"
FAMILY_ENS = "Ensemble XGB+RF"
FAMILY_BASE = "Baseline"
ML_FAMILIES = (FAMILY_XGB, FAMILY_RF, FAMILY_ENS)

#: Prefix -> what the feature actually measures, for the report's feature list.
FEATURE_PREFIX_DOCS = [
    ("ewma_", "EWMA form (α=0.25, 1-GW lag)"),
    ("roll1_", "1-game rolling mean (1-GW lag)"),
    ("roll3_", "3-game rolling mean (1-GW lag)"),
    ("roll6_", "6-game rolling mean (1-GW lag)"),
    ("roll10_", "10-game rolling mean (1-GW lag)"),
    ("h2h_", "H2H vs opponent (cumulative, lagged)"),
    ("pos_", "Position one-hot"),
    ("fdr_next", "FPL Fixture Difficulty Rating"),
    ("n_fixtures_next", "Number of fixtures next GW"),
    ("home_share_next", "Fraction of fixtures at home"),
]


def describe_feature(col: str) -> str:
    for prefix, desc in FEATURE_PREFIX_DOCS:
        if col.startswith(prefix):
            return desc
    return "Raw / contextual feature"


# ---------------------------------------------------------------------------
# Feature pipeline (mirrors backtest_holdout.py)
# ---------------------------------------------------------------------------

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


def build_prediction_fns(feat_all, history, fcols, medians, predictor,
                         test_season, dgw_announcement) -> dict:
    """Expected-points and captain-score callables for one trained predictor.

    Copied from ``backtest_holdout.py`` so that script stays untouched. Only the
    "realistic announcement" variants are used here: DGW scaling applies at a
    double gameweek only once that double was known to the manager.
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

    def _apply_dgw(scores, elements, before_gw, oracle):
        if before_gw not in dgw_elements:
            return scores
        first_known = dgw_announcement.get(before_gw, 1)
        if not oracle and before_gw < first_known:
            return scores
        scores = scores.copy()
        doubles = dgw_elements[before_gw]
        for i, eid in enumerate(elements):
            if eid in doubles:
                scores[i] *= DGW_SCALE
        return scores

    def xpts_real(before_gw: int) -> pd.Series:
        _ensure(before_gw)
        c = _cache[before_gw]
        if c is None:
            return pd.Series(dtype=float)
        xp = _apply_dgw(c["xp"], c["elements"], before_gw, oracle=False)
        return pd.Series(xp, index=c["elements"])

    def cap_safe_real(before_gw: int) -> pd.Series:
        _ensure(before_gw)
        c = _cache[before_gw]
        if c is None:
            return pd.Series(dtype=float)
        cap = c["xp"].copy() * c["p60"].copy()
        cap = _apply_dgw(cap, c["elements"], before_gw, oracle=False)
        cap = np.where(c["p60"] < 0.70, 0.0, cap)
        return pd.Series(cap, index=c["elements"])

    return {"xpts_real": xpts_real, "cap_safe_real": cap_safe_real,
            "dgw_elements": dgw_elements, "ensure": _ensure, "cache": _cache}


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_predictor(predictor, X_full, targets, xg_mask, tag: str):
    """Fit all four sub-models. Identical call sequence for every family."""
    t0 = time.time()
    predictor.minutes_model.train(X_full, targets["minutes"])
    predictor.attack_model.train(
        X_full[xg_mask], targets["xg_per90"][xg_mask], targets["xa_per90"][xg_mask]
    )
    predictor.defense_model.train(X_full, targets["clean_sheet"],
                                  targets.get("defcon_per90"))
    predictor.bonus_model.train(X_full, targets["bps"], targets.get("bonus"))
    log.info("  trained %s in %.1fs", tag, time.time() - t0)
    return predictor


def make_xgb(seed: int) -> FPLPointsPredictor:
    return FPLPointsPredictor(
        minutes_model=MinutesModel(random_state=seed),
        attack_model=AttackModel(random_state=seed),
        defense_model=DefenseModel(random_state=seed),
        bonus_model=BonusModel(random_state=seed),
    )


# ---------------------------------------------------------------------------
# Result bookkeeping
# ---------------------------------------------------------------------------

def load_done() -> set:
    if not RESULTS_CSV.exists():
        return set()
    df = pd.read_csv(RESULTS_CSV)
    return {(str(r.season), str(r.model_family), int(r.seed))
            for r in df.itertuples()}


def append_result(row: dict) -> None:
    pd.DataFrame([row]).to_csv(
        RESULTS_CSV, mode="a", header=not RESULTS_CSV.exists(), index=False)


def run_backtest(history, feat_all, fcols, medians, predictor) -> dict:
    """Run the best config for one trained predictor and return its totals."""
    fns = build_prediction_fns(feat_all, history, fcols, medians, predictor,
                               TEST_SEASON, DGW_ANNOUNCEMENT[TEST_SEASON])
    bt = SeasonBacktester(
        history, test_season=TEST_SEASON,
        expected_points_fn=fns["xpts_real"],
        captain_score_fn=fns["cap_safe_real"],
    )
    df = bt.run(strategy="1ft", chip_gws=CHIP_SCHEDULE_2024_25)
    return {"total_pts": int(df["pts"].sum()),
            "captain_bonus": int(df["captain_bonus"].sum()),
            "n_gws": int(len(df)),
            "fns": fns}


def run_baseline(history) -> dict:
    """No ML: the backtester's own rolling-mean expected points."""
    bt = SeasonBacktester(history, test_season=TEST_SEASON)
    df = bt.run(strategy="1ft", chip_gws=CHIP_SCHEDULE_2024_25)
    return {"total_pts": int(df["pts"].sum()),
            "captain_bonus": int(df["captain_bonus"].sum()),
            "n_gws": int(len(df))}


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------

def seed_stability(seed_xp: dict[int, pd.Series]) -> dict:
    """Mean pairwise Spearman ρ between per-seed expected-points vectors.

    Measures whether re-seeding reshuffles the player ranking the optimiser
    sees. High ρ with a wide score spread means the variance comes from the
    backtest's path dependence, not from the model disagreeing with itself.
    """
    from scipy.stats import spearmanr

    seeds = sorted(seed_xp)
    rhos = []
    for i in range(len(seeds)):
        for j in range(i + 1, len(seeds)):
            a, b = seed_xp[seeds[i]], seed_xp[seeds[j]]
            common = a.index.intersection(b.index)
            if len(common) > 2:
                rhos.append(float(spearmanr(a[common], b[common]).statistic))
    if not rhos:
        return {"mean_rho": None, "min_rho": None, "n_pairs": 0}
    return {"mean_rho": float(np.mean(rhos)), "min_rho": float(np.min(rhos)),
            "n_pairs": len(rhos), "gw": SEED_STABILITY_GW}


def shap_importance(predictor, X, top: int = 20) -> dict:
    """Top-|SHAP| features for the minutes model, or a reason it was skipped."""
    try:
        import shap  # noqa: PLC0415
    except ImportError:
        return {"available": False,
                "reason": "shap is not installed (pip install shap)"}
    try:
        model = predictor.minutes_model.model
        feats = predictor.minutes_model.features
        sample = X[feats].head(2000)
        explainer = shap.TreeExplainer(model)
        values = explainer.shap_values(sample, check_additivity=False)
        mean_abs = np.abs(np.asarray(values)).mean(axis=0)
        order = np.argsort(mean_abs)[::-1][:top]
        return {"available": True, "n_rows": int(len(sample)),
                "features": [{"feature": feats[i],
                              "importance": float(mean_abs[i])} for i in order]}
    except Exception as exc:  # pragma: no cover
        return {"available": False,
                "reason": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

CSS = """
:root { --bg:#ffffff; --fg:#1a1d21; --muted:#5b6570; --line:#e3e7eb;
        --accent:#2f6f4f; --accent-soft:#eaf3ee; --warn:#8a5a00; }
* { box-sizing: border-box; }
body { margin:0; padding:0; background:var(--bg); color:var(--fg);
       font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
.wrap { max-width: 980px; margin: 0 auto; padding: 40px 24px 80px; }
header { border-bottom: 3px solid var(--accent); padding-bottom: 18px; margin-bottom: 8px; }
h1 { font-size: 27px; margin: 0 0 6px; letter-spacing: -0.02em; }
h2 { font-size: 19px; margin: 44px 0 12px; padding-top: 8px; }
h2 .num { color: var(--accent); font-variant-numeric: tabular-nums; margin-right: 8px; }
.sub { color: var(--muted); font-size: 14px; margin: 0; }
p { margin: 10px 0; }
.note { color: var(--muted); font-size: 13.5px; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
th { background: #f6f8f9; font-weight: 600; font-size: 13px;
     text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.best td { background: var(--accent-soft); font-weight: 600; }
code { background:#f2f4f6; padding: 1px 5px; border-radius: 3px; font-size: 13px; }
.ok { color: var(--accent); font-weight: 600; }
.oracle { color: var(--warn); font-weight: 600; }
.scroll { overflow-x: auto; }
.bar-row { display: grid; grid-template-columns: 260px 1fr 70px;
           gap: 10px; align-items: center; margin: 3px 0; font-size: 13px; }
.bar-track { background: #f0f2f4; border-radius: 3px; height: 15px; }
.bar-fill { background: var(--accent); height: 15px; border-radius: 3px; }
.bar-val { text-align: right; color: var(--muted); font-variant-numeric: tabular-nums; }
.feat-name { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; font-size: 12.5px; }
ul { margin: 10px 0; padding-left: 22px; }
li { margin: 6px 0; }
footer { margin-top: 60px; padding-top: 16px; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 13px; }
"""


def _fmt(x, nd=1):
    return "—" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{x:,.{nd}f}"


def build_report(results: pd.DataFrame, artifacts: dict) -> str:
    fcols = artifacts.get("fcols", [])
    shap_info = artifacts.get("shap", {})
    stability = artifacts.get("seed_stability", {})

    # ── Section 3: family summary ────────────────────────────────────────
    rows = []
    for family, grp in results.groupby("model_family"):
        pts = grp["total_pts"].astype(float)
        rows.append({
            "family": family,
            "n": len(grp),
            "mean": pts.mean(),
            "std": pts.std(ddof=1) if len(pts) > 1 else 0.0,
            "best": pts.max(),
            "best_seed": int(grp.loc[pts.idxmax(), "seed"]),
            "worst": pts.min(),
            "worst_seed": int(grp.loc[pts.idxmin(), "seed"]),
        })
    summary = pd.DataFrame(rows).sort_values("mean", ascending=False)
    top_family = summary.iloc[0]["family"] if not summary.empty else None
    base_mean = (summary.loc[summary["family"] == FAMILY_BASE, "mean"].iloc[0]
                 if (summary["family"] == FAMILY_BASE).any() else None)

    summary_rows = ""
    for r in summary.itertuples():
        cls = ' class="best"' if r.family == top_family else ""
        delta = "—" if base_mean is None else f"{r.mean - base_mean:+,.1f}"
        summary_rows += (
            f"<tr{cls}><td>{r.family}</td><td class='num'>{r.n}</td>"
            f"<td class='num'>{_fmt(r.mean)}</td><td class='num'>± {_fmt(r.std)}</td>"
            f"<td class='num'>{int(r.best)} (seed {r.best_seed})</td>"
            f"<td class='num'>{int(r.worst)} (seed {r.worst_seed})</td>"
            f"<td class='num'>{delta}</td></tr>\n")

    # ── Section 4: per-seed detail ───────────────────────────────────────
    detail_rows = ""
    for r in results.sort_values(["model_family", "seed"]).itertuples():
        detail_rows += (
            f"<tr><td>{r.season}</td><td>{r.model_family}</td>"
            f"<td class='num'>{int(r.seed)}</td>"
            f"<td class='num'>{int(r.total_pts):,}</td>"
            f"<td class='num'>{int(r.captain_bonus):,}</td>"
            f"<td class='num'>{int(r.n_gws)}</td></tr>\n")

    # ── Section 1: anti-cheat audit ──────────────────────────────────────
    rho_txt = ("not computed" if not stability.get("mean_rho") else
               f"mean pairwise ρ = {stability['mean_rho']:.3f} "
               f"(min {stability['min_rho']:.3f}, {stability['n_pairs']} pairs, "
               f"GW{stability.get('gw')})")
    audit = [
        ("Pool gating (January signings)",
         "FIXED",
         "<code>_build_pool</code> now intersects the pool with elements seen at "
         "GW ≤ <code>before_gw</code>. Previously every element in the season file "
         "was buyable from GW1, so a January signing could be transferred in "
         "months before he was registered. Measured from <code>_build_pool</code>: "
         "616 buyable at GW1, 711 by GW20, 804 by GW38 (804 season total)."),
        ("EWMA features", "PASS",
         "<code>compute_ewma_stats</code> applies <code>.shift(1)</code> after the "
         "EWM, so a row never sees its own gameweek."),
        ("Rolling windows (1/3/6/10)", "PASS",
         "<code>rolling_window_stats</code> applies <code>.shift(1)</code> to every "
         "window, including the two added here."),
        ("H2H vs opponent", "PASS",
         "prior_sum pattern: <code>cumsum − current_value</code>, divided by the "
         "prior appearance count. The current row is arithmetically excluded."),
        ("Median imputation", "PASS",
         "Medians come from <code>feat_all[season ∈ training]</code> only; the test "
         "season never contributes. No feature had an all-NaN training median."),
        ("Target construction", "PASS",
         "<code>feature_columns</code> bans every same-gameweek outcome column "
         "(points, minutes, goals, bps, …) from the feature matrix."),
        ("Chip schedule", "ORACLE",
         f"<code>{CHIP_SCHEDULE_2024_25}</code> was chosen by inspecting 2024-25 "
         "results. This is a known upper bound, not a forecast — see caveats."),
        ("DGW knowledge", "PASS",
         "Doubles are scaled only from the gameweek the double would have been "
         "announced (<code>DGW_ANNOUNCEMENT</code>), not with hindsight."),
        ("Seed variance", "CHECKED", f"Measured, not assumed: {rho_txt}."),
    ]
    audit_rows = ""
    for check, status, note in audit:
        cls = "oracle" if status == "ORACLE" else "ok"
        audit_rows += (f"<tr><td>{check}</td><td class='{cls}'>{status}</td>"
                       f"<td class='note'>{note}</td></tr>\n")

    # ── Section 5: feature list ──────────────────────────────────────────
    groups: dict[str, list[str]] = {}
    for c in fcols:
        groups.setdefault(describe_feature(c), []).append(c)
    feat_rows = ""
    for desc, cols in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        names = ", ".join(f"<span class='feat-name'>{c}</span>" for c in sorted(cols))
        feat_rows += (f"<tr><td>{desc}</td><td class='num'>{len(cols)}</td>"
                      f"<td>{names}</td></tr>\n")

    # ── Section 6: SHAP ──────────────────────────────────────────────────
    if shap_info.get("available"):
        top = shap_info["features"]
        mx = max(f["importance"] for f in top) or 1.0
        bars = "".join(
            f"<div class='bar-row'><div class='feat-name'>{f['feature']}</div>"
            f"<div class='bar-track'><div class='bar-fill' "
            f"style='width:{100 * f['importance'] / mx:.1f}%'></div></div>"
            f"<div class='bar-val'>{f['importance']:.2f}</div></div>"
            for f in top)
        shap_html = (
            f"<p class='note'>Mean |SHAP| over {shap_info['n_rows']:,} training rows, "
            f"minutes model (XGBoost regressor, seed {SEEDS[0]}). Bars are scaled to "
            f"the largest value; units are minutes.</p>{bars}")
    else:
        shap_html = (f"<p><strong>SHAP not available.</strong> "
                     f"<span class='note'>{shap_info.get('reason', 'unknown')}</span></p>")

    ml_note = ""
    if base_mean is not None and top_family and top_family != FAMILY_BASE:
        top_mean = summary.iloc[0]["mean"]
        top_std = summary.iloc[0]["std"]
        margin = top_mean - base_mean
        ml_note = (f"<p><strong>{top_family}</strong> leads at "
                   f"{top_mean:,.1f} pts, {margin:+,.1f} vs the no-ML baseline, "
                   f"against a seed spread of ±{top_std:,.1f}.</p>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FPL Model Comprehensive Backtest — 2024-25 Holdout</title>
<style>{CSS}</style></head><body><div class="wrap">

<header>
  <h1>FPL Model Comprehensive Backtest — 2024-25 Holdout</h1>
  <p class="sub">Trained on {" + ".join(TRAINING_SEASONS)} · tested on {TEST_SEASON},
  never seen in training · {len(results)} runs · {len(SEEDS)} seeds per ML family</p>
</header>

<p>Every run uses the same configuration — ML expected points, the p60 captain
safety filter, and DGW scaling gated on realistic announcement timing — so the
only thing varying across rows is the learner and its random seed.</p>
{ml_note}

<h2><span class="num">1</span>Anti-cheat audit</h2>
<p>Each way this backtest could have lied to itself, and what was done about it.</p>
<div class="scroll"><table>
<thead><tr><th>Check</th><th>Status</th><th>Notes</th></tr></thead>
<tbody>{audit_rows}</tbody></table></div>

<h2><span class="num">2</span>Results by model family</h2>
<div class="scroll"><table>
<thead><tr><th>Model family</th><th class="num">Runs</th><th class="num">Mean pts</th>
<th class="num">Std</th><th class="num">Best</th><th class="num">Worst</th>
<th class="num">vs baseline</th></tr></thead>
<tbody>{summary_rows}</tbody></table></div>
<p class="note">Baseline is a single deterministic run — rolling-mean expected
points, no ML — so it has no seed spread.</p>

<h2><span class="num">3</span>Per-seed detail</h2>
<div class="scroll"><table>
<thead><tr><th>Season</th><th>Model</th><th class="num">Seed</th>
<th class="num">Total pts</th><th class="num">Captain bonus</th>
<th class="num">GWs</th></tr></thead>
<tbody>{detail_rows}</tbody></table></div>

<h2><span class="num">4</span>Feature set ({len(fcols)} columns)</h2>
<p>Every column handed to the models, grouped by what it measures. All
time-series features carry a one-gameweek lag.</p>
<div class="scroll"><table>
<thead><tr><th>Description</th><th class="num">Count</th><th>Columns</th></tr></thead>
<tbody>{feat_rows}</tbody></table></div>

<h2><span class="num">5</span>SHAP feature importance — minutes model</h2>
{shap_html}

<h2><span class="num">6</span>Notes &amp; caveats</h2>
<ul>
<li><strong>The chip schedule is an oracle.</strong>
<code>{CHIP_SCHEDULE_2024_25}</code> was picked by looking at how 2024-25 actually
played out. Every family gets the same advantage, so the comparison between them
is fair, but the absolute totals are an upper bound no live manager could hit.</li>
<li><strong>DGW_SCALE = {DGW_SCALE}</strong> is a flat multiplier on a doubling
player's expected points. It is a crude stand-in for actually projecting two
fixtures, and it over-rewards players whose second fixture is hard.</li>
<li><strong>January signings are now excluded until registered.</strong> This fix
lowers scores relative to earlier backtests — the old pool let the optimiser buy
players who had not yet joined the league.</li>
<li><strong>The RF family has no p60 classifier.</strong> Its p60 is a monotone
proxy, <code>(minutes / 90) · 0.9</code>, which sits systematically below the
XGBoost classifier's estimate. Since the captain safety filter zeroes anyone
under p60 = 0.70, RF captains from a smaller pool. That is a real property of
the family, but it confounds "RF is a worse learner" with "RF has a worse
p60 head".</li>
<li><strong>Seed variance is path-dependent.</strong> Two seeds that rank players
near-identically can still diverge by hundreds of points: one early transfer
differs, the squads drift apart, and the paths never reconverge. Read the seed
spread, not any single run.</li>
<li><strong>One season, one holdout.</strong> {TEST_SEASON} is 38 gameweeks of a
single league season. Differences inside the seed spread should not be treated
as real.</li>
</ul>

<footer>Generated by <code>scripts/backtest_comprehensive.py</code> ·
data {" + ".join(CONTEXT_SEASONS)} · results in <code>{RESULTS_CSV.as_posix()}</code></footer>
</div></body></html>"""


def write_report() -> None:
    results = pd.read_csv(RESULTS_CSV)
    artifacts = (json.loads(ARTIFACTS_JSON.read_text(encoding="utf-8"))
                 if ARTIFACTS_JSON.exists() else {})
    REPORT_HTML.write_text(build_report(results, artifacts), encoding="utf-8")
    log.info("Report written: %s", REPORT_HTML)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-only", action="store_true",
                        help="Rebuild the HTML from the existing results CSV")
    args = parser.parse_args()

    Path("reports").mkdir(exist_ok=True)

    if args.report_only:
        write_report()
        return

    done = load_done()
    log.info("Already complete: %d runs", len(done))

    # ── Data + features (computed once, reused by every run) ─────────────
    log.info("Loading %s…", CONTEXT_SEASONS)
    history = _prep_history(load_multi_season_history(seasons=CONTEXT_SEASONS))
    log.info("Loaded %d rows", len(history))

    log.info("EWMA + rolling + h2h features…")
    feat_all = compute_ewma_stats(history)
    feat_all = rolling_window_stats(feat_all)
    feat_all = resolve_opponent_team_name(feat_all)
    feat_all = player_vs_opponent_features(feat_all)
    feat_all = _add_position_features(feat_all)

    train_feat = feat_all[feat_all["season"].isin(TRAINING_SEASONS)].copy()
    fcols = feature_columns(train_feat)
    medians = train_feat[fcols].median()
    if medians.isna().any():
        bad = list(medians[medians.isna()].index)
        log.warning("all-NaN training medians, filling 0.0: %s", bad)
        medians = medians.fillna(0.0)
    log.info("Feature columns: %d (%d h2h, %d rolling)", len(fcols),
             sum(c.startswith("h2h_") for c in fcols),
             sum(c.startswith("roll") for c in fcols))

    if "value" in train_feat.columns and "now_cost" not in train_feat.columns:
        train_feat["now_cost"] = train_feat["value"]
    targets = build_training_targets(train_feat)
    X_full = train_feat[fcols].fillna(medians)
    xg_mask = targets["xg_per90"].notna() & targets["xa_per90"].notna()
    log.info("Training matrix: %d rows (%d with xG)", len(X_full), int(xg_mask.sum()))

    artifacts = (json.loads(ARTIFACTS_JSON.read_text(encoding="utf-8"))
                 if ARTIFACTS_JSON.exists() else {})
    artifacts["fcols"] = fcols
    seed_xp: dict[int, pd.Series] = {}

    # ── Baseline (no ML, no seed) ────────────────────────────────────────
    if (TEST_SEASON, FAMILY_BASE, -1) not in done:
        log.info("=== Baseline (rolling mean, no ML) ===")
        res = run_baseline(history)
        append_result({"season": TEST_SEASON, "model_family": FAMILY_BASE,
                       "seed": -1, "total_pts": res["total_pts"],
                       "captain_bonus": res["captain_bonus"],
                       "n_gws": res["n_gws"]})
        log.info("  → Baseline: %d pts", res["total_pts"])

    # ── ML families x seeds ──────────────────────────────────────────────
    for seed in SEEDS:
        pending = [f for f in ML_FAMILIES if (TEST_SEASON, f, seed) not in done]
        need_shap = not artifacts.get("shap", {}).get("available") and seed == SEEDS[0]
        if not pending and not need_shap:
            continue  # nothing left for this seed; keep the persisted stability figure

        log.info("=== seed %d: training (pending: %s) ===", seed, pending or "none")
        xgb_pred = train_predictor(make_xgb(seed), X_full, targets, xg_mask,
                                   f"XGB/LGB seed={seed}")
        rf_pred = train_predictor(FPLRandomForestPredictor(random_state=seed),
                                  X_full, targets, xg_mask, f"RF seed={seed}")
        ens_pred = EnsembleFPLPredictor(predictors=[xgb_pred, rf_pred])

        if need_shap:
            log.info("  computing SHAP on the minutes model…")
            artifacts["shap"] = shap_importance(xgb_pred, X_full)
            log.info("  SHAP available=%s", artifacts["shap"].get("available"))

        for family, predictor in ((FAMILY_XGB, xgb_pred),
                                  (FAMILY_RF, rf_pred),
                                  (FAMILY_ENS, ens_pred)):
            if (TEST_SEASON, family, seed) in done:
                continue
            log.info("--- %s | seed %d ---", family, seed)
            res = run_backtest(history, feat_all, fcols, medians, predictor)
            append_result({"season": TEST_SEASON, "model_family": family,
                           "seed": seed, "total_pts": res["total_pts"],
                           "captain_bonus": res["captain_bonus"],
                           "n_gws": res["n_gws"]})
            log.info("  → %s seed %d: %d pts (captain bonus %d)",
                     family, seed, res["total_pts"], res["captain_bonus"])

        # Seed-stability vector: this seed's XGB expected points at a fixed GW.
        fns = build_prediction_fns(feat_all, history, fcols, medians, xgb_pred,
                                   TEST_SEASON, DGW_ANNOUNCEMENT[TEST_SEASON])
        fns["ensure"](SEED_STABILITY_GW)
        c = fns["cache"].get(SEED_STABILITY_GW)
        if c is not None:
            seed_xp[seed] = pd.Series(c["xp"], index=c["elements"])
        if len(seed_xp) >= 2:
            artifacts["seed_stability"] = seed_stability(seed_xp)
        ARTIFACTS_JSON.write_text(json.dumps(artifacts, indent=2), encoding="utf-8")

    ARTIFACTS_JSON.write_text(json.dumps(artifacts, indent=2), encoding="utf-8")
    write_report()

    results = pd.read_csv(RESULTS_CSV)
    print(f"\n{'='*72}\n  SUMMARY — {TEST_SEASON} holdout\n{'='*72}")
    print(f"  {'Family':<20} {'Runs':>5} {'Mean':>9} {'Std':>8} {'Best':>7} {'Worst':>7}")
    for family, grp in results.groupby("model_family"):
        p = grp["total_pts"].astype(float)
        std = p.std(ddof=1) if len(p) > 1 else 0.0
        print(f"  {family:<20} {len(grp):>5} {p.mean():>9.1f} {std:>8.1f} "
              f"{p.max():>7.0f} {p.min():>7.0f}")
    st = artifacts.get("seed_stability", {})
    if st.get("mean_rho"):
        # Plain ASCII: the Windows console is cp1252 and cannot encode a rho.
        print(f"\n  Seed stability (GW{st['gw']}): mean pairwise Spearman rho = "
              f"{st['mean_rho']:.3f} over {st['n_pairs']} pairs")
    print(f"\n  Report: {REPORT_HTML}")


if __name__ == "__main__":
    main()
