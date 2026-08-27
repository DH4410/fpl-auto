"""
Automatic narrative report generator for FPL bot decisions.

No external AI or API calls — all text is generated locally from model data,
FPL bootstrap data, and rule-based templates. Runs on every workflow execution.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from .fpl_rules import CHIP_LABELS

log = logging.getLogger(__name__)

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def _mp(tenths: int) -> str:
    return f"£{tenths / 10:.1f}m"


def _pct(f: float) -> str:
    return f"{f * 100:.0f}%"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_report(
    plan: dict,
    current_gw: int,
    forecasts: pd.DataFrame,
    bootstrap: dict,
    last_gw: Optional[int] = None,
    last_gw_data: Optional[dict] = None,
) -> str:
    """
    Build the full narrative Markdown report from model outputs.

    Parameters
    ----------
    plan         : result from SeasonPlanner.plan()
    current_gw   : next GW being planned
    forecasts    : full forecast DataFrame from SeasonForecaster.forecast()
    bootstrap    : FPL bootstrap-static JSON
    last_gw      : last finished GW number (optional)
    last_gw_data : {element_id: stats_dict} from event_live (optional)
    """
    gw_fore = (
        forecasts[forecasts["gw"] == current_gw]
        .drop_duplicates("element")
        .set_index("element")
    )
    teams_by_id = {t["id"]: t["name"] for t in bootstrap.get("teams", [])}
    bp_by_id = {p["id"]: p for p in bootstrap.get("elements", [])}

    sections = [
        _header(current_gw),
        _model_note(),
        _projection_warning_block(plan),
        _immediate_action(plan, current_gw),
        _squad_rationale(plan, current_gw, gw_fore, teams_by_id),
        _transfer_rationale(plan, current_gw, gw_fore),
        _notable_omissions(plan, current_gw, gw_fore, teams_by_id, bp_by_id, last_gw, last_gw_data),
    ]

    if plan.get("chip_plan"):
        sections.append(_chip_schedule(plan))

    rt = plan.get("report_table")
    if rt is not None and not rt.empty:
        sections.append("## GW-by-GW Plan\n\n" + rt.to_markdown(index=False))

    sections.append(_starting_xi_section(plan, current_gw))

    return "\n\n---\n\n".join(s for s in sections if s.strip())


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _header(current_gw: int) -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"# FPL Season Plan — GW{current_gw}\n\n"
        f"*Generated {now} — advisory only, no transfers executed*"
    )


def _projection_warning_block(plan: dict) -> str:
    """Loud banner when the forecast handed the planner implausible inputs.

    Empty (and dropped from the report) in the normal case.
    """
    warnings = plan.get("projection_warnings") or []
    if not warnings:
        return ""
    lines = [
        "## Projection sanity warning",
        "",
        "The forward projection looks corrupted — treat the chip schedule and "
        "any Wildcard/Triple-Captain recommendation below with suspicion:",
        "",
    ]
    lines += [f"- {w}" for w in warnings]
    return "\n".join(lines)


def _model_note() -> str:
    return (
        "## How the Bot Works\n\n"
        "All predictions run locally — no external AI APIs are called. "
        "GitHub Actions fetches fresh FPL data every run, re-scores all players, "
        "and solves the MILP. Nothing is hardcoded between gameweeks.\n\n"
        "**Scoring model:** FPL's own `ep_next` × P(start) × fixture-difficulty multiplier "
        "for the immediate GW. For subsequent GWs the base rate is a reliability-adjusted "
        "points-per-game — raw current-season PPG regressed toward a position / `ep_next` "
        "prior so a one- or two-game sample can't inflate the projection — then scaled by "
        "FDR. The result is a 6-GW forward projection per player, which the MILP uses to "
        "find the optimal squad and transfer.\n\n"
        "**DEFCON:** The training data includes CBIT, recoveries, and tackles from 2025-26 "
        "onward. Players with consistently high defensive activity score higher on the DC model "
        "sub-head, so DEFCON potential is captured indirectly. FPL's own `ep_next` also "
        "includes the DEFCON bonus in its expected-points calculation (60% weight here), "
        "so it's partially accounted for. The bot does not predict threshold-crossing "
        "probability explicitly — that would require match-level simulation."
    )


def _immediate_action(plan: dict, current_gw: int) -> str:
    lines = [f"## Immediate Action — GW{current_gw}", ""]

    chip = plan.get("chip")
    if chip:
        lines.append(f"**Chip:** {CHIP_LABELS.get(chip, chip)}  ")

    cap = plan.get("captain", {})
    vice = plan.get("vice", {})
    cap_xpts = cap.get("xpts", 0)
    lines += [
        f"**Captain:** {cap.get('name', '?')} ({cap_xpts:.2f} xPts → {cap_xpts * 2:.2f} effective with double)  ",
        f"**Vice:** {vice.get('name', '?')} ({vice.get('xpts', 0):.2f} xPts)  ",
    ]

    if plan.get("transfers_in"):
        for tin, tout in zip(plan["transfers_in"], plan["transfers_out"]):
            lines.append(
                f"**Transfer:** OUT {tout['name']} (£{tout.get('selling_price', '?')}m) "
                f"→ IN {tin['name']} (£{tin.get('cost', '?')}m)  "
            )
        if plan["hits"] > 0:
            lines.append(f"**Hits:** {plan['hits']} (−{plan.get('hit_cost', plan['hits'] * 4)} pts)  ")
    else:
        lines.append("**Transfer:** Roll (banking free transfer)  ")

    lines += [
        f"**Bank after:** £{plan.get('bank_after', 0)}m  ",
        f"**FT next GW:** {plan.get('ft_banked_next', 1)}  ",
    ]
    return "\n".join(lines)


def _squad_rationale(
    plan: dict, current_gw: int, gw_fore: pd.DataFrame, teams_by_id: dict,
) -> str:
    first = plan["gw_plan"][0]
    xi = first.get("starting_xi", [])
    bench = first.get("bench", [])
    cap = plan.get("captain", {})
    vice = plan.get("vice", {})

    # Rank per position among all candidates in forecast
    pos_rank: dict[int, list] = {}
    for el, row in gw_fore.iterrows():
        pos = int(row["position"])
        pos_rank.setdefault(pos, []).append((float(row["xpts"]), int(el)))
    for pos in pos_rank:
        pos_rank[pos].sort(reverse=True)

    def rank_of(element, pos):
        for r, (_, el) in enumerate(pos_rank.get(pos, []), 1):
            if el == element:
                return r
        return None

    lines = ["## Why the Bot Chose This Squad", ""]
    lines.append("### Starting XI")
    lines.append("")

    pos_order = {1: 0, 2: 1, 3: 2, 4: 3}
    for p in sorted(xi, key=lambda p: (pos_order.get(p["position"], 9), p["name"])):
        el = p["element"]
        pos = p["position"]
        pos_label = POS.get(pos, "?")
        xpts = float(gw_fore.at[el, "xpts"]) if el in gw_fore.index else 0.0
        p_start = float(gw_fore.at[el, "p_start"]) if el in gw_fore.index else 0.0
        team_id = int(gw_fore.at[el, "team"]) if el in gw_fore.index else 0
        team_name = teams_by_id.get(team_id, "?")
        rank = rank_of(el, pos)
        pool_size = len(pos_rank.get(pos, []))
        rank_str = f"#{rank}/{pool_size}" if rank else "?"

        tags = []
        if el == cap.get("element"):
            tags.append("**CAPTAIN**")
        elif el == vice.get("element"):
            tags.append("**Vice**")
        tag_str = f" [{', '.join(tags)}]" if tags else ""

        if el == cap.get("element"):
            note = (
                f"Highest projected return in the squad: {xpts:.2f} xPts "
                f"({xpts * 2:.2f} effective with captain double). "
                f"Ranked {rank_str} among {pos_label}s in the 150-player candidate pool. "
                f"Captain is always the highest-xPts player in the XI."
            )
        else:
            note = (
                f"{xpts:.2f} xPts for GW{current_gw}, ranked {rank_str} among {pos_label}s "
                f"in candidate pool. {team_name} fixture. P(start) {_pct(p_start)}."
            )

        lines.append(f"- **{p['name']}** ({pos_label}, £{p.get('cost', '?')}m){tag_str}: {note}")

    lines += [
        "",
        "### Bench",
        "",
        "*Bench picks are weighted at 10% in the MILP objective. The optimizer intentionally "
        "spends budget on the starting XI and uses bench slots for legal squad shape.*",
        "",
    ]

    for p in bench:
        el = p["element"]
        xpts = float(gw_fore.at[el, "xpts"]) if el in gw_fore.index else 0.0
        lines.append(
            f"- **{p['name']}** ({POS.get(p['position'], '?')}, £{p.get('cost', '?')}m): "
            f"{xpts:.2f} xPts. Budget saved here funds the premium XI picks."
        )

    return "\n".join(lines)


def _transfer_rationale(plan: dict, current_gw: int, gw_fore: pd.DataFrame) -> str:
    t_in = plan.get("transfers_in", [])
    t_out = plan.get("transfers_out", [])
    ft_next = plan.get("ft_banked_next", 1)

    if not t_in:
        return (
            f"## Transfer Decision\n\n"
            f"**Rolling the free transfer.** No single swap improves the 6-GW projected total "
            f"enough to justify spending the FT now. Banking gives {ft_next} FT(s) next GW "
            f"(valued at ~{ft_next * 1.5:.0f} expected pts in the planner). "
            f"Rolling is often optimal mid-season when the squad is healthy."
        )

    lines = ["## Transfer Decision", ""]

    for tin, tout in zip(t_in, t_out):
        el_in = tin["element"]
        el_out = tout["element"]
        xpts_in = float(gw_fore.at[el_in, "xpts"]) if el_in in gw_fore.index else 0.0
        xpts_out = float(gw_fore.at[el_out, "xpts"]) if el_out in gw_fore.index else 0.0
        gain = xpts_in - xpts_out

        lines += [
            f"**OUT:** {tout['name']} (£{tout.get('selling_price', '?')}m, {xpts_out:.2f} xPts GW{current_gw})",
            f"**IN:** {tin['name']} (£{tin.get('cost', '?')}m, {xpts_in:.2f} xPts GW{current_gw})",
            f"**Net this week:** {gain:+.2f} xPts",
            "",
        ]

        if gain >= 0:
            lines.append(
                f"{tin['name']} projects {xpts_in:.2f} xPts vs {tout['name']}'s {xpts_out:.2f} — "
                f"a {gain:.2f} xPts improvement this GW alone. The MILP confirmed this swap "
                f"also improves the full 6-GW plan after accounting for future fixtures and "
                f"the value of free transfers."
            )
        else:
            lines.append(
                f"The immediate GW gain is slightly negative but the MILP found a better "
                f"6-GW trajectory with {tin['name']} in the squad — either upcoming fixtures "
                f"heavily favour them, or {tout['name']}'s form trend is declining."
            )

    hits = plan.get("hits", 0)
    if hits > 0:
        lines.append(
            f"\n⚠️ **{hits} hit(s) required (−{plan.get('hit_cost', hits * 4)} pts).** "
            f"The planner only recommends hits when the projected 6-GW gain exceeds the penalty. "
            f"Skip and roll if you want to avoid the risk — the bot will adapt next week."
        )

    return "\n".join(lines)


def _notable_omissions(
    plan: dict, current_gw: int, gw_fore: pd.DataFrame,
    teams_by_id: dict, bp_by_id: dict,
    last_gw: Optional[int], last_gw_data: Optional[dict],
) -> str:
    first = plan["gw_plan"][0]
    squad_elements = {p["element"] for p in first.get("squad", [])}

    club_counts: dict[int, int] = {}
    for el in squad_elements:
        if el in gw_fore.index:
            c = int(gw_fore.at[el, "team"])
            club_counts[c] = club_counts.get(c, 0) + 1

    pos_min: dict[int, float] = {}
    for el in squad_elements:
        if el in gw_fore.index:
            pos = int(gw_fore.at[el, "position"])
            xpts = float(gw_fore.at[el, "xpts"])
            pos_min[pos] = min(pos_min.get(pos, 9999.0), xpts)

    xpts_sorted = gw_fore["xpts"].sort_values(ascending=False)

    def overall_rank(element):
        if element not in xpts_sorted.index:
            return None
        try:
            return int((xpts_sorted.index == element).argmax()) + 1
        except Exception:
            return None

    lines = ["## Players We Considered But Didn't Pick", ""]

    # --- Last GW top scorers not in squad ---
    if last_gw and last_gw_data:
        lines += [
            f"### GW{last_gw} Standout Performers — Why They're Not In Your Squad",
            "",
            f"*(A big GW score doesn't automatically trigger a transfer — the bot's 6-GW "
            f"forward model uses EWMA form features that smooth out single-match spikes. "
            f"One hot game shifts the model's view less than you'd expect.)*",
            "",
        ]

        notable = sorted(
            [(el, s) for el, s in last_gw_data.items() if s.get("total_points", 0) >= 8],
            key=lambda x: x[1].get("total_points", 0),
            reverse=True,
        )

        found = 0
        for el, stats in notable[:12]:
            if el in squad_elements:
                continue

            pts = stats.get("total_points", 0)
            goals = stats.get("goals_scored", 0)
            assists = stats.get("assists", 0)
            cs = stats.get("clean_sheets", 0)
            saves = stats.get("saves", 0)
            cbit = stats.get("clearances_blocks_interceptions", 0)

            bp = bp_by_id.get(el, {})
            name = bp.get("web_name", str(el))
            pos = int(bp.get("element_type", 3))
            pos_label = POS.get(pos, "?")
            team_id = int(bp.get("team", 0))
            team_name = teams_by_id.get(team_id, "?")
            cost = int(bp.get("now_cost", 0))

            perf = []
            if goals:
                perf.append(f"{goals} goal{'s' if goals > 1 else ''}")
            if assists:
                perf.append(f"{assists} assist{'s' if assists > 1 else ''}")
            if cs:
                perf.append("clean sheet")
            if saves >= 3:
                perf.append(f"{saves} saves")
            if cbit >= 10:
                perf.append(f"DEFCON bonus ({cbit} CBIT)")
            perf_str = f" ({', '.join(perf)})" if perf else ""

            if el in gw_fore.index:
                xpts = float(gw_fore.at[el, "xpts"])
                rank = overall_rank(el)
                rank_str = f"ranked #{rank} overall" if rank else "ranked outside pool"
            else:
                xpts = None
                rank_str = "outside the top-150 candidate pool"

            if xpts is None:
                reason = (
                    "Their 6-GW projected return sits outside the top-150 candidate pool. "
                    "The EWMA model hasn't built enough forward-looking form from this "
                    "performance to move them into contention yet."
                )
            elif club_counts.get(team_id, 0) >= 3:
                reason = (
                    f"The 3-player {team_name} cap is already maxed in the squad. "
                    f"Bringing them in would mean dropping another {team_name} player, "
                    f"which the MILP found to be a worse 6-GW outcome."
                )
            else:
                min_pos_xpts = pos_min.get(pos, 0.0)
                if xpts < min_pos_xpts:
                    reason = (
                        f"Forward projection: {xpts:.2f} xPts for GW{current_gw} ({rank_str}). "
                        f"This is below our lowest-ranked {pos_label} in the squad "
                        f"({min_pos_xpts:.2f} xPts). "
                        f"The EWMA form model smooths over single-match spikes — "
                        f"a big GW shifts the average less than the raw score suggests."
                    )
                else:
                    reason = (
                        f"Forward projection {xpts:.2f} xPts ({rank_str}) is competitive, "
                        f"but bringing them in at {_mp(cost)} would require dropping a player "
                        f"the MILP values more over the full 6-GW horizon."
                    )

            lines.append(
                f"- **{name}** ({pos_label}, {team_name}): "
                f"**{pts} pts in GW{last_gw}**{perf_str}. "
                f"Not transferred in — {reason}"
            )
            found += 1

        if not found:
            lines.append("*(All GW top scorers are already in your squad.)*")
        lines.append("")

    # --- Highest-projected players outside squad ---
    lines += ["### Highest-Projected Players Not In Your Squad", ""]

    not_in = gw_fore[~gw_fore.index.isin(squad_elements)].sort_values("xpts", ascending=False).head(8)

    for el, row in not_in.iterrows():
        pos = int(row["position"])
        xpts = float(row["xpts"])
        team_id = int(row["team"])
        team_name = teams_by_id.get(team_id, "?")
        cost = int(row["now_cost"])
        rank = overall_rank(int(el))
        rank_str = f"#{rank}" if rank else "?"

        if club_counts.get(team_id, 0) >= 3:
            reason = f"3-player {team_name} cap is maxed."
        elif xpts >= pos_min.get(pos, 0.0):
            reason = (
                f"{_mp(cost)} is hard to fit within £100m without dropping a player "
                f"the MILP values more over 6 GWs."
            )
        else:
            reason = f"Edged out by selected {POS.get(pos, '?')}s with better 6-GW projections."

        lines.append(
            f"- **{row.get('name', str(el))}** ({POS.get(pos, '?')}, {team_name}, "
            f"{_mp(cost)}, {xpts:.2f} xPts): ranked {rank_str} overall. {reason}"
        )

    return "\n".join(lines)


def _chip_schedule(plan: dict) -> str:
    chip_plan = plan.get("chip_plan", [])
    if not chip_plan:
        return ""
    lines = ["## Chip Schedule", "", "| GW | Chip | Est. Gain |", "|---:|---|---:|"]
    for cp in chip_plan:
        lines.append(f"| {cp['gw']} | {cp['chip_label']} | +{cp['expected_gain']:.1f} pts |")
    reason = plan.get("chip_reason", "")
    if reason:
        lines.append(f"\n*{reason}*")
    return "\n".join(lines)


def _starting_xi_section(plan: dict, current_gw: int) -> str:
    first = plan["gw_plan"][0] if plan.get("gw_plan") else {}
    xi = first.get("starting_xi", [])
    bench = first.get("bench", [])
    cap = plan.get("captain", {})
    if not xi:
        return ""

    lines = [f"## Starting XI — GW{current_gw}", ""]
    for pos_id in (1, 2, 3, 4):
        at_pos = [p for p in xi if p["position"] == pos_id]
        if at_pos:
            row = " | ".join(
                f"**{p['name']}**{'(C)' if p['element'] == cap.get('element') else ''}"
                for p in at_pos
            )
            lines.append(f"**{POS.get(pos_id, '?')}:** {row}  ")
    lines += ["", f"**Bench:** {' | '.join(p['name'] for p in bench)}"]
    return "\n".join(lines)
