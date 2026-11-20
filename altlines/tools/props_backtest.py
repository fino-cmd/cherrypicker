"""
Backtest de las líneas de jugador (puntos, rebotes, asistencias, triples y
combinaciones) con las mismas simulaciones del doble-doble, sin mirar el futuro.

Para cada partido elegido: equipos y jugadores sólo con historia ANTERIOR;
juegan los que jugaron ese día (en vivo eso lo da el reporte de lesionados).
Para cada jugador de 16+ min y cada mercado se evalúan todas las líneas
enteras k con P(X >= k) entre 3 % y 97 % (las que una casa ofrecería):
log-loss del partido simulado, del jugador solo y de mezclas, y calibración.

    python -m altlines.tools.props_backtest [--every 4] [--n-sim 3000] [--from 2025-11-20]
"""

from __future__ import annotations

import argparse
import sys

import numpy as np

from altlines import dd, dd_game, nba_box, props_dist as pd_

ACT = {"pts": ("pts",), "reb": ("reb",), "ast": ("ast",), "threes": ("tpm",),
       "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast"), "pra": ("pts", "reb", "ast")}
WEIGHTS = (0.0, 0.3, 0.5, 0.7, 1.0)      # peso del partido simulado


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=4)
    ap.add_argument("--n-sim", type=int, default=3000)
    ap.add_argument("--from", dest="start", default="2025-11-20")
    ap.add_argument("--to", dest="end", default="2026-04-13")
    ap.add_argument("--min", dest="min_avg", type=float, default=16.0)
    ap.add_argument("--save", default=None, help="guarda las líneas (npz) para calibrar")
    a = ap.parse_args()

    T, P = nba_box.tables()
    games = T[(T["date"] >= a.start) & (T["date"] < a.end) & T["home"]].iloc[:: a.every]
    # filas: (mercado, k, p_partido, p_jugador, y, es_linea_central)
    rows = []
    for i, g in enumerate(games.itertuples()):
        before = g.date
        lg = dd_game.league_means(T[T["date"] < before])
        home = dd_game.team_profile(T, g.team, before, lg)
        away = dd_game.team_profile(T, g.opp, before, lg)
        today = P[(P["game"] == g.game) & (P["min"] > 0)]
        lists, truth = {"home": [], "away": []}, {}
        for p in today.itertuples():
            prof = dd_game.player_profile(P, T, p.id, before)
            if prof is None:
                continue
            side = "home" if p.team == g.team else "away"
            lists[side].append(prof)
            hist = P[(P["id"] == p.id) & (P["date"] < before)].tail(40)
            truth[p.id] = (hist, {"pts": p.pts, "reb": p.reb, "ast": p.ast, "tpm": p.tpm})
        if not lists["home"] or not lists["away"]:
            continue
        r = dd_game.simulate_game(home, away, lists["home"], lists["away"], lg, n=a.n_sim, seed=i, dist=True)
        for pr in r["players"]:
            hist, act = truth[pr["id"]]
            if len(hist) < 5 or hist["min"].tail(10).mean() < a.min_avg:
                continue
            hh = [{"min": x.min, "pts": x.pts, "reb": x.reb, "ast": x.ast, "stl": x.stl, "blk": x.blk,
                   "season": x.season, "team": x.team} for x in hist.itertuples()]
            par = dd.project(hh)
            solo = dd.simulate(par, n=a.n_sim, seed=i, dist=True)["dist"] if par else {}
            for m, keys in ACT.items():
                cg = pr["dist"].get(m)
                if not cg:
                    continue
                cs = solo.get(m) or cg          # triples: sólo el partido
                y_val = sum(act[k] for k in keys)
                med = pd_.median(cg)
                for k in range(1, len(cg)):
                    pg = pd_.p_at_least(cg, k)
                    if not (0.03 <= pg <= 0.97):
                        continue
                    rows.append((m, k, pg, pd_.p_at_least(cs, k), float(y_val >= k), k == med + 1))
        if i % 25 == 0:
            print(f"  {i}/{len(games)} partidos · {len(rows)} líneas", file=sys.stderr)

    ms = np.array([x[0] for x in rows])
    G, S, Y, C = (np.array([x[j] for x in rows], float) for j in (2, 3, 4, 5))
    C = C.astype(bool)
    if a.save:
        K = np.array([x[1] for x in rows], np.int32)
        np.savez_compressed(a.save, m=ms, k=K, g=G, s=S, y=Y, c=C)
    ll = lambda q, t: float(-(t * np.log(np.clip(q, 1e-4, 1 - 1e-4)) + (1 - t) * np.log(np.clip(1 - q, 1e-4, 1 - 1e-4))).mean())  # noqa: E731
    print(f"\nlíneas evaluadas: {len(rows)}")
    print("log-loss por mercado (menor = mejor); columnas = peso del partido simulado")
    print("mercado    n      " + "  ".join(f"w={w:.1f} " for w in WEIGHTS) + "  p media  real")
    for m in list(ACT) + ["TODOS"]:
        sel = np.ones(len(ms), bool) if m == "TODOS" else ms == m
        if not sel.any():
            continue
        vals = [ll(w * G[sel] + (1 - w) * S[sel], Y[sel]) for w in WEIGHTS]
        b = 0.5 * G[sel] + 0.5 * S[sel]
        print(f"{m:7s} {int(sel.sum()):7d}  " + "  ".join(f"{v:.4f}" for v in vals) + f"   {b.mean():.3f}  {Y[sel].mean():.3f}")
    for w_name, w in (("mezcla 0.5", 0.5),):
        q = w * G + (1 - w) * S
        print(f"\nCalibración ({w_name}), todas las líneas:")
        for lo, hi in [(.03, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, .8), (.8, .9), (.9, .97)]:
            sel = (q >= lo) & (q < hi)
            if sel.sum() >= 30:
                print(f"  {lo:4.2f}-{hi:4.2f}  n={int(sel.sum()):6d}  p={q[sel].mean():.3f}  real={Y[sel].mean():.3f}")
        print(f"\nLínea central (la que suele dar la casa: mediana + 0,5), over:")
        for m in ACT:
            sel = C & (ms == m)
            if sel.sum() >= 30:
                print(f"  {m:7s} n={int(sel.sum()):5d}  p over={q[sel].mean():.3f}  real={Y[sel].mean():.3f}")
        print(f"\nColas (P < 20 %, donde están las escaleras de overs):")
        for m in ACT:
            sel = (q < 0.2) & (ms == m)
            if sel.sum() >= 30:
                print(f"  {m:7s} n={int(sel.sum()):6d}  p={q[sel].mean():.3f}  real={Y[sel].mean():.3f}")


if __name__ == "__main__":
    main()
