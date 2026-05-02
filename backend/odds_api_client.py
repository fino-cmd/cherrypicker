"""
The Odds API client for CherryPick.

Handles MLB pitcher_strikeouts props ingestion across multiple sportsbooks.
Free tier: 500 reqs/month. The cache strategy is designed around that:
  • Events list (today's MLB schedule) cached 1h — first call of the day
    pays the request, the rest of the slate reuses it.
  • Per-event odds cached 5m — submit_pick_line typically processes one
    pitcher at a time, but a re-submit within 5 min costs 0 calls.

Returns gracefully empty when no API key is configured, so the existing
manual-entry flow keeps working.

Docs: https://the-odds-api.com/liveapi/guides/v4/
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = "baseball_mlb"

# Default per-book weights for the consensus fair-price calculation.
# These are HEURISTIC starting points — Tanda 3 will replace with backtested
# weights derived from each book's historical accuracy on settled picks.
# Keys match The Odds API's bookmaker `key` field (lowercase, no spaces).
PROP_BOOK_WEIGHTS: dict[str, float] = {
    "fanduel":      0.20,
    "draftkings":   0.20,
    "betmgm":       0.15,
    "williamhill_us": 0.15,  # Caesars in The Odds API
    "betrivers":    0.10,
    "betonlineag":  0.10,
    "bovada":       0.05,
    "lowvig":       0.05,
    "mybookieag":   0.03,
    "betus":        0.02,
    # Sharp anchors when accessible — Tanda 3 (Pinnacle scraper) sets pinnacle higher
    "pinnacle":     0.30,
}
DEFAULT_BOOK_WEIGHT = 0.05  # any book not in the map above

# Map MLB abbrev → full team name as used by The Odds API (matches MLB Stats
# API's "teamName" field). Maintained manually because abbreviations don't
# round-trip cleanly across sources.
TEAM_NAME_MAP: dict[str, str] = {
    "ARI": "Arizona Diamondbacks",
    "ATL": "Atlanta Braves",
    "BAL": "Baltimore Orioles",
    "BOS": "Boston Red Sox",
    "CHC": "Chicago Cubs",
    "CWS": "Chicago White Sox",
    "CHW": "Chicago White Sox",
    "CIN": "Cincinnati Reds",
    "CLE": "Cleveland Guardians",
    "COL": "Colorado Rockies",
    "DET": "Detroit Tigers",
    "HOU": "Houston Astros",
    "KC":  "Kansas City Royals",
    "KCR": "Kansas City Royals",
    "LAA": "Los Angeles Angels",
    "LAD": "Los Angeles Dodgers",
    "MIA": "Miami Marlins",
    "MIL": "Milwaukee Brewers",
    "MIN": "Minnesota Twins",
    "NYM": "New York Mets",
    "NYY": "New York Yankees",
    "OAK": "Oakland Athletics",
    "ATH": "Athletics",
    "PHI": "Philadelphia Phillies",
    "PIT": "Pittsburgh Pirates",
    "SD":  "San Diego Padres",
    "SDP": "San Diego Padres",
    "SF":  "San Francisco Giants",
    "SFG": "San Francisco Giants",
    "SEA": "Seattle Mariners",
    "STL": "St. Louis Cardinals",
    "TB":  "Tampa Bay Rays",
    "TBR": "Tampa Bay Rays",
    "TEX": "Texas Rangers",
    "TOR": "Toronto Blue Jays",
    "WSH": "Washington Nationals",
    "WAS": "Washington Nationals",
}

# In-memory cache: { key: (timestamp, payload) }.
_cache: dict = {}
_DEFAULT_CACHE_TTL = 300       # 5 min for per-event odds
_EVENTS_CACHE_TTL  = 3600      # 1 h for today's schedule

# Quota tracking — exposed via /api/odds/quota for UI display.
_QUOTA_STATE: dict = {"used": None, "remaining": None, "last_seen": None}


def _api_key() -> Optional[str]:
    """Return the API key from env, or None when unset."""
    return os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")


def is_configured() -> bool:
    """True when an API key is present in env (length > 10 = sane key)."""
    key = _api_key()
    return bool(key and len(key) > 10)


def quota_state() -> dict:
    """Latest known quota from response headers. Returns empty dict before any call."""
    return dict(_QUOTA_STATE)


def _cache_get(key: str, ttl: float = _DEFAULT_CACHE_TTL):
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return data


def _cache_set(key: str, data) -> None:
    _cache[key] = (time.time(), data)


def _record_quota(response: httpx.Response) -> None:
    """The Odds API returns request usage in response headers."""
    used = response.headers.get("x-requests-used")
    remaining = response.headers.get("x-requests-remaining")
    if used is not None or remaining is not None:
        try:
            _QUOTA_STATE["used"] = int(used) if used is not None else _QUOTA_STATE["used"]
            _QUOTA_STATE["remaining"] = int(remaining) if remaining is not None else _QUOTA_STATE["remaining"]
            _QUOTA_STATE["last_seen"] = time.time()
        except ValueError:
            pass
        if remaining is not None and int(remaining or 0) < 50:
            logger.warning("Odds API quota low: %s remaining", remaining)


# ── HTTP layer ────────────────────────────────────────────────────────────────

async def _get(url: str, params: dict, timeout: float = 20.0) -> Optional[dict | list]:
    """One-shot GET with structured error handling. Returns None on any failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url, params=params)
            _record_quota(r)
            if r.status_code == 401:
                logger.error("Odds API: 401 unauthorized — check THE_ODDS_API_KEY")
                return None
            if r.status_code == 429:
                logger.warning("Odds API: 429 rate-limited at %s", url)
                return None
            if r.status_code == 422:
                logger.warning("Odds API: 422 — request invalid (likely market not on plan): %s", r.text[:200])
                return None
            r.raise_for_status()
            return r.json()
    except httpx.TimeoutException:
        logger.warning("Odds API: timeout on %s", url)
    except Exception as e:
        logger.warning("Odds API: request failed on %s: %s", url, e)
    return None


