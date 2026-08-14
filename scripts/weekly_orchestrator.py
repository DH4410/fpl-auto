#!/usr/bin/env python3
"""
Weekly FPL Automation Orchestrator

State machine:
  POST_GW_ANALYSIS   — runs once the last GW is fully scored (all fixtures
                       finished): compares actual vs forecast, records
                       injuries/suspensions, builds the idea list.
  MONITORING         — daily check during GW week (injuries, news). Read-only.
  PRE_DEADLINE_PLAN  — 18-36h before the next deadline: run the MILP through
                       the pre-deadline simulator and freeze an approved plan.
  EXECUTE            — 0.5-18h before the deadline: submit the approved plan
                       one transfer at a time, then set picks/chip.
  INTERNATIONAL_BREAK — next deadline is >14 days away; no write actions.

Run by GitHub Actions every 2h (replaces post_gw.py --auto in post_gw.yml).

Usage:
    python scripts/weekly_orchestrator.py [--dry-run] [--auto]
                                          [--force-stage STAGE]
                                          [--stage transfer_window]

Flags:
    --auto           Accepted for workflow compatibility; the orchestrator is
                     autonomous by default.
    --dry-run        Run every read and every decision, skip all API writes.
    --force-stage    Force a stage instead of deriving it from the calendar.
    --stage          Run a single sub-task only. Currently: transfer_window.

Safety
------
Every authentication and every write is wrapped; failures raise an email alert
via bot.email_alerts and exit non-zero rather than continuing blind. State is
committed to git immediately after a successful execute so a re-run two hours
later cannot submit the same transfers twice.

All FPL deadline_time values are UTC. Every comparison here is UTC — no BST
offset is hardcoded anywhere.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import pandas as pd  # noqa: E402
import requests  # noqa: E402

import fpl_api  # noqa: E402
import fpl_auth  # noqa: E402
from bot import email_alerts  # noqa: E402
from bot.post_match_analyzer import PostMatchAnalyzer  # noqa: E402
from bot.top100_tracker import Top100Tracker  # noqa: E402
from bot.transfer_window_monitor import TransferWindowMonitor  # noqa: E402

log = logging.getLogger("weekly_orchestrator")

DATA_DIR = ROOT / "data"
STATE_FILE = DATA_DIR / "orchestrator_state.json"
PRE_DEADLINE_DIR = DATA_DIR / "pre_deadline"

DEFAULT_STATE = {
    "last_analyzed_gw": 0,
    "last_simulated_gw": 0,
    "last_executed_gw": 0,
    "approved_plan": None,
    "execute_target_utc": None,
    "last_deep_research_gw": 0,
}

# Stage names
POST_GW_ANALYSIS = "POST_GW_ANALYSIS"
MONITORING = "MONITORING"
PRE_DEADLINE_PLAN = "PRE_DEADLINE_PLAN"
EXECUTE = "EXECUTE"
INTERNATIONAL_BREAK = "INTERNATIONAL_BREAK"
STAGES = (POST_GW_ANALYSIS, MONITORING, PRE_DEADLINE_PLAN, EXECUTE, INTERNATIONAL_BREAK)

# Timing windows (hours before the next deadline)
PLAN_WINDOW = (18.0, 36.0)
EXECUTE_WINDOW = (0.5, 18.0)
#: A gap larger than this before the next deadline is an international break.
BREAK_DAYS = 14

# Execute-stage pacing
TRANSFER_GAP_RANGE_MIN = (4.0, 13.0)        # minutes between transfers
MIN_MINUTES_BEFORE_DEADLINE = 30.0

DEFAULT_EMAIL = "dimahuang8@gmail.com"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state() -> dict:
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULT_STATE)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not parse state file (%s) — starting from defaults", exc)
        return dict(DEFAULT_STATE)
    merged = dict(DEFAULT_STATE)
    if isinstance(state, dict):
        merged.update(state)
    return merged


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    log.info("State saved: analyzed=%s simulated=%s executed=%s",
             state.get("last_analyzed_gw"), state.get("last_simulated_gw"),
             state.get("last_executed_gw"))


def commit_state(message: str) -> None:
    """Commit data/ back to the repo so state survives the CI runner.

    Without this the EXECUTE stage would re-fire on the next 2-hourly run and
    submit the same transfers again. Only active inside GitHub Actions; local
    runs leave the working tree alone.
    """
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        log.info("Not in GitHub Actions — skipping state commit.")
        return
    try:
        subprocess.run(["git", "config", "user.name", "FPL Auto Bot"],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "bot@fplauto.local"],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run(["git", "add", "data/", "reports/"],
                       cwd=ROOT, check=True, capture_output=True)
        staged = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                cwd=ROOT, capture_output=True)
        if staged.returncode == 0:
            log.info("No state changes to commit.")
            return
        subprocess.run(["git", "commit", "-m", f"{message} [skip ci]"],
                       cwd=ROOT, check=True, capture_output=True)
        # Other workflows write to the same branch; rebase before pushing.
        subprocess.run(["git", "pull", "--rebase", "--autostash"],
                       cwd=ROOT, check=False, capture_output=True)
        subprocess.run(["git", "push"], cwd=ROOT, check=True, capture_output=True)
        log.info("State committed and pushed: %s", message)
    except Exception as exc:  # noqa: BLE001
        msg = (f"State commit/push failed ({type(exc).__name__}: {exc}) — "
               f"state persists locally only; risk of re-submission on next CI run")
        log.warning(msg)
        email_alerts.send_alert("FPL Auto: state commit failed (re-submission risk)", msg)


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def _parse_deadline(value: str) -> datetime:
    """FPL deadline_time is UTC, serialised with a trailing Z."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def next_event(bootstrap: dict) -> dict | None:
    """The next gameweek that has not finished."""
    upcoming = [
        e for e in bootstrap.get("events", [])
        if not e.get("finished") and e.get("deadline_time")
    ]
    return upcoming[0] if upcoming else None


