"""Pre-deadline cohort selection for post-deadline elite scouting.

The important distinction is *who* we track versus *what team they picked*:

* Before a GW deadline, this module may snapshot the public Overall standings
  to freeze the cohort of managers who were genuinely top-ranked going into the
  deadline. It never requests their GW picks at this stage.
* After the deadline, it delegates to :class:`PostDeadlineTopManagerScout` but
  forces it to use that frozen cohort instead of the now-live Overall table.

This avoids selection bias where querying the live Overall standings after
matches start simply finds managers who already hauled in the current GW.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from .top100_post_deadline import PostDeadlineTopManagerScout


class CohortLockedTopManagerScout(PostDeadlineTopManagerScout):
    """Scout elite managers selected before the same GW's deadline."""

    COHORT_WINDOW_HOURS = 1.0

    def cohort_path_for_gw(self, gw: int) -> Path:
        return self.data_dir / f"cohort_gw{int(gw)}.json"

    def load_cohort(self, gw: int) -> dict | None:
        path = self.cohort_path_for_gw(gw)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def capture_pre_deadline_cohort(
        self,
        *,
        gw: int,
        bootstrap: dict,
        session: requests.Session,
        persist: bool = True,
        now_utc: datetime | None = None,
        window_hours: float | None = None,
    ) -> dict:
        """Freeze Overall standings shortly before ``gw`` locks.

        This function fetches standings only. It intentionally does **not** call
        any manager picks endpoint, so competitors' locked teams remain unseen
        until after the deadline.
        """
        event = self._event(bootstrap, gw)
        if not event or not event.get("deadline_time"):
            return {"captured": False, "reason": "deadline unavailable", "cohort": None}

        try:
            deadline = datetime.fromisoformat(
                event["deadline_time"].replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except (TypeError, ValueError):
            return {"captured": False, "reason": "invalid deadline", "cohort": None}

        now = now_utc or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        hours = (deadline - now).total_seconds() / 3600.0
        window = float(window_hours if window_hours is not None else self.COHORT_WINDOW_HOURS)

        if hours <= 0:
            return {
                "captured": False,
                "reason": f"GW{int(gw)} already locked; refusing to select a cohort from live standings.",
                "cohort": self.load_cohort(gw),
            }
        if hours > window:
            return {
                "captured": False,
                "reason": f"GW{int(gw)} deadline is still {hours:.2f}h away; cohort window not open.",
                "cohort": self.load_cohort(gw),
            }

        standings = self.tracker.fetch_standings(session)
        if not standings:
            return {"captured": False, "reason": "Overall standings unavailable", "cohort": None}

        cohort = {
            "gw": int(gw),
            "captured_at": now.isoformat(),
            "deadline_time": event.get("deadline_time"),
            "standings_only": True,
            "n_standings": len(standings),
            "standings": standings,
        }
        if persist:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.cohort_path_for_gw(gw).write_text(json.dumps(cohort, indent=2), encoding="utf-8")
        return {"captured": True, "reason": "pre-deadline Overall cohort frozen", "cohort": cohort}

    def snapshot_locked_teams(
        self,
        *,
        gw: int,
        bootstrap: dict,
        session: requests.Session,
        persist: bool = True,
        now_utc: datetime | None = None,
    ) -> dict:
        """Fetch picks after the deadline for the *pre-deadline* frozen cohort."""
        if not self.deadline_has_passed(bootstrap, gw, now_utc=now_utc):
            return super().snapshot_locked_teams(
                gw=gw,
                bootstrap=bootstrap,
                session=session,
                persist=persist,
                now_utc=now_utc,
            )

        cohort = self.load_cohort(gw)
        if not cohort or not cohort.get("standings"):
            return {
                "created": False,
                "blocked_pre_deadline": False,
                "retry_later": False,
                "no_predeadline_cohort": True,
                "reason": (
                    f"No pre-deadline Overall cohort was frozen for GW{int(gw)}. "
                    "Skipping elite-strategy analysis rather than selecting managers "
                    "from live standings after matches started."
                ),
                "snapshot": None,
            }

        original_fetch = self.tracker.fetch_standings
        self.tracker.fetch_standings = lambda _session: list(cohort.get("standings") or [])
        try:
            result = super().snapshot_locked_teams(
                gw=gw,
                bootstrap=bootstrap,
                session=session,
                persist=persist,
                now_utc=now_utc,
            )
        finally:
            self.tracker.fetch_standings = original_fetch

        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        if isinstance(snapshot, dict):
            snapshot["cohort_captured_at"] = cohort.get("captured_at")
            snapshot["cohort_source"] = "pre_deadline_overall_standings"
            if persist and result.get("created"):
                self.path_for_gw(gw).write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return result
