"""
Futbol — cuotas de remates de OddsPapi (bet365 AR, Betano, Betsson).

Plan gratis: 250 consultas por mes. UNA consulta trae las tres ligas de una
casa (odds-by-tournaments), así que refrescar una jornada son 3 consultas.
Todo se guarda en data/futbol/cache/oddspapi/ y se reutiliza por ODDS_TTL_S;
los catálogos (mercados, participantes) se bajan una sola vez.

Formatos de mercado que llegan:
  players-shots / players-shotsongoal        escalera "1+", "2+", … (sólo Over)
  playertotals-shots / playertotals-shotsongoal  Over/Under con línea (Betsson)
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from difflib import SequenceMatcher

import httpx
import numpy as np
import pandas as pd

from futbol import config as cfg
from futbol.espn import norm_name

DIR = cfg.CACHE_DIR / "oddspapi"
STAT_OF = {"players-shots": "shots", "players-shotsongoal": "sot",
           "playertotals-shots": "shots", "playertotals-shotsongoal": "sot"}
# Mercados del partido que también se guardan (vienen en la misma consulta):
# sirven de ancla para el volumen de remates (futbol.market).
MATCH_TYPES = {"1x2", "totals"}
LAST_RAW: dict[str, list[dict]] = {}     # casa → respuesta (recortada) de la última lectura
_FETCHED = [False]                       # ¿la última fetch_all consultó a OddsPapi?


def _get(path: str, **params):
    key = cfg.oddspapi_key()
    if not key:
        raise RuntimeError("Falta ODDSPAPI_KEY en backend/.env")
    r = httpx.get(f"{cfg.ODDSPAPI_BASE}/{path}", params={**params, "apiKey": key}, timeout=180)
    _count_usage(r)
    r.raise_for_status()
    return r.json()


def _count_usage(r) -> None:
    """Cuenta cada consulta del mes (el plan da 250) y guarda lo que diga la API."""
    from datetime import datetime, timezone
    f = DIR / "usage.json"
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        u = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    u[month] = int(u.get(month, 0)) + 1
    hdr = {k: v for k, v in r.headers.items() if "remaining" in k.lower() or "limit" in k.lower() or "used" in k.lower()}
    if hdr:
        u["headers"] = hdr
    u["since"] = u.get("since") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f.write_text(json.dumps(u), encoding="utf-8")


def usage() -> dict:
    from datetime import datetime, timezone
    try:
        u = json.loads((DIR / "usage.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return {"used": int(u.get(month, 0)), "limit": 250, "period": "mes", "since": u.get("since"),
            "headers": u.get("headers"), "exact": False}


def _catalog(name: str, path: str, **params):
    f = DIR / f"{name}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    DIR.mkdir(parents=True, exist_ok=True)
    data = _get(path, **params)
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def markets() -> dict:
    return {m["marketId"]: m for m in _catalog("markets_soccer", "markets", sportId=10)}


def participants() -> dict:
    return _catalog("participants_soccer", "participants", sportId=10)


def fetch_book(book: str, refresh: bool = False, allow_fetch: bool = True) -> list[dict]:
    """Cuotas de una casa para las tres ligas (1 consulta, cacheada).

    allow_fetch=False: nunca consulta (usa el caché aunque esté vencido);
    para re-simular un partido sin gastar el plan de OddsPapi."""
    ids = ",".join(str(v["oddspapi"]) for v in cfg.LEAGUES.values())
    f = DIR / f"odds_{book}_{ids.replace(',', '_')}.json"
    fresh = f.exists() and time.time() - f.stat().st_mtime < cfg.ODDS_TTL_S
    if f.exists() and not refresh and (fresh or not allow_fetch):
        return json.loads(f.read_text(encoding="utf-8"))
    if not allow_fetch:
        return []
    DIR.mkdir(parents=True, exist_ok=True)
    data = _get("odds-by-tournaments", bookmaker=book, tournamentIds=ids, oddsFormat="decimal")
    _FETCHED[0] = True
    # Se guarda sólo lo que usamos (los remates): la respuesta entera pesa ~20 MB.
    mk = markets()
    slim = []
    for fx in data:
        keep = {k: v for k, v in fx.items() if k != "bookmakerOdds"}
        bo = {}
        for bk, b in (fx.get("bookmakerOdds") or {}).items():
            ms = {mid: m for mid, m in (b.get("markets") or {}).items()
                  if mk.get(int(mid), {}).get("marketType") in STAT_OF
                  or (mk.get(int(mid), {}).get("marketType") in MATCH_TYPES
                      and mk.get(int(mid), {}).get("period") == "fulltime")}
            if ms:
                bo[bk] = {"markets": ms}
        keep["bookmakerOdds"] = bo
        slim.append(keep)
    slim = _keep_through_suspension(slim, f)
    f.write_text(json.dumps(slim), encoding="utf-8")
    return slim


KEEP_SUSPENDED_MAX_S = 6 * 3600


def _has_live_players(fx: dict) -> bool:
    """¿Hay alguna cuota de jugador activa (no suspendida) en este partido?"""
    for b in (fx.get("bookmakerOdds") or {}).values():
        for m in (b.get("markets") or {}).values():
            for o in (m.get("outcomes") or {}).values():
                for pl in (o.get("players") or {}).values():
                    if pl.get("playerName") and pl.get("active", True) and pl.get("price"):
                        return True
    return False


def _keep_through_suspension(new: list[dict], f) -> list[dict]:
    """bet365 suspende TODO el partido un rato antes de empezar (cuando salen
    las formaciones). Si justo en ese momento se bajan las cuotas, el partido
    queda sin precios. En ese caso se conservan las de la descarga anterior
    (si tienen menos de KEEP_SUSPENDED_MAX_S) y se marca de cuándo son."""
    if not f.exists() or time.time() - f.stat().st_mtime > KEEP_SUSPENDED_MAX_S:
        return new
    try:
        old = {fx["fixtureId"]: fx for fx in json.loads(f.read_text(encoding="utf-8"))}
    except Exception:  # noqa: BLE001
        return new
    old_at = datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
    for i, fx in enumerate(new):
        prev = old.get(fx.get("fixtureId"))
        if prev and not _has_live_players(fx) and _has_live_players(prev):
            new[i] = {**fx, "bookmakerOdds": prev["bookmakerOdds"], "kept_from": prev.get("kept_from") or old_at}
    return new


def parse(data: list[dict], book: str) -> pd.DataFrame:
    """→ una fila por (partido, jugador, stat, línea, lado) con su cuota."""
    mk = markets()
    names = participants()
    league_of = {v["oddspapi"]: k for k, v in cfg.LEAGUES.items()}
    rows = []
    for fx in data:
        lg = league_of.get(fx.get("tournamentId"))
        if lg is None:
            continue
        base = {"fixture_id": fx["fixtureId"], "league": lg, "start": fx.get("startTime"),
                "home": names.get(str(fx["participant1Id"])), "away": names.get(str(fx["participant2Id"])),
                "book": book}
        for b in (fx.get("bookmakerOdds") or {}).values():
            for mid, m in (b.get("markets") or {}).items():
                spec = mk.get(int(mid))
                if not spec or spec.get("marketType") not in STAT_OF:
                    continue
                stat = STAT_OF[spec["marketType"]]
                oname = {str(o["outcomeId"]): o["outcomeName"] for o in spec["outcomes"]}
                ladder = spec["marketType"].startswith("players-")
                for oid, o in (m.get("outcomes") or {}).items():
                    label = oname.get(str(oid), "")
                    for pl in (o.get("players") or {}).values():
                        if not pl.get("active", True) or not pl.get("price"):
                            continue
                        if ladder:
                            if not label.endswith("+"):
                                continue                       # "0" exacto: no lo usamos
                            line, side = int(label[:-1]) - 0.5, "over"
                        else:
                            line, side = float(spec.get("handicap") or 0), label.lower()
                        rows.append({**base, "player_name": pl.get("playerName"),
                                     "player_key": norm_name(pl.get("playerName")),
                                     "stat": stat, "line": line, "side": side,
                                     "price": float(pl["price"])})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(["fixture_id", "player_key", "stat", "line", "side"])
    return df


def _local_day() -> str:
    return datetime.now().strftime("%Y-%m-%d")       # día de acá (la PC está en Argentina)


def refreshes_today() -> int:
    try:
        u = json.loads((DIR / "usage.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    return int((u.get("refresh_days") or {}).get(_local_day(), 0))


def _count_refresh() -> None:
    f = DIR / "usage.json"
    try:
        u = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    day = _local_day()
    u["refresh_days"] = {day: int((u.get("refresh_days") or {}).get(day, 0)) + 1}
    f.write_text(json.dumps(u), encoding="utf-8")


def cache_age_s() -> float | None:
    """Antigüedad de las cuotas guardadas (la casa más vieja), en segundos."""
    ids = "_".join(str(v["oddspapi"]) for v in cfg.LEAGUES.values())
    ages = []
    for book in cfg.BOOKS:
        f = DIR / f"odds_{book}_{ids}.json"
        if not f.exists():
            return None
        ages.append(time.time() - f.stat().st_mtime)
    return max(ages) if ages else None


def cache_stamp() -> int | None:
    """Marca de las cuotas guardadas (cambia con cada descarga)."""
    ids = "_".join(str(v["oddspapi"]) for v in cfg.LEAGUES.values())
    ts = []
    for book in cfg.BOOKS:
        f = DIR / f"odds_{book}_{ids}.json"
        if not f.exists():
            return None
        ts.append(int(f.stat().st_mtime))
    return max(ts)


def maybe_refresh(max_age_s: float | None = None, day_cap: int | None = None) -> dict:
    """Al simular: si las cuotas tienen más de `max_age_s`, se piden de nuevo
    (3 consultas, todas las ligas de una), salvo que ya se llegó al tope del día."""
    max_age_s = cfg.ODDS_MAX_AGE_SIM_S if max_age_s is None else max_age_s
    day_cap = cfg.ODDS_REFRESH_DAY_CAP if day_cap is None else day_cap
    age = cache_age_s()
    if age is not None and age <= max_age_s:
        return {"refreshed": False, "reason": "fresh", "age_s": round(age), "today": refreshes_today(), "cap": day_cap}
    if refreshes_today() >= day_cap or not cfg.oddspapi_key():
        return {"refreshed": False, "reason": "cap" if cfg.oddspapi_key() else "nokey",
                "age_s": None if age is None else round(age), "today": refreshes_today(), "cap": day_cap}
    fetch_all(refresh=True)
    age2 = cache_age_s()
    return {"refreshed": _FETCHED[0], "reason": "refreshed" if _FETCHED[0] else "error",
            "age_s": None if age2 is None else round(age2), "today": refreshes_today(), "cap": day_cap}


def fetch_all(refresh: bool = False, allow_fetch: bool = True) -> pd.DataFrame:
    _FETCHED[0] = False
    frames = []
    for book in cfg.BOOKS:
        try:
            raw = fetch_book(book, refresh, allow_fetch)
            LAST_RAW[book] = raw
            frames.append(parse(raw, book))
        except Exception as exc:  # noqa: BLE001 - una casa caída no corta las demás
            print(f"  OddsPapi {book}: {exc}")
    if _FETCHED[0]:
        _count_refresh()
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── Cruce con ESPN ────────────────────────────────────────────────────────────

_CLUB_WORDS = {"ca", "cd", "club", "atletico", "fc", "sc", "ec", "se", "cr", "afc", "de", "the",
               "deportivo", "sport", "esporte", "clube", "futebol", "regatas"}


def _team_key(name: str) -> str:
    return " ".join(w for w in norm_name(name).split() if w not in _CLUB_WORDS)


def team_similarity(a: str, b: str) -> float:
    ka, kb = _team_key(a), _team_key(b)
    if not ka or not kb:
        return 0.0
    if ka in kb or kb in ka:
        return 1.0
    return SequenceMatcher(None, ka, kb).ratio()


def match_fixture(ev: dict, fixtures: pd.DataFrame) -> str | None:
    """fixture_id de OddsPapi para un partido de ESPN (misma liga, ±1 día, nombres)."""
    if fixtures.empty:
        return None
    c = fixtures[fixtures["league"] == ev["league"]].drop_duplicates("fixture_id")
    t = pd.Timestamp(ev["date"])
    c = c[(pd.to_datetime(c["start"]) - t).abs() <= pd.Timedelta(hours=30)]
    best, score = None, 0.0
    for r in c.itertuples():
        sh, sa = team_similarity(ev["home"], r.home), team_similarity(ev["away"], r.away)
        s = min(sh, sa)
        # Un equipo idéntico y el mismo horario alcanzan aunque el otro nombre
        # sea muy distinto ("Athletico-PR" / "Atletico Paranaense").
        same_time = abs(pd.Timestamp(r.start) - t) <= pd.Timedelta(hours=3)
        if same_time and max(sh, sa) >= 0.95 and min(sh, sa) >= 0.25:
            s = max(s, 0.9)
        if s > score:
            best, score = r.fixture_id, s
    return best if score >= 0.6 else None


def match_player(name_key: str, candidates: dict) -> str | None:
    """player_key de OddsPapi → player_id de ESPN (dentro de los dos planteles)."""
    if name_key in candidates:
        return candidates[name_key]
    # Nombre legal completo de un lado y corto del otro: "alberto campo" ⊂
    # "driden alberto campo deusa", "emerson" ⊂ "emerson royal". Sólo si hay
    # un único candidato así.
    toks = set(name_key.split())
    # El lado corto necesita 2+ palabras: un apodo de una palabra ("Pedro",
    # "Matheus") cabe en muchos nombres legales y cruzaría al jugador equivocado.
    sub = [pid for k, pid in candidates.items()
           if toks and min(len(toks), len(k.split())) >= 2
           and (toks <= set(k.split()) or set(k.split()) <= toks)]
    if len(set(sub)) == 1:
        return sub[0]
    parts = name_key.split()
    best, score = None, 0.0
    for k, pid in candidates.items():
        kp = k.split()
        s = SequenceMatcher(None, name_key, k).ratio()
        if parts and kp and parts[-1] == kp[-1]:
            s += 0.25                                   # mismo apellido
        if s > score:
            best, score = pid, s
    return best if score >= 0.82 else None


# ── Precio justo del libro ───────────────────────────────────────────────────

def fair_prob(df: pd.DataFrame) -> pd.Series:
    """Probabilidad implícita sin margen.

    Over/Under con los dos lados: se normaliza el par. Escaleras de un solo
    lado ("1+, 2+…"): no se ve el margen en el par, se usa el método potencia
    p = p_impl ** γ con γ = MARGIN_GAMMA (las casas cargan más a las cuotas
    largas). Es aproximado: el modelo no depende de esto.
    """
    p_imp = 1.0 / df["price"]
    out = pd.Series(np.nan, index=df.index)
    keys = ["book", "fixture_id", "player_key", "stat", "line"]
    for _, g in df.groupby(keys):
        if set(g["side"]) >= {"over", "under"}:
            s = p_imp.loc[g.index].sum()
            out.loc[g.index] = p_imp.loc[g.index] / s
        else:
            out.loc[g.index] = np.clip(p_imp.loc[g.index] ** MARGIN_GAMMA, 1e-4, 0.999)
    return out


MARGIN_GAMMA = 1.08


def match_markets(fixture_id: str) -> dict | None:
    """1X2 sin margen y P(Over) por línea de goles, promediados entre casas,
    de la última lectura (no consulta nada)."""
    mk = markets()
    p1x2, totals = [], {}
    for raw in LAST_RAW.values():
        fx = next((f for f in raw if f.get("fixtureId") == fixture_id), None)
        if not fx:
            continue
        for b in (fx.get("bookmakerOdds") or {}).values():
            for mid, m in (b.get("markets") or {}).items():
                spec = mk.get(int(mid)) or {}
                if spec.get("period") != "fulltime" or spec.get("marketType") not in MATCH_TYPES:
                    continue
                oname = {str(o["outcomeId"]): o["outcomeName"] for o in spec["outcomes"]}
                pr = {}
                for oid, o in (m.get("outcomes") or {}).items():
                    for pl in (o.get("players") or {}).values():
                        if pl.get("active", True) and pl.get("price"):
                            pr[oname.get(str(oid), "")] = float(pl["price"])
                if spec["marketType"] == "1x2" and {"1", "X", "2"} <= set(pr):
                    q = [1 / pr["1"], 1 / pr["X"], 1 / pr["2"]]
                    p1x2.append([x / sum(q) for x in q])
                elif spec["marketType"] == "totals" and {"Over", "Under"} <= set(pr):
                    line = float(spec.get("handicap") or 0)
                    if abs(line % 1 - 0.5) < 1e-9 and 0.5 <= line <= 5.5:
                        q = [1 / pr["Over"], 1 / pr["Under"]]
                        totals.setdefault(line, []).append(q[0] / sum(q))
    if not p1x2 and not totals:
        return None
    return {"p1x2": tuple(float(np.mean([x[i] for x in p1x2])) for i in range(3)) if p1x2 else None,
            "totals": {l: float(np.mean(v)) for l, v in totals.items()}}
