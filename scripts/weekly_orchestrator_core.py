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
    "research_ideas": [],
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
#: Total wall-clock the EXECUTE stage may spend pacing transfers. Must stay
#: well under post_gw.yml's 30-minute job cap, or the runner is killed
#: mid-sequence.
MAX_EXECUTE_MINUTES = 18.0
#: Below this many hours to the deadline, the pre-committed execution target is
#: ignored and the plan is submitted immediately. The target is drawn as low as
#: 0.5h before the deadline while cron only ticks every 2h, so without this
#: override a GW whose target lands in a cron gap would never be submitted at
#: all. Must stay above EXECUTE_WINDOW[0] or the override could never fire.
EXECUTE_LAST_CHANCE_H = 2.5
#: Chips consumed by the /transfers/ call. Every other chip is activated
#: through the /my-team/ picks payload instead.
TRANSFER_CHIPS = ("wildcard", "freehit")
CHIP_WILDCARD = "wildcard"
CHIP_FREE_HIT = "freehit"

DEFAULT_EMAIL = ""

#: Must match bot.pre_deadline_simulator.EXECUTION_PLAN_VERSION without importing
#: the heavier planner stack at module import time.
CURRENT_EXECUTION_PLAN_VERSION = 4


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
        # Other workflows write to the same branch and their crons collide
        # (0 */2 and 0 */6 coincide four times a day), so a push can lose a
        # race. A single attempt that loses it leaves the state unpushed, which
        # is precisely what lets the next run repeat a write — so retry.
        last_err = ""
        for attempt in range(3):
            subprocess.run(["git", "pull", "--rebase", "--autostash"],
                           cwd=ROOT, check=False, capture_output=True)
            pushed = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True)
            if pushed.returncode == 0:
                log.info("State committed and pushed: %s", message)
                return
            last_err = (pushed.stderr or b"").decode("utf-8", "replace").strip()[-400:]
            log.warning("Push attempt %d/3 failed: %s", attempt + 1, last_err)
            time.sleep(random.uniform(2, 6))
        raise RuntimeError(f"git push failed after 3 attempts: {last_err}")
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

    Uses kickoff_time + 3h as a proxy for match completion — simpler and more
    reliable than polling the ``finished`` flag, which can lag behind reality.
    Conservative: any fixture with a missing or unparseable kickoff is treated
    as not yet complete.
    """
    try:
        fixtures = fpl_api.fixtures(session, event=gw)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not fetch GW%d fixtures (%s) — assuming not scored", gw, exc)
        return False
    if not fixtures:
        return False
    now_utc = datetime.now(timezone.utc)
    for fx in fixtures:
        kt = fx.get("kickoff_time")
        if not kt:
            return False  # unknown kickoff → conservative
        try:
            ko = _parse_deadline(kt)
            if now_utc < ko + timedelta(hours=3):
                return False
        except Exception:
            return False
    return True


def determine_stage(bootstrap: dict, state_file: dict) -> str:
    """Derive the stage from the calendar and what has already been done.

    Ordering note: deadline-critical work is checked *first*. A pending post-GW
    analysis must never preempt planning or execution. Analysis is entirely
    read-only and losing it for one 2-hourly tick costs nothing, but a missed
    deadline cannot be recovered.

    This ordering also removes a season-ending deadlock. ``last_analyzed_gw``
    only advances once :func:`is_gw_fully_scored` returns True, which requires
    every fixture in the GW to have a parseable kickoff more than 3h in the
    past. A postponed fixture (``kickoff_time: null``) or a repeatedly failing
    analysis therefore pins that check False forever — and under the old
    "analysis first" ordering the orchestrator would return POST_GW_ANALYSIS on
    every run for the rest of the season, silently never planning or executing
    another transfer while every CI run still reported success.
    """
    ev = next_event(bootstrap)
    hours = hours_until_deadline(bootstrap) if ev else None
    on_break = is_international_break(bootstrap)

    if ev is not None and hours is not None and not on_break:
        next_gw = int(ev["id"])
        simulated = int(state_file.get("last_simulated_gw", 0)) >= next_gw
        executed = int(state_file.get("last_executed_gw", 0)) >= next_gw

        # A plan frozen under an older safety schema (or without a confirmed
        # healthy ML runtime) is not "simulated" for deadline purposes.  Replan
        # proactively on the next cron tick instead of waiting until its old
        # execution target to discover that it is invalid.
        approved = state_file.get("approved_plan") or {}
        approved_health = dict(approved.get("model_health") or {})
        approved_is_current = int(approved.get("gw") or 0) == next_gw
        approved_is_safe = (
            int(approved.get("execution_plan_version") or 0) == CURRENT_EXECUTION_PLAN_VERSION
            and approved_health.get("loaded") is True
            and approved_health.get("inference_ok") is True
        )
        if (
            simulated
            and approved_is_current
            and not approved_is_safe
            and not executed
            and hours <= PLAN_WINDOW[1]
            and hours >= EXECUTE_WINDOW[0]
        ):
            return PRE_DEADLINE_PLAN

        if PLAN_WINDOW[0] <= hours <= PLAN_WINDOW[1] and not simulated:
            return PRE_DEADLINE_PLAN

        if EXECUTE_WINDOW[0] <= hours < EXECUTE_WINDOW[1] and not executed:
            # Missed the planning window (e.g. a CI outage) — plan now, and the
            # next run inside the execute window will submit it.
            return EXECUTE if simulated else PRE_DEADLINE_PLAN

    last_gw = get_last_finished_gw(bootstrap)
    if last_gw and int(state_file.get("last_analyzed_gw", 0)) < last_gw:
        return POST_GW_ANALYSIS

    if on_break:
        return INTERNATIONAL_BREAK

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

    email = os.environ.get("FPL_EMAIL", DEFAULT_EMAIL).strip()
    password = os.environ.get("FPL_PASSWORD", "")
    if not email:
        raise RuntimeError("No FPL_REFRESH_TOKEN worked and FPL_EMAIL is unset.")
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
        # Heredoc form, not a bare key=value line: any newline in the token
        # would otherwise corrupt the whole step-output file and could spill
        # the remainder of the token into the build log.
        delim = f"ghadelim_{os.urandom(16).hex()}"
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"new_refresh_token<<{delim}\n{new_rt}\n{delim}\n")
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

    if ideas:
        state.setdefault("signing_ideas", [])
        existing = {i["element"] for i in state["signing_ideas"]}
        state["signing_ideas"] = state["signing_ideas"] + [
            i for i in ideas if i["element"] not in existing
        ]

    if not dry_run:
        # Persist the official element baseline and any new ideas in one state
        # commit. The standalone transfer-window workflow has no later catch-all
        # commit, so saving ideas after the baseline push would silently lose
        # them when the Actions runner exits.
        monitor.update_known_ids(bootstrap)
        if ideas:
            save_state(state)
        commit_state("auto: transfer window baseline and signing ideas")

    if ideas:
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
            ideas = dr.get("ideas", [])
            log.info("Deep research generated %d advisory idea(s) for the bot.", len(ideas))
            if not dry_run:
                state["last_deep_research_gw"] = int(last_gw)
                state["research_ideas"] = ideas
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


def _fmt_price(tenths) -> str:
    """FPL prices are carried in tenths of a million: 60 -> '£6.0m'."""
    try:
        return f"£{int(tenths) / 10.0:.1f}m"
    except (TypeError, ValueError):
        return "£?m"


def _send_deadline_digest(next_gw: int, decision: dict, plan: dict, state: dict,
                          bootstrap: dict, deadline_utc: datetime,
                          target_h_before: float) -> None:
    """Email a pre-deadline summary: transfers, captain, reasoning, injury news."""
    try:
        transfers = decision.get("approved_transfers") or []
        chip = decision.get("approved_chip")
        net_gain = decision.get("expected_net_gain", 0.0)
        reasoning = decision.get("reasoning", "")
        execute_target = decision.get("execute_target_utc", "")

        lines: list[str] = [
            f"GW{next_gw} Pre-Deadline Plan",
            f"Deadline: {deadline_utc.strftime('%a %d %b %Y %H:%M UTC')}",
            "",
        ]

        # --- Decision ---
        if not transfers and not chip:
            lines.append("DECISION: HOLD — rolling free transfer, no action this GW.")
        else:
            if chip:
                lines.append(f"CHIP: {chip.upper()}")
            if transfers:
                lines.append(f"TRANSFERS ({len(transfers)}):")
                for t in transfers:
                    lines.append(f"  OUT {t.get('name_out','?')} "
                                 f"({_fmt_price(t.get('selling_price'))})"
                                 f"  →  IN {t.get('name_in','?')} "
                                 f"({_fmt_price(t.get('purchase_price'))})")
            lines.append(f"Expected net gain: {net_gain:+.2f} pts")
            paid = int(decision.get("hit_count") or 0)
            lines.append(f"Paid transfers: {paid} (points deduction: -{paid * 4})")

        # --- Captain ---
        cap = plan.get("captain") or decision.get("captain") or {}
        vc = None
        gw_plan = plan.get("gw_plan") or []
        if gw_plan:
            vc = gw_plan[0].get("vice")
        # The planner only exposes the vice inside gw_plan; the frozen decision
        # carries it at the top level. Fall back so the digest is never blank.
        vc = vc or decision.get("vice") or {}
        if cap.get("name"):
            lines.append(f"\nCaptain:       {cap['name']} ({cap.get('xpts', '?')} xPts)")
        if vc and vc.get("name"):
            lines.append(f"Vice-captain:  {vc['name']} ({vc.get('xpts', '?')} xPts)")

        # --- Reasoning ---
        if reasoning:
            lines += ["", "REASONING:", reasoning]

        # --- Forecast sanity warnings ---
        proj_warnings = plan.get("projection_warnings") or []
        if proj_warnings:
            lines += ["", "PROJECTION SANITY WARNING (the forward model looks off — "
                          "treat any chip / big-move recommendation with suspicion):"]
            lines += [f"  ! {w}" for w in proj_warnings]

        # --- Execution time ---
        if execute_target:
            try:
                exec_dt = datetime.fromisoformat(execute_target)
                lines.append(f"\nExecution scheduled: {exec_dt.strftime('%a %d %b %H:%M UTC')}"
                             f" ({target_h_before:.1f}h before deadline)")
            except Exception:
                pass

        # --- Injury / availability flags ---
        flags = [i for i in (state.get("idea_list") or [])
                 if i.get("action") == "transfer_out" and i.get("reason")]
        if flags:
            lines += ["", "AVAILABILITY WARNINGS (your squad):"]
            for f in flags:
                lines.append(f"  ⚠ {f['name']}: {f['reason']}")

        # --- Transfer window / signing ideas ---
        signing_ideas = state.get("signing_ideas") or []
        if signing_ideas:
            lines += ["", "TRANSFER WINDOW INTEL:"]
            for idea in signing_ideas[:5]:
                lines.append(f"  • {idea.get('name','?')}: {idea.get('reason','')}")

        # --- Squad availability from bootstrap (fresh) ---
        elements = {int(e["id"]): e for e in bootstrap.get("elements", [])}
        my_team_ids = {p["element"] for p in (decision.get("picks_payload") or [])}
        doubt_lines = []
        for el_id in my_team_ids:
            el = elements.get(el_id)
            if not el:
                continue
            status = el.get("status", "a")
            chance = el.get("chance_of_playing_next_round")
            if status != "a" or (chance is not None and chance < 100):
                doubt_lines.append(
                    f"  {el.get('web_name','?')}: status={status},"
                    f" chance={chance if chance is not None else '?'}%"
                    f" — {(el.get('news') or '').strip()}"
                )
        if doubt_lines:
            lines += ["", "DOUBTS / INJURIES IN SQUAD (from FPL API):"]
            lines.extend(doubt_lines)

        body = "\n".join(lines)
        transfers_summary = (
            f"{len(transfers)} transfer(s)" if transfers else
            (chip.upper() if chip else "HOLD")
        )
        email_alerts.send_alert(
            f"GW{next_gw} Digest — {transfers_summary}",
            body,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Deadline digest email failed (%s) — continuing.", exc)


def _candidate_watch_ids(state: dict) -> list[int]:
    """Selectable ideas that deserve evaluation outside the normal top-N cutoff.

    This does not endorse or force a transfer. It simply guarantees that a
    newly added player or research target reaches the forecaster/MILP once.
    """
    values: set[int] = set()
    for bucket in ("signing_ideas", "research_ideas", "idea_list"):
        for idea in state.get(bucket) or []:
            if str(idea.get("action") or "") != "transfer_in":
                continue
            try:
                element = int(idea.get("element") or 0)
            except (TypeError, ValueError):
                continue
            if element > 0:
                values.add(element)
    return sorted(values)


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
             (entry_info.get("last_deadline_bank") or 0) / 10.0)

    # Imported here rather than at module scope: the planner pulls in pulp and
    # scipy, which the light single-stage workflows do not install.
    from bot.pre_deadline_simulator import HIT_MARGIN, PreDeadlineSimulator
    from bot.season_forecaster import projection_warnings
    from bot.updater import (
        SeasonUpdater,
        _discounted_plan_value,
        build_current_state,
    )

    candidate_watch_ids = _candidate_watch_ids(state)
    if candidate_watch_ids:
        log.info(
            "Forcing %d transfer/research watchlist player(s) into candidate evaluation.",
            len(candidate_watch_ids),
        )
    updater = SeasonUpdater(horizon=6)
    plan = updater.run(
        current_gw=next_gw,
        my_team=my_team,
        entry_info=entry_info,
        forced_ids=candidate_watch_ids,
        write_reports=False,
    )
    model_health = dict(plan.get("model_health") or {})
    if not (
        model_health.get("loaded") is True
        and model_health.get("inference_ok") is True
    ):
        detail = model_health.get("error") or "unknown model runtime failure"
        email_alerts.send_alert(
            f"GW{next_gw} autonomous plan BLOCKED — ML unhealthy",
            "The production model did not load/infer successfully. "
            "No transfer or chip plan will be frozen from fallback forecasts.\n\n"
            f"Model detail: {detail}",
        )
        raise RuntimeError(
            f"production ML unhealthy; refusing to freeze GW{next_gw} plan: {detail}"
        )
    planning_bootstrap = updater._last_bootstrap or bootstrap
    current_state = build_current_state(
        planning_bootstrap,
        my_team,
        next_gw,
        entry_info,
    )

    # Rebuild the forecast table the planner used, so the simulator can value
    # the proposed transfers and the post-GW analyzer gets a genuine pre-GW view.
    # Reuse the updater's exact bootstrap/fixture snapshot.
    forecasts = _rebuild_forecasts(
        updater,
        planning_bootstrap,
        next_gw,
        current_state,
        forced_ids=candidate_watch_ids,
    )
    save_forecast_snapshot(forecasts, next_gw)

    # Autonomous paid hits are compared against an explicit zero-hit
    # counterfactual and capped at two.  The optimiser already subtracts the
    # four-point hit cost; this gate demands an additional uncertainty margin
    # per paid transfer before accepting the risk.
    if (
        plan.get("chip") not in (CHIP_WILDCARD, CHIP_FREE_HIT)
        and int(plan.get("hits") or 0) > 0
    ):
        no_hit_plan = updater.planner.plan(
            forecasts=forecasts,
            current_state=current_state,
            max_current_gw_hits=0,
        )
        candidate_plan = plan
        if int(plan.get("hits") or 0) > 2:
            log.warning(
                "Planner requested %d paid hits; capping autonomous candidate at 2.",
                int(plan.get("hits") or 0),
            )
            candidate_plan = updater.planner.plan(
                forecasts=forecasts,
                current_state=current_state,
                max_current_gw_hits=2,
            )

        candidate_hits = int(candidate_plan.get("hits") or 0)
        counterfactual_edge = (
            _discounted_plan_value(candidate_plan)
            - _discounted_plan_value(no_hit_plan)
        )
        required_edge = HIT_MARGIN * candidate_hits
        if candidate_hits > 0 and counterfactual_edge < required_edge:
            log.warning(
                "Paid-hit plan rejected by counterfactual gate: edge %.2f < %.2f required.",
                counterfactual_edge,
                required_edge,
            )
            plan = no_hit_plan
            plan["transfer_plan_kind"] = "ordinary_no_hit_counterfactual"
            plan["chip"] = None
            plan["chip_plan"] = []
            for gw_row in plan.get("gw_plan") or []:
                gw_row["chip"] = None
        else:
            plan = candidate_plan
        plan["hit_counterfactual"] = {
            "candidate_hits": candidate_hits,
            "discounted_edge_vs_zero_hit": round(counterfactual_edge, 2),
            "required_edge": round(required_edge, 2),
        }
        plan["model_health"] = model_health

    # Recompute projection sanity after any paid-hit counterfactual re-solve.
    # These warnings used to be email-only; autonomous writes now fail closed
    # because a visibly corrupt horizon should never freeze an execution plan.
    plan["projection_warnings"] = projection_warnings(plan.get("gw_plan", []))
    if plan["projection_warnings"]:
        detail = " | ".join(plan["projection_warnings"])
        email_alerts.send_alert(
            f"GW{next_gw} autonomous plan BLOCKED — projection sanity",
            "The forward model produced a projection sanity warning. "
            "No transfer or chip plan was frozen.\n\n" + detail,
        )
        raise RuntimeError(
            f"projection sanity warning; refusing to freeze GW{next_gw}: {detail}"
        )

    idea_list = (list(state.get("idea_list") or [])
                 + list(state.get("signing_ideas") or [])
                 + list(state.get("research_ideas") or []))
    decision = PreDeadlineSimulator(horizon=6).simulate(
        idea_list=idea_list,
        forecasts=forecasts,
        current_state=current_state,
        next_gw=next_gw,
        plan=plan,
    )

    if decision.get("requires_replan"):
        log.warning("Rejected transfer plan — rebuilding with zero paid hits.")
        external_vetoes = (
            set(PreDeadlineSimulator._blocked_elements(idea_list))
            - set(current_state.get("squad") or [])
        )
        plan = updater.planner.plan(
            forecasts=forecasts,
            current_state=current_state,
            max_current_gw_hits=0,
            forbidden_current_ids=external_vetoes,
        )
        plan["transfer_plan_kind"] = "ordinary_no_hit_fallback"
        plan["chip"] = None
        plan["chip_plan"] = []
        plan["chip_recommendation"] = None
        decision = PreDeadlineSimulator(horizon=6).simulate(
            idea_list=idea_list,
            forecasts=forecasts,
            current_state=current_state,
            next_gw=next_gw,
            plan=plan,
        )
        if decision.get("requires_replan"):
            raise RuntimeError(
                "Fresh no-hit fallback was rejected; refusing to freeze an "
                "internally inconsistent execution plan"
            )

    # Only now is the human-readable report written. This exact plan has passed
    # model-health, projection, hit/Wildcard and veto gates, so the report can
    # no longer disagree with the frozen execution decision.
    try:
        final_report_paths = updater._write_reports(
            plan,
            next_gw,
            forecasts,
            planning_bootstrap,
            updater._last_report_gw,
            updater._last_report_gw_data,
        )
        plan["report_paths"] = final_report_paths
    except Exception as exc:  # noqa: BLE001
        log.warning("Final deadline report refresh failed (%s).", exc)

    decision["picks_payload"] = build_picks_payload(plan)
    decision["entry_id"] = entry_id
    decision["source_squad_signature"] = sorted(
        int(element) for element in current_state.get("squad", [])
    )
    decision["target_squad_signature"] = sorted(
        int(pick["element"]) for pick in decision["picks_payload"]
    )
    guard_ids = sorted(
        set(decision["source_squad_signature"])
        | set(decision["target_squad_signature"])
    )
    decision["planning_news_guard"] = _build_planning_news_guard(
        planning_bootstrap,
        guard_ids,
    )

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

    _send_deadline_digest(
        next_gw,
        decision,
        plan,
        state,
        planning_bootstrap,
        deadline_utc,
        target_h_before,
    )
    return decision


def _rebuild_forecasts(
    updater,
    bootstrap: dict,
    next_gw: int,
    current_state: dict,
    forced_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Return the exact fully-adjusted forecast frame used by the planner."""
    try:
        canonical = getattr(updater, "_last_forecasts", None)
        if isinstance(canonical, pd.DataFrame) and not canonical.empty:
            return canonical.copy()

        # Backwards-compatible fallback for callers that did not run the full
        # updater first.  Live pre-deadline planning should never need this.
        fixtures = list(updater._last_fixtures or [])
        if not fixtures:
            from bot import data_collector
            fixtures = data_collector.fetch_fixtures(force=True)
        ml_xpts = None
        if updater._predictor is not None:
            ml_xpts = updater._compute_ml_xpts(bootstrap, next_gw) or None
        return updater.forecaster.forecast(
            bootstrap=bootstrap,
            fixtures=fixtures,
            current_gw=next_gw,
            owned_ids=current_state["squad"],
            forced_ids=forced_ids or [],
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
    # Outfield bench priority is an expected-points decision, not a price
    # decision. Put the player most likely to help an auto-sub first.
    bench_out = sorted(
        [p for p in bench if p["position"] != 1],
        key=lambda p: (float(p.get("xpts") or 0.0), float(p.get("cost") or 0.0)),
        reverse=True,
    )

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


def _transfer_key(t: dict) -> str:
    """Stable identity for one transfer pair, used to skip completed work."""
    return f"{t['element_out']}->{t['element_in']}"


def _executed_keys(state: dict, gw: int) -> set[str]:
    """Transfer keys already confirmed submitted for ``gw``."""
    rec = state.get("executed_transfers") or {}
    try:
        if int(rec.get("gw", 0)) != int(gw):
            return set()
    except (TypeError, ValueError):
        return set()
    return {str(k) for k in (rec.get("pairs") or [])}


def _record_executed(state: dict, gw: int, key: str) -> None:
    rec = state.get("executed_transfers") or {}
    try:
        same_gw = int(rec.get("gw", 0)) == int(gw)
    except (TypeError, ValueError):
        same_gw = False
    pairs = list(rec.get("pairs") or []) if same_gw else []
    pairs.append(key)
    state["executed_transfers"] = {"gw": int(gw), "pairs": pairs}


def _build_planning_news_guard(bootstrap: dict, element_ids: list[int]) -> list[dict]:
    """Stable official-FPL availability snapshot used to detect late news."""
    elements = {
        int(row["id"]): row
        for row in bootstrap.get("elements", [])
        if row.get("id") is not None
    }
    guard: list[dict] = []
    for element in sorted(set(int(value) for value in element_ids)):
        row = elements.get(element) or {}
        news = " ".join(str(row.get("news") or "").split())
        chance = row.get("chance_of_playing_next_round")
        guard.append({
            "element": element,
            "status": str(row.get("status") or "missing"),
            "chance_next_round": chance,
            "news": news,
            "can_select": row.get("can_select"),
            "can_transact": row.get("can_transact"),
            "removed": bool(row.get("removed")),
        })
    return guard


def _guard_changed(frozen: list[dict] | None, bootstrap: dict) -> tuple[bool, str]:
    if not frozen:
        return False, ""
    ids = [int(row.get("element") or 0) for row in frozen if row.get("element")]
    current = _build_planning_news_guard(bootstrap, ids)
    if current == frozen:
        return False, ""

    before = {int(row["element"]): row for row in frozen}
    after = {int(row["element"]): row for row in current}
    changed = []
    for element in sorted(set(before) | set(after)):
        if before.get(element) != after.get(element):
            old = before.get(element) or {}
            new = after.get(element) or {}
            changed.append(
                f"{element}: status {old.get('status')}→{new.get('status')}, "
                f"chance {old.get('chance_next_round')}→{new.get('chance_next_round')}, "
                f"select {old.get('can_select')}→{new.get('can_select')}, "
                f"transact {old.get('can_transact')}→{new.get('can_transact')}, "
                f"removed {old.get('removed')}→{new.get('removed')}, "
                f"news changed={old.get('news') != new.get('news')}"
            )
    return True, "; ".join(changed[:8])


def _expected_live_squad(decision: dict, completed_keys: set[str]) -> set[int]:
    expected = {
        int(value) for value in (decision.get("source_squad_signature") or [])
    }
    if not expected:
        return set()
    for transfer in decision.get("approved_transfers") or []:
        if _transfer_key(transfer) not in completed_keys:
            continue
        expected.discard(int(transfer["element_out"]))
        expected.add(int(transfer["element_in"]))
    return expected


def _picks_readback_errors(expected: list[dict], live_team: dict) -> list[str]:
    live = list(live_team.get("picks") or [])
    errors: list[str] = []
    if len(live) != len(expected):
        errors.append(f"pick count {len(live)} != {len(expected)}")
        return errors

    expected_by_pos = {
        int(row["position"]): (
            int(row["element"]),
            bool(row.get("is_captain")),
            bool(row.get("is_vice_captain")),
        )
        for row in expected
    }
    live_by_pos = {
        int(row.get("position") or 0): (
            int(row.get("element") or 0),
            bool(row.get("is_captain")),
            bool(row.get("is_vice_captain")),
        )
        for row in live
    }
    for position in sorted(expected_by_pos):
        if live_by_pos.get(position) != expected_by_pos[position]:
            errors.append(
                f"position {position}: expected {expected_by_pos[position]} "
                f"got {live_by_pos.get(position)}"
            )
    return errors



def _chip_readback_error(chip: str | None, gw: int, live_team: dict) -> str | None:
    if not chip:
        return None
    rows = list(live_team.get("chips") or [])
    for row in rows:
        if str(row.get("name") or "") != str(chip):
            continue
        status = str(row.get("status_for_entry") or "").lower()
        event = row.get("event")
        try:
            event_id = int(event) if event is not None else None
        except (TypeError, ValueError):
            event_id = None
        if status == "played" and event_id == int(gw):
            return None
    return f"chip {chip} is not confirmed played for GW{gw} in /my-team read-back"


def _replan_stale_execution(
    bootstrap: dict,
    state: dict,
    dry_run: bool,
    reason: str,
) -> dict:
    """Invalidate a frozen plan and build a fresh one before any new write."""
    log.warning("Frozen execution plan is stale: %s", reason)
    email_alerts.send_alert(
        "FPL plan invalidated before execution",
        reason + "\n\nNo new FPL write was made from the stale plan. Replanning now.",
    )
    state["last_simulated_gw"] = 0
    state["approved_plan"] = None
    state["executed_transfers"] = {}
    if not dry_run:
        save_state(state)
        commit_state("auto: invalidate stale pre-deadline plan")
    fresh = stage_pre_deadline_plan(bootstrap, state, dry_run)
    hours_left = hours_until_deadline(bootstrap) or 0.0
    if not dry_run and hours_left <= EXECUTE_LAST_CHANCE_H:
        log.warning(
            "Replan completed with only %.2fh left; executing the fresh plan now.",
            hours_left,
        )
        return stage_execute(bootstrap, state, dry_run)
    return fresh


def _frozen_plan_errors(decision: dict) -> list[str]:
    """Reject legacy or internally inconsistent plans before any FPL write."""
    errors: list[str] = []
    if int(decision.get("execution_plan_version") or 0) != CURRENT_EXECUTION_PLAN_VERSION:
        errors.append("legacy frozen plan predates model-health/counterfactual safety validation")
    model_health = dict(decision.get("model_health") or {})
    if not (
        model_health.get("loaded") is True
        and model_health.get("inference_ok") is True
    ):
        errors.append("frozen plan was not produced by a healthy production ML runtime")
    chip = decision.get("approved_chip")
    transfers = list(decision.get("approved_transfers") or [])
    source = [int(value) for value in decision.get("source_squad_signature") or []]
    target = [int(value) for value in decision.get("target_squad_signature") or []]
    if len(source) != 15 or len(set(source)) != 15:
        errors.append("source squad signature is not 15 unique players")
    if len(target) != 15 or len(set(target)) != 15:
        errors.append("target squad signature is not 15 unique players")
    if len(source) == 15 and len(target) == 15:
        derived = set(source)
        for transfer in transfers:
            try:
                outgoing = int(transfer["element_out"])
                incoming = int(transfer["element_in"])
            except (KeyError, TypeError, ValueError):
                errors.append("transfer record has invalid player IDs")
                continue
            if outgoing not in derived or incoming in derived:
                errors.append("transfer batch is inconsistent with its source squad")
                continue
            derived.remove(outgoing)
            derived.add(incoming)
        if derived != set(target):
            errors.append("transfer batch does not produce the target squad")
    if chip == CHIP_FREE_HIT:
        errors.append("Free Hit execution is not supported")
    if chip == CHIP_WILDCARD:
        if not transfers:
            errors.append("Wildcard plan contains no transfers")
        if decision.get("wildcard_validated") is not True:
            errors.append("Wildcard lacks an explicit validation marker")
        if decision.get("transfer_plan_kind") != "wildcard_rebuild":
            errors.append("Wildcard lacks a dedicated rebuild")
        if int(decision.get("hit_count") or 0) != 0:
            errors.append("Wildcard plan contains paid hits")
        errors.extend(str(e) for e in decision.get("wildcard_validation_errors") or [])
    if decision.get("requires_replan"):
        errors.append("plan is explicitly marked for replanning")
    return errors


def stage_execute(bootstrap: dict, state: dict, dry_run: bool) -> dict:
    """Submit the approved plan: atomic transfer batch, then picks and chip."""
    ev = next_event(bootstrap)
    if not ev:
        return {}
    next_gw = int(ev["id"])
    decision = state.get("approved_plan") or {}

    if not decision or int(decision.get("gw", 0)) != next_gw:
        log.info("No approved plan for GW%d — skipping execution.", next_gw)
        return {}

    frozen_errors = _frozen_plan_errors(decision)
    if frozen_errors:
        return _replan_stale_execution(
            bootstrap, state, dry_run, "; ".join(frozen_errors)
        )

    transfers = decision.get("approved_transfers") or []
    chip = decision.get("approved_chip")
    picks_payload = decision.get("picks_payload") or []

    changed, change_detail = _guard_changed(
        decision.get("planning_news_guard"),
        bootstrap,
    )
    if changed:
        return _replan_stale_execution(
            bootstrap,
            state,
            dry_run,
            "Official FPL availability/news changed since planning: " + change_detail,
        )

    if chip == CHIP_FREE_HIT:
        return _replan_stale_execution(
            bootstrap,
            state,
            dry_run,
            "Approved plan contains unsupported Free Hit execution.",
        )

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
            if now_utc < target_dt and hours_left > EXECUTE_LAST_CHANCE_H:
                log.info("Execution target not reached (target=%s, now=%s) — "
                         "exiting; next 2h run will check.", target_dt.isoformat(),
                         now_utc.isoformat())
                return {}
            if now_utc < target_dt:
                log.warning("Only %.1fh to the deadline (below the %.1fh last-chance "
                            "threshold) — overriding execution target %s and "
                            "submitting now.", hours_left, EXECUTE_LAST_CHANCE_H,
                            target_dt.isoformat())
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not parse execute_target_utc (%s) — proceeding now.", exc)

    token, session = authenticate()
    me = fpl_api.me(session, token)
    entry_id = me["player"]["entry"]

    # Prices move every night (~01:30 UTC) and the plan was frozen up to 36h
    # ago. FPL rejects a transfer whose purchase_price/selling_price disagree
    # with the live values, and the rejection would repeat every run until the
    # deadline passed. Re-read both from live data, exactly as apply_team.py does.
    live_prices = {int(el["id"]): int(el["now_cost"])
                   for el in bootstrap.get("elements", [])}
    try:
        live_team = fpl_api.my_team(session, token, entry_id)
        live_picks = list(live_team.get("picks", []))
        selling_now = {
            int(p["element"]): int(p["selling_price"])
            for p in live_picks
        }
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Could not re-read live squad/selling prices before execution: {exc}"
        ) from exc

    # Skip anything a previous (crashed or timed-out) run already submitted.
    done = _executed_keys(state, next_gw)

    expected_live = _expected_live_squad(decision, done)
    actual_live = {int(p["element"]) for p in live_picks}
    if expected_live and actual_live != expected_live:
        return _replan_stale_execution(
            bootstrap,
            state,
            dry_run,
            "Live FPL squad no longer matches the frozen plan source state "
            f"(expected {sorted(expected_live)}, got {sorted(actual_live)}).",
        )
    pending = [t for t in transfers if _transfer_key(t) not in done]
    if done:
        log.warning("Resuming GW%d execution: %d/%d transfer(s) already submitted "
                    "by an earlier run — skipping them.", next_gw, len(done), len(transfers))

    # Submit transfers as one atomic FPL batch. Coordinated moves can
    # depend on simultaneous sale proceeds, and a mid-sequence network failure
    # must not leave only half of the strategy applied. A validated Wildcard is
    # attached here; Free Hit remains blocked above.
    submitted: list[str] = []
    payloads: list[dict] = []
    labels: list[str] = []
    pending_keys: list[str] = []

    for t in pending:
        buy = live_prices.get(int(t["element_in"]), t["purchase_price"])
        sell = selling_now.get(int(t["element_out"]), t["selling_price"])
        if buy != t["purchase_price"] or sell != t["selling_price"]:
            log.info(
                "Price refresh: in %s->%s, out %s->%s (tenths)",
                t["purchase_price"], buy, t["selling_price"], sell,
            )
        if int(t["element_out"]) not in selling_now:
            raise RuntimeError(
                f"Planned outgoing element {t['element_out']} is not in the "
                "live squad; refusing a stale transfer payload."
            )
        payloads.append({
            "element_in": t["element_in"],
            "element_out": t["element_out"],
            "purchase_price": buy,
            "selling_price": sell,
        })
        labels.append(f"OUT {t['name_out']} -> IN {t['name_in']}")
        pending_keys.append(_transfer_key(t))

    if payloads:
        log.info("Submitting %d transfer(s) atomically.", len(payloads))
        if dry_run:
            log.info("[DRY RUN] would POST /transfers/ batch: %s", payloads)
            submitted.extend(labels)
        else:
            result = fpl_api.transfer(
                session,
                token,
                entry_id,
                event=next_gw,
                transfers=payloads,
                chip=CHIP_WILDCARD if chip == CHIP_WILDCARD else None,
            )
            log.info("Atomic transfer result: %s", result)
            submitted.extend(labels)
            for key in pending_keys:
                _record_executed(state, next_gw, key)
            save_state(state)
            commit_state(
                f"auto: GW{next_gw} atomic transfer batch submitted "
                f"({len(payloads)} transfer(s))"
            )

    if picks_payload:
        cap = next((p["element"] for p in picks_payload if p["is_captain"]), None)
        # Wildcard/Free Hit were already consumed by the /transfers/ call above;
        # sending them again here makes FPL reject the picks update, which used
        # to abort the stage after the transfers had gone through.
        picks_chips = [chip] if (chip and chip not in TRANSFER_CHIPS) else []
        log.info("Updating picks (captain element %s%s)...",
                 cap, f", chip {picks_chips[0]}" if picks_chips else "")
        if dry_run:
            log.info("[DRY RUN] would POST /my-team/ with %d picks", len(picks_payload))
        else:
            time.sleep(random.uniform(3, 7))
            result2 = fpl_api.update_picks(
                session, token, entry_id, picks=picks_payload,
                chips=picks_chips,
            )
            log.info("Picks updated: %s", result2)

    if not dry_run:
        verified_team = fpl_api.my_team(session, token, entry_id)
        readback_errors = _picks_readback_errors(picks_payload, verified_team)
        chip_error = _chip_readback_error(chip, next_gw, verified_team)
        if chip_error:
            readback_errors.append(chip_error)
        if readback_errors:
            raise RuntimeError(
                "Exact post-write FPL read-back failed: "
                + "; ".join(readback_errors[:8])
            )

        state["last_executed_gw"] = next_gw
        state["executed_transfers"] = {}
        state["idea_list"] = []
        state["signing_ideas"] = []
        state["research_ideas"] = []
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
