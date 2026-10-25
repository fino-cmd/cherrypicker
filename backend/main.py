import asyncio
import io
import json
import logging
import math

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False
    logging.getLogger(__name__).warning("apscheduler not installed — weekly retrain scheduler disabled")
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

# Tanda 1 — multi-book odds ingestion. Optional (graceful no-op when no API key).
try:
    import odds_api_client as _oac
except ImportError:
    _oac = None  # client missing → fall back to manual odds entry only

# ── Strikeout prediction model (root-level module) ────────────────────────────
warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
sys.path.insert(0, str(Path(__file__).parent.parent))
from predict import predict_strikeouts  # noqa: E402

# ── Config ─────────────────────────────────────────────────────────────────────
# DB_PATH: always resolve to an absolute path anchored at this file's directory,
# so the app opens the same tracker.db regardless of the working directory
# uvicorn was launched from. Prevents the "two DB" split-brain bug (Apr 2026).
_DEFAULT_DB = str(Path(__file__).parent / "tracker.db")
DB_PATH       = os.getenv("DB_PATH") or _DEFAULT_DB
if not os.path.isabs(DB_PATH):
    DB_PATH = str(Path(DB_PATH).resolve())
ODDS_API_KEY  = os.getenv("ODDS_API_KEY", "")
CACHE_TTL     = int(os.getenv("CACHE_TTL_HOURS", "24")) * 3600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

pybaseball.cache.enable()

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="CherryPick MLB API", version="0.1.0")

# ── Scheduler ─────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="America/Argentina/Buenos_Aires") if _APSCHEDULER_AVAILABLE else None

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Simple in-memory cache ─────────────────────────────────────────────────────
_cache: dict = {}
_gamelogs_cache: dict = {}  # {pitcher_id: {"rest_days": int|None, "recent_k_rate": float|None}}

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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS matchup_picks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                date          TEXT    NOT NULL,
                game_id       INTEGER NOT NULL,
                home_team     TEXT,
                away_team     TEXT,
                home_pitcher  TEXT,
                away_pitcher  TEXT,
                ou_line       REAL,
                direction     TEXT,
                edge_pct      REAL,
                avg_total     REAL,
                confidence    TEXT,
                actual_total  REAL,
                result        TEXT,
                UNIQUE(date, game_id)
            )
        """)
        # Migrate: add pitcher_id column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE picks ADD COLUMN pitcher_id INTEGER")
        except Exception:
            pass
        # Migrate: odds + casino for enhanced view (added 2026-04)
        for col_sql in (
            "ALTER TABLE picks ADD COLUMN odds_decimal REAL",
            "ALTER TABLE picks ADD COLUMN casino TEXT",
        ):
            try:
                conn.execute(col_sql)
            except Exception:
                pass
        # Migrate: calibrated probability (Bundle A #2, added 2026-04)
        try:
            conn.execute("ALTER TABLE picks ADD COLUMN p_over REAL")
        except Exception:
            pass
        # Migrate: EV + Kelly sizing (Bundle A #3, added 2026-04)
        for col_sql in (
            "ALTER TABLE picks ADD COLUMN ev_pct REAL",
            "ALTER TABLE picks ADD COLUMN kelly_units REAL",
        ):
            try:
                conn.execute(col_sql)
            except Exception:
                pass
        # Migrate: auto-P&L in pesos (added 2026-04). Stake is derived from
        # kelly_units × unit_size at pick-submission time; pnl is derived from
        # stake × (odds-1) on W, -stake on L, 0 on P at result-log time.
        for col_sql in (
            "ALTER TABLE picks ADD COLUMN stake_pesos REAL",
            "ALTER TABLE picks ADD COLUMN pnl_pesos   REAL",
        ):
            try:
                conn.execute(col_sql)
            except Exception:
                pass
        # App-wide settings (key-value store). Used for bankroll config.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Tanda 1 — multi-book odds ingestion from The Odds API.
        # Per-pick market columns (added 2026-05).
        # ─ p_over_market    : weighted-mean fair P(OVER) across N books, de-vigged
        # ─ p_under_market   : 1 - p_over_market (stored for fast UI display)
        # ─ ev_market_pct    : EV computed against market consensus (vs ev_pct
        #                      which is computed against model probability).
        # ─ n_books_market   : how many books contributed to the consensus
        # ─ best_book_casino : book with highest odds for the recommended side
        # ─ best_book_odds   : that book's price (auto-populated into casino/odds_decimal)
        # ─ market_event_id  : The Odds API's event UUID, used to refresh later
        for col_sql in (
            "ALTER TABLE picks ADD COLUMN p_over_market      REAL",
            "ALTER TABLE picks ADD COLUMN p_under_market     REAL",
            "ALTER TABLE picks ADD COLUMN ev_market_pct      REAL",
            "ALTER TABLE picks ADD COLUMN n_books_market     INTEGER",
            # best_book_* = the user's best executable price (from user_books filter)
            "ALTER TABLE picks ADD COLUMN best_book_casino   TEXT",
            "ALTER TABLE picks ADD COLUMN best_book_odds     REAL",
            # market_best_* = the highest price across THE ENTIRE market (info only,
            # surfaces "what could you have gotten" if you had access elsewhere)
            "ALTER TABLE picks ADD COLUMN market_best_casino TEXT",
            "ALTER TABLE picks ADD COLUMN market_best_odds   REAL",
            "ALTER TABLE picks ADD COLUMN market_event_id    TEXT",
        ):
            try:
                conn.execute(col_sql)
            except Exception:
                pass
        # Per-book odds snapshots — historical record for CLV tracking (Tanda 2)
        # and audit trail of what each book was offering at submit-time.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pick_odds_snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                pick_id       INTEGER,
                pitcher_name  TEXT NOT NULL,
                date          TEXT NOT NULL,
                captured_at   TEXT NOT NULL,
                casino_key    TEXT NOT NULL,
                casino        TEXT NOT NULL,
                point         REAL,
                over_odds     REAL,
                under_odds    REAL,
                vig           REAL,
                p_over_fair   REAL,
                weight        REAL,
                FOREIGN KEY (pick_id) REFERENCES picks(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pick_odds_snapshots_pick
                ON pick_odds_snapshots(pick_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pick_odds_snapshots_date
                ON pick_odds_snapshots(date)
        """)
        # Migrate: weather columns on matchup_picks (added 2026-04)
        for col_sql in (
            "ALTER TABLE matchup_picks ADD COLUMN weather_factor REAL",
            "ALTER TABLE matchup_picks ADD COLUMN weather_temp_f REAL",
            "ALTER TABLE matchup_picks ADD COLUMN weather_wind_mph REAL",
            "ALTER TABLE matchup_picks ADD COLUMN weather_wind_label TEXT",
            "ALTER TABLE matchup_picks ADD COLUMN weather_roof TEXT",
        ):
            try:
                conn.execute(col_sql)
            except Exception:
                pass
        conn.commit()

        # Load Platt + Isotonic calibration from disk. Must happen BEFORE the
        # p_over recompute below — otherwise the first boot after a fit would
        # still write raw (uncalibrated) probabilities to the DB.
        try:
            _load_platt()
            _load_isotonic()
            if _PLATT_PARAMS is None:
                auto_params = _fit_platt_from_db(conn)
                if auto_params is not None:
                    _save_platt(auto_params)
                    logger.info(
                        "Platt auto-fitted at startup: A=%+.4f B=%+.4f (n=%d, Brier %.4f → %.4f)",
                        auto_params["A"], auto_params["B"], auto_params["n_samples"],
                        auto_params["brier_before"], auto_params["brier_after"],
                    )
            if _ISOTONIC_PARAMS is None:
                auto_iso = _fit_isotonic_from_db(conn)
                if auto_iso is not None:
                    _save_isotonic(auto_iso)
                    logger.info(
                        "Isotonic auto-fitted at startup: %d breakpoints (n=%d, Brier %.4f → %.4f)",
                        len(auto_iso["breakpoints"]), auto_iso["n_samples"],
                        auto_iso["brier_before"], auto_iso["brier_after"],
                    )
            # Pick active method based on setting + which fits exist.
            _refresh_active_calibration(conn)
        except Exception as e:
            logger.warning("Calibration startup init failed: %s", e)

        # Recompute p_over for ALL picks where source data is available.
        # Idempotent; protects against stale values from earlier calibrations
        # of _p_over_ks (e.g., the Poisson-floor fix of Apr-2026 which replaced
        # the degenerate tree-ensemble std with a physically-meaningful σ).
        try:
            rows = conn.execute("""
                SELECT id, model_proj, ci_high, k_line
                  FROM picks
                 WHERE model_proj IS NOT NULL
                   AND ci_high    IS NOT NULL
                   AND k_line     IS NOT NULL
            """).fetchall()
            touched = 0
            for r in rows:
                _std = r["ci_high"] - r["model_proj"] if r["ci_high"] is not None and r["model_proj"] is not None else None
                _p   = _p_over_ks(r["model_proj"], _std, r["k_line"])
                if _p is not None:
                    conn.execute("UPDATE picks SET p_over = ? WHERE id = ?", (_p, r["id"]))
                    touched += 1
            if touched:
                conn.commit()
                logger.info("Recomputed p_over on %d picks (Poisson-floor σ)", touched)
        except Exception as e:
            logger.warning("p_over recompute failed: %s", e)

        # Auto-P&L backfill. For every actionable pick, derive (in order):
        #   kelly_units = Kelly(p_bet, odds)  when NULL but p_over + odds exist
        #   ev_pct      = EV(p_bet, odds)     same guard
        #   stake_pesos = kelly_fraction × bankroll (capped)   ← recomputed ALWAYS
        #   pnl_pesos   = stake × (odds-1)/-stake/0            ← recomputed ALWAYS
        #
        # Stake and P&L are NOT idempotent: a bankroll change must propagate
        # to historical picks, so we overwrite those every boot.
        try:
            bankroll  = _bankroll_pesos(conn)
            unit      = _unit_size_pesos(conn)
            max_frac  = _max_stake_fraction(conn)
            rows = conn.execute("""
                SELECT id, kelly_units, ev_pct, odds_decimal, result, recommendation, p_over,
                       stake_pesos, pnl_pesos
                  FROM picks
                 WHERE recommendation IN ('OVER','UNDER')
            """).fetchall()
            kelly_touched = 0
            stake_touched = 0
            pnl_touched   = 0
            for r in rows:
                kelly = r["kelly_units"]
                evp   = r["ev_pct"]
                # Derive kelly+ev from p_over + odds if missing (picks pre-Bundle A #3)
                if (kelly is None or evp is None) and r["p_over"] is not None and r["odds_decimal"]:
                    p_bet = float(r["p_over"]) if r["recommendation"] == "OVER" else (1.0 - float(r["p_over"]))
                    if evp is None:
                        evp = _ev_pct(p_bet, r["odds_decimal"])
                        if evp is not None:
                            conn.execute("UPDATE picks SET ev_pct = ? WHERE id = ?", (evp, r["id"]))
                    if kelly is None:
                        kelly = _kelly_fraction(p_bet, r["odds_decimal"])
                        if kelly is not None:
                            conn.execute("UPDATE picks SET kelly_units = ? WHERE id = ?", (kelly, r["id"]))
                            kelly_touched += 1

                # Stake ALWAYS recomputed so bankroll edits propagate across history.
                new_stake = _stake_pesos(kelly, bankroll, max_frac) if kelly is not None else None
                if new_stake != r["stake_pesos"]:
                    conn.execute("UPDATE picks SET stake_pesos = ? WHERE id = ?", (new_stake, r["id"]))
                    stake_touched += 1

                # PnL follows stake; recompute when stake changes OR pnl is stale.
                if r["result"] in ("W","L","P") and new_stake is not None:
                    new_pnl = _pnl_pesos(new_stake, r["odds_decimal"], r["result"])
                    if new_pnl != r["pnl_pesos"]:
                        conn.execute("UPDATE picks SET pnl_pesos = ? WHERE id = ?", (new_pnl, r["id"]))
                        pnl_touched += 1
            if kelly_touched or stake_touched or pnl_touched:
                conn.commit()
                logger.info("Auto-P&L backfill: kelly=%d, stake=%d, pnl=%d (bankroll=$%.0f, unit=$%.0f, cap=%.1f%%)",
                            kelly_touched, stake_touched, pnl_touched, bankroll, unit, max_frac*100)
        except Exception as e:
            logger.warning("Auto-P&L backfill failed: %s", e)

async def _run_retrain_job():
    logger.info("Scheduled weekly retrain starting...")
    try:
        await asyncio.to_thread(_run_retrain)
        logger.info("Scheduled weekly retrain completed OK")
    except Exception as e:
        logger.error("Scheduled weekly retrain failed: %s", e)


async def _warmup_gamelogs(year: int):
    """Pre-fetch gamelogs (rest days + recent K/9) for today's pitchers."""
    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
        pitcher_ids = []
        for g in games:
            for side in ("home_pitcher", "away_pitcher"):
                pid = g.get(side, {}).get("id")
                if pid and pid not in pitcher_ids:
                    pitcher_ids.append(pid)
        if not pitcher_ids:
            logger.info("Gamelogs warmup: no pitchers found today")
            return
        results = await asyncio.gather(*[_pitcher_gamelogs(pid, year) for pid in pitcher_ids])
        for pid, result in zip(pitcher_ids, results):
            _gamelogs_cache[pid] = result
        logger.info("Gamelogs warmed for %d pitchers", len(pitcher_ids))
    except Exception as e:
        logger.warning("Gamelogs warmup failed: %s", e)

def _log_pipeline_health(year: int) -> None:
    """One-shot summary of which features actually populated after warmup.
    Quickly tells you when a Savant schema change has silently broken a key
    feature (e.g., K% on pitchers vanished when their default leaderboard
    rotated to batted-ball metrics in early 2026).

    Output format:
      Pipeline health 2026:
        pitchers (N=534): xFIP=534/534, K%=412/534, PutAway%=380/534, K-BB%=380/534
        batters  (N=456): xwOBA=456/456, K%=424/456, K%_vsL=311/456, K%_vsR=311/456
        warnings: PutAway% coverage <70% (380/534)
    """
    try:
        pitch_df = cache_get(f"pitching_{year}")
        bat_df   = cache_get(f"batting_{year}")
    except Exception:
        return
    warnings = []
    parts = [f"Pipeline health {year}:"]

    if pitch_df is not None and not pitch_df.empty:
        n = len(pitch_df)
        cov = lambda c: pitch_df[c].notna().sum() if c in pitch_df.columns else 0
        cols = ["xFIP", "K%", "PutAway%", "K-BB%", "SwStr%", "Chase%"]
        summary = ", ".join(f"{c}={cov(c)}/{n}" for c in cols)
        parts.append(f"  pitchers (N={n}): {summary}")
        for c in ("K%", "xFIP", "PutAway%"):
            pct = cov(c) / n if n else 0
            if pct < 0.70:
                warnings.append(f"pitchers {c} coverage {pct:.0%} ({cov(c)}/{n})")
    else:
        parts.append("  pitchers: NO DATA")
        warnings.append("pitcher dataframe empty")

    if bat_df is not None and not bat_df.empty:
        n = len(bat_df)
        cov = lambda c: bat_df[c].notna().sum() if c in bat_df.columns else 0
        cols = ["xwOBA", "K%", "K%_vsL", "K%_vsR", "Chase%", "SwStr%"]
        summary = ", ".join(f"{c}={cov(c)}/{n}" for c in cols)
        parts.append(f"  batters  (N={n}): {summary}")
        for c in ("K%", "xwOBA"):
            pct = cov(c) / n if n else 0
            if pct < 0.70:
                warnings.append(f"batters {c} coverage {pct:.0%} ({cov(c)}/{n})")
    else:
        parts.append("  batters: NO DATA")
        warnings.append("batter dataframe empty")

    if warnings:
        parts.append("  ⚠ warnings: " + "; ".join(warnings))
    else:
        parts.append("  ✓ all critical features populated")

    logger.info("\n".join(parts))


async def _warmup_cache():
    """Pre-load pitching + batting data in background so first user request is fast."""
    try:
        year = date.today().year
        await asyncio.gather(
            asyncio.to_thread(_load_pitching, year),
            asyncio.to_thread(_load_batting, year),
        )
        logger.info("Cache warmup complete")
        # Health summary — surfaces silent failures in feature merges.
        _log_pipeline_health(year)
        await _warmup_gamelogs(year)
    except Exception as e:
        logger.warning("Cache warmup failed: %s", e)
    # Tanda 1 — Odds API warmup. One events fetch at startup populates the
    # quota counter (so the UI chip shows real numbers before the first pick)
    # AND warms the events cache for 1h, so the first pick of the day pays
    # zero events-list calls.
    if _oac is not None and _oac.is_configured():
        try:
            events = await _oac.fetch_events_today()
            logger.info("Odds API warmup: %d events cached, quota %s",
                        len(events), _oac.quota_state())
        except Exception as e:
            logger.warning("Odds API warmup failed: %s", e)

@app.on_event("startup")
async def startup():
    init_db()
    # Clear stale cache
    for k in [k for k in list(_cache.keys()) if k.startswith("batting_") or k.startswith("pitching_") or k.startswith("pitchers_")]:
        _cache.pop(k, None)
    asyncio.create_task(_warmup_cache())
    if scheduler is not None:
        scheduler.add_job(
            _run_retrain_job,
            "cron",
            day_of_week="mon",
            hour=3,
            minute=0,
            id="weekly_retrain",
            replace_existing=True,
        )
        scheduler.start()
        logger.info("Scheduler started — weekly retrain every Monday 3am ART")
    logger.info("CherryPick API started — DB initialised at %s", DB_PATH)


@app.on_event("shutdown")
def shutdown():
    if scheduler is not None and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

