"""
NBA — "Armate un live": la MISMA simulación del partido (altlines.dd_game), pero
jugada en vivo.

Con el partido empezado (ideal: al entretiempo) se toma el marcador, el reloj y
lo que lleva cada jugador (box score de ESPN, gratis) y se simula 20.000 veces
SÓLO lo que falta: las posesiones que quedan, los minutos que le quedan a cada
uno (faltas y paliza incluidas) y el reparto de puntos, rebotes y asistencias.
A cada simulación se le suma lo que ya pasó, y de ahí sale la distribución
final de cada estadística, el doble-doble y quién gana.

Lo que falta NO se juega con los números de antes del partido: se rearma con
todo lo que se ve en vivo.
  · Ritmo: las posesiones jugadas hasta ahora, llevadas a 48 minutos y mezcladas
    con el ritmo previo (pesa más cuanto más se jugó).
  · Cada equipo: % de tiro, pérdidas, tiros libres, rebote ofensivo, triples y
    asistencias de HOY, mezclados con lo previo según la muestra (el % de tiro
    es el que más vuelve a lo normal: una racha de tiro es casi toda suerte).
  · Cada jugador: los minutos de la rotación de HOY y cómo viene produciendo en
    cada estadística (achicado hacia su rol de siempre).
  · El mercado: la línea de puntos y el hándicap en vivo (Pinnacle si está). Los
    puntos que faltan de cada equipo se arriman a mitad de camino de esa línea.

Con las cuotas en vivo (OpticOdds, 1 pedido) arma lo mismo que antes del
partido, con las mismas reglas:
  · Destacados: jugadas con valor y lo más firme.
  · Parlay: la línea más probable de cada jugador que alguna casa paga cerca o
    por encima de la cuota justa.
La probabilidad se corre hacia el mercado igual que antes del partido
(props_live.BLEND_W) y la ventaja se calcula achicada (EDGE_SHRINK). Si el
modelo queda muy lejos del mercado la jugada es "dudosa" y no se recomienda.
"""

from __future__ import annotations

import math
import time

# Peso de lo previo en cada tasa de equipo, en la unidad de su muestra (tiros, posesiones…)
OBS_PRIOR = {"fg": 160.0, "tov_pp": 110.0, "fta_pp": 110.0, "ft": 60.0, "orb": 70.0, "tp_share": 60.0, "ast": 60.0}
PACE_PRIOR_MIN = 20.0     # minutos "virtuales" del ritmo previo
ROLE_PRIOR_MIN = 30.0     # minutos "virtuales" del rol de siempre de cada jugador
FAC_CLIP = (0.65, 1.5)
MKT_W = 0.5               # cuánto se arriman los puntos que faltan a la línea en vivo
EFF_CLIP = (0.82, 1.22)
MIN_LEFT = 3.0            # con menos minutos por jugar no se arma nada
MKT_MAX_GAP = 0.15        # modelo vs mercado más lejos que esto → dudosa
MIN_ODDS = 1.40
LIVE_ODDS_MAX_AGE_S = 8 * 60   # una cuota en vivo más vieja que esto no se usa
MAX_EDGE = 0.45
FIRM_MIN_P = 0.55
PARLAY_MIN_FAIR = 1.40
PARLAY_MIN_P = 0.38
PARLAY_NEAR = 0.93        # "cerca": la casa paga al menos el 93 % de la justa
PARLAY_MAX = 6
PLAY = ("betano", "betsson", "bet365.bet.ar")
DD_LABEL = {"dd": "Doble-doble", "td": "Triple-doble"}
PARTS = {"pts": ("pts",), "reb": ("reb",), "ast": ("ast",), "threes": ("threes",), "pr": ("pts", "reb"),
         "pa": ("pts", "ast"), "ra": ("reb", "ast"), "pra": ("pts", "reb", "ast")}


