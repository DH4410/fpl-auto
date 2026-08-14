#!/usr/bin/env python3
"""
Post-GW transfer analysis and optional auto-apply.

Usage:
    python scripts/post_gw.py [--gw N] [--dry-run] [--auto]

    --gw N       Gameweek to plan FOR (next GW to be played).
                 If omitted, auto-detects from bootstrap (last finished GW + 1).
    --dry-run    Fetch and plan, but do not submit any transfers.
    --auto       Apply recommended transfer without confirmation prompt
                 (only if 0 hits required; skips if hits needed).

Credentials (read from environment / .env):
    FPL_EMAIL     defaults to Dimahuang8@gmail.com
    FPL_PASSWORD  required

What it does:
    1. Logs in, fetches your squad + bank.
    2. Runs the 6-GW rolling MILP planner on fresh data.
    3. Writes a report to reports/season_plan_latest.{md,csv}.
    4. Prints the recommended transfer.
    5. With --auto and 0 hits, submits the transfer with human-like delays.
"""
import argparse
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import fpl_auth
import fpl_api
from bot.updater import SeasonUpdater

DEFAULT_EMAIL = "dimahuang10@gmail.com"
REPORTS_DIR = Path(__file__).parent.parent / "reports"


def _pause(lo: float, hi: float) -> None:
    """Random sleep to mimic human timing between API calls."""
    t = random.uniform(lo, hi)
    print(f"  [waiting {t:.1f}s]")
    time.sleep(t)


def _emit_new_refresh_token() -> None:
    """Write the rotated refresh token to GITHUB_OUTPUT so the workflow can
    update the FPL_REFRESH_TOKEN secret automatically (prevents single-use expiry)."""
    new_rt = fpl_auth._last_refresh_token.get("value", "")
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if new_rt and github_output:
        with open(github_output, "a") as f:
            f.write(f"new_refresh_token={new_rt}\n")
        print(f"  [new refresh token written to GITHUB_OUTPUT for secret rotation]")


def _detect_next_gw(session) -> int:
    """Find the next GW to plan for (last finished + 1) from bootstrap."""
    bs = fpl_api.bootstrap(session)
    finished = [e for e in bs["events"] if e.get("finished")]
    if not finished:
        return 1
    return finished[-1]["id"] + 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Post-GW FPL analysis.")
    parser.add_argument("--gw", type=int,
                        help="Next GW to plan for. Omit to auto-detect from bootstrap.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and plan, skip API writes.")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-apply recommended transfer (0-hit only).")
    args = parser.parse_args()

    # --- Authenticate ---
    # Try refresh token first (works for Google-linked accounts, no password needed).
    token, session = None, None
    refresh_token = os.environ.get("FPL_REFRESH_TOKEN", "").strip()
    if refresh_token:
        try:
            print("Logging in via refresh token...")
            token, session = fpl_auth.refresh_login(refresh_token)
            print("Login OK (refresh token).")
        except Exception as e:
            print(f"Refresh token failed ({e}), falling back to password login...")

    if token is None:
        email = os.environ.get("FPL_EMAIL", DEFAULT_EMAIL)
        password = os.environ.get("FPL_PASSWORD") or input("FPL password: ").strip()
        if not password:
            print("ERROR: Set FPL_REFRESH_TOKEN or FPL_PASSWORD.")
            sys.exit(1)
        print(f"Logging in as {email}...")
        token, session = fpl_auth.login(email, password)
        print("Login OK (password).")

    _emit_new_refresh_token()
    _pause(4, 9)  # human pause after login

    # --- Resolve GW ---
    if args.gw:
        next_gw = args.gw
    else:
        print("Auto-detecting current gameweek...")
        next_gw = _detect_next_gw(session)
        print(f"  → planning for GW{next_gw}")

    _pause(2, 5)

    me = fpl_api.me(session, token)
    entry_id = me["player"]["entry"]
    print(f"Entry ID: {entry_id} | Planning for GW{next_gw}")

    _pause(2, 4)

    my_team = fpl_api.my_team(session, token, entry_id)
    entry_info = fpl_api.entry_info(session, token, entry_id)

    picks = my_team.get("picks", [])
    bank_tenths = entry_info.get("last_deadline_bank", 0)
    print(f"Squad: {len(picks)} players | Bank: £{bank_tenths / 10:.1f}m")

    _pause(3, 7)

    # --- Run planner ---
    updater = SeasonUpdater(horizon=6, verbose=True)
    plan = updater.run(current_gw=next_gw, my_team=my_team, entry_info=entry_info)

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
    print(f"\nRecommended: OUT {t_out['name']} (£{t_out.get('selling_price', '?')}m) "
          f"→ IN {t_in['name']} (£{t_in.get('cost', '?')}m)")
    if hits > 0:
        print(f"WARNING: {hits} hit(s) required (-{plan.get('hit_cost', hits*4)} pts).")

    if args.dry_run:
        print("[DRY RUN] Skipping transfer submission.")
        return

    if hits > 0 and args.auto:
        print("Auto mode: skipping hit transfer — review manually.")
        return

    if not args.auto:
        ans = input("Apply this transfer? [y/N]: ").strip().lower()
        if ans != "y":
            print("Cancelled.")
            return

    _pause(6, 14)  # longer human pause before submitting

    sell_price = int(t_out.get("selling_price", 0) * 10)
    buy_price  = int(t_in.get("cost", 0) * 10)

    print("Submitting transfer...")
    result = fpl_api.transfer(
        session, token, entry_id,
        event=next_gw,
        transfers=[{
            "element_in":     t_in["element"],
            "element_out":    t_out["element"],
            "purchase_price": buy_price,
            "selling_price":  sell_price,
        }],
        chip=chip,
    )
    print(f"Transfer accepted: {result}")

    _pause(3, 6)  # pause after write

    if chip:
        print(f"Chip activated: {chip}")

    # After transfer: update picks (captain, XI order, bench order)
    gw_plan = plan.get("gw_plan", [])
    if gw_plan:
        first = gw_plan[0]
        starting_xi = first.get("starting_xi", [])
        bench = first.get("bench", [])
        cap_el = plan.get("captain", {}).get("element")
        vc_el = None
        # Vice is the highest-xPts non-captain outfield starter
        outfield = [p for p in starting_xi if p["position"] != 1 and p["element"] != cap_el]
        if outfield:
            forecasts_gw = plan.get("gw_plan", [{}])[0]
            vc_el = first.get("vice", {}).get("element")

        xi_sorted = sorted(starting_xi, key=lambda p: p["position"])
        bench_gkp = [p for p in bench if p["position"] == 1]
        bench_out = sorted([p for p in bench if p["position"] != 1],
                           key=lambda p: p.get("cost", 0), reverse=True)
        bench_ordered = bench_gkp + bench_out

        picks_payload = []
        for i, p in enumerate(xi_sorted + bench_ordered):
            picks_payload.append({
                "element": p["element"],
                "position": i + 1,
                "is_captain": p["element"] == cap_el,
                "is_vice_captain": p["element"] == vc_el,
            })

        if picks_payload:
            _pause(3, 7)
            cap_name = plan.get("captain", {}).get("name", "?")
            print(f"Setting captain to {cap_name} and updating bench order...")
            result2 = fpl_api.update_picks(session, token, entry_id, picks=picks_payload)
            print(f"Picks updated: {result2}")

    print("\nDone. Verify at fantasy.premierleague.com/my-team")


if __name__ == "__main__":
    main()
