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


def _emit_new_refresh_token() -> None:
    """Write the rotated refresh token to GITHUB_OUTPUT so the workflow can
    update the FPL_REFRESH_TOKEN secret automatically (prevents single-use expiry)."""
    new_rt = fpl_auth._last_refresh_token.get("value", "")
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if new_rt and github_output:
        with open(github_output, "a") as f:
            f.write(f"new_refresh_token={new_rt}\n")
        print(f"  [new refresh token written to GITHUB_OUTPUT for secret rotation]")

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
    import time, random

    refresh_token = os.environ.get("FPL_REFRESH_TOKEN", "").strip()
    if refresh_token:
        print("Logging in via refresh token...")
        token, session = fpl_auth.refresh_login(refresh_token)
        print("Login OK (refresh token).")
    else:
        email = os.environ.get("FPL_EMAIL", DEFAULT_EMAIL)
        password = os.environ.get("FPL_PASSWORD") or input("FPL password: ").strip()
        if not password:
            print("ERROR: Set FPL_REFRESH_TOKEN or FPL_PASSWORD.")
            sys.exit(1)
        print(f"Logging in as {email}...")
        token, session = fpl_auth.login(email, password)
        print("Login OK.")

    # Write the new (rotated) refresh token to GitHub Actions output so the
    # workflow can update the FPL_REFRESH_TOKEN secret automatically.
    _emit_new_refresh_token()

    # Human-like pause after login
    t = random.uniform(4, 9)
    print(f"  [waiting {t:.1f}s]")
    time.sleep(t)

    me = fpl_api.me(session, token)
    entry_id = me["player"]["entry"]
    print(f"Entry ID: {entry_id}")

    if dry_run:
        print("[DRY RUN] Skipping API writes.")
        return

    # Fetch current squad and diff against the target.
    my_team_data = fpl_api.my_team(session, token, entry_id)
    current_picks = my_team_data.get("picks", [])
    current_ids = {p["element"] for p in current_picks}
    target_ids = {t["element_in"] for t in squad["transfers"]}

    to_remove = [p for p in current_picks if p["element"] not in target_ids]
    to_add = [t for t in squad["transfers"] if t["element_in"] not in current_ids]

    gw = squad["gameweek"]

    if not to_remove and not to_add:
        print("Squad already matches the target — no player swaps needed.")
    else:
        selling = {p["element"]: p["selling_price"] for p in current_picks}

        # Fetch live prices — player prices change daily in preseason and FPL
        # rejects any transfer where purchase_price doesn't match the current price.
        print("Fetching live player prices from FPL...")
        import requests as _req
        _bs = _req.get("https://fantasy.premierleague.com/api/bootstrap-static/", timeout=15).json()
        live_prices = {el["id"]: el["now_cost"] for el in _bs["elements"]}

        if to_remove:
            # Batch all swaps in a single API call.
            swaps = [
                {
                    "element_in": a["element_in"],
                    "element_out": r["element"],
                    "purchase_price": live_prices.get(a["element_in"], a["purchase_price"]),
                    "selling_price": selling[r["element"]],
                }
                for a, r in zip(to_add, to_remove)
            ]
            # Log price check so we can see if anything changed from the stored JSON.
            for s in swaps:
                stored = next((t["purchase_price"] for t in squad["transfers"] if t["element_in"] == s["element_in"]), None)
                if stored and stored != s["purchase_price"]:
                    print(f"  [price updated: element {s['element_in']} stored={stored} live={s['purchase_price']}]")
            print(f"Swapping {len(swaps)} player(s) to match target squad...")
            t2 = random.uniform(6, 14)
            print(f"  [waiting {t2:.1f}s before submitting...]")
            time.sleep(t2)
            result = fpl_api.transfer(
                session, token, entry_id,
                event=gw,
                transfers=swaps,
                chip=None,
            )
            print(f"Swaps accepted: {result}")
            t3 = random.uniform(3, 7)
            print(f"  [waiting {t3:.1f}s]")
            time.sleep(t3)
        else:
            # to_add but no to_remove means we have extra players — shouldn't happen normally
            print(f"WARNING: {len(to_add)} players to add but nothing to remove. Check squad state.")

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
