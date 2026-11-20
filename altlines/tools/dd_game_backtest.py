"""
Backtest del simulador de PARTIDO (altlines.dd_game) contra el modelo por
jugador (altlines.dd), sobre los mismos jugadores y partidos de 2025-26.

Para cada partido elegido: equipos y jugadores con historia sólo ANTERIOR a esa
fecha; juegan los que jugaron ese día (en vivo eso lo da el reporte de
lesionados). Se mide Brier y calibración de P(doble-doble) y P(triple-doble)
de jugadores con 20+ minutos de promedio.

    python -m altlines.tools.dd_game_backtest [--every 4] [--n-sim 3000] [--from 2025-11-20]
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from altlines import dd, dd_game, nba_box


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=4)
    ap.add_argument("--n-sim", type=int, default=3000)
    ap.add_argument("--from", dest="start", default="2025-11-20")
    ap.add_argument("--to", dest="end", default="2026-04-13")
    ap.add_argument("--roster", action="store_true",
                    help="lista como en vivo: los que jugaron alguno de los 8 partidos previos del equipo; "
                         "se sacan sólo los de 20+ min que no jugaron (lo que diría el reporte de lesionados)")
    a = ap.parse_args()

    T, P = nba_box.tables()
    lg_all = None
    games = T[(T["date"] >= a.start) & (T["date"] < a.end) & T["home"]].iloc[:: a.every]
    rows = []
    for i, g in enumerate(games.itertuples()):
        before = g.date
        lg_all = dd_game.league_means(T[T["date"] < before])
        home = dd_game.team_profile(T, g.team, before, lg_all)
        away = dd_game.team_profile(T, g.opp, before, lg_all)
        today = P[P["game"] == g.game]
        lists, truth = {"home": [], "away": []}, {}
        cand = today
        if a.roster:
            ids = []
            for tm in (g.team, g.opp):
                tg = T[(T["team"] == tm) & (T["date"] < before)].tail(8)["game"]
                recent = P[P["game"].isin(tg) & (P["team"] == tm)]
                for pid, grp in recent.groupby("id"):
                    played = pid in set(today["id"])
                    if played or grp["min"].median() < 20:
                        ids.append((pid, tm))
            rows_ = []
            for pid, tm in ids:
                r_ = today[today["id"] == pid]
                if len(r_):
                    rows_.append(r_.iloc[0])
                else:                         # no jugó: cuenta como 0 en todo
                    rows_.append(pd.Series({"id": pid, "team": tm, "pts": 0, "reb": 0, "ast": 0, "stl": 0, "blk": 0, "min": 0}))
            cand = pd.DataFrame(rows_)
        for p in cand.itertuples():
            prof = dd_game.player_profile(P, T, p.id, before)
            if prof is None:
                continue
            side = "home" if p.team == g.team else "away"
            lists[side].append(prof)
            c = sum(getattr(p, s) >= 10 for s in ("pts", "reb", "ast", "stl", "blk"))
            hist = P[(P["id"] == p.id) & (P["date"] < before)].tail(40)
            truth[p.id] = (c >= 2, c >= 3, hist, (p.pts, p.reb, p.ast, p.min))
        if not lists["home"] or not lists["away"]:
            continue
        r = dd_game.simulate_game(home, away, lists["home"], lists["away"], lg_all, n=a.n_sim, seed=i)
        for pr in r["players"]:
            y2, y3, hist, act = truth[pr["id"]]
            if hist["min"].tail(10).mean() < 20:
                continue
            # modelo anterior (jugador solo), mismo historial
            hh = [{"min": x.min, "pts": x.pts, "reb": x.reb, "ast": x.ast, "stl": x.stl, "blk": x.blk,
                   "season": x.season, "team": x.team} for x in hist.itertuples()]
            par = dd.project(hh)
            old = dd.simulate(par, n=a.n_sim, seed=i) if par else {"p_dd": np.nan, "p_td": np.nan, "mean": {"pts": np.nan}}
            rows.append((pr["p_dd"], pr["p_td"], old["p_dd"], old["p_td"], y2, y3,
                         pr["mean"]["pts"], pr["mean"]["reb"], pr["mean"]["ast"], pr["min"], *act, old["mean"]["pts"]))
        if i % 25 == 0:
            print(f"  {i}/{len(games)} partidos · {len(rows)} jugadores", file=sys.stderr)
    A = np.array(rows, float)
    A = A[~np.isnan(A).any(1)]
    g2, g3, o2, o3, y2, y3, sp, sr, sa, sm, ap_, ar, aa, am, op_ = A.T
    hi = sp >= np.quantile(sp, .8)
    print(f"PTS top20% (por sim partido): partido {sp[hi].mean():.2f} · jugador {op_[hi].mean():.2f} · real {ap_[hi].mean():.2f} · corr jugador {np.corrcoef(op_, ap_)[0, 1]:.3f}")
    print(f"promedios  sim vs real · PTS {sp.mean():.2f}/{ap_.mean():.2f} · REB {sr.mean():.2f}/{ar.mean():.2f} · AST {sa.mean():.2f}/{aa.mean():.2f} · MIN {sm.mean():.1f}/{am.mean():.1f}")
    for nm, s_, a_ in (("PTS", sp, ap_), ("REB", sr, ar), ("AST", sa, aa)):
        hi = s_ >= np.quantile(s_, .8)
        print(f"  {nm} top 20%: sim {s_[hi].mean():.2f} real {a_[hi].mean():.2f} · corr {np.corrcoef(s_, a_)[0, 1]:.3f} · min sim {sm[hi].mean():.1f} real {am[hi].mean():.1f}")
    br = lambda q, t: float(((q - t) ** 2).mean())  # noqa: E731
    ll = lambda q, t: float(-(t * np.log(np.clip(q, 1e-4, 1)) + (1 - t) * np.log(np.clip(1 - q, 1e-4, 1))).mean())  # noqa: E731
    print(f"\njugadores-partido: {len(A)}  · DD real {y2.mean():.4f} · TD real {y3.mean():.4f}")
    print(f"DD  partido: media {g2.mean():.4f}  Brier {br(g2, y2):.5f}  logloss {ll(g2, y2):.4f}")
    print(f"DD  jugador: media {o2.mean():.4f}  Brier {br(o2, y2):.5f}  logloss {ll(o2, y2):.4f}")
    for wgt in (0.3, 0.5, 0.7):
        b2 = wgt * g2 + (1 - wgt) * o2
        b3 = wgt * g3 + (1 - wgt) * o3
        print(f"DD  mezcla {wgt:.1f} partido: media {b2.mean():.4f}  Brier {br(b2, y2):.5f}  logloss {ll(b2, y2):.4f} · TD Brier {br(b3, y3):.6f}")
    played = am > 0
    print(f"sólo los que jugaron (n={int(played.sum())}): Brier partido {br(g2[played], y2[played]):.5f} · jugador {br(o2[played], y2[played]):.5f}")
    print(f"los que no jugaron (n={int((~played).sum())}): p media partido {g2[~played].mean():.3f} · jugador {o2[~played].mean():.3f}")
    print(f"TD  partido: media {g3.mean():.4f}  Brier {br(g3, y3):.6f}")
    print(f"TD  jugador: media {o3.mean():.4f}  Brier {br(o3, y3):.6f}")
    for name, q in (("partido", g2), ("jugador", o2)):
        print(f"\nCalibración DD ({name}):")
        for lo, hi in [(0, .05), (.05, .1), (.1, .2), (.2, .3), (.3, .4), (.4, .5), (.5, .6), (.6, .7), (.7, .85), (.85, 1.01)]:
            m = (q >= lo) & (q < hi)
            if m.sum() >= 15:
                print(f"  {lo:4.2f}-{hi:4.2f}  n={int(m.sum()):5d}  p={q[m].mean():.3f}  real={y2[m].mean():.3f}")


if __name__ == "__main__":
    main()