# ── Simulation constants ───────────────────────────────────────────────────────
LEAGUE_AVG_RUNS    = 4.5
LEAGUE_AVG_XFIP    = 4.20
LEAGUE_MEDIAN_XFIP = 4.05
LEAGUE_AVG_XWOBA   = 0.315
LEAGUE_AVG_K_PCT   = 0.228
HOME_ADVANTAGE   = 0.1
MONTE_CARLO_N    = 10_000
# Bullpen blending — typical starter covers 5 of 9 innings
STARTER_INN_SHARE  = 5.0 / 9.0   # ≈ 0.556
BULLPEN_INN_SHARE  = 4.0 / 9.0   # ≈ 0.444
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
    Fetch pitcher analytics from Savant + MLB Stats API.

    Sources (parallel fetch):
      1. Savant expected_statistics → xERA (stored as 'xFIP' for sim math
         compatibility), est_woba_against ('xwOBA_against'), PA
      2. Savant statcast            → BB%, Whiff%, Chase%, Zone%, hard_hit%,
                                      barrel%, avg_velo (K% used to come
                                      from here but Savant changed the
                                      default cols to batted-ball metrics
                                      in 2026; we now fall back to MLB API)
      3. Savant put_away (custom)   → PutAway% (2-strike K conversion)
      4. MLB Stats API (season)     → strikeOuts, battersFaced → reliable K%

    Also derives K/9 from (K% × ~3.85 BF/IP). MLB API K% is treated as the
    source of truth — Savant's discipline endpoint has historically been
    flaky, especially mid-season when the schema rotates.
    """
    xstats_url = (
        "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
        f"?type=pitcher&year={year}&position=&team=&min=1&csv=true"
    )
    statcast_url = (
        "https://baseballsavant.mlb.com/leaderboard/statcast"
        f"?type=pitcher&year={year}&position=&team=&min=1&csv=true"
    )
    # Put-away% leaderboard. Custom URL with explicit selections — Savant's
    # default leaderboard schema rotates, so we pin the column.
    putaway_url = (
        "https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={year}&type=pitcher&filter=&sort=9&sortDir=desc&min=1"
        "&selections=put_away&chart=false&x=put_away&y=put_away"
        "&r=no&chartType=beeswarm&csv=true"
    )
    # MLB Stats API — source of truth for pitcher K%. battersFaced
    # corresponds to PA-against; strikeOuts/battersFaced = K%.
    mlb_pitch_url = f"{MLB_API}/stats"
    mlb_pitch_params = dict(stats="season", group="pitching", gameType="R",
                            season=year, playerPool="All", limit=2000,
                            sortStat="battersFaced")
    try:
        async with httpx.AsyncClient() as client:
            xstats_r, statcast_r, putaway_r, mlb_pitch_r = await asyncio.gather(
                client.get(xstats_url, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True),
                client.get(statcast_url, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True),
                client.get(putaway_url, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True),
                client.get(mlb_pitch_url, params=mlb_pitch_params, timeout=20),
            )
        xstats_r.raise_for_status()
        df = _parse_savant_csv(xstats_r.content)

        # Map expected_statistics columns onto our schema
        if "xera" in df.columns:
            df["xFIP"] = pd.to_numeric(df["xera"], errors="coerce")
        if "est_woba" in df.columns:
            df["xwOBA_against"] = pd.to_numeric(df["est_woba"], errors="coerce")
        if "pa" in df.columns:
            df["PA"] = pd.to_numeric(df["pa"], errors="coerce")

        # ── Merge statcast leaderboard (K%, BB%, whiff%, chase, etc.) ──────────
        if statcast_r.status_code == 200:
            try:
                sc = _parse_savant_csv(statcast_r.content)
                if "player_id" in sc.columns and "player_id" in df.columns:
                    sc["player_id"] = pd.to_numeric(sc["player_id"], errors="coerce")
                    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
                    keep: dict = {}
                    # Map Savant statcast column names → internal schema.
                    # All /100 because Savant exposes them as whole-number percents.
                    col_map = {
                        "k_percent":            ("K%",         0.01),
                        "bb_percent":           ("BB%",        0.01),
                        "whiff_percent":        ("SwStr%",     0.01),
                        "swing_percent":        ("Swing%",     0.01),
                        "oz_swing_percent":     ("Chase%",     0.01),
                        "z_swing_percent":      ("ZSwing%",    0.01),
                        "barrel_batted_rate":   ("Barrel%",    0.01),
                        "hard_hit_percent":     ("HardHit%",   0.01),
                        "avg_best_speed":       ("AvgVelo",    1.0),
                        "p_formatted_ip":       ("IP_season",  1.0),
                    }
                    for src, (dst, scale) in col_map.items():
                        if src in sc.columns:
                            keep[dst] = pd.to_numeric(sc[src], errors="coerce") * scale
                    if keep:
                        merge_df = pd.DataFrame({"player_id": sc["player_id"], **keep}).dropna(subset=["player_id"])
                        df = df.merge(merge_df, on="player_id", how="left")
                        logger.info("_savant_pitching: merged %s from statcast", list(keep.keys()))
                    # Derive K-BB% and K/9 when raw percents present
                    if "K%" in df.columns and "BB%" in df.columns:
                        df["K-BB%"] = df["K%"] - df["BB%"]
                    if "K%" in df.columns:
                        # K/9 ≈ K% × (batters/inning) × 9.
                        # MLB average ~3.85 BF/IP → (K% × 3.85 × 9) ≈ K% × 34.65.
                        df["K/9"] = df["K%"] * 34.65
            except Exception as e:
                logger.warning("_savant_pitching statcast merge failed: %s", e)
        else:
            logger.warning("Savant pitching statcast HTTP %s", statcast_r.status_code)

        # ── Merge put-away% leaderboard ────────────────────────────────────────
        # Savant rotates column names on the custom leaderboard. We try a
        # series of candidate patterns; whichever one matches wins. If no
        # column matches, log the actual response cols so the next fix can
        # see what came back instead of a silent zero-merge.
        if putaway_r.status_code == 200:
            try:
                pa = _parse_savant_csv(putaway_r.content)
                # Try multiple naming patterns observed across Savant rev'ns.
                _putaway_candidates = [
                    "put_away", "putaway", "put_away_pct", "put_aways",
                    "PutAway%", "PA%", "putawaypercent",
                ]
                pa_col = None
                for cand in _putaway_candidates:
                    for col in pa.columns:
                        if col.lower().replace("%", "").replace("_", "") == cand.lower().replace("%", "").replace("_", ""):
                            pa_col = col
                            break
                    if pa_col:
                        break
                # Last-resort: any column that contains "put" AND ("away" OR "aways")
                if pa_col is None:
                    pa_col = next((c for c in pa.columns
                                   if "put" in c.lower() and "away" in c.lower()), None)

                if pa_col and "player_id" in pa.columns and "player_id" in df.columns:
                    pa["player_id"] = pd.to_numeric(pa["player_id"], errors="coerce")
                    pa_clean = pa[["player_id", pa_col]].copy()
                    raw_vals = pd.to_numeric(pa_clean[pa_col], errors="coerce")
                    # Auto-detect scale: Savant sometimes returns 0-1 fractions,
                    # other times 0-100 integers. Heuristic: if max > 1.5,
                    # divide by 100; otherwise leave as-is.
                    if raw_vals.notna().any() and raw_vals.max() > 1.5:
                        pa_clean["PutAway%"] = raw_vals * 0.01
                    else:
                        pa_clean["PutAway%"] = raw_vals
                    pa_clean = pa_clean[["player_id", "PutAway%"]].dropna(subset=["player_id"])
                    df = df.merge(pa_clean, on="player_id", how="left")
                    logger.info("_savant_pitching: merged PutAway%% from col=%r (%d non-null, max=%.4f)",
                                pa_col,
                                df["PutAway%"].notna().sum(),
                                float(df["PutAway%"].max()) if df["PutAway%"].notna().any() else float("nan"))
                else:
                    logger.warning("_savant_pitching: PutAway%% column not found. "
                                   "Available cols: %s", list(pa.columns[:20]))
            except Exception as e:
                logger.warning("_savant_pitching put_away merge failed: %s", e)

        # ── Merge K% from MLB Stats API (reliable source of truth) ────────────
        # Savant's pitcher statcast leaderboard rotated to batted-ball metrics
        # and no longer ships k_percent in the default schema, so we pull
        # K% directly from MLB Stats API where it's stable.
        try:
            if mlb_pitch_r.status_code == 200:
                pitcher_kpct: dict = {}
                stats_data = mlb_pitch_r.json().get("stats", [{}])
                splits = stats_data[0].get("splits", []) if stats_data else []
                for split in splits:
                    pid = split.get("player", {}).get("id")
                    stat = split.get("stat", {})
                    so = safe_float(stat.get("strikeOuts"))
                    bf = safe_float(stat.get("battersFaced"))
                    if pid and so is not None and bf and bf >= 10:
                        pitcher_kpct[int(pid)] = round(so / bf, 4)
                if pitcher_kpct and "player_id" in df.columns:
                    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
                    # If K% was already merged from Savant, only fill gaps;
                    # otherwise inject as the primary source.
                    mlb_kpct = df["player_id"].map(pitcher_kpct)
                    if "K%" in df.columns:
                        df["K%"] = pd.to_numeric(df["K%"], errors="coerce").fillna(mlb_kpct)
                    else:
                        df["K%"] = mlb_kpct
                    # Refresh derived K/9 from the (possibly newly populated) K%
                    df["K/9"] = df["K%"] * 34.65
                    if "BB%" in df.columns:
                        df["K-BB%"] = df["K%"] - df["BB%"]
                    n_kpct = df["K%"].notna().sum()
                    logger.info("_savant_pitching: K%% from MLB API → %d/%d non-null (sample %d players)",
                                n_kpct, len(df), len(pitcher_kpct))
            else:
                logger.warning("_savant_pitching MLB API K%% fetch HTTP %s",
                               mlb_pitch_r.status_code)
        except Exception as e:
            logger.warning("_savant_pitching MLB K%% merge failed: %s", e)

        logger.info("_savant_pitching final cols: %s", list(df.columns[:40]))
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
    # statcast leaderboard includes k_percent which expected_statistics omits
    statcast_url = (
        "https://baseballsavant.mlb.com/leaderboard/statcast"
        f"?type=batter&year={year}&position=&team=&min=50&csv=true"
    )
    stats_url = f"{MLB_API}/stats"
    stats_params      = dict(stats="season", group="hitting", gameType="R",
                             season=year, playerPool="All", limit=3000,
                             sortStat="plateAppearances")
    stats_params_vsL  = {**stats_params, "sitCodes": "vl"}
    stats_params_vsR  = {**stats_params, "sitCodes": "vr"}
    teams_url = f"{MLB_API}/teams"
    teams_params = dict(sportId=1, season=year)
    try:
        async with httpx.AsyncClient() as client:
            savant_r, statcast_r, stats_r, stats_vsL_r, stats_vsR_r, teams_r = await asyncio.gather(
                client.get(savant_url, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True),
                client.get(statcast_url, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True),
                client.get(stats_url,  params=stats_params,      timeout=20),
                client.get(stats_url,  params=stats_params_vsL,  timeout=20),
                client.get(stats_url,  params=stats_params_vsR,  timeout=20),
                client.get(teams_url,  params=teams_params,       timeout=20),
            )
        savant_r.raise_for_status()
        df = _parse_savant_csv(savant_r.content)
        if "est_woba" in df.columns:
            df["xwOBA"] = pd.to_numeric(df["est_woba"], errors="coerce")
        if "pa" in df.columns:
            df["PA"] = pd.to_numeric(df["pa"], errors="coerce")
        if "k_percent" in df.columns:
            df["K%"] = pd.to_numeric(df["k_percent"], errors="coerce") / 100.0
        # Merge statcast leaderboard: K% (if missing) + batter discipline stats
        logger.info("After expected_stats: K%% in cols=%s, statcast_r.status=%s",
                    "K%" in df.columns, statcast_r.status_code)
        if statcast_r.status_code == 200:
            try:
                sc_df = _parse_savant_csv(statcast_r.content)
                logger.info("Statcast cols: %s", list(sc_df.columns[:20]))
                if "player_id" in sc_df.columns and "player_id" in df.columns:
                    sc_df["player_id"] = pd.to_numeric(sc_df["player_id"], errors="coerce")
                    df["player_id"]    = pd.to_numeric(df["player_id"],    errors="coerce")
                    # K% — only merge if missing from expected_statistics
                    if "K%" not in df.columns and "k_percent" in sc_df.columns:
                        kp = sc_df[["player_id","k_percent"]].copy()
                        kp["K%"] = pd.to_numeric(kp["k_percent"], errors="coerce") / 100.0
                        df = df.merge(kp[["player_id","K%"]], on="player_id", how="left")
                        logger.info("Merged K%% from statcast leaderboard (%d rows)", df["K%"].notna().sum())
                    # Batter discipline stats used for opp_chase_pct / opp_swstr_pct / opp_contact_pct
                    _disc_map = {
                        "oz_swing_percent":   ("Chase%",    0.01),
                        "whiff_percent":      ("SwStr%",    0.01),
                        "iz_contact_percent": ("ZContact%", 0.01),
                    }
                    _disc_keep: dict = {}
                    for _src, (_dst, _scale) in _disc_map.items():
                        if _src in sc_df.columns:
                            _disc_keep[_dst] = pd.to_numeric(sc_df[_src], errors="coerce") * _scale
                    if _disc_keep:
                        _disc_df = pd.DataFrame({"player_id": sc_df["player_id"], **_disc_keep}).dropna(subset=["player_id"])
                        df = df.merge(_disc_df, on="player_id", how="left")
                        logger.info("_savant_batting: merged batter discipline %s", list(_disc_keep.keys()))
                else:
                    logger.warning("Statcast merge skipped: player_id_sc=%s player_id_df=%s",
                                   "player_id" in sc_df.columns, "player_id" in df.columns)
            except Exception as e:
                logger.warning("_savant_batting statcast merge failed: %s", e)
        else:
            logger.warning("Savant batting statcast HTTP %s", statcast_r.status_code)
        # Build team_id → normalised abbreviation from /teams
        team_abbr: dict = {}
        if teams_r.status_code == 200:
            for t in teams_r.json().get("teams", []):
                team_abbr[t["id"]] = _norm_team(t.get("abbreviation", ""))
        # Build player_id → team abbreviation AND K% (overall + platoon splits)
        player_team: dict = {}
        player_kpct: dict = {}
        if stats_r.status_code == 200:
            for split in stats_r.json().get("stats", [{}])[0].get("splits", []):
                pid  = split.get("player", {}).get("id")
                tid  = split.get("team",   {}).get("id")
                abbr = team_abbr.get(tid, "")
                stat = split.get("stat", {})
                if pid and abbr:
                    player_team[int(pid)] = abbr
                if pid:
                    so = safe_float(stat.get("strikeOuts"))
                    pa = safe_float(stat.get("plateAppearances"))
                    if so is not None and pa and pa >= 10:
                        player_kpct[int(pid)] = round(so / pa, 4)

        def _parse_platoon_kpct(resp) -> dict:
            # Require a meaningful PA sample — a 5-PA threshold let random
            # noise into platoon splits during the April small-sample window.
            # Keep per-player K% only when it carries real signal (≥50 PA);
            # otherwise we fall back to the overall K% upstream.
            out = {}
            if resp.status_code != 200:
                return out
            for split in resp.json().get("stats", [{}])[0].get("splits", []):
                pid  = split.get("player", {}).get("id")
                stat = split.get("stat", {})
                if pid:
                    so = safe_float(stat.get("strikeOuts"))
                    pa = safe_float(stat.get("plateAppearances"))
                    if so is not None and pa and pa >= 50:
                        out[int(pid)] = round(so / pa, 4)
            return out

        player_kpct_vsL = _parse_platoon_kpct(stats_vsL_r)
        player_kpct_vsR = _parse_platoon_kpct(stats_vsR_r)

        logger.info("_savant_batting: team_abbr=%d, player_team=%d, k_pct=%d, k_vsL=%d, k_vsR=%d, df=%d rows",
                    len(team_abbr), len(player_team), len(player_kpct),
                    len(player_kpct_vsL), len(player_kpct_vsR), len(df))

        if player_team and "player_id" in df.columns:
            df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce")
            df["Team"] = df["player_id"].map(player_team)
            mapped = df["Team"].notna().sum()
            logger.info("_savant_batting: Team column populated for %d/%d rows", mapped, len(df))
        else:
            logger.warning("_savant_batting: no team mapping — team_abbr=%d, player_team=%d",
                           len(team_abbr), len(player_team))

        # Inject overall K% and platoon splits
        if "player_id" in df.columns:
            if "K%" not in df.columns and player_kpct:
                df["K%"] = df["player_id"].map(player_kpct)
                logger.info("_savant_batting: K%% injected for %d/%d rows", df["K%"].notna().sum(), len(df))
            if player_kpct_vsL:
                df["K%_vsL"] = df["player_id"].map(player_kpct_vsL)
                logger.info("_savant_batting: K%%_vsL injected for %d/%d rows", df["K%_vsL"].notna().sum(), len(df))
            if player_kpct_vsR:
                df["K%_vsR"] = df["player_id"].map(player_kpct_vsR)
                logger.info("_savant_batting: K%%_vsR injected for %d/%d rows", df["K%_vsR"].notna().sum(), len(df))
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
                k_valid = df["K%"].notna().sum() if "K%" in df.columns else 0
                if valid >= 20:
                    cache_set(key, df)
                    logger.info("Pitching data loaded from Savant %d (%d rows, %d xFIP, %d K%%)",
                                yr, len(df), valid, k_valid)
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

def _team_bullpen_xfip(pitch_df, team_abbr: str, starter_name: str) -> Optional[float]:
    """
    IP-weighted average xFIP for a team's bullpen (all pitchers except the starter).
    Works with both Savant CSV (team_name_alt) and pybaseball fallback (Team).

    Small-sample protection: drops any reliever with < 2 IP (their xFIP is
    essentially noise) and, when the surviving bullpen has < 30 IP total,
    regresses the resulting xFIP toward the league average.
    """
    if pitch_df is None or "xFIP" not in pitch_df.columns:
        return None
    team_col = next(
        (c for c in pitch_df.columns if c.lower() in ("team_name_alt", "team", "tm")),
        None,
    )
    if team_col is None:
        logger.warning(
            "_team_bullpen_xfip: no team column found — available cols: %s",
            list(pitch_df.columns[:20]),
        )
        return None
    mask = pitch_df[team_col].str.upper() == team_abbr.upper()
    team_df = pitch_df[mask].copy()
    if team_df.empty:
        return None
    # Exclude today's starter
    if starter_name and "Name" in team_df.columns:
        team_df = team_df[team_df["Name"].str.lower() != starter_name.lower()]
    if team_df.empty:
        return None

    ip_col = next((c for c in team_df.columns if c.lower() == "ip"), None)
    # Filter out relievers with tiny samples (< 2 IP) — their xFIP is noise
    if ip_col:
        team_df = team_df.assign(_ip=pd.to_numeric(team_df[ip_col], errors="coerce").fillna(0))
        team_df = team_df[team_df["_ip"] >= 2.0]
        if team_df.empty:
            return None

    xfip_vals = pd.to_numeric(team_df["xFIP"], errors="coerce")
    if ip_col:
        ip_vals  = team_df["_ip"]
        total_ip = float(ip_vals.sum())
        if total_ip > 0:
            weighted = float((xfip_vals * ip_vals).sum() / total_ip)
            if not np.isnan(weighted):
                # Regress toward league average when bullpen sample is thin
                # (< 30 IP post-filter). Full signal above 30 IP.
                if total_ip < 30.0:
                    w = total_ip / 30.0
                    weighted = w * weighted + (1 - w) * LEAGUE_AVG_XFIP
                    logger.info(
                        "_team_bullpen_xfip: %s thin sample (%.1f IP) → regressed to %.3f",
                        team_abbr, total_ip, weighted,
                    )
                return round(weighted, 3)
    # Fallback: median (no IP column available)
    med = xfip_vals.median()
    return round(float(med), 3) if not np.isnan(med) else None

def _team_stat(bat_df, team_abbr: str, col: str, default: float, pitcher_hand: str = None):
    if bat_df is None or "Team" not in bat_df.columns:
        return default
    # Use platoon split column when available and pitcher_hand is known
    if col == "K%" and pitcher_hand in ("L", "R"):
        platoon_col = f"K%_vs{pitcher_hand}"
        if platoon_col in bat_df.columns:
            col = platoon_col
    if col not in bat_df.columns:
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
def _parse_lineup_side(side_data: dict) -> list:
    """Extract ordered lineup from a teams side dict (boxscore or schedule lineups)."""
    lineup = []
    # boxscore format: battingOrder list + players dict
    batting_order = side_data.get("battingOrder", [])
    players_dict  = side_data.get("players", {})
    if batting_order and players_dict:
        for pid in batting_order:
            key    = f"ID{pid}"
            person = players_dict.get(key, {}).get("person", {})
            name   = person.get("fullName", "")
            if name:
                lineup.append({"id": pid, "name": name})
        return lineup
    # schedule lineups format: list of {id, fullName}
    for p in side_data.get("players", []):
        if isinstance(p, dict):
            pid  = p.get("id")
            name = p.get("fullName", "")
            if pid and name:
                lineup.append({"id": pid, "name": name})
    return lineup


async def _fetch_lineups(client: httpx.AsyncClient, game_id: int) -> dict:
    empty = {"home": [], "away": []}
    today = date.today().strftime("%Y-%m-%d")

    # ── Strategy 1: schedule?hydrate=lineups (available pre-game) ──
    try:
        data = await _mlb_get(client, "/schedule",
                              sportId=1, date=today,
                              gamePk=game_id,
                              hydrate="lineups")
        for date_block in data.get("dates", []):
            for g in date_block.get("games", []):
                if g.get("gamePk") == game_id:
                    lineups = g.get("lineups", {})
                    home_lp = lineups.get("homePlayers", [])
                    away_lp = lineups.get("awayPlayers", [])
                    home = [{"id": p.get("id"), "name": p.get("fullName","")} for p in home_lp if p.get("fullName")]
                    away = [{"id": p.get("id"), "name": p.get("fullName","")} for p in away_lp if p.get("fullName")]
                    if len(home) >= 7 or len(away) >= 7:
                        logger.info("Lineups from schedule endpoint: home=%d away=%d", len(home), len(away))
                        return {"home": home, "away": away}
    except Exception as e:
        logger.debug("schedule lineups fetch failed: %s", e)

    # ── Strategy 2: boxscore battingOrder (available once game starts) ──
    try:
        data  = await _mlb_get(client, f"/game/{game_id}/boxscore")
        teams = data.get("teams", {})
        result = {side: _parse_lineup_side(teams.get(side, {})) for side in ("home", "away")}
        if any(len(v) >= 7 for v in result.values()):
            logger.info("Lineups from boxscore: home=%d away=%d", len(result["home"]), len(result["away"]))
            return result
    except Exception as e:
        logger.debug("boxscore lineups fetch failed: %s", e)

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


_LINEUP_POS_WEIGHTS = [1.4, 1.4, 1.4, 1.4, 1.0, 1.0, 1.0, 0.7, 0.7]

def _lineup_weighted_k_pct(lineup: list, pitcher_hand: str, bat_df) -> Optional[float]:
    """
    Compute batting-order-weighted K% for a lineup vs pitcher_hand.
    Uses platoon split (K%_vsL / K%_vsR) when available, falls back to overall K%.
    lineup: [{id, name}, ...] in batting order (index 0 = leadoff).
    Returns weighted K% or None if fewer than 5 batters matched.
    """
    if bat_df is None or not lineup:
        return None

    # Prefer platoon split column; fall back to overall K%
    platoon_col = f"K%_vs{pitcher_hand}" if pitcher_hand in ("L", "R") else None
    k_col = platoon_col if (platoon_col and platoon_col in bat_df.columns) else "K%"

    has_id   = "player_id" in bat_df.columns and k_col in bat_df.columns
    has_name = "Name"      in bat_df.columns and k_col in bat_df.columns
    if not has_id and not has_name:
        return None

    k_vals: list = []
    wts:    list = []
    for i, player in enumerate(lineup[:9]):
        w   = _LINEUP_POS_WEIGHTS[i]
        val = None
        pid = player.get("id")
        if has_id and pid:
            mask = bat_df["player_id"] == int(pid)
            if mask.any():
                v = bat_df.loc[mask, k_col].values[0]
                if pd.notna(v):
                    val = float(v)
        if val is None and has_name:
            name = player.get("name", "")
            if name:
                mask = bat_df["Name"].str.lower() == name.lower()
                if not mask.any():
                    last = name.split()[-1].lower()
                    mask = bat_df["Name"].str.lower().str.endswith(last)
                if mask.any():
                    v = bat_df.loc[mask, k_col].values[0]
                    if pd.notna(v):
                        val = float(v)
        if val is not None:
            k_vals.append(val * w)
            wts.append(w)

    if len(k_vals) < 5:
        return None
    used_col = k_col
    logger.debug("lineup_weighted_k_pct: col=%s matched=%d/%d weighted=%.4f",
                 used_col, len(k_vals), len(lineup), sum(k_vals)/sum(wts))
    return round(sum(k_vals) / sum(wts), 4)


# ── Monte Carlo ────────────────────────────────────────────────────────────────
def _simulate(exp_home: float, exp_away: float, n: int = MONTE_CARLO_N):
    rng = np.random.default_rng()
    h = rng.poisson(max(exp_home, 0.1), n)
    a = rng.poisson(max(exp_away, 0.1), n)
    hw = int(np.sum(h > a))
    aw = int(np.sum(a > h))
    ties = n - hw - aw
    return round((hw + ties * 0.5) / n, 4), round((aw + ties * 0.5) / n, 4)

# ── Pitcher gamelogs (rest days + recent K/9) ──────────────────────────────────
_rest_days_sem: Optional[asyncio.Semaphore] = None

def _parse_ip(ip_str) -> float:
    """Convert '6.2' (6 innings + 2 outs) to fractional innings (6.667)."""
    try:
        parts = str(ip_str).split(".")
        full   = int(parts[0])
        thirds = int(parts[1]) if len(parts) > 1 else 0
        return full + thirds / 3.0
    except Exception:
        return 0.0

async def _pitcher_gamelogs(pitcher_id: Optional[int], year: int) -> dict:
    """Return {rest_days, recent_k_rate, recent_ip} for a pitcher via MLB gameLog API."""
    global _rest_days_sem
    if _rest_days_sem is None:
        _rest_days_sem = asyncio.Semaphore(5)
    empty = {"rest_days": None, "recent_k_rate": None, "recent_ip": None}
    if not pitcher_id:
        return empty
    async with _rest_days_sem:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                data = await _mlb_get(
                    client, f"/people/{pitcher_id}/stats",
                    stats="gameLog", group="pitching", season=year,
                )
            splits = data.get("stats", [{}])[0].get("splits", [])
            if not splits:
                return empty

            # Rest days from last start date
            last_date_str = splits[-1].get("date", "")
            rest_days = None
            if last_date_str:
                last = datetime.strptime(last_date_str, "%Y-%m-%d").date()
                rest_days = (date.today() - last).days

            # K/9 + avg IP/start over last 3 starts — require ≥12 IP sample
            recent = splits[-3:]
            total_k  = sum(s.get("stat", {}).get("strikeOuts", 0) for s in recent)
            total_ip = sum(_parse_ip(s.get("stat", {}).get("inningsPitched", "0")) for s in recent)
            if total_ip >= 12.0:
                recent_k_rate = round(total_k / total_ip * 9, 2)
                recent_ip     = round(total_ip / len(recent), 2)
            else:
                if total_ip > 0:
                    logger.info(
                        "Pitcher %s recent_k_rate skipped — only %.1f IP in last 3 starts",
                        pitcher_id, total_ip,
                    )
                recent_k_rate = None
                recent_ip     = None

            return {"rest_days": rest_days, "recent_k_rate": recent_k_rate, "recent_ip": recent_ip}
        except Exception as e:
            logger.warning("Gamelogs fetch failed for pitcher %s: %s", pitcher_id, e)
            return empty

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
# ENDPOINT 1b — GET /api/lineup-status
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/api/lineup-status")
async def get_lineup_status():
    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
            lineups = await asyncio.gather(*[_fetch_lineups(client, g["game_id"]) for g in games])
        return {
            g["game_id"]: {
                "home": len(lu.get("home", [])) >= 7,
                "away": len(lu.get("away", [])) >= 7,
            }
            for g, lu in zip(games, lineups)
        }
    except Exception as e:
        logger.error("lineup_status error: %s", e)
        raise HTTPException(502, str(e))

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

    pitch_df, bat_df, train_df = await asyncio.gather(
        asyncio.to_thread(_load_pitching, year),
        asyncio.to_thread(_load_batting, year),
        asyncio.to_thread(_load_training_df),
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

    # Build pitcher list without rest_days first
    pitcher_candidates = []
    seen: set = set()

    for game in games:
        for side, opp_side, home_away_flag in [("home", "away", 1), ("away", "home", 0)]:
            pitcher = game[f"{side}_pitcher"]
            p_name  = pitcher["name"]
            p_id    = pitcher["id"]
            opp     = game[f"{opp_side}_team"]

            if not p_name or p_name == "TBD" or p_name in seen:
                continue
            seen.add(p_name)

            p_row = _find_pitcher_row(pitch_df, p_name)
            data_available = 0
            # 8 signals feed confidence: K%, K-BB%, SwStr%, PutAway%, CSW%,
            # opp_k_pct, recent_k_rate, hand/platoon.
            total_stats    = 8

            csw_pct    = None   # legacy path (pybaseball fallback)
            swstr_pct  = None
            stuff_plus = None
            k9         = None
            hand       = None
            k_pct      = None   # K% per PA — sharp baseline
            bb_pct     = None
            kbb_pct    = None
            putaway    = None

            if p_row is not None:
                csw_pct    = safe_col(p_row, "CSW%", "CSW")
                swstr_pct  = safe_col(p_row, "SwStr%", "SwStr")
                stuff_plus = safe_col(p_row, "Stuff+", "Stuff")
                k9         = safe_col(p_row, "K/9", "SO9")
                k_pct      = safe_col(p_row, "K%")
                bb_pct     = safe_col(p_row, "BB%")
                kbb_pct    = safe_col(p_row, "K-BB%")
                putaway    = safe_col(p_row, "PutAway%")
                hand_raw   = p_row.get("Throws") if hasattr(p_row, "get") else None
                hand       = str(hand_raw) if hand_raw else None
                data_available += sum(1 for v in [k_pct, kbb_pct, swstr_pct, putaway, csw_pct] if v is not None)

            opp_k_pct = _team_stat(bat_df, opp, "K%", None, pitcher_hand=hand)
            if opp_k_pct is not None:
                data_available += 1
            if hand in ("L", "R"):
                data_available += 1

            # Recent form + rest days from warmup cache (single lookup)
            gl            = _gamelogs_cache.get(p_id, {})
            recent_k_rate = gl.get("recent_k_rate")
            rest_days_val = gl.get("rest_days")
            if recent_k_rate is not None:
                data_available += 1

            # ── GBM projection ────────────────────────────────────────────────
            # Build feature dict from live Savant stats and call trained model.
            # Season-level stats serve as proxies for L15 rolling windows.
            # Falls back to the analytic formula if the model throws.
            recent_ip = gl.get("recent_ip")

            _feats: dict = {
                "k_pct_L15":             float(k_pct)         if k_pct is not None         else None,
                "csw_pct_L15":           float(csw_pct)       if csw_pct is not None       else None,
                "swstr_pct_L15":         float(swstr_pct)     if swstr_pct is not None     else None,
                "stuff_plus":            float(stuff_plus)    if stuff_plus is not None    else None,
                "k9_season":             float(k9)            if k9 is not None            else None,
                "k9_L3":                 float(recent_k_rate) if recent_k_rate is not None else None,
                "rest_days":             rest_days_val,
                "innings_prev_start":    recent_ip,
                "cumulative_pitches_7d": None,
                "pitch_limit_flag":      0,
                "implied_max_batters":   max(18.0, min(28.0, float(recent_ip) * 4.3)) if recent_ip else 24.0,
                "opp_k_pct_vs_hand":     float(opp_k_pct) if opp_k_pct is not None else None,
                "opp_k_pct_lineup":      None,
                "lineup_confirmed":      0,
                "park_factor":           _PARK_FACTORS.get(_norm_team(game["home_team"]), 100),
                "home_away":             home_away_flag,
                "month":                 date.today().month,
                "k_line":                odds_k_map.get(p_name.lower()),
            }
            _feats.update(_opp_stats(train_df, opp))
            _feats.update(_live_opp_discipline(bat_df, opp))

            model_projection = None
            ci_low = ci_high = None
            try:
                _ml = predict_strikeouts(_feats)
                model_projection = _ml["model_projection"]
                ci_low  = _ml.get("ci_low")
                ci_high = _ml.get("ci_high")
            except Exception as _ml_err:
                logger.warning("GBM failed for %s (%s) — formula fallback", p_name, _ml_err)
                # Analytic fallback
                LG_K_PCT   = 0.225
                LG_WHIFF   = 0.250
                LG_PUTAWAY = 0.190
                LG_KBB     = 0.135

                def _nudge(val, baseline, weight, lo, hi):
                    if val is None or baseline <= 0:
                        return 1.0
                    tilt = 1.0 + weight * ((float(val) - baseline) / baseline)
                    return max(lo, min(hi, tilt))

                pitcher_k_pa = float(k_pct) if k_pct is not None else (
                    max(0.10, 0.78 * float(csw_pct) - 0.007) if csw_pct is not None else None
                )
                if pitcher_k_pa is not None:
                    matchup_mult = max(0.80, min(1.25, float(opp_k_pct) / LG_K_PCT)) if opp_k_pct is not None else 1.0
                    effective_k  = pitcher_k_pa * matchup_mult * _nudge(swstr_pct, LG_WHIFF, 0.25, 0.85, 1.20) * _nudge(putaway, LG_PUTAWAY, 0.15, 0.90, 1.15) * _nudge(kbb_pct, LG_KBB, 0.10, 0.92, 1.12)
                    effective_k  = max(0.10, min(0.45, effective_k))
                    expected_bf  = max(18.0, min(28.0, float(recent_ip) * 4.3)) if recent_ip else 24.0
                    raw_proj     = effective_k * expected_bf
                    if recent_k_rate is not None and k9 is not None and float(k9) > 0:
                        raw_proj *= max(0.88, min(1.15, float(recent_k_rate) / float(k9)))
                    model_projection = round(raw_proj, 1)

            k_line   = odds_k_map.get(p_name.lower())

            # Bundle A #7 — Park K factor. Applied to pitcher's projection based
            # on the home park (where the game is played). Multiplicative and
            # dampened so we don't double-count whatever K signal the GBM may
            # have already learned from the runs park_factor feature.
            park_k_raw  = _PARK_K_FACTORS.get(_norm_team(game["home_team"]), 100)
            park_k_mult = _park_k_mult(game["home_team"])
            park_k_delta = None
            if model_projection is not None and park_k_mult != 1.0:
                adj_proj  = model_projection * park_k_mult
                park_k_delta = round(adj_proj - model_projection, 2)
                model_projection = round(adj_proj, 1)
                if ci_low  is not None: ci_low  = round(ci_low  * park_k_mult, 2)
                if ci_high is not None: ci_high = round(ci_high * park_k_mult, 2)

            edge_pct = round(model_projection - k_line, 2) if (model_projection is not None and k_line is not None) else None
            # Convert edge → calibrated probability via Normal(proj, std) where
            # std is inferred from the CI band (ci_high - proj). This is the
            # foundation for EV/Kelly sizing in Bundle A #3.
            _std = (ci_high - model_projection) if (ci_high is not None and model_projection is not None) else None
            p_over  = _p_over_ks(model_projection, _std, k_line)
            p_under = round(1 - p_over, 4) if p_over is not None else None

            pitcher_candidates.append({
                "name":              p_name,
                "pitcher_id":        p_id,
                "team":              game[f"{side}_team"],
                "opponent":          opp,
                "hand":              hand,
                "k_line":            k_line,
                "model_projection":  model_projection,
                "ci_low":            ci_low,
                "ci_high":           ci_high,
                "edge_pct":          edge_pct,
                "p_over":            p_over,
                "p_under":           p_under,
                "park_k_factor":     park_k_raw,
                "park_k_delta":      park_k_delta,
                "csw_pct":           csw_pct,
                "swstr_pct":         swstr_pct,
                "stuff_plus":        stuff_plus,
                "k9":                k9,
                "k_pct":             k_pct,
                "bb_pct":            bb_pct,
                "kbb_pct":           kbb_pct,
                "putaway_pct":       putaway,
                "opp_k_pct":         opp_k_pct,
                "recent_k_rate":     recent_k_rate,
                "rest_days":         rest_days_val,
                "confidence_level":  max(1, min(5, round(data_available / total_stats * 5))),
                "data_incomplete":   p_row is None,
                "game_id":           game["game_id"],
                "home_away":         home_away_flag,
            })

    pitchers = []
    for p in pitcher_candidates:
        p["pitch_limit_flag"] = False
        pitchers.append(p)

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

# Bundle A #7 — Park K-rate factors (100 = neutral).
# Independent of run factors: hitter-friendly parks often *suppress* Ks because
# balls in play carry better (Coors altitude wrecks breaking-ball movement),
# while pitcher-friendly parks (marine layer, big foul territory) usually boost
# K rate. Values are 3-year rolling (2023-2025) from public park-factor aggregates.
#   >100 = park boosts Ks (pitcher helps)
#   <100 = park suppresses Ks (batter helps)
_PARK_K_FACTORS = {
    "SD":  105, "MIA": 104, "OAK": 104, "TB":  103, "SEA": 103,
    "DET": 102, "NYM": 101, "CLE": 101, "MIL": 101, "MIN": 100,
    "BAL": 100, "LAD": 100, "LAA": 100, "STL": 100, "HOU":  99,
    "SF":   99, "ATL":  99, "WSH":  99, "NYY":  99, "TOR":  98,
    "AZ":   98, "PIT":  98, "CHC":  97, "CWS":  97, "CHW":  97,
    "KC":   97, "TEX":  97, "BOS":  96, "PHI":  96, "CIN":  95,
    "COL":  93, "ATH": 104,
}


def _park_k_mult(home_team: Optional[str]) -> float:
    """Return a multiplier (e.g., 0.93–1.05) to scale K projection by park.

    Applied *post* GBM so we don't need to retrain. The GBM was trained with the
    runs park_factor as a feature, which partially captures K effects inversely;
    the dampening in the conversion below (factor - 100) * 0.6 acknowledges that
    residual co-learning and avoids double-counting.
    """
    if not home_team:
        return 1.0
    raw = _PARK_K_FACTORS.get(_norm_team(home_team), 100)
    # Dampen by 0.6 because the GBM already learned some park-related signal
    # via the runs park factor — prevents over-correction.
    return 1.0 + ((raw - 100) / 100.0) * 0.6

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

# ── Weather: park geography & roof status ───────────────────────────────────
# (lat, lon, cf_bearing_deg, roof)
#   cf_bearing_deg = compass bearing home-plate → center-field (0°=N, 90°=E).
#   roof: "open" | "retractable" | "dome"
#     - dome:        always closed → ignore weather entirely.
#     - retractable: treat wind at 40%, temp/humidity at 80% (partial exposure).
#     - open:        full weather effect.
_PARK_GEO = {
    "ARI": (33.4455,  -112.0667,   0,   "retractable"),
    "AZ":  (33.4455,  -112.0667,   0,   "retractable"),
    "ATL": (33.8907,   -84.4677,  84,   "open"),
    "BAL": (39.2839,   -76.6218,  61,   "open"),
    "BOS": (42.3467,   -71.0972,  45,   "open"),
    "CHC": (41.9484,   -87.6553,  45,   "open"),
    "CWS": (41.8299,   -87.6338,  40,   "open"),
    "CIN": (39.0975,   -84.5072,  22,   "open"),
    "CLE": (41.4962,   -81.6852,   0,   "open"),
    "COL": (39.7559,  -104.9942,   0,   "open"),
    "DET": (42.3390,   -83.0485, 148,   "open"),
    "HOU": (29.7573,   -95.3555,   0,   "retractable"),
    "KC":  (39.0517,   -94.4803,  45,   "open"),
    "LAA": (33.8003,  -117.8827,  45,   "open"),
    "LAD": (34.0739,  -118.2400,  22,   "open"),
    "MIA": (25.7781,   -80.2196,  30,   "retractable"),
    "MIL": (43.0280,   -87.9712,   0,   "retractable"),
    "MIN": (44.9817,   -93.2777,  90,   "open"),
    "NYM": (40.7571,   -73.8458,  67,   "open"),
    "NYY": (40.8296,   -73.9262,  75,   "open"),
    "OAK": (37.7516,  -122.2005,  57,   "open"),
    "ATH": (38.5803,  -121.5135,  45,   "open"),
    "PHI": (39.9061,   -75.1665,  67,   "open"),
    "PIT": (40.4469,   -80.0057, 116,   "open"),
    "SD":  (32.7073,  -117.1566,   0,   "open"),
    "SEA": (47.5914,  -122.3325,  65,   "retractable"),
    "SF":  (37.7786,  -122.3893, 115,   "open"),
    "STL": (38.6226,   -90.1928,  67,   "open"),
    "TB":  (27.7682,   -82.6534,   0,   "dome"),
    "TEX": (32.7473,   -97.0847,   0,   "retractable"),
    "TOR": (43.6414,   -79.3894,   0,   "retractable"),
    "WSH": (38.8729,   -77.0074,  67,   "open"),
    # Legacy / alt abbreviations
    "SFG": (37.7786,  -122.3893, 115,   "open"),
    "SDP": (32.7073,  -117.1566,   0,   "open"),
    "KCR": (39.0517,   -94.4803,  45,   "open"),
    "TBR": (27.7682,   -82.6534,   0,   "dome"),
    "WSN": (38.8729,   -77.0074,  67,   "open"),
}


async def _fetch_weather(client: httpx.AsyncClient, team: str, game_iso: str) -> Optional[dict]:
    """
    Pull temperature_f, wind speed (mph), wind direction, humidity, and
    cloud cover for a park at the game's first-pitch hour from Open-Meteo
    (no API key). Returns None if the park or timestamp is unknown.
    """
    geo = _PARK_GEO.get(team.upper())
    if not geo or not game_iso:
        return None
    lat, lon, cf_bearing, roof = geo
    try:
        # MLB schedule returns ISO8601 UTC (…Z). Round to the nearest hour.
        dt = datetime.strptime(game_iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    except Exception:
        return None
    target_hour = dt.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:00")

    cache_key = f"wx_{team}_{target_hour}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        r = await client.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude":         lat,
                "longitude":        lon,
                "hourly":           "temperature_2m,wind_speed_10m,wind_direction_10m,relative_humidity_2m,cloud_cover,precipitation_probability",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit":  "mph",
                "timezone":         "UTC",
                "forecast_days":    2,
            },
            timeout=8.0,
        )
        if r.status_code != 200:
            logger.warning("Open-Meteo %s → %d", team, r.status_code)
            return None
        data = r.json().get("hourly", {})
        times = data.get("time", [])
        if target_hour not in times:
            return None
        idx = times.index(target_hour)
        payload = {
            "park":           team.upper(),
            "roof":           roof,
            "temp_f":         round(float(data["temperature_2m"][idx]), 1),
            "wind_mph":       round(float(data["wind_speed_10m"][idx]), 1),
            "wind_dir_deg":   int(round(float(data["wind_direction_10m"][idx]))),
            "humidity_pct":   int(round(float(data["relative_humidity_2m"][idx]))),
            "cloud_pct":      int(round(float(data["cloud_cover"][idx]))),
            "precip_pct":     int(round(float(data["precipitation_probability"][idx]))) if data.get("precipitation_probability") else 0,
            "cf_bearing":     cf_bearing,
            "first_pitch_utc": target_hour + ":00Z",
        }
        # Cache the hourly reading (forecasts barely change intra-hour)
        cache_set(cache_key, payload)
        return payload
    except Exception as e:
        logger.warning("Weather fetch failed for %s: %s", team, e)
        return None


def _weather_runs_adj(wx: Optional[dict]) -> tuple[float, dict]:
    """
    Multiplicative adjustment to expected runs based on weather.
    Returns (factor, details). Factor is 1.0 when wx is None or dome.

    Components (calibrated to Alan Nathan, Baseball Prospectus, FanGraphs):
      temp:  +0.0050 per °F above 70  (≈ 20% swing across a 40°F range;
             splits roughly 0.4-0.6% per °F — use 0.5%)
      wind:  +0.010 × wind_component_mph (~10% per 10 mph straight OUT;
             matches published BIP carry research)

    Humidity is intentionally OMITTED: the physical effect is a small
    POSITIVE (water vapor is lighter than N2/O2, so humid air is less dense
    and balls carry slightly farther) but most public models get the sign
    wrong, and the magnitude is ≤0.5% so it barely moves the needle. Safer
    to leave it out than to risk miscalibrating it.

    Wind effective component = -wind_mph × cos(wind_dir − cf_bearing),
    since wind_direction is FROM which the wind blows.
      wind_dir == cf_bearing        → FROM CF TOWARD home  = IN  → negative
      wind_dir == cf_bearing + 180° → FROM home TOWARD CF  = OUT → positive
    """
    if not wx:
        return 1.0, {}
    roof = wx.get("roof", "open")
    if roof == "dome":
        return 1.0, {"roof": "dome", "note": "indoor — weather ignored"}

    temp   = wx["temp_f"]
    wind   = wx["wind_mph"]
    wdir   = wx["wind_dir_deg"]
    cf     = wx["cf_bearing"]

    # Wind component along home→CF axis (positive = OUT)
    import math
    theta = math.radians(wdir - cf)
    wind_component = -wind * math.cos(theta)  # mph, + = out

    # Retractable roofs dampen wind heavily, temp only mildly
    wind_weight   = 1.0 if roof == "open" else 0.30
    thermo_weight = 1.0 if roof == "open" else 0.75

    temp_adj = 0.0050 * (temp - 70.0) * thermo_weight
    wind_adj = 0.010  * wind_component * wind_weight

    factor = 1.0 + temp_adj + wind_adj
    # Hard cap so a 30-mph gust day can't by itself flip everything
    factor = max(0.85, min(1.20, factor))

    return round(factor, 4), {
        "roof":             roof,
        "temp_adj":         round(temp_adj, 4),
        "wind_adj":         round(wind_adj, 4),
        "wind_component":   round(wind_component, 2),
        "wind_label":       (
            "OUT" if wind_component > 2 else
            "IN"  if wind_component < -2 else
            "CROSS"
        ),
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


def _live_opp_discipline(bat_df, team_abbr: str) -> dict:
    """Team-level batter discipline from live Savant statcast data.
    Overrides stale training-CSV values for opp_chase_pct, opp_swstr_pct,
    opp_contact_pct when the columns exist in bat_df."""
    out = {}
    if bat_df is None or "Team" not in bat_df.columns:
        return out
    for col, feat in (("Chase%", "opp_chase_pct"), ("SwStr%", "opp_swstr_pct"), ("ZContact%", "opp_contact_pct")):
        if col in bat_df.columns:
            v = _team_stat(bat_df, team_abbr, col, None)
            if v is not None:
                out[feat] = v
    if out:
        logger.info("_live_opp_discipline: %s → %s", team_abbr, {k: round(v, 4) for k, v in out.items()})
    return out


def _picks_recommend(edge: float, confidence: int, pitch_limit: bool) -> str:
    if confidence < 3:
        return "SKIP"
    if abs(edge) < 0.8:
        return "SKIP"
    if pitch_limit:
        return "SKIP"
    return "OVER" if edge > 0 else "UNDER"


def _p_over_ks(proj: Optional[float], std: Optional[float], k_line: Optional[float]) -> Optional[float]:
    """P(actual_Ks > k_line) under a Normal(proj, σ) approximation.

    IMPORTANT: The GBM's tree-ensemble std measures *disagreement between base
    learners*, not irreducible game-to-game variance. Empirically it's ~0.3 K,
    but actual K totals in a single start have σ ≈ 2.0-2.5 (overdispersed
    Poisson — pitchers get pulled early, innings vary, opponent lineups vary).
    Using the raw tree-std makes every pick look 99% certain, destroying
    calibration (Brier goes from 0.21 → 0.36).

    Fix: floor σ at √proj (Poisson assumption with λ = proj). For a 6-K start
    this gives σ ≈ 2.45, which matches historical K variance data.

    Returns a probability in [0.02, 0.98] (clamped to avoid degenerate Kelly).
    """
    if proj is None or k_line is None:
        return None
    import math
    # Poisson floor on σ — the tree-std is used only if it exceeds this floor
    # (which shouldn't happen with typical training data, but we respect it).
    poisson_std = math.sqrt(max(float(proj), 1.0))
    effective_std = max(float(std) if std is not None else 0.0, poisson_std)
    if effective_std <= 0:
        return 0.98 if proj > k_line else (0.02 if proj < k_line else 0.50)
    z = (float(proj) - float(k_line)) / effective_std
    # Standard-normal CDF via erf: Φ(z) = 0.5 · (1 + erf(z / √2))
    p_raw = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    # Apply calibration (Platt or Isotonic, whichever is active per settings).
    p_cal = _apply_calibration(p_raw)
    # Clamp away from 0/1 to protect Kelly/EV from outlier priors
    return round(max(0.02, min(0.98, p_cal)), 4)


# ══════════════════════════════════════════════════════════════════════════════
# Settings + auto-P&L helpers
#
# Kelly theory outputs a FRACTION OF BANKROLL to stake (e.g., 0.085 = 8.5% of
# the roll). So the stake math is:
#
#     stake_pesos = kelly_fraction × bankroll_pesos
#
# Unit size is a DISPLAY convention only — "units" = stake / unit_size.
# Common practice: 1 unit = 1% of bankroll (so $120k roll → $1,200/unit).
#
# Also applies a safety cap: max_stake_fraction (default 3% of bankroll) to
# prevent tail-risk blowups from outlier model probabilities.
#
# Pnl_pesos = stake × (odds - 1) on W | -stake on L | 0 on P.
# ══════════════════════════════════════════════════════════════════════════════
DEFAULT_UNIT_SIZE_PESOS     = 1000.0
DEFAULT_BANKROLL_PESOS      = 100_000.0
DEFAULT_MAX_STAKE_FRACTION  = 0.03   # 3% of bankroll per pick hard cap
# Marginal-pick filters — picks that pass _picks_recommend but fail these
# thresholds get demoted to SKIP (model still projects them, but no bet is
# advised). Rationale: with Brier ~0.22 (only 12% better than coin-flip), the
# noise on p_over is ±3-5 pp, which makes EV<5% picks statistically
# indistinguishable from -EV. Quarter-Kelly assumes p is known exactly — when
# p has noise larger than the edge, Kelly destroys you. Hard floors save the
# user from acting on hallucinated edges + on stakes too small to matter.
DEFAULT_MIN_EV_PCT          = 0.05   # require ≥5% EV to recommend
DEFAULT_MIN_STAKE_PESOS     = 2000.0 # require ≥$2k stake to recommend


def _get_setting(conn, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def _set_setting(conn, key: str, value: str) -> None:
    conn.execute("""
        INSERT INTO app_settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))


