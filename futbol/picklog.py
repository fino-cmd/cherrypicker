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
    # Promo de suplente: quién entró, cuánto aportó y si la ganó él.
    for col, typ in (("own_actual", "REAL"), ("sub_player", "TEXT"), ("sub_actual", "REAL"), ("via_sub", "INTEGER DEFAULT 0")):
        if col not in cols:
            c.execute(f"ALTER TABLE picks ADD COLUMN {col} {typ}")
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
                # Si se había retirado y vuelve a aparecer, se reabre.
                c.execute("UPDATE picks SET status='open' WHERE key=? AND status='withdrawn'", (key,))
                c.execute("""UPDATE picks SET last_seen=?, last_book=?, last_price=?, last_p_model=?,
                             lineup=MAX(lineup, ?) WHERE key=?""",
                          (now, k.get("best_book"), k.get("best_price"), k["p_model"],
                           int(bool(k.get("lineup"))), key))
    return n_new


def withdraw_missing(result: dict) -> int:
    """Retira las jugadas guardadas que la app ya no muestra.

    Una jugada se guarda la primera vez que aparece; si después desaparece
    (cambian las cuotas, sale la formación, se vuelve a simular) ya no es una
    jugada de la app y no debe liquidarse. Sólo antes del partido: después del
    inicio queda como estaba la última vez. Los partidos simulados con una
    alineación propia (Modo DT) no se tocan."""
    shown = {key_of(k) for k in result.get("picks") or []}
    events = {m["event_id"]: m for m in result.get("matches") or [] if not m.get("custom_lineup")}
    ts_now = pd.Timestamp.now(tz="UTC")
    n = 0
    with _db() as c:
        for key, eid, date in c.execute("SELECT key, event_id, date FROM picks WHERE status='open'").fetchall():
            if eid in events and key not in shown and ts_now < _kickoff(date):
                c.execute("UPDATE picks SET status='withdrawn' WHERE key=?", (key,))
                n += 1
    return n


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
            g = _grade(r, box)
            c.execute("""UPDATE picks SET status=?, actual=?, minutes=?, settled_at=?, own_actual=?,
                         sub_player=?, sub_actual=?, via_sub=? WHERE key=?""",
                      (g["status"], g["actual"], g["minutes"], _now(), g["own"], g["sub_player"],
                       g["sub_actual"], int(g["via_sub"]), r["key"]))
            val, status = g["actual"], g["status"]
            n += 1
            if verbose:
                print(f"  {status:4} {r['player']:24} {r['stat']} >{r['line']}  real {val}")
    return n


def promo_applies(r: dict) -> bool:
    """La jugada tiene promo de suplente (Suplente+ de bet365, Suplente de Oro
    de Betano, Suplente de Betsson): la casa donde se tomó la ofrece."""
    from futbol import books
    return r.get("mode") == "sub" or (r.get("first_book") in books.sub_promo_ids())


def _grade(r: dict, box: dict) -> dict:
    """Resultado de una jugada. Con promo de suplente: si el titular sale antes
    del final, lo que hizo el que entró en su lugar se suma a lo suyo."""
    # Por el id de ESPN y el equipo: dos homónimos pueden compartir id (ver espn.split_id).
    rid, team = espn.raw_id(r["player_id"]), str(r.get("team_id") or "")
    pl = next((p for p in box["players"] if str(p["player_id"]) == rid
               and (not team or str(p.get("team_id")) == team)), None)
    mins = (pl or {}).get("minutes") or 0
    own = (pl or {}).get(r["stat"])
    out = {"minutes": mins, "own": None, "sub_player": None, "sub_actual": None, "via_sub": False}
    if pl is None or mins <= 0 or own is None:
        return {**out, "status": "void", "actual": None}
    own = float(own)
    total = own
    # La promo vale salga cuando salga (antes había un tope de 88' y una salida
    # en el 88' quedaba afuera): alcanza con que ESPN registre el cambio o con
    # que no haya jugado los 90.
    pairs = _sub_pairs(r["league"], r["event_id"]) if promo_applies(r) and pl.get("starter") else {}
    subbed = str(pl.get("player_id")) in pairs or mins < 90
    if promo_applies(r) and pl.get("starter") and subbed:
        rp = _replacement(box["players"], pl, pairs)
        if rp is not None:
            sv = float(rp.get(r["stat"]) or 0)
            total = own + sv
            out.update(sub_player=rp.get("player"), sub_actual=sv)
    won = total > r["line"]
    return {**out, "own": own, "actual": total, "status": "won" if won else "lost",
            "via_sub": bool(won and own <= r["line"])}


