#!/usr/bin/env python3
"""Final runtime layer: freeze elite manager cohort before deadline.

Imports the tested weekly runtime, replaces only the elite-manager selector, and
keeps all live scoring / execution locks / reflection behaviour unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.top100_cohort_scout import CohortLockedTopManagerScout  # noqa: E402
from scripts import weekly_orchestrator as runtime  # noqa: E402

# Functions defined in weekly_orchestrator resolve this module-global class at
# call time, so swapping it here also fixes the live MONITORING elite watch.
runtime.PostDeadlineTopManagerScout = CohortLockedTopManagerScout


def stage_top100(bootstrap: dict, dry_run: bool) -> dict:
    """Freeze pre-deadline ranks; fetch their locked teams only post-deadline."""
    scout = CohortLockedTopManagerScout()

    # During the final hour before a deadline, capture standings only. Repeated
    # 15-minute runs refresh the cohort closer to lock; no manager picks are
    # requested here.
    next_ev = runtime.next_future_event(bootstrap)
    hours = runtime.hours_until_next_deadline(bootstrap)
    if next_ev is not None and hours is not None and 0 < hours <= scout.COHORT_WINDOW_HOURS:
        gw = int(next_ev["id"])
        result = scout.capture_pre_deadline_cohort(
            gw=gw,
            bootstrap=bootstrap,
            session=runtime.core.public_session(),
            persist=not dry_run,
        )
        if result.get("captured"):
            cohort = result.get("cohort") or {}
            runtime.core.log.info(
                "PRE-DEADLINE ELITE COHORT — GW%d: froze %d Overall standings "
                "entries %.1f minutes before lock. No competitor picks fetched.",
                gw,
                cohort.get("n_standings", 0),
                hours * 60.0,
            )
            if not dry_run:
                runtime.core.commit_state(f"auto: GW{gw} pre-deadline elite cohort")
        else:
            runtime.core.log.info("Elite cohort not refreshed: %s", result.get("reason"))
        return {"gw": gw, "cohort_only": True}

    # Once a deadline has passed, inspect only the managers frozen above.
    gw = runtime.latest_started_gw(bootstrap) or 0
    if not gw:
        runtime.core.log.info("No passed gameweek deadline yet — elite manager scout is idle.")
        return {}

    result = scout.snapshot_locked_teams(
        gw=gw,
        bootstrap=bootstrap,
        session=runtime.core.public_session(),
        persist=not dry_run,
    )
    if result.get("blocked_pre_deadline"):
        runtime.core.log.info("Post-deadline scout blocked: %s", result.get("reason"))
        return {}
    if result.get("no_predeadline_cohort"):
        runtime.core.log.info("ELITE STRATEGY SKIPPED: %s", result.get("reason"))
        return {"gw": gw, "elite_strategy_skipped": True}
    if result.get("retry_later"):
        runtime.core.log.info("Elite scout waiting for FPL propagation: %s", result.get("reason"))
        return {}

    snapshot = result.get("snapshot") or {}
    runtime.core.log.info(
        "=== POST-DEADLINE ELITE SNAPSHOT — GW%d === %d pre-selected standings, "
        "%d locked squads sampled",
        gw,
        snapshot.get("n_standings", 0),
        snapshot.get("n_picks_sampled", 0),
    )
    if result.get("created"):
        runtime.core.log.info(
            "These managers were selected from Overall standings BEFORE GW%d locked; "
            "their teams were fetched only AFTER the deadline.",
            gw,
        )
        if not dry_run:
            runtime.core.commit_state(f"auto: GW{gw} post-deadline elite snapshot")
    else:
        runtime.core.log.info("GW%d locked elite snapshot already exists — no refetch needed.", gw)

    for observation in ((snapshot.get("strategy") or {}).get("observations") or [])[:4]:
        runtime.core.log.info("ELITE STRATEGY: %s", observation)
    return snapshot.get("summary") or {"gw": gw}


runtime.stage_top100 = stage_top100
runtime.core.stage_top100 = stage_top100


if __name__ == "__main__":
    sys.exit(runtime.core.main())
