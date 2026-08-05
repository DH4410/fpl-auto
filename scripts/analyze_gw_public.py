#!/usr/bin/env python3
"""
Public-only post-GW reflection analysis. No FPL credentials needed.

Uses only public FPL API endpoints (no auth required) to fetch the user's
GW picks, live results, and compare against pre-computed predictions.
Writes a markdown reflection report to reports/reflect_gw{N}.md and
reports/reflect_latest.md.

Usage:
    FPL_ENTRY_ID=<your_entry_id> python scripts/analyze_gw_public.py [--gw N]

Set FPL_ENTRY_ID as a GitHub Actions secret (Settings → Secrets → Actions).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://fantasy.premierleague.com/api"
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"
RESEARCH_DIR = Path(__file__).resolve().parents[1] / "research"
ENTRY_ID = os.environ.get("FPL_ENTRY_ID", "")


def fetch(url: str) -> dict:
    r = requests.get(url, timeout=20, headers={"User-Agent": "fpl-auto-bot/1.0"})
    r.raise_for_status()
    return r.json()


def find_last_finished_gw(bootstrap: dict) -> int | None:
    finished = [e for e in bootstrap["events"] if e.get("finished")]
    return finished[-1]["id"] if finished else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Public post-GW analysis.")
    parser.add_argument("--gw", type=int, help="Gameweek to analyze (default: last finished)")
    args = parser.parse_args()

    entry_id = ENTRY_ID.strip()
    if not entry_id:
        print("ERROR: Set FPL_ENTRY_ID environment variable.")
        sys.exit(1)

    print("Fetching bootstrap...")
    bootstrap = fetch(f"{BASE_URL}/bootstrap-static/")

    gw = args.gw or find_last_finished_gw(bootstrap)
    if not gw:
        print("No finished gameweeks yet — nothing to analyze.")
        sys.exit(0)

    print(f"Analyzing GW{gw} for entry {entry_id}...")

    players_by_id = {p["id"]: p for p in bootstrap["elements"]}
    teams_by_id   = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    pos_map        = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

    live       = fetch(f"{BASE_URL}/event/{gw}/live/")
    picks_data = fetch(f"{BASE_URL}/entry/{entry_id}/event/{gw}/picks/")

    live_by_id = {e["id"]: e.get("stats", {}) for e in live["elements"]}

    # Load GW1 predictions if available
    predicted_by_element: dict = {}
    squad_file = RESEARCH_DIR / "gw1_squad_2026.json"
    if squad_file.exists():
        sq = json.loads(squad_file.read_text(encoding="utf-8"))
        for p in sq.get("squad_detail", []):
            predicted_by_element[p["element"]] = p.get("blended_xpts")

    picks         = picks_data.get("picks", [])
    entry_history = picks_data.get("entry_history", {})
    total_actual  = entry_history.get("points", 0)
    active_chip   = picks_data.get("active_chip")

    starters = [p for p in picks if p["position"] <= 11]
    bench    = [p for p in picks if p["position"] > 11]

    xi_preds = [predicted_by_element.get(p["element"]) for p in starters]
    total_predicted = sum(x for x in xi_preds if x is not None)

    def stats_str(stats: dict) -> str:
        parts = [f"{stats.get('minutes',0)}m"]
        if stats.get("goals_scored"):  parts.append(f"{stats['goals_scored']}G")
        if stats.get("assists"):        parts.append(f"{stats['assists']}A")
        if stats.get("clean_sheets"):   parts.append("CS")
        if stats.get("bonus"):          parts.append(f"+{stats['bonus']}b")
        return " ".join(parts)

    def player_row(pick: dict, bench_mark: str = "") -> str:
        el    = pick["element"]
        p     = players_by_id.get(el, {})
        stats = live_by_id.get(el, {})
        name  = p.get("web_name", str(el))
        pos   = pos_map.get(p.get("element_type", 0), "?")
        mult  = pick.get("multiplier", 1)
        pts   = stats.get("total_points", 0) * mult
        pred  = predicted_by_element.get(el)
        pred_s = f"{pred:.2f}" if pred is not None else "—"
        diff_s = f"{pts - pred:+.1f}" if pred is not None else "—"
        cap    = " ©" if pick.get("is_captain") else (" ®" if pick.get("is_vice_captain") else "")
        return f"| {name}{cap}{bench_mark} | {pos} | {pts} | {pred_s} | {diff_s} | {stats_str(stats)} |"

    diff = total_actual - total_predicted
    now  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# FPL Auto — GW{gw} Post-Match Reflection",
        f"",
        f"*Generated: {now}*",
        f"{f'*Active chip: {active_chip}*' if active_chip else ''}",
        f"",
        f"## Summary",
        f"",
        f"| Metric | Value |",
        f"|--------|------:|",
        f"| Actual GW Points | **{total_actual}** |",
        f"| Predicted (xPts) | {total_predicted:.1f} |",
        f"| Difference | {diff:+.1f} |",
        f"",
        f"## Player Breakdown",
        f"",
        f"| Player | Pos | Pts | xPts | Diff | Stats |",
        f"|--------|-----|----:|-----:|-----:|-------|",
    ]
    for p in starters:
        lines.append(player_row(p))
    lines.append("| *— Bench —* | | | | | |")
    for p in bench:
        lines.append(player_row(p, " (B)"))

    lines += [
        f"",
        f"## Notes",
        f"",
        f"- Predicted values are blended xPts from the pre-GW ML model (40% model + 60% FPL ep_next).",
        f"- Predictions currently only available for GW1 squad; later GWs will add per-GW reports.",
        f"- To get transfer recommendations for next GW, run `python scripts/post_gw.py --gw {gw+1}` locally.",
    ]

    report = "\n".join(lines)
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / f"reflect_gw{gw}.md").write_text(report, encoding="utf-8")
    (REPORTS_DIR / "reflect_latest.md").write_text(report, encoding="utf-8")
    print(f"Wrote reports/reflect_gw{gw}.md and reports/reflect_latest.md")
    print(f"GW{gw}: Actual={total_actual}  Predicted={total_predicted:.1f}  Diff={diff:+.1f}")


if __name__ == "__main__":
    main()
