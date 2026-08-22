"""Live gameweek scoring helpers.

These functions are read-only. They combine a manager's locked GW picks with
FPL's public ``/event/{gw}/live/`` feed to produce a provisional score while the
round is in progress. The score is intentionally labelled provisional because
bonus, autosubs and late corrections can change before FPL finalises the GW.
"""
from __future__ import annotations

from statistics import median


def normalise_live(live_data: dict) -> dict[int, dict]:
    """Return ``{element_id: stats}`` from the raw event-live response."""
    if not live_data:
        return {}
    rows = live_data.get("elements")
    if isinstance(rows, list):
        return {
            int(row["id"]): (row.get("stats") or {})
            for row in rows
            if row.get("id") is not None
        }
    out: dict[int, dict] = {}
    for key, value in live_data.items():
        try:
            out[int(key)] = value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            continue
    return out


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_picks(picks: list[dict], live_data: dict, transfer_cost: int | float = 0) -> dict:
    """Calculate a manager's provisional GW score from locked picks.

    ``multiplier`` already captures captaincy (2/3) and any autosubs FPL has
    processed. Bench players with multiplier 0 do not count. Transfer hit cost
    is subtracted when supplied.
    """
    live = normalise_live(live_data)
    rows: list[dict] = []
    gross = 0.0
    bench_raw = 0.0

    for pick in picks or []:
        element = pick.get("element")
        if element is None:
            continue
        element = int(element)
        stats = live.get(element, {})
        raw = _num(stats.get("total_points"))
        multiplier = int(pick.get("multiplier") or 0)
        counted = raw * multiplier
        gross += counted
        if multiplier == 0:
            bench_raw += raw
        rows.append({
            "element": element,
            "position": int(pick.get("position") or 0),
            "multiplier": multiplier,
            "is_captain": bool(pick.get("is_captain")),
            "raw_points": raw,
            "counted_points": counted,
            "minutes": int(_num(stats.get("minutes"))),
        })

    hit = _num(transfer_cost)
    return {
        "gross": round(gross, 1),
        "transfer_cost": round(hit, 1),
        "net": round(gross - hit, 1),
        "bench_raw": round(bench_raw, 1),
        "players": rows,
    }


def build_live_summary(
    *,
    gw: int,
    picks: list[dict],
    live_data: dict,
    bootstrap: dict,
    transfer_cost: int | float = 0,
) -> dict:
    """Add names, captain and top contributors to :func:`score_picks`."""
    scored = score_picks(picks, live_data, transfer_cost=transfer_cost)
    elements = {
        int(row["id"]): row
        for row in bootstrap.get("elements", [])
        if row.get("id") is not None
    }

    player_rows = []
    for row in scored["players"]:
        element = elements.get(row["element"], {})
        enriched = dict(row)
        enriched["name"] = element.get("web_name") or str(row["element"])
        enriched["team"] = element.get("team")
        player_rows.append(enriched)

    counted = [p for p in player_rows if p["counted_points"] != 0]
    counted.sort(key=lambda p: (-p["counted_points"], p["position"]))
    captain = next((p for p in player_rows if p["is_captain"]), None)

    return {
        "gw": int(gw),
        "gross": scored["gross"],
        "transfer_cost": scored["transfer_cost"],
        "net": scored["net"],
        "bench_raw": scored["bench_raw"],
        "captain": captain,
        "top_contributors": counted[:5],
        "players": player_rows,
    }


def live_score_distribution(
    manager_pick_lists: list[list[dict]],
    live_data: dict,
    transfer_costs: list[int | float] | None = None,
) -> dict:
    """Aggregate provisional net scores for a sample of locked manager teams."""
    costs = list(transfer_costs or [])
    scores = []
    for idx, picks in enumerate(manager_pick_lists):
        cost = costs[idx] if idx < len(costs) else 0
        scores.append(score_picks(picks, live_data, transfer_cost=cost)["net"])
    if not scores:
        return {"sample_size": 0, "average": 0.0, "median": 0.0, "high": 0.0, "low": 0.0}
    return {
        "sample_size": len(scores),
        "average": round(sum(scores) / len(scores), 1),
        "median": round(float(median(scores)), 1),
        "high": round(max(scores), 1),
        "low": round(min(scores), 1),
    }
