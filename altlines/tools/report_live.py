"""Measure the logged AltLines picks against what actually happened.

    python -m altlines.tools.report_live [--game ID] [--db path] [--all-levels]

Unit of account: one PICK = one (game, player, market, side, point), taken from
the FIRST pre-tip run that priced it (its p_model, p_fair, price, book and
top-X% flag). Later pre-tip runs of the same line are only snapshots for CLV;
anything priced after the tip-off is ignored.

  1. Reliability of p_model: bucket the picks by p_model (20-35, 10-20, 5-10,
     2-5 %) and compare the mean p_model with the realised hit rate (Wilson 95 %
     interval). Same table bucketed by p_fair: if p_fair is calibrated and
     p_model is ~1.4× too high, the "edge" is model bias.
  2. ROI per unit staked: the top X% shortlist vs the rest of the board, and
     by Pickstachio tier (Dorado / Abierto / Cáscara) for the lines with EV > 0.
  3. CLV, when a line has more than one pre-tip snapshot: price CLV =
     pick_odds / closing_odds − 1 (> 0 = we got a better price than the close)
     and the drift of the market's p_fair toward our side.

Pushes and voids are excluded from hit rates; they count as 0 in ROI and are
not staked.
"""
from __future__ import annotations

import argparse
import math
import sys

import numpy as np
import pandas as pd

from altlines import picklog

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

