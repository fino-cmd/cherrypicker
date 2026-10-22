"""Replay settled real nights with and without the game-context adjustment.

    python -m altlines.tools.context_replay [--games 401909088,401909089]
                                            [--variants base,rest,home,opp,inj,all]

For every settled game in picks.db: rebuild the live board from the CACHED
odds (0 Odds API credits) with the player histories cut the day before the
game (no look-ahead), once per variant of the config.CONTEXT_* flags, and
score it against what happened (`actual` in picks.db):

  * log-loss / Brier of p_engine (the anchored engine) and p_model (the
    engine–market blend) over every distinct (player, market, side, point);
  * the recommended board (ev.select_board): W-L and units at flat stake.

The injury list is the one ESPN attaches to that game's summary, not today's
league report. Two games are a sanity check, not a verdict: the walk-forward
backtest (`backtest --variants`) is the measurement.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

from altlines import config as cfg
from altlines import context as ctx_mod
from altlines import live
from altlines import slate as slate_mod

FLAGS = {"rest": "CONTEXT_REST", "home": "CONTEXT_HOME", "opp": "CONTEXT_OPP",
         "inj": "CONTEXT_INJURIES"}


def _variant_flags(name: str) -> dict[str, bool]:
    if name == "base":
        on = set()
    elif name == "all":
        on = set(FLAGS)
    elif name == "config":
        on = {k for k, f in FLAGS.items() if getattr(cfg, f)}
    else:
        on = set(name.split("+"))
    return {f: (k in on) for k, f in FLAGS.items()}


def _settled(db) -> pd.DataFrame:
    with sqlite3.connect(db) as c:
        lines = pd.read_sql("select game_id, game_date, player_id, market, actual "
                            "from lines where actual is not null", c)
    return lines.drop_duplicates(["game_id", "player_id", "market"])


KEY = ["player_id", "market", "side", "point"]


def _lines(table: pd.DataFrame, actual: pd.DataFrame) -> pd.DataFrame:
    """Distinct priced lines with their outcome (pushes dropped)."""
    t = table.merge(actual[["player_id", "market", "actual"]], on=["player_id", "market"])
    t = t.drop_duplicates(KEY).copy()
    t["y"] = np.where(t["side"].str.lower() == "over", t["actual"] > t["point"],
                      t["actual"] < t["point"]).astype(float)
    return t[t["actual"] != t["point"]]


def _score(t: pd.DataFrame, board: pd.DataFrame, actual: pd.DataFrame) -> dict:
    y = t["y"].to_numpy(float)
    out = {"lines": len(t)}
    for col in ("p_engine", "p_model"):
        if col in t.columns:
            p = np.clip(t[col].to_numpy(float), 1e-4, 1 - 1e-4)
            out[f"ll_{col}"] = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
            out[f"brier_{col}"] = float(np.mean((p - y) ** 2))
    b = board.merge(actual[["player_id", "market", "actual"]], on=["player_id", "market"])
    if len(b):
        bw = np.where(b["side"].str.lower() == "over", b["actual"] > b["point"], b["actual"] < b["point"])
        bp = b["actual"] == b["point"]
        pnl = np.where(bp, 0.0, np.where(bw, b["decimal_odds"] - 1.0, -1.0))
        out.update(board=len(b), wins=int((bw & ~bp).sum()), units=float(pnl.sum()))
    else:
        out.update(board=0, wins=0, units=0.0)
    return out


def _cached_events() -> list[dict]:
    """The events of the cached odds payloads: a finished game is no longer in
    The Odds API's (free) events list, but its tray is still on disk."""
    out = []
    for f in (live.CACHE_DIR / "odds").glob("*_us_*.json"):
        with contextlib.suppress(Exception):
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({k: d.get(k) for k in ("id", "home_team", "away_team", "commence_time")})
    return out


