"""
AltLines — real data (module 1, live edition).

Builds the two input frames the engine needs from REAL sources, for the games
of one slate:

    game logs  ← ESPN athlete game logs (free, no key)
    alt odds   ← The Odds API v4, the full alternate ladder of every US book

Why ESPN and not stats.nba.com: stats.nba.com throttles/blocks non-browser
clients (it times out from most home connections), while ESPN's public JSON is
the same source the calendar already uses.

Quota — read this before clicking "Simular todos" in real mode
---------------------------------------------------------------
The Odds API charges   credits = markets_returned × regions   per event call.
With the default (5 alt markets, region "us") a game costs ≤ 5 credits. The
free plan is 500 credits/month and the MLB tab spends from the same key, so a
full 10-game night (~50 credits) is affordable a handful of times, not daily.
Every response is cached on disk (`data/altlines/cache/`) for
`LIVE_ODDS_TTL_S`, so re-opening a game never pays twice.

Identity
--------
Players are keyed `ESPN-<athlete id>` in BOTH frames. The Odds API names a
player only by display name, so odds are joined to the ESPN roster of the two
teams of that game through a normalised name (accents, punctuation and
Jr./II suffixes stripped). Odds whose name cannot be resolved are dropped and
counted in `meta["unmatched_names"]` — never guessed.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from altlines import config as cfg
from altlines.ingest import GAME_LOG_COLUMNS, ODDS_COLUMNS
from altlines.slate import normalize_tricode

logger = logging.getLogger(__name__)

CACHE_DIR = cfg.DATA_DIR / "cache"

_ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team}/roster"
_ESPN_GAMELOG = ("https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/"
                 "athletes/{aid}/gamelog")

# Our tricodes → the abbreviation ESPN's team endpoints expect.
_ESPN_TEAM_ABBR = {"GSW": "gs", "NYK": "ny", "SAS": "sa", "NOP": "no",
                   "UTA": "utah", "WAS": "wsh"}

# Odds API market key → our market. Main AND alternate keys map to the same
# market: both are just points on the same ladder.
_MARKET_FROM_API = {}
for _m, _k in cfg.ODDS_API_MARKET_KEYS.items():
    _MARKET_FROM_API[_k] = _m
    _MARKET_FROM_API[_k + cfg.ODDS_API_ALT_SUFFIX] = _m


# ══════════════════════════════════════════════════════════════════════════════
# Small helpers: names, disk cache, HTTP
# ══════════════════════════════════════════════════════════════════════════════

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(name) -> str:
    """'Nikola Jokić' / 'Jaren Jackson Jr.' → 'nikola jokic' / 'jaren jackson'."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", " ", s.lower().replace("'", "").replace(".", ""))
    parts = [p for p in s.split() if p and p not in _SUFFIXES]
    return " ".join(parts)


def _cache_path(kind: str, key: str) -> Path:
    return CACHE_DIR / kind / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.json"


def _cache_get(kind: str, key: str, ttl_s: float | None):
    """Cached JSON, or None when missing / older than `ttl_s` (None = forever)."""
    p = _cache_path(kind, key)
    if not p.exists():
        return None
    if ttl_s is not None and time.time() - p.stat().st_mtime > ttl_s:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt cache file is just a miss
        return None


def _cache_put(kind: str, key: str, payload) -> None:
    p = _cache_path(kind, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload), encoding="utf-8")


def _get_json(url: str, params: dict | None = None, timeout: float = 20.0):
    r = httpx.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r


def _season_end_year(on: datetime | None = None) -> int:
    """ESPN labels a season by the year it ends (2025-26 → 2026)."""
    on = on or datetime.now(timezone.utc)
    return on.year + 1 if on.month >= 8 else on.year


