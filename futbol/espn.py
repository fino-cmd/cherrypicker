"""
Futbol — ingesta de ESPN (gratis, sin clave).

Por cada partido terminado guarda dos tablas compactas:

  teams    una fila por equipo y partido: remates, al arco, posesión, córners,
           goles, local/visitante, rival.
  players  una fila por jugador y partido: titular, MINUTOS (calculados con
           el minuto de cada cambio), posición, remates, al arco, goles.

Cada partido se guarda ya procesado en data/futbol/cache/espn/<liga>/<id>.json
(un partido terminado no cambia: se baja una sola vez).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import httpx
import pandas as pd

from futbol import config as cfg

logger = logging.getLogger(__name__)

SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"


def norm_name(name) -> str:
    """'Alejandro Garnacho' / 'Garnacho, Alejandro' → 'alejandro garnacho'."""
    s = str(name or "")
    if "," in s:                                   # "Apellido, Nombre"
        last, first = s.split(",", 1)
        s = f"{first} {last}"
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", " ", s.replace("'", ""))
    return " ".join(w for w in s.split() if w not in {"jr", "junior", "sr", "ii", "iii"})


def position_group(abbr) -> str:
    """ESPN position abbreviation → G / D / M / F."""
    a = str(abbr or "").upper()
    if a in ("", "SUB", "NONE"):
        return "?"                 # suplente: se resuelve con su posición habitual
    if a in ("G", "GK"):
        return "G"
    if a.startswith(("CD", "LB", "RB", "D", "SW", "WB", "LWB", "RWB", "CB")):
        return "D"
    if a.startswith(("F", "CF", "ST", "LW", "RW", "S")) or a in ("LF", "RF"):
        return "F"
    return "M"


def _minute(clock) -> int | None:
    m = re.match(r"\s*(\d+)", str(clock or ""))
    return int(m.group(1)) if m else None


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


# ── Calendario ────────────────────────────────────────────────────────────────

def month_events(league: str, yyyymm: str, retries: int = 3) -> list[dict]:
    """Partidos de un mes. Un mes ya terminado no cambia: se guarda en disco y
    un corte de red nunca deja huecos en la historia."""
    f = cfg.CACHE_DIR / "espn" / "months" / f"{league}_{yyyymm}.json"
    closed = yyyymm < datetime.now(timezone.utc).strftime("%Y%m")
    if closed and f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    code = cfg.LEAGUES[league]["espn"]
    last = None
    for attempt in range(retries):
        try:
            r = httpx.get(f"{SITE}/{code}/scoreboard", params={"dates": yyyymm, "limit": 500}, timeout=30)
            r.raise_for_status()
            break
        except Exception as exc:  # noqa: BLE001 - red inestable: se reintenta
            last = exc
            import time as _t
            _t.sleep(2 * (attempt + 1))
    else:
        raise last
    out = []
    for ev in r.json().get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        if "home" not in teams or "away" not in teams:
            continue
        out.append({
            "event_id": str(ev["id"]), "league": league, "date": ev.get("date"),
            "state": ((ev.get("status") or {}).get("type") or {}).get("state"),
            "home_id": str(teams["home"]["team"]["id"]), "away_id": str(teams["away"]["team"]["id"]),
            "home": teams["home"]["team"].get("displayName"), "away": teams["away"]["team"].get("displayName"),
            "home_abbr": teams["home"]["team"].get("abbreviation"),
            "away_abbr": teams["away"]["team"].get("abbreviation"),
            "venue": (comp.get("venue") or {}).get("fullName"),
        })
    if closed:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out), encoding="utf-8")
    return out


def months_between(start: str, end: date | None = None) -> list[str]:
    end = end or datetime.now(timezone.utc).date()
    y, m = int(start[:4]), int(start[4:])
    out = []
    while (y, m) <= (end.year, end.month):
        out.append(f"{y}{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


# ── Un partido ────────────────────────────────────────────────────────────────

def _cache_path(league: str, event_id: str):
    return cfg.CACHE_DIR / "espn" / league / f"{event_id}.json"


def parse_summary(s: dict, ev: dict) -> dict:
    """ESPN summary → {'teams': [...], 'players': [...]} (compact)."""
    teams_out, players_out = [], []
    formation_of = {str((ro.get("team") or {}).get("id")): ro.get("formation")
                    for ro in s.get("rosters") or []}
    box = {str(t["team"]["id"]): t for t in (s.get("boxscore") or {}).get("teams") or []}
    side_of = {ev["home_id"]: 1, ev["away_id"]: 0}
    opp_of = {ev["home_id"]: ev["away_id"], ev["away_id"]: ev["home_id"]}
    for tid, t in box.items():
        st = {x["name"]: x.get("displayValue") for x in t.get("statistics") or []}
        teams_out.append({
            "event_id": ev["event_id"], "league": ev["league"], "date": ev["date"][:10],
            "team_id": tid, "team": t["team"].get("displayName"), "abbr": t["team"].get("abbreviation"),
            "opp_id": opp_of.get(tid), "home": side_of.get(tid),
            "shots": _num(st.get("totalShots")), "sot": _num(st.get("shotsOnTarget")),
            "possession": _num(st.get("possessionPct")), "corners": _num(st.get("wonCorners")),
            "fouls": _num(st.get("foulsCommitted")),
        })
    for t in teams_out:
        t["formation"] = formation_of.get(t["team_id"])
    for ro in s.get("rosters") or []:
        tid = str((ro.get("team") or {}).get("id"))
        for p in ro.get("roster") or []:
            stats = {x["name"]: x.get("value") for x in p.get("stats") or []}
            starter = bool(p.get("starter"))
            on, off = (0 if starter else None), None
            for pl in p.get("plays") or []:
                mnt = _minute((pl.get("clock") or {}).get("displayValue"))
                if mnt is None or not pl.get("substitution"):
                    continue
                if not starter and on is None:
                    on = mnt                       # entró
                else:
                    off = mnt                      # salió
            if on is None:
                minutes = 0
            else:
                minutes = max(0, min(off if off is not None else 90, 90) - on)
            pos = (p.get("position") or {}).get("abbreviation")
            players_out.append({
                "event_id": ev["event_id"], "league": ev["league"], "date": ev["date"][:10],
                "team_id": tid, "opp_id": opp_of.get(tid), "home": side_of.get(tid),
                "player_id": str((p.get("athlete") or {}).get("id")),
                "player": (p.get("athlete") or {}).get("displayName"),
                "pos": pos, "pos_group": position_group(pos), "starter": int(starter),
                "formation_place": int(p["formationPlace"]) if str(p.get("formationPlace") or "0").isdigit()
                and int(p.get("formationPlace") or 0) > 0 else None,
                "minutes": minutes,
                "shots": _num(stats.get("totalShots")), "sot": _num(stats.get("shotsOnTarget")),
                "goals": _num(stats.get("totalGoals")),
            })
    return {"teams": teams_out, "players": players_out}


def fetch_event(ev: dict, refresh: bool = False) -> dict | None:
    path = _cache_path(ev["league"], ev["event_id"])
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    code = cfg.LEAGUES[ev["league"]]["espn"]
    try:
        r = httpx.get(f"{SITE}/{code}/summary", params={"event": ev["event_id"]}, timeout=30)
        r.raise_for_status()
        data = parse_summary(r.json(), ev)
    except Exception as exc:  # noqa: BLE001 - un partido que falla no corta la ingesta
        logger.warning("ESPN %s %s: %s", ev["league"], ev["event_id"], exc)
        return None
    if not data["teams"]:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def build_history(leagues=None, start: str = cfg.HISTORY_FROM, workers: int = 6,
                  verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every finished match of the leagues since `start` → (teams, players)."""
    leagues = leagues or list(cfg.LEAGUES)
    events = []
    for lg in leagues:
        for mm in months_between(start):
            try:
                events += [e for e in month_events(lg, mm) if e["state"] == "post"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("ESPN calendario %s %s: %s", lg, mm, exc)
    events = list({e["event_id"]: e for e in events}.values())
    if verbose:
        print(f"ESPN: {len(events)} partidos terminados en {len(leagues)} ligas desde {start}")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(fetch_event, events))
    teams = [t for r in res if r for t in r["teams"]]
    players = [p for r in res if r for p in r["players"]]
    tdf = pd.DataFrame(teams)
    pdf = pd.DataFrame(players)
    for df in (tdf, pdf):
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
    if verbose:
        print(f"      {len(tdf)} filas de equipo, {len(pdf)} de jugador "
              f"({int((pdf['minutes'] > 0).sum()) if not pdf.empty else 0} con minutos)")
    return tdf, pdf


