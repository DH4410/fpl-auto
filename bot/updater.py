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
from .season_forecaster import (
    SANITY_MAX_SINGLE_XPTS,
    SeasonForecaster,
    projection_warnings,
)
from .season_planner import SeasonPlanner
from .chip_planner import ChipPlanner
from .fpl_rules import chip_half, CHIP_WILDCARD, CHIP_FREE_HIT, CHIP_TRIPLE_CAPTAIN, CHIP_BENCH_BOOST, CHIP_LABELS

log = logging.getLogger(__name__)

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

MODELS_DIR = Path(__file__).resolve().parent / "models"

#: ML blend weight (fraction from ML model; 1 - ML_BLEND from ep_next).
# Three-season leave-one-out CV on played rows (minutes>0):
#   2023-24 holdout: best_w=0.40 (rho=+0.316 > +0.312 at 0.60)
#   2024-25 holdout: best_w=0.40 (rho=+0.324 > +0.319 at 0.60)
#   2025-26 holdout: best_w=0.60 (rho=+0.307 > +0.302 at 0.40)  ← overfit
# Mean played rho: 0.40→0.3139, 0.60→0.3126. w=0.40 generalises better.
# Pool ρ (all rows) consistently improves with higher w, but the 70 % of rows
# with minutes=0 dominate pool ρ; played rows are the relevant decision set.
ML_BLEND = 0.40

# Conservative anchors when FPL has not published ep_next. Kept at module
# scope so the ML blend can be tested independently from network/model loading.
EP_ZERO_PRIOR: dict[int, float] = {1: 2.5, 2: 2.0, 3: 2.0, 4: 2.0}


def _blend_ml_xpts(raw_prediction: float, ep_next: float, position: int) -> float:
    """Blend one ML estimate without letting an outlier dominate live plans."""
    raw = min(max(0.0, float(raw_prediction)), SANITY_MAX_SINGLE_XPTS)
    ep = max(0.0, float(ep_next))
    anchor = ep if ep > 0 else EP_ZERO_PRIOR.get(int(position), 2.0)
    return ML_BLEND * raw + (1.0 - ML_BLEND) * anchor


CURRENT_FORM_ALPHA = 0.25

# Current-season FPL element-summary fields that live on the same per-GW scale
# as the lagged training EWMA columns. history_past gives a stable preseason
# prior; completed current-season GWs update that prior before inference.
_CURRENT_HISTORY_TO_EWMA: dict[str, str] = {
    "minutes": "ewma_minutes",
    "goals_scored": "ewma_goals_scored",
    "assists": "ewma_assists",
    "bonus": "ewma_bonus",
    "bps": "ewma_bps",
    "clean_sheets": "ewma_clean_sheets",
    "expected_goals": "ewma_expected_goals",
    "expected_assists": "ewma_expected_assists",
    "expected_goal_involvements": "ewma_expected_goal_involvements",
    "expected_goals_conceded": "ewma_expected_goals_conceded",
    "saves": "ewma_saves",
    "goals_conceded": "ewma_goals_conceded",
    "total_points": "ewma_total_points",
    "influence": "ewma_influence",
    "creativity": "ewma_creativity",
    "threat": "ewma_threat",
    "ict_index": "ewma_ict_index",
    "clearances_blocks_interceptions": "ewma_clearances_blocks_interceptions",
    "recoveries": "ewma_recoveries",
    "tackles": "ewma_tackles",
    "defensive_contribution": "ewma_defensive_contribution",
    "yellow_cards": "ewma_yellow_cards",
    "red_cards": "ewma_red_cards",
}

_ROLLING_CURRENT_STATS = (
    "minutes",
    "total_points",
    "expected_goals",
    "expected_assists",
)


