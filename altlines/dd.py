"""
NBA — doble-doble y triple-doble: simulación conjunta por jugador.

Un doble-doble son 10+ en al menos dos de puntos, rebotes, asistencias, robos y
tapas (triple-doble: en tres). Como depende de VARIAS estadísticas a la vez, no
alcanza con la probabilidad de cada una por separado: un partido con muchos
minutos sube todas juntas. Por eso se simula el partido entero del jugador:

  minutos   M  ~ remuestreo de sus minutos recientes (con peso a lo reciente),
                 así entran también las noches cortas (lesión, paliza, faltas)
  ritmo     G  ~ LogNormal(0, SIG_GAME), común a todas sus estadísticas
  stat s | M ~ BinomialNegativa(media = tasa_s · M · G, varianza = φ_s · media)

La tasa por minuto y la sobredispersión φ_s salen de su historia (con peso a lo
reciente y φ achicado hacia el valor de liga). Con 20.000 partidos simulados se
cuenta cuántos tienen 2+ (DD) y 3+ (TD) estadísticas en 10 o más.

El backtest (altlines/tools/dd_backtest.py) mide la calibración sin mirar el
futuro sobre la temporada 2025-26 (9.705 partidos, pts/reb/ast): DD media
8,14 % vs 8,12 % real, tramos dentro del ruido. El TD se subestima algo en
el tramo 1-3 % (1,8 % vs 4,6 % real, n=323).
"""

from __future__ import annotations

import numpy as np

STATS = ("pts", "reb", "ast", "stl", "blk")
N_SIM = 20_000
HALFLIFE = 15          # partidos: semivida del peso de lo reciente
LOOKBACK = 40          # partidos que se miran como máximo
MIN_LOOKBACK = 30      # partidos de donde se remuestrean los minutos
SIG_GAME = 0.05        # ritmo común del partido (correlación entre stats)
SHRINK_N = 20          # partidos "virtuales" del prior de φ
# Sobredispersión típica de liga (varianza/media condicionada a los minutos).
PHI_PRIOR = {"pts": 1.8, "reb": 1.15, "ast": 1.25, "stl": 1.05, "blk": 1.25}
PHI_MIN = 1.02


PREV_SEASON_W = 0.5    # un partido de la temporada pasada vale la mitad
OTHER_TEAM_W = 0.5     # y uno con otro equipo (cambió el rol), también


def _weights(n: int) -> np.ndarray:
    """Peso por antigüedad de cada partido, el más reciente al final."""
    age = np.arange(n)[::-1]
    return 0.5 ** (age / HALFLIFE)


def _game_weights(g: list[dict]) -> np.ndarray:
    """Antigüedad × temporada × equipo: el rol de ESTE año y ESTE equipo manda."""
    w = _weights(len(g))
    season = max((x.get("season") or 0) for x in g)
    team = g[-1].get("team")
    for i, x in enumerate(g):
        if x.get("season") and x["season"] < season:
            w[i] *= PREV_SEASON_W
        if team and x.get("team") and x["team"] != team:
            w[i] *= OTHER_TEAM_W
    return w


def project(games: list[dict]) -> dict | None:
    """Historia (orden cronológico, sólo partidos jugados) → parámetros."""
    g = [x for x in games if (x.get("min") or 0) > 0][-LOOKBACK:]
    if len(g) < 5:
        return None
    w = _game_weights(g)
    mins = np.array([x["min"] for x in g], float)
    out = {"n": len(g), "min_w": w[-MIN_LOOKBACK:], "min_v": mins[-MIN_LOOKBACK:],
           "rate": {}, "phi": {}}
    wm = float((w * mins).sum())
    for s in STATS:
        v = np.array([float(x.get(s) or 0) for x in g])
        r = float((w * v).sum() / wm)
        mu = r * mins
        ok = mu > 0.3
        if ok.sum() >= 3:
            ww = w[ok]
            phi_obs = float((ww * (v[ok] - mu[ok]) ** 2 / mu[ok]).sum() / ww.sum())
        else:
            phi_obs = PHI_PRIOR[s]
        neff = float(w.sum() ** 2 / (w ** 2).sum())
        phi = (neff * phi_obs + SHRINK_N * PHI_PRIOR[s]) / (neff + SHRINK_N)
        out["rate"][s] = r
        out["phi"][s] = max(PHI_MIN, phi)
    return out


def simulate(par: dict, n: int = N_SIM, seed: int = 0, min_scale: float = 1.0, dist: bool = False) -> dict:
    """Simula `n` partidos del jugador. `min_scale` ajusta los minutos (rol)."""
    rng = np.random.default_rng(seed)
    p = par["min_w"] / par["min_w"].sum()
    M = rng.choice(par["min_v"], size=n, p=p)
    M = np.clip(M * min_scale + rng.normal(0, 1.5, n), 0, 53)
    G = rng.lognormal(-SIG_GAME ** 2 / 2, SIG_GAME, n)
    sims = {}
    for s in STATS:
        mu = np.maximum(par["rate"][s] * M * G, 1e-6)
        phi = par["phi"][s]
        # BinNeg con varianza φ·μ: n = μ/(φ−1), p = 1/φ
        k = mu / (phi - 1)
        lam = rng.gamma(k, (phi - 1), n)        # Poisson-Gamma
        sims[s] = rng.poisson(lam)
    ten = np.stack([sims[s] >= 10 for s in STATS])
    cnt = ten.sum(0)
    res = {
        "p_dd": float((cnt >= 2).mean()),
        "p_td": float((cnt >= 3).mean()),
        "p10": {s: float(ten[i].mean()) for i, s in enumerate(STATS)},
        "mean": {s: float(sims[s].mean()) for s in STATS},
        "min": float(M.mean()),
    }
    # Cómo llega al doble-doble: el par más probable
    combos = {}
    for i in range(len(STATS)):
        for j in range(i + 1, len(STATS)):
            pr = float((ten[i] & ten[j]).mean())
            if pr >= 0.005:
                combos[f"{STATS[i]}+{STATS[j]}"] = pr
    res["combos"] = dict(sorted(combos.items(), key=lambda kv: -kv[1])[:3])
    if dist:
        from altlines import props_dist
        res["dist"] = props_dist.dists({k: sims[k] for k in ("pts", "reb", "ast")})
    return res


def empirical(games: list[dict], last: int = 10) -> dict:
    """Lo que realmente hizo en sus últimos `last` partidos."""
    g = [x for x in games if (x.get("min") or 0) > 0][-last:]
    if not g:
        return {"n": 0, "dd": 0, "td": 0}
    cnt = [sum(float(x.get(s) or 0) >= 10 for s in STATS) for x in g]
    return {"n": len(g), "dd": sum(c >= 2 for c in cnt), "td": sum(c >= 3 for c in cnt)}
