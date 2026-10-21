"""
AltLines — Pickstachio Score and tiers (the single source of truth).

Every priced line gets a 0–100 score and a tier:

    gold   "Pistacho Dorado"   score ≥ 70
    open   "Pistacho Abierto"  50 ≤ score < 70
    shell  "Cáscara Dura"      score < 50

Score = 100 · (0.40·edge + 0.25·tail + 0.20·prob + 0.15·level), each part in [0, 1]:

    edge   (p_model − p_fair) / 0.10      ventaja sobre el precio justo del libro
    tail   (p_model / p_curve − 1) / 3    cuánto le gana la cola a la curva genérica
    prob   p_model / 0.35                 qué tan seguido pega
    level  1 − |anchor_scale − 1| / 0.25  modelo y mercado de acuerdo en el nivel
           (sólo con la mediana anclada al mercado; si no, 0.8 fijo)

The engine stamps these on the value table, the pick log stores them and the
web panel shows them as they come: the browser never recomputes a tier.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

GOLD_MIN = 70
OPEN_MIN = 50

TIER_NAMES = {
    "gold": "Pistacho Dorado",
    "open": "Pistacho Abierto",
    "shell": "Cáscara Dura",
}


def _clamp(v: float) -> float:
    return 0.0 if not math.isfinite(v) else min(1.0, max(0.0, v))


def pistachio_score(p_model: float, p_fair: float, curve_ratio: float | None,
                    anchor_scale: float | None, anchored: bool) -> int:
    edge = _clamp((p_model - p_fair) / 0.10)
    tail = _clamp(((curve_ratio if curve_ratio is not None else 1.0) - 1.0) / 3.0)
    prob = _clamp(p_model / 0.35)
    if anchored and anchor_scale is not None and math.isfinite(anchor_scale):
        level = _clamp(1.0 - abs(anchor_scale - 1.0) / 0.25)
    else:
        level = 0.8
    raw = 100.0 * (0.40 * edge + 0.25 * tail + 0.20 * prob + 0.15 * level)
    return int(math.floor(raw + 0.5))          # same rounding as JS Math.round


def tier_of(score: float) -> str:
    return "gold" if score >= GOLD_MIN else "open" if score >= OPEN_MIN else "shell"


def add_tiers(table: pd.DataFrame, anchored: bool) -> pd.DataFrame:
    """Add `pst_score` and `pst_tier` to a value table (copy)."""
    if table is None or table.empty:
        return table
    out = table.copy()

    def _num(row, col):
        v = row.get(col)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return v if np.isfinite(v) else None

    scores = []
    for _, r in out.iterrows():
        ratio = _num(r, "curve_ratio")
        if ratio is None:
            pc = _num(r, "p_curve")
            ratio = (float(r["p_model"]) / pc) if pc else None
        scores.append(pistachio_score(float(r["p_model"]), float(r["p_fair"]), ratio,
                                      _num(r, "anchor_scale"), anchored))
    out["pst_score"] = scores
    out["pst_tier"] = [tier_of(s) for s in scores]
    return out
