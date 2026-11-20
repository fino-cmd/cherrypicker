"""
NBA — cuotas de OpticOdds (api.opticodds.com, clave OPTICODDS_KEY en
backend/.env). Se elige con NBA_ODDS=optic; altlines.odds_net le pasa el
trabajo, así el resto de la app (dd_live, props_live) no cambia.

  · Sin tope mensual ni créditos por pedido: el límite es de 2500 pedidos cada
    15 segundos. Se pide 1 vez por partido.
  · Casas: Betano (Argentina si publica la línea; si no, la de OpticOdds, que es
    betano.ca), bet365, Betsson y Pinnacle como referencia sin margen.
    OpticOdds deja pedir hasta 5 casas por pedido.
  · Trae doble-doble, triple-doble y las líneas de jugador (puntos, rebotes,
    asistencias, triples y combinaciones). Se guarda con la misma forma que
    odds_net (odds_<casa>.json y oddspapi.json); Pinnacle va en las mismas
    líneas con la casa "pinnacle", que props_live usa como mercado.
  · Con esta clave OpticOdds no devuelve partidos de fútbol: sólo NBA.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime

import httpx

BASE = "https://api.opticodds.com/api/v3"
REF = "pinnacle"
# id en OpticOdds → id en la app (futbol/books.json). El orden es la preferencia:
# si dos dan la misma línea, gana la primera (Betano Argentina antes que la otra).
BOOKS = {"betano_argentina_": "betano", "betano": "betano", "bet365": "bet365.bet.ar", "betsson": "betsson",
         "pinnacle": REF}
MARKETS = {"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast",
           "player_made_threes": "threes", "player_points_+_rebounds": "pr", "player_points_+_assists": "pa",
           "player_rebounds_+_assists": "ra", "player_points_+_rebounds_+_assists": "pra",
           "player_double_double": "dd", "player_triple_double": "td"}
FIXTURES_TTL_S = 30 * 60
PARALLEL = 4
_LOCK = threading.Lock()


def key() -> str | None:
    return os.getenv("OPTICODDS_KEY")


def on() -> bool:
    return (os.getenv("NBA_ODDS") or "").strip().lower() == "optic"


def _dir():
    from altlines import props_live as pl
    return pl.DIR


def _count() -> None:
    """Pedidos hechos por mes (sólo para mostrar: no hay tope mensual)."""
    with _LOCK:
        f = _dir() / "optic_usage.json"
        try:
            u = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            u = {}
        m = datetime.now().strftime("%Y-%m")
        u[m] = int(u.get(m, 0)) + 1
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(u), encoding="utf-8")


def _get(path: str, **params) -> dict:
    k = key()
    if not k:
        raise RuntimeError("Falta OPTICODDS_KEY en backend/.env")
    r = httpx.get(f"{BASE}/{path.lstrip('/')}", params=params, headers={"X-Api-Key": k}, timeout=60)
    _count()
    r.raise_for_status()
    return r.json()


def usage() -> dict:
    try:
        u = json.loads((_dir() / "optic_usage.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    used = int(u.get(datetime.now().strftime("%Y-%m"), 0))
    # Sin tope mensual: "remaining" alto para que nada se bloquee por plan.
    return {"used": used, "limit": None, "remaining": 10 ** 6, "period": "mes", "exact": False, "api": "optic"}


def book_ids() -> list[str]:
    return [b for b in dict.fromkeys(BOOKS.values()) if b != REF]


# ── Partidos ─────────────────────────────────────────────────────────────────

def fixtures(refresh: bool = False) -> list[dict]:
    f = _dir() / "optic_fixtures.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < FIXTURES_TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    out, page = [], 1
    for _ in range(10):
        d = _get("fixtures/active", sport="basketball", league="nba", page=page)
        out += [{k: x.get(k) for k in ("id", "start_date", "home_team_display", "away_team_display", "status")}
                for x in d.get("data") or []]
        if not d.get("has_more"):
            break
        page += 1
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def fixture_for(game: dict) -> dict | None:
    """El partido de OpticOdds que corresponde a uno de ESPN (por apodo y hora)."""
    from altlines import dd_live, live
    words = lambda s: set(live.norm_name(s or "").split())
    nick = lambda t: (live.norm_name(t.get("name") or t.get("display") or "").split() or [""])[-1]
    hn, an = nick(game["home"]), nick(game["away"])
    tip = dd_live._ts(game.get("tip_off") or "")
    for x in fixtures():
        if hn in words(x.get("home_team_display")) and an in words(x.get("away_team_display")):
            if not tip or abs(dd_live._ts(x.get("start_date") or "") - tip) < 8 * 3600:
                return x
    return None


# ── Cuotas ───────────────────────────────────────────────────────────────────

def fetch_game(fx_id: str, start: float | None = None, max_age_s: float | None = None) -> dict:
    """Cuotas de un partido → misma forma que odds_net.fetch_game (Pinnacle va en props)."""
    from altlines import props_live as pl
    d = _get("fixtures/odds", fixture_id=fx_id, sportsbook=list(BOOKS), market=list(MARKETS),
             odds_format="DECIMAL")
    odds = (d.get("data") or [{}])[0].get("odds") or []
    order = {b: i for i, b in enumerate(BOOKS)}
    odds.sort(key=lambda o: order.get(str(o.get("sportsbook") or "").lower().replace(" (argentina)", "_argentina_"), 99))
    seen, props, ddr, ddno = set(), [], [], []
    now = time.time()
    for o in odds:
        if max_age_s and o.get("timestamp") and now - float(o["timestamp"]) > max_age_s:
            continue                                  # en vivo: una cuota vieja quedó colgada
        sb = str(o.get("sportsbook") or "")
        oid = "betano_argentina_" if sb.lower() == "betano (argentina)" else sb.lower()
        book = BOOKS.get(oid)
        mk = MARKETS.get(o.get("market_id") or "")
        side = (o.get("selection_line") or "").lower()
        name = o.get("selection")
        price, ln = o.get("price"), o.get("points")
        if not book or not mk or side not in ("over", "under") or not name or not isinstance(price, (int, float)) \
                or ln is None or price <= 1:
            continue
        k = (book, mk, side, float(ln), pl._pkey(name))
        if k in seen:
            continue
        seen.add(k)
        if mk in ("dd", "td"):
            if side == "over" and book != REF:              # "over 0.5" = Sí
                ddr.append([book, mk, name, float(price)])
            elif side == "under" and book != REF:
                ddno.append([book, mk, name, float(price)])
            continue
        props.append({"book": book, "mk": mk, "side": side, "line": float(ln), "odds": float(price),
                      "player": pl._pkey(name)})
    return {"eid": str(fx_id), "t": time.time(), "start": start, "dd": ddr, "dd_no": ddno, "props": props, "ref": []}


GAME_BOOKS = {"betano_argentina_": "betano", "betano": "betano", "betsson": "betsson", "bet365": "bet365.bet.ar",
              "pinnacle": REF}


def game_odds(fx: dict, max_age_s: float | None = None) -> dict:
    """Mercados del partido, en vivo si está en juego (1 pedido): línea de puntos, hándicap,
    ganador y puntos de cada equipo.
    → {"rows": [{book, mk, side, line, odds}], "lines": {"total", "home_margin", "book"}}
       mk: "total" (over/under) · "spread" (home/away, línea = hándicap de ese equipo) ·
           "ml" (home/away) · "tt_home" / "tt_away" (over/under)"""
    d = _get("fixtures/odds", fixture_id=fx["id"], sportsbook=list(GAME_BOOKS),
             market=["total_points", "point_spread", "moneyline", "team_total"], odds_format="DECIMAL")
    odds = (d.get("data") or [{}])[0].get("odds") or []
    now = time.time()
    home = (fx.get("home_team_display") or "").lower()
    order = {b: i for i, b in enumerate(GAME_BOOKS)}

    def oid(x):
        sb = str(x.get("sportsbook") or "").lower()
        return "betano_argentina_" if sb == "betano (argentina)" else sb
    odds.sort(key=lambda x: order.get(oid(x), 99))
    seen, rows = set(), []
    for x in odds:
        book = GAME_BOOKS.get(oid(x))
        price = x.get("price")
        if not book or not isinstance(price, (int, float)) or price <= 1:
            continue
        if max_age_s and x.get("timestamp") and now - float(x["timestamp"]) > max_age_s:
            continue
        mid = x.get("market_id")
        sel = (x.get("selection") or "").lower()
        sl = (x.get("selection_line") or "").lower()
        pts = x.get("points")
        if mid == "total_points" and sl in ("over", "under") and pts is not None:
            mk, side, line = "total", sl, float(pts)
        elif mid == "point_spread" and sel and pts is not None:
            mk, side, line = "spread", ("home" if sel == home else "away"), float(pts)
        elif mid == "moneyline" and sel:
            mk, side, line = "ml", ("home" if sel == home else "away"), None
        elif mid == "team_total" and sel and sl in ("over", "under") and pts is not None:
            mk, side, line = ("tt_home" if sel == home else "tt_away"), sl, float(pts)
        else:
            continue
        k = (book, mk, side, line)
        if k in seen:
            continue
        seen.add(k)
        rows.append({"book": book, "mk": mk, "side": side, "line": line, "odds": float(price)})
    # La línea principal de cada mercado: la más pareja de la casa más confiable
    rank = {REF: 0, "bet365.bet.ar": 1, "betsson": 2, "betano": 3}
    lines = {"total": None, "home_margin": None, "book": None}
    for mk, key in (("total", "total"), ("spread", "home_margin")):
        pairs: dict[tuple, dict] = {}
        for r in rows:
            if r["mk"] != mk:
                continue
            ln = r["line"] if mk == "total" or r["side"] == "home" else -r["line"]     # hándicap, del lado del local
            pairs.setdefault((r["book"], ln), {})[r["side"]] = r["odds"]
        best = None
        for (bk, ln), s in pairs.items():
            if len(s) < 2:
                continue
            a, b = list(s.values())[:2]
            score = (rank.get(bk, 9), abs(a - b))
            if best is None or score < best[0]:
                best = (score, ln, bk)
        if best:
            lines[key] = best[1] if mk == "total" else -best[1]      # −3,5 del local = gana por 3,5
            lines["book"] = lines["book"] or best[2]
    return {"rows": rows, "lines": lines}


def game_lines(fx: dict, max_age_s: float | None = None) -> dict:
    """Línea de puntos y hándicap del partido (en vivo si está en juego).
    → {"total": 224.5, "home_margin": -3.5, "book": "pinnacle"} (lo que falte, None).
    De cada mercado se toma la línea más pareja (la principal) de la casa más confiable."""
    d = _get("fixtures/odds", fixture_id=fx["id"], sportsbook=["pinnacle", "bet365", "betsson", "betano"],
             market=["total_points", "point_spread"], odds_format="DECIMAL")
    odds = (d.get("data") or [{}])[0].get("odds") or []
    now = time.time()
    rank = {"pinnacle": 0, "bet365": 1, "betsson": 2, "betano": 3}
    out = {"total": None, "home_margin": None, "book": None}
    home = (fx.get("home_team_display") or "").lower()
    for mk, key in (("total_points", "total"), ("point_spread", "home_margin")):
        best = None
        pairs: dict[tuple, dict] = {}
        for x in odds:
            if x.get("market_id") != mk or x.get("points") is None or not isinstance(x.get("price"), (int, float)):
                continue
            if max_age_s and x.get("timestamp") and now - float(x["timestamp"]) > max_age_s:
                continue
            bk = str(x.get("sportsbook") or "").lower()
            if mk == "total_points":
                side, pts = (x.get("selection_line") or "").lower(), float(x["points"])
            else:                                   # hándicap: se expresa siempre del lado del local
                is_home = (x.get("selection") or "").lower() == home
                side, pts = ("home" if is_home else "away"), (float(x["points"]) if is_home else -float(x["points"]))
            pairs.setdefault((bk, pts), {})[side] = float(x["price"])
        for (bk, pts), s in pairs.items():
            if len(s) < 2:
                continue
            a, b = list(s.values())[:2]
            score = (rank.get(bk, 9), abs(a - b))   # la casa más confiable y, de ella, la línea más pareja
            if best is None or score < best[0]:
                best = (score, pts, bk)
        if best:
            out[key] = best[1] if mk == "total_points" else -best[1]     # hándicap −3,5 del local = gana por 3,5
            out["book"] = out["book"] or best[2]
    return out


def _mark_refresh(gid: str | None = None) -> None:
    from altlines import dd_live
    if gid is None:
        (dd_live.ODDS_DIR / "refresh.json").write_text(
            json.dumps({dd_live._day(): dd_live.odds_refreshes_today() + 1}), encoding="utf-8")
        (dd_live.ODDS_DIR / "game_t.json").unlink(missing_ok=True)
        return
    f = dd_live.ODDS_DIR / "game_t.json"
    try:
        gt = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        gt = {}
    gt[str(gid)] = time.time()
    f.write_text(json.dumps(gt), encoding="utf-8")


def refresh_all() -> dict:
    """Cuotas de todos los partidos de la noche que no empezaron (1 pedido por partido)."""
    from concurrent.futures import ThreadPoolExecutor
    from altlines import dd_live, odds_net
    now = time.time()
    games = sorted((g for g in dd_live.get_slate(None).get("games") or []
                    if g.get("state") == "pre" and dd_live._ts(g.get("tip_off") or "") > now),
                   key=lambda g: dd_live._ts(g.get("tip_off") or ""))
    errors, pairs = [], []
    try:
        fixtures(refresh=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "error", "errors": [f"OpticOdds: {exc}"]}
    for g in games:
        fx = fixture_for(g)
        if fx:
            pairs.append((g, fx))
        else:
            errors.append(f'OpticOdds no tiene {g["away"]["tricode"]} @ {g["home"]["tricode"]}')
    if not pairs:
        return {"ok": False, "reason": "error", "errors": errors or ["no hay partidos por empezar"]}

    def one(pair):
        g, fx = pair
        try:
            return fetch_game(fx["id"], dd_live._ts(g.get("tip_off") or "") or None)
        except Exception as exc:  # noqa: BLE001 - un partido caído no corta el resto
            errors.append(f'{g["away"]["tricode"]} @ {g["home"]["tricode"]}: {exc}')
            return None
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        got = [x for x in ex.map(one, pairs) if x]
    if not got:
        return {"ok": False, "reason": "error", "errors": errors}
    books, n_props = odds_net.save(got)
    _mark_refresh()
    return {"ok": True, "books": books, "games": len(got), "errors": errors, "props_lines": n_props,
            "today": dd_live.odds_refreshes_today(), "cap": dd_live.ODDS_DAY_CAP, "usage": usage()}


def refresh_game(gid: str) -> dict:
    """Cuotas de UN partido (1 pedido)."""
    from altlines import dd_live, odds_net
    game = dd_live.find_game(gid)
    if not game:
        raise KeyError(gid)
    tip = dd_live._ts(game.get("tip_off") or "")
    if game.get("state") != "pre" or (tip and tip <= time.time()):
        return {"ok": False, "reason": "started"}
    try:
        fx = fixture_for(game) or (fixtures(refresh=True) and fixture_for(game))
        if not fx:
            return {"ok": False, "reason": "nofixture"}
        g = fetch_game(fx["id"], tip or None)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "error", "errors": [str(exc)]}
    books, n_props = odds_net.save([g], merge=True)
    _mark_refresh(gid)
    return {"ok": True, "books": books, "props_lines": n_props, "errors": [], "usage": usage()}
