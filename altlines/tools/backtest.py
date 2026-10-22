"""Walk-forward backtest of the engine's TAIL calibration on real ESPN logs.

    python -m altlines.tools.backtest [--season 2026] [--teams cached|ALL|BOS,DET]
                                      [--min-games 15] [--min-minutes 20]
                                      [--n-iter 10000] [--workers 4] [--no-save]

The question: when the engine says P(over 35.5 points) = 6 %, does it happen
6 % of the time? Picks need weeks to settle; finished seasons are already
there. For every rotation player and every game from his `--min-games`-th of
`--season` on:

  1. history = his games BEFORE that date (that season + the previous one),
     with exactly the live pipeline's treatment: playoffs out, the minutes
     filter (`LIVE_MIN_MINUTES_FRAC`) computed from that history only, the
     offseason half-life guard (`LIVE_STALE_DAYS`);
  2. project_player + calibrate_player (the live configuration) → sims;
  3. P(value > t) for a fixed ladder of thresholds per market (`LADDERS`);
  4. the outcome: what he really did that night.

Two references are priced on the same lines:
  * naive — Normal(mean, sd) of the same history;
  * book  — the generic curve the thesis says books use (one σ per market,
            simulation.casino_curve_prob), centred on the model's median on the
            .5 grid: same level as the model, only the SHAPE differs.

Then: reliability by predicted-probability bucket, Brier, log-loss, splits by
market and profile; a calibration map p_model → p_real per market (isotonic and
Platt, picked on a time hold-out) saved to `TAIL_CALIBRATION_JSON`, which
ev.build_value_table applies when `APPLY_TAIL_CALIBRATION` is on.

Costs NO Odds API credits: only ESPN's public JSON, cached forever for
finished seasons (`data/altlines/cache/gamelog/`).

Caveat: the live board ANCHORS each distribution's median on the market's main
line; there is no historical main line here, so this measures the unanchored
engine. The shape — what anchoring keeps — is the part being tested.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats

from altlines import config as cfg
from altlines import context as ctxmod
from altlines import live
from altlines.slate import NBA_TEAMS
from altlines.simulation import (
    calibrate_player,
    casino_curve_prob,
    player_rng,
    profile_label,
    project_player,
    summarize,
)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

# Over-thresholds priced per market: the span of a real alt tray, from the
# main-line zone of a role player out to the deep alts of a star.
LADDERS: dict[str, list[float]] = {
    "points":   [10.5, 15.5, 20.5, 25.5, 30.5, 35.5, 40.5, 45.5],
    "rebounds": [3.5, 5.5, 7.5, 9.5, 11.5, 13.5, 15.5, 17.5],
    "assists":  [2.5, 4.5, 6.5, 8.5, 10.5, 12.5, 14.5],
    "threes":   [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5],
    "pra":      [15.5, 20.5, 25.5, 30.5, 35.5, 40.5, 45.5, 50.5, 55.5, 60.5, 65.5],
}
# Box-score column holding each market's outcome.
_OUTCOME = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "tpm"}

# (lo, hi] buckets of the predicted probability, deepest last.
BUCKETS = [(0.50, 1.00), (0.35, 0.50), (0.20, 0.35), (0.10, 0.20),
           (0.05, 0.10), (0.02, 0.05), (0.00, 0.02)]
_EPS = 1e-4
PREDICTORS = (("p_model", "motor"), ("p_naive", "Normal ingenua"),
              ("p_book", "curva libro"))


# ══════════════════════════════════════════════════════════════════════════════
# 1. Players & logs
# ══════════════════════════════════════════════════════════════════════════════

def _cached_teams() -> list[str]:
    return sorted(p.stem for p in (live.CACHE_DIR / "roster").glob("*.json"))


def load_players(teams: list[str], use_cache_only: bool) -> list[dict]:
    players, seen = [], set()
    for tr in teams:
        roster = (live._cache_get("roster", tr, None) if use_cache_only else None) \
            or live.fetch_roster(tr)
        for a in roster or []:
            if a["athlete_id"] not in seen:
                seen.add(a["athlete_id"])
                players.append(a)
    return players


def _season_games(athlete_id: str, season: int) -> list[dict]:
    games = live.fetch_athlete_games(athlete_id, season, current=False)
    return sorted((g for g in games if g.get("min") and g["min"] > 0 and g.get("date")),
                  key=lambda g: g["date"])


# ══════════════════════════════════════════════════════════════════════════════
# 1b. Context variants (altlines/context.py)
# ══════════════════════════════════════════════════════════════════════════════

# Built once per worker process from the disk cache (no pickling of big frames).
_CTX: dict = {}


def _init_context() -> None:
    if not _CTX:
        games = ctxmod.load_cached_games()
        _CTX["model"] = ctxmod.ContextModel(games)
        _CTX["book"] = ctxmod.TeamBook(games)


def parse_variant(name: str) -> tuple[tuple[str, ...], float | None]:
    """'base' | 'rest' | 'home' | 'opp' | 'ctx' (= rest+home+opp) | 'inj' |
    'inj@0.3' | 'all' (= ctx+inj) | 'lvl@1.02' (placebo: the same multiplier
    for every game), combinable with '+'.
    Returns (context factors used, injury redistribution or None)."""
    use: list[str] = []
    redist = None
    for tok in name.split("+"):
        tok = tok.strip()
        if tok in ("base", ""):
            continue
        if tok == "ctx":
            use += ["rest", "home", "opp"]
        elif tok == "all":
            use += ["rest", "home", "opp"]
            redist = cfg.INJ_REDIST
        elif tok.startswith("inj"):
            redist = float(tok.split("@", 1)[1]) if "@" in tok else cfg.INJ_REDIST
        elif tok in ("rest", "home", "opp"):
            use.append(tok)
        elif tok.startswith("lvl@"):          # placebo: a constant level shift
            float(tok[4:])
            use.append(tok)
        else:
            raise ValueError(f"variante desconocida: {tok}")
    return tuple(dict.fromkeys(use)), redist


def vcol(name: str) -> str:
    return "p_" + name.replace("+", "_").replace("@", "").replace(".", "")


def _target_context(a: dict, season: int, target: dict, prior: list[dict],
                    variants: list[str]) -> tuple[dict, dict]:
    """{variant: {market: mult}} for one game, plus diagnostics."""
    from altlines.slate import normalize_tricode
    parsed = {v: parse_variant(v) for v in variants}
    day = ctxmod._day(target["date"])
    rest_days = day - ctxmod._day(prior[-1]["date"]) if prior else None
    opp = normalize_tricode(target.get("opponent"))
    home = int(target.get("home_away") or 0)
    model = _CTX["model"]
    f = {m: model.factors(a["athlete_id"], m, season, day, opp, home, rest_days)
         for m in LADDERS}
    for use, _ in parsed.values():
        for k in use:
            if k.startswith("lvl@"):
                for m in LADDERS:
                    f[m][k] = float(k[4:])
    diag = {"n_out": 0, "inj_boost_min": 0.0}
    inj: dict[float, float] = {}
    if any(r is not None for _, r in parsed.values()):
        d_prior = [ctxmod._day(g["date"]) for g in prior]
        cut = d_prior[-3] if len(d_prior) >= 3 else (d_prior[0] if d_prior else day)
        out, active = _CTX["book"].game_context(
            a["athlete_id"], normalize_tricode(target.get("team")), season, day,
            str(target["event_id"]), cut)
        typ = float(np.mean([g["min"] for g in prior[-10:]])) if prior else 0.0
        active = [x for x in active if x[0] != a["athlete_id"]]
        active.append((a["athlete_id"], typ, ctxmod._pos(a.get("position"))))
        diag["n_out"] = len(out)
        for _, r in parsed.values():
            if r is None or r in inj:
                continue
            boost = ctxmod.redistribute(out, active, a["athlete_id"], redist=r) if typ > 0 else 0.0
            inj[r] = float(np.clip((typ + boost) / typ, 1.0, cfg.INJ_MULT_MAX)) if typ > 0 else 1.0
            if r == cfg.INJ_REDIST:
                diag["inj_boost_min"] = boost
    mults = {}
    for v, (use, r) in parsed.items():
        mults[v] = {m: (ctxmod.combine(f[m], use) if use else 1.0) * (inj[r] if r is not None else 1.0)
                    for m in LADDERS}
    diag["f"] = f
    return mults, diag


# ══════════════════════════════════════════════════════════════════════════════
# 2. One player, walk-forward
# ══════════════════════════════════════════════════════════════════════════════

def _outcome(g: dict, market: str) -> float:
    if market == "pra":
        return float(g["pts"] + g["reb"] + g["ast"])
    return float(g[_OUTCOME[market]])


def backtest_player(job: dict) -> list[dict]:
    """Every (game, market, threshold) prediction for one player."""
    a, season = job["player"], job["season"]
    n_iter, seed, min_games = job["n_iter"], job["seed"], job["min_games"]
    # Rotation filter here, not in the parent: the ESPN downloads then run in
    # parallel too.
    cur = _season_games(a["athlete_id"], season)
    if len(cur) < min_games + 5 or             float(np.median([g["min"] for g in cur])) < job["min_minutes"]:
        return []
    prev = _season_games(a["athlete_id"], season - 1)
    pid = f"ESPN-{a['athlete_id']}"
    rows: list[dict] = []
    for i in range(min_games - 1, len(cur)):
        target = cur[i]
        prior = prev + cur[:i]
        hist = live.games_to_logs(prior, pid, a["name"], a["team"], a["position"], "bt")
        if not hist:
            continue
        logs = pd.DataFrame(hist)
        logs["date"] = pd.to_datetime(logs["date"])
        typical = float(np.median([g["min"] for g in prior]))
        short = target["min"] < cfg.LIVE_MIN_MINUTES_FRAC * typical
        halflife = 6.0
        gap = (pd.Timestamp(target["date"]) - logs["date"].max()).days
        if gap > cfg.LIVE_STALE_DAYS:
            halflife = max(halflife, cfg.LIVE_STALE_HALFLIFE)
        variants = job.get("variants") or []
        if variants:
            _init_context()
            vmults, diag = _target_context(a, season, target, prior, variants)
        for market, ladder in LADDERS.items():
            spec = cfg.get_spec(market)
            grp = logs[logs["market"] == market]
            if len(grp) < 5:
                continue
            # Same RNG stream for every variant (common random numbers): the
            # differences between variants are the context, not Monte Carlo noise.
            stream = f"{market}|{target['date']}"
            try:
                params = project_player(grp, spec, halflife=halflife)
                sims = calibrate_player(params, spec, n_iter=n_iter,
                                        rng=player_rng(seed, pid, stream))
                vsims: dict[str, np.ndarray] = {}
                by_mult: dict[float, np.ndarray] = {1.0: sims}
                for v in variants:
                    mlt = round(float(vmults[v][market]), 6)
                    if mlt not in by_mult:
                        pv = project_player(grp, spec, halflife=halflife, level_mult=mlt)
                        by_mult[mlt] = calibrate_player(pv, spec, n_iter=n_iter,
                                                        rng=player_rng(seed, pid, stream))
                    vsims[v] = by_mult[mlt]
            except Exception:  # noqa: BLE001 - one bad history must not kill the run
                continue
            summ = summarize(sims)
            profile = profile_label(summ, spec)
            vals = grp["value"].to_numpy(float)
            mu, sd = float(vals.mean()), float(max(vals.std(ddof=1), 0.5))
            main = float(np.floor(np.median(sims)) + 0.5)
            y = _outcome(target, market)
            for t in ladder:
                row = {
                    "player_id": pid, "player": a["name"], "date": target["date"],
                    "market": market, "threshold": t, "profile": profile,
                    "n_hist": len(grp), "short_minutes": bool(short),
                    "p_model": float((sims > t).mean()),
                    "p_naive": float(stats.norm.sf(t, mu, sd)),
                    "p_book": casino_curve_prob(t, main, spec),
                    "hit": int(y > t), "value": y,
                }
                if variants:
                    for v in variants:
                        row[vcol(v)] = float((vsims[v] > t).mean())
                        row["m_" + vcol(v)[2:]] = float(vmults[v][market])
                    f = diag["f"][market]
                    row.update({"n_out": diag["n_out"],
                                "inj_boost_min": round(diag["inj_boost_min"], 2),
                                "f_rest": round(f["rest"], 4), "f_home": round(f["home"], 4),
                                "f_opp": round(f["opp"], 4)})
                rows.append(row)
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 3. Scoring
# ══════════════════════════════════════════════════════════════════════════════

def _clip(p) -> np.ndarray:
    return np.clip(np.asarray(p, float), _EPS, 1 - _EPS)


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - y) ** 2))


def logloss(p, y) -> float:
    p = _clip(p)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def bucket_label(lo: float, hi: float) -> str:
    if lo == 0:
        return f"<{hi:.0%}"
    return f"{lo:.0%}-{hi:.0%}"


def reliability(df: pd.DataFrame, col: str) -> pd.DataFrame:
    out = []
    for lo, hi in BUCKETS:
        m = (df[col] > lo) & (df[col] <= hi) if lo > 0 else (df[col] <= hi)
        sub = df[m]
        if sub.empty:
            out.append({"bucket": bucket_label(lo, hi), "n": 0, "p_pred": np.nan,
                        "freq_obs": np.nan, "ratio": np.nan})
            continue
        pp, fo = sub[col].mean(), sub["hit"].mean()
        out.append({"bucket": bucket_label(lo, hi), "n": len(sub),
                    "p_pred": pp, "freq_obs": fo,
                    "ratio": pp / fo if fo > 0 else np.inf})
    return pd.DataFrame(out)


def _fmt_rel(rel: pd.DataFrame) -> str:
    lines = [f"  {'bucket':>8} {'n':>7} {'p pred':>8} {'f obs':>8} {'pred/obs':>9}"]
    for r in rel.itertuples():
        if not r.n:
            lines.append(f"  {r.bucket:>8} {0:>7}")
            continue
        ratio = "inf" if not np.isfinite(r.ratio) else f"{r.ratio:.2f}x"
        lines.append(f"  {r.bucket:>8} {r.n:>7} {r.p_pred:>8.2%} {r.freq_obs:>8.2%} {ratio:>9}")
    return "\n".join(lines)


def score_table(df: pd.DataFrame, by: str | None, cols=("p_model", "p_naive", "p_book")) -> pd.DataFrame:
    groups = [("TOTAL", df)] if by is None else list(df.groupby(by))
    rows = []
    for key, g in groups:
        row = {"group": key, "n": len(g), "base_rate": g["hit"].mean()}
        for c in cols:
            row[f"brier_{c}"] = brier(g[c], g["hit"])
            row[f"ll_{c}"] = logloss(g[c], g["hit"])
        rows.append(row)
    return pd.DataFrame(rows)


def _fmt_scores(t: pd.DataFrame, cols=("p_model", "p_naive", "p_book")) -> str:
    names = {"p_model": "motor", "p_naive": "naive", "p_book": "libro", "p_cal": "motor+cal"}
    head = f"  {'grupo':<10} {'n':>7} {'base':>6} " + " ".join(
        f"{'Brier ' + names[c]:>13}" for c in cols) + " " + " ".join(
        f"{'LL ' + names[c]:>13}" for c in cols)
    lines = [head]
    for r in t.itertuples(index=False):
        d = r._asdict()
        lines.append(f"  {str(d['group']):<10} {d['n']:>7} {d['base_rate']:>6.1%} "
                     + " ".join(f"{d['brier_' + c]:>13.5f}" for c in cols) + " "
                     + " ".join(f"{d['ll_' + c]:>13.5f}" for c in cols))
    return "\n".join(lines)


def tail_ratio_by(df: pd.DataFrame, by: str, col: str = "p_model") -> pd.DataFrame:
    """pred/obs in the tail buckets, per group — the one-glance overstatement."""
    rows = []
    for key, g in df.groupby(by):
        row = {"group": key}
        for lo, hi in BUCKETS[3:]:
            m = (g[col] > lo) & (g[col] <= hi) if lo > 0 else (g[col] <= hi)
            sub = g[m]
            fo = sub["hit"].mean() if len(sub) else np.nan
            row[bucket_label(lo, hi)] = (f"{sub[col].mean() / fo:.2f}x (n={len(sub)})"
                                         if len(sub) and fo > 0 else f"— (n={len(sub)})")
        rows.append(row)
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 4. Calibration map p_model → p_real
# ══════════════════════════════════════════════════════════════════════════════

def _logit(p):
    p = _clip(p)
    return np.log(p / (1 - p))


def fit_platt(p, y) -> dict:
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(C=1e6, max_iter=1000)
    lr.fit(_logit(p).reshape(-1, 1), y)
    return {"a": float(lr.coef_[0][0]), "b": float(lr.intercept_[0])}


def fit_isotonic(p, y, max_knots: int = 60) -> dict:
    from sklearn.isotonic import IsotonicRegression
    iso = IsotonicRegression(y_min=_EPS, y_max=1 - _EPS, increasing=True,
                             out_of_bounds="clip")
    iso.fit(np.asarray(p, float), np.asarray(y, float))
    x, yy = iso.X_thresholds_, iso.y_thresholds_
    if len(x) > max_knots:                       # thin the step function
        idx = np.unique(np.linspace(0, len(x) - 1, max_knots).round().astype(int))
        x, yy = x[idx], yy[idx]
    return {"x": [round(float(v), 6) for v in x], "y": [round(float(v), 6) for v in yy]}


def apply_map(entry: dict, p) -> np.ndarray:
    """Shared with ev.py (which re-implements the two lines to stay import-light)."""
    p = np.asarray(p, float)
    if entry["method"] == "isotonic":
        iso = entry["isotonic"]
        return np.interp(p, iso["x"], iso["y"])
    pl = entry["platt"]
    return 1.0 / (1.0 + np.exp(-(pl["a"] * _logit(p) + pl["b"])))


def fit_calibration(df: pd.DataFrame, holdout_frac: float = 0.4) -> tuple[dict, pd.DataFrame]:
    """Per market: fit isotonic and Platt on the EARLY games, score both on the
    LATE ones (time split → no look-ahead), keep the lower log-loss, refit the
    winner on everything. Returns (json payload, hold-out comparison)."""
    cut = df["date"].quantile(1 - holdout_frac, interpolation="nearest")
    out, rows = {}, []
    for market, g in df.groupby("market"):
        tr, te = g[g["date"] < cut], g[g["date"] >= cut]
        if len(tr) < 500 or len(te) < 200 or tr["hit"].nunique() < 2:
            continue
        cand = {"isotonic": {"isotonic": fit_isotonic(tr["p_model"], tr["hit"])},
                "platt": {"platt": fit_platt(tr["p_model"], tr["hit"])}}
        scores = {}
        for meth, e in cand.items():
            pc = apply_map({"method": meth, **e}, te["p_model"])
            scores[meth] = (logloss(pc, te["hit"]), brier(pc, te["hit"]))
        best = min(scores, key=lambda k: scores[k][0])
        rows.append({"market": market, "n_test": len(te),
                     "ll_raw": logloss(te["p_model"], te["hit"]),
                     "ll_isotonic": scores["isotonic"][0], "ll_platt": scores["platt"][0],
                     "brier_raw": brier(te["p_model"], te["hit"]),
                     "brier_isotonic": scores["isotonic"][1], "brier_platt": scores["platt"][1],
                     "chosen": best})
        out[market] = {
            "method": best,
            "isotonic": fit_isotonic(g["p_model"], g["hit"]),
            "platt": fit_platt(g["p_model"], g["hit"]),
            "n": int(len(g)),
        }
    return out, pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════════════
# 4b. Context variants: before / after
# ══════════════════════════════════════════════════════════════════════════════

def _delta_line(name: str, g: pd.DataFrame, col: str) -> str:
    b_ll, v_ll = logloss(g["p_model"], g["hit"]), logloss(g[col], g["hit"])
    b_br, v_br = brier(g["p_model"], g["hit"]), brier(g[col], g["hit"])
    # Paired per-row difference → standard error of the log-loss change.
    y = g["hit"].to_numpy(float)
    pb, pv = _clip(g["p_model"]), _clip(g[col])
    d = -(y * np.log(pv) + (1 - y) * np.log(1 - pv)) + (y * np.log(pb) + (1 - y) * np.log(1 - pb))
    # Rows of one player-game-market are correlated: cluster the SE by them.
    cl = pd.Series(d).groupby([g["player_id"].to_numpy(), g["date"].to_numpy(),
                               g["market"].to_numpy()]).sum()
    se = float(cl.std(ddof=1) * np.sqrt(len(cl)) / len(d)) if len(cl) > 1 else np.nan
    return (f"  {name:<14} {len(g):>8} {b_ll:>9.5f} {v_ll:>9.5f} {v_ll - b_ll:>+10.5f} "
            f"{se:>8.5f} {b_br:>9.5f} {v_br:>9.5f} {v_br - b_br:>+10.6f}")


def compare_variants(df: pd.DataFrame, variants: list[str], holdout_frac: float = 0.4) -> str:
    """Log-loss / Brier of every variant against the base engine (p_model):
    total, per market, early vs late games, games with teammates out, and the
    tail reliability."""
    head = (f"  {'grupo':<14} {'n':>8} {'LL base':>9} {'LL var':>9} {'ΔLL':>10} "
            f"{'±SE':>8} {'Br base':>9} {'Br var':>9} {'ΔBrier':>10}")
    cut = df["date"].quantile(1 - holdout_frac, interpolation="nearest")
    out = []
    for v in variants:
        col = vcol(v)
        if col not in df.columns or v == "base":
            continue
        mcol = "m_" + col[2:]
        lines = [f"── Variante {v} ({col}) vs motor base ──────────────────────", head,
                 _delta_line("TOTAL", df, col),
                 _delta_line("1º 60% fechas", df[df["date"] < cut], col),
                 _delta_line("últ. 40%", df[df["date"] >= cut], col)]
        for mk, g in df.groupby("market"):
            lines.append(_delta_line(mk, g, col))
        if "n_out" in df.columns:
            g = df[df["n_out"] > 0]
            if len(g):
                lines.append(_delta_line("con OUT", g, col))
        if mcol in df.columns:
            m = df[mcol]
            lines.append(f"  multiplicador: media {m.mean():.4f}, p05 {m.quantile(.05):.3f}, "
                         f"p95 {m.quantile(.95):.3f}, ≠1 en {(m.sub(1).abs() > 1e-9).mean():.0%} de filas")
        rb, rv = reliability(df, "p_model"), reliability(df, col)
        lines.append("  pred/obs por bucket   " + "  ".join(
            f"{a.bucket}: {a.ratio:.2f}→{b.ratio:.2f}" for a, b in zip(rb.itertuples(), rv.itertuples())
            if a.n and b.n))
        out.append("\n".join(lines))
    return "\n\n".join(out)


# ══════════════════════════════════════════════════════════════════════════════
# 5. CLI
# ══════════════════════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--season", type=int, default=2026,
                   help="ESPN season (end year) to backtest; 2026 = 2025-26")
    p.add_argument("--teams", default="cached",
                   help="'cached' (rosters in data/altlines/cache/roster), 'ALL', or BOS,DET,…")
    p.add_argument("--min-games", type=int, default=15,
                   help="first predicted game = the player's Nth of the season")
    p.add_argument("--min-minutes", type=float, default=20.0,
                   help="rotation filter: median minutes in the season")
    p.add_argument("--n-iter", type=int, default=10_000)
    p.add_argument("--seed", type=int, default=cfg.RANDOM_SEED)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--conditional", action="store_true",
                   help="drop target games under the minutes floor (injury exits)")
    p.add_argument("--no-save", action="store_true",
                   help="do not write tail_calibration.json / the predictions file")
    p.add_argument("--variants", default="",
                   help="context variants to measure against the base engine, e.g. "
                        "rest,home,opp,ctx,inj,inj@0.3,all (altlines/context.py). With "
                        "variants the calibration map is NOT refitted; predictions go to "
                        "backtest_context_predictions.csv.gz")
    p.add_argument("--rescore", default="",
                   help="re-score a saved context predictions file (no simulation)")
    args = p.parse_args(argv)
    t0 = time.time()
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for v in variants:
        parse_variant(v)                      # fail fast on a typo
    if args.rescore:
        df = pd.read_csv(args.rescore, parse_dates=["date"])
        if not variants:
            variants = [c[2:] for c in df.columns
                        if c.startswith("p_") and c not in ("p_model", "p_naive", "p_book", "p_cal")]
            # column names lost '+', '@' and '.': print them as stored
            df = df.rename(columns={f"p_{v}": vcol(v) for v in variants})
        if args.conditional:
            df = df[~df["short_minutes"]].reset_index(drop=True)
        print(compare_variants(df, variants))
        return 0

    if args.teams.lower() == "cached":
        teams, cache_only = _cached_teams(), True
    elif args.teams.upper() == "ALL":
        teams, cache_only = [t[0] for t in NBA_TEAMS], False
    else:
        teams, cache_only = [t.strip().upper() for t in args.teams.split(",") if t.strip()], False
    players = load_players(teams, cache_only)
    print(f"[1/4] {len(players)} jugadores en {len(teams)} rosters ({', '.join(teams)})")

    jobs = [{"player": a, "season": args.season, "n_iter": args.n_iter,
             "seed": args.seed, "min_games": args.min_games,
             "min_minutes": args.min_minutes, "variants": variants} for a in players]
    print(f"[2/4] Walk-forward desde el partido {args.min_games} de {args.season} "
          f"(historia {args.season - 1}+{args.season}, sólo jugadores con ≥"
          f"{args.min_games + 5} PJ y mediana ≥{args.min_minutes:g} min), "
          f"{args.n_iter:,} sims c/u, {args.workers} procesos…")
    rows: list[dict] = []
    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            for k, r in enumerate(ex.map(backtest_player, jobs), 1):
                rows += r
                if k % 50 == 0:
                    print(f"      {k}/{len(jobs)} jugadores ({time.time() - t0:.0f}s)")
    else:
        for k, j in enumerate(jobs, 1):
            rows += backtest_player(j)
            if k % 50 == 0:
                print(f"      {k}/{len(jobs)} jugadores ({time.time() - t0:.0f}s)")
    if not rows:
        print("Nada que backtestear.")
        return 1
    df = pd.DataFrame(rows)
    n_players = df["player_id"].nunique()
    df["date"] = pd.to_datetime(df["date"])
    n_short = int(df.drop_duplicates(["player_id", "date"])["short_minutes"].sum())
    if args.conditional:
        df = df[~df["short_minutes"]].reset_index(drop=True)
    n_pg = df[["player_id", "date"]].drop_duplicates().shape[0]
    print(f"      {n_players} jugadores de rotación, {len(df):,} predicciones, {n_pg:,} partidos-jugador "
          f"({n_short} con minutos bajo el piso {'excluidos' if args.conditional else 'incluidos'})")

    print("\n[3/4] Calibración (P(valor > umbral), lado OVER)\n")
    for col, name in PREDICTORS:
        print(f"── Reliability — {name} ({col}) ─────────────────────────")
        print(_fmt_rel(reliability(df, col)))
        print()
    print("── Brier / log-loss (menor = mejor) ─────────────────────────")
    print(_fmt_scores(score_table(df, None)))
    print(_fmt_scores(score_table(df, "market")).split("\n", 1)[1])
    print(_fmt_scores(score_table(df, "profile")).split("\n", 1)[1])
    print("\n── Motor: pred/obs en la cola, por mercado ───────────────────")
    print(tail_ratio_by(df, "market").to_string(index=False))
    print("\n── Motor: pred/obs en la cola, por perfil ────────────────────")
    print(tail_ratio_by(df, "profile").to_string(index=False))
    print("\n── Normal ingenua: pred/obs en la cola, por mercado ──────────")
    print(tail_ratio_by(df, "market", "p_naive").to_string(index=False))

    if variants:
        print("\n[4/4] Contexto: cada variante contra el motor base (mismas sims, CRN)\n")
        print(compare_variants(df, variants))
        if not args.no_save:
            pred_path = cfg.DATA_DIR / "backtest_context_predictions.csv.gz"
            df.to_csv(pred_path, index=False, compression="gzip")
            print(f"\nGuardado: {pred_path.relative_to(cfg.ROOT)}")
        print(f"Listo en {time.time() - t0:.0f}s.")
        return 0

    print("\n[4/4] Mapa de calibración p_model → p_real (isotónica vs Platt, hold-out temporal)")
    maps, hold = fit_calibration(df)
    with pd.option_context("display.float_format", "{:.5f}".format):
        print(hold.to_string(index=False))
    if maps:
        df["p_cal"] = df["p_model"]
        for market, e in maps.items():
            m = df["market"] == market
            df.loc[m, "p_cal"] = apply_map(e, df.loc[m, "p_model"])
        print("\n── Reliability — motor calibrado (in-sample, sólo para ver la forma) ──")
        print(_fmt_rel(reliability(df, "p_cal")))

    if not args.no_save:
        payload = {
            "version": 1,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "description": "p_model (P over, unanchored engine) -> observed frequency, "
                           "walk-forward on ESPN logs; see altlines/tools/backtest.py",
            "source": {"season": args.season, "teams": teams, "players": int(n_players),
                       "predictions": int(len(df)), "player_games": int(n_pg),
                       "n_iter": args.n_iter, "min_games": args.min_games,
                       "min_minutes": args.min_minutes, "conditional": args.conditional},
            "holdout": hold.round(6).to_dict(orient="records"),
            "markets": maps,
        }
        cfg.TAIL_CALIBRATION_JSON.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        pred_path = cfg.DATA_DIR / "backtest_predictions.csv.gz"
        df.to_csv(pred_path, index=False, compression="gzip")
        print(f"\nGuardado: {cfg.TAIL_CALIBRATION_JSON.relative_to(cfg.ROOT)}  "
              f"+ {pred_path.relative_to(cfg.ROOT)}")
    print(f"Listo en {time.time() - t0:.0f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
