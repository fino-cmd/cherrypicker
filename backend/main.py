import asyncio
import io
import logging
import os
import sqlite3
import sys
import time
import warnings
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import httpx
import numpy as np
import pandas as pd
import pybaseball
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

# ── Strikeout prediction model (root-level module) ────────────────────────────
warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
sys.path.insert(0, str(Path(__file__).parent.parent))
from predict import predict_strikeouts  # noqa: E402

# ── Config ─────────────────────────────────────────────────────────────────────
DB_PATH       = os.getenv("DB_PATH", "tracker.db")
ODDS_API_KEY  = os.getenv("ODDS_API_KEY", "")
CACHE_TTL     = int(os.getenv("CACHE_TTL_HOURS", "6")) * 3600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

pybaseball.cache.enable()

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="CherryPick MLB API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Simple in-memory cache ─────────────────────────────────────────────────────
_cache: dict = {}

def cache_get(key: str):
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None

def cache_set(key: str, data):
    _cache[key] = {"data": data, "ts": time.time()}

# ── Database ───────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT,
                game         TEXT,
                market       TEXT,
                odds_decimal REAL,
                result       TEXT,
                clv          REAL,
                units        REAL,
                notes        TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS picks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                date             TEXT    NOT NULL,
                pitcher_name     TEXT    NOT NULL,
                team             TEXT,
                opponent         TEXT,
                pitcher_id       INTEGER,
                k_line           REAL,
                model_proj       REAL,
                edge             REAL,
                ci_low           REAL,
                ci_high          REAL,
                pitch_limit_flag INTEGER DEFAULT 0,
                confidence       INTEGER,
                recommendation   TEXT,
                actual_ks        REAL,
                result           TEXT,
                UNIQUE(date, pitcher_name)
            )
        """)
        # Migrate: add pitcher_id column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE picks ADD COLUMN pitcher_id INTEGER")
        except Exception:
            pass
        conn.commit()

@app.on_event("startup")
def startup():
    init_db()
    logger.info("CherryPick API started — DB initialised at %s", DB_PATH)

# ── Simulation constants ───────────────────────────────────────────────────────
LEAGUE_AVG_RUNS    = 4.5
LEAGUE_AVG_XFIP    = 4.20
LEAGUE_MEDIAN_XFIP = 4.05
LEAGUE_AVG_XWOBA   = 0.315
LEAGUE_AVG_K_PCT   = 0.228
HOME_ADVANTAGE   = 0.1
MONTE_CARLO_N    = 10_000
LINEUP_AVAILABLE_HOURS_BEFORE = 4  # hours before game time

# ── Helpers ────────────────────────────────────────────────────────────────────
def safe_float(val, default=None):
    try:
        v = float(val)
        return default if np.isnan(v) else round(v, 4)
    except Exception:
        return default

def safe_col(row, *names, default=None):
    """Try multiple column name variants; return first found."""
    for name in names:
        try:
            v = safe_float(row[name])
            if v is not None:
                return v
        except (KeyError, TypeError):
            pass
    return default

# ── Baseball Savant data layer ─────────────────────────────────────────────────
def _parse_savant_csv(content: bytes) -> pd.DataFrame:
    """Parse a Savant CSV response, handling BOM and the combined 'last_name, first_name' column."""
    df = pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")
    # Savant uses one combined column: 'last_name, first_name' → "Abbott, Andrew"
    combined = next((c for c in df.columns if "last_name" in c.lower()), None)
    if combined:
        parts = df[combined].str.split(", ", n=1, expand=True)
        if parts.shape[1] == 2:
            df["Name"] = parts[1].str.strip() + " " + parts[0].str.strip()
        df = df.drop(columns=[combined])
    if "player_id" in df.columns:
        df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
    return df


async def _savant_pitching(year: int) -> pd.DataFrame:
    """
    Fetch pitcher xERA from Savant expected_statistics leaderboard.
    xERA is a Savant-native ERA estimator on the same scale as xFIP —
    stored as 'xFIP' so the rest of the simulation math is unchanged.
    """
    url = (
        "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=pitcher&year={year}&position=&team=&min=1&csv=true"
    )
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=30,
                headers={"User-Agent": "Mozilla/5.0"},
                follow_redirects=True)
            r.raise_for_status()
        df = _parse_savant_csv(r.content)
        if "xera" in df.columns:
            df["xFIP"] = pd.to_numeric(df["xera"], errors="coerce")
        return df
    except Exception as e:
        logger.error("_savant_pitching error: %s", e)
        return pd.DataFrame()


async def _savant_batting(year: int) -> pd.DataFrame:
    """
    Fetch individual batter est_woba from Savant expected_statistics,
    then join with player→team mapping from MLB Stats API to produce
    a DataFrame with Team and xwOBA columns for _team_stat().
    """
    savant_url = (
        "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=batter&year={year}&position=&team=&min=50&csv=true"
    )
    stats_url = f"{MLB_API}/stats"
    stats_params = dict(stats="season", group="hitting", gameType="R",
                        season=year, playerPool="All", limit=3000,
                        sortStat="plateAppearances")
    teams_url = f"{MLB_API}/teams"
    teams_params = dict(sportId=1, season=year)
    try:
        async with httpx.AsyncClient() as client:
            savant_r, stats_r, teams_r = await asyncio.gather(
                client.get(savant_url, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True),
                client.get(stats_url,  params=stats_params,  timeout=20),
                client.get(teams_url,  params=teams_params,  timeout=20),
            )
        savant_r.raise_for_status()
        df = _parse_savant_csv(savant_r.content)
        if "est_woba" in df.columns:
            df["xwOBA"] = pd.to_numeric(df["est_woba"], errors="coerce")
        if "pa" in df.columns:
            df["PA"] = pd.to_numeric(df["pa"], errors="coerce")
        # Build team_id → normalised abbreviation from /teams
        team_abbr: dict = {}
        if teams_r.status_code == 200:
            for t in teams_r.json().get("teams", []):
                team_abbr[t["id"]] = _norm_team(t.get("abbreviation", ""))
        # Build player_id → team abbreviation from /stats splits
        player_team: dict = {}
        if stats_r.status_code == 200:
            for split in stats_r.json().get("stats", [{}])[0].get("splits", []):
                pid = split.get("player", {}).get("id")
                tid = split.get("team",   {}).get("id")
                abbr = team_abbr.get(tid, "")
                if pid and abbr:
                    player_team[int(pid)] = abbr
        logger.info("_savant_batting: team_abbr=%d, player_team=%d, df=%d rows",
                    len(team_abbr), len(player_team), len(df))
        if player_team and "player_id" in df.columns:
            df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
            df["Team"] = df["player_id"].map(player_team)
            mapped = df["Team"].notna().sum()
            logger.info("_savant_batting: Team column populated for %d/%d rows", mapped, len(df))
        else:
            logger.warning("_savant_batting: no team mapping — team_abbr=%d, player_team=%d",
                           len(team_abbr), len(player_team))
        return df
    except Exception as e:
        logger.error("_savant_batting error: %s", e)
        return pd.DataFrame()


# ── pybaseball data layer ──────────────────────────────────────────────────────
def _load_pitching(year: int):
    key = f"pitching_{year}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    # Savant first — no FanGraphs 403; try prior year if current xFIP all null (early season)
    for yr in (year, year - 1):
        try:
            df = asyncio.run(_savant_pitching(yr))
            if df is not None and not df.empty and "xFIP" in df.columns:
                valid = df["xFIP"].notna().sum()
                if valid >= 20:
                    cache_set(key, df)
                    logger.info("Pitching data loaded from Savant %d (%d rows, %d xFIP)", yr, len(df), valid)
                    return df
                logger.warning("Savant pitching %d: only %d valid xFIP rows, trying prior year", yr, valid)
        except Exception as e:
            logger.warning("Savant pitching %d failed: %s", yr, e)
    # Fallback to pybaseball
    try:
        df = pybaseball.pitching_stats(year, qual=1)
        cache_set(key, df)
        return df
    except Exception as e:
        logger.error("pybaseball pitching_stats error: %s", e)
        return None


def _load_batting(year: int):
    key = f"batting_{year}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    # Savant first — no FanGraphs 403; try prior year if current xwOBA thin
    for yr in (year, year - 1):
        try:
            df = asyncio.run(_savant_batting(yr))
            if df is not None and not df.empty and "xwOBA" in df.columns:
                valid = df["xwOBA"].notna().sum()
                if valid >= 50:
                    cache_set(key, df)
                    logger.info("Batting data loaded from Savant %d (%d rows, %d xwOBA)", yr, len(df), valid)
                    return df
                logger.warning("Savant batting %d: only %d valid xwOBA rows, trying prior year", yr, valid)
        except Exception as e:
            logger.warning("Savant batting %d failed: %s", yr, e)
    # Fallback to pybaseball
    try:
        df = pybaseball.batting_stats(year, qual=1)
        cache_set(key, df)
        return df
    except Exception as e:
        logger.error("pybaseball batting_stats error: %s", e)
        return None

def _find_pitcher_row(df, name: str):
    if df is None or name in (None, "TBD", "") or "Name" not in df.columns:
        return None
    # Exact match
    mask = df["Name"].str.lower() == name.lower()
    if mask.any():
        return df[mask].iloc[0]
    # Last-name match
    last = name.split()[-1].lower()
    mask = df["Name"].str.lower().str.endswith(last)
    if mask.any():
        return df[mask].iloc[0]
    return None

def _team_stat(bat_df, team_abbr: str, col: str, default: float):
    if bat_df is None or "Team" not in bat_df.columns or col not in bat_df.columns:
        return default
    subset = bat_df[bat_df["Team"].str.upper() == team_abbr.upper()]
    if subset.empty:
        return default
    # Weighted by PA when available, otherwise mean
    if "PA" in subset.columns and subset["PA"].sum() > 0:
        vals = subset[col].apply(lambda x: safe_float(x)).dropna()
        pa   = subset.loc[vals.index, "PA"]
        return round((vals * pa).sum() / pa.sum(), 4) if pa.sum() > 0 else default
    vals = subset[col].apply(lambda x: safe_float(x)).dropna()
    return round(vals.mean(), 4) if not vals.empty else default

# ── MLB Stats API ──────────────────────────────────────────────────────────────
MLB_API = "https://statsapi.mlb.com/api/v1"

async def _mlb_get(client: httpx.AsyncClient, path: str, **params) -> dict:
    r = await client.get(f"{MLB_API}{path}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def _parse_schedule(data: dict) -> list:
    games = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            home = g["teams"]["home"]
            away = g["teams"]["away"]

            def pitcher(side):
                pp = side.get("probablePitcher", {})
                return {"id": pp.get("id"), "name": pp.get("fullName", "TBD")} if pp else {"id": None, "name": "TBD"}

            games.append({
                "game_id":        g["gamePk"],
                "home_team":      home["team"]["abbreviation"],
                "away_team":      away["team"]["abbreviation"],
                "home_team_id":   home["team"]["id"],
                "away_team_id":   away["team"]["id"],
                "home_team_name": home["team"]["name"],
                "away_team_name": away["team"]["name"],
                "game_time":      g.get("gameDate", ""),
                "status":         g.get("status", {}).get("detailedState", ""),
                "home_pitcher":   pitcher(home),
                "away_pitcher":   pitcher(away),
            })
    return games

async def _today_games(client: httpx.AsyncClient) -> list:
    today = date.today().strftime("%Y-%m-%d")
    cached = cache_get(f"games_{today}")
    if cached:
        return cached
    data = await _mlb_get(client, "/schedule", sportId=1, date=today, hydrate="probablePitcher,team")
    games = _parse_schedule(data)
    cache_set(f"games_{today}", games)
    return games

# ── Lineup helpers ────────────────────────────────────────────────────────────
async def _fetch_lineups(client: httpx.AsyncClient, game_id: int) -> dict:
    empty = {"home": [], "away": []}
    try:
        data = await _mlb_get(client, f"/game/{game_id}/boxscore")
        teams = data.get("teams", {})
        result = {}
        for side in ("home", "away"):
            side_data = teams.get(side, {})
            batting_order = side_data.get("battingOrder", [])
            players = side_data.get("players", {})
            lineup = []
            for pid in batting_order:
                key = f"ID{pid}"
                p = players.get(key, {})
                person = p.get("person", {})
                full_name = person.get("fullName", "")
                if full_name:
                    lineup.append({"id": pid, "name": full_name})
            result[side] = lineup
        return result if result else empty
    except Exception:
        return empty


async def _lineup_xwoba(client: httpx.AsyncClient, players: list, bat_df) -> float:
    if bat_df is None or not players:
        return LEAGUE_AVG_XWOBA
    matched_xwoba = []
    for player in players:
        name = player.get("name", "")
        if not name:
            continue
        # Exact match
        mask = bat_df["Name"].str.lower() == name.lower()
        if not mask.any():
            # Last-name fallback
            last = name.split()[-1].lower()
            mask = bat_df["Name"].str.lower().str.endswith(last)
        if mask.any():
            row = bat_df[mask].iloc[0]
            val = safe_float(row.get("xwOBA") if hasattr(row, "get") else row["xwOBA"])
            if val is not None:
                matched_xwoba.append(val)
    if len(matched_xwoba) < 5:
        return LEAGUE_AVG_XWOBA
    return round(sum(matched_xwoba) / len(matched_xwoba), 4)


# ── Monte Carlo ────────────────────────────────────────────────────────────────
def _simulate(exp_home: float, exp_away: float, n: int = MONTE_CARLO_N):
    rng = np.random.default_rng()
    h = rng.poisson(max(exp_home, 0.1), n)
    a = rng.poisson(max(exp_away, 0.1), n)
    hw = int(np.sum(h > a))
    aw = int(np.sum(a > h))
    ties = n - hw - aw
    return round((hw + ties * 0.5) / n, 4), round((aw + ties * 0.5) / n, 4)

# ── Pitcher rest days ──────────────────────────────────────────────────────────
async def _rest_days(pitcher_id: Optional[int], year: int) -> Optional[int]:
    if not pitcher_id:
        return None
    try:
        async with httpx.AsyncClient() as client:
            data = await _mlb_get(
                client, f"/people/{pitcher_id}/stats",
                stats="gameLog", group="pitching", season=year,
            )
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        last_date_str = splits[-1].get("date", "")
        if not last_date_str:
            return None
        last = datetime.strptime(last_date_str, "%Y-%m-%d").date()
        return (date.today() - last).days
    except Exception as e:
        logger.warning("Rest-days fetch failed for pitcher %s: %s", pitcher_id, e)
        return None

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 1 — GET /api/games/today
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/games/today")
async def get_games_today():
    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
        return games
    except Exception as e:
        logger.error("get_games_today error: %s", e)
        raise HTTPException(502, f"MLB API error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 2 — GET /api/simulate/{game_id}
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/simulate/{game_id}")
async def simulate_game(game_id: int):
    cache_key = f"sim_{game_id}_{date.today()}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    year = date.today().year

    # Resolve game from today's schedule
    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
    except Exception as e:
        raise HTTPException(502, f"Schedule fetch failed: {e}")

    game = next((g for g in games if g["game_id"] == game_id), None)
    if not game:
        raise HTTPException(404, "Game not found in today's schedule")

    home_team = game["home_team"]
    away_team = game["away_team"]
    home_p_name = game["home_pitcher"]["name"]
    away_p_name = game["away_pitcher"]["name"]

    # Load pybaseball stats (blocking → run in thread pool)
    pitch_df, bat_df = await asyncio.gather(
        asyncio.to_thread(_load_pitching, year),
        asyncio.to_thread(_load_batting, year),
    )

    data_points = 0

    # Pitcher xFIP (home pitcher faces away batters; away pitcher faces home batters)
    home_p_row = _find_pitcher_row(pitch_df, home_p_name)
    away_p_row = _find_pitcher_row(pitch_df, away_p_name)

    home_p_xfip = safe_col(home_p_row, "xFIP", default=LEAGUE_AVG_XFIP) if home_p_row is not None else LEAGUE_AVG_XFIP
    away_p_xfip = safe_col(away_p_row, "xFIP", default=LEAGUE_AVG_XFIP) if away_p_row is not None else LEAGUE_AVG_XFIP
    if home_p_row is not None: data_points += 1
    if away_p_row is not None: data_points += 1

    # Team batting xwOBA
    home_xwoba = _team_stat(bat_df, home_team, "xwOBA", LEAGUE_AVG_XWOBA)
    away_xwoba = _team_stat(bat_df, away_team, "xwOBA", LEAGUE_AVG_XWOBA)
    if home_xwoba != LEAGUE_AVG_XWOBA: data_points += 1
    if away_xwoba != LEAGUE_AVG_XWOBA: data_points += 1

    # Expected runs:
    #   runs scored by home = how hard home bats vs away pitcher, plus home field
    #   runs scored by away = how hard away bats vs home pitcher
    exp_home = round(
        max(LEAGUE_AVG_RUNS * (away_p_xfip / LEAGUE_AVG_XFIP) * (home_xwoba / LEAGUE_AVG_XWOBA) + HOME_ADVANTAGE, 0.5), 2
    )
    exp_away = round(
        max(LEAGUE_AVG_RUNS * (home_p_xfip / LEAGUE_AVG_XFIP) * (away_xwoba / LEAGUE_AVG_XWOBA), 0.5), 2
    )

    home_win_prob, away_win_prob = _simulate(exp_home, exp_away)

    completeness = data_points / 4
    confidence = "high" if completeness >= 0.75 else "medium" if completeness >= 0.5 else "low"

    result = {
        "game_id":        game_id,
        "home_team":      home_team,
        "away_team":      away_team,
        "home_win_prob":  round(home_win_prob, 2),
        "away_win_prob":  round(away_win_prob, 2),
        "exp_runs_home":  exp_home,
        "exp_runs_away":  exp_away,
        "expected_total": round(exp_home + exp_away, 2),
        "confidence":     confidence,
    }
    cache_set(cache_key, result)
    return result

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 3 — GET /api/pitchers/today
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/pitchers/today")
async def get_pitchers_today():
    today_str = date.today().strftime("%Y-%m-%d")
    cache_key  = f"pitchers_{today_str}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    year = date.today().year

    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
    except Exception as e:
        raise HTTPException(502, f"Schedule fetch failed: {e}")

    pitch_df, bat_df = await asyncio.gather(
        asyncio.to_thread(_load_pitching, year),
        asyncio.to_thread(_load_batting, year),
    )

    # Fetch K-prop lines from odds API (best effort)
    odds_k_map: dict = {}
    if ODDS_API_KEY:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
                    params={
                        "apiKey":  ODDS_API_KEY,
                        "regions": "us",
                        "markets": "pitcher_strikeouts",
                    },
                    timeout=10,
                )
                if r.status_code == 200:
                    for event in r.json():
                        for bm in event.get("bookmakers", [])[:1]:
                            for mkt in bm.get("markets", []):
                                if mkt.get("key") == "pitcher_strikeouts":
                                    for outcome in mkt.get("outcomes", []):
                                        odds_k_map[outcome.get("description", "").lower()] = outcome.get("point")
        except Exception as e:
            logger.warning("Odds API skipped: %s", e)

    pitchers = []
    seen: set = set()

    for game in games:
        for side, opp_side in [("home", "away"), ("away", "home")]:
            pitcher = game[f"{side}_pitcher"]
            p_name  = pitcher["name"]
            p_id    = pitcher["id"]
            opp     = game[f"{opp_side}_team"]

            if not p_name or p_name == "TBD" or p_name in seen:
                continue
            seen.add(p_name)

            p_row = _find_pitcher_row(pitch_df, p_name)
            data_available = 0
            total_stats    = 5

            csw_pct    = None
            swstr_pct  = None
            stuff_plus = None
            k9         = None
            hand       = None

            if p_row is not None:
                csw_pct   = safe_col(p_row, "CSW%", "CSW")
                swstr_pct = safe_col(p_row, "SwStr%", "SwStr")
                stuff_plus= safe_col(p_row, "Stuff+", "Stuff")
                k9        = safe_col(p_row, "K/9", "SO9")
                hand_raw  = p_row.get("Throws") if hasattr(p_row, "get") else None
                hand      = str(hand_raw) if hand_raw else None
                data_available += sum(1 for v in [csw_pct, swstr_pct, stuff_plus, k9] if v is not None)

            opp_k_pct = _team_stat(bat_df, opp, "K%", None)
            if opp_k_pct is not None:
                data_available += 1

            # Model K projection
            model_projection = None
            if csw_pct is not None:
                base = (csw_pct / 0.28) * 5.0
                if opp_k_pct is not None:
                    model_projection = round(base * (opp_k_pct / 0.22), 1)
                else:
                    model_projection = round(base, 1)

            k_line   = odds_k_map.get(p_name.lower())
            edge_pct = round(model_projection - k_line, 2) if (model_projection is not None and k_line is not None) else None

            rest_days        = await _rest_days(p_id, year)
            pitch_limit_flag = rest_days is not None and rest_days < 4
            confidence_level = max(1, min(5, round(data_available / total_stats * 5)))

            pitchers.append({
                "name":              p_name,
                "pitcher_id":        p_id,
                "team":              game[f"{side}_team"],
                "opponent":          opp,
                "hand":              hand,
                "k_line":            k_line,
                "model_projection":  model_projection,
                "edge_pct":          edge_pct,
                "csw_pct":           csw_pct,
                "swstr_pct":         swstr_pct,
                "stuff_plus":        stuff_plus,
                "k9":                k9,
                "opp_k_pct":         opp_k_pct,
                "rest_days":         rest_days,
                "pitch_limit_flag":  pitch_limit_flag,
                "confidence_level":  confidence_level,
                "data_incomplete":   p_row is None,
            })

    cache_set(cache_key, pitchers)
    return pitchers

# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT 4 — GET + POST /api/tracker
# ══════════════════════════════════════════════════════════════════════════════
class BetCreate(BaseModel):
    date:         str
    game:         str
    market:       str
    odds_decimal: float
    result:       Optional[str]  = None   # "W" | "L" | "P"
    clv:          Optional[float] = None
    units:        float
    notes:        Optional[str]  = None

@app.get("/api/tracker")
def get_tracker():
    try:
        with get_db() as conn:
            bets = [dict(r) for r in conn.execute("SELECT * FROM bets ORDER BY date DESC").fetchall()]

        wins    = [b for b in bets if b.get("result") == "W"]
        losses  = [b for b in bets if b.get("result") == "L"]
        pushes  = [b for b in bets if b.get("result") == "P"]
        settled = wins + losses + pushes

        total_wagered = sum(b["units"] for b in settled)
        profit = sum(
            b["units"] * (b["odds_decimal"] - 1) if b["result"] == "W" else
            (-b["units"]                          if b["result"] == "L" else 0.0)
            for b in settled
        )

        clv_vals = [b["clv"] for b in bets if b.get("clv") is not None]

        summary = {
            "total_bets":  len(bets),
            "wins":        len(wins),
            "losses":      len(losses),
            "pushes":      len(pushes),
            "total_units": round(sum(b["units"] for b in bets), 2),
            "roi_pct":     round(profit / total_wagered * 100, 2) if total_wagered else None,
            "avg_clv":     round(sum(clv_vals) / len(clv_vals), 4) if clv_vals else None,
        }
        return {"bets": bets, "summary": summary}
    except Exception as e:
        logger.error("GET /api/tracker error: %s", e)
        raise HTTPException(500, str(e))

@app.post("/api/tracker", status_code=201)
def create_bet(bet: BetCreate):
    try:
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO bets (date, game, market, odds_decimal, result, clv, units, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (bet.date, bet.game, bet.market, bet.odds_decimal,
                 bet.result, bet.clv, bet.units, bet.notes),
            )
            conn.commit()
            return dict(conn.execute("SELECT * FROM bets WHERE id = ?", (cur.lastrowid,)).fetchone())
    except Exception as e:
        logger.error("POST /api/tracker error: %s", e)
        raise HTTPException(500, str(e))

# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ==============================================================================
# K-PROP PICKS — shared helpers
# ==============================================================================

# Team abbreviation normalisation: MLB API abbr → training-data abbr
_MLB_TO_INTERNAL = {
    "ARI": "AZ",  "CWS": "CHW", "KCR": "KC",  "SFG": "SF",
    "SDP": "SD",  "TBR": "TB",  "WSN": "WSH", "ATH": "OAK",
}

_PARK_FACTORS = {
    "COL": 112, "CIN": 105, "BOS": 104, "TEX": 103, "PHI": 103,
    "MIL": 102, "LAD": 100, "NYY": 100, "CHC": 100, "ATL":  99,
    "HOU":  99, "DET":  99, "TOR":  98, "SF":   98, "MIN":  98,
    "MIA":  97, "CLE":  97, "STL":  97, "SEA":  97, "SD":   96,
    "WSH":  97, "BAL":  98, "TB":   96, "KC":   98, "LAA":  97,
    "NYM":  97, "PIT":  98, "AZ":   99, "CHW": 100, "OAK":  95,
}

_PARK_NAMES = {
    "COL": "Coors Field",
    "BOS": "Fenway Park",
    "TEX": "Globe Life Field",
    "CIN": "Great American BP",
    "LAD": "Dodger Stadium",
    "NYY": "Yankee Stadium",
    "NYM": "Citi Field",
    "CHC": "Wrigley Field",
    "SF":  "Oracle Park",
    "SEA": "T-Mobile Park",
    "SD":  "Petco Park",
    "OAK": "Oakland Coliseum",
    "MIA": "loanDepot Park",
    "TB":  "Tropicana Field",
    "CLE": "Progressive Field",
    "ATL": "Truist Park",
    "HOU": "Minute Maid Park",
    "MIN": "Target Field",
    "DET": "Comerica Park",
    "STL": "Busch Stadium",
    "PIT": "PNC Park",
    "CWS": "Guaranteed Rate Field",
    "KC":  "Kauffman Stadium",
    "LAA": "Angel Stadium",
    "AZ":  "Chase Field",
    "PHI": "Citizens Bank Park",
    "MIL": "American Family Field",
    "TOR": "Rogers Centre",
    "BAL": "Camden Yards",
    "WSH": "Nationals Park",
    "ATH": "Sutter Health Park",
}

_PITCHER_FEAT_COLS = [
    "k9_L3", "k_pct_L15", "k9_L15", "k9_L30", "k9_season",
    "innings_prev_start", "cumulative_pitches_7d", "pitch_limit_flag",
    "implied_max_batters", "csw_pct_L15", "swstr_pct_L15", "stuff_plus",
]
_OPP_FEAT_COLS = [
    "opp_k_pct_vs_hand", "opp_chase_pct", "opp_contact_pct", "opp_swstr_pct",
]

_TRAINING_CSV = Path(__file__).parent.parent / "data" / "training_data.csv"
_train_df: Optional[pd.DataFrame] = None


def _norm_team(t: str) -> str:
    return _MLB_TO_INTERNAL.get(str(t).upper(), str(t).upper())


def _load_training_df() -> pd.DataFrame:
    global _train_df
    if _train_df is not None:
        return _train_df
    if not _TRAINING_CSV.exists():
        logger.warning("training_data.csv not found at %s", _TRAINING_CSV)
        _train_df = pd.DataFrame()
        return _train_df
    _train_df = pd.read_csv(_TRAINING_CSV)
    logger.info("Training data loaded: %d rows", len(_train_df))
    return _train_df


def _pitcher_baseline(df: pd.DataFrame, pitcher_id: Optional[int], name: str) -> dict:
    """Most-recent rolling stats for this pitcher from training data."""
    if df.empty:
        return {c: None for c in _PITCHER_FEAT_COLS}

    match = pd.DataFrame()
    if pitcher_id:
        match = df[df["pitcher_id"] == pitcher_id]
    if match.empty:
        match = df[df["pitcher_name"].str.lower() == name.lower()]
    if match.empty:
        last = name.split()[-1].lower()
        match = df[df["pitcher_name"].str.lower().str.endswith(last)]

    if match.empty:
        return {c: None for c in _PITCHER_FEAT_COLS}

    row = match.sort_values("date").iloc[-1]
    return {
        col: (None if pd.isna(row.get(col)) else float(row[col]))
        for col in _PITCHER_FEAT_COLS
    }


def _opp_stats(df: pd.DataFrame, opp_abbr: str) -> dict:
    """Average opponent discipline stats across all training rows for this team."""
    if df.empty or not opp_abbr:
        return {}
    rows = df[df["opponent"] == opp_abbr]
    if rows.empty:
        return {}
    out = {}
    for col in _OPP_FEAT_COLS:
        if col in rows.columns:
            val = rows[col].mean()
            out[col] = None if pd.isna(val) else float(round(val, 4))
    return out


def _picks_recommend(edge: float, confidence: int, pitch_limit: bool) -> str:
    if confidence < 3:
        return "SKIP"
    if abs(edge) < 0.8:
        return "SKIP"
    if pitch_limit:
        return "SKIP"
    return "OVER" if edge > 0 else "UNDER"


def _calc_result(actual_ks: float, k_line: float, rec: str) -> str:
    """W/L/P relative to the recommended direction."""
    if rec == "SKIP" or k_line is None:
        return "SKIP"
    if actual_ks == k_line:
        return "P"
    if rec == "OVER":
        return "W" if actual_ks > k_line else "L"
    # UNDER
    return "W" if actual_ks < k_line else "L"


def _picks_summary(picks: list[dict]) -> dict:
    """Compute record, hit_rate, roi_pct, avg_edge from a list of pick dicts."""
    # Only count non-SKIP picks with a settled result
    settled = [
        p for p in picks
        if p.get("recommendation") not in ("SKIP", None)
        and p.get("result") in ("W", "L", "P")
    ]
    wins   = sum(1 for p in settled if p["result"] == "W")
    losses = sum(1 for p in settled if p["result"] == "L")
    pushes = sum(1 for p in settled if p["result"] == "P")
    total  = wins + losses + pushes

    # ROI at standard -110 juice (risking 1 unit per bet)
    profit = wins * (100 / 110) - losses * 1.0
    wagered = wins + losses  # pushes return stake
    roi_pct = round(profit / wagered * 100, 2) if wagered else None

    hit_rate = round(wins / (wins + losses), 3) if (wins + losses) else None

    active_edges = [
        p["edge"] for p in picks
        if p.get("recommendation") not in ("SKIP", None)
        and p.get("edge") is not None
    ]
    avg_edge = round(sum(active_edges) / len(active_edges), 2) if active_edges else None

    record = f"{wins}-{losses}-{pushes}" if total else "0-0-0"

    return {
        "record":   record,
        "hit_rate": hit_rate,
        "roi_pct":  roi_pct,
        "avg_edge": avg_edge,
    }


def _pick_row_to_dict(row) -> dict:
    d = dict(row)
    d["pitch_limit_flag"] = bool(d.get("pitch_limit_flag"))
    return d


# ==============================================================================
# FLOW 1 — POST /api/picks/lines
# ==============================================================================

class LineInput(BaseModel):
    pitcher_name: str
    team:         str
    k_line:       float


@app.post("/api/picks/lines", status_code=201)
async def submit_pick_line(body: LineInput):
    today      = date.today().isoformat()
    team_norm  = _norm_team(body.team)
    opponent   = None
    home_away  = 0
    park_factor = 100
    pitcher_id  = None

    # Resolve opponent + home/away from today's schedule
    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
        for g in games:
            home_norm = _norm_team(g["home_team"])
            away_norm = _norm_team(g["away_team"])
            if home_norm == team_norm:
                opponent    = away_norm
                home_away   = 1
                park_factor = _PARK_FACTORS.get(home_norm, 100)
                # Also grab pitcher_id if this pitcher is the home starter
                if g["home_pitcher"]["name"].lower() == body.pitcher_name.lower():
                    pitcher_id = g["home_pitcher"]["id"]
                break
            elif away_norm == team_norm:
                opponent    = home_norm
                home_away   = 0
                park_factor = _PARK_FACTORS.get(home_norm, 100)
                if g["away_pitcher"]["name"].lower() == body.pitcher_name.lower():
                    pitcher_id = g["away_pitcher"]["id"]
                break
    except Exception as e:
        logger.warning("Schedule lookup failed for %s: %s", body.team, e)

    # Load training data + build feature dict
    df    = await asyncio.to_thread(_load_training_df)
    feats = _pitcher_baseline(df, pitcher_id, body.pitcher_name)
    feats.update(_opp_stats(df, opponent or ""))
    feats["k_line"]      = body.k_line
    feats["home_away"]   = home_away
    feats["park_factor"] = park_factor
    feats["month"]       = date.today().month

    # Run ML model
    try:
        result = await asyncio.to_thread(predict_strikeouts, feats)
    except Exception as e:
        logger.error("predict_strikeouts failed for %s: %s", body.pitcher_name, e)
        raise HTTPException(500, f"Model error: {e}")

    proj  = result["model_projection"]
    edge  = round(proj - body.k_line, 2)
    plf   = bool(result["pitch_limit_flag"])
    conf  = result["confidence"]
    rec   = _picks_recommend(edge, conf, plf)

    # Upsert into picks table (preserve actual_ks/result if already settled)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO picks
                (date, pitcher_name, team, opponent, pitcher_id, k_line, model_proj, edge,
                 ci_low, ci_high, pitch_limit_flag, confidence, recommendation,
                 actual_ks, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            ON CONFLICT(date, pitcher_name) DO UPDATE SET
                team             = excluded.team,
                opponent         = excluded.opponent,
                pitcher_id       = excluded.pitcher_id,
                k_line           = excluded.k_line,
                model_proj       = excluded.model_proj,
                edge             = excluded.edge,
                ci_low           = excluded.ci_low,
                ci_high          = excluded.ci_high,
                pitch_limit_flag = excluded.pitch_limit_flag,
                confidence       = excluded.confidence,
                recommendation   = excluded.recommendation
        """, (
            today, body.pitcher_name, body.team, opponent or "",
            pitcher_id,
            body.k_line, proj, edge,
            result["ci_low"], result["ci_high"],
            int(plf), conf, rec,
        ))
        conn.commit()

    return {
        "date":             today,
        "pitcher_name":     body.pitcher_name,
        "team":             body.team,
        "opponent":         opponent or "",
        "pitcher_id":       pitcher_id,
        "k_line":           body.k_line,
        "model_proj":       proj,
        "edge":             edge,
        "ci_low":           result["ci_low"],
        "ci_high":          result["ci_high"],
        "pitch_limit_flag": plf,
        "confidence":       conf,
        "recommendation":   rec,
    }


