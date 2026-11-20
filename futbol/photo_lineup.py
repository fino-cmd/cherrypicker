"""
Fútbol — "Cargar alineación": leer la formación de una foto (la placa que
publican los clubes, una captura de la tele o de otra app) con Claude.

  read_photo(event_id, image_b64, media_type) → borrador para revisar (no guarda)
  confirm(event_id, sides)                     → la guarda como alineación oficial

La foto se manda a Claude junto con el plantel de los dos equipos (nombre e id
de cada jugador): así cada nombre de la foto se cruza con nuestro id. Si la foto
no muestra los puestos (una lista de nombres), Claude los deduce del dibujo y de
dónde suele jugar cada uno. También lee el banco de suplentes si aparece.

Clave en backend/.env: GEMINI_API_KEY (gratis, Google AI Studio) o ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
import os
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import config as cfg
from . import espn
from . import lineups as lu

MODEL = "claude-opus-5-5"

# Puestos al estilo ESPN (los que entiende el resto del código: line_of / side_of).
Pos = Literal["G", "RB", "CD-R", "CD", "CD-L", "LB", "RWB", "LWB", "SW", "DM", "CM-R", "CM", "CM-L",
              "RM", "LM", "AM-R", "AM", "AM-L", "RW", "LW", "RF", "LF", "CF-R", "CF-L", "F"]


class PhotoPlayer(BaseModel):
    name_in_image: str = Field(description="Nombre tal como aparece en la foto")
    number: Optional[int] = Field(None, description="Número de camiseta si se ve")
    player_id: Optional[str] = Field(None, description="id del plantel que corresponde a este jugador; null si no está en la lista")
    pos: Optional[Pos] = Field(None, description="Puesto en la cancha (sólo titulares)")
    pos_from_image: bool = Field(description="true si el puesto se ve en la foto; false si lo deducís")
    confidence: float = Field(description="0 a 1: qué tan seguro estás del jugador (y del puesto, en titulares)")


class PhotoSide(BaseModel):
    detected: bool = Field(description="¿La foto trae la formación de este equipo?")
    formation: Optional[str] = Field(None, description='Dibujo, p. ej. "4-2-3-1"')
    starters: list[PhotoPlayer] = Field(default_factory=list, description="Los 11 titulares, del arquero hacia adelante")
    bench: list[PhotoPlayer] = Field(default_factory=list, description="Suplentes, si aparecen")


class PhotoLineup(BaseModel):
    home: PhotoSide
    away: PhotoSide
    notes: Optional[str] = Field(None, description="Algo raro o dudoso de la foto, en una frase")


GEMINI_MODEL = os.getenv("GEMINI_MODEL") or "gemini-flash-latest"   # plan gratis de Google AI Studio


def provider() -> str | None:
    """Gemini si hay GEMINI_API_KEY (gratis); si no, Claude con ANTHROPIC_API_KEY."""
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    return None


def has_key() -> bool:
    return provider() is not None


def _gemini_schema() -> dict:
    """El mismo formato de PhotoLineup, en el dialecto de esquemas de Gemini."""
    player = {"type": "OBJECT", "properties": {
        "name_in_image": {"type": "STRING"},
        "number": {"type": "INTEGER", "nullable": True},
        "player_id": {"type": "STRING", "nullable": True},
        "pos": {"type": "STRING", "nullable": True, "enum": list(Pos.__args__)},
        "pos_from_image": {"type": "BOOLEAN"},
        "confidence": {"type": "NUMBER"},
    }, "required": ["name_in_image", "player_id", "pos", "pos_from_image", "confidence"]}
    side = {"type": "OBJECT", "properties": {
        "detected": {"type": "BOOLEAN"},
        "formation": {"type": "STRING", "nullable": True},
        "starters": {"type": "ARRAY", "items": player},
        "bench": {"type": "ARRAY", "items": player},
    }, "required": ["detected", "formation", "starters", "bench"]}
    return {"type": "OBJECT", "properties": {"home": side, "away": side, "notes": {"type": "STRING", "nullable": True}},
            "required": ["home", "away"]}


def _ask_gemini(image_b64: str, media_type: str, text: str) -> PhotoLineup:
    import httpx
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {"contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": media_type, "data": image_b64}}, {"text": text}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": _gemini_schema(),
                                 "temperature": 0.1}}
    r = httpx.post(url, json=body, headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, timeout=120)
    if r.status_code == 429:
        raise RuntimeError("Se terminó el cupo gratis de Gemini por ahora: probá en un rato")
    if r.status_code >= 400:
        try:
            msg = r.json()["error"]["message"]
        except Exception:  # noqa: BLE001
            msg = r.text[:200]
        raise RuntimeError(f"Gemini: {msg}")
    d = r.json()
    try:
        raw = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError):
        raise RuntimeError("Gemini no devolvió una respuesta (¿imagen bloqueada?)")
    return PhotoLineup.model_validate_json(raw)


def _load():
    from .run import JORNADA
    if not JORNADA.exists():
        raise ValueError("Todavía no hay jornada calculada")
    return json.loads(JORNADA.read_text(encoding="utf-8"))


def _match(result: dict, event_id: str) -> dict:
    m = next((x for x in result["matches"] if x["event_id"] == event_id), None)
    if m is None or not m.get("ev"):
        raise ValueError("Partido no encontrado en la jornada")
    return m


def squads(m: dict) -> dict:
    """Plantel de cada lado: player_id → {player, pos}. Junta todo lo que tenemos
    del partido (jugadores del modelo, arqueros, formaciones de las fuentes) y los
    que jugaron en el club según la historia."""
    from .run import _name_cands
    out = {}
    for side in ("home", "away"):
        tid = str(m["ev"][f"{side}_id"])
        pool: dict[str, dict] = {}

        def add(pid, name, pos=None):
            pid = str(pid or "")
            if not pid or pid.startswith(("pa:", "foto:")) or not name:
                return
            if pid not in pool:
                pool[pid] = {"player": name, "pos": pos or ""}
            elif pos and not pool[pid]["pos"]:
                pool[pid]["pos"] = pos

        for p in m.get("players") or []:
            if str(p.get("team_id")) == tid:
                add(p["player_id"], p["player"], p.get("pos"))
        for p in (m.get("keepers") or {}).get(side) or []:
            add(p["player_id"], p["player"], "G")
        for sq in [m.get("official_squad") or []] + list((m.get("lineup_src") or {}).values()):
            for s in sq:
                if str(s.get("team_id")) == tid:
                    add(s["player_id"], s.get("player"), s.get("pos") if s.get("starter") else None)
        for p in ((m.get("lineup") or {}).get(side) or {}).get("players") or []:
            add(p["player_id"], p.get("player"), p.get("pos"))
        try:
            for name, pid in (_name_cands().get(tid) or {}).items():
                add(pid, name.title())
        except Exception:  # noqa: BLE001 - sin historia: alcanza con lo del partido
            pass
        out[side] = pool
    return out


PROMPT = """Esta imagen muestra la alineación de un partido de fútbol: {home} (local) vs {away} (visitante).
Puede ser la placa oficial de un club, una captura de la tele o de otra app, y puede traer uno o los dos equipos.