def regrade(days: int = 120, verbose: bool = False) -> int:
    """Vuelve a liquidar las jugadas ya cerradas con la regla de la promo (no
    consulta nada nuevo si el box score está guardado). Devuelve cuántas cambian."""
    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).isoformat()[:16]
    with _db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM picks WHERE status IN ('won','lost') AND date >= ?", (since,))]
    changed = 0
    with _db() as c:
        for r in rows:
            box = espn.fetch_event({"league": r["league"], "event_id": r["event_id"]})
            if not box:
                continue
            g = _grade(r, box)
            if g["status"] == "void":
                continue
            if g["status"] != r["status"] or bool(g["via_sub"]) != bool(r.get("via_sub")):
                changed += 1
                if verbose:
                    print(f"  {r['status']} → {g['status']}{' (suplente)' if g['via_sub'] else ''}  {r['player']} {r['stat']} >{r['line']}  propio {g['own']} + {g['sub_player']} {g['sub_actual']}")
            c.execute("""UPDATE picks SET status=?, actual=?, own_actual=?, sub_player=?, sub_actual=?, via_sub=?
                         WHERE key=?""", (g["status"], g["actual"], g["own"], g["sub_player"], g["sub_actual"],
                                          int(g["via_sub"]), r["key"]))
    return changed


def _sub_pairs(league: str, event_id: str) -> dict[str, str]:
    """Cambios exactos del partido según ESPN ("X replaces Y"): id que sale → id
    que entra. Del resumen del partido (keyEvents), gratis y guardado en disco."""
    import httpx
    f = cfg.CACHE_DIR / "espn_subs" / f"{event_id}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    code = (cfg.LEAGUES.get(league) or {}).get("espn")
    if not code:
        return {}
    try:
        d = httpx.get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/summary",
                      params={"event": event_id}, timeout=30).json()
    except Exception:  # noqa: BLE001 - sin datos exactos se usa el minuto del cambio
        return {}
    pairs = {}
    for k in d.get("keyEvents") or []:
        if "ubstitution" not in json.dumps(k.get("type") or {}):
            continue
        ps = [str((p.get("athlete") or {}).get("id")) for p in k.get("participants") or []]
        if len(ps) >= 2 and ps[1] not in pairs:
            pairs[ps[1]] = ps[0]            # [0] entra, [1] sale
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(pairs), encoding="utf-8")
    return pairs


def _replacement(players: list[dict], starter: dict, pairs: dict | None = None) -> dict | None:
    """El que entró por `starter`. Primero el cambio exacto de ESPN; si no está,
    el suplente que ingresó en el minuto en que él salió (± 2'), preferido del
    mismo grupo de posición."""
    if pairs:
        pin = pairs.get(str(starter.get("player_id")))
        if pin:
            exact = next((p for p in players if str(p.get("player_id")) == pin), None)
            if exact is not None:
                return exact
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
        rows = [dict(r) for r in c.execute("SELECT * FROM picks WHERE status != 'withdrawn' ORDER BY date DESC")]
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
        "won_via_sub": sum(1 for r in done if r["status"] == "won" and r.get("via_sub")),
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
        rows = [dict(r) for r in c.execute("SELECT * FROM picks WHERE date >= ? AND status != 'withdrawn' ORDER BY date DESC", (since[:16],))]
    return rows


# ═════════════════════════════════════════════════════════════════════════
#  Toda la fecha: la probabilidad de CADA jugador y línea, no sólo las
#  jugadas. Sirve para separar "las jugadas anduvieron mal" de "toda la
#  fecha rindió por debajo" (tabla `preds`, mismo archivo).
# ═════════════════════════════════════════════════════════════════════════
_PRED_SCHEMA = """
CREATE TABLE IF NOT EXISTS preds (
  key TEXT PRIMARY KEY,                  -- event|player_id|stat|line
  event_id TEXT, league TEXT, date TEXT,
  player_id TEXT, team_id TEXT, stat TEXT, line REAL,
  p_model REAL, logged_at TEXT,
  status TEXT DEFAULT 'open',            -- open | won | lost | void
  actual REAL, minutes REAL
);
CREATE INDEX IF NOT EXISTS ix_preds_status ON preds(status);
CREATE INDEX IF NOT EXISTS ix_preds_event ON preds(event_id);
"""


