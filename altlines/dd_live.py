"""
NBA — doble-doble / triple-doble en vivo: calendario, simulación, cuotas,
registro de jugadas y liquidación.

  • calendario y planteles: ESPN (gratis, ya cacheado por altlines.live)
  • logs con robos y tapas: ESPN gamelog (caché propio "ddlog")
  • lesionados: reporte de ESPN (altlines.context.fetch_injuries)
  • cuotas: con NBA_ODDS=net en backend/.env, odds-api.net (altlines.odds_net).
    Si no, OddsPapi, torneo NBA (132), mercados "Player Double-Double" y
    "Player Triple-Double" (sólo Sí) de bet365 AR, Betano y Betsson.
    Una consulta por casa = 3 por actualización, del MISMO plan que Fútbol
    (250/mes): se piden sólo cuando el usuario toca "Pedir cuotas".
  • jugadas: data/altlines/dd_picks.db; se liquidan con el box score de ESPN.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from altlines import config as cfg, props_dist
from altlines import dd, live
from altlines.slate import get_slate

logger = logging.getLogger(__name__)

DIR = cfg.DATA_DIR / "dd"
ODDS_DIR = DIR / "odds"
DB = cfg.DATA_DIR / "dd_picks.db"
SIM_TTL_S = 2 * 3600
DIST_V = 1                   # versión de la distribución de puntos/rebotes/asistencias guardada
MIN_AVG_MIN = 16             # minutos promedio recientes para entrar a la lista
def _books(priority_max: int = 9) -> list[str]:
    """Casas para NBA: futbol/books.json ('nba': true). Nada fijo en el código."""
    from altlines import odds_net
    if odds_net.on():
        return odds_net.book_ids() + odds_net.papi_books()
    from futbol import books
    return books.nba_ids(priority_max)


ODDSPAPI_MONTH_LIMIT = 250     # (v4, ya no se usa: el plan lo informa RapidAPI)
NBA_TOURNAMENT = 132
MARKET_IDS = {112602: "dd", 112603: "td"}
ODDS_DAY_CAP = None          # sin tope diario: NBA pide cuotas cuando quiera mientras quede plan de RapidAPI
# Una jugada: la cuota paga al menos esto por encima de la justa del modelo.
PICK_EDGE = 0.08
PICK_MIN_P = {"dd": 0.15, "td": 0.05}
# Más que esto no es ventaja: es que la casa sabe algo que el modelo no (rol,
# minutos, lesión). Se muestra como "dudosa", no como jugada.
PICK_MAX_EDGE = 0.60
# "Lo más firme": los doble-doble más probables que pagan algo jugable. La app
# los muestra como recomendación, así que también van a Resultados.
FIRM_MIN_P = 0.40
FIRM_MIN_ODDS = 1.40
HEADSHOT = "https://a.espncdn.com/i/headshots/nba/players/full/{aid}.png"

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


# ══════════════════════════════════════════════════════════════════════════════
# Logs con robos y tapas
# ══════════════════════════════════════════════════════════════════════════════

def fetch_games(aid: str, season: int, current: bool) -> list[dict]:
    key = f"{aid}_{season}"
    ttl = cfg.LIVE_LOGS_TTL_S if current else None
    cached = live._cache_get("ddlog", key, ttl)
    if cached is not None:
        return cached
    try:
        data = live._get_json(live._ESPN_GAMELOG.format(aid=aid), params={"season": season}).json()
    except Exception as exc:  # noqa: BLE001
        status = getattr(getattr(exc, "response", None), "status_code", 500)
        if status and status < 500:
            live._cache_put("ddlog", key, [])
            return []
        logger.warning("ESPN gamelog %s/%s: %s", aid, season, exc)
        return live._cache_get("ddlog", key, None) or []
    names = data.get("names") or []
    idx = {n: i for i, n in enumerate(names)}
    events = data.get("events") or {}
    rows, seen = [], set()
    for st in data.get("seasonTypes") or []:
        label = str(st.get("displayName") or "")
        if "Regular Season" not in label:
            continue
        for cat in st.get("categories") or []:
            for ev in cat.get("events") or []:
                eid = str(ev.get("eventId"))
                stats = ev.get("stats") or []
                if eid in seen or len(stats) < len(names):
                    continue
                seen.add(eid)
                meta = events.get(eid) or {}
                f = lambda k: live._to_float(stats[idx[k]]) if k in idx else 0.0  # noqa: E731
                rows.append({"event_id": eid, "date": (meta.get("gameDate") or "")[:10],
                             "season": season, "team": live._abbr(meta.get("team")),
                             "opp": live._abbr(meta.get("opponent")),
                             "min": f("minutes"), "pts": f("points"), "reb": f("totalRebounds"),
                             "ast": f("assists"), "stl": f("steals"), "blk": f("blocks")})
    rows.sort(key=lambda r: r["date"])
    live._cache_put("ddlog", key, rows)
    return rows


def history(aid: str, season: int) -> list[dict]:
    g = fetch_games(aid, season - 1, False) + fetch_games(aid, season, True)
    return [x for x in sorted(g, key=lambda r: r["date"]) if (x.get("min") or 0) > 0]


# ══════════════════════════════════════════════════════════════════════════════
# Simulación de un partido
# ══════════════════════════════════════════════════════════════════════════════

def _injuries() -> dict[str, dict]:
    try:
        from altlines.context import fetch_injuries
        return {str(i["athlete_id"]): i for i in fetch_injuries()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("injuries: %s", exc)
        return {}


def _team(t: dict) -> dict:
    return {k: t.get(k) for k in ("tricode", "name", "display", "city", "color", "logo", "score")}


_BOX_SYNC = {"t": 0.0}
BOX_SYNC_S = 3600


def _sync_boxes(force: bool = False) -> None:
    """Baja los box scores nuevos de esta temporada (gratis, ESPN), cada hora."""
    from altlines import nba_box
    if force or time.time() - _BOX_SYNC["t"] > BOX_SYNC_S:
        try:
            nba_box.crawl(seasons=(live._season_end_year(),))
        except Exception as exc:  # noqa: BLE001
            logger.warning("box sync: %s", exc)
        _BOX_SYNC["t"] = time.time()


def _in_rotation(P, T, pid: str, team: str) -> bool:
    """¿Viene jugando? Jugó alguno de los últimos 8 partidos de su equipo; o,
    con la temporada recién empezada, fue de rotación la temporada pasada."""
    tg = T[T["team"] == team].tail(8)
    mine = P[P["id"] == pid]
    if mine.empty:
        return False
    if mine["game"].isin(tg["game"]).any():
        return True
    season = live._season_end_year()
    this = T[(T["team"] == team) & (T["season"] == season)]
    prev = mine[mine["season"] == season - 1]
    return len(this) < 6 and len(prev) >= 15 and prev["min"].tail(15).mean() >= 15


# Probabilidad final = mitad el partido simulado, mitad su historia propia
# (altlines.dd). En el backtest 2025-26 con planteles como en vivo, la mezcla
# le gana a cada uno solo: Brier 0,0748 vs 0,0773 (partido) y 0,0751 (jugador).
GAME_WEIGHT = 0.5
# Lo mismo para puntos, rebotes, asistencias y combinaciones (altlines.props_dist).
# Backtest 2025-26 (altlines/tools/props_backtest.py, 571.737 líneas): mitad y mitad
# también gana (log-loss 0,4449 vs 0,4545 partido solo y 0,4615 jugador solo).
PROPS_GAME_WEIGHT = 0.5


def _blend(pr: dict, P) -> dict:
    h = P[P["id"] == pr["id"]].tail(40)
    hh = [{"min": x.min, "pts": x.pts, "reb": x.reb, "ast": x.ast, "stl": x.stl, "blk": x.blk,
           "season": x.season, "team": x.team} for x in h.itertuples()]
    par = dd.project(hh)
    gd = pr.get("dist") or {}
    if par is None:
        return {"p_dd": round(pr["p_dd"], 4), "p_td": round(pr["p_td"], 4),
                "p_dd_game": round(pr["p_dd"], 4), "p_dd_player": None, "dist": gd}
    solo = dd.simulate(par, seed=int(pr["id"]) % 100_000, dist=True)
    w = GAME_WEIGHT
    sd = solo.get("dist") or {}
    dist = {m: props_dist.blend(gd.get(m), sd.get(m), PROPS_GAME_WEIGHT) if m in sd else gd[m] for m in gd}
    return {"p_dd": round(w * pr["p_dd"] + (1 - w) * solo["p_dd"], 4),
            "p_td": round(w * pr["p_td"] + (1 - w) * solo["p_td"], 4),
            "p_dd_game": round(pr["p_dd"], 4), "p_dd_player": round(solo["p_dd"], 4), "dist": dist}


def simulate_game(game: dict, refresh: bool = False) -> dict:
    """Simula el PARTIDO completo (altlines.dd_game) 20.000 veces."""
    from altlines import dd_game, nba_box
    gid = str(game["game_id"])
    f = DIR / f"sim_{gid}.json"
    started = _ts(game.get("tip_off") or "") <= time.time()
    if f.exists() and (started or (not refresh and time.time() - f.stat().st_mtime < SIM_TTL_S)):
        out = json.loads(f.read_text(encoding="utf-8"))
        if out.get("engine") == "game" and out.get("dist_v") == DIST_V:
            return _attach_odds(out)
    # Sólo partidos de días anteriores: si se simula tarde, el propio partido no entra.
    cutoff = (game.get("tip_off") or "9999-12-31")[:10]
    with _lock(gid):
        _sync_boxes(force=refresh)
        T, P = nba_box.tables()
        lg = dd_game.league_means(T)
        inj = _injuries()
        prof, lists, meta, out_list = {}, {"home": [], "away": []}, {}, []
        for side in ("home", "away"):
            tr = game[side]["tricode"]
            prof[side] = dd_game.team_profile(T, tr, cutoff, lg)
            for a in live.fetch_roster(tr, refresh=refresh):
                aid = str(a["athlete_id"])
                st = (inj.get(aid) or {}).get("status", "")
                if not _in_rotation(P, T, aid, tr):
                    continue
                if st.lower() == "out":
                    out_list.append({"player_id": aid, "name": a["name"], "team": tr, "status": st})
                    continue
                pp = dd_game.player_profile(P, T, aid, cutoff)
                if pp is None:
                    continue
                lists[side].append(pp)
                meta[aid] = (a, tr, side, st)
        r = dd_game.simulate_game(prof["home"], prof["away"], lists["home"], lists["away"], lg,
                                  seed=int(gid) % 1_000_000, dist=True)
        players = []
        for pr in r["players"]:
            a, tr, side, st = meta[pr["id"]]
            if pr["min"] < MIN_AVG_MIN - 2:
                continue
            h = P[P["id"] == pr["id"]].tail(10)
            cnt = [sum(getattr(x, k) >= 10 for k in dd.STATS) for x in h.itertuples()]
            last = h.iloc[-1] if len(h) else None
            players.append({
                "player_id": pr["id"], "name": a["name"], "team": tr, "side": side,
                "pos": a.get("position") or "", "photo": HEADSHOT.format(aid=pr["id"]),
                "status": st, "doubt": bool(st),
                **_blend(pr, P),
                "p10": {k: round(v, 3) for k, v in pr["p10"].items()},
                "mean": {k: round(v, 1) for k, v in pr["mean"].items()},
                "min": round(pr["min"], 1), "combos": {k: round(v, 3) for k, v in pr["combos"].items()},
                "l10": {"n": len(cnt), "dd": sum(c >= 2 for c in cnt), "td": sum(c >= 3 for c in cnt)},
                "last_date": None if last is None else last["date"],
            })
        players.sort(key=lambda p: -p["p_dd"])
        sim = {"pace": round(r["pace"], 1), "p_home": round(r["p_home"], 3), "margin": round(r["margin"], 1),
               "p_ot": round(r["p_ot"], 3), "p_blowout": round(r["p_blowout"], 3),
               "teams": {k: {kk: round(vv, 3 if kk == "fg" else 1) for kk, vv in v.items()} for k, v in r["teams"].items()}}
        out = {"game_id": gid, "tip_off": game.get("tip_off"), "state": game.get("state"),
               "status": game.get("status"), "home": _team(game["home"]), "away": _team(game["away"]),
               "players": players, "out": out_list, "sim": sim, "engine": "game", "dist_v": DIST_V,
               "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "n_sim": dd_game.N_SIM}
        DIR.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out), encoding="utf-8")
    return _attach_odds(out)


def cached_sim(gid: str) -> dict | None:
    f = DIR / f"sim_{gid}.json"
    if not f.exists():
        return None
    try:
        return _attach_odds(json.loads(f.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Cuotas (OddsPapi)
# ══════════════════════════════════════════════════════════════════════════════

def _odds_file(book: str) -> Path:
    return ODDS_DIR / f"odds_{book}.json"


def odds_age_s() -> float | None:
    from altlines import odds_net      # Stake (OddsPapi) puede quedar viejo sin cupo: no cuenta
    bs = odds_net.book_ids() if odds_net.on() else _books()
    ages = [time.time() - _odds_file(b).stat().st_mtime for b in bs if _odds_file(b).exists()]
    return max(ages) if ages else None


def _day() -> str:
    from altlines.slate import nba_today            # la noche dura hasta las 3 a.m.
    return nba_today().strftime("%Y-%m-%d")


def odds_refreshes_today() -> int:
    try:
        u = json.loads((ODDS_DIR / "refresh.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    return int(u.get(_day(), 0))


def plan_odds() -> dict:
    """Casas de books.json con 'nba': true. Con la v5 (RapidAPI) se piden todas
    juntas: cuesta 1 pedido por partido de la noche (+1 por la lista).
    NBA puede usar el plan de RapidAPI hasta el último pedido (sin reserva).
    Con NBA_ODDS=net el plan es el de odds-api.net (altlines.odds_net)."""
    from altlines import odds_net
    if odds_net.on():
        u = odds_net.usage()
        return {"books": _books() if u["remaining"] > 0 else [], "used": u["used"], "limit": u["limit"],
                "remaining": u["remaining"]}
    from futbol import oddspapi as op
    u = op.usage(own=True)
    left = u["remaining"] if u.get("remaining") is not None else u["limit"] - u["used"]
    pick = _books() if left > 0 else []
    return {"books": pick, "used": u["used"], "limit": u["limit"], "remaining": left}


def _keep_dd(mid: int) -> bool:
    return mid in MARKET_IDS


def refresh_odds() -> dict:
    """Pide las cuotas DD/TD de la noche por RapidAPI (v5): 1 pedido por partido."""
    from altlines import odds_net
    if odds_net.on():
        return odds_net.refresh_all()
    from futbol import oddspapi as op
    plan = plan_odds()
    if not plan["books"]:
        return {"ok": False, "reason": "month", "used": plan["used"], "limit": plan["limit"]}
    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    errors = []
    try:
        games = [x for x in op.fixtures(NBA_TOURNAMENT, hours=36) if (x.get("status") or {}).get("statusId") == 0]
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "error", "errors": [str(exc)]}
    # Si el plan no alcanza para toda la noche, primero los que empiezan antes.
    left = plan_odds()["remaining"]
    if len(games) > left:
        games = sorted(games, key=lambda x: x.get("startTime") or 0)[:max(0, left)]
        errors.append(f"el plan alcanzó para {len(games)} partidos")
    if not games:
        return {"ok": False, "reason": "month", "used": plan["used"], "limit": plan["limit"]}
    per_book = {b: [] for b in plan["books"]}
    got_odds = op.fixture_odds(games, plan["books"])
    if len(got_odds) < len(games):
        errors.append(f"{len(games) - len(got_odds)} partidos sin respuesta")
    # Del mismo pedido salen puntos, rebotes, asistencias y combinaciones (sin costo extra)
    n_props = 0
    try:
        from altlines import props_live
        n_props = props_live.save_oddspapi(got_odds)
    except Exception as exc:  # noqa: BLE001 - el doble-doble sigue aunque esto falle
        errors.append(f"líneas de jugador: {exc}")
    for fx, d in got_odds:
        conv = op.to_v4(fx, d, _keep_dd)
        for b in plan["books"]:
            per_book[b].append({**conv, "bookmakerOdds": {b: conv["bookmakerOdds"][b]} if b in conv["bookmakerOdds"] else {}})
    op.name_players([fx for lst in per_book.values() for fx in lst])
    got = 0
    for b, lst in per_book.items():
        if lst:
            _odds_file(b).write_text(json.dumps(lst), encoding="utf-8")
            got += 1
    f = ODDS_DIR / "refresh.json"
    f.write_text(json.dumps({_day(): odds_refreshes_today() + 1}), encoding="utf-8")
    (ODDS_DIR / "game_t.json").unlink(missing_ok=True)      # todas quedaron con la misma hora
    return {"ok": got > 0, "books": got, "games": len(games), "errors": errors, "props_lines": n_props,
            "today": odds_refreshes_today(), "cap": ODDS_DAY_CAP}


def game_odds_t(gid) -> float | None:
    """Cuándo se pidieron las cuotas de ESTE partido solo (None: valen las de la noche)."""
    try:
        return json.loads((ODDS_DIR / "game_t.json").read_text(encoding="utf-8")).get(str(gid))
    except Exception:  # noqa: BLE001
        return None


def refresh_odds_game(gid: str) -> dict:
    """Pide las cuotas de UN partido (1 pedido de OddsPapi, +1 si la lista de
    partidos está vencida) y reemplaza sólo las suyas: las de los demás no se tocan.
    No cuenta para el tope diario de actualizaciones de la noche."""
    import os
    from altlines import odds_net
    if odds_net.on():
        return odds_net.refresh_game(gid)
    from futbol import oddspapi as op
    game = find_game(gid)
    if not game:
        raise KeyError(gid)
    tip = _ts(game.get("tip_off") or "")
    if game.get("state") != "pre" or (tip and tip <= time.time()):
        return {"ok": False, "reason": "started"}
    plan = plan_odds()
    if not plan["books"]:
        return {"ok": False, "reason": "month", "used": plan["used"], "limit": plan["limit"]}
    words = lambda s: set(live.norm_name(s or "").split())
    nick = lambda t: (live.norm_name(t.get("name") or t.get("display") or "").split() or [""])[-1]
    hn, an = nick(game["home"]), nick(game["away"])
    try:
        fxs = op.fixtures(NBA_TOURNAMENT, hours=36)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "error", "errors": [str(exc)]}
    fx = None
    for x in fxs:
        pt = x.get("participants") or {}
        names = words(pt.get("participant1Name")) | words(pt.get("participant2Name"))
        if hn in names and an in names and (not tip or abs((x.get("startTime") or 0) - tip) < 6 * 3600):
            fx = x
            break
    if fx is None:
        return {"ok": False, "reason": "nofixture"}
    got = op.fixture_odds([fx], plan["books"])
    if not got:
        return {"ok": False, "reason": "error", "errors": ["OddsPapi no respondió"]}
    errors, n_props = [], 0
    try:
        from altlines import props_live
        n_props = props_live.save_oddspapi(got, merge=True)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"líneas de jugador: {exc}")
    conv = op.to_v4(got[0][0], got[0][1], _keep_dd)
    op.name_players([conv])
    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    books = 0
    for b in plan["books"]:
        f = _odds_file(b)
        old_m = f.stat().st_mtime if f.exists() else None
        try:
            lst = json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
        except Exception:  # noqa: BLE001
            lst = []
        lst = [x for x in lst if x.get("fixtureId") != conv["fixtureId"]]
        if b in conv["bookmakerOdds"]:
            books += 1
        lst.append({**conv, "bookmakerOdds": {b: conv["bookmakerOdds"][b]} if b in conv["bookmakerOdds"] else {}})
        f.write_text(json.dumps(lst), encoding="utf-8")
        if old_m is not None:               # la antigüedad de la noche sigue siendo la del pedido general
            os.utime(f, (old_m, old_m))
    try:
        gt = json.loads((ODDS_DIR / "game_t.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        gt = {}
    gt[str(gid)] = time.time()
    (ODDS_DIR / "game_t.json").write_text(json.dumps(gt), encoding="utf-8")
    # Pinnacle (la referencia sin margen) de este partido, para comparar con cuotas de la misma hora
    try:
        from altlines import props_live
        if props_live.api_key():
            eid = props_live.event_for(game)
            if eid:
                props_live.fetch_net(eid, refresh=True)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Pinnacle: {exc}")
    return {"ok": True, "books": books, "props_lines": n_props, "errors": errors, "usage": op.usage(own=True)}


def _slim(data: list[dict]) -> list[dict]:
    """Sólo los mercados de doble-doble y triple-doble (la respuesta entera pesa mucho)."""
    slim = []
    for fx in data or []:
        keep = {k: v for k, v in fx.items() if k != "bookmakerOdds"}
        bo = {}
        for bk, b in (fx.get("bookmakerOdds") or {}).items():
            ms = {mid: m for mid, m in (b.get("markets") or {}).items() if int(mid) in MARKET_IDS}
            if ms:
                bo[bk] = {"markets": ms}
        keep["bookmakerOdds"] = bo
        slim.append(keep)
    return slim


def _odds_rows() -> list[dict]:
    rows = []
    for book in _books():
        f = _odds_file(book)
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for fx in data:
            for b in (fx.get("bookmakerOdds") or {}).values():
                for mid, m in (b.get("markets") or {}).items():
                    mk = MARKET_IDS.get(int(mid))
                    if not mk:
                        continue
                    for ok_, o in (m.get("outcomes") or {}).items():
                        for pk, pl in (o.get("players") or {}).items():
                            if not pl.get("price") or not pl.get("active", True):
                                continue
                            rows.append({"fixture": fx.get("fixtureId"), "start": fx.get("startTime"),
                                         "book": book, "market": mk, "key": _key(pl.get("playerName")),
                                         "price": float(pl["price"]), "pid": pk, "mid": mid, "oid": ok_})
    return rows


def _attach_odds(sim: dict) -> dict:
    rows = _odds_rows()
    tip = sim.get("tip_off") or ""
    by_key: dict[tuple, dict] = {}
    for r in rows:
        if tip and r["start"] and abs(_ts(r["start"]) - _ts(tip)) > 12 * 3600:
            continue
        by_key.setdefault((r["key"], r["market"]), {})[r["book"]] = r["price"]
    for p in sim["players"]:
        k = _key(p["name"])
        p["odds"] = {}
        for mk in ("dd", "td"):
            books = by_key.get((k, mk)) or {}
            if not books:
                continue
            best = max(books, key=books.get)
            pm = p["p_" + mk]
            price = books[best]
            p["odds"][mk] = {"books": books, "best_book": best, "best_price": price,
                             "min_odds": round(1 / pm, 2) if pm > 0 else None,
                             "edge": round(pm * price - 1, 4)}
    gt = game_odds_t(sim.get("game_id"))
    age = time.time() - gt if gt else odds_age_s()
    sim["odds_age_s"] = None if age is None else round(age)
    sim["picks"] = picks_of(sim)
    return sim


# ── Movimiento de la línea ────────────────────────────────────────────────
# OddsPapi v5 (RapidAPI) /fixtures/odds/historical con los oddsIds exactos del
# jugador en cada casa: 1 pedido chico por jugada. Se reusa HIST_TTL_S.
HIST_DIR = ODDS_DIR / "hist"
HIST_TTL_S = 10 * 60


def _hist(fixture: str, odds_ids: list) -> dict:
    """{oddsId: [[t_ms, price, active], ...]} de un partido (sólo esos oddsIds)."""
    from futbol import oddspapi as op
    key = "-".join(sorted(i.split(":", 1)[1].replace(":", "_") for i in odds_ids))
    f = HIST_DIR / f"v5_{fixture}_{abs(hash(key)) % 10**10}.json"
    if f.exists() and time.time() - f.stat().st_mtime < HIST_TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    from altlines import odds_net
    if op.direct() or odds_net.on():
        return {}          # la API directa cobra el historial por casa y odds-api.net no lo da así: no se pide
    with _lock("hist"):
        d = op._get("fixtures/odds/historical", fixtureId=fixture, oddsIds=",".join(odds_ids))
    out = {}
    for book, entries in (d.get("odds") or {}).items():
        for oid, snaps in (entries or {}).items():
            out[oid] = sorted([[s.get("changedAt"), s.get("price"), bool(s.get("active", True))]
                               for s in (snaps or {}).values() if s.get("changedAt")], key=lambda x: x[0])
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def line_history(gid: str, player_id: str, market: str) -> dict:
    """Cómo se movió la cuota de un jugador en cada casa (para la ficha de la jugada)."""
    sim = cached_sim(gid)
    if sim is None:
        raise LookupError("Partido sin simular")
    p = next((x for x in sim["players"] if x["player_id"] == player_id), None)
    if p is None:
        raise LookupError("Jugador no encontrado")
    tip, key = sim.get("tip_off") or "", _key(p["name"])
    rows = [r for r in _odds_rows() if r["key"] == key and r["market"] == market
            and not (tip and r["start"] and abs(_ts(r["start"]) - _ts(tip)) > 12 * 3600)]
    if not rows:
        return {"series": {}, "fixture": None}
    fixture = rows[0]["fixture"]
    rows = sorted(rows, key=lambda r: -r["price"])[:3]
    ids = {f"{fixture}:{r['book']}:{r['oid']}:{r['pid']}": r for r in rows}
    h = _hist(fixture, list(ids))
    series = {}
    for oid, r in ids.items():
        pts = [{"t": datetime.fromtimestamp(t_ / 1000, timezone.utc).isoformat(timespec="seconds"), "p": float(pr), "on": on}
               for t_, pr, on in h.get(oid, []) if t_ and pr]
        if not pts or pts[-1]["p"] != r["price"]:               # el precio de ahora siempre al final
            at = datetime.fromtimestamp(_odds_file(r["book"]).stat().st_mtime, timezone.utc)
            pts.append({"t": at.isoformat(timespec="seconds"), "p": r["price"], "on": True})
        series[r["book"]] = pts
    return {"series": series, "fixture": fixture, "tip_off": tip}


def _key(name) -> str:
    """Nombre comparable sin importar el orden ("Randle Julius" en bet365)."""
    return " ".join(sorted(live.norm_name(name).split()))


def _ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def firm_of(sim: dict) -> list[dict]:
    """Los de "Lo más firme" de este partido (misma regla que la app)."""
    out = []
    for p in sim["players"]:
        o = (p.get("odds") or {}).get("dd")
        if o and o.get("best_price") and o["best_price"] >= FIRM_MIN_ODDS and p["p_dd"] >= FIRM_MIN_P and not p.get("doubt"):
            out.append({"game_id": sim["game_id"], "player_id": p["player_id"], "player": p["name"],
                        "team": p["team"], "market": "dd", "p_model": p["p_dd"], "book": o["best_book"],
                        "price": o["best_price"], "min_odds": o.get("min_odds"), "edge": o.get("edge"),
                        "photo": p.get("photo"), "kind": "firme"})
    return out


def picks_of(sim: dict) -> list[dict]:
    out = []
    for p in sim["players"]:
        for mk, o in (p.get("odds") or {}).items():
            pm = p["p_" + mk]
            # Si el partido simulado y su historial no coinciden (uno da más del
            # doble que el otro), el rol cambió: no es una jugada confiable.
            pg, pp = p.get("p_dd_game"), p.get("p_dd_player")
            split = mk == "dd" and pg is not None and pp is not None and max(pg, pp) > 2 * max(min(pg, pp), 1e-3)
            o["dubious"] = o["edge"] > PICK_MAX_EDGE or split
            if pm >= PICK_MIN_P[mk] and PICK_EDGE <= o["edge"] and not o["dubious"] and not p.get("doubt"):
                out.append({"game_id": sim["game_id"], "player_id": p["player_id"], "player": p["name"],
                            "team": p["team"], "market": mk, "p_model": pm, "book": o["best_book"],
                            "price": o["best_price"], "min_odds": o["min_odds"], "edge": o["edge"],
                            "photo": p["photo"]})
    out.sort(key=lambda x: -x["edge"])
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Registro y liquidación
# ══════════════════════════════════════════════════════════════════════════════

def _db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS picks (
        game_id TEXT, player_id TEXT, market TEXT, player TEXT, team TEXT, date TEXT, tip_off TEXT,
        book TEXT, price REAL, p_model REAL, edge REAL, logged_at TEXT,
        result TEXT, pnl REAL, line TEXT, settled_at TEXT,
        PRIMARY KEY (game_id, player_id, market))""")
    cols = {r[1] for r in c.execute("PRAGMA table_info(picks)")}
    if "kind" not in cols:                       # 'pick' (con valor), 'firme' o 'dudosa'
        c.execute("ALTER TABLE picks ADD COLUMN kind TEXT DEFAULT 'pick'")
    if "photo" not in cols:
        c.execute("ALTER TABLE picks ADD COLUMN photo TEXT")
    if "late" not in cols:                       # 1 = la simulación se hizo con el partido ya empezado
        c.execute("ALTER TABLE picks ADD COLUMN late INTEGER DEFAULT 0")
    # Las que el usuario marcó "La juego" (se ven en Resultados con otro color)
    c.execute("""CREATE TABLE IF NOT EXISTS played (
        game_id TEXT, player_id TEXT, market TEXT, at TEXT, PRIMARY KEY (game_id, player_id, market))""")
    return c