# ══════════════════════════════════════════════════════════════════════════════
# 1. Rosters (name → ESPN athlete id)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_roster(tricode: str, refresh: bool = False) -> list[dict]:
    """Current roster of one team: [{athlete_id, name, position, team}]."""
    tr = normalize_tricode(tricode)
    cached = None if refresh else _cache_get("roster", tr, cfg.LIVE_ROSTER_TTL_S)
    if cached is not None:
        return cached
    abbr = _ESPN_TEAM_ABBR.get(tr, tr.lower())
    try:
        data = _get_json(_ESPN_ROSTER.format(team=abbr)).json()
    except Exception as exc:  # noqa: BLE001 - network boundary
        logger.warning("ESPN roster failed for %s: %s", tr, exc)
        return _cache_get("roster", tr, None) or []
    out = []
    for a in data.get("athletes") or []:
        out.append({
            "athlete_id": str(a.get("id")),
            "name": a.get("displayName") or a.get("fullName") or "",
            "position": ((a.get("position") or {}).get("abbreviation")) or "",
            "team": tr,
        })
    _cache_put("roster", tr, out)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 2. Game logs
# ══════════════════════════════════════════════════════════════════════════════

def _split_made_att(s) -> tuple[float, float]:
    try:
        made, att = str(s).split("-")
        return float(made), float(att)
    except Exception:  # noqa: BLE001
        return np.nan, np.nan


def _to_float(s) -> float:
    try:
        return float(s)
    except Exception:  # noqa: BLE001
        return np.nan


def _abbr(team) -> str | None:
    """ESPN sends the team as 'DET' in the regular season and as an object in
    the playoffs."""
    if isinstance(team, dict):
        return team.get("abbreviation")
    return team


def fetch_athlete_games(athlete_id: str, season: int, current: bool,
                        refresh: bool = False) -> list[dict]:
    """One row per game played (regular season + playoffs) for one season.

    A finished season is cached forever; the current one for
    `LIVE_LOGS_TTL_S`, since a game may have been added since.
    """
    key = f"{athlete_id}_{season}"
    ttl = cfg.LIVE_LOGS_TTL_S if current else None
    cached = None if refresh else _cache_get("gamelog", key, ttl)
    if cached is not None:
        return cached
    try:
        data = _get_json(_ESPN_GAMELOG.format(aid=athlete_id),
                         params={"season": season}).json()
    except httpx.HTTPStatusError as exc:
        # ESPN answers 4xx for a season the player was not in the league.
        if exc.response is not None and exc.response.status_code < 500:
            _cache_put("gamelog", key, [])
            return []
        logger.warning("ESPN gamelog failed for %s/%s: %s", athlete_id, season, exc)
        return _cache_get("gamelog", key, None) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("ESPN gamelog failed for %s/%s: %s", athlete_id, season, exc)
        return _cache_get("gamelog", key, None) or []

    names = data.get("names") or []
    idx = {n: i for i, n in enumerate(names)}
    events = data.get("events") or {}
    rows: list[dict] = []
    seen: set[str] = set()
    for st in data.get("seasonTypes") or []:
        label = str(st.get("displayName") or "")
        if "Preseason" in label or "All-Star" in label:
            continue
        if "Postseason" in label and not cfg.LIVE_INCLUDE_PLAYOFFS:
            continue
        for cat in st.get("categories") or []:
            for ev in cat.get("events") or []:
                eid = str(ev.get("eventId"))
                stats = ev.get("stats") or []
                if eid in seen or len(stats) < len(names):
                    continue
                seen.add(eid)
                meta = events.get(eid) or {}
                fgm, fga = _split_made_att(stats[idx["fieldGoalsMade-fieldGoalsAttempted"]])
                tpm, tpa = _split_made_att(stats[idx["threePointFieldGoalsMade-threePointFieldGoalsAttempted"]])
                rows.append({
                    "event_id": eid,
                    "date": (meta.get("gameDate") or "")[:10],
                    "team": _abbr(meta.get("team")),
                    "opponent": _abbr(meta.get("opponent")),
                    "home_away": 0 if meta.get("atVs") == "@" else 1,
                    "season": season,
                    "playoffs": "Postseason" in label,
                    "min": _to_float(stats[idx["minutes"]]),
                    "fga": fga, "tpm": tpm, "tpa": tpa,
                    "reb": _to_float(stats[idx["totalRebounds"]]),
                    "ast": _to_float(stats[idx["assists"]]),
                    "pts": _to_float(stats[idx["points"]]),
                })
    _cache_put("gamelog", key, rows)
    return rows


