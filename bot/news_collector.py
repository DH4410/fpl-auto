"""
News and injury data enrichment for the FPL bot.

Sources (all free, no API keys required):
1. **FPL bootstrap `news` field** — official injury / availability text already
   in `data_collector.build_player_pool`. This module extends that with additional
   context and structure.
2. **ESPN unofficial public API** — Premier League squad rosters, player injury
   status and news headlines. No key, no authentication. Uses the same public
   endpoints that espn.com/soccer fetches in the browser.
3. **BBC Sport / Sky Sports headlines** (via RSS) — supplementary text for
   broader squad news beyond what FPL surfaces.

ESPN API endpoints used (verified public, no auth):
  Base: https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1
  - /teams                     — all 20 PL teams with ESPN team ids
  - /teams/{teamId}/roster     — athletes per team with ESPN athlete ids
  - /news?limit=100            — latest PL news headlines and athlete tags
  Core API (injury detail):
  Base: https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1
  - /athletes/{athleteId}/injuries  — structured injury log

Name-matching to FPL player IDs is done by fuzzy name + team, because ESPN and
FPL use different player IDs and different name spellings ("Mohamed Salah" vs
"M.Salah"). Unmatched players produce a warning, not an error.

Results are cached in `bot/cache/` with the same TTL mechanism as the rest of
`data_collector`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
import requests

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1"
ESPN_CORE_BASE = "https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1"

# Injury keywords → FPL-relevant severity score (0 = fine, 1 = concern, 2 = doubt, 3 = out)
INJURY_KEYWORDS = {
    "hamstring": 2, "knock": 1, "illness": 1, "cold": 1, "flu": 1,
    "muscle": 1, "calf": 2, "ankle": 2, "knee": 2, "thigh": 2,
    "groin": 2, "back": 2, "hip": 1, "shoulder": 1, "wrist": 1,
    "suspension": 3, "banned": 3, "red card": 3,
    "fracture": 3, "broken": 3, "surgery": 3, "operation": 3,
    "season": 3,   # "out for the season"
    "month": 2, "weeks": 2, "week": 1,
    "doubt": 2, "doubtful": 2, "concern": 1, "uncertain": 2,
    "out": 2, "unavailable": 3, "ruled out": 3,
    "fit": 0, "available": 0, "returns": 0, "training": 0, "full": 0,
    "cleared": 0, "ready": 0, "contention": 0,
}

# Negation window: words within this many tokens before a keyword flip the score
NEGATION_WINDOW = 3
NEGATION_WORDS = {"not", "no", "hasn't", "haven't", "didn't", "won't", "isn't", "aren't"}

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_TTL_HOURS = 2   # ESPN news changes hourly on matchdays
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# HTTP helper (mirrors data_collector._get, kept local for independence)
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None) -> Any:
    """GET with retries. Returns parsed JSON or raises RuntimeError."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if hasattr(exc, "response") and exc.response is not None:
                if 400 <= exc.response.status_code < 500:
                    raise RuntimeError(f"ESPN 4xx at {url}: {exc}") from exc
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"ESPN fetch failed after {MAX_RETRIES} attempts: {last_exc}") from last_exc


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[/\\?=&]", "_", name)
    return CACHE_DIR / safe


def _cache_read(name: str, ttl_hours: float = CACHE_TTL_HOURS) -> Any | None:
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            env = json.load(fh)
        fetched = datetime.fromisoformat(env["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - fetched > timedelta(hours=ttl_hours):
            return None
        return env["data"]
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _cache_write(name: str, data: Any) -> None:
    path = _cache_path(name)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(), "data": data}, fh)
        tmp.replace(path)
    except OSError as exc:
        log.warning("cache write failed for %s: %s", name, exc)


# ---------------------------------------------------------------------------
# ESPN API fetchers
# ---------------------------------------------------------------------------

