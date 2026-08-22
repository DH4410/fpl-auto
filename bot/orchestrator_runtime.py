"""Small calendar helpers used by the weekly orchestrator entrypoint.

FPL's ``event.finished`` flag means the whole gameweek has finished.  It does
*not* mean its transfer deadline is still upcoming.  The orchestrator needs two
separate concepts:

* the next event whose deadline is still in the future (planning/execution), and
* the latest event whose deadline has passed (current squad monitoring).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _utc_now(now_utc: datetime | None = None) -> datetime:
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def parse_fpl_time(value: str) -> datetime:
    """Parse an FPL UTC timestamp, accepting the API's trailing ``Z``."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def next_future_event(bootstrap: dict, now_utc: datetime | None = None) -> dict | None:
    """Return the event with the nearest deadline strictly after ``now``."""
    now = _utc_now(now_utc)
    candidates: list[tuple[datetime, dict]] = []
    for event in bootstrap.get("events", []):
        raw = event.get("deadline_time")
        if not raw:
            continue
        try:
            deadline = parse_fpl_time(raw)
        except (TypeError, ValueError):
            continue
        if deadline > now:
            candidates.append((deadline, event))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def latest_started_gw(bootstrap: dict, now_utc: datetime | None = None) -> int | None:
    """Return the most recent GW whose deadline has passed.

    Public ``entry/{id}/event/{gw}/picks`` becomes useful after the deadline,
    even while the gameweek is still live, so this is the right squad source
    for the MONITORING stage.
    """
    now = _utc_now(now_utc)
    started: list[tuple[datetime, int]] = []
    for event in bootstrap.get("events", []):
        raw = event.get("deadline_time")
        if not raw or event.get("id") is None:
            continue
        try:
            deadline = parse_fpl_time(raw)
        except (TypeError, ValueError):
            continue
        if deadline <= now:
            started.append((deadline, int(event["id"])))
    return max(started, key=lambda item: item[0])[1] if started else None


def hours_until_next_deadline(bootstrap: dict, now_utc: datetime | None = None) -> float | None:
    """Hours until the next *future* FPL deadline; never returns a negative value."""
    now = _utc_now(now_utc)
    event = next_future_event(bootstrap, now)
    if not event:
        return None
    return (parse_fpl_time(event["deadline_time"]) - now).total_seconds() / 3600.0


def is_international_break(
    bootstrap: dict,
    break_days: int = 14,
    now_utc: datetime | None = None,
) -> bool:
    """Whether the next future deadline is more than ``break_days`` away."""
    now = _utc_now(now_utc)
    event = next_future_event(bootstrap, now)
    if not event:
        return False
    return parse_fpl_time(event["deadline_time"]) - now > timedelta(days=break_days)
