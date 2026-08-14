#!/usr/bin/env python3
"""
Regenerate research/gw1_squad_2026.json from the current model.

Run this locally before triggering apply_gw1.yml to ensure the squad file
matches the latest model output:

    python scripts/generate_gw1_squad.py

No FPL credentials needed — uses bootstrap-static (public endpoint) only.
Requires all bot/ dependencies (pandas, scikit-learn, pulp, etc.).
"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.updater import SeasonUpdater

OUT = Path(__file__).parent.parent / "research" / "gw1_squad_2026.json"
POS_NAMES = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def main() -> None:
    print("Running optimizer for GW1 (pre-season, no auth needed)…")
    updater = SeasonUpdater()
    plan = updater.run(current_gw=1, my_team=None)

    gw1 = plan["gw_plan"][0]
    squad = gw1["squad"]          # 15 × {element, name, cost, position}
    xi_els = {p["element"] for p in gw1["starting_xi"]}
    captain_el = gw1["captain"]["element"]
    vice_el = gw1["vice"]["element"]

    squad_detail = [
        {
            "element": p["element"],
            "name": p["name"],
            "position": str(p["position"]),
            "position_name": POS_NAMES[p["position"]],
            "cost": p["cost"],
            "blended_xpts": 0.0,   # see reports/season_plan_latest.md for actual values
            "ep_next": 0.0,
            "in_xi": p["element"] in xi_els,
            "is_captain": p["element"] == captain_el,
            "is_vice_captain": p["element"] == vice_el,
        }
        for p in squad
    ]

    # FPL picks: positions 1-11 starters (sorted by position type), 12-15 bench.
    # Bench GKP must come before outfield bench players (position 12).
    starters = sorted(gw1["starting_xi"], key=lambda p: p["position"])
    bench_gkp = [p for p in gw1["bench"] if p["position"] == 1]
    bench_out = [p for p in gw1["bench"] if p["position"] != 1]
    picks = [
        {
            "element": p["element"],
            "position": i,
            "is_captain": p["element"] == captain_el,
            "is_vice_captain": p["element"] == vice_el,
        }
        for i, p in enumerate(starters, start=1)
    ] + [
        {
            "element": p["element"],
            "position": i,
            "is_captain": False,
            "is_vice_captain": False,
        }
        for i, p in enumerate(bench_gkp + bench_out, start=12)
    ]

    # All 15 players are "transfers in" for the initial squad selection.
    transfers = [
        {"element_in": p["element"], "purchase_price": int(round(p["cost"] * 10))}
        for p in squad
    ]

    doc = {
        "generated_date": str(date.today()),
        "season": "2026-27",
        "gameweek": 1,
        "model": "FPLPointsPredictor (element-summary API, 40% ML + 60% ep_next)",
        "squad_cost_gpm": round(sum(p["cost"] for p in squad), 1),
        "expected_xi_pts": gw1["xi_xpts"],
        "captain_element": captain_el,
        "captain_name": gw1["captain"]["name"],
        "vice_captain_element": vice_el,
        "vice_captain_name": gw1["vice"]["name"],
        "squad_detail": squad_detail,
        "picks": picks,
        "transfers": transfers,
    }

    OUT.write_text(json.dumps(doc, indent=2))

    xi_names = [p["name"] for p in squad_detail if p["in_xi"]]
    bench_names = [p["name"] for p in squad_detail if not p["in_xi"]]
    print(f"\nWritten: {OUT}")
    print(f"Captain : {doc['captain_name']} (element {captain_el})")
    print(f"Vice    : {doc['vice_captain_name']} (element {vice_el})")
    print(f"XI      : {', '.join(xi_names)}")
    print(f"Bench   : {', '.join(bench_names)}")
    print(f"Cost    : £{doc['squad_cost_gpm']:.1f}m  |  XI xPts: {gw1['xi_xpts']:.2f}")
    print("\nNext: commit research/gw1_squad_2026.json, then trigger apply_gw1.yml.")


if __name__ == "__main__":
    main()