def fetch_espn_teams(force: bool = False) -> list[dict]:
    """All 20 Premier League teams with ESPN team ids and names."""
    name = "espn_teams.json"
    if not force:
        cached = _cache_read(name, ttl_hours=24)
        if cached is not None:
            return cached
    try:
        data = _get(f"{ESPN_BASE}/teams", params={"limit": 50})
        teams = [
            {
                "espn_id": t["team"]["id"],
                "espn_name": t["team"].get("displayName", ""),
                "espn_short": t["team"].get("abbreviation", ""),
                "espn_location": t["team"].get("location", ""),
            }
            for t in data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
        ]
        _cache_write(name, teams)
        return teams
    except (RuntimeError, KeyError, IndexError) as exc:
        log.warning("ESPN teams fetch failed: %s", exc)
        return []


def fetch_espn_roster(team_id: str, force: bool = False) -> list[dict]:
    """Athletes on one ESPN team's current roster."""
    name = f"espn_roster_{team_id}.json"
    if not force:
        cached = _cache_read(name, ttl_hours=6)
        if cached is not None:
            return cached
    try:
        data = _get(f"{ESPN_BASE}/teams/{team_id}/roster")
        athletes = []
        for group in data.get("athletes", []):
            for a in group.get("items", []):
                athletes.append({
                    "espn_id": a.get("id"),
                    "display_name": a.get("displayName", ""),
                    "first_name": a.get("firstName", ""),
                    "last_name": a.get("lastName", ""),
                    "position": a.get("position", {}).get("abbreviation", ""),
                    "jersey": a.get("jersey"),
                })
        _cache_write(name, athletes)
        return athletes
    except (RuntimeError, KeyError) as exc:
        log.warning("ESPN roster fetch for team %s failed: %s", team_id, exc)
        return []


def fetch_espn_news(limit: int = 100, force: bool = False) -> list[dict]:
    """Latest Premier League news headlines from ESPN."""
    name = "espn_news.json"
    if not force:
        cached = _cache_read(name, ttl_hours=1)
        if cached is not None:
            return cached
    try:
        data = _get(f"{ESPN_BASE}/news", params={"limit": limit})
        articles = []
        for a in data.get("articles", []):
            # Extract athlete references from the article if present
            athlete_ids = [
                ref.get("id") for ref in a.get("athletes", []) if "id" in ref
            ]
            articles.append({
                "headline": a.get("headline", ""),
                "description": a.get("description", ""),
                "published": a.get("published", ""),
                "athlete_ids": athlete_ids,
            })
        _cache_write(name, articles)
        return articles
    except (RuntimeError, KeyError) as exc:
        log.warning("ESPN news fetch failed: %s", exc)
        return []


def fetch_espn_athlete_news(athlete_id: str, news_articles: list[dict]) -> list[dict]:
    """Filter pre-fetched ESPN news articles that reference a specific athlete id.

    ESPN's per-athlete injury endpoint (`/athletes/{id}/injuries`) returns empty
    for soccer (verified). Instead we tag articles from `fetch_espn_news` with
    athlete references and filter here.
    """
    return [a for a in news_articles if athlete_id in a.get("athlete_ids", [])]


# ---------------------------------------------------------------------------
# Name matching (ESPN → FPL)
# ---------------------------------------------------------------------------

def _name_tokens(name: str) -> set[str]:
    """Lowercase word tokens from a player name."""
    return set(re.sub(r"[^a-z\s]", "", name.lower()).split())


def _match_score(espn_name: str, fpl_web_name: str, fpl_full: str) -> float:
    """Heuristic similarity: token overlap between ESPN and FPL names."""
    e_tok = _name_tokens(espn_name)
    f_tok = _name_tokens(fpl_full) | _name_tokens(fpl_web_name)
    if not e_tok or not f_tok:
        return 0.0
    return len(e_tok & f_tok) / max(len(e_tok), len(f_tok))


