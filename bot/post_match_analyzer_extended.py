"""Post-match analyzer with human-readable learning messages."""
from __future__ import annotations

import json
import logging
from collections import Counter

from .post_match_analyzer import PostMatchAnalyzer
from .reflection_insights import build_reflection_messages

log = logging.getLogger(__name__)


class ReflectivePostMatchAnalyzer(PostMatchAnalyzer):
    """Keep the existing analyzer intact, then add concise retrospective lessons."""

    def _elite_snapshot(self, gw: int) -> dict | None:
        path = self.data_dir / "top100" / f"gw{int(gw)}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def _elite_structure_message(self, gw: int, bootstrap: dict, my_picks: list[dict]) -> tuple[str | None, dict]:
        """Compare my club exposure with the post-deadline elite sample.

        This is deliberately framed as evidence to review, not an instruction to
        copy. The teams were unavailable until after the GW deadline, so the
        comparison can only teach future decisions.
        """
        snapshot = self._elite_snapshot(gw)
        strategy = (snapshot or {}).get("strategy") or {}
        exposure = strategy.get("club_exposure") or []
        if not exposure:
            return None, strategy

        elements = {
            int(row["id"]): row
            for row in bootstrap.get("elements", [])
            if row.get("id") is not None
        }
        my_clubs = Counter()
        for pick in my_picks or []:
            try:
                element = elements.get(int(pick.get("element")), {})
            except (TypeError, ValueError):
                continue
            team_id = int(element.get("team") or 0)
            if team_id:
                my_clubs[team_id] += 1

        gaps = []
        for row in exposure:
            team_id = int(row.get("team") or 0)
            elite_avg = float(row.get("avg_players_per_manager") or 0.0)
            mine = int(my_clubs.get(team_id, 0))
            gap = elite_avg - mine
            # Require a meaningful structural difference, not a tiny rounding gap.
            if elite_avg >= 1.5 and gap >= 0.75:
                gaps.append((gap, elite_avg, mine, row.get("name") or str(team_id)))
        if not gaps:
            return None, strategy

        _, elite_avg, mine, team_name = max(gaps)
        message = (
            f"Elite structure check: the post-deadline sample averaged {elite_avg:.2f} "
            f"{team_name} players while I had {mine}. I was materially lighter on "
            f"{team_name}; I should review whether my fixture/model assumptions justified "
            "that gap. This is a learning signal for future GWs, not a reason to copy "
            "other managers blindly."
        )
        return message, strategy

    def analyze(self, gw, live_data, bootstrap, my_picks, forecasts, fixtures=None):
        fixture_rows = fixtures
        if fixture_rows is None:
            try:
                from . import data_collector
                fixture_rows = data_collector.fetch_fixtures()
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not fetch fixtures for reflection messages (%s)", exc)
                fixture_rows = []

        result = super().analyze(
            gw=gw,
            live_data=live_data,
            bootstrap=bootstrap,
            my_picks=my_picks,
            forecasts=forecasts,
            fixtures=fixture_rows,
        )

        messages = build_reflection_messages(
            gw=int(gw),
            live_data=live_data,
            bootstrap=bootstrap,
            my_picks=my_picks,
            fixtures=fixture_rows,
        )

        # Make forecast failures explicit in the same plain-English voice.
        my_ids = {
            int(p["element"])
            for p in (my_picks or [])
            if p.get("element") is not None
        }
        owned_misses = [
            row for row in result.get("underperformers", [])
            if int(row.get("element", -1)) in my_ids
        ]
        if owned_misses:
            miss = min(owned_misses, key=lambda row: float(row.get("delta", 0.0)))
            forecast_msg = (
                f"Forecast miss: I expected {miss['name']} to score {miss['expected']:.1f} "
                f"points but got {miss['actual']:.0f} ({miss['delta']:+.1f}). I should lower "
                "confidence in the signals that drove that pick and check whether the miss was "
                "minutes, role, fixture assumptions or simple variance."
            )
            messages.insert(0, forecast_msg)

        elite_message, elite_strategy = self._elite_structure_message(int(gw), bootstrap, my_picks)
        if elite_message:
            # Put structural learning near the front but behind a concrete
            # forecast miss, if one exists.
            insert_at = 1 if owned_misses else 0
            messages.insert(insert_at, elite_message)
        if elite_strategy:
            result["elite_strategy"] = elite_strategy

        result["reflection_messages"] = messages[:5]

        # The parent writes before this extension runs, so rewrite the same JSON
        # once to persist the additional field. This remains inside data/ and is
        # picked up by the orchestrator's normal state commit.
        path = self.post_match_dir / f"gw{int(gw)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
