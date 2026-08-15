"""
Reddit FPL sentiment scanner — best-effort player buzz detection.

Scrapes r/FantasyPremierLeague hot posts via the public JSON API (no auth
required). Matches player web_names against post titles/bodies and scores
sentiment using keyword windows.

This module is report-only: results go into the deep research markdown and
are NOT converted to idea_list entries (Reddit sentiment is too noisy to
drive automatic transfers).

Usage:
    from bot.web_scanner import WebScanner
    report = WebScanner().scan(bootstrap_elements)
    # report = {"players": [...], "available": bool, "source": str}
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_REDDIT_URL = "https://www.reddit.com/r/FantasyPremierLeague/hot.json?limit=50"
_USER_AGENT = "fpl-auto-bot/1.0 research-scanner"
_CONTEXT_CHARS = 120

_POSITIVE = [
    "buy", "captain", "essential", "must have", "must own", "haul",
    "great pick", "good pick", "clean sheet", "form", "on fire", "starter",
    "guaranteed", "back fit", "fit again", "return", "in form",
]
_NEGATIVE = [
    "injury", "injured", "doubt", "suspended", "suspension", "banned",
    "bench", "benched", "dropped", "sell", "avoid", "rotation", "rotated",
    "rest", "crisis", "poor form", "bad form", "not starting", "miss",
    "missing", "absent", "withdrawn",
]


def _compile_patterns(words: list[str]) -> list[re.Pattern]:
    return [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in words]


_POS_RE = _compile_patterns(_POSITIVE)
_NEG_RE = _compile_patterns(_NEGATIVE)


def _sentiment_score(text: str, name: str) -> tuple[int, int]:
    """Count positive and negative keyword hits near the player name."""
    pos = neg = 0
    name_re = re.compile(re.escape(name), re.IGNORECASE)
    for m in name_re.finditer(text):
        start = max(0, m.start() - _CONTEXT_CHARS)
        end = min(len(text), m.end() + _CONTEXT_CHARS)
        window = text[start:end]
        pos += sum(1 for p in _POS_RE if p.search(window))
        neg += sum(1 for n in _NEG_RE if n.search(window))
    return pos, neg


class WebScanner:
    def scan(self, elements: list[dict]) -> dict:
        """Scan Reddit for player mentions and return a sentiment report.

        Parameters
        ----------
        elements:
            ``bootstrap["elements"]`` list.

        Returns
        -------
        dict
            ``available`` bool, ``source`` URL, ``players`` list sorted by
            |sentiment| descending (most-discussed first).
        """
        try:
            import requests
            resp = requests.get(
                _REDDIT_URL,
                headers={"User-Agent": _USER_AGENT},
                timeout=15,
            )
            if resp.status_code == 403:
                log.warning("Reddit returned 403 (likely GitHub Actions IP block) — skipping web scan.")
                return {"available": False, "source": _REDDIT_URL, "players": []}
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("Web scanner failed to fetch Reddit (%s) — skipping.", exc)
            return {"available": False, "source": _REDDIT_URL, "players": []}

        posts = []
        for child in (data.get("data") or {}).get("children") or []:
            post = child.get("data") or {}
            title = post.get("title") or ""
            body = post.get("selftext") or ""
            posts.append(title + " " + body)

        combined = " ".join(posts)
        log.info("Web scanner: fetched %d Reddit posts (%d chars).", len(posts), len(combined))

        results = []
        seen_names: set[str] = set()
        for el in elements:
            name = (el.get("web_name") or "").strip()
            if not name or name in seen_names or len(name) < 4:
                continue
            seen_names.add(name)
            pos, neg = _sentiment_score(combined, name)
            if pos + neg == 0:
                continue
            results.append({
                "name": name,
                "element": int(el.get("id", 0)),
                "positive": pos,
                "negative": neg,
                "net": pos - neg,
            })

        results.sort(key=lambda r: -(abs(r["net"]) + r["positive"] + r["negative"]))
        log.info("Web scanner: %d player(s) mentioned on Reddit.", len(results))
        return {
            "available": True,
            "source": _REDDIT_URL,
            "players": results[:20],
        }
