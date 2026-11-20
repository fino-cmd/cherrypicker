"""
Futbol — cuotas de OpticOdds (api.opticodds.com, clave OPTICODDS_KEY en
backend/.env). Se elige con FUTBOL_ODDS=optic. Tiene las mismas funciones que
futbol.oddsnet y devuelve la misma tabla que futbol.oddspapi.parse, así el
resto de la app no cambia.

  · Sin tope mensual ni créditos: el límite es de 2500 pedidos cada 15 segundos.
    1 pedido por partido (OpticOdds deja pedir hasta 5 casas juntas).
  · Casas: Betano, 1xBet, bet365 y Betsson (las marcadas con "optic": true en
    books.json), más Pinnacle para el 1X2 y los goles del partido.
  · Remates y remates al arco de jugador: hoy los publican Betano y 1xBet;
    bet365 y Betsson sólo dan mercados del partido.
  · Las cuotas de jugador aparecen unos 2 o 3 días antes del partido.
  · bet365: sus remates de jugador no están en OpticOdds. Se piden a
    odds-api.net (NET_BOOK, 1 crédito por partido) y se suman al mismo partido.
    Si a ese plan no le quedan créditos, el partido queda con Betano y 1xBet.
  · Liga que OpticOdds no tiene cargada (hoy, la Liga Profesional argentina):
    sus partidos y cuotas se siguen pidiendo a odds-api.net (futbol.oddsnet,
    1 crédito por partido), y se suman a la misma tabla.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from altlines import odds_optic as _oo
from futbol import config as cfg
from futbol import oddsnet as _on
from futbol.espn import norm_name

DIR = cfg.CACHE_DIR / "oddsoptic"
# id de cada liga en OpticOdds (GET /leagues?sport=soccer).
LEAGUE = {"arg": "argentina_-_primera_division", "bra": "brazil_-_serie_a", "eng": "england_-_premier_league",
          "ita": "italy_-_serie_a", "esp": "spain_-_la_liga", "mex": "mexico_-_liga_mx",
          "ucl": "uefa_-_champions_league", "uel": "uefa_-_europa_league",
          "cdb": "brazil_-_copa_do_brasil", "unl": "uefa_-_nations_league"}
# id en OpticOdds → marca en la app. Pinnacle sólo se usa en los mercados del partido.
BOOKS = {"betano": "betano", "1xbet": "1xbet", "bet365": "bet365", "betsson": "betsson", "pinnacle": "pinnacle"}
REF = "pinnacle"
NET_BOOK = "bet365"           # casa cuyos remates se piden a odds-api.net
STAT_OF = {"player_shots": "shots", "player_shots_on_target": "sot"}
MARKETS = list(STAT_OF) + ["moneyline", "total_goals"]
PARALLEL = 4
LAST: dict[str, list] = {}    # event_id → filas de partido (1X2 / totales) de la última lectura
_FETCHED = [False]
_LOCK = threading.Lock()


def key() -> str | None:
    return _oo.key()


def _get(path: str, **params):
    return _oo._get(path, **params)


def usage() -> dict:
    return {**_oo.usage(), "today": 0}


def _brand_of() -> dict:
    """Nombre de la casa como viene en cada cuota ("Betano", "1XBet") → marca de la app."""
    f = DIR / "sportsbooks.json"
    if f.exists() and time.time() - f.stat().st_mtime < 30 * 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    out = {x["name"]: BOOKS[x["id"]] for x in _get("sportsbooks").get("data") or [] if x.get("id") in BOOKS}
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ── Partidos ─────────────────────────────────────────────────────────────────

def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())


def events(lg: str, refresh: bool = False) -> list[dict]:
    """Partidos de una liga que todavía no empezaron (1 pedido, guardado FIXTURES_TTL_S)."""
    f = DIR / f"events_{lg}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < cfg.FIXTURES_TTL_S:
        data = json.loads(f.read_text(encoding="utf-8"))
    else:
        d = _get("fixtures/active", league=LEAGUE[lg])
        data = [{"event_id": str(x["id"]), "league": lg, "start_time": _epoch(x["start_date"]),
                 "home": x.get("home_team_display"), "away": x.get("away_team_display")}
                for x in d.get("data") or [] if x.get("start_date") and x.get("status") in (None, "unplayed")]
        DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if not data and _net_ok(lg):          # OpticOdds no tiene la liga: la da odds-api.net
        return [{**x, "via": "net"} for x in _on.events(lg, refresh=refresh)]
    now = time.time()
    return [x for x in data if 0 <= (x.get("start_time") or 0) - now <= cfg.ODDS_WINDOW_H * 3600]


def _net_ok(lg: str) -> bool:
    return lg in _on.LEAGUE and bool(_on.key())


def _net_leagues() -> set[str]:
    """Ligas que hoy salen de odds-api.net: las que OpticOdds devolvió vacías."""
    out = set()
    for lg in LEAGUE:
        f = DIR / f"events_{lg}.json"
        try:
            if f.exists() and not json.loads(f.read_text(encoding="utf-8")) and _net_ok(lg):
                out.add(lg)
        except Exception:  # noqa: BLE001
            pass
    return out


def league_fixtures(lg: str) -> list[dict]:
    return events(lg)


def all_fixtures() -> list[dict]:
    out = []
    for lg in cfg.LEAGUES:
        if lg in LEAGUE:
            try:
                out += events(lg)
            except Exception as exc:  # noqa: BLE001
                print(f"  OpticOdds partidos {lg}: {exc}")
    return out


def fixture_for_event(ev: dict) -> dict | None:
    """El partido de OpticOdds que corresponde a un partido de ESPN."""
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


def _slim(ev: dict, odds: list[dict]) -> dict:
    """Respuesta de fixtures/odds → lo que se guarda: remates / al arco de
    jugador y 1X2 / goles del partido."""
    brand = _brand_of()
    props, match = [], []
    for o in odds:
        bk, mid, price = brand.get(o.get("sportsbook")), o.get("market_id"), o.get("price")
        if not bk or not price:
            continue
        side, pts = (o.get("selection_line") or "").lower(), o.get("points")
        if mid in STAT_OF:
            if bk != REF and o.get("selection") and side in ("over", "under") and pts is not None:
                props.append([bk, o["selection"], STAT_OF[mid], float(pts), side, float(price)])
        elif mid == "moneyline":
            sel = (o.get("selection") or o.get("name") or "").strip().lower()
            s = ("home" if sel == (ev.get("home") or "").lower() else
                 "away" if sel == (ev.get("away") or "").lower() else
                 "draw" if sel in ("draw", "tie", "x") else None)
            if s:
                match.append([bk, "1x2", s, None, float(price)])
        elif mid == "total_goals" and side in ("over", "under") and pts is not None:
            match.append([bk, "total", side, float(pts), float(price)])
    return {**ev, "fetched_at": int(time.time()), "props": props, "match": match}


def _fetch_event(ev: dict) -> dict | None:
    if ev.get("via") == "net":
        return _on._fetch_event({k: v for k, v in ev.items() if k != "via"})
    try:
        d = _get("fixtures/odds", fixture_id=ev["event_id"], sportsbook=list(BOOKS),
                 market=MARKETS, odds_format="DECIMAL")
    except Exception as exc:  # noqa: BLE001 - un partido caído no corta el resto
        print(f"  OpticOdds {ev.get('home')} - {ev.get('away')}: {exc}")
        return None
    out = _slim(ev, ((d.get("data") or [{}])[0].get("odds")) or [])
    f = _odds_file(ev["event_id"])
    f.parent.mkdir(parents=True, exist_ok=True)
    # Cuando salen las formaciones las casas suspenden los remates: si ahora no
    # hay pero antes sí (hace menos de 6 h), se conservan los anteriores.
    if not out["props"] and f.exists() and time.time() - f.stat().st_mtime < 6 * 3600:
        try:
            old = json.loads(f.read_text(encoding="utf-8"))
            if old.get("props"):
                out["props"], out["kept_from"] = old["props"], old.get("kept_from") or old.get("fetched_at")
        except Exception:  # noqa: BLE001
            pass
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _netmap_file():
    return DIR / "netmap.json"


def _netmap() -> dict:
    try:
        return json.loads(_netmap_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _net_twin(ev: dict) -> dict | None:
    """El mismo partido en odds-api.net (por equipos y hora)."""
    from futbol.oddspapi import team_similarity
    best, score = None, 0.0
    for x in _on.events(ev["league"]):
        if abs((x.get("start_time") or 0) - ev["start_time"]) > 3 * 3600:
            continue
        sh, sa = team_similarity(ev.get("home") or "", x.get("home") or ""), team_similarity(ev.get("away") or "", x.get("away") or "")
        s = min(sh, sa) if min(sh, sa) >= 0.6 else (0.6 if max(sh, sa) >= 0.95 and min(sh, sa) >= 0.25 else 0.0)
        if s > score:
            best, score = x, s
    return best


def _fetch_net_book(evs: list[dict]) -> int:
    """Remates de bet365 de estos partidos desde odds-api.net. → créditos gastados."""
    from concurrent.futures import ThreadPoolExecutor
    evs = [e for e in evs if e.get("via") != "net" and _net_ok(e["league"])]
    if not evs:
        return 0
    mp, twins = _netmap(), []
    for e in evs:
        try:
            t = _net_twin(e)
        except Exception as exc:  # noqa: BLE001
            print(f"  odds-api.net partidos {e['league']}: {exc}")
            t = None
        if t:
            mp[e["event_id"]] = t["event_id"]
            twins.append(t)
    left = _on.usage()["remaining"] - _on.RESERVE
    twins = twins[:max(0, left)]
    before = _on._month_count()
    if twins:
        with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
            list(ex.map(_on._fetch_event, twins))
        _on.sync_usage()
    DIR.mkdir(parents=True, exist_ok=True)
    _netmap_file().write_text(json.dumps(mp), encoding="utf-8")
    return _on._month_count() - before


def refresh_fixtures(evs: list[dict]) -> int:
    """Cuotas nuevas de estos partidos: 1 pedido c/u en OpticOdds, más 1 crédito
    de odds-api.net por partido para bet365. → pedidos hechos."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        got = [x for x in ex.map(_fetch_event, evs) if x]
    try:
        n = _fetch_net_book(evs)
        print(f"  odds-api.net ({NET_BOOK}): {n} créditos")
    except Exception as exc:  # noqa: BLE001
        print(f"  odds-api.net ({NET_BOOK}): {exc}")
    if got:
        _FETCHED[0] = True
        _count_refresh()
    return len(evs)