def hours_until_deadline(bootstrap: dict) -> float | None:
    ev = next_event(bootstrap)
    if not ev:
        return None
    delta = _parse_deadline(ev["deadline_time"]) - datetime.now(timezone.utc)
    return delta.total_seconds() / 3600.0


def is_international_break(bootstrap: dict) -> bool:
    """True when the next deadline is more than BREAK_DAYS away."""
    events = bootstrap.get("events", [])
    now_utc = datetime.now(timezone.utc)
    upcoming = [e for e in events if not e.get("finished") and e.get("deadline_time")]
    if not upcoming:
        return False
    deadline = _parse_deadline(upcoming[0]["deadline_time"])
    days_until = (deadline - now_utc).days
    return days_until > BREAK_DAYS


def get_last_finished_gw(bootstrap: dict) -> int | None:
    finished = [e for e in bootstrap.get("events", []) if e.get("finished")]
    return finished[-1]["id"] if finished else None


def is_gw_fully_scored(gw: int, session: requests.Session) -> bool:
    """True once every fixture in ``gw`` has finished.

    The fixtures endpoint is used rather than the live endpoint: live element
    stats carry no per-match state, whereas each fixture exposes ``finished``
    and ``finished_provisional``. A GW is only safe to analyse once no match
    is still in play.
    """
    try:
        fixtures = fpl_api.fixtures(session, event=gw)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fetch GW%d fixtures (%s) — assuming not scored", gw, exc)
        return False
    if not fixtures:
        return False
    return all(fx.get("finished") or fx.get("finished_provisional") for fx in fixtures)


def determine_stage(bootstrap: dict, state_file: dict) -> str:
    """Derive the stage from the calendar and what has already been done.

    Ordering note: a pending post-GW analysis is checked *before* the
    international-break test. Analysis is entirely read-only, and the GW
    immediately preceding a break is exactly the one worth analysing. The
    break test then blocks the two stages that write to the live account.
    """
    last_gw = get_last_finished_gw(bootstrap)
    if last_gw and int(state_file.get("last_analyzed_gw", 0)) < last_gw:
        return POST_GW_ANALYSIS

    if is_international_break(bootstrap):
        return INTERNATIONAL_BREAK

    ev = next_event(bootstrap)
    if not ev:
        return MONITORING  # season over

    next_gw = int(ev["id"])
    hours = hours_until_deadline(bootstrap)
    if hours is None:
        return MONITORING

    if PLAN_WINDOW[0] <= hours <= PLAN_WINDOW[1]:
        if int(state_file.get("last_simulated_gw", 0)) < next_gw:
            return PRE_DEADLINE_PLAN
        return MONITORING

    if EXECUTE_WINDOW[0] <= hours < EXECUTE_WINDOW[1]:
        simulated = int(state_file.get("last_simulated_gw", 0)) >= next_gw
        executed = int(state_file.get("last_executed_gw", 0)) >= next_gw
        if simulated and not executed:
            return EXECUTE
        if not simulated and not executed:
            # Missed the planning window (e.g. a CI outage) — plan now, and the
            # next run inside the execute window will submit it.
            return PRE_DEADLINE_PLAN
        return MONITORING

    return MONITORING


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def authenticate() -> tuple[str, requests.Session]:
    """Refresh-token login with password fallback. Raises on total failure."""
    refresh_token = os.environ.get("FPL_REFRESH_TOKEN", "").strip()
    if refresh_token:
        try:
            log.info("Authenticating via refresh token...")
            token, session = fpl_auth.refresh_login(refresh_token)
            log.info("Login OK (refresh token).")
            _emit_new_refresh_token()
            return token, session
        except Exception as exc:  # noqa: BLE001
            log.warning("Refresh token login failed (%s) — trying password.", exc)

    email = os.environ.get("FPL_EMAIL", DEFAULT_EMAIL)
    password = os.environ.get("FPL_PASSWORD", "")
    if not password:
        raise RuntimeError("No FPL_REFRESH_TOKEN worked and FPL_PASSWORD is unset.")
    log.info("Authenticating as %s (password)...", email)
    token, session = fpl_auth.login(email, password)
    log.info("Login OK (password).")
    _emit_new_refresh_token()
    return token, session