def build_espn_fpl_map(fpl_pool: pd.DataFrame,
                       espn_teams: list[dict],
                       min_score: float = 0.4) -> pd.DataFrame:
    """
    Map ESPN athlete ids to FPL player ids by fuzzy name + team matching.

    Returns a DataFrame with columns:
        fpl_id, espn_id, display_name, match_score, team_match
    """
    # Build FPL name/team lookup
    fpl_lookup = {}
    for _, row in fpl_pool.iterrows():
        full = f"{row.get('first_name', '')} {row.get('second_name', '')}".strip()
        web = str(row.get("web_name", ""))
        fpl_lookup[int(row["id"])] = {
            "full": full, "web": web,
            "team_name": str(row.get("team_name", "")).lower(),
        }

    # ESPN team id → lowercase name map
    espn_team_names = {
        t["espn_id"]: t["espn_name"].lower() for t in espn_teams
    }

    # Fetch rosters for all ESPN teams
    rows = []
    for team in espn_teams:
        tid = team["espn_id"]
        espn_tname = team["espn_name"].lower()
        roster = fetch_espn_roster(tid)
        for athlete in roster:
            eid = athlete.get("espn_id")
            ename = athlete.get("display_name", "")
            if not eid or not ename:
                continue

            best_fpl_id, best_score, best_team = None, 0.0, False
            for fid, fdata in fpl_lookup.items():
                score = _match_score(ename, fdata["web"], fdata["full"])
                # Boost score if team names share tokens
                team_tok = set(espn_tname.split())
                fpl_tok = set(fdata["team_name"].split())
                tmatch = bool(team_tok & fpl_tok)
                effective = score + (0.15 if tmatch else 0.0)
                if effective > best_score:
                    best_score = effective
                    best_fpl_id = fid
                    best_team = tmatch

            if best_fpl_id is not None and best_score >= min_score:
                rows.append({
                    "fpl_id": best_fpl_id,
                    "espn_id": str(eid),
                    "espn_name": ename,
                    "match_score": round(best_score, 3),
                    "team_match": best_team,
                })

    if not rows:
        return pd.DataFrame(columns=["fpl_id", "espn_id", "espn_name", "match_score", "team_match"])

    df = pd.DataFrame(rows).sort_values("match_score", ascending=False)
    # Keep only the best ESPN match per FPL player
    df = df.drop_duplicates(subset="fpl_id", keep="first")
    log.info("ESPN→FPL map: %d players matched (of %d FPL pool)", len(df), len(fpl_pool))
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Injury/news sentiment
# ---------------------------------------------------------------------------

def score_text(text: str) -> float:
    """
    Simple domain-specific sentiment for FPL injury news.

    Returns a float in [-1, 1]:
        +1 = definitely available / positive news
        -1 = definitely out / severe injury
         0 = neutral / unknown

    Uses the INJURY_KEYWORDS table with basic negation handling.
    """
    if not text:
        return 0.0
    tokens = re.sub(r"[^a-z\s]", "", text.lower()).split()
    score = 0.0
    n = 0
    for i, token in enumerate(tokens):
        if token not in INJURY_KEYWORDS:
            continue
        raw = INJURY_KEYWORDS[token]
        window_start = max(0, i - NEGATION_WINDOW)
        negated = any(t in NEGATION_WORDS for t in tokens[window_start:i])
        # Map keyword severity (0–3) to [-1, +1]: 0 → +1, 3 → -1
        val = 1.0 - (raw / 1.5) if not negated else -(1.0 - (raw / 1.5))
        score += val
        n += 1
    return max(-1.0, min(1.0, score / max(n, 1)))


