"""
Futbol — alineaciones de PitchAPI (fuente de las formaciones oficiales desde 02/11/2026).

API REST gratis, sin cupo diario (sólo un tope de ráfaga que devuelve 429).
Clave en backend/.env: PITCHAPI_KEY (header X-API-KEY).

  GET /v1/date/{AAAA-MM-DD}?status=all   partidos del día (con su liga y equipos)
  GET /v1/matches/{id}/lineups           formación de cada lado: confirmed (oficial)
                                          o predicha (hasta 48 h antes), titulares
                                          con pitch_x (línea) / pitch_y (0 izq · 1 der)

Sólo cuenta como oficial un lado con confirmed = true. Devuelve el mismo
formato que espn.lineup() (puesto al estilo ESPN, armado desde las
coordenadas), así el resto de la app no cambia.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd

from futbol import config as cfg

BASE = "https://api.pitchapi.dev/v1"
DIR = cfg.CACHE_DIR / "pitchapi"
# Liga de la app → id de liga en PitchAPI (GET /v1/leagues).
LEAGUE = {"arg": "l_45VZuL", "bra": "l_1D8Xrl", "eng": "l_4WFCIZ", "ita": "l_0ALvwF", "esp": "l_0ErfuF",
          "ucl": "l_0bfbkO", "uel": "l_38d9HA"}
DAY_TTL_S = 10 * 60          # la lista de partidos de un día se vuelve a pedir cada 10'
BEFORE_MIN = 90              # se empieza a preguntar 90' antes del inicio…
EVERY_S = 90                 # … cada 90 segundos (no hay cupo diario)
_last_try: dict[str, float] = {}


def key() -> str | None:
    return os.getenv("PITCHAPI_KEY")


def _get(path: str, **params):
    if not key():
        raise RuntimeError("Falta PITCHAPI_KEY en backend/.env")
    for attempt in range(3):
        r = httpx.get(f"{BASE}/{path.lstrip('/')}", params=params, headers={"X-API-KEY": key()}, timeout=30)
        _log(path, r)
        if r.status_code == 429:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 5)))
            continue
        d = r.json()
        if "error" in d:
            if d["error"].get("code") == "RESOURCE_NOT_FOUND":
                return None
            raise RuntimeError(f"PitchAPI: {d['error'].get('code')} {d['error'].get('message')}")
        return d.get("data")
    raise RuntimeError("PitchAPI: demasiados pedidos seguidos (429)")


def _log(path: str, r) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    f = DIR / "requests.csv"
    new = not f.exists()
    with f.open("a", encoding="utf-8") as fh:
        if new:
            fh.write("utc,path,status,bytes\n")
        fh.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')},{path},{r.status_code},{len(r.content)}\n")


# ── Partidos ─────────────────────────────────────────────────────────────────

def day_matches(day: str) -> list[dict]:
    """Partidos de nuestras ligas en un día (UTC), guardados DAY_TTL_S."""
    f = DIR / f"date_{day}.json"
    if f.exists() and time.time() - f.stat().st_mtime < DAY_TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    data = _get(f"date/{day}", status="all") or {}
    ours = {v: k for k, v in LEAGUE.items()}
    out = [{"id": m["id"], "league": ours[m["league"]["id"]], "time_utc": m.get("time_utc"),
            "home": (m.get("home_team") or {}).get("name"), "away": (m.get("away_team") or {}).get("name")}
           for m in data.get("matches") or [] if (m.get("league") or {}).get("id") in ours]
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def match_for_event(ev: dict) -> dict | None:
    """El partido de PitchAPI que corresponde a uno de ESPN (misma liga, ±3 h, nombres)."""
    from futbol.oddspapi import team_similarity
    lg = ev.get("league")
    if lg not in LEAGUE:
        return None
    t = pd.Timestamp(ev["date"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    days = sorted({(t + timedelta(hours=h)).strftime("%Y-%m-%d") for h in (-3, 0, 3)})
    best, score = None, 0.0
    for day in days:
        for m in day_matches(day):
            if m["league"] != lg or not m.get("time_utc"):
                continue
            if abs(pd.Timestamp(m["time_utc"]) - t) > pd.Timedelta(hours=3):
                continue
            sh, sa = team_similarity(ev["home"], m["home"] or ""), team_similarity(ev["away"], m["away"] or "")
            s = min(sh, sa)
            if max(sh, sa) >= 0.95 and min(sh, sa) >= 0.25:
                s = max(s, 0.9)
            if s > score:
                best, score = m, s
    return best if score >= 0.6 else None


# ── Alineación ───────────────────────────────────────────────────────────────

def _positions(starters: list[dict]) -> list[str]:
    """Puesto al estilo ESPN desde las coordenadas: las líneas salen de pitch_x
    (arquero, defensa, medio…), y dentro de cada línea el orden de pitch_y
    (0 = izquierda) da el lado. Se reusa la traducción de la grilla de API-Football."""
    from futbol.apifootball import _pseudo_pos
    xs = sorted({round(float(p.get("pitch_x") or 0), 2) for p in starters})
    rows: list[float] = []
    for x in xs:                                   # x a menos de .04 = misma línea
        if not rows or x - rows[-1] > 0.04:
            rows.append(x)
    row_of = lambda x: min(range(len(rows)), key=lambda i: abs(rows[i] - x)) + 1
    by_row: dict[int, list[dict]] = {}
    for p in starters:
        by_row.setdefault(row_of(round(float(p.get("pitch_x") or 0), 2)), []).append(p)
    max_row = len(rows)
    pos = {}
    for r, ps in by_row.items():
        ps = sorted(ps, key=lambda p: float(p.get("pitch_y") or 0))
        for c, p in enumerate(ps, 1):
            pos[p["player_id"]] = _pseudo_pos(r, c, len(ps), max_row)
    return [pos[p["player_id"]] for p in starters]


def _group(pos: str) -> str:
    from futbol.espn import position_group
    return position_group(pos)


def lineup(ev: dict, cands_by_team: dict[str, dict[str, str]]) -> list[dict]:
    """Formación OFICIAL de PitchAPI para un partido de ESPN, en el formato de
    espn.lineup(). Sólo los lados con confirmed = true; [] si todavía no salió.
    `cands_by_team`: por team_id de ESPN, nombre normalizado → player_id."""
    from futbol.apifootball import _match_player
    m = match_for_event(ev)
    if m is None:
        return []
    f = DIR / f"lineup_{m['id']}.json"
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
    else:
        data = _get(f"matches/{m['id']}/lineups") or {}
        # Se guarda sólo cuando los dos lados son oficiales (si no, se vuelve a preguntar).
        if all((data.get(s) or {}).get("confirmed") for s in ("home", "away")):
            DIR.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = []
    for side in ("home", "away"):
        s = data.get(side) or {}
        if not s.get("confirmed") or len(s.get("starters") or []) < 11:
            continue
        tid = str(ev[f"{side}_id"])
        cands = cands_by_team.get(tid, {})
        st = s["starters"]
        for i, (p, pos) in enumerate(zip(st, _positions(st))):
            pid = _match_player(p["name"], cands)
            out.append({"team_id": tid, "formation": s.get("formation"), "formation_place": i + 1, "src": "pitchapi",
                        "player_id": pid or f"pa:{p['player_id']}", "player": p["name"], "starter": True,
                        "pos": pos, "pos_group": _group(pos)})
        for p in s.get("subs") or []:
            pid = _match_player(p["name"], cands)
            grp = {0: "G", 1: "D", 2: "M", 3: "F"}.get(p.get("position_id"), "M")
            out.append({"team_id": tid, "formation": s.get("formation"), "formation_place": None,
                        "player_id": pid or f"pa:{p['player_id']}", "player": p["name"], "starter": False,
                        "pos": "SUB", "pos_group": grp})
    return out


def should_ask(ev: dict, priority: bool = False) -> bool:
    """¿Le pregunto ahora? Desde BEFORE_MIN antes del inicio hasta 10' después,
    cada EVERY_S. Gratis y sin cupo: no hace falta priorizar."""
    if ev.get("league") not in LEAGUE or not key():
        return False
    t = pd.Timestamp(ev["date"])
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    mins = (t - pd.Timestamp.now(tz="UTC")).total_seconds() / 60
    if not (-10 <= mins <= BEFORE_MIN):
        return False
    if time.time() - _last_try.get(ev["event_id"], 0) < EVERY_S:
        return False
    _last_try[ev["event_id"]] = time.time()
    return True