def _emit_new_refresh_token() -> None:
    """Publish the rotated refresh token so the workflow can update the secret.

    Only the token value is written to GITHUB_OUTPUT; it is never printed.
    """
    new_rt = fpl_auth._last_refresh_token.get("value", "")
    github_output = os.environ.get("GITHUB_OUTPUT", "")
    if new_rt and github_output:
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"new_refresh_token={new_rt}\n")
        log.info("Rotated refresh token written to GITHUB_OUTPUT for secret rotation.")


def public_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (fpl-auto orchestrator)"})
    return s


# ---------------------------------------------------------------------------
# Forecast snapshots (so post-GW deltas compare against a genuine pre-GW view)
# ---------------------------------------------------------------------------

def _forecast_snapshot_path(gw: int) -> Path:
    return PRE_DEADLINE_DIR / f"forecast_gw{gw}.csv"


def save_forecast_snapshot(forecasts: pd.DataFrame, gw: int) -> None:
    """Store the pre-deadline forecast rows for ``gw``.

    The post-GW analyzer needs the xPts that were expected *before* the GW was
    played. Re-running the forecaster afterwards would produce a view built
    from post-GW data, so the snapshot is written at planning time.
    """
    try:
        if forecasts is None or forecasts.empty or "gw" not in forecasts.columns:
            return
        rows = forecasts[forecasts["gw"] == gw][["element", "gw", "xpts"]]
        if rows.empty:
            return
        _forecast_snapshot_path(gw).parent.mkdir(parents=True, exist_ok=True)
        rows.to_csv(_forecast_snapshot_path(gw), index=False)
        log.info("Saved pre-deadline forecast snapshot for GW%d (%d rows)", gw, len(rows))
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not save forecast snapshot for GW%d (%s)", gw, exc)


def load_forecast_snapshot(gw: int) -> pd.DataFrame:
    path = _forecast_snapshot_path(gw)
    if not path.exists():
        log.info("No pre-GW forecast snapshot for GW%d — performance deltas "
                 "will be skipped.", gw)
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read forecast snapshot for GW%d (%s)", gw, exc)
        return pd.DataFrame()


def resolve_entry_id() -> int:
    """My FPL entry id from the environment.

    Returns 0 when unset. Callers must treat 0 as "cannot identify my squad"
    and say so loudly: an empty squad makes the injury and suspension checks
    silently pass, which looks identical to a clean bill of health.
    """
    raw = (os.environ.get("FPL_ENTRY_ID") or "").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        log.warning("FPL_ENTRY_ID is set but is not an integer — ignoring.")
        return 0


def warn_no_entry_id(stage: str) -> None:
    msg = (f"{stage}: FPL_ENTRY_ID is not set, so the squad could not be "
           f"identified. Injury and suspension checks cannot flag your own "
           f"players and will report nothing. Set FPL_ENTRY_ID as a repo secret.")
    log.error(msg)
    email_alerts.send_alert("FPL_ENTRY_ID not configured", msg)


