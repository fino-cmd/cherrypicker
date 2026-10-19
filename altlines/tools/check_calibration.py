"""Re-measure the calibration numbers quoted in altlines/README.md (§5).

    python -m altlines.tools.check_calibration [--offline] [--n-iter 20000]

Two views, both against the demo's own synthetic history:

  * reliability — bucket by the in-sample empirical CDF and compare it to the
    model's CDF. This is the "is the tail too fat or too thin?" question, and it
    is the one that decides whether the reported edges are a floor or a ceiling.
  * quantiles / shape — model P50…P99, std, CV and skewness over the player's
    empirical ones. Says *where* the distribution is off.

Reminder: the demo generator uses the same family as the engine
(NegativeBinomial + Beta-Binomial), so this is **self-consistency of the
pipeline, not validation against reality**. Real validation needs real logs and
the settlement of the lines.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from altlines.run_pipeline import run

RELIABILITY_BUCKETS = [(0.20, 0.35), (0.10, 0.20), (0.05, 0.10), (0.02, 0.05)]
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]


def _sims_for(store: dict, pid, market: str) -> np.ndarray | None:
    got = store.get((pid, market))
    if got is None:
        got = store.get((str(pid), market))
    return None if got is None else np.asarray(got, dtype=float)


def reliability(store: dict, logs: pd.DataFrame) -> pd.DataFrame:
    """(P_empirical, P_model) for every observed value of every player-market."""
    rows = []
    for (pid, market), grp in logs.groupby(["player_id", "market"]):
        sims = _sims_for(store, pid, market)
        if sims is None:
            continue
        obs = grp["value"].to_numpy(dtype=float)
        for v in obs:
            p_emp = float((obs >= v).mean())
            if p_emp > 0:
                rows.append((p_emp, float((sims >= v).mean())))
    return pd.DataFrame(rows, columns=["p_emp", "p_model"])


def quantile_and_shape(store: dict, logs: pd.DataFrame) -> tuple[dict, dict]:
    """mean(model/empirical) per quantile, plus the shape ratios."""
    q_acc: dict[float, list[float]] = {q: [] for q in QUANTILES}
    std_r, cv_r, skew_r = [], [], []
    for (pid, market), grp in logs.groupby(["player_id", "market"]):
        sims = _sims_for(store, pid, market)
        if sims is None:
            continue
        obs = grp["value"].to_numpy(dtype=float)
        if obs.size < 10:
            continue
        for q in QUANTILES:
            emp = float(np.quantile(obs, q))
            if emp > 0.5:                       # a ratio below that is all noise
                q_acc[q].append(float(np.quantile(sims, q)) / emp)
        if obs.std() > 0:
            std_r.append(float(sims.std() / obs.std()))
            cv_r.append(float((sims.std() / sims.mean()) / (obs.std() / obs.mean())))
        emp_skew = float(pd.Series(obs).skew())
        if abs(emp_skew) > 1e-3:
            skew_r.append(float(pd.Series(sims).skew()) / emp_skew)
    return ({q: v for q, v in q_acc.items() if v},
            {"std": std_r, "cv": cv_r, "skew": skew_r})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true",
                    help="deterministic demo calendar instead of the real one")
    ap.add_argument("--n-iter", type=int, default=None,
                    help="Monte Carlo iterations per player-market")
    args = ap.parse_args(argv)

    kwargs = dict(demo=True, offline=args.offline, write_html=False,
                  write_data=False, quiet=True)
    if args.n_iter:
        kwargs["n_iter"] = args.n_iter
    artifacts = run(**kwargs)

    logs, store = artifacts["logs"], artifacts["sims_store"]
    n_markets = logs.groupby(["player_id", "market"]).ngroups

    rel = reliability(store, logs)
    print(f"\nplayer-markets: {n_markets}   puntos evaluados: {len(rel)}")
    print("\n=== reliability: P_modelo vs P_empirica (bucket por P_empirica) ===")
    for lo, hi in RELIABILITY_BUCKETS:
        sel = rel[(rel.p_emp >= lo) & (rel.p_emp < hi)]
        if len(sel):
            print(f"  {lo:>5.0%}-{hi:<5.0%}  n={len(sel):5d}  "
                  f"p_model={sel.p_model.mean():.3f}  p_emp={sel.p_emp.mean():.3f}  "
                  f"ratio={sel.p_model.mean() / sel.p_emp.mean():.2f}")
    print(f"  GLOBAL        n={len(rel):5d}  "
          f"ratio={rel.p_model.mean() / rel.p_emp.mean():.2f}")

    qs, shape = quantile_and_shape(store, logs)
    print("\n=== cuantiles: media de (cuantil modelo / cuantil empirico) ===")
    for q in QUANTILES:
        if q in qs:
            print(f"  P{q * 100:>4.0f}  n={len(qs[q]):3d}  ratio={np.mean(qs[q]):.3f}")
    print("\n=== forma ===")
    for key, label in (("std", "desvio"), ("cv", "CV"), ("skew", "sesgo")):
        vals = shape[key]
        if vals:
            print(f"  {label:<7} modelo/empirico: {np.mean(vals):.3f}  (n={len(vals)})")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