def log_picks(sim: dict) -> None:
    """Guarda todo lo que la app recomienda de un partido simulado ANTES del
    inicio (aunque se abra la app después). Si ya estaba, actualiza precio y prob."""
    tip = _ts(sim.get("tip_off") or "")
    if _ts(sim.get("computed_at") or "") >= tip:
        return
    _store(sim, datetime.now(timezone.utc).isoformat(timespec="seconds"), prune=time.time() < tip)


def _rows(sim: dict) -> list[dict]:
    """Todo lo que la app recomienda de este partido: jugadas con valor y Lo más firme."""
    rows = [{**k, "kind": "pick"} for k in sim.get("picks") or []]
    have = {(k["player_id"], k["market"]) for k in rows}
    rows += [k for k in firm_of(sim) if (k["player_id"], k["market"]) not in have]
    have = {(k["player_id"], k["market"]) for k in rows}
    rows += [k for k in dubious_of(sim) if (k["player_id"], k["market"]) not in have]
    return rows


def dubious_of(sim: dict) -> list[dict]:
    """Las "Dudosas": la casa paga mucho más de lo esperado o los dos modelos no coinciden."""
    out = []
    for p in sim["players"]:
        for mk, o in (p.get("odds") or {}).items():
            if o.get("dubious"):
                out.append({"game_id": sim["game_id"], "player_id": p["player_id"], "player": p["name"],
                            "team": p["team"], "market": mk, "p_model": p["p_" + mk], "book": o["best_book"],
                            "price": o["best_price"], "min_odds": o.get("min_odds"), "edge": o.get("edge"),
                            "photo": p.get("photo"), "kind": "dudosa"})
    return out