def fetch_public_picks(entry_id: int, gw: int, session: requests.Session) -> list[dict]:
    """My squad for a finished GW via the public picks endpoint (no auth)."""
    if not entry_id:
        return []
    try:
        r = session.get(
            f"https://fantasy.premierleague.com/api/entry/{entry_id}/event/{gw}/picks/",
            timeout=15,
        )
        if not r.ok:
            log.warning("Public picks for entry %s GW%d -> %d", entry_id, gw, r.status_code)
            return []
        return r.json().get("picks", []) or []
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fetch public picks (%s)", exc)
        return []


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------

def stage_transfer_window(bootstrap: dict, state: dict, dry_run: bool) -> list[dict]:
    """Check for new signings while the window is open. Returns idea entries."""
    monitor = TransferWindowMonitor()
    if not monitor.is_window_open():
        log.info("Transfer window closed (%s) — nothing to monitor.",
                 monitor.__class__.__name__)
        return []

    log.info("Transfer window open — %.1f days remaining.", monitor.days_remaining())
    signings = monitor.check_new_signings(bootstrap)
    for s in signings:
        log.info("New element: %s (%s, %s, £%.1fm) ep_next=%.2f worth=%s — %s",
                 s["name"], s["team"], s["position"], s["cost"],
                 s["ep_next"], s["worth_considering"], s["reason"])

    ideas = monitor.to_ideas(signings)
    if not dry_run:
        # Always persist and commit the baseline. check_new_signings() seeds it
        # on the first run; if that seed never reaches the repo, every CI run
        # would re-seed and no signing would ever be reported.
        monitor.update_known_ids(bootstrap)
        commit_state("auto: transfer window element baseline")

    if ideas:
        state.setdefault("signing_ideas", [])
        existing = {i["element"] for i in state["signing_ideas"]}
        state["signing_ideas"] = state["signing_ideas"] + [
            i for i in ideas if i["element"] not in existing
        ]
        if not dry_run:
            save_state(state)
        worthwhile = ", ".join(i["name"] for i in ideas)
        email_alerts.send_alert(
            "New signing(s) worth considering",
            f"The transfer window monitor flagged: {worthwhile}\n\n"
            + "\n".join(f"- {i['name']}: {i['reason']}" for i in ideas),
        )
    return ideas


def stage_top100(bootstrap: dict, dry_run: bool) -> dict:
    """Standalone top-100 snapshot for the most recently finished GW."""
    last_gw = get_last_finished_gw(bootstrap)
    if not last_gw:
        log.info("No finished GW yet — nothing to snapshot.")
        return {}
    log.info("=== TOP100 — GW%d ===", last_gw)
    if dry_run:
        log.info("[DRY RUN] would snapshot the top 100 for GW%d", last_gw)
        return {}
    summary = Top100Tracker().fetch_and_store(last_gw, session=public_session())
    log.info("Snapshot summary: %s", json.dumps(summary)[:400])
    trends = Top100Tracker().analyze_trends()
    log.info("Trends across %s GWs", trends.get("gws_analyzed"))
    commit_state(f"auto: top-100 snapshot GW{last_gw}")
    return summary


