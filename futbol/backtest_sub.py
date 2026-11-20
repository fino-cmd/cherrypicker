"""
Futbol — backtest walk-forward del modo "con suplente" (Suplente+, Suplente de
Oro, Suplente).

    python -m futbol.backtest_sub [--leagues arg,bra,eng,ita] [--since 2025-06-01] [--n-sim 4000]

No cambia nada del modelo ni de la simulación: usa futbol.model tal como está
(las mismas funciones que la app) y sólo compara lo que dice con lo que pasó.

Para cada partido ya jugado:
  • el modelo se ajusta con los partidos ANTERIORES a ese día, con la
    alineación real (igual que futbol.backtest);
  • para cada titular se toman dos probabilidades del motor:
      p_normal: él solo  (shots_dist / sot_dist)
      p_sub:    él + el que entra si sale  (shots_sp_dist / sot_sp_dist)
  • el resultado real se arma con la misma regla que la liquidación de la app
    (futbol.picklog): si no jugó los 90, se suma lo del que entró por él
    (cambio exacto de ESPN si está guardado en disco; si no, el suplente que
    entró en el minuto en que salió, ± 2').

Además del acierto, separa de dónde viene el error: remates propios del
titular, remates del reemplazo y minutos.

No gasta cuotas ni consultas: sólo historia de ESPN ya descargada.
Resultados en data/futbol/backtest_sub.csv.gz.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from futbol import config as cfg
from futbol import espn
from futbol import model as fm
from futbol import picklog

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

MARKETS = [("shots", 0.5), ("shots", 1.5), ("shots", 2.5), ("shots", 3.5), ("sot", 0.5), ("sot", 1.5)]


def _cached_pairs(event_id) -> dict:
    """Cambios exactos de ESPN sólo si ya están en disco (no se consulta nada)."""
    f = cfg.CACHE_DIR / "espn_subs" / f"{event_id}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _mean(dist: np.ndarray) -> float:
    return float(np.dot(np.arange(len(dist)), dist))


def run(leagues=None, since: str | None = None, n_sim: int = 4000, verbose: bool = True) -> pd.DataFrame:
    leagues = leagues or [lg for lg in cfg.LEAGUES if lg not in cfg.CUPS]
    teams, players = espn.build_history(leagues, verbose=verbose)
    hist = fm.History(players, teams)
    rows = []
    for lg in leagues:
        T = teams[teams["league"] == lg]
        P = players[players["league"] == lg]
        if T.empty:
            continue
        start = pd.Timestamp(since) if since else T["date"].min() + pd.Timedelta(days=150)
        matches = T[T["date"] >= start].sort_values("date").drop_duplicates("event_id")
        if verbose:
            print(f"\n[{lg}] {len(matches)} partidos de prueba desde {start.date()}", flush=True)
        last_day, tm, pri = None, None, None
        for i, (ev_id, day) in enumerate(matches[["event_id", "date"]].itertuples(index=False)):
            if verbose and i and i % 100 == 0:
                print(f"  {i}/{len(matches)}", flush=True)
            if day != last_day:
                try:
                    tm = fm.fit_team_model(T, day)
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

            box = [{"player_id": str(r.player_id), "player": r.player, "team_id": r.team_id,
                    "starter": r.starter, "minutes": float(r.minutes or 0), "pos_group": r.pos_group,
                    "shots": r.shots, "sot": r.sot} for r in sq.itertuples()]
            by_id = {(b["player_id"], b["team_id"]): b for b in box}
            pairs = _cached_pairs(ev_id)

            for pr_ in sim["players"]:
                if pr_["p_start"] < 1.0:
                    continue
                a = by_id.get((str(pr_["player_id"]), pr_["team_id"]))
                if a is None or a["minutes"] <= 0 or pd.isna(a["shots"]):
                    continue
                subbed = str(a["player_id"]) in pairs or a["minutes"] < 90
                rp = picklog._replacement(box, a, pairs) if subbed else None
                rep_shots = float(rp["shots"] or 0) if rp is not None and not pd.isna(rp["shots"]) else 0.0
                rep_sot = float(rp["sot"] or 0) if rp is not None and not pd.isna(rp["sot"]) else 0.0
                rep_min = float(rp["minutes"]) if rp is not None else 0.0
                own = {"shots": float(a["shots"] or 0), "sot": float(a["sot"] or 0)}
                rep = {"shots": rep_shots, "sot": rep_sot}
                base = {"league": lg, "event_id": ev_id, "date": day, "player": pr_["player"],
                        "player_id": pr_["player_id"], "pos": pr_["pos_group"], "role": pr_.get("role"),
                        "exp_min": pr_["exp_minutes"], "min": a["minutes"], "subbed": int(subbed),
                        "rep_found": int(rp is not None), "rep_min": rep_min}
                for stat in ("shots", "sot"):
                    d_n, d_s = pr_[f"{stat}_dist"], pr_[f"{stat}_sp_dist"]
                    e_own, e_tot = _mean(d_n), _mean(d_s)
                    for st, line in MARKETS:
                        if st != stat:
                            continue
                        rows.append({**base, "stat": stat, "line": line,
                                     "p_normal": fm.p_over(d_n, line), "p_sub": fm.p_over(d_s, line),
                                     "y_normal": int(own[stat] > line),
                                     "y_sub": int(own[stat] + rep[stat] > line),
                                     "e_own": e_own, "e_rep": e_tot - e_own,
                                     "a_own": own[stat], "a_rep": rep[stat]})
    return pd.DataFrame(rows)


# ── Informe ───────────────────────────────────────────────────────────────────

def _ll(p, y):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _cal(x: pd.DataFrame) -> pd.Series:
    return pd.Series({
        "n": len(x),
        "normal_dice": x["p_normal"].mean(), "normal_pasa": x["y_normal"].mean(),
        "sub_dice": x["p_sub"].mean(), "sub_pasa": x["y_sub"].mean(),
        "sub_error": x["y_sub"].mean() - x["p_sub"].mean(),
    })


def _parts(x: pd.DataFrame) -> pd.Series:
    """De dónde sale el error, en remates promedio por titular."""
    return pd.Series({
        "n": len(x),
        "propio_dice": x["e_own"].mean(), "propio_pasa": x["a_own"].mean(),
        "reemplazo_dice": x["e_rep"].mean(), "reemplazo_pasa": x["a_rep"].mean(),
        "min_dice": x["exp_min"].mean(), "min_pasa": x["min"].mean(),
        "sale_pct": x["subbed"].mean(), "sin_reemplazo_pct": 1 - x.loc[x["subbed"] == 1, "rep_found"].mean(),
    })


def report(df: pd.DataFrame) -> None:
    if df.empty:
        print("sin filas")
        return
    f = lambda v: f"{v:.3f}"  # noqa: E731
    print(f"\n{len(df)} filas · {df['event_id'].nunique()} partidos · "
          f"{df[['event_id', 'player_id']].drop_duplicates().shape[0]} titulares")

    print("\n══ Calibración por puesto y línea (dice = modelo, pasa = real) ══")
    print(df.groupby(["pos", "stat", "line"]).apply(_cal, include_groups=False)
          .query("n >= 100").to_string(float_format=f))

    print("\n══ Delanteros por liga (todas las líneas) ══")
    F = df[df["pos"] == "F"]
    print(F.groupby(["league", "stat"]).apply(_cal, include_groups=False).to_string(float_format=f))

    print("\n══ Delanteros por rol ══")
    print(F.groupby(["role", "stat"]).apply(_cal, include_groups=False)
          .query("n >= 100").to_string(float_format=f))

    print("\n══ Calibración del modo con suplente por tramo de probabilidad (delanteros) ══")
    Fb = F.assign(tramo=pd.cut(F["p_sub"], [0, .2, .35, .5, .65, .8, 1]))
    print(Fb.groupby(["stat", "tramo"], observed=True).apply(_cal, include_groups=False)
          .to_string(float_format=f))

    print("\n══ ¿Dónde está el error? (remates por titular, una fila por jugador-partido) ══")
    one = df[(df["line"] == 0.5)]
    print(one.groupby(["pos", "stat"]).apply(_parts, include_groups=False).to_string(float_format=f))

    print("\n══ Delanteros: según cuándo sale ══")
    o = F[(F["line"] == 0.5) & (F["stat"] == "shots")].copy()
    o["sale"] = pd.cut(o["min"], [0, 45, 60, 70, 80, 89.9, 200],
                       labels=["<45'", "45-60'", "60-70'", "70-80'", "80-90'", "no sale"])
    print(o.groupby("sale", observed=True).apply(_parts, include_groups=False).to_string(float_format=f))

    tot = df.groupby("pos").apply(lambda x: pd.Series({
        "LL_normal": _ll(x["p_normal"], x["y_normal"]), "LL_sub": _ll(x["p_sub"], x["y_sub"])}),
        include_groups=False)
    print("\n══ Log-loss (menor es mejor) ══")
    print(tot.to_string(float_format=lambda v: f"{v:.4f}"))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Backtest walk-forward del modo con suplente")
    ap.add_argument("--leagues", default="arg,bra,eng,ita")
    ap.add_argument("--since", default=None, help="fecha de inicio del período de prueba")
    ap.add_argument("--n-sim", type=int, default=4000)
    ap.add_argument("--save", default=str(cfg.DATA_DIR / "backtest_sub.csv.gz"))
    ap.add_argument("--report-only", action="store_true", help="sólo el informe, con el archivo ya guardado")
    a = ap.parse_args(argv)
    if a.report_only:
        report(pd.read_csv(a.save))
        return
    df = run(a.leagues.split(","), a.since, a.n_sim)
    if a.save:
        cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
        df.to_csv(a.save, index=False)
    report(df)


if __name__ == "__main__":
    main()