# ==============================================================================
# FLOW 1 — GET /api/picks/today
# ==============================================================================

@app.get("/api/picks/today")
def get_picks_today():
    today = date.today().isoformat()
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM picks WHERE date = ? ORDER BY recommendation, edge DESC",
                (today,),
            ).fetchall()
        return [_pick_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.error("GET /api/picks/today error: %s", e)
        raise HTTPException(500, str(e))


# ==============================================================================
# FLOW 2 — POST /api/picks/result
# ==============================================================================

class ResultInput(BaseModel):
    date:         str
    pitcher_name: str
    actual_ks:    float


@app.post("/api/picks/result")
def submit_pick_result(body: ResultInput):
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM picks WHERE date = ? AND pitcher_name = ?",
                (body.date, body.pitcher_name),
            ).fetchone()

            if not row:
                raise HTTPException(
                    404,
                    f"No pick found for {body.pitcher_name} on {body.date}",
                )

            pick = dict(row)
            result = _calc_result(body.actual_ks, pick["k_line"], pick["recommendation"])

            conn.execute(
                "UPDATE picks SET actual_ks = ?, result = ? WHERE date = ? AND pitcher_name = ?",
                (body.actual_ks, result, body.date, body.pitcher_name),
            )
            conn.commit()

        return {
            "date":         body.date,
            "pitcher_name": body.pitcher_name,
            "actual_ks":    body.actual_ks,
            "k_line":       pick["k_line"],
            "recommendation": pick["recommendation"],
            "result":       result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("POST /api/picks/result error: %s", e)
        raise HTTPException(500, str(e))


# ==============================================================================
# DELETE /api/picks/cancel — undo a pick (pitcher scratched / injured)
# ==============================================================================

class CancelInput(BaseModel):
    date: str
    pitcher_name: str

@app.post("/api/picks/cancel")
def cancel_pick(body: CancelInput):
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT result FROM picks WHERE date = ? AND pitcher_name = ?",
                (body.date, body.pitcher_name),
            ).fetchone()
            if not row:
                raise HTTPException(404, f"No pick found for {body.pitcher_name} on {body.date}")
            if row["result"] and row["result"] not in ("SKIP", None):
                raise HTTPException(400, "Cannot cancel a settled pick (result already logged)")
            conn.execute(
                "DELETE FROM picks WHERE date = ? AND pitcher_name = ?",
                (body.date, body.pitcher_name),
            )
            conn.commit()
        return {"ok": True, "pitcher_name": body.pitcher_name}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("POST /api/picks/cancel error: %s", e)
        raise HTTPException(500, str(e))