def stage_post_gw_analysis(bootstrap: dict, state: dict, session: requests.Session,
                           dry_run: bool) -> dict:
    """Analyse the gameweek that just finished."""
    last_gw = get_last_finished_gw(bootstrap)
    if not last_gw:
        log.info("No finished GW yet — nothing to analyse.")
        return {}

    if not is_gw_fully_scored(last_gw, session):
        log.info("GW%d is flagged finished but fixtures are not all complete — "
                 "waiting for the next run.", last_gw)
        return {}

    log.info("=== POST_GW_ANALYSIS — GW%d ===", last_gw)
    live_data = fpl_api.event_live(session, last_gw)
    entry_id = resolve_entry_id()
    if not entry_id:
        warn_no_entry_id("POST_GW_ANALYSIS")
    my_picks = fetch_public_picks(entry_id, last_gw, session)
    if entry_id and not my_picks:
        log.warning("Entry %s returned no picks for GW%d — squad-specific "
                    "flags will be empty this run.", entry_id, last_gw)
    forecasts = load_forecast_snapshot(last_gw)

    result = PostMatchAnalyzer().analyze(
        gw=last_gw,
        live_data=live_data,
        bootstrap=bootstrap,
        my_picks=my_picks,
        forecasts=forecasts,
    )

    log.info("Analysis: %d idea(s). %s", len(result["idea_list"]), result["notes"])
    for idea in result["idea_list"][:10]:
        log.info("  [%.2f] %s %s — %s", idea["priority"], idea["action"],
                 idea["name"], idea["reason"])

    # The human-readable reflection report (reports/reflect_gw{N}.md) used to be
    # produced by post_gw.yml's public-analysis step. That step is gone, so it is
    # triggered here instead — once per GW, which is its natural cadence.
    if not dry_run:
        try:
            subprocess.run(
                [sys.executable, "scripts/analyze_gw_public.py", "--gw", str(last_gw)],
                cwd=ROOT, check=False, capture_output=True, timeout=300,
            )
            log.info("Public reflection report refreshed for GW%d.", last_gw)
        except Exception as exc:  # noqa: BLE001
            log.warning("Public reflection report failed (%s)", exc)

    # Top-100 snapshot is best-effort; a failure must not block the stage.
    try:
        summary = Top100Tracker().fetch_and_store(last_gw, session=public_session())
        log.info("Top-100 snapshot: %s", json.dumps(summary)[:300])
    except Exception as exc:  # noqa: BLE001
        log.warning("Top-100 tracking failed (%s)", exc)

    # Deep research every 2 GWs — wider forward view, chip/captaincy/differential insights.
    last_deep_gw = int(state.get("last_deep_research_gw", 0))
    if last_gw - last_deep_gw >= 2:
        try:
            from bot import data_collector
            from bot.deep_researcher import DeepResearcher
            fixtures = data_collector.fetch_fixtures()
            dr = DeepResearcher().research(last_gw, bootstrap, fixtures, my_picks)
            log.info("Deep research: top captain=%s, best chip window=GW%s",
                     dr["captaincy_top3"][0]["name"] if dr["captaincy_top3"] else "?",
                     dr["chip_windows"][0]["gw"] if dr["chip_windows"] else "?")
            email_alerts.send_alert(
                f"GW{last_gw} deep research complete", dr["email_body"]
            )
            if not dry_run:
                state["last_deep_research_gw"] = int(last_gw)
        except Exception as exc:  # noqa: BLE001
            log.warning("Deep research failed (%s) — continuing without it", exc)

    if not dry_run:
        state["last_analyzed_gw"] = int(last_gw)
        state["idea_list"] = result["idea_list"]
        save_state(state)
        commit_state(f"auto: GW{last_gw} post-match analysis")
    return result


def stage_monitoring(bootstrap: dict, state: dict, session: requests.Session,
                     dry_run: bool) -> None:
    """Daily read-only check for new injuries/suspensions in my squad."""
    log.info("=== MONITORING ===")
    entry_id = resolve_entry_id()
    if not entry_id:
        warn_no_entry_id("MONITORING")
    last_gw = get_last_finished_gw(bootstrap) or 0
    my_picks = fetch_public_picks(entry_id, last_gw, session) if last_gw else []
    my_ids = {p["element"] for p in my_picks}
    if not my_ids:
        log.warning("Squad unknown (entry=%s, last finished GW=%s) — availability "
                    "checks are running against an empty squad.", entry_id or "unset", last_gw)

    elements = {int(e["id"]): e for e in bootstrap.get("elements", [])}
    ideas = list(state.get("idea_list") or [])
    known = {i["element"] for i in ideas if i.get("action") == "transfer_out"}

    new_flags = []
    for el_id in my_ids:
        el = elements.get(el_id)
        if not el or (el.get("status") or "a") == "a" or el_id in known:
            continue
        chance = el.get("chance_of_playing_next_round")
        new_flags.append({
            "action": "transfer_out",
            "element": el_id,
            "name": el.get("web_name", str(el_id)),
            "reason": (el.get("news") or "").strip()
                      or f"Status '{el.get('status')}', chance {chance}.",
            # Fresh news about a player I own is the most urgent signal there is.
            "priority": 1.0,
        })

    if new_flags:
        for f in new_flags:
            log.info("NEW FLAG: %s — %s", f["name"], f["reason"])
        ideas.extend(new_flags)
        state["idea_list"] = ideas
        if not dry_run:
            save_state(state)
            commit_state("auto: monitoring flags")
        email_alerts.send_alert(
            f"{len(new_flags)} new availability flag(s) in squad",
            "\n".join(f"- {f['name']}: {f['reason']}" for f in new_flags),
        )
    else:
        log.info("No new availability flags in squad. No action taken.")

    hours = hours_until_deadline(bootstrap)
    if hours is not None:
        log.info("Next deadline in %.1f hours.", hours)


