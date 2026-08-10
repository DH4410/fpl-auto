"""
Live fixture and news monitor — Module 10.

Polls the FPL API for:
  1. Fixture list changes → DGW/BGW announcements (teams suddenly have 2 games
     in one GW or drop from 1 to 0 because a postponement was scheduled).
  2. Bootstrap news changes → player injury updates, status changes, and
     ``chance_of_playing`` revisions published after press conferences.

Run this once per hour via a cron job or Windows Task Scheduler:

    python scripts/live_check.py

Or call ``LiveMonitor.run_once()`` programmatically. Each invocation:
  - Fetches fresh bootstrap-static and fixtures (bypassing the 6-hour cache).
  - Diffs against the previous snapshot persisted in ``SNAPSHOT_PATH``.
  - Logs and returns a ``MonitorAlert`` describing any changes.

Alerts are purely advisory — the monitor never writes to FPL or modifies squad
files. If ``rescore`` is True, the updater pipeline is re-run automatically.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).resolve().parent / "cache" / "live_monitor_snapshot.json"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FixtureChange:
    team: str
    gw: int
    old_n_fixtures: int
    new_n_fixtures: int

    @property
    def is_dgw_added(self) -> bool:
        return self.new_n_fixtures > 1 and self.old_n_fixtures <= 1

    @property
    def is_bgw_added(self) -> bool:
        return self.new_n_fixtures == 0 and self.old_n_fixtures > 0

    @property
    def description(self) -> str:
        if self.is_dgw_added:
            return f"DGW DETECTED: {self.team} GW{self.gw} now has {self.new_n_fixtures} fixtures (was {self.old_n_fixtures})"
        if self.is_bgw_added:
            return f"BGW DETECTED: {self.team} GW{self.gw} now has 0 fixtures (was {self.old_n_fixtures})"
        return f"Fixture change: {self.team} GW{self.gw}: {self.old_n_fixtures} → {self.new_n_fixtures} fixtures"


@dataclass
class NewsChange:
    element_id: int
    player_name: str
    field: str
    old_value: Any
    new_value: Any

    @property
    def description(self) -> str:
        return f"NEWS: {self.player_name} [{self.field}] {self.old_value!r} → {self.new_value!r}"


@dataclass
class MonitorAlert:
    timestamp: str
    fixture_changes: list[FixtureChange] = field(default_factory=list)
    news_changes: list[NewsChange] = field(default_factory=list)
    has_dgw_change: bool = False
    has_bgw_change: bool = False
    has_injury_news: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.fixture_changes and not self.news_changes

    def summary(self) -> str:
        lines = [f"LiveMonitor alert at {self.timestamp}"]
        if self.is_empty:
            lines.append("  No changes detected.")
        for fc in self.fixture_changes:
            lines.append(f"  {fc.description}")
        for nc in self.news_changes:
            lines.append(f"  {nc.description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def _build_fixture_snapshot(fixtures: list[dict], teams_by_id: dict[int, str]) -> dict:
    """Compact snapshot: {team_name: {gw: n_fixtures}} for all future GWs."""
    snap: dict[str, dict[str, int]] = {}
    for fix in fixtures:
        if fix.get("finished", False):
            continue
        gw = fix.get("event")
        if gw is None:
            continue
        for side in ("team_h", "team_a"):
            team_id = fix.get(side)
            if team_id is None:
                continue
            team_name = teams_by_id.get(int(team_id), str(team_id))
            if team_name not in snap:
                snap[team_name] = {}
            snap[team_name][str(gw)] = snap[team_name].get(str(gw), 0) + 1
    return snap


def _build_player_snapshot(elements: list[dict]) -> dict:
    """Compact snapshot: {element_id: {status, chance, news}} for injury tracking."""
    return {
        str(e["id"]): {
            "name": f"{e.get('first_name', '')} {e.get('second_name', '')}".strip(),
            "status": e.get("status", ""),
            "chance": e.get("chance_of_playing_next_round"),
            "news": e.get("news", ""),
        }
        for e in elements
        if e.get("id") is not None
    }


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

def _diff_fixtures(
    old_snap: dict,
    new_snap: dict,
    current_gw: int,
    horizon: int = 8,
) -> list[FixtureChange]:
    """Compare two fixture snapshots and return meaningful changes."""
    changes: list[FixtureChange] = []
    all_teams = set(old_snap) | set(new_snap)
    for team in all_teams:
        old_gws = old_snap.get(team, {})
        new_gws = new_snap.get(team, {})
        all_gws = set(old_gws) | set(new_gws)
        for gw_str in all_gws:
            gw = int(gw_str)
            if gw < current_gw or gw > current_gw + horizon:
                continue
            old_n = old_gws.get(gw_str, 0)
            new_n = new_gws.get(gw_str, 0)
            if old_n != new_n:
                changes.append(FixtureChange(
                    team=team, gw=gw, old_n_fixtures=old_n, new_n_fixtures=new_n
                ))
    return changes


_INJURY_NEWS_KEYWORDS = (
    "injury", "injured", "doubt", "doubtful", "scan", "surgery",
    "hamstring", "knee", "ankle", "calf", "groin", "thigh", "muscle",
    "suspended", "ban", "banned", "ruled out", "out for",
)

def _diff_players(old_snap: dict, new_snap: dict) -> list[NewsChange]:
    """Return NewsChange for status, chance, or meaningful news-text updates."""
    changes: list[NewsChange] = []
    for eid, new_data in new_snap.items():
        old_data = old_snap.get(eid, {})
        name = new_data.get("name", f"element_{eid}")
        for field_key in ("status", "chance"):
            old_val = old_data.get(field_key)
            new_val = new_data.get(field_key)
            if old_val != new_val:
                changes.append(NewsChange(
                    element_id=int(eid),
                    player_name=name,
                    field=field_key,
                    old_value=old_val,
                    new_value=new_val,
                ))
        # Only flag news text changes that contain injury-relevant keywords
        old_news = str(old_data.get("news", "") or "").lower()
        new_news = str(new_data.get("news", "") or "").lower()
        if old_news != new_news and any(kw in new_news for kw in _INJURY_NEWS_KEYWORDS):
            changes.append(NewsChange(
                element_id=int(eid),
                player_name=name,
                field="news",
                old_value=old_data.get("news", ""),
                new_value=new_data.get("news", ""),
            ))
    return changes


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class LiveMonitor:
    """Hourly FPL fixture + news change detector.

    Parameters
    ----------
    snapshot_path:
        Where to persist the last-run snapshot (JSON). Defaults to
        ``bot/cache/live_monitor_snapshot.json``.
    current_gw:
        Current gameweek. If None, determined from bootstrap.
    horizon:
        How many future GWs to watch for fixture changes.
    """

    def __init__(
        self,
        snapshot_path: Path | str | None = None,
        current_gw: int | None = None,
        horizon: int = 8,
    ) -> None:
        self.snapshot_path = Path(snapshot_path or SNAPSHOT_PATH)
        self.current_gw = current_gw
        self.horizon = horizon

    def _load_snapshot(self) -> dict:
        if self.snapshot_path.exists():
            try:
                return json.loads(self.snapshot_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {"fixtures": {}, "players": {}, "current_gw": 1}

    def _save_snapshot(self, snap: dict) -> None:
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(snap, indent=2))

    def run_once(self, force: bool = True) -> MonitorAlert:
        """Fetch fresh data, diff against snapshot, return alert.

        Parameters
        ----------
        force:
            Pass True to bypass the bot's normal HTTP cache and always fetch
            fresh data from the FPL API. Defaults to True.
        """
        from .data_collector import fetch_bootstrap, fetch_fixtures

        ts = datetime.now(timezone.utc).isoformat()
        alert = MonitorAlert(timestamp=ts)

        try:
            bootstrap = fetch_bootstrap(force=force)
            fixtures = fetch_fixtures(force=force)
        except Exception as exc:
            log.error("LiveMonitor: API fetch failed: %s", exc)
            return alert

        teams_by_id = {t["id"]: t["name"] for t in bootstrap.get("teams", [])}
        elements = bootstrap.get("elements", [])

        # Determine current GW
        current_gw = self.current_gw
        if current_gw is None:
            events = bootstrap.get("events", [])
            for ev in events:
                if ev.get("is_current"):
                    current_gw = ev["id"]
                    break
            if current_gw is None:
                current_gw = 1

        # Build new snapshots
        new_fix_snap = _build_fixture_snapshot(fixtures, teams_by_id)
        new_player_snap = _build_player_snapshot(elements)

        # Load and diff against old snapshot
        old = self._load_snapshot()
        old_fix_snap = old.get("fixtures", {})
        old_player_snap = old.get("players", {})

        fix_changes = _diff_fixtures(old_fix_snap, new_fix_snap, current_gw, self.horizon)
        player_changes = _diff_players(old_player_snap, new_player_snap)

        alert.fixture_changes = fix_changes
        alert.news_changes = player_changes
        alert.has_dgw_change = any(fc.is_dgw_added for fc in fix_changes)
        alert.has_bgw_change = any(fc.is_bgw_added for fc in fix_changes)
        alert.has_injury_news = any(
            nc.field in ("status", "chance") or "news" == nc.field
            for nc in player_changes
        )

        # Log summary
        if not alert.is_empty:
            log.info("%s", alert.summary())
        else:
            log.debug("LiveMonitor: no changes at %s", ts)

        # Persist new snapshot
        self._save_snapshot({
            "fixtures": new_fix_snap,
            "players": new_player_snap,
            "current_gw": current_gw,
            "checked_at": ts,
        })

        return alert
