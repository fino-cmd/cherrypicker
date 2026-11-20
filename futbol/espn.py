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
import time
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
    # "Jr"/"Junior" se saca sólo como sufijo final ("Neymar Jr"): como nombre
    # ("Júnior Santos", "Eli Junior Kroupi") es parte del nombre, y sacarlo deja
    # "santos" a secas, que se confunde con cualquier otro Santos.
    w = s.split()
    while len(w) > 1 and w[-1] in {"jr", "junior", "sr", "ii", "iii"}:
        w.pop()
    return " ".join(w)


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
    code = (cfg.LEAGUES.get(league) or {}).get("espn") or league     # o el código de ESPN tal cual
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
            "saves": _num(st.get("saves")),            # atajadas del equipo (futbol.saves)
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
                "saves": _num(stats.get("saves")) if "saves" in stats else None,
                "goals_conceded": _num(stats.get("goalsConceded")) if "saves" in stats else None,
            })
    return {"teams": teams_out, "players": players_out}


def fetch_event(ev: dict, refresh: bool = False) -> dict | None:
    path = _cache_path(ev["league"], ev["event_id"])
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    code = (cfg.LEAGUES.get(ev["league"]) or {}).get("espn") or ev["league"]
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


# Lo terminado sólo cambia cuando termina un partido: 10' en memoria alcanzan y
# cada simulación se ahorra ~5 s (calendario de ESPN + ~3.000 archivos).
HISTORY_TTL_S = 600
_history_mem: dict = {}


