"""
Futbol — selecciones: ¿cuánto pesa lo que el jugador hace con su selección?

    python -m futbol.backtest_nt [--since 2026-03-01] [--n-sim 1500]

Walk-forward sobre partidos de selecciones ya jugados (Nations League,
Eliminatorias, Mundial, amistosos de los equipos de cfg.NATIONAL): para cada
fecha se ajusta todo con lo anterior y se simula con la formación real.
Compara valores de cfg.NT_PRIOR_MIN (minutos de selección que pesan lo mismo
que toda la historia de club):
  • "mezcla"   : None, club y selección mezclados por fecha (lo de antes);
  • "club"     : casi sólo el club;
  • 90…1080    : la selección manda cuando junta esos minutos;
  • "selección": casi sólo la selección.
Mide log-loss de los mercados de jugador titular (menor = mejor), en total y
sólo en los jugadores que tienen historia de club Y de selección (los únicos
donde las versiones difieren). No gasta cuotas.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from futbol import config as cfg
from futbol import espn
from futbol import model as fm
from futbol.backtest import PLAYER_MARKETS, _ll

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

VARIANTS = {"mezcla": None, "club": 1e7, "1080": 1080.0, "540": 540.0, "270": 270.0, "90": 90.0, "selección": 1.0}


def run(since="2026-03-01", n_sim=1500, verbose=True):
    cup = sorted(cfg.NATIONAL)[0]
    teams, players = espn.build_history(list(cfg.LEAGUES), verbose=False)
    hist = fm.History(players, teams)
    nat = hist.national_teams
    T = teams[teams["team_id"].isin(nat) & teams["opp_id"].isin(nat)]
    evs = T[T["date"] >= pd.Timestamp(since, tz=T["date"].dt.tz)].sort_values("date").drop_duplicates("event_id")
    if verbose:
        print(f"{len(evs)} partidos de selecciones de prueba ({', '.join(sorted(evs['league'].unique()))})", flush=True)
    rows = []
    last, tm, pri = None, None, None
    keep = cfg.NT_PRIOR_MIN
    try:
        for i, (ev_id, day) in enumerate(evs[["event_id", "date"]].itertuples(index=False)):
            if day != last:
                try:
                    tm = fm.fit_team_model_cup(teams, day, cup)
                except ValueError:
                    continue
                pri = hist.priors(day)
                last = day
            two = T[T["event_id"] == ev_id]
            if len(two) != 2:
                continue
            home = two[two["home"] == 1].iloc[0]
            away = two[two["home"] == 0].iloc[0]
            sq = players[players["event_id"] == ev_id]
            actual = sq.set_index("player_id")
            # ¿quién tiene historia de club y de selección antes de hoy?
            both = set()
            for pid in sq["player_id"]:
                g = hist.by_player.get(pid)
                if g is None:
                    continue
                h = g[(g["date"] < day) & (g["minutes"] > 0)]
                isn = h["team_id"].isin(nat)
                if isn.any() and (~isn).any():
                    both.add(pid)
            for name, k in VARIANTS.items():
                cfg.NT_PRIOR_MIN = k
                prof = {}
                for tid in (home["team_id"], away["team_id"]):
                    s = sq[sq["team_id"] == tid]
                    squad = [{"player_id": r.player_id, "player": r.player, "starter": r.starter,
                              "pos_group": r.pos_group, "pos": r.pos} for r in s.itertuples()]
                    prof[tid] = fm.build_profiles(hist, tid, day, pri, squad)
                try:
                    sim = fm.simulate_match(tm, home["team_id"], away["team_id"], prof[home["team_id"]],
                                            prof[away["team_id"]], n=n_sim, key=ev_id)
                except Exception as exc:  # noqa: BLE001 - selección sin historia suficiente
                    if verbose and name == "mezcla":
                        print(f"  salteado {ev_id}: {exc}", flush=True)
                    break
                for pr_ in sim["players"]:
                    if pr_["p_start"] < 1.0 or pr_["player_id"] not in actual.index:
                        continue
                    a = actual.loc[pr_["player_id"]]
                    if isinstance(a, pd.DataFrame):
                        a = a.iloc[0]
                    if a["minutes"] <= 0 or pd.isna(a["shots"]):
                        continue
                    for stat, line in PLAYER_MARKETS:
                        rows.append({"event_id": ev_id, "date": day, "variant": name, "who": pr_["player"],
                                     "both": pr_["player_id"] in both, "stat": stat, "line": line,
                                     "p": fm.p_over(pr_[f"{stat}_dist"], line), "y": int(a[stat] > line),
                                     "mu": pr_.get("shots") if stat == "shots" else pr_.get("sot"),
                                     "real": float(a[stat])})
            if verbose and i % 25 == 0:
                print(f"  {i}/{len(evs)}", flush=True)
    finally:
        cfg.NT_PRIOR_MIN = keep
    return pd.DataFrame(rows)


def report(df):
    if df.empty:
        print("sin filas")
        return
    for title, d in (("Todos los titulares", df), ("Sólo jugadores con historia de club Y de selección", df[df["both"]])):
        n = len(d) // max(len(VARIANTS), 1)
        print(f"\n══ {title} ({n} filas por versión) — log-loss (menor = mejor) ══")
        tab = {}
        for v, g in d.groupby("variant"):
            row = {"total": _ll(g["p"], g["y"])}
            for (stat, line), gg in g.groupby(["stat", "line"]):
                row[f"{stat}>{line}"] = _ll(gg["p"], gg["y"])
            tab[v] = row
        out = pd.DataFrame(tab).T.reindex(list(VARIANTS))
        print(out.to_string(float_format=lambda v: f"{v:.4f}"))
        base = out.loc["mezcla", "total"]
        print("  diferencia contra 'mezcla': " + "  ".join(f"{v}={out.loc[v, 'total'] - base:+.4f}" for v in VARIANTS if v != "mezcla"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-03-01")
    ap.add_argument("--n-sim", type=int, default=1500)
    ap.add_argument("--out", default=str(cfg.DATA_DIR / "backtest_nt.csv.gz"))
    a = ap.parse_args()
    df = run(a.since, a.n_sim)
    if not df.empty:
        df.to_csv(a.out, index=False)
    report(df)


if __name__ == "__main__":
    main()
