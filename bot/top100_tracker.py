"""
Top-100 manager tracker.

Snapshots the FPL overall league (id 314) each gameweek and inspects what the
very best managers actually own, so the planner's own picks can be compared
against the field's consensus.

Only public read endpoints are used — no authentication:

    /api/leagues-classic/314/standings/?page_standings=N
    /api/entry/{entry_id}/event/{gw}/picks/

The standings endpoint returns 50 managers per page, so two pages are needed
for the top 100. Picks are only fetched for the top ``PICKS_SAMPLE_SIZE``
managers: 100 extra requests per run would risk rate limiting for very little
extra signal.

Snapshots (raw standings + derived summary) go to ``data/top100/gw{N}.json``.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
API_BASE = "https://fantasy.premierleague.com/api"
REQUEST_TIMEOUT = 15
#: Seconds between consecutive API requests.
RATE_LIMIT_SLEEP = 0.5
#: Managers per standings page (FPL's fixed page size).
PAGE_SIZE = 50


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not read %s (%s)", path, exc)
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class Top100Tracker:
    """Fetch, store and analyse what the top overall managers are doing."""

    OVERALL_LEAGUE_ID = 314
    DATA_DIR = Path("data/top100")
    #: How many managers to pull full picks for.
    PICKS_SAMPLE_SIZE = 10
    #: How many managers to record in standings.
    TRACK_SIZE = 100

    def __init__(self, data_dir: Path | None = None):
        # Resolve relative to the repo root so the class works from any cwd.
        self.data_dir = Path(data_dir) if data_dir else ROOT / self.DATA_DIR

    # -- HTTP -------------------------------------------------------------

    def _get(self, session: requests.Session, url: str) -> dict | None:
        """GET a public endpoint. Returns None on any failure — one bad
        manager or a transient 404 must never abort the whole run."""
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            if not resp.ok:
                log.warning("GET %s -> %d", url, resp.status_code)
                return None
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("GET %s failed (%s)", url, exc)
            return None
        finally:
            time.sleep(RATE_LIMIT_SLEEP)

    def fetch_standings(self, session: requests.Session) -> list[dict]:
        """Top ``TRACK_SIZE`` managers, paging until the target is reached."""
        results: list[dict] = []
        pages = -(-self.TRACK_SIZE // PAGE_SIZE)  # ceil
        for page in range(1, pages + 1):
            data = self._get(
                session,
                f"{API_BASE}/leagues-classic/{self.OVERALL_LEAGUE_ID}"
                f"/standings/?page_standings={page}",
            )
            if not data:
                break
            standings = data.get("standings") or {}
            results.extend(standings.get("results") or [])
            if not standings.get("has_next"):
                break
        return results[: self.TRACK_SIZE]

    # -- main -------------------------------------------------------------

    def fetch_and_store(self, gw: int, session: requests.Session | None = None) -> dict:
        """Snapshot the top of the overall league for ``gw``.

        Returns a summary containing ``most_owned_players``,
        ``popular_transfers`` (vs the previous stored GW) and ``chips_played``.
        """
        own_session = session is None
        session = session or requests.Session()
        try:
            standings = self.fetch_standings(session)
            if not standings:
                log.warning("No standings returned for league %d", self.OVERALL_LEAGUE_ID)
                return {"gw": int(gw), "error": "standings unavailable"}

            managers: list[dict] = []
            for row in standings[: self.PICKS_SAMPLE_SIZE]:
                entry_id = row.get("entry")
                if entry_id is None:
                    continue
                picks_data = self._get(
                    session, f"{API_BASE}/entry/{entry_id}/event/{gw}/picks/"
                )
                if not picks_data:
                    # Manager may not have set a team, or the GW hasn't started.
                    continue
                picks = picks_data.get("picks") or []
                entry_hist = picks_data.get("entry_history") or {}
                captain = next(
                    (p["element"] for p in picks if p.get("is_captain")), None
                )
                managers.append({
                    "entry": entry_id,
                    "rank": row.get("rank"),
                    "player_name": row.get("player_name"),
                    "total": row.get("total"),
                    "event_total": row.get("event_total"),
                    "picks": [
                        {"element": p.get("element"),
                         "position": p.get("position"),
                         "multiplier": p.get("multiplier"),
                         "is_captain": bool(p.get("is_captain"))}
                        for p in picks
                    ],
                    "captain": captain,
                    "chip": picks_data.get("active_chip"),
                    "event_transfers": entry_hist.get("event_transfers"),
                    "event_transfers_cost": entry_hist.get("event_transfers_cost"),
                })

            summary = self._summarise(gw, managers)
            snapshot = {
                "gw": int(gw),
                "league_id": self.OVERALL_LEAGUE_ID,
                "n_standings": len(standings),
                "n_picks_sampled": len(managers),
                "standings": standings,
                "managers": managers,
                "summary": summary,
            }
            _write_json(self.data_dir / f"gw{gw}.json", snapshot)
            log.info("Top-100 snapshot GW%d: %d standings, %d picks sampled",
                     gw, len(standings), len(managers))
            return summary

        finally:
            if own_session:
                session.close()

    def _summarise(self, gw: int, managers: list[dict]) -> dict:
        """Ownership, captaincy, chips, and transfers vs the previous snapshot."""
        n = len(managers)
        owned = Counter()
        started = Counter()
        captains = Counter()
        chips = Counter()

        for m in managers:
            for p in m["picks"]:
                el = p.get("element")
                if el is None:
                    continue
                owned[el] += 1
                if (p.get("multiplier") or 0) > 0:
                    started[el] += 1
            if m.get("captain"):
                captains[m["captain"]] += 1
            if m.get("chip"):
                chips[m["chip"]] += 1

        most_owned = [
            {"element": el, "count": cnt,
             "ownership_pct": round(100.0 * cnt / n, 1) if n else 0.0,
             "started": started.get(el, 0)}
            for el, cnt in owned.most_common(20)
        ]

        prev = _read_json(self.data_dir / f"gw{gw - 1}.json", None)
        popular_in: list[dict] = []
        popular_out: list[dict] = []
        if prev:
            prev_owned = Counter()
            for m in prev.get("managers", []):
                for p in m.get("picks", []):
                    if p.get("element") is not None:
                        prev_owned[p["element"]] += 1
            deltas = Counter()
            for el in set(owned) | set(prev_owned):
                deltas[el] = owned.get(el, 0) - prev_owned.get(el, 0)
            popular_in = [
                {"element": el, "net_in": d}
                for el, d in deltas.most_common(10) if d > 0
            ]
            popular_out = [
                {"element": el, "net_out": -d}
                for el, d in sorted(deltas.items(), key=lambda kv: kv[1])[:10] if d < 0
            ]

        return {
            "gw": int(gw),
            "sample_size": n,
            "most_owned_players": most_owned,
            "most_captained": [
                {"element": el, "count": cnt} for el, cnt in captains.most_common(5)
            ],
            "popular_transfers": {"in": popular_in, "out": popular_out},
            "chips_played": dict(chips),
        }

    # -- trends -----------------------------------------------------------

    def analyze_trends(self) -> dict:
        """Aggregate every stored snapshot into season-level patterns.

        Returns
        -------
        dict
            ``core_holdings``    players owned in the most snapshots
            ``recent_transfers`` net ownership moves in the latest GW
            ``chip_timing``      which GWs the sample played each chip
            ``captain_history``  most-captained player per GW
        """
        files = sorted(
            self.data_dir.glob("gw*.json"),
            key=lambda p: int("".join(c for c in p.stem if c.isdigit()) or 0),
        )
        if not files:
            log.info("No top-100 snapshots stored yet.")
            return {"gws_analyzed": [], "core_holdings": [], "recent_transfers": {},
                    "chip_timing": {}, "captain_history": {}}

        gws: list[int] = []
        holding_gws = Counter()      # element → number of GWs present
        holding_share = Counter()    # element → cumulative ownership count
        chip_timing: dict[str, list[int]] = {}
        captain_history: dict[str, int] = {}
        recent_transfers: dict = {}

        for path in files:
            snap = _read_json(path, None)
            if not snap:
                continue
            gw = int(snap.get("gw", 0))
            gws.append(gw)
            summary = snap.get("summary") or {}

            for rec in summary.get("most_owned_players", []):
                holding_gws[rec["element"]] += 1
                holding_share[rec["element"]] += rec.get("count", 0)

            for chip, _cnt in (summary.get("chips_played") or {}).items():
                chip_timing.setdefault(chip, []).append(gw)

            top_cap = (summary.get("most_captained") or [None])[0]
            if top_cap:
                captain_history[str(gw)] = top_cap["element"]

            recent_transfers = summary.get("popular_transfers", {})

        n_gws = len(gws) or 1
        core = [
            {"element": el,
             "gws_in_top20": cnt,
             "presence_pct": round(100.0 * cnt / n_gws, 1),
             "avg_owners": round(holding_share[el] / cnt, 2) if cnt else 0.0}
            for el, cnt in holding_gws.most_common(20)
        ]

        return {
            "gws_analyzed": sorted(gws),
            "core_holdings": core,
            "recent_transfers": recent_transfers,
            "chip_timing": chip_timing,
            "captain_history": captain_history,
        }