def _pdb() -> sqlite3.Connection:
    c = _db()
    c.executescript(_PRED_SCHEMA)
    return c


def log_preds(matches: list[dict], allow_past: bool = False) -> int:
    """Guarda (o actualiza antes del partido) la probabilidad de cada línea.
    Con `allow_past` también agrega partidos ya empezados que no estaban
    (para cargar una vez lo que hay en jornada.json); nunca los pisa."""
    now, ts_now, n = _now(), pd.Timestamp.now(tz="UTC"), 0
    with _pdb() as c:
        for m in matches or []:
            if m.get("custom_lineup") or not m.get("players"):
                continue
            future = ts_now < _kickoff(m["date"])
            if not future and not allow_past:
                continue
            for p in m["players"]:
                for ln in p.get("lines") or []:
                    if ln.get("p_model") is None:
                        continue
                    key = f"{m['event_id']}|{p['player_id']}|{ln['stat']}|{ln['line']}"
                    vals = (key, m["event_id"], m["league"], m["date"], str(p["player_id"]),
                            str(p.get("team_id") or ""), ln["stat"], float(ln["line"]), float(ln["p_model"]), now)
                    if future:
                        c.execute("""INSERT INTO preds (key, event_id, league, date, player_id, team_id, stat, line, p_model, logged_at)
                                     VALUES (?,?,?,?,?,?,?,?,?,?)
                                     ON CONFLICT(key) DO UPDATE SET p_model=excluded.p_model,
                                       logged_at=excluded.logged_at, date=excluded.date""", vals)
                    else:
                        c.execute("""INSERT OR IGNORE INTO preds (key, event_id, league, date, player_id, team_id, stat, line, p_model, logged_at)
                                     VALUES (?,?,?,?,?,?,?,?,?,?)""", vals)
                    n += 1
    return n


def settle_preds() -> int:
    """Liquida las líneas de partidos terminados con la misma planilla de ESPN
    que las jugadas (sin promo de suplente: cuenta lo que hizo el jugador)."""
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=SETTLE_AFTER_H)
    with _pdb() as c:
        due = [dict(r) for r in c.execute(
            "SELECT event_id, league, MIN(date) AS date FROM preds WHERE status='open' GROUP BY event_id, league")]
    due = [r for r in due if _kickoff(r["date"]) <= cutoff]
    if not due:
        return 0
    finished = {}
    for lg, mm in {(r["league"], _kickoff(r["date"]).strftime("%Y%m")) for r in due}:
        for e in espn.month_events(lg, mm):
            if e["state"] == "post":
                finished[e["event_id"]] = e
    n = 0
    with _pdb() as c:
        for r in due:
            ev = finished.get(r["event_id"])
            box = espn.fetch_event(ev) if ev else None
            if not box:
                continue
            for row in c.execute("SELECT * FROM preds WHERE event_id=? AND status='open'", (r["event_id"],)).fetchall():
                rid, team = espn.raw_id(row["player_id"]), str(row["team_id"] or "")
                pl = next((p for p in box["players"] if str(p["player_id"]) == rid
                           and (not team or str(p.get("team_id")) == team)), None)
                mins = (pl or {}).get("minutes") or 0
                val = (pl or {}).get(row["stat"])
                if pl is None or mins <= 0 or val is None:
                    st, val = "void", None
                else:
                    val = float(val)
                    st = "won" if val > row["line"] else "lost"
                c.execute("UPDATE preds SET status=?, actual=?, minutes=? WHERE key=?", (st, val, mins, row["key"]))
                n += 1
    return n


# ═════════════════════════════════════════════════════════════════════════
#  Informe de rendimiento: por semana (lunes a domingo, hora de Argentina),
#  las jugadas contra lo que esperaba el modelo y contra toda la fecha.
# ═════════════════════════════════════════════════════════════════════════
TZ = "America/Argentina/Buenos_Aires"


def _rate(rows: list[dict]) -> dict:
    """Ganadas, lo que esperaba el modelo y cuántos desvíos se alejó."""
    n = len(rows)
    if not n:
        return {"n": 0}
    won = sum(r["status"] == "won" for r in rows)
    exp = sum(r["p_model"] for r in rows)
    sd = sum(r["p_model"] * (1 - r["p_model"]) for r in rows) ** 0.5
    return {"n": n, "won": won, "rate": round(won / n, 4), "exp": round(exp / n, 4),
            "z": round((won - exp) / sd, 2) if sd > 0 else None}


