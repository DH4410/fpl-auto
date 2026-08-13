"""
Post-forecast adjustments for the lightweight expected-points table.

Everything here operates on the DataFrame returned by
:meth:`season_forecaster.SeasonForecaster.forecast` — double/blank gameweek
scaling, set-piece duty boosts, doubtful-availability discounts, a
returning-from-injury filter, a free-transfer sidegrade check and bench
ordering. Each function is pure and advisory: inputs are copied, nothing is
written anywhere and no recommendation is ever executed.

These adjustments are deliberately *not* applied to
:class:`models.FPLPointsPredictor`. That model already trains on
``penalties_order``, ``corners_and_indirect_freekicks_order`` and availability
features, so layering these boosts on top of its output would double-count the
same signal. The forecaster is the light-weight path that lacks those features,
which is exactly why it needs them added back here.

Integration
-----------
Call after :meth:`SeasonForecaster.forecast` and before
:mod:`season_planner`/:mod:`optimizer` consume the table. A typical chain is
``apply_setpiece_boosts`` → ``apply_doubtful_discount``, with
``apply_dgw_scaling`` inserted only for callers projecting per-GW with
:meth:`FPLPointsPredictor.predict` (the forecaster now scales doubles itself).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .fpl_rules import GKP, DEF, MID, FWD, POINTS_GOAL

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Empirically calibrated (NOT the directive's erroneous 0.75 xG/90):
#: a primary penalty taker gets ~4-6 pens/season at 78% conversion, so
#: roughly 0.09-0.12 extra xG/90 — 0.10 is the working estimate.
PENALTY_TAKER_XG_PER90 = 0.10
#: Primary corner/indirect FK taker: ~8 corners/game for the team at ~2% goal
#: conversion → ~0.48 expected pts/GW, expressed as xA per 90 (3 pts/assist).
CORNER_TAKER_XA_PER90 = 0.16
#: Direct FK specialist: low conversion rate, small signal.
DIRECT_FK_XG_PER90 = 0.05

SIDEGRADE_THRESHOLD_PTS = 1.5   # horizon gain below which a transfer is a sidegrade
DOUBTFUL_DISCOUNT = 0.25        # -25% xpts for doubtful players
RETURNING_MIN_MINUTES = 60      # minutes threshold for returning-from-injury clearance
RETURNING_MIN_CONSECUTIVE = 1   # consecutive matches meeting the threshold

BENCH_GK_MAX_COST_TENTHS = 45   # £4.5m max for bench GK slot (cost is in tenths)

#: Substrings in the ``news`` field that flag a fitness concern.
_INJURY_KEYWORDS = (
    "injur", "knock", "doubt", "miss", "absent", "recover",
    "surgery", "hamstring", "muscle", "ligament",
)

#: Status codes that are themselves an injury/unavailability signal.
_INJURY_STATUSES = ("i", "u", "s")

#: Average minutes played given a start, split by whether the player lasts 60+.
_MINUTES_IF_60 = 68
_MINUTES_IF_EARLY = 25

_SETPIECE_COLUMNS = (
    "penalties_order",
    "corners_and_indirect_freekicks_order",
    "direct_freekicks_order",
)


# ---------------------------------------------------------------------------
# Double / blank gameweeks
# ---------------------------------------------------------------------------

def detect_dgw_bgw(fixtures: list[dict], current_gw: int, horizon: int = 5) -> pd.DataFrame:
    """Fixture count per (team, gw) over ``[current_gw, current_gw + horizon)``.

    ``fixtures`` is the raw list from :func:`data_collector.fetch_fixtures`.
    Returns one row per team per gameweek in the window, including the blanks:
    a team with no fixture in a gameweek gets ``n_fixtures=0``.
    """
    gws = list(range(current_gw, current_gw + horizon))
    counts: dict[tuple[int, int], int] = {}
    teams: set[int] = set()

    for fix in fixtures:
        gw = fix.get("event")
        if gw is None or int(gw) not in gws:
            continue
        gw = int(gw)
        for key in ("team_h", "team_a"):
            team_id = fix.get(key)
            if team_id is None:
                continue
            team_id = int(team_id)
            teams.add(team_id)
            counts[(team_id, gw)] = counts.get((team_id, gw), 0) + 1

    rows = [
        {"team": team, "gw": gw, "n_fixtures": counts.get((team, gw), 0)}
        for team in sorted(teams)
        for gw in gws
    ]
    if not rows:
        log.info("detect_dgw_bgw: no fixtures in GW%d-%d window", current_gw, current_gw + horizon - 1)
        return pd.DataFrame({
            "team": pd.Series(dtype=int),
            "gw": pd.Series(dtype=int),
            "n_fixtures": pd.Series(dtype=int),
            "is_dgw": pd.Series(dtype=bool),
            "is_bgw": pd.Series(dtype=bool),
        })

    df = pd.DataFrame(rows)
    df["is_dgw"] = df["n_fixtures"] >= 2
    df["is_bgw"] = df["n_fixtures"] == 0
    log.info(
        "detect_dgw_bgw: %d double and %d blank team-gameweeks in GW%d-%d",
        int(df["is_dgw"].sum()), int(df["is_bgw"].sum()),
        current_gw, current_gw + horizon - 1,
    )
    return df


def apply_dgw_scaling(forecasts_df: pd.DataFrame, dgw_frame: pd.DataFrame) -> pd.DataFrame:
    """Scale ``xpts`` by the number of fixtures each team plays that gameweek.

    Doubles are multiplied up, blanks go to zero and single-fixture rows are
    unchanged. Rows with no match in ``dgw_frame`` are left alone; they show
    ``n_fixtures=1``.

    The :class:`SeasonForecaster` already does this internally, so this is for
    external callers scaling per-GW :meth:`FPLPointsPredictor.predict` output.
    """
    out = forecasts_df.copy()
    if out.empty or dgw_frame.empty:
        out["n_fixtures"] = 1
        return out

    counts = dgw_frame[["team", "gw", "n_fixtures"]].copy()
    out = out.merge(counts, on=["team", "gw"], how="left")
    out["n_fixtures"] = out["n_fixtures"].fillna(1).astype(int)
    out["xpts"] = out["xpts"] * out["n_fixtures"]

    log.info(
        "apply_dgw_scaling: %d rows scaled up, %d blanked",
        int((out["n_fixtures"] >= 2).sum()), int((out["n_fixtures"] == 0).sum()),
    )
    return out


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

def returning_player_filter(
    player_pool_df: pd.DataFrame,
    element_summaries: dict[int, dict],
    min_minutes: int = RETURNING_MIN_MINUTES,
    min_consecutive: int = RETURNING_MIN_CONSECUTIVE,
) -> pd.DataFrame:
    """Flag players still working back from an injury.

    A player carrying an injury signal (status flag or ``news`` text) is only
    cleared once their most recent ``min_consecutive`` appearances each cleared
    ``min_minutes``. Players with no injury signal, and players with no history
    at all (preseason), are treated as ready.
    """
    out = player_pool_df.copy()
    returning: list[bool] = []

    for _, row in out.iterrows():
        if not _has_injury_signal(row):
            returning.append(False)
            continue

        summary = element_summaries.get(int(row["id"])) or {}
        history = summary.get("history") or []
        if not history:
            # Preseason or uncached: no evidence to filter on.
            returning.append(False)
            continue

        recent = sorted(history, key=lambda h: h.get("round", 0), reverse=True)[:min_consecutive]
        cleared = (
            len(recent) >= min_consecutive
            and all(_safe_float(h.get("minutes", 0)) >= min_minutes for h in recent)
        )
        returning.append(not cleared)

    out["returning_from_injury"] = returning
    out["ready_to_buy"] = ~out["returning_from_injury"]
    log.info(
        "returning_player_filter: %d of %d players still returning from injury",
        int(out["returning_from_injury"].sum()), len(out),
    )
    return out


def apply_doubtful_discount(
    forecasts_df: pd.DataFrame,
    player_pool_df: pd.DataFrame,
    current_gw: int,
    discount: float = DOUBTFUL_DISCOUNT,
) -> pd.DataFrame:
    """Discount ``xpts`` for doubtful players in the immediate gameweek.

    Doubtful means ``status == 'd'`` or ``chance_of_playing_next_round <= 75``.
    The chance-of-playing field only describes the next gameweek, so the
    discount lands on ``gw == current_gw`` rows only — but ``is_doubtful`` is a
    player-level flag, True on every row of that player, so callers can see who
    is flagged without re-deriving it.

    The forecaster's ``P_START_BY_STATUS['d'] = 0.55`` already captures part of
    this; the extra penalty reflects that managers want availability risk priced
    more aggressively than the base projection does.
    """
    out = forecasts_df.copy()
    if out.empty:
        out["is_doubtful"] = pd.Series(dtype=bool)
        return out

    cols = [c for c in ("id", "status", "chance_of_playing_next_round") if c in player_pool_df.columns]
    availability = player_pool_df[cols].copy().rename(columns={"id": "element"})
    out = out.merge(availability, on="element", how="left")

    status = out["status"].astype(str).str.lower() if "status" in out.columns else pd.Series("", index=out.index)
    chance = (
        pd.to_numeric(out["chance_of_playing_next_round"], errors="coerce")
        if "chance_of_playing_next_round" in out.columns
        else pd.Series(np.nan, index=out.index)
    )
    out["is_doubtful"] = (status == "d") | (chance.notna() & (chance <= 75))

    discounted = out["is_doubtful"] & (out["gw"] == current_gw)
    out.loc[discounted, "xpts"] = out.loc[discounted, "xpts"] * (1.0 - discount)
    out = out.drop(columns=[c for c in ("status", "chance_of_playing_next_round") if c in out.columns])

    log.info(
        "apply_doubtful_discount: %d GW%d rows discounted by %.0f%%",
        int(discounted.sum()), current_gw, discount * 100,
    )
    return out


# ---------------------------------------------------------------------------
# Set-piece duty
# ---------------------------------------------------------------------------

def apply_setpiece_boosts(
    forecasts_df: pd.DataFrame,
    bootstrap_df: pd.DataFrame,
    scale: float = 1.0,
) -> pd.DataFrame:
    """Add expected points for penalty, corner and direct free-kick duty.

    Only the *primary* taker on each set-piece order (order == 1) is boosted;
    a missing, zero or secondary order contributes nothing. Boosts are scaled by
    expected minutes so a rotation risk on pens is not credited a full 90.

    ``scale`` should be set to ``1 - ml_blend`` when the forecaster xpts already
    contain a partial ML signal (FPLPointsPredictor trains on set-piece features
    and would double-count the full boost). Set to 1.0 when xpts is ep_next only.
    """
    out = forecasts_df.copy()
    if out.empty:
        out["xpts_setpiece_boost"] = pd.Series(dtype=float)
        return out

    keep = ["id"] + [c for c in _SETPIECE_COLUMNS if c in bootstrap_df.columns]
    orders = bootstrap_df[keep].copy().rename(columns={"id": "element"})
    n_before = len(out)
    out = out.merge(orders, on="element", how="left")
    if len(out) != n_before:
        log.warning(
            "apply_setpiece_boosts: merge changed row count %d → %d "
            "(bootstrap_df may have duplicate element ids)",
            n_before, len(out),
        )

    p_start = pd.to_numeric(out["p_start"], errors="coerce").fillna(0.0)
    p_60 = pd.to_numeric(out["p_60"], errors="coerce").fillna(0.0)
    expected_mins = p_start * (p_60 * _MINUTES_IF_60 + (1.0 - p_60) * _MINUTES_IF_EARLY)
    minutes_share = expected_mins / 90.0

    # Position-dependent goal value; fall back to the lowest (FWD) if unknown.
    pts_per_goal = (
        pd.to_numeric(out["position"], errors="coerce")
        .map(POINTS_GOAL)
        .fillna(POINTS_GOAL[FWD])
        .astype(float)
    )

    penalty_boost = _is_primary(out, "penalties_order") * PENALTY_TAKER_XG_PER90 * minutes_share * pts_per_goal
    corner_boost = _is_primary(out, "corners_and_indirect_freekicks_order") * CORNER_TAKER_XA_PER90 * minutes_share * 3
    fk_boost = _is_primary(out, "direct_freekicks_order") * DIRECT_FK_XG_PER90 * minutes_share * pts_per_goal

    out["xpts_setpiece_boost"] = (penalty_boost + corner_boost + fk_boost) * scale
    out["xpts"] = out["xpts"] + out["xpts_setpiece_boost"]
    out = out.drop(columns=[c for c in _SETPIECE_COLUMNS if c in out.columns])

    log.info(
        "apply_setpiece_boosts: %d rows boosted (scale=%.2f), %.2f xPts added in total",
        int((out["xpts_setpiece_boost"] > 0).sum()), scale, float(out["xpts_setpiece_boost"].sum()),
    )
    return out


# ---------------------------------------------------------------------------
# Transfer and bench advice
# ---------------------------------------------------------------------------

def check_ft_sidegrade(
    out_element: int,
    in_element: int,
    forecasts_df: pd.DataFrame,
    current_gw: int,
    horizon: int = 3,
    threshold: float = SIDEGRADE_THRESHOLD_PTS,
) -> dict:
    """Judge whether a same-position transfer is worth the free transfer.

    A lateral move inside a position that gains less than ``threshold`` points
    over the horizon is a sidegrade: rolling the transfer is usually better.
    Advisory only — nothing is executed.
    """
    gws = range(current_gw, current_gw + horizon)
    xpts_out = _horizon_xpts(forecasts_df, out_element, gws)
    xpts_in = _horizon_xpts(forecasts_df, in_element, gws)
    xp_gain = xpts_in - xpts_out

    out_position = _player_position(forecasts_df, out_element)
    in_position = _player_position(forecasts_df, in_element)
    same_position = bool(out_position == in_position)
    is_sidegrade = same_position and xp_gain < threshold

    return {
        "out_element": out_element,
        "in_element": in_element,
        "horizon_gws": horizon,
        "horizon_xpts_out": round(xpts_out, 2),
        "horizon_xpts_in": round(xpts_in, 2),
        "xp_gain": round(xp_gain, 2),
        "same_position": same_position,
        "is_sidegrade": is_sidegrade,
        "recommendation": (
            "ROLL free transfer — lateral sidegrade" if is_sidegrade else "Transfer approved"
        ),
        "advisory_only": True,
    }


def sort_bench_slots(bench_df: pd.DataFrame) -> pd.DataFrame:
    """Order the four bench players into their FPL slots.

    Slot 0 is the reserve keeper — he can only replace the keeper, so he never
    competes with the outfielders. Slots 1-3 are the outfielders in descending
    expected points, since that is the order auto-substitution walks.
    """
    out = bench_df.copy().reset_index(drop=True)
    if out.empty:
        out["bench_slot"] = pd.Series(dtype=int)
        return out

    keepers = out[out["position"] == GKP]
    if not keepers.empty:
        affordable = keepers[keepers["now_cost"] <= BENCH_GK_MAX_COST_TENTHS]
        pick = affordable if not affordable.empty else keepers
        slot0_idx = pick["now_cost"].idxmin()
    else:
        # Not a legal FPL squad, but do something sane rather than crash.
        log.info("sort_bench_slots: no goalkeeper on the bench; slotting lowest-xPts player first")
        slot0_idx = out["xpts"].idxmin()

    rest = out.drop(index=slot0_idx).sort_values("xpts", ascending=False)
    order = [slot0_idx] + list(rest.index)
    out["bench_slot"] = pd.Series({idx: slot for slot, idx in enumerate(order)})
    return out.sort_values("bench_slot").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _has_injury_signal(row) -> bool:
    """True when the status flag or the news text points at a fitness problem."""
    status = str(row.get("status", "")).lower()
    if status in _INJURY_STATUSES:
        return True
    news = row.get("news")
    if news is None or (isinstance(news, float) and pd.isna(news)):
        return False
    news = str(news).lower()
    return bool(news) and any(word in news for word in _INJURY_KEYWORDS)


def _is_primary(df: pd.DataFrame, column: str) -> pd.Series:
    """1.0 where the player is the primary taker on ``column``, else 0.0.

    Missing columns and NaN orders both compare False, which is the guard
    against older bootstrap payloads that omit the set-piece fields.
    """
    if column not in df.columns:
        return pd.Series(0.0, index=df.index)
    orders = pd.to_numeric(df[column], errors="coerce")
    return (orders == 1).astype(float)


def _horizon_xpts(forecasts_df: pd.DataFrame, element: int, gws) -> float:
    """Total xPts for one player across ``gws``; 0.0 if absent from the table."""
    rows = forecasts_df[
        (forecasts_df["element"] == element) & (forecasts_df["gw"].isin(list(gws)))
    ]
    return float(rows["xpts"].sum()) if not rows.empty else 0.0


def _player_position(forecasts_df: pd.DataFrame, element: int):
    """The player's position id, or None when they are not in the table."""
    rows = forecasts_df[forecasts_df["element"] == element]
    return None if rows.empty else rows["position"].iloc[0]