# ==============================================================================
# FLOW 2 — GET /api/picks/history
# ==============================================================================

@app.get("/api/picks/history")
def get_picks_history():
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM picks ORDER BY date DESC, id DESC"
            ).fetchall()
        picks = [_pick_row_to_dict(r) for r in rows]
        summary = _picks_summary(picks)
        return {"picks": picks, "summary": summary}
    except Exception as e:
        logger.error("GET /api/picks/history error: %s", e)
        raise HTTPException(500, str(e))


# ==============================================================================
# FLOW 3 — GET /api/stats/summary
# ==============================================================================

@app.get("/api/stats/summary")
def get_stats_summary():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM picks ORDER BY date DESC").fetchall()
        picks = [_pick_row_to_dict(r) for r in rows]

        # Only non-SKIP picks for the record
        actionable = [
            p for p in picks if p.get("recommendation") not in ("SKIP", None)
        ]
        settled = [p for p in actionable if p.get("result") in ("W", "L", "P")]
        wins    = sum(1 for p in settled if p["result"] == "W")
        losses  = sum(1 for p in settled if p["result"] == "L")
        pushes  = sum(1 for p in settled if p["result"] == "P")

        wagered = wins + losses
        profit  = wins * (100 / 110) - losses * 1.0
        roi_pct = round(profit / wagered * 100, 2) if wagered else None
        hit_rate = round(wins / wagered, 3) if wagered else None

        active_edges = [
            p["edge"] for p in actionable if p.get("edge") is not None
        ]
        avg_edge = round(sum(active_edges) / len(active_edges), 2) if active_edges else None

        # Hit rate per confidence level (settled picks only)
        hit_rate_by_confidence: dict = {}
        best_conf = None
        best_conf_rate = -1.0
        for conf in [3, 4, 5]:
            group = [
                p for p in settled
                if p.get("confidence") == conf and p["result"] in ("W", "L")
            ]
            if group:
                g_wins = sum(1 for p in group if p["result"] == "W")
                rate   = round(g_wins / len(group), 3)
                hit_rate_by_confidence[str(conf)] = rate
                if rate > best_conf_rate:
                    best_conf_rate = rate
                    best_conf = conf

        return {
            "total_picks":             len(actionable),
            "wins":                    wins,
            "losses":                  losses,
            "pushes":                  pushes,
            "hit_rate":                hit_rate,
            "roi_pct":                 roi_pct,
            "avg_edge":                avg_edge,
            "best_confidence_level":   best_conf,
            "hit_rate_by_confidence":  hit_rate_by_confidence,
        }
    except Exception as e:
        logger.error("GET /api/stats/summary error: %s", e)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT — POST /api/model/retrain
