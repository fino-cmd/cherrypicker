"""
AltLines — pick log (validation against real results).

Every REAL-mode run (CLI `--live` or `/api/altlines/edges?source=live`)
persists its whole value table here, so the lines can later be settled against
the ESPN box score (`python -m altlines.tools.settle`) and measured
(`python -m altlines.tools.report_live`).

Storage: `data/altlines/picks.db` (SQLite, out of git), separate from the MLB
tracker on purpose.

    runs   one row per pipeline run (params, slate date, how many lines)
    lines  one row per priced line of that run:
           (run_id, game_id, player_id, market, side, point, book) is UNIQUE,
           so the same quote is never stored twice within a run. The same line
           seen by a LATER run is a new row: that is what gives CLV its
           snapshots. `actual` / `outcome` / `pnl` are filled by settle.
           `pst_score` / `pst_tier` keep the Pickstachio tier the board showed
           (gold = Pistacho Dorado, open = Pistacho Abierto, shell = Cáscara
           Dura) and `extra` the card fields (JSON), so a finished game can be
           reopened from here after the books pulled their lines.

Logging must never break a run: every failure here is caught and logged.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from altlines import config as cfg

logger = logging.getLogger(__name__)

PICKS_DB = cfg.DATA_DIR / "picks.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts      TEXT NOT NULL,              -- UTC ISO, when the run priced
    origin      TEXT,                       -- cli | api
    slate_date  TEXT,
    game_filter TEXT,
    n_iter      INTEGER,
    seed        INTEGER,
    devig       TEXT,
    top_pct     REAL,
    metric      TEXT,
    n_lines     INTEGER,
    n_top       INTEGER
);
CREATE TABLE IF NOT EXISTS lines (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(run_id),
    run_ts       TEXT NOT NULL,
    game_id      TEXT NOT NULL,
    game_date    TEXT,
    tip_off      TEXT,                      -- UTC ISO from the ESPN slate
    player_id    TEXT NOT NULL,             -- ESPN-<athlete id>
    player       TEXT,
    team         TEXT,
    market       TEXT NOT NULL,
    side         TEXT NOT NULL,             -- OVER | UNDER
    point        REAL NOT NULL,
    book         TEXT NOT NULL,
    decimal_odds REAL,
    p_model      REAL,
    p_fair       REAL,
    p_curve      REAL,
    ev           REAL,
    main_line    REAL,
    anchor_scale REAL,
    level_ok     INTEGER,
    in_top       INTEGER,                   -- 1 = in that run's top-X% shortlist
    pst_score    INTEGER,                   -- Pickstachio Score 0-100 (altlines/tiers.py)
    pst_tier     TEXT,                      -- gold | open | shell
    extra        TEXT,                      -- JSON: the card fields (profile, sim_*, stake…)
    snapshot_ts  TEXT,                      -- the book's last_update for the quote
    actual       REAL,                      -- settled stat (NULL until settled / DNP)
    outcome      TEXT,                      -- win | loss | push | void
    pnl          REAL,                      -- per 1 unit staked
    settled_ts   TEXT,
    UNIQUE (run_id, game_id, player_id, market, side, point, book)
);
CREATE INDEX IF NOT EXISTS ix_lines_game ON lines(game_id);
CREATE INDEX IF NOT EXISTS ix_lines_unsettled ON lines(outcome);
"""

_LINE_COLS = ["game_id", "game_date", "tip_off", "player_id", "player", "team",
              "market", "side", "point", "book", "decimal_odds", "p_model",
              "p_fair", "p_curve", "ev", "main_line", "anchor_scale", "level_ok",
              "in_top", "snapshot_ts", "pst_score", "pst_tier", "extra"]
# Columns added after the first version of the table: ALTER'd in on connect.
_ADDED_COLS = {"pst_score": "INTEGER", "pst_tier": "TEXT", "extra": "TEXT"}
# Card fields kept in `extra` (everything the web panel needs besides the
# columns above).
_EXTRA_FIELDS = ["market_label", "profile", "sim_mean", "sim_median", "sim_skew",
                 "sim_cv", "curve_ratio", "discrepancy", "ev_pct", "kelly",
                 "stake_units", "fair_odds", "vig", "level_gap", "p_model_raw"]
_KEY = ["game_id", "player_id", "market", "side", "point", "book"]


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or PICKS_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    _migrate(con)
    return con


