#!/usr/bin/env python3
"""
Hourly live monitor — run via cron or Windows Task Scheduler.

Usage:
    python scripts/live_check.py [--gw GW] [--horizon HORIZON]

Exit codes:
    0  = no actionable changes
    1  = DGW/BGW detected or injury news for a likely-captain player

Schedule:
    Windows Task Scheduler: run every 60 minutes, trigger on battery/AC.
    Unix cron: 0 * * * * cd /path/to/fpl-auto && python scripts/live_check.py
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.live_monitor import LiveMonitor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="FPL live fixture/news monitor")
    parser.add_argument("--gw", type=int, default=None, help="Override current GW")
    parser.add_argument("--horizon", type=int, default=8,
                        help="GWs ahead to watch for fixture changes (default 8)")
    parser.add_argument("--no-force", action="store_true",
                        help="Allow HTTP cache (skips fresh fetch)")
    args = parser.parse_args()

    monitor = LiveMonitor(current_gw=args.gw, horizon=args.horizon)
    alert = monitor.run_once(force=not args.no_force)

    print(alert.summary())

    if alert.has_dgw_change:
        log.warning("ACTION REQUIRED: DGW change detected — re-run season_planner.")
    if alert.has_bgw_change:
        log.warning("ACTION REQUIRED: BGW change detected — re-run season_planner.")
    if alert.has_injury_news:
        log.info("HEADS UP: Injury/status update on one or more players.")

    return 1 if (alert.has_dgw_change or alert.has_bgw_change) else 0


if __name__ == "__main__":
    sys.exit(main())
