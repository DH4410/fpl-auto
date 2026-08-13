"""
Post-matchday automated update loop — Module 8.

Orchestrates the full planning pipeline after each gameweek or matchday:

  1. Fetch live FPL API data (bootstrap, fixtures, live GW scores).
  2. Update player statuses (injuries, suspensions, price changes).
  3. Run ML inference (FPLPointsPredictor) blended 40/60 with ep_next.
  4. Re-run the lightweight forecaster with ML predictions.
  5. Re-run the rolling MILP planner.
  6. Evaluate chip timing.
  7. Write updated reports to ``reports/``.

This module is the main entry point for automated operation. It can be called
manually after each gameweek or wired into a scheduler (cron, GitHub Actions).

No write endpoints are called here. Every output is advisory.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from . import data_collector, reporter
from .season_forecaster import SeasonForecaster
from .season_planner import SeasonPlanner
from .chip_planner import ChipPlanner
from .fpl_rules import chip_half, CHIP_WILDCARD, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN, CHIP_BENCH_BOOST

log = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

MODELS_DIR = Path(__file__).resolve().parent / "models"

#: ML blend weight (fraction from ML model; 1 - ML_BLEND from ep_next).
ML_BLEND = 0.40


# ---------------------------------------------------------------------------
# Current state helpers
# ---------------------------------------------------------------------------

def build_current_state(
    bootstrap: dict,
    my_team: dict | None,
    current_gw: int,
    entry_info: dict | None = None,
) -> dict[str, Any]:
    """Assemble ``current_state`` for the planner from raw API responses.

    If ``my_team`` is None (pre-season or unauthenticated), returns an empty
    squad with full starting budget.

    Parameters
    ----------
    bootstrap:
        Full bootstrap-static JSON.
    my_team:
        Response from ``/my-team/{entry_id}/``, or None.
    current_gw:
        The gameweek being planned.
    entry_info:
        Response from ``/entry/{entry_id}/``, used for bank balance.
    """
    if my_team is None:
        return {
            "gameweek": current_gw,
            "squad": [],
            "selling_prices": {},
            "bank": 1000,  # £100.0m in tenths
            "ft": 1,
            "chips_available": [CHIP_WILDCARD, CHIP_FREE_HIT,
                                  CHIP_TRIPLE_CAPTAIN, CHIP_BENCH_BOOST],
            "current_half": chip_half(current_gw),
        }

    picks = my_team.get("picks", [])
    squad_ids = [p["element"] for p in picks]
    selling_prices = {p["element"]: p["selling_price"] for p in picks}

    # Bank from entry_info if available, else from my_team.
    if entry_info:
        bank_tenths = int(entry_info.get("last_deadline_bank", 0))
    else:
        bank_tenths = int(my_team.get("transfers", {}).get("bank", 0))

    ft = int(my_team.get("transfers", {}).get("limit", 1))

    chips_status = my_team.get("chips", [])
    chips_used_this_half = {
        c["name"] for c in chips_status
        if c.get("status_for_entry") == "played"
        and chip_half(c.get("event", current_gw)) == chip_half(current_gw)
    }
    all_chips = [CHIP_WILDCARD, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN, CHIP_BENCH_BOOST]
    chips_available = [c for c in all_chips if c not in chips_used_this_half]

    return {
        "gameweek": current_gw,
        "squad": squad_ids,
        "selling_prices": selling_prices,
        "bank": bank_tenths,
        "ft": ft,
        "chips_available": chips_available,
        "current_half": chip_half(current_gw),
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

class SeasonUpdater:
    """Runs the full post-matchday pipeline and writes reports.

    Parameters
    ----------
    horizon:
        Planning horizon in gameweeks.
    max_candidates:
        Maximum candidate pool size passed to the forecaster.
    """

    def __init__(
        self,
        horizon: int = 6,
        max_candidates: int = 150,
        verbose: bool = False,
    ):
        self.horizon = horizon
        self.max_candidates = max_candidates
        self.forecaster = SeasonForecaster(horizon=horizon, max_candidates=max_candidates)
        self.planner = SeasonPlanner(horizon=horizon)
        self.chip_planner = ChipPlanner()
        if verbose:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        # Try to load ML predictor (fails gracefully if models not yet trained).
        self._predictor = None
        self._model_features: list[str] = []
        try:
            from .models import FPLPointsPredictor
            sidecar = MODELS_DIR / "minutes.pkl.json"
            self._model_features = json.loads(sidecar.read_text())["features"]
            self._predictor = FPLPointsPredictor.load(MODELS_DIR)
            log.info("ML predictor loaded from %s (%d features)", MODELS_DIR, len(self._model_features))
        except Exception as exc:
            log.warning("ML predictor unavailable (%s) — forecaster will use ep_next + PPG only", exc)

    def run(
        self,
        current_gw: int,
        my_team: dict | None = None,
        entry_info: dict | None = None,
    ) -> dict[str, Any]:
        """Execute the full update loop for ``current_gw``.

        Parameters
        ----------
        current_gw:
            The GW being planned (next to be played).
        my_team:
            Live ``/my-team/{entry_id}/`` JSON, or None for pre-season.
        entry_info:
            Live ``/entry/{entry_id}/`` JSON, or None.

        Returns
        -------
        dict
            Full plan + chip recommendations + paths to written reports.
        """
        log.info("=== FPL Season Updater — GW%d ===", current_gw)

        # 1. Fetch data.
        log.info("Fetching bootstrap and fixtures…")
        bootstrap = data_collector.fetch_bootstrap()
        fixtures = data_collector.fetch_fixtures()

        # 2. Build current state.
        state = build_current_state(bootstrap, my_team, current_gw, entry_info)
        log.info(
            "Squad: %d players | Bank: £%.1fm | FT: %d | Chips: %s",
            len(state["squad"]),
            state["bank"] / 10.0,
            state["ft"],
            state["chips_available"],
        )

        # 3. ML predictions — blended 40% ML + 60% ep_next per player.
        ml_xpts: dict[int, float] | None = None
        if self._predictor is not None:
            ml_xpts = self._compute_ml_xpts(bootstrap)
            if not ml_xpts:
                ml_xpts = None

        # 4. Forecast.
        log.info("Running forecaster (horizon=%d GWs, ML=%s)…",
                 self.horizon, "on" if ml_xpts else "off (ep_next only)")
        forecasts = self.forecaster.forecast(
            bootstrap=bootstrap,
            fixtures=fixtures,
            current_gw=current_gw,
            owned_ids=state["squad"],
            ml_xpts=ml_xpts,
        )
        log.info("Forecast table: %d rows (%d players × %d GWs)",
                 len(forecasts), forecasts["element"].nunique(), self.horizon)

        # Adjust xPts downward for players flagged injured/doubtful in news.
        # Only applies to the CURRENT gameweek — a doubtful player for GW1 may
        # be fully available by GW2+, so discounting all future rows would cause
        # the planner to incorrectly sell players who should be held.
        try:
            from .news_collector import build_news_features
            pool_df = data_collector.build_player_pool()
            pool_with_news = build_news_features(pool_df, espn_enriched=True)
            avail = {
                int(row["id"]): float(row.get("availability_index", 1.0))
                for _, row in pool_with_news.iterrows()
            }
            avail_col = forecasts["element"].map(avail).fillna(1.0)
            mask = (forecasts["gw"] == current_gw) & (avail_col < 0.85)
            forecasts.loc[mask, "xpts"] *= avail_col[mask]
            log.info("News adjustment applied to %d current-GW forecast rows", mask.sum())
        except Exception as exc:
            log.warning("News enrichment skipped (%s)", exc)

        # Apply set-piece duty bonuses (penalty takers, corner takers, FK specialists).
        try:
            from .prediction_adjustments import apply_setpiece_boosts
            bootstrap_df = pd.DataFrame(bootstrap["elements"])
            forecasts = apply_setpiece_boosts(forecasts, bootstrap_df)
        except Exception as exc:
            log.warning("Set-piece boost skipped (%s)", exc)

        # 5. Plan.
        log.info("Running MILP planner…")
        plan = self.planner.plan(forecasts=forecasts, current_state=state)
        log.info(
            "GW%d: %d transfer(s), %d hit(s), Captain=%s",
            current_gw,
            len(plan["transfers_in"]),
            plan["hits"],
            plan["captain"]["name"],
        )

        # 6. Chips.
        chip_result = self.chip_planner.evaluate(
            forecasts=forecasts,
            gw_plan=plan["gw_plan"],
            chips_available=state["chips_available"],
            current_gw=current_gw,
            current_half=state["current_half"],
        )
        # Merge chip recommendations into GW plan.
        chip_map = {c["gw"]: c["chip"] for c in chip_result.get("chip_plan", [])}
        for g in plan["gw_plan"]:
            g["chip"] = chip_map.get(g["gw"])
        plan["chip"] = chip_result.get("recommendation")
        plan["chip_plan"] = chip_result.get("chip_plan", [])
        plan["chip_reason"] = chip_result.get("reason", "")

        # 7. Fetch last GW live data for the report (public endpoint, no auth needed).
        finished_gws = [e for e in bootstrap["events"] if e.get("finished")]
        last_gw = finished_gws[-1]["id"] if finished_gws else None
        last_gw_data: dict = {}
        if last_gw:
            try:
                live_raw = data_collector.fetch_event_live(last_gw, force=True)
                last_gw_data = {
                    el["id"]: el.get("stats", {})
                    for el in live_raw.get("elements", [])
                }
                log.info("Fetched GW%d live data: %d players", last_gw, len(last_gw_data))
            except Exception as exc:
                log.warning("Could not fetch GW%d live data: %s", last_gw, exc)

        # 8. Write reports.
        report_paths = self._write_reports(
            plan, current_gw, forecasts, bootstrap, last_gw, last_gw_data
        )

        result = {**plan, "report_paths": report_paths, "run_at": _now_iso()}
        log.info("Done. Reports written to %s", REPORTS_DIR)
        return result

    # ------------------------------------------------------------------
    # ML inference
    # ------------------------------------------------------------------

    def _compute_ml_xpts(self, bootstrap: dict) -> dict[int, float]:
        """Run FPLPointsPredictor on current-season element summaries.

        Fetches history_past for every pool player (cached after first run),
        builds EWMA features, runs inference, then blends 40% ML + 60% ep_next.
        Falls back to an empty dict on any failure so the pipeline continues
        with the ep_next + PPG forecaster branch.
        """
        try:
            pool = data_collector.build_player_pool()
            ep_by_id: dict[int, float] = {
                int(row["id"]): float(row.get("ep_next") or 0)
                for _, row in pool.iterrows()
            }
            summaries = data_collector.fetch_element_summaries_bulk(pool["id"].tolist())
            hist_df = data_collector.element_summaries_to_features(summaries)
            if hist_df.empty:
                log.warning("ML inference: element_summaries_to_features returned empty frame")
                return {}

            pool_by_id = pool.set_index("id")
            common_ids = hist_df.index.intersection(pool_by_id.index)
            if len(common_ids) == 0:
                return {}

            pred_df = hist_df.loc[common_ids].copy()
            element_types = pool_by_id.loc[common_ids, "element_type"].astype(int).tolist()
            element_ids = [int(i) for i in common_ids]

            medians = pred_df.reindex(columns=self._model_features).median()
            X_pred = pred_df.reindex(columns=self._model_features).fillna(medians)

            preds = self._predictor.predict(X_pred, element_types)

            ml_xpts: dict[int, float] = {}
            for el_id, xpts_val in zip(element_ids, preds["expected_points"]):
                if pd.notna(xpts_val) and xpts_val > 0:
                    ep_val = ep_by_id.get(el_id, 0.0)
                    blended = (ML_BLEND * float(xpts_val) + (1 - ML_BLEND) * ep_val
                               if ep_val > 0 else float(xpts_val))
                    ml_xpts[el_id] = blended

            log.info("ML inference: %d/%d players predicted (%.0f%% ML + %.0f%% ep_next)",
                     len(ml_xpts), len(pool), ML_BLEND * 100, (1 - ML_BLEND) * 100)
            return ml_xpts

        except Exception as exc:
            log.warning("ML inference failed (%s) — using ep_next + PPG only", exc)
            return {}

    # ------------------------------------------------------------------
    # Report writing
    # ------------------------------------------------------------------

    def _write_reports(
        self,
        plan: dict,
        current_gw: int,
        forecasts: pd.DataFrame,
        bootstrap: dict,
        last_gw: int | None = None,
        last_gw_data: dict | None = None,
    ) -> dict[str, str]:
        md_path = REPORTS_DIR / "season_plan_latest.md"
        csv_path = REPORTS_DIR / "season_plan_latest.csv"

        md = reporter.build_report(plan, current_gw, forecasts, bootstrap, last_gw, last_gw_data)
        md_path.write_text(md, encoding="utf-8")

        rt: pd.DataFrame = plan.get("report_table", pd.DataFrame())
        if not rt.empty:
            rt.to_csv(csv_path, index=False)

        return {"markdown": str(md_path), "csv": str(csv_path)}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
