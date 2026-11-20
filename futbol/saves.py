"""
Futbol — atajadas del arquero (Liga MX).

No toca el modelo ni la simulación: usa lo que la simulación del partido ya
calcula (cuántos remates al arco hace el RIVAL, con toda su distribución) y le
suma una sola cosa: qué parte de los remates al arco ataja el arquero.

    atajadas ~ Binomial(remates al arco del rival, q)

  · q del equipo = atajadas / remates al arco recibidos en sus últimos
    partidos, achicado hacia el promedio de la liga (PRIOR_SOT remates
    "virtuales"): con pocos partidos manda la liga.
  · ESPN cuenta las atajadas por arquero y por equipo; en casi todos los
    partidos atajadas = remates al arco del rival − goles recibidos.
  · La probabilidad es "si ataja los 90 minutos": si lo cambian o lo expulsan,
    las atajadas del suplente no cuentan para su línea.

Sólo en las ligas de config.SAVES_LEAGUES (las que se bajaron con atajadas).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from futbol import config as cfg

PRIOR_SOT = 45.0        # remates al arco "virtuales" con la tasa de la liga
LAST_MATCHES = 24       # partidos del equipo que se miran
Q_CLIP = (0.55, 0.85)
Q_DEFAULT = 0.70


def _faced(teams: pd.DataFrame, league: str, asof) -> pd.DataFrame:
    """Una fila por equipo y partido: atajadas propias y remates al arco recibidos."""
    if "saves" not in teams.columns:
        return pd.DataFrame()
    t = teams[(teams["league"] == league) & (teams["date"] < pd.Timestamp(asof))]
    t = t[t["saves"].notna()]
    if t.empty:
        return pd.DataFrame()
    opp = t[["event_id", "team_id", "sot"]].rename(columns={"team_id": "opp_id", "sot": "faced"})
    m = t.merge(opp, on=["event_id", "opp_id"], how="inner")
    return m[m["faced"].notna()]


def rates(teams: pd.DataFrame, league: str, asof) -> tuple[float, dict]:
    """(q de la liga, {team_id: (q del equipo, partidos)})."""
    m = _faced(teams, league, asof)
    if m.empty or m["faced"].sum() <= 0:
        return Q_DEFAULT, {}
    q_lg = float(min(max(m["saves"].sum() / m["faced"].sum(), Q_CLIP[0]), Q_CLIP[1]))
    out = {}
    for tid, g in m.sort_values("date").groupby("team_id"):
        g = g.tail(LAST_MATCHES)
        q = (g["saves"].sum() + q_lg * PRIOR_SOT) / (g["faced"].sum() + PRIOR_SOT)
        out[str(tid)] = (float(min(max(q, Q_CLIP[0]), Q_CLIP[1])), int(len(g)))
    return q_lg, out


def pmf(sot_dist, q: float) -> np.ndarray:
    """Distribución de atajadas: mezcla de binomiales sobre los remates al arco del rival."""
    d = np.asarray(sot_dist, float)
    out = np.zeros(len(d))
    for s, ps in enumerate(d):
        if ps <= 0:
            continue
        for k in range(s + 1):
            out[k] += ps * math.comb(s, k) * q ** k * (1 - q) ** (s - k)
    return out


def for_match(sim: dict, ev: dict, teams: pd.DataFrame, league: str, asof, photo_of: dict | None = None) -> list[dict]:
    """Las atajadas de los dos arqueros titulares del partido."""
    q_lg, by_team = rates(teams, league, asof)
    res = []
    for side, opp in (("home", "away"), ("away", "home")):
        tid = str(ev[f"{side}_id"])
        gks = [p for p in sim["players"] if str(p["team_id"]) == tid and p.get("pos_group") == "G"]
        gk = max(gks, key=lambda p: (p.get("p_start", 0), p.get("exp_minutes", 0))) if gks else None
        q, n = by_team.get(tid, (q_lg, 0))
        d = pmf(sim["teams"][opp]["sot_dist"], q)
        mean = float(np.dot(np.arange(len(d)), d))
        lines = []
        for ln in cfg.SAVE_LINES:
            po = float(d[int(math.floor(ln)) + 1:].sum())
            if 0.03 <= po <= 0.97:
                lines.append({"line": ln, "p_over": round(po, 3), "fair_over": round(1 / po, 2),
                              "p_under": round(1 - po, 3), "fair_under": round(1 / (1 - po), 2)})
        res.append({
            "side": side, "team_id": tid, "team": ev[side],
            "player_id": None if gk is None else str(gk["player_id"]),
            "player": None if gk is None else gk["player"],
            "photo": None if gk is None else (photo_of or {}).get(str(gk["player_id"]), {}).get("url"),
            "p_start": None if gk is None else round(float(gk.get("p_start", 0)), 2),
            "saves": round(mean, 2), "opp_sot": round(float(sim["teams"][opp]["sot_mean"]), 2),
            "q": round(q, 3), "q_league": round(q_lg, 3), "q_matches": n, "lines": lines,
        })
    return res
