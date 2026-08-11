#!/usr/bin/env python3
"""
Generate a GW1 squad recommendation for 2026-27 using the trained ML model.

Writes to research/gw1_squad_2026.json.

Matching strategy: Vaastav 'name' is the full player name (e.g. "Mohamed Salah");
FPL 'web_name' is typically the last name ("Salah"). We match on the last word of
the Vaastav name. Unmatched players fall back to ep_next for xPts.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from bot.data_collector import fetch_bootstrap, fetch_fixtures, load_multi_season_history
from bot.feature_engineering import (
    compute_ewma_stats,
    feature_columns,  # noqa: F401 — kept to satisfy IDE; we use sidecar list directly
    player_vs_opponent_features,
    resolve_opponent_team_name,
    rolling_window_stats,
)
from bot.fpl_rules import (
    CHIP_BENCH_BOOST,
    CHIP_FREE_HIT,
    CHIP_TRIPLE_CAPTAIN,
    CHIP_WILDCARD,
    chip_half,
)
from bot.season_forecaster import SeasonForecaster
from bot.season_planner import SeasonPlanner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).resolve().parents[1] / "bot" / "models"
RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"

POSITION_MAP = {"GK": 1, "GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}


def _add_position_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    pos_str = df["position"].str.upper().str.strip().replace({"GK": "GKP"})
    df["element_type"] = pos_str.map({"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4})
    df["pos_gkp"] = (df["element_type"] == 1).astype(float)
    df["pos_def"] = (df["element_type"] == 2).astype(float)
    df["pos_mid"] = (df["element_type"] == 3).astype(float)
    df["pos_fwd"] = (df["element_type"] == 4).astype(float)
    return df


def main() -> None:
    log.info("Fetching 2026-27 bootstrap and fixtures...")
    bootstrap = fetch_bootstrap(force=False)
    fixtures = fetch_fixtures(force=False)

    events = bootstrap.get("events", [])
    next_gw = next((e["id"] for e in events if e.get("is_next")), None)
    current_gw_event = next((e["id"] for e in events if e.get("is_current")), None)
    gw1 = next_gw or current_gw_event or 1
    log.info("Planning for GW%d", gw1)

    # Build lookups from 2026-27 bootstrap
    players_by_web_name = {}
    for p in bootstrap["elements"]:
        players_by_web_name[p["web_name"]] = p  # last writer wins for duplicate web_names
    ep_by_id = {int(p["id"]): float(p.get("ep_next") or 0) for p in bootstrap["elements"]}

    # Load 2025-26 history and compute feature state per player
    log.info("Loading 2025-26 player history...")
    history = load_multi_season_history(seasons=("2025-26",))
    log.info("Loaded %d rows", len(history))

    history = _add_position_features(history)
    if "value" in history.columns and "now_cost" not in history.columns:
        history = history.rename(columns={"value": "now_cost"})
    if "was_home" in history.columns:
        history["is_home"] = history["was_home"].astype(float)

    log.info("Computing features...")
    feat = compute_ewma_stats(history)
    feat = rolling_window_stats(feat)
    feat = resolve_opponent_team_name(feat)
    feat = player_vs_opponent_features(feat)

    # Last row per player = most recent feature state at end of 2025-26
    feat_sorted = feat.sort_values(["name", "season", "round"])
    last_rows = feat_sorted.groupby("name").last().reset_index()
    log.info("Unique players in feature frame: %d", len(last_rows))

    # Load exact model feature list from sidecar (same 53 cols across all 4 sub-models)
    sidecar = json.loads((MODELS_DIR / "minutes.pkl.json").read_text())
    model_features = sidecar["features"]

    # --- ML prediction (with graceful fallback) ---
    ml_xpts: dict[int, float] = {}
    matched = 0
    unmatched = 0
    ml_attempted = False

    try:
        from bot.models import FPLPointsPredictor

        rows_for_pred = []
        element_ids_for_pred = []
        element_types_for_pred = []

        for _, row in last_rows.iterrows():
            full_name = str(row.get("name", ""))
            last_word = full_name.strip().split()[-1] if full_name.strip() else ""
            player_2027 = players_by_web_name.get(last_word)
            if player_2027 is None:
                unmatched += 1
                continue
            matched += 1
            rows_for_pred.append(row)
            element_ids_for_pred.append(int(player_2027["id"]))
            element_types_for_pred.append(int(player_2027["element_type"]))

        log.info(
            "Name match: %d matched, %d unmatched (%.0f%%)",
            matched, unmatched, 100 * matched / max(1, matched + unmatched),
        )

        if rows_for_pred:
            pred_df = pd.DataFrame(rows_for_pred)
            medians = pred_df.reindex(columns=model_features).median()
            X_pred = pred_df.reindex(columns=model_features).fillna(medians)

            predictor = FPLPointsPredictor.load(MODELS_DIR)
            preds = predictor.predict(X_pred, element_types_for_pred)

            for el_id, xpts_val in zip(element_ids_for_pred, preds["expected_points"]):
                if pd.notna(xpts_val) and xpts_val > 0:
                    ml_xpts[el_id] = float(xpts_val)

            log.info("ML predictions generated for %d players", len(ml_xpts))
            ml_attempted = True

    except Exception as exc:
        log.warning("ML prediction failed (%s) — using ep_next only", exc)

    # Blend ML with ep_next: 0.5/0.5 where ep_next > 0, else ML alone
    blended_xpts: dict[int, float] = {}
    for el_id, ml_val in ml_xpts.items():
        ep_val = ep_by_id.get(el_id, 0.0)
        blended_xpts[el_id] = 0.5 * ml_val + 0.5 * ep_val if ep_val > 0 else ml_val

    # Run forecaster and planner with horizon=1 (GW1 only)
    log.info("Running SeasonForecaster...")
    forecaster = SeasonForecaster(horizon=1, max_candidates=150)
    forecasts = forecaster.forecast(
        bootstrap=bootstrap,
        fixtures=fixtures,
        current_gw=gw1,
        ml_xpts=blended_xpts if blended_xpts else None,
    )

    current_state = {
        "gameweek": gw1,
        "squad": [],
        "selling_prices": {},
        "bank": 1000,  # £100.0m — full pre-season budget
        "ft": 1,
        "chips_available": [CHIP_WILDCARD, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN, CHIP_BENCH_BOOST],
        "current_half": chip_half(gw1),
    }

    log.info("Running SeasonPlanner...")
    planner = SeasonPlanner(horizon=1)
    plan = planner.plan(forecasts=forecasts, current_state=current_state)

    output = {
        "gw": gw1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ml_used": ml_attempted and bool(ml_xpts),
        "match_stats": {"matched": matched, "unmatched": unmatched},
        "captain": plan.get("captain"),
        "transfers_in": plan.get("transfers_in", []),
        "transfers_out": plan.get("transfers_out", []),
        "chip": plan.get("chip"),
        "chip_reason": plan.get("chip_reason", ""),
        "gw_plan": plan.get("gw_plan", []),
    }

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESEARCH_DIR / "gw1_squad_2026.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    log.info("Written to %s", out_path)

    print("\n" + "=" * 60)
    print(f"GW{gw1} Squad Recommendation — 2026-27")
    print(f"ML used: {output['ml_used']}")
    if ml_attempted:
        total = matched + unmatched
        print(f"Players matched: {matched}/{total} ({100 * matched // max(1, total)}%)")
    cap = plan.get("captain")
    if cap:
        print(f"Captain: {cap.get('name', cap)}")
    print(f"Output: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
