"""
Futbol — fotos de jugadores (TheSportsDB, clave pública "123").

ESPN casi no tiene fotos de fútbol (≈ 5–10 % de los jugadores). TheSportsDB
tiene la mayoría, recortadas (PNG sin fondo), ideales para los cromos.

La clave pública tiene un límite bajo (≈ 1 consulta cada 2 s; si no, 429):
cada jugador se busca UNA vez y el resultado (con o sin foto) queda en
data/futbol/cache/photos.json para siempre.

    python -m futbol.photos          # completa las fotos de la jornada en curso
"""

from __future__ import annotations

import json
import sys
import time

import httpx

from futbol import config as cfg
from futbol.espn import norm_name

URL = "https://www.thesportsdb.com/api/v1/json/123/searchplayers.php"
STORE = cfg.CACHE_DIR / "photos.json"
GAP_S = 2.2
_last = 0.0


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")


def cached(player_id: str) -> str | None:
    """URL de la foto si ya se buscó (None si no hay o todavía no se buscó)."""
    return (_load().get(str(player_id)) or {}).get("url")


def lookup(player_id: str, name: str, team: str | None = None, store: dict | None = None) -> str | None:
    """Busca la foto de un jugador (respetando el límite) y la guarda."""
    global _last
    d = store if store is not None else _load()
    key = str(player_id)
    if key in d:
        return d[key].get("url")
    wait = GAP_S - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    try:
        r = httpx.get(URL, params={"p": str(name).replace(" ", "_")}, timeout=20)
        if r.status_code == 429:
            time.sleep(30)
            return None                      # no se guarda: se reintenta otro día
        players = [p for p in (r.json().get("player") or []) if p.get("strSport") == "Soccer"]
    except Exception:  # noqa: BLE001
        return None
    want = norm_name(name)
    best = None
    for p in players:
        if norm_name(p.get("strPlayer")) != want and len(players) > 1:
            continue
        if team and p.get("strTeam") and norm_name(team).split()[-1] in norm_name(p["strTeam"]):
            best = p
            break
        best = best or p
    url = (best or {}).get("strCutout") or (best or {}).get("strThumb")
    d[key] = {"name": name, "url": url, "cutout": bool(best and best.get("strCutout"))}
    if store is None:
        _save(d)
    return url


def fill(players: list[dict], max_new: int = 200, verbose: bool = True) -> int:
    """Completa fotos de una lista [{player_id, player, team}] (sólo las que faltan)."""
    d = _load()
    todo = [p for p in players if str(p["player_id"]) not in d][:max_new]
    found = 0
    for i, p in enumerate(todo, 1):
        if lookup(p["player_id"], p["player"], p.get("team"), store=d):
            found += 1
        if i % 20 == 0:
            _save(d)
            if verbose:
                print(f"  fotos: {i}/{len(todo)} buscadas, {found} encontradas")
    _save(d)
    return found


def main():
    for s in (sys.stdout,):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    f = cfg.DATA_DIR / "jornada.json"
    if not f.exists():
        print("Primero corré `python -m futbol.run`.")
        return
    j = json.loads(f.read_text(encoding="utf-8"))
    names = {t["team_id"]: t["name"] for m in j["matches"] for t in m["teams"].values()}
    players = [{"player_id": p["player_id"], "player": p["player"], "team": names.get(p["team_id"])}
               for m in j["matches"] for p in m["players"]]
    print(f"{len(players)} jugadores en la jornada")
    n = fill(players)
    print(f"{n} fotos nuevas · guardadas en {STORE}")


if __name__ == "__main__":
    main()