def upcoming(leagues=None, days: int = 7) -> list[dict]:
    """Partidos por jugarse en los próximos `days` días."""
    leagues = leagues or list(cfg.LEAGUES)
    now = datetime.now(timezone.utc)
    months = sorted({now.strftime("%Y%m"), (now + pd.Timedelta(days=days)).strftime("%Y%m")})
    out = []
    for lg in leagues:
        for mm in months:
            for e in month_events(lg, mm):
                t = pd.Timestamp(e["date"])
                if e["state"] == "pre" and now <= t <= now + pd.Timedelta(days=days):
                    out.append(e)
    return sorted(out, key=lambda e: e["date"])


def lineup(ev: dict) -> list[dict]:
    """Formación confirmada (si ESPN ya la publicó, ~1 h antes): titulares y suplentes."""
    code = cfg.LEAGUES[ev["league"]]["espn"]
    try:
        s = httpx.get(f"{SITE}/{code}/summary", params={"event": ev["event_id"]}, timeout=30).json()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ro in s.get("rosters") or []:
        tid = str((ro.get("team") or {}).get("id"))
        for p in ro.get("roster") or []:
            pos = (p.get("position") or {}).get("abbreviation")
            fp = str(p.get("formationPlace") or "0")
            out.append({"team_id": tid, "formation": ro.get("formation"),
                        "formation_place": int(fp) if fp.isdigit() and int(fp) > 0 else None,
                        "player_id": str((p.get("athlete") or {}).get("id")),
                        "player": (p.get("athlete") or {}).get("displayName"),
                        "starter": int(bool(p.get("starter"))), "pos": pos,
                        "pos_group": position_group(pos)})
    return out
