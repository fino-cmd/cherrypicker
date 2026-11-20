"""
NBA — líneas de jugador en vivo: puntos, rebotes, asistencias, triples y
combinaciones (P+R, P+A, R+A, P+R+A).

  · Modelo: el mismo del doble-doble (altlines.dd_live.simulate_game): partido
    simulado 20.000 veces + historia propia del jugador, mitad y mitad, y de
    ahí la distribución entera de cada estadística (altlines.props_dist).
    Una corrección final (props_calib.json) baja un poco los overs, que el
    backtest muestra algo altos.
  · Cuotas de las casas donde se juega (bet365 AR, Betano, Betsson, 1xBet):
    OddsPapi, del MISMO pedido por partido que trae el doble-doble (no cuesta
    nada extra; se guardan al tocar "Actualizar cuotas" en NBA).
    bet365 sólo ofrece OVERS (escaleras 10+, 15+, 20+…). Betano, Betsson y
    1xBet dan over y under en cada línea, también en las combinaciones.
  · Odds API (api.odds-api.net, clave ODDSAPI_NET_KEY): Pinnacle, la casa de
    menor margen, como referencia ("lo que piensa el mercado") y bet365 de
    respaldo. Si el modelo se aleja mucho de Pinnacle, la jugada es dudosa.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from altlines import config as cfg, props_dist

logger = logging.getLogger(__name__)

BASE = "https://api.odds-api.net/v1"
DIR = cfg.DATA_DIR / "props"
CALIB_FILE = cfg.DATA_DIR / "props_calib.json"
ODDS_TTL_S = 10 * 60          # Odds API: las cuotas de un partido se reusan 10 minutos
EVENTS_TTL_S = 30 * 60
OP_FILE_TTL_S = 8 * 3600      # OddsPapi: más viejas que esto no se usan
# Casas donde se puede jugar (ids de OddsPapi, como futbol/books.json)
PLAY_BOOKS = ("bet365.bet.ar", "betano", "betsson", "1xbet")
# Odds API → nuestro id: Pinnacle de referencia y bet365 de respaldo
NET_BOOKS = {"pinnacle": "pinnacle", "bet365": "bet365.bet.ar"}
REF_BOOK = "pinnacle"
BOOK_LABEL = {"bet365.bet.ar": "bet365", "betano": "Betano", "betsson": "Betsson", "1xbet": "1xBet",
              "pinnacle": "Pinnacle"}
NET_METRIC = {"player points": "pts", "player rebounds": "reb", "player assists": "ast",
              "player threes": "threes", "player 3pt field goals": "threes",
              "player pr": "pr", "player pa": "pa", "player ra": "ra", "player pra": "pra"}
OP_TYPES = {"points": "pts", "rebounds": "reb", "assists": "ast", "3pointers": "threes",
            "ptsreb": "pr", "ptsass": "pa", "assreb": "ra", "ptsassreb": "pra"}
# Una jugada: la cuota paga al menos PICK_EDGE por encima de la justa, con la probabilidad
# mezclada con el mercado y achicada (EDGE_SHRINK). Era 0.12 sobre el modelo solo: sus
# jugadas de cuota alta perdían (9 de 35, −30%) porque la ventaja era ruido del modelo.
PICK_EDGE = 0.03
PICK_MIN_P = 0.15            # colas más finas que esto: manda el error del modelo
PICK_MIN_ODDS = 1.40         # cuotas más bajas no son jugada: con el umbral bajo (PICK_EDGE) se colaban las de 1,10–1,20
PICK_MAX_EDGE = 0.45         # más que esto no es ventaja: la casa sabe algo (minutos, rol)
# Mezcla con el mercado: la probabilidad que se muestra y con la que se elige es la del
# modelo corrida hacia el mercado sin margen (en logit), con este peso para el modelo.
# En la línea con precio propio se usa ese precio; en las alternativas, la diferencia de
# la línea central (el ancla). El Parlay también usa esta (p_raw queda guardada aparte).
BLEND_W = 0.5
# Ventaja conservadora: a la probabilidad se le resta EDGE_SHRINK desvíos binomiales antes
# de compararla con la cuota. Pega más en las cuotas altas, donde el error pesa más.
EDGE_SHRINK = 0.08
# Modelo vs mercado sin margen (Pinnacle, o el promedio de las casas que dan
# over y under en esa línea) más lejos que esto → dudosa: el mercado sabe algo.
MKT_MAX_GAP = 0.10
PICKS_PER_PLAYER = 1

_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def _lock(k: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(k, threading.Lock())


def _ts(s) -> float:
    if not s:
        return 0.0
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _pkey(name: str) -> str:
    """Nombre comparable sin importar el orden ("Towns, Karl-Anthony" en OddsPapi)."""
    from altlines import dd_live
    return dd_live._key(name or "")


# ── Corrección de calibración (altlines/tools/props_calib.py) ────────────────

_CAL: dict = {}


def calibrate(p: float, mk: str) -> float:
    """P(over) del modelo → corregida: σ(a + b·logit p), por mercado."""
    if not _CAL:
        try:
            _CAL.update(json.loads(CALIB_FILE.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            _CAL["_none"] = True
    c = _CAL.get(mk)
    if not c or p <= 0 or p >= 1:
        return p
    z = c["a"] + c["b"] * math.log(p / (1 - p))
    return 1 / (1 + math.exp(-z))


# ── Odds API (Pinnacle y respaldo de bet365) ─────────────────────────────────

def api_key() -> str | None:
    return os.getenv("ODDSAPI_NET_KEY")


def _get(path: str, params: dict) -> dict:
    key = api_key()
    if not key:
        raise RuntimeError("Falta ODDSAPI_NET_KEY en backend/.env")
    from futbol import oddsnet
    if oddsnet.key() == key:          # misma cuenta que Fútbol: el pedido se cuenta en su contador de créditos
        return oddsnet._get(path, **params)
    url = BASE + path + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"X-API-Key": key, "Accept": "application/json",
                                               "User-Agent": "cherrypicker/1.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def _events() -> list[dict]:
    f = DIR / "events.json"
    if f.exists() and time.time() - f.stat().st_mtime < EVENTS_TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    now = int(time.time())
    items, cursor = [], None
    for _ in range(5):
        d = _get("/events", {"sport": "basketball", "league": "NBA", "limit": 100,
                             "start_from": now - 6 * 3600, "start_to": now + 3 * 86400, "cursor": cursor})
        items += [{k: e.get(k) for k in ("event_id", "start_time", "home_team", "away_team")}
                  for e in d.get("items") or []]
        cursor = d.get("next_cursor")
        if not cursor:
            break
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(items), encoding="utf-8")
    return items


def _words(s: str) -> list[str]:
    from altlines import live
    return live.norm_name(s or "").split()


def event_for(game: dict) -> str | None:
    """El partido de Odds API que corresponde a uno de ESPN (por apodo y hora)."""
    tip = _ts(game.get("tip_off"))
    hn = (_words(game["home"].get("name") or game["home"].get("display")) or [""])[-1]
    an = (_words(game["away"].get("name") or game["away"].get("display")) or [""])[-1]
    for e in _events():
        if tip and abs((e.get("start_time") or 0) - tip) > 8 * 3600:
            continue
        if hn and an and hn in _words(e.get("home_team")) and an in _words(e.get("away_team")):
            return str(e["event_id"])
    return None


def fetch_net(eid: str, refresh: bool = False, frozen: bool = False) -> dict:
    """{"t": epoch, "rows": [{book, mk, side, line, odds, player}]} de Odds API (cacheado).
    `frozen`: el partido ya empezó; sólo lo guardado, sin pedir nada."""
    f = DIR / f"net_{eid}.json"
    if frozen:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"t": time.time(), "rows": []}
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < ODDS_TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    with _lock(eid):
        d = _get(f"/events/{urllib.parse.quote(eid)}/odds/snapshot",
                 {"bookmakers": ",".join(NET_BOOKS), "types": "player prop"})
        rows = []
        for x in d.get("items") or []:
            mk = NET_METRIC.get(x.get("metric") or "")
            if not mk or not x.get("is_available", True) or not isinstance(x.get("odds"), (int, float)):
                continue
            parts = (x.get("line") or "").split()
            if len(parts) != 2 or parts[0] not in ("over", "under"):
                continue
            try:
                ln = float(parts[1])
            except ValueError:
                continue
            rows.append({"book": NET_BOOKS.get(x["bookmaker"], x["bookmaker"]), "mk": mk, "side": parts[0],
                         "line": ln, "odds": float(x["odds"]), "player": _pkey(x.get("player_name") or "")})
        out = {"t": time.time(), "rows": rows}
        DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out), encoding="utf-8")
        return out


# ── OddsPapi: del pedido del doble-doble ─────────────────────────────────────

_OP_CAT: dict = {}


def _op_catalog() -> dict:
    """marketId → (mercado, {outcomeId: ("over"/"under", línea)})."""
    if _OP_CAT:
        return _OP_CAT
    from futbol import oddspapi as op
    for m in json.loads((op.DIR / "markets_basketball.json").read_text(encoding="utf-8")):
        if not m.get("playerProp") or m.get("period") != "result":
            continue
        kind, _, stat = (m.get("marketType") or "").partition("-")
        mk = OP_TYPES.get(stat)
        if not mk or kind not in ("playertotals", "players"):
            continue
        outs = {}
        for o in m.get("outcomes") or []:
            nm = (o.get("outcomeName") or "").strip().lower()
            if kind == "playertotals" and nm in ("over", "under") and m.get("handicap") is not None:
                outs[str(o["outcomeId"])] = (nm, float(m["handicap"]))
            elif kind == "players" and nm.endswith("+") and nm[:-1].isdigit() and int(nm[:-1]) > 0:
                outs[str(o["outcomeId"])] = ("over", int(nm[:-1]) - 0.5)      # "25+" = over 24.5
        if outs:
            _OP_CAT[int(m["marketId"])] = (mk, outs)
    return _OP_CAT


def keep_prop(mid: int) -> bool:
    return int(mid) in _op_catalog()


def save_oddspapi(got_odds: list, merge: bool = False) -> int:
    """Lo llama altlines.dd_live.refresh_odds con las respuestas que ya bajó.
    `merge`: pedido de un solo partido; reemplaza sus líneas y deja las demás."""
    from futbol import oddspapi as op
    cat = _op_catalog()
    fxs = [op.to_v4(fx, d, keep_prop) for fx, d in got_odds]
    try:
        op.name_players(fxs)
    except Exception as exc:  # noqa: BLE001 - sin nombres nuevos igual sirven los conocidos
        logger.warning("props nombres: %s", exc)
    rows = []
    for fx in fxs:
        start = fx.get("startTime")
        for book, b in (fx.get("bookmakerOdds") or {}).items():
            if book not in PLAY_BOOKS:
                continue
            for mid, m in (b.get("markets") or {}).items():
                c = cat.get(int(mid))
                if not c:
                    continue
                mk, outs = c
                for oid, o in (m.get("outcomes") or {}).items():
                    so = outs.get(str(oid))
                    if not so:
                        continue
                    for pl in (o.get("players") or {}).values():
                        if not pl.get("price") or not pl.get("active", True) or not pl.get("playerName"):
                            continue
                        rows.append({"book": book, "mk": mk, "side": so[0], "line": so[1],
                                     "odds": float(pl["price"]), "player": _pkey(pl["playerName"]), "start": start,
                                     "fx": fx.get("fixtureId")})
    DIR.mkdir(parents=True, exist_ok=True)
    f = DIR / "oddspapi.json"
    t = time.time()
    if merge and f.exists():
        old = json.loads(f.read_text(encoding="utf-8"))
        ids = {fx.get("fixtureId") for fx in fxs}
        starts = {fx.get("startTime") for fx in fxs}
        names = {r["player"] for r in rows}
        # fuera las líneas viejas de ese partido (las de antes de este cambio no traen "fx")
        keep = [r for r in old.get("rows") or [] if not (
            r.get("fx") in ids or (r.get("fx") is None and r.get("start") in starts and r["player"] in names))]
        f.write_text(json.dumps({"t": old.get("t", t), "rows": keep + rows}), encoding="utf-8")
    else:
        f.write_text(json.dumps({"t": t, "rows": rows}), encoding="utf-8")
    return len(rows)


def _op_rows(tip, gid=None) -> tuple[list, float | None]:
    f = DIR / "oddspapi.json"
    if not f.exists():
        return [], None
    d = json.loads(f.read_text(encoding="utf-8"))
    from altlines import dd_live
    age = time.time() - (dd_live.game_odds_t(gid) or d["t"])       # si se pidió este partido solo, manda esa hora
    if age > OP_FILE_TTL_S:
        return [], age
    t0 = _ts(tip)
    return [r for r in d["rows"] if not t0 or not r.get("start") or abs(_ts(r["start"]) - t0) < 12 * 3600], age


# ── Modelo contra cuotas ─────────────────────────────────────────────────────

def _novig(o_over: float, o_under: float) -> float:
    """Probabilidad del over sin el margen de la casa (multiplicativa)."""
    a, b = 1 / o_over, 1 / o_under
    return a / (a + b)


def _p_model(cc: list[float], mk: str, line: float, side: str) -> float:
    po = calibrate(props_dist.p_over(cc, line), mk)
    if side == "over":
        return po
    if float(line).is_integer():      # línea entera: el empate devuelve la plata
        return 1 - po - props_dist.p_at_least(cc, int(line)) + props_dist.p_at_least(cc, int(line) + 1)
    return 1 - po


def _lg(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _blend(it: dict, d_anchor: float) -> None:
    """p del modelo corrida hacia el mercado (BLEND_W) y la ventaja conservadora.
    Las marcas de dudosa / sin ancla ya quedaron calculadas con la del modelo solo."""
    po = it.pop("_po")
    push = it.pop("_push")
    pm_over = it["p_mkt"] if it["side"] == "over" else (None if it["p_mkt"] is None else 1 - it["p_mkt"])
    d = (_lg(pm_over) - _lg(po)) if pm_over is not None else d_anchor
    pa = 1 / (1 + math.exp(-(_lg(po) + (1 - BLEND_W) * d)))
    p = pa if it["side"] == "over" else max(1e-4, 1 - pa - push)
    pc = max(0.0, p - EDGE_SHRINK * math.sqrt(p * (1 - p)))
    it["edge_raw"] = it["edge"]
    it["p"] = round(p, 4)
    it["fair"] = round(1 / p, 2) if p > 0.001 else None
    it["edge"] = round(p * it["best_odds"] - 1, 4)
    it["edge_cons"] = round(pc * it["best_odds"] - 1, 4)


def attach(sim: dict, game: dict, refresh: bool = False, frozen: bool = False) -> dict:
    """Las líneas con cuotas de cada jugador del partido, y sus jugadas."""
    res = {"game_id": sim.get("game_id"), "event_id": None, "odds_age_s": None, "players": [],
           "picks": [], "books": BOOK_LABEL, "labels": props_dist.LABEL, "sources": {}}
    from altlines import odds_net
    net = odds_net.on()        # todas las casas y Pinnacle salen del mismo pedido por partido
    if net and refresh and not frozen:
        try:
            odds_net.refresh_game(str(sim.get("game_id")))
        except Exception as exc:  # noqa: BLE001 - se muestra lo guardado
            logger.warning("props odds-api.net %s: %s", sim.get("game_id"), exc)
    op_rows, op_age = _op_rows(sim.get("tip_off"), sim.get("game_id"))
    res["sources"]["oddspapi_age_s"] = None if op_age is None else round(op_age)
    net_rows = []
    if api_key() and not odds_net.optic():      # con OpticOdds, Pinnacle ya viene en las líneas
        try:
            eid = event_for(game)
            res["event_id"] = eid
            if eid:
                if net:                # sólo lo guardado: se pide con "Pedir cuotas", no al abrir el partido
                    nf = DIR / f"net_{eid}.json"
                    od = json.loads(nf.read_text(encoding="utf-8")) if nf.exists() else None
                else:
                    od = fetch_net(eid, refresh=refresh and not frozen, frozen=frozen)
                if od:
                    net_rows = od["rows"]
                    res["sources"]["oddsapi_age_s"] = round(time.time() - od["t"])
        except Exception as exc:  # noqa: BLE001 - sin Pinnacle igual se muestra OddsPapi
            logger.warning("props odds-api %s: %s", sim.get("game_id"), exc)
            res["sources"]["oddsapi_error"] = str(exc)
    # bet365 de Odds API sólo donde OddsPapi no tiene bet365 para ese jugador y mercado
    have365 = {(r["player"], r["mk"]) for r in op_rows if r["book"] == "bet365.bet.ar"}
    rows = op_rows + [r for r in net_rows if r["book"] != "bet365.bet.ar" or (r["player"], r["mk"]) not in have365]
    ages = [a for a in (op_age if op_rows else None, res["sources"].get("oddsapi_age_s")) if a is not None]
    res["odds_age_s"] = round(max(ages)) if ages else None
    if not any(r["book"] in PLAY_BOOKS for r in rows):
        res["error"] = "Todavía no hay cuotas de jugadores para este partido: tocá Actualizar cuotas."
    # (jugador, mercado, línea) → {"over": {casa: cuota}, "under": {...}}
    table: dict[tuple, dict] = {}
    for r in rows:
        table.setdefault((r["player"], r["mk"], r["line"]), {"over": {}, "under": {}})[r["side"]][r["book"]] = r["odds"]
    by_player: dict[str, list] = {}
    for (pl, mk, ln), sides in table.items():
        by_player.setdefault(pl, []).append((mk, ln, sides))
    play = set(PLAY_BOOKS)
    for p in sim.get("players") or []:
        dist = p.get("dist") or {}
        prow = by_player.get(_pkey(p["name"]))
        if not prow or not dist:
            continue
        markets: dict[str, list] = {}
        for mk, ln, sides in sorted(prow, key=lambda t: (t[0], t[1])):
            cc = dist.get(mk)
            if not cc:
                continue
            pin_o, pin_u = sides["over"].get(REF_BOOK), sides["under"].get(REF_BOOK)
            p_pin = round(_novig(pin_o, pin_u), 4) if pin_o and pin_u else None
            # el mercado: Pinnacle si está; si no, el promedio sin margen de las que dan los dos lados
            both = [_novig(sides["over"][b], sides["under"][b]) for b in sides["over"] if b in sides["under"] and b != REF_BOOK]
            p_mkt = p_pin if p_pin is not None else (round(sum(both) / len(both), 4) if both else None)
            for side in ("over", "under"):
                books = {b: o for b, o in sides[side].items() if b in play}
                if not books:
                    continue
                pm = _p_model(cc, mk, ln, side)
                best = max(books, key=books.get)
                item = {"line": ln, "side": side, "p": round(pm, 4),
                        "fair": round(1 / pm, 2) if pm > 0.001 else None,
                        "p_raw": round(pm, 4), "fair_raw": round(1 / pm, 2) if pm > 0.001 else None,
                        "_po": _p_model(cc, mk, ln, "over"),
                        "_push": (props_dist.p_at_least(cc, int(ln)) - props_dist.p_at_least(cc, int(ln) + 1))
                        if float(ln).is_integer() else 0.0,
                        "books": books, "best_book": best, "best_odds": books[best],
                        "edge": round(pm * books[best] - 1, 4),
                        "p_pin": None if p_pin is None else (p_pin if side == "over" else round(1 - p_pin, 4)),
                        "p_mkt": None if p_mkt is None else (p_mkt if side == "over" else round(1 - p_mkt, 4)),
                        "mkt_src": None if p_mkt is None else ("pinnacle" if p_pin is not None else "casas")}
                gap = None if item["p_mkt"] is None else abs(pm - item["p_mkt"])
                item["dubious"] = item["edge"] > PICK_MAX_EDGE or (gap is not None and gap > MKT_MAX_GAP)
                markets.setdefault(mk, []).append(item)
        # Ancla: en la línea central (donde hay precio de mercado sin margen) el
        # modelo y el mercado tienen que coincidir. Si no, el jugador está mal
        # leído (minutos, rol) y ninguna línea de su escalera es jugada. Sin
        # ninguna referencia tampoco: ahí el error del modelo es el más grande.
        for mk, items in markets.items():
            refs = [it for it in items if it["p_mkt"] is not None and it["side"] == "over"]
            if refs:
                c = min(refs, key=lambda it: abs(it["p_mkt"] - 0.5))
                gap = round(abs(c["p"] - c["p_mkt"]), 4)
                anchor = {"line": c["line"], "p": c["p"], "p_mkt": c["p_mkt"], "gap": gap, "src": c["mkt_src"]}
            else:
                anchor = None
            d_anchor = (_lg(anchor["p_mkt"]) - _lg(anchor["p"])) if anchor else 0.0
            for it in items:
                it["anchor"] = anchor
                if anchor is None or anchor["gap"] > MKT_MAX_GAP:
                    it["unanchored"] = True
                _blend(it, d_anchor)
        if not markets:
            continue
        mean = {m: round(sum(c[1:]), 1) for m, c in dist.items()}     # E[X] = Σ P(X >= k)
        res["players"].append({"player_id": p["player_id"], "name": p["name"], "team": p["team"],
                               "photo": p.get("photo"), "status": p.get("status"), "doubt": p.get("doubt"),
                               "min": p.get("min"), "mean": mean, "markets": markets})
        # Jugadas: las mejores líneas del jugador que pasan el filtro (una por jugador)
        ok = [(mk, it) for mk, items in markets.items() for it in items
              if it["edge_cons"] >= PICK_EDGE and it["p"] >= PICK_MIN_P and not it["dubious"]
              and (it.get("best_odds") or 0) >= PICK_MIN_ODDS
              and not it.get("unanchored") and not p.get("doubt")]
        for mk, it in sorted(ok, key=lambda t: -t[1]["edge_cons"])[:PICKS_PER_PLAYER]:
            it["pick"] = True
            res["picks"].append({"game_id": sim.get("game_id"), "player_id": p["player_id"], "player": p["name"],
                                 "team": p["team"], "photo": p.get("photo"), "market": mk,
                                 "label": props_dist.LABEL[mk], **it})
    res["players"].sort(key=lambda x: -(x["mean"].get("pra") or x["mean"].get("pts") or 0))
    res["picks"].sort(key=lambda x: -x.get("edge_cons", x["edge"]))
    return res


def game_props(gid: str, refresh: bool = False) -> dict:
    from altlines import dd_live
    game = dd_live.find_game(gid)
    if not game:
        raise KeyError(gid)
    sim = dd_live.simulate_game(game)
    started = game.get("state") != "pre" or _ts(game.get("tip_off") or "") <= time.time()
    out = attach(sim, game, refresh=refresh, frozen=started)
    try:
        if started:
            freeze(out)
        else:
            log_highlights(out, sim.get("tip_off"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("props log %s: %s", gid, exc)
    out.update(home=sim.get("home"), away=sim.get("away"), tip_off=sim.get("tip_off"),
               state=sim.get("state"), computed_at=sim.get("computed_at"))
    return out


def slate_picks() -> dict:
    """Jugadas de todos los partidos de hoy que ya están simulados."""
    from altlines import dd_live
    s = dd_live.get_slate(None)
    picks, games, missing = [], 0, 0
    for g in s.get("games") or []:
        if g.get("state") != "pre":
            continue
        sim = dd_live.cached_sim(str(g["game_id"]))
        if not sim or sim.get("dist_v") != dd_live.DIST_V:
            missing += 1
            continue
        try:
            r = attach(sim, g)
        except Exception as exc:  # noqa: BLE001
            logger.warning("props %s: %s", g["game_id"], exc)
            continue
        games += 1
        try:
            log_highlights(r, g.get("tip_off"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("props log %s: %s", g["game_id"], exc)
        for k in r["picks"]:
            k["matchup"] = f'{g["away"]["tricode"]} @ {g["home"]["tricode"]}'
            k["tip_off"] = g.get("tip_off")
        picks += r["picks"]
    picks.sort(key=lambda x: -x.get("edge_cons", x["edge"]))
    return {"date": s.get("date"), "picks": picks, "games": games, "not_simulated": missing,
            "books": BOOK_LABEL, "labels": props_dist.LABEL,
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ══════════════════════════════════════════════════════════════════════════════
# Destacados: registro y liquidación (como las jugadas del doble-doble)
# ══════════════════════════════════════════════════════════════════════════════

HL_PICKS = 3                 # jugadas con valor por partido
HL_FIRM = 5                  # "lo más firme" por partido
HL_FIRM_MIN_P = 0.55
HL_MIN_ODDS = 1.40
HL_PICK_MAX_ODDS = 2.50     # jugadas con valor de Destacados: a cuota más alta perdían (5 de 44 a 3,00 o más)
STAT_OF = {"pts": ("pts",), "reb": ("reb",), "ast": ("ast",), "threes": ("threes",),
           "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast"), "pra": ("pts", "reb", "ast")}


def highlights(res: dict) -> list[dict]:
    """Los Destacados de un partido, igual que los arma la app: hasta 3 jugadas
    con valor (cuota hasta 2,50, las de más chance primero) y 5 "lo más firme" (la línea más probable que paga 1,40 o más)."""
    top = sorted((k for k in (res.get("picks") or []) if (k.get("best_odds") or 0) <= HL_PICK_MAX_ODDS), key=lambda k: -k["p"])
    out = [{**k, "kind": "pick"} for k in top[:HL_PICKS]]
    seen = {k["player_id"] for k in out}
    sure = []
    for p in res.get("players") or []:
        if p.get("doubt"):
            continue
        for mk, items in (p.get("markets") or {}).items():
            for it in items:
                if (not it.get("unanchored") and not it.get("dubious") and it["p"] >= HL_FIRM_MIN_P
                        and (it.get("best_odds") or 0) >= HL_MIN_ODDS):
                    sure.append((p, mk, it))
    sure.sort(key=lambda t: (-t[2]["p"], -t[2]["best_odds"]))
    n = 0
    for p, mk, it in sure:
        if n >= HL_FIRM:
            break
        if p["player_id"] in seen:
            continue
        seen.add(p["player_id"])
        n += 1
        out.append({"game_id": res.get("game_id"), "player_id": p["player_id"], "player": p["name"],
                    "team": p["team"], "photo": p.get("photo"), "market": mk,
                    "label": props_dist.LABEL[mk], **it, "kind": "firme"})
    return out


def _hdb():
    import sqlite3
    from altlines import dd_live
    dd_live.DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(dd_live.DB)
    c.execute("""CREATE TABLE IF NOT EXISTS prop_picks (
        game_id TEXT, player_id TEXT, market TEXT, line REAL, side TEXT,
        player TEXT, team TEXT, photo TEXT, label TEXT, date TEXT, tip_off TEXT, kind TEXT,
        book TEXT, price REAL, p_model REAL, fair REAL, edge REAL, p_mkt REAL, logged_at TEXT,
        result TEXT, pnl REAL, value REAL, stat TEXT, settled_at TEXT,
        PRIMARY KEY (game_id, player_id, market, line, side))""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(prop_picks)")}
    if "dropped" not in cols:      # 1 = se recomendó y salió de la lista antes del partido (se sigue igual)
        c.execute("ALTER TABLE prop_picks ADD COLUMN dropped INTEGER DEFAULT 0")
    if "late" not in cols:         # 1 = se agregó con el partido ya empezado (recuperada a mano)
        c.execute("ALTER TABLE prop_picks ADD COLUMN late INTEGER DEFAULT 0")
    if "p_raw" not in cols:        # la del modelo solo (p_model es la mezclada con el mercado desde BLEND_W)
        c.execute("ALTER TABLE prop_picks ADD COLUMN p_raw REAL")
    return c


