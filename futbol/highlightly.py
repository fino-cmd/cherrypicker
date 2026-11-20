"""
Futbol — titulares de Highlightly (highlightly.net), tercera fuente.

Plan gratis: 100 consultas por día (x-ratelimit-requests-remaining).
  GET /matches?leagueId=&date=   partidos de una liga en un día (1 consulta)
  GET /lineups/{matchId}         formación, titulares por líneas y suplentes

Los titulares vienen por líneas (arquero → delanteros) y cada línea de
izquierda a derecha (comparado con ESPN el 30/10/2026), igual que la grilla
de API-Football: se usa la misma conversión a puestos.

Cupo: se pregunta desde HL_BEFORE_MIN antes del partido, cada HL_EVERY_MIN
minutos (el doble si el partido no tiene jugadas ni lo seguís), con tope
diario HL_DAILY_CAP y una reserva para los prioritarios.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from futbol import apifootball as af
from futbol import config as cfg

DIR = cfg.CACHE_DIR / "highlightly"
BASE = "https://soccer.highlightly.net"
LEAGUES = {"arg": 109712, "bra": 61205, "eng": 33973, "ita": 115669}
HL_BEFORE_MIN = 65
HL_EVERY_MIN = 2
HL_DAILY_CAP = 92
HL_PRIORITY_RESERVE = 25
MATCHES_TTL_S = 6 * 3600
_last_try: dict[str, float] = {}


def key() -> str | None:
    return os.getenv("HIGHLIGHTLY_KEY")


def _usage_file():
    return DIR / "usage.json"


def usage_today() -> int:
    try:
        u = json.loads(_usage_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    return int(u.get(datetime.now(timezone.utc).strftime("%Y-%m-%d"), 0))


def _count(remaining) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        u = json.loads(_usage_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    _usage_file().write_text(json.dumps({day: int(u.get(day, 0)) + 1, "remaining": remaining}), encoding="utf-8")


def _get(path: str, **params):
    if not key():
        raise RuntimeError("Falta HIGHLIGHTLY_KEY en backend/.env")
    if usage_today() >= HL_DAILY_CAP:
        raise RuntimeError("Cupo diario de Highlightly agotado")
    r = httpx.get(f"{BASE}{path}", params=params, headers={"x-rapidapi-key": key()}, timeout=30)
    _count(r.headers.get("x-ratelimit-requests-remaining"))
    r.raise_for_status()
    return r.json()


def matches(league: str, day: str) -> list[dict]:
    """Partidos de una liga en `day` (UTC), guardados MATCHES_TTL_S."""
    f = DIR / f"matches_{league}_{day}.json"
    if f.exists() and time.time() - f.stat().st_mtime < MATCHES_TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    d = _get("/matches", leagueId=LEAGUES[league], date=day, timezone="UTC")
    slim = [{"id": m["id"], "date": m["date"], "home": m["homeTeam"]["name"], "away": m["awayTeam"]["name"]}
            for m in d.get("data") or []]
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(slim), encoding="utf-8")
    return slim


def match_id(ev: dict) -> int | None:
    from futbol.oddspapi import team_similarity
    if ev.get("league") not in LEAGUES:
        return None
    t = pd.Timestamp(ev["date"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    best, score = None, 0.0
    for m in matches(ev["league"], t.strftime("%Y-%m-%d")):
        if abs(pd.Timestamp(m["date"]) - t) > pd.Timedelta(hours=3):
            continue
        s = min(team_similarity(ev["home"], m["home"]), team_similarity(ev["away"], m["away"]))
        if s > score:
            best, score = m["id"], s
    return best if score >= 0.5 else None


def should_ask(ev: dict, priority: bool) -> bool:
    if ev.get("league") not in LEAGUES or not key():
        return False
    t = pd.Timestamp(ev["date"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    mins = (t - pd.Timestamp.now(tz="UTC")).total_seconds() / 60
    if not (-10 <= mins <= HL_BEFORE_MIN):
        return False
    left = HL_DAILY_CAP - usage_today()
    if left <= 0 or (left < HL_PRIORITY_RESERVE and not priority):
        return False
    every = HL_EVERY_MIN if priority else HL_EVERY_MIN * 2
    if time.time() - _last_try.get(ev["event_id"], 0) < every * 60:
        return False
    _last_try[ev["event_id"]] = time.time()
    return True


def lineup(ev: dict, cands_by_team: dict[str, dict[str, str]]) -> list[dict]:
    """Formación de Highlightly en el mismo formato que espn.lineup(). [] si no salió."""
    from futbol.oddspapi import team_similarity
    mid = match_id(ev)
    if mid is None:
        return []
    f = DIR / f"lineup_{mid}.json"
    if f.exists():
        resp = json.loads(f.read_text(encoding="utf-8"))
    else:
        try:
            resp = _get(f"/lineups/{mid}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:          # todavía no la publicaron
                return []
            raise
        teams = [resp.get("homeTeam") or {}, resp.get("awayTeam") or {}]
        if not any(t.get("initialLineup") for t in teams):
            return []
        if all(t.get("initialLineup") for t in teams):
            DIR.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(resp), encoding="utf-8")
    out = []
    for t in (resp.get("homeTeam") or {}, resp.get("awayTeam") or {}):
        rows = t.get("initialLineup") or []
        if not rows:
            continue
        name = t.get("name") or ""
        side = "home" if team_similarity(name, ev["home"]) >= team_similarity(name, ev["away"]) else "away"
        tid = ev[f"{side}_id"]
        cands = cands_by_team.get(tid, {})
        place, max_row = 0, len(rows)
        from futbol.lineups import grid_labels
        labels = grid_labels(t.get("formation"), [len(r) for r in rows[1:]])
        for ri, row in enumerate(rows, start=1):
            for ci, p in enumerate(row, start=1):
                place += 1
                if ri == 1:
                    pos = "G"
                elif labels:
                    pos = labels[ri - 2][ci - 1]       # mismo puesto que usaría ESPN
                else:
                    pos = af._pseudo_pos(ri, ci, len(row), max_row)
                pid = af._match_player(p.get("name") or "", cands)
                out.append({"team_id": tid, "formation": t.get("formation"), "formation_place": place,
                            "src": "highlightly", "player_id": pid or f"hl:{p.get('id')}",
                            "player": p.get("name"), "starter": True, "pos": pos,
                            "pos_group": {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M",
                                          "Forward": "F"}.get(p.get("position"), "M")})
        for p in t.get("substitutes") or []:
            pid = af._match_player(p.get("name") or "", cands)
            out.append({"team_id": tid, "formation": t.get("formation"), "formation_place": None,
                        "player_id": pid or f"hl:{p.get('id')}", "player": p.get("name"), "starter": False,
                        "pos": "SUB", "pos_group": {"Goalkeeper": "G", "Defender": "D", "Midfielder": "M",
                                                    "Forward": "F"}.get(p.get("position"), "M")})
    return out
