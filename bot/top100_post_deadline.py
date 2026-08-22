"""Post-deadline elite-manager scouting.

FPL manager picks are intentionally treated as *post-lock information*. This
module refuses to request another manager's GW picks until that GW's deadline
has passed. The locked teams are fetched once, persisted, and then live score
comparisons reuse the saved picks plus the single public event-live feed.

That design lets the bot learn from elite-manager structure and strategy for
future gameweeks without using other managers' teams to make same-deadline
choices.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from .live_gameweek import live_score_distribution
from .top100_tracker import API_BASE, Top100Tracker


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _now(now_utc: datetime | None = None) -> datetime:
    value = now_utc or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PostDeadlineTopManagerScout:
    """Snapshot top-manager locked teams only after a GW deadline."""

    SAMPLE_SIZE = 25

    def __init__(self, data_dir: Path | None = None, sample_size: int | None = None):
        self.tracker = Top100Tracker(data_dir=data_dir)
        self.data_dir = self.tracker.data_dir
        self.sample_size = int(sample_size or self.SAMPLE_SIZE)

    def path_for_gw(self, gw: int) -> Path:
        return self.data_dir / f"gw{int(gw)}.json"

    def _event(self, bootstrap: dict, gw: int) -> dict | None:
        return next(
            (event for event in bootstrap.get("events", []) if int(event.get("id") or 0) == int(gw)),
            None,
        )

    def deadline_has_passed(
        self, bootstrap: dict, gw: int, now_utc: datetime | None = None
    ) -> bool:
        event = self._event(bootstrap, gw)
        if not event or not event.get("deadline_time"):
            return False
        try:
            return _parse_time(event["deadline_time"]) <= _now(now_utc)
        except (TypeError, ValueError):
            return False

    def load_snapshot(self, gw: int) -> dict | None:
        path = self.path_for_gw(gw)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def snapshot_locked_teams(
        self,
        *,
        gw: int,
        bootstrap: dict,
        session: requests.Session,
        persist: bool = True,
        now_utc: datetime | None = None,
    ) -> dict:
        """Fetch locked elite teams, refusing to run before ``gw``'s deadline."""
        if not self.deadline_has_passed(bootstrap, gw, now_utc=now_utc):
            return {
                "created": False,
                "blocked_pre_deadline": True,
                "reason": f"GW{int(gw)} deadline has not passed; manager picks will not be fetched.",
                "snapshot": None,
            }

        existing = self.load_snapshot(gw)
        if existing and existing.get("post_deadline_locked"):
            return {"created": False, "blocked_pre_deadline": False, "snapshot": existing}

        standings = self.tracker.fetch_standings(session)
        managers: list[dict] = []
        for row in standings[: self.sample_size]:
            entry_id = row.get("entry")
            if entry_id is None:
                continue
            picks_data = self.tracker._get(  # existing rate limiting + failure handling
                session,
                f"{API_BASE}/entry/{entry_id}/event/{int(gw)}/picks/",
            )
            if not picks_data:
                continue
            picks = picks_data.get("picks") or []
            entry_hist = picks_data.get("entry_history") or {}
            captain = next((p.get("element") for p in picks if p.get("is_captain")), None)
            managers.append({
                "entry": int(entry_id),
                "rank": row.get("rank"),
                "player_name": row.get("player_name"),
                "total": row.get("total"),
                "event_total": row.get("event_total"),
                "picks": [
                    {
                        "element": p.get("element"),
                        "position": p.get("position"),
                        "multiplier": p.get("multiplier"),
                        "is_captain": bool(p.get("is_captain")),
                        "is_vice_captain": bool(p.get("is_vice_captain")),
                    }
                    for p in picks
                ],
                "captain": captain,
                "chip": picks_data.get("active_chip"),
                "event_transfers": entry_hist.get("event_transfers"),
                "event_transfers_cost": entry_hist.get("event_transfers_cost"),
            })

        summary = self.tracker._summarise(int(gw), managers)
        strategy = self._strategy_summary(managers, bootstrap)
        event = self._event(bootstrap, gw) or {}
        snapshot = {
            "gw": int(gw),
            "league_id": self.tracker.OVERALL_LEAGUE_ID,
            "post_deadline_locked": True,
            "deadline_time": event.get("deadline_time"),
            "n_standings": len(standings),
            "n_picks_sampled": len(managers),
            "sample_target": self.sample_size,
            "standings": standings,
            "managers": managers,
            "summary": summary,
            "strategy": strategy,
        }
        if persist:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.path_for_gw(gw).write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return {"created": True, "blocked_pre_deadline": False, "snapshot": snapshot}

    def _strategy_summary(self, managers: list[dict], bootstrap: dict) -> dict:
        elements = {
            int(row["id"]): row
            for row in bootstrap.get("elements", [])
            if row.get("id") is not None
        }
        teams = {
            int(row["id"]): (row.get("name") or row.get("short_name") or str(row["id"]))
            for row in bootstrap.get("teams", [])
            if row.get("id") is not None
        }
        formations = Counter()
        captains = Counter()
        club_slots = Counter()
        club_managers = Counter()

        for manager in managers:
            squad_clubs: set[int] = set()
            pos = Counter()
            for pick in manager.get("picks", []):
                try:
                    el_id = int(pick.get("element"))
                except (TypeError, ValueError):
                    continue
                element = elements.get(el_id, {})
                team_id = int(element.get("team") or 0)
                if team_id:
                    club_slots[team_id] += 1
                    squad_clubs.add(team_id)
                if int(pick.get("position") or 99) <= 11:
                    pos[int(element.get("element_type") or 0)] += 1
            for team_id in squad_clubs:
                club_managers[team_id] += 1
            if pos:
                formations[f"{pos.get(2, 0)}-{pos.get(3, 0)}-{pos.get(4, 0)}"] += 1
            if manager.get("captain") is not None:
                captains[int(manager["captain"])] += 1

        n = len(managers)
        club_exposure = []
        for team_id, slots in club_slots.most_common():
            club_exposure.append({
                "team": team_id,
                "name": teams.get(team_id, str(team_id)),
                "avg_players_per_manager": round(slots / n, 2) if n else 0.0,
                "managers_with_player_pct": round(100 * club_managers[team_id] / n, 1) if n else 0.0,
            })

        captain_rows = []
        for el_id, count in captains.most_common(10):
            element = elements.get(el_id, {})
            captain_rows.append({
                "element": el_id,
                "name": element.get("web_name") or str(el_id),
                "count": count,
                "pct": round(100 * count / n, 1) if n else 0.0,
            })

        formation_rows = [
            {"formation": formation, "count": count, "pct": round(100 * count / n, 1) if n else 0.0}
            for formation, count in formations.most_common()
        ]

        observations: list[str] = []
        if club_exposure:
            top = club_exposure[0]
            observations.append(
                f"Highest squad exposure: {top['name']} at {top['avg_players_per_manager']:.2f} "
                f"players per sampled manager ({top['managers_with_player_pct']:.0f}% own at least one)."
            )
        if captain_rows:
            top = captain_rows[0]
            observations.append(f"Most common captain: {top['name']} ({top['pct']:.0f}% of sample).")
        if formation_rows:
            top = formation_rows[0]
            observations.append(f"Most common starting formation: {top['formation']} ({top['pct']:.0f}% of sample).")

        return {
            "sample_size": n,
            "club_exposure": club_exposure[:10],
            "captaincy": captain_rows,
            "formations": formation_rows,
            "observations": observations,
        }

    def live_comparison(self, snapshot: dict, live_data: dict, my_score: float | None = None) -> dict:
        managers = (snapshot or {}).get("managers") or []
        distribution = live_score_distribution(
            [m.get("picks") or [] for m in managers],
            live_data,
        )
        result = dict(distribution)
        if my_score is not None and distribution["sample_size"]:
            result["my_score"] = round(float(my_score), 1)
            result["vs_average"] = round(float(my_score) - distribution["average"], 1)
            result["vs_median"] = round(float(my_score) - distribution["median"], 1)
        return result


class LockedTop100Tracker(Top100Tracker):
    """Compatibility tracker that never downgrades a richer locked snapshot."""

    def fetch_and_store(self, gw: int, session: requests.Session | None = None) -> dict:
        path = self.data_dir / f"gw{int(gw)}.json"
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            existing = None
        if isinstance(existing, dict) and existing.get("post_deadline_locked"):
            return existing.get("summary") or {"gw": int(gw)}
        return super().fetch_and_store(gw, session=session)
