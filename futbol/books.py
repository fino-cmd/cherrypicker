"""
Futbol — casas de apuestas configurables (futbol/books.json).

Nada de listas fijas en el código: qué casas se usan, de qué fuente salen sus
cuotas, su prioridad y si ofrecen la promo de suplente se leen de books.json.
"""

from __future__ import annotations

import json
from pathlib import Path

FILE = Path(__file__).with_name("books.json")
_CACHE: dict = {"mtime": None, "books": []}


def all_books() -> list[dict]:
    """Todas las casas del archivo (relee si cambió)."""
    try:
        mt = FILE.stat().st_mtime
    except OSError:
        return []
    if _CACHE["mtime"] != mt:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        _CACHE.update(mtime=mt, books=[b for b in data.get("books", []) if b.get("id")])
    return list(_CACHE["books"])


def enabled(source: str | None = None) -> list[dict]:
    return [b for b in all_books() if b.get("enabled") and (source is None or b.get("source") == source)]


def oddspapi_ids(priority_max: int = 9) -> list[str]:
    return [b["id"] for b in enabled("oddspapi") if int(b.get("priority", 2)) <= priority_max]


def nba_ids(priority_max: int = 9) -> list[str]:
    """Casas de OddsPapi activas para la pestaña NBA ('nba': true)."""
    return [b["id"] for b in all_books() if b.get("nba") and b.get("source") == "oddspapi"
            and int(b.get("priority", 2)) <= priority_max]


def nba_public() -> list[dict]:
    ids = set(nba_ids())
    return [{"id": b["id"], "name": b.get("name") or b["id"], "logo": b.get("logo")}
            for b in all_books() if b["id"] in ids]


def _base(book_id: str) -> str:
    """Marca de la casa sin país ni dominio: "bet365.bet.ar" / "bet365_es" → "bet365"."""
    return str(book_id).split(".")[0].split("_")[0].lower()


class _PromoSet(set):
    """Casas con promo de suplente. Con odds-api.net los ids son la marca
    ("bet365"): cuenta la marca, no el id exacto."""

    def __contains__(self, book_id) -> bool:
        return set.__contains__(self, book_id) or _base(book_id) in {_base(b) for b in self}


def sub_promo_ids() -> set[str]:
    return _PromoSet(b["id"] for b in enabled() if b.get("sub_promo"))


def net_brands() -> dict[str, dict]:
    """Marcas que se usan con odds-api.net ('net': true en books.json) → su ficha."""
    from futbol import config as cfg
    flag = "optic" if cfg.futbol_odds() == "optic" else "net"      # con OpticOdds: las marcadas "optic"
    return {_base(b["id"]): b for b in all_books() if b.get(flag)}


def _net_on() -> bool:
    from futbol import config as cfg
    return cfg.futbol_odds() in ("net", "optic")


def public() -> list[dict]:
    """Lo que necesita la app para mostrar cada casa. Con odds-api.net: una
    caja por marca de las elegidas ('net': true), sin Polymarket."""
    if _net_on():
        return [{"id": brand, "name": b.get("name") or brand, "logo": b.get("logo"), "group": b.get("group")}
                for brand, b in net_brands().items()]
    return [{"id": b["id"], "name": b.get("name") or b["id"], "logo": b.get("logo"), "group": b.get("group")}
            for b in enabled()]