def _unit_size_pesos(conn) -> float:
    """Read unit_size_pesos from settings (default 1000)."""
    raw = _get_setting(conn, "unit_size_pesos", str(DEFAULT_UNIT_SIZE_PESOS))
    try:
        return max(1.0, float(raw))
    except Exception:
        return DEFAULT_UNIT_SIZE_PESOS


def _bankroll_pesos(conn) -> float:
    """Read bankroll_pesos from settings (default 100k)."""
    raw = _get_setting(conn, "bankroll_pesos", str(DEFAULT_BANKROLL_PESOS))
    try:
        return max(1.0, float(raw))
    except Exception:
        return DEFAULT_BANKROLL_PESOS


def _max_stake_fraction(conn) -> float:
    """Read safety cap as a fraction of bankroll (default 0.03 = 3%)."""
    raw = _get_setting(conn, "max_stake_fraction", str(DEFAULT_MAX_STAKE_FRACTION))
    try:
        f = float(raw)
        return max(0.005, min(0.25, f))  # clamp to 0.5%..25%
    except Exception:
        return DEFAULT_MAX_STAKE_FRACTION


def _min_ev_pct(conn) -> float:
    """Minimum EV (as fraction, e.g. 0.05 = +5%) to recommend a bet.
    Picks below this floor get demoted to SKIP — assumes model noise > marginal edge."""
    raw = _get_setting(conn, "min_ev_pct", str(DEFAULT_MIN_EV_PCT))
    try:
        f = float(raw)
        return max(0.0, min(0.50, f))  # clamp to 0..50%
    except Exception:
        return DEFAULT_MIN_EV_PCT


