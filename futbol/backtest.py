"""
Futbol — backtest walk-forward del modelo de remates.

    python -m futbol.backtest [--leagues arg,bra,eng] [--since 2026-02-01] [--n-sim 4000]

Para cada partido del período de prueba:
  • el modelo de equipos se ajusta con los partidos ANTERIORES a ese día;
  • los jugadores salen de su historia anterior, con la alineación real de
    ese partido (la que se conoce ~1 h antes) — escenario de uso real;
  • se comparan las probabilidades con lo que pasó.

Mercados evaluados (los de bet365/Betano/Betsson):
  jugador titular: remates ≥1, ≥2, ≥3 · al arco ≥1, ≥2
  equipo:          remates > 8.5 / 10.5 / 12.5 · al arco > 2.5 / 3.5 / 4.5

Referencias ingenuas (para saber si el modelo aporta algo):
  jugador: Poisson con sus remates por 90' (recencia) × minutos esperados
  equipo:  Poisson con el promedio de remates del equipo (últimos 10)

No gasta cuotas: sólo historia de ESPN.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd
from scipy.stats import poisson

from futbol import config as cfg
from futbol import espn
from futbol import model as fm

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

PLAYER_MARKETS = [("shots", 0.5), ("shots", 1.5), ("shots", 2.5), ("sot", 0.5), ("sot", 1.5)]
TEAM_MARKETS = [("shots", 8.5), ("shots", 10.5), ("shots", 12.5), ("sot", 2.5), ("sot", 3.5), ("sot", 4.5)]
BUCKETS = [(0.0, 0.05), (0.05, 0.1), (0.1, 0.2), (0.2, 0.35), (0.35, 0.5), (0.5, 0.65), (0.65, 0.8), (0.8, 1.0)]


def _ll(p, y):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def run(leagues=None, since: str | None = None, n_sim: int = 4000, verbose: bool = True,
        calibrated: bool = True) -> pd.DataFrame:
    leagues = leagues or list(cfg.LEAGUES)
    teams, players = espn.build_history(leagues, verbose=verbose)
    hist = fm.History(players, teams)
    rows = []
    for lg in leagues:
        T = teams[teams["league"] == lg]
        P = players[players["league"] == lg]
        start = pd.Timestamp(since) if since else T["date"].min() + pd.Timedelta(days=150)
        matches = (T[T["date"] >= start].sort_values("date")
                   .drop_duplicates("event_id"))
        if verbose:
            print(f"\n[{lg}] {matches['event_id'].nunique()} partidos de prueba desde {start.date()}")
        last_day, tm, pri = None, None, None
        for ev_id, day in matches[["event_id", "date"]].itertuples(index=False):
            if day != last_day:
                try:
                    tm = fm.fit_team_model(T, day, calibrated=calibrated)
                except ValueError:
                    continue
                pri = hist.priors(day)
                last_day = day
            two = T[T["event_id"] == ev_id]
            if len(two) != 2:
                continue
            home = two[two["home"] == 1].iloc[0]
            away = two[two["home"] == 0].iloc[0]
            sq = P[P["event_id"] == ev_id]
            squads = {}
            for tid in (home["team_id"], away["team_id"]):
                s = sq[sq["team_id"] == tid]
                squads[tid] = [{"player_id": r.player_id, "player": r.player, "starter": r.starter,
                                "pos_group": r.pos_group, "pos": r.pos} for r in s.itertuples()]
            hp = fm.build_profiles(hist, home["team_id"], day, pri, squads[home["team_id"]])
            ap = fm.build_profiles(hist, away["team_id"], day, pri, squads[away["team_id"]])
            sim = fm.simulate_match(tm, home["team_id"], away["team_id"], hp, ap, n=n_sim, key=ev_id)

            # Equipos
            for side, row in (("home", home), ("away", away)):
                t = sim["teams"][side]
                prevT = T[(T["team_id"] == row["team_id"]) & (T["date"] < day)].tail(10)
                base_rate = {"shots": prevT["shots"].mean() if len(prevT) else tm.league_shots,
                             "sot": prevT["sot"].mean() if len(prevT) else tm.league_shots * tm.league_acc}
                for stat, line in TEAM_MARKETS:
                    actual = row[stat]
                    if pd.isna(actual):
                        continue
                    rows.append({"league": lg, "event_id": ev_id, "date": day, "level": "team",
                                 "who": row["abbr"], "stat": stat, "line": line,
                                 "p_model": fm.p_over(t[f"{stat}_dist"], line),
                                 "p_naive": float(poisson.sf(int(line), base_rate[stat])),
                                 "y": int(actual > line)})
            # Jugadores titulares
            actual_p = sq.set_index("player_id")
            for pr_ in sim["players"]:
                if pr_["p_start"] < 1.0:
                    continue
                a = actual_p.loc[pr_["player_id"]]
                if isinstance(a, pd.DataFrame):
                    a = a.iloc[0]
                if a["minutes"] <= 0 or pd.isna(a["shots"]):
                    continue
                g = hist.by_player.get(pr_["player_id"])
                gh = g[(g["date"] < day) & (g["minutes"] > 0)] if g is not None else None
                if gh is not None and len(gh):
                    w = 0.5 ** (np.arange(len(gh))[::-1] / cfg.PLAYER_HALFLIFE)
                    m = gh["minutes"].to_numpy(float)
                    rate_s = np.sum(w * gh["shots"].fillna(0)) / np.sum(w * m) * 90
                    rate_o = np.sum(w * gh["sot"].fillna(0)) / np.sum(w * m) * 90
                else:
                    rate_s, rate_o = 1.0, 0.35
                em = pr_["exp_minutes"] / 90.0
                for stat, line in PLAYER_MARKETS:
                    dist = pr_[f"{stat}_dist"]
                    rate = (rate_s if stat == "shots" else rate_o) * em
                    rows.append({"league": lg, "event_id": ev_id, "date": day, "level": "player",
                                 "who": pr_["player"], "pos": pr_["pos_group"], "stat": stat, "line": line,
                                 "p_model": fm.p_over(dist, line),
                                 "p_naive": float(poisson.sf(int(line), max(rate, 1e-6))),
                                 "y": int(a[stat] > line), "n_min": pr_["n_min"],
                                 "min_app": float(gh["minutes"].tail(10).mean()) if gh is not None and len(gh) else np.nan})
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> None:
    if df.empty:
        print("sin filas")
        return
    print("\n══ Log-loss y Brier (menor es mejor) ══")
    g = df.groupby(["level", "league", "stat", "line"])
    tab = g.apply(lambda x: pd.Series({
        "n": len(x), "real": x["y"].mean(), "pred": x["p_model"].mean(),
        "LL_modelo": _ll(x["p_model"], x["y"]), "LL_ingenuo": _ll(x["p_naive"], x["y"]),
        "Brier_modelo": _brier(x["p_model"], x["y"]), "Brier_ingenuo": _brier(x["p_naive"], x["y"]),
    }), include_groups=False).reset_index()
    tab["mejora_LL"] = tab["LL_ingenuo"] - tab["LL_modelo"]
    print(tab.to_string(index=False, float_format=lambda v: f"{v:.4f}"))

    print("\n══ Calibración (jugadores, todas las líneas) ══")
    for lvl in ("player", "team"):
        d = df[df["level"] == lvl]
        print(f"  {lvl}:")
        for lo, hi in BUCKETS:
            b = d[(d["p_model"] >= lo) & (d["p_model"] < hi)]
            if len(b) >= 30:
                print(f"    {lo:>4.0%}-{hi:<4.0%} n={len(b):6d} pred={b['p_model'].mean():.3f} "
                      f"real={b['y'].mean():.3f} real/pred={b['y'].mean() / b['p_model'].mean():.2f}")
    d = df[(df["level"] == "player") & df.get("min_app", pd.Series(dtype=float)).notna()] if "min_app" in df else df.iloc[0:0]
    if len(d):
        print("\n══ Titulares según minutos por partido en sus últimos 10 (¿se infla a los suplentes?) ══")
        d = d.assign(mb=pd.cut(d["min_app"], [0, 45, 60, 75, 91], labels=["<45'", "45-60'", "60-75'", "75'+"]))
        t2 = d.groupby(["league", "mb"], observed=True).apply(lambda x: pd.Series({
            "n": len(x), "pred": x["p_model"].mean(), "real": x["y"].mean(),
            "LL_modelo": _ll(x["p_model"], x["y"]), "LL_ingenuo": _ll(x["p_naive"], x["y"])}), include_groups=False)
        print(t2.to_string(float_format=lambda v: f"{v:.4f}"))
    tot = df.groupby("level").apply(lambda x: pd.Series({
        "LL_modelo": _ll(x["p_model"], x["y"]), "LL_ingenuo": _ll(x["p_naive"], x["y"])}),
        include_groups=False)
    print("\n══ Total ══")
    print(tot.to_string(float_format=lambda v: f"{v:.4f}"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Backtest walk-forward del modelo de remates")
    ap.add_argument("--leagues", default="arg,bra,eng")
    ap.add_argument("--since", default=None, help="fecha de inicio del período de prueba")
    ap.add_argument("--n-sim", type=int, default=4000)
    ap.add_argument("--raw-team", action="store_true", help="sin la calibración de equipos (para comparar)")
    ap.add_argument("--save", default=str(cfg.DATA_DIR / "backtest_predictions.csv.gz"))
    a = ap.parse_args(argv)
    df = run(a.leagues.split(","), a.since, a.n_sim, calibrated=not a.raw_team)
    if a.save:
        cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.save, index=False)
    report(df)


if __name__ == "__main__":
    main()
