"""
Futbol — props de remates de Polymarket (API pública, sin clave y gratis).

Documentación oficial (docs.polymarket.com, "Discover Markets"):
  GET https://gamma-api.polymarket.com/events/keyset?tag_id=<liga>&closed=false
Cada partido tiene un evento "…-player-props" con mercados de tipo
soccer_player_shots y soccer_player_shots_on_target ("Nombre: 2+ shots",
line = 1.5). Son contratos Sí/No: comprar "Sí" cuesta best_ask, así que la cuota
decimal es 1/best_ask; el "No" cuesta 1 − best_bid → cuota 1/(1 − best_bid).
Polymarket los abre ~20-50 minutos antes del partido (medido 27/10/2026).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from futbol import config as cfg
from futbol.espn import norm_name

DIR = cfg.CACHE_DIR / "polymarket"
GAMMA = "https://gamma-api.polymarket.com"
TTL_S = 10 * 60
STAT_OF = {"soccer_player_shots": "shots", "soccer_player_shots_on_target": "sot"}
MIN_LIQUIDITY = 50.0           # USDC en el libro: menos que esto no es un precio real
MAX_PAGES = 6


def _ts(s) -> datetime | None:
    try:
        s = str(s).replace(" ", "T").replace("Z", "+00:00")
        if s.endswith("+00"):
            s += ":00"
        return datetime.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return None


def _events(tag: str, refresh: bool = False) -> list[dict]:
    """Eventos abiertos de una liga (cacheados TTL_S; la API es gratis)."""
    f = DIR / f"events_{tag}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < TTL_S:
        return json.loads(f.read_text(encoding="utf-8"))
    out, cursor = [], None
    try:
        for _ in range(MAX_PAGES):
            p = {"tag_id": tag, "closed": "false", "limit": 100}
            if cursor:
                p["after_cursor"] = cursor
            r = httpx.get(f"{GAMMA}/events/keyset", params=p, timeout=30)
            r.raise_for_status()
            d = r.json()
            evs = d.get("events", []) if isinstance(d, dict) else d
            for e in evs:
                ms = [m for m in e.get("markets") or [] if m.get("sportsMarketType") in STAT_OF]
                if ms:
                    out.append({k: e.get(k) for k in ("id", "slug", "title", "gameId", "startTime")} | {"markets": [
                        {k: m.get(k) for k in ("question", "line", "bestBid", "bestAsk", "liquidityNum",
                                               "acceptingOrders", "gameStartTime", "sportsMarketType")}
                        for m in ms]})
            cursor = d.get("next_cursor") if isinstance(d, dict) else None
            if not cursor or not evs:
                break
    except Exception as exc:  # noqa: BLE001 - sin Polymarket la app sigue igual
        print(f"  Polymarket {tag}: {exc}")
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    DIR.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(out), encoding="utf-8")
    return out


def _teams(title: str) -> tuple[str, str]:
    t = str(title or "").split(" - ")[0]
    a, _, b = t.partition(" vs. ")
    if not b:
        a, _, b = t.partition(" vs ")
    return a.strip(), b.strip()


def fetch(refresh: bool = False) -> pd.DataFrame:
    """→ mismas columnas que oddspapi.parse(): una fila por jugador/stat/línea/lado."""
    now = datetime.now(timezone.utc)
    rows = []
    for lg, spec in cfg.LEAGUES.items():
        tag = spec.get("polymarket")
        if not tag:
            continue
        for e in _events(str(tag), refresh):
            home, away = _teams(e.get("title"))
            for m in e["markets"]:
                st = _ts(m.get("gameStartTime"))
                if not m.get("acceptingOrders") or not st or st < now:
                    continue
                if float(m.get("liquidityNum") or 0) < MIN_LIQUIDITY:
                    continue
                name = str(m.get("question") or "").split(":")[0].strip()
                line = m.get("line")
                if not name or line is None:
                    continue
                base = {"fixture_id": f"pm-{e.get('gameId') or e.get('id')}", "league": lg,
                        "start": st.isoformat(), "home": home, "away": away, "book": "polymarket",
                        "player_name": name, "player_key": norm_name(name),
                        "stat": STAT_OF[m["sportsMarketType"]], "line": float(line), "susp": False}
                ask, bid = m.get("bestAsk"), m.get("bestBid")
                if ask and 0.02 <= float(ask) <= 0.98:
                    rows.append({**base, "side": "over", "price": round(1 / float(ask), 3)})
                if bid and 0.02 <= float(bid) <= 0.98:
                    rows.append({**base, "side": "under", "price": round(1 / (1 - float(bid)), 3)})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.drop_duplicates(["fixture_id", "player_key", "stat", "line", "side"])
    return df
