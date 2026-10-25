"""
Futbol — avisos al celular con ntfy (https://ntfy.sh, gratis y sin cuenta).

El servidor publica en un canal con nombre aleatorio (NTFY_TOPIC en
backend/.env). En el celular: instalar la app "ntfy" y suscribirse a ese canal.
Si NTFY_TOPIC no está, no se manda nada.
"""

from __future__ import annotations

import os

import httpx

NTFY_URL = "https://ntfy.sh"


def topic() -> str | None:
    return os.getenv("NTFY_TOPIC")


def send(title: str, message: str, tags: list[str] | None = None, priority: int = 4) -> bool:
    t = topic()
    if not t:
        return False
    try:
        r = httpx.post(NTFY_URL, json={"topic": t, "title": title, "message": message,
                                       "tags": tags or [], "priority": priority}, timeout=10)
        return r.status_code < 300
    except Exception:  # noqa: BLE001 - un aviso que falla nunca corta el vigía
        return False
