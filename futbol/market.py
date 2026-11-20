"""
Futbol — ancla del mercado del partido (1X2 y goles totales).

Idea: el precio del partido resume TODO lo que el mercado sabe (lesiones,
rotación, motivación, clima) en dos números, los goles esperados de cada
equipo. Un equipo que el mercado ve más fuerte de lo que dice nuestro modelo
de remates va a rematar más. Se corrige el volumen de remates así:

    log λ_remates' = log λ_remates + β1·log(g_mkt / ĝ) + β2·log(g_mkt_rival / ĝ_rival)

  g_mkt  goles esperados del equipo según el mercado (sale del 1X2 y, si hay,
         de las líneas de goles: Poisson independiente que mejor las reproduce);
  ĝ      goles esperados según un modelo de goles hecho igual que el de remates
         (ataque, defensa, localía, con recencia) — "lo que ya sabíamos";
  β1, β2 se miden con historia: cierres de football-data.co.uk (gratis) para
         los partidos de ESPN desde 2025. β2 suele ser negativo: el que va a
         estar abajo en el marcador remata más que lo que dice su fuerza.

    python -m futbol.market            # mide β por liga (fuera de muestra) y los guarda

En vivo, g_mkt sale de OddsPapi (la misma consulta que trae los remates: no
gasta cuotas extra).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time

import httpx
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import nbinom, poisson

from futbol import config as cfg
from futbol import model as fm

FDCO_DIR = cfg.CACHE_DIR / "fdco"
ANCHOR_FILE = cfg.DATA_DIR / "market_anchor.json"
FDCO_FILES = {
    "arg": ["new/ARG.csv"],
    "bra": ["new/BRA.csv"],
    "eng": ["mmz4281/2425/E0.csv", "mmz4281/2526/E0.csv", "mmz4281/2627/E0.csv"],
    "ita": ["mmz4281/2425/I1.csv", "mmz4281/2526/I1.csv", "mmz4281/2627/I1.csv"],
    "esp": ["mmz4281/2425/SP1.csv", "mmz4281/2526/SP1.csv", "mmz4281/2627/SP1.csv"],
    "mex": ["new/MEX.csv"],
}
SPLIT = "2026-04-01"            # β se mide antes; se evalúa después (como calibrate.py)
GOAL_HALFLIFE_DAYS = 120.0
GOAL_PRIOR_K = 12.0
MAX_SHIFT = 0.5                 # el ancla nunca mueve λ más de e^±0,5 (±65 % / −40 %)

# ── Goles esperados desde precios ─────────────────────────────────────────────
_K = np.arange(0, 11)


def _probs(lh: float, la: float):
    ph, pa = poisson.pmf(_K, lh), poisson.pmf(_K, la)
    m = np.outer(ph, pa)
    home = np.tril(m, -1).sum()
    draw = np.trace(m)
    tot = np.convolve(ph, pa)
    return home, draw, 1 - home - draw, tot


def implied_goals(p1x2: tuple[float, float, float] | None,
                  totals: dict[float, float] | None = None) -> tuple[float, float] | None:
    """(goles local, goles visitante) de una Poisson independiente que mejor
    reproduce el 1X2 (sin margen) y las probabilidades de Over de cada línea."""
    if not p1x2 and not totals:
        return None

    def loss(x):
        lh, la = math.exp(x[0]), math.exp(x[1])
        h, d, a, tot = _probs(lh, la)
        e = 0.0
        if p1x2:
            e += (h - p1x2[0]) ** 2 + (d - p1x2[1]) ** 2 + (a - p1x2[2]) ** 2
        for line, po in (totals or {}).items():
            e += (tot[int(math.floor(line)) + 1:].sum() - po) ** 2
        return e

    r = minimize(loss, [math.log(1.4), math.log(1.1)], method="Nelder-Mead",
                 options={"xatol": 1e-4, "fatol": 1e-9})
    lh, la = math.exp(r.x[0]), math.exp(r.x[1])
    return (lh, la) if 0.1 < lh < 6 and 0.1 < la < 6 else None


def _devig(prices) -> list[float] | None:
    q = [1 / float(p) for p in prices]
    if any(not np.isfinite(x) or x <= 0 for x in q):
        return None
    s = sum(q)
    return [x / s for x in q]


# ── Historia de precios (football-data.co.uk) ────────────────────────────────

def _fdco_csv(path: str, ttl_h: float = 12.0) -> pd.DataFrame:
    FDCO_DIR.mkdir(parents=True, exist_ok=True)
    f = FDCO_DIR / path.replace("/", "_")
    if not f.exists() or time.time() - f.stat().st_mtime > ttl_h * 3600:
        try:
            r = httpx.get(f"https://football-data.co.uk/{path}", timeout=60, follow_redirects=True)
            r.raise_for_status()
            f.write_bytes(r.content)
        except Exception as exc:  # noqa: BLE001 - sin red: se usa lo guardado
            if not f.exists():
                print(f"  football-data {path}: {exc}")
                return pd.DataFrame()
    return pd.read_csv(f, encoding="utf-8-sig", low_memory=False)


def _first(row, cols):
    for c in cols:
        v = row.get(c)
        if v is not None and pd.notna(v) and float(v) > 1:
            return float(v)
    return None


def fdco_odds(league: str) -> pd.DataFrame:
    """Partidos con precio de cierre: date, home, away, p1, px, p2, po25."""
    rows = []
    for path in FDCO_FILES[league]:
        df = _fdco_csv(path)
        if df.empty:
            continue
        home_c = "Home" if "Home" in df.columns else "HomeTeam"
        away_c = "Away" if "Away" in df.columns else "AwayTeam"
        for r in df.to_dict("records"):
            d = pd.to_datetime(r.get("Date"), dayfirst=True, errors="coerce")
            if pd.isna(d) or d < pd.Timestamp("2024-12-01"):
                continue
            pr = [_first(r, [f"PSC{s}", f"AvgC{s}", f"Avg{s}", f"B365C{s}", f"B365{s}"]) for s in ("H", "D", "A")]
            if any(p is None for p in pr):
                continue
            p = _devig(pr)
            o = _first(r, ["AvgC>2.5", "Avg>2.5", "B365C>2.5", "B365>2.5"])
            u = _first(r, ["AvgC<2.5", "Avg<2.5", "B365C<2.5", "B365<2.5"])
            po = _devig([o, u])[0] if o and u else None
            rows.append({"date": d.normalize(), "home": str(r[home_c]).strip(), "away": str(r[away_c]).strip(),
                         "p1": p[0], "px": p[1], "p2": p[2], "po25": po})
    return pd.DataFrame(rows)


def link_history(teams: pd.DataFrame, league: str) -> dict:
    """event_id de ESPN → (g_mkt local, g_mkt visitante) con los cierres históricos."""
    from futbol.oddspapi import team_similarity
    fd = fdco_odds(league)
    if fd.empty:
        return {}
    T = teams[teams["league"] == league]
    homes = T[T["home"] == 1][["event_id", "date", "team"]].rename(columns={"team": "h"})
    aways = T[T["home"] == 0][["event_id", "team"]].rename(columns={"team": "a"})
    ev = homes.merge(aways, on="event_id")
    out = {}
    fd["day"] = fd["date"]
    for e in ev.itertuples():
        day = pd.Timestamp(e.date).tz_localize(None).normalize() if pd.Timestamp(e.date).tzinfo else pd.Timestamp(e.date).normalize()
        c = fd[(fd["day"] - day).abs() <= pd.Timedelta(days=1)]
        best, score = None, 0.0
        for r in c.itertuples():
            s = min(team_similarity(e.h, r.home), team_similarity(e.a, r.away))
            if s > score:
                best, score = r, s
        if best is None or score < 0.55:
            continue
        tot = {2.5: best.po25} if best.po25 is not None and pd.notna(best.po25) else None
        g = implied_goals((best.p1, best.px, best.p2), tot)
        if g:
            out[e.event_id] = g
    return out


# ── Modelo de goles (lo que el modelo ya sabe) ───────────────────────────────

def team_goals(teams: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    g = players.groupby(["event_id", "team_id"])["goals"].sum(min_count=1).rename("goals").reset_index()
    return teams.merge(g, on=["event_id", "team_id"], how="left")


def fit_goal_model(tg: pd.DataFrame, asof) -> tuple:
    """Poisson con ridge, como el de remates: (μ, localía, ataque, defensa)."""
    asof = pd.Timestamp(asof)
    d = tg[(tg["date"] < asof) & tg["goals"].notna()]
    if d.empty:
        raise ValueError("sin goles")
    w = fm._weights(d["date"], asof, GOAL_HALFLIFE_DAYS)
    ids = sorted(set(d["team_id"]) | set(d["opp_id"].dropna()))
    ix = {t: i for i, t in enumerate(ids)}
    n = len(ids)
    ti, oi = d["team_id"].map(ix).to_numpy(), d["opp_id"].map(ix).to_numpy()
    hm = np.where(d["home"].to_numpy() == 1, 1.0, -1.0)
    y = d["goals"].to_numpy(float)
    lam_bar = float(np.average(y, weights=w))
    ridge = GOAL_PRIOR_K * lam_bar * 0.5

    def nll(th):
        eta = th[0] + th[1] * hm + th[2:2 + n][ti] + th[2 + n:][oi]
        lam = np.exp(eta)
        a, b = th[2:2 + n], th[2 + n:]
        r = w * (lam - y)
        g = np.empty_like(th)
        g[0], g[1] = r.sum(), (r * hm).sum()
        g[2:2 + n] = np.bincount(ti, weights=r, minlength=n) + ridge * a
        g[2 + n:] = np.bincount(oi, weights=r, minlength=n) + ridge * b
        return np.sum(w * (lam - y * eta)) + ridge * (a @ a + b @ b) / 2, g

    th0 = np.zeros(2 + 2 * n)
    th0[0] = math.log(max(lam_bar, 0.3))
    th = minimize(nll, th0, jac=True, method="L-BFGS-B").x
    return th[0], th[1], {t: th[2 + ix[t]] for t in ids}, {t: th[2 + n + ix[t]] for t in ids}


def goal_expected(gm: tuple, team: str, opp: str, home: int) -> float:
    mu, h, att, dfn = gm
    return math.exp(mu + h * (1 if home else -1) + att.get(team, 0.0) + dfn.get(opp, 0.0))


# ── Ancla ────────────────────────────────────────────────────────────────────

def load_anchor() -> dict:
    try:
        return json.loads(ANCHOR_FILE.read_text(encoding="utf-8"))["leagues"]
    except Exception:  # noqa: BLE001 - sin archivo: sin ancla
        return {}


def shot_multiplier(beta: dict | None, g_mkt: float, g_hat: float, go_mkt: float, go_hat: float) -> float:
    if not beta:
        return 1.0
    x = beta["b1"] * math.log(g_mkt / g_hat) + beta["b2"] * math.log(go_mkt / go_hat)
    return math.exp(max(-MAX_SHIFT, min(MAX_SHIFT, x)))


def _fit_beta(df: pd.DataFrame) -> dict:
    """Poisson con offset log λ: shots ~ λ·exp(b1·x1 + b2·x2)."""
    y, off = df["shots"].to_numpy(float), np.log(df["lam"].to_numpy(float))
    X = df[["x1", "x2"]].to_numpy(float)

    def nll(b):
        eta = off + X @ b
        lam = np.exp(eta)
        return np.sum(lam - y * eta), X.T @ (lam - y)

    b = minimize(nll, np.zeros(2), jac=True, method="L-BFGS-B").x
    return {"b1": round(float(b[0]), 4), "b2": round(float(b[1]), 4)}


def study(leagues=None, verbose: bool = True) -> dict:
    """Arma la tabla (λ remates, ĝ, g_mkt, remates reales) con ajustes walk-forward
    y mide β antes de SPLIT; evalúa después. Devuelve el resumen por liga."""
    from futbol import espn
    leagues = leagues or list(cfg.LEAGUES)
    teams, players = espn.build_history(leagues, verbose=False)
    tg = team_goals(teams, players)
    res = {}
    for lg in leagues:
        T = tg[tg["league"] == lg]
        mk = link_history(teams, lg)
        evs = T[T["event_id"].isin(mk)].drop_duplicates("event_id")
        start = T["date"].min() + pd.Timedelta(days=150)
        evs = evs[evs["date"] >= start].sort_values("date")
        if verbose:
            n_all = T[T["date"] >= start]["event_id"].nunique()
            print(f"[{lg}] partidos con precio de cierre: {len(evs)}/{n_all}")
        rows, last, tm, gm = [], None, None, None
        for ev_id, day in evs[["event_id", "date"]].itertuples(index=False):
            if day != last:
                try:
                    tm = fm.fit_team_model(T, day)
                    gm = fit_goal_model(T, day)
                except ValueError:
                    continue
                last = day
            two = T[T["event_id"] == ev_id]
            if len(two) != 2:
                continue
            gh_m, ga_m = mk[ev_id]
            for r in two.itertuples():
                if pd.isna(r.shots):
                    continue
                lam, _ = tm.expected(r.team_id, r.opp_id, r.home)
                g_hat = goal_expected(gm, r.team_id, r.opp_id, r.home)
                go_hat = goal_expected(gm, r.opp_id, r.team_id, 1 - r.home)
                g_mkt, go_mkt = (gh_m, ga_m) if r.home == 1 else (ga_m, gh_m)
                rows.append({"date": day, "shots": r.shots, "lam": lam, "disp": tm.disp,
                             "x1": math.log(g_mkt / g_hat), "x2": math.log(go_mkt / go_hat)})
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        tr, te = df[df["date"] < pd.Timestamp(SPLIT)], df[df["date"] >= pd.Timestamp(SPLIT)]
        beta = _fit_beta(tr)
        te = te.copy()
        te["lam_a"] = te["lam"] * np.exp(np.clip(beta["b1"] * te["x1"] + beta["b2"] * te["x2"], -MAX_SHIFT, MAX_SHIFT))

        def score(lam_col):
            r = te["disp"].to_numpy()
            lam = te[lam_col].to_numpy()
            p = r / (r + lam)
            ll = -np.mean(nbinom.logpmf(te["shots"].to_numpy().astype(int), r, p))
            lines = {}
            for line in (8.5, 10.5, 12.5, 14.5):
                po = nbinom.sf(int(line), r, p).clip(1e-4, 1 - 1e-4)
                yv = (te["shots"].to_numpy() > line).astype(float)
                lines[line] = float(-np.mean(yv * np.log(po) + (1 - yv) * np.log(1 - po)))
            return ll, lines

        ll0, l0 = score("lam")
        ll1, l1 = score("lam_a")
        corr = float(np.corrcoef(te["x1"], np.log(te["shots"].clip(lower=1)) - np.log(te["lam"]))[0, 1])
        res[lg] = {**beta, "n_train": int(len(tr)), "n_test": int(len(te)),
                   "nll_model": round(ll0, 4), "nll_anchor": round(ll1, 4),
                   "line_ll_model": {str(k): round(v, 4) for k, v in l0.items()},
                   "line_ll_anchor": {str(k): round(v, 4) for k, v in l1.items()},
                   "corr_x1_residual": round(corr, 3)}
        if verbose:
            print(f"   β1={beta['b1']:+.3f} β2={beta['b2']:+.3f}  (n={len(tr)} antes de {SPLIT})")
            print(f"   fuera de muestra n={len(te)}: log-lik remates {ll0:.4f} → {ll1:.4f}  "
                  f"({'mejora' if ll1 < ll0 else 'empeora'})")
            for k in l0:
                print(f"     >{k}: {l0[k]:.4f} → {l1[k]:.4f}")
    return res


def main(argv=None):
    for s in (sys.stdout,):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description="Mide el ancla del mercado del partido")
    ap.add_argument("--leagues", default="arg,bra,eng,ita")
    ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args(argv)
    res = study(a.leagues.split(","))
    if not a.no_save:
        # Sólo se activa el ancla en las ligas donde mejora fuera de muestra.
        keep = {lg: {"b1": r["b1"], "b2": r["b2"]} for lg, r in res.items() if r["nll_anchor"] < r["nll_model"]}
        ANCHOR_FILE.write_text(json.dumps({"split": SPLIT, "leagues": keep, "study": res}, indent=2),
                               encoding="utf-8")
        print(f"\nAncla activa en: {', '.join(keep) or 'ninguna'} → {ANCHOR_FILE}")


if __name__ == "__main__":
    main()
