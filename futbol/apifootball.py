"""
Futbol — alineaciones rápidas de API-Football (api-sports.io).

ESPN publica las formaciones de la Liga Profesional tarde (a veces casi al
empezar). API-Football las tiene ~40-60 min antes. Plan gratis: 100 consultas
por día, y sólo deja consultar POR FECHA (no por temporada actual), que es lo
que usamos:
  • /fixtures?date=YYYY-MM-DD  → todos los partidos del día (1 consulta, se guarda)
  • /fixtures/lineups?fixture=ID → titulares y suplentes con su lugar en la cancha

Cuidado del cupo:
  • sólo ligas de AF_LEAGUES (hoy la LPF), desde AF_BEFORE_MIN antes del partido;
  • a cada partido se le pregunta como mucho cada AF_EVERY_MIN minutos;
  • una formación encontrada se guarda y no se vuelve a pedir;
  • tope diario AF_DAILY_CAP; con poco cupo, sólo partidos que seguís o con jugadas.

La clave va en backend/.env como APIFOOTBALL_KEY.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from futbol import config as cfg
from futbol.espn import norm_name

BASE = "https://v3.football.api-sports.io"
DIR = cfg.CACHE_DIR / "apifootball"
AF_LEAGUES = {"arg": 128}           # ligas donde ESPN llega tarde (formaciones)
PHOTO_LEAGUES = {"arg": 128, "bra": 71, "eng": 39}   # fotos: las tres ligas
PHOTO_TEAMS_PER_DAY = 20            # planteles por día (1 consulta cada uno)
PHOTO_REFRESH_DAYS = 30             # un plantel se vuelve a pedir al mes
AF_BEFORE_MIN = 70                  # empezar a preguntar 70' antes
AF_EVERY_MIN = 5                    # cada cuánto, por partido
AF_DAILY_CAP = 90                   # nunca pasar de esto (el plan da 100)
AF_PRIORITY_RESERVE = 25            # con menos cupo que esto, sólo prioritarios
FIXTURES_TTL_S = 6 * 3600
MIN_GAP_S = 6.5
_last_call = [0.0]


def key() -> str | None:
    return os.getenv("APIFOOTBALL_KEY")


# ── Cupo diario ──────────────────────────────────────────────────────────────
def _usage_file():
    return DIR / "usage.json"


def usage_today() -> int:
    try:
        u = json.loads(_usage_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    return int(u.get(datetime.now(timezone.utc).strftime("%Y-%m-%d"), 0))


def _count(remaining: str | None) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        u = json.loads(_usage_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        u = {}
    u = {day: int(u.get(day, 0)) + 1, "remaining": remaining}
    _usage_file().write_text(json.dumps(u), encoding="utf-8")


def _get(path: str, **params) -> list:
    k = key()
    if not k:
        raise RuntimeError("Falta APIFOOTBALL_KEY en backend/.env")
    if usage_today() >= AF_DAILY_CAP:
        raise RuntimeError("Cupo diario de API-Football agotado")
    # El plan gratis admite 10 consultas por minuto: una cada MIN_GAP_S como mucho.
    wait = MIN_GAP_S - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()
    r = httpx.get(f"{BASE}/{path}", params=params, headers={"x-apisports-key": k}, timeout=30)
    _count(r.headers.get("x-ratelimit-requests-remaining"))
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError(str(d["errors"]))
    return d.get("response") or []


# ── Partidos del día ─────────────────────────────────────────────────────────
def fixtures(day: str) -> list[dict]:
    """Partidos de todas las ligas en `day` (UTC), guardados FIXTURES_TTL_S."""
    f = DIR / f"fixtures2_{day}.json"
    if f.exists() and time.time() - f.stat().st_mtime < FIXTURES_TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    raw = _get("fixtures", date=day, timezone="UTC")
    keep = set(AF_LEAGUES.values()) | set(PHOTO_LEAGUES.values())
    slim = [{"id": x["fixture"]["id"], "date": x["fixture"]["date"], "league": x["league"]["id"],
             "home": x["teams"]["home"]["name"], "away": x["teams"]["away"]["name"],
             "home_id": x["teams"]["home"]["id"], "away_id": x["teams"]["away"]["id"]}
            for x in raw if x["league"]["id"] in keep]
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(slim), encoding="utf-8")
    return slim


def _date_allowed(day: str) -> bool:
    """El plan gratis sólo deja consultar fechas entre ayer y mañana."""
    d = pd.Timestamp(day).date()
    today = pd.Timestamp.now(tz="UTC").date()
    return abs((d - today).days) <= 1


def match_fixture(ev: dict, lid: int | None = None) -> dict | None:
    from futbol.oddspapi import team_similarity
    lid = lid or AF_LEAGUES.get(ev["league"])
    if lid is None:
        return None
    t = pd.Timestamp(ev["date"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    best, score = None, 0.0
    for day in {t.strftime("%Y-%m-%d"), (t - pd.Timedelta(hours=6)).strftime("%Y-%m-%d")}:
        if not _date_allowed(day):
            continue
        for fx in fixtures(day):
            if fx["league"] != lid or abs(pd.Timestamp(fx["date"]) - t) > pd.Timedelta(hours=3):
                continue
            s = min(team_similarity(ev["home"], fx["home"]), team_similarity(ev["away"], fx["away"]))
            if s > score:
                best, score = fx, s
    return best if score >= 0.5 else None


# ── Alineación ───────────────────────────────────────────────────────────────
def _pseudo_pos(row: int, col: int, n: int, max_row: int) -> str:
    """Puesto al estilo ESPN desde la grilla 'fila:columna' (columna 1 = izquierda)."""
    mid = (n + 1) / 2
    if row == 1:
        return "G"
    if row == 2:                                         # defensa
        if n >= 4 and col == 1:
            return "LB"
        if n >= 4 and col == n:
            return "RB"
        return "CD" if col == mid else ("CD-L" if col < mid else "CD-R")
    if row == max_row:                                   # ataque
        if n == 1:
            return "F"
        if n == 2:
            return "CF-L" if col == 1 else "CF-R"
        return "LF" if col == 1 else "RF" if col == n else "F"
    if row == max_row - 1 and max_row >= 5:              # enganche / extremos del 4-2-3-1
        if n == 1:
            return "AM"
        return "AM-L" if col == 1 else "AM-R" if col == n else "AM"
    if n == 1:
        return "DM"
    if n >= 4:
        return "LM" if col == 1 else "RM" if col == n else ("CM-L" if col < mid else "CM-R")
    return "CM" if col == mid else ("CM-L" if col < mid else "CM-R")


def _match_player(name: str, cands: dict[str, str]) -> str | None:
    """'F. Zenobio' / 'Luciano Vallejo' → player_id de ESPN (nombre normalizado → id)."""
    k = norm_name(name)
    if k in cands:
        return cands[k]
    toks = k.split()
    if not toks:
        return None
    first = toks[0] if len(toks[0]) > 1 else None
    initial = toks[0][0]
    surname = toks[1:] if (len(toks) > 1 and len(toks[0]) == 1) else toks[-1:]
    hits = []
    for ck, pid in cands.items():
        ct = ck.split()
        if not ct or not all(s in ct for s in surname):
            continue
        if first and first in ct:
            hits.append((2, pid))
        elif ct[0][0] == initial:
            hits.append((1, pid))
        elif len(surname) >= 2:
            hits.append((0, pid))
        else:
            hits.append((-1, pid))           # sólo el apellido: vale si es único en el plantel
    if not hits:
        return None
    top = max(h[0] for h in hits)
    ids = {pid for sc, pid in hits if sc == top}
    return ids.pop() if len(ids) == 1 else None


def lineup(ev: dict, cands_by_team: dict[str, dict[str, str]]) -> list[dict]:
    """Formación de API-Football para un partido de ESPN, en el mismo formato
    que espn.lineup(). [] si todavía no salió. `cands_by_team`: por team_id de
    ESPN, nombre normalizado → player_id (de la historia del plantel)."""
    fx = match_fixture(ev)
    if fx is None:
        return []
    f = DIR / f"lineup_{fx['id']}.json"
    if f.exists():
        resp = json.loads(f.read_text(encoding="utf-8"))
    else:
        resp = _get("fixtures/lineups", fixture=fx["id"])
        if not resp or not any(t.get("startXI") for t in resp):
            return []
        f.write_text(json.dumps(resp), encoding="utf-8")
    from futbol.oddspapi import team_similarity
    out = []
    for t in resp:
        side = "home" if team_similarity(t["team"]["name"], ev["home"]) >= team_similarity(t["team"]["name"], ev["away"]) else "away"
        tid = ev[f"{side}_id"]
        cands = cands_by_team.get(tid, {})
        xi = [p["player"] for p in t.get("startXI") or []]
        grid = [(int(p["grid"].split(":")[0]), int(p["grid"].split(":")[1])) if p.get("grid") else (0, 0) for p in xi]
        max_row = max((g[0] for g in grid), default=0)
        per_row = {}
        for r, c in grid:
            per_row.setdefault(r, []).append(c)
        for i, (p, (r, c)) in enumerate(zip(xi, grid)):
            cols = sorted(per_row.get(r, [c]))
            pos = _pseudo_pos(r, cols.index(c) + 1 if c in cols else 1, len(cols), max_row) if r else (p.get("pos") or "M")
            pid = _match_player(p["name"], cands)
            if pid:
                _photos_new[pid] = p.get("photo") or photo_url(p["id"])
            out.append({"team_id": tid, "formation": t.get("formation"), "formation_place": i + 1,
                        "player_id": pid or f"af:{p['id']}", "player": p["name"], "starter": True,
                        "pos": pos, "pos_group": p.get("pos") if p.get("pos") in ("G", "D", "M", "F") else "M"})
        for p in (x["player"] for x in t.get("substitutes") or []):
            pid = _match_player(p["name"], cands)
            if pid:
                _photos_new[pid] = p.get("photo") or photo_url(p["id"])
            out.append({"team_id": tid, "formation": t.get("formation"), "formation_place": None,
                        "player_id": pid or f"af:{p['id']}", "player": p["name"], "starter": False,
                        "pos": "SUB", "pos_group": p.get("pos") if p.get("pos") in ("G", "D", "M", "F") else "M"})
    _flush_photos()
    return out


# ── Fotos: todas iguales (150×150, cabeza y hombros) ─────────────────────────
_photos_new: dict[str, str] = {}


def photo_url(af_player_id) -> str:
    return f"https://media.api-sports.io/football/players/{af_player_id}.png"


def _flush_photos() -> int:
    """Guarda las fotos nuevas en el mismo almacén que usa la app (pisa las viejas)."""
    if not _photos_new:
        return 0
    from futbol import photos
    store = photos._load()
    for pid, url in _photos_new.items():
        store[str(pid)] = {"url": url, "src": "api-football"}
    photos._save(store)
    n = len(_photos_new)
    _photos_new.clear()
    return n


def photos_step(matches: list[dict], players, max_teams: int = PHOTO_TEAMS_PER_DAY, verbose: bool = False) -> int:
    """Pide de a poco los planteles (1 consulta por equipo) y cambia las fotos
    de sus jugadores por las de API-Football. Primero los equipos que juegan
    antes. Respeta el cupo: deja AF_PRIORITY_RESERVE para las formaciones.
    `players`: historia de ESPN (DataFrame) para cruzar nombres."""
    DIR.mkdir(parents=True, exist_ok=True)
    done_f = DIR / "squads.json"
    try:
        done = json.loads(done_f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        done = {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used_today = sum(1 for v in done.values() if v.get("date") == today)
    budget = min(max_teams - used_today, AF_DAILY_CAP - usage_today() - AF_PRIORITY_RESERVE)
    if budget <= 0:
        return 0
    # Equipos de ESPN → id de API-Football, por los partidos de la jornada.
    queue = []
    for m in sorted(matches, key=lambda x: x["date"]):
        ev = m.get("ev")
        lid = PHOTO_LEAGUES.get(m.get("league"))
        if not ev or not lid:
            continue
        need = [s for s in ("home", "away")
                if (done.get(ev[f"{s}_id"]) or {}).get("date", "2000-01-01") < (pd.Timestamp.now() - pd.Timedelta(days=PHOTO_REFRESH_DAYS)).strftime("%Y-%m-%d")]
        if not need:
            continue
        try:
            fx = match_fixture(ev, lid)
        except Exception as exc:  # noqa: BLE001 - un partido que falla no corta el resto
            if verbose:
                print(f"  fotos: {exc}")
            continue
        if fx is None:
            continue
        for sd in need:
            queue.append((ev[f"{sd}_id"], fx[f"{sd}_id"], ev[sd]))
    n_photos, seen = 0, set()
    last = players.sort_values("date").groupby("player_id").tail(1)
    for espn_tid, af_tid, name in queue:
        if budget <= 0:
            break
        if espn_tid in seen:
            continue
        seen.add(espn_tid)
        try:
            resp = _get("players/squads", team=af_tid)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"  fotos: {exc}")
            break
        budget -= 1
        cands = {norm_name(r.player): str(r.player_id) for r in last[last["team_id"].astype(str) == str(espn_tid)].itertuples()}
        for p in (resp[0]["players"] if resp else []):
            pid = _match_player(p["name"], cands)
            if pid:
                _photos_new[pid] = p.get("photo") or photo_url(p["id"])
        got = _flush_photos()
        n_photos += got
        done[espn_tid] = {"date": today, "af_team": af_tid, "team": name, "photos": got}
        done_f.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        if verbose:
            print(f"  {name}: {got} fotos")
    return n_photos


# ── ¿Le pregunto ahora a este partido? ───────────────────────────────────────
_last_try: dict[str, float] = {}


def should_ask(ev: dict, priority: bool) -> bool:
    if ev.get("league") not in AF_LEAGUES or not key():
        return False
    t = pd.Timestamp(ev["date"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    mins = (t - pd.Timestamp.now(tz="UTC")).total_seconds() / 60
    if not (-10 <= mins <= AF_BEFORE_MIN):
        return False
    left = AF_DAILY_CAP - usage_today()
    if left <= 0 or (left < AF_PRIORITY_RESERVE and not priority):
        return False
    if time.time() - _last_try.get(ev["event_id"], 0) < AF_EVERY_MIN * 60:
        return False
    _last_try[ev["event_id"]] = time.time()
    return True
