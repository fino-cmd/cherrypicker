"""
Generates the AltLines "simulating" mini video (the clip that plays on each
game card while that game's Monte Carlo is running).

Why a script instead of a hand-made asset: the clip is drawn frame by frame with
numpy and piped straight into ffmpeg as raw RGB, so there is no image library
dependency and the thing can be regenerated/tweaked at any time.

The animation is ON PURPOSE the product's own thesis, in motion: a right-skewed
distribution being filled in by simulated draws, with the mean sitting to the
RIGHT of the median. That is the gap the engine hunts for.

It also loops **perfectly**: every oscillation is a sine whose period divides the
clip length exactly, and the sweep is a modulo of the clip length, so frame 0
and frame N are the same image.

Usage (from the repo root):

    python altlines/tools/make_sim_video.py

Outputs (assets/altlines/): sim-loop.webm, sim-loop.mp4, sim-loop-poster.jpg
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H = 640, 360
FPS = 24
SECONDS = 6.0
N_FRAMES = int(FPS * SECONDS)

# ── Palette (matches the web app) ─────────────────────────────────────────────
BG_TOP = np.array([10, 15, 22], dtype=float)      # #0a0f16
BG_BOT = np.array([22, 32, 44], dtype=float)      # #16202c
GRID = np.array([27, 42, 58], dtype=float)        # #1b2a3a
GREEN = np.array([110, 231, 183], dtype=float)    # #6ee7b7 (`.al-pos` en la app)
BLUE = np.array([47, 128, 237], dtype=float)      # #2f80ed
INK = np.array([15, 22, 30], dtype=float)
WHITE = np.array([232, 240, 248], dtype=float)

# ── Layout ────────────────────────────────────────────────────────────────────
BASE_Y = 314          # histogram baseline
TOP_Y = 62            # highest a bar may reach
X0, X1 = 44, 596      # plot area
N_BARS = 56
BAR_W = (X1 - X0) / N_BARS
N_DOTS = 360

# The clip is rendered at 640×360 and encoded downscaled: the cards show it at
# roughly 300 px wide, so 512 px loses nothing and costs ~40 % fewer bytes.
ENCODE_W, ENCODE_H = 512, 288


def ramp(a: np.ndarray, b: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Linear colour ramp: u in [0,1] → colour between a and b."""
    u = np.clip(u, 0.0, 1.0)[..., None]
    return a + (b - a) * u


