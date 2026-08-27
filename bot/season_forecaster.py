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
  already incorporates fixture and form. For subsequent GWs a
  reliability-adjusted PPG (raw ``points_per_game`` regressed toward a
  position / fixture-neutral ``ep_next`` prior — see :data:`PRIOR_MATCHES`)
  scaled by fixture difficulty is used. Early in the season the prior
  dominates, so a single big score does not turn a fringe player into a
  captain pick or flood the candidate pool.
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
from collections import defaultdict
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

#: Early-season reliability adjustment. Raw ``points_per_game`` is meaningless
#: after one or two matches — a single 17-point haul makes PPG = 17, which the
#: future-GW branch and the candidate ranking would then treat as a repeatable
#: per-game rate. Both instead use :func:`_stabilized_ppg`, which regresses raw
#: PPG toward a prior with Bayesian-style shrinkage::
#:
#:     stab_ppg = (eff_games * raw_ppg + PRIOR_MATCHES * prior) / (eff_games + PRIOR_MATCHES)
#:
#: ``eff_games`` is the current-season sample size (max of ``starts`` and
#: ``minutes`` / 90). With PRIOR_MATCHES = 10 a single standout GW barely moves
#: the estimate; real form carries the majority of the weight by ~GW10-12 and
#: dominates by late season.
PRIOR_MATCHES = 10.0

#: Per-game points a replacement-level starter in each position averages. Used
#: as the shrinkage prior, blended 50/50 with a fixture-neutralised ``ep_next``
#: when FPL has published one.
POS_BASELINE_PPG = {GKP: 3.0, DEF: 3.0, MID: 3.2, FWD: 3.2}

#: An individual player projecting above this for a single (non-double) fixture
#: almost always means a corrupt input, not a real edge. Logged, never clamped.
SANITY_MAX_SINGLE_XPTS = 12.0

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
        ml_xpts: dict[int, float] | None = None,
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
        ml_xpts:
            Optional mapping of ``element_id → predicted xPts for GW1``.
            When provided, the ML value replaces the rule-based base for the
            immediate GW; for subsequent GWs it is scaled by the FDR/PPG
            branch multipliers (treating it as a per-game base rate).

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
            for _, p in pool.iterrows():
                team_id = int(p["team"])
                fixture_fdr = fdr_map.get((team_id, gw))  # None if blank

                if fixture_fdr is None:
                    xpts = 0.0
                    p_start = 0.0
                    p_60 = 0.0
                else:
                    avg_fdr, n_fixtures = fixture_fdr
                    p_start = float(p["p_start"])
                    fdr_mult = FDR_MULTIPLIER.get(round(avg_fdr), 1.0)

                    el_id = int(p["id"])
                    ml_val = (ml_xpts or {}).get(el_id)

                    if ml_val is not None and gw_offset == 0:
                        # ML prediction is a single-GW value, already fixture-aware.
                        xpts = ml_val * p_start
                    elif ml_val is not None:
                        # Scale ML value as a per-game base rate for future GWs.
                        xpts = ml_val * fdr_mult * p_start * n_fixtures
                    else:
                        # Immediate GW: use FPL's ep_next, which already accounts
                        # for the fixture (including DGW scaling). Further GWs use
                        # the reliability-adjusted PPG × FDR × n_fixtures so a
                        # one-game sample cannot drive the projection.
                        using_ep_next = (
                            gw_offset == 0
                            and p["ep_next"] is not None
                            and float(p["ep_next"]) > 0
                        )
                        if using_ep_next:
                            base = float(p["ep_next"])
                            xpts = base * p_start  # ep_next is already DGW-aware
                        else:
                            base = float(p["stab_ppg"]) * fdr_mult
                            xpts = base * p_start * n_fixtures
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

        # Sanity guard — surfaced, never clamped. A single-fixture projection
        # this high almost always means a corrupt input (typically a raw PPG
        # from a one- or two-game sample leaking into the base rate).
        hot = df[df["xpts"] > SANITY_MAX_SINGLE_XPTS]
        if not hot.empty:
            worst = hot.loc[hot["xpts"].idxmax()]
            log.warning(
                "forecast: %d player-GW rows exceed %.0f xPts (worst %.1f — %s, GW%d); "
                "check for single-sample PPG inflation",
                len(hot), SANITY_MAX_SINGLE_XPTS, worst["xpts"],
                worst["name"], int(worst["gw"]),
            )
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
        players["ep_next"] = players["ep_next"].apply(_safe_float)

        # Reliability-adjusted PPG — the base rate for every future GW. Ranking
        # and forecast() MUST use the same stabilized value, otherwise a GW1
        # one-hit wonder floods the candidate pool even if forecast() is sane.
        imm_mult = {
            int(tid): (
                FDR_MULTIPLIER.get(round(fdr[0]), 1.0)
                if (fdr := fdr_map.get((int(tid), current_gw))) else 1.0
            )
            for tid in players["team"].unique()
        }
        players["stab_ppg"] = players.apply(
            lambda r: _stabilized_ppg(r, imm_mult.get(int(r["team"]), 1.0)), axis=1
        )

        # Horizon xPts estimate for ranking.
        def horizon_xpts(row):
            total = 0.0
            for gw_offset, gw in enumerate(range(current_gw, current_gw + self.horizon)):
                fdr = fdr_map.get((int(row["team"]), gw))
                if fdr is None:
                    continue
                avg_fdr, n_fixtures = fdr
                mult = FDR_MULTIPLIER.get(round(avg_fdr), 1.0)
                base = float(row["stab_ppg"]) * mult
                total += base * float(row["p_start"]) * (self.decay ** gw_offset) * n_fixtures
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
    ) -> dict[tuple[int, int], tuple[float, int]]:
        """Return {(team_id, gw): (avg_fdr, n_fixtures)} for [first_gw, last_gw].

        A team normally has one fixture per gameweek, but in a double gameweek
        it has two; both are accumulated and averaged, and ``n_fixtures`` lets
        callers scale expected points by the number of matches played.
        """
        collected: dict[tuple[int, int], list[int]] = defaultdict(list)
        for fix in fixtures:
            gw = fix.get("event")
            if gw is None or not (first_gw <= gw <= last_gw):
                continue
            home_id = fix.get("team_h")
            away_id = fix.get("team_a")
            home_fdr = fix.get("team_h_difficulty", 3)
            away_fdr = fix.get("team_a_difficulty", 3)
            if home_id is not None:
                collected[(home_id, gw)].append(int(home_fdr))
            if away_id is not None:
                collected[(away_id, gw)].append(int(away_fdr))
        return {
            key: (sum(vals) / len(vals), len(vals))
            for key, vals in collected.items()
        }


