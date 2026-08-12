#!/usr/bin/env python3
"""
Generate a GW1 squad recommendation for 2026-27 using the trained ML model.

Uses the FPL element-summary API (history_past) keyed to current element IDs —
this avoids the cross-season element ID mismatch that caused wrong ML features
when FPL reset all player IDs for 2026-27.

Writes to research/gw1_squad_2026.json in the format expected by /api/predict.
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from bot.data_collector import (
    build_player_pool,
    element_summaries_to_features,
    fetch_bootstrap,
    fetch_element_summaries_bulk,
    fetch_fixtures,
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

MODELS_DIR = Path(__file__).parent.parent / "bot" / "models"
RESEARCH_DIR = Path(__file__).parent.parent / "research"

GKP_POSITION = 1
POS_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Re-fetch summaries (bypass cache)")
    args = parser.parse_args()

    log.info("Fetching 2026-27 bootstrap and fixtures...")
    bootstrap = fetch_bootstrap(force=args.force)
    fixtures = fetch_fixtures(force=args.force)

    events = bootstrap.get("events", [])
    next_gw = next((e["id"] for e in events if e.get("is_next")), None)
    current_gw_event = next((e["id"] for e in events if e.get("is_current")), None)
    gw1 = next_gw or current_gw_event or 1
    log.info("Planning for GW%d", gw1)

    log.info("Building player pool...")
    pool = build_player_pool(force=args.force)
    ep_by_id: dict[int, float] = {
        int(row["id"]): _safe_float(row.get("ep_next"))
        for _, row in pool.iterrows()
    }

    log.info("Fetching element summaries for %d players (60-90s on first run)...", len(pool))
    summaries = fetch_element_summaries_bulk(pool["id"].tolist(), force=args.force)
    hist_df = element_summaries_to_features(summaries)
    log.info("History features for %d / %d players", len(hist_df), len(pool))

    # --- ML prediction (graceful fallback to ep_next-only) ---
    ml_xpts: dict[int, float] = {}
    ml_attempted = False

    try:
        from bot.models import FPLPointsPredictor

        sidecar_path = MODELS_DIR / "minutes.pkl.json"
        model_features = json.loads(sidecar_path.read_text())["features"]

        if not hist_df.empty:
            pool_by_id = pool.set_index("id")
            common_ids = hist_df.index.intersection(pool_by_id.index)

            pred_df = hist_df.loc[common_ids].copy()
            element_types = pool_by_id.loc[common_ids, "element_type"].astype(int).tolist()
            element_ids = [int(i) for i in common_ids]

            medians = pred_df.reindex(columns=model_features).median()
            X_pred = pred_df.reindex(columns=model_features).fillna(medians)

            predictor = FPLPointsPredictor.load(MODELS_DIR)
            preds = predictor.predict(X_pred, element_types)

            for el_id, xpts_val in zip(element_ids, preds["expected_points"]):
                if pd.notna(xpts_val) and xpts_val > 0:
                    ml_xpts[el_id] = float(xpts_val)

            log.info("ML predictions for %d / %d pool players", len(ml_xpts), len(pool))
            ml_attempted = True

    except Exception as exc:
        log.warning("ML prediction failed (%s) — falling back to ep_next only", exc)

    # Blend: 40% ML + 60% ep_next where ep_next > 0, else ML alone
    blended_xpts: dict[int, float] = {}
    for el_id, ml_val in ml_xpts.items():
        ep_val = ep_by_id.get(el_id, 0.0)
        blended_xpts[el_id] = 0.4 * ml_val + 0.6 * ep_val if ep_val > 0 else ml_val

    # Adjust blended_xpts downward for injured/doubtful players using ESPN news
    try:
        from bot.news_collector import build_news_features
        log.info("Fetching injury/news data from ESPN + FPL...")
        pool_with_news = build_news_features(pool, espn_enriched=True, force=args.force)
        avail_by_id = {
            int(row["id"]): float(row.get("availability_index", 1.0))
            for _, row in pool_with_news.iterrows()
        }
        adjusted = 0
        for el_id in list(blended_xpts.keys()):
            avail = avail_by_id.get(el_id, 1.0)
            if avail < 0.85:
                blended_xpts[el_id] *= avail
                adjusted += 1
        log.info("News availability: adjusted xPts for %d players", adjusted)
    except Exception as exc:
        log.warning("News fetch failed (%s) — using FPL status only", exc)

    log.info("Running SeasonForecaster (horizon=1, max_candidates=300)...")
    forecaster = SeasonForecaster(horizon=1, max_candidates=300)
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
        "bank": 1000,  # £100.0m pre-season budget (tenths)
        "ft": 1,
        "chips_available": [CHIP_WILDCARD, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN, CHIP_BENCH_BOOST],
        "current_half": chip_half(gw1),
    }

    log.info("Running SeasonPlanner...")
    planner = SeasonPlanner(horizon=1)
    plan = planner.plan(forecasts=forecasts, current_state=current_state)

    gw_plan = plan.get("gw_plan", [])
    if not gw_plan:
        log.error("Planner returned no gw_plan — aborting")
        sys.exit(1)

    first_gw = gw_plan[0]
    squad_15 = first_gw.get("squad", [])
    starting_xi = first_gw.get("starting_xi", [])
    bench = first_gw.get("bench", [])
    xi_ids = {p["element"] for p in starting_xi}

    # Captain override: never pick a GKP
    outfield_xi = [p for p in starting_xi if int(p["position"]) != GKP_POSITION]
    if not outfield_xi:
        outfield_xi = starting_xi

    captain_p = max(outfield_xi, key=lambda p: blended_xpts.get(p["element"], _safe_float(p.get("cost"))))
    vc_pool = [p for p in outfield_xi if p["element"] != captain_p["element"]]
    vice_p = max(vc_pool, key=lambda p: blended_xpts.get(p["element"], _safe_float(p.get("cost")))) if vc_pool else outfield_xi[0]

    log.info("Captain: %s (blended xPts=%.2f)", captain_p["name"], blended_xpts.get(captain_p["element"], 0))
    log.info("Vice:    %s (blended xPts=%.2f)", vice_p["name"], blended_xpts.get(vice_p["element"], 0))

    # Build squad_detail
    squad_detail = []
    for p in squad_15:
        el_id = p["element"]
        squad_detail.append({
            "element": el_id,
            "name": p["name"],
            "position": p["position"],
            "position_name": POS_NAME.get(p["position"], ""),
            "cost": p["cost"],
            "blended_xpts": round(blended_xpts.get(el_id, 0.0), 3),
            "ep_next": round(ep_by_id.get(el_id, 0.0), 2),
            "in_xi": el_id in xi_ids,
            "is_captain": el_id == captain_p["element"],
            "is_vice_captain": el_id == vice_p["element"],
        })

    # XI first (by position then xPts desc), bench last
    squad_detail.sort(key=lambda p: (0 if p["in_xi"] else 1, p["position"], -p["blended_xpts"]))

    total_cost = round(sum(p["cost"] for p in squad_detail), 1)
    xi_xpts = sum(p["blended_xpts"] for p in squad_detail if p["in_xi"])
    cap_xpts = blended_xpts.get(captain_p["element"], 0.0)
    expected_xi_pts = round(xi_xpts + cap_xpts, 2)  # captain double-counted

    # Build FPL picks payload (positions 1-11=XI, 12=bench GKP, 13-15=outfield bench)
    xi_sorted = sorted(starting_xi, key=lambda p: p["position"])  # GKP=1, DEF=2, MID=3, FWD=4
    bench_gkp = [p for p in bench if p["position"] == GKP_POSITION]
    bench_out = sorted(
        [p for p in bench if p["position"] != GKP_POSITION],
        key=lambda p: blended_xpts.get(p["element"], 0.0),
        reverse=True,
    )
    bench_ordered = bench_gkp + bench_out
    picks = []
    for i, p in enumerate(xi_sorted + bench_ordered):
        picks.append({
            "element": p["element"],
            "position": i + 1,
            "is_captain": p["element"] == captain_p["element"],
            "is_vice_captain": p["element"] == vice_p["element"],
        })

    # Build transfers payload for initial squad selection (no element_out)
    transfers = [
        {"element_in": p["element"], "purchase_price": int(round(_safe_float(p.get("cost")) * 10))}
        for p in squad_15
    ]

    output = {
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "season": "2026-27",
        "gameweek": gw1,
        "model": "FPLPointsPredictor (element-summary API, 40% ML + 60% ep_next)",
        "squad_cost_gpm": total_cost,
        "expected_xi_pts": expected_xi_pts,
        "captain_element": captain_p["element"],
        "captain_name": captain_p["name"],
        "vice_captain_element": vice_p["element"],
        "vice_captain_name": vice_p["name"],
        "squad_detail": squad_detail,
        "picks": picks,
        "transfers": transfers,
    }

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESEARCH_DIR / "gw1_squad_2026.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    log.info("Written to %s", out_path)

    print("\n" + "=" * 60)
    print(f"GW{gw1} Squad Recommendation — 2026-27")
    print(f"Captain:    {captain_p['name']} (xPts: {blended_xpts.get(captain_p['element'], 0):.2f})")
    print(f"Vice:       {vice_p['name']} (xPts: {blended_xpts.get(vice_p['element'], 0):.2f})")
    print(f"Total cost: £{total_cost}m | Expected XI pts: {expected_xi_pts}")
    print(f"ML used:    {ml_attempted and bool(ml_xpts)}")
    print(f"Output:     {out_path}")
    print("=" * 60)
    print("\nStarting XI:")
    for p in squad_detail:
        if p["in_xi"]:
            tag = " [C]" if p["is_captain"] else (" [V]" if p["is_vice_captain"] else "")
            print(f"  {p['position_name']:3}  {p['name']:<22} £{p['cost']:4.1f}m  xPts:{p['blended_xpts']:.2f}{tag}")
    print("\nBench:")
    for p in squad_detail:
        if not p["in_xi"]:
            print(f"  {p['position_name']:3}  {p['name']:<22} £{p['cost']:4.1f}m  xPts:{p['blended_xpts']:.2f}")


if __name__ == "__main__":
    main()