def freeze(res: dict) -> None:
    """Partido empezado: las jugadas que se muestran son las que quedaron guardadas
    antes del inicio, no las que saldrían de recalcular con cuotas de después."""
    import sqlite3
    gid = str(res.get("game_id"))
    with _hdb() as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute("SELECT * FROM prop_picks WHERE game_id=? AND dropped=0", (gid,))]
    if not rows:
        return
    items = {}
    for p in res.get("players") or []:
        for mk, its in (p.get("markets") or {}).items():
            for it in its:
                it.pop("pick", None)
                items[(str(p["player_id"]), mk, float(it["line"]), it["side"])] = it
    out = []
    for r in rows:
        it = items.get((r["player_id"], r["market"], float(r["line"]), r["side"])) or {}
        if it and r["kind"] != "firme":
            it["pick"] = True
        out.append({**it, "game_id": gid, "player_id": r["player_id"], "player": r["player"], "team": r["team"],
                    "photo": r["photo"], "market": r["market"], "label": r["label"], "line": r["line"], "side": r["side"],
                    "p": r["p_model"], "fair": r["fair"], "edge": r["edge"], "p_mkt": r["p_mkt"],
                    **({"p_raw": r["p_raw"], "fair_raw": round(1 / r["p_raw"], 2)} if r.get("p_raw") else
                       {"p_raw": it.get("p_raw", r["p_model"]), "fair_raw": it.get("fair_raw", r["fair"])}),
                    "books": {**(it.get("books") or {}), r["book"]: r["price"]},
                    "best_book": r["book"], "best_odds": r["price"], "kind": r["kind"]})
    out.sort(key=lambda x: -(x["edge"] or 0))
    res["picks"] = [k for k in out if k["kind"] != "firme"]
    res["hl_saved"] = [k for k in out if k["kind"] in ("pick", "firme")]
    res["frozen"] = True