Para cada equipo que aparezca, leé:
- el dibujo (formation), p. ej. "4-2-3-1";
- los 11 titulares, del arquero hacia adelante;
- los suplentes (bench), si aparecen en la imagen.

Para cada jugador:
- name_in_image: el nombre tal como se ve;
- player_id: el id del plantel de abajo que corresponde a ese jugador. Las placas suelen poner sólo el apellido o un apodo: elegí el del plantel de ESE equipo. Si no está o hay dos posibles y no podés decidir, null;
- pos (sólo titulares): el puesto en la cancha, con estas etiquetas: G arquero; RB/LB laterales; CD-R, CD, CD-L centrales (de derecha a izquierda); RWB/LWB carrileros; SW líbero o 5 tapón; DM volante central defensivo; CM-R, CM, CM-L volantes centrales; RM/LM volantes por afuera; AM-R, AM, AM-L mediapuntas/enganche; RW/LW extremos; RF, LF, CF-R, CF-L, F delanteros. Derecha/izquierda son las del propio equipo mirando al arco rival;
- pos_from_image: true si la imagen muestra dónde juega (dibujo en una cancha, puesto escrito); false si lo deducís (una lista sin ubicación: en ese caso usá el dibujo y el puesto habitual del jugador que figura en el plantel);
- confidence: de 0 a 1, qué tan seguro estás de quién es y de su puesto.