def games_to_logs(games: list[dict], player_id: str, player_name: str,
                  team: str, position: str, game_id: str) -> list[dict]:
    """Wide box-score rows → the engine's long (one row per market) format.

    `usage` is the opportunity driver each market declares in config:
    FGA for points, minutes for rebounds/assists/PRA, 3PA for threes.
    `team` is the player's CURRENT team (it is what places him in tonight's
    game); the historical opponent is kept.
    """
    out: list[dict] = []
    played = [g for g in games if g.get("min") and g["min"] > 0]
    # Condition on the player's NORMAL role: a 9-minute injury exit or a
    # minutes-restricted return is an availability event, not a draw from his
    # stat distribution. Left in, those games drag the projected level down
    # and fatten the spread at the same time (both fake a tail edge).
    if played:
        typical = float(np.median([g["min"] for g in played]))
        floor = cfg.LIVE_MIN_MINUTES_FRAC * typical
        played = [g for g in played if g["min"] >= floor]
    for g in played:
        pts, reb, ast = g["pts"], g["reb"], g["ast"]
        per_market = {
            "points":   (pts, g["fga"]),
            "rebounds": (reb, g["min"]),
            "assists":  (ast, g["min"]),
            "threes":   (g["tpm"], g["tpa"]),
            "pra":      (pts + reb + ast, g["min"]),
        }
        for market, (value, usage) in per_market.items():
            if market not in cfg.MARKET_SPECS or not np.isfinite(value):
                continue
            out.append({
                "date": g["date"], "season": g["season"], "week": None,
                "game_id": game_id, "player_id": player_id,
                "player_name": player_name, "team": team,
                "opponent": normalize_tricode(g.get("opponent")),
                "position": position, "market": market, "value": value,
                "usage": usage, "snap_share": None,
                "home_away": g.get("home_away"), "rest_days": None,
                "opp_def_rating": None,
            })
    return out


# ══════════════════════════════════════════════════════════════════════════════
# 3. Odds (The Odds API) — the alt tray of every book
# ══════════════════════════════════════════════════════════════════════════════

def _nickname(team_display: str) -> str:
    """'Portland Trail Blazers' → 'blazers' (the last word is unique in the NBA)."""
    words = norm_name(team_display).split()
    return words[-1] if words else ""


def list_odds_events(refresh: bool = False) -> list[dict]:
    """Upcoming NBA events. This endpoint is FREE (costs no credits)."""
    cached = None if refresh else _cache_get("odds", "events", cfg.LIVE_EVENTS_TTL_S)
    if cached is not None:
        return cached
    if not cfg.is_odds_configured():
        raise RuntimeError("Falta THE_ODDS_API_KEY / ODDS_API_KEY (ver altlines/config.py).")
    r = _get_json(f"{cfg.ODDS_API_BASE}/sports/{cfg.SPORT_KEY}/events",
                  params={"apiKey": cfg.odds_api_key()})
    events = r.json()
    _cache_put("odds", "events", events)
    return events


def match_event(game: dict, events: list[dict]) -> dict | None:
    """Find the Odds API event of a slate game (by team nicknames + date)."""
    home = _nickname((game.get("home") or {}).get("display") or "")
    away = _nickname((game.get("away") or {}).get("display") or "")
    tip = str(game.get("tip_off") or "")[:10]
    best = None
    for ev in events:
        if _nickname(ev.get("home_team", "")) == home and _nickname(ev.get("away_team", "")) == away:
            # Same matchup can repeat within a season: prefer the same day.
            if str(ev.get("commence_time", ""))[:10] == tip:
                return ev
            best = best or ev
    return best


_LAST_QUOTA: dict = {"remaining": None, "used": None, "last_cost": None, "ts": None}


def quota_status() -> dict:
    return dict(_LAST_QUOTA)