def _history_float(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _overlay_current_season_features(
    pred_df: pd.DataFrame,
    summaries: dict,
    current_gw: int,
    *,
    alpha: float = CURRENT_FORM_ALPHA,
) -> pd.DataFrame:
    """Fold completed current-season GWs into inference-time form features.

    element_summaries_to_features builds a stable prior from history_past.
    Before this helper, GW3 inference still used those preseason/past-season
    values for EWMA and rolling features, so the model could not react to
    GW1/GW2 form except through FPL's ep_next anchor.

    Training features are lagged by one GW. Therefore only rows with
    round < current_gw are consumed here, preserving the no-leakage contract.
    """
    out = pred_df.copy()
    if out.empty:
        return out

    for element in out.index:
        summary = summaries.get(int(element), {}) or {}
        history = []
        for row in summary.get("history") or []:
            try:
                round_id = int(row.get("round") or 0)
            except (TypeError, ValueError):
                continue
            if 0 < round_id < int(current_gw):
                history.append(row)
        history.sort(key=lambda row: int(row.get("round") or 0))

        out.at[element, "games_played"] = float(len(history))
        if not history:
            continue

        historical_minutes = 0.0
        if "ewma_minutes" in out.columns:
            try:
                historical_minutes = float(out.at[element, "ewma_minutes"])
                if pd.isna(historical_minutes):
                    historical_minutes = 0.0
            except (TypeError, ValueError):
                historical_minutes = 0.0

        for raw_col, ewma_col in _CURRENT_HISTORY_TO_EWMA.items():
            if ewma_col not in out.columns:
                continue
            try:
                value = float(out.at[element, ewma_col])
                if pd.isna(value):
                    value = 0.0
            except (TypeError, ValueError):
                value = 0.0
            for row in history:
                value = (1.0 - alpha) * value + alpha * _history_float(row, raw_col)
            out.at[element, ewma_col] = value

        prior_rate = min(1.0, max(0.0, historical_minutes / 90.0))
        flag_observations = {
            "ewma_start_rate": lambda row: (
                1.0 if _history_float(row, "starts") > 0
                else (1.0 if _history_float(row, "minutes") >= 45 else 0.0)
            ),
            "ewma_p60_rate": lambda row: (
                1.0 if _history_float(row, "minutes") >= 60 else 0.0
            ),
            "ewma_played_any": lambda row: (
                1.0 if _history_float(row, "minutes") > 0 else 0.0
            ),
        }
        for col, observe in flag_observations.items():
            if col in out.columns and pd.notna(out.at[element, col]):
                value = float(out.at[element, col])
            else:
                value = prior_rate
            for row in history:
                value = (1.0 - alpha) * value + alpha * observe(row)
            out.at[element, col] = value

        for window in (1, 3, 6, 10):
            recent = history[-window:]
            for stat in _ROLLING_CURRENT_STATS:
                col = f"roll{window}_{stat}"
                values = [_history_float(row, stat) for row in recent]
                out.at[element, col] = sum(values) / len(values)

    return out


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

    # Authenticated /my-team is the live transfer-market authority. The
    # public entry's last_deadline_bank is only a historical snapshot and may
    # be stale after a transfer made during the current GW.
    transfers = my_team.get("transfers") or {}
    live_bank = transfers.get("bank")
    if live_bank not in (None, ""):
        bank_tenths = int(live_bank)
    elif entry_info:
        bank_tenths = int(entry_info.get("last_deadline_bank") or 0)
    else:
        bank_tenths = 0

    ft = int(transfers.get("limit") or 1)

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


def unavailable_for_rebuild(bootstrap: dict) -> set[int]:
    """Players a permanent unlimited-transfer rebuild must not retain/buy."""
    blocked: set[int] = set()
    for player in bootstrap.get("elements", []):
        try:
            element = int(player["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            str(player.get("status") or "a") == "u"
            or player.get("can_select") is False
            or player.get("can_transact") is False
            or bool(player.get("removed"))
        ):
            blocked.add(element)
    return blocked


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
        self._last_bootstrap: dict = {}
        self._last_fixtures: list[dict] = []
        # Canonical, fully-adjusted forecast table used by the planner.  The
        # pre-deadline simulator must reuse this exact frame instead of
        # rebuilding a subtly different valuation snapshot.
        self._last_forecasts: pd.DataFrame = pd.DataFrame()
        # Autonomous writes fail closed when the production model did not load
        # and run successfully.  A fallback forecast is still useful for
        # reports, but it is not allowed to approve transfers/chips.
        self._ml_health: dict[str, Any] = {
            "loaded": False,
            "inference_ok": False,
            "error": None,
        }
        if verbose:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

        # Try to load ML predictor (fails gracefully if models not yet trained).
        self._predictor = None
        self._model_features: list[str] = []
        self._mkt_medians: dict[str, float] = {}
        try:
            from .models import FPLPointsPredictor
            sidecar = MODELS_DIR / "minutes.pkl.json"
            self._model_features = json.loads(sidecar.read_text())["features"]
            self._predictor = FPLPointsPredictor.load(MODELS_DIR)
            self._ml_health["loaded"] = True
            log.info("ML predictor loaded from %s (%d features)", MODELS_DIR, len(self._model_features))
        except Exception as exc:
            self._ml_health["error"] = f"model load failed: {exc}"
            log.exception("ML predictor unavailable — fallback forecasts are advisory only")
        try:
            mkt_path = MODELS_DIR / "mkt_medians.json"
            if mkt_path.exists():
                self._mkt_medians = json.loads(mkt_path.read_text())
                log.info("Loaded training medians for %d mkt_ odds features",
                         len(self._mkt_medians))
        except Exception as exc:
            log.warning("Could not load mkt_medians.json: %s", exc)

    def run(
        self,
        current_gw: int,
        my_team: dict | None = None,
        entry_info: dict | None = None,
        forced_ids: list[int] | None = None,
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
        forced_ids:
            Optional transfer/research watchlist element IDs that must enter
            the candidate universe for evaluation. The optimizer remains free
            to reject them.

        Returns
        -------
        dict
            Full plan + chip recommendations + paths to written reports.
        """
        log.info("=== FPL Season Updater — GW%d ===", current_gw)

        # 1. Fetch data.
        log.info("Fetching bootstrap and fixtures…")
        bootstrap = data_collector.fetch_bootstrap(force=True)
        fixtures = data_collector.fetch_fixtures(force=True)
        # Expose the exact authoritative snapshot used by this plan so the
        # orchestrator can freeze news/squad guards and rebuild valuation from
        # identical inputs rather than a second near-simultaneous fetch.
        self._last_bootstrap = bootstrap
        self._last_fixtures = fixtures

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
            ml_xpts = self._compute_ml_xpts(bootstrap, current_gw)
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
            forced_ids=forced_ids or [],
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
            pool_df = data_collector.build_player_pool(bootstrap=bootstrap)
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
        # Only discount the explicit boost when the *actual persisted model
        # feature list* contains set-piece rank fields.  The current 60-feature
        # models do not, so reducing this boost merely because ML is on would
        # under-credit genuine set-piece takers.
        try:
            from .prediction_adjustments import apply_setpiece_boosts
            bootstrap_df = pd.DataFrame(bootstrap["elements"])
            setpiece_features = {
                "penalties_order", "penalties_order_rank",
                "corners_and_indirect_freekicks_order",
                "corners_and_indirect_freekicks_order_rank",
                "direct_freekicks_order", "direct_freekicks_order_rank",
            }
            model_has_setpieces = bool(set(self._model_features) & setpiece_features)
            sp_scale = (
                (1.0 - ML_BLEND)
                if ml_xpts is not None and model_has_setpieces
                else 1.0
            )
            forecasts = apply_setpiece_boosts(forecasts, bootstrap_df, scale=sp_scale)
        except Exception as exc:
            log.warning("Set-piece boost skipped (%s)", exc)

        # This is the single source of truth for downstream transfer valuation,
        # report snapshots and the final safety simulator.
        self._last_forecasts = forecasts.copy()

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
        # Free Hit does not yet have a legal temporary-squad builder (budget,
        # positions and club limits).  Exclude it from live scheduling rather
        # than letting its intentionally-loose upper bound displace a real chip.
        live_chip_pool = [
            chip for chip in state["chips_available"]
            if chip != CHIP_FREE_HIT
        ]
        chip_result = self.chip_planner.evaluate(
            forecasts=forecasts,
            gw_plan=plan["gw_plan"],
            chips_available=live_chip_pool,
            current_gw=current_gw,
            current_half=state["current_half"],
        )

        # A Wildcard recommendation must produce a genuine second-pass rebuild.
        # The ordinary plan above prices every transfer beyond the FT allowance
        # as a hit, so merely attaching the chip to it creates inconsistent FT,
        # hit and squad semantics. Re-solve with unlimited current-GW transfers,
        # preserve banked FTs, and force permanently unavailable players out.
        if chip_result.get("recommendation") == CHIP_WILDCARD:
            ordinary_plan = plan
            blocked = unavailable_for_rebuild(bootstrap)
            log.info(
                "Wildcard recommended — solving a dedicated rebuild (%d unavailable excluded).",
                len(blocked),
            )
            validation_errors: list[str] = []
            wildcard_plan: dict = {}
            target_ids: set[int] = set()
            try:
                wildcard_plan = self.planner.plan(
                    forecasts=forecasts,
                    current_state=state,
                    unlimited_transfers_current_gw=True,
                    forbidden_current_ids=blocked,
                )
                target_ids = {
                    int(player["element"])
                    for player in (wildcard_plan.get("gw_plan") or [{}])[0].get("squad", [])
                }
                retained_blocked = sorted(target_ids & blocked)
                if int(wildcard_plan.get("hits") or 0) != 0:
                    validation_errors.append("dedicated Wildcard rebuild contains hits")
                if retained_blocked:
                    validation_errors.append(
                        "unavailable player(s) retained: "
                        + ", ".join(str(element) for element in retained_blocked)
                    )
            except Exception as exc:  # noqa: BLE001
                log.exception("Dedicated Wildcard rebuild failed")
                validation_errors.append(f"dedicated Wildcard rebuild failed: {exc}")
            if len(target_ids) != 15:
                validation_errors.append(
                    f"target squad has {len(target_ids)} players instead of 15"
                )

            if wildcard_plan:
                # Validate the Wildcard against the best ordinary plan, not
                # against a heuristic based on how many hits that ordinary
                # optimiser happened to request.  This prevents an over-
                # aggressive hit plan from manufacturing its own WC signal.
                wc_entry = next(
                    (
                        row for row in chip_result.get("chip_plan", [])
                        if row.get("chip") == CHIP_WILDCARD
                        and int(row.get("gw") or 0) == int(current_gw)
                    ),
                    {},
                )
                wc_required = float(
                    wc_entry.get("required_gain", self.chip_planner.min_wc_gain)
                )
                wc_counterfactual_gain = (
                    _discounted_plan_value(wildcard_plan)
                    - _discounted_plan_value(ordinary_plan)
                )
                if wc_counterfactual_gain < wc_required:
                    validation_errors.append(
                        f"Wildcard counterfactual gain {wc_counterfactual_gain:.2f} "
                        f"is below required {wc_required:.2f}"
                    )
                wildcard_plan["wildcard_counterfactual_gain"] = round(
                    wc_counterfactual_gain, 2
                )
                wildcard_plan["wildcard_required_gain"] = round(wc_required, 2)
                wildcard_plan["wildcard_validation_errors"] = validation_errors
                wildcard_plan["wildcard_validated"] = not validation_errors
            if validation_errors:
                # Emergency fail-closed fallback: no Wildcard and no paid
                # transfers. This is deliberately re-optimised rather than
                # clearing only the chip from a Wildcard-sized batch.
                log.error(
                    "Wildcard rebuild rejected (%s) — regenerating a no-hit plan.",
                    "; ".join(validation_errors),
                )
                plan = self.planner.plan(
                    forecasts=forecasts,
                    current_state=state,
                    max_current_gw_hits=0,
                )
                plan["transfer_plan_kind"] = "ordinary_no_hit_fallback"
                chip_result["recommendation"] = None
                chip_result["reason"] = (
                    "Wildcard rebuild failed validation; using a freshly "
                    "optimised no-hit/free-transfer plan."
                )
                chip_result["chip_plan"] = [
                    row for row in chip_result.get("chip_plan", [])
                    if not (
                        row.get("chip") == CHIP_WILDCARD
                        and int(row.get("gw") or 0) == int(current_gw)
                    )
                ]
            else:
                plan = wildcard_plan
                # Recompute TC/BB timing from the rebuilt Wildcard squad.  The
                # old schedule was evaluated on the pre-WC trajectory and could
                # recommend a Bench Boost using players no longer in the squad.
                remaining_chips = [
                    chip for chip in live_chip_pool
                    if chip != CHIP_WILDCARD
                ]
                future_chip_result = self.chip_planner.evaluate(
                    forecasts=forecasts,
                    gw_plan=plan["gw_plan"],
                    chips_available=remaining_chips,
                    current_gw=current_gw,
                    current_half=state["current_half"],
                )
                future_rows = [
                    row for row in future_chip_result.get("chip_plan", [])
                    if int(row.get("gw") or 0) > int(current_gw)
                ]
                wc_gain = float(plan.get("wildcard_counterfactual_gain") or 0.0)
                wc_required = float(plan.get("wildcard_required_gain") or 0.0)
                chip_result["chip_plan"] = [{
                    "chip": CHIP_WILDCARD,
                    "gw": int(current_gw),
                    "chip_label": CHIP_LABELS.get(CHIP_WILDCARD, CHIP_WILDCARD),
                    "expected_gain": round(wc_gain, 2),
                    "required_gain": round(wc_required, 2),
                    "base_required_gain": round(self.chip_planner.min_wc_gain, 2),
                }, *future_rows]
                chip_result["recommendation"] = CHIP_WILDCARD
                chip_result["reason"] = (
                    f"Play Wildcard this GW: dedicated legal rebuild beats the "
                    f"best ordinary plan by {wc_gain:+.2f} discounted xPts "
                    f"(needs {wc_required:.2f}). Future chips were recalculated "
                    f"from the rebuilt squad."
                )

        # Merge chip recommendations before projection checks so the sanity
        # guard can correctly exempt genuine TC/BB chip weeks.
        chip_map = {c["gw"]: c["chip"] for c in chip_result.get("chip_plan", [])}
        for g in plan["gw_plan"]:
            g["chip"] = chip_map.get(g["gw"])

        # Projection sanity guard — surfaced to the orchestrator. A healthy
        # single-fixture plan stays near the immediate GW's XI total; a later
        # non-chip GW towering above it indicates corrupt forecast inputs.
        plan["projection_warnings"] = projection_warnings(plan.get("gw_plan", []))
        for w in plan["projection_warnings"]:
            log.warning("projection sanity: %s", w)
        plan["chip"] = chip_result.get("recommendation")
        plan["chip_plan"] = chip_result.get("chip_plan", [])
        plan["chip_reason"] = chip_result.get("reason", "")
        plan["model_health"] = dict(self._ml_health)
        # Rebuild the Chip column in the report_table so it reflects the
        # chip_planner output (report_table is built in season_planner.plan()
        # before chips are merged, so it would otherwise always show "—").
        rt = plan.get("report_table")
        if rt is not None and not rt.empty and "GW" in rt.columns:
            plan["report_table"] = rt.assign(
                Chip=rt["GW"].map(
                    lambda gw: CHIP_LABELS.get(chip_map.get(gw, ""), "") or "—"
                )
            )

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

    def _compute_fixture_mkt_features(
        self,
        current_gw: int,
        pool_by_id: pd.DataFrame,
        element_ids: list[int],
    ) -> tuple[dict[int, dict[str, float]], dict[int, float]]:
        """Compute Dixon-Coles implied mkt_* features for the upcoming GW fixtures.

        Fits DC team strength on the most recent completed season (football-data
        cache) and converts to perspective-adjusted fixture probabilities. Much
        more informative than uniform training medians since Arsenal-at-home and
        Ipswich-away will get very different values.

        Returns:
            mkt_dict: element_id → {mkt_col: value}  (empty on failure)
            is_home_dict: element_id → 0.0 or 1.0     (empty on failure)
        """
        try:
            import numpy as np
            from scipy.stats import poisson as _poisson
            from .feature_engineering import team_strength_matrix
            from .data_collector import (
                fetch_football_data_odds, fetch_fixtures,
                match_team_names, bootstrap_frames,
            )

            raw = fetch_football_data_odds(season="2025-26")
            valid = raw.dropna(subset=["home_goals", "away_goals"]) if not raw.empty else raw
            if len(valid) < 50:
                return {}, {}

            # team_strength_matrix expects lowercase home_team/away_team.
            dc_input = valid.rename(columns={"HomeTeam": "home_team", "AwayTeam": "away_team"})
            strength = team_strength_matrix(dc_input, xi=0.0018)
            home_adv = float(strength.attrs.get("home_advantage", 0.25))

            frames = bootstrap_frames()
            teams_df = frames["teams"]
            fd_names = list(set(valid["HomeTeam"].dropna().tolist()))
            fd_to_fpl = match_team_names(fd_names, teams_df)
            fpl_to_fd = {v: k for k, v in fd_to_fpl.items() if v is not None}

            gw_fixtures = [fx for fx in fetch_fixtures()
                           if fx.get("event") == current_gw]

            team_mkt: dict[int, dict] = {}
            team_is_home: dict[int, float] = {}
            _goals = np.arange(20)

            for fx in gw_fixtures:
                h_id = fx.get("team_h")
                a_id = fx.get("team_a")
                h_fd = fpl_to_fd.get(h_id)
                a_fd = fpl_to_fd.get(a_id)
                if h_fd not in strength.index or a_fd not in strength.index:
                    continue

                lam_h = max(float(np.exp(
                    strength.loc[h_fd, "attack"] + home_adv
                    - strength.loc[a_fd, "defence"])), 0.1)
                lam_a = max(float(np.exp(
                    strength.loc[a_fd, "attack"]
                    - strength.loc[h_fd, "defence"])), 0.1)

                p_h = _poisson.pmf(_goals, lam_h)
                p_a = _poisson.pmf(_goals, lam_a)
                grid = np.outer(p_h, p_a)
                p_hw = float(np.tril(grid, -1).sum())
                p_dr = float(np.trace(grid))
                p_aw = float(np.triu(grid, 1).sum())
                H, A = np.meshgrid(_goals, _goals, indexing="ij")
                p_o25 = float(grid[H + A > 2].sum())

                team_mkt[h_id] = dict(
                    mkt_p_win=p_hw, mkt_p_draw=p_dr, mkt_p_lose=p_aw,
                    mkt_lambda_for=lam_h, mkt_lambda_against=lam_a,
                    mkt_clean_sheet=float(p_a[0]), mkt_p_over25=p_o25,
                )
                team_mkt[a_id] = dict(
                    mkt_p_win=p_aw, mkt_p_draw=p_dr, mkt_p_lose=p_hw,
                    mkt_lambda_for=lam_a, mkt_lambda_against=lam_h,
                    mkt_clean_sheet=float(p_h[0]), mkt_p_over25=p_o25,
                )
                team_is_home[h_id] = 1.0
                team_is_home[a_id] = 0.0

            mkt_out: dict[int, dict[str, float]] = {}
            home_out: dict[int, float] = {}
            for el_id in element_ids:
                if el_id not in pool_by_id.index:
                    continue
                fpl_team = int(pool_by_id.loc[el_id, "team"])
                if fpl_team in team_mkt:
                    mkt_out[el_id] = team_mkt[fpl_team]
                if fpl_team in team_is_home:
                    home_out[el_id] = team_is_home[fpl_team]

            log.info("Fixture mkt_ features: %d/%d players assigned DC-implied odds (GW%d)",
                     len(mkt_out), len(element_ids), current_gw)
            return mkt_out, home_out

        except Exception as exc:
            log.warning("Fixture mkt_ features failed (%s) — using training medians", exc)
            return {}, {}

    def _compute_ml_xpts(self, bootstrap: dict, current_gw: int) -> dict[int, float]:
        """Run FPLPointsPredictor on current-season element summaries.

        Fetches history_past for every pool player (cached after first run),
        builds EWMA features, runs inference, then blends 40% ML + 60% ep_next.
        Falls back to an empty dict on any failure so the pipeline continues
        with the ep_next + PPG forecaster branch.
        """
        try:
            pool = data_collector.build_player_pool(bootstrap=bootstrap)
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

            # ---------------------------------------------------------------
            # Enrich pred_df with contextual features that
            # element_summaries_to_features() cannot derive from history_past
            # alone. Without these the model sees every player as a cheap,
            # position-unknown bench player and predicts near-zero minutes.
            # ---------------------------------------------------------------
            et_series = pool_by_id.loc[common_ids, "element_type"].astype(int)
            pred_df["now_cost"] = pool_by_id.loc[common_ids, "now_cost"].astype(float)
            pred_df["pos_gkp"] = (et_series == 1).astype(float)
            pred_df["pos_def"] = (et_series == 2).astype(float)
            pred_df["pos_mid"] = (et_series == 3).astype(float)
            pred_df["pos_fwd"] = (et_series == 4).astype(float)
            # Compute fixture-specific DC-implied odds first (used for both
            # mkt_* features and is_home correction below).
            fixture_mkt, fixture_is_home = self._compute_fixture_mkt_features(
                current_gw, pool_by_id, element_ids)
            # is_home: 0.5 neutral if fixture data unavailable.
            pred_df["is_home"] = 0.5
            if fixture_is_home:
                for _el, _h in fixture_is_home.items():
                    if _el in pred_df.index:
                        pred_df.loc[_el, "is_home"] = _h
            # start_rate and p60_rate are available from history_past-derived
            # ewma_minutes (starters average ~80 min → start_rate ≈ 0.9).
            # Use ewma_minutes / 90 as a single proxy for both start/p60 rates.
            _min_share = (pred_df["ewma_minutes"] / 90.0).clip(0.0, 1.0)
            pred_df["ewma_start_rate"] = _min_share
            pred_df["ewma_p60_rate"] = _min_share
            pred_df["ewma_played_any"] = _min_share
            pred_df["games_played"] = 0.0
            pred_df = _overlay_current_season_features(
                pred_df,
                summaries,
                current_gw,
            )
            # At GW1 there is no current-season history, so rolling windows
            # still need a sensible historical prior. At GW2+ the overlay above
            # has already populated them from actual completed current GWs.
            for prefix in ("roll1", "roll3", "roll6", "roll10"):
                for stat, ewma_col in (
                    ("minutes", "ewma_minutes"),
                    ("total_points", "ewma_total_points"),
                    ("expected_goals", "ewma_expected_goals"),
                    ("expected_assists", "ewma_expected_assists"),
                ):
                    col = f"{prefix}_{stat}"
                    if col in self._model_features and col not in pred_df.columns:
                        pred_df[col] = pred_df.get(ewma_col, 0.0)
            # h2h features: no head-to-head history yet at GW1; use the
            # player's own career averages from the EWMA frame as a prior.
            for h2h_col, ewma_fallback in (
                ("h2h_appearances_vs_opp", "games_played"),
                ("h2h_goals_scored_vs_opp", "ewma_goals_scored"),
                ("h2h_assists_vs_opp", "ewma_assists"),
                ("h2h_total_points_vs_opp", "ewma_total_points"),
            ):
                if h2h_col in self._model_features and h2h_col not in pred_df.columns:
                    pred_df[h2h_col] = pred_df.get(ewma_fallback, 0.0)

            # Fill mkt_* columns: training medians as neutral baseline, then
            # override with fixture-specific DC-implied probabilities where
            # available. Players whose team is not in the DC model (newly
            # promoted, unmapped) keep the median.
            for _mkt_col, _mkt_val in self._mkt_medians.items():
                if _mkt_col not in pred_df.columns:
                    pred_df[_mkt_col] = _mkt_val
            if fixture_mkt:
                for _el, _mkt_vals in fixture_mkt.items():
                    if _el in pred_df.index:
                        for _col, _val in _mkt_vals.items():
                            if _col in pred_df.columns:
                                pred_df.loc[_el, _col] = _val

            medians = pred_df.reindex(columns=self._model_features).median()
            X_pred = pred_df.reindex(columns=self._model_features).fillna(medians)

            preds = self._predictor.predict(X_pred, element_types)

            # Conservative position-level priors used when FPL hasn't set
            # ep_next (new season, new signings, injury unknowns).  Without an
            # anchor the blended value would be 100% ML, which over-rates fringe
            # players whose per-90 history is limited. The prior is set below the
            # average starter so genuinely good players still rank above it via the
            # ML channel, while unknowns don't crowd out ep_next players.
            et_map: dict[int, int] = dict(zip(element_ids, element_types))
            ml_xpts: dict[int, float] = {}
            capped_predictions = 0
            for el_id, xpts_val in zip(element_ids, preds["expected_points"]):
                if pd.notna(xpts_val) and xpts_val > 0:
                    ep_val = ep_by_id.get(el_id, 0.0)
                    if float(xpts_val) > SANITY_MAX_SINGLE_XPTS:
                        capped_predictions += 1
                    ml_xpts[el_id] = _blend_ml_xpts(
                        float(xpts_val), ep_val, et_map.get(el_id, 3)
                    )

            if capped_predictions:
                log.warning(
                    "ML inference: capped %d single-GW prediction(s) at %.1f xPts "
                    "before blending",
                    capped_predictions,
                    SANITY_MAX_SINGLE_XPTS,
                )

            self._ml_health["inference_ok"] = True
            self._ml_health["error"] = None
            log.info("ML inference: %d/%d players predicted (%.0f%% ML + %.0f%% ep_next)",
                     len(ml_xpts), len(pool), ML_BLEND * 100, (1 - ML_BLEND) * 100)
            return ml_xpts

        except Exception as exc:
            self._ml_health["inference_ok"] = False
            self._ml_health["error"] = f"inference failed: {exc}"
            log.exception(
                "ML inference failed — fallback forecasts are advisory only; "
                "autonomous transfers/chips must fail closed"
            )
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


def _discounted_plan_value(plan: dict, decay: float = 0.85) -> float:
    """Comparable six-GW value for ordinary vs Wildcard counterfactuals.

    Uses the same captain-inclusive XI value exposed by SeasonPlanner and
    subtracts paid hit cost exactly once.  This is deliberately a comparison
    metric, not a claim about realised FPL points.
    """
    total = 0.0
    for offset, gw in enumerate(plan.get("gw_plan") or []):
        total += (
            float(gw.get("xi_xpts") or 0.0)
            - float(gw.get("hit_cost") or 0.0)
        ) * (float(decay) ** offset)
    return float(total)
