"""
Futbol — cuotas de remates de OddsPapi por RapidAPI (v5): bet365 AR, Betano, Betsson.

La v5 trae las cuotas de jugadores de A UN PARTIDO por pedido (las 3 casas
juntas, ~25 KB comprimido). Una actualización = 1 pedido por partido de las
próximas ODDS_WINDOW_H horas + la lista de partidos de cada liga (guardada
FIXTURES_TTL_S). Los nombres de los jugadores se piden aparte (/players) una
sola vez y quedan guardados. El resultado se guarda con el MISMO formato que
usaba la v4 (data/futbol/cache/oddspapi/odds_<casa>_*.json), así el resto de
la app no cambia. Cada pedido queda anotado en requests.csv para medir el gasto.

Formatos de mercado que llegan:
  players-shots / players-shotsongoal        escalera "1+", "2+", … (sólo Over)
  playertotals-shots / playertotals-shotsongoal  Over/Under con línea (Betsson)
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from difflib import SequenceMatcher

import httpx
import numpy as np
import pandas as pd

from futbol import books
from futbol import config as cfg
from futbol.espn import norm_name

DIR = cfg.CACHE_DIR / "oddspapi"
STAT_OF = {"players-shots": "shots", "players-shotsongoal": "sot",
           "playertotals-shots": "shots", "playertotals-shotsongoal": "sot"}
# Mercados del partido que también se guardan (vienen en la misma consulta):
# sirven de ancla para el volumen de remates (futbol.market).
MATCH_TYPES = {"1x2", "totals"}
LAST_RAW: dict[str, list[dict]] = {}     # casa → respuesta (recortada) de la última lectura
_FETCHED = [False]                       # ¿la última fetch_all consultó a OddsPapi?


def direct() -> bool:
    """¿Se usa la API directa de OddsPapi (v4) en vez de RapidAPI (v5)? (ODDS_API en .env)"""
    return cfg.odds_api() == "direct"


def net() -> bool:
    """¿Las cuotas de Fútbol salen de odds-api.net (futbol.oddsnet)? Las
    funciones públicas de este módulo le pasan el trabajo cuando es así."""
    return cfg.futbol_odds() in ("net", "optic")


def _net():
    if cfg.futbol_odds() == "optic":
        from futbol import oddsoptic
        return oddsoptic
    from futbol import oddsnet
    return oddsnet


def _get(path: str, **params):
    key = cfg.oddspapi_key()
    if not key:
        raise RuntimeError("Falta " + ("ODDSPAPI_KEY" if direct() else "RAPIDAPI_KEY") + " en backend/.env")
    if direct():
        r = httpx.get(f"{cfg.ODDSPAPI_BASE}/{path.lstrip('/')}", params={**params, "apiKey": key}, timeout=180)
    else:
        url = f"https://{cfg.RAPIDAPI_HOST}/{path.lstrip('/')}"
        r = httpx.get(url, params=params, timeout=120,
                      headers={"x-rapidapi-key": key, "x-rapidapi-host": cfg.RAPIDAPI_HOST})
    if r.status_code != 429:                 # un "esperá" no gasta pedidos del plan
        _count_usage(r, path)
    r.raise_for_status()
    return r.json()


def _get_patient(path: str, tries: int = 5, **params):
    """Como _get, pero si OddsPapi dice 429 (muy seguido) espera y reintenta.
    Usa el tiempo que indica la respuesta si lo trae."""
    for i in range(tries):
        try:
            return _get(path, **params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 or i == tries - 1:
                raise
            wait = 1.5 * (2 ** i)
            try:
                ra = exc.response.headers.get("retry-after")
                body = exc.response.json() if exc.response.content else {}
                ms = (body.get("error") or body).get("retryMs") if isinstance(body, dict) else None
                wait = max(wait, float(ra)) if ra else (max(wait, ms / 1000 + .2) if ms else wait)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(min(wait, 30))


import threading as _threading
_USAGE_LOCK = _threading.Lock()


def _count_usage(r, path: str = "") -> None:
    with _USAGE_LOCK:
        _count_usage_locked(r, path)


def _count_usage_locked(r, path: str = "") -> None:
    """Cuenta cada pedido (por mes y por día), guarda lo que informa RapidAPI
    (límite y restantes del plan) y lo anota en requests.csv."""
    from datetime import datetime, timezone
    f = DIR / "usage.json"
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        u = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    now = datetime.now(timezone.utc)
    pre = _prefix()
    month = pre + now.strftime("%Y-%m")
    u[month] = int(u.get(month, 0)) + 1
    day = _local_day()
    days = u.get(pre + "days") or {}
    days[day] = int(days.get(day, 0)) + 1
    u[pre + "days"] = dict(sorted(days.items())[-45:])
    if direct():
        f.write_text(json.dumps(u), encoding="utf-8")
        _log_request(now, path, r)
        return
    h = {k.lower(): v for k, v in r.headers.items()}
    if "x-ratelimit-requests-limit" in h:
        u["rapid"] = {"limit": int(h["x-ratelimit-requests-limit"]),
                      "remaining": int(h.get("x-ratelimit-requests-remaining", 0)),
                      "reset_s": int(h.get("x-ratelimit-requests-reset", 0)),
                      "at": now.isoformat(timespec="seconds")}
    u["since"] = u.get("rapid_since") or now.strftime("%Y-%m-%d")
    u["rapid_since"] = u["since"]
    f.write_text(json.dumps(u), encoding="utf-8")
    _log_request(now, path, r)


def _prefix() -> str:
    """Contador en usage.json: la API directa usa 'AAAA-MM' (como siempre) y
    RapidAPI 'rapid-AAAA-MM'. Cada plan se cuenta por separado."""
    return "" if direct() else "rapid-"


def _log_request(now, path: str, r) -> None:
    log = DIR / "requests.csv"
    new = not log.exists()
    with log.open("a", encoding="utf-8") as fh:
        if new:
            fh.write("utc,path,status,bytes\n")
        api = "direct" if direct() else "rapid"
        fh.write(f"{now.isoformat(timespec='seconds')},{api}:{path},{r.status_code},{len(r.content)}\n")


def usage(own: bool = False) -> dict:
    """Pedidos del plan de RapidAPI: exactos (los informa RapidAPI en cada respuesta)."""
    if net() and not own:          # own=True: el plan de OddsPapi (NBA), aunque Fútbol use odds-api.net
        return _net().usage()
    from datetime import datetime, timezone
    try:
        u = json.loads((DIR / "usage.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    if direct():
        # La API directa no informa el saldo: se cuenta acá (250 por mes calendario).
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        used = int(u.get(month, 0))
        return {"used": used, "limit": cfg.ODDSPAPI_MONTH_LIMIT, "period": "mes",
                "remaining": max(0, cfg.ODDSPAPI_MONTH_LIMIT - used), "since": u.get("since"),
                "exact": False, "today": int((u.get("days") or {}).get(_local_day(), 0)), "api": "direct"}
    rp = u.get("rapid") or {}
    month = "rapid-" + datetime.now(timezone.utc).strftime("%Y-%m")
    if rp.get("limit"):
        return {"used": rp["limit"] - rp.get("remaining", 0), "limit": rp["limit"], "period": "mes",
                "remaining": rp.get("remaining"), "since": u.get("rapid_since"), "exact": True,
                "today": int((u.get("rapid_days") or {}).get(_local_day(), 0))}
    return {"used": int(u.get(month, 0)), "limit": cfg.ODDSPAPI_MONTH_LIMIT, "period": "mes",
            "remaining": None, "since": u.get("rapid_since"), "exact": False,
            "today": int((u.get("rapid_days") or {}).get(_local_day(), 0))}


def remaining() -> int | None:
    return usage().get("remaining")


def _catalog(name: str, path: str, **params):
    f = DIR / f"{name}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    DIR.mkdir(parents=True, exist_ok=True)
    data = _get(path, **params)
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def markets() -> dict:
    return {m["marketId"]: m for m in _catalog("markets_soccer", "markets", sportId=10)}


def participants() -> dict:
    return _catalog("participants_soccer", "participants", sportId=10)


def _book_file(book: str):
    ids = "_".join(str(v["oddspapi"]) for v in cfg.LEAGUES.values())
    return DIR / f"odds_{book}_{ids}.json"


def fetch_book(book: str, refresh: bool = False, allow_fetch: bool = True) -> list[dict]:
    """Cuotas guardadas de una casa (formato v4). La descarga la hace
    fetch_all → fetch_round (un pedido por partido con todas las casas)."""
    f = _book_file(book)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return []


# ── v5: lista de partidos, cuotas por partido y nombres de jugadores ─────────

def fixtures(tournament_id: int, hours: float | None = None, refresh: bool = False) -> list[dict]:
    """Partidos de un torneo que empiezan entre hace 3 h y dentro de `hours`
    (1 pedido, guardado FIXTURES_TTL_S)."""
    hours = cfg.ODDS_WINDOW_H if hours is None else hours
    f = DIR / f"{'v4' if direct() else 'v5'}_fixtures_{tournament_id}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < cfg.FIXTURES_TTL_S:
        data = json.loads(f.read_text(encoding="utf-8"))
    elif direct():
        now = datetime.utcnow()
        raw = _get("fixtures", tournamentId=tournament_id,
                   **{"from": (now - pd.Timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "to": (now + pd.Timedelta(hours=max(hours, 48))).strftime("%Y-%m-%dT%H:%M:%SZ")})
        data = [_fixture_v4_to_v5(x) for x in raw or []]
        DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data), encoding="utf-8")
    else:
        now = int(time.time())
        data = _get("fixtures", tournamentId=tournament_id, startTimeFrom=now - 3 * 3600,
                    startTimeTo=now + int(max(hours, 48) * 3600))
        DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data), encoding="utf-8")
    now = time.time()
    return [x for x in data or [] if -3 * 3600 <= (x.get("startTime") or 0) - now <= hours * 3600]


def _team_names(sport_id: int) -> dict:
    name = "participants_soccer" if sport_id == 10 else f"participants_{sport_id}"
    return _catalog(name, "participants", sportId=sport_id)


def _fixture_v4_to_v5(x: dict) -> dict:
    """Partido de la API directa (v4) → la forma de la v5 que usa el resto del
    módulo (startTime en segundos, participantes y estado anidados)."""
    names = _team_names(int(x.get("sportId") or 10))
    st = x.get("startTime")
    return {"fixtureId": x["fixtureId"],
            "startTime": int(pd.Timestamp(st).timestamp()) if st else None,
            "tournament": {"tournamentId": x.get("tournamentId")},
            "status": {"statusId": x.get("statusId")},
            "participants": {"participant1Id": x.get("participant1Id"), "participant2Id": x.get("participant2Id"),
                             "participant1Name": names.get(str(x.get("participant1Id"))),
                             "participant2Name": names.get(str(x.get("participant2Id")))}}


def _names_file():
    return DIR / "players_names.json"


def player_names() -> dict:
    """playerId → nombre ("Apellido, Nombre"), guardado para siempre. Arranca
    con los nombres que ya traían las cuotas de la v4 (mismos ids)."""
    f = _names_file()
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    names = {}
    from altlines import config as acfg
    for g in list(DIR.glob("odds_*.json")) + list((acfg.DATA_DIR / "dd" / "odds").glob("odds_*.json")):
        try:
            for fx in json.loads(g.read_text(encoding="utf-8")):
                for b in (fx.get("bookmakerOdds") or {}).values():
                    for m in (b.get("markets") or {}).values():
                        for o in (m.get("outcomes") or {}).values():
                            for pid, pl in (o.get("players") or {}).items():
                                if pl.get("playerName") and pid != "0":
                                    names[str(pid)] = pl["playerName"]
        except Exception:  # noqa: BLE001
            continue
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(names), encoding="utf-8")
    return names


def fill_names(pids) -> dict:
    """Pide los nombres que falten (/players, de a 100 ids por pedido)."""
    names = player_names()
    need = sorted({str(p) for p in pids if str(p) not in names and str(p) != "0"})
    if direct():
        return names                 # la v4 trae los nombres con las cuotas (ver v4_slim)
    for i in range(0, len(need), 100):
        try:
            for pl in _get("players", playerIds=",".join(need[i:i + 100])) or []:
                if pl.get("playerName"):
                    names[str(pl["playerId"])] = pl["playerName"]
        except Exception as exc:  # noqa: BLE001
            print(f"  OddsPapi nombres: {exc}")
            break
    if need:
        _names_file().write_text(json.dumps(names), encoding="utf-8")
    return names


def v5_to_v4(fx: dict, odds: dict, keep) -> dict:
    """Un partido de la v5 → el formato v4 que lee el resto de la app.
    `keep(marketId)` decide qué mercados se guardan."""
    p = fx.get("participants") or {}
    st = fx.get("startTime")
    out = {"fixtureId": fx["fixtureId"], "tournamentId": (fx.get("tournament") or {}).get("tournamentId"),
           "startTime": datetime.utcfromtimestamp(st).strftime("%Y-%m-%dT%H:%M:%S.000Z") if st else None,
           "participant1Id": p.get("participant1Id"), "participant2Id": p.get("participant2Id"),
           "participant1Name": p.get("participant1Name"), "participant2Name": p.get("participant2Name"),
           "statusId": (fx.get("status") or {}).get("statusId"), "bookmakerOdds": {}}
    for book, entries in (odds or {}).items():
        ms = {}
        for x in (entries or {}).values():
            mid = x.get("marketId")
            if mid is None or not keep(int(mid)):
                continue
            o = ms.setdefault(str(mid), {"outcomes": {}})["outcomes"].setdefault(str(x.get("outcomeId")), {"players": {}})
            o["players"][str(x.get("playerId", 0))] = {"price": x.get("price"), "active": x.get("active", True),
                                                       "changedAt": x.get("changedAt")}
        if ms:
            out["bookmakerOdds"][book] = {"markets": ms}
    return out


def v4_slim(fx: dict, keep, header: dict | None = None) -> dict:
    """Partido de la API directa (v4) con sólo los mercados que `keep` acepta.
    Los nombres de jugadores que trae se guardan en players_names.json, así
    name_players no tiene que pedirlos."""
    out = {k: v for k, v in fx.items() if k != "bookmakerOdds"}
    if header:
        out.update({k: v for k, v in header.items() if v is not None and k != "bookmakerOdds"})
    bo, names = {}, {}
    for bk, b in (fx.get("bookmakerOdds") or {}).items():
        ms = {mid: m for mid, m in (b.get("markets") or {}).items() if keep(int(mid))}
        for m in ms.values():
            for o in (m.get("outcomes") or {}).values():
                for pid, pl in (o.get("players") or {}).items():
                    if pid != "0" and pl.get("playerName"):
                        names[str(pid)] = pl["playerName"]
        if ms:
            bo[bk] = {"markets": ms}
            if b.get("bookmakerFixtureId"):
                bo[bk]["fid"] = str(b["bookmakerFixtureId"])   # n° del partido en la casa (para el link)
    out["bookmakerOdds"] = bo
    if names:
        allp = player_names()
        if any(allp.get(k) != v for k, v in names.items()):
            allp.update(names)
            _names_file().write_text(json.dumps(allp), encoding="utf-8")
    return out


def to_v4(fx: dict, d: dict, keep) -> dict:
    """Respuesta de fixture_odds (v5 o directa) → partido en formato v4."""
    if "v4" in d:
        return v4_slim(d["v4"], keep, header=v5_to_v4(fx, {}, keep))
    return v5_to_v4(fx, d.get("odds") or {}, keep)


def name_players(fxs: list) -> None:
    """Pone playerName en las cuotas (pide los nombres que falten)."""
    pids = {pid for fx in fxs for b in fx["bookmakerOdds"].values() for m in b["markets"].values()
            for o in m["outcomes"].values() for pid in o["players"] if pid != "0"}
    names = fill_names(pids)
    for fx in fxs:
        for b in fx["bookmakerOdds"].values():
            for m in b["markets"].values():
                for o in m["outcomes"].values():
                    for pid, pl in o["players"].items():
                        if pid != "0":
                            pl["playerName"] = names.get(pid) or pl.get("playerName")


DIRECT_GAP_S = 1.0   # pausa entre pedidos de la API directa
PARALLEL = 8          # pedidos de cuotas al mismo tiempo (la v5 permite 10 por segundo)


def fixture_odds(fxs: list, book_ids: list):
    """Cuotas de varios partidos, de a PARALLEL a la vez (cada uno tarda ~20-30 s
    en llegar por RapidAPI). → [(partido, respuesta)]; los que fallan se saltean."""
    from concurrent.futures import ThreadPoolExecutor

    def one(fx):
        try:
            if direct():
                return fx, {"v4": _get_patient("odds", fixtureId=fx["fixtureId"], bookmakers=",".join(book_ids),
                                               oddsFormat="decimal")}
            return fx, _get("fixtures/odds", fixtureId=fx["fixtureId"], bookmakers=",".join(book_ids))
        except Exception as exc:  # noqa: BLE001 - un partido caído no corta el resto
            print(f"  OddsPapi {fx.get('fixtureId')}: {exc}")
            return fx, None

    if direct():
        # La API directa corta con 429 si llegan varios pedidos juntos: de a uno,
        # con una pausa corta y reintento si igual pide esperar.
        out = []
        for i, fx in enumerate(fxs):
            if i:
                time.sleep(DIRECT_GAP_S)
            fx_, d = one(fx)
            if d is not None:
                out.append((fx_, d))
        return out
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        return [(fx, d) for fx, d in ex.map(one, fxs) if d is not None]


def _keep_soccer(mid: int) -> bool:
    spec = markets().get(mid) or {}
    return spec.get("marketType") in STAT_OF or (spec.get("marketType") in MATCH_TYPES and spec.get("period") == "fulltime")


def fetch_round(book_ids: list) -> int:
    """Cuotas nuevas de las 4 ligas: 1 pedido por partido de la ventana (las
    casas juntas). Guarda un archivo por casa con el formato v4. → pedidos hechos."""
    before = _req_count()
    if direct():
        # API directa: 1 pedido POR CASA trae las 4 ligas enteras (mucho más
        # barato que 1 por partido).
        ids = ",".join(str(v["oddspapi"]) for v in cfg.LEAGUES.values())
        for b in book_ids:
            try:
                data = _book_tournaments(b, ids)
            except Exception as exc:  # noqa: BLE001 - una casa caída no corta las demás
                print(f"  OddsPapi {b}: {exc}")
                continue
            f = _book_file(b)
            DIR.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(_keep_through_suspension(data, f)), encoding="utf-8")
        return _req_count() - before
    fxs = []
    for spec in cfg.LEAGUES.values():
        fxs += [x for x in fixtures(spec["oddspapi"]) if (x.get("status") or {}).get("statusId") == 0]
    per_book = {b: [] for b in book_ids}
    for fx, d in fixture_odds(fxs, book_ids):
        conv = to_v4(fx, d, _keep_soccer)
        for b in book_ids:
            per_book[b].append({**conv, "bookmakerOdds": {b: conv["bookmakerOdds"][b]} if b in conv["bookmakerOdds"] else {}})
    name_players([fx for lst in per_book.values() for fx in lst])
    DIR.mkdir(parents=True, exist_ok=True)
    for b, lst in per_book.items():
        f = _book_file(b)
        f.write_text(json.dumps(_keep_through_suspension(lst, f)), encoding="utf-8")
    return _req_count() - before


def refresh_fixtures(fxs: list, book_ids: list | None = None) -> int:
    """Cuotas nuevas SÓLO de estos partidos (1 pedido c/u, todas las casas
    juntas). Se suman a las guardadas: las de los otros partidos no se tocan.
    → pedidos hechos."""
    if net():
        return _net().refresh_fixtures(fxs)
    book_ids = book_ids or books.oddspapi_ids()
    before = _req_count()
    if direct() and len(fxs) > len(book_ids):
        # Muchos partidos (una liga entera): sale más barato 1 pedido por casa
        # con los torneos de esos partidos, quedándose sólo con ellos.
        want = {x["fixtureId"] for x in fxs}
        tids = ",".join(sorted({str((x.get("tournament") or {}).get("tournamentId")) for x in fxs}))
        convs, got = {}, []
        for b in book_ids:
            try:
                for fx in _book_tournaments(b, tids):
                    if fx["fixtureId"] in want:
                        c = convs.setdefault(fx["fixtureId"], {**fx, "bookmakerOdds": {}})
                        c["bookmakerOdds"].update(fx.get("bookmakerOdds") or {})
                got.append(b)
            except Exception as exc:  # noqa: BLE001
                print(f"  OddsPapi {b}: {exc}")
        convs = list(convs.values())
    else:
        got = fixture_odds(fxs, book_ids)
        convs = [to_v4(fx, d, _keep_soccer) for fx, d in got]
    per_book = {b: [{**c, "bookmakerOdds": {b: c["bookmakerOdds"][b]} if b in c["bookmakerOdds"] else {}}
                    for c in convs] for b in book_ids}
    name_players([fx for lst in per_book.values() for fx in lst])
    DIR.mkdir(parents=True, exist_ok=True)
    old_cut = time.time() - 2 * 86400
    for b, lst in per_book.items():
        f = _book_file(b)
        old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        lst = _keep_through_suspension(lst, f)
        ids = {x["fixtureId"] for x in lst}

        def recent(x):
            try:
                return pd.Timestamp(x.get("startTime")).timestamp() >= old_cut
            except Exception:  # noqa: BLE001
                return True
        merged = [x for x in old if x.get("fixtureId") not in ids and recent(x)] + lst
        f.write_text(json.dumps(merged), encoding="utf-8")
    if got:
        _FETCHED[0] = True
    return _req_count() - before


_FID_INDEX: dict = {}


def book_fixture_ids(fixture_id) -> dict:
    """Número del partido en cada casa ({casa: id}), tal como lo guardó la última
    descarga. Betano usa el mismo número en todos sus países."""
    if net():
        return {}                     # odds-api.net no da el n° del partido en cada casa
    if not fixture_id:
        return {}
    out = {}
    for f in _book_files():
        if not f.exists():
            continue
        key = (str(f), f.stat().st_mtime)
        idx = _FID_INDEX.get(key)
        if idx is None:
            idx = {}
            try:
                for fx in json.loads(f.read_text(encoding="utf-8")):
                    for bk, b in (fx.get("bookmakerOdds") or {}).items():
                        if b.get("fid"):
                            e = idx.setdefault(fx["fixtureId"], {})
                            e[bk] = b["fid"]
                            mk = markets()
                            if any((mk.get(int(m)) or {}).get("marketType") in STAT_OF for m in b.get("markets") or {}):
                                e.setdefault("_props", []).append(bk)       # ya publicó remates de jugador
                            e["_teams"] = (str(fx.get("participant1Id")), str(fx.get("participant2Id")),
                                           fx.get("participant1Name"), fx.get("participant2Name"))
            except Exception:  # noqa: BLE001
                pass
            for k in [k for k in _FID_INDEX if k[0] == str(f)]:
                del _FID_INDEX[k]
            _FID_INDEX[key] = idx
        out.update(idx.get(fixture_id) or {})
    return out


MAX_TOURNAMENTS = 5             # tope de tournamentIds por pedido en odds-by-tournaments


def _book_tournaments(book: str, tournament_ids: str) -> list[dict]:
    """API directa: todas las cuotas de una casa en esos torneos (1 pedido),
    recortadas a los mercados que usamos (la respuesta entera pesa ~20 MB).
    OddsPapi acepta hasta 5 torneos por pedido: si hay más, se piden en tandas."""
    ids = [t for t in str(tournament_ids).split(",") if t]
    data = []
    for i in range(0, len(ids), MAX_TOURNAMENTS):
        data += _get("odds-by-tournaments", bookmaker=book,
                     tournamentIds=",".join(ids[i:i + MAX_TOURNAMENTS]), oddsFormat="decimal") or []
    names = _team_names(10)
    out = []
    for fx in data or []:
        s = v4_slim(fx, _keep_soccer)
        s.setdefault("participant1Name", names.get(str(fx.get("participant1Id"))))
        s.setdefault("participant2Name", names.get(str(fx.get("participant2Id"))))
        out.append(s)
    return out


def league_fixtures(lg: str) -> list:
    """Partidos de una liga que todavía no empezaron (ventana ODDS_WINDOW_H)."""
    if net():
        return _net().league_fixtures(lg)
    tid = cfg.LEAGUES[lg]["oddspapi"]
    return [x for x in fixtures(tid) if (x.get("status") or {}).get("statusId") == 0]


def fixture_for_event(ev: dict) -> dict | None:
    """El partido de OddsPapi (v5) que corresponde a un partido de ESPN."""
    if net():
        return _net().fixture_for_event(ev)
    lg = ev.get("league")
    if lg not in cfg.LEAGUES:
        return None
    cands = fixtures(cfg.LEAGUES[lg]["oddspapi"])
    t = pd.Timestamp(ev["date"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    # OddsPapi a veces deja el mismo cruce dos veces (uno viejo suspendido o
    # reprogramado, sin mercados): a igual parecido gana el que no empezó
    # (statusId 0) y después el de horario más cercano al de ESPN.
    best, rank = None, None
    for x in cands:
        p = x.get("participants") or {}
        st = pd.Timestamp(x.get("startTime"), unit="s", tz="UTC")
        if abs(st - t) > pd.Timedelta(hours=30):
            continue
        sh, sa = team_similarity(ev["home"], p.get("participant1Name") or ""), team_similarity(ev["away"], p.get("participant2Name") or "")
        s = min(sh, sa)
        if abs(st - t) <= pd.Timedelta(hours=3) and max(sh, sa) >= 0.95 and min(sh, sa) >= 0.25:
            s = max(s, 0.9)
        r = (s, (x.get("status") or {}).get("statusId") == 0, -abs((st - t).total_seconds()))
        if rank is None or r > rank:
            best, rank = x, r
    return best if rank and rank[0] >= 0.6 else None


def _req_count() -> int:
    try:
        return int(json.loads((DIR / "usage.json").read_text(encoding="utf-8")).get(
            _prefix() + datetime.utcnow().strftime("%Y-%m"), 0))
    except Exception:  # noqa: BLE001
        return 0


def refresh_cost() -> int:
    """Pedidos que costaría actualizar ahora (sin gastar: usa las listas guardadas)."""
    if net():
        return _net().refresh_cost()
    if direct():
        tandas = -(-len(cfg.LEAGUES) // MAX_TOURNAMENTS)
        return len(books.oddspapi_ids()) * tandas     # por casa: 1 pedido cada 5 ligas
    n, lists = 0, 0
    now = time.time()
    for spec in cfg.LEAGUES.values():
        f = DIR / f"v5_fixtures_{spec['oddspapi']}.json"
        if f.exists() and now - f.stat().st_mtime < cfg.FIXTURES_TTL_S:
            n += sum(1 for x in json.loads(f.read_text(encoding="utf-8")) or []
                     if (x.get("status") or {}).get("statusId") == 0
                     and 0 <= (x.get("startTime") or 0) - now <= cfg.ODDS_WINDOW_H * 3600)
        else:
            lists += 1
            n += 12                                   # estimación: ~12 partidos por liga en 4 días
    return n + lists


KEEP_SUSPENDED_MAX_S = 6 * 3600


def _has_live_players(fx: dict) -> bool:
    """¿Hay alguna cuota de jugador activa (no suspendida) en este partido?"""
    for b in (fx.get("bookmakerOdds") or {}).values():
        for m in (b.get("markets") or {}).values():
            for o in (m.get("outcomes") or {}).values():
                for pl in (o.get("players") or {}).values():
                    if pl.get("playerName") and pl.get("active", True) and pl.get("price"):
                        return True
    return False


def _keep_through_suspension(new: list[dict], f) -> list[dict]:
    """bet365 suspende TODO el partido un rato antes de empezar (cuando salen
    las formaciones). Si justo en ese momento se bajan las cuotas, el partido
    queda sin precios. En ese caso se conservan las de la descarga anterior
    (si tienen menos de KEEP_SUSPENDED_MAX_S) y se marca de cuándo son."""
    if not f.exists() or time.time() - f.stat().st_mtime > KEEP_SUSPENDED_MAX_S:
        return new
    try:
        old = {fx["fixtureId"]: fx for fx in json.loads(f.read_text(encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return new
    old_at = datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
    for i, fx in enumerate(new):
        prev = old.get(fx.get("fixtureId"))
        if prev and not _has_live_players(fx) and _has_live_players(prev):
            new[i] = {**fx, "bookmakerOdds": prev["bookmakerOdds"], "kept_from": prev.get("kept_from") or old_at}
    return new


def parse(data: list[dict], book: str) -> pd.DataFrame:
    """→ una fila por (partido, jugador, stat, línea, lado) con su cuota."""
    mk = markets()
    names = participants()
    league_of = {v["oddspapi"]: k for k, v in cfg.LEAGUES.items()}
    rows = []
    for fx in data:
        lg = league_of.get(fx.get("tournamentId"))
        if lg is None:
            continue
        base = {"fixture_id": fx["fixtureId"], "league": lg, "start": fx.get("startTime"),
                "home": names.get(str(fx["participant1Id"])) or fx.get("participant1Name"),
                "away": names.get(str(fx["participant2Id"])) or fx.get("participant2Name"),
                "book": book}
        # bet365 suspende el partido ENTERO cuando salen las formaciones: en ese
        # caso se usan los últimos precios (marcados "susp"). Si el partido tiene
        # cuotas activas, un jugador inactivo es un jugador bajado: se descarta.
        susp = not _has_live_players(fx)
        for b in (fx.get("bookmakerOdds") or {}).values():
            for mid, m in (b.get("markets") or {}).items():
                spec = mk.get(int(mid))
                if not spec or spec.get("marketType") not in STAT_OF:
                    continue
                stat = STAT_OF[spec["marketType"]]
                oname = {str(o["outcomeId"]): o["outcomeName"] for o in spec["outcomes"]}
                ladder = spec["marketType"].startswith("players-")
                for oid, o in (m.get("outcomes") or {}).items():
                    label = oname.get(str(oid), "")
                    for pl in (o.get("players") or {}).values():
                        if not pl.get("price") or (not pl.get("active", True) and not susp):
                            continue
                        if ladder:
                            if not label.endswith("+"):
                                continue                       # "0" exacto: no lo usamos
                            line, side = int(label[:-1]) - 0.5, "over"
                        else:
                            line, side = float(spec.get("handicap") or 0), label.lower()
                        rows.append({**base, "player_name": pl.get("playerName"),
                                     "player_key": norm_name(pl.get("playerName")),
                                     "stat": stat, "line": line, "side": side,
                                     "price": float(pl["price"]), "susp": susp})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(["fixture_id", "player_key", "stat", "line", "side"])
    return df


def _local_day() -> str:
    return datetime.now().strftime("%Y-%m-%d")       # día de acá (la PC está en Argentina)


def refreshes_today() -> int:
    if net():
        return _net().refreshes_today()
    try:
        u = json.loads((DIR / "usage.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    return int((u.get("refresh_days") or {}).get(_local_day(), 0))


def _count_refresh() -> None:
    f = DIR / "usage.json"
    try:
        u = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    day = _local_day()
    u["refresh_days"] = {day: int((u.get("refresh_days") or {}).get(day, 0)) + 1}
    f.write_text(json.dumps(u), encoding="utf-8")


def _book_files() -> list:
    ids = "_".join(str(v["oddspapi"]) for v in cfg.LEAGUES.values())
    return [DIR / f"odds_{book}_{ids}.json" for book in books.oddspapi_ids()]


def cache_age_s() -> float | None:
    """Antigüedad de las cuotas guardadas (la casa más vieja que tenga archivo)."""
    if net():
        return _net().cache_age_s()
    ages = [time.time() - f.stat().st_mtime for f in _book_files() if f.exists()]
    return max(ages) if ages else None


def cache_stamp() -> int | None:
    """Marca de las cuotas guardadas (cambia con cada descarga)."""
    if net():
        return _net().cache_stamp()
    ts = [int(f.stat().st_mtime) for f in _book_files() if f.exists()]
    return max(ts) if ts else None


def plan_refresh() -> dict:
    """¿Se puede actualizar ahora sin pasarse del plan de RapidAPI? Una
    actualización cuesta ~1 pedido por partido de la ventana (todas las casas)."""
    if net():
        return _net().plan_refresh()
    allb = books.oddspapi_ids()
    u = usage()
    cost = refresh_cost()
    left = u["remaining"] if u.get("remaining") is not None else u["limit"] - u["used"]
    ok = left - cost >= cfg.RAPIDAPI_RESERVE
    return {"books": allb if ok else [], "all": allb, "used": u["used"], "limit": u["limit"],
            "remaining": left, "cost": cost, "cap": u["limit"] - cfg.RAPIDAPI_RESERVE}


def maybe_refresh(max_age_s: float | None = None, day_cap: int | None = None) -> dict:
    """Con la v5 se paga por partido: la app ya NO pide cuotas por su cuenta
    (ni al simular). Se piden con los botones de partido, de liga o "Cuotas nuevas"."""
    age = cache_age_s()
    return {"refreshed": False, "reason": "manual", "age_s": None if age is None else round(age),
            "today": refreshes_today(), "cap": cfg.ODDS_REFRESH_DAY_CAP}


def fetch_all(refresh: bool = False, allow_fetch: bool = True, only: list | None = None) -> pd.DataFrame:
    """Cuotas de todas las casas activas (books.json). Si hay que actualizar
    (refresh, o el caché venció y allow_fetch), se piden de nuevo con la v5:
    1 pedido por partido, todas las casas juntas."""
    _FETCHED[0] = False
    if net():
        return _fetch_all_net(refresh, allow_fetch, only)
    allb = books.oddspapi_ids()
    # Sólo si se pide explícitamente (botón "Cuotas nuevas"): ya no se piden
    # solas aunque estén viejas, porque cada partido gasta 1 pedido.
    if allow_fetch and refresh and allb:
        if only is None:
            only = plan_refresh()["books"]
        if only:
            try:
                n = fetch_round(allb)
                _FETCHED[0] = True
                print(f"  OddsPapi (RapidAPI): {n} pedidos")
            except Exception as exc:  # noqa: BLE001
                print(f"  OddsPapi (RapidAPI): {exc}")
    frames = []
    for book in allb:
        try:
            raw = fetch_book(book)
            LAST_RAW[book] = raw
            frames.append(parse(raw, book))
        except Exception as exc:  # noqa: BLE001 - una casa caída no corta las demás
            print(f"  OddsPapi {book}: {exc}")
    if _FETCHED[0]:
        _count_refresh()
    if any(b["id"] == "polymarket" for b in books.enabled("polymarket")):
        try:
            from futbol import polymarket as pm
            frames.append(pm.fetch(refresh=refresh))
        except Exception as exc:  # noqa: BLE001
            print(f"  Polymarket: {exc}")
    frames = [f for f in frames if not f.empty]
    return _unify_fixtures(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()


def _fetch_all_net(refresh: bool, allow_fetch: bool, only: list | None) -> pd.DataFrame:
    """fetch_all con odds-api.net: sólo las marcas elegidas (sin Polymarket)."""
    on = _net()
    on._FETCHED[0] = False
    if allow_fetch and refresh:
        if only is None:
            only = on.plan_refresh()["books"]
        if only:
            try:
                n = on.fetch_round()
                print(f"  {cfg.futbol_odds()}: {n} pedidos")
            except Exception as exc:  # noqa: BLE001
                print(f"  odds-api.net: {exc}")
    _FETCHED[0] = on._FETCHED[0]
    return on.table()


def _unify_fixtures(df: pd.DataFrame) -> pd.DataFrame:
    """Polymarket (pm-…) trae su propio id de partido. Se le pone el id de
    OddsPapi del mismo partido para que sus cuotas se sumen a las de las otras
    casas (si no, cada partido usaba un solo origen)."""
    foreign = df["fixture_id"].astype(str).str.startswith("pm-")
    if not foreign.any() or foreign.all():
        return df
    base = df[~foreign]
    remap = {}
    for r in df[foreign].drop_duplicates("fixture_id").itertuples():
        fx = match_fixture({"league": r.league, "date": r.start, "home": r.home, "away": r.away}, base)
        if fx is not None:
            remap[r.fixture_id] = fx
    if remap:
        df = df.copy()
        df["fixture_id"] = df["fixture_id"].map(lambda x: remap.get(x, x))
    return df


# ── Cruce con ESPN ────────────────────────────────────────────────────────────

_CLUB_WORDS = {"ca", "cd", "club", "atletico", "fc", "sc", "ec", "se", "cr", "afc", "de", "the",
               "deportivo", "sport", "esporte", "clube", "futebol", "regatas"}


# Sufijos de estado que ESPN usa en Brasil ("Atlético-MG") y las casas escriben enteros.
_TEAM_WORD_ALIAS = {"mg": "mineiro", "pr": "paranaense", "go": "goianiense"}


def _team_key(name: str) -> str:
    return " ".join(_TEAM_WORD_ALIAS.get(w, w) for w in norm_name(name).split() if w not in _CLUB_WORDS)


def team_similarity(a: str, b: str) -> float:
    ka, kb = _team_key(a), _team_key(b)
    if not ka or not kb:
        return 0.0
    if ka in kb or kb in ka:
        return 1.0
    return SequenceMatcher(None, ka, kb).ratio()


def match_fixture(ev: dict, fixtures: pd.DataFrame) -> str | None:
    """fixture_id de OddsPapi para un partido de ESPN (misma liga, ±1 día, nombres)."""
    if fixtures.empty:
        return None
    c = fixtures[fixtures["league"] == ev["league"]].drop_duplicates("fixture_id")
    t = pd.Timestamp(ev["date"])
    c = c[(pd.to_datetime(c["start"], utc=True, format="ISO8601") - t).abs() <= pd.Timedelta(hours=30)]
    best, score = None, 0.0
    for r in c.itertuples():
        sh, sa = team_similarity(ev["home"], r.home), team_similarity(ev["away"], r.away)
        s = min(sh, sa)
        # Un equipo idéntico y el mismo horario alcanzan aunque el otro nombre
        # sea muy distinto ("Athletico-PR" / "Atletico Paranaense").
        same_time = abs(pd.Timestamp(r.start) - t) <= pd.Timedelta(hours=3)
        if same_time and max(sh, sa) >= 0.95 and min(sh, sa) >= 0.25:
            s = max(s, 0.9)
        if s > score:
            best, score = r.fixture_id, s
    return best if score >= 0.6 else None


# Apodos de primer nombre que las casas y ESPN usan indistinto.
NICK = {"kike": "enrique", "quique": "enrique", "nacho": "ignacio", "pepe": "jose", "paco": "francisco",
        "pancho": "francisco", "chema": "jose", "juanma": "juan"}
# Mismo jugador con otro nombre de pila (segundo nombre): casa → ESPN.
NAME_ALIAS = {"ignacio malcorra": "victor malcorra"}


def _first_ok(a: list, b: list) -> bool:
    """El apellido igual sólo suma si el primer nombre no choca: "kauan vitor"
    no es "ze vitor" aunque compartan "vitor". Vale el mismo nombre, una
    inicial ("j alvarez") o un nombre parecido ("joao"/"joão" ya normalizados).
    Si un lado es una sola palabra no suma: "marlon freitas" (Botafogo) no es
    "freitas" (Remo); las casas no dicen el equipo y los dos planteles van juntos."""
    if len(a) < 2 or len(b) < 2:
        return False
    x, y = NICK.get(a[0], a[0]), NICK.get(b[0], b[0])
    if x == y or x.startswith(y) or y.startswith(x):
        return True
    return SequenceMatcher(None, x, y).ratio() >= 0.6


def _typo(a: str, b: str, swap: bool = True) -> bool:
    """A lo sumo una letra de diferencia (de más o de menos; cambiada sólo con
    `swap`). Sólo en nombres de 5+ letras: "ruan"/"juan" son dos jugadores."""
    if a == b or min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 1:
        return a == b
    if len(a) == len(b):
        return swap and sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    i = next((j for j, (x, y) in enumerate(zip(a, b)) if x != y), len(a))
    return a[i:] == b[i + 1:]


def match_player(name_key: str, candidates: dict) -> str | None:
    """player_key de OddsPapi → player_id de ESPN (dentro de los dos planteles)."""
    if name_key in candidates:
        return candidates[name_key]
    if NAME_ALIAS.get(name_key) in candidates:
        return candidates[NAME_ALIAS[name_key]]
    # Nombre legal completo de un lado y corto del otro: "alberto campo" ⊂
    # "driden alberto campo deusa", "emerson" ⊂ "emerson royal". Sólo si hay
    # un único candidato así.
    toks = set(name_key.split())
    # El lado corto necesita 2+ palabras: un apodo de una palabra ("Pedro",
    # "Matheus") cabe en muchos nombres legales y cruzaría al jugador equivocado.
    sub = [pid for k, pid in candidates.items()
           if toks and min(len(toks), len(k.split())) >= 2
           and (toks <= set(k.split()) or set(k.split()) <= toks)]
    if len(set(sub)) == 1:
        return sub[0]
    parts = name_key.split()
    # La casa da sólo el apellido ("Kenji"): vale si un único jugador de los dos
    # planteles se apellida así ("Victor" con Marcos Victor y João Victor, no).
    if len(parts) == 1:
        same = {pid for k, pid in candidates.items() if k.split()[-1:] == parts}
        if len(same) == 1:
            return same.pop()
        if same:
            return None
    best_by_pid: dict = {}
    for k, pid in candidates.items():
        kp = k.split()
        s = SequenceMatcher(None, name_key, k).ratio()
        if parts and kp and parts[-1] == kp[-1] and _first_ok(parts, kp):
            s += 0.25                                   # mismo apellido
        elif (len(parts) == 1 and len(kp) == 1 and _typo(name_key, k, swap=len(k) >= 8)) or (
                len(parts) >= 2 and len(kp) >= 2 and _typo(parts[0], kp[0])
                and _typo("".join(parts[1:]), "".join(kp[1:]))):
            # Una letra de diferencia: "Matheuzinho"/"Mateuzinho", "Timur Tuterov"/"Tymur Tutierov",
            # "Demartini"/"De Martini". No "Andrei"/"Andrew" ni "Fernando Juárez"/"Facundo Suárez".
            s = max(s, 0.9)
        else:
            continue                                    # "Santiago Sosa" no es "Santiago Solari"
        best_by_pid[pid] = max(s, best_by_pid.get(pid, 0.0))
    ranked = sorted(best_by_pid.items(), key=lambda x: -x[1])
    if not ranked or ranked[0][1] < 0.82:
        return None
    # Dos jugadores casi empatados: mejor no cruzar que cruzar al equivocado.
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.05:
        return None
    return ranked[0][0]


# ── Precio justo del libro ───────────────────────────────────────────────────

def fair_prob(df: pd.DataFrame) -> pd.Series:
    """Probabilidad implícita sin margen.

    Over/Under con los dos lados: se normaliza el par. Escaleras de un solo
    lado ("1+, 2+…"): no se ve el margen en el par, se usa el método potencia
    p = p_impl ** γ con γ = MARGIN_GAMMA (las casas cargan más a las cuotas
    largas). Es aproximado: el modelo no depende de esto.
    """
    # Vectorizado: antes era un bucle por grupo (~50.000) y tardaba más de un minuto.
    p_imp = 1.0 / df["price"]
    keys = ["book", "fixture_id", "player_key", "stat", "line"]
    gid = df.groupby(keys, sort=False, dropna=True).ngroup()   # -1: falta alguna clave
    side = df["side"]
    both = ((side == "over").groupby(gid).transform("any")
            & (side == "under").groupby(gid).transform("any"))
    pair = p_imp / p_imp.groupby(gid).transform("sum")
    ladder = np.clip(p_imp ** MARGIN_GAMMA, 1e-4, 0.999)
    return pd.Series(np.where(both, pair, ladder), index=df.index).where(gid >= 0)


MARGIN_GAMMA = 1.08


def match_markets(fixture_id: str) -> dict | None:
    """1X2 sin margen y P(Over) por línea de goles, promediados entre casas,
    de la última lectura (no consulta nada)."""
    if net():
        return _net().match_markets(fixture_id)
    mk = markets()
    p1x2, totals = [], {}
    for raw in LAST_RAW.values():
        fx = next((f for f in raw if f.get("fixtureId") == fixture_id), None)
        if not fx:
            continue
        for b in (fx.get("bookmakerOdds") or {}).values():
            for mid, m in (b.get("markets") or {}).items():
                spec = mk.get(int(mid)) or {}
                if spec.get("period") != "fulltime" or spec.get("marketType") not in MATCH_TYPES:
                    continue
                oname = {str(o["outcomeId"]): o["outcomeName"] for o in spec["outcomes"]}
                pr = {}
                for oid, o in (m.get("outcomes") or {}).items():
                    for pl in (o.get("players") or {}).values():
                        if pl.get("active", True) and pl.get("price"):
                            pr[oname.get(str(oid), "")] = float(pl["price"])
                if spec["marketType"] == "1x2" and {"1", "X", "2"} <= set(pr):
                    q = [1 / pr["1"], 1 / pr["X"], 1 / pr["2"]]
                    p1x2.append([x / sum(q) for x in q])
                elif spec["marketType"] == "totals" and {"Over", "Under"} <= set(pr):
                    line = float(spec.get("handicap") or 0)
                    if abs(line % 1 - 0.5) < 1e-9 and 0.5 <= line <= 5.5:
                        q = [1 / pr["Over"], 1 / pr["Under"]]
                        totals.setdefault(line, []).append(q[0] / sum(q))
    if not p1x2 and not totals:
        return None
    return {"p1x2": tuple(float(np.mean([x[i] for x in p1x2])) for i in range(3)) if p1x2 else None,
            "totals": {l: float(np.mean(v)) for l, v in totals.items()}}
