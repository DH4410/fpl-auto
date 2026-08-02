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
4. **``/team/set-piece-notes/``** -- penalty / corner / free-kick takers. (Note the
   ``/team/`` segment: ``/api/set-piece-notes/`` returns 404.)
5. **football-data.co.uk** -- free historical CSVs carrying closing bookmaker
   odds (Pinnacle, Bet365, market average) plus shots, corners and cards.
   Plain HTTP GET, no account, no key.

**No API keys anywhere.** Every source here is a public, unauthenticated HTTP
GET. Nothing in this package reads a token, and no paid data provider is used.
The only authenticated calls in the wider repo are the user's own FPL session in
``fpl_auth.py``, which this package never touches.

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

#: All historical seasons available on Vaastav, from oldest to newest.
#: 2019-20 was COVID-affected (season finished in neutral venues, no fans from GW29+),
#: but the data is clean and usable for model training.
#: DC columns only exist from 2025-26 onward.
ALL_TRAINING_SEASONS = (
    "2019-20",
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
)

#: Seasons to use by default (last 5 complete seasons give ~180k rows).
#: Excludes 2019-20 by default because COVID-era data has unusual patterns
#: (no-crowd games, compressed fixtures) that differ from normal football.
DEFAULT_TRAINING_SEASONS = (
    "2020-21",
    "2021-22",
    "2022-23",
    "2023-24",
    "2024-25",
    "2025-26",
)

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
        except requests.HTTPError as exc:
            # 4xx responses are permanent -- a missing CSV will not appear on
            # the third attempt, so retrying only wastes time and log noise.
            status = getattr(exc.response, "status_code", None)
            if status is not None and 400 <= status < 500:
                raise DataFetchError(f"could not fetch {url}: {exc}") from exc
            last_exc = exc
            wait = RETRY_BACKOFF ** attempt
            log.warning("fetch failed (%s/%s) %s: %s; retrying in %.0fs",
                        attempt + 1, MAX_RETRIES, url, exc, wait)
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
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