def _clock(d: dict) -> dict:
    st = ((d.get("header") or {}).get("competitions") or [{}])[0].get("status") or {}
    t = st.get("type") or {}
    period = int(st.get("period") or 0)
    clock = str(st.get("displayClock") or "0:00")
    try:
        if ":" in clock:
            mm, _, ss = clock.partition(":")
            left = float(mm) + float(ss or 0) / 60
        else:
            left = float(clock) / 60            # "53.8": segundos
    except ValueError:
        left = 0.0
    total = 48.0 + 5.0 * max(0, period - 4)
    el = 0.0 if period <= 0 else (period - 1) * 12 + (12 - left) if period <= 4 else total - left
    half = (t.get("name") or "") == "STATUS_HALFTIME"
    if half:
        el = 24.0
    if t.get("completed"):
        el = total
    return {"elapsed": el, "total": total, "state": t.get("state") or "", "period": period, "halftime": half,
            "detail": t.get("shortDetail") or t.get("detail") or ""}


def _box(gid: str) -> dict:
    from altlines import live
    d = live._get_json("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary",
                       params={"event": gid}).json()
    out = _clock(d)
    score = {}
    for c in ((d.get("header") or {}).get("competitions") or [{}])[0].get("competitors") or []:
        try:
            score[c.get("homeAway")] = int(c.get("score") or 0)
        except ValueError:
            score[c.get("homeAway")] = 0

    def num(x, i=0):
        try:
            return float(str(x).split("-")[i])
        except Exception:  # noqa: BLE001
            return 0.0
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0].get("competitors") or []
    side_of = {str((c.get("team") or {}).get("id")): c.get("homeAway") for c in comp}
    players, teams = {}, {}
    for team in (d.get("boxscore") or {}).get("players") or []:
        side = side_of.get(str((team.get("team") or {}).get("id")))
        tot = teams.setdefault(side, {k: 0.0 for k in ("fgm", "fga", "tpm", "ftm", "fta", "oreb", "dreb", "ast", "tov")})
        for blk in team.get("statistics") or []:
            labels = blk.get("labels") or []
            for a in blk.get("athletes") or []:
                v = dict(zip(labels, a.get("stats") or []))
                row = {"pts": num(v.get("PTS")), "reb": num(v.get("REB")), "ast": num(v.get("AST")),
                       "stl": num(v.get("STL")), "blk": num(v.get("BLK")), "tpm": num(v.get("3PT")),
                       "fgm": num(v.get("FG")), "fga": num(v.get("FG"), 1), "ftm": num(v.get("FT")),
                       "fta": num(v.get("FT"), 1), "oreb": num(v.get("OREB")), "dreb": num(v.get("DREB")),
                       "tov": num(v.get("TO")),
                       "min": num(v.get("MIN")), "pf": num(v.get("PF")), "dnp": bool(a.get("didNotPlay")),
                       "side": side}
                players[str((a.get("athlete") or {}).get("id"))] = row
                for k in tot:
                    tot[k] += row[k]
    out.update(score=score, players=players, teams=teams)
    return out


