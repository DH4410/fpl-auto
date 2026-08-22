#!/usr/bin/env python3
"""Runtime entrypoint for the weekly FPL orchestrator.

The original implementation lives in ``weekly_orchestrator_core.py``. This
entrypoint keeps that mature state machine intact while correcting the live-GW
calendar semantics, adding a hard live-gameweek execution lock, and surfacing
human-readable post-GW lessons.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.orchestrator_runtime import (  # noqa: E402
    active_live_event,
    hours_until_next_deadline,
    is_international_break as runtime_is_break,
    latest_started_gw,
    next_future_event,
)
from bot.post_match_analyzer_extended import ReflectivePostMatchAnalyzer  # noqa: E402
from scripts import weekly_orchestrator_core as core  # noqa: E402


# Planning/execution must use a future deadline, not ``event.finished``.
core.next_event = next_future_event
core.hours_until_deadline = hours_until_next_deadline
core.is_international_break = lambda bootstrap: runtime_is_break(  # noqa: E731
    bootstrap, break_days=core.BREAK_DAYS
)
core.PostMatchAnalyzer = ReflectivePostMatchAnalyzer


def stage_monitoring(bootstrap: dict, state: dict, session, dry_run: bool) -> None:
    """Monitor the squad from the latest GW whose deadline has passed."""
    core.log.info("=== MONITORING ===")
    entry_id = core.resolve_entry_id()
    if not entry_id:
        core.warn_no_entry_id("MONITORING")

    squad_gw = latest_started_gw(bootstrap) or 0
    live_ev = active_live_event(bootstrap)
    if live_ev is not None:
        core.log.info(
            "GW%d is currently LIVE/LOCKED — no transfers, picks or chip actions "
            "will be submitted until FPL marks the gameweek finished.",
            int(live_ev["id"]),
        )

    my_picks = core.fetch_public_picks(entry_id, squad_gw, session) if squad_gw else []
    my_ids = {int(p["element"]) for p in my_picks if p.get("element") is not None}

    if my_ids:
        core.log.info("Monitoring squad from GW%d: %d players.", squad_gw, len(my_ids))
    else:
        core.log.warning(
            "Squad unknown (entry=%s, latest started GW=%s) — availability checks "
            "are running against an empty squad.",
            entry_id or "unset",
            squad_gw,
        )

    elements = {int(e["id"]): e for e in bootstrap.get("elements", [])}
    ideas = list(state.get("idea_list") or [])
    known = {i["element"] for i in ideas if i.get("action") == "transfer_out"}

    new_flags = []
    for el_id in my_ids:
        element = elements.get(el_id)
        if not element or (element.get("status") or "a") == "a" or el_id in known:
            continue
        chance = element.get("chance_of_playing_next_round")
        new_flags.append({
            "action": "transfer_out",
            "element": el_id,
            "name": element.get("web_name", str(el_id)),
            "reason": (element.get("news") or "").strip()
                      or f"Status '{element.get('status')}', chance {chance}.",
            "priority": 1.0,
        })

    if new_flags:
        for flag in new_flags:
            core.log.info("NEW FLAG: %s — %s", flag["name"], flag["reason"])
        ideas.extend(new_flags)
        state["idea_list"] = ideas
        if not dry_run:
            core.save_state(state)
            core.commit_state("auto: monitoring flags")
        core.email_alerts.send_alert(
            f"{len(new_flags)} new availability flag(s) in squad",
            "\n".join(f"- {f['name']}: {f['reason']}" for f in new_flags),
        )
    else:
        core.log.info("No new availability flags in squad. No action taken.")

    next_ev = next_future_event(bootstrap)
    hours = hours_until_next_deadline(bootstrap)
    if next_ev is not None and hours is not None:
        core.log.info("Next deadline: GW%d in %.1f hours.", int(next_ev["id"]), hours)


core.stage_monitoring = stage_monitoring

# Defense in depth: even a forced EXECUTE stage or a future stage-detection bug
# cannot submit transfers/picks/chips while the current gameweek remains live.
_original_execute = core.stage_execute


def stage_execute(bootstrap: dict, state: dict, dry_run: bool) -> dict:
    live_ev = active_live_event(bootstrap)
    if live_ev is not None:
        target = next_future_event(bootstrap)
        target_text = f"GW{int(target['id'])}" if target is not None else "the next GW"
        core.log.warning(
            "SAFETY LOCK: GW%d is still live. EXECUTE for %s is blocked — no "
            "transfers, picks or chips will be submitted until the live GW finishes.",
            int(live_ev["id"]),
            target_text,
        )
        return {}
    return _original_execute(bootstrap, state, dry_run)


core.stage_execute = stage_execute

_original_post_gw = core.stage_post_gw_analysis


def stage_post_gw_analysis(bootstrap: dict, state: dict, session, dry_run: bool) -> dict:
    """Run the existing post-GW stage and surface its new learning messages."""
    result = _original_post_gw(bootstrap, state, session, dry_run)
    messages = result.get("reflection_messages", []) if isinstance(result, dict) else []
    if not messages:
        return result

    for message in messages:
        core.log.info("LESSON: %s", message)

    if not dry_run:
        try:
            gw = int(result.get("gw", 0))
            body = "What the bot learned from the completed gameweek:\n\n" + "\n\n".join(
                f"- {message}" for message in messages
            )
            core.email_alerts.send_alert(f"GW{gw} lessons — what the bot got wrong", body)
        except Exception as exc:  # noqa: BLE001
            core.log.warning("Could not send post-GW lessons email (%s)", exc)
    return result


core.stage_post_gw_analysis = stage_post_gw_analysis


if __name__ == "__main__":
    sys.exit(core.main())
