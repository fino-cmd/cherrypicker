"""
NBA — box scores completos de ESPN (equipo + jugadores), para simular partidos.

Cada partido terminado se baja una sola vez (summary de ESPN, gratis) y queda en
data/altlines/cache/box/{event}.json en formato compacto:

  {"id", "date", "season", "home", "away",
   "teams":   {TRI: {pts, fgm, fga, tpm, tpa, ftm, fta, oreb, dreb, reb, ast, stl, blk, tov}},
   "players": [{id, name, team, starter, min, pts, fgm, fga, tpm, tpa, ftm, fta,
                oreb, dreb, reb, ast, stl, blk, tov}]}

    python -m altlines.nba_box                 # temporada pasada + la actual hasta hoy
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd

from altlines import config as cfg

logger = logging.getLogger(__name__)

DIR = cfg.DATA_DIR / "cache" / "box"
SB_DIR = cfg.DATA_DIR / "cache" / "scoreboard"
_SB = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
_SUM = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
# Fase regular de cada temporada (ESPN la nombra por el año en que termina).
SEASONS = {2025: ("2024-10-22", "2025-04-13"), 2026: ("2025-10-21", "2026-04-12"), 2027: ("2026-10-20", "2027-04-11")}
_TRI = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}


def tri(abbr: str | None) -> str:
    a = str(abbr or "").upper()
    return _TRI.get(a, a)


def _split(s) -> tuple[float, float]:
    try:
        a, b = str(s).split("-")
        return float(a), float(b)
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _f(s) -> float:
    try:
        return float(str(s).replace("+", ""))
    except Exception:  # noqa: BLE001
        return 0.0


# ── calendario ──────────────────────────────────────────────────────────────

def day_events(d: date) -> list[dict]:
    """Partidos de fase regular de un día: [{id, completed}]."""
    f = SB_DIR / f"{d:%Y%m%d}.json"
    past = d < (datetime.now(timezone.utc) - timedelta(days=2)).date()
    if f.exists() and past:
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        data = httpx.get(_SB, params={"dates": f"{d:%Y%m%d}"}, timeout=20).json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("scoreboard %s: %s", d, exc)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    out = []
    for e in data.get("events") or []:
        if (e.get("season") or {}).get("type") != 2:
            continue
        st = (((e.get("competitions") or [{}])[0].get("status") or {}).get("type") or {})
        out.append({"id": str(e["id"]), "completed": bool(st.get("completed"))})
    SB_DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


# ── box score ───────────────────────────────────────────────────────────────

def _team_stats(stats: list[dict]) -> dict:
    v = {s["name"]: s.get("displayValue") for s in stats}
    fgm, fga = _split(v.get("fieldGoalsMade-fieldGoalsAttempted"))
    tpm, tpa = _split(v.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
    ftm, fta = _split(v.get("freeThrowsMade-freeThrowsAttempted"))
    return {"fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa, "ftm": ftm, "fta": fta,
            "oreb": _f(v.get("offensiveRebounds")), "dreb": _f(v.get("defensiveRebounds")),
            "reb": _f(v.get("totalRebounds")), "ast": _f(v.get("assists")),
            "stl": _f(v.get("steals")), "blk": _f(v.get("blocks")),
            "tov": _f(v.get("totalTurnovers") or v.get("turnovers"))}


def fetch_box(eid: str) -> dict | None:
    """Box score compacto de un partido terminado (cacheado para siempre)."""
    f = DIR / f"{eid}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        d = httpx.get(_SUM, params={"event": eid}, timeout=25).json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("summary %s: %s", eid, exc)
        return None
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
    if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
        return None
    side, score = {}, {}
    for c in comp.get("competitors") or []:
        t = tri((c.get("team") or {}).get("abbreviation"))
        side[c.get("homeAway")] = t
        score[t] = _f(c.get("score"))
    bs = d.get("boxscore") or {}
    teams = {}
    for t in bs.get("teams") or []:
        tr = tri((t.get("team") or {}).get("abbreviation"))
        teams[tr] = {**_team_stats(t.get("statistics") or []), "pts": score.get(tr, 0.0)}
    players = []
    for t in bs.get("players") or []:
        tr = tri((t.get("team") or {}).get("abbreviation"))
        for blk in t.get("statistics") or []:
            lab = blk.get("labels") or []
            for a in blk.get("athletes") or []:
                st = a.get("stats") or []
                if not st or a.get("didNotPlay"):
                    continue
                v = dict(zip(lab, st))
                fgm, fga = _split(v.get("FG"))
                tpm, tpa = _split(v.get("3PT"))
                ftm, fta = _split(v.get("FT"))
                mins = _f(v.get("MIN"))
                if mins <= 0:
                    continue
                ath = a.get("athlete") or {}
                players.append({"id": str(ath.get("id")), "name": ath.get("displayName"), "team": tr,
                                "starter": bool(a.get("starter")), "min": mins, "pts": _f(v.get("PTS")),
                                "fgm": fgm, "fga": fga, "tpm": tpm, "tpa": tpa, "ftm": ftm, "fta": fta,
                                "oreb": _f(v.get("OREB")), "dreb": _f(v.get("DREB")), "reb": _f(v.get("REB")),
                                "ast": _f(v.get("AST")), "stl": _f(v.get("STL")), "blk": _f(v.get("BLK")),
                                "tov": _f(v.get("TO"))})
    if len(teams) != 2 or not players:
        return None
    out = {"id": str(eid), "date": str(comp.get("date") or "")[:10], "home": side.get("home"),
           "away": side.get("away"), "teams": teams, "players": players}
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def season_of(day: str) -> int:
    d = date.fromisoformat(day)
    return d.year + 1 if d.month >= 8 else d.year


def crawl(seasons=(2026, 2027), until: date | None = None, workers: int = 6) -> int:
    """Baja todo lo que falte. Devuelve cuántos partidos hay en caché."""
    until = until or datetime.now(timezone.utc).date()
    ids = []
    for s in seasons:
        a, b = (date.fromisoformat(x) for x in SEASONS[s])
        d = a
        days = []
        while d <= min(b, until):
            days.append(d)
            d += timedelta(days=1)
        with ThreadPoolExecutor(workers) as ex:
            for evs in ex.map(day_events, days):
                ids += [e["id"] for e in evs if e["completed"]]
    todo = [i for i in ids if not (DIR / f"{i}.json").exists()]
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(fetch_box, todo))
    return sum(1 for i in ids if (DIR / f"{i}.json").exists())


# ── tablas ──────────────────────────────────────────────────────────────────

_CACHE: dict = {"n": -1, "t": None, "p": None}


def tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    """(equipos, jugadores) con todos los box scores en caché, en orden de fecha."""
    files = sorted(DIR.glob("*.json")) if DIR.exists() else []
    if _CACHE["n"] == len(files):
        return _CACHE["t"], _CACHE["p"]
    trow, prow = [], []
    for f in files:
        g = json.loads(f.read_text(encoding="utf-8"))
        season = season_of(g["date"])
        for t, s in g["teams"].items():
            opp = g["away"] if t == g["home"] else g["home"]
            trow.append({"game": g["id"], "date": g["date"], "season": season, "team": t, "opp": opp,
                         "home": t == g["home"], **s})
        for p in g["players"]:
            opp = g["away"] if p["team"] == g["home"] else g["home"]
            prow.append({"game": g["id"], "date": g["date"], "season": season, "opp": opp, **p})
    T = pd.DataFrame(trow)
    P = pd.DataFrame(prow)
    if not T.empty:
        T = T.sort_values(["date", "game"]).reset_index(drop=True)
        T["poss"] = T["fga"] + 0.44 * T["fta"] - T["oreb"] + T["tov"]
        P = P.sort_values(["date", "game"]).reset_index(drop=True)
    _CACHE.update(n=len(files), t=T, p=P)
    return T, P


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    n = crawl()
    T, P = tables()
    print(f"partidos en caché: {n} · filas equipo {len(T)} · filas jugador {len(P)}")