def _store(sim: dict, now: str, prune: bool = True, late: bool = False) -> int:
    rows = _rows(sim)
    keep = {(k["player_id"], k["market"]) for k in rows}
    with _db() as c:
        # Antes del partido una jugada se puede caer (cuota, lesión, rol): se borra
        for pid, mk in [] if not prune else c.execute(
                "SELECT player_id, market FROM picks WHERE game_id=? AND result IS NULL", (sim["game_id"],)).fetchall():
            if (pid, mk) not in keep:
                c.execute("DELETE FROM picks WHERE game_id=? AND player_id=? AND market=?", (sim["game_id"], pid, mk))
        for k in rows:
            c.execute("""INSERT INTO picks (game_id, player_id, market, player, team, date, tip_off, book,
                         price, p_model, edge, logged_at, kind, photo, late) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(game_id, player_id, market) DO UPDATE SET book=excluded.book,
                         price=excluded.price, p_model=excluded.p_model, edge=excluded.edge,
                         logged_at=excluded.logged_at, kind=excluded.kind, photo=excluded.photo
                         WHERE picks.result IS NULL""",
                      (k["game_id"], k["player_id"], k["market"], k["player"], k["team"],
                       (sim.get("tip_off") or "")[:10], sim.get("tip_off"), k["book"], k["price"],
                       k["p_model"], k["edge"], now, k["kind"], k.get("photo"), int(bool(late))))
    return len(rows)