def _min_stake_pesos(conn) -> float:
    """Minimum stake in pesos to recommend a bet.
    Picks where Kelly suggests less than this get demoted to SKIP — operational
    friction (logging, tracking) makes tiny stakes not worth executing."""
    raw = _get_setting(conn, "min_stake_pesos", str(DEFAULT_MIN_STAKE_PESOS))
    try:
        return max(0.0, float(raw))
    except Exception:
        return DEFAULT_MIN_STAKE_PESOS


# ── Tanda 1 fix — user's actual sportsbooks ───────────────────────────────────
# Without this filter, "best price" surfaces the highest odds across the
# entire global market — useless if the user can't actually bet at FanDuel
# from Argentina. Match the bet to what the user can really execute.
def _user_books(conn) -> list[str]:
    """Comma-separated list of casino_keys the user has accounts at.
    Empty string → no filter (use whole market for best-price selection,
    legacy behavior). Typical AR user: "bet365,betano".
    Available keys: see odds_api_client.PROP_BOOK_WEIGHTS."""
    raw = _get_setting(conn, "user_books", "")
    if not raw:
        return []
    return [k.strip().lower() for k in str(raw).split(",") if k.strip()]


def _odds_regions(conn) -> str:
    """Which The Odds API regions to query. Defaults to all 4.
    'us'  = FanDuel/DK/MGM/Caesars/BetRivers
    'us2' = Bovada/BetOnline (offshore)
    'eu'  = Pinnacle/Betano/Matchbook
    'uk'  = bet365/Betfair Exchange/Paddy Power
    Adding regions does NOT increase quota cost (one request per event)."""
    raw = _get_setting(conn, "odds_regions", "us,us2,eu,uk")
    cleaned = ",".join(
        r.strip().lower() for r in str(raw).split(",")
        if r.strip().lower() in {"us", "us2", "eu", "uk", "au"}
    )
    return cleaned or "us,us2,eu,uk"


def _should_demote_to_skip(rec: Optional[str], ev_pct: Optional[float],
                            stake_pesos: Optional[float],
                            min_ev: float, min_stake: float) -> bool:
    """True when an OVER/UNDER pick fails marginal-edge filters → demote SKIP.

    Returns True only when:
      - rec is OVER or UNDER (we never demote SKIP further)
      - AND (ev_pct < min_ev) OR (stake_pesos < min_stake)

    Picks with no odds yet (ev_pct/stake None) are NOT demoted — user hasn't
    submitted odds, we can't judge marginality. They keep their rec until the
    user enters a price."""
    if rec not in ("OVER", "UNDER"):
        return False
    if ev_pct is not None and ev_pct < min_ev:
        return True
    if stake_pesos is not None and stake_pesos < min_stake:
        return True
    return False


def _stake_pesos(kelly_fraction: Optional[float], bankroll: float,
                 max_fraction: float = DEFAULT_MAX_STAKE_FRACTION) -> Optional[float]:
    """Convert Kelly fraction-of-bankroll → stake in pesos.

    kelly_fraction is the quarter-Kelly output in (0, 1) range (fraction of
    bankroll). Applies a hard cap at max_fraction × bankroll to prevent
    tail-risk disasters from model overconfidence on single picks.
    """
    if kelly_fraction is None or kelly_fraction <= 0:
        return None
    capped = min(float(kelly_fraction), float(max_fraction))
    return round(capped * float(bankroll), 0)


def _pnl_pesos(stake: Optional[float], odds: Optional[float], result: Optional[str]) -> Optional[float]:
    """Compute P&L in pesos for a settled pick.
      W: stake * (odds - 1)   (profit, +)
      L: -stake               (loss, -)
      P: 0                    (push returns the stake)
      None or missing data: None
    """
    if stake is None or result not in ("W", "L", "P"):
        return None
    if result == "P":
        return 0.0
    if result == "L":
        return round(-float(stake), 0)
    # W
    if odds is None or odds <= 1:
        return None
    return round(float(stake) * (float(odds) - 1.0), 0)


# ══════════════════════════════════════════════════════════════════════════════
# Bundle A #1 — Platt scaling (post-hoc probability recalibration)
#
# Fits P_calibrated = σ(A · logit(P_raw) + B) on (p_over, outcome) pairs from
# settled actionable picks. Corrects systematic over/under-confidence —
# e.g., the uncalibrated model saying "72% OVER" but hitting only 63% would
# get A<1 (squashed toward 50%) via the fitted logistic.
# ══════════════════════════════════════════════════════════════════════════════
_PLATT_PARAMS: Optional[dict] = None  # {"A": float, "B": float, "fitted_at": iso, "n_samples": int, "brier_before": float, "brier_after": float}
_PLATT_PATH   = Path(__file__).parent / "platt_cal.json"


def _load_platt() -> None:
    """Load Platt params from disk on startup (if present)."""
    global _PLATT_PARAMS
    try:
        if _PLATT_PATH.exists():
            _PLATT_PARAMS = json.loads(_PLATT_PATH.read_text(encoding="utf-8"))
            logger.info("Platt calibration loaded: A=%+.4f B=%+.4f (n=%s)",
                        _PLATT_PARAMS.get("A", 0), _PLATT_PARAMS.get("B", 0),
                        _PLATT_PARAMS.get("n_samples"))
    except Exception as e:
        logger.warning("Platt calibration load failed: %s", e)
        _PLATT_PARAMS = None


def _save_platt(params: dict) -> None:
    global _PLATT_PARAMS
    _PLATT_PATH.write_text(json.dumps(params, indent=2), encoding="utf-8")
    _PLATT_PARAMS = params


def _apply_platt(p_raw: float) -> float:
    """Transform raw probability via fitted Platt sigmoid. Returns p in (eps,1-eps)."""
    if _PLATT_PARAMS is None:
        return p_raw
    eps = 1e-6
    p_clamped = max(eps, min(1 - eps, float(p_raw)))
    x = math.log(p_clamped / (1.0 - p_clamped))
    z = _PLATT_PARAMS["A"] * x + _PLATT_PARAMS["B"]
    return 1.0 / (1.0 + math.exp(-z))


# ══════════════════════════════════════════════════════════════════════════════
# Tier 1 #2 — Isotonic regression as alternative to Platt.
# Platt assumes monotonic miscalibration (a global sigmoid squash). When the
# reliability diagram shows non-monotonic patterns (e.g., model OK in 50-65%
# range but underconfident in 65-70%), isotonic — which fits any monotonically
# increasing piecewise-linear map — handles it without that assumption.
#
# Storage: a list of (x, y) breakpoints. Apply = linear interp + clip.
# ══════════════════════════════════════════════════════════════════════════════
_ISOTONIC_PARAMS: Optional[dict] = None  # {"breakpoints": [[x0,y0],...], "fitted_at", "n_samples", "brier_before", "brier_after"}
_ISOTONIC_PATH   = Path(__file__).parent / "isotonic_cal.json"


def _load_isotonic() -> None:
    global _ISOTONIC_PARAMS
    try:
        if _ISOTONIC_PATH.exists():
            _ISOTONIC_PARAMS = json.loads(_ISOTONIC_PATH.read_text(encoding="utf-8"))
            n = len(_ISOTONIC_PARAMS.get("breakpoints", []))
            logger.info("Isotonic calibration loaded: %d breakpoints (n_samples=%s)",
                        n, _ISOTONIC_PARAMS.get("n_samples"))
    except Exception as e:
        logger.warning("Isotonic calibration load failed: %s", e)
        _ISOTONIC_PARAMS = None


def _save_isotonic(params: dict) -> None:
    global _ISOTONIC_PARAMS
    _ISOTONIC_PATH.write_text(json.dumps(params, indent=2), encoding="utf-8")
    _ISOTONIC_PARAMS = params


def _apply_isotonic(p_raw: float) -> float:
    """Linear interp through fitted breakpoints; clip outside x-range."""
    if _ISOTONIC_PARAMS is None or not _ISOTONIC_PARAMS.get("breakpoints"):
        return p_raw
    pts = _ISOTONIC_PARAMS["breakpoints"]
    x = float(p_raw)
    if x <= pts[0][0]:
        return float(pts[0][1])
    if x >= pts[-1][0]:
        return float(pts[-1][1])
    # Binary search would be faster, but len(pts) is small (typically <50).
    for i in range(1, len(pts)):
        x0, y0 = pts[i-1]
        x1, y1 = pts[i]
        if x <= x1:
            if x1 == x0:
                return float(y1)
            t = (x - x0) / (x1 - x0)
            return float(y0 + t * (y1 - y0))
    return float(pts[-1][1])


