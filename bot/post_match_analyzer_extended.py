"""Post-match analyzer with human-readable learning messages."""
from __future__ import annotations

import json
import logging

from .post_match_analyzer import PostMatchAnalyzer
from .reflection_insights import build_reflection_messages

log = logging.getLogger(__name__)


class ReflectivePostMatchAnalyzer(PostMatchAnalyzer):
    """Keep the existing analyzer intact, then add concise retrospective lessons."""

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

        result["reflection_messages"] = messages[:5]

        # The parent writes before this extension runs, so rewrite the same JSON
        # once to persist the additional field. This remains inside data/ and is
        # picked up by the orchestrator's normal state commit.
        path = self.post_match_dir / f"gw{int(gw)}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return result
