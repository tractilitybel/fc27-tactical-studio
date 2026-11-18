"""Image utilities (numpy only): PNG encode/decode, polygon masks, player
cut-outs and background fill for the gap a moved player leaves behind."""
from __future__ import annotations

import base64
import struct
import zlib
from dataclasses import dataclass
from typing import Optional

import numpy as np

Point = tuple[float, float]


# ------------------------------------------------------------------ PNG I/O
def encode_png(rgba: np.ndarray) -> bytes:
    """Encode an HxWx4 (or HxWx3) uint8 array as PNG."""
    arr = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w = arr.shape[:2]
    channels = arr.shape[2] if arr.ndim == 3 else 1
    color_type = {1: 0, 3: 2, 4: 6}[channels]
    raw = np.zeros((h, w * channels + 1), np.uint8)
    raw[:, 1:] = arr.reshape(h, w * channels)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw.tobytes(), 6)) + chunk(b"IEND", b""))


def decode_png(data: bytes) -> np.ndarray:
    """Decode 8-bit non-interlaced PNGs (all filter types). Returns HxWxC."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos, idat = 8, []
    w = h = ct = 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h, depth, ct, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or interlace:
                raise ValueError("unsupported PNG")
        elif tag == b"IDAT":
            idat.append(body)
        pos += 12 + n
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    raw = np.frombuffer(zlib.decompress(b"".join(idat)), np.uint8).reshape(h, w * ch + 1)
    out = np.zeros((h, w * ch), np.int32)
    prev = np.zeros(w * ch, np.int32)
    for y in range(h):
        f, line = raw[y, 0], raw[y, 1:].astype(np.int32)
        if f == 0:
            cur = line
        elif f == 2:
            cur = (line + prev) & 255
        else:
            cur = np.zeros_like(line)
            for x in range(w * ch):
                a = cur[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                if f == 1:
                    pred = a
                elif f == 3:
                    pred = (a + b) // 2
                else:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[x] = (line[x] + pred) & 255
        out[y] = cur
        prev = cur
    return out.astype(np.uint8).reshape(h, w, ch)


def to_b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def from_b64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


# ------------------------------------------------------------------ masks
def polygon_mask(poly: list[Point], x0: int, y0: int, w: int, h: int, supersample: int = 2) -> np.ndarray:
    """Anti-aliased coverage (0..1 float) of *poly* over the box (x0, y0, w, h)."""
    s = supersample
    ys = (np.arange(h * s) + 0.5) / s + y0
    xs = (np.arange(w * s) + 0.5) / s + x0
    X, Y = np.meshgrid(xs, ys)
    inside = np.zeros(X.shape, bool)
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if y1 == y2:
            continue
        crosses = (y1 > Y) != (y2 > Y)
        xi = x1 + (Y - y1) * (x2 - x1) / (y2 - y1)
        inside ^= crosses & (X < xi)
    cov = inside.reshape(h, s, w, s).mean(axis=(1, 3))
    return cov.astype(np.float32)


def dilate(mask: np.ndarray, r: int) -> np.ndarray:
    """Binary dilation by a square of radius *r* (mask: bool/float)."""
    m = mask > 0.5 if mask.dtype != bool else mask
    out = m.copy()
    for _ in range(r):
        grown = out.copy()
        grown[1:] |= out[:-1]
        grown[:-1] |= out[1:]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def box_blur(a: np.ndarray, r: int = 1) -> np.ndarray:
    if r <= 0:
        return a
    p = np.pad(a, r, mode="edge")
    k = 2 * r + 1
    c = np.cumsum(np.cumsum(p, 0), 1)
    c = np.pad(c, ((1, 0), (1, 0)))
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return (s / (k * k)).astype(a.dtype)


# ------------------------------------------------------------------ cut-out
@dataclass
class Cutout:
    x: int                 # crop origin in frame pixels
    y: int
    image: np.ndarray      # HxWx4 RGBA of the player (soft edge)
    fill: np.ndarray       # HxWx4 RGBA patch that hides the player at the origin


def make_cutout(frame: np.ndarray, poly: list[Point], fill: bool = True) -> Optional[Cutout]:
    """Cut *poly* out of an RGB *frame* and synthesise a background patch."""
    H, W = frame.shape[:2]
    if len(poly) < 3:
        return None
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    pad = 6  # room for the dilated, feathered fill edge
    x0 = max(0, int(np.floor(min(xs))) - pad)
    y0 = max(0, int(np.floor(min(ys))) - pad)
    x1 = min(W, int(np.ceil(max(xs))) + pad)
    y1 = min(H, int(np.ceil(max(ys))) + pad)
    w, h = x1 - x0, y1 - y0
    if w < 3 or h < 3:
        return None
    cov = polygon_mask(poly, x0, y0, w, h)
    if cov.sum() < 4:
        return None
    crop = frame[y0:y1, x0:x1, :3]
    img = np.zeros((h, w, 4), np.uint8)
    img[..., :3] = crop
    img[..., 3] = np.clip(cov * 255, 0, 255).astype(np.uint8)
    patch = np.zeros((h, w, 4), np.uint8)
    if fill:
        hole = dilate(cov > 0.02, 3)
        alpha = box_blur(dilate(hole, 1).astype(np.float32), 1)
        rgb = fill_hole(frame, hole, x0, y0)
        patch[..., :3] = rgb
        patch[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    return Cutout(x0, y0, img, patch)


def fill_hole(frame: np.ndarray, hole: np.ndarray, x0: int, y0: int) -> np.ndarray:
    """Fill *hole* (mask over the box at x0,y0) with nearby texture.

    1. Try copying the same-shaped region from several offsets around the hole
       (left/right/up/down/diagonals at 1–2 hole sizes) and keep the offset
       whose surroundings best match the hole's surroundings — on a football
       pitch this continues grass stripes and lines naturally.
    2. Blend the copied texture seamlessly: the colour difference along the
       hole border is spread smoothly across the hole (membrane / Poisson
       style), so there is no visible edge.
    """
    H, W = frame.shape[:2]
    h, w = hole.shape
    f = frame.astype(np.float32)
    ring = dilate(hole, 4) & ~hole
    if not ring.any():
        ring = ~hole
    target = f[y0:y0 + h, x0:x0 + w, :3]
    best, best_score = None, np.inf
    for k in (1.0, 1.5, 2.0, 3.0):
        dx, dy = int(round(w * k)), int(round(h * k))
        for sx, sy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1),
                       (2, 1), (-2, 1), (2, -1), (-2, -1)):
            ox = x0 + sx * dx // (2 if abs(sx) == 2 else 1)
            oy = y0 + sy * dy
            if ox < 0 or oy < 0 or ox + w > W or oy + h > H:
                continue
            src = f[oy:oy + h, ox:ox + w, :3]
            score = float(((src[ring] - target[ring]) ** 2).mean())
            if score < best_score:
                best, best_score = src, score
    if best is None:
        # Hole touches the frame on all sides: fall back to smooth fill.
        best = np.repeat(np.repeat(target[ring].mean(0)[None, None, :], h, 0), w, 1)
    return np.clip(seamless(best, target, hole), 0, 255).astype(np.uint8)


def seamless(src: np.ndarray, dst: np.ndarray, hole: np.ndarray, iters: int = 400) -> np.ndarray:
    """Add a harmonic correction so *src* matches *dst* at the hole border."""
    diff = dst - src
    corr = np.where(hole[..., None], diff[~hole].mean(0) if (~hole).any() else 0, diff).astype(np.float32)
    # Multi-scale Jacobi relaxation of Laplace's equation inside the hole.
    inside = hole.copy()
    inside[0, :] = inside[-1, :] = False
    inside[:, 0] = inside[:, -1] = False
    if inside.any():
        n = max(inside.shape)
        iters = min(iters, 40 + 3 * n)
        for _ in range(iters):
            avg = 0.25 * (np.roll(corr, 1, 0) + np.roll(corr, -1, 0) + np.roll(corr, 1, 1) + np.roll(corr, -1, 1))
            corr[inside] = avg[inside]
    out = dst.copy()
    out[hole] = (src + corr)[hole]
    return out