# ══════════════════════════════════════════════════════════════════════════════

import subprocess
import threading

_retrain_status: dict = {"running": False, "last": None, "error": None}


def _run_retrain():
    global _retrain_status
    _retrain_status["running"] = True
    _retrain_status["error"]   = None
    try:
        root = Path(__file__).parent.parent
        # Step 1: fetch latest data (2022–2026)
        logger.info("Retrain: running data_pipeline.py")
        r1 = subprocess.run(
            ["python", "data_pipeline.py"],
            cwd=str(root), capture_output=True, text=True, timeout=3600
        )
        if r1.returncode != 0:
            raise RuntimeError(f"data_pipeline failed:\n{r1.stderr[-2000:]}")

        # Step 2: retrain model
        logger.info("Retrain: running train_model.py")
        r2 = subprocess.run(
            ["python", "train_model.py"],
            cwd=str(root), capture_output=True, text=True, timeout=1800
        )
        if r2.returncode != 0:
            raise RuntimeError(f"train_model failed:\n{r2.stderr[-2000:]}")

        # Step 3: reload model in memory
        from predict import _reload_model  # noqa
        _reload_model()

        _retrain_status["last"]    = datetime.now().strftime("%Y-%m-%d %H:%M")
        _retrain_status["running"] = False
        logger.info("Retrain complete")

    except Exception as exc:
        _retrain_status["error"]   = str(exc)
        _retrain_status["running"] = False
        logger.error("Retrain failed: %s", exc)