def stage_pre_deadline_plan(bootstrap: dict, state: dict, dry_run: bool) -> dict:
    """Run the planner and freeze an approved plan for the upcoming GW."""
    ev = next_event(bootstrap)
    if not ev:
        log.info("No upcoming gameweek — nothing to plan.")
        return {}
    next_gw = int(ev["id"])
    log.info("=== PRE_DEADLINE_PLAN — GW%d ===", next_gw)

    token, session = authenticate()
    me = fpl_api.me(session, token)
    entry_id = me["player"]["entry"]
    my_team = fpl_api.my_team(session, token, entry_id)
    entry_info = fpl_api.entry_info(session, token, entry_id)
    log.info("Entry %s: %d players, bank £%.1fm",
             entry_id, len(my_team.get("picks", [])),
             entry_info.get("last_deadline_bank", 0) / 10.0)

    # Imported here rather than at module scope: the planner pulls in pulp and
    # scipy, which the light single-stage workflows do not install.
    from bot.pre_deadline_simulator import PreDeadlineSimulator
    from bot.updater import SeasonUpdater, build_current_state

    updater = SeasonUpdater(horizon=6)
    plan = updater.run(current_gw=next_gw, my_team=my_team, entry_info=entry_info)
    current_state = build_current_state(bootstrap, my_team, next_gw, entry_info)

    # Rebuild the forecast table the planner used, so the simulator can value
    # the proposed transfers and the post-GW analyzer gets a genuine pre-GW view.
    forecasts = _rebuild_forecasts(updater, bootstrap, next_gw, current_state)
    save_forecast_snapshot(forecasts, next_gw)

    idea_list = list(state.get("idea_list") or []) + list(state.get("signing_ideas") or [])
    decision = PreDeadlineSimulator(horizon=6).simulate(
        idea_list=idea_list,
        forecasts=forecasts,
        current_state=current_state,
        next_gw=next_gw,
        plan=plan,
    )

    decision["picks_payload"] = build_picks_payload(plan)
    decision["entry_id"] = entry_id

    # Pre-commit a randomised execution time so EXECUTE stage never sleeps in CI.
    # The target is drawn [0.5h, 18h] before the deadline, clamped so all
    # per-transfer gaps plus 40 min of safety slack still clear the deadline.
    deadline_utc = _parse_deadline(ev["deadline_time"])
    n_xfers = len(decision.get("approved_transfers") or [])
    exec_buffer_h = (max(0, n_xfers - 1) * TRANSFER_GAP_RANGE_MIN[1] + 40.0) / 60.0
    hours_until = (deadline_utc - datetime.now(timezone.utc)).total_seconds() / 3600.0
    hi_h = max(0.5, min(18.0, hours_until - exec_buffer_h))
    target_h_before = random.uniform(0.5, hi_h)
    decision["execute_target_utc"] = (deadline_utc - timedelta(hours=target_h_before)).isoformat()

    log.info("Decision: %d transfer(s), chip=%s, net %+.2f pts",
             len(decision["approved_transfers"]), decision["approved_chip"],
             decision["expected_net_gain"])
    log.info("Reasoning: %s", decision["reasoning"])
    log.info("Execution target: %s (%.1fh before deadline)",
             decision["execute_target_utc"], target_h_before)

    if not decision["approved_transfers"] and not decision["approved_chip"]:
        log.info("HOLD — no good transfer this GW; the free transfer rolls.")

    if not dry_run:
        state["last_simulated_gw"] = next_gw
        state["approved_plan"] = decision
        save_state(state)
        commit_state(f"auto: GW{next_gw} pre-deadline plan")
    return decision


def _rebuild_forecasts(updater, bootstrap: dict, next_gw: int,
                       current_state: dict) -> pd.DataFrame:
    """Reproduce the forecaster output for valuation/snapshot purposes."""
    try:
        from bot import data_collector
        fixtures = data_collector.fetch_fixtures()
        ml_xpts = None
        if updater._predictor is not None:
            ml_xpts = updater._compute_ml_xpts(bootstrap, next_gw) or None
        return updater.forecaster.forecast(
            bootstrap=bootstrap,
            fixtures=fixtures,
            current_gw=next_gw,
            owned_ids=current_state["squad"],
            ml_xpts=ml_xpts,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not rebuild forecast table (%s) — transfer valuation "
                    "will fall back to 0.0 gain", exc)
        return pd.DataFrame()