def fetch_event_odds(event_id: str, markets: list[str] | None = None,
                     refresh: bool = False) -> dict:
    """Raw odds payload of one event (cached on disk for LIVE_ODDS_TTL_S).

    THIS is the call that costs credits — one per market returned per region.
    """
    markets = markets or list(cfg.LIVE_MARKETS)
    keys = []
    for m in markets:
        base = cfg.ODDS_API_MARKET_KEYS.get(m)
        if not base:
            continue
        keys.append(base + cfg.ODDS_API_ALT_SUFFIX)
        if cfg.LIVE_INCLUDE_MAIN:
            keys.append(base)
    key_str = ",".join(sorted(keys))
    ck = f"{event_id}_{cfg.LIVE_ODDS_REGIONS}_{key_str}"
    cached = None if refresh else _cache_get("odds", ck, cfg.LIVE_ODDS_TTL_S)
    if cached is not None:
        return cached
    if not cfg.is_odds_configured():
        raise RuntimeError("Falta THE_ODDS_API_KEY / ODDS_API_KEY (ver altlines/config.py).")
    r = _get_json(f"{cfg.ODDS_API_BASE}/sports/{cfg.SPORT_KEY}/events/{event_id}/odds",
                  params={"apiKey": cfg.odds_api_key(), "regions": cfg.LIVE_ODDS_REGIONS,
                          "markets": key_str, "oddsFormat": "decimal"},
                  timeout=30)
    _LAST_QUOTA.update({
        "remaining": r.headers.get("x-requests-remaining"),
        "used": r.headers.get("x-requests-used"),
        "last_cost": r.headers.get("x-requests-last"),
        "ts": datetime.now().isoformat(timespec="seconds"),
    })
    logger.info("Odds API: event %s cost %s credits, %s remaining", event_id,
                _LAST_QUOTA["last_cost"], _LAST_QUOTA["remaining"])
    payload = r.json()
    _cache_put("odds", ck, payload)
    return payload


def parse_event_odds(payload: dict) -> pd.DataFrame:
    """Odds API event payload → one row per (book, player, market, side, point)."""
    rows = []
    for bm in payload.get("bookmakers") or []:
        for mkt in bm.get("markets") or []:
            market = _MARKET_FROM_API.get(mkt.get("key"))
            if market is None:
                continue
            for oc in mkt.get("outcomes") or []:
                side = str(oc.get("name") or "").lower()
                if side not in ("over", "under") or oc.get("point") is None:
                    continue
                rows.append({
                    "snapshot_ts": mkt.get("last_update") or bm.get("last_update"),
                    "event_date": payload.get("commence_time"),
                    "player_name": oc.get("description"),
                    "market": market,
                    "book": bm.get("key"),
                    "side": side,
                    "point": float(oc["point"]),
                    "decimal_odds": float(oc.get("price") or 0),
                    "is_alt": str(mkt.get("key", "")).endswith(cfg.ODDS_API_ALT_SUFFIX),
                })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df[df["decimal_odds"] > 1.0]
    # A book listing the exact same quote twice (Bovada does) is one quote.
    return df.drop_duplicates(["book", "player_name", "market", "side", "point"]).reset_index(drop=True)


def consensus_main_line(odds: pd.DataFrame) -> pd.Series:
    """Market median per (player, market): the point where the book is 50/50.

    For each book, interpolate the point at which P(over) crosses 0.5 along its
    ladder (over prices only, a fixed margin removed); then take the median
    across books. That is exactly the "main line" the thesis says books anchor
    on, rebuilt from the tray itself, so we do not need to pay for the main
    market too.
    """
    over = odds[odds["side"] == "over"].copy()
    if over.empty:
        return pd.Series(dtype=float)
    over["p"] = (1.0 / over["decimal_odds"]) / (1.0 + cfg.ASSUMED_MARGIN_SINGLE_SIDED)
    per_book = []
    for (pname, market, book), g in over.groupby(["player_name", "market", "book"]):
        g = g.sort_values("point")
        pts, ps = g["point"].to_numpy(float), g["p"].to_numpy(float)
        if len(pts) == 1:
            # A lone quote is only informative if it is near even money.
            if 0.40 <= ps[0] <= 0.60:
                per_book.append((pname, market, pts[0]))
            continue
        # P(over) falls with the point; interpolate the 0.5 crossing.
        if ps.min() > 0.5 or ps.max() < 0.5:
            continue
        ml = float(np.interp(0.5, ps[::-1], pts[::-1]))
        per_book.append((pname, market, ml))
    if not per_book:
        return pd.Series(dtype=float)
    pb = pd.DataFrame(per_book, columns=["player_name", "market", "main_line"])
    # Main lines live on the .5 grid.
    med = pb.groupby(["player_name", "market"])["main_line"].median()
    return (np.floor(med) + 0.5).rename("main_line")


