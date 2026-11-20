"""
Futbol — cuotas de odds-api.net (alternativa a OddsPapi, se elige con
FUTBOL_ODDS=net en backend/.env).

Un pedido por partido trae las casas pedidas juntas. Se piden sólo las marcas
marcadas con "net": true en books.json (bet365, Betano, Betsson) con todos sus
países (bet365, bet365_es, …) y se juntan en una caja por marca. No hay casas
con versión argentina, y la API no da el n° del partido en cada casa: las
cajas de cuota no abren el partido en la casa.

Devuelve la misma tabla que futbol.oddspapi.parse (una fila por partido,
jugador, stat, línea, lado y casa), así el resto de la app no cambia.

Plan sandbox: 1000 créditos por mes, 30 pedidos por minuto, 1 crédito por pedido.
Clave en backend/.env: ODDS_API_NET_KEY (ODDS_API_NET_BASE_URL opcional).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd

from futbol import config as cfg
from futbol.espn import norm_name

DIR = cfg.CACHE_DIR / "oddsnet"
BASE = (os.getenv("ODDS_API_NET_BASE_URL") or "https://api.odds-api.net/v1").rstrip("/")
# Nombre de cada liga en odds-api.net (GET /leagues?sport=soccer).
LEAGUE = {"arg": "Argentina Liga Profesional", "bra": "Brazil Serie A", "eng": "EPL", "ita": "Serie A", "esp": "La Liga", "mex": "Liga MX",
          "ucl": "UEFA Champions League", "uel": "UEFA Europa League",
          "cdb": "Copa do Brasil", "unl": "UEFA Nations League"}
STAT_OF = {"player shots": "shots", "player shots on target": "sot"}
TYPES = "player prop,moneyline 3w,total"
MONTH_LIMIT = 1000
RESERVE = 20                  # créditos que siempre quedan libres
PARALLEL = 4                  # pedidos al mismo tiempo (el plan permite 30 por minuto)
LAST: dict[str, list] = {}    # event_id → filas de partido (1X2 / totales) de la última lectura
_FETCHED = [False]
_LOCK = threading.Lock()


def key() -> str | None:
    return os.getenv("ODDS_API_NET_KEY")


# ── Pedidos y créditos ───────────────────────────────────────────────────────

def _get(path: str, **params):
    k = key()
    if not k:
        raise RuntimeError("Falta ODDS_API_NET_KEY en backend/.env")
    r = httpx.get(f"{BASE}/{path.lstrip('/')}", params={x: v for x, v in params.items() if v is not None},
                  headers={"X-API-Key": k}, timeout=120)
    _count(path, r)
    r.raise_for_status()
    return r.json()


def _usage_file():
    return DIR / "usage.json"


def _read_usage() -> dict:
    try:
        return json.loads(_usage_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _count(path: str, r) -> None:
    with _LOCK:
        DIR.mkdir(parents=True, exist_ok=True)
        u = _read_usage()
        now = datetime.now(timezone.utc)
        month = now.strftime("%Y-%m")
        u[month] = int(u.get(month, 0)) + 1
        day = datetime.now().strftime("%Y-%m-%d")
        days = u.get("days") or {}
        days[day] = int(days.get(day, 0)) + 1
        u["days"] = dict(sorted(days.items())[-45:])
        if u.get("exact") and u["exact"].get("month") == month:
            u["exact"]["used"] = int(u["exact"]["used"]) + 1      # sigue contando hasta la próxima sincronización
        _usage_file().write_text(json.dumps(u), encoding="utf-8")
        log = DIR / "requests.csv"
        new = not log.exists()
        with log.open("a", encoding="utf-8") as fh:
            if new:
                fh.write("utc,path,status,bytes\n")
            fh.write(f"{now.isoformat(timespec='seconds')},{path},{r.status_code},{len(r.content)}\n")


def sync_usage() -> None:
    """Créditos exactos del plan (GET /usage, cuesta 1 crédito): se llama después de cada actualización."""
    try:
        d = _get("usage")
    except Exception as exc:  # noqa: BLE001
        print(f"  odds-api.net uso: {exc}")
        return
    with _LOCK:
        u = _read_usage()
        u["exact"] = {"month": datetime.now(timezone.utc).strftime("%Y-%m"),
                      "used": int(d.get("api_credits_used", d.get("used", 0))),
                      "limit": int(d.get("api_credits_limit", d.get("limit", MONTH_LIMIT))),
                      "period_end": d.get("period_end_utc")}
        _usage_file().write_text(json.dumps(u), encoding="utf-8")


def usage() -> dict:
    u = _read_usage()
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    ex = u.get("exact") or {}
    if ex.get("month") == month:
        used, limit, exact = int(ex["used"]), int(ex.get("limit") or MONTH_LIMIT), True
    else:
        used, limit, exact = int(u.get(month, 0)), MONTH_LIMIT, False
    return {"used": used, "limit": limit, "period": "mes", "remaining": max(0, limit - used),
            "exact": exact, "today": int((u.get("days") or {}).get(datetime.now().strftime("%Y-%m-%d"), 0)),
            "api": "net"}


# ── Casas ────────────────────────────────────────────────────────────────────

def catalog() -> dict:
    """id de casa → {name, countries} (GET /bookmakers, 1 crédito, guardado 30 días)."""
    f = DIR / "bookmakers.json"
    if f.exists() and time.time() - f.stat().st_mtime < 30 * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        items = _get("bookmakers").get("items") or []
    except Exception as exc:  # noqa: BLE001
        print(f"  odds-api.net casas: {exc}")
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    out = {x["bookmaker"]: {"name": x.get("display_name"), "countries": x.get("country_codes") or []}
           for x in items if x.get("bookmaker")}
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _brand(book: str) -> str:
    return book.split("_")[0].lower()


def wanted_books() -> list[str]:
    """Ids de odds-api.net de las marcas elegidas en books.json ('net': true),
    con todos sus países (bet365, bet365_es, …)."""
    from futbol import books
    brands = set(books.net_brands())
    return sorted(b for b in catalog() if _brand(b) in brands)


# ── Partidos ─────────────────────────────────────────────────────────────────

def events(lg: str, refresh: bool = False) -> list[dict]:
    """Partidos de una liga que todavía no empezaron (1 pedido, guardado FIXTURES_TTL_S)."""
    f = DIR / f"events_{lg}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < cfg.FIXTURES_TTL_S:
        data = json.loads(f.read_text(encoding="utf-8"))
    else:
        now = int(time.time())
        d = _get("events", sport="soccer", league=LEAGUE[lg], not_started_only="true", limit=100,
                 start_from=now - 3 * 3600, start_to=now + int(max(cfg.ODDS_WINDOW_H, 48) * 3600))
        data = [{"event_id": str(x["event_id"]), "league": lg, "start_time": x.get("start_time"),
                 "home": x.get("home_team"), "away": x.get("away_team")} for x in d.get("items") or []]
        DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    now = time.time()
    return [x for x in data if 0 <= (x.get("start_time") or 0) - now <= cfg.ODDS_WINDOW_H * 3600]


def league_fixtures(lg: str) -> list[dict]:
    return events(lg)


def all_fixtures() -> list[dict]:
    out = []
    for lg in cfg.LEAGUES:
        if lg in LEAGUE:
            try:
                out += events(lg)
            except Exception as exc:  # noqa: BLE001
                print(f"  odds-api.net partidos {lg}: {exc}")
    return out


def fixture_for_event(ev: dict) -> dict | None:
    """El partido de odds-api.net que corresponde a un partido de ESPN."""
    from futbol.oddspapi import team_similarity
    lg = ev.get("league")
    if lg not in LEAGUE:
        return None
    t = pd.Timestamp(ev["date"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    best, score = None, 0.0
    for x in events(lg):
        st = pd.Timestamp(x["start_time"], unit="s", tz="UTC")
        if abs(st - t) > pd.Timedelta(hours=30):
            continue
        sh, sa = team_similarity(ev["home"], x.get("home") or ""), team_similarity(ev["away"], x.get("away") or "")
        s = min(sh, sa)
        if abs(st - t) <= pd.Timedelta(hours=3) and max(sh, sa) >= 0.95 and min(sh, sa) >= 0.25:
            s = max(s, 0.9)
        if s > score:
            best, score = x, s
    return best if score >= 0.6 else None


# ── Cuotas ───────────────────────────────────────────────────────────────────

def _odds_file(event_id: str):
    return DIR / "odds" / f"{event_id}.json"


def _slim(ev: dict, items: list[dict]) -> dict:
    """Respuesta del snapshot → lo que se guarda: remates/al arco de jugador
    (escalera "over N.5") y 1X2 / goles del partido."""
    props, match = [], []
    for x in items:
        if not x.get("is_available", True) or not x.get("odds"):
            continue
        bt, metric = x.get("bet_type"), x.get("metric")
        if bt == "player prop" and metric in STAT_OF and x.get("player_name"):
            side, _, num = str(x.get("line") or "").partition(" ")
            try:
                line = float(num)
            except ValueError:
                continue
            props.append([x["bookmaker"], x["player_name"], STAT_OF[metric], line, side.lower(), float(x["odds"])])
        elif bt == "moneyline 3w" and x.get("side") in ("home", "draw", "away"):
            match.append([x["bookmaker"], "1x2", x["side"], None, float(x["odds"])])
        elif bt == "total" and metric == "standard":
            mk = str(x.get("market_key") or "")
            side = "over" if "/over " in mk else "under" if "/under " in mk else None
            try:
                line = float(str(x.get("line") or "").split()[-1])
            except (ValueError, IndexError):
                continue
            if side:
                match.append([x["bookmaker"], "total", side, line, float(x["odds"])])
    return {**ev, "fetched_at": int(time.time()), "props": props, "match": match}


def _fetch_event(ev: dict) -> dict | None:
    try:
        d = _get(f"events/{ev['event_id']}/odds/snapshot", types=TYPES, periods="full time",
                 price_fields="odds", limit=25000, bookmakers=",".join(wanted_books()) or None)
    except Exception as exc:  # noqa: BLE001 - un partido caído no corta el resto
        print(f"  odds-api.net {ev.get('home')} - {ev.get('away')}: {exc}")
        return None
    out = _slim(ev, d.get("items") or [])
    f = _odds_file(ev["event_id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    # bet365 suspende el partido cuando salen las formaciones: si ahora no hay
    # remates pero antes sí (hace menos de 6 h), se conservan los anteriores.
    if not out["props"] and f.exists() and time.time() - f.stat().st_mtime < 6 * 3600:
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
            if old.get("props"):
                out["props"], out["kept_from"] = old["props"], old.get("kept_from") or old.get("fetched_at")
        except Exception:  # noqa: BLE001
            pass
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def refresh_fixtures(evs: list[dict]) -> int:
    """Cuotas nuevas de estos partidos: 1 pedido c/u. → pedidos hechos."""
    from concurrent.futures import ThreadPoolExecutor
    before = _month_count()
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        got = [x for x in ex.map(_fetch_event, evs) if x]
    if got:
        _FETCHED[0] = True
        _count_refresh()
        sync_usage()
    return _month_count() - before


def fetch_round() -> int:
    """Cuotas nuevas de todas las ligas (próximas ODDS_WINDOW_H horas)."""
    return refresh_fixtures(all_fixtures())


def _month_count() -> int:
    return int(_read_usage().get(datetime.now(timezone.utc).strftime("%Y-%m"), 0))


def _count_refresh() -> None:
    with _LOCK:
        u = _read_usage()
        day = datetime.now().strftime("%Y-%m-%d")
        u["refresh_days"] = {day: int((u.get("refresh_days") or {}).get(day, 0)) + 1}
        _usage_file().write_text(json.dumps(u), encoding="utf-8")


def refreshes_today() -> int:
    return int((_read_usage().get("refresh_days") or {}).get(datetime.now().strftime("%Y-%m-%d"), 0))


def _cached() -> list[dict]:
    out, cut = [], time.time() - 2 * 86400
    for f in (DIR / "odds").glob("*.json") if (DIR / "odds").exists() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if (d.get("start_time") or 0) >= cut:
            out.append(d)
    return out


def table() -> pd.DataFrame:
    """Cuotas guardadas → tabla de oddspapi.parse (sin pedir nada). Una caja
    por marca: la versión principal ("bet365") primero, los países completan."""
    from futbol import books
    brands = set(books.net_brands())
    rows = []
    LAST.clear()
    for d in _cached():
        start = datetime.fromtimestamp(d["start_time"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        base = {"fixture_id": d["event_id"], "league": d["league"], "start": start,
                "home": d.get("home"), "away": d.get("away")}
        susp = bool(d.get("kept_from"))
        LAST[d["event_id"]] = d.get("match") or []
        for book, name, stat, line, side, price in d.get("props") or []:
            if _brand(book) not in brands:
                continue
            rows.append({**base, "book": _brand(book), "src": book, "main": book == _brand(book),
                         "player_name": name, "player_key": norm_name(name),
                         "stat": stat, "line": line, "side": side, "price": price, "susp": susp})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = (df.sort_values(["main", "src"], ascending=[False, True])
                .drop_duplicates(["fixture_id", "book", "player_key", "stat", "line", "side"])
                .drop(columns=["main"]))
    return df


def match_markets(fixture_id: str) -> dict | None:
    """1X2 sin margen y P(Over) de goles por línea, promediados entre casas."""
    rows = LAST.get(str(fixture_id))
    if not rows:
        return None
    by_book: dict[str, dict] = {}
    for book, kind, side, line, price in rows:
        by_book.setdefault(book, {}).setdefault((kind, line), {})[side] = price
    p1x2, totals = [], {}
    for mk in by_book.values():
        x = mk.get(("1x2", None)) or {}
        if {"home", "draw", "away"} <= set(x):
            q = [1 / x["home"], 1 / x["draw"], 1 / x["away"]]
            p1x2.append([v / sum(q) for v in q])
        for (kind, line), pr in mk.items():
            if kind == "total" and {"over", "under"} <= set(pr) and abs(line % 1 - 0.5) < 1e-9 and 0.5 <= line <= 5.5:
                q = [1 / pr["over"], 1 / pr["under"]]
                totals.setdefault(line, []).append(q[0] / sum(q))
    if not p1x2 and not totals:
        return None
    return {"p1x2": tuple(float(np.mean([v[i] for v in p1x2])) for i in range(3)) if p1x2 else None,
            "totals": {l: float(np.mean(v)) for l, v in totals.items()}}


def cache_age_s() -> float | None:
    """Antigüedad de las cuotas MÁS VIEJAS de los próximos partidos. None si
    alguno todavía no tiene cuotas. Usa sólo las listas guardadas: no gasta pedidos."""
    now, ages = time.time(), []
    for lg in LEAGUE:
        f = DIR / f"events_{lg}.json"
        if not f.exists():
            return None
        for x in json.loads(f.read_text(encoding="utf-8")):
            if not 0 <= (x.get("start_time") or 0) - now <= cfg.ODDS_WINDOW_H * 3600:
                continue
            g = _odds_file(x["event_id"])
            if not g.exists():
                return None
            ages.append(now - g.stat().st_mtime)
    return max(ages) if ages else None


def cache_stamp() -> int | None:
    fs = list((DIR / "odds").glob("*.json")) if (DIR / "odds").exists() else []
    return int(max(f.stat().st_mtime for f in fs)) if fs else None


def refresh_cost() -> int:
    """Créditos que costaría actualizar todo ahora (listas guardadas; si vencieron, se estiman)."""
    n = 0
    now = time.time()
    for lg in LEAGUE:
        f = DIR / f"events_{lg}.json"
        if f.exists() and now - f.stat().st_mtime < cfg.FIXTURES_TTL_S:
            n += sum(1 for x in json.loads(f.read_text(encoding="utf-8"))
                     if 0 <= (x.get("start_time") or 0) - now <= cfg.ODDS_WINDOW_H * 3600)
        else:
            n += 1 + 12
    return n + 1                                  # + la sincronización de créditos


def plan_refresh() -> dict:
    u = usage()
    cost = refresh_cost()
    left = u["remaining"]
    ok = left - cost >= RESERVE
    return {"books": ["net"] if ok else [], "all": ["net"], "used": u["used"], "limit": u["limit"],
            "remaining": left, "cost": cost, "cap": u["limit"] - RESERVE}
