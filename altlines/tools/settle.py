"""Settle the logged AltLines picks against the real ESPN box score.

    python -m altlines.tools.settle [--game 401909088] [--db path] [--dry-run]

For every game in `data/altlines/picks.db` with unsettled lines:

  1. ask ESPN whether the game is final (event summary, free, no key);
  2. for each player of that game, re-download his ESPN game log of that season
     (`live.fetch_athlete_games`, refresh) and take the row of that event;
  3. mark each line win / loss / push, with `pnl` per unit staked
     (win = decimal_odds − 1, loss = −1, push = 0).

A player with no row for a final game (DNP, inactive) is `void` (pnl 0), but
only once the tip-off is `VOID_AFTER_H` hours old: ESPN can take a while to
post the game log, and a void is final.

Spends NO Odds API credits: only ESPN's public JSON.
"""
from __future__ import annotations

import argparse
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from altlines import live
from altlines import picklog

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

_ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
VOID_AFTER_H = 36


def stat_value(row: dict, market: str) -> float | None:
    """Box-score row (live.fetch_athlete_games) → the market's stat."""
    pts, reb, ast = row.get("pts"), row.get("reb"), row.get("ast")
    if market == "points":
        return pts
    if market == "rebounds":
        return reb
    if market == "assists":
        return ast
    if market == "threes":
        return row.get("tpm")
    if market == "pra":
        if None in (pts, reb, ast):
            return None
        return pts + reb + ast
    return None


def grade(side: str, point: float, actual: float, decimal_odds: float) -> tuple[str, float]:
    if actual == point:
        return "push", 0.0
    won = actual > point if side.upper() == "OVER" else actual < point
    return ("win", round(decimal_odds - 1.0, 4)) if won else ("loss", -1.0)


def game_state(game_id: str) -> str | None:
    """'pre' | 'in' | 'post' from ESPN, or None if ESPN did not answer."""
    try:
        r = httpx.get(_ESPN_SUMMARY, params={"event": game_id}, timeout=20)
        r.raise_for_status()
        comp = ((r.json().get("header") or {}).get("competitions") or [{}])[0]
        return ((comp.get("status") or {}).get("type") or {}).get("state")
    except Exception as exc:  # noqa: BLE001 - network boundary
        print(f"  ESPN summary {game_id}: {exc}")
        return None


def _parse_ts(s) -> datetime | None:
    try:
        ts = pd.Timestamp(s)
        return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()
    except Exception:  # noqa: BLE001
        return None


def settle(db_path=None, game: str | None = None, dry_run: bool = False,
           assume_final: bool = False) -> pd.DataFrame:
    """Settle what can be settled. Returns one row per (game, player, market)."""
    with closing(picklog.connect(db_path)) as con:
        q = ("SELECT DISTINCT game_id, game_date, tip_off, player_id, player, market "
             "FROM lines WHERE outcome IS NULL")
        args: tuple = ()
        if game:
            q += " AND game_id = ?"
            args = (str(game),)
        todo = pd.read_sql_query(q, con, params=args)
    if todo.empty:
        print("Nada pendiente de liquidar.")
        return pd.DataFrame()

    now = datetime.now(timezone.utc)
    summary = []
    updates = []          # (actual, outcome, pnl, settled_ts, game, player, market)
    for gid, g in todo.groupby("game_id"):
        state = "post" if assume_final else game_state(str(gid))
        tip = _parse_ts(g["tip_off"].dropna().iloc[0]) if g["tip_off"].notna().any() else None
        print(f"Partido {gid} ({g['game_date'].iloc[0]}): estado ESPN = {state}")
        if state != "post":
            print("  todavía no terminó: se deja pendiente.")
            continue
        old_enough = tip is None or now - tip > timedelta(hours=VOID_AFTER_H)
        when = tip or now
        season = live._season_end_year(when)
        for pid, gp in g.groupby("player_id"):
            aid = str(pid).replace("ESPN-", "")
            games = live.fetch_athlete_games(aid, season, current=True, refresh=True)
            row = next((r for r in games if str(r.get("event_id")) == str(gid)), None)
            name = gp["player"].iloc[0]
            for market in gp["market"].unique():
                if row is None:
                    status = "void" if old_enough else "pending"
                    summary.append({"game_id": gid, "player": name, "market": market,
                                    "actual": None, "status": status})
                    if status == "void":
                        updates.append((None, "void", 0.0, gid, pid, market))
                    continue
                actual = stat_value(row, market)
                summary.append({"game_id": gid, "player": name, "market": market,
                                "actual": actual, "status": "final"})
                if actual is not None:
                    updates.append((actual, None, None, gid, pid, market))
            if row is None:
                print(f"  {name}: sin fila de box score "
                      f"({'void (DNP)' if old_enough else f'pendiente hasta {VOID_AFTER_H} h tras el tip'})")

    if updates and not dry_run:
        ts = now.isoformat(timespec="seconds")
        with closing(picklog.connect(db_path)) as con, con:
            for actual, outcome, pnl, gid, pid, market in updates:
                if outcome == "void":
                    con.execute("UPDATE lines SET actual=NULL, outcome='void', pnl=0, settled_ts=? "
                                "WHERE game_id=? AND player_id=? AND market=? AND outcome IS NULL",
                                (ts, gid, pid, market))
                    continue
                rows = con.execute(
                    "SELECT id, side, point, decimal_odds FROM lines WHERE game_id=? AND "
                    "player_id=? AND market=? AND outcome IS NULL", (gid, pid, market)).fetchall()
                con.executemany(
                    "UPDATE lines SET actual=?, outcome=?, pnl=?, settled_ts=? WHERE id=?",
                    [(actual, *grade(r["side"], r["point"], float(actual), r["decimal_odds"]),
                      ts, r["id"]) for r in rows])

    out = pd.DataFrame(summary)
    if not out.empty:
        n_final = int((out["status"] == "final").sum())
        n_void = int((out["status"] == "void").sum())
        print(f"\n{n_final} jugador-mercado liquidados, {n_void} void, "
              f"{int((out['status'] == 'pending').sum())} pendientes"
              f"{' (dry-run: nada escrito)' if dry_run else ''}.")
        if not dry_run:
            with closing(picklog.connect(db_path)) as con:
                tally = con.execute(
                    "SELECT outcome, COUNT(*), ROUND(SUM(pnl), 2) FROM lines "
                    + ("WHERE game_id=? " if game else "")
                    + "GROUP BY outcome", (str(game),) if game else ()).fetchall()
            for o, n, pnl in tally:
                print(f"  {o or 'pendiente':<10} {n:>5} líneas   pnl {pnl if pnl is not None else '-'}")
    return out


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Liquida los picks AltLines contra ESPN")
    p.add_argument("--game", default=None, help="solo este ESPN game id")
    p.add_argument("--db", default=None, help=f"ruta a la base (default {picklog.PICKS_DB})")
    p.add_argument("--dry-run", action="store_true", help="no escribir nada")
    p.add_argument("--assume-final", action="store_true",
                   help="no preguntar a ESPN si terminó (tests / ESPN caído)")
    a = p.parse_args(argv)
    settle(a.db, a.game, a.dry_run, a.assume_final)


if __name__ == "__main__":
    main()