# ── Public fetch helpers ──────────────────────────────────────────────────────

async def fetch_events_today() -> list[dict]:
    """List MLB events that haven't started yet. Cached 1h.

    Each event has: id, commence_time, home_team, away_team. We use this to
    map a (pitcher_name, team) pick to the correct event_id before pulling
    its odds.
    """
    if not is_configured():
        return []
    cached = _cache_get("events_today", ttl=_EVENTS_CACHE_TTL)
    if cached is not None:
        return cached
    url = f"{API_BASE}/sports/{SPORT_KEY}/events"
    data = await _get(url, params={"apiKey": _api_key()})
    if not isinstance(data, list):
        return []
    _cache_set("events_today", data)
    logger.info("Odds API: fetched %d MLB events for today", len(data))
    return data


async def fetch_pitcher_props(event_id: str) -> Optional[dict]:
    """Fetch all pitcher_strikeouts odds for one event (every book in us,us2).

    Cached 5 min. Returns the event dict with .bookmakers populated, or None.
    """
    if not is_configured():
        return None
    cached = _cache_get(f"props_{event_id}")
    if cached is not None:
        return cached
    url = f"{API_BASE}/sports/{SPORT_KEY}/events/{event_id}/odds"
    params = {
        "apiKey":      _api_key(),
        "regions":     "us,us2",
        "markets":     "pitcher_strikeouts",
        "oddsFormat":  "decimal",
    }
    data = await _get(url, params=params)
    if not isinstance(data, dict):
        return None
    _cache_set(f"props_{event_id}", data)
    return data


# ── Matching helpers ──────────────────────────────────────────────────────────

def find_event_for_pitcher(events: list[dict], team_abbr: str) -> Optional[dict]:
    """Match an MLB event by team abbreviation. The Odds API uses full team
    names ("New York Yankees"); we map our internal abbrev → full name.

    Falls back to a substring match if the abbrev isn't in TEAM_NAME_MAP.
    """
    if not events or not team_abbr:
        return None
    full_name = TEAM_NAME_MAP.get(team_abbr.upper())
    for e in events:
        home = e.get("home_team") or ""
        away = e.get("away_team") or ""
        if full_name and (home == full_name or away == full_name):
            return e
    # Fallback: case-insensitive substring (handles abbreviations not in our map)
    abbr_lower = team_abbr.lower()
    for e in events:
        home = (e.get("home_team") or "").lower()
        away = (e.get("away_team") or "").lower()
        if abbr_lower in home or abbr_lower in away:
            return e
    return None


