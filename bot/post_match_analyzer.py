"""
Post-gameweek analysis — runs once all matches in a GW are finished.

Compares what actually happened (FPL ``/event/{gw}/live/``) against what the
forecaster expected, then turns the differences into a prioritised *idea list*
that :mod:`pre_deadline_simulator` uses as an override/veto layer over the MILP
plan.

Everything here is derived from FPL's own data — live stats, bootstrap player
status, and the forecast table. No external AI or ML services are called.

Outputs are written to ``data/post_match/gw{N}.json``.

Suspension rules (Premier League):
    5 yellow cards  → 1-match ban
    10 yellow cards → 2-match ban
    15 yellow cards → 3-match ban
    1 red card      → immediate ban (1+ matches)

The yellow-card tally is cumulative across gameweeks and stored in
``data/yellow_card_tally.json``. Because the orchestrator may run several times
per gameweek, the tally records which gameweeks have already been counted and
refuses to add the same GW twice.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
POST_MATCH_DIR = DATA_DIR / "post_match"
YELLOW_TALLY_FILE = DATA_DIR / "yellow_card_tally.json"

#: Actual − expected above this is an overperformance.
OVERPERFORM_DELTA = 4.0
#: Actual − expected below this (negative) is an underperformance.
UNDERPERFORM_DELTA = -3.0
#: Yellow-card counts that trigger a ban.
YELLOW_BAN_THRESHOLDS = (5, 10, 15)
#: Max extra spend (£m) allowed when proposing a replacement.
REPLACEMENT_BUDGET_SLACK = 2.0
#: Replacement suggestions per outgoing player.
REPLACEMENTS_PER_PLAYER = 3


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:  # noqa: BLE001 — corrupt file must not stop analysis
        log.warning("Could not read %s (%s) — using default", path, exc)
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Live-data normalisation
# ---------------------------------------------------------------------------

def _normalise_live(live_data: dict) -> dict[int, dict]:
    """Accept either the raw ``/event/{gw}/live/`` body or a pre-mapped dict.

    Raw form: ``{"elements": [{"id": 1, "stats": {...}}, ...]}``
    Mapped form (as built by :mod:`updater`): ``{element_id: stats_dict}``
    """
    if not live_data:
        return {}
    if isinstance(live_data.get("elements"), list):
        return {
            int(el["id"]): (el.get("stats") or {})
            for el in live_data["elements"]
            if el.get("id") is not None
        }
    out: dict[int, dict] = {}
    for key, val in live_data.items():
        try:
            out[int(key)] = val if isinstance(val, dict) else {}
        except (TypeError, ValueError):
            continue
    return out


def _num(stats: dict, key: str) -> float:
    try:
        return float(stats.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class PostMatchAnalyzer:
    """Turn a finished gameweek into a prioritised list of actions."""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.post_match_dir = self.data_dir / "post_match"
        self.tally_file = self.data_dir / "yellow_card_tally.json"

    # -- yellow card tally ------------------------------------------------

    def _load_tally(self) -> dict:
        raw = _read_json(self.tally_file, {})
        if not isinstance(raw, dict):
            raw = {}
        # Seed file ships as ``{}``; normalise to the versioned shape.
        counted = raw.get("counted_gws") or []
        tally = raw.get("tally") or {}
        return {
            "counted_gws": [int(g) for g in counted],
            "tally": {str(k): int(v) for k, v in tally.items()},
        }

    def update_yellow_tally(self, gw: int, live_by_id: dict[int, dict]) -> dict[str, int]:
        """Add this GW's yellows to the running season total, idempotently.

        Returns the full ``{element_id_str: total_yellows}`` mapping. If ``gw``
        has already been counted the stored totals are returned unchanged — the
        orchestrator runs every 2 hours and must not double-count.
        """
        state = self._load_tally()
        if gw in state["counted_gws"]:
            log.info("GW%d yellows already counted — tally unchanged", gw)
            return state["tally"]

        for el_id, stats in live_by_id.items():
            yellows = int(_num(stats, "yellow_cards"))
            if yellows:
                key = str(el_id)
                state["tally"][key] = state["tally"].get(key, 0) + yellows

        state["counted_gws"] = sorted(set(state["counted_gws"] + [int(gw)]))
        _write_json(self.tally_file, state)
        log.info("Yellow tally updated for GW%d (%d players carded)",
                 gw, sum(1 for s in live_by_id.values() if _num(s, "yellow_cards")))
        return state["tally"]

    # -- main -------------------------------------------------------------

    def analyze(
        self,
        gw: int,
        live_data: dict,
        bootstrap: dict,
        my_picks: list[dict],
        forecasts: pd.DataFrame,
        fixtures: list | None = None,
    ) -> dict:
        """Analyse a finished gameweek.

        Parameters
        ----------
        gw:
            The gameweek that just finished.
        live_data:
            ``/event/{gw}/live/`` body, or ``{element_id: stats}``.
        bootstrap:
            Current ``bootstrap-static`` JSON (post-GW, so ``status`` reflects
            any injury picked up during the round).
        my_picks:
            My squad — list of dicts with an ``element`` key.
        forecasts:
            Forecast table with ``element``/``gw``/``xpts`` columns. Rows for
            ``gw`` are required for the over/underperformance section; if the
            table was regenerated for a later GW that section is skipped rather
            than reporting bogus deltas.
        fixtures:
            Optional fixture list, used to rank replacement suggestions by
            next-GW difficulty. Fetched from cache when omitted.

        Returns
        -------
        dict
            See module docstring / task spec for the schema.
        """
        live_by_id = _normalise_live(live_data)
        elements = {int(e["id"]): e for e in bootstrap.get("elements", [])}
        teams = {int(t["id"]): t for t in bootstrap.get("teams", [])}
        pos_short = {
            int(t["id"]): t.get("singular_name_short", "?")
            for t in bootstrap.get("element_types", [])
        }
        my_ids = {int(p["element"]) for p in (my_picks or []) if p.get("element") is not None}

        def name_of(el_id: int) -> str:
            el = elements.get(el_id, {})
            return el.get("web_name") or f"element {el_id}"

        notes: list[str] = []

        # 1. Over / underperformance vs forecast --------------------------
        overperformers, underperformers = self._performance_deltas(
            gw, live_by_id, forecasts, name_of, notes
        )

        # 2. Injuries picked up (whole game, but priority given to my squad)
        injured_after_gw = self._injuries(elements, my_ids, name_of)

        # 3. Suspensions from cards --------------------------------------
        tally = self.update_yellow_tally(gw, live_by_id)
        suspensions = self._suspensions(gw, live_by_id, tally, elements, my_ids, name_of)

        # 4. Idea list ----------------------------------------------------
        idea_list = self._build_idea_list(
            gw=gw,
            my_ids=my_ids,
            injured=injured_after_gw,
            suspensions=suspensions,
            underperformers=underperformers,
            elements=elements,
            teams=teams,
            pos_short=pos_short,
            forecasts=forecasts,
            fixtures=fixtures,
            name_of=name_of,
        )

        notes.append(
            f"GW{gw}: {len(overperformers)} overperformer(s), "
            f"{len(underperformers)} underperformer(s), "
            f"{len([i for i in injured_after_gw if i['in_my_squad']])} injury flag(s) in my squad, "
            f"{len(suspensions)} suspension risk(s)."
        )

        result = {
            "gw": int(gw),
            "overperformers": overperformers,
            "underperformers": underperformers,
            "injured_after_gw": injured_after_gw,
            "suspensions": suspensions,
            "idea_list": idea_list,
            "notes": " ".join(notes),
        }

        _write_json(self.post_match_dir / f"gw{gw}.json", result)
        log.info("Post-match analysis for GW%d written (%d ideas)", gw, len(idea_list))
        return result

    # -- sections ---------------------------------------------------------

    def _performance_deltas(self, gw, live_by_id, forecasts, name_of, notes):
        over: list[dict] = []
        under: list[dict] = []

        if forecasts is None or not isinstance(forecasts, pd.DataFrame) or forecasts.empty:
            notes.append("No forecast table supplied — performance deltas skipped.")
            return over, under
        if "gw" not in forecasts.columns or "element" not in forecasts.columns:
            notes.append("Forecast table missing element/gw columns — deltas skipped.")
            return over, under

        gw_rows = forecasts[forecasts["gw"] == gw]
        if gw_rows.empty:
            # The forecast table was built for a later GW (the usual case when
            # the forecaster is re-run at analysis time). No honest comparison
            # is possible, so report nothing rather than nonsense deltas.
            notes.append(
                f"Forecast table has no GW{gw} rows (built for a later GW) — "
                "performance deltas skipped."
            )
            return over, under

        expected = (
            gw_rows.groupby("element")["xpts"].mean().to_dict()
            if "xpts" in gw_rows.columns else {}
        )

        for el_id, exp in expected.items():
            el_id = int(el_id)
            stats = live_by_id.get(el_id)
            if stats is None:
                continue
            actual = _num(stats, "total_points")
            delta = actual - float(exp)
            record = {
                "element": el_id,
                "name": name_of(el_id),
                "actual": round(actual, 2),
                "expected": round(float(exp), 2),
                "delta": round(delta, 2),
            }
            if delta > OVERPERFORM_DELTA:
                over.append(record)
            elif delta < UNDERPERFORM_DELTA:
                under.append(record)

        over.sort(key=lambda r: -r["delta"])
        under.sort(key=lambda r: r["delta"])
        return over, under

    def _injuries(self, elements, my_ids, name_of) -> list[dict]:
        """Players whose FPL availability status is no longer 'a' (available)."""
        out: list[dict] = []
        for el_id, el in elements.items():
            status = (el.get("status") or "a").strip()
            if status == "a":
                continue
            chance = el.get("chance_of_playing_next_round")
            out.append({
                "element": el_id,
                "name": name_of(el_id),
                "status": status,
                "chance": int(chance) if chance is not None else 0,
                "news": (el.get("news") or "").strip(),
                "in_my_squad": el_id in my_ids,
            })
        # My squad first, then most severe (lowest chance).
        out.sort(key=lambda r: (not r["in_my_squad"], r["chance"]))
        return out

    def _suspensions(self, gw, live_by_id, tally, elements, my_ids, name_of) -> list[dict]:
        """Card-driven ban risks: reds this GW, and yellow totals at a threshold."""
        out: list[dict] = []
        for el_id, el in elements.items():
            stats = live_by_id.get(el_id, {})
            reds_this_gw = int(_num(stats, "red_cards"))
            total_yellows = int(tally.get(str(el_id), 0))

            banned = reds_this_gw > 0
            at_threshold = total_yellows in YELLOW_BAN_THRESHOLDS
            if not (banned or at_threshold):
                continue

            if banned:
                reason = f"Red card in GW{gw} — automatic suspension."
            else:
                n_games = YELLOW_BAN_THRESHOLDS.index(total_yellows) + 1
                reason = (f"{total_yellows} yellow cards — "
                          f"{n_games}-match suspension.")

            out.append({
                "element": el_id,
                "name": name_of(el_id),
                "yellow_cards": total_yellows,
                "red_cards": reds_this_gw,
                "banned": banned,
                "reason": reason,
                "in_my_squad": el_id in my_ids,
            })
        out.sort(key=lambda r: (not r["in_my_squad"], not r["banned"]))
        return out

    # -- idea list --------------------------------------------------------

    def _build_idea_list(self, gw, my_ids, injured, suspensions, underperformers,
                         elements, teams, pos_short, forecasts, fixtures, name_of) -> list[dict]:
        ideas: list[dict] = []
        flagged_out: dict[int, float] = {}

        # Red cards / active bans in my squad — most urgent.
        for s in suspensions:
            if not s["in_my_squad"]:
                continue
            priority = 1.0 if s["banned"] else 0.9
            ideas.append({
                "action": "transfer_out",
                "element": s["element"],
                "name": s["name"],
                "reason": s["reason"],
                "priority": priority,
            })
            flagged_out[s["element"]] = max(flagged_out.get(s["element"], 0.0), priority)

        # Injuries / doubts in my squad.
        for inj in injured:
            if not inj["in_my_squad"] or inj["element"] in flagged_out:
                continue
            # status 'i' (injured), 'u' (unavailable), 's' (suspended) are hard
            # outs; 'd' (doubtful) scales with the reported chance of playing.
            if inj["status"] in ("i", "u", "s"):
                priority = 0.9
            else:
                priority = round(0.9 * (1.0 - inj["chance"] / 100.0), 3)
            if priority < 0.2:
                continue
            reason = inj["news"] or f"FPL status '{inj['status']}', {inj['chance']}% chance of playing."
            ideas.append({
                "action": "transfer_out",
                "element": inj["element"],
                "name": inj["name"],
                "reason": reason,
                "priority": priority,
            })
            flagged_out[inj["element"]] = priority

        # Underperformers in my squad — consideration only, never urgent.
        # severity is capped at 1.0 so priority stays within [0, 0.5].
        for u in underperformers:
            el_id = u["element"]
            if el_id not in my_ids or el_id in flagged_out:
                continue
            severity = min(1.0, abs(u["delta"]) / 10.0)
            priority = round(0.5 * severity, 3)
            ideas.append({
                "action": "transfer_out",
                "element": el_id,
                "name": u["name"],
                "reason": (f"Scored {u['actual']:.0f} vs {u['expected']:.1f} expected "
                           f"in GW{gw} (delta {u['delta']:+.1f})."),
                "priority": priority,
            })
            flagged_out[el_id] = priority

        # Replacement suggestions for everyone flagged out.
        fdr = self._next_gw_difficulty(gw + 1, fixtures, teams)
        for el_id, priority in flagged_out.items():
            for cand in self._suggest_replacements(
                el_id, my_ids, elements, pos_short, forecasts, fdr, gw + 1
            ):
                ideas.append({
                    "action": "transfer_in",
                    "element": cand["element"],
                    "name": cand["name"],
                    "reason": (f"Replacement for {name_of(el_id)}: "
                               f"{cand['position']} £{cand['cost']:.1f}m, "
                               f"xPts {cand['xpts']:.2f}, GW{gw + 1} FDR {cand['fdr']:.1f}."),
                    "priority": round(priority * 0.9, 3),
                })

        ideas.sort(key=lambda i: -i["priority"])
        return ideas

    def _next_gw_difficulty(self, next_gw: int, fixtures: list | None,
                            teams: dict) -> dict[int, float]:
        """Mean fixture difficulty per team for ``next_gw`` (3.0 when unknown)."""
        if fixtures is None:
            try:
                from . import data_collector
                fixtures = data_collector.fetch_fixtures()
            except Exception as exc:  # noqa: BLE001
                log.warning("Fixture fetch failed (%s) — using neutral difficulty", exc)
                fixtures = []

        acc: dict[int, list[float]] = {}
        for fx in fixtures or []:
            if fx.get("event") != next_gw:
                continue
            h, a = fx.get("team_h"), fx.get("team_a")
            if h is not None:
                acc.setdefault(int(h), []).append(float(fx.get("team_h_difficulty") or 3))
            if a is not None:
                acc.setdefault(int(a), []).append(float(fx.get("team_a_difficulty") or 3))

        # Teams with no fixture (blank GW) get the worst possible difficulty so
        # they are never suggested as a replacement.
        out = {int(t): 5.0 for t in teams}
        for team_id, vals in acc.items():
            out[team_id] = sum(vals) / len(vals)
        return out

    def _suggest_replacements(self, out_element, my_ids, elements, pos_short,
                              forecasts, fdr, next_gw) -> list[dict]:
        """Best same-position, affordable, available replacements for a player."""
        out_el = elements.get(out_element)
        if not out_el:
            return []
        position_type = int(out_el.get("element_type", 0))
        budget = float(out_el.get("now_cost", 0)) / 10.0 + REPLACEMENT_BUDGET_SLACK

        # Prefer forecast xPts for the upcoming GW; fall back to FPL's ep_next.
        xpts_by_el: dict[int, float] = {}
        if (isinstance(forecasts, pd.DataFrame) and not forecasts.empty
                and {"element", "gw", "xpts"} <= set(forecasts.columns)):
            nxt = forecasts[forecasts["gw"] == next_gw]
            if not nxt.empty:
                xpts_by_el = {
                    int(k): float(v)
                    for k, v in nxt.groupby("element")["xpts"].mean().items()
                }

        cands: list[dict] = []
        for el_id, el in elements.items():
            if el_id in my_ids or el_id == out_element:
                continue
            if int(el.get("element_type", 0)) != position_type:
                continue
            if (el.get("status") or "a") != "a":
                continue
            cost = float(el.get("now_cost", 0)) / 10.0
            if cost > budget:
                continue
            try:
                ep_next = float(el.get("ep_next") or 0.0)
            except (TypeError, ValueError):
                ep_next = 0.0
            xpts = xpts_by_el.get(el_id, ep_next)
            if xpts <= 0:
                continue
            team_fdr = fdr.get(int(el.get("team", 0)), 3.0)
            cands.append({
                "element": el_id,
                "name": el.get("web_name") or f"element {el_id}",
                "position": pos_short.get(position_type, "?"),
                "cost": cost,
                "xpts": xpts,
                "fdr": team_fdr,
                # Rank on xPts, lightly penalised by fixture difficulty.
                "_score": xpts - 0.15 * team_fdr,
            })

        cands.sort(key=lambda c: -c["_score"])
        for c in cands:
            c.pop("_score", None)
        return cands[:REPLACEMENTS_PER_PLAYER]