Un equipo que no aparece en la imagen va con detected=false y listas vacías. No inventes jugadores.

Plantel de {home} (local) — id: nombre (puesto habitual):
{home_list}

Plantel de {away} (visitante) — id: nombre (puesto habitual):
{away_list}
"""


def _fmt(pool: dict) -> str:
    return "\n".join(f"{pid}: {v['player']}" + (f" ({v['pos']})" if v.get("pos") else "")
                     for pid, v in pool.items()) or "(sin datos)"


def _ask(image_b64: str, media_type: str, text: str) -> PhotoLineup:
    if provider() == "gemini":
        return _ask_gemini(image_b64, media_type, text)
    import anthropic
    if not has_key():
        raise RuntimeError("Falta GEMINI_API_KEY (o ANTHROPIC_API_KEY) en backend/.env")
    client = anthropic.Anthropic()
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        # Si el pedido se rechaza, la API lo vuelve a correr con otro modelo.
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
            {"type": "text", "text": text},
        ]}],
        output_format=PhotoLineup,
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude no pudo leer la imagen")
    if resp.parsed_output is None:
        raise RuntimeError("No se pudo leer la alineación de la foto")
    return resp.parsed_output


def _row(p: PhotoPlayer, pool: dict, starter: bool, i: int) -> dict:
    pid = p.player_id if p.player_id in pool else None
    pos = (p.pos or "") if starter else ""
    if starter and i == 0 and not pos:
        pos = "G"
    return {"player_id": pid or f"foto:{espn.norm_name(p.name_in_image)}",
            "player": pool[pid]["player"] if pid else p.name_in_image,
            "name_in_image": p.name_in_image, "number": p.number, "known": bool(pid),
            "pos": pos, "line": lu.line_of(pos) if starter else None, "side": lu.side_of(pos) if starter else None,
            "place": i + 1 if starter else None, "pos_from_image": p.pos_from_image,
            "confidence": round(max(0.0, min(1.0, p.confidence if pid else min(p.confidence, 0.4))), 2)}


def read_photo(event_id: str, image_b64: str, media_type: str = "image/jpeg") -> dict:
    """Lee la foto y devuelve el borrador (no guarda nada)."""
    result = _load()
    m = _match(result, event_id)
    pools = squads(m)
    ev = m["ev"]
    text = PROMPT.format(home=ev["home"], away=ev["away"],
                         home_list=_fmt(pools["home"]), away_list=_fmt(pools["away"]))
    out = _ask(image_b64, media_type, text)
    draft = {}
    for side in ("home", "away"):
        s: PhotoSide = getattr(out, side)
        if not s.detected or len(s.starters) < lu.MIN_XI:
            draft[side] = {"detected": False}
            continue
        # Un mismo id no puede estar dos veces: el segundo queda "sin cruzar".
        seen, starters, bench = set(), [], []
        for i, p in enumerate(s.starters[:11]):
            if p.player_id in seen:
                p = p.model_copy(update={"player_id": None})
            seen.add(p.player_id)
            starters.append(_row(p, pools[side], True, i))
        for p in s.bench:
            if p.player_id in seen:
                p = p.model_copy(update={"player_id": None})
            seen.add(p.player_id)
            bench.append(_row(p, pools[side], False, 0))
        form = (s.formation or "").replace(" ", "") or None
        if not form:                                     # la foto no dice el dibujo: sale de los puestos
            n = [sum(1 for r in starters if r["line"] == ln) for ln in (1, 2, 3, 4, 5)]
            form = "-".join(str(x) for x in n if x) or None
        draft[side] = {"detected": True, "formation": form,
                       "players": starters, "bench": bench}
    if not any(d["detected"] for d in draft.values()):
        raise ValueError("No encontré una alineación de este partido en la foto")
    return {"event_id": event_id, "draft": draft, "notes": out.notes,
            "squad": {sd: [{"player_id": k, "player": v["player"], "pos": v["pos"]} for k, v in pools[sd].items()]
                      for sd in ("home", "away")}}


MANUAL = ("foto", "dt")        # alineaciones cargadas por el usuario: mandan sobre ESPN / PitchAPI
BENCH_LOOKBACK = 5            # partidos del equipo que se miran para armar el banco del Modo DT
BENCH_DECAY = 0.75


def _hist_bench(hp, tid: str, asof, exclude: set) -> list[dict]:
    """Banco probable: los que fueron suplentes (con o sin minutos) en los
    últimos BENCH_LOOKBACK partidos del equipo, que hoy no están en el XI y
    siguen en el club. Tantos como el banco habitual del equipo."""
    t = hp[(hp["team_id"].astype(str) == tid) & (hp["date"] < asof)]
    evs = t.drop_duplicates("event_id").sort_values("date")["event_id"].tail(BENCH_LOOKBACK).tolist()
    if not evs:
        return []
    w = {e: BENCH_DECAY ** i for i, e in enumerate(evs[::-1])}
    subs = t[t["event_id"].isin(evs) & (t["starter"] == 0)]
    size = int(round(subs.groupby("event_id").size().median())) if not subs.empty else 0
    # que sigan en el club: su último partido registrado es con este equipo
    last_team = hp.sort_values("date").groupby("player_id")["team_id"].last().astype(str)
    score: dict[str, list] = {}
    for r in subs.itertuples():
        pid = str(r.player_id)
        if pid in exclude or last_team.get(r.player_id, tid) != tid:
            continue
        sc = score.setdefault(pid, [0.0, r.player, r.date])
        sc[0] += w[r.event_id]
        if r.date >= sc[2]:
            sc[1], sc[2] = r.player, r.date
    best = sorted(score.items(), key=lambda kv: (-kv[1][0], -kv[1][2].value))[:max(size, 7)]
    return [{"player_id": pid, "player": v[1], "known": True, "confidence": 1.0} for pid, v in best]


def dt_draft(event_id: str) -> dict:
    """Modo DT: el borrador sale de la alineación que se ve ahora (oficial o
    probable). El banco: el oficial si ya salió de una fuente; si no, los que
    estuvieron en el banco en los últimos partidos y no están en el XI."""
    import pandas as pd
    result = _load()
    m = _match(result, event_id)
    _, hp = espn.build_history(list(cfg.LEAGUES), verbose=False)
    asof = pd.Timestamp(m["date"]).tz_convert(None)
    srcs = m.get("lineup_sources") or {}
    draft = {}
    for side in ("home", "away"):
        tid = str(m["ev"][f"{side}_id"])
        xi = (m.get("lineup") or {}).get(side) or {}
        ps = xi.get("players") or []
        if len(ps) < lu.MIN_XI:
            draft[side] = {"detected": False}
            continue
        players = [{"player_id": str(p["player_id"]), "player": p.get("player"), "pos": p.get("pos") or "",
                    "line": p.get("line"), "side": p.get("side"), "place": p.get("place") or i + 1,
                    "known": True, "confidence": 1.0} for i, p in enumerate(ps)]
        ids = {p["player_id"] for p in players}
        off = [s for s in m.get("official_squad") or []
               if str(s["team_id"]) == tid and not s.get("starter") and str(s["player_id"]) not in ids]
        if off and m.get("lineup_official"):
            bench = [{"player_id": str(s["player_id"]), "player": s.get("player"), "known": True, "confidence": 1.0}
                     for s in off]
            bench_src = "oficial" if srcs.get(tid) not in MANUAL else "tuyo"
        else:
            bench, bench_src = _hist_bench(hp, tid, asof, ids), "historia"
        draft[side] = {"detected": True, "formation": xi.get("formation"), "players": players,
                       "bench": bench, "bench_src": bench_src}
    if not any(d["detected"] for d in draft.values()):
        raise ValueError("Este partido todavía no tiene alineación para editar")
    pools = squads(m)
    return {"event_id": event_id, "mode": "dt", "draft": draft, "notes": None,
            "squad": {sd: [{"player_id": k, "player": v["player"], "pos": v["pos"]} for k, v in pools[sd].items()]
                      for sd in ("home", "away")}}


def confirm(event_id: str, sides: dict, src: str = "foto") -> dict:
    """Guarda la alineación revisada como fuente "foto" (o "dt", Modo DT) y la aplica como oficial
    (mismo camino que las de ESPN/PitchAPI). `sides`: {home|away: {formation,
    players: [{player_id, player, pos}], bench: [{player_id, player}]}}."""
    from .run import _save, check_lineups
    if src not in MANUAL:
        raise ValueError("Fuente desconocida")
    result = _load()
    m = _match(result, event_id)
    rows, teams = [], set()
    for side in ("home", "away"):
        s = sides.get(side) or {}
        st = s.get("players") or []
        if len(st) < lu.MIN_XI:
            continue
        tid = str(m["ev"][f"{side}_id"])
        teams.add(tid)
        f = str(s.get("formation") or "").replace(" ", "") or None
        for i, p in enumerate(st):
            pos = str(p.get("pos") or "") or ("G" if i == 0 else "")
            rows.append({"team_id": tid, "formation": f, "formation_place": i + 1, "src": src,
                         "player_id": str(p["player_id"]), "player": p.get("player"), "starter": 1,
                         "pos": pos, "pos_group": espn.position_group(pos)})
        for p in s.get("bench") or []:
            rows.append({"team_id": tid, "formation": f, "formation_place": None, "src": src,
                         "player_id": str(p["player_id"]), "player": p.get("player"), "starter": 0,
                         "pos": "SUB", "pos_group": "?"})
    if not teams:
        raise ValueError("Falta el XI completo de al menos un equipo")
    snaps = m.setdefault("lineup_src", {})
    snaps[src] = rows + [s for s in snaps.get(src) or [] if str(s["team_id"]) not in teams]
    for other in MANUAL:                                 # de cada equipo manda la última que cargaste
        if other != src and snaps.get(other):
            snaps[other] = [s for s in snaps[other] if str(s["team_id"]) not in teams]
            if not snaps[other]:
                snaps.pop(other)
    # La probable de antes, para poder volver a ella si ninguna fuente la publicó.
    if "lineup_probable" not in m and not m.get("lineup_official"):
        m["lineup_probable"] = m.get("lineup")
    _save(result)
    updated = check_lineups(only_event=event_id)
    return {"updated": updated, "teams": sorted(teams)}


def discard(event_id: str) -> dict:
    """Saca la alineación cargada (foto o Modo DT): vuelve a mandar ESPN /
    PitchAPI; si ninguna la publicó todavía, vuelve la probable."""
    from .run import _save, check_lineups
    result = _load()
    m = _match(result, event_id)
    snaps = m.get("lineup_src") or {}
    if not any(snaps.get(x) for x in MANUAL):
        return {"updated": []}
    for x in MANUAL:
        snaps.pop(x, None)
    if any(lu.has_xi(sq, str(m["ev"][f"{sd}_id"])) for sq in snaps.values() for sd in ("home", "away")):
        _save(result)
        return {"updated": check_lineups(only_event=event_id)}
    # Nadie más la publicó: vuelve la alineación probable.
    if m.get("lineup_probable"):
        m["lineup"] = m.pop("lineup_probable")
    for k in ("official_squad", "lineup_source", "lineup_sources", "lineup_sig", "lineup_conflicts",
              "lineup_official_at", "pos_alerts"):
        m.pop(k, None)
    m["lineup_official"] = False
    m["lineup_confirmed"] = False
    _save(result)
    return {"updated": [], "reverted": True}