def lognorm_pdf(x: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    x = np.maximum(x, 1e-6)
    z = (np.log(x) - mu) / sigma
    return np.exp(-0.5 * z * z) / (x * sigma * np.sqrt(2 * np.pi))


def build_frame(f: int, rng: np.random.Generator, dots_x, dots_y, shape) -> np.ndarray:
    """One frame. `f` in [0, N_FRAMES); phase wraps exactly at the loop point."""
    phase = 2 * np.pi * f / N_FRAMES
    img = np.zeros((H, W, 3), dtype=float)

    # 1. Background: vertical gradient + a faint grid (drifting one full cycle
    #    per loop, so the seam is invisible).
    u = np.linspace(0.0, 1.0, H)[:, None, None]
    img[:] = BG_TOP + (BG_BOT - BG_TOP) * u
    drift = int(round(20 * (0.5 + 0.5 * np.sin(phase))))
    for y in range(drift, H, 36):
        img[y, :, :] = img[y, :, :] * 0.55 + GRID * 0.45
    for x in range(0, W, 64):
        img[:, x, :] = img[:, x, :] * 0.60 + GRID * 0.40

    # 2. Histogram: a right-skewed distribution, filled by simulated draws.
    centres = X0 + (np.arange(N_BARS) + 0.5) * BAR_W
    x_norm = (centres - X0) / (X1 - X0)
    pdf = lognorm_pdf(0.06 + 3.0 * x_norm, 0.0, 0.62)
    pdf = pdf / pdf.max()

    # Two integer-frequency oscillations + one small per-bar phase.
    wobble = (0.055 * np.sin(2 * phase + np.arange(N_BARS) * 0.70)
              + 0.035 * np.sin(3 * phase + np.arange(N_BARS) * 1.90))
    grow = 0.92 + 0.08 * np.sin(phase)          # the whole cloud "breathes"
    heights = np.clip((0.16 + 0.70 * pdf) * grow + wobble, 0.04, 1.0)
    bar_top = (BASE_Y - (BASE_Y - TOP_Y) * heights).astype(int)

    # A sweep highlight travelling left→right exactly once per loop.
    sweep_x = X0 + ((f / N_FRAMES) * (X1 - X0)) % (X1 - X0)
    xs = np.arange(W)
    sweep = np.exp(-((xs - sweep_x) ** 2) / (2 * 26.0 ** 2))

    col = ramp(GREEN, BLUE, np.full(N_BARS, 0.0) + (centres - X0) / (X1 - X0))
    for i in range(N_BARS):
        xl = int(round(X0 + i * BAR_W + 1))
        xr = int(round(X0 + (i + 1) * BAR_W)) - 1
        if xr <= xl or xr >= W:
            continue
        yt = int(bar_top[i])
        seg = img[yt:BASE_Y, xl:xr, :]
        # Fill with the bar colour, dimmed towards the bottom (2.5D feel).
        depth = np.linspace(0.35, 1.0, seg.shape[0])[:, None, None]
        tint = col[i][None, None, :] * depth
        img[yt:BASE_Y, xl:xr, :] = seg * 0.25 + tint * 0.85
        # Bar cap.
        img[yt:yt + 2, xl:xr, :] = col[i] * 1.05
        # Sweep glow.
        s = sweep[xl:xr][None, :, None]
        img[yt:BASE_Y, xl:xr, :] += WHITE * s * 0.30

    # 3. The point cloud: individual draws, density following the histogram.
    di = rng.integers(0, N_BARS, N_DOTS)
    hx = X0 + (di + rng.random(N_DOTS)) * BAR_W
    hy = BASE_Y - (BASE_Y - TOP_Y) * heights[di] * rng.random(N_DOTS) ** 0.85
    hy = hy + 3.5 * np.sin(phase + np.arange(N_DOTS) * 0.35)    # gentle drift
    xi = np.clip(hx.astype(int), 0, W - 2)
    yi = np.clip(hy.astype(int), 0, H - 2)
    dot_col = ramp(GREEN, BLUE, (hx - X0) / (X1 - X0)) * 1.25 + 40.0
    # Single-pixel draws: crisp at card size and far cheaper to encode than a
    # dithered 2×2 block (which is basically high-frequency noise).
    img[yi, xi, :] += dot_col

    # 4. The fitted curve over the cloud — the shape the engine actually reads.
    cx = np.arange(X0, X1)
    cn = (cx - X0) / (X1 - X0)
    curve = lognorm_pdf(0.06 + 3.0 * cn, 0.0, 0.62)
    curve = curve / curve.max()
    cy = (BASE_Y - (BASE_Y - TOP_Y) * (0.16 + 0.70 * curve) * grow).astype(int)
    for w in range(-2, 3):
        yy = np.clip(cy + w, 0, H - 1)
        if w == 0:
            img[yy, cx, :] = (GREEN * 0.55 + WHITE * 0.45)[None, :]
        else:
            img[yy, cx, :] = img[yy, cx, :] * 0.55 + (GREEN * 0.45)[None, :]

    # 5. Mean (right of) vs median — the whole point, drawn on top.
    cn_curve = cn
    cdf = np.cumsum(curve)
    cdf = cdf / cdf[-1]
    med_px = int(X0 + cn_curve[int(np.argmax(cdf >= 0.5))] * (X1 - X0))
    mean_px = int(X0 + (curve * cn_curve).sum() / curve.sum() * (X1 - X0))

    for px, colour, alpha in ((med_px, WHITE, 0.55), (mean_px, GREEN, 0.75)):
        y0 = int(BASE_Y - (BASE_Y - TOP_Y) * (0.16 + 0.70) * grow) - 14
        img[max(y0, 0):BASE_Y, px, :] = (img[max(y0, 0):BASE_Y, px, :] * (1 - alpha)
                                         + colour * alpha)

    # 6. Baseline.
    img[BASE_Y, X0 - 6:X1 + 6, :] = img[BASE_Y, X0 - 6:X1 + 6, :] * 0.3 + GRID * 0.7
    return img


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="assets/altlines", help="output directory")
    ap.add_argument("--seed", type=int, default=20261018)
    args = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        print("[ERROR] ffmpeg no está en el PATH. Instalalo y volvé a intentar.",
              file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)

    raw = np.empty((N_FRAMES, H, W, 3), dtype=np.uint8)
    for f in range(N_FRAMES):
        img = build_frame(f, rng, None, None, None)
        raw[f] = np.clip(img, 0, 255).astype(np.uint8)
        if (f + 1) % 24 == 0:
            print(f"  frame {f + 1}/{N_FRAMES}")

    source = ["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
              "-r", str(FPS), "-i", "pipe:0"]

    targets = [
        (out / "sim-loop.webm", ["-vf", f"scale={ENCODE_W}:{ENCODE_H}:flags=lanczos",
                                 "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "41",
                                 "-pix_fmt", "yuv420p", "-an", "-row-mt", "1"]),
        (out / "sim-loop.mp4", ["-vf", f"scale={ENCODE_W}:{ENCODE_H}:flags=lanczos",
                                "-c:v", "libx264", "-crf", "32", "-preset", "slower",
                                "-tune", "animation", "-pix_fmt", "yuv420p",
                                "-movflags", "+faststart", "-an"]),
    ]
    for path, codec in targets:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *source,
               *codec, str(path)]
        proc = subprocess.run(cmd, input=raw.tobytes())
        if proc.returncode != 0:
            print(f"[ERROR] ffmpeg falló escribiendo {path}", file=sys.stderr)
            return proc.returncode
        print(f"  -> {path}  ({path.stat().st_size / 1024:.0f} KB)")

    poster = out / "sim-loop-poster.jpg"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(out / "sim-loop.mp4"), "-frames:v", "1", "-q:v", "4",
                    str(poster)], check=False)
    if poster.exists():
        print(f"  -> {poster}  ({poster.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