def _fit_isotonic_from_db(conn) -> Optional[dict]:
    """Fit isotonic regression on settled picks. Mirrors _fit_platt_from_db."""
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        logger.warning("sklearn not available for isotonic fit")
        return None

    rows = conn.execute("""
        SELECT model_proj, ci_high, k_line, recommendation, result
          FROM picks
         WHERE recommendation IN ('OVER','UNDER')
           AND result IN ('W','L')
           AND model_proj IS NOT NULL
           AND ci_high    IS NOT NULL
           AND k_line     IS NOT NULL
    """).fetchall()

    probs, outcomes = [], []
    for r in rows:
        std = r["ci_high"] - r["model_proj"] if r["ci_high"] is not None else None
        if r["model_proj"] is None or r["k_line"] is None:
            continue
        poisson_std = math.sqrt(max(float(r["model_proj"]), 1.0))
        eff = max(float(std) if std else 0.0, poisson_std)
        if eff <= 0:
            continue
        z = (float(r["model_proj"]) - float(r["k_line"])) / eff
        p_raw = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        p_raw = max(0.02, min(0.98, p_raw))
        if r["recommendation"] == "OVER":
            over_happened = 1 if r["result"] == "W" else 0
        else:
            over_happened = 0 if r["result"] == "W" else 1
        probs.append(p_raw)
        outcomes.append(over_happened)

    if len(probs) < 30:
        logger.info("Isotonic: not enough settled picks to fit (%d < 30)", len(probs))
        return None

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.02, y_max=0.98)
    iso.fit(probs, outcomes)

    # Extract breakpoints. sklearn exposes the piecewise-constant thresholds.
    xs = [float(x) for x in iso.X_thresholds_]
    ys = [float(y) for y in iso.y_thresholds_]
    breakpoints = list(zip(xs, ys))

    # Brier diagnostics
    brier_before = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)
    cals = [float(iso.predict([p])[0]) for p in probs]
    brier_after  = sum((c - o) ** 2 for c, o in zip(cals, outcomes)) / len(probs)

    return {
        "breakpoints":  [[round(x, 6), round(y, 6)] for x, y in breakpoints],
        "fitted_at":    datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_samples":    len(probs),
        "brier_before": round(brier_before, 4),
        "brier_after":  round(brier_after,  4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Calibration dispatcher. Method is read from settings (default "auto").
#   "platt"    → only Platt
#   "isotonic" → only Isotonic
#   "auto"     → whichever has lower brier_after on the same fit set
#   "off"      → no calibration (use raw p)
# ══════════════════════════════════════════════════════════════════════════════
DEFAULT_CALIBRATION_METHOD = "auto"


def _calibration_method(conn=None) -> str:
    """Read calibration method setting. Defaults to 'auto'."""
    if conn is None:
        # No conn at module level — fall back to default.
        return DEFAULT_CALIBRATION_METHOD
    raw = _get_setting(conn, "calibration_method", DEFAULT_CALIBRATION_METHOD)
    if raw not in ("platt", "isotonic", "auto", "off"):
        return DEFAULT_CALIBRATION_METHOD
    return raw


def _resolve_active_calibration() -> str:
    """Decide which method to actually apply based on stored params + setting.
    Called per-prediction; results are O(1) so no caching needed."""
    # We can't open a DB conn here cheaply (this is called inside _p_over_ks).
    # Instead: at startup or on settings change, set a global hint.
    return _ACTIVE_CAL_METHOD


_ACTIVE_CAL_METHOD = "platt"  # set at startup based on settings + which fits exist


def _refresh_active_calibration(conn) -> str:
    """Re-evaluate which calibration method to use given current settings + fits."""
    global _ACTIVE_CAL_METHOD
    method = _calibration_method(conn)
    if method == "off":
        _ACTIVE_CAL_METHOD = "off"
    elif method == "platt":
        _ACTIVE_CAL_METHOD = "platt" if _PLATT_PARAMS else "off"
    elif method == "isotonic":
        _ACTIVE_CAL_METHOD = "isotonic" if _ISOTONIC_PARAMS else "off"
    else:  # auto
        platt_brier = _PLATT_PARAMS.get("brier_after") if _PLATT_PARAMS else None
        iso_brier   = _ISOTONIC_PARAMS.get("brier_after") if _ISOTONIC_PARAMS else None
        if platt_brier is not None and iso_brier is not None:
            _ACTIVE_CAL_METHOD = "isotonic" if iso_brier < platt_brier else "platt"
        elif platt_brier is not None:
            _ACTIVE_CAL_METHOD = "platt"
        elif iso_brier is not None:
            _ACTIVE_CAL_METHOD = "isotonic"
        else:
            _ACTIVE_CAL_METHOD = "off"
    logger.info("Active calibration: %s (setting=%s, platt=%s, iso=%s)",
                _ACTIVE_CAL_METHOD, method,
                "yes" if _PLATT_PARAMS else "no",
                "yes" if _ISOTONIC_PARAMS else "no")
    return _ACTIVE_CAL_METHOD


def _apply_calibration(p_raw: float) -> float:
    """Single entry point used by _p_over_ks. Dispatches to the active method."""
    if _ACTIVE_CAL_METHOD == "platt":
        return _apply_platt(p_raw)
    if _ACTIVE_CAL_METHOD == "isotonic":
        return _apply_isotonic(p_raw)
    return p_raw  # "off"


def _fit_platt(probs: list[float], outcomes: list[int],
               n_iter: int = 2000, lr: float = 0.05) -> Optional[tuple[float, float]]:
    """Fit Platt (A,B) via gradient descent with pseudo-targets (Platt 1999).

    Args:
      probs:    raw probabilities in (0,1) for class=1 (in our case, "OVER happened")
      outcomes: observed binary labels (1 = OVER happened, 0 = UNDER happened)
      n_iter, lr: optimizer knobs; defaults converge on 134-sample dataset.

    Returns:
      (A, B) tuple, or None if can't fit (empty/single-class data).
    """
    n = len(probs)
    if n < 10:
        return None
    n_pos = sum(1 for o in outcomes if o == 1)
    n_neg = n - n_pos
    if n_pos == 0 or n_neg == 0:
        return None

    # Platt's pseudo-targets — regularizes small-sample fits against overfitting
    y_plus  = (n_pos + 1) / (n_pos + 2)
    y_minus = 1 / (n_neg + 2)
    y_ps = [y_plus if o == 1 else y_minus for o in outcomes]

    eps = 1e-6
    x_arr = [math.log(max(eps, min(1-eps, p)) / (1 - max(eps, min(1-eps, p)))) for p in probs]

    A, B = 1.0, 0.0  # identity transform as starting point
    for _ in range(n_iter):
        dA = 0.0
        dB = 0.0
        for xi, yi in zip(x_arr, y_ps):
            z = A * xi + B
            p_hat = 1.0 / (1.0 + math.exp(-z))
            d = p_hat - yi
            dA += d * xi
            dB += d
        dA /= n
        dB /= n
        A -= lr * dA
        B -= lr * dB

    return A, B


def _fit_platt_from_db(conn) -> Optional[dict]:
    """Fit Platt on all settled actionable picks. Returns meta dict or None."""
    rows = conn.execute("""
        SELECT model_proj, ci_high, k_line, recommendation, result, p_over
          FROM picks
         WHERE recommendation IN ('OVER','UNDER')
           AND result IN ('W','L')
           AND model_proj IS NOT NULL
           AND ci_high    IS NOT NULL
           AND k_line     IS NOT NULL
    """).fetchall()

    # Build (raw_p_over, over_happened) pairs. Raw = pre-Platt.
    probs = []
    outcomes = []
    for r in rows:
        std = r["ci_high"] - r["model_proj"] if r["ci_high"] is not None else None
        # Compute RAW p_over (bypass Platt so we can re-fit)
        if r["model_proj"] is None or r["k_line"] is None:
            continue
        poisson_std = math.sqrt(max(float(r["model_proj"]), 1.0))
        eff = max(float(std) if std else 0.0, poisson_std)
        if eff <= 0:
            continue
        z = (float(r["model_proj"]) - float(r["k_line"])) / eff
        p_raw = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        p_raw = max(0.02, min(0.98, p_raw))
        # Outcome from OVER-perspective
        if r["recommendation"] == "OVER":
            over_happened = 1 if r["result"] == "W" else 0
        else:  # UNDER
            over_happened = 0 if r["result"] == "W" else 1
        probs.append(p_raw)
        outcomes.append(over_happened)

    if len(probs) < 30:
        logger.info("Platt: not enough settled picks to fit (%d < 30)", len(probs))
        return None

    ab = _fit_platt(probs, outcomes)
    if ab is None:
        return None
    A, B = ab

    # Compute before/after Brier for diagnostic
    eps = 1e-6
    def _platt_local(p, A, B):
        pc = max(eps, min(1 - eps, p))
        x = math.log(pc / (1 - pc))
        return 1.0 / (1.0 + math.exp(-(A * x + B)))

    brier_before = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)
    brier_after  = sum((_platt_local(p, A, B) - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)

    params = {
        "A": round(A, 6),
        "B": round(B, 6),
        "fitted_at":    datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "n_samples":    len(probs),
        "brier_before": round(brier_before, 4),
        "brier_after":  round(brier_after,  4),
    }
    return params


# ══════════════════════════════════════════════════════════════════════════════
# Bundle A #3 — De-vig, Expected Value, and fractional Kelly
# ══════════════════════════════════════════════════════════════════════════════
def _implied_prob(odds_decimal: Optional[float]) -> Optional[float]:
    """Raw implied probability from decimal odds (includes vig)."""
    if odds_decimal is None or odds_decimal <= 1:
        return None
    return 1.0 / float(odds_decimal)


def _devig_two_way(odds_yes: Optional[float], odds_no: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    """Given decimal odds for both sides of a two-way market, return the
    normalized (no-vig) probability for each. Assumes proportional devigging,
    which is industry standard for efficient two-way markets.
    """
    p_yes = _implied_prob(odds_yes)
    p_no  = _implied_prob(odds_no)
    if p_yes is None or p_no is None:
        return None, None
    total = p_yes + p_no
    if total <= 0:
        return None, None
    return round(p_yes / total, 4), round(p_no / total, 4)


def _ev_pct(p: Optional[float], odds_decimal: Optional[float]) -> Optional[float]:
    """Expected value per unit staked. Returns a decimal fraction.

    EV = p · (odds - 1) - (1 - p)
    Example: p=0.72, odds=1.91 → EV = 0.72*0.91 - 0.28 = 0.375 → +37.5% per unit.
    Positive = +EV bet (your prob beats the market prob); negative = don't bet.
    """
    if p is None or odds_decimal is None or odds_decimal <= 1:
        return None
    b = float(odds_decimal) - 1.0
    return round(float(p) * b - (1.0 - float(p)), 4)


# Tier 1 #5 — Empirical band boost. The reliability diagram has consistently
# shown the model is UNDERconfident in p_bet ∈ [0.65, 0.72]: predicting ~67%
# while the realized rate is ~79% (n=33, +11.5pp gap). To stop leaving alpha
# on the table in that band, we apply a fraction of 0.35 (vs. the default
# Quarter-Kelly 0.25) when the calibrated probability falls there.
#
# Caveats — read before changing:
#   • Only safe because the gap is sustained across multiple refits. If the
#     reliability diagram changes shape (e.g., bin matches predicted), drop
#     this back to 0.25 to avoid Kelly blow-up on a bias that disappeared.
#   • Wilson 95% CI on 79% from n=33 spans roughly [65%, 93%] — direction is
#     real, magnitude is noisy. 0.35 is conservative vs. the ~0.5 you'd pick
#     if you fully believed the +11.5pp underconfidence.
#   • Re-evaluate at N=400+ picks settled.
KELLY_BAND_BOOST_LO       = 0.65
KELLY_BAND_BOOST_HI       = 0.72
KELLY_BAND_BOOST_FRACTION = 0.35


def _kelly_fraction(p: Optional[float], odds_decimal: Optional[float],
                    fraction: Optional[float] = None,
                    cap: float = 2.0) -> Optional[float]:
    """Fractional Kelly stake as fraction-of-bankroll. Returns 0 when EV ≤ 0.

    full_kelly = (b·p - q) / b  where b = odds - 1, q = 1 - p
    Uses Quarter-Kelly (0.25) by default; bumps to KELLY_BAND_BOOST_FRACTION
    in the 65-72% band where the reliability diagram shows underconfidence.
    Capped at 2 (units of bankroll) to avoid blowups from outlier priors.
    """
    if p is None or odds_decimal is None or odds_decimal <= 1:
        return None
    b = float(odds_decimal) - 1.0
    q = 1.0 - float(p)
    full = (b * float(p) - q) / b
    if full <= 0:
        return 0.0
    # Decide effective fraction: explicit > band-boost > default.
    if fraction is None:
        if KELLY_BAND_BOOST_LO <= float(p) <= KELLY_BAND_BOOST_HI:
            effective = KELLY_BAND_BOOST_FRACTION
        else:
            effective = 0.25
    else:
        effective = float(fraction)
    return round(min(full * effective, float(cap)), 3)


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
    """Compute record, hit_rate, roi_pct, avg_edge, pnl_pesos from picks."""
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

    # Auto-P&L aggregates (pesos). Open = staked but unresolved; Realized =
    # settled picks where pnl_pesos is populated.
    pnl_realized  = round(sum(p["pnl_pesos"]   for p in picks if p.get("pnl_pesos")   is not None), 0)
    staked_total  = round(sum(p["stake_pesos"] for p in picks if p.get("stake_pesos") is not None
                                                            and p.get("recommendation") not in ("SKIP", None)), 0)
    staked_open   = round(sum(p["stake_pesos"] for p in picks if p.get("stake_pesos") is not None
                                                            and p.get("recommendation") not in ("SKIP", None)
                                                            and p.get("result") not in ("W","L","P")), 0)
    pnl_roi_pct   = round(pnl_realized / (staked_total - staked_open) * 100, 2) if (staked_total - staked_open) > 0 else None

    return {
        "record":       record,
        "hit_rate":     hit_rate,
        "roi_pct":      roi_pct,
        "avg_edge":     avg_edge,
        "pnl_pesos":    pnl_realized,
        "staked_pesos": staked_total,
        "staked_open_pesos": staked_open,
        "pnl_roi_pct":  pnl_roi_pct,
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
    odds_decimal: Optional[float] = None
    casino:       Optional[str]   = None
    # Tanda 1 — when True (default), submit_pick_line auto-populates odds and
    # casino from the user's best_book if they didn't supply them. Set False
    # to skip this and force manual entry (used by auto-generate so the user
    # always validates each cuota personally — API prices can be stale by the
    # time they actually open bet365/Betano).
    auto_fill_odds: bool = True


@app.post("/api/picks/lines", status_code=201)
async def submit_pick_line(body: LineInput):
    today      = date.today().isoformat()
    team_norm  = _norm_team(body.team)
    opponent         = None
    home_away        = 0
    park_factor      = 100
    pitcher_id       = None
    game_id_for_lineup: Optional[int] = None

    # Resolve opponent + home/away from today's schedule
    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
        for g in games:
            home_norm = _norm_team(g["home_team"])
            away_norm = _norm_team(g["away_team"])
            if home_norm == team_norm:
                opponent           = away_norm
                home_away          = 1
                park_factor        = _PARK_FACTORS.get(home_norm, 100)
                game_id_for_lineup = g.get("game_id")
                # Also grab pitcher_id if this pitcher is the home starter
                if g["home_pitcher"]["name"].lower() == body.pitcher_name.lower():
                    pitcher_id = g["home_pitcher"]["id"]
                break
            elif away_norm == team_norm:
                opponent           = home_norm
                home_away          = 0
                park_factor        = _PARK_FACTORS.get(home_norm, 100)
                game_id_for_lineup = g.get("game_id")
                if g["away_pitcher"]["name"].lower() == body.pitcher_name.lower():
                    pitcher_id = g["away_pitcher"]["id"]
                break
    except Exception as e:
        logger.warning("Schedule lookup failed for %s: %s", body.team, e)

    # Load training data + live pitching + batting data concurrently
    year = date.today().year
    df, pitch_df, bat_df = await asyncio.gather(
        asyncio.to_thread(_load_training_df),
        asyncio.to_thread(_load_pitching, year),
        asyncio.to_thread(_load_batting, year),
    )
    feats = _pitcher_baseline(df, pitcher_id, body.pitcher_name)
    feats.update(_opp_stats(df, opponent or ""))
    feats.update(_live_opp_discipline(bat_df, opponent or ""))
    feats["k_line"]      = body.k_line
    feats["home_away"]   = home_away
    feats["park_factor"] = park_factor
    feats["month"]       = date.today().month

    # Overlay live Savant stats (today's season numbers) on top of stale CSV
    _p_row = _find_pitcher_row(pitch_df, body.pitcher_name) if pitch_df is not None else None
    if _p_row is not None:
        _live = {
            "k_pct_L15":     safe_col(_p_row, "K%"),
            "csw_pct_L15":   safe_col(_p_row, "CSW%", "CSW"),
            "swstr_pct_L15": safe_col(_p_row, "SwStr%", "SwStr"),
            "stuff_plus":    safe_col(_p_row, "Stuff+", "Stuff"),
            "k9_season":     safe_col(_p_row, "K/9", "SO9"),
        }
        feats.update({k: v for k, v in _live.items() if v is not None})
        logger.info("Picks: live Savant stats overlaid for %s", body.pitcher_name)

    # Lineup K% from today's confirmed batting order
    opp_k_pct_lineup = None
    lineup_confirmed  = 0
    bat_has_k = bat_df is not None and "K%" in bat_df.columns
    logger.info("Lineup check: game_id=%s bat_has_K%%=%s", game_id_for_lineup, bat_has_k)
    if game_id_for_lineup:
        try:
            async with httpx.AsyncClient() as client:
                lineup_data = await _fetch_lineups(client, game_id_for_lineup)
            # opponent's lineup: away if home pitcher, home if away pitcher
            opp_side     = "away" if home_away == 1 else "home"
            opp_lineup   = lineup_data.get(opp_side, [])
            logger.info("Lineup for game %s opp_side=%s players=%d", game_id_for_lineup, opp_side, len(opp_lineup))
            if len(opp_lineup) >= 7:
                # Pitcher hand from MLB API
                p_hand = "R"
                if pitcher_id:
                    try:
                        async with httpx.AsyncClient() as client:
                            people_r = await client.get(
                                f"{MLB_API}/people/{pitcher_id}", timeout=10)
                            people_r.raise_for_status()
                            ppl = people_r.json().get("people", [])
                            if ppl:
                                p_hand = ppl[0].get("pitchHand", {}).get("code", "R") or "R"
                    except Exception:
                        pass
                opp_k_pct_lineup = _lineup_weighted_k_pct(opp_lineup, p_hand, bat_df)
                lineup_confirmed  = 1 if opp_k_pct_lineup is not None else 0
                logger.info("Lineup K%% result: opp_k_pct=%.4f confirmed=%d", opp_k_pct_lineup or 0, lineup_confirmed)
            else:
                logger.info("Lineup not ready yet: only %d batters found (need >=7)", len(opp_lineup))
        except Exception as e:
            logger.info("Lineup K%% lookup failed for game %s: %s", game_id_for_lineup, e)

    feats["opp_k_pct_lineup"] = opp_k_pct_lineup
    feats["lineup_confirmed"]  = lineup_confirmed

    # NOTE: temperature_f and wind_speed_mph are 100% null in training_data.csv
    # (pipeline hardcodes np.nan). Passing real values adds noise to the GBM
    # until the pipeline is backfilled and the model is retrained. Skipped for now.

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
    # Calibrated probability of OVER under Normal(proj, std) — Bundle A #2
    std   = result.get("std_dev")
    p_over  = _p_over_ks(proj, std, body.k_line)
    p_under = round(1 - p_over, 4) if p_over is not None else None

    # ─── Tanda 1 — multi-book odds ingestion ──────────────────────────────────
    # Fetch this pitcher's K-prop quotes across every book in The Odds API,
    # de-vig to get a per-book fair P(OVER), then weighted-average to a
    # market-consensus fair price. Also identify the book offering the highest
    # odds for the recommended side ("best price"), which auto-populates the
    # casino/odds_decimal fields when the user didn't pass them.
    market_data: dict   = {}        # consensus + per-book detail
    market_event_id: Optional[str]  = None
    # User-executable best (filtered to user_books — what they can actually take)
    best_book_casino:  Optional[str]   = None
    best_book_odds:    Optional[float] = None
    # Whole-market best (any book — info display only)
    market_best_casino: Optional[str]   = None
    market_best_odds:   Optional[float] = None
    api_books:        list[dict]    = []
    p_over_market:    Optional[float] = None
    p_under_market:   Optional[float] = None
    n_books_market:   Optional[int] = None
    if _oac is not None and _oac.is_configured() and rec in ("OVER", "UNDER"):
        try:
            with get_db() as _settings_conn:
                user_books_list = _user_books(_settings_conn)
                regions         = _odds_regions(_settings_conn)
            events = await _oac.fetch_events_today()
            event = _oac.find_event_for_pitcher(events, body.team)
            if event:
                market_event_id = event.get("id")
                event_odds = await _oac.fetch_pitcher_props(market_event_id, regions=regions)
                if event_odds:
                    market_data = _oac.extract_pitcher_market(event_odds, body.pitcher_name)
                    api_books = market_data.get("books") or []
                    if api_books:
                        # Consensus uses ALL books at the consensus k-line (broad market signal).
                        consensus = _oac.consensus_fair_price(api_books)
                        p_over_market  = consensus.get("p_over_consensus")
                        p_under_market = consensus.get("p_under_consensus")
                        n_books_market = consensus.get("n_books") or 0
                        market_data["consensus"] = consensus
                        # Whole-market best — informational only.
                        market_bp = _oac.best_price(api_books, rec)
                        if market_bp:
                            market_best_casino = market_bp.get("casino")
                            market_best_odds   = market_bp.get("odds")
                        # User-executable best — only books the user has access to.
                        # Falls back to whole market when user_books is empty (legacy behavior).
                        user_bp = _oac.best_price(api_books, rec, restrict_to=user_books_list or None)
                        if user_bp:
                            best_book_casino = user_bp.get("casino")
                            best_book_odds   = user_bp.get("odds")
                        elif user_books_list:
                            # User has filter but none of their books carry this market.
                            logger.info(
                                "%s: ningún libro del usuario %s tiene mercado disponible (best %s = %s @ %s)",
                                body.pitcher_name, user_books_list, rec, market_best_odds, market_best_casino,
                            )
                        logger.info(
                            "Odds API: %s — %d books @ k=%.1f, consensus p_over=%.4f. "
                            "User best (%s @ %s) · Market best (%s @ %s)",
                            body.pitcher_name, len(api_books),
                            market_data.get("k_line") or float("nan"),
                            p_over_market or float("nan"),
                            best_book_casino or "—", best_book_odds or float("nan"),
                            market_best_casino or "—", market_best_odds or float("nan"),
                        )
        except Exception as e:
            logger.warning("Odds API integration failed for %s: %s", body.pitcher_name, e)

    # Auto-populate odds + casino with the best market price when the user
    # didn't pass them. User input always takes precedence (manual override).
    # Skipped when body.auto_fill_odds=False — auto-generate uses that path
    # so picks come back with line + market refs only, leaving cuota entry
    # to the user (their actual book may show different price than API).
    if body.auto_fill_odds:
        if not body.odds_decimal and best_book_odds is not None:
            body.odds_decimal = best_book_odds
        if not body.casino and best_book_casino:
            body.casino = best_book_casino

    # Bundle A #3 — EV and fractional Kelly sizing (needs user-provided odds)
    # p_bet is the probability of the *side we'd bet* (favored side).
    # Tanda 1 — when consensus is available, use MARKET p_bet (sharper signal
    # than model p_over against bookmaker-set odds). Falls back to model p
    # when no API data exists.
    ev_pct          = None
    ev_market_pct   = None
    kelly_units     = None
    if body.odds_decimal and p_over is not None:
        if rec == "OVER":
            p_bet_model  = p_over
            p_bet_market = p_over_market
        elif rec == "UNDER":
            p_bet_model  = p_under
            p_bet_market = p_under_market
        else:
            # SKIP — still compute on the model's favored side for transparency
            p_bet_model  = max(p_over, p_under)
            p_bet_market = None
        # ev_pct = model-derived (existing semantics, kept for backwards compat).
        ev_pct = _ev_pct(p_bet_model, body.odds_decimal)
        # ev_market_pct = sharper signal — uses market-consensus fair price.
        if p_bet_market is not None:
            ev_market_pct = _ev_pct(p_bet_market, body.odds_decimal)
        # Kelly sizes against the MARKET probability when available; this
        # protects against model overconfidence on unseen distributions.
        p_for_kelly = p_bet_market if p_bet_market is not None else p_bet_model
        kelly_units = _kelly_fraction(p_for_kelly, body.odds_decimal)

    # Upsert into picks table (preserve actual_ks/result if already settled)
    with get_db() as conn:
        # Auto-P&L — stake in pesos = kelly_fraction × bankroll (capped).
        bankroll        = _bankroll_pesos(conn)
        unit_size       = _unit_size_pesos(conn)
        max_frac        = _max_stake_fraction(conn)
        stake_pesos_val = _stake_pesos(kelly_units, bankroll, max_frac)
        # Marginal-pick filter — demote OVER/UNDER → SKIP if EV or stake too small.
        # Only kicks in once odds (and therefore EV/stake) are known. Uses the
        # MARKET EV when available (sharper signal than model EV).
        min_ev    = _min_ev_pct(conn)
        min_stake = _min_stake_pesos(conn)
        ev_for_filter = ev_market_pct if ev_market_pct is not None else ev_pct
        if _should_demote_to_skip(rec, ev_for_filter, stake_pesos_val, min_ev, min_stake):
            logger.info("Demoting %s → SKIP (EV=%.3f stake=%s, floors EV>=%.2f stake>=%s, source=%s)",
                        rec, ev_for_filter or 0, stake_pesos_val, min_ev, min_stake,
                        "market" if ev_market_pct is not None else "model")
            rec = "SKIP"
            stake_pesos_val = None  # don't suggest a stake on demoted picks
        conn.execute("""
            INSERT INTO picks
                (date, pitcher_name, team, opponent, pitcher_id, k_line, model_proj, edge,
                 ci_low, ci_high, pitch_limit_flag, confidence, recommendation,
                 odds_decimal, casino, p_over, ev_pct, kelly_units, stake_pesos,
                 p_over_market, p_under_market, ev_market_pct, n_books_market,
                 best_book_casino, best_book_odds,
                 market_best_casino, market_best_odds,
                 market_event_id,
                 actual_ks, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
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
                recommendation   = excluded.recommendation,
                odds_decimal     = COALESCE(excluded.odds_decimal, odds_decimal),
                casino           = COALESCE(excluded.casino,       casino),
                p_over           = excluded.p_over,
                ev_pct           = COALESCE(excluded.ev_pct,      ev_pct),
                kelly_units      = COALESCE(excluded.kelly_units, kelly_units),
                stake_pesos      = COALESCE(excluded.stake_pesos, stake_pesos),
                p_over_market    = COALESCE(excluded.p_over_market, p_over_market),
                p_under_market   = COALESCE(excluded.p_under_market, p_under_market),
                ev_market_pct    = COALESCE(excluded.ev_market_pct, ev_market_pct),
                n_books_market   = COALESCE(excluded.n_books_market, n_books_market),
                best_book_casino   = COALESCE(excluded.best_book_casino, best_book_casino),
                best_book_odds     = COALESCE(excluded.best_book_odds, best_book_odds),
                market_best_casino = COALESCE(excluded.market_best_casino, market_best_casino),
                market_best_odds   = COALESCE(excluded.market_best_odds, market_best_odds),
                market_event_id  = COALESCE(excluded.market_event_id, market_event_id)
        """, (
            today, body.pitcher_name, body.team, opponent or "",
            pitcher_id,
            body.k_line, proj, edge,
            result["ci_low"], result["ci_high"],
            int(plf), conf, rec,
            body.odds_decimal, body.casino, p_over, ev_pct, kelly_units,
            stake_pesos_val,
            p_over_market, p_under_market, ev_market_pct, n_books_market,
            best_book_casino, best_book_odds,
            market_best_casino, market_best_odds,
            market_event_id,
        ))
        # Snapshot every book's quote for this pick (CLV / audit trail).
        if api_books:
            pick_id_row = conn.execute(
                "SELECT id FROM picks WHERE date = ? AND pitcher_name = ?",
                (today, body.pitcher_name),
            ).fetchone()
            pick_id = pick_id_row["id"] if pick_id_row else None
            now_iso = datetime.utcnow().isoformat(timespec="seconds") + "Z"
            consensus_detail = (market_data.get("consensus") or {}).get("books_detail") or []
            # Map casino_key → consensus row (for vig + p_over_fair + weight)
            detail_by_key = {d.get("casino_key"): d for d in consensus_detail}
            for book in api_books:
                d = detail_by_key.get(book.get("casino_key"), {})
                conn.execute(
                    """INSERT INTO pick_odds_snapshots
                       (pick_id, pitcher_name, date, captured_at, casino_key, casino,
                        point, over_odds, under_odds, vig, p_over_fair, weight)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pick_id, body.pitcher_name, today, now_iso,
                     book.get("casino_key"), book.get("casino"),
                     book.get("point"), book.get("over"), book.get("under"),
                     d.get("vig"), d.get("p_over_fair"), d.get("weight")),
                )
        # Read back the persisted odds/casino (COALESCE preserves old values when null passed)
        _persisted = conn.execute(
            "SELECT odds_decimal, casino FROM picks WHERE date = ? AND pitcher_name = ?",
            (today, body.pitcher_name),
        ).fetchone()
        persisted_odds   = _persisted["odds_decimal"] if _persisted else body.odds_decimal
        persisted_casino = _persisted["casino"]       if _persisted else body.casino
        # Auto-add to Bet Tracker when model recommends a play
        if rec != "SKIP":
            direction_lbl = "Over" if rec == "OVER" else "Under"
            bet_game   = f"{body.pitcher_name} ({body.team} vs {opponent or '?'})"
            bet_market = f"K Props — {direction_lbl} {body.k_line}"
            bet_notes  = f"Model: {proj:.1f} · Edge: {edge:+.2f} · Conf: {conf}/5"
            existing = conn.execute(
                "SELECT id FROM bets WHERE date=? AND game=? AND market=?",
                (today, bet_game, bet_market),
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO bets (date, game, market, odds_decimal, result, clv, units, notes)
                       VALUES (?, ?, ?, ?, NULL, NULL, 1.0, ?)""",
                    (today, bet_game, bet_market, 1.909, bet_notes),
                )
        conn.commit()

    return {
        "date":               today,
        "pitcher_name":       body.pitcher_name,
        "team":               body.team,
        "opponent":           opponent or "",
        "pitcher_id":         pitcher_id,
        "k_line":             body.k_line,
        "model_proj":         proj,
        "edge":               edge,
        "ci_low":             result["ci_low"],
        "ci_high":            result["ci_high"],
        "pitch_limit_flag":   plf,
        "confidence":         conf,
        "recommendation":     rec,
        "odds_decimal":       persisted_odds,
        "casino":             persisted_casino,
        "p_over":             p_over,
        "p_under":            p_under,
        "ev_pct":             ev_pct,
        "kelly_units":        kelly_units,
        "stake_pesos":        stake_pesos_val,
        "bankroll_pesos":     bankroll,
        "unit_size_pesos":    unit_size,
        "lineup_confirmed":   lineup_confirmed,
        "opp_k_pct_lineup":   opp_k_pct_lineup,
        # Tanda 1 — market data
        "p_over_market":      p_over_market,
        "p_under_market":     p_under_market,
        "ev_market_pct":      ev_market_pct,
        "n_books_market":     n_books_market,
        "best_book_casino":   best_book_casino,
        "best_book_odds":     best_book_odds,
        "market_best_casino": market_best_casino,
        "market_best_odds":   market_best_odds,
        "market_event_id":    market_event_id,
        "market_books":       (market_data.get("consensus") or {}).get("books_detail") or [],
    }


# ══════════════════════════════════════════════════════════════════════════════
# Tanda 1 — Odds API quota + status endpoint
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# Tanda 1 — manual cuota validation with Monte Carlo simulation
#
# Flow: user sees "cuota óptima del mercado" (best across all books) as
# reference; checks their actual book (bet365/betano); pastes their cuota
# here. The endpoint returns:
#   • market_data: optimal price + fair price (de-vigged consensus)
#   • user_data:   their odds, EV, Kelly stake at THEIR price
#   • simulation:  distribution of P&L over N hypothetical bets at this edge
#   • verdict:     APOSTÁ / MARGINAL / NO based on filters
# ══════════════════════════════════════════════════════════════════════════════

import random as _random


def _simulate_bet_pnl(p_win: float, odds_decimal: float,
                      stake_pesos: float,
                      n_bets: int = 100, n_trials: int = 5000) -> dict:
    """Monte Carlo: simulates n_trials sequences of n_bets bets, each at
    fixed stake, returning the distribution of cumulative P&L.

    Useful to give the user a *long-run* picture: "if you take this bet
    repeatedly under the same edge, here's the range of outcomes."

    Returns:
      {
        "n_bets":       100,
        "n_trials":     5000,
        "stake_pesos":  2520,
        "expected_pnl": 12450,    # mean of trial endings
        "median_pnl":   12100,
        "p5_pnl":       -8200,    # 5th percentile (bad-luck case)
        "p95_pnl":      32500,    # 95th percentile (good-luck case)
        "p_profit":     0.71,     # fraction of trials ending up
        "win_per_bet":  p_win,
      }
    """
    if (p_win is None or odds_decimal is None
            or odds_decimal <= 1 or stake_pesos <= 0):
        return {}
    profit_per_win = stake_pesos * (float(odds_decimal) - 1.0)
    loss_per_loss  = -stake_pesos
    rng = _random.Random(42)  # deterministic for stable UX

    finals: list[float] = []
    for _ in range(n_trials):
        total = 0.0
        for _ in range(n_bets):
            if rng.random() < float(p_win):
                total += profit_per_win
            else:
                total += loss_per_loss
        finals.append(total)
    finals.sort()

    n = len(finals)
    return {
        "n_bets":       n_bets,
        "n_trials":     n_trials,
        "stake_pesos":  round(stake_pesos, 0),
        "expected_pnl": round(sum(finals) / n, 0),
        "median_pnl":   round(finals[n // 2], 0),
        "p5_pnl":       round(finals[int(n * 0.05)], 0),
        "p95_pnl":      round(finals[int(n * 0.95)], 0),
        "p_profit":     round(sum(1 for f in finals if f > 0) / n, 4),
        "win_per_bet":  round(float(p_win), 4),
    }


class EvaluateBetInput(BaseModel):
    pitcher_name: str
    team:         str
    k_line:       Optional[float] = None  # if None, uses consensus from API
    odds_decimal: float
    casino:       Optional[str]   = None
    n_sim_bets:   int             = 100  # how many bets to project over


@app.post("/api/picks/evaluate-bet")
async def evaluate_manual_bet(body: EvaluateBetInput):
    """3-panel validator: market reference + user's cuota + simulation.

    Doesn't mutate the picks table — pure read-only evaluation. Use this
    when the user wants to test "what would happen if I took this bet at
    this price" before committing. To persist the pick, call /api/picks/lines.
    """
    if _oac is None or not _oac.is_configured():
        raise HTTPException(400, "Odds API no configurada")

    with get_db() as conn:
        bankroll  = _bankroll_pesos(conn)
        max_frac  = _max_stake_fraction(conn)
        min_ev    = _min_ev_pct(conn)
        min_stake = _min_stake_pesos(conn)
        regions   = _odds_regions(conn)

    # Pull market data
    events = await _oac.fetch_events_today()
    event = _oac.find_event_for_pitcher(events, body.team)
    if not event:
        raise HTTPException(404, f"No se encontró partido para team={body.team}")
    event_odds = await _oac.fetch_pitcher_props(event["id"], regions=regions)
    if not event_odds:
        raise HTTPException(404, "No hay odds disponibles para este partido")

    market = _oac.extract_pitcher_market(event_odds, body.pitcher_name)
    api_books = market.get("books") or []
    if not api_books:
        raise HTTPException(404, "Ningún libro ofrece K-prop para este pitcher")

    consensus = _oac.consensus_fair_price(api_books)
    p_over_consensus  = consensus.get("p_over_consensus")
    p_under_consensus = consensus.get("p_under_consensus")
    n_books_consensus = consensus.get("n_books") or 0

    consensus_k_line = market.get("k_line")
    k_line = body.k_line if body.k_line is not None else consensus_k_line

    # Determine direction: we don't have the model rec from this endpoint, but
    # we can infer "the favored side per market consensus" if not provided.
    # For evaluation we use the side the user implicitly bet (if their odds
    # match closer to OVER vs UNDER consensus implied price).
    # Simpler: take the side where the user's cuota gives positive expected
    # value vs market consensus. If both negative, take the higher-EV one.
    ev_if_over  = (p_over_consensus  * (body.odds_decimal - 1) - (1 - p_over_consensus))  if p_over_consensus  is not None else None
    ev_if_under = (p_under_consensus * (body.odds_decimal - 1) - (1 - p_under_consensus)) if p_under_consensus is not None else None

    # User probably bet whichever side they were thinking — both EVs are
    # informative, but for "best-case interpretation" take the higher EV.
    if ev_if_over is not None and ev_if_under is not None:
        if ev_if_over >= ev_if_under:
            direction       = "OVER"
            p_user          = p_over_consensus
            ev_pct          = ev_if_over
        else:
            direction       = "UNDER"
            p_user          = p_under_consensus
            ev_pct          = ev_if_under
    else:
        direction = "OVER" if (ev_if_over or 0) >= (ev_if_under or 0) else "UNDER"
        p_user    = p_over_consensus if direction == "OVER" else p_under_consensus
        ev_pct    = ev_if_over if direction == "OVER" else ev_if_under

    # Best market price + fair price (no-vig)
    market_best = _oac.best_price(api_books, direction)
    # Fair price for that side from consensus
    fair_price  = round(1.0 / p_user, 4) if p_user and p_user > 0 else None
    # User's implied probability and edge over fair
    user_implied = round(1.0 / body.odds_decimal, 4) if body.odds_decimal > 1 else None
    edge_vs_fair_pct = round((p_user - user_implied) * 100, 2) if (p_user and user_implied) else None

    # Kelly stake at user's odds (capped)
    kelly_fraction = _kelly_fraction(p_user, body.odds_decimal)
    stake_pesos    = _stake_pesos(kelly_fraction, bankroll, max_frac) if kelly_fraction else None

    # Verdict against user's filters
    if ev_pct is None or ev_pct < 0:
        verdict      = "NO"
        verdict_msg  = "EV negativo — no apostar."
    elif ev_pct < min_ev:
        verdict      = "MARGINAL"
        verdict_msg  = f"EV +{ev_pct*100:.1f}% por debajo de tu mín ({min_ev*100:.1f}%) — pasar."
    elif stake_pesos is not None and stake_pesos < min_stake:
        verdict      = "MARGINAL"
        verdict_msg  = f"Stake ${stake_pesos:,.0f} por debajo de tu mín (${min_stake:,.0f}) — pasar."
    elif ev_pct >= 0.10:
        verdict      = "STRONG_BET"
        verdict_msg  = f"EV +{ev_pct*100:.1f}% — apostá fuerte."
    else:
        verdict      = "BET"
        verdict_msg  = f"EV +{ev_pct*100:.1f}% — apostable."

    # Run the simulation only when there's a real stake to project
    sim = None
    if stake_pesos is not None and stake_pesos > 0 and p_user is not None:
        sim = _simulate_bet_pnl(p_user, body.odds_decimal, stake_pesos,
                                n_bets=max(20, min(int(body.n_sim_bets), 500)))

    return {
        "pitcher_name": body.pitcher_name,
        "team":         body.team,
        "direction":    direction,
        "k_line":       k_line,
        # Market reference (the "óptima" + fair)
        "market": {
            "consensus_k_line":    consensus_k_line,
            "best_casino":         (market_best or {}).get("casino"),
            "best_odds":           (market_best or {}).get("odds"),
            "fair_price":          fair_price,
            "p_consensus":         round(p_user, 4) if p_user else None,
            "n_books":             n_books_consensus,
        },
        # User's actual price + math at it
        "user": {
            "casino":              body.casino,
            "odds":                body.odds_decimal,
            "implied_prob":        user_implied,
            "edge_vs_fair_pp":     edge_vs_fair_pct,
            "ev_pct":              round(ev_pct, 4) if ev_pct is not None else None,
            "kelly_fraction":      kelly_fraction,
            "stake_pesos":         stake_pesos,
        },
        # Simulation distribution (long-run)
        "simulation":   sim,
        # Verdict against user's filters
        "verdict":      verdict,
        "verdict_msg":  verdict_msg,
        # Filter context
        "filters": {
            "min_ev_pct":     min_ev,
            "min_stake_pesos": min_stake,
            "max_stake_fraction": max_frac,
            "bankroll_pesos": bankroll,
        },
    }


@app.get("/api/odds/debug")
async def debug_odds_for_pitcher(pitcher: str, team: str):
    """Diagnostic endpoint — returns the raw market data for a single pitcher.

    Use this to verify what books are actually returned by The Odds API,
    what k-lines they offer, and what filtering is being applied.

    Example:
      curl 'http://localhost:8002/api/odds/debug?pitcher=Kyle%20Bradish&team=BAL'
    """
    if _oac is None or not _oac.is_configured():
        raise HTTPException(400, "Odds API no configurada")

    with get_db() as conn:
        user_books_list = _user_books(conn)
        regions         = _odds_regions(conn)

    events = await _oac.fetch_events_today()
    event = _oac.find_event_for_pitcher(events, team)
    if not event:
        return {
            "pitcher":          pitcher,
            "team":             team,
            "error":            "no event found for this team",
            "events_count":     len(events),
            "user_books":       user_books_list,
            "odds_regions":     regions,
        }

    event_odds = await _oac.fetch_pitcher_props(event["id"], regions=regions)
    if not event_odds:
        return {
            "pitcher":         pitcher,
            "team":             team,
            "event_id":         event.get("id"),
            "matchup":          f"{event.get('away_team')} @ {event.get('home_team')}",
            "error":            "event_odds returned None",
            "user_books":       user_books_list,
            "odds_regions":     regions,
        }

    market = _oac.extract_pitcher_market(event_odds, pitcher)
    consensus = _oac.consensus_fair_price(market.get("books") or [])
    market_best = _oac.best_price(market.get("books") or [], "OVER")
    user_best   = _oac.best_price(market.get("books") or [], "OVER",
                                   restrict_to=user_books_list or None)

    return {
        "pitcher":          pitcher,
        "team":             team,
        "event_id":         event.get("id"),
        "matchup":          f"{event.get('away_team')} @ {event.get('home_team')}",
        "user_books":       user_books_list,
        "odds_regions":     regions,
        "consensus_k_line": market.get("k_line"),
        "books_at_consensus": market.get("books") or [],
        "books_other_lines":  market.get("rejected_books") or [],
        "consensus":        consensus,
        "market_best_OVER": market_best,
        "user_best_OVER":   user_best,
        "n_bookmakers_in_response": len(event_odds.get("bookmakers", [])),
        "all_bookmaker_keys": [bm.get("key") for bm in event_odds.get("bookmakers", [])],
    }


@app.get("/api/odds/quota")
def get_odds_quota():
    """Latest known The Odds API quota (from response headers).
    Returns null fields when no API call has been made this process lifetime.
    `configured` reports whether THE_ODDS_API_KEY is set in env."""
    if _oac is None:
        return {"configured": False, "used": None, "remaining": None, "last_seen": None}
    state = _oac.quota_state()
    return {
        "configured": _oac.is_configured(),
        "used":       state.get("used"),
        "remaining":  state.get("remaining"),
        "last_seen":  state.get("last_seen"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Tanda 1 bonus — auto-generate the whole slate from market data.
# One-click: fetch consensus K-line + best-price odds for every probable
# starter today, run the model, persist picks. Skips pitchers who already
# have a pick today (preserves user edits / overrides).
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/picks/lines/auto-generate")
async def auto_generate_today_picks(force: bool = False):
    """Iterate today's probable starters; for each, pull the consensus K-line
    and best-price odds from The Odds API, then run submit_pick_line internally.

    Args:
      force: if true, regenerate even pitchers that already have a pick today
             (overwrites user edits). Default false = preserve existing picks.

    Returns: { generated: [...], skipped: [...], n_generated, n_skipped, quota }
    """
    if _oac is None or not _oac.is_configured():
        raise HTTPException(
            400,
            "The Odds API no está configurada (falta ODDS_API_KEY en .env)."
        )

    today = date.today().isoformat()

    # 1) Pull today's probable starters from MLB API
    try:
        async with httpx.AsyncClient() as client:
            games = await _today_games(client)
    except Exception as e:
        logger.error("auto-generate: _today_games failed: %s", e)
        raise HTTPException(500, f"No se pudieron obtener los juegos de hoy: {e}")

    pitchers: list[dict] = []
    for g in games:
        for side, team_key in (("home_pitcher", "home_team"), ("away_pitcher", "away_team")):
            p = g.get(side) or {}
            name = p.get("name")
            if not name:
                continue
            team_norm = _norm_team(g.get(team_key, ""))
            pitchers.append({"pitcher_name": name, "team": team_norm})

    if not pitchers:
        return {
            "generated":   [],
            "skipped":     [],
            "n_generated": 0,
            "n_skipped":   0,
            "quota":       _oac.quota_state(),
            "message":     "No hay starters confirmados para hoy.",
        }

    # 2) Filter / wipe existing picks based on force flag.
    deleted_rows = 0
    if not force:
        # Default: skip pitchers that already have a pick today (preserves user edits).
        with get_db() as conn:
            existing = conn.execute(
                "SELECT pitcher_name FROM picks WHERE date = ?", (today,)
            ).fetchall()
        existing_set = {r["pitcher_name"].lower() for r in existing}
    else:
        # force=true → DELETE today's picks first, so regen runs from a clean
        # slate. Without this, the upsert's COALESCE would preserve old
        # odds_decimal/casino values when the new fetch returns null
        # (e.g., user_books filter excludes all books for that pitcher),
        # leaving stale data in place.
        # Note: we only delete UNSETTLED picks — settled W/L/P are preserved
        # for historical record (you wouldn't want to lose your P&L history).
        with get_db() as conn:
            deleted_rows = conn.execute(
                """DELETE FROM picks
                   WHERE date = ?
                     AND (result IS NULL OR result = '' OR result NOT IN ('W','L','P'))""",
                (today,),
            ).rowcount
            # Cascade-delete the snapshots for those picks (FK ON DELETE CASCADE)
            conn.commit()
        logger.info("auto-generate force=true: wiped %d unsettled picks for %s", deleted_rows, today)
        existing_set = set()

    # 3) Pre-fetch the events list once (shared cache for all pitchers)
    events = await _oac.fetch_events_today()

    generated:    list[dict] = []
    skipped:      list[dict] = []
    seen_event_ids: dict[str, dict] = {}  # event_id → odds payload (per-batch cache)

    for p in pitchers:
        pname = p["pitcher_name"]
        team  = p["team"]

        if pname.lower() in existing_set:
            skipped.append({**p, "reason": "ya tiene pick hoy (usá force=true para regenerar)"})
            continue

        event = _oac.find_event_for_pitcher(events, team)
        if not event:
            skipped.append({**p, "reason": f"no se encontró evento para team={team}"})
            continue

        event_id = event.get("id")
        if event_id in seen_event_ids:
            event_odds = seen_event_ids[event_id]
        else:
            event_odds = await _oac.fetch_pitcher_props(event_id)
            seen_event_ids[event_id] = event_odds

        if not event_odds:
            skipped.append({**p, "reason": "no hay odds disponibles para este evento"})
            continue

        market = _oac.extract_pitcher_market(event_odds, pname)
        k_line = market.get("k_line")
        if k_line is None or not market.get("books"):
            skipped.append({**p, "reason": "ningún book ofrece K-prop para este pitcher"})
            continue

        # 4) Submit the pick using the existing pipeline. We DELIBERATELY
        # leave odds_decimal/casino null AND set auto_fill_odds=False so the
        # pick lands in the DB with line + market refs only — no fake stake
        # or EV based on API prices. The user enters their real cuota in
        # the manual-entry form, which triggers the simulation + verdict.
        try:
            line_input = LineInput(
                pitcher_name   = pname,
                team           = team,
                k_line         = float(k_line),
                auto_fill_odds = False,
            )
            result = await submit_pick_line(line_input)
            generated.append({
                "pitcher_name":   pname,
                "team":           team,
                "k_line":         k_line,
                "rec":            result.get("recommendation"),
                "best_book":      result.get("best_book_casino"),
                "best_odds":      result.get("best_book_odds"),
                "ev_market_pct":  result.get("ev_market_pct"),
                "n_books":        result.get("n_books_market"),
                "stake_pesos":    result.get("stake_pesos"),
            })
        except HTTPException as he:
            skipped.append({**p, "reason": f"submit falló: {he.detail}"})
        except Exception as e:
            logger.warning("auto-generate failed for %s: %s", pname, e)
            skipped.append({**p, "reason": f"error: {str(e)[:100]}"})

    logger.info(
        "auto-generate: %d generated / %d skipped (force=%s, quota=%s)",
        len(generated), len(skipped), force, _oac.quota_state()
    )
    return {
        "generated":   generated,
        "skipped":     skipped,
        "n_generated": len(generated),
        "n_skipped":   len(skipped),
        "n_wiped":     deleted_rows,
        "quota":       _oac.quota_state(),
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

            # Auto-P&L — if we have a stake (or can derive one from kelly)
            # and odds, compute winnings. Triggered on every result update so
            # editing the result (e.g., correcting ks) updates the P&L too.
            stake = pick.get("stake_pesos")
            if stake is None and pick.get("kelly_units") is not None:
                bankroll_p = _bankroll_pesos(conn)
                max_frac_p = _max_stake_fraction(conn)
                stake = _stake_pesos(pick["kelly_units"], bankroll_p, max_frac_p)
            pnl = _pnl_pesos(stake, pick.get("odds_decimal"), result)

            conn.execute(
                """UPDATE picks
                      SET actual_ks   = ?,
                          result      = ?,
                          stake_pesos = COALESCE(stake_pesos, ?),
                          pnl_pesos   = ?
                    WHERE date = ? AND pitcher_name = ?""",
                (body.actual_ks, result, stake, pnl, body.date, body.pitcher_name),
            )
            conn.commit()

        return {
            "date":         body.date,
            "pitcher_name": body.pitcher_name,
            "actual_ks":    body.actual_ks,
            "k_line":       pick["k_line"],
            "recommendation": pick["recommendation"],
            "result":       result,
            "stake_pesos":  stake,
            "pnl_pesos":    pnl,
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
# POST /api/picks/manual — back-fill a pitcher pick for a past date.
# Used when the user forgot to log a pick at the time. No model projection
# runs — all fields are taken from the user. `result` is derived if
# actual_ks + recommendation are given.
# ==============================================================================

class ManualPickInput(BaseModel):
    date:           str                 # YYYY-MM-DD (any past date allowed)
    pitcher_name:   str
    team:           str
    opponent:       Optional[str] = ""
    k_line:         float
    recommendation: str                 # "OVER" | "UNDER" | "SKIP"
    actual_ks:      Optional[float] = None
    odds_decimal:   Optional[float] = None
    casino:         Optional[str]   = None
    units:          Optional[float] = 1.0
    notes:          Optional[str]   = None


@app.post("/api/picks/manual", status_code=201)
def create_manual_pick(body: ManualPickInput):
    rec = body.recommendation.upper().strip()
    if rec not in ("OVER", "UNDER", "SKIP"):
        raise HTTPException(400, "recommendation must be OVER, UNDER, or SKIP")

    # Derive result only if the user supplied an actual K count
    result = None
    if body.actual_ks is not None and rec != "SKIP":
        result = _calc_result(body.actual_ks, body.k_line, rec)

    team_norm = _norm_team(body.team)

    try:
        with get_db() as conn:
            # Upsert into picks (manual entries have no model_proj/edge/CI)
            conn.execute("""
                INSERT INTO picks
                    (date, pitcher_name, team, opponent, pitcher_id, k_line,
                     model_proj, edge, ci_low, ci_high, pitch_limit_flag,
                     confidence, recommendation, odds_decimal, casino,
                     actual_ks, result)
                VALUES (?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, NULL, 0,
                        NULL, ?, ?, ?, ?, ?)
                ON CONFLICT(date, pitcher_name) DO UPDATE SET
                    team           = excluded.team,
                    opponent       = excluded.opponent,
                    k_line         = excluded.k_line,
                    recommendation = excluded.recommendation,
                    odds_decimal   = COALESCE(excluded.odds_decimal, odds_decimal),
                    casino         = COALESCE(excluded.casino, casino),
                    actual_ks      = COALESCE(excluded.actual_ks, actual_ks),
                    result         = COALESCE(excluded.result, result)
            """, (
                body.date, body.pitcher_name, team_norm, body.opponent or "",
                body.k_line, rec, body.odds_decimal, body.casino,
                body.actual_ks, result,
            ))

            # Mirror into bets so the tracker totals reflect it
            if rec != "SKIP":
                direction_lbl = "Over" if rec == "OVER" else "Under"
                bet_game   = f"{body.pitcher_name} ({team_norm} vs {body.opponent or '?'})"
                bet_market = f"K Props — {direction_lbl} {body.k_line}"
                odds = body.odds_decimal if body.odds_decimal and body.odds_decimal > 1 else 1.909
                existing = conn.execute(
                    "SELECT id FROM bets WHERE date=? AND game=? AND market=?",
                    (body.date, bet_game, bet_market),
                ).fetchone()
                if existing:
                    conn.execute(
                        """UPDATE bets SET odds_decimal=?, result=?, units=?, notes=COALESCE(?, notes)
                           WHERE id=?""",
                        (odds, result, body.units or 1.0, body.notes, existing["id"]),
                    )
                else:
                    conn.execute(
                        """INSERT INTO bets (date, game, market, odds_decimal, result, clv, units, notes)
                           VALUES (?, ?, ?, ?, ?, NULL, ?, ?)""",
                        (body.date, bet_game, bet_market, odds, result,
                         body.units or 1.0, body.notes),
                    )
            conn.commit()

        return {
            "ok":             True,
            "date":           body.date,
            "pitcher_name":   body.pitcher_name,
            "team":           team_norm,
            "opponent":       body.opponent,
            "k_line":         body.k_line,
            "recommendation": rec,
            "actual_ks":      body.actual_ks,
            "result":         result,
            "odds_decimal":   body.odds_decimal,
            "casino":         body.casino,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("POST /api/picks/manual error: %s", e)
        raise HTTPException(500, str(e))


# ==============================================================================
# Bundle A #12 — GET /api/picks/calibration
#   Measures whether the model's predicted probabilities match reality.
#     • Brier score: mean((p - outcome)^2); 0 = perfect, 0.25 = coin-flip.
#     • Reliability bins: 50-55, 55-60, ..., 95-100 — predicted vs actual win%.
# ==============================================================================
def _pick_side_probability(pick: dict) -> Optional[float]:
    """Return the probability associated with the side we actually played.
    For an OVER pick: p_over. For UNDER: 1 - p_over. SKIP returns None.
    """
    rec = pick.get("recommendation")
    po  = pick.get("p_over")
    if po is None or rec == "SKIP" or rec is None:
        return None
    if rec == "OVER":
        return float(po)
    if rec == "UNDER":
        return round(1.0 - float(po), 4)
    return None


@app.get("/api/picks/calibration")
def get_picks_calibration():
    """Brier score + reliability bins over all settled, non-SKIP picks."""
    try:
        with get_db() as conn:
            rows = conn.execute(
                """SELECT * FROM picks
                    WHERE recommendation IN ('OVER', 'UNDER')
                      AND result IN ('W', 'L')
                      AND p_over IS NOT NULL
                    ORDER BY date"""
            ).fetchall()

        samples = []  # list of (p_predicted, outcome 0/1, date, pitcher_name)
        for r in rows:
            d = dict(r)
            p = _pick_side_probability(d)
            if p is None:
                continue
            outcome = 1 if d["result"] == "W" else 0
            samples.append({
                "p":            round(p, 4),
                "outcome":      outcome,
                "date":         d["date"],
                "pitcher_name": d["pitcher_name"],
                "recommendation": d["recommendation"],
            })

        n = len(samples)
        if n == 0:
            return {
                "n":            0,
                "brier":        None,
                "bins":         [],
                "samples":      [],
                "baseline":     {"random_50": 0.25, "coinflip_pct": 50.0},
                "interpretation": "Sin picks settled con p_over — esperá a que salgan resultados.",
            }

        brier = round(sum((s["p"] - s["outcome"]) ** 2 for s in samples) / n, 4)

        # Reliability bins: predicted prob grouped into 10-pp buckets starting
        # at 0.50. Below 0.50 shouldn't happen (we'd flip to the other side),
        # but handle it defensively.
        bin_edges = [(0.00, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 0.65),
                     (0.65, 0.70), (0.70, 0.75), (0.75, 0.80), (0.80, 0.85),
                     (0.85, 0.90), (0.90, 0.95), (0.95, 1.01)]
        bins_out = []
        for lo, hi in bin_edges:
            bucket = [s for s in samples if lo <= s["p"] < hi]
            if not bucket:
                continue
            avg_pred = sum(s["p"]        for s in bucket) / len(bucket)
            avg_actual = sum(s["outcome"] for s in bucket) / len(bucket)
            bins_out.append({
                "range_low":     lo,
                "range_high":    hi,
                "n":             len(bucket),
                "avg_predicted": round(avg_pred,   4),
                "avg_actual":    round(avg_actual, 4),
                "gap":           round(avg_actual - avg_pred, 4),
            })

        # Interpretation buckets, anchored to real reference points:
        #   coin-flip (0.25)  ·  vig-inclusive market (~0.21)  ·  sharp bettor (~0.18)
        # Thresholds use a small tolerance so 0.220 doesn't fall off the edge.
        _tol = 1e-4
        # Active method (post Tier 1 #2) — used to phrase the suggestion text.
        _method_human = {
            "platt":    "Platt",
            "isotonic": "Isotonic",
            "auto":     "calibración",
            "off":      "calibración",
        }.get(_ACTIVE_CAL_METHOD, "calibración")
        cal_active = _ACTIVE_CAL_METHOD in ("platt", "isotonic")
        if brier < 0.18 + _tol:
            interp = f"Bien calibrado (Brier {brier:.3f} — mejor que el mercado)."
        elif brier < 0.22 + _tol:
            interp = (f"Calibración decente (Brier {brier:.3f} — cerca del mercado ~0.21, "
                      f"mejor que coin-flip 0.25).")
        elif brier < 0.25:
            base = f"Calibración mejorable (Brier {brier:.3f} — peor que el mercado pero mejor que coin-flip 0.25)."
            interp = base + (f" {_method_human} ya está activa; más picks settled o un retrain pueden ayudar."
                              if cal_active else " Considerá aplicar Platt o Isotonic scaling.")
        else:
            base = f"Mal calibrado (Brier {brier:.3f} — peor que coin-flip 0.25)."
            interp = base + (f" {_method_human} ya está activa — revisá si hay drift de features o re-entrená el modelo."
                              if cal_active else " Aplicá Platt o Isotonic scaling.")

        # Platt calibration metadata (Bundle A #1) — null when no fit yet.
        platt_meta = None
        if _PLATT_PARAMS is not None:
            platt_meta = {
                "A":             _PLATT_PARAMS.get("A"),
                "B":             _PLATT_PARAMS.get("B"),
                "fitted_at":     _PLATT_PARAMS.get("fitted_at"),
                "n_samples":     _PLATT_PARAMS.get("n_samples"),
                "brier_before":  _PLATT_PARAMS.get("brier_before"),
                "brier_after":   _PLATT_PARAMS.get("brier_after"),
            }
        # Isotonic calibration metadata (Tier 1 #2) — null when no fit yet.
        iso_meta = None
        if _ISOTONIC_PARAMS is not None:
            iso_meta = {
                "n_breakpoints": len(_ISOTONIC_PARAMS.get("breakpoints", [])),
                "fitted_at":     _ISOTONIC_PARAMS.get("fitted_at"),
                "n_samples":     _ISOTONIC_PARAMS.get("n_samples"),
                "brier_before":  _ISOTONIC_PARAMS.get("brier_before"),
                "brier_after":   _ISOTONIC_PARAMS.get("brier_after"),
            }
        method_setting = _calibration_method(conn)

        return {
            "n":       n,
            "brier":   brier,
            "bins":    bins_out,
            "samples": samples,
            "baseline": {"random_50": 0.25, "coinflip_pct": 50.0},
            "interpretation": interp,
            "platt":   platt_meta,
            "isotonic": iso_meta,
            "active_method": _ACTIVE_CAL_METHOD,
            "method_setting": method_setting,
        }
    except Exception as e:
        logger.error("GET /api/picks/calibration error: %s", e)
        raise HTTPException(500, str(e))


class CalibrationMethodInput(BaseModel):
    method: str  # "platt" | "isotonic" | "auto" | "off"


@app.put("/api/model/calibration-method")
def put_calibration_method(body: CalibrationMethodInput):
    """Set the user's preference for which calibration method to apply."""
    method = (body.method or "").strip().lower()
    if method not in ("platt", "isotonic", "auto", "off"):
        raise HTTPException(400, "method debe ser 'platt' | 'isotonic' | 'auto' | 'off'")
    try:
        with get_db() as conn:
            _set_setting(conn, "calibration_method", method)
            active = _refresh_active_calibration(conn)
            # Recompute p_over with the new active calibration so existing rows update
            rows = conn.execute("""
                SELECT id, model_proj, ci_high, k_line FROM picks
                 WHERE model_proj IS NOT NULL AND ci_high IS NOT NULL AND k_line IS NOT NULL
            """).fetchall()
            touched = 0
            for r in rows:
                _std = r["ci_high"] - r["model_proj"] if r["ci_high"] is not None and r["model_proj"] is not None else None
                _p   = _p_over_ks(r["model_proj"], _std, r["k_line"])
                if _p is not None:
                    conn.execute("UPDATE picks SET p_over = ? WHERE id = ?", (_p, r["id"]))
                    touched += 1
            conn.commit()
        return {"ok": True, "method": method, "active": active, "recomputed_rows": touched}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("PUT /api/model/calibration-method error: %s", e)
        raise HTTPException(500, str(e))


@app.post("/api/model/calibrate")
def post_model_calibrate():
    """Re-fit BOTH Platt and Isotonic on-demand and re-evaluate which is active.
    Recomputes p_over on all settled picks afterwards so the UI reflects the
    new calibration immediately."""
    try:
        with get_db() as conn:
            platt_params = _fit_platt_from_db(conn)
            iso_params   = _fit_isotonic_from_db(conn)
            if platt_params is None and iso_params is None:
                raise HTTPException(
                    400,
                    "No hay suficientes picks settled (mínimo 30) para ajustar calibración."
                )
            if platt_params:
                _save_platt(platt_params)
            if iso_params:
                _save_isotonic(iso_params)
            active = _refresh_active_calibration(conn)

            # Recompute p_over across the whole table.
            rows = conn.execute("""
                SELECT id, model_proj, ci_high, k_line
                  FROM picks
                 WHERE model_proj IS NOT NULL
                   AND ci_high    IS NOT NULL
                   AND k_line     IS NOT NULL
            """).fetchall()
            touched = 0
            for r in rows:
                _std = r["ci_high"] - r["model_proj"] if r["ci_high"] is not None and r["model_proj"] is not None else None
                _p   = _p_over_ks(r["model_proj"], _std, r["k_line"])
                if _p is not None:
                    conn.execute("UPDATE picks SET p_over = ? WHERE id = ?", (_p, r["id"]))
                    touched += 1
            conn.commit()
            logger.info(
                "Calibration refit via endpoint: active=%s, Platt%s, Iso%s, recomputed %d rows",
                active,
                f" Brier {platt_params['brier_before']:.4f}→{platt_params['brier_after']:.4f}" if platt_params else " skipped",
                f" Brier {iso_params['brier_before']:.4f}→{iso_params['brier_after']:.4f}" if iso_params else " skipped",
                touched,
            )

        return {
            "ok":              True,
            "platt":           platt_params,
            "isotonic":        iso_params,
            "active_method":   active,
            "recomputed_rows": touched,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("POST /api/model/calibrate error: %s", e)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# Bankroll settings + P&L
#   GET  /api/settings/bankroll → bankroll + unit + cap + summary totals
#   PUT  /api/settings/bankroll → partial update of any of those, recompute
#                                 stakes + pnl across all actionable picks.
# ══════════════════════════════════════════════════════════════════════════════
class BankrollInput(BaseModel):
    bankroll_pesos:      Optional[float] = None
    unit_size_pesos:     Optional[float] = None
    max_stake_fraction:  Optional[float] = None  # e.g. 0.03 = 3% hard cap
    min_ev_pct:          Optional[float] = None  # e.g. 0.05 = +5% min EV
    min_stake_pesos:     Optional[float] = None  # e.g. 2000 = $2k min stake
    user_books:          Optional[str]   = None  # csv of casino_keys, e.g. "bet365,betano"
    odds_regions:        Optional[str]   = None  # csv of regions, e.g. "us,us2,eu,uk"


@app.get("/api/settings/bankroll")
def get_bankroll():
    try:
        with get_db() as conn:
            bankroll     = _bankroll_pesos(conn)
            unit         = _unit_size_pesos(conn)
            max_frac     = _max_stake_fraction(conn)
            min_ev       = _min_ev_pct(conn)
            min_stake    = _min_stake_pesos(conn)
            user_books   = _user_books(conn)
            odds_regions = _odds_regions(conn)
            rows = conn.execute("SELECT * FROM picks").fetchall()
            picks = [_pick_row_to_dict(r) for r in rows]
            summary = _picks_summary(picks)
        return {
            "bankroll_pesos":             bankroll,
            "default_bankroll_pesos":     DEFAULT_BANKROLL_PESOS,
            "unit_size_pesos":            unit,
            "default_unit_size":          DEFAULT_UNIT_SIZE_PESOS,
            "max_stake_fraction":         max_frac,
            "default_max_stake_fraction": DEFAULT_MAX_STAKE_FRACTION,
            "min_ev_pct":                 min_ev,
            "default_min_ev_pct":         DEFAULT_MIN_EV_PCT,
            "min_stake_pesos":            min_stake,
            "default_min_stake_pesos":    DEFAULT_MIN_STAKE_PESOS,
            "user_books":                 ",".join(user_books),
            "odds_regions":               odds_regions,
            "pnl_pesos":                  summary["pnl_pesos"],
            "staked_pesos":               summary["staked_pesos"],
            "staked_open_pesos":          summary["staked_open_pesos"],
            "pnl_roi_pct":                summary["pnl_roi_pct"],
        }
    except Exception as e:
        logger.error("GET /api/settings/bankroll error: %s", e)
        raise HTTPException(500, str(e))


@app.put("/api/settings/bankroll")
def put_bankroll(body: BankrollInput):
    """Partial update of bankroll / unit_size / max_stake_fraction / min_ev_pct
    / min_stake_pesos. Recomputes stake_pesos and pnl_pesos across all
    actionable picks; also re-applies the marginal-pick filter to UNSETTLED
    picks (settled picks keep their original recommendation for history)."""
    # Validate any provided fields
    if body.bankroll_pesos is not None:
        if body.bankroll_pesos <= 0 or body.bankroll_pesos > 100_000_000:
            raise HTTPException(400, "bankroll_pesos fuera de rango razonable.")
    if body.unit_size_pesos is not None:
        if body.unit_size_pesos <= 0 or body.unit_size_pesos > 1_000_000:
            raise HTTPException(400, "unit_size_pesos fuera de rango razonable.")
    if body.max_stake_fraction is not None:
        if body.max_stake_fraction <= 0 or body.max_stake_fraction > 0.25:
            raise HTTPException(400, "max_stake_fraction fuera de rango (0, 0.25].")
    if body.min_ev_pct is not None:
        if body.min_ev_pct < 0 or body.min_ev_pct > 0.50:
            raise HTTPException(400, "min_ev_pct fuera de rango [0, 0.50].")
    if body.min_stake_pesos is not None:
        if body.min_stake_pesos < 0 or body.min_stake_pesos > 1_000_000:
            raise HTTPException(400, "min_stake_pesos fuera de rango razonable.")
    if body.user_books is not None:
        # Sanity-check: comma-separated, alphanumerics + underscore only
        cleaned = ",".join(s.strip().lower() for s in body.user_books.split(",") if s.strip())
        if cleaned and not all(c.isalnum() or c in "_-, " for c in body.user_books):
            raise HTTPException(400, "user_books debe ser una lista CSV de keys (ej: 'bet365,betano').")
    if body.odds_regions is not None:
        valid_regions = {"us", "us2", "eu", "uk", "au"}
        regions_list = [r.strip().lower() for r in body.odds_regions.split(",") if r.strip()]
        if not all(r in valid_regions for r in regions_list):
            raise HTTPException(400, f"odds_regions inválida. Válidas: {sorted(valid_regions)}")

    try:
        with get_db() as conn:
            if body.bankroll_pesos is not None:
                _set_setting(conn, "bankroll_pesos", str(float(body.bankroll_pesos)))
            if body.unit_size_pesos is not None:
                _set_setting(conn, "unit_size_pesos", str(float(body.unit_size_pesos)))
            if body.max_stake_fraction is not None:
                _set_setting(conn, "max_stake_fraction", str(float(body.max_stake_fraction)))
            if body.min_ev_pct is not None:
                _set_setting(conn, "min_ev_pct", str(float(body.min_ev_pct)))
            if body.min_stake_pesos is not None:
                _set_setting(conn, "min_stake_pesos", str(float(body.min_stake_pesos)))
            if body.user_books is not None:
                cleaned = ",".join(s.strip().lower() for s in body.user_books.split(",") if s.strip())
                _set_setting(conn, "user_books", cleaned)
            if body.odds_regions is not None:
                cleaned = ",".join(r.strip().lower() for r in body.odds_regions.split(",") if r.strip())
                _set_setting(conn, "odds_regions", cleaned or "us,us2,eu,uk")

            bankroll  = _bankroll_pesos(conn)
            unit      = _unit_size_pesos(conn)
            max_frac  = _max_stake_fraction(conn)
            min_ev    = _min_ev_pct(conn)
            min_stake = _min_stake_pesos(conn)

            rows = conn.execute("""
                SELECT id, kelly_units, ev_pct, odds_decimal, result, recommendation
                  FROM picks
                 WHERE recommendation IN ('OVER','UNDER')
                   AND kelly_units IS NOT NULL
            """).fetchall()
            stake_n   = 0
            pnl_n     = 0
            demoted_n = 0
            for r in rows:
                new_stake = _stake_pesos(r["kelly_units"], bankroll, max_frac)
                if new_stake is None:
                    continue
                # Apply marginal-pick demotion ONLY to unsettled picks (preserve
                # history for settled W/L/P).
                if r["result"] in (None, "") and _should_demote_to_skip(
                        r["recommendation"], r["ev_pct"], new_stake, min_ev, min_stake):
                    conn.execute(
                        "UPDATE picks SET recommendation = 'SKIP', stake_pesos = NULL WHERE id = ?",
                        (r["id"],),
                    )
                    demoted_n += 1
                    continue
                conn.execute(
                    "UPDATE picks SET stake_pesos = ? WHERE id = ?",
                    (new_stake, r["id"]),
                )
                stake_n += 1
                if r["result"] in ("W","L","P"):
                    new_pnl = _pnl_pesos(new_stake, r["odds_decimal"], r["result"])
                    if new_pnl is not None:
                        conn.execute(
                            "UPDATE picks SET pnl_pesos = ? WHERE id = ?",
                            (new_pnl, r["id"]),
                        )
                        pnl_n += 1
            conn.commit()
            logger.info("Bankroll updated: bankroll=$%.0f unit=$%.0f cap=%.2f%% min_ev=%.1f%% min_stake=$%.0f · stake=%d · pnl=%d · demoted=%d",
                        bankroll, unit, max_frac*100, min_ev*100, min_stake,
                        stake_n, pnl_n, demoted_n)

            picks = [_pick_row_to_dict(r) for r in conn.execute("SELECT * FROM picks").fetchall()]
            summary = _picks_summary(picks)

        return {
            "ok":                  True,
            "bankroll_pesos":      bankroll,
            "unit_size_pesos":     unit,
            "max_stake_fraction":  max_frac,
            "min_ev_pct":          min_ev,
            "min_stake_pesos":     min_stake,
            "stake_updated":       stake_n,
            "pnl_updated":         pnl_n,
            "demoted":             demoted_n,
            "pnl_pesos":           summary["pnl_pesos"],
            "staked_pesos":        summary["staked_pesos"],
            "pnl_roi_pct":         summary["pnl_roi_pct"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("PUT /api/settings/bankroll error: %s", e)
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

        # Step 4: Re-fit calibration on the new model's probabilities.
        # The old Platt / Isotonic params were fit against the OLD GBM's raw
        # probabilities — applying them on a freshly-trained model is incoherent.
        # We refit BOTH (Platt + Isotonic) and let _refresh_active_calibration
        # pick whichever now has the lower Brier.
        try:
            with get_db() as conn:
                # Recompute p_over on every settled pick first — we need the new
                # model_proj/std to be reflected in p_over so the calibration fit
                # sees the latest raw probabilities. (Stored model_proj is from
                # the OLD model at submit-time; for refit purposes we recompute
                # using the stored model_proj + Poisson-floor σ pipeline, which
                # is independent of any calibration. This is a good-enough proxy
                # — true freshness would require re-running predictions on the
                # full feature set per pick, which is expensive.)
                # Note: _fit_platt_from_db / _fit_isotonic_from_db both call
                # _p_over_ks-style logic internally, which doesn't depend on the
                # active calibration (they bypass it). So no need to recompute.

                new_platt = _fit_platt_from_db(conn)
                if new_platt:
                    _save_platt(new_platt)
                    logger.info("Retrain: Platt refitted A=%+.4f B=%+.4f (Brier %.4f → %.4f, n=%d)",
                                new_platt["A"], new_platt["B"],
                                new_platt["brier_before"], new_platt["brier_after"],
                                new_platt["n_samples"])

                new_iso = _fit_isotonic_from_db(conn)
                if new_iso:
                    _save_isotonic(new_iso)
                    logger.info("Retrain: Isotonic refitted %d bps (Brier %.4f → %.4f, n=%d)",
                                len(new_iso["breakpoints"]),
                                new_iso["brier_before"], new_iso["brier_after"],
                                new_iso["n_samples"])

                _refresh_active_calibration(conn)
        except Exception as cal_exc:
            logger.warning("Retrain: calibration refit failed: %s", cal_exc)

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


@app.get("/api/model/retrain/schedule")
def get_retrain_schedule():
    if scheduler is None:
        return {"next_run": None, "timezone": "America/Argentina/Buenos_Aires",
                "schedule": "Scheduler not available (apscheduler not installed)"}
    job = scheduler.get_job("weekly_retrain")
    return {
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "timezone": "America/Argentina/Buenos_Aires",
        "schedule": "Every Monday at 3:00 AM",
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
    fallback = _team_stat(bat_df, team, "K%", LEAGUE_AVG_K_PCT, pitcher_hand=hand)
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
    game_iso    = game.get("game_time", "")

    pitch_df, bat_df, train_df = await asyncio.gather(
        asyncio.to_thread(_load_pitching, year),
        asyncio.to_thread(_load_batting, year),
        asyncio.to_thread(_load_training_df),
    )

    # Fetch lineups + weather concurrently
    async with httpx.AsyncClient() as client:
        lineup_data, weather = await asyncio.gather(
            _fetch_lineups(client, game_id),
            _fetch_weather(client, home_team, game_iso),
        )
    weather_factor, weather_detail = _weather_runs_adj(weather)

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
    # Weather rounded into the key so the cached sim refreshes as the forecast
    # updates through the day (bucketing to 0.02 keeps hits high on rounding).
    wx_key = f"{round(weather_factor, 2)}"
    cache_key = f"ou_sim_{game_id}_{ou_line}_{park_factor}_{confirmed_tag}_{wx_key}_{date.today()}"
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

    # Small-sample blending toward league median — denominator raised to 40
    # so early-season starts don't over-credit outlier xFIP readings. A
    # starter with 20 IP now gets 50% signal / 50% league median (was 100%
    # signal), which matches published regression weights for xFIP stability.
    SSS_IP_FLOOR = 40.0
    home_ip = safe_col(home_p_row, "IP", "ip", default=0) if home_p_row is not None else 0
    if home_ip is None or home_ip < SSS_IP_FLOOR:
        blend        = min((home_ip or 0) / SSS_IP_FLOOR, 1.0)
        home_p_xfip  = blend * home_p_xfip + (1 - blend) * LEAGUE_MEDIAN_XFIP

    away_ip = safe_col(away_p_row, "IP", "ip", default=0) if away_p_row is not None else 0
    if away_ip is None or away_ip < SSS_IP_FLOOR:
        blend        = min((away_ip or 0) / SSS_IP_FLOOR, 1.0)
        away_p_xfip  = blend * away_p_xfip + (1 - blend) * LEAGUE_MEDIAN_XFIP

    # Bullpen xFIP — blended effective xFIP for each pitching staff
    # home team scores against: away starter (5 inn) + away bullpen (4 inn)
    # away team scores against: home starter (5 inn) + home bullpen (4 inn)
    home_bullpen_xfip = _team_bullpen_xfip(pitch_df, home_team, home_p_name)
    away_bullpen_xfip = _team_bullpen_xfip(pitch_df, away_team, away_p_name)

    eff_home_xfip = (
        STARTER_INN_SHARE * home_p_xfip
        + BULLPEN_INN_SHARE * (home_bullpen_xfip if home_bullpen_xfip is not None else LEAGUE_AVG_XFIP)
    )
    eff_away_xfip = (
        STARTER_INN_SHARE * away_p_xfip
        + BULLPEN_INN_SHARE * (away_bullpen_xfip if away_bullpen_xfip is not None else LEAGUE_AVG_XFIP)
    )

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
        home_bullpen_xfip is not None,
        away_bullpen_xfip is not None,
        weather is not None,
    ])

    exp_home = round(
        max(
            LEAGUE_AVG_RUNS * (eff_away_xfip / LEAGUE_AVG_XFIP)
            * (home_xwoba / LEAGUE_AVG_XWOBA) * home_k_adj * park_adj * weather_factor
            + HOME_ADVANTAGE,
            0.5,
        ), 2
    )
    exp_away = round(
        max(
            LEAGUE_AVG_RUNS * (eff_home_xfip / LEAGUE_AVG_XFIP)
            * (away_xwoba / LEAGUE_AVG_XWOBA) * away_k_adj * park_adj * weather_factor,
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

    confidence = "high" if data_points >= 5 else "medium" if data_points >= 3 else "low"

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
        "home_bullpen_xfip":   home_bullpen_xfip,
        "away_bullpen_xfip":   away_bullpen_xfip,
        "lineup_confirmed":    lineup_confirmed,
        "home_lineup":         lineup_data.get("home", []) if lineup_confirmed else [],
        "away_lineup":         lineup_data.get("away", []) if lineup_confirmed else [],
        "weather":             weather,
        "weather_factor":      weather_factor,
        "weather_detail":      weather_detail,
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


# ══════════════════════════════════════════════════════════════════════════════
# MATCHUP PICKS — save O/U sim picks → Today's Picks + Bet Tracker
# ══════════════════════════════════════════════════════════════════════════════

_OU_EDGE_THRESHOLD = 0.05   # minimum |edge_pct| to qualify (5%)


class MatchupPickSave(BaseModel):
    game_id:      int
    ou_line:      float
    direction:    str    # "OVER" | "UNDER"
    edge_pct:     float
    avg_total:    float
    confidence:   str
    home_team:    str
    away_team:    str
    home_pitcher: str
    away_pitcher: str
    # Weather snapshot at the moment of sim save (optional — older clients
    # / dome games may omit these). Stored so the locked card can display
    # "wx applied" without re-fetching.
    weather_factor:      Optional[float] = None
    weather_temp_f:      Optional[float] = None
    weather_wind_mph:    Optional[float] = None
    weather_wind_label:  Optional[str]   = None
    weather_roof:        Optional[str]   = None


@app.post("/api/matchup-picks", status_code=201)
def save_matchup_pick(body: MatchupPickSave):
    pass  # all simulations are saved regardless of edge

    today = date.today().isoformat()
    direction_lbl = "Over" if body.direction == "OVER" else "Under"
    bet_game   = f"{body.away_team} @ {body.home_team}"
    bet_market = f"O/U — {direction_lbl} {body.ou_line}"
    bet_notes  = f"MC sim: {body.avg_total:.1f} avg · edge {body.edge_pct * 100:+.1f}%"

    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO matchup_picks
                    (date, game_id, home_team, away_team, home_pitcher, away_pitcher,
                     ou_line, direction, edge_pct, avg_total, confidence,
                     weather_factor, weather_temp_f, weather_wind_mph,
                     weather_wind_label, weather_roof)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, game_id) DO UPDATE SET
                    ou_line=excluded.ou_line, direction=excluded.direction,
                    edge_pct=excluded.edge_pct, avg_total=excluded.avg_total,
                    confidence=excluded.confidence,
                    weather_factor=excluded.weather_factor,
                    weather_temp_f=excluded.weather_temp_f,
                    weather_wind_mph=excluded.weather_wind_mph,
                    weather_wind_label=excluded.weather_wind_label,
                    weather_roof=excluded.weather_roof
            """, (
                today, body.game_id, body.home_team, body.away_team,
                body.home_pitcher, body.away_pitcher,
                body.ou_line, body.direction, body.edge_pct, body.avg_total,
                body.confidence,
                body.weather_factor, body.weather_temp_f, body.weather_wind_mph,
                body.weather_wind_label, body.weather_roof,
            ))
            # Also add to Bet Tracker (skip if already inserted today for this game/market)
            existing = conn.execute(
                "SELECT id FROM bets WHERE date=? AND game=? AND market=?",
                (today, bet_game, bet_market),
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO bets (date, game, market, odds_decimal, result, clv, units, notes)
                       VALUES (?, ?, ?, ?, NULL, NULL, 1.0, ?)""",
                    (today, bet_game, bet_market, 1.909, bet_notes),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM matchup_picks WHERE date=? AND game_id=?",
                (today, body.game_id),
            ).fetchone()
        return dict(row)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("POST /api/matchup-picks error: %s", e)
        raise HTTPException(500, str(e))


@app.get("/api/matchup-picks/today")
def get_matchup_picks_today():
    today = date.today().isoformat()
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM matchup_picks WHERE date=? ORDER BY ABS(edge_pct) DESC",
                (today,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("GET /api/matchup-picks/today error: %s", e)
        raise HTTPException(500, str(e))


@app.get("/api/matchup-picks/history")
def get_matchup_picks_history():
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM matchup_picks ORDER BY date DESC, id DESC"
            ).fetchall()
        picks = [dict(r) for r in rows]

        settled  = [p for p in picks if p.get("result")]
        wins     = [p for p in settled if p["result"] == "W"]
        losses   = [p for p in settled if p["result"] == "L"]
        pushes   = [p for p in settled if p["result"] == "P"]
        edges    = [p["edge_pct"] for p in picks if p.get("edge_pct") is not None]

        summary = {
            "record":   f"{len(wins)}-{len(losses)}-{len(pushes)}",
            "total":    len(picks),
            "settled":  len(settled),
            "wins":     len(wins),
            "losses":   len(losses),
            "pushes":   len(pushes),
            "hit_rate": round(len(wins) / len(settled), 3) if settled else None,
            "avg_edge": round(sum(edges) / len(edges) * 100, 2) if edges else None,
        }
        return {"picks": picks, "summary": summary}
    except Exception as e:
        logger.error("GET /api/matchup-picks/history error: %s", e)
        raise HTTPException(500, str(e))


@app.delete("/api/matchup-picks/{game_id}", status_code=200)
def delete_matchup_pick(game_id: int):
    today = date.today().isoformat()
    try:
        with get_db() as conn:
            # Remove from matchup_picks
            conn.execute("DELETE FROM matchup_picks WHERE date=? AND game_id=?", (today, game_id))
            # Remove from bets tracker if it was saved there too
            home_away_rows = conn.execute(
                "SELECT game FROM bets WHERE date=? AND notes LIKE '%MC sim%'", (today,)
            ).fetchall()
            conn.commit()
        return {"ok": True}
    except Exception as e:
        logger.error("DELETE /api/matchup-picks/%s error: %s", game_id, e)
        raise HTTPException(500, str(e))


class MatchupResultInput(BaseModel):
    game_id:      int
    actual_total: float


@app.post("/api/matchup-picks/result")
def submit_matchup_result(body: MatchupResultInput):
    today = date.today().isoformat()
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM matchup_picks WHERE date=? AND game_id=?",
                (today, body.game_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, "Matchup pick not found for today")
            pick = dict(row)
            ou_line   = pick["ou_line"]
            direction = pick["direction"]
            if body.actual_total == ou_line:
                result = "P"
            elif direction == "OVER":
                result = "W" if body.actual_total > ou_line else "L"
            else:
                result = "W" if body.actual_total < ou_line else "L"
            conn.execute(
                "UPDATE matchup_picks SET actual_total=?, result=? WHERE date=? AND game_id=?",
                (body.actual_total, result, today, body.game_id),
            )
            conn.commit()
        return {"game_id": body.game_id, "actual_total": body.actual_total, "result": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("POST /api/matchup-picks/result error: %s", e)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# NBA TAB — GET /api/altlines/edges
# AltLines long-tail player-prop engine (packages/altlines, repo root)
# ══════════════════════════════════════════════════════════════════════════════
# Completely standalone from the MLB strikeout model: its own data, its own
# Monte Carlo engine, its own cache. Imported lazily INSIDE the handler so a
# problem in the AltLines package can never stop the MLB API from booting.
#
# Declared as a plain `def` on purpose: the work is CPU-bound (thousands of
# simulations per player-market), so FastAPI runs it in its threadpool instead
# of blocking the event loop the MLB endpoints share.

@app.get("/api/altlines/edges")
def get_altlines_edges(
    n_iter: int = 20000,
    top_pct: float = 5.0,
    metric: str = "ev",
    devig: str = "multiplicative",
    max_odds: float = 50.0,
    min_ev: float = 0.0,
    halflife: float = 6.0,
    seed: int | None = None,
    game: str | None = None,
    date: str | None = None,
    offline: bool = False,
    refresh: bool = False,
    source: str = "demo",
    calibrated: bool | None = None,
    refresh_odds: bool = False,
):
    """Return the AltLines edge board as JSON for the NBA tab.

    `calibrated` applies the walk-forward tail calibration (None = config
    default). `refresh` rebuilds the board; only `refresh_odds` re-downloads
    the real odds (credits).

    `source=live` prices the real alt trays (The Odds API — spends ~5 credits
    per game, cached 20 min) against real ESPN game logs.

    `game` restricts the simulation to one game of the slate (the "por partido"
    view). Same seed + same engine ⇒ identical probabilities to the league-wide
    run, just faster.
    """
    try:
        from altlines.api import build_payload
    except Exception as e:                                  # noqa: BLE001
        logger.error("AltLines package unavailable: %s", e)
        raise HTTPException(500, f"AltLines engine unavailable: {e}")

    try:
        return build_payload(
            n_iter=n_iter, top_pct=top_pct, metric=metric,
            devig_method=devig, max_odds=max_odds, min_ev=min_ev,
            halflife=halflife, seed=seed, refresh=refresh,
            game=game, date=date, offline=offline,
            live=(source == "live"), calibrate=calibrated,
            refresh_odds=refresh_odds,
        )
    except Exception as e:                                  # noqa: BLE001
        logger.error("GET /api/altlines/edges error: %s", e)
        raise HTTPException(500, str(e))


@app.get("/api/altlines/results")
def get_altlines_results(game: str, settle: bool = True):
    """Picks guardados de un partido (modo real) con su resultado: liquida contra
    el box score de ESPN si el partido ya terminó. No gasta créditos."""
    try:
        from altlines.api import results_payload
        return results_payload(game, settle_now=settle)
    except Exception as e:                                  # noqa: BLE001
        logger.error("GET /api/altlines/results error: %s", e)
        raise HTTPException(500, str(e))


@app.get("/api/altlines/slate")
def get_altlines_slate(
    date: str | None = None,
    offline: bool = False,
    n_games: int = 6,
    seed: int | None = None,
    refresh: bool = False,
    direction: int = 0,
    source: str = "demo",
):
    """Tonight's NBA games (real calendar) + what we can price in each.

    Deliberately does NOT simulate: drawing the grid of games must be instant so
    the user can pick which game to run. Also a plain `def` for the same reason
    as above (it reads CSVs / generates the demo dataset).

    `direction` (-1 / 0 / +1) is the arrow the user pressed; it only steers the
    search when the requested day has no games.
    """
    try:
        from altlines.api import slate_payload
    except Exception as e:                                  # noqa: BLE001
        logger.error("AltLines package unavailable: %s", e)
        raise HTTPException(500, f"AltLines engine unavailable: {e}")

    try:
        return slate_payload(date=date, offline=offline, n_games=n_games,
                             seed=seed, refresh=refresh, direction=direction,
                             live=(source == "live"))
    except Exception as e:                                  # noqa: BLE001
        logger.error("GET /api/altlines/slate error: %s", e)
        raise HTTPException(500, str(e))


# ══════════════════════════════════════════════════════════════════════════════
# FÚTBOL TAB — GET /api/futbol/jornada
# Modelo de remates y remates al arco (paquete futbol/, raíz del repo)
# ══════════════════════════════════════════════════════════════════════════════
# Igual que AltLines: import perezoso dentro del handler (un problema en el
# paquete futbol/ nunca impide que arranque la API de MLB) y `def` común para
# que la simulación corra en el threadpool.
#
#   (sin parámetros)      lee data/futbol/jornada.json (instantáneo)
#   refresh=true          vuelve a correr la jornada (ESPN + simulación)
#   refresh_odds=true     además pide cuotas nuevas a OddsPapi (3 consultas)
#
# Ojo: con refresh=true, si el caché de cuotas tiene más de ODDS_TTL_S el
# paquete las vuelve a pedir igual. `odds.would_fetch` se lo avisa a la UI
# para que pida confirmación antes.

import threading as _fb_threading

_fb_lock = _fb_threading.Lock()


def _futbol_odds_meta():
    from futbol import config as fcfg
    from futbol import oddspapi as fop
    ids = "_".join(str(v["oddspapi"]) for v in fcfg.LEAGUES.values())
    now = time.time()
    books, would_fetch = {}, False
    for book in ("bet365.bet.ar", "betano", "betsson"):
        f = fop.DIR / f"odds_{book}_{ids}.json"
        age = (now - f.stat().st_mtime) if f.exists() else None
        books[book] = None if age is None else round(age)
        if age is None or age >= fcfg.ODDS_TTL_S:
            would_fetch = True
    return {"ttl_s": fcfg.ODDS_TTL_S, "age_s": books, "would_fetch": would_fetch,
            "cost_per_refresh": 3, "plan_month": 250,
            "has_key": bool(fcfg.oddspapi_key())}


@app.get("/api/futbol/jornada")
def get_futbol_jornada(refresh: bool = False, refresh_odds: bool = False,
                       days: int = 4, leagues: str | None = None):
    try:
        from futbol import config as fcfg
        from futbol import photos as fphotos
        from futbol import run as frun
    except Exception as e:                                  # noqa: BLE001
        logger.error("futbol package unavailable: %s", e)
        raise HTTPException(500, f"Modelo de fútbol no disponible: {e}")

    f = fcfg.DATA_DIR / "jornada.json"
    try:
        if refresh or refresh_odds or not f.exists():
            if not _fb_lock.acquire(blocking=False):
                raise HTTPException(409, "Ya se está recalculando la jornada")
            try:
                lg = [x for x in (leagues or "").split(",") if x] or None
                data = frun.run(lg, days=days, refresh_odds=refresh_odds, verbose=False)
            finally:
                _fb_lock.release()
        else:
            data = json.loads(f.read_text(encoding="utf-8"))
        # Fotos buscadas después de correr la jornada (python -m futbol.photos)
        store = fphotos._load()
        for m in data.get("matches", []):
            for p in m.get("players", []):
                e = store.get(str(p["player_id"])) or {}
                if e.get("url") and (e.get("src") == "api-football" or not p.get("photo")):
                    p["photo"] = e["url"]
        data["odds"] = _futbol_odds_meta()
        data["running"] = _fb_lock.locked()
        return data
    except HTTPException:
        raise
    except Exception as e:                                  # noqa: BLE001
        logger.error("GET /api/futbol/jornada error: %s", e)
        raise HTTPException(500, str(e))


# "Simular partido": vuelve a simular un solo partido (formación al día de
# ESPN, cuotas guardadas: no gasta OddsPapi).
@app.get("/api/futbol/simulate")
def simulate_futbol_match(event_id: str):
    try:
        from futbol import run as frun
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(500, f"Modelo de fútbol no disponible: {e}")
    if not _fb_lock.acquire(blocking=False):
        raise HTTPException(409, "Ya se está calculando algo, probá en un minuto")
    _fb_status.update(running=event_id, since=time.time())
    try:
        return frun.simulate_one(event_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:                                  # noqa: BLE001
        logger.error("GET /api/futbol/simulate error: %s", e)
        raise HTTPException(500, str(e))
    finally:
        _fb_status.update(running=None, since=None)
        _fb_lock.release()


# Vigía de formaciones: el servidor pregunta solo cada 2 minutos (ESPN y, para
# la LPF, API-Football) aunque nadie tenga la app abierta, y re-simula el
# partido apenas sale la formación. También liquida los picks terminados.
FB_WATCH_EVERY_S = 120
_fb_photo_last = [0.0]


def _fb_photos_step(max_teams: int | None = None) -> int:
    from futbol import apifootball as faf
    from futbol import config as fcfg
    from futbol import espn as fespn
    f = fcfg.DATA_DIR / "jornada.json"
    if not f.exists() or not faf.key():
        return 0
    data = json.loads(f.read_text(encoding="utf-8"))
    _, players = fespn.build_history(list(fcfg.LEAGUES), verbose=False)
    kw = {"max_teams": max_teams} if max_teams else {}
    n = faf.photos_step(data.get("matches", []), players, **kw)
    if n:
        logger.info("Fotos de API-Football: %d jugadores", n)
    return n


def _fb_watch_loop():
    import time as _t
    while True:
        _t.sleep(FB_WATCH_EVERY_S)
        if not _fb_lock.acquire(blocking=False):
            continue
        try:
            from futbol import picklog as fpl
            from futbol import run as frun
            upd = frun.check_lineups()
            if upd:
                logger.info("Formaciones nuevas: %s", ", ".join(f"{u['match']} ({u.get('source')})" for u in upd))
            fpl.settle()
            # Fotos de API-Football: un tramo de planteles por hora (con tope diario).
            if _t.time() - _fb_photo_last[0] > 3600:
                _fb_photo_last[0] = _t.time()
                _fb_photos_step()
        except Exception as e:                              # noqa: BLE001 - el vigía nunca se cae
            logger.warning("Vigía de formaciones: %s", e)
        finally:
            _fb_lock.release()


@app.on_event("startup")
async def _fb_start_watch():
    _fb_threading.Thread(target=_fb_watch_loop, name="fb-lineups", daemon=True).start()


# Consumo de las APIs pagas/limitadas, para el visor de créditos de la barra.
#  · API-Football: /status no gasta cupo → dato exacto del día.
#  · The Odds API: /v4/sports no gasta créditos y trae used/remaining en cabeceras.
#  · OddsPapi: no expone su consumo → lo contamos nosotros (desde que se activó).
_fb_usage_cache = {"t": 0.0, "data": None}


@app.get("/api/usage")
def get_api_usage(fresh: bool = False):
    if not fresh and _fb_usage_cache["data"] and time.time() - _fb_usage_cache["t"] < 300:
        return _fb_usage_cache["data"]
    import httpx as _hx
    out = {}
    try:
        from futbol import apifootball as faf
        k = faf.key()
        if k:
            r = _hx.get(f"{faf.BASE}/status", headers={"x-apisports-key": k}, timeout=15)
            req = ((r.json() or {}).get("response") or {}).get("requests") or {}
            out["apifootball"] = {"name": "API-Football", "used": req.get("current"), "limit": req.get("limit_day"),
                                  "period": "hoy", "exact": True, "use": "Formaciones LPF y fotos"}
    except Exception as e:                                  # noqa: BLE001
        out["apifootball"] = {"name": "API-Football", "error": str(e)}
    try:
        from futbol import oddspapi as fop
        u = fop.usage()
        out["oddspapi"] = {"name": "OddsPapi", **u, "use": "Cuotas de remates (fútbol)"}
    except Exception as e:                                  # noqa: BLE001
        out["oddspapi"] = {"name": "OddsPapi", "error": str(e)}
    try:
        k = os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")
        if k:
            r = _hx.get("https://api.the-odds-api.com/v4/sports", params={"apiKey": k}, timeout=15)
            used, rem = r.headers.get("x-requests-used"), r.headers.get("x-requests-remaining")
            if used is not None and rem is not None:
                out["theoddsapi"] = {"name": "The Odds API", "used": int(float(used)), "limit": int(float(used)) + int(float(rem)),
                                     "period": "mes", "exact": True, "use": "Cuotas MLB y NBA"}
    except Exception as e:                                  # noqa: BLE001
        out["theoddsapi"] = {"name": "The Odds API", "error": str(e)}
    _fb_usage_cache.update(t=time.time(), data=out)
    return out


# Estado liviano para que todos los dispositivos (PC, celular) se enteren de
# los cambios sin bajar la jornada entera: versión de la jornada y de las
# preferencias, y qué partido se está simulando ahora.
_fb_status = {"running": None, "since": None}


def _fb_mtime(name: str) -> float | None:
    from futbol import config as fcfg
    f = fcfg.DATA_DIR / name
    return f.stat().st_mtime if f.exists() else None


@app.get("/api/futbol/status")
def get_futbol_status():
    return {"version": _fb_mtime("jornada.json"), "prefs_version": _fb_mtime("prefs.json"),
            "running": _fb_status["running"], "running_since": _fb_status["since"],
            "busy": _fb_lock.locked(), "now": time.time()}


# Preferencias compartidas entre dispositivos: partidos con estrella y boleta.
@app.get("/api/futbol/prefs")
def get_futbol_prefs():
    from futbol import config as fcfg
    f = fcfg.DATA_DIR / "prefs.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:                                       # noqa: BLE001
        data = {}
    return {"fav": data.get("fav", []), "slip": data.get("slip", []), "version": _fb_mtime("prefs.json")}


class _FbPrefs(BaseModel):
    fav: list[str] = []
    slip: list[str] = []


@app.put("/api/futbol/prefs")
def put_futbol_prefs(p: _FbPrefs):
    from futbol import config as fcfg
    fcfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = fcfg.DATA_DIR / "prefs.json"
    f.write_text(json.dumps({"fav": sorted(set(p.fav))[:500], "slip": sorted(set(p.slip))[:500]}), encoding="utf-8")
    return {"ok": True, "version": _fb_mtime("prefs.json")}


# Resultados de los picks de fútbol: liquida contra ESPN lo que ya terminó y
# devuelve el récord (data/futbol/picks.db, lo llena futbol.run).
@app.get("/api/futbol/results")
def get_futbol_results(days: int = 10):
    try:
        from futbol import picklog
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(500, f"Modelo de fútbol no disponible: {e}")
    try:
        settled = picklog.settle()
    except Exception as e:                                  # noqa: BLE001
        logger.error("futbol settle error: %s", e)
        settled = 0
    return {"settled_now": settled, "summary": picklog.summary(),
            "summary_recent": picklog.summary(days), "picks": picklog.recent(days)}


# Formaciones: ESPN las publica ~1 h antes. La UI llama esto cada pocos
# minutos; sólo consulta ESPN por los partidos que empiezan en las próximas
# 2 h y re-simula los que confirmaron formación. No gasta cuotas.
@app.get("/api/futbol/lineups/check")
def check_futbol_lineups():
    try:
        from futbol import run as frun
    except Exception as e:                                  # noqa: BLE001
        raise HTTPException(500, f"Modelo de fútbol no disponible: {e}")
    if not _fb_lock.acquire(blocking=False):
        return {"updated": [], "busy": True}
    try:
        updated = frun.check_lineups()
    except Exception as e:                                  # noqa: BLE001
        logger.error("GET /api/futbol/lineups/check error: %s", e)
        raise HTTPException(500, str(e))
    finally:
        _fb_lock.release()
    return {"updated": updated, "busy": False,
            "checked_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
