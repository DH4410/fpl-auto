"""
Day-before-deadline simulation and sanity check.

The MILP in :mod:`season_planner` already optimises transfers, captain and
chip timing over a rolling horizon *including* the 4-point hit cost. This
module does **not** re-optimise. Its job is to be the last gate before a live
write: re-run the planner on fresh data, then apply two independent checks the
optimiser cannot make on its own —

  1. **Hit margin.** The optimiser will take a hit for any positive net gain,
     however small. Because the forecast itself is uncertain, we demand an
     extra ``HIT_MARGIN`` points of headroom on top of the hit cost before a
     paid transfer is allowed through.
  2. **Idea-list veto.** :mod:`post_match_analyzer` knows things the forecast
     table does not (a red card served next GW, an injury announced after the
     forecast was built). Any plan that transfers *in* a player the analyzer
     flagged as unavailable is rejected outright.

Hit-margin arithmetic
---------------------
``gross_gain`` is the horizon xPts the incoming players add over the outgoing
ones, *before* the hit is charged. ``net_gain = gross_gain - HIT_COST * hits``
is what the optimiser maximises. The rule

    approve  ⟺  gross_gain ≥ HIT_COST * hits + HIT_MARGIN
             ⟺  net_gain   ≥ HIT_MARGIN

is applied on the gross figure so the hit is charged exactly once. Comparing
the already-hit-adjusted net gain against ``HIT_COST * hits + HIT_MARGIN``
would charge it twice and reject nearly every hit.

Results are written to ``data/pre_deadline/gw{N}.json``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from .chip_planner import ChipPlanner
from .fpl_rules import CHIP_BENCH_BOOST, CHIP_TRIPLE_CAPTAIN, HIT_COST

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
PRE_DEADLINE_DIR = DATA_DIR / "pre_deadline"

#: Extra expected points demanded on top of the hit cost before approving a hit.
HIT_MARGIN = 2.0
#: idea_list priority at or above which a flagged player is treated as unusable.
VETO_PRIORITY = 0.8


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


class PreDeadlineSimulator:
    """Validate the planner's proposal before it is submitted to FPL."""

    def __init__(self, horizon: int = 6, data_dir: Path | None = None):
        self.horizon = horizon
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.pre_deadline_dir = self.data_dir / "pre_deadline"

    def simulate(
        self,
        idea_list: list[dict],
        forecasts: pd.DataFrame,
        current_state: dict,
        next_gw: int,
        plan: dict | None = None,
        my_team: dict | None = None,
        entry_info: dict | None = None,
    ) -> dict:
        """Produce the final, approved action for ``next_gw``.

        Parameters
        ----------
        idea_list:
            ``idea_list`` from :class:`~bot.post_match_analyzer.PostMatchAnalyzer`.
            Used only as a veto/annotation layer — it never selects players.
        forecasts:
            Forecast table (``element``/``gw``/``xpts``). Used to value the
            proposed transfers over the horizon.
        current_state:
            State dict from :func:`bot.updater.build_current_state`, for
            context in the saved record.
        next_gw:
            The GW being planned (the one whose deadline is approaching).
        plan:
            A plan already produced by :meth:`bot.updater.SeasonUpdater.run`.
            When omitted the updater is run here, which refetches everything.
        my_team, entry_info:
            Passed through to the updater when ``plan`` is not supplied.

        Returns
        -------
        dict
            ``approved_transfers``, ``approved_chip``, ``expected_net_gain``,
            ``hit_count``, ``reasoning`` (plus supporting detail).
        """
        reasons: list[str] = []

        # 1. Baseline plan — the MILP is the source of truth for *what* to do.
        if plan is None:
            from .updater import SeasonUpdater
            log.info("No plan supplied — running SeasonUpdater for GW%d", next_gw)
            plan = SeasonUpdater(horizon=self.horizon).run(
                current_gw=next_gw, my_team=my_team, entry_info=entry_info
            )
            forecasts = forecasts if forecasts is not None else pd.DataFrame()

        transfers_in = list(plan.get("transfers_in") or [])
        transfers_out = list(plan.get("transfers_out") or [])
        hit_count = int(plan.get("hits") or 0)
        chip = plan.get("chip")

        # 2. Value the proposed move over the horizon (gross, pre-hit).
        gross_gain = self._horizon_gain(forecasts, transfers_in, transfers_out, next_gw)
        net_gain = gross_gain - HIT_COST * hit_count

        approved_transfers = self._pair_transfers(transfers_in, transfers_out)
        approved_chip = chip

        if not approved_transfers:
            reasons.append("Planner recommends no transfer this GW (rolling the free transfer).")
        else:
            reasons.append(
                f"Planner proposes {len(approved_transfers)} transfer(s) worth "
                f"{gross_gain:+.2f} xPts gross over {self.horizon} GWs "
                f"({net_gain:+.2f} net of {hit_count} hit(s))."
            )

        # 3. Idea-list veto — never buy a player the analyzer flagged.
        blocked = self._blocked_elements(idea_list)
        vetoed = [t for t in approved_transfers if t["element_in"] in blocked]
        if vetoed:
            names = ", ".join(f"{t['name_in']} ({blocked[t['element_in']]})" for t in vetoed)
            reasons.append(
                f"REJECTED: plan buys player(s) flagged unavailable by post-match "
                f"analysis — {names}. Holding transfers this GW."
            )
            approved_transfers = []
            hit_count = 0
            net_gain = 0.0
            gross_gain = 0.0

        # 4. Hit margin gate.
        if approved_transfers and hit_count > 0:
            required_gross = HIT_COST * hit_count + HIT_MARGIN
            if gross_gain < required_gross:
                reasons.append(
                    f"REJECTED: {hit_count} hit(s) need {required_gross:.1f} gross xPts "
                    f"(hit cost {HIT_COST * hit_count} + {HIT_MARGIN:.0f} margin) but the "
                    f"plan is worth only {gross_gain:.2f}. Holding transfers this GW."
                )
                approved_transfers = []
                hit_count = 0
                net_gain = 0.0
                gross_gain = 0.0
            else:
                reasons.append(
                    f"Hit approved: {gross_gain:.2f} gross >= {required_gross:.1f} required."
                )

        # 5. Chip gate.
        approved_chip, chip_reason = self._check_chip(plan, approved_chip, next_gw)
        if chip_reason:
            reasons.append(chip_reason)

        # 6. Unactioned urgent flags — recorded, not forced.
        squad = set(current_state.get("squad") or [])
        out_elements = {t["element_out"] for t in approved_transfers}
        for idea in idea_list or []:
            if (idea.get("action") == "transfer_out"
                    and float(idea.get("priority", 0)) >= 0.9
                    and idea.get("element") in squad
                    and idea.get("element") not in out_elements):
                reasons.append(
                    f"NOTE: {idea.get('name')} is flagged urgent "
                    f"({idea.get('reason', '')}) but the optimiser kept them — "
                    f"benched or judged still worth holding."
                )

        result = {
            "gw": int(next_gw),
            "approved_transfers": approved_transfers,
            "approved_chip": approved_chip,
            "expected_net_gain": round(net_gain, 2),
            "expected_gross_gain": round(gross_gain, 2),
            "hit_count": hit_count,
            "captain": plan.get("captain"),
            "vice": plan.get("vice"),
            "reasoning": " ".join(reasons),
        }

        _write_json(self.pre_deadline_dir / f"gw{next_gw}.json", result)
        log.info("Pre-deadline simulation for GW%d: %d transfer(s), chip=%s, net %+.2f",
                 next_gw, len(approved_transfers), approved_chip, net_gain)
        return result

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _pair_transfers(transfers_in: list[dict], transfers_out: list[dict]) -> list[dict]:
        """Zip the planner's in/out lists into executable transfer records."""
        paired: list[dict] = []
        for t_in, t_out in zip(transfers_in, transfers_out):
            paired.append({
                "element_in": int(t_in["element"]),
                "name_in": t_in.get("name", ""),
                "purchase_price": int(round(float(t_in.get("cost", 0)) * 10)),
                "element_out": int(t_out["element"]),
                "name_out": t_out.get("name", ""),
                "selling_price": int(round(float(t_out.get("selling_price", 0)) * 10)),
            })
        return paired

    @staticmethod
    def _blocked_elements(idea_list: list[dict] | None) -> dict[int, str]:
        """Elements the analyzer flagged strongly enough to forbid buying."""
        blocked: dict[int, str] = {}
        for idea in idea_list or []:
            if idea.get("action") != "transfer_out":
                continue
            try:
                priority = float(idea.get("priority", 0))
            except (TypeError, ValueError):
                continue
            if priority < VETO_PRIORITY or idea.get("element") is None:
                continue
            blocked[int(idea["element"])] = idea.get("reason", "flagged unavailable")
        return blocked

    def _horizon_gain(self, forecasts: pd.DataFrame, transfers_in: list[dict],
                      transfers_out: list[dict], next_gw: int) -> float:
        """Gross horizon xPts of incoming players minus outgoing ones."""
        if not transfers_in:
            return 0.0
        if (forecasts is None or not isinstance(forecasts, pd.DataFrame)
                or forecasts.empty
                or not {"element", "gw", "xpts"} <= set(forecasts.columns)):
            log.warning("No usable forecast table — cannot value transfers; assuming 0.0 gain")
            return 0.0

        window = forecasts[
            (forecasts["gw"] >= next_gw) & (forecasts["gw"] < next_gw + self.horizon)
        ]
        if window.empty:
            return 0.0
        totals = window.groupby("element")["xpts"].sum()

        gain = 0.0
        for t in transfers_in:
            gain += float(totals.get(int(t["element"]), 0.0))
        for t in transfers_out:
            gain -= float(totals.get(int(t["element"]), 0.0))
        return float(gain)

    @staticmethod
    def _check_chip(plan: dict, chip: str | None, next_gw: int) -> tuple[str | None, str]:
        """Re-apply the exact threshold used by the chip planner.

        Newer plans persist an expiry-aware required_gain per chip/GW. Older
        saved plans fall back to the historical static TC/BB thresholds so
        recovery remains backwards compatible.
        """
        if not chip:
            return None, ""

        entry = next(
            (c for c in (plan.get("chip_plan") or []) if c.get("gw") == next_gw),
            None,
        )
        if entry is None:
            return chip, f"Chip {chip} recommended for GW{next_gw}."

        gain = float(entry.get("expected_gain", 0.0))
        required_raw = entry.get("required_gain")
        if required_raw is None:
            defaults = ChipPlanner()
            thresholds = {
                CHIP_TRIPLE_CAPTAIN: defaults.min_tc_gain,
                CHIP_BENCH_BOOST: defaults.min_bb_gain,
            }
            required = thresholds.get(chip)
        else:
            required = float(required_raw)

        if required is not None and gain < required:
            return None, (
                f"REJECTED chip {chip}: expected gain {gain:.1f} < "
                f"{required:.1f} effective threshold."
            )

        planner_reason = str(plan.get("chip_reason") or "").strip()
        reason = (
            f"Chip {chip} approved for GW{next_gw} "
            f"(est. {gain:+.1f} pts"
            + (f", threshold {required:.1f}" if required is not None else "")
            + ")."
        )
        if planner_reason:
            reason += f" {planner_reason}"
        return chip, reason
