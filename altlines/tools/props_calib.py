"""
Corrección de calibración de las líneas de jugador, por mercado:
P(over) corregida = σ(a + b·logit p), con p = mitad partido simulado + mitad
jugador solo (como en vivo). Se ajusta con la primera mitad de la temporada y
se controla con la segunda (en orden de fecha, sin mirar el futuro).

    python -m altlines.tools.props_backtest --save data/altlines/props_backtest.npz
    python -m altlines.tools.props_calib
"""

from __future__ import annotations

import json

import numpy as np

from altlines import config as cfg

W = 0.5


def _fit(x: np.ndarray, y: np.ndarray, iters: int = 50) -> tuple[float, float]:
    """Regresión logística de una variable (Newton)."""
    a, b = 0.0, 1.0
    for _ in range(iters):
        z = a + b * x
        p = 1 / (1 + np.exp(-z))
        g = np.array([np.sum(p - y), np.sum((p - y) * x)])
        h = p * (1 - p)
        H = np.array([[h.sum(), (h * x).sum()], [(h * x).sum(), (h * x * x).sum()]])
        step = np.linalg.solve(H + 1e-9 * np.eye(2), g)
        a, b = a - step[0], b - step[1]
        if np.abs(step).max() < 1e-8:
            break
    return float(a), float(b)


def main() -> None:
    d = np.load(cfg.DATA_DIR / "props_backtest.npz", allow_pickle=False)
    m, q, y = d["m"], W * d["g"] + (1 - W) * d["s"], d["y"]
    q = np.clip(q, 1e-4, 1 - 1e-4)
    x = np.log(q / (1 - q))
    ll = lambda p, t: float(-(t * np.log(np.clip(p, 1e-4, 1)) + (1 - t) * np.log(np.clip(1 - p, 1e-4, 1))).mean())  # noqa: E731
    out = {}
    print("mercado   n_ajuste  a       b      log-loss control: antes → después · colas <20%: p antes/después/real")
    for mk in sorted(set(m.tolist())):
        idx = np.where(m == mk)[0]
        half = len(idx) // 2
        tr, te = idx[:half], idx[half:]
        a, b = _fit(x[tr], y[tr])
        pc = 1 / (1 + np.exp(-(a + b * x[te])))
        tail = q[te] < 0.2
        print(f"{mk:7s} {len(tr):9d}  {a:+.3f}  {b:.3f}   {ll(q[te], y[te]):.4f} → {ll(pc, y[te]):.4f}"
              f"   {q[te][tail].mean():.3f}/{pc[tail].mean():.3f}/{y[te][tail].mean():.3f}")
        a_all, b_all = _fit(x[idx], y[idx])          # lo que se usa en vivo: toda la temporada
        out[mk] = {"a": round(a_all, 4), "b": round(b_all, 4), "n": int(len(idx))}
    f = cfg.DATA_DIR / "props_calib.json"
    f.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nguardado {f}")


if __name__ == "__main__":
    main()
