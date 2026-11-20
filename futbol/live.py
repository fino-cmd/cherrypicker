"""Remates en vivo de las jugadas de la boleta (ESPN, gratis).

ESPN actualiza las estadísticas de cada jugador durante el partido en el mismo
resumen que se usa para liquidar. Esto NO liquida nada: sólo informa cómo va.
La liquidación oficial sigue siendo picklog.settle() al terminar.
"""
from __future__ import annotations

import json
import time

import httpx

from futbol import config as cfg
from futbol import espn

SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
TTL_S = 40                      # un mismo partido no se pide más seguido que esto
_mem: dict[str, tuple[float, dict]] = {}


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def event(league: str, event_id: str) -> dict | None:
    """Estado del partido y estadística de cada jugador, ahora."""
    hit = _mem.get(event_id)
    if hit and time.time() - hit[0] < TTL_S:
        return hit[1]
    code = (cfg.LEAGUES.get(league) or {}).get("espn")
    if not code:
        return None
    try:
        s = httpx.get(f"{SITE}/{code}/summary", params={"event": event_id}, timeout=20).json()
    except Exception:  # noqa: BLE001 - sin red: se usa lo último que haya
        return hit[1] if hit else None
    comp = ((s.get("header") or {}).get("competitions") or [{}])[0]
    st = comp.get("status") or {}
    typ = st.get("type") or {}
    score = {str(c.get("id")): c.get("score") for c in comp.get("competitors") or []}
    # Cambios: el que sale → el que entra (keyEvents "X replaces Y").
    pairs, out_min = {}, {}
    for k in s.get("keyEvents") or []:
        if "ubstitution" not in json.dumps(k.get("type") or {}):
            continue
        ps = [str((p.get("athlete") or {}).get("id")) for p in k.get("participants") or []]
        if len(ps) >= 2 and ps[1] not in pairs:
            pairs[ps[1]] = ps[0]            # [0] entra, [1] sale
            out_min[ps[1]] = (k.get("clock") or {}).get("displayValue")
    players = {}
    for ro in s.get("rosters") or []:
        tid = str((ro.get("team") or {}).get("id"))
        for p in ro.get("roster") or []:
            a = p.get("athlete") or {}
            pid = str(a.get("id"))
            stats = {x["name"]: x.get("value") for x in p.get("stats") or []}
            # Minuto en que entró / salió (jugadas de cambio de su ficha).
            starter = bool(p.get("starter"))
            on_min = off_min = None
            for pl in p.get("plays") or []:
                if not pl.get("substitution"):
                    continue
                mnt = espn._minute((pl.get("clock") or {}).get("displayValue"))
                if mnt is None:
                    continue
                if not starter and on_min is None:
                    on_min = mnt
                else:
                    off_min = mnt
            players[pid] = {
                "player": a.get("displayName"), "team_id": tid, "starter": bool(p.get("starter")),
                "subbed_in": bool(p.get("subbedIn")), "subbed_out": bool(p.get("subbedOut")) or pid in pairs,
                "shots": _num(stats.get("totalShots")), "sot": _num(stats.get("shotsOnTarget")),
                "pos": (p.get("position") or {}).get("abbreviation"), "on_min": on_min, "off_min": off_min,
            }
    # Plan B, como al liquidar: si ESPN todavía no publicó el cambio en keyEvents,
    # el que salió se empareja con el suplente que entró en ese mismo minuto (± 2').
    taken = set(pairs.values())
    for pid, pl in players.items():
        if not pl["starter"] or pid in pairs or pl["off_min"] is None:
            continue
        cand = [(qid, q) for qid, q in players.items() if qid not in taken and q["team_id"] == pl["team_id"]
                and not q["starter"] and q["on_min"] is not None and abs(q["on_min"] - pl["off_min"]) <= 2]
        same = [c for c in cand if c[1]["pos"] and c[1]["pos"] == pl["pos"]]
        pick = cand if len(cand) == 1 else same if len(same) == 1 else []
        if pick:                    # cambio doble en el mismo minuto: no adivinar, esperar a ESPN
            qid = pick[0][0]
            pairs[pid] = qid
            taken.add(qid)
            out_min.setdefault(pid, f"{pl['off_min']}'")
            pl["subbed_out"] = True
    out = {
        "state": typ.get("state") or "pre",            # pre / in / post
        "completed": bool(typ.get("completed")),
        "clock": st.get("displayClock"), "detail": typ.get("shortDetail") or typ.get("detail"),
        "period": st.get("period"), "score": score,
        "players": players, "pairs": pairs, "out_min": out_min,
    }
    _mem[event_id] = (time.time(), out)
    return out


def picks(rows: list[dict]) -> dict[str, dict]:
    """rows: [{key, league, event_id, player_id, stat, line, mode, book}] →
    {key: cómo va la jugada}. `hit` = ya cumplió la línea."""
    from futbol import picklog
    out = {}
    for r in rows:
        ev = event(r["league"], r["event_id"])
        if ev is None:
            continue
        base = {"state": ev["state"], "clock": ev["clock"], "detail": ev["detail"], "completed": ev["completed"]}
        if ev["state"] == "pre":
            out[r["key"]] = base
            continue
        pl = ev["players"].get(str(r["player_id"]))
        line = float(r["line"])
        need = int(line) + 1                           # 2.5 → 3
        if pl is None:
            out[r["key"]] = {**base, "playing": False, "own": 0, "total": 0, "need": need, "hit": False}
            continue
        own = pl.get(r["stat"]) or 0.0
        total, sub = own, None
        promo = picklog.promo_applies({"mode": r.get("mode"), "first_book": r.get("book")})
        if promo and pl["starter"] and pl["subbed_out"]:
            pin = ev["pairs"].get(str(r["player_id"]))
            rp = ev["players"].get(pin) if pin else None
            if rp is not None:
                sub = {"player": rp["player"], "player_id": pin, "value": rp.get(r["stat"]) or 0.0}
                total = own + sub["value"]
        playing = pl["starter"] or pl["subbed_in"]
        out[r["key"]] = {**base, "playing": playing, "subbed_out": pl["subbed_out"],
                         "out_min": ev["out_min"].get(str(r["player_id"])), "promo": promo,
                         "own": own, "sub": sub, "total": total, "need": need, "hit": total > line}
    return out
