"""
Futbol — prueba de las copas (Champions / Europa League) con partidos ya jugados.

    python -m futbol.backtest_cups [--since 2025-09-01] [--n-sim 2000]

Walk-forward: para cada fecha de copa, los modelos se ajustan sólo con lo
anterior y se simula cada partido con la formación real. Compara tres
versiones del modelo de equipos:
  • "copa"  : el de antes (sólo partidos de esa copa);
  • "liga"  : todas las ligas juntas con el nivel de cada liga (κ = 1);
  • "liga+κ": igual, con la compresión κ aprendida de los cruces.

Mide log-loss de remates del equipo (conteo exacto y líneas de las casas),
mercados de jugador titular, y si el equipo más débil remata más de lo que
dice el modelo (la hipótesis "el chico juega sin presión").
No gasta cuotas.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from futbol import config as cfg
from futbol import espn
from futbol import model as fm
from futbol.backtest import PLAYER_MARKETS, TEAM_MARKETS, _ll

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

VARIANTS = ("copa", "liga", "liga+k")


def _fit(variant, teams, T_cup, day, lg):
    if variant == "copa":
        return fm.fit_team_model(T_cup, day)
    return fm.fit_team_model_cup(teams, day, lg, kappa_fixed=1.0 if variant == "liga" else None)


def run(since="2025-09-01", n_sim=2000, cups=None, verbose=True):
    cups = cups or sorted(cfg.CUPS)
    teams, players = espn.build_history(list(cfg.LEAGUES), verbose=False)
    hist = fm.History(players, teams)
    rows, kap = [], []
    for lg in cups:
        T = teams[teams["league"] == lg]
        P = players[players["league"] == lg]
        evs = T[T["date"] >= pd.Timestamp(since, tz=T["date"].dt.tz)].sort_values("date").drop_duplicates("event_id")
        if verbose:
            print(f"[{lg}] {len(evs)} partidos de prueba", flush=True)
        last, tms, pri = None, {}, None
        for ev_id, day in evs[["event_id", "date"]].itertuples(index=False):
            if day != last:
                try:
                    tms = {v: _fit(v, teams, T, day, lg) for v in VARIANTS}
                except ValueError:
                    continue
                pri = hist.priors(day)
                last = day
                kap.append({"league": lg, "date": day, "kappa": tms["liga+k"].kappa})
            two = T[T["event_id"] == ev_id]
            if len(two) != 2:
                continue
            home = two[two["home"] == 1].iloc[0]
            away = two[two["home"] == 0].iloc[0]
            sq = P[P["event_id"] == ev_id]
            prof = {}
            for tid in (home["team_id"], away["team_id"]):
                s = sq[sq["team_id"] == tid]
                squad = [{"player_id": r.player_id, "player": r.player, "starter": r.starter,
                          "pos_group": r.pos_group, "pos": r.pos} for r in s.itertuples()]
                prof[tid] = fm.build_profiles(hist, tid, day, pri, squad)
            actual_p = sq.set_index("player_id")
            sims = {v: fm.simulate_match(tms[v], home["team_id"], away["team_id"], prof[home["team_id"]],
                                         prof[away["team_id"]], n=n_sim, key=ev_id) for v in VARIANTS}
            for side, row, orow in (("home", home, away), ("away", away, home)):
                if pd.isna(row["shots"]):
                    continue
                y = int(row["shots"])
                rec = {"league": lg, "event_id": ev_id, "date": day, "level": "team", "who": row["abbr"],
                       "y_shots": y, "y_sot": row["sot"]}
                for v in VARIANTS:
                    t = sims[v]["teams"][side]
                    rec[f"lam_{v}"] = t["lam_shots"]
                    rec[f"ls_{v}"] = -np.log(max(t["shots_dist"][min(y, 39)], 1e-4))
                    for stat, line in TEAM_MARKETS:
                        rec[f"p_{v}_{stat}_{line}"] = fm.p_over(t[f"{stat}_dist"], line)
                # ¿es el más débil? (según el modelo con niveles de liga, sin κ)
                tl = sims["liga"]["teams"]
                rec["gap"] = float(np.log(tl[side]["lam_shots"] / tl["away" if side == "home" else "home"]["lam_shots"]))
                rows.append(rec)
            for v in VARIANTS:
                for pr_ in sims[v]["players"]:
                    if pr_["p_start"] < 1.0 or pr_["player_id"] not in actual_p.index:
                        continue
                    a = actual_p.loc[pr_["player_id"]]
                    if isinstance(a, pd.DataFrame):
                        a = a.iloc[0]
                    if a["minutes"] <= 0 or pd.isna(a["shots"]):
                        continue
                    for stat, line in PLAYER_MARKETS:
                        rows.append({"league": lg, "event_id": ev_id, "date": day, "level": "player",
                                     "variant": v, "who": pr_["player"], "stat": stat, "line": line,
                                     "p": fm.p_over(pr_[f"{stat}_dist"], line), "y": int(a[stat] > line)})
    return pd.DataFrame(rows), pd.DataFrame(kap)


def report(df, kap):
    t = df[df["level"] == "team"]
    print(f"\n══ Remates del equipo ({len(t)} filas) — log-loss del conteo exacto (menor = mejor) ══")
    for lg, g in t.groupby("league"):
        print(f"  {lg}: " + "  ".join(f"{v}={g[f'ls_{v}'].mean():.4f}" for v in VARIANTS))
    print("  total: " + "  ".join(f"{v}={t[f'ls_{v}'].mean():.4f}" for v in VARIANTS))
    print("\n  Líneas de las casas (log-loss):")
    for stat, line in TEAM_MARKETS:
        yy = (t[f"y_{stat}"] > line).astype(int)
        ok = t[f"y_{stat}"].notna()
        print(f"   {stat:5s} >{line:<5}" + "  ".join(f"{v}={_ll(t.loc[ok, f'p_{v}_{stat}_{line}'], yy[ok]):.4f}" for v in VARIANTS))
    print("\n  Remates esperados vs reales (sesgo):")
    for v in VARIANTS:
        print(f"   {v:7s} pred={t[f'lam_{v}'].mean():.2f} real={t['y_shots'].mean():.2f}")

    print("\n══ ¿El más débil remata más de lo que dice su nivel? ══")
    t = t.assign(bucket=pd.cut(t["gap"], [-9, -0.6, -0.3, -0.1, 0.1, 0.3, 0.6, 9],
                               labels=["muy débil", "débil", "algo débil", "parejo", "algo fuerte", "fuerte", "muy fuerte"]))
    tab = t.groupby("bucket", observed=True).apply(lambda g: pd.Series({
        "n": len(g), "real": g["y_shots"].mean(), "liga": g["lam_liga"].mean(),
        "real/liga": g["y_shots"].mean() / g["lam_liga"].mean(),
        "liga+k": g["lam_liga+k"].mean(), "copa": g["lam_copa"].mean()}), include_groups=False)
    print(tab.to_string(float_format=lambda v: f"{v:.2f}"))

    p = df[df["level"] == "player"]
    print(f"\n══ Jugadores titulares ({len(p) // len(VARIANTS)} filas por versión) — log-loss ══")
    for (stat, line), g in p.groupby(["stat", "line"]):
        print(f"   {stat:5s} >{line:<4}" + "  ".join(
            f"{v}={_ll(g[g['variant'] == v]['p'], g[g['variant'] == v]['y']):.4f}" for v in VARIANTS)
            + f"   real={g[g['variant'] == 'copa']['y'].mean():.3f} pred(liga+k)={g[g['variant'] == 'liga+k']['p'].mean():.3f}")
    print("  total: " + "  ".join(f"{v}={_ll(p[p['variant'] == v]['p'], p[p['variant'] == v]['y']):.4f}" for v in VARIANTS))

    print("\n  Calibración liga+k (jugadores):")
    q = p[p["variant"] == "liga+k"]
    for lo, hi in [(0, .1), (.1, .2), (.2, .35), (.35, .5), (.5, .65), (.65, .8), (.8, 1.01)]:
        b = q[(q["p"] >= lo) & (q["p"] < hi)]
        if len(b) >= 30:
            print(f"    {lo:>4.0%}-{hi:<4.0%} n={len(b):5d} pred={b['p'].mean():.3f} real={b['y'].mean():.3f}")
    if len(kap):
        print("\n══ κ aprendido (1 = sin compresión; <1 = el chico rinde más de lo que dice su nivel) ══")
        k = kap.groupby(kap["date"].dt.to_period("M"))["kappa"].mean()
        print(k.to_string(float_format=lambda v: f"{v:.3f}"))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2025-09-01")
    ap.add_argument("--n-sim", type=int, default=2000)
    ap.add_argument("--cups", default=",".join(sorted(cfg.CUPS)))
    ap.add_argument("--save", default=str(cfg.DATA_DIR / "backtest_cups.csv.gz"))
    a = ap.parse_args(argv)
    df, kap = run(a.since, a.n_sim, a.cups.split(","))
    if a.save:
        df.to_csv(a.save, index=False)
    report(df, kap)


if __name__ == "__main__":
    main()