def build_picks_payload(plan: dict) -> list[dict]:
    """Starting XI / bench / captain payload for /my-team/ from the GW plan."""
    gw_plan = plan.get("gw_plan") or []
    if not gw_plan:
        return []
    first = gw_plan[0]
    starting_xi = first.get("starting_xi", [])
    bench = first.get("bench", [])
    cap_el = (plan.get("captain") or {}).get("element")
    vc_el = (first.get("vice") or {}).get("element")

    xi_sorted = sorted(starting_xi, key=lambda p: p["position"])
    bench_gkp = [p for p in bench if p["position"] == 1]
    bench_out = sorted([p for p in bench if p["position"] != 1],
                       key=lambda p: p.get("cost", 0), reverse=True)

    payload = []
    for i, p in enumerate(xi_sorted + bench_gkp + bench_out):
        payload.append({
            "element": p["element"],
            "position": i + 1,
            "is_captain": p["element"] == cap_el,
            "is_vice_captain": p["element"] == vc_el,
        })
    return payload


def _transfer_gap_minutes(deadline: datetime, transfers_left: int) -> float:
    """Human-like gap before the next transfer, shrunk to fit the deadline.

    Recomputed from the live clock before every submission, so a slow run can
    never spend its remaining time waiting and miss the deadline with
    transfers still unsent. 5 minutes of slack plus a minute per outstanding
    submission is reserved for the API calls themselves.
    """
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds() / 60.0
    budget = remaining - 5.0 - transfers_left
    if budget <= 0 or transfers_left <= 0:
        return 0.0
    return min(random.uniform(*TRANSFER_GAP_RANGE_MIN), budget / transfers_left)


