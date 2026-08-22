"""Human-readable post-GW lessons derived from public FPL data.

The goal is not to manufacture hindsight.  Team-exposure lessons require both a
strong just-finished GW and an attractive upcoming fixture run, while captain
and bench lessons are based on the user's actual locked picks.
"""
from __future__ import annotations

from collections import defaultdict


def _normalise_live(live_data: dict) -> dict[int, dict]:
    if not live_data:
        return {}
    if isinstance(live_data.get("elements"), list):
        return {
            int(row["id"]): (row.get("stats") or {})
            for row in live_data["elements"]
            if row.get("id") is not None
        }
    out: dict[int, dict] = {}
    for key, value in live_data.items():
        try:
            out[int(key)] = value if isinstance(value, dict) else {}
        except (TypeError, ValueError):
            continue
    return out


def _points(stats: dict) -> float:
    try:
        return float(stats.get("total_points") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _fixture_runs(fixtures: list[dict], start_gw: int, horizon: int) -> dict[int, dict]:
    by_team: dict[int, list[float]] = defaultdict(list)
    end_gw = start_gw + horizon - 1
    for fixture in fixtures or []:
        try:
            event = int(fixture.get("event"))
        except (TypeError, ValueError):
            continue
        if not start_gw <= event <= end_gw:
            continue
        h, a = fixture.get("team_h"), fixture.get("team_a")
        if h is not None:
            by_team[int(h)].append(float(fixture.get("team_h_difficulty") or 3.0))
        if a is not None:
            by_team[int(a)].append(float(fixture.get("team_a_difficulty") or 3.0))
    return {
        team_id: {
            "avg_fdr": sum(values) / len(values),
            "fixtures": len(values),
        }
        for team_id, values in by_team.items()
        if values
    }


def build_reflection_messages(
    *,
    gw: int,
    live_data: dict,
    bootstrap: dict,
    my_picks: list[dict],
    fixtures: list[dict] | None = None,
    horizon: int = 3,
    max_messages: int = 5,
) -> list[str]:
    """Return concise first-person lessons for a completed gameweek."""
    live = _normalise_live(live_data)
    elements = {int(e["id"]): e for e in bootstrap.get("elements", []) if e.get("id") is not None}
    teams = {int(t["id"]): t for t in bootstrap.get("teams", []) if t.get("id") is not None}
    picks = list(my_picks or [])
    my_ids = {int(p["element"]) for p in picks if p.get("element") is not None}

    messages: list[str] = []

    # Captaincy: compare raw points among the locked starting XI.
    starters = [p for p in picks if int(p.get("position") or 99) <= 11]
    captain = next((p for p in picks if p.get("is_captain")), None)
    if captain and starters:
        cap_id = int(captain["element"])
        cap_pts = _points(live.get(cap_id, {}))
        best = max(starters, key=lambda p: _points(live.get(int(p["element"]), {})))
        best_id = int(best["element"])
        best_pts = _points(live.get(best_id, {}))
        if best_id != cap_id and best_pts - cap_pts >= 4.0:
            cap_name = elements.get(cap_id, {}).get("web_name", str(cap_id))
            best_name = elements.get(best_id, {}).get("web_name", str(best_id))
            messages.append(
                f"Captaincy miss: {best_name} scored {best_pts:.0f} while I captained "
                f"{cap_name} for {cap_pts:.0f}. I should tighten the captain model around "
                "ceiling, minutes security and fixture quality."
            )

    # Bench: only count players whose final multiplier is zero, so autosubs do
    # not create a fake regret message.
    bench = [
        p for p in picks
        if int(p.get("position") or 0) > 11 and int(p.get("multiplier") or 0) == 0
    ]
    if bench and starters:
        best_bench = max(bench, key=lambda p: _points(live.get(int(p["element"]), {})))
        worst_start = min(starters, key=lambda p: _points(live.get(int(p["element"]), {})))
        bench_pts = _points(live.get(int(best_bench["element"]), {}))
        start_pts = _points(live.get(int(worst_start["element"]), {}))
        swing = bench_pts - start_pts
        if swing >= 4.0:
            bench_name = elements.get(int(best_bench["element"]), {}).get("web_name", str(best_bench["element"]))
            start_name = elements.get(int(worst_start["element"]), {}).get("web_name", str(worst_start["element"]))
            messages.append(
                f"Bench call hurt: {bench_name} scored {bench_pts:.0f} on my bench while "
                f"{start_name} scored {start_pts:.0f} in the XI — a {swing:.0f}-point selection "
                "swing before any autosub. I should review the start/bench weighting."
            )

    # Team exposure: combine what just happened with the *next* fixture run so
    # a one-week haul alone does not cause a knee-jerk recommendation.
    if fixtures:
        upcoming = _fixture_runs(fixtures, gw + 1, horizon)
        owned_by_team: dict[int, int] = defaultdict(int)
        points_by_team: dict[int, list[float]] = defaultdict(list)
        for el_id, element in elements.items():
            team_id = int(element.get("team") or 0)
            if el_id in my_ids:
                owned_by_team[team_id] += 1
            points_by_team[team_id].append(_points(live.get(el_id, {})))

        candidates: list[tuple[float, int, int, float, float, int]] = []
        for team_id, run in upcoming.items():
            owned = owned_by_team.get(team_id, 0)
            if owned > 1 or run["fixtures"] < max(2, horizon - 1):
                continue
            avg_fdr = float(run["avg_fdr"])
            if avg_fdr > 2.8:
                continue
            top_scores = sorted(points_by_team.get(team_id, []), reverse=True)[:5]
            top5_avg = sum(top_scores) / len(top_scores) if top_scores else 0.0
            if top5_avg < 4.5:
                continue
            # Easy fixtures matter most; recent team returns are supporting evidence.
            score = (3.5 - avg_fdr) * 2.0 + top5_avg / 5.0 - owned * 0.25
            candidates.append((score, team_id, owned, top5_avg, avg_fdr, run["fixtures"]))

        for _, team_id, owned, top5_avg, avg_fdr, fixture_count in sorted(candidates, reverse=True)[:2]:
            team = teams.get(team_id, {})
            team_name = team.get("name") or team.get("short_name") or f"team {team_id}"
            exposure = "no players" if owned == 0 else "only 1 player"
            messages.append(
                f"I underweighted {team_name}: I had {exposure}, their five best FPL scores "
                f"averaged {top5_avg:.1f} points in GW{gw}, and their next {fixture_count} "
                f"fixture(s) average FDR {avg_fdr:.1f}. I should give {team_name} more weight "
                f"in GW{gw + 1} planning instead of treating this as a one-week fluke."
            )

    return messages[:max_messages]
