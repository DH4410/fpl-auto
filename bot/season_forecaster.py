"""
Lightweight per-player, per-GW expected-points engine.

Inputs: bootstrap-static JSON and fixtures JSON from data_collector.
Output: DataFrame[element, gw, xpts, p_start, p_60, uncertainty].

This module is intentionally light. It uses FPL's own signals (ep_next, ppg,
status, FDR) rather than heavy ML to stay fast enough for re-projection after
every gameweek. The ML models in models.py are for training; this module is for
live planning cadence.

Design notes
------------
* Base rate uses FPL's ``ep_next`` for the immediate next GW because it
  already incorporates fixture and form. For subsequent GWs, ``points_per_game``
  (PPG) scaled by fixture difficulty is used as a stable proxy.
* ``p_start`` is inferred from the status flag and
  ``chance_of_playing_next_round``. The status flag carries more information
  than the percentage alone (it separates injury from rotation risk).
* FDR multipliers convert the 1-5 difficulty scale to a points adjustment.
  Calibrated so FDR 3 (average fixture) is 1.0x and the range spans ±30%.
* A player with no fixture in a gameweek (blank) receives xPts = 0.
* Uncertainty grows with the horizon: future weeks carry more noise. It is
  stored as a fraction of the mean so callers can construct confidence
  intervals without needing the raw distribution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .fpl_rules import GKP, DEF, MID, FWD, MAX_BANKED_FT

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HORIZON = 6
DEFAULT_MAX_CANDIDATES = 150
DEFAULT_DECAY = 0.85

#: FDR 1 (easiest) → 1.30x, FDR 5 (hardest) → 0.70x, FDR 3 → 1.00x.
FDR_MULTIPLIER = {1: 1.30, 2: 1.15, 3: 1.00, 4: 0.85, 5: 0.70}

#: Probability of starting by status code.
#: a=available, d=doubt, n=unknown (FPL default), i=injured, u=unavailable, s=suspended.
P_START_BY_STATUS = {
    "a": 0.90,   # fully available; 0.90 not 1.0 because rotation always possible
    "d": 0.55,   # doubt
    "n": 0.70,   # no news (treat as mostly available)
    "i": 0.05,   # injured (small chance of rushed return)
    "u": 0.00,   # unavailable
    "s": 0.00,   # suspended
}

#: Scaling for chance_of_playing (overrides status when non-null).
#: Maps 25/50/75/100 (FPL integer percentages) to float P(start).
CHANCE_SCALE = {100: 0.90, 75: 0.70, 50: 0.55, 25: 0.25, 0: 0.00}

#: Minimum meaningful PPG. Prevents very new or rarely-playing players from
#: getting projected as zero when they do have real point potential.
MIN_PPG = 1.0

#: Uncertainty grows per gameweek beyond GW1. Stored as fraction of mean xPts.
UNCERTAINTY_PER_GW = 0.08


# ---------------------------------------------------------------------------
# Forecaster
# ---------------------------------------------------------------------------

@dataclass
class SeasonForecaster:
    """Builds a multi-GW expected-points table for all players.

    Call :meth:`forecast` after loading bootstrap and fixture data.
    The returned DataFrame is the direct input for :mod:`season_planner`.
    """

    horizon: int = DEFAULT_HORIZON
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    decay: float = DEFAULT_DECAY

    def forecast(
        self,
        bootstrap: dict[str, Any],
        fixtures: list[dict],
        current_gw: int,
        owned_ids: list[int] | None = None,
    ) -> pd.DataFrame:
        """Compute xPts for every player over the next ``horizon`` gameweeks.

        Parameters
        ----------
        bootstrap:
            Full ``bootstrap-static`` JSON from the FPL API.
        fixtures:
            Full fixtures list (all 380 matches) from ``/fixtures/``.
        current_gw:
            The gameweek being planned from (i.e. the next GW to be played).
        owned_ids:
            Element IDs already in the manager's squad. These are always
            included in the candidate pool regardless of projected points.

        Returns
        -------
        pd.DataFrame
            Columns: ``element``, ``gw``, ``xpts``, ``p_start``, ``p_60``,
            ``uncertainty``, ``name``, ``position``, ``team``, ``now_cost``,
            ``selling_price``, ``selected_by_percent``.
            Only covers the top :attr:`max_candidates` players plus any
            ``owned_ids`` not in that set.
        """
        players = pd.DataFrame(bootstrap["elements"])
        teams = {t["id"]: t for t in bootstrap["teams"]}

        # Map team id → FDR by gameweek (built from fixtures).
        fdr_map = self._build_fdr_map(fixtures, current_gw, current_gw + self.horizon - 1)

        # Candidate pool: eligible players with a realistic chance of playing.
        pool = self._filter_candidates(players, fdr_map, current_gw, owned_ids or [])

        rows: list[dict] = []
        for gw_offset, gw in enumerate(range(current_gw, current_gw + self.horizon)):
            gw_decay = self.decay ** gw_offset
            for _, p in pool.iterrows():
                team_id = int(p["team"])
                fixture_fdr = fdr_map.get((team_id, gw))  # None if blank

                if fixture_fdr is None:
                    xpts = 0.0
                    p_start = 0.0
                    p_60 = 0.0
                else:
                    p_start = float(p["p_start"])
                    fdr_mult = FDR_MULTIPLIER.get(fixture_fdr, 1.0)

                    # GW1 projection: use FPL's ep_next which already accounts for
                    # the immediate fixture. Further GWs use PPG × FDR.
                    if gw_offset == 0 and p["ep_next"] is not None and float(p["ep_next"]) > 0:
                        base = float(p["ep_next"])
                    else:
                        ppg = max(MIN_PPG, float(p["ppg"]))
                        base = ppg * fdr_mult

                    xpts = base * p_start * gw_decay
                    p_60 = p_start * 0.85  # approximate: most starters play 60+

                uncertainty = min(0.90, UNCERTAINTY_PER_GW * (gw_offset + 1))
                rows.append({
                    "element": int(p["id"]),
                    "gw": gw,
                    "xpts": round(xpts, 3),
                    "p_start": round(p_start, 3),
                    "p_60": round(p_60, 3),
                    "uncertainty": round(uncertainty, 3),
                    "name": p.get("web_name", p.get("known_name", str(p["id"]))),
                    "position": int(p["element_type"]),
                    "team": team_id,
                    "now_cost": int(p["now_cost"]),
                    "selected_by_percent": _safe_float(p.get("selected_by_percent", 0)),
                })

        df = pd.DataFrame(rows)
        if df.empty:
            log.warning("forecast produced an empty table; check data sources")
        return df

    # ------------------------------------------------------------------
    # Candidate filtering
    # ------------------------------------------------------------------

    def _filter_candidates(
        self,
        players: pd.DataFrame,
        fdr_map: dict,
        current_gw: int,
        owned_ids: list[int],
    ) -> pd.DataFrame:
        """Return the top :attr:`max_candidates` players by projected horizon value.

        Unavailable, non-selectable and zero-minute players are excluded.
        Current squad members are always retained.
        """
        players = players.copy()

        # Hard exclusions: cannot select or has been removed.
        players = players[players["can_select"].fillna(True).astype(bool)]
        players = players[~players["status"].isin(["u"])]

        # Soft exclusion: 0% chance of playing in any upcoming fixture.
        p_start_arr = players.apply(self._player_p_start, axis=1)
        players["p_start"] = p_start_arr
        players["ppg"] = players["points_per_game"].apply(
            lambda x: max(MIN_PPG, _safe_float(x))
        )
        players["ep_next"] = players["ep_next"].apply(_safe_float)

        # Horizon xPts estimate for ranking.
        def horizon_xpts(row):
            total = 0.0
            for gw_offset, gw in enumerate(range(current_gw, current_gw + self.horizon)):
                fdr = fdr_map.get((int(row["team"]), gw))
                if fdr is None:
                    continue
                mult = FDR_MULTIPLIER.get(fdr, 1.0)
                ppg = max(MIN_PPG, float(row["ppg"]))
                base = ppg * mult
                total += base * float(row["p_start"]) * (self.decay ** gw_offset)
            return total

        players["horizon_xpts"] = players.apply(horizon_xpts, axis=1)

        # Always keep owned players; rank the rest.
        owned = players[players["id"].isin(owned_ids)]
        rest = players[~players["id"].isin(owned_ids)]
        rest_sorted = rest.sort_values("horizon_xpts", ascending=False)
        slots = max(0, self.max_candidates - len(owned))
        candidates = pd.concat(
            [owned, rest_sorted.head(slots)], ignore_index=True
        )
        log.debug(
            "candidate pool: %d players (%d owned, %d from open pool)",
            len(candidates), len(owned), slots,
        )
        return candidates.reset_index(drop=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _player_p_start(row) -> float:
        """Infer P(start) for one player row."""
        chance = row.get("chance_of_playing_next_round")
        if chance is not None and not pd.isna(chance):
            c = int(chance)
            return CHANCE_SCALE.get(c, c / 100.0 * 0.90)
        status = str(row.get("status", "a")).lower()
        return P_START_BY_STATUS.get(status, 0.70)

    @staticmethod
    def _build_fdr_map(
        fixtures: list[dict], first_gw: int, last_gw: int
    ) -> dict[tuple[int, int], int]:
        """Return {(team_id, gw): fdr} for fixtures in [first_gw, last_gw].

        Each team appears once as home and once as away; FDR is from the
        *difficulty* perspective of that team (difficulty of facing their
        opponent).
        """
        fdr: dict[tuple[int, int], int] = {}
        for fix in fixtures:
            gw = fix.get("event")
            if gw is None or not (first_gw <= gw <= last_gw):
                continue
            home_id = fix.get("team_h")
            away_id = fix.get("team_a")
            home_fdr = fix.get("team_h_difficulty", 3)
            away_fdr = fix.get("team_a_difficulty", 3)
            if home_id is not None:
                fdr[(home_id, gw)] = int(home_fdr)
            if away_id is not None:
                fdr[(away_id, gw)] = int(away_fdr)
        return fdr


def _safe_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