def stage_execute(bootstrap: dict, state: dict, dry_run: bool) -> dict:
    """Submit the approved plan: transfers one by one, then picks and chip."""
    ev = next_event(bootstrap)
    if not ev:
        return {}
    next_gw = int(ev["id"])
    decision = state.get("approved_plan") or {}

    if not decision or int(decision.get("gw", 0)) != next_gw:
        log.info("No approved plan for GW%d — skipping execution.", next_gw)
        return {}

    transfers = decision.get("approved_transfers") or []
    chip = decision.get("approved_chip")
    picks_payload = decision.get("picks_payload") or []

    if not transfers and not chip and not picks_payload:
        log.info("Approved plan is a hold — nothing to submit for GW%d.", next_gw)
        state["last_executed_gw"] = next_gw
        if not dry_run:
            save_state(state)
            commit_state(f"auto: GW{next_gw} hold (no action)")
        return decision

    hours_left = hours_until_deadline(bootstrap) or 0.0
    log.info("=== EXECUTE — GW%d === %d transfer(s), chip=%s, %.1fh to deadline",
             next_gw, len(transfers), chip, hours_left)

    # Check the pre-committed execution target (set at planning time). This lets
    # the CI runner exit immediately rather than sleeping for up to 3.5h.
    target_str = decision.get("execute_target_utc")
    if target_str and not dry_run:
        try:
            target_dt = datetime.fromisoformat(target_str)
            now_utc = datetime.now(timezone.utc)
            if now_utc < target_dt:
                log.info("Execution target not reached (target=%s, now=%s) — "
                         "exiting; next 2h run will check.", target_dt.isoformat(),
                         now_utc.isoformat())
                return {}
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse execute_target_utc (%s) — proceeding now.", exc)

    token, session = authenticate()
    me = fpl_api.me(session, token)
    entry_id = me["player"]["entry"]

    deadline = _parse_deadline(ev["deadline_time"])
    submitted: list[str] = []
    for idx, t in enumerate(transfers):
        if idx > 0:
            gap = _transfer_gap_minutes(deadline, len(transfers) - idx)
            log.info("Waiting %.1f min before transfer %d/%d...",
                     gap, idx + 1, len(transfers))
            if gap > 0 and not dry_run:
                time.sleep(gap * 60)

        # Wildcard / Free Hit activate on the transfer call; TC and BB are set
        # through the picks payload instead.
        chip_this = chip if (idx == 0 and chip in ("wildcard", "freehit")) else None
        label = f"OUT {t['name_out']} -> IN {t['name_in']}"
        log.info("Transfer %d/%d: %s", idx + 1, len(transfers), label)

        if dry_run:
            log.info("[DRY RUN] would POST /transfers/ %s", t)
            submitted.append(label)
            continue

        payload = {
            "element_in": t["element_in"],
            "element_out": t["element_out"],
            "purchase_price": t["purchase_price"],
            "selling_price": t["selling_price"],
        }
        result = fpl_api.transfer(session, token, entry_id, event=next_gw,
                                  transfers=[payload], chip=chip_this)
        log.info("Result: %s", result)
        submitted.append(label)
        time.sleep(random.uniform(3, 8))

    if picks_payload:
        cap = next((p["element"] for p in picks_payload if p["is_captain"]), None)
        log.info("Updating picks (captain element %s%s)...",
                 cap, f", chip {chip}" if chip else "")
        if dry_run:
            log.info("[DRY RUN] would POST /my-team/ with %d picks", len(picks_payload))
        else:
            time.sleep(random.uniform(3, 7))
            result2 = fpl_api.update_picks(
                session, token, entry_id, picks=picks_payload,
                chips=[chip] if chip else [],
            )
            log.info("Picks updated: %s", result2)

    if not dry_run:
        state["last_executed_gw"] = next_gw
        state["idea_list"] = []
        state["signing_ideas"] = []
        save_state(state)
        # Commit immediately: if this is lost, the next 2-hourly run would
        # resubmit the same transfers.
        commit_state(f"auto: GW{next_gw} transfers executed")

        body = (f"GW{next_gw} transfers done:\n\n"
                + ("\n".join(f"- {s}" for s in submitted) or "- none")
                + f"\n\nChip: {chip or 'none'}"
                + f"\nExpected net gain: {decision.get('expected_net_gain')} pts"
                + f"\nHits: {decision.get('hit_count', 0)}"
                + f"\n\nReasoning: {decision.get('reasoning', '')}")
        email_alerts.send_alert(f"GW{next_gw} transfers done", body)

    return decision


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly FPL automation orchestrator.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run all reads and decisions, skip every API write.")
    parser.add_argument("--auto", action="store_true",
                        help="Accepted for workflow compatibility (autonomous by default).")
    parser.add_argument("--force-stage", choices=STAGES,
                        help="Force a stage instead of deriving it.")
    parser.add_argument("--stage", choices=["transfer_window", "top100"],
                        help="Run a single sub-task only.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    state = load_state()
    session = public_session()

    try:
        bootstrap = fpl_api.bootstrap(session)
    except Exception as exc:  # noqa: BLE001
        msg = f"Could not fetch bootstrap-static: {exc}"
        log.error(msg)
        email_alerts.send_alert("Orchestrator failed at startup", msg)
        return 1

    # Single sub-task mode (used by transfer_window.yml and top100.yml).
    if args.stage:
        try:
            if args.stage == "transfer_window":
                stage_transfer_window(bootstrap, state, args.dry_run)
            elif args.stage == "top100":
                stage_top100(bootstrap, args.dry_run)
            return 0
        except Exception as exc:  # noqa: BLE001
            msg = f"Sub-task {args.stage} failed: {type(exc).__name__}: {exc}"
            log.exception(msg)
            email_alerts.send_alert(f"{args.stage} sub-task failed", msg)
            return 1

    stage = args.force_stage or determine_stage(bootstrap, state)
    hours = hours_until_deadline(bootstrap)
    log.info("Stage: %s (next deadline in %s hours, dry_run=%s)",
             stage, f"{hours:.1f}" if hours is not None else "n/a", args.dry_run)

    try:
        # Keep the signing watch running alongside the normal weekly cycle.
        if TransferWindowMonitor().is_window_open() and stage != INTERNATIONAL_BREAK:
            stage_transfer_window(bootstrap, state, args.dry_run)

        if stage == POST_GW_ANALYSIS:
            stage_post_gw_analysis(bootstrap, state, session, args.dry_run)
        elif stage == MONITORING:
            stage_monitoring(bootstrap, state, session, args.dry_run)
        elif stage == PRE_DEADLINE_PLAN:
            stage_pre_deadline_plan(bootstrap, state, args.dry_run)
        elif stage == EXECUTE:
            stage_execute(bootstrap, state, args.dry_run)
        elif stage == INTERNATIONAL_BREAK:
            log.info("International break — next deadline is more than %d days "
                     "away. No action.", BREAK_DAYS)
    except Exception as exc:  # noqa: BLE001
        msg = f"Stage {stage} failed: {type(exc).__name__}: {exc}"
        log.exception(msg)
        email_alerts.send_alert(f"Orchestrator {stage} failed", msg)
        return 1

    log.info("Stage %s complete.", stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