def _units(rows: list[dict]) -> float | None:
    priced = [r for r in rows if r.get("first_price")]
    if not priced:
        return None
    return round(sum((r["first_price"] - 1) if r["status"] == "won" else -1 for r in priced), 2)


def report(weeks: int = 5) -> dict:
    with _pdb() as c:
        picks = [dict(r) for r in c.execute("SELECT * FROM picks WHERE status != 'withdrawn'")]
        preds = [dict(r) for r in c.execute(
            "SELECT event_id, league, date, p_model, status FROM preds WHERE status IN ('won','lost')")]

    for r in picks + preds:
        t = _kickoff(r["date"]).tz_convert(TZ)
        r["_day"] = t.date()
        r["_week"] = (t.normalize() - pd.Timedelta(days=t.weekday())).date()

    done = [r for r in picks if r["status"] in ("won", "lost")]
    # La franja de "toda la fecha": donde caen las jugadas (del 10 % al 90 %), en pasos de 5 puntos.
    if len(done) >= 10:
        qs = pd.Series([r["p_model"] for r in done]).quantile([0.1, 0.9])
        band = [max(0.05, int(qs.iloc[0] * 20) / 20), min(0.95, -int(-qs.iloc[1] * 20) / 20)]
    else:
        band = [0.30, 0.50]
    in_band = lambda r: band[0] <= r["p_model"] < band[1]

    periods = []
    for wk in sorted({r["_week"] for r in done}, reverse=True)[:weeks]:
        wd = [r for r in done if r["_week"] == wk]
        wall = [r for r in picks if r["_week"] == wk]
        wp = [r for r in preds if r["_week"] == wk]
        days = sorted({r["_day"] for r in wd})
        lost = [r for r in wd if r["status"] == "lost"]
        periods.append({
            "week": wk.isoformat(), "from": days[0].isoformat(), "to": days[-1].isoformat(),
            "picks": {**_rate(wd), "units": _units(wd),
                      "open": sum(r["status"] == "open" for r in wall),
                      "void": sum(r["status"] == "void" for r in wall)},
            "field": _rate([r for r in wp if in_band(r)]),
            "field_events": len({r["event_id"] for r in wp}), "pick_events": len({r["event_id"] for r in wd}),
            # Las que perdieron: ¿jugaron de verdad o fue la formación?
            "lost": {"n": len(lost), "played45": sum((r.get("minutes") or 0) >= 45 for r in lost)},
            "leagues": sorted(({"league": lg, **_rate([r for r in wd if r["league"] == lg]),
                                "units": _units([r for r in wd if r["league"] == lg])}
                               for lg in {r["league"] for r in wd}), key=lambda x: -x["n"]),
        })
    first_field = min((r["_day"] for r in preds), default=None)
    # Una fila por jugada (compacta) y la fecha por día y liga: la pantalla
    # corta por rango, finde/entre semana, mercado, cuota, etc.
    rows = [{"d": r["_day"].isoformat(), "t": _kickoff(r["date"]).tz_convert(TZ).strftime("%H:%M"),
             "lg": r["league"], "m": r["match"], "pl": r["player"], "st": r["stat"], "ln": r["line"],
             "p": round(r["p_model"], 4), "o": r["first_price"], "s": r["status"],
             "a": r.get("actual"), "min": r.get("minutes"), "sub": 1 if r.get("via_sub") else 0}
            for r in sorted(picks, key=lambda r: r["date"]) if r["status"] in ("won", "lost", "open", "void")]
    fday: dict = {}
    for r in preds:
        if not in_band(r):
            continue
        k = (r["_day"].isoformat(), r["league"])
        a = fday.setdefault(k, [0, 0, 0.0, 0.0])
        a[0] += 1
        a[1] += r["status"] == "won"
        a[2] += r["p_model"]
        a[3] += r["p_model"] * (1 - r["p_model"])
    field_days = [[d, lg, n, w, round(e, 3), round(v, 3)] for (d, lg), (n, w, e, v) in sorted(fday.items())]
    return {"band": band, "periods": periods, "rows": rows, "field_days": field_days,
            "today": pd.Timestamp.now(tz=TZ).date().isoformat(),
            "total": {"picks": {**_rate(done), "units": _units(done)},
                      "field": _rate([r for r in preds if in_band(r)])},
            "field_since": first_field.isoformat() if first_field else None,
            "generated_at": _now()}


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