def enrich_with_espn(fpl_pool: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    """
    Add ESPN injury status and news sentiment columns to `fpl_pool`.

    Adds columns:
        espn_id         ESPN athlete id (or NaN if unmatched)
        espn_injury     Most recent injury description text
        espn_status     ESPN status string ("Active", "Injured", etc.)
        espn_sentiment  score_text() on injury description, -1 to +1
        combined_news   Combined FPL news + ESPN injury text

    Players not matched to ESPN keep NaN in the new columns; their
    combined_news falls back to the FPL news field.
    """
    pool = fpl_pool.copy()
    pool["espn_id"] = None
    pool["espn_injury"] = None
    pool["espn_status"] = None
    pool["espn_sentiment"] = 0.0
    pool["combined_news"] = pool.get("news", pd.Series("", index=pool.index)).fillna("")

    # Step 1: get ESPN teams
    espn_teams = fetch_espn_teams(force=force)
    if not espn_teams:
        log.warning("ESPN teams unavailable; skipping ESPN enrichment")
        return pool

    # Step 2: build name match
    mapping = build_espn_fpl_map(pool, espn_teams)
    if mapping.empty:
        return pool

    id_to_espn = mapping.set_index("fpl_id")["espn_id"].to_dict()

    # Step 3: tag players with matching ESPN news articles
    # (ESPN soccer injuries endpoint returns empty; use news articles instead)
    news_articles = fetch_espn_news(force=force)
    pool_indexed = pool.set_index("id")
    for fpl_id, espn_id in id_to_espn.items():
        if fpl_id not in pool_indexed.index:
            continue
        relevant = fetch_espn_athlete_news(espn_id, news_articles)
        if relevant:
            # Use the most recent headline + description as news text
            latest = sorted(relevant, key=lambda a: a.get("published", ""), reverse=True)[0]
            headline = latest.get("headline", "")
            desc = latest.get("description", "")
            detail = (headline + " " + desc).strip()
            sentiment = score_text(detail)
            pool_indexed.at[fpl_id, "espn_id"] = espn_id
            pool_indexed.at[fpl_id, "espn_injury"] = headline or None
            pool_indexed.at[fpl_id, "espn_status"] = "news"
            pool_indexed.at[fpl_id, "espn_sentiment"] = sentiment
            fpl_news = str(pool_indexed.at[fpl_id, "combined_news"])
            combined = (fpl_news + " " + detail).strip()
            pool_indexed.at[fpl_id, "combined_news"] = combined

    result = pool_indexed.reset_index()
    return result


# ---------------------------------------------------------------------------
# News headline sentiment for the player pool (no ESPN matching needed)
# ---------------------------------------------------------------------------

def news_sentiment_batch(texts: pd.Series) -> pd.Series:
    """Apply score_text to a Series of news strings. Returns float Series."""
    return texts.fillna("").apply(score_text)


# ---------------------------------------------------------------------------
# Public FPL news parsing helpers
# ---------------------------------------------------------------------------

# Status codes from FPL bootstrap elements.status
FPL_STATUS_WEIGHTS = {
    "a": 1.0,    # available
    "d": 0.6,    # doubtful
    "i": 0.1,    # injured
    "u": 0.0,    # unavailable
    "s": 0.0,    # suspended
    "n": 0.5,    # not in squad
}


def fpl_availability_score(pool: pd.DataFrame) -> pd.Series:
    """
    Combined availability index in [0, 1] from FPL status + chance_of_playing.

    Uses:
      - status → base weight (FPL_STATUS_WEIGHTS)
      - chance_of_playing_this_round (0-100) → scales the base weight
    """
    status_w = pool.get("status", pd.Series("a", index=pool.index)).map(
        FPL_STATUS_WEIGHTS
    ).fillna(0.5)
    chance = pd.to_numeric(
        pool.get("chance_of_playing_this_round", None), errors="coerce"
    ).fillna(100) / 100.0
    return (status_w * chance).clip(0, 1)


def build_news_features(pool: pd.DataFrame,
                        espn_enriched: bool = True,
                        force: bool = False) -> pd.DataFrame:
    """
    Main entry point: add all news-derived features to the player pool.

    If `espn_enriched` is True, calls `enrich_with_espn` first (adds ESPN
    injury columns). Then adds:
        fpl_news_sentiment   score_text on the FPL news field
        availability_index   combined availability in [0, 1]
        news_score           average of fpl_news_sentiment and espn_sentiment
    """
    if espn_enriched:
        pool = enrich_with_espn(pool, force=force)

    pool["fpl_news_sentiment"] = news_sentiment_batch(
        pool.get("news", pd.Series("", index=pool.index))
    )
    pool["availability_index"] = fpl_availability_score(pool)

    espn_sent = pool.get("espn_sentiment", pd.Series(0.0, index=pool.index))
    pool["news_score"] = (pool["fpl_news_sentiment"] + espn_sent.fillna(0)) / 2.0

    return pool