def _safe_float(value) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Captain safety filter
# ---------------------------------------------------------------------------

#: Players below this start probability are excluded from captaincy to avoid
#: the "0-minute captain kill" scenario. 0.70 means the player must be at
#: worst a 70% starter (status='a' or chance_of_playing=75+).
CAPTAIN_MIN_P_START: float = 0.70


def apply_captain_safety_filter(
    captain_scores: "pd.Series",
    forecasts_df: "pd.DataFrame",
    current_gw: int,
    min_p_start: float = CAPTAIN_MIN_P_START,
) -> "pd.Series":
    """Zero out captain scores for players whose start probability is too low.

    The single most costly mistake in FPL is captaining a player who does not
    start. ``p_start`` from the forecaster already incorporates injury status
    and ``chance_of_playing``; this filter enforces a hard floor so the
    optimizer cannot pick a doubtful player as captain even when their raw
    expected points are very high.

    Parameters
    ----------
    captain_scores:
        Series indexed by element IDs containing the pre-filter captain scores.
    forecasts_df:
        The forecaster output; must contain ``element``, ``gw``, ``p_start``.
    current_gw:
        The gameweek being planned.
    min_p_start:
        Players below this start-probability are zeroed out.

    Returns
    -------
    pd.Series
        Captain scores with unsafe picks set to 0.0.
    """
    import pandas as _pd
    out = captain_scores.copy()
    gw_rows = forecasts_df[forecasts_df["gw"] == current_gw]
    if "p_start" not in gw_rows.columns:
        return out
    p_start_map = gw_rows.set_index("element")["p_start"].to_dict()
    for element, score in out.items():
        p = p_start_map.get(int(element), 1.0)
        if float(p) < min_p_start:
            out[element] = 0.0
    log.info(
        "apply_captain_safety_filter: zeroed %d of %d candidates (p_start < %.2f)",
        int((captain_scores > 0).sum() - (out > 0).sum()),
        len(out),
        min_p_start,
    )
    return out
