"""
Summer transfer window monitor.

New Premier League signings appear in ``bootstrap-static`` as brand-new element
IDs, often days before they are widely owned. This module diffs the current
element list against the IDs seen on the previous run and evaluates anything
new, so a genuinely strong signing can be picked up early.

The 2026 summer window closes at 2026-09-01 22:00 UTC (23:00 BST). All time
comparisons are done in UTC — no BST offset is ever hardcoded.

State lives in ``data/known_element_ids.json``. On the very first run the file
is empty, so every player in the game would otherwise look "new"; the monitor
detects that case, seeds the file, and reports no signings.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

#: Summer 2026 window close — 22:00 UTC = 23:00 BST on deadline day.
WINDOW_CLOSE_UTC = datetime(2026, 9, 1, 22, 0, 0, tzinfo=timezone.utc)

#: ep_next above which a new signing is worth considering regardless of club.
EP_NEXT_THRESHOLD = 4.0
#: How many clubs count as "top" when judging a signing's likely quality.
TOP_CLUB_COUNT = 6


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read %s (%s) — using default", path, exc)
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class TransferWindowMonitor:
    """Detect and evaluate new players added during the transfer window."""

    KNOWN_IDS_FILE = Path("data/known_element_ids.json")

    def __init__(self, data_dir: Path | None = None):
        base = Path(data_dir) if data_dir else DATA_DIR
        self.known_ids_file = base / self.KNOWN_IDS_FILE.name

    # -- window state -----------------------------------------------------

    def is_window_open(self) -> bool:
        """True while the summer window is still open (UTC comparison)."""
        return datetime.now(timezone.utc) < WINDOW_CLOSE_UTC

    def days_remaining(self) -> float:
        """Days until the window closes (negative once shut)."""
        return (WINDOW_CLOSE_UTC - datetime.now(timezone.utc)).total_seconds() / 86400.0

    # -- known IDs --------------------------------------------------------

    def _load_known_ids(self) -> set[int]:
        raw = _read_json(self.known_ids_file, [])
        if not isinstance(raw, list):
            return set()
        out: set[int] = set()
        for v in raw:
            try:
                out.add(int(v))
            except (TypeError, ValueError):
                continue
        return out

    def update_known_ids(self, bootstrap: dict) -> None:
        """Persist the current element IDs as the new baseline."""
        ids = sorted(int(e["id"]) for e in bootstrap.get("elements", []) if e.get("id") is not None)
        _write_json(self.known_ids_file, ids)
        log.info("Known element IDs updated: %d players", len(ids))

    # -- detection --------------------------------------------------------

    def check_new_signings(self, bootstrap: dict) -> list[dict]:
        """Evaluate every element ID not seen on the previous run.

        Returns an empty list on the first ever run (the baseline is seeded
        instead), and whenever nothing new has appeared. Does not itself
        update the baseline — call :meth:`update_known_ids` once the caller
        has acted on the result, so a crash mid-run doesn't lose a signing.
        """
        elements = bootstrap.get("elements", [])
        if not elements:
            return []

        known = self._load_known_ids()
        if not known:
            # First run: everything would look new. Seed and report nothing.
            self.update_known_ids(bootstrap)
            log.info("Seeded known element IDs baseline (%d players) — "
                     "no signings reported on first run", len(elements))
            return []

        current_ids = {int(e["id"]) for e in elements if e.get("id") is not None}
        new_ids = current_ids - known
        if not new_ids:
            log.info("No new elements since last check (%d known)", len(known))
            return []

        teams = {int(t["id"]): t for t in bootstrap.get("teams", [])}
        pos_short = {
            int(t["id"]): t.get("singular_name_short", "?")
            for t in bootstrap.get("element_types", [])
        }
        top_clubs = self._top_clubs(teams)

        out: list[dict] = []
        for el in elements:
            el_id = int(el.get("id", -1))
            if el_id not in new_ids:
                continue
            out.append(self._evaluate(el, teams, pos_short, top_clubs))

        out.sort(key=lambda r: (not r["worth_considering"], -r["ep_next"]))
        log.info("Detected %d new element(s); %d worth considering",
                 len(out), sum(1 for r in out if r["worth_considering"]))
        return out

    def _top_clubs(self, teams: dict[int, dict]) -> set[int]:
        """The strongest clubs by FPL's own strength ratings — never hardcoded.

        A signing for one of these is worth watching even before FPL has
        published a meaningful ep_next for them.
        """
        def strength(t: dict) -> float:
            overall = _to_float(t.get("strength_overall_home")) + _to_float(
                t.get("strength_overall_away"))
            return overall if overall else _to_float(t.get("strength"))

        ranked = sorted(teams.values(), key=strength, reverse=True)
        return {int(t["id"]) for t in ranked[:TOP_CLUB_COUNT]}

    def _evaluate(self, el: dict, teams: dict, pos_short: dict,
                  top_clubs: set[int]) -> dict:
        el_id = int(el["id"])
        team_id = int(el.get("team", 0))
        team = teams.get(team_id, {})
        ep_next = _to_float(el.get("ep_next"))
        cost = _to_float(el.get("now_cost")) / 10.0
        status = (el.get("status") or "a").strip()
        news = (el.get("news") or "").strip()
        is_top_club = team_id in top_clubs

        # A brand-new element with no PL minutes has no history to judge on.
        # FPL's ep_next is the only forward-looking number available, so treat
        # a strong ep_next, or a signing for one of the strongest clubs, as the
        # trigger for consideration. Overseas arrivals at mid/lower-table clubs
        # default to "watch only" — they rarely start immediately.
        worth = (ep_next > EP_NEXT_THRESHOLD) or is_top_club

        if status != "a":
            worth = False
            reason = (f"Unavailable on arrival (status '{status}'"
                      + (f": {news}" if news else "") + ").")
        elif ep_next > EP_NEXT_THRESHOLD and is_top_club:
            reason = (f"Top-club signing ({team.get('name', '?')}) with strong "
                      f"ep_next {ep_next:.1f} — strong candidate.")
        elif ep_next > EP_NEXT_THRESHOLD:
            reason = (f"ep_next {ep_next:.1f} exceeds the {EP_NEXT_THRESHOLD:.1f} "
                      f"threshold — worth evaluating against the squad.")
        elif is_top_club:
            reason = (f"Signed by {team.get('name', '?')}, one of the strongest "
                      f"clubs — worth watching even at ep_next {ep_next:.1f}.")
        else:
            reason = (f"ep_next {ep_next:.1f} below {EP_NEXT_THRESHOLD:.1f} at "
                      f"{team.get('name', '?')} — new arrivals at mid-table clubs "
                      f"rarely start immediately.")

        return {
            "element": el_id,
            "name": el.get("web_name") or f"element {el_id}",
            "team": team.get("name", "?"),
            "position": pos_short.get(int(el.get("element_type", 0)), "?"),
            "cost": round(cost, 1),
            "ep_next": round(ep_next, 2),
            "status": status,
            "news": news,
            "top_club": is_top_club,
            "worth_considering": bool(worth),
            "reason": reason,
        }

    # -- idea-list bridge -------------------------------------------------

    @staticmethod
    def to_ideas(signings: list[dict]) -> list[dict]:
        """Convert worthwhile signings into ``idea_list`` entries.

        Priority is deliberately modest (max 0.6): a new signing is never as
        urgent as replacing an injured or suspended player.
        """
        ideas: list[dict] = []
        for s in signings:
            if not s.get("worth_considering"):
                continue
            priority = 0.6 if (s.get("top_club") and s.get("ep_next", 0) > EP_NEXT_THRESHOLD) else 0.4
            ideas.append({
                "action": "transfer_in",
                "element": s["element"],
                "name": s["name"],
                "reason": f"New signing: {s['reason']}",
                "priority": priority,
            })
        return ideas
