"""
Deep research analysis — runs every 2 GWs from stage_post_gw_analysis.

Looks 6 GWs ahead at fixture difficulty, captaincy rotation opportunities,
differentials, and chip-window candidates. Cross-references top-100 holdings.

Output: reports/deep_research_gw{N}.md
Returns a dict with concise summaries for the email body.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
TOP100_DIR = ROOT / "data" / "top100"

_FDR_HORIZON = 6
_CAPTAIN_HORIZON = 4
_DIFFERENTIAL_MAX_OWNED_PCT = 5.0
_DIFFERENTIAL_MIN_EP_NEXT = 5.0


def _to_float(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Forward fixture difficulty
# ---------------------------------------------------------------------------

def _forward_fdr(
    fixtures: list[dict],
    teams: dict[int, dict],
    start_gw: int,
    horizon: int,
) -> dict[int, dict]:
    """Per-team fixture info for GWs [start_gw, start_gw + horizon)."""
    acc: dict[int, list[dict]] = {tid: [] for tid in teams}
    for fx in fixtures:
        ev = fx.get("event")
        if ev is None or ev < start_gw or ev >= start_gw + horizon:
            continue
        h, a = fx.get("team_h"), fx.get("team_a")
        if h is not None:
            acc.setdefault(int(h), []).append({
                "gw": ev,
                "opp": teams.get(int(a), {}).get("short_name", "?") if a else "?",
                "fdr": _to_float(fx.get("team_h_difficulty") or 3),
                "home": True,
            })
        if a is not None:
            acc.setdefault(int(a), []).append({
                "gw": ev,
                "opp": teams.get(int(h), {}).get("short_name", "?") if h else "?",
                "fdr": _to_float(fx.get("team_a_difficulty") or 3),
                "home": False,
            })
    out: dict[int, dict] = {}
    for tid in teams:
        fxs = sorted(acc.get(tid, []), key=lambda f: f["gw"])
        avg = sum(f["fdr"] for f in fxs) / len(fxs) if fxs else 5.0
        out[tid] = {"avg_fdr": round(avg, 2), "fixtures": fxs}
    return out


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def _squad_fdr_table(
    elements: dict, teams: dict, fdr_by_team: dict, my_ids: set
) -> list[dict]:
    rows = []
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    for el_id in my_ids:
        el = elements.get(el_id)
        if not el:
            continue
        tid = int(el.get("team", 0))
        info = fdr_by_team.get(tid, {"avg_fdr": 3.0, "fixtures": []})
        rows.append({
            "name": el.get("web_name", str(el_id)),
            "pos": pos_map.get(int(el.get("element_type", 0)), "?"),
            "team": teams.get(tid, {}).get("short_name", "?"),
            "avg_fdr": info["avg_fdr"],
            "ep_next": round(_to_float(el.get("ep_next")), 2),
            "fixtures": [
                f"{f['opp']}({'H' if f['home'] else 'A'})"
                for f in info["fixtures"][:_FDR_HORIZON]
            ],
        })
    rows.sort(key=lambda r: r["avg_fdr"])
    return rows


def _captaincy_candidates(
    elements: dict, teams: dict, fdr_by_team: dict, horizon: int
) -> list[dict]:
    out = []
    for el_id, el in elements.items():
        if (el.get("status") or "a") != "a":
            continue
        ep = _to_float(el.get("ep_next"))
        if ep < 6.0:
            continue
        tid = int(el.get("team", 0))
        info = fdr_by_team.get(tid, {"avg_fdr": 3.0, "fixtures": []})
        score = ep / max(info["avg_fdr"], 1.0)
        out.append({
            "name": el.get("web_name", str(el_id)),
            "team": teams.get(tid, {}).get("short_name", "?"),
            "cost": round(_to_float(el.get("now_cost")) / 10.0, 1),
            "ep_next": round(ep, 2),
            "selected_pct": round(_to_float(el.get("selected_by_percent")), 1),
            "avg_fdr": info["avg_fdr"],
            "next_fixtures": [
                f"{f['opp']}({'H' if f['home'] else 'A'})"
                for f in info["fixtures"][:horizon]
            ],
            "_score": score,
        })
    out.sort(key=lambda r: -r["_score"])
    for r in out:
        del r["_score"]
    return out[:12]


def _differentials(
    elements: dict, teams: dict, fdr_by_team: dict, my_ids: set
) -> list[dict]:
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    out = []
    for el_id, el in elements.items():
        if el_id in my_ids:
            continue
        if (el.get("status") or "a") != "a":
            continue
        owned = _to_float(el.get("selected_by_percent"))
        if owned > _DIFFERENTIAL_MAX_OWNED_PCT:
            continue
        ep = _to_float(el.get("ep_next"))
        if ep < _DIFFERENTIAL_MIN_EP_NEXT:
            continue
        tid = int(el.get("team", 0))
        info = fdr_by_team.get(tid, {"avg_fdr": 3.0, "fixtures": []})
        score = ep / max(info["avg_fdr"], 1.0)
        out.append({
            "element": el_id,
            "name": el.get("web_name", str(el_id)),
            "pos": pos_map.get(int(el.get("element_type", 0)), "?"),
            "team": teams.get(tid, {}).get("short_name", "?"),
            "cost": round(_to_float(el.get("now_cost")) / 10.0, 1),
            "ep_next": round(ep, 2),
            "selected_pct": round(owned, 1),
            "avg_fdr": info["avg_fdr"],
            "next_fixtures": [
                f"{f['opp']}({'H' if f['home'] else 'A'})"
                for f in info["fixtures"][:4]
            ],
            "_score": score,
        })
    out.sort(key=lambda r: -r["_score"])
    for r in out:
        del r["_score"]
    return out[:10]


def _chip_windows(
    fixtures: list[dict], teams: dict, elements: dict, after_gw: int
) -> list[dict]:
    """Rank GWs by how many premium players have easy fixtures."""
    premium = [
        e for e in elements.values()
        if _to_float(e.get("ep_next")) >= 6.0 and (e.get("status") or "a") == "a"
    ]
    team_gw_fdr: dict[int, dict[int, float]] = {}
    for fx in fixtures:
        ev = fx.get("event")
        if ev is None or ev <= after_gw or ev > after_gw + 10:
            continue
        h, a = fx.get("team_h"), fx.get("team_a")
        if h:
            team_gw_fdr.setdefault(int(h), {})[ev] = _to_float(fx.get("team_h_difficulty") or 3)
        if a:
            team_gw_fdr.setdefault(int(a), {})[ev] = _to_float(fx.get("team_a_difficulty") or 3)

    windows = []
    for gw in range(after_gw + 1, after_gw + 9):
        good = []
        for p in premium:
            tid = int(p.get("team", 0))
            fdr = team_gw_fdr.get(tid, {}).get(gw, 5.0)
            if fdr <= 3:
                good.append({
                    "name": p.get("web_name"),
                    "ep_next": round(_to_float(p.get("ep_next")), 2),
                    "fdr": fdr,
                })
        good.sort(key=lambda p: -p["ep_next"])
        ep_sum = sum(p["ep_next"] for p in good[:6])
        windows.append({
            "gw": gw,
            "n_easy_premiums": len(good),
            "top6_ep_sum": round(ep_sum, 1),
            "top_names": [p["name"] for p in good[:4]],
        })
    windows.sort(key=lambda w: -w["top6_ep_sum"])
    return windows[:6]


def _top100_insights(elements: dict, my_ids: set, gw: int) -> dict:
    snap = None
    for candidate_gw in range(gw, max(0, gw - 5), -1):
        snap = _load_json(TOP100_DIR / f"gw{candidate_gw}.json")
        if snap:
            break
    if not snap:
        return {"available": False}

    summary = snap.get("summary") or {}
    most_owned = summary.get("most_owned_players") or []
    top100_ids = {int(r["element"]) for r in most_owned if r.get("count", 0) >= 3}

    not_in_squad = []
    for r in most_owned:
        el_id = int(r.get("element", 0))
        if el_id in my_ids or el_id not in elements:
            continue
        el = elements[el_id]
        not_in_squad.append({
            "element": el_id,
            "name": el.get("web_name") or str(el_id),
            "top100_count": r.get("count", 0),
            "ep_next": round(_to_float(el.get("ep_next")), 2),
            "selected_pct": round(_to_float(el.get("selected_by_percent")), 1),
        })

    unique_to_me = []
    for el_id in my_ids:
        if el_id in top100_ids:
            continue
        el = elements.get(el_id, {})
        unique_to_me.append({
            "element": el_id,
            "name": el.get("web_name", str(el_id)),
            "ep_next": round(_to_float(el.get("ep_next")), 2),
            "selected_pct": round(_to_float(el.get("selected_by_percent")), 1),
        })
    unique_to_me.sort(key=lambda r: r["ep_next"])

    return {
        "available": True,
        "snap_gw": snap.get("gw"),
        "n_sampled": int(snap.get("n_picks_sampled") or 0),
        "top100_not_mine": not_in_squad[:8],
        "unique_to_me": unique_to_me[:5],
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _build_report(
    gw: int,
    squad_fdr: list[dict],
    captaincy: list[dict],
    differentials: list[dict],
    chip_windows: list[dict],
    top100: dict,
    web_report: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Deep Research — After GW{gw}",
        f"*Generated {now} — looking {_FDR_HORIZON} GWs ahead*",
        "",
    ]

    lines += ["## Squad Fixture Difficulty (next 6 GWs)", ""]
    lines += ["| Player | Pos | Team | Avg FDR | ep_next | Fixtures |",
              "|--------|-----|------|---------|---------|----------|"]
    for r in squad_fdr:
        lines.append(
            f"| {r['name']} | {r['pos']} | {r['team']} | {r['avg_fdr']} "
            f"| {r['ep_next']} | {' '.join(r['fixtures'])} |"
        )
    lines.append("")

    lines += [f"## Captaincy Candidates (next {_CAPTAIN_HORIZON} GWs)", ""]
    lines += ["| Player | Team | Cost | ep_next | Avg FDR | Next fixtures |",
              "|--------|------|------|---------|---------|---------------|"]
    for r in captaincy[:8]:
        lines.append(
            f"| {r['name']} | {r['team']} | £{r['cost']}m | {r['ep_next']} "
            f"| {r['avg_fdr']} | {' '.join(r['next_fixtures'])} |"
        )
    lines.append("")

    lines += [
        f"## Differentials (<{_DIFFERENTIAL_MAX_OWNED_PCT}% owned, ep_next ≥{_DIFFERENTIAL_MIN_EP_NEXT})",
        "",
    ]
    if differentials:
        lines += ["| Player | Pos | Team | Cost | ep_next | Owned | Next 4 |",
                  "|--------|-----|------|------|---------|-------|--------|"]
        for r in differentials[:8]:
            lines.append(
                f"| {r['name']} | {r['pos']} | {r['team']} | £{r['cost']}m "
                f"| {r['ep_next']} | {r['selected_pct']}% | {' '.join(r['next_fixtures'])} |"
            )
    else:
        lines.append("*No differentials meeting criteria.*")
    lines.append("")

    lines += ["## Best Chip Windows (next 8 GWs)", ""]
    lines += ["| GW | Easy premiums | Top-6 ep sum | Top targets |",
              "|----|--------------|--------------|-------------|"]
    for w in chip_windows:
        lines.append(
            f"| GW{w['gw']} | {w['n_easy_premiums']} | {w['top6_ep_sum']} "
            f"| {', '.join(w['top_names'][:3])} |"
        )
    lines.append("")

    lines += ["## Top-100 Insights", ""]
    if top100.get("available"):
        lines.append(f"*Based on GW{top100.get('snap_gw', '?')} top-100 snapshot.*")
        lines.append("")
        if top100["top100_not_mine"]:
            lines.append("**Smart money holds but I don't:**")
            denom = max(1, int(top100.get("n_sampled") or 0))
            for r in top100["top100_not_mine"]:
                pct = 100.0 * float(r["top100_count"]) / denom
                lines.append(
                    f"- {r['name']} — {r['top100_count']}/{denom} managers "
                    f"({pct:.0f}%), ep_next {r['ep_next']}, "
                    f"{r['selected_pct']}% owned"
                )
        if top100["unique_to_me"]:
            lines += ["", "**I hold but top-100 don't (differential risk):**"]
            for r in top100["unique_to_me"]:
                lines.append(
                    f"- {r['name']} — ep_next {r['ep_next']}, {r['selected_pct']}% owned"
                )
    else:
        lines.append("*No top-100 snapshot available yet.*")

    lines += ["", "## Reddit Buzz (r/FantasyPremierLeague)", ""]
    if web_report and web_report.get("available") and web_report.get("players"):
        lines += ["| Player | Positive | Negative | Net |",
                  "|--------|----------|----------|-----|"]
        for r in web_report["players"][:10]:
            sentiment = "📈" if r["net"] > 0 else ("📉" if r["net"] < 0 else "—")
            lines.append(
                f"| {r['name']} | {r['positive']} | {r['negative']} "
                f"| {r['net']:+d} {sentiment} |"
            )
    else:
        lines.append("*Reddit scan unavailable (likely blocked from GitHub Actions runner).*")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Bot idea generation
# ---------------------------------------------------------------------------

def _generate_ideas(top100: dict, differentials: list[dict]) -> list[dict]:
    """Convert deep research findings into advisory idea_list entries.

    All priorities are capped below VETO_PRIORITY (0.8) so these never hard-block
    a transfer; they are advisory inputs to the MILP via the pre-deadline simulator.
    """
    ideas = []

    if top100.get("available"):
        # Smart-money threshold is a proportion of the actual locked sample,
        # not a hard-coded denominator. GW2 sampled 25 managers, so the old
        # "/10" text produced impossible values such as 24/10.
        denom = max(1, int(top100.get("n_sampled") or 0))
        min_holders = max(1, math.ceil(0.70 * denom))
        for r in top100.get("top100_not_mine") or []:
            if r.get("top100_count", 0) >= min_holders and r.get("ep_next", 0) >= 5.0:
                pct = 100.0 * float(r["top100_count"]) / denom
                ideas.append({
                    "action": "transfer_in",
                    "element": r["element"],
                    "name": r["name"],
                    "reason": (
                        f"Top-100 smart money: {r['top100_count']}/{denom} "
                        f"managers hold ({pct:.0f}%), ep_next {r['ep_next']}"
                    ),
                    "priority": 0.4,
                })

        # I hold but top-100 don't and ep_next is low — mild transfer_out nudge
        for r in top100.get("unique_to_me") or []:
            if r.get("ep_next", 99.0) < 4.0:
                ideas.append({
                    "action": "transfer_out",
                    "element": r["element"],
                    "name": r["name"],
                    "reason": (f"Top-100 avoid: ep_next only {r['ep_next']}, "
                               f"{r['selected_pct']}% overall ownership — likely liability"),
                    "priority": 0.3,
                })

    # High-value differentials with easy fixtures → speculative transfer_in
    for d in differentials or []:
        if d.get("ep_next", 0) >= 7.0 and d.get("avg_fdr", 5.0) <= 2.5:
            ideas.append({
                "action": "transfer_in",
                "element": d["element"],
                "name": d["name"],
                "reason": (f"Differential: ep_next {d['ep_next']}, "
                           f"avg FDR {d['avg_fdr']}, only {d['selected_pct']}% owned"),
                "priority": 0.35,
            })

    return ideas


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

class DeepResearcher:
    def research(
        self,
        gw: int,
        bootstrap: dict,
        fixtures: list[dict],
        my_picks: list[dict],
    ) -> dict:
        elements = {int(e["id"]): e for e in bootstrap.get("elements", [])}
        teams = {int(t["id"]): t for t in bootstrap.get("teams", [])}
        my_ids = {int(p["element"]) for p in my_picks if p.get("element") is not None}

        fdr_by_team = _forward_fdr(fixtures, teams, gw + 1, _FDR_HORIZON)

        squad_fdr = _squad_fdr_table(elements, teams, fdr_by_team, my_ids)
        captaincy = _captaincy_candidates(elements, teams, fdr_by_team, _CAPTAIN_HORIZON)
        differentials = _differentials(elements, teams, fdr_by_team, my_ids)
        chip_windows_list = _chip_windows(fixtures, teams, elements, gw)
        top100 = _top100_insights(elements, my_ids, gw)

        # Web scanner — best-effort; failure never blocks the rest.
        try:
            from bot.web_scanner import WebScanner
            web_report = WebScanner().scan(list(bootstrap.get("elements", [])))
        except Exception as exc:
            log.warning("Web scanner failed (%s) — omitting from report.", exc)
            web_report = {"available": False, "source": "", "players": []}

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        md = _build_report(gw, squad_fdr, captaincy, differentials, chip_windows_list, top100,
                           web_report)
        (REPORTS_DIR / f"deep_research_gw{gw}.md").write_text(md, encoding="utf-8")
        log.info("Deep research report: reports/deep_research_gw%d.md", gw)

        ideas = _generate_ideas(top100, differentials)
        log.info("Deep research: %d advisory idea(s) generated for bot planning.", len(ideas))

        return {
            "gw": gw,
            "captaincy_top3": captaincy[:3],
            "differentials_top5": differentials[:5],
            "chip_windows": chip_windows_list,
            "top100": top100,
            "ideas": ideas,
            "email_body": _email_body(gw, captaincy, differentials, chip_windows_list, top100,
                                      web_report),
        }


def _email_body(gw, captaincy, differentials, chip_windows, top100,
                web_report=None) -> str:
    lines = [f"Deep research complete after GW{gw}.\n"]
    if captaincy:
        c = captaincy[0]
        lines.append(
            f"Top captaincy pick: {c['name']} ({c['team']}, £{c['cost']}m, "
            f"ep_next {c['ep_next']}, avg FDR {c['avg_fdr']})"
        )
    if chip_windows:
        w = chip_windows[0]
        lines.append(
            f"Best chip window: GW{w['gw']} — {w['n_easy_premiums']} premium(s) "
            f"with easy fixtures, top-6 ep sum {w['top6_ep_sum']}"
        )
    if differentials:
        d = differentials[0]
        lines.append(
            f"Top differential: {d['name']} ({d['pos']}, £{d['cost']}m, "
            f"ep_next {d['ep_next']}, {d['selected_pct']}% owned)"
        )
    if top100.get("available") and top100.get("top100_not_mine"):
        r = top100["top100_not_mine"][0]
        denom = max(1, int(top100.get("n_sampled") or 0))
        lines.append(
            f"Top-100 hold but you don't: {r['name']} — "
            f"{r['top100_count']}/{denom} smart managers, ep_next {r['ep_next']}"
        )
    if web_report and web_report.get("available") and web_report.get("players"):
        buzzed = [r for r in web_report["players"] if abs(r["net"]) >= 2][:3]
        if buzzed:
            buzz_str = ", ".join(
                f"{r['name']} ({'+' if r['net'] > 0 else ''}{r['net']})"
                for r in buzzed
            )
            lines.append(f"Reddit buzz: {buzz_str}")
    lines.append(f"\nFull report: reports/deep_research_gw{gw}.md")
    return "\n".join(lines)
