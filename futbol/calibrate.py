"""
Futbol — calibración del modelo de equipos, fuera de muestra.

    python -m futbol.calibrate [--until 2026-04-01]

El ajuste de equipos mide la dispersión DENTRO de la muestra, donde ya
"conoce" los partidos, y queda demasiado angosto: no incluye el error de
pronosticar el nivel de un equipo la semana que viene. Acá se reproduce el
uso real (para cada fecha, ajustar con lo anterior y pronosticar ese día) y se
miden, por liga:

  level  remates reales / predichos   (sesgo de nivel)
  r      dispersión de la binomial negativa que reproduce la varianza real

Sólo se usan fechas ANTERIORES a `until`, para poder evaluar después en
partidos que la calibración no vio. Resultado: data/futbol/team_calibration.json
(lo aplica model.fit_team_model).
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

for _s in (sys.stdout,):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

PATH = cfg.DATA_DIR / "team_calibration.json"


def run(until: str, leagues=None, step: int = 2) -> dict:
    leagues = leagues or list(cfg.LEAGUES)
    teams, _ = espn.build_history(leagues, verbose=False)
    out = {"until": until, "leagues": {}}
    for lg in leagues:
        T = teams[teams["league"] == lg].sort_values("date")
        start = T["date"].min() + pd.Timedelta(days=150)
        days = sorted(d for d in T["date"].unique() if start <= d < pd.Timestamp(until))[::step]
        rows = []
        for day in days:
            tm = fm.fit_team_model(T, day, calibrated=False)
            for r in T[T["date"] == day].itertuples():
                if pd.isna(r.shots):
                    continue
                lam, _ = tm.expected(r.team_id, r.opp_id, r.home)
                rows.append((lam, r.shots))
        d = pd.DataFrame(rows, columns=["lam", "y"])
        level = float(d["y"].mean() / d["lam"].mean())
        lam = d["lam"] * level
        excess = float(((d["y"] - lam) ** 2 - lam).mean())
        r = float(np.clip((lam ** 2).mean() / max(excess, 1e-6), 3.0, 200.0))
        out["leagues"][lg] = {"level": round(level, 4), "r": round(r, 2), "n": len(d)}
        print(f"{lg}: {len(d)} pronósticos fuera de muestra hasta {until} → nivel ×{level:.3f}, r = {r:.1f}")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Calibración fuera de muestra del modelo de equipos")
    ap.add_argument("--until", default="2026-04-01")
    a = ap.parse_args(argv)
    run(a.until)


if __name__ == "__main__":
    main()
