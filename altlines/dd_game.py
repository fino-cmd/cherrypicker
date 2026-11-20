"""
NBA — simulación del PARTIDO completo para doble-doble y triple-doble.

Cada una de las N simulaciones juega el partido entero, a nivel equipo, y
después reparte lo que hizo cada equipo entre sus jugadores:

  1. ritmo:     posesiones del partido (las comparten los dos equipos)
  2. tiro:      tiros de campo, % de acierto (su ataque × la defensa rival),
                triples y libres de cada equipo → puntos y marcador
  3. rebotes:   los tiros errados de los DOS equipos son los rebotes en juego;
                cada equipo toma un % ofensivo según su ataque y la defensa
                rival. El pivote de un equipo depende de cuánto erra el otro.
  4. asistencias: una parte de los dobles/triples convertidos
  5. robos y tapas: según las posesiones y los tiros del rival
  6. minutos:   los de cada jugador (de su historia), ajustados a 240 por
                equipo (si falta uno, los demás juegan más); si el partido
                se define por mucho, los titulares descansan; empate → alargue
  7. reparto:   lo del equipo se reparte entre los jugadores según sus minutos
                y su rol (qué parte de los tiros, rebotes y asistencias del
                equipo toma por minuto), con un ruido de rol por partido.

Con eso cada jugador tiene su línea en cada simulación, y se cuenta en cuántas
llega a 10+ en dos (DD) o tres (TD) estadísticas. Además sale el marcador
simulado, el ritmo y la probabilidad de ganar.

Backtest 2025-26 sin mirar el futuro (altlines/tools/dd_game_backtest.py,
2.656 jugadores-partido de 20+ min): DD media 11,2 % vs 11,4 % real, Brier
0,0766 (modelo jugador-solo: 0,0770). Minutos y puntos de las figuras, exactos.
El TD queda bajo (0,59 % vs 0,90 %, sólo 24 casos en la muestra).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from altlines import props_dist

N_SIM = 20_000
HALFLIFE_T = 12          # partidos, para el estilo de cada equipo
HALFLIFE_P = 15          # partidos, para el rol de cada jugador
PREV_W = 0.5             # temporada pasada vale la mitad (rol de jugador)
TEAM_PREV_W = 0.5        # y para el estilo/fuerza del equipo (calibrado aparte)
PRIOR_GAMES_T = 6        # partidos "virtuales" de liga en cada tasa de equipo…
PRIOR_EARLY = 60         # …más estos al arrancar la temporada, que se van con
EARLY_GAMES = 20         # los primeros EARLY_GAMES partidos (los planteles cambian)
PRIOR_MIN_P = 40         # minutos "virtuales" de rol promedio (achique suave)
PACE_SD = 4.0            # desvío del ritmo de un partido (posesiones)
FG_CONC = 900            # concentración Beta del % de campo de una noche
# Concentración del reparto de cada estadística (más alto = rol más estable de
# un partido a otro). Calibrado con el backtest.
ROLE_CONC = {"fgm": 60.0, "tpm": 25.0, "ftm": 20.0, "oreb": 25.0, "dreb": 50.0, "ast": 35.0, "stl": 25.0, "blk": 15.0}
ROLE_SCALE = 4.0
ROLE_MODE = "rate"
# "Noche" de cada jugador: un mismo factor sube o baja TODAS sus estadísticas
# (las grandes noches vienen juntas). Es lo que hace aparecer los triple-doble.
NIGHT_SD = 0.0
BLOWOUT_FROM = 7.5       # puntos de diferencia desde los que descansan titulares
BLOWOUT_SLOPE = 0.0068   # minutos que pierden por punto de diferencia (fracción)
BLOWOUT_MAX = 0.20
TEAM_MIN = 240.0
# No todo tiro errado termina en el rebote de un jugador (pelota afuera,
# rebote "de equipo"): en 2025-26 fue el 92,9 % (43,7 rebotes por 47,1 errados).
REB_CREDIT = 0.929
MARGIN_SD_RATIO = 15.1 / 17.1
# Ajuste a 240: con potencia > 1 el recorte (o el sobrante) cae más sobre los de
# pocos minutos. Los suplentes juegan más justo cuando falta alguien, así que su
# historia sobreestima sus minutos con el plantel completo.
MIN_GAMMA = 1.3
MIN_CAP = 44.0
BENCH_MIN = 20.0
STATS = ("pts", "reb", "ast", "stl", "blk")
SHARE_KEYS = ("fgm", "tpm", "ftm", "oreb", "dreb", "ast", "stl", "blk")


def target_season(before: str) -> int:
    """Temporada del partido que se simula (ESPN la nombra por el año en que
    termina). `before` = "9999-…" significa "hoy"."""
    from datetime import date, datetime, timezone
    d = datetime.now(timezone.utc).date() if before >= "9000" else date.fromisoformat(before[:10])
    return d.year + 1 if d.month >= 8 else d.year


def _w(n: int, hl: float) -> np.ndarray:
    return 0.5 ** (np.arange(n)[::-1] / hl)


def league_means(T: pd.DataFrame) -> dict:
    t = T.tail(600)
    s = t[["poss", "fga", "fgm", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pts"]].sum()
    n = len(t)
    return {
        "pace": s["poss"] / n,
        "fga_pp": s["fga"] / s["poss"], "fg": s["fgm"] / s["fga"], "tp_share": s["tpm"] / s["fgm"],
        "ftm_pp": s["ftm"] / s["poss"], "orb": s["oreb"] / (s["oreb"] + s["dreb"]),
        "ast": s["ast"] / s["fgm"], "stl_pp": s["stl"] / s["poss"], "blk_pf": s["blk"] / s["fga"],
        "tov_pp": s["tov"] / s["poss"], "fta_pp": s["fta"] / s["poss"], "ft": s["ftm"] / s["fta"],
    }


_RATES = {   # nombre: (numerador, denominador) sobre la fila de un equipo con las del rival en *_o
    "fga_pp": ("fga", "poss"), "fg": ("fgm", "fga"), "tp_share": ("tpm", "fgm"), "ftm_pp": ("ftm", "poss"),
    "orb": ("oreb", "orb_den"), "ast": ("ast", "fgm"), "stl_pp": ("stl", "poss_o"), "blk_pf": ("blk", "fga_o"),
    "tov_pp": ("tov", "poss"), "fta_pp": ("fta", "poss"), "ft": ("ftm", "fta"),
}
_TT: dict = {"id": None, "df": None}


def paired(T: pd.DataFrame) -> pd.DataFrame:
    """Cada fila de equipo con los números de su rival al lado (sufijo _o)."""
    if _TT["id"] is id(T):
        return _TT["df"]
    o = T[["game", "team", "poss", "fga", "dreb"]].rename(columns={"team": "opp", "poss": "poss_o", "fga": "fga_o", "dreb": "dreb_o"})
    df = T.merge(o, on=["game", "opp"], how="left")
    df["orb_den"] = df["oreb"] + df["dreb_o"]
    _TT.update(id=id(T), df=df)
    return df


def team_profile(T: pd.DataFrame, team: str, before: str, lg: dict) -> dict:
    """Ataque (lo que hace) y defensa (lo que le hacen) de un equipo, con sus
    partidos ANTERIORES a `before`, achicado hacia la liga."""
    D = paired(T)
    prof = {}
    out = {}
    for role, rows in (("off", D[(D["team"] == team) & (D["date"] < before)].tail(40)),
                       ("def", D[(D["opp"] == team) & (D["date"] < before)].tail(40))):
        if rows.empty:
            out[role] = {k: lg[k] for k in _RATES} | {"pace": lg["pace"]}
            continue
        season = target_season(before)
        w = _w(len(rows), HALFLIFE_T) * np.where(rows["season"].to_numpy() < season, TEAM_PREV_W, 1.0)
        n_cur = int((rows["season"] == season).sum())
        prior_g = PRIOR_GAMES_T + PRIOR_EARLY * max(0.0, 1 - n_cur / EARLY_GAMES)
        k = prior_g * w.mean()
        r = {}
        for name, (num, den) in _RATES.items():
            a = float(np.nansum(w * rows[num].to_numpy(float)))
            b = float(np.nansum(w * rows[den].to_numpy(float)))
            per = b / max(w.sum(), 1e-9)
            r[name] = (a + k * per * lg[name] / max(w.mean(), 1e-9) * w.mean()) / (b + k * per) if b > 0 else lg[name]
        r["pace"] = (float((w * rows["poss"]).sum()) + k * lg["pace"]) / (w.sum() + k)
        out[role] = r
    for name in _RATES:
        prof[name] = (out["off"][name], out["def"][name])
    prof["pace"] = (out["off"]["pace"] + out["def"]["pace"]) / 2
    return prof


def _mix(off: float, deff: float, lg: float) -> float:
    """Ataque de uno contra la defensa del otro (aditivo sobre la liga)."""
    return off + deff - lg


def player_profile(P: pd.DataFrame, T: pd.DataFrame, pid: str, before: str) -> dict | None:
    """Minutos y rol (parte de cada estadística del equipo por minuto en cancha)."""
    g = P[(P["id"] == pid) & (P["date"] < before)].tail(40)
    if len(g) < 3:
        return None
    season = target_season(before)
    team = g["team"].iloc[-1]
    w = _w(len(g), HALFLIFE_P) * np.where(g["season"].to_numpy() < season, PREV_W, 1.0)
    w = w * np.where(g["team"].to_numpy() != team, PREV_W, 1.0)
    tt = T.set_index(["game", "team"])
    tot = tt.reindex(list(zip(g["game"], g["team"])))
    on = g["min"].to_numpy() / TEAM_MIN * 5                    # fracción del partido en cancha (×5 jugadores)
    shares = {}
    for k in SHARE_KEYS:
        team_tot = tot[k].to_numpy(float)
        exp = (w * team_tot * on / 5).sum()                   # lo que le tocaría con un rol "promedio"
        got = (w * g[k].to_numpy(float)).sum()
        prior_exp = PRIOR_MIN_P / TEAM_MIN * np.nanmean(team_tot) if np.isfinite(team_tot).any() else 0
        if ROLE_MODE == "rate":       # producción por minuto (sin mirar al equipo)
            shares[k] = float(got / max((w * g["min"].to_numpy(float)).sum(), 1e-9))
        else:                         # parte del total del equipo por minuto en cancha
            shares[k] = float((got + prior_exp * 1.0) / (exp + prior_exp)) if exp + prior_exp > 0 else 1.0
    last = g.tail(15)
    wl = w[-len(last):]
    min_v, min_w = last["min"].to_numpy(float), wl / wl.sum()
    # Suplente (mediana < BENCH_MIN): las noches que no entró también cuentan
    # (DNP del entrenador). A los titulares no: si faltaron fue por lesión, y
    # eso lo dice el reporte de lesionados.
    if np.median(min_v) < BENCH_MIN:
        tg = T[(T["team"] == team) & (T["date"] < before)].tail(10)
        first = g[g["team"] == team]["date"].min()
        miss = int(((tg["date"] >= first) & ~tg["game"].isin(g["game"])).sum())
        if miss:
            min_v = np.concatenate([min_v, np.zeros(miss)])
            min_w = np.concatenate([min_w * len(last), np.full(miss, float(np.mean(min_w * len(last))))])
            min_w = min_w / min_w.sum()
    return {"id": pid, "name": g["name"].iloc[-1], "team": team, "games": len(g),
            "min_v": min_v, "min_w": min_w,
            "starter": float((wl * last["starter"].astype(float).to_numpy()).sum() / wl.sum()),
            "share": shares}


def _alloc(rng, total: np.ndarray, weights: np.ndarray, conc: float) -> np.ndarray:
    """Reparte `total` (N,) entre jugadores: parte esperada ∝ weights (N, m),
    con variación de rol por partido (Dirichlet de concentración `conc`, que
    respeta exactamente la parte esperada de cada uno)."""
    p = weights / np.maximum(weights.sum(1, keepdims=True), 1e-12)
    g = rng.gamma(np.maximum(conc * p, 1e-6))
    q = g / np.maximum(g.sum(1, keepdims=True), 1e-12)
    return rng.multinomial(np.maximum(total, 0).astype(np.int64), q)


def simulate_game(home: dict, away: dict, home_players: list[dict], away_players: list[dict],
                  lg: dict, n: int = N_SIM, seed: int = 0, home_adv: float = 1.5,
                  dist: bool = False, live: dict | None = None) -> dict:
    """Juega `n` veces el partido. Devuelve equipo y jugadores.

    `live` (partido en juego): se juega sólo lo que FALTA y se suma a lo que ya pasó.
      {"frac": parte del partido que queda (minutos que faltan / 48),
       "left_min": minutos que faltan, "score": {"home": pts, "away": pts},
       "players": {id: {"min", "pts", "reb", "ast", "stl", "blk", "tpm", "pf", "dnp",
                        "fac": {estadística: cuánto más/menos viene produciendo hoy}}},
       "elapsed": minutos jugados (para el reparto de minutos de HOY),
       "pace": ritmo a usar en lo que falta (el de hoy mezclado con el previo),
       "obs": {lado: {tasa: (valor de hoy, muestra, peso del previo)}} → cada tasa del
              equipo (tiro, pérdidas, tiros libres, rebote ofensivo…) se mezcla con la de hoy,
       "eff": {lado: multiplicador de puntos} para arrimar el total a la línea en vivo}
    Sin `live` no cambia nada: es la simulación de antes del partido."""
    rng = np.random.default_rng(seed)
    f = float(live["frac"]) if live else 1.0
    base = int(live["score"].get("home", 0) - live["score"].get("away", 0)) if live else 0
    ot_p = min(1.0, 0.104 / max(f, 1e-6)) if live else 0.104      # el alargue dura lo mismo, quede lo que quede
    pace_mu = (home["pace"] + away["pace"]) / 2 * (lg["pace"] / lg["pace"])
    if live and live.get("pace"):
        pace_mu = float(live["pace"])
    pace = rng.normal(pace_mu, PACE_SD, n).clip(80, 125)
    # Posesión por posesión (a nivel equipo): pérdidas, viajes a la línea y
    # tiros; cada tiro errado puede volver como rebote ofensivo y otro tiro.
    # Así el % de acierto, los tiros y los rebotes quedan atados, como en la
    # realidad (un equipo que erra mucho toma más rebotes y tira más veces).
    team = {s_: {"dreb": np.zeros(n, np.int64)} for s_ in ("home", "away")}
    for side, me, op, other in (("home", home, away, "away"), ("away", away, home, "home")):
        obs = ((live or {}).get("obs") or {}).get(side) or {}
        eff = float(((live or {}).get("eff") or {}).get(side) or 1.0)

        def mx(k, me=me, op=op, obs=obs):
            v = _mix(me[k][0], op[k][1], lg[k])
            o = obs.get(k)                       # en vivo: la tasa de hoy, según cuánta muestra hay
            return v if not o else (v * o[2] + o[0] * o[1]) / (o[2] + o[1])
        poss = np.rint(pace * f).astype(np.int64)
        tov = rng.binomial(poss, np.clip(mx("tov_pp"), 0.05, 0.25))
        fta = rng.poisson(np.maximum(pace * f * mx("fta_pp") * eff, 0.1))
        ftm = rng.binomial(fta, np.clip(mx("ft"), 0.5, 0.95))
        att = np.maximum(poss - tov - np.rint(0.44 * fta).astype(np.int64), 0)
        fg = (mx("fg") + (0.006 if side == "home" else -0.006) * home_adv / 1.5) * eff
        p_fg = rng.beta(fg * FG_CONC, (1 - fg) * FG_CONC, n)
        orb = np.clip(mx("orb"), 0.1, 0.45)
        fga = np.zeros(n, np.int64); fgm = np.zeros(n, np.int64); oreb = np.zeros(n, np.int64)
        for _ in range(4):                       # tiro, rebote, segundo tiro…
            made = rng.binomial(att, p_fg)
            avail = rng.binomial(att - made, REB_CREDIT)
            o = rng.binomial(avail, orb)
            fga += att; fgm += made; oreb += o
            team[other]["dreb"] = team[other]["dreb"] + (avail - o)
            att = o
        tpm = rng.binomial(fgm, np.clip(mx("tp_share"), 0.05, 0.7))
        team[side].update({"fga": fga, "fgm": fgm, "tpm": tpm, "ftm": ftm, "oreb": oreb,
                           "pts": 2 * fgm + tpm + ftm,
                           "ast": rng.binomial(fgm, np.clip(mx("ast"), 0.3, 0.9))})
    for side, me, op, other in (("home", home, away, "away"), ("away", away, home, "home")):
        stl = rng.poisson(np.maximum(pace * f * _mix(me["stl_pp"][0], op["stl_pp"][1], lg["stl_pp"]), 0.1))
        blk = rng.binomial(team[other]["fga"], np.clip(_mix(me["blk_pf"][0], op["blk_pf"][1], lg["blk_pf"]), 0.005, 0.2))
        team[side]["stl"], team[side]["blk"] = stl, blk
    margin = team["home"]["pts"] - team["away"]["pts"] + base
    # Alargue: un empate se juega 5 minutos más (≈ +10 % de todo)
    ot = margin == 0
    if ot.any():
        for side in team:
            for k in ("fgm", "tpm", "ftm", "ast", "oreb", "dreb", "stl", "blk"):
                team[side][k] = team[side][k] + rng.binomial(team[side][k], ot_p) * ot
            team[side]["pts"] = 2 * team[side]["fgm"] + team[side]["tpm"] + team[side]["ftm"]
        margin = team["home"]["pts"] - team["away"]["pts"] + base
        margin = np.where(margin == 0, rng.choice([-1, 1], n), margin)
    # En la realidad los dos marcadores van más juntos (ritmo, árbitros,
    # cansancio): la diferencia final se aleja de lo esperado 15,1 puntos,
    # y acá 17,1. Se calibra a eso para quién gana, palizas y minutos.
    margin = margin.mean() + (margin - margin.mean()) * MARGIN_SD_RATIO
    out = {"teams": {}, "players": []}
    for side, plist in (("home", home_players), ("away", away_players)):
        t = team[side]
        out["teams"][side] = {"pts": float(t["pts"].mean()) + (live["score"].get(side, 0) if live else 0), "pts_sd": float(t["pts"].std()),
                              "reb": float((t["oreb"] + t["dreb"]).mean()), "ast": float(t["ast"].mean()),
                              "fg": float((t["fgm"] / np.maximum(t["fga"], 1)).mean())}
        if not plist:
            continue
        m = len(plist)
        # minutos: remuestreo de su historia
        M = np.stack([rng.choice(p["min_v"], size=n, p=p["min_w"]) + rng.normal(0, 1.5, n) for p in plist], 1).clip(0, 48)
        mine = margin if side == "home" else -margin
        # Titulares: 31,2 min en partidos parejos → 28,9 (15-20 pts) → 25,9 (30+), 2025-26
        blow = np.clip(BLOWOUT_SLOPE * (np.abs(mine) - BLOWOUT_FROM), 0, BLOWOUT_MAX)
        st = np.array([p["starter"] for p in plist])
        cur = None
        if live:
            # Lo que le queda a cada uno: mitad "sus minutos de siempre × lo que falta",
            # mitad "sus minutos de siempre menos los que ya jugó". Las faltas y la
            # paliza le sacan minutos; el que no entró en todo el partido casi no cuenta.
            lp = [live["players"].get(str(p["id"])) or {} for p in plist]
            now = np.array([float(x.get("min") or 0) for x in lp])
            left = float(live.get("left_min") or 48 * f)
            full = M
            M = 0.5 * full * f + 0.5 * np.clip(full - now[None, :], 0, None)
            el = float(live.get("elapsed") or 0)
            if el >= 6:                          # la rotación de HOY pesa más cuanto más se jugó
                w_t = min(0.7, el / 40)
                M = (1 - w_t) * M + w_t * (now / el * left)[None, :]
            # ESPN marca "no jugó" a todo suplente hasta que entra: recién pesa pasada la mitad.
            mult = np.array([0.0 if (x.get("pf") or 0) >= 6 else
                             0.6 if (x.get("pf") or 0) >= 5 else
                             (0.1 if f < 0.3 else 0.3) if f < 0.5 and not x.get("min") else 1.0 for x in lp])
            M = M * mult[None, :]
            M = np.clip(M - blow[:, None] * st[None, :] * full * min(1.0, left / 24), 0, None)
            tot_min = TEAM_MIN * f + TEAM_MIN * 0.104 * ot
            G = np.power(M, MIN_GAMMA)
            M = G * (tot_min[:, None] / np.maximum(G.sum(1, keepdims=True), 1e-9))
            M = np.minimum(M, np.minimum(left, MIN_CAP * f + 4) + 5 * ot[:, None])
            cur = {k: np.array([int(x.get(k) or 0) for x in lp]) for k in ("pts", "reb", "ast", "stl", "blk", "tpm")}
            cur["min"] = now
        else:
            M = M * (1 - blow[:, None] * st[None, :])
            tot_min = TEAM_MIN * (1 + 0.104 * ot)
            G = np.power(M, MIN_GAMMA)
            M = G * (tot_min[:, None] / np.maximum(G.sum(1, keepdims=True), 1e-9))
            M = np.minimum(M, MIN_CAP + 5 * ot[:, None])
        line = {}
        night = rng.lognormal(-NIGHT_SD ** 2 / 2, NIGHT_SD, size=(n, m)) if NIGHT_SD > 0 else 1.0
        for k in SHARE_KEYS:
            sh = np.array([p["share"][k] for p in plist])
            if cur is not None:                  # cómo viene hoy cada uno en esa estadística
                sh = sh * np.array([float((x.get("fac") or {}).get(k, 1.0)) for x in lp])
            tot = t[k] if k in t else t["fgm"] * 0
            # parte de cada jugador ∝ rol × minutos (todo el total del equipo se
            # reparte entre los que juegan: ESPN no cuenta rebotes "de equipo")
            line[k] = _alloc(rng, tot, M * sh[None, :] * night, ROLE_CONC[k] * ROLE_SCALE)
        pts = 2 * line["fgm"] + line["tpm"] + line["ftm"]
        reb = line["oreb"] + line["dreb"]
        if cur is not None:                      # en vivo: lo que falta + lo que ya lleva
            pts = pts + cur["pts"][None, :]
            reb = reb + cur["reb"][None, :]
            for k in ("ast", "stl", "blk", "tpm"):
                line[k] = line[k] + cur[k][None, :]
        stats = {"pts": pts, "reb": reb, "ast": line["ast"], "stl": line["stl"], "blk": line["blk"]}
        ten = np.stack([stats[s] >= 10 for s in STATS])          # (5, N, m)
        cnt = ten.sum(0)
        for j, p in enumerate(plist):
            combos = {}
            for a in range(5):
                for b in range(a + 1, 5):
                    pr = float((ten[a, :, j] & ten[b, :, j]).mean())
                    if pr >= 0.005:
                        combos[f"{STATS[a]}+{STATS[b]}"] = pr
            out["players"].append({
                "id": p["id"], "side": side,
                "p_dd": float((cnt[:, j] >= 2).mean()), "p_td": float((cnt[:, j] >= 3).mean()),
                "p10": {s: float(ten[i, :, j].mean()) for i, s in enumerate(STATS)},
                "mean": {s: float(stats[s][:, j].mean()) for s in STATS},
                "min": float(M[:, j].mean()) + (float(cur["min"][j]) if cur is not None else 0.0),
                **({"min_left": float(M[:, j].mean())} if cur is not None else {}),
                "combos": dict(sorted(combos.items(), key=lambda kv: -kv[1])[:3]),
                **({"dist": props_dist.dists({"pts": pts[:, j], "reb": reb[:, j], "ast": line["ast"][:, j],
                                               "tpm": line["tpm"][:, j]})} if dist else {}),
            })
    out["pace"] = float(pace.mean())
    out["p_home"] = float((margin > 0).mean() + 0.5 * (margin == 0).mean())
    out["margin"] = float(margin.mean())
    out["margin_sd"] = float(margin.std())
    out["p_ot"] = float(ot.mean())
    out["p_blowout"] = float((np.abs(margin) >= 20).mean())
    if live:        # marcador final de cada simulación: para la línea de puntos, el hándicap y el ganador
        out["_final"] = {"margin": np.asarray(margin, float),
                         "home": team["home"]["pts"] + int(live["score"].get("home", 0)),
                         "away": team["away"]["pts"] + int(live["score"].get("away", 0))}
    return out
