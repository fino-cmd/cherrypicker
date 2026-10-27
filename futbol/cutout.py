"""
Futbol — fotos de API-Football sin el fondo blanco.

Las fotos de media.api-sports.io vienen en 150×150 con fondo blanco. Acá se
bajan una sola vez (es un CDN público: no gasta créditos), se le saca el
blanco que toca los bordes de arriba y de los costados (el de abajo es la
camiseta y no se toca) y se guardan como PNG transparente en
data/futbol/cache/photos_cut/.

La silueta gris que API-Football devuelve cuando no tiene foto se marca como
"sin foto" para que la app muestre las iniciales.
"""

from __future__ import annotations

import hashlib
import io

import httpx
import numpy as np
from PIL import Image
from scipy import ndimage

from futbol import config as cfg

DIR = cfg.CACHE_DIR / "photos_cut"
SRC = "https://media.api-sports.io/football/players/{pid}.png"
PLACEHOLDER_MD5 = {"430d67fd79ad0a355b212d5780886e34"}


def remove_white(img: Image.Image) -> Image.Image:
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    h, w, _ = a.shape
    mn, mx = a.min(2), a.max(2)
    cand = (mn >= 236) & ((mx - mn) <= 16)            # blanco o casi blanco
    lab, _ = ndimage.label(cand)
    seed = np.zeros_like(cand)
    low = int(h * 0.78)                               # abajo está la camiseta: no se siembra
    seed[0, :] = True
    seed[:low, 0] = True
    seed[:low, -1] = True
    keep = np.unique(lab[seed & cand])
    bg = np.isin(lab, keep[keep > 0])
    # borde suave de 2 px, más transparente cuanto más blanco
    near = ndimage.binary_dilation(bg, iterations=2) & ~bg
    alpha = np.full((h, w), 255.0)
    alpha[bg] = 0
    soft = np.clip((255 - mn) / 55.0, 0, 1) * 255
    alpha[near] = np.minimum(255, soft[near] + 40)
    rgba = np.dstack([a.clip(0, 255).astype(np.uint8), alpha.astype(np.uint8)])
    return Image.fromarray(rgba, "RGBA")


def _is_placeholder(raw: bytes, img: Image.Image) -> bool:
    if hashlib.md5(raw).hexdigest() in PLACEHOLDER_MD5:
        return True
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    return float(((a.max(2) - a.min(2)) > 18).mean()) < 0.01   # todo gris: silueta


def get(pid: str) -> bytes | None:
    """PNG transparente del jugador, o None si no tiene foto."""
    pid = "".join(ch for ch in str(pid) if ch.isdigit())
    if not pid:
        return None
    out = DIR / f"{pid}.png"
    none = DIR / f"{pid}.none"
    if out.exists():
        return out.read_bytes()
    if none.exists():
        return None
    try:
        r = httpx.get(SRC.format(pid=pid), timeout=20)
        r.raise_for_status()
        raw = r.content
        img = Image.open(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        return None                                   # no se guarda: se reintenta después
    DIR.mkdir(parents=True, exist_ok=True)
    if _is_placeholder(raw, img):
        none.write_bytes(b"")
        return None
    buf = io.BytesIO()
    remove_white(img).save(buf, "PNG", optimize=True)
    out.write_bytes(buf.getvalue())
    return buf.getvalue()
