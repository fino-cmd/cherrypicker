"""
Backtest walk-forward del modelo de doble-doble / triple-doble (altlines.dd).

Con los logs de ESPN ya cacheados (temporadas 2024-25 y 2025-26), para cada
partido de 2025-26 desde el 10.º del jugador: historia = sólo lo anterior,
simulación, y lo que pasó esa noche. Mide calibración por tramos, Brier y lo
compara con la frecuencia reciente ponderada (la referencia ingenua).

El caché no tiene robos ni tapas: en el backtest se cuentan sólo puntos,
rebotes y asistencias (de los dos lados, así que la comparación es justa).

    python -m altlines.tools.dd_backtest [--every 3] [--n-sim 4000]
"""

from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np

from altlines import dd

CACHE = os.path.join("data", "altlines", "cache", "gamelog")


def load(aid: str, season: int) -> list[dict]:
    try:
        rows = json.load(open(os.path.join(CACHE, f"{aid}_{season}.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    rows = [r for r in rows if not r.get("playoffs") and (r.get("min") or 0) > 0]
    for r in rows:
        r.setdefault("stl", 0)
        r.setdefault("blk", 0)
    return sorted(rows, key=lambda r: r["date"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=3)
    ap.add_argument("--n-sim", type=int, default=4000)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--start", type=int, default=10, help="desde qué partido de la temporada")
    ap.add_argument("--until", type=int, default=999, help="hasta qué partido de la temporada")
    a = ap.parse_args()

    out = []
    for f in sorted(glob.glob(os.path.join(CACHE, f"*_{a.season}.json"))):
        aid = os.path.basename(f).split("_")[0]
        cur = load(aid, a.season)
        if len(cur) < 20:
            continue
        prev = load(aid, a.season - 1)
        hist_all = prev + cur
        off = len(prev)
        for i in range(a.start, min(len(cur), a.until), a.every):
            hist = hist_all[: off + i]
            par = dd.project(hist)
            if par is None:
                continue
            # stl/blk en cero también en la simulación (el caché no los tiene)
            for s in ("stl", "blk"):
                par["rate"][s] = 0.0
            r = dd.simulate(par, n=a.n_sim, seed=i)
            g = cur[i]
            c = sum(g[s] >= 10 for s in ("pts", "reb", "ast"))
            w = dd._weights(min(len(hist), 20))
            hh = hist[-20:]
            base_dd = float(sum(wi * (sum(x[s] >= 10 for s in ("pts", "reb", "ast")) >= 2) for wi, x in zip(w, hh)) / w.sum())
            out.append((r["p_dd"], r["p_td"], c >= 2, c >= 3, base_dd))
    arr = np.array(out, float)
    p, pt, y, yt, b = arr.T
    print(f"partidos: {len(arr)}  DD reales: {y.mean():.4f}  modelo: {p.mean():.4f}  |  TD reales: {yt.mean():.4f}  modelo: {pt.mean():.4f}")
    def brier(q, t):
        return float(((q - t) ** 2).mean())
    bb = np.clip(b, 0.005, 0.995)
    print(f"Brier DD  modelo {brier(p, y):.5f}   frecuencia ponderada {brier(bb, y):.5f}")
    print(f"Brier TD  modelo {brier(pt, yt):.6f}   base (tasa global) {brier(np.full_like(yt, yt.mean()), yt):.6f}")
    print("\nCalibración DD (modelo):")
    for lo, hi in [(0, .02), (.02, .05), (.05, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, 1.01)]:
        m = (p >= lo) & (p < hi)
        if m.sum() >= 20:
            print(f"  {lo:4.2f}-{hi:4.2f}  n={int(m.sum()):6d}  p={p[m].mean():.3f}  real={y[m].mean():.3f}")
    print("\nCalibración TD (modelo):")
    for lo, hi in [(0, .01), (.01, .03), (.03, .06), (.06, .1), (.1, .2), (.2, .35), (.35, 1.01)]:
        m = (pt >= lo) & (pt < hi)
        if m.sum() >= 20:
            print(f"  {lo:4.2f}-{hi:4.2f}  n={int(m.sum()):6d}  p={pt[m].mean():.4f}  real={yt[m].mean():.4f}")


if __name__ == "__main__":
    main()