def build_history(leagues=None, start: str = cfg.HISTORY_FROM, workers: int = 6,
                  verbose: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every finished match of the leagues since `start` → (teams, players)."""
    leagues = leagues or list(cfg.LEAGUES)
    key = (tuple(sorted(leagues)), start)
    hit = _history_mem.get(key)
    if hit and time.time() - hit[0] < HISTORY_TTL_S:
        return hit[1].copy(), hit[2].copy()
    tdf, pdf = _build_history(leagues, start, workers, verbose)
    _history_mem[key] = (time.time(), tdf, pdf)
    return tdf.copy(), pdf.copy()


def _build_history(leagues, start, workers, verbose):
    events = []
    for lg in leagues:
        for mm in months_between(start):
            try:
                events += [e for e in month_events(lg, mm) if e["state"] == "post"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("ESPN calendario %s %s: %s", lg, mm, exc)
    if set(leagues) & cfg.NATIONAL:
        # Selecciones: la historia de club de quienes juegan fuera de nuestras ligas
        for lg in cfg.HISTORY_ONLY:
            for mm in months_between(start):
                try:
                    events += [e for e in month_events(lg, mm) if e["state"] == "post"]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("ESPN calendario %s %s: %s", lg, mm, exc)
    try:
        events += _side_events(leagues, start)       # liga local de los equipos de copa
    except Exception as exc:  # noqa: BLE001
        logger.warning("ESPN partidos extra de copa: %s", exc)
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
    pdf = _apply_splits(pdf, full=set(leagues) >= set(cfg.LEAGUES))   # dos homónimos con el mismo id → ids distintos
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
    for o in out:
        o["player_id"] = split_id(o["player_id"], o["team_id"])
    return out


# ── Ids mezclados: dos jugadores con el mismo id de ESPN ─────────────────────
# ESPN a veces le da el mismo id a dos jugadores homónimos de equipos distintos
# (Santiago Sosa de Racing/Vasco y el de Defensa y Justicia: 271473). La
# historia los sumaba como uno. Se detecta porque "juega" para dos equipos con
# menos de SPLIT_DAYS días de diferencia, SPLIT_MIN_CLASH veces o más (un pase
# real choca a lo sumo una vez, el fin de semana del cambio de club). Cada
# cadena de equipos que no chocan entre sí es una persona: la que tiene más
# partidos se queda con el id de ESPN y las otras pasan a "id~equipo".

SPLIT_DAYS = 3
SPLIT_MIN_CLASH = 2
SPLITS_FILE = cfg.CACHE_DIR / "espn" / "id_splits.json"
_splits_mem: dict = {"mtime": None, "map": {}}


def _find_splits(pdf: pd.DataFrame) -> dict[str, dict[str, str]]:
    """→ {id de ESPN: {team_id: id nuevo}} sólo para las cadenas que cambian de id."""
    out: dict[str, dict[str, str]] = {}
    p = pdf[["player_id", "team_id", "date"]].assign(pid=pdf["player_id"].astype(str), tid=pdf["team_id"].astype(str))
    multi = p.groupby("pid")["tid"].nunique()
    sub = p[p["pid"].isin(multi[multi > 1].index)].sort_values("date", kind="stable")
    for pid, g in sub.groupby("pid", sort=False):       # una pasada (antes filtraba todo por jugador)
        rows = list(zip(pd.to_datetime(g["date"]), g["tid"]))
        clash: dict[frozenset, int] = {}
        for i, (d1, t1) in enumerate(rows):
            for d2, t2 in rows[i + 1:]:
                if (d2 - d1).days > SPLIT_DAYS:
                    break
                if t1 != t2:
                    k = frozenset((t1, t2))
                    clash[k] = clash.get(k, 0) + 1
        if not any(n >= SPLIT_MIN_CLASH for n in clash.values()):
            continue
        chains: list[list[str]] = []
        for t in dict.fromkeys(g["tid"]):                       # equipos en orden de aparición
            for ch in chains:
                if all(clash.get(frozenset((t, x)), 0) < SPLIT_MIN_CLASH for x in ch):
                    ch.append(t)
                    break
            else:
                chains.append([t])
        if len(chains) < 2:
            continue
        size = {i: int(g["tid"].isin(ch).sum()) for i, ch in enumerate(chains)}
        main = max(size, key=size.get)
        for i, ch in enumerate(chains):
            if i != main:
                new = f"{pid}~{min(ch)}"
                out.setdefault(pid, {}).update({t: new for t in ch})
    return out


def _apply_splits(pdf: pd.DataFrame, full: bool = True) -> pd.DataFrame:
    """Reescribe los ids mezclados en la historia. El mapa se calcula sólo con
    TODAS las ligas (con una sola, una cadena como Racing → Vasco queda cortada
    y cambia quién conserva el id) y se guarda; las historias de menos ligas
    usan ese mismo mapa."""
    if pdf.empty:
        return pdf
    if full:
        m = _find_splits(pdf)
        SPLITS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SPLITS_FILE.write_text(json.dumps(m, ensure_ascii=False, indent=0), encoding="utf-8")
    else:
        if not SPLITS_FILE.exists():
            build_history(list(cfg.LEAGUES), verbose=False)   # arma el mapa con todas las ligas
        try:
            m = json.loads(SPLITS_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            m = {}
    if not m:
        return pdf
    pid, tid = pdf["player_id"].astype(str), pdf["team_id"].astype(str)
    new = [m.get(a, {}).get(b, a) for a, b in zip(pid, tid)]
    if m and verbose_splits:
        logger.info("ESPN: %d ids mezclados separados", len(m))
    return pdf.assign(player_id=new)


verbose_splits = False


def split_id(player_id, team_id) -> str:
    """Id de la app para un jugador de ESPN en ese equipo (el mismo, salvo ids mezclados)."""
    try:
        mt = SPLITS_FILE.stat().st_mtime
    except OSError:
        return str(player_id)
    if _splits_mem["mtime"] != mt:
        try:
            _splits_mem.update(mtime=mt, map=json.loads(SPLITS_FILE.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return str(player_id)
    return (_splits_mem["map"].get(str(player_id)) or {}).get(str(team_id), str(player_id))


def raw_id(player_id) -> str:
    """El id de ESPN detrás de un id de la app ("271473~8950" → "271473")."""
    return str(player_id).split("~")[0]


# ── Plantel: puesto de los que nunca jugaron con posición ───────────────────
# Un suplente que entra pocos minutos queda en la historia como "SUB" en todos
# sus partidos (Nazareno Roselli, Defensa y Justicia) y el modelo no sabe dónde
# juega. El plantel de ESPN sí trae el puesto (D/M/F/G). Se guarda un día.

_roster_mem: dict = {}


def roster_positions(league: str, team_id) -> dict[str, str]:
    """{player_id de la app: G/D/M/F} según el plantel de ESPN del equipo."""
    team_id = str(team_id)
    f = cfg.CACHE_DIR / "espn" / "rosters" / f"{league}_{team_id}.json"
    key = (league, team_id)
    if key in _roster_mem and time.time() - _roster_mem[key][0] < 3600:
        return _roster_mem[key][1]
    data = None
    if f.exists() and time.time() - f.stat().st_mtime < 86400:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = None
    if data is None:
        try:
            code = (cfg.LEAGUES.get(league) or {}).get("espn") or league
            r = httpx.get(f"{SITE}/{code}/teams/{team_id}/roster", timeout=20)
            r.raise_for_status()
            data = {str(a["id"]): (a.get("position") or {}).get("abbreviation")
                    for a in r.json().get("athletes") or [] if a.get("id")}
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data), encoding="utf-8")
        except Exception:  # noqa: BLE001
            data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    out = {}
    for pid, ab in data.items():
        g = position_group(ab) if ab else "?"
        if g in ("G", "D", "M", "F"):
            out[split_id(pid, team_id)] = g
    _roster_mem[key] = (time.time(), out)
    return out


# ── Equipos de copa de ligas que no seguimos ────────────────────────────────
# Bayern, Real Madrid, PSG… sólo aparecen en la Champions/Europa League (3-8
# partidos): pocos minutos por jugador y una formación probable armada con
# partidos de hace un mes. Se suman TODOS sus partidos (liga local y copas
# nacionales) desde el calendario de cada equipo en ESPN. Esas filas llevan la
# liga de ESPN ("ger.1", "esp.1"…) y sólo sirven para el historial de esos
# jugadores y equipos (el modelo de equipos de la copa y los promedios
# generales siguen saliendo de las ligas de la app).

SIDE_SKIP = {"club.friendly"}
SCHEDULE_TTL_S = 12 * 3600


def team_schedule(team_id: str, season: int) -> list[dict]:
    """Partidos de un equipo en una temporada (todas las competencias)."""
    f = cfg.CACHE_DIR / "espn" / "schedules" / f"{team_id}_{season}.json"
    closed = season < datetime.now(timezone.utc).year - 1
    if f.exists() and (closed or time.time() - f.stat().st_mtime < SCHEDULE_TTL_S):
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    try:
        r = httpx.get(f"{SITE}/all/teams/{team_id}/schedule", params={"season": season}, timeout=30)
        r.raise_for_status()
        raw = r.json().get("events") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("ESPN calendario equipo %s %s: %s", team_id, season, exc)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    out = []
    for ev in raw:
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors") or []}
        slug = (ev.get("league") or {}).get("slug")
        if "home" not in teams or "away" not in teams or not slug:
            continue
        out.append({
            "event_id": str(ev["id"]), "league": slug, "date": ev.get("date"),
            "state": (((comp.get("status") or ev.get("status") or {}).get("type")) or {}).get("state"),
            "home_id": str(teams["home"].get("id") or (teams["home"].get("team") or {}).get("id")),
            "away_id": str(teams["away"].get("id") or (teams["away"].get("team") or {}).get("id")),
        })
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def _side_events(leagues, start: str) -> list[dict]:
    """Partidos extra de los equipos de copa que no juegan en nuestras ligas."""
    cups = [lg for lg in leagues if lg in cfg.CUPS]
    if not cups:
        return []
    now = datetime.now(timezone.utc)
    months = months_between(start, (now + pd.Timedelta(days=40)).date())
    core = [lg for lg in cfg.LEAGUES if lg not in cfg.CUPS]
    covered, cup_teams = set(), set()
    for lg in core:
        for mm in months:
            try:
                covered |= {t for e in month_events(lg, mm) for t in (e["home_id"], e["away_id"])}
            except Exception:  # noqa: BLE001
                pass
    for lg in cups:
        for mm in months:
            try:
                cup_teams |= {t for e in month_events(lg, mm) for t in (e["home_id"], e["away_id"])}
            except Exception:  # noqa: BLE001
                pass
    ours = {v["espn"] for v in cfg.LEAGUES.values()}
    first = pd.Timestamp(f"{start[:4]}-{start[4:]}-01", tz="UTC")
    seasons = range(int(start[:4]) - 1, now.year + 1)
    teams = sorted(cup_teams - covered)
    out = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda a: team_schedule(*a), [(t, s) for t in teams for s in seasons]))
    for evs in res:
        for e in evs:
            if e["state"] != "post" or e["league"] in SIDE_SKIP or e["league"] in ours:
                continue
            if pd.Timestamp(e["date"]) < first:
                continue
            out[e["event_id"]] = e
    return list(out.values())
