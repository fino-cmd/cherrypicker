"""
Futbol — registro de picks y liquidación automática con ESPN.

Cada vez que la jornada se calcula, sus picks se guardan en
data/futbol/picks.db (SQLite). De cada pick queda:
  • la PRIMERA vez que apareció (probabilidad, cuota mínima, mejor casa y precio);
  • la ÚLTIMA cuota vista antes del partido ("cierre"): si el precio que se
    tomó es mejor que el de cierre de forma sistemática, el modelo encuentra
    valor de verdad (CLV), aunque una racha corta diga otra cosa.
Cuando ESPN da el partido por terminado se liquida solo:
  ganada / perdida / nula (el jugador no entró).

    python -m futbol.picklog            # liquida lo pendiente y muestra el récord
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone

import pandas as pd

from futbol import config as cfg
from futbol import espn

DB = cfg.DATA_DIR / "picks.db"
SETTLE_AFTER_H = 2.25          # desde el inicio: un partido dura ~1 h 55

_SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
  key TEXT PRIMARY KEY,
  event_id TEXT, league TEXT, date TEXT, match TEXT,
  player TEXT, player_id TEXT, team_id TEXT, pos TEXT,
  stat TEXT, line REAL,
  p_model REAL, fair_odds REAL, min_odds REAL, lineup INTEGER,
  first_seen TEXT, first_book TEXT, first_price REAL,
  last_seen TEXT, last_book TEXT, last_price REAL, last_p_model REAL,
  status TEXT DEFAULT 'open',          -- open | won | lost | void
  mode TEXT DEFAULT 'normal',          -- normal | sub (con Suplente+ / de Oro / Suplente)
  actual REAL, minutes REAL, settled_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_picks_status ON picks(status);
CREATE INDEX IF NOT EXISTS ix_picks_date ON picks(date);
"""


def _db() -> sqlite3.Connection:
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(picks)")}
    if "mode" not in cols:
        c.execute("ALTER TABLE picks ADD COLUMN mode TEXT DEFAULT 'normal'")
    return c


def key_of(k: dict) -> str:
    return f"{k['event_id']}|{k['player_id']}|{k['stat']}|{k['line']}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _kickoff(iso: str) -> pd.Timestamp:
    t = pd.Timestamp(iso)
    return t.tz_localize("UTC") if t.tzinfo is None else t


