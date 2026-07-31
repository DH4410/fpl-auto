"""
Data ingest for the FPL bot, with on-disk caching.

Sources (see ``DATA_SOURCES.md`` at the repo root for the full audit):

1. **Official FPL REST API** -- primary. ``bootstrap-static`` gives the whole
   player pool, prices, teams, gameweek metadata and the FPL scoring config.
   ``fixtures``, ``element-summary/{id}`` and ``event/{gw}/live`` fill in
   fixture difficulty, per-player history and live scoring.
2. **Vaastav's Fantasy-Premier-League GitHub repo** -- historical per-gameweek
   CSVs used to train the models. Weekly updates stopped after 2024/25, so this
   is a *training* source only, never a live one.
3. **FPL-Core-Insights** -- secondary CSV warehouse for cross-season snapshots.
4. **``/team/set-piece-notes/``** -- penalty / corner / free-kick takers.

Caching
-------
Every fetch writes ``{"fetched_at": <iso8601>, "data": ...}`` into ``bot/cache/``
and re-reads it for :data:`CACHE_TTL_HOURS` hours. Pass ``force=True`` to bypass.
The cache directory is git-ignored -- ``bootstrap.json`` alone is ~1.3 MB.

Important data-availability caveats
-----------------------------------
* **2026/27 has no gameweek data yet.** ``event/1/live`` returns an empty
  element list until the season starts. Season-total fields in
  ``bootstrap-static`` during preseason are carried reference values, not
  2026/27 scoring.
* **2024/25 Vaastav CSVs have no defensive-contribution columns.** Verified
  against ``data/2024-25/gws/merged_gw.csv``: it lacks
  ``clearances_blocks_interceptions``, ``recoveries``, ``tackles`` and
  ``defensive_contribution``. Those four columns only appear from 2025/26, when
  the DC rule was introduced. :func:`fetch_vaastav_history` therefore reports
  which columns are missing rather than silently zero-filling them --
  zero-filling would tell the model "this defender made no clearances", which is
  false rather than merely unknown.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FPL_BASE = "https://fantasy.premierleague.com/api"

VAASTAV_BASE = (
    "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
)

#: FPL-Core-Insights CSV warehouse. Resource names map onto files in the repo.
CORE_INSIGHTS_BASE = (
    "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main"
)

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_TTL_HOURS = 6

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

#: Columns that exist in 2025/26+ Vaastav gameweek data but not in 2024/25.
DEFCON_COLUMNS = (
    "clearances_blocks_interceptions",
    "recoveries",
    "tackles",
    "defensive_contribution",
)

#: Earliest season whose gameweek CSVs carry the defensive-contribution columns.
FIRST_DEFCON_SEASON = "2025-26"


class DataFetchError(RuntimeError):
    """Raised when a source cannot be reached after retries."""


# ---------------------------------------------------------------------------
# Cache plumbing
# ---------------------------------------------------------------------------

def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = name.replace("/", "_").replace("?", "_").replace("=", "_")
    return CACHE_DIR / safe


def _cache_read(name: str, ttl_hours: float = CACHE_TTL_HOURS) -> Any | None:
    """Return cached payload if present and younger than ``ttl_hours``."""
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            envelope = json.load(fh)
        fetched_at = datetime.fromisoformat(envelope["fetched_at"])
    except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
        log.warning("cache %s unreadable (%s); refetching", name, exc)
        return None

    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - fetched_at
    if age > timedelta(hours=ttl_hours):
        log.debug("cache %s stale (%s old)", name, age)
        return None
    return envelope["data"]


def _cache_write(name: str, data: Any) -> None:
    path = _cache_path(name)
    envelope = {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data}
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(envelope, fh)
        tmp.replace(path)
    except OSError as exc:
        log.warning("could not write cache %s: %s", name, exc)


def cache_info() -> pd.DataFrame:
    """List what is cached and how old each entry is -- handy in the notebook."""
    rows = []
    if CACHE_DIR.exists():
        for path in sorted(CACHE_DIR.glob("*")):
            if path.suffix == ".tmp":
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    fetched_at = json.load(fh).get("fetched_at")
            except (json.JSONDecodeError, OSError):
                fetched_at = None
            rows.append({
                "file": path.name,
                "size_kb": round(path.stat().st_size / 1024, 1),
                "fetched_at": fetched_at,
            })
    return pd.DataFrame(rows)


def clear_cache() -> int:
    """Delete every cache file. Returns the number removed."""
    if not CACHE_DIR.exists():
        return 0
    n = 0
    for path in CACHE_DIR.glob("*"):
        path.unlink()
        n += 1
    return n


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

_session: requests.Session | None = None


def get_session() -> requests.Session:
    """Shared requests session with a browser-ish User-Agent.

    The FPL API rejects some default client UAs, and reusing one session keeps
    the connection pool warm across the few hundred ``element-summary`` calls a
    full pool refresh makes.
    """
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return _session


def _get(url: str, expect: str = "json") -> Any:
    """GET with retries and exponential backoff. Raises :class:`DataFetchError`."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = get_session().get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json() if expect == "json" else resp.text
        except (requests.RequestException, json.JSONDecodeError) as exc:
            last_exc = exc
            wait = RETRY_BACKOFF ** attempt
            log.warning("fetch failed (%s/%s) %s: %s; retrying in %.0fs",
                        attempt + 1, MAX_RETRIES, url, exc, wait)
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
    raise DataFetchError(f"could not fetch {url}: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# Official FPL API
# ---------------------------------------------------------------------------

def fetch_bootstrap(force: bool = False) -> dict:
    """The whole FPL universe: elements, teams, events, element_types, chips.

    Primary ingest per the data-source audit. Cached as ``cache/bootstrap.json``.
    """
    if not force:
        cached = _cache_read("bootstrap.json")
        if cached is not None:
            return cached
    data = _get(f"{FPL_BASE}/bootstrap-static/")
    _cache_write("bootstrap.json", data)
    return data


def fetch_fixtures(event: int | None = None, force: bool = False) -> list:
    """All 380 fixtures, or just one gameweek's when ``event`` is given.

    Carries kickoff times, team ids, FPL's own difficulty ratings
    (``team_h_difficulty`` / ``team_a_difficulty``) and, once played, scores.
    """
    name = "fixtures.json" if event is None else f"fixtures_event_{event}.json"
    if not force:
        cached = _cache_read(name)
        if cached is not None:
            return cached
    url = f"{FPL_BASE}/fixtures/"
    if event is not None:
        url += f"?event={event}"
    data = _get(url)
    _cache_write(name, data)
    return data


def fetch_element_summary(player_id: int, force: bool = False) -> dict:
    """One player's upcoming fixtures, current-season history and past seasons.

    Cached per player -- a full 564-player sweep is ~10 minutes of polite
    requests, so let the cache do its job.
    """
    name = f"element_summary_{player_id}.json"
    if not force:
        cached = _cache_read(name, ttl_hours=CACHE_TTL_HOURS * 4)
        if cached is not None:
            return cached
    data = _get(f"{FPL_BASE}/element-summary/{player_id}/")
    _cache_write(name, data)
    return data


def fetch_event_live(event: int, force: bool = False) -> dict:
    """Live per-player stats and points for a gameweek.

    Returns ``{"elements": []}`` before a gameweek's first match kicks off --
    that is the API working correctly, not an error. Callers should check for an
    empty ``elements`` list rather than assuming data exists.
    """
    name = f"event_live_{event}.json"
    if not force:
        # Live data changes every few minutes during matches.
        cached = _cache_read(name, ttl_hours=0.25)
        if cached is not None:
            return cached
    data = _get(f"{FPL_BASE}/event/{event}/live/")
    _cache_write(name, data)
    return data


def fetch_set_piece_notes(force: bool = False) -> dict:
    """Per-team penalty, corner and free-kick taker notes.

    Free-text, but the strongest public signal for who takes penalties, which
    dominates forward and midfield expected goals.
    """
    if not force:
        cached = _cache_read("set_piece_notes.json")
        if cached is not None:
            return cached
    data = _get(f"{FPL_BASE}/team/set-piece-notes/")
    _cache_write("set_piece_notes.json", data)
    return data


# ---------------------------------------------------------------------------
# Convenience frames over bootstrap
# ---------------------------------------------------------------------------

def bootstrap_frames(force: bool = False) -> dict[str, pd.DataFrame]:
    """Bootstrap split into tidy DataFrames: players, teams, events, positions."""
    bs = fetch_bootstrap(force=force)
    return {
        "players": pd.DataFrame(bs["elements"]),
        "teams": pd.DataFrame(bs["teams"]),
        "events": pd.DataFrame(bs["events"]),
        "element_types": pd.DataFrame(bs["element_types"]),
    }


def fixtures_frame(force: bool = False) -> pd.DataFrame:
    """All fixtures as a DataFrame with a parsed ``kickoff_time``."""
    df = pd.DataFrame(fetch_fixtures(force=force))
    if "kickoff_time" in df.columns:
        df["kickoff_time"] = pd.to_datetime(df["kickoff_time"], errors="coerce",
                                            utc=True)
    return df


# ---------------------------------------------------------------------------
# Vaastav historical CSVs (training data)
# ---------------------------------------------------------------------------

def fetch_vaastav_history(season: str = "2024-25", force: bool = False
                          ) -> pd.DataFrame:
    """Per-gameweek player rows for one season, from Vaastav's repo.

    Downloads ``data/{season}/gws/merged_gw.csv``, which is one row per player
    per gameweek: minutes, goals, assists, xG, xA, xGC, bps, bonus, value,
    opponent, was_home and total_points.

    A ``season`` column is added, and a ``_missing_defcon`` attribute is attached
    to ``df.attrs`` listing which defensive-contribution columns the season
    lacks. **2024/25 lacks all four** -- see the module docstring. Callers that
    need DC data must either train on 2025/26 or use a rule-based estimator; do
    not zero-fill.
    """
    name = f"vaastav_{season}_merged_gw.json"
    if not force:
        cached = _cache_read(name, ttl_hours=CACHE_TTL_HOURS * 28)  # ~1 week
        if cached is not None:
            df = pd.read_json(StringIO(cached), orient="split")
            df.attrs["season"] = season
            df.attrs["missing_defcon"] = [c for c in DEFCON_COLUMNS
                                          if c not in df.columns]
            return df

    url = f"{VAASTAV_BASE}/{season}/gws/merged_gw.csv"
    text = _get(url, expect="text")
    df = pd.read_csv(StringIO(text))

    df["season"] = season
    if "kickoff_time" in df.columns:
        df["kickoff_time"] = pd.to_datetime(df["kickoff_time"], errors="coerce",
                                            utc=True)
    if "GW" in df.columns and "round" not in df.columns:
        df["round"] = df["GW"]

    missing = [c for c in DEFCON_COLUMNS if c not in df.columns]
    if missing:
        log.info("season %s has no defensive-contribution columns: %s",
                 season, missing)

    _cache_write(name, df.to_json(orient="split", date_format="iso"))
    df.attrs["season"] = season
    df.attrs["missing_defcon"] = missing
    return df


def fetch_vaastav_players_raw(season: str = "2024-25", force: bool = False
                              ) -> pd.DataFrame:
    """Season-total player rows (the bootstrap ``elements`` snapshot at season end).

    Useful for positional priors -- notably ``defensive_contribution_per_90``,
    which exists from 2025/26 onward and anchors the DC model.
    """
    name = f"vaastav_{season}_players_raw.json"
    if not force:
        cached = _cache_read(name, ttl_hours=CACHE_TTL_HOURS * 28)
        if cached is not None:
            return pd.read_json(StringIO(cached), orient="split")

    text = _get(f"{VAASTAV_BASE}/{season}/players_raw.csv", expect="text")
    df = pd.read_csv(StringIO(text))
    df["season"] = season
    _cache_write(name, df.to_json(orient="split", date_format="iso"))
    return df


def load_multi_season_history(seasons: tuple[str, ...] = ("2024-25", "2025-26"),
                              force: bool = False) -> pd.DataFrame:
    """Concatenate several seasons of gameweek data with an explicit NaN policy.

    Seasons do not share a column set:

    * 2024/25 carries ``mng_*`` manager-scoring columns that 2025/26 dropped;
    * 2025/26 carries the four defensive-contribution columns that 2024/25
      never had.

    Naive concatenation produces a ragged frame full of silent NaNs. This helper
    takes the column union, leaves genuinely-absent values as NaN (**never**
    zero), and records the per-season availability in ``df.attrs["coverage"]``
    so downstream models can mask rather than impute.
    """
    frames, coverage = [], {}
    for season in seasons:
        try:
            df = fetch_vaastav_history(season, force=force)
        except DataFetchError as exc:
            log.warning("skipping season %s: %s", season, exc)
            continue
        coverage[season] = {
            "rows": len(df),
            "has_defcon": [c for c in DEFCON_COLUMNS if c in df.columns],
            "missing_defcon": [c for c in DEFCON_COLUMNS if c not in df.columns],
        }
        frames.append(df)

    if not frames:
        raise DataFetchError(f"no seasons could be loaded from {seasons}")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined.attrs["coverage"] = coverage
    combined.attrs["defcon_seasons"] = [
        s for s, c in coverage.items() if not c["missing_defcon"]]
    return combined


# ---------------------------------------------------------------------------
# FPL-Core-Insights
# ---------------------------------------------------------------------------

#: Known resource -> path candidates within the FPL-Core-Insights repo. The repo
#: reorganises occasionally, so each resource lists fallbacks that are tried in
#: order.
CORE_INSIGHTS_RESOURCES = {
    "players": ("2025-2026/players.csv", "data/players.csv", "players.csv"),
    "teams": ("2025-2026/teams.csv", "data/teams.csv", "teams.csv"),
    "fixtures": ("2025-2026/fixtures.csv", "data/fixtures.csv", "fixtures.csv"),
    "gameweeks": ("2025-2026/gameweeks.csv", "data/gameweeks.csv", "gameweeks.csv"),
}


def fetch_fpl_core_insights(resource: str = "players", force: bool = False
                            ) -> pd.DataFrame:
    """Secondary CSV warehouse (cross-season snapshots, Elo-style enrichment).

    Treated as best-effort: the upstream repo restructures its paths between
    seasons, so several candidate paths are tried and a clear
    :class:`DataFetchError` is raised if none resolve. Nothing in the pipeline
    depends on this source -- it is enrichment only.
    """
    name = f"core_insights_{resource}.json"
    if not force:
        cached = _cache_read(name, ttl_hours=CACHE_TTL_HOURS * 4)
        if cached is not None:
            return pd.read_json(StringIO(cached), orient="split")

    candidates = CORE_INSIGHTS_RESOURCES.get(resource, (f"{resource}.csv",))
    errors = []
    for path in candidates:
        try:
            text = _get(f"{CORE_INSIGHTS_BASE}/{path}", expect="text")
        except DataFetchError as exc:
            errors.append(f"{path}: {exc}")
            continue
        df = pd.read_csv(StringIO(text))
        _cache_write(name, df.to_json(orient="split", date_format="iso"))
        return df

    raise DataFetchError(
        f"FPL-Core-Insights resource {resource!r} not found. Tried:\n  "
        + "\n  ".join(errors))


# ---------------------------------------------------------------------------
# Derived helpers
# ---------------------------------------------------------------------------

def build_player_pool(force: bool = False) -> pd.DataFrame:
    """Current player pool joined to team names -- the optimiser's input frame.

    Columns kept are the ones the optimiser and predictor actually consume:
    identity, position, club, price, availability and the FPL-provided season
    aggregates. Prices stay in integer tenths (``now_cost``) plus a ``price``
    float for reporting.
    """
    frames = bootstrap_frames(force=force)
    players, teams = frames["players"], frames["teams"]

    team_lookup = teams.set_index("id")["name"].to_dict()
    short_lookup = teams.set_index("id")["short_name"].to_dict()

    pool = players.copy()
    pool["team_name"] = pool["team"].map(team_lookup)
    pool["team_short"] = pool["team"].map(short_lookup)
    pool["price"] = pool["now_cost"] / 10.0
    pool["available"] = pool["status"].isin(["a", "d"])

    numeric = [
        "minutes", "starts", "goals_scored", "assists", "clean_sheets",
        "goals_conceded", "saves", "bonus", "bps", "total_points",
        "expected_goals", "expected_assists", "expected_goal_involvements",
        "expected_goals_conceded", "influence", "creativity", "threat",
        "ict_index", "form", "points_per_game", "selected_by_percent",
        "clearances_blocks_interceptions", "recoveries", "tackles",
        "defensive_contribution",
    ]
    for col in numeric:
        if col in pool.columns:
            pool[col] = pd.to_numeric(pool[col], errors="coerce")

    return pool


def upcoming_fixtures(n_gameweeks: int = 5, force: bool = False) -> pd.DataFrame:
    """The next ``n_gameweeks`` of unplayed fixtures, sorted by gameweek."""
    fx = fixtures_frame(force=force)
    if fx.empty:
        return fx
    unplayed = fx[~fx["finished"].fillna(False)] if "finished" in fx.columns else fx
    if "event" not in unplayed.columns:
        return unplayed
    events = sorted(e for e in unplayed["event"].dropna().unique())
    keep = events[:n_gameweeks]
    return unplayed[unplayed["event"].isin(keep)].sort_values(["event", "kickoff_time"])


def current_gameweek(force: bool = False) -> int | None:
    """The gameweek FPL currently considers 'next'. ``None`` if the season ended."""
    events = bootstrap_frames(force=force)["events"]
    nxt = events[events["is_next"]] if "is_next" in events.columns else events.iloc[0:0]
    if not nxt.empty:
        return int(nxt.iloc[0]["id"])
    unfinished = events[~events["finished"]] if "finished" in events.columns else events
    return int(unfinished.iloc[0]["id"]) if not unfinished.empty else None


# TODO(odds): no free bookmaker odds API is available to this project. When one
# is added (e.g. the-odds-api, Betfair Exchange), fetch 1X2 + Asian handicap +
# over/under 2.5 per fixture, de-vig with Shin or proportional normalisation,
# and convert to implied team goal expectations. Those would replace the
# Dixon-Coles ratings in feature_engineering.team_strength_matrix as the
# strongest single input to the simulator.

# TODO(nlp): press-conference and injury-news sentiment would sharpen the
# minutes model more than any other missing feature. It needs a scraper
# (official club sites / a news API) plus a rotation-risk classifier. The
# `news` and `chance_of_playing_next_round` fields in bootstrap are the only
# structured substitute currently ingested.
