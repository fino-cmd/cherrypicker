"""
NBA — distribución completa de puntos, rebotes, asistencias, triples y sus
combinaciones, a partir de las simulaciones que ya corren para el doble-doble
(altlines.dd_game: partido entero; altlines.dd: jugador solo).

Para cada mercado se guarda la cola: P(X >= k) para k = 0, 1, 2, …  Con eso
sale cualquier línea: over 24.5 = P(X >= 25) y under 24.5 = 1 − eso.
Las combinaciones (P+R, P+A, R+A, P+R+A) se suman DENTRO de cada simulación,
así queda la correlación real (una noche de muchos minutos sube todo junto).
"""

from __future__ import annotations

import math

import numpy as np

# mercado → estadísticas que suma
MARKETS = {
    "pts": ("pts",), "reb": ("reb",), "ast": ("ast",), "threes": ("tpm",),
    "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast"), "pra": ("pts", "reb", "ast"),
}
LABEL = {"pts": "Puntos", "reb": "Rebotes", "ast": "Asistencias", "threes": "Triples",
         "pr": "PTS+REB", "pa": "PTS+ASIS", "ra": "REB+ASIS",
         "pra": "PTS+REB+ASIS"}
TAIL_MIN = 0.0005      # la cola se corta cuando P(X >= k) baja de esto


def ccdf(x: np.ndarray) -> list[float]:
    """Muestras enteras → [P(X>=0), P(X>=1), …] hasta que la cola es despreciable."""
    x = np.asarray(x).astype(np.int64).clip(0, 200)
    c = np.bincount(x)
    tail = np.cumsum(c[::-1])[::-1] / len(x)
    k = int(np.searchsorted(-tail, -TAIL_MIN))      # primer k con cola < TAIL_MIN
    return [round(float(v), 4) for v in tail[:max(k, 1)]]


def dists(stats: dict[str, np.ndarray]) -> dict[str, list[float]]:
    """stats: {"pts": (N,), "reb": …, "ast": …, ["tpm": …]} de UN jugador."""
    out = {}
    for m, keys in MARKETS.items():
        if all(k in stats for k in keys):
            out[m] = ccdf(sum(np.asarray(stats[k]) for k in keys))
    return out


def p_at_least(cc: list[float], k: int) -> float:
    if k <= 0:
        return 1.0
    return float(cc[k]) if k < len(cc) else 0.0


def p_over(cc: list[float], line: float) -> float:
    """P(X > line). Línea .5 → P(X >= line+.5); línea entera → P(X >= line+1)."""
    return p_at_least(cc, math.floor(line) + 1)


def p_under(cc: list[float], line: float) -> float:
    """P(X < line). Con línea entera el empate (push) no cuenta para ninguno."""
    return 1.0 - p_at_least(cc, math.ceil(line))


def blend(a: list[float] | None, b: list[float] | None, w: float) -> list[float] | None:
    """w·a + (1−w)·b, punto a punto (rellena la más corta con ceros)."""
    if not a:
        return b
    if not b:
        return a
    n = max(len(a), len(b))
    aa = np.zeros(n); aa[:len(a)] = a
    bb = np.zeros(n); bb[:len(b)] = b
    return [round(float(v), 4) for v in w * aa + (1 - w) * bb]


def median(cc: list[float]) -> int:
    """Menor k con P(X >= k+1) < 0.5."""
    for k in range(len(cc)):
        if p_at_least(cc, k + 1) < 0.5:
            return k
    return len(cc)