BUCKETS = [(0.20, 0.35), (0.10, 0.20), (0.05, 0.10), (0.02, 0.05)]
LINE_KEY = ["game_id", "player_id", "market", "side", "point"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def _pretip(df: pd.DataFrame) -> pd.DataFrame:
    run = pd.to_datetime(df["run_ts"], utc=True, errors="coerce")
    tip = pd.to_datetime(df["tip_off"], utc=True, errors="coerce")
    return df[tip.isna() | (run < tip)].copy()


def picks_from(lines: pd.DataFrame) -> pd.DataFrame:
    """First pre-tip sighting of every line."""
    df = _pretip(lines).sort_values(["run_ts", "id"])
    return df.drop_duplicates(LINE_KEY, keep="first").reset_index(drop=True)


def reliability(picks: pd.DataFrame, col: str) -> pd.DataFrame:
    graded = picks[picks["outcome"].isin(["win", "loss"])]
    out = []
    for lo, hi in BUCKETS:
        b = graded[(graded[col] >= lo) & (graded[col] < hi)]
        n, k = len(b), int((b["outcome"] == "win").sum())
        mean_p = float(b[col].mean()) if n else np.nan
        lo_ci, hi_ci = wilson(k, n)
        out.append({"bucket": f"{lo:.0%}-{hi:.0%}", "n": n, f"mean_{col}": mean_p,
                    "hit_rate": k / n if n else np.nan, "ci95_lo": lo_ci, "ci95_hi": hi_ci,
                    "real_vs_prob": (k / n) / mean_p if n and mean_p else np.nan})
    return pd.DataFrame(out)


def roi(picks: pd.DataFrame) -> pd.DataFrame:
    staked = picks[picks["outcome"].isin(["win", "loss", "push"])]
    out = []
    for name, grp in (("top", staked[staked["in_top"] == 1]),
                      ("resto", staked[staked["in_top"] != 1]),
                      ("resto EV>0", staked[(staked["in_top"] != 1) & (staked["ev"] > 0)]),
                      ("todo", staked)):
        n = len(grp)
        pnl = float(grp["pnl"].sum()) if n else 0.0
        out.append({"grupo": name, "n": n, "ganadas": int((grp["outcome"] == "win").sum()),
                    "p_model_medio": grp["p_model"].mean() if n else np.nan,
                    "ev_medio": grp["ev"].mean() if n else np.nan,
                    "pnl_u": round(pnl, 2), "roi": pnl / n if n else np.nan})
    return pd.DataFrame(out)


TIER_ORDER = [("gold", "Pistacho Dorado"), ("open", "Pistacho Abierto"), ("shell", "Cáscara Dura")]


def by_tier(picks: pd.DataFrame) -> pd.DataFrame:
    """Record and ROI per Pickstachio tier, over the value lines (EV > 0)."""
    if "pst_tier" not in picks:
        return pd.DataFrame()
    staked = picks[picks["outcome"].isin(["win", "loss", "push"]) & (picks["ev"] > 0)]
    out = []
    for key, name in TIER_ORDER:
        g = staked[staked["pst_tier"] == key]
        n, k = len(g), int((g["outcome"] == "win").sum())
        graded = int(g["outcome"].isin(["win", "loss"]).sum())
        pnl = float(g["pnl"].sum()) if n else 0.0
        out.append({"categoría": name, "n": n, "ganadas": k,
                    "perdidas": int((g["outcome"] == "loss").sum()),
                    "hit_rate": k / graded if graded else np.nan,
                    "p_model_medio": g["p_model"].mean() if n else np.nan,
                    "pnl_u": round(pnl, 2), "roi": pnl / n if n else np.nan})
    return pd.DataFrame(out)


def clv(lines: pd.DataFrame) -> pd.DataFrame:
    """Pick vs closing (last pre-tip) snapshot, for lines seen more than once."""
    df = _pretip(lines)
    if df.empty:
        return pd.DataFrame()
    df["snap"] = df["snapshot_ts"].fillna(df["run_ts"])
    df = df.sort_values(["run_ts", "id"])
    n_snaps = df.groupby(LINE_KEY)["snap"].transform("nunique")
    df = df[n_snaps > 1]
    if df.empty:
        return pd.DataFrame()
    first = df.groupby(LINE_KEY).head(1).set_index(LINE_KEY)
    # Close = the best price on the latest snapshot of that line.
    last_snap = df.groupby(LINE_KEY)["snap"].transform("max")
    close = (df[df["snap"] == last_snap].sort_values("decimal_odds")
             .groupby(LINE_KEY).tail(1).set_index(LINE_KEY))
    out = first[["in_top", "decimal_odds", "p_fair", "p_model"]].join(
        close[["decimal_odds", "p_fair"]], rsuffix="_close")
    out["clv_price"] = out["decimal_odds"] / out["decimal_odds_close"] - 1.0
    out["p_fair_drift"] = out["p_fair_close"] - out["p_fair"]
    return out.reset_index()


def _fmt(df: pd.DataFrame) -> str:
    return df.to_string(index=False, float_format=lambda v: f"{v:.3f}")


def report(db_path=None, game: str | None = None, all_levels: bool = False) -> dict:
    lines = picklog.load_lines(db_path)
    if lines.empty:
        print(f"No hay picks en {db_path or picklog.PICKS_DB}. Corré el modo real primero.")
        return {}
    if game:
        lines = lines[lines["game_id"].astype(str) == str(game)]
    if not all_levels and "level_ok" in lines:
        lines = lines[lines["level_ok"].fillna(1).astype(int) == 1]
    picks = picks_from(lines)
    n_set = int(picks["outcome"].notna().sum())
    print(f"AltLines — validación real  ({picklog.PICKS_DB if db_path is None else db_path})")
    print(f"{lines['run_id'].nunique()} corridas · {lines['game_id'].nunique()} partidos · "
          f"{len(picks)} picks únicos · {n_set} liquidados "
          f"({int((picks['outcome'] == 'void').sum())} void, "
          f"{int((picks['outcome'] == 'push').sum())} push) · "
          f"{int(picks['outcome'].isna().sum())} pendientes"
          f"{'' if all_levels else '   [solo level_ok; --all-levels para todo]'}")

    res = {"picks": picks}
    if n_set:
        res["rel_model"] = reliability(picks, "p_model")
        res["rel_fair"] = reliability(picks, "p_fair")
        res["roi"] = roi(picks)
        print("\n1a. Reliability de p_model (real_vs_prob = frecuencia real / p_model medio; 1 = calibrado, <1 = sobreestima)")
        print(_fmt(res["rel_model"]))
        print("\n1b. Reliability de p_fair (el mercado sin vig)")
        print(_fmt(res["rel_fair"]))
        print("\n2. ROI por unidad apostada (push = 0, void fuera)")
        print(_fmt(res["roi"]))
        res["tiers"] = by_tier(picks)
        if not res["tiers"].empty:
            print("\n2b. Por categoría Pickstachio (líneas con EV > 0)")
            print(_fmt(res["tiers"]))
    else:
        print("\nNada liquidado todavía: corré `python -m altlines.tools.settle` cuando terminen los partidos.")

    c = clv(lines)
    res["clv"] = c
    print("\n3. CLV (pick = primer snapshot pre-tip, cierre = último)")
    if c.empty:
        print("   Sin líneas con más de un snapshot: corré el modo real otra vez cerca del tip.")
    else:
        g = c.assign(grupo=np.where(c["in_top"] == 1, "top", "resto")).groupby("grupo").agg(
            n=("clv_price", "size"), clv_price_medio=("clv_price", "mean"),
            pct_clv_pos=("clv_price", lambda s: float((s > 0).mean())),
            p_fair_drift_medio=("p_fair_drift", "mean")).reset_index()
        res["clv_summary"] = g
        print(_fmt(g))
    return res


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Reporte de validación real de AltLines")
    p.add_argument("--game", default=None, help="solo este ESPN game id")
    p.add_argument("--db", default=None, help=f"ruta a la base (default {picklog.PICKS_DB})")
    p.add_argument("--all-levels", action="store_true",
                   help="incluir líneas fuera del tablero (level_ok = 0)")
    a = p.parse_args(argv)
    report(a.db, a.game, a.all_levels)


if __name__ == "__main__":
    main()