def _live_inputs(bx: dict, prof: dict, lists: dict) -> tuple[dict, dict]:
    """Lo que se ve en vivo, listo para el motor → (ritmo y tasas de equipo, resumen para mostrar)."""
    el = bx["elapsed"]
    T = bx.get("teams") or {}
    info = {"pace_pre": round(float(prof["home"]["pace"] + prof["away"]["pace"]) / 2, 1), "pace_now": None,
            "pace_used": None, "teams": {}}
    extra: dict = {"obs": {}}
    poss = {}
    for s in ("home", "away"):
        t = T.get(s)
        if not t or not t["fga"]:
            continue
        poss[s] = t["fga"] - t["oreb"] + t["tov"] + 0.44 * t["fta"]
    if len(poss) == 2 and el >= 3:
        pos = (poss["home"] + poss["away"]) / 2
        now = pos / el * 48
        w = el / (el + PACE_PRIOR_MIN)
        used = w * now + (1 - w) * info["pace_pre"]
        info.update(pace_now=round(now, 1), pace_used=round(used, 1))
        extra["pace"] = used
        for s, o in (("home", "away"), ("away", "home")):
            t, to = T[s], T[o]
            ps = max(poss[s], 1.0)
            raw = {"fg": (t["fgm"] / t["fga"], t["fga"]),
                   "tov_pp": (t["tov"] / ps, ps),
                   "fta_pp": (t["fta"] / ps, ps),
                   "ft": (t["ftm"] / t["fta"], t["fta"]) if t["fta"] else None,
                   "orb": (t["oreb"] / (t["oreb"] + to["dreb"]), t["oreb"] + to["dreb"]) if t["oreb"] + to["dreb"] else None,
                   "tp_share": (t["tpm"] / t["fgm"], t["fgm"]) if t["fgm"] else None,
                   "ast": (t["ast"] / t["fgm"], t["fgm"]) if t["fgm"] else None}
            extra["obs"][s] = {k: (v[0], v[1], OBS_PRIOR[k]) for k, v in raw.items() if v}
            info["teams"][s] = {"fg": round(t["fgm"] / t["fga"], 3), "tov": int(t["tov"]), "fta": int(t["fta"]),
                                "oreb": int(t["oreb"]), "tpm": int(t["tpm"]), "poss": round(poss[s], 1)}
    # Cada jugador: cómo viene hoy en cada estadística contra su rol de siempre (por minuto)
    for side in ("home", "away"):
        for pp in lists[side]:
            b = bx["players"].get(str(pp["id"]))
            if not b or b["min"] < 4:
                continue
            fac = {}
            for k in ("fgm", "tpm", "ftm", "oreb", "dreb", "ast", "stl", "blk"):
                r0 = float(pp["share"].get(k) or 0)
                if r0 <= 0:
                    continue
                post = (b[k] + r0 * ROLE_PRIOR_MIN) / (b["min"] + ROLE_PRIOR_MIN)
                fac[k] = min(max(post / r0, FAC_CLIP[0]), FAC_CLIP[1])
            b["fac"] = fac
    return extra, info