def backfill_shown() -> int:
    """Una vez: todo lo que la app mostró de partidos ya empezados y no se
    guardó (jugadas, Lo más firme, Dudosas). Las simuladas con el partido ya
    empezado quedan marcadas `late`. También completa las fotos que falten."""
    n = 0
    for f in DIR.glob("sim_*.json"):
        sim = cached_sim(f.stem.split("_", 1)[1])
        if not sim or _ts(sim.get("tip_off") or "") > time.time():
            continue
        late = _ts(sim.get("computed_at") or "") >= _ts(sim.get("tip_off") or "")
        rows = _rows(sim)
        with _db() as c:
            have = {(r[0], r[1]) for r in c.execute("SELECT player_id, market FROM picks WHERE game_id=?", (sim["game_id"],))}
            photos = {p["player_id"]: p.get("photo") for p in sim["players"]}
            for pid, ph in photos.items():
                c.execute("UPDATE picks SET photo=? WHERE game_id=? AND player_id=? AND photo IS NULL", (ph, sim["game_id"], pid))
            for k in rows:
                if (k["player_id"], k["market"]) in have:
                    continue
                c.execute("""INSERT OR IGNORE INTO picks (game_id, player_id, market, player, team, date, tip_off,
                             book, price, p_model, edge, logged_at, kind, photo, late) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (k["game_id"], k["player_id"], k["market"], k["player"], k["team"],
                           (sim.get("tip_off") or "")[:10], sim.get("tip_off"), k["book"], k["price"],
                           k["p_model"], k["edge"], sim.get("computed_at"), k["kind"], k.get("photo"), int(late)))
                n += 1
    return n


def played_keys() -> list[str]:
    with _db() as c:
        return [f"{g}|{p}|{m}" for g, p, m in c.execute("SELECT game_id, player_id, market FROM played")]


def set_played(game_id: str, player_id: str, market: str, on: bool) -> list[str]:
    with _db() as c:
        if on:
            c.execute("INSERT OR IGNORE INTO played VALUES (?,?,?,?)",
                      (game_id, player_id, market, datetime.now(timezone.utc).isoformat(timespec="seconds")))
        else:
            c.execute("DELETE FROM played WHERE game_id=? AND player_id=? AND market=?", (game_id, player_id, market))
    return played_keys()


def _box(gid: str) -> dict | None:
    """Box score de ESPN de un partido terminado: athlete_id → stats."""
    d = live._get_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
                       params={"event": gid}).json()
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
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
                out[aid] = {k.lower(): live._to_float(v.get(k)) for k in ("MIN", "PTS", "REB", "AST", "STL", "BLK")}
    return out


def settle() -> int:
    with _db() as c:
        pend = c.execute("SELECT DISTINCT game_id FROM picks WHERE result IS NULL AND tip_off < ?",
                         (datetime.now(timezone.utc).isoformat(),)).fetchall()
    n = 0
    for (gid,) in pend:
        try:
            box = _box(gid)
        except Exception as exc:  # noqa: BLE001
            logger.warning("box %s: %s", gid, exc)
            continue
        if box is None:
            continue
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with _db() as c:
            for pid, mk, price in c.execute("SELECT player_id, market, price FROM picks WHERE game_id=? AND result IS NULL",
                                            (gid,)).fetchall():
                s = box.get(pid)
                if s is None:
                    res, pnl, line = "void", 0.0, "no jugó"
                else:
                    cnt = sum(s[k] >= 10 for k in ("pts", "reb", "ast", "stl", "blk"))
                    hit = cnt >= (2 if mk == "dd" else 3)
                    res, pnl = ("win", price - 1) if hit else ("loss", -1.0)
                    line = " · ".join(f"{int(s[k])} {k.upper()}" for k in ("pts", "reb", "ast", "stl", "blk")
                                      if s[k] >= 5 or k in ("pts", "reb", "ast"))
                c.execute("UPDATE picks SET result=?, pnl=?, line=?, settled_at=? WHERE game_id=? AND player_id=? AND market=?",
                          (res, pnl, line, now, gid, pid, mk))
                n += 1
    return n


def results(days: int = 60) -> dict:
    try:
        settle()
    except Exception as exc:  # noqa: BLE001
        logger.warning("settle: %s", exc)
    with _db() as c:
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM picks WHERE date >= date('now', ?) ORDER BY tip_off DESC", (f"-{days} days",))]
        played = {(g, p, m) for g, p, m in c.execute("SELECT game_id, player_id, market FROM played")}
    for r in rows:
        r["kind"] = r.get("kind") or "pick"
        r["played"] = (r["game_id"], r["player_id"], r["market"]) in played

    def summ(rs):
        done = [r for r in rs if r["result"] in ("win", "loss")]
        won = sum(r["result"] == "win" for r in done)
        pnl = sum(r["pnl"] or 0 for r in done)
        return {"picks": len(rs), "settled": len(done), "won": won, "lost": len(done) - won,
                "open": sum(r["result"] is None for r in rs), "pnl": round(pnl, 2),
                "roi": round(pnl / len(done), 4) if done else None,
                "expected": round(sum(r["p_model"] for r in done), 2)}
    # El récord principal es de lo que la app recomienda; las Dudosas van aparte.
    return {"picks": rows, "summary": summ([r for r in rows if r["kind"] != "dudosa"]),
            "by_kind": {"pick": summ([r for r in rows if r["kind"] == "pick"]),
                        "firme": summ([r for r in rows if r["kind"] == "firme"]),
                        "dudosa": summ([r for r in rows if r["kind"] == "dudosa"]),
                        "played": summ([r for r in rows if r["played"]])}}


# ══════════════════════════════════════════════════════════════════════════════
# Calendario
# ══════════════════════════════════════════════════════════════════════════════

INJ_CHECK_TTL_S = 5 * 60       # cada cuánto se vuelve a bajar el parte de ESPN para el aviso


def _norm_status(s) -> str:
    return str(s or "").strip().lower()


def injury_status(date: str | None = None) -> dict:
    """Parte de lesionados de AHORA contra el que usó cada simulación guardada.

    Para cada partido que todavía no empezó y ya está simulado, lista los
    jugadores cuyo estado cambió desde la simulación (entró al parte, salió, o
    pasó de duda a baja). No simula ni gasta créditos: sólo baja el parte de
    ESPN (gratis), como mucho una vez cada `INJ_CHECK_TTL_S`.
    """
    from altlines.context import fetch_injuries
    fresh = live._cache_get("injuries", "nba", INJ_CHECK_TTL_S)
    report = fresh if fresh is not None else fetch_injuries(refresh=True)
    try:
        checked = datetime.fromtimestamp(live._cache_path("injuries", "nba").stat().st_mtime,
                                         timezone.utc).isoformat(timespec="seconds")
    except Exception:  # noqa: BLE001
        checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
    now_by_id = {str(i["athlete_id"]): i for i in report}
    games, total, latest = [], 0, None
    for g in (get_slate(date).get("games") or []):
        gid = str(g["game_id"])
        teams = {g["home"]["tricode"], g["away"]["tricode"]}
        for i in report:
            if i.get("team") in teams and i.get("date") and (latest is None or str(i["date"]) > latest):
                latest = str(i["date"])
        started = g.get("state") != "pre" or _ts(g.get("tip_off") or "") <= time.time()
        sim = None if started else cached_sim(gid)
        if not sim or sim.get("error"):
            continue
        # lo que vio la simulación: las bajas y el estado de cada jugador simulado
        was = {str(o["player_id"]): (o.get("name") or "", o.get("team") or "", o.get("status") or "Out")
               for o in sim.get("out") or []}
        for p in sim.get("players") or []:
            was[str(p["player_id"])] = (p.get("name") or "", p.get("team") or "", p.get("status") or "")
        changes = []
        for pid, (name, team, st) in was.items():
            cur = (now_by_id.get(pid) or {}).get("status") or ""
            if _norm_status(cur) != _norm_status(st):
                changes.append({"player_id": pid, "name": name, "team": team, "was": st, "now": cur})
        if changes:
            total += len(changes)
            games.append({"game_id": gid, "home": g["home"]["tricode"], "away": g["away"]["tricode"],
                          "tip_off": g.get("tip_off"), "computed_at": sim.get("computed_at"),
                          "changes": changes})
    return {"checked_at": checked, "report_at": latest, "n_changed": total, "games": games}


def slate(date: str | None = None) -> dict:
    s = get_slate(date)
    games = []
    for g in s.get("games") or []:
        sim = cached_sim(str(g["game_id"]))
        item = {"game_id": str(g["game_id"]), "tip_off": g.get("tip_off"), "state": g.get("state"),
                "status": g.get("status"), "home": _team(g["home"]), "away": _team(g["away"]),
                "simulated": sim is not None}
        if sim:
            item["top"] = [{k: p[k] for k in ("name", "team", "p_dd", "p_td", "photo")} for p in sim["players"][:3]]
            item["n_picks"] = len(sim.get("picks") or [])
            item["computed_at"] = sim.get("computed_at")
        games.append(item)
    age = odds_age_s()
    from futbol import oddspapi as op
    from futbol import books as fbooks
    from altlines import odds_net
    net = odds_net.on()
    pub = fbooks.nba_public()
    if net:
        meta = {b["id"]: b for b in fbooks.all_books()}
        pub = [{"id": i, "name": meta[i].get("name") or i, "logo": meta[i].get("logo")} for i in _books() if i in meta]
    return {"date": s.get("date"), "requested_date": s.get("requested_date"),
            "auto_shifted": s.get("auto_shifted"), "games": games,
            "odds_age_s": None if age is None else round(age),
            "odds_today": odds_refreshes_today(), "odds_cap": ODDS_DAY_CAP,
            "usage": odds_net.usage() if net else op.usage(own=True),
            "books": pub,
            "odds_cost": sum(1 for g in games if g.get("state") == "pre") + 1}


def find_game(gid: str, date: str | None = None) -> dict | None:
    s = get_slate(date)
    return next((g for g in s.get("games") or [] if str(g["game_id"]) == str(gid)), None)