def _migrate(con: sqlite3.Connection) -> None:
    """Bring an older picks.db up to date: add the new columns and fill the
    tier of lines logged before tiers existed (every logged run is real mode,
    i.e. anchored)."""
    have = {r[1] for r in con.execute("PRAGMA table_info(lines)")}
    for col, typ in _ADDED_COLS.items():
        if col not in have:
            con.execute(f"ALTER TABLE lines ADD COLUMN {col} {typ}")
    todo = con.execute("SELECT id, p_model, p_fair, p_curve, anchor_scale FROM lines "
                       "WHERE pst_tier IS NULL AND p_model IS NOT NULL AND p_fair IS NOT NULL").fetchall()
    if todo:
        from altlines.tiers import pistachio_score, tier_of
        upd = []
        for r in todo:
            ratio = (r["p_model"] / r["p_curve"]) if r["p_curve"] else None
            sc = pistachio_score(r["p_model"], r["p_fair"], ratio, r["anchor_scale"], True)
            upd.append((sc, tier_of(sc), r["id"]))
        con.executemany("UPDATE lines SET pst_score = ?, pst_tier = ? WHERE id = ?", upd)
        con.commit()


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_rows(table: pd.DataFrame, odds: pd.DataFrame,
               shortlist: pd.DataFrame | None, slate: dict | None) -> pd.DataFrame:
    """Value table + the columns it dropped (game_id, snapshot_ts) + top flag."""
    if table is None or table.empty:
        return pd.DataFrame(columns=_LINE_COLS)
    df = table.copy()
    df["side"] = df["side"].astype(str).str.upper()
    df["point"] = df["point"].astype(float)

    # game_id / snapshot_ts live on the raw odds (one row per book quote).
    o = odds.copy()
    o["side"] = o["side"].astype(str).str.upper()
    o["point"] = o["point"].astype(float)
    okeys = ["player_id", "market", "side", "point", "book"]
    extra = [c for c in ("game_id", "snapshot_ts") if c in o.columns]
    o = o[okeys + extra].drop_duplicates(okeys)
    df = df.merge(o, on=okeys, how="left")
    if "game_id" not in df.columns:
        df["game_id"] = None
    if "snapshot_ts" not in df.columns:
        df["snapshot_ts"] = None

    games = {str(g["game_id"]): g for g in (slate or {}).get("games") or []}
    df["game_id"] = df["game_id"].astype(str)
    df["game_date"] = df["game_id"].map(lambda g: (games.get(g) or {}).get("date"))
    df["tip_off"] = df["game_id"].map(lambda g: (games.get(g) or {}).get("tip_off"))

    df["in_top"] = 0
    if shortlist is not None and not shortlist.empty:
        top = shortlist[["player_id", "market", "side", "point", "book"]].copy()
        top["side"] = top["side"].astype(str).str.upper()
        top["point"] = top["point"].astype(float)
        top["_top"] = 1
        df = df.merge(top.drop_duplicates(), on=okeys, how="left")
        df["in_top"] = df["_top"].fillna(0).astype(int)
        df = df.drop(columns="_top")
    if "level_ok" in df.columns:
        df["level_ok"] = df["level_ok"].astype(bool).astype(int)
    else:
        df["level_ok"] = None
    keep = [c for c in _EXTRA_FIELDS if c in df.columns]

    def _extra(row) -> str:
        out = {}
        for c in keep:
            v = row[c]
            if isinstance(v, (float, np.floating)) and not np.isfinite(v):
                v = None
            out[c] = v.item() if hasattr(v, "item") else v
        return json.dumps(out, ensure_ascii=False)

    df["extra"] = df.apply(_extra, axis=1) if keep else None
    for c in _LINE_COLS:
        if c not in df.columns:
            df[c] = None
    df = df[df["game_id"].notna() & (df["game_id"] != "None")]
    return df[_LINE_COLS].drop_duplicates(_KEY).reset_index(drop=True)


def log_run(artifacts: dict, params: dict, origin: str = "cli",
            db_path: Path | str | None = None) -> int | None:
    """Persist one live run. Returns the run_id (None if nothing was logged)."""
    try:
        rows = build_rows(artifacts.get("value_table"), artifacts.get("odds"),
                          artifacts.get("shortlist"), artifacts.get("slate"))
        if rows.empty:
            return None
        ts = _now_utc()
        slate = artifacts.get("slate") or {}
        with closing(connect(db_path)) as con, con:
            cur = con.execute(
                "INSERT INTO runs (run_ts, origin, slate_date, game_filter, n_iter, seed,"
                " devig, top_pct, metric, n_lines, n_top) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, origin, slate.get("date"), artifacts.get("game"),
                 params.get("n_iter"), params.get("seed"), params.get("devig"),
                 params.get("top_pct"), params.get("metric"),
                 int(len(rows)), int(rows["in_top"].sum())))
            run_id = cur.lastrowid
            recs = []
            for r in rows.itertuples(index=False):
                vals = [None if (isinstance(v, float) and np.isnan(v)) else
                        (v.item() if hasattr(v, "item") else v) for v in r]
                recs.append([run_id, ts] + vals)
            con.executemany(
                f"INSERT OR IGNORE INTO lines (run_id, run_ts, {', '.join(_LINE_COLS)}) "
                f"VALUES ({', '.join('?' * (len(_LINE_COLS) + 2))})", recs)
        logger.info("picklog: run %s, %d lines (%d top) -> %s", run_id, len(rows),
                    int(rows["in_top"].sum()), db_path or PICKS_DB)
        return run_id
    except Exception as exc:  # noqa: BLE001 - logging must never break a run
        logger.warning("picklog: could not persist the run: %s", exc)
        return None


def load_lines(db_path: Path | str | None = None) -> pd.DataFrame:
    path = Path(db_path or PICKS_DB)
    if not path.exists():
        return pd.DataFrame()
    with closing(connect(path)) as con:
        return pd.read_sql_query("SELECT * FROM lines", con)
