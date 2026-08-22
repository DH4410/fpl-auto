#!/usr/bin/env python3
"""Runtime entrypoint for the weekly FPL orchestrator.

The original implementation lives in ``weekly_orchestrator_core.py``. This
entrypoint keeps that mature state machine intact while correcting live-GW
calendar semantics, adding a hard execution lock, live scoring, post-deadline
elite-manager scouting, and human-readable post-GW lessons.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.live_gameweek import build_live_summary  # noqa: E402
from bot.orchestrator_runtime import (  # noqa: E402
    active_live_event,
    hours_until_next_deadline,
    is_international_break as runtime_is_break,
    latest_started_gw,
    next_future_event,
)
from bot.post_match_analyzer_extended import ReflectivePostMatchAnalyzer  # noqa: E402
from bot.top100_post_deadline import (  # noqa: E402
    LockedTop100Tracker,
    PostDeadlineTopManagerScout,
)
from scripts import weekly_orchestrator_core as core  # noqa: E402


# Planning/execution must use a future deadline, not ``event.finished``.
core.next_event = next_future_event
core.hours_until_deadline = hours_until_next_deadline
core.is_international_break = lambda bootstrap: runtime_is_break(  # noqa: E731
    bootstrap, break_days=core.BREAK_DAYS
)
core.PostMatchAnalyzer = ReflectivePostMatchAnalyzer
# Do not let the later post-GW snapshot replace the richer post-deadline sample.
core.Top100Tracker = LockedTop100Tracker


def _fetch_public_entry_gw(entry_id: int, gw: int, session) -> dict:
    """Full public GW picks body (picks + entry_history + chip)."""
    if not entry_id or not gw:
        return {}
    try:
        response = session.get(
            f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gw}/picks/",
            timeout=20,
        )
        if not response.ok:
            core.log.warning("Public GW data for entry %s GW%d -> %d", entry_id, gw, response.status_code)
            return {}
        return response.json()
    except Exception as exc:  # noqa: BLE001
        core.log.warning("Could not fetch public GW data for entry %s (%s)", entry_id, exc)
        return {}


def _log_live_and_elite_watch(
    *,
    bootstrap: dict,
    state: dict,
    session,
    dry_run: bool,
    squad_gw: int,
    picks_body: dict,
) -> None:
    """Log my provisional score and compare with a post-deadline elite sample."""
    picks = picks_body.get("picks") or []
    if not picks:
        return

    try:
        live_data = core.fpl_api.event_live(session, squad_gw)
    except Exception as exc:  # noqa: BLE001
        core.log.warning("Could not fetch GW%d live points (%s)", squad_gw, exc)
        return

    history = picks_body.get("entry_history") or {}
    live = build_live_summary(
        gw=squad_gw,
        picks=picks,
        live_data=live_data,
        bootstrap=bootstrap,
        transfer_cost=history.get("event_transfers_cost") or 0,
    )
    hit_text = (
        f" after {live['transfer_cost']:.0f} hit point(s)"
        if live["transfer_cost"]
        else ""
    )
    core.log.info(
        "GW%d LIVE SCORE (provisional): %.0f pts%s.",
        squad_gw,
        live["net"],
        hit_text,
    )

    captain = live.get("captain")
    if captain:
        core.log.info(
            "Captain: %s — %.0f raw pts, x%d = %.0f counted pts, %d minutes.",
            captain.get("name", captain.get("element")),
            captain.get("raw_points", 0),
            captain.get("multiplier", 0),
            captain.get("counted_points", 0),
            captain.get("minutes", 0),
        )
    contributors = live.get("top_contributors") or []
    if contributors:
        core.log.info(
            "Top live contributors: %s",
            ", ".join(
                f"{p['name']} {p['counted_points']:.0f}"
                for p in contributors[:5]
            ),
        )

    # Other managers' picks are fetched only after the same GW deadline. The
    # scout has its own hard deadline guard, so a manual/forced run cannot use
    # these teams to influence a still-open deadline.
    scout = PostDeadlineTopManagerScout()
    result = scout.snapshot_locked_teams(
        gw=squad_gw,
        bootstrap=bootstrap,
        session=session,
        persist=not dry_run,
    )
    if result.get("blocked_pre_deadline"):
        core.log.info("Elite watch blocked: %s", result.get("reason"))
        return

    snapshot = result.get("snapshot") or {}
    if result.get("created") and not dry_run:
        core.commit_state(f"auto: GW{squad_gw} post-deadline elite snapshot")

    comparison = scout.live_comparison(snapshot, live_data, my_score=live["net"])
    if comparison.get("sample_size"):
        core.log.info(
            "TOP-MANAGER LIVE WATCH (post-deadline locked sample, n=%d): "
            "avg %.1f, median %.1f, high %.1f; my %.1f (%+.1f vs avg).",
            comparison["sample_size"],
            comparison["average"],
            comparison["median"],
            comparison["high"],
            comparison.get("my_score", live["net"]),
            comparison.get("vs_average", 0.0),
        )

    strategy = snapshot.get("strategy") or {}
    if squad_gw == 1 and strategy:
        core.log.info(
            "GW1 note: overall ranks are initially tied/unstable, so elite-strategy "
            "signals are provisional; the locked-team and live-score data are still valid."
        )
    for observation in (strategy.get("observations") or [])[:3]:
        core.log.info("ELITE STRATEGY: %s", observation)


def stage_monitoring(bootstrap: dict, state: dict, session, dry_run: bool) -> None:
    """Monitor the live squad, score and post-deadline elite strategy."""
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

    picks_body = _fetch_public_entry_gw(entry_id, squad_gw, session) if squad_gw else {}
    my_picks = picks_body.get("picks") or []
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

    if live_ev is not None and int(live_ev.get("id") or 0) == squad_gw:
        _log_live_and_elite_watch(
            bootstrap=bootstrap,
            state=state,
            session=session,
            dry_run=dry_run,
            squad_gw=squad_gw,
            picks_body=picks_body,
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


# The dedicated Top-100 workflow runs frequently but is intentionally cheap:
# before a deadline there is no started GW; after the first successful snapshot
# it simply loads the saved locked teams and makes no manager-picks requests.
def stage_top100(bootstrap: dict, dry_run: bool) -> dict:
    gw = latest_started_gw(bootstrap) or 0
    if not gw:
        core.log.info("No passed gameweek deadline yet — elite manager scout is idle.")
        return {}

    scout = PostDeadlineTopManagerScout()
    result = scout.snapshot_locked_teams(
        gw=gw,
        bootstrap=bootstrap,
        session=core.public_session(),
        persist=not dry_run,
    )
    if result.get("blocked_pre_deadline"):
        core.log.info("Post-deadline scout blocked: %s", result.get("reason"))
        return {}

    snapshot = result.get("snapshot") or {}
    core.log.info(
        "=== POST-DEADLINE ELITE SNAPSHOT — GW%d === %d standings, %d locked squads sampled",
        gw,
        snapshot.get("n_standings", 0),
        snapshot.get("n_picks_sampled", 0),
    )
    if result.get("created"):
        core.log.info(
            "Elite teams became available only after the GW%d deadline; snapshot is "
            "for live comparison and future-GW learning, never same-deadline copying.",
            gw,
        )
        if not dry_run:
            core.commit_state(f"auto: GW{gw} post-deadline elite snapshot")
    else:
        core.log.info("GW%d locked elite snapshot already exists — no refetch needed.", gw)

    for observation in ((snapshot.get("strategy") or {}).get("observations") or [])[:3]:
        core.log.info("ELITE STRATEGY: %s", observation)
    return snapshot.get("summary") or {"gw": gw}


core.stage_top100 = stage_top100


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
