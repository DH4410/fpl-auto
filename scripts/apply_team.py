#!/usr/bin/env python3
"""
Apply the bot's GW1 squad recommendation to your FPL account.

Usage:
    python scripts/apply_team.py [--dry-run] [--auto]

Credentials (set as environment variables):
    FPL_EMAIL     — defaults to dimahuang10@gmail.com
    FPL_PASSWORD  — required

What it does:
    1. Loads research/gw1_squad_2026.json (pre-computed optimal squad).
    2. Prints the 15-player squad for review.
    3. On confirmation (or --auto), submits the initial pick and sets captain.

For subsequent GWs, use scripts/post_gw.py instead.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

import fpl_auth
import fpl_api

SQUAD_FILE = Path(__file__).resolve().parents[1] / "research" / "gw1_squad_2026.json"
DEFAULT_EMAIL = "dimahuang10@gmail.com"


def load_squad() -> dict:
    if not SQUAD_FILE.exists():
        print(f"ERROR: Squad file not found: {SQUAD_FILE}")
        sys.exit(1)
    with SQUAD_FILE.open() as f:
        return json.load(f)


def print_squad(squad: dict) -> None:
    print(f"\nFPL GW{squad['gameweek']} Squad — {squad['season']}")
    print(f"Model : {squad['model']}")
    print(f"Cost  : £{squad['squad_cost_gpm']:.1f}m | Expected XI pts: {squad['expected_xi_pts']:.1f}")
    print(f"Capt  : {squad['captain_name']}  |  Vice: {squad['vice_captain_name']}")
    print()
    print(f"{'Pos':<5} {'Player':<18} {'Team':<16} {'Price':>6} {'xPts':>6}  Role")
    print("-" * 66)
    for p in squad["squad_detail"]:
        print(f"{p['pos']:<5} {p['name']:<18} {p['team']:<16} {p['price']:>6.1f} {p['blended_xpts']:>6.3f}  {p['role']}")
    print()


def apply_squad(squad: dict, dry_run: bool) -> None:
    email = os.environ.get("FPL_EMAIL", DEFAULT_EMAIL)
    password = os.environ.get("FPL_PASSWORD") or input("FPL password: ").strip()
    if not password:
        print("ERROR: FPL_PASSWORD is required.")
        sys.exit(1)

    print(f"Logging in as {email}...")
    token, session = fpl_auth.login(email, password)
    print("Login OK.")

    me = fpl_api.me(session, token)
    entry_id = me["player"]["entry"]
    print(f"Entry ID: {entry_id}")

    if dry_run:
        print("[DRY RUN] Skipping API writes.")
        return

    gw = squad["gameweek"]
    print(f"Submitting initial squad (GW{gw}, {len(squad['transfers'])} players)...")
    result = fpl_api.transfer(
        session, token, entry_id,
        event=gw,
        transfers=squad["transfers"],
        chip=None,
    )
    print(f"Transfers: {result}")

    print("Setting starting XI, captain, bench order...")
    result = fpl_api.update_picks(
        session, token, entry_id,
        picks=squad["picks"],
        chips=[],
    )
    print(f"Picks: {result}")
    print("\nDone. Verify at fantasy.premierleague.com/my-team")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply GW1 squad to FPL.")
    parser.add_argument("--dry-run", action="store_true", help="Print squad, skip API calls")
    parser.add_argument("--auto", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    squad = load_squad()
    print_squad(squad)

    if not args.auto and not args.dry_run:
        ans = input("Apply this squad to your FPL account? [y/N]: ").strip().lower()
        if ans != "y":
            print("Cancelled.")
            return

    apply_squad(squad, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