def log_highlights(res: dict, tip_off: str | None) -> int:
    """Guarda los Destacados y el resto de las Jugadas de un partido que todavía
    no empezó. Hasta el inicio se actualizan con las cuotas. La que sale de la lista
    NO se borra: queda marcada `dropped` y se sigue hasta el final, porque la app la recomendó."""
    tip = _ts(tip_off or "")
    if not tip or time.time() >= tip or res.get("error") or not res.get("players"):
        return 0
    rows = highlights(res)
    # el resto de la lista "Jugadas" (las que no entraron en los Destacados del partido)
    have = {(k["player_id"], k["market"], k["line"], k["side"]) for k in rows}
    rows += [{**k, "kind": "jugada"} for k in res.get("picks") or []
             if (k["player_id"], k["market"], k["line"], k["side"]) not in have]
    keep = {(str(k["player_id"]), k["market"], float(k["line"]), k["side"]) for k in rows}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    gid = str(res.get("game_id"))
    with _hdb() as c:
        for pid, mk, ln, side in c.execute(
                "SELECT player_id, market, line, side FROM prop_picks WHERE game_id=? AND result IS NULL", (gid,)).fetchall():
            if (pid, mk, ln, side) not in keep:
                c.execute("UPDATE prop_picks SET dropped=1 WHERE game_id=? AND player_id=? AND market=? AND line=? AND side=?",
                          (gid, pid, mk, ln, side))
        for k in rows:
            c.execute("""INSERT INTO prop_picks (game_id, player_id, market, line, side, player, team, photo, label,
                         date, tip_off, kind, book, price, p_model, fair, edge, p_mkt, logged_at, p_raw)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(game_id, player_id, market, line, side) DO UPDATE SET kind=excluded.kind,
                         book=excluded.book, price=excluded.price, p_model=excluded.p_model, fair=excluded.fair,
                         p_raw=excluded.p_raw,
                         edge=excluded.edge, p_mkt=excluded.p_mkt, logged_at=excluded.logged_at, photo=excluded.photo,
                         dropped=0
                         WHERE prop_picks.result IS NULL""",
                      (gid, str(k["player_id"]), k["market"], float(k["line"]), k["side"], k["player"], k["team"],
                       k.get("photo"), k["label"], (tip_off or "")[:10], tip_off, k["kind"], k["best_book"],
                       k["best_odds"], k["p"], k.get("fair"), k["edge"], k.get("p_mkt"), now, k.get("p_raw")))
    return len(rows)


