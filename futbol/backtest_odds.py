"""
Futbol — backtest del modelo de remates CONTRA LAS CUOTAS REALES.

    python -m futbol.backtest_odds --download   # baja cuotas históricas (gratis, ~5 s por partido)
    python -m futbol.backtest_odds              # simula y compara

Sólo lee: no toca el modelo, la app ni picks.db.

1. Partidos jugados de cada liga desde --since (OddsPapi /fixtures: 1 consulta
   por liga, una sola vez; queda guardado).
2. Cuotas históricas de bet365 AR, Betano y Betsson de cada partido
   (/historical-odds, gratis). De cada línea de remates se guarda el precio
   1 h antes del partido (cuando ya hay formaciones) y el último antes del
   inicio (cierre).
3. Predicciones walk-forward del modelo (futbol.backtest: sólo datos
   anteriores a cada partido, con la formación real).
4. Cruce y medición: ¿se gana apostando cuando el modelo ve valor? ¿el modelo
   sabe algo que el mercado no?

Límite: OddsPapi identifica a los jugadores sólo con un número; el nombre sale
de las cuotas guardadas por la app, así que los que ya no están en estas ligas
quedan afuera.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta

import httpx
import numpy as np
import pandas as pd

from futbol import backtest as bt
from futbol import config as cfg
from futbol import oddspapi as op
from futbol.espn import norm_name

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

DIR = op.DIR / "hist"
BOOKS = ("bet365.bet.ar", "betano", "betsson")
PRED = cfg.DATA_DIR / "backtest_odds_pred.csv.gz"
OUT = cfg.DATA_DIR / "backtest_odds_rows.csv.gz"
SHOT_LINES = [0.5, 1.5, 2.5, 3.5, 4.5]
SOT_LINES = [0.5, 1.5, 2.5]


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── 1-2. Descarga ─────────────────────────────────────────────────────────────

def fixtures(since: str) -> pd.DataFrame:
    rows = []
    for lg, spec in cfg.LEAGUES.items():
        f = DIR / f"fixtures_{lg}.json"
        if not f.exists():                       # 1 consulta del plan por liga
            DIR.mkdir(parents=True, exist_ok=True)
            fx = op._get("fixtures", tournamentId=spec["oddspapi"], statusId=2,
                         **{"from": "2026-01-01T00:00:00Z", "to": datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")})
            f.write_text(json.dumps(fx), encoding="utf-8")
        for x in json.loads(f.read_text(encoding="utf-8")):
            if x["startTime"] >= since:
                rows.append({"fixture_id": x["fixtureId"], "league": lg, "start": x["startTime"],
                             "home": x["participant1Name"], "away": x["participant2Name"]})
    return pd.DataFrame(rows).sort_values("start", ascending=False)


TIMES_MIN = (180, 60, 30, 10)      # minutos antes del inicio en que se mira la cuota


def _slim(raw: dict, start: str) -> list[dict]:
    """Sólo los mercados de remates: la cuota VIGENTE a 180/60/30/10 min del
    inicio. Vigente = el último registro hasta ese momento; si ese registro
    dice suspendida (bet365 suspende al salir las formaciones), no había
    precio para apostar → None."""
    mk = op.markets()
    ko = _ts(start)
    out = []
    for book, b in (raw.get("bookmakers") or {}).items():
        for mid, m in (b.get("markets") or {}).items():
            spec = mk.get(int(mid))
            if not spec or spec.get("marketType") not in op.STAT_OF:
                continue
            oname = {str(o["outcomeId"]): o["outcomeName"] for o in spec["outcomes"]}
            for oid, o in (m.get("outcomes") or {}).items():
                for pid, snaps in (o.get("players") or {}).items():
                    pre = sorted((s for s in snaps if _ts(s["createdAt"]) < ko), key=lambda s: s["createdAt"])
                    if not pre:
                        continue
                    row = {"book": book, "type": spec["marketType"], "handicap": spec.get("handicap"),
                           "label": oname.get(str(oid), ""), "pid": pid}
                    for m_ in TIMES_MIN:
                        t = ko - timedelta(minutes=m_)
                        cur = [s for s in pre if _ts(s["createdAt"]) <= t]
                        s = cur[-1] if cur else None
                        row[f"t{m_}"] = s["price"] if s and s.get("active", True) and s.get("price") else None
                    if any(row[f"t{m_}"] for m_ in TIMES_MIN):
                        out.append(row)
    return out


def download(since: str) -> None:
    fx = fixtures(since)
    key = cfg.oddspapi_key()
    todo = [r for r in fx.itertuples() if not (DIR / f"slim_{r.fixture_id}.json").exists()]
    print(f"{len(fx)} partidos desde {since}; faltan {len(todo)} (~{len(todo) * 6 // 60} min)", flush=True)
    for i, r in enumerate(todo):
        for attempt in range(4):
            t0 = time.time()
            try:
                resp = httpx.get(f"{cfg.ODDSPAPI_BASE}/historical-odds", timeout=180,
                                 params={"apiKey": key, "fixtureId": r.fixture_id, "bookmakers": ",".join(BOOKS)})
            except Exception as exc:  # noqa: BLE001
                print(f"  {r.fixture_id}: {exc}", flush=True)
                time.sleep(10)
                continue
            if resp.status_code == 429:
                time.sleep(8)
                continue
            if resp.status_code != 200:
                print(f"  {r.fixture_id}: HTTP {resp.status_code} {resp.text[:100]}", flush=True)
                break
            rows = _slim(resp.json(), r.start)
            (DIR / f"slim_{r.fixture_id}.json").write_text(json.dumps(rows), encoding="utf-8")
            break
        if i % 10 == 0:
            print(f"  {i + 1}/{len(todo)} {r.league} {r.home} vs {r.away}", flush=True)
        time.sleep(max(0.0, 5.2 - (time.time() - t0)))


# ── 3. Predicciones del modelo ────────────────────────────────────────────────

def predictions(since: str, n_sim: int) -> pd.DataFrame:
    if PRED.exists():
        return pd.read_csv(PRED)
    bt.PLAYER_MARKETS = [("shots", x) for x in SHOT_LINES] + [("sot", x) for x in SOT_LINES]
    df = bt.run(list(cfg.LEAGUES), since[:10], n_sim, verbose=True)
    df = df[df["level"] == "player"]
    df.to_csv(PRED, index=False)
    return df


# ── 4. Cruce ──────────────────────────────────────────────────────────────────

def _player_names() -> dict:
    names = {}
    for f in op.DIR.glob("odds_*.json"):
        for fx in json.loads(f.read_text(encoding="utf-8")):
            for b in (fx.get("bookmakerOdds") or {}).values():
                for m in (b.get("markets") or {}).values():
                    for o in (m.get("outcomes") or {}).values():
                        for pid, pl in (o.get("players") or {}).items():
                            if pl.get("playerName"):
                                names[str(pid)] = pl["playerName"]
    return names


def build(since: str, n_sim: int) -> pd.DataFrame:
    from futbol import espn
    fx = fixtures(since)
    pred = predictions(since, n_sim)
    teams, players = espn.build_history(list(cfg.LEAGUES), verbose=False)
    names = _player_names()
    ev = teams[teams["event_id"].astype(str).isin(pred["event_id"].astype(str).unique())]
    rows = []
    pred["event_id"] = pred["event_id"].astype(str)
    players["event_id"] = players["event_id"].astype(str)
    for eid, two in ev.groupby(ev["event_id"].astype(str)):
        if len(two) != 2:
            continue
        h, a = two[two["home"] == 1].iloc[0], two[two["home"] == 0].iloc[0]
        day = pd.Timestamp(h["date"]).tz_localize("UTC") + pd.Timedelta(hours=12)   # ESPN da sólo el día
        fid = op.match_fixture({"league": h["league"], "date": day, "home": h["team"], "away": a["team"]}, fx)
        f = DIR / f"slim_{fid}.json" if fid else None
        if not f or not f.exists():
            continue
        cands = {norm_name(n): n for n in players.loc[players["event_id"] == eid, "player"].dropna().unique()}
        pe = pred[pred["event_id"] == eid].set_index(["who", "stat", "line"])["p_model"].to_dict()
        ye = pred[pred["event_id"] == eid].set_index(["who", "stat", "line"])["y"].to_dict()
        for o in json.loads(f.read_text(encoding="utf-8")):
            nm = names.get(str(o["pid"]))
            who = op.match_player(norm_name(nm), cands) if nm else None
            if not who:
                continue
            stat = op.STAT_OF[o["type"]]
            if o["type"].startswith("players-"):
                if not str(o["label"]).endswith("+"):
                    continue
                line, side = int(o["label"][:-1]) - 0.5, "over"
            else:
                line, side = float(o["handicap"] or 0), str(o["label"]).lower()
            k = (who, stat, line)
            if k not in pe:
                continue
            p = pe[k] if side == "over" else 1 - pe[k]
            y = ye[k] if side == "over" else 1 - ye[k]
            rows.append({"event_id": eid, "league": h["league"], "date": h["date"], "player": who,
                         "book": o["book"], "stat": stat, "line": line, "side": side,
                         "p_model": p, "y": y, **{f"t{m_}": o.get(f"t{m_}") for m_ in TIMES_MIN}})
    df = pd.DataFrame(rows).drop_duplicates(["event_id", "player", "book", "stat", "line", "side"])
    df.to_csv(OUT, index=False)
    return df


def _cluster_ci(d: pd.DataFrame, col: str, n: int = 3000) -> tuple[float, float]:
    """IC 90 % re-muestreando PARTIDOS (las apuestas de un mismo partido van juntas)."""
    g = d.groupby("event_id")[col].agg(["sum", "size"])
    if len(g) < 5:
        return (np.nan, np.nan)
    rng = np.random.default_rng(0)
    idx = rng.integers(0, len(g), size=(n, len(g)))
    s, c = g["sum"].to_numpy()[idx].sum(1), g["size"].to_numpy()[idx].sum(1)
    return tuple(np.percentile(s / c, [5, 95]))


def _info_test(df: pd.DataFrame) -> None:
    """¿Sabe el modelo algo que el mercado no? Las casas sólo publican MÁS, así
    que no se puede sacar el margen directo. Se ajusta, fuera de muestra
    (5 grupos de partidos), y ~ logit(1/cuota de cierre) [solo mercado] contra
    y ~ logit(1/cuota) + logit(modelo). Si agregar el modelo no baja el
    log-loss, el modelo no aporta nada que la cuota no tenga ya."""
    from scipy.optimize import minimize
    d = df[df["side"] == "over"].copy()
    d["close"] = d["t10"].fillna(d["t30"])          # la última cuota apostable
    d = d[d["close"].notna()]
    if len(d) < 200:
        return
    lg = lambda p: np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))  # noqa: E731
    d["xm"], d["xp"] = lg(1 / d["close"]), lg(d["p_model"])
    ev = d["event_id"].unique()
    fold = dict(zip(ev, np.random.default_rng(1).integers(0, 5, len(ev))))
    d["fold"] = d["event_id"].map(fold)

    def fit(X, y):
        def nll(w):
            z = X @ w
            return float(np.sum(np.logaddexp(0, z) - y * z))
        return minimize(nll, np.zeros(X.shape[1]), method="BFGS").x

    def ll(z, y):
        return float(np.mean(np.logaddexp(0, z) - y * z))
    res = {"mercado": [], "mercado + modelo": [], "modelo solo": []}
    coefs = []
    for k in range(5):
        tr, te = d[d["fold"] != k], d[d["fold"] == k]
        for name, cols in (("mercado", ["xm"]), ("mercado + modelo", ["xm", "xp"]), ("modelo solo", ["xp"])):
            Xtr = np.column_stack([np.ones(len(tr))] + [tr[c] for c in cols])
            Xte = np.column_stack([np.ones(len(te))] + [te[c] for c in cols])
            w = fit(Xtr, tr["y"].to_numpy(float))
            res[name].append(ll(Xte @ w, te["y"].to_numpy(float)) * len(te))
            if name == "mercado + modelo":
                coefs.append(w)
    n = len(d)
    print(f"\n══ ¿El modelo sabe algo que la cuota de cierre no? (fuera de muestra, n={n}) ══")
    for name, v in res.items():
        print(f"  log-loss {name:<17} {sum(v) / n:.4f}")
    w = np.mean(coefs, axis=0)
    print(f"  peso en la combinación: mercado {w[1]:.2f} · modelo {w[2]:.2f}  "
          f"(modelo ≈ 0 → no aporta; > 0 → aporta información)")
    print(f"  margen implícito promedio (1/cuota − real): {float((1 / d['close']).mean() - d['y'].mean()):+.1%}")


def report(df: pd.DataFrame) -> None:
    if df.empty:
        print("sin cruces")
        return
    print(f"\n{len(df)} cuotas cruzadas · {df['event_id'].nunique()} partidos · "
          f"{df['player'].nunique()} jugadores · {df['date'].min()} a {df['date'].max()}")
    print(df.groupby(["league", "book"]).size().unstack(fill_value=0))

    _info_test(df)

    ov = df[df["side"] == "over"].copy()
    for col, name in (("t180", "3 h antes (sin formaciones)"), ("t60", "1 h antes"),
                      ("t30", "30 min antes"), ("t10", "10 min antes")):
        d0 = ov[ov[col].notna()].copy()
        d0["ev"] = d0["p_model"] * d0[col] - 1
        d0["pnl"] = np.where(d0["y"] == 1, d0[col] - 1, -1.0)
        # la mejor casa de cada línea, como hace la app
        best = d0.sort_values(col).groupby(["event_id", "player", "stat", "line"]).tail(1)
        print(f"\n══ Apostando a MÁS cuando el modelo ve valor · precio {name} · mejor casa ══")
        print(f"  {'valor mín.':>10} {'apuestas':>8} {'partidos':>8} {'acierto':>8} {'esperado':>8} {'ROI':>7}  IC 90 % (por partido)")
        for thr in (0.0, 0.05, 0.10, 0.20, 0.30):
            b = best[best["ev"] > thr]
            if len(b) < 20:
                continue
            lo, hi = _cluster_ci(b, "pnl")
            print(f"  {thr:>9.0%} {len(b):>8} {b['event_id'].nunique():>8} {b['y'].mean():>8.1%} "
                  f"{b['p_model'].mean():>8.1%} {b['pnl'].mean():>+7.1%}  [{lo:+.1%}, {hi:+.1%}]")
        if col == "t30":
            b = best[best["ev"] > 0.05]
            print("  por liga (valor > 5 %):")
            print(b.groupby("league").agg(apuestas=("pnl", "size"), acierto=("y", "mean"),
                                          esperado=("p_model", "mean"), ROI=("pnl", "mean")).round(3).to_string())
            print("  por casa (valor > 5 %):")
            print(b.groupby("book").agg(apuestas=("pnl", "size"), ROI=("pnl", "mean")).round(3).to_string())
            print("  por mercado (valor > 5 %):")
            print(b.groupby(["stat", "line"]).agg(apuestas=("pnl", "size"), acierto=("y", "mean"),
                                                  esperado=("p_model", "mean"), ROI=("pnl", "mean")).round(3).to_string())
            c = b[b["t10"].notna()]
            print(f"  CLV: precio 30 min antes / 10 min antes = {float((c['t30'] / c['t10']).mean()):.3f} (n={len(c)}) "
                  f"(>1 = el mercado se movió a favor del modelo)")
    # Calibración contra el resultado, sólo en las líneas que ofrecen las casas
    ov = ov.assign(bin=pd.cut(ov["p_model"], [0, .1, .2, .35, .5, .65, .8, 1]))
    print("\n══ Calibración en líneas ofrecidas (MÁS) ══")
    print(ov.drop_duplicates(["event_id", "player", "stat", "line"]).groupby("bin", observed=True)
          .agg(n=("y", "size"), pred=("p_model", "mean"), real=("y", "mean")).round(3).to_string())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Backtest del modelo de remates contra cuotas reales")
    ap.add_argument("--since", default="2026-08-01")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--n-sim", type=int, default=4000)
    a = ap.parse_args(argv)
    if a.download:
        download(a.since)
        return
    report(build(a.since, a.n_sim))


if __name__ == "__main__":
    main()