def _safe_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _stabilized_ppg(row, immediate_fdr_mult: float = 1.0) -> float:
    """Reliability-adjusted points-per-game for future-GW projection and ranking.

    ``row`` is any mapping exposing ``points_per_game``, ``minutes``, ``starts``,
    ``ep_next`` and ``element_type`` (a plain dict or a DataFrame row both work).
    ``immediate_fdr_mult`` is the FDR multiplier of the player's *next* fixture;
    it is divided out of ``ep_next`` so the prior stays fixture-neutral and the
    immediate opponent does not leak into GW+2..GW+5.

    See :data:`PRIOR_MATCHES` for the shrinkage formula and rationale.
    """
    raw_ppg = _safe_float(row.get("points_per_game"))
    eff_games = max(_safe_float(row.get("starts")), _safe_float(row.get("minutes")) / 90.0)

    pos = int(row.get("element_type") or MID)
    baseline = POS_BASELINE_PPG.get(pos, 3.0)

    ep_next = _safe_float(row.get("ep_next"))
    if ep_next > 0:
        mult = immediate_fdr_mult or 1.0
        prior = 0.5 * (ep_next / mult) + 0.5 * baseline
    else:
        prior = baseline

    adjusted = (eff_games * raw_ppg + PRIOR_MATCHES * prior) / (eff_games + PRIOR_MATCHES)
    return max(MIN_PPG, adjusted)


#: A future GW's XI projection above this multiple of the immediate GW's is
#: implausible for normal single fixtures. Chip GWs are exempt — Bench Boost
#: and Triple Captain legitimately lift the total.
XI_JUMP_RATIO = 1.6


def projection_warnings(gw_plan: list[dict]) -> list[str]:
    """Flag future-GW XI projections that tower over the immediate GW.

    A healthy single-fixture plan stays near the first GW's XI total. A later
    GW running far above it means the forecast fed the planner corrupt base
    rates (the early-season raw-PPG failure mode). Returns human-readable
    strings — advisory only, nothing is modified or clamped.
    """
    if not gw_plan:
        return []
    base = float(gw_plan[0].get("xi_xpts") or 0.0)
    if base <= 0:
        return []
    out: list[str] = []
    for g in gw_plan[1:]:
        xi = float(g.get("xi_xpts") or 0.0)
        if not g.get("chip") and xi > base * XI_JUMP_RATIO:
            out.append(
                f"GW{g['gw']} XI projection {xi:.1f} is {xi / base:.1f}x the "
                f"GW{gw_plan[0]['gw']} total ({base:.1f}) with no chip — "
                f"likely inflated forecast inputs"
            )
    return out