def _hbox(gid: str) -> dict | None:
    """Box score de ESPN de un partido terminado: athlete_id → pts, reb, ast, triples."""
    from altlines import live
    d = live._get_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
                       params={"event": gid}).json()
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
        return None

    def num(x):
        try:
            return float(str(x).split("-")[0])        # "3-7" (triples) → 3
        except Exception:  # noqa: BLE001
            return None
    out = {}
    for team in (d.get("boxscore") or {}).get("players") or []:
        for blk in team.get("statistics") or []:
            labels = blk.get("labels") or []
            for a in blk.get("athletes") or []:
                aid = str((a.get("athlete") or {}).get("id"))
                st = a.get("stats") or []
                if not st or a.get("didNotPlay"):
                    out[aid] = None
                    continue
                v = dict(zip(labels, st))
                s = {"pts": num(v.get("PTS")), "reb": num(v.get("REB")), "ast": num(v.get("AST")), "threes": num(v.get("3PT"))}
                out[aid] = None if any(x is None for x in s.values()) else s
    return out


def settle_highlights() -> int:
    with _hdb() as c:
        pend = c.execute("SELECT DISTINCT game_id FROM prop_picks WHERE result IS NULL AND tip_off < ?",
                         (datetime.now(timezone.utc).isoformat(),)).fetchall()
    n = 0
    for (gid,) in pend:
        try:
            box = _hbox(gid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("props box %s: %s", gid, exc)
            continue
        if box is None:
            continue
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with _hdb() as c:
            for pid, mk, ln, side, price in c.execute(
                    "SELECT player_id, market, line, side, price FROM prop_picks WHERE game_id=? AND result IS NULL", (gid,)).fetchall():
                s = box.get(pid)
                if s is None:
                    res, pnl, val, stat = "void", 0.0, None, "no jugó"
                else:
                    val = sum(s[k] for k in STAT_OF[mk])
                    if val == ln:                      # línea entera clavada: devuelve la plata
                        res, pnl = "void", 0.0
                    else:
                        hit = val > ln if side == "over" else val < ln
                        res, pnl = ("win", price - 1) if hit else ("loss", -1.0)
                    stat = f'{int(s["pts"])} PTS · {int(s["reb"])} REB · {int(s["ast"])} AST · {int(s["threes"])} 3P'
                c.execute("""UPDATE prop_picks SET result=?, pnl=?, value=?, stat=?, settled_at=?
                             WHERE game_id=? AND player_id=? AND market=? AND line=? AND side=?""",
                          (res, pnl, val, stat, now, gid, pid, mk, ln, side))
                n += 1
    return n


def results(days: int = 60) -> dict:
    """Destacados guardados con su resultado, con la misma forma que los del doble-doble."""
    import sqlite3
    try:
        settle_highlights()
    except Exception as exc:  # noqa: BLE001
        logger.warning("props settle: %s", exc)
    with _hdb() as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM prop_picks WHERE date >= date('now', ?) ORDER BY tip_off DESC, edge DESC", (f"-{days} days",))]

    def summ(rs):
        done = [r for r in rs if r["result"] in ("win", "loss")]
        won = sum(r["result"] == "win" for r in done)
        pnl = sum(r["pnl"] or 0 for r in done)
        return {"picks": len(rs), "settled": len(done), "won": won, "lost": len(done) - won,
                "open": sum(r["result"] is None for r in rs), "pnl": round(pnl, 2),
                "roi": round(pnl / len(done), 4) if done else None,
                "expected": round(sum(r["p_model"] for r in done), 2)}
    # El récord principal es el de los Destacados; "jugada" suma todas las de la lista Jugadas.
    live_ = [r for r in rows if not r.get("dropped")]
    return {"picks": rows, "summary": summ([r for r in live_ if r["kind"] != "jugada"]),
            "by_kind": {"pick": summ([r for r in live_ if r["kind"] == "pick"]),
                        "firme": summ([r for r in live_ if r["kind"] == "firme"]),
                        "jugada": summ([r for r in live_ if r["kind"] != "firme"]),
                        "retirada": summ([r for r in rows if r.get("dropped")])}}


# ── Seguimiento en vivo de las jugadas abiertas ──────────────────────────────

LIVE_TTL_S = 20              # cada cuánto se vuelve a bajar el box score de un partido en juego
_live_cache: dict[str, tuple[float, dict]] = {}


def _live_box(gid: str) -> dict:
    """Box score de ESPN de un partido en juego o terminado (gratis, sin créditos)."""
    hit = _live_cache.get(gid)
    if hit and time.time() - hit[0] < LIVE_TTL_S:
        return hit[1]
    from altlines import live
    d = live._get_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
                       params={"event": gid}).json()
    st = (((d.get("header") or {}).get("competitions") or [{}])[0].get("status") or {}).get("type") or {}

    def num(x):
        try:
            return float(str(x).split("-")[0])
        except Exception:  # noqa: BLE001
            return 0.0
    players = {}
    for team in (d.get("boxscore") or {}).get("players") or []:
        for blk in team.get("statistics") or []:
            labels = blk.get("labels") or []
            for a in blk.get("athletes") or []:
                v = dict(zip(labels, a.get("stats") or []))
                players[str((a.get("athlete") or {}).get("id"))] = {
                    "pts": num(v.get("PTS")), "reb": num(v.get("REB")), "ast": num(v.get("AST")),
                    "threes": num(v.get("3PT")), "min": num(v.get("MIN")), "dnp": bool(a.get("didNotPlay"))}
    out = {"state": st.get("state"), "detail": st.get("shortDetail") or st.get("detail") or "",
           "completed": bool(st.get("completed")), "players": players}
    _live_cache[gid] = (time.time(), out)
    return out