def load_multi_season_history(seasons: tuple[str, ...] = DEFAULT_TRAINING_SEASONS,
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

#: Resources available per season directory, verified against the repo tree.
#: The layout is ``data/{season}/{resource}.csv`` with the season written in
#: full ("2025-2026", not "2025-26").
CORE_INSIGHTS_RESOURCES = (
    "players", "teams", "playerstats", "gameweek_summaries", "team_history",
)

#: Seasons with the flat per-resource CSV layout. The 2024-2025 directory uses a
#: different, directory-per-resource structure and is not supported here.
CORE_INSIGHTS_SEASONS = ("2025-2026", "2026-2027")


def _core_insights_season(season: str) -> str:
    """Normalise "2026-27" to the repo's full-year form "2026-2027"."""
    if len(season) == 9 and season[4] == "-":
        return season
    start, end = season.split("-")
    century = start[:2]
    return f"{start}-{century}{end}" if len(end) == 2 else f"{start}-{end}"


def fetch_fpl_core_insights(resource: str = "players", season: str = "2026-2027",
                            force: bool = False) -> pd.DataFrame:
    """Secondary CSV warehouse -- cross-season snapshots and enrichment.

    Layout verified against the repository tree: ``data/{season}/{resource}.csv``
    where ``season`` is the full-year form ("2026-2027"). Available resources
    are listed in :data:`CORE_INSIGHTS_RESOURCES`.

    Notably this repo **does** carry a ``2026-2027`` directory, so it is the one
    source here with current-season snapshots -- useful given that Vaastav
    stopped weekly updates after 2024/25 and the FPL API has no gameweek data
    until the season starts.

    Nothing in the pipeline depends on this source; it is enrichment only, so a
    failure raises a clear :class:`DataFetchError` rather than breaking a run.
    """
    season_dir = _core_insights_season(season)
    name = f"core_insights_{season_dir}_{resource}.json"
    if not force:
        cached = _cache_read(name, ttl_hours=CACHE_TTL_HOURS * 4)
        if cached is not None:
            return pd.read_json(StringIO(cached), orient="split")

    if resource not in CORE_INSIGHTS_RESOURCES:
        log.warning("resource %r is not one of the verified resources %s; "
                    "attempting it anyway", resource, CORE_INSIGHTS_RESOURCES)

    url = f"{CORE_INSIGHTS_BASE}/data/{season_dir}/{resource}.csv"
    try:
        text = _get(url, expect="text")
    except DataFetchError as exc:
        raise DataFetchError(
            f"FPL-Core-Insights {resource!r} for season {season_dir} not found "
            f"at {url}. Known seasons: {CORE_INSIGHTS_SEASONS}; known "
            f"resources: {CORE_INSIGHTS_RESOURCES}.") from exc

    df = pd.read_csv(StringIO(text))
    df.attrs["season"] = season_dir
    _cache_write(name, df.to_json(orient="split", date_format="iso"))
    return df


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


# ---------------------------------------------------------------------------
# football-data.co.uk -- free historical odds and match stats
# ---------------------------------------------------------------------------

FOOTBALL_DATA_BASE = "https://www.football-data.co.uk/mmz4281"

#: football-data.co.uk encodes seasons as a 4-digit code: 2024-25 -> "2425".
def _fd_season_code(season: str) -> str:
    """Convert a Vaastav-style season string ("2024-25") to "2425"."""
    try:
        start, end = season.split("-")
        return f"{start[-2:]}{end[-2:]}"
    except ValueError as exc:
        raise ValueError(f"season must look like '2024-25', got {season!r}") from exc


#: Preference order for the 1X2 columns. Pinnacle closing odds are the sharpest
#: public price and the best single input; the market average and Bet365 are
#: fallbacks for seasons where a bookmaker's columns are absent. Column sets
#: genuinely differ between seasons, so never assume one exists.
ODDS_1X2_PREFERENCE = (
    ("PSCH", "PSCD", "PSCA"),   # Pinnacle closing
    ("PSH", "PSD", "PSA"),      # Pinnacle opening
    ("AvgCH", "AvgCD", "AvgCA"),  # market average closing
    ("AvgH", "AvgD", "AvgA"),   # market average opening
    ("B365CH", "B365CD", "B365CA"),
    ("B365H", "B365D", "B365A"),
)

#: Same idea for the over/under 2.5 goals market, which pins total goals.
ODDS_OU25_PREFERENCE = (
    ("PC>2.5", "PC<2.5"),
    ("P>2.5", "P<2.5"),
    ("AvgC>2.5", "AvgC<2.5"),
    ("Avg>2.5", "Avg<2.5"),
    ("B365C>2.5", "B365C<2.5"),
    ("B365>2.5", "B365<2.5"),
)


def _first_available(df: pd.DataFrame, preference: tuple) -> tuple | None:
    """First column group from ``preference`` that is fully present in ``df``."""
    for group in preference:
        if all(c in df.columns for c in group):
            return group
    return None


def fetch_football_data_odds(season: str = "2024-25", league: str = "E0",
                             force: bool = False) -> pd.DataFrame:
    """Free historical match odds and stats from football-data.co.uk.

    ``league="E0"`` is the Premier League. The CSV carries full-time scores,
    shots, corners, cards and closing odds from several bookmakers -- everything
    needed to de-vig a market without paying for an odds API.

    Bookmaker column sets change between seasons (2024/25 has William Hill and
    1XBet columns that 2025/26 replaces with BetVictor and Coral), so the frame
    is normalised: whichever of the preferred 1X2 and over/under-2.5 groups is
    available is copied into stable ``odds_home`` / ``odds_draw`` /
    ``odds_away`` / ``odds_over25`` / ``odds_under25`` columns, and the source
    used is recorded in ``df.attrs["odds_source"]``.

    Team names here are football-data's own strings ("Man United", "Nott'm
    Forest") and do **not** match FPL team ids -- use
    :func:`match_team_names` to join.
    """
    code = _fd_season_code(season)
    name = f"football_data_{league}_{code}.json"
    if not force:
        cached = _cache_read(name, ttl_hours=CACHE_TTL_HOURS * 28)
        if cached is not None:
            df = pd.read_json(StringIO(cached), orient="split")
            # attrs do not survive JSON round-tripping, so rebuild them from the
            # cached columns rather than returning a frame whose metadata is
            # present on a fresh fetch and missing on a cached one.
            df.attrs["season"] = season
            df.attrs["odds_source"] = _first_available(df, ODDS_1X2_PREFERENCE)
            df.attrs["ou25_source"] = _first_available(df, ODDS_OU25_PREFERENCE)
            return df

    text = _get(f"{FOOTBALL_DATA_BASE}/{code}/{league}.csv", expect="text")
    df = pd.read_csv(StringIO(text), encoding_errors="ignore")
    df = df.dropna(how="all").dropna(subset=["HomeTeam", "AwayTeam"])

    # Build the normalised columns in one go -- assigning them one at a time to
    # a 130-column frame triggers pandas' fragmentation warning.
    normalised = {"season": season}
    if "Date" in df.columns:
        normalised["date"] = pd.to_datetime(df["Date"], dayfirst=True,
                                            errors="coerce")
    group_1x2 = _first_available(df, ODDS_1X2_PREFERENCE)
    if group_1x2:
        for out_col, src in zip(("odds_home", "odds_draw", "odds_away"), group_1x2):
            normalised[out_col] = pd.to_numeric(df[src], errors="coerce")
    group_ou = _first_available(df, ODDS_OU25_PREFERENCE)
    if group_ou:
        normalised["odds_over25"] = pd.to_numeric(df[group_ou[0]], errors="coerce")
        normalised["odds_under25"] = pd.to_numeric(df[group_ou[1]], errors="coerce")

    # Canonical result columns, matching the Dixon-Coles fitter's expectations.
    if {"FTHG", "FTAG"} <= set(df.columns):
        normalised["home_goals"] = pd.to_numeric(df["FTHG"], errors="coerce")
        normalised["away_goals"] = pd.to_numeric(df["FTAG"], errors="coerce")

    df = pd.concat([df, pd.DataFrame(normalised, index=df.index)], axis=1)

    _cache_write(name, df.to_json(orient="split", date_format="iso"))
    df.attrs["season"] = season
    df.attrs["odds_source"] = group_1x2
    df.attrs["ou25_source"] = group_ou
    return df


#: Fallback aliases for football-data.co.uk names that FPL spells differently.
#: Most names already match FPL's ``name`` field exactly ("Man Utd" is FPL's own
#: spelling, as is "Nott'm Forest"), so these are tried *after* an exact match,
#: never instead of one -- overriding first would break the names that agree.
FOOTBALL_DATA_NAME_ALIASES = {
    "Man United": "Man Utd",
    "Tottenham": "Spurs",
    "Nott'm Forest": "Nottingham Forest",
    "Newcastle": "Newcastle United",
    "West Ham": "West Ham United",
    "Wolves": "Wolverhampton Wanderers",
    "Sheffield United": "Sheffield Utd",
    "Leeds": "Leeds United",
    "Brighton": "Brighton and Hove Albion",
    "Leicester": "Leicester City",
    "Ipswich": "Ipswich Town",
    "Luton": "Luton Town",
    "Norwich": "Norwich City",
    "Bournemouth": "AFC Bournemouth",
    "Hull": "Hull City",
    "Coventry": "Coventry City",
}


def match_team_names(names: Iterable[str], teams_df: pd.DataFrame) -> dict:
    """Map football-data.co.uk team names onto FPL team ids.

    Resolution order: exact match on FPL ``name`` or ``short_name``, then the
    alias table, then case-insensitive containment either way.

    Unmatched names map to ``None``, which is expected and not an error: a
    historical season contains clubs that have since been relegated, so fitting
    2024/25 odds against the 2026/27 team list will legitimately leave several
    names unmapped. Callers should drop those rows rather than assume the
    mapping is total.
    """
    lookup: dict[str, int] = {}
    for _, row in teams_df.iterrows():
        lookup[str(row["name"]).lower()] = int(row["id"])
        if "short_name" in teams_df.columns:
            lookup.setdefault(str(row["short_name"]).lower(), int(row["id"]))

    out: dict[str, int | None] = {}
    unmatched = []
    for raw in names:
        low = str(raw).lower()
        hit = lookup.get(low)
        if hit is None:
            alias = FOOTBALL_DATA_NAME_ALIASES.get(raw)
            if alias:
                hit = lookup.get(str(alias).lower())
        if hit is None:
            hit = next((tid for nm, tid in lookup.items()
                        if low in nm or nm in low), None)
        out[raw] = hit
        if hit is None:
            unmatched.append(raw)

    if unmatched:
        log.info("football-data teams not in the current FPL team list "
                 "(likely relegated): %s", unmatched)
    return out


# ---------------------------------------------------------------------------
# Optional free enrichment
# ---------------------------------------------------------------------------

def fetch_soccerdata_fbref(season: str = "2024-25", stat_type: str = "standard"
                           ) -> pd.DataFrame:
    """FBref player stats via the free ``soccerdata`` package, if installed.

    ``soccerdata`` scrapes public pages -- no key, no account. It is an optional
    dependency because it is heavy and rate-limits itself; the pipeline works
    without it. Raises :class:`DataFetchError` with install instructions when
    the package is absent.

    Entity matching is on you: FBref player names do not carry FPL ids, so a
    fuzzy name+club join is required before these columns can be used as
    features.
    """
    try:
        import soccerdata as sd  # noqa: PLC0415 -- optional dependency
    except ImportError as exc:
        raise DataFetchError(
            "soccerdata is not installed. It is optional free enrichment: "
            "`pip install soccerdata`. The pipeline runs fine without it."
        ) from exc

    fbref = sd.FBref(leagues="ENG-Premier League", seasons=season)
    return fbref.read_player_season_stats(stat_type=stat_type).reset_index()


# ---------------------------------------------------------------------------
# Vaastav understat xG (shot-level, per player per season)
# ---------------------------------------------------------------------------

def fetch_vaastav_understat(season: str = "2024-25", force: bool = False
                            ) -> pd.DataFrame:
    """Season-level understat xG/xA/npxG data from Vaastav's repo.

    Vaastav mirrors understat data in ``data/{season}/understat/understat_plus.csv``
    (or ``understat.csv`` for older seasons). Returns a DataFrame with columns
    including ``player``, ``xG``, ``xA``, ``npxG``, ``xGChain``, ``xGBuildup``,
    ``time`` (minutes played) and ``team``.

    No account or key required. Returns an empty DataFrame if the file is absent
    for the requested season.
    """
    name = f"vaastav_{season}_understat.json"
    if not force:
        cached = _cache_read(name, ttl_hours=CACHE_TTL_HOURS * 28)
        if cached is not None:
            return pd.read_json(StringIO(cached), orient="split")

    # Vaastav uses different filenames across seasons
    for fname in ("understat_plus.csv", "understat.csv"):
        url = f"{VAASTAV_BASE}/{season}/understat/{fname}"
        try:
            text = _get(url, expect="text")
            df = pd.read_csv(StringIO(text))
            df["season"] = season
            _cache_write(name, df.to_json(orient="split", date_format="iso"))
            return df
        except DataFetchError:
            continue

    log.info("vaastav understat not available for season %s", season)
    return pd.DataFrame()


def fetch_vaastav_cleaned(season: str = "2024-25", force: bool = False
                          ) -> pd.DataFrame:
    """Season-total cleaned player rows from Vaastav (cleaned_players.csv).

    Useful for whole-season statistics and positional priors, especially for
    seasons without per-gameweek data in the main merged_gw.csv.
    """
    name = f"vaastav_{season}_cleaned.json"
    if not force:
        cached = _cache_read(name, ttl_hours=CACHE_TTL_HOURS * 28)
        if cached is not None:
            return pd.read_json(StringIO(cached), orient="split")
    try:
        text = _get(f"{VAASTAV_BASE}/{season}/cleaned_players.csv", expect="text")
    except DataFetchError as exc:
        log.warning("vaastav cleaned_players not available for %s: %s", season, exc)
        return pd.DataFrame()
    df = pd.read_csv(StringIO(text))
    df["season"] = season
    _cache_write(name, df.to_json(orient="split", date_format="iso"))
    return df


def load_understat_history(seasons: tuple[str, ...] = DEFAULT_TRAINING_SEASONS,
                           force: bool = False) -> pd.DataFrame:
    """Concatenate Vaastav understat xG data for multiple seasons.

    Returns empty DataFrame if no seasons have understat data. Column ``season``
    allows groupby per season. ``player`` is the player name as used by understat
    (not FPL ids -- fuzzy matching needed to join).
    """
    frames = []
    for s in seasons:
        df = fetch_vaastav_understat(s, force=force)
        if not df.empty:
            frames.append(df)
    if not frames:
        log.warning("no understat data found for seasons %s", seasons)
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


# ---------------------------------------------------------------------------
# ESPN news (free, no key, supplementary signal)
# ---------------------------------------------------------------------------

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1"


def fetch_espn_news(limit: int = 100, force: bool = False) -> list[dict]:
    """Latest Premier League news from the unofficial ESPN public API.

    Returns a list of article dicts with keys: headline, description, published.
    Uses the same ``/api/bootstrap-static/`` style: public, unauthenticated.

    The endpoint ``/teams/{id}/injuries`` is documented but returns empty for
    soccer -- use the FPL ``news`` field on each element for injury status.
    """
    name = "espn_news.json"
    if not force:
        cached = _cache_read(name, ttl_hours=1)
        if cached is not None:
            return cached
    try:
        resp = get_session().get(
            f"{ESPN_BASE}/news",
            params={"limit": limit},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        articles = []
        for a in resp.json().get("articles", []):
            articles.append({
                "headline": a.get("headline", ""),
                "description": a.get("description", ""),
                "published": a.get("published", ""),
            })
        _cache_write(name, articles)
        return articles
    except Exception as exc:  # noqa: BLE001
        log.warning("ESPN news fetch failed: %s", exc)
        return []


def fetch_espn_scoreboard(date_str: str | None = None, force: bool = False
                          ) -> list[dict]:
    """ESPN Premier League scoreboard for a date (YYYYMMDD) or today.

    Returns a list of match dicts with home team, away team, score and event id.
    Useful for mapping ESPN event ids to match results for the summary API.
    """
    params = {}
    if date_str:
        params["dates"] = date_str
    name = f"espn_scoreboard_{date_str or 'today'}.json"
    if not force:
        cached = _cache_read(name, ttl_hours=0.5)
        if cached is not None:
            return cached
    try:
        resp = get_session().get(
            f"{ESPN_BASE}/scoreboard",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        events = []
        for ev in resp.json().get("events", []):
            comps = ev.get("competitions", [{}])
            comp = comps[0] if comps else {}
            competitors = comp.get("competitors", [])
            home = next((c for c in competitors if c.get("homeAway") == "home"), {})
            away = next((c for c in competitors if c.get("homeAway") == "away"), {})
            events.append({
                "event_id": ev.get("id"),
                "date": ev.get("date", ""),
                "home_team": home.get("team", {}).get("displayName", ""),
                "away_team": away.get("team", {}).get("displayName", ""),
                "home_score": home.get("score"),
                "away_score": away.get("score"),
                "status": ev.get("status", {}).get("type", {}).get("name", ""),
            })
        _cache_write(name, events)
        return events
    except Exception as exc:  # noqa: BLE001
        log.warning("ESPN scoreboard fetch failed: %s", exc)
        return []


# TODO(transfermarkt): preseason arrivals and departures are not in any FPL
# feed, so a promoted or heavily rebuilt squad looks identical to a stable one.
# transfermarkt.com/premier-league is scrapeable with ordinary headers and no
# key; parsing the squad and arrivals tables would give a "squad churn" feature
# to widen uncertainty on rebuilt teams. Until then,
# feature_engineering.promoted_team_priors handles the promoted-club case with
# worst-quartile priors.


# ---------------------------------------------------------------------------
# FPL element-summary — per-player history keyed to CURRENT element IDs
# ---------------------------------------------------------------------------
# FPL resets element IDs each season (confirmed: Raya=1, Saka=12 in 2026/27,
# completely different from their 2025-26 IDs). Matching current IDs against
# Vaastav historical data therefore produces wrong features for every player.
# The element-summary endpoint returns history_past keyed to the CURRENT
# element ID, so it is the correct source for inference-time features.

#: Map from element-summary history_past field names to the EWMA column names
#: the ML models expect. Right-hand names must match feature_engineering output.
_HISTORY_PAST_TO_EWMA: dict[str, str] = {
    "expected_goals":                  "ewma_expected_goals",
    "expected_assists":                "ewma_expected_assists",
    "expected_goal_involvements":      "ewma_expected_goal_involvements",
    "expected_goals_conceded":         "ewma_expected_goals_conceded",
    "goals_scored":                    "ewma_goals_scored",
    "assists":                         "ewma_assists",
    "clean_sheets":                    "ewma_clean_sheets",
    "saves":                           "ewma_saves",
    "goals_conceded":                  "ewma_goals_conceded",
    "bonus":                           "ewma_bonus",
    "bps":                             "ewma_bps",
    "minutes":                         "ewma_minutes",
    "total_points":                    "ewma_total_points",
    "influence":                       "ewma_influence",
    "creativity":                      "ewma_creativity",
    "threat":                          "ewma_threat",
    "ict_index":                       "ewma_ict_index",
    "clearances_blocks_interceptions": "ewma_clearances_blocks_interceptions",
    "recoveries":                      "ewma_recoveries",
    "tackles":                         "ewma_tackles",
    "defensive_contribution":          "ewma_defensive_contribution",
}


def fetch_element_summary(player_id: int, force: bool = False) -> dict:
    """Fetch /api/element-summary/{id}/ with 24-hour caching.

    Returns a dict with keys ``history`` (current-season GW rows, empty before
    the season starts), ``history_past`` (one row per past season with full
    cumulative stats) and ``fixtures`` (upcoming matches).

    ``history_past`` is indexed by the current element ID, making it the
    correct source for inference features after a season-wide ID reset.
    """
    name = f"element_summary_{int(player_id)}.json"
    if not force:
        cached = _cache_read(name, ttl_hours=24)
        if cached is not None:
            return json.loads(cached) if isinstance(cached, str) else cached
    try:
        data = _get(f"{FPL_BASE}/element-summary/{int(player_id)}/", expect="json")
    except DataFetchError as exc:
        log.warning("element-summary %d failed: %s", player_id, exc)
        return {"history": [], "history_past": [], "fixtures": []}
    _cache_write(name, json.dumps(data))
    return data


def fetch_element_summaries_bulk(
    player_ids,
    force: bool = False,
    batch_delay: float = 0.05,
) -> dict:
    """Fetch element-summary for every player ID in *player_ids*.

    Reads from the 24-hour cache first; only uncached players hit the network.
    A small *batch_delay* (seconds per 10 requests) avoids spiking the FPL
    API on a cold run. Returns ``{player_id: summary_dict}``.
    """
    results: dict = {}
    fresh = 0
    for i, pid in enumerate(player_ids):
        pid = int(pid)
        name = f"element_summary_{pid}.json"
        cached = _cache_read(name, ttl_hours=24) if not force else None
        if cached is not None:
            results[pid] = json.loads(cached) if isinstance(cached, str) else cached
        else:
            results[pid] = fetch_element_summary(pid, force=force)
            fresh += 1
            if batch_delay and fresh % 10 == 0:
                time.sleep(batch_delay)
        if (i + 1) % 100 == 0:
            log.debug("element summaries: %d/%d done", i + 1, len(player_ids))
    log.info("element summaries: %d total, %d freshly fetched", len(results), fresh)
    return results


def element_summaries_to_features(
    summaries: dict,
    recent_seasons: int = 2,
    recency_weights: tuple = (0.7, 0.3),
) -> "pd.DataFrame":
    """Convert element-summary history_past into per-game inference features.

    For each player, takes the most recent *recent_seasons* from history_past,
    computes per-game averages (stat / starts.clip(1)), and blends them with
    *recency_weights* (index 0 = most recent season).

    Returns a DataFrame indexed by current element ID with EWMA-named columns
    (``ewma_expected_goals``, ``ewma_minutes``, etc.) that match what the ML
    models were trained on, plus ``fpl_pts_per_game`` and ``fpl_starts``.
    """
    rows: list[dict] = []
    for pid, summary in summaries.items():
        past = summary.get("history_past", [])
        if not past:
            continue
        past_sorted = sorted(past, key=lambda r: r.get("season_name", ""), reverse=True)
        recent = past_sorted[:recent_seasons]
        if not recent:
            continue

        w = list(recency_weights[:len(recent)])
        w_sum = sum(w)
        if w_sum == 0:
            continue
        w = [x / w_sum for x in w]

        row: dict = {"element": int(pid)}
        for stat, ewma_col in _HISTORY_PAST_TO_EWMA.items():
            weighted = 0.0
            for season_row, weight in zip(recent, w):
                raw = float(season_row.get(stat) or 0)
                minutes_s = float(season_row.get("minutes") or 0)
                starts_s = float(season_row.get("starts") or 0)

                if stat == "minutes":
                    # Per-GW average including non-playing weeks (38 GW season)
                    denom = 38.0
                elif stat == "clean_sheets":
                    # CS is per-start; sub appearances rarely qualify
                    denom = max(starts_s, 1.0)
                else:
                    # Per-90-minute rate: scales correctly for 90-min starters and
                    # discounts fringe/bench players by their actual time on pitch.
                    # Using starts as denominator breaks for 0-start players:
                    # max(0,1)=1 inflates their entire season total into one game.
                    denom = max(minutes_s / 90.0, 1.0)

                weighted += weight * (raw / denom)
            row[ewma_col] = weighted

        row["fpl_pts_per_game"] = row.get("ewma_total_points", 0.0)
        row["fpl_starts"] = float(recent[0].get("starts") or 0)
        row["fpl_season_count"] = len(past)
        rows.append(row)

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("element")