def fetch_round() -> int:
    """Cuotas nuevas de todas las ligas (próximas ODDS_WINDOW_H horas)."""
    return refresh_fixtures(all_fixtures())


def _state_file():
    return DIR / "state.json"


def _count_refresh() -> None:
    with _LOCK:
        day = datetime.now().strftime("%Y-%m-%d")
        try:
            u = json.loads(_state_file().read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            u = {}
        u = {"refresh_days": {day: int((u.get("refresh_days") or {}).get(day, 0)) + 1}}
        DIR.mkdir(parents=True, exist_ok=True)
        _state_file().write_text(json.dumps(u), encoding="utf-8")


def refreshes_today() -> int:
    try:
        u = json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    return int((u.get("refresh_days") or {}).get(datetime.now().strftime("%Y-%m-%d"), 0))


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
    """Cuotas guardadas → tabla de oddspapi.parse (sin pedir nada)."""
    from futbol import books
    brands = set(books.net_brands())
    rows = []
    LAST.clear()
    mp = _netmap()
    for d in _cached():
        start = datetime.fromtimestamp(d["start_time"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        base = {"fixture_id": d["event_id"], "league": d["league"], "start": start,
                "home": d.get("home"), "away": d.get("away")}
        susp = bool(d.get("kept_from"))
        LAST[d["event_id"]] = d.get("match") or []
        # bet365 del mismo partido en odds-api.net (la versión principal antes que las de otros países)
        nf = _on._odds_file(mp[d["event_id"]]) if d["event_id"] in mp else None
        if NET_BOOK in brands and nf is not None and nf.exists():
            try:
                nd = json.loads(nf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                nd = {}
            if time.time() - (nd.get("fetched_at") or 0) < 2 * 86400:
                for book, name, stat, line, side, price in sorted(nd.get("props") or [], key=lambda r: (r[0] != NET_BOOK, r[0])):
                    if _on._brand(book) == NET_BOOK:
                        rows.append({**base, "book": NET_BOOK, "src": book, "player_name": name, "player_key": norm_name(name),
                                     "stat": stat, "line": line, "side": side, "price": price, "susp": bool(nd.get("kept_from"))})
        for book, name, stat, line, side, price in d.get("props") or []:
            if book not in brands:
                continue
            rows.append({**base, "book": book, "src": book, "player_name": name, "player_key": norm_name(name),
                         "stat": stat, "line": line, "side": side, "price": price, "susp": susp})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(["fixture_id", "book", "player_key", "stat", "line", "side"])
    via = _net_leagues()
    if via:
        try:
            nt = _on.table()
            LAST.update({k: v for k, v in _on.LAST.items()})
            if not nt.empty:
                nt = nt[nt["league"].isin(via)]
                df = pd.concat([df, nt], ignore_index=True) if not df.empty else nt.reset_index(drop=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  odds-api.net (ligas sin OpticOdds): {exc}")
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
    alguno todavía no tiene cuotas. Usa sólo las listas guardadas: no pide nada."""
    now, ages = time.time(), []
    for lg in LEAGUE:
        f = DIR / f"events_{lg}.json"
        if not f.exists():
            return None
        data, odds_file = json.loads(f.read_text(encoding="utf-8")), _odds_file
        if not data and _net_ok(lg):
            nf = _on.DIR / f"events_{lg}.json"
            if not nf.exists():
                return None
            data, odds_file = json.loads(nf.read_text(encoding="utf-8")), _on._odds_file
        for x in data:
            if not 0 <= (x.get("start_time") or 0) - now <= cfg.ODDS_WINDOW_H * 3600:
                continue
            g = odds_file(x["event_id"])
            if not g.exists():
                return None
            ages.append(now - g.stat().st_mtime)
    return max(ages) if ages else None


def cache_stamp() -> int | None:
    fs = list((DIR / "odds").glob("*.json")) if (DIR / "odds").exists() else []
    if _net_leagues() and (_on.DIR / "odds").exists():
        fs += list((_on.DIR / "odds").glob("*.json"))
    return int(max(f.stat().st_mtime for f in fs)) if fs else None


def refresh_cost() -> int:
    """Pedidos que haría actualizar todo ahora (no gastan plan: es informativo)."""
    n, now = 0, time.time()
    for lg in LEAGUE:
        f = DIR / f"events_{lg}.json"
        if f.exists() and now - f.stat().st_mtime < cfg.FIXTURES_TTL_S:
            n += sum(1 for x in json.loads(f.read_text(encoding="utf-8"))
                     if 0 <= (x.get("start_time") or 0) - now <= cfg.ODDS_WINDOW_H * 3600)
        else:
            n += 1 + 12
    return n


def plan_refresh() -> dict:
    u = usage()
    return {"books": ["optic"], "all": ["optic"], "used": u["used"], "limit": None,
            "remaining": u["remaining"], "cost": refresh_cost(), "cap": None}