def log(picks: list[dict]) -> int:
    """Guarda los picks nuevos; de los ya guardados actualiza la última cuota
    (sólo antes del partido: después ya no es un precio que se pueda tomar)."""
    now, n_new = _now(), 0
    ts_now = pd.Timestamp.now(tz="UTC")
    with _db() as c:
        for k in picks:
            key = key_of(k)
            row = c.execute("SELECT key FROM picks WHERE key = ?", (key,)).fetchone()
            if row is None:
                c.execute("""INSERT INTO picks (key, event_id, league, date, match, player, player_id, team_id,
                             pos, stat, line, p_model, fair_odds, min_odds, lineup, first_seen, first_book,
                             first_price, last_seen, last_book, last_price, last_p_model, mode)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (key, k["event_id"], k["league"], k["date"], k["match"], k["player"],
                           str(k["player_id"]), str(k["team_id"]), k.get("pos"), k["stat"], float(k["line"]),
                           k["p_model"], k["fair_odds"], k["min_odds"], int(bool(k.get("lineup"))),
                           now, k.get("best_book"), k.get("best_price"),
                           now, k.get("best_book"), k.get("best_price"), k["p_model"], k.get("mode", "normal")))
                n_new += 1
            elif ts_now < _kickoff(k["date"]):
                c.execute("""UPDATE picks SET last_seen=?, last_book=?, last_price=?, last_p_model=?,
                             lineup=MAX(lineup, ?) WHERE key=?""",
                          (now, k.get("best_book"), k.get("best_price"), k["p_model"],
                           int(bool(k.get("lineup"))), key))
    return n_new


def settle(verbose: bool = False) -> int:
    """Liquida los picks de partidos que ESPN ya da por terminados."""
    with _db() as c:
        open_ = [dict(r) for r in c.execute("SELECT * FROM picks WHERE status = 'open'")]
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=SETTLE_AFTER_H)
    due = [r for r in open_ if _kickoff(r["date"]) <= cutoff]
    if not due:
        return 0
    # Estado de cada partido según el listado del mes (un pedido por liga y mes).
    finished = {}
    for lg, mm in {(r["league"], _kickoff(r["date"]).strftime("%Y%m")) for r in due}:
        for e in espn.month_events(lg, mm):
            if e["state"] == "post":
                finished[e["event_id"]] = e
    n = 0
    boxes = {}
    with _db() as c:
        for r in due:
            ev = finished.get(r["event_id"])
            if ev is None:
                continue
            if r["event_id"] not in boxes:
                boxes[r["event_id"]] = espn.fetch_event(ev)
            box = boxes[r["event_id"]]
            if not box:
                continue
            pl = next((p for p in box["players"] if str(p["player_id"]) == r["player_id"]), None)
            mins = (pl or {}).get("minutes") or 0
            val = (pl or {}).get(r["stat"])
            if pl is None or mins <= 0 or val is None:
                status, val = "void", None
            else:
                val = float(val)
                if r.get("mode") == "sub" and pl.get("starter") and mins < 88:
                    rp = _replacement(box["players"], pl)
                    if rp is not None:
                        val += float(rp.get(r["stat"]) or 0)
                status = "won" if val > r["line"] else "lost"
            c.execute("UPDATE picks SET status=?, actual=?, minutes=?, settled_at=? WHERE key=?",
                      (status, val, mins, _now(), r["key"]))
            n += 1
            if verbose:
                print(f"  {status:4} {r['player']:24} {r['stat']} >{r['line']}  real {val}")
    return n


def _replacement(players: list[dict], starter: dict) -> dict | None:
    """El que entró por `starter`: compañero suplente que ingresó en el minuto
    en que él salió (± 2'); si hay varios, el del mismo grupo de posición."""
    out_min = float(starter.get("minutes") or 0)
    c = [p for p in players if p.get("team_id") == starter.get("team_id") and not p.get("starter")
         and (p.get("minutes") or 0) > 0 and abs((90 - float(p["minutes"])) - out_min) <= 2]
    if not c:
        return None
    same = [p for p in c if p.get("pos_group") == starter.get("pos_group")]
    return (same or c)[0]


def summary(days: int | None = None) -> dict:
    """Récord, unidades (1 u por pick al precio tomado) y CLV."""
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM picks ORDER BY date DESC")]
    if days:
        since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)
        rows = [r for r in rows if _kickoff(r["date"]) >= since]
    done = [r for r in rows if r["status"] in ("won", "lost")]
    won = sum(r["status"] == "won" for r in done)
    units = sum((r["first_price"] - 1) if r["status"] == "won" else -1 for r in done if r["first_price"])
    clv = [r["first_price"] / r["last_price"] - 1 for r in rows
           if r["first_price"] and r["last_price"] and r["first_seen"] != r["last_seen"]]
    exp = sum(r["p_model"] for r in done)
    return {
        "picks": len(rows), "settled": len(done), "won": won, "lost": len(done) - won,
        "void": sum(r["status"] == "void" for r in rows),
        "open": sum(r["status"] == "open" for r in rows),
        "units": round(units, 2), "roi": round(units / len(done), 3) if done else None,
        "expected_won": round(exp, 1),
        "clv": round(sum(clv) / len(clv), 4) if clv else None, "clv_n": len(clv),
    }


def recent(days: int = 10) -> list[dict]:
    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).isoformat()
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM picks WHERE date >= ? ORDER BY date DESC", (since[:16],))]
    return rows


def main():
    for s in (sys.stdout,):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    n = settle(verbose=True)
    print(f"{n} picks liquidados")
    print(json.dumps(summary(), indent=2))


if __name__ == "__main__":
    main()