def replay(game_id: str, game_date: str, variant: str, actual: pd.DataFrame) -> dict:
    from altlines.run_pipeline import run

    flags = _variant_flags(variant)
    saved = {f: getattr(cfg, f) for f in FLAGS.values()}
    saved_cache_only = cfg.LIVE_ODDS_CACHE_ONLY
    real_fetch = live.fetch_athlete_games
    real_events = live.list_odds_events

    def fetch_before(athlete_id, season, current, refresh=False):
        games = real_fetch(athlete_id, season, current, refresh)
        return [g for g in games if str(g.get("date") or "") < game_date]

    try:
        for f, v in flags.items():
            setattr(cfg, f, v)
        cfg.LIVE_ODDS_CACHE_ONLY = True
        live.fetch_athlete_games = fetch_before
        live.list_odds_events = lambda refresh=False: _cached_events()
        live.CONTEXT_INJURY_OVERRIDE = lambda gid: ctx_mod.game_injuries(gid)
        slate = slate_mod.get_slate(game_date)
        slate = {**slate, "games": [{**g, "state": "pre"} for g in slate["games"]
                                    if str(g["game_id"]) == game_id]}
        with contextlib.redirect_stdout(io.StringIO()):
            art = run(live=True, game=game_id, slate=slate, write_html=False,
                      log_picks=False)
    finally:
        for f, v in saved.items():
            setattr(cfg, f, v)
        cfg.LIVE_ODDS_CACHE_ONLY = saved_cache_only
        live.fetch_athlete_games = real_fetch
        live.list_odds_events = real_events
        live.CONTEXT_INJURY_OVERRIDE = None
    lines = _lines(art["value_table"], actual)
    res = {"_lines": lines, "_board": art["shortlist"], "_actual": actual}
    ctx = (art.get("live_meta") or {}).get("context") or []
    mults = [r["mult"] for r in ctx]
    res.update(game=game_id, variant=variant,
               mult_mean=float(np.mean(mults)) if mults else 1.0,
               out=", ".join(sorted({n for r in ctx for n in r.get("teammates_out") or []})))
    return res


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--games", default="", help="game ids (default: every settled game)")
    p.add_argument("--variants", default="base,rest,home,opp,inj,all,config")
    args = p.parse_args(argv)
    settled = _settled(cfg.DATA_DIR / "picks.db")
    games = [g for g in args.games.split(",") if g] or sorted(settled["game_id"].astype(str).unique())
    rows = []
    for gid in games:
        actual = settled[settled["game_id"].astype(str) == gid]
        gdate = str(actual["game_date"].iloc[0])[:10]
        runs = [replay(gid, gdate, v.strip(), actual) for v in args.variants.split(",")]
        # Score every variant on the SAME lines: the level filter and the price
        # cap change which lines each variant prices.
        common = None
        for r in runs:
            k = set(map(tuple, r["_lines"][KEY].to_numpy().tolist()))
            common = k if common is None else common & k
        for r in runs:
            t = r["_lines"]
            t = t[[tuple(x) in common for x in t[KEY].to_numpy().tolist()]]
            r.update(_score(t, r["_board"], r["_actual"]))
            for k in ("_lines", "_board", "_actual"):
                r.pop(k)
            rows.append(r)
            print(f"  {gid} {r['variant']:<8} lines {r['lines']:>4}  "
                  f"LL engine {r.get('ll_p_engine', np.nan):.4f}  LL blend {r.get('ll_p_model', np.nan):.4f}  "
                  f"board {r['wins']}-{r['board'] - r['wins']} ({r['units']:+.2f} u)  "
                  f"mult {r['mult_mean']:.3f}  OUT: {r['out'] or '—'}", flush=True)
    df = pd.DataFrame(rows)
    print("\nTotal por variante (las dos noches juntas, ponderado por líneas):")
    agg = []
    for v, g in df.groupby("variant", sort=False):
        w = g["lines"]
        agg.append({"variant": v, "lines": int(w.sum()),
                    "ll_engine": float(np.average(g["ll_p_engine"], weights=w)),
                    "ll_blend": float(np.average(g["ll_p_model"], weights=w)),
                    "brier_engine": float(np.average(g["brier_p_engine"], weights=w)),
                    "board": f"{int(g['wins'].sum())}-{int((g['board'] - g['wins']).sum())}",
                    "units": round(float(g["units"].sum()), 2)})
    print(pd.DataFrame(agg).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.exit(main())