def live_tracker() -> dict:
    """Lo que lleva cada jugador de las jugadas abiertas cuyo partido ya empezó."""
    with _hdb() as c:
        rows = c.execute("""SELECT game_id, player_id, market, line, side FROM prop_picks
                            WHERE result IS NULL AND tip_off < ?""",
                         (datetime.now(timezone.utc).isoformat(),)).fetchall()
    games, picks, done = {}, [], False
    # además de las guardadas: los partidos de hoy que ya empezaron (para las tarjetas de Jugadas)
    gids = {r[0] for r in rows}
    try:
        from altlines import dd_live
        gids |= {str(g["game_id"]) for g in dd_live.get_slate(None).get("games") or []
                 if g.get("state") in ("in", "post") or _ts(g.get("tip_off") or "") <= time.time()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("props live slate: %s", exc)
    pending = {r[0] for r in rows}
    for gid in sorted(gids):
        try:
            b = _live_box(gid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("props live %s: %s", gid, exc)
            continue
        games[gid] = {"state": b["state"], "detail": b["detail"],
                      "players": {} if b["state"] == "pre" else
                      {pid: {k: s[k] for k in ("pts", "reb", "ast", "threes", "min")} for pid, s in b["players"].items()}}
        done = done or (b["completed"] and gid in pending)
        if b["state"] == "pre":
            continue
        for _, pid, mk, ln, side in (r for r in rows if r[0] == gid):
            s = b["players"].get(pid)
            picks.append({"game_id": gid, "player_id": pid, "market": mk, "line": ln, "side": side,
                          "value": None if s is None else sum(s[k] for k in STAT_OF[mk]),
                          "min": None if s is None else s["min"], "dnp": bool(s and s["dnp"])})
    settled = 0
    if done:                 # terminó alguno: se cierra ya, y la app recarga Resultados
        try:
            settled = settle_highlights()
        except Exception as exc:  # noqa: BLE001
            logger.warning("props settle: %s", exc)
    return {"games": games, "picks": picks, "settled": settled,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