def _lg(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _simulate(game: dict, bx: dict, market: dict | None = None) -> tuple[dict, dict, dict]:
    """La simulación del partido, en vivo. → (resultado de dd_game, {id: (atleta, equipo, lado)}, datos en vivo usados)."""
    from altlines import dd_game, dd_live, live, nba_box
    T, P = nba_box.tables()
    lg = dd_game.league_means(T)
    inj = dd_live._injuries()
    cutoff = (game.get("tip_off") or "9999-12-31")[:10]
    prof, lists, meta = {}, {"home": [], "away": []}, {}
    for side in ("home", "away"):
        tr = game[side]["tricode"]
        prof[side] = dd_game.team_profile(T, tr, cutoff, lg)
        for a in live.fetch_roster(tr):
            aid = str(a["athlete_id"])
            b = bx["players"].get(aid)
            if b is None and bx["players"]:
                continue                                  # no está en la planilla de hoy
            if b is None and (inj.get(aid) or {}).get("status", "").lower() == "out":
                continue
            if not dd_live._in_rotation(P, T, aid, tr) and not (b and b["min"] > 0):
                continue
            pp = dd_game.player_profile(P, T, aid, cutoff)
            if pp is None:
                continue
            lists[side].append(pp)
            meta[aid] = (a, tr, side)
    left = max(0.0, bx["total"] - bx["elapsed"])
    extra, info = _live_inputs(bx, prof, lists)
    lv = {"frac": left / 48.0, "left_min": left, "elapsed": bx["elapsed"], "score": bx["score"],
          "players": bx["players"], **extra}
    seed = (int(game["game_id"]) + int(bx["elapsed"] * 10)) % 1_000_000

    def run(n, dist):
        return dd_game.simulate_game(prof["home"], prof["away"], lists["home"], lists["away"], lg,
                                     n=n, seed=seed, dist=dist, live=lv)
    # El mercado en vivo: los puntos que le faltan a cada equipo se arriman a la línea.
    info["market"] = None
    if market and market.get("total") is not None and market.get("home_margin") is not None:
        pre = run(3000, False)
        cur = bx["score"]
        info["model_total"] = round(pre["teams"]["home"]["pts"] + pre["teams"]["away"]["pts"], 1)
        info["model_margin"] = round(pre["teams"]["home"]["pts"] - pre["teams"]["away"]["pts"], 1)
        tgt = {"home": (market["total"] + market["home_margin"]) / 2, "away": (market["total"] - market["home_margin"]) / 2}
        eff = {}
        for s in ("home", "away"):
            mod_rem = pre["teams"][s]["pts"] - cur.get(s, 0)
            mkt_rem = tgt[s] - cur.get(s, 0)
            if mod_rem > 3 and mkt_rem > 0:
                want = (1 - MKT_W) * mod_rem + MKT_W * mkt_rem
                eff[s] = min(max(want / mod_rem, EFF_CLIP[0]), EFF_CLIP[1])
        if eff:
            lv["eff"] = eff
        info["market"] = {**market, "eff": {k: round(v, 3) for k, v in eff.items()}}
    r = run(dd_game.N_SIM, True)
    return r, meta, info


def _game_cands(fin: dict | None, rows: list, out: dict, cand) -> list:
    """Línea de puntos, hándicap, ganador y puntos por equipo: la probabilidad sale del
    marcador final de las 20.000 simulaciones."""
    if not fin or not rows:
        return []
    import numpy as np
    from altlines import props_live as pl
    home, away = out["home"], out["away"]
    sc = out["score"]
    total = fin["home"] + fin["away"]
    tri = {"home": home["tricode"], "away": away["tricode"]}

    def prob(mk, side, line):
        if mk == "total":
            p = float((total > line).mean())
            return p if side == "over" else 1 - p - float((total == line).mean())
        if mk in ("tt_home", "tt_away"):
            x = fin["home" if mk == "tt_home" else "away"]
            p = float((x > line).mean())
            return p if side == "over" else 1 - p - float((x == line).mean())
        m = fin["margin"] if side == "home" else -fin["margin"]
        return float((m + (line or 0) > 0).mean())

    # los dos lados de cada mercado, por casa (para el mercado sin margen)
    def pair_key(r):
        if r["mk"] == "spread":
            return ("spread", r["line"] if r["side"] == "home" else -r["line"])
        return (r["mk"], r["line"])
    first = {"total": "over", "tt_home": "over", "tt_away": "over", "spread": "home", "ml": "home"}
    table: dict[tuple, dict] = {}
    for r in rows:
        table.setdefault(pair_key(r), {}).setdefault(r["side"], {})[r["book"]] = r["odds"]
    res = []
    for (mk, kline), sides in table.items():
        a_side = first[mk]
        b_side = next((s for s in sides if s != a_side), None)
        A, B = sides.get(a_side) or {}, sides.get(b_side) or {}
        nv = lambda b: (1 / A[b]) / (1 / A[b] + 1 / B[b])  # noqa: E731
        both = [nv(b) for b in A if b in B and b != pl.REF_BOOK]
        mkt_a = nv(pl.REF_BOOK) if pl.REF_BOOK in A and pl.REF_BOOK in B else (sum(both) / len(both) if both else None)
        for side, books_all in sides.items():
            books = {b: v for b, v in books_all.items() if b in PLAY}
            if not books:
                continue
            line = kline if mk != "spread" or side == "home" else -kline
            po = prob(mk, side, line)
            if po >= 0.985 or po <= 0.015:
                continue
            m_side = None if mkt_a is None else (mkt_a if side == a_side else 1 - mkt_a)
            if mk == "total":
                who, team, label = f'{tri["away"]} @ {tri["home"]}', None, "Puntos del partido"
                txt = f'{"Más de" if side == "over" else "Menos de"} {line:g}'.replace(".", ",")
                now, mean, tag = sc.get("home", 0) + sc.get("away", 0), float(total.mean()), "TOTAL"
            elif mk in ("tt_home", "tt_away"):
                s_ = "home" if mk == "tt_home" else "away"
                who, team, label = (home if s_ == "home" else away)["name"], tri[s_], "Puntos del equipo"
                txt = f'{"Más de" if side == "over" else "Menos de"} {line:g}'.replace(".", ",")
                now, mean, tag = sc.get(s_, 0), float(fin[s_].mean()), "PTS EQ"
            elif mk == "spread":
                who, team, label = (home if side == "home" else away)["name"], tri[side], "Hándicap"
                txt = f'{tri[side]} {"+" if line > 0 else "−"}{abs(line):g}'.replace(".", ",")
                now, mean, tag = None, None, "HÁND"
            else:
                who, team, label = (home if side == "home" else away)["name"], tri[side], "Ganador"
                txt = f'Gana {tri[side]}'
                now, mean, tag = None, None, "GANA"
            pr = {"name": who, "player_id": f"game:{mk}", "team": team, "photo": None}
            c = cand(pr, mk, label, side, line, now, mean, po, m_side, books)
            c.update(game=True, line_txt=txt, tag=tag, teams=[tri["away"], tri["home"]])
            res.append(c)
    return res


def _mean(cc: list[float]) -> float:
    return float(sum(cc[1:]))                 # E[X] = Σ P(X >= k), k ≥ 1


def build(gid: str) -> dict:
    from altlines import dd_live, odds_optic, props_live as pl, props_dist
    game = dd_live.find_game(gid)
    if not game:
        raise KeyError(gid)
    bx = _box(gid)
    state, left = bx["state"], max(0.0, bx["total"] - bx["elapsed"])
    margin = abs(bx["score"].get("home", 0) - bx["score"].get("away", 0))
    out = {"game_id": gid, "state": state, "detail": bx["detail"], "elapsed": round(bx["elapsed"], 1),
           "left_min": round(left, 1), "halftime": bx["halftime"], "period": bx["period"],
           "score": bx["score"], "margin": margin, "home": dd_live._team(game["home"]),
           "away": dd_live._team(game["away"]), "at": time.time(), "sim": None, "live": None, "players": [],
           "highlights": [], "parlay": [], "dubious": [], "odds_books": [], "errors": []}
    if state == "pre":
        out["errors"].append("El partido todavía no empezó: el live se arma con el partido en juego (ideal, al entretiempo).")
        return out
    if state != "in":
        out["errors"].append("El partido ya terminó.")
        return out
    if left < MIN_LEFT:
        out["errors"].append("Queda muy poco partido: ya no se arma una apuesta en vivo.")
        return out

    # Línea de puntos y hándicap en vivo (1 pedido): entran a la simulación
    fx, market, gm = None, None, None
    try:
        fx = odds_optic.fixture_for(game) or (odds_optic.fixtures(refresh=True) and odds_optic.fixture_for(game))
        gm = odds_optic.game_odds(fx, max_age_s=LIVE_ODDS_MAX_AGE_S) if fx else None
        market = gm and gm["lines"]
    except Exception as exc:  # noqa: BLE001 - sin mercado la simulación corre igual
        out["errors"].append(f"Línea en vivo: {exc}")
    r, meta, info = _simulate(game, bx, market)
    out["live"] = info
    out["sim"] = {"p_home": round(r["p_home"], 3), "margin": round(r["margin"], 1), "p_ot": round(r["p_ot"], 3),
                  "home_pts": round(r["teams"]["home"]["pts"], 1), "away_pts": round(r["teams"]["away"]["pts"], 1),
                  "n": 20000}
    proj: dict[str, dict] = {}
    for pr in r["players"]:
        a, tr, side = meta[pr["id"]]
        b = bx["players"].get(str(pr["id"])) or {}
        dist = pr.get("dist") or {}
        row = {"player_id": pr["id"], "name": a["name"], "team": tr, "side": side,
               "photo": dd_live.HEADSHOT.format(aid=pr["id"]),
               "min": b.get("min", 0.0), "min_left": round(pr.get("min_left", 0.0), 1), "pf": b.get("pf", 0.0),
               "now": {"pts": b.get("pts", 0.0), "reb": b.get("reb", 0.0), "ast": b.get("ast", 0.0),
                       "threes": b.get("tpm", 0.0)},
               "proj": {"pts": round(pr["mean"]["pts"], 1), "reb": round(pr["mean"]["reb"], 1),
                        "ast": round(pr["mean"]["ast"], 1),
                        "threes": round(_mean(dist["threes"]), 1) if dist.get("threes") else None},
               "p_dd": round(pr["p_dd"], 3), "p_td": round(pr["p_td"], 3)}
        proj[pl._pkey(a["name"])] = {**row, "dist": dist}
        if row["min"] > 0 or row["min_left"] >= 4:
            out["players"].append(row)
    out["players"].sort(key=lambda x: -(x["proj"]["pts"] or 0))

    # ── Cuotas en vivo ──
    try:
        g = odds_optic.fetch_game(fx["id"], max_age_s=LIVE_ODDS_MAX_AGE_S) if fx else None
    except Exception as exc:  # noqa: BLE001
        g = None
        out["errors"].append(f"OpticOdds: {exc}")
    g = g or {"props": [], "dd": [], "dd_no": []}
    grows = (gm or {}).get("rows") or []
    if not (g["props"] or g["dd"] or grows):
        out["errors"].append("Las casas no tienen cuotas en vivo para este partido ahora. Abajo está la simulación.")
        return out
    if not (g["props"] or g["dd"]):
        out["errors"].append("Ahora las casas no publican líneas de jugador en vivo para este partido: el armado usa los mercados del partido (puntos, hándicap, ganador).")
    out["odds_books"] = sorted({r_["book"] for r_ in g["props"] if r_["book"] != pl.REF_BOOK} | {d[0] for d in g["dd"]}
                               | {r_["book"] for r_ in grows if r_["book"] != pl.REF_BOOK})

    def cand(pr, mk, label, side, line, now, mean, po, p_mkt, books):
        """Una jugada posible: probabilidad corrida hacia el mercado y ventaja achicada, como antes del partido."""
        best = max(books, key=books.get)
        price = books[best]
        pa = po if p_mkt is None else 1 / (1 + math.exp(-(_lg(po) + (1 - pl.BLEND_W) * (_lg(p_mkt) - _lg(po)))))
        pc = max(0.0, pa - pl.EDGE_SHRINK * math.sqrt(pa * (1 - pa)))
        return {"player": pr["name"], "player_id": pr["player_id"], "team": pr["team"], "photo": pr["photo"],
                "mk": mk, "label": label, "side": side, "line": line, "now": now,
                "proj": None if mean is None else round(mean, 1),
                "p": round(pa, 3), "p_model": round(po, 3), "p_mkt": None if p_mkt is None else round(p_mkt, 3),
                "fair": round(1 / pa, 2) if pa > 0 else None, "book": best, "price": price,
                "books": dict(sorted(books.items(), key=lambda kv: -kv[1])),
                "edge": round(pc * price - 1, 3), "ratio": round(price * pa - 1, 3),
                # sin los dos lados no hay mercado contra el que comparar: no se recomienda
                "dubious": p_mkt is None or abs(po - p_mkt) > MKT_MAX_GAP}

    table: dict[tuple, dict] = {}
    for r_ in g["props"]:
        table.setdefault((r_["player"], r_["mk"], r_["line"]), {"over": {}, "under": {}})[r_["side"]][r_["book"]] = r_["odds"]
    cands = []
    for (pk, mk, ln), sides in table.items():
        pr = proj.get(pk)
        cc = pr and pr["dist"].get(mk)
        if not cc or mk not in PARTS:
            continue
        po = props_dist.p_over(cc, ln)
        if po >= 0.985 or po <= 0.015:
            continue                               # ya está definida: no es una apuesta
        now = sum(pr["now"][s] or 0 for s in PARTS[mk])
        pin_o, pin_u = sides["over"].get(pl.REF_BOOK), sides["under"].get(pl.REF_BOOK)
        both = [(1 / sides["over"][b]) / (1 / sides["over"][b] + 1 / sides["under"][b])
                for b in sides["over"] if b in sides["under"] and b != pl.REF_BOOK]
        mkt_o = ((1 / pin_o) / (1 / pin_o + 1 / pin_u)) if pin_o and pin_u else (sum(both) / len(both) if both else None)
        for side in ("over", "under"):
            books = {b: v for b, v in sides[side].items() if b in PLAY}
            if not books:
                continue
            p_side = po if side == "over" else 1 - po
            m_side = None if mkt_o is None else (mkt_o if side == "over" else 1 - mkt_o)
            cands.append(cand(pr, mk, props_dist.LABEL.get(mk, mk), side, ln, now, _mean(cc), p_side, m_side, books))
    # Doble-doble / triple-doble en vivo (sólo el "Sí")
    ddb: dict[tuple, dict] = {}
    for book, mk, name, price in g["dd"]:
        if book in PLAY:
            ddb.setdefault((pl._pkey(name), mk), {})[book] = price
    ddn = {(pl._pkey(name), mk, book): price for book, mk, name, price in g.get("dd_no") or []}
    for (pk, mk), books in ddb.items():
        pr = proj.get(pk)
        if not pr:
            continue
        po = pr["p_" + mk]
        nv = [(1 / y) / (1 / y + 1 / ddn[(pk, mk, b)]) for b, y in books.items() if (pk, mk, b) in ddn]
        if 0.015 < po < 0.985:
            cands.append(cand(pr, mk, DD_LABEL[mk], "yes", None, None, None, po, sum(nv) / len(nv) if nv else None, books))

    cands += _game_cands(r.get("_final"), grows, out, cand)
    clean = [c for c in cands if not c["dubious"] and c["price"] >= MIN_ODDS]
    # ── Destacados: hasta 3 con valor y 2 "lo más firme", un renglón por jugador ──
    seen, hl = set(), []
    for c in sorted((c for c in clean if pl.PICK_EDGE <= c["edge"] <= MAX_EDGE and c["p"] >= pl.PICK_MIN_P),
                    key=lambda c: -c["edge"]):
        if c["player_id"] not in seen and len(hl) < 3:
            seen.add(c["player_id"])
            hl.append({**c, "kind": "pick"})
    n_firm = 0
    for c in sorted((c for c in clean if c["p"] >= FIRM_MIN_P), key=lambda c: (-c["p"], -c["price"])):
        if c["player_id"] not in seen and n_firm < 2:
            seen.add(c["player_id"])
            n_firm += 1
            hl.append({**c, "kind": "sure"})
    out["highlights"] = hl
    # ── Parlay: la línea más probable de cada jugador que se paga cerca o encima de la justa ──
    best: dict[str, dict] = {}
    for c in clean:
        if not c["fair"] or c["fair"] < PARLAY_MIN_FAIR or c["p"] < PARLAY_MIN_P or c["price"] < c["fair"] * PARLAY_NEAR:
            continue
        b = best.get(c["player_id"])
        if not b or c["p"] > b["p"]:
            best[c["player_id"]] = c
    out["parlay"] = sorted(best.values(), key=lambda c: -c["p"])[:PARLAY_MAX]
    # ── Dudosas: el modelo ve valor pero queda lejos del mercado ──
    dseen = set()
    for c in sorted((c for c in cands if c["dubious"] and c["p_mkt"] is not None and c["price"] >= MIN_ODDS
                     and c["edge"] >= pl.PICK_EDGE),
                    key=lambda c: -c["edge"]):
        if c["player_id"] not in dseen and len(out["dubious"]) < 3:
            dseen.add(c["player_id"])
            out["dubious"].append(c)
    return out
