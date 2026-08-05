#!/usr/bin/env python3
"""
Post-GW transfer analysis and optional auto-apply.

Run after each gameweek finishes to get the bot's transfer recommendation
and optionally submit it.

Usage:
    python scripts/post_gw.py --gw <N> [--dry-run] [--auto]

    --gw N       Gameweek to plan FOR (the NEXT gameweek to be played)
    --dry-run    Fetch and plan, but do not submit any transfers
    --auto       Apply recommended transfer without confirmation prompt
                 (skips hits — only applies if 0 hits needed)

Credentials:
    FPL_EMAIL     env var (default: dimahuang10@gmail.com)
    FPL_PASSWORD  env var (required)

What it does:
    1. Logs in and fetches your current squad + bank from the FPL API.
    2. Runs the 6-GW rolling MILP planner on fresh data.
    3. Writes a report to reports/season_plan_latest.{md,csv}.
    4. Prints the recommended transfer (or "Roll" if none needed).
    5. With --auto and 0 hits, submits the transfer automatically.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fpl_auth
import fpl_api
from bot.updater import SeasonUpdater

DEFAULT_EMAIL = "dimahuang10@gmail.com"
REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"


def main() -> None:
    parser = argparse.ArgumentParser(description="Post-GW FPL analysis.")
    parser.add_argument("--gw", type=int, required=True,
                        help="Next gameweek to plan for (e.g. 2 after GW1 finishes)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and plan, but do not call any write endpoints")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-apply the 1st recommended transfer (only if 0 hits)")
    args = parser.parse_args()

    # --- Authenticate ---
    email = os.environ.get("FPL_EMAIL", DEFAULT_EMAIL)
    password = os.environ.get("FPL_PASSWORD") or input("FPL password: ").strip()
    if not password:
        print("ERROR: FPL_PASSWORD is required.")
        sys.exit(1)

    print(f"Logging in as {email}...")
    token, session = fpl_auth.login(email, password)

    me = fpl_api.me(session, token)
    entry_id = me["player"]["entry"]
    print(f"Entry ID: {entry_id} | Planning for GW{args.gw}")

    # --- Fetch current squad ---
    my_team = fpl_api.my_team(session, token, entry_id)
    entry_info = fpl_api.entry_info(session, token, entry_id)

    picks = my_team.get("picks", [])
    bank_tenths = entry_info.get("last_deadline_bank", 0)
    print(f"Current squad: {len(picks)} players | Bank: £{bank_tenths / 10:.1f}m")

    # --- Run planner ---
    updater = SeasonUpdater(horizon=6, verbose=True)
    plan = updater.run(current_gw=args.gw, my_team=my_team, entry_info=entry_info)

    # --- Print report ---
    paths = plan.get("report_paths", {})
    md_path = paths.get("markdown", "")
    if md_path and Path(md_path).exists():
        print(f"\nReport written to: {md_path}")
        print("=" * 60)
        print(Path(md_path).read_text(encoding="utf-8"))
        print("=" * 60)

    # --- Transfer summary ---
    transfers_in  = plan.get("transfers_in", [])
    transfers_out = plan.get("transfers_out", [])
    hits = plan.get("hits", 0)
    chip = plan.get("chip")

    if chip:
        print(f"\nCHIP RECOMMENDED: {chip}")
        print(f"Reason: {plan.get('chip_reason', '')}")

    if not transfers_in:
        print("\nNo transfer recommended — rolling free transfer.")
        return

    t_in  = transfers_in[0]
    t_out = transfers_out[0]
    print(f"\nRecommended transfer: OUT {t_out['name']} (£{t_out['selling_price']}m) "
          f"-> IN {t_in['name']} (£{t_in['cost']}m)")
    if hits > 0:
        print(f"WARNING: {hits} hit(s) required (-{plan['hit_cost']} pts). Careful before applying.")

    if args.dry_run:
        print("[DRY RUN] Skipping transfer submission.")
        return

    # --- Confirm and apply ---
    if hits > 0 and args.auto:
        print("Auto mode: skipping hit transfer (review manually).")
        return

    if not args.auto:
        ans = input("Apply this transfer? [y/N]: ").strip().lower()
        if ans != "y":
            print("Cancelled.")
            return

    sell_price = int(t_out.get("selling_price", 0) * 10)
    buy_price  = int(t_in.get("cost", 0) * 10)

    print("Submitting transfer...")
    result = fpl_api.transfer(
        session, token, entry_id,
        event=args.gw,
        transfers=[{
            "element_in":     t_in["element"],
            "element_out":    t_out["element"],
            "purchase_price": buy_price,
            "selling_price":  sell_price,
        }],
        chip=chip,
    )
    print(f"Transfer accepted: {result}")

    if chip:
        print(f"Chip activated: {chip}")

    print("\nDone. Verify at fantasy.premierleague.com/my-team")


if __name__ == "__main__":
    main()