@app.post("/api/model/retrain")
def trigger_retrain():
    if _retrain_status["running"]:
        raise HTTPException(409, "Retrain already in progress")
    t = threading.Thread(target=_run_retrain, daemon=True)
    t.start()
    return {"status": "started", "message": "Pipeline corriendo en background. Puede tardar 10-30 min."}


@app.get("/api/model/retrain/status")
def retrain_status():
    return {
        "running":   _retrain_status["running"],
        "last":      _retrain_status["last"],
        "error":     _retrain_status["error"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# ENDPOINT — POST /api/simulation/ou
# Over/Under Monte Carlo simulator (Matchup Tool)
# ══════════════════════════════════════════════════════════════════════════════

def _pitcher_hand(p_row) -> str:
    """Return 'L' or 'R' from a pybaseball pitcher row; default 'R'."""
    if p_row is None:
        return "R"
    for col in ("Throws", "Hand", "PitcherHand"):
        try:
            v = p_row[col]
            if v in ("L", "R"):
                return v
            if isinstance(v, str) and v.upper() in ("L", "R"):
                return v.upper()
        except (KeyError, TypeError):
            pass
    return "R"


def _k_pct_vs_hand(train_df: pd.DataFrame, bat_df, team: str, hand: str) -> float:
    """
    Return the opponent K% vs the given pitcher hand from training data (2024+).
    Falls back to pybaseball team K% if training data is missing or thin.
    """
    fallback = _team_stat(bat_df, team, "K%", LEAGUE_AVG_K_PCT)
    if train_df is None or train_df.empty:
        return fallback
    if "opp_k_pct_vs_hand" not in train_df.columns or "opponent" not in train_df.columns:
        return fallback

    subset = train_df[
        (train_df["opponent"].str.upper() == team.upper()) &
        (train_df.get("season", train_df.get("date", pd.Series(dtype=str)))
         .astype(str).str[:4] >= "2024")
    ]
    if subset.empty:
        # Widen to all years
        subset = train_df[train_df["opponent"].str.upper() == team.upper()]
    if subset.empty:
        return fallback

    vals = subset["opp_k_pct_vs_hand"].apply(safe_float).dropna()
    if vals.empty:
        return fallback
    return round(float(vals.mean()), 4)


def _k_adj(k_pct: float) -> float:
    """Clamp K% adjustment factor to ±25%."""
    raw = 1.0 - (k_pct - LEAGUE_AVG_K_PCT) * 2.5
    return max(0.75, min(1.25, raw))


class OUSimRequest(BaseModel):
    game_id: int
    ou_line: float


@app.post("/api/simulation/ou")
async def simulate_ou(req: OUSimRequest):
    game_id = req.game_id
    ou_line = req.ou_line

    year = date.today().year

    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
    except Exception as e:
        raise HTTPException(502, f"Schedule fetch failed: {e}")

    game = next((g for g in games if g["game_id"] == game_id), None)
    if not game:
        raise HTTPException(404, "Game not found in today's schedule")

    home_team   = game["home_team"]
    away_team   = game["away_team"]

    park_factor = _PARK_FACTORS.get(home_team, 100)
    park_adj    = park_factor / 100.0
    park_name   = _PARK_NAMES.get(home_team, f"{home_team} Park")

    home_p_name = game["home_pitcher"]["name"]
    away_p_name = game["away_pitcher"]["name"]

    pitch_df, bat_df, train_df = await asyncio.gather(
        asyncio.to_thread(_load_pitching, year),
        asyncio.to_thread(_load_batting, year),
        asyncio.to_thread(_load_training_df),
    )

    # Fetch lineups and batting data concurrently
    async with httpx.AsyncClient() as client:
        lineup_data = await _fetch_lineups(client, game_id)

    lineup_confirmed = len(lineup_data.get("home", [])) >= 7

    if lineup_confirmed:
        async with httpx.AsyncClient() as client:
            home_xwoba, away_xwoba = await asyncio.gather(
                _lineup_xwoba(client, lineup_data["home"], bat_df),
                _lineup_xwoba(client, lineup_data["away"], bat_df),
            )
    else:
        home_xwoba = _team_stat(bat_df, home_team, "xwOBA", LEAGUE_AVG_XWOBA)
        away_xwoba = _team_stat(bat_df, away_team, "xwOBA", LEAGUE_AVG_XWOBA)

    confirmed_tag = "confirmed" if lineup_confirmed else "aggregate"
    cache_key = f"ou_sim_{game_id}_{ou_line}_{park_factor}_{confirmed_tag}_{date.today()}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    home_p_row = _find_pitcher_row(pitch_df, home_p_name)
    away_p_row = _find_pitcher_row(pitch_df, away_p_name)

    home_p_xfip = safe_col(home_p_row, "xFIP", default=LEAGUE_AVG_XFIP) if home_p_row is not None else LEAGUE_AVG_XFIP
    away_p_xfip = safe_col(away_p_row, "xFIP", default=LEAGUE_AVG_XFIP) if away_p_row is not None else LEAGUE_AVG_XFIP

    # Clamp xFIP to realistic range (small sample protection)
    XFIP_MIN, XFIP_MAX = 2.8, 5.5
    if home_p_xfip > XFIP_MAX or home_p_xfip < XFIP_MIN:
        logger.warning("Clamping home xFIP %.2f → [%.1f, %.1f] for %s",
                       home_p_xfip, XFIP_MIN, XFIP_MAX, home_p_name)
        home_p_xfip = max(XFIP_MIN, min(XFIP_MAX, home_p_xfip))
    if away_p_xfip > XFIP_MAX or away_p_xfip < XFIP_MIN:
        logger.warning("Clamping away xFIP %.2f → [%.1f, %.1f] for %s",
                       away_p_xfip, XFIP_MIN, XFIP_MAX, away_p_name)
        away_p_xfip = max(XFIP_MIN, min(XFIP_MAX, away_p_xfip))

    # Small-sample blending toward league median (< 20 IP → partial regression)
    home_ip = safe_col(home_p_row, "IP", "ip", default=0) if home_p_row is not None else 0
    if home_ip is None or home_ip < 20:
        blend        = (home_ip or 0) / 20.0
        home_p_xfip  = blend * home_p_xfip + (1 - blend) * LEAGUE_MEDIAN_XFIP

    away_ip = safe_col(away_p_row, "IP", "ip", default=0) if away_p_row is not None else 0
    if away_ip is None or away_ip < 20:
        blend        = (away_ip or 0) / 20.0
        away_p_xfip  = blend * away_p_xfip + (1 - blend) * LEAGUE_MEDIAN_XFIP

    # L/R splits — pitcher hand drives which K% bucket to use
    home_p_hand = _pitcher_hand(home_p_row)
    away_p_hand = _pitcher_hand(away_p_row)

    # home team bats vs away pitcher; away team bats vs home pitcher
    home_k_pct = _k_pct_vs_hand(train_df, bat_df, home_team, away_p_hand)
    away_k_pct = _k_pct_vs_hand(train_df, bat_df, away_team, home_p_hand)

    home_k_adj = _k_adj(home_k_pct)
    away_k_adj = _k_adj(away_k_pct)

    data_points = sum([
        home_p_row is not None,
        away_p_row is not None,
        lineup_confirmed or home_xwoba != LEAGUE_AVG_XWOBA,
        lineup_confirmed or away_xwoba != LEAGUE_AVG_XWOBA,
    ])

    exp_home = round(
        max(
            LEAGUE_AVG_RUNS * (away_p_xfip / LEAGUE_AVG_XFIP)
            * (home_xwoba / LEAGUE_AVG_XWOBA) * home_k_adj * park_adj
            + HOME_ADVANTAGE,
            0.5,
        ), 2
    )
    exp_away = round(
        max(
            LEAGUE_AVG_RUNS * (home_p_xfip / LEAGUE_AVG_XFIP)
            * (away_xwoba / LEAGUE_AVG_XWOBA) * away_k_adj * park_adj,
            0.5,
        ), 2
    )

    # Monte Carlo — 10,000 games
    rng        = np.random.default_rng()
    home_runs  = rng.poisson(max(exp_home, 0.1), 10_000)
    away_runs  = rng.poisson(max(exp_away, 0.1), 10_000)
    totals     = home_runs + away_runs

    avg_total  = round(float(np.mean(totals)), 2)
    over_prob  = round(float(np.mean(totals > ou_line)), 4)
    under_prob = round(1.0 - over_prob, 4)
    edge_pct   = round(over_prob - 0.5, 4)

    # Histogram — 20 equal-width buckets over the realistic range
    t_min, t_max = int(np.percentile(totals, 1)), int(np.percentile(totals, 99))
    t_min = max(t_min, 0)
    t_max = max(t_max, t_min + 1)
    bucket_size = max(1, (t_max - t_min) // 20)

    histogram = []
    lo = t_min
    while lo < t_max and len(histogram) < 20:
        hi   = lo + bucket_size
        cnt  = int(np.sum((totals >= lo) & (totals < hi)))
        histogram.append({"bucket": f"{lo}-{hi - 1}", "lo": lo, "hi": hi - 1, "count": cnt})
        lo = hi
    # Catch any overflow in the last bucket
    if histogram:
        histogram[-1]["count"] += int(np.sum(totals >= lo))

    confidence = "high" if data_points >= 3 else "medium" if data_points >= 2 else "low"

    result = {
        "game_id":             game_id,
        "home_team":           home_team,
        "away_team":           away_team,
        "home_pitcher":        home_p_name,
        "away_pitcher":        away_p_name,
        "exp_runs_home":       exp_home,
        "exp_runs_away":       exp_away,
        "avg_total":           avg_total,
        "over_prob":           over_prob,
        "under_prob":          under_prob,
        "edge_pct":            edge_pct,
        "ou_line":             ou_line,
        "histogram":           histogram,
        "confidence":          confidence,
        "home_pitcher_hand":   home_p_hand,
        "away_pitcher_hand":   away_p_hand,
        "home_k_pct_vs_hand":  home_k_pct,
        "away_k_pct_vs_hand":  away_k_pct,
        "park_factor":         park_factor,
        "park_name":           park_name,
        "home_p_xfip":         home_p_xfip,
        "away_p_xfip":         away_p_xfip,
        "lineup_confirmed":    lineup_confirmed,
        "home_lineup":         lineup_data.get("home", []) if lineup_confirmed else [],
        "away_lineup":         lineup_data.get("away", []) if lineup_confirmed else [],
    }
    cache_set(cache_key, result)
    return result


@app.get("/api/simulation/debug/{game_id}")
async def debug_simulation(game_id: int):
    year = date.today().year
    async with httpx.AsyncClient() as client:
        games = await _today_games(client)
    game = next((g for g in games if g["game_id"] == game_id), None)
    if not game:
        raise HTTPException(404, "Game not found")

    pitch_df, bat_df = await asyncio.gather(
        asyncio.to_thread(_load_pitching, year),
        asyncio.to_thread(_load_batting, year),
    )

    home_p_row = _find_pitcher_row(pitch_df, game["home_pitcher"]["name"])
    away_p_row = _find_pitcher_row(pitch_df, game["away_pitcher"]["name"])

    return {
        "home_pitcher":  game["home_pitcher"]["name"],
        "away_pitcher":  game["away_pitcher"]["name"],
        "home_p_found":  home_p_row is not None,
        "away_p_found":  away_p_row is not None,
        "home_p_xfip":   safe_col(home_p_row, "xFIP") if home_p_row is not None else None,
        "away_p_xfip":   safe_col(away_p_row, "xFIP") if away_p_row is not None else None,
        "home_xwoba":    _team_stat(bat_df, game["home_team"], "xwOBA", LEAGUE_AVG_XWOBA),
        "away_xwoba":    _team_stat(bat_df, game["away_team"], "xwOBA", LEAGUE_AVG_XWOBA),
    }


@app.post("/api/simulation/clear-cache")
def clear_sim_cache():
    keys_to_delete = [k for k in _cache if k.startswith("ou_sim_")]
    for k in keys_to_delete:
        del _cache[k]
    return {"cleared": len(keys_to_delete)}
