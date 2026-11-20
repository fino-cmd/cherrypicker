"""
NBA — cuotas de odds-api.net (alternativa a OddsPapi, se elige con
NBA_ODDS=net en backend/.env; es la misma cuenta y el mismo plan que Fútbol).

Un pedido por partido trae todo junto: doble-doble, triple-doble y las líneas
de jugador (puntos, rebotes, asistencias, triples y combinaciones) de las
marcas con "net": true y "nba": true en futbol/books.json, más Pinnacle como
referencia sin margen. Las versiones por país de cada marca (bet365, bet365_es,
betano_de…) se juntan en una sola caja, prefiriendo la más fresca.

Lo que baja se guarda con la misma forma que usaba OddsPapi (odds_<casa>.json
del doble-doble, oddspapi.json de las líneas y net_<partido>.json de Pinnacle),
así el resto de la app no cambia.

No hay historial de la cuota (el "pulso de la línea" queda sólo con el precio
actual) ni 1xBet; Betsson y Betano dependen de lo que publique odds-api.net.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

REF = "pinnacle"
METRIC_DD = {"player double doubles": "dd", "player triple doubles": "td"}
DD_MID = {"dd": "112602", "td": "112603"}      # ids de mercado que lee dd_live._odds_rows
PARALLEL = 4


def on() -> bool:
    """¿NBA usa una fuente alternativa a OddsPapi? 'net' (odds-api.net) u 'optic'
    (OpticOdds, altlines.odds_optic: este módulo le pasa el trabajo)."""
    return (os.getenv("NBA_ODDS") or "").strip().lower() in ("net", "optic")


def optic() -> bool:
    return (os.getenv("NBA_ODDS") or "").strip().lower() == "optic"


def brands() -> dict[str, str]:
    """Marca en odds-api.net → id de la casa en la app (futbol/books.json)."""
    from futbol import books
    return {br: b["id"] for br, b in books.net_brands().items() if b.get("nba")}


def book_ids() -> list[str]:
    if optic():
        from altlines import odds_optic
        return odds_optic.book_ids()
    return list(brands().values())


def usage() -> dict:
    if optic():
        from altlines import odds_optic
        return odds_optic.usage()
    from futbol import oddsnet
    return oddsnet.usage()


def _wanted() -> list[str]:
    from futbol import oddsnet
    br = set(brands()) | {REF}
    return sorted(b for b in oddsnet.catalog() if oddsnet._brand(b) in br)


def fetch_game(eid: str, start: float | None = None) -> dict:
    """Cuotas de un partido (1 crédito) → doble-doble, líneas de las casas y Pinnacle."""
    from futbol import oddsnet
    from altlines import props_live as pl
    d = oddsnet._get(f"events/{eid}/odds/snapshot", types="player prop", periods="full time",
                     price_fields="odds", limit=25000, bookmakers=",".join(_wanted()) or None)
    br = brands()
    stale = set(d.get("stale_bookmakers") or [])
    # De cada marca gana la versión más fresca; a igualdad, la principal ("bet365").
    items = sorted(d.get("items") or [], key=lambda x: (
        x.get("bookmaker") in stale, x.get("bookmaker") != oddsnet._brand(x.get("bookmaker") or ""),
        x.get("bookmaker") or ""))
    seen, props, ref, ddr = set(), [], [], []
    for x in items:
        if not x.get("is_available", True) or not isinstance(x.get("odds"), (int, float)) or not x.get("player_name"):
            continue
        brand = oddsnet._brand(x.get("bookmaker") or "")
        book = REF if brand == REF else br.get(brand)
        if not book:
            continue
        parts = str(x.get("line") or "").split()
        if len(parts) != 2 or parts[0] not in ("over", "under"):
            continue
        try:
            ln = float(parts[1])
        except ValueError:
            continue
        metric = x.get("metric") or ""
        key = pl._pkey(x["player_name"])
        k = (book, metric, parts[0], ln, key)
        if k in seen:
            continue
        seen.add(k)
        if metric in METRIC_DD:
            if parts[0] == "over" and book != REF:               # "over 0.5" = Sí
                ddr.append([book, METRIC_DD[metric], x["player_name"], float(x["odds"])])
            continue
        mk = pl.NET_METRIC.get(metric)
        if not mk:
            continue
        row = {"book": book, "mk": mk, "side": parts[0], "line": ln, "odds": float(x["odds"]), "player": key}
        (ref if book == REF else props).append(row)
    return {"eid": str(eid), "t": time.time(), "start": start, "dd": ddr, "props": props, "ref": ref}


def _iso(t: float | None) -> str | None:
    return datetime.fromtimestamp(t, timezone.utc).isoformat() if t else None


def _dd_entry(g: dict, book: str) -> dict:
    markets: dict = {}
    for b, mk, name, price in g["dd"]:
        if b != book:
            continue
        from altlines import dd_live
        players = markets.setdefault(DD_MID[mk], {"outcomes": {"1": {"players": {}}}})["outcomes"]["1"]["players"]
        players[dd_live._key(name)] = {"price": price, "active": True, "playerName": name}
    return {"fixtureId": g["eid"], "startTime": _iso(g["start"]), "source": "net",
            "bookmakerOdds": {book: {"markets": markets}} if markets else {}}


def _same_game(old_start, start: float | None, old_players: set, players: set) -> bool:
    """¿Una entrada vieja (p. ej. de OddsPapi) es de este mismo partido?"""
    from altlines import props_live as pl
    return bool(start and old_players & players and abs(pl._ts(old_start) - start) < 3600)


def save(got: list[dict], merge: bool = False) -> tuple[int, int]:
    """Guarda lo bajado. `merge`: pedido de un solo partido; reemplaza lo suyo y
    deja lo de los demás. → (casas con doble-doble, líneas de jugador)."""
    from altlines import dd_live, props_live as pl
    t = time.time()
    pl.DIR.mkdir(parents=True, exist_ok=True)
    dd_live.ODDS_DIR.mkdir(parents=True, exist_ok=True)
    ids = {g["eid"] for g in got}
    # Pinnacle (la referencia) de cada partido
    for g in got:
        (pl.DIR / f"net_{g['eid']}.json").write_text(json.dumps({"t": g["t"], "rows": g["ref"]}), encoding="utf-8")
    # Líneas de jugador
    rows = [{**r, "start": g["start"], "fx": g["eid"]} for g in got for r in g["props"]]
    f = pl.DIR / "oddspapi.json"
    if merge and f.exists():
        old = json.loads(f.read_text(encoding="utf-8"))
        names = {g["eid"]: {r["player"] for r in g["props"]} for g in got}
        keep = [r for r in old.get("rows") or [] if not (
            str(r.get("fx")) in ids
            or any(_same_game(r.get("start"), g["start"], {r["player"]}, names[g["eid"]]) for g in got))]
        f.write_text(json.dumps({"t": old.get("t", t), "rows": keep + rows}), encoding="utf-8")
    else:
        f.write_text(json.dumps({"t": t, "rows": rows}), encoding="utf-8")
    # Doble-doble / triple-doble, un archivo por casa
    books = 0
    for b in book_ids():
        new = [_dd_entry(g, b) for g in got]
        books += any(e["bookmakerOdds"] for e in new)
        bf = dd_live._odds_file(b)
        if merge and bf.exists():
            old_m = bf.stat().st_mtime
            try:
                lst = json.loads(bf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                lst = []
            names = {g["eid"]: {dd_live._key(n) for _, _, n, _ in g["dd"]} for g in got}

            def players_of(fx: dict) -> set:
                return {dd_live._key(p.get("playerName"))
                        for bo in (fx.get("bookmakerOdds") or {}).values()
                        for m in (bo.get("markets") or {}).values()
                        for o in (m.get("outcomes") or {}).values()
                        for p in (o.get("players") or {}).values()}
            lst = [x for x in lst if not (
                str(x.get("fixtureId")) in ids
                or any(_same_game(x.get("startTime"), g["start"], players_of(x), names[g["eid"]]) for g in got))]
            bf.write_text(json.dumps(lst + new), encoding="utf-8")
            os.utime(bf, (old_m, old_m))        # la antigüedad de la noche sigue siendo la del pedido general
        else:
            bf.write_text(json.dumps(new), encoding="utf-8")
    return books, len(rows)


def refresh_all() -> dict:
    """Cuotas de todos los partidos de la noche que no empezaron: 1 crédito por partido."""
    if optic():
        from altlines import odds_optic
        return odds_optic.refresh_all()
    from concurrent.futures import ThreadPoolExecutor
    from altlines import dd_live, props_live as pl
    u = usage()
    if u["remaining"] <= 0:
        return {"ok": False, "reason": "month", "used": u["used"], "limit": u["limit"]}
    now = time.time()
    games = sorted((g for g in dd_live.get_slate(None).get("games") or []
                    if g.get("state") == "pre" and dd_live._ts(g.get("tip_off") or "") > now),
                   key=lambda g: dd_live._ts(g.get("tip_off") or ""))
    errors, pairs = [], []
    for g in games:
        try:
            eid = pl.event_for(g)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "error", "errors": [str(exc)]}
        if eid:
            pairs.append((g, eid))
        else:
            errors.append(f'odds-api.net no tiene {g["away"]["tricode"]} @ {g["home"]["tricode"]}')
    left = usage()["remaining"]
    if len(pairs) > left:                      # si no alcanza, primero los que empiezan antes
        pairs = pairs[:max(0, left)]
        errors.append(f"el plan alcanzó para {len(pairs)} partidos")
    if not pairs:
        return {"ok": False, "reason": "month" if left <= 0 else "error", "errors": errors,
                "used": u["used"], "limit": u["limit"]}

    def one(pair):
        g, eid = pair
        try:
            return fetch_game(eid, dd_live._ts(g.get("tip_off") or "") or None)
        except Exception as exc:  # noqa: BLE001 - un partido caído no corta el resto
            errors.append(f'{g["away"]["tricode"]} @ {g["home"]["tricode"]}: {exc}')
            return None
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        got = [x for x in ex.map(one, pairs) if x]
    if not got:
        return {"ok": False, "reason": "error", "errors": errors}
    books, n_props = save(got)
    px = papi_refresh([g for g, _ in pairs])
    books += px["books"]
    errors += px["errors"]
    (dd_live.ODDS_DIR / "refresh.json").write_text(
        json.dumps({dd_live._day(): dd_live.odds_refreshes_today() + 1}), encoding="utf-8")
    (dd_live.ODDS_DIR / "game_t.json").unlink(missing_ok=True)      # todas quedaron con la misma hora
    return {"ok": True, "books": books, "games": len(got), "errors": errors, "props_lines": n_props,
            "today": dd_live.odds_refreshes_today(), "cap": dd_live.ODDS_DAY_CAP, "usage": usage()}


def refresh_game(gid: str) -> dict:
    """Cuotas de UN partido (1 crédito, +1 si la lista de partidos está vencida)."""
    if optic():
        from altlines import odds_optic
        return odds_optic.refresh_game(gid)
    from altlines import dd_live, props_live as pl
    game = dd_live.find_game(gid)
    if not game:
        raise KeyError(gid)
    tip = dd_live._ts(game.get("tip_off") or "")
    if game.get("state") != "pre" or (tip and tip <= time.time()):
        return {"ok": False, "reason": "started"}
    u = usage()
    if u["remaining"] <= 0:
        return {"ok": False, "reason": "month", "used": u["used"], "limit": u["limit"]}
    try:
        eid = pl.event_for(game)
        if not eid:
            return {"ok": False, "reason": "nofixture"}
        g = fetch_game(eid, tip or None)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "error", "errors": [str(exc)]}
    books, n_props = save([g], merge=True)
    px = papi_refresh([game], merge=True)
    books += px["books"]
    f = dd_live.ODDS_DIR / "game_t.json"
    try:
        gt = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        gt = {}
    gt[str(gid)] = time.time()
    f.write_text(json.dumps(gt), encoding="utf-8")
    return {"ok": True, "books": books, "props_lines": n_props, "errors": px["errors"], "usage": usage()}


# ── Casas que odds-api.net no trae para NBA (Stake): de OddsPapi ─────────────
# Stake en odds-api.net sólo publica ganador/hándicap/totales; OddsPapi sí trae
# su doble-doble y triple-doble. NBA_ODDS_PAPI en backend/.env (por defecto
# "stake"; vacío = ninguna). Cuesta 1 pedido de OddsPapi por partido (+1 por la
# lista de partidos, guardada 6 h); sin cupo se saltea sin cortar lo demás.
def papi_books() -> list[str]:
    if optic():
        return []                 # OpticOdds no necesita OddsPapi para nada
    v = os.getenv("NBA_ODDS_PAPI")
    v = "stake" if v is None else v
    return [b.strip() for b in v.split(",") if b.strip() and b.strip() not in brands().values()]


def _papi_fixture(fxs: list, game: dict):
    from altlines import dd_live, live
    words = lambda s: set(live.norm_name(s or "").split())
    nick = lambda t: (live.norm_name(t.get("name") or t.get("display") or "").split() or [""])[-1]
    hn, an = nick(game["home"]), nick(game["away"])
    tip = dd_live._ts(game.get("tip_off") or "")
    for x in fxs:
        pt = x.get("participants") or {}
        names = words(pt.get("participant1Name")) | words(pt.get("participant2Name"))
        if hn in names and an in names and (not tip or abs((x.get("startTime") or 0) - tip) < 6 * 3600):
            return x
    return None


def papi_refresh(games: list[dict], merge: bool = False) -> dict:
    """Doble-doble de las casas de papi_books() para estos partidos (de ESPN)."""
    books = papi_books()
    if not books or not games:
        return {"books": 0, "errors": []}
    from futbol import oddspapi as op
    from altlines import dd_live
    left = op.usage(own=True)["remaining"]
    if left <= 1:
        return {"books": 0, "errors": [f"{', '.join(books)}: sin pedidos de OddsPapi este mes"]}
    errors = []
    try:
        fxs = op.fixtures(dd_live.NBA_TOURNAMENT, hours=36)
    except Exception as exc:  # noqa: BLE001
        return {"books": 0, "errors": [f"OddsPapi: {exc}"]}
    pairs = [(g, _papi_fixture(fxs, g)) for g in games]
    want = [fx for _, fx in pairs if fx]
    left = op.usage(own=True)["remaining"]
    if len(want) > left:
        want = want[:max(0, left)]
        errors.append(f"OddsPapi alcanzó para {len(want)} partidos")
    if not want:
        return {"books": 0, "errors": errors}
    got = op.fixture_odds(want, books)
    convs = [op.to_v4(fx, d, dd_live._keep_dd) for fx, d in got]
    try:
        op.name_players(convs)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"nombres OddsPapi: {exc}")
    ids = {c.get("fixtureId") for c in convs}
    n = 0
    for b in books:
        new = [{**c, "bookmakerOdds": {b: c["bookmakerOdds"][b]} if b in c["bookmakerOdds"] else {}} for c in convs]
        n += any(e["bookmakerOdds"] for e in new)
        bf = dd_live._odds_file(b)
        if merge and bf.exists():
            old_m = bf.stat().st_mtime
            try:
                lst = json.loads(bf.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                lst = []
            bf.write_text(json.dumps([x for x in lst if x.get("fixtureId") not in ids] + new), encoding="utf-8")
            os.utime(bf, (old_m, old_m))
        else:
            bf.write_text(json.dumps(new), encoding="utf-8")
    return {"books": n, "errors": errors}