# ══════════════════════════════════════════════════════════════════════════════
# 4. The whole thing for a slate
# ══════════════════════════════════════════════════════════════════════════════

def build_live_dataset(slate: dict, game: str | None = None,
                       markets: list[str] | None = None,
                       refresh_odds: bool = False) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Real (logs, odds, meta) for the games of `slate` (or only `game`).

    Only players that actually HAVE alt lines tonight get their logs fetched:
    that is what keeps a 10-game night to ~100 ESPN calls instead of 450.
    """
    games = list(slate.get("games") or [])
    if game:
        games = [g for g in games if str(g["game_id"]) == str(game)]
    events = list_odds_events()
    season_now = _season_end_year()

    log_rows: list[dict] = []
    odds_frames: list[pd.DataFrame] = []
    meta = {"games": [], "unmatched_names": [], "no_event": [], "quota": None}

    for g in games:
        gid = str(g["game_id"])
        home_tr = normalize_tricode(g["home"]["tricode"])
        away_tr = normalize_tricode(g["away"]["tricode"])
        if g.get("state") == "post":
            meta["no_event"].append({"game_id": gid, "reason": "partido terminado"})
            continue
        ev = match_event(g, events)
        if ev is None:
            meta["no_event"].append({"game_id": gid, "reason": "sin evento en The Odds API"})
            continue

        odds = parse_event_odds(fetch_event_odds(ev["id"], markets, refresh=refresh_odds))
        if odds.empty:
            meta["no_event"].append({"game_id": gid, "reason": "el libro aún no publicó props"})
            continue

        # name → (athlete id, team, position) across both rosters
        roster = {}
        for tr in (home_tr, away_tr):
            for a in fetch_roster(tr):
                roster.setdefault(norm_name(a["name"]), a)

        odds["key"] = odds["player_name"].map(norm_name)
        known = odds["key"].isin(roster.keys())
        for nm in sorted(odds.loc[~known, "player_name"].unique()):
            meta["unmatched_names"].append({"game_id": gid, "name": nm})
        odds = odds[known].copy()
        if odds.empty:
            continue

        odds["player_id"] = odds["key"].map(lambda k: f"ESPN-{roster[k]['athlete_id']}")
        odds["team"] = odds["key"].map(lambda k: roster[k]["team"])
        odds["game_id"] = gid
        main = consensus_main_line(odds)
        odds = odds.merge(main.reset_index(), on=["player_name", "market"], how="left")
        odds_frames.append(odds)

        n_players = 0
        for k in odds["key"].unique():
            a = roster[k]
            pid = f"ESPN-{a['athlete_id']}"
            games_hist = []
            for season in (season_now, season_now - 1):
                games_hist += fetch_athlete_games(a["athlete_id"], season,
                                                  current=(season == season_now))
            rows = games_to_logs(games_hist, pid, a["name"], a["team"],
                                 a["position"], gid)
            if rows:
                n_players += 1
                log_rows += rows
        meta["games"].append({"game_id": gid, "event_id": ev["id"],
                              "players": n_players, "lines": int(len(odds)),
                              "books": sorted(odds["book"].unique().tolist())})

    meta["quota"] = quota_status()
    logs = pd.DataFrame(log_rows, columns=GAME_LOG_COLUMNS)
    if not logs.empty:
        logs["date"] = pd.to_datetime(logs["date"], errors="coerce")
        logs = (logs.dropna(subset=["value"])
                    .drop_duplicates(["date", "player_id", "market"])
                    .sort_values(["player_id", "market", "date"])
                    .reset_index(drop=True))
    odds_df = (pd.concat(odds_frames, ignore_index=True) if odds_frames
               else pd.DataFrame(columns=ODDS_COLUMNS))
    keep = [c for c in ODDS_COLUMNS if c in odds_df.columns] + ["is_alt"]
    odds_df = odds_df[[c for c in keep if c in odds_df.columns]]
    return logs, odds_df, meta