def extract_pitcher_market(event_odds: dict, pitcher_name: str) -> dict:
    """Pull this pitcher's K-prop quotes out of an event's bookmakers blob.

    Returns:
        {
            "pitcher_name": "Gerrit Cole",
            "k_line": 7.5,        # mode of points across books
            "books": [
                {"casino_key": "fanduel", "casino": "FanDuel",
                 "over": 1.91, "under": 1.95, "point": 7.5},
                ...
            ]
        }
    """
    if not event_odds or not pitcher_name:
        return {"pitcher_name": pitcher_name, "k_line": None, "books": []}

    pitcher_lower = pitcher_name.lower()
    books: list[dict] = []
    points: list[float] = []

    for bm in event_odds.get("bookmakers", []):
        casino_key   = (bm.get("key") or "").lower()
        casino_title = bm.get("title") or bm.get("key", "")
        for market in bm.get("markets", []):
            if market.get("key") != "pitcher_strikeouts":
                continue
            over_odds: Optional[float] = None
            under_odds: Optional[float] = None
            point: Optional[float] = None
            for outcome in market.get("outcomes", []):
                desc = (outcome.get("description") or "").lower()
                # Some books wrap names slightly differently — substring match
                # is more forgiving than equality.
                if pitcher_lower not in desc:
                    continue
                name  = (outcome.get("name") or "").lower()
                price = outcome.get("price")
                pt    = outcome.get("point")
                if price is None or pt is None:
                    continue
                if name == "over":
                    over_odds = float(price)
                    point     = float(pt)
                elif name == "under":
                    under_odds = float(price)
                    point      = float(pt)
            if over_odds is not None and under_odds is not None and point is not None:
                books.append({
                    "casino_key": casino_key,
                    "casino":     casino_title,
                    "over":       over_odds,
                    "under":      under_odds,
                    "point":      point,
                })
                points.append(point)

    # Most common point across books — handles minor disagreements (7.5 vs 8.0).
    k_line = max(set(points), key=points.count) if points else None
    return {"pitcher_name": pitcher_name, "k_line": k_line, "books": books}


# ── Consensus + best-price math ───────────────────────────────────────────────

def _book_weight(casino_key: str) -> float:
    """Weight for the consensus average. Defaults to DEFAULT_BOOK_WEIGHT for
    any book not explicitly mapped."""
    return PROP_BOOK_WEIGHTS.get(casino_key.lower(), DEFAULT_BOOK_WEIGHT)


def consensus_fair_price(books: list[dict]) -> dict:
    """De-vig each book independently, then weighted-average their fair p_over.

    Why per-book de-vig: if a book has tighter vig (Pinnacle ~2%) vs a wider
    one (DraftKings 6%), de-vigging at book level preserves their relative
    sharpness. A naive cross-book average would dilute.

    Returns:
        {
            "p_over_consensus":  0.487,     # weighted-mean fair P(OVER)
            "p_under_consensus": 0.513,
            "n_books":           7,         # books that contributed
            "weighted_total":    0.95,      # sum of weights used (sanity)
            "books_detail":      [...]      # per-book de-vigged probs
        }
    """
    detail = []
    weighted_sum = 0.0
    total_weight = 0.0
    n = 0
    for b in books:
        over = b.get("over")
        under = b.get("under")
        if not (over and under and over > 1 and under > 1):
            continue
        p_over_raw  = 1.0 / over
        p_under_raw = 1.0 / under
        z = p_over_raw + p_under_raw
        if z <= 0:
            continue
        p_over_fair = p_over_raw / z
        weight = _book_weight(b.get("casino_key", ""))
        detail.append({
            "casino":       b.get("casino"),
            "casino_key":   b.get("casino_key"),
            "p_over_fair":  round(p_over_fair, 4),
            "vig":          round(z - 1.0, 4),
            "weight":       round(weight, 4),
            "over":         over,
            "under":        under,
            "point":        b.get("point"),
        })
        weighted_sum += p_over_fair * weight
        total_weight += weight
        n += 1

    if total_weight == 0:
        return {
            "p_over_consensus":  None,
            "p_under_consensus": None,
            "n_books":           n,
            "weighted_total":    0.0,
            "books_detail":      detail,
        }
    p_over_consensus = weighted_sum / total_weight
    return {
        "p_over_consensus":  round(p_over_consensus, 4),
        "p_under_consensus": round(1.0 - p_over_consensus, 4),
        "n_books":           n,
        "weighted_total":    round(total_weight, 4),
        "books_detail":      detail,
    }


def best_price(books: list[dict], direction: str) -> Optional[dict]:
    """The book offering the highest odds for the given direction.

    Highest odds = best price for the bet (more profit per dollar staked).
    Returns {"casino", "casino_key", "odds", "point"} or None.
    """
    if not books or direction not in ("OVER", "UNDER"):
        return None
    key = "over" if direction == "OVER" else "under"
    best: Optional[dict] = None
    for b in books:
        v = b.get(key)
        if v is None:
            continue
        if best is None or v > best["odds"]:
            best = {
                "casino":     b.get("casino"),
                "casino_key": b.get("casino_key"),
                "odds":       float(v),
                "point":      b.get("point"),
            }
    return best
