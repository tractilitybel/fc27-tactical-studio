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


# ------------------------------------------------------------ masks → cut-outs
def largest_component(mask: np.ndarray, seed: Optional[tuple[int, int]] = None) -> np.ndarray:
    """Keep the 8-connected component containing *seed* (x, y), or the largest one."""
    m = mask.astype(bool)
    if not m.any():
        return m
    h, w = m.shape
    labels = np.zeros((h, w), np.int32)
    sizes = [0]
    cur = 0
    ys, xs = np.nonzero(m)
    for y0, x0 in zip(ys, xs):
        if labels[y0, x0]:
            continue
        cur += 1
        stack = [(y0, x0)]
        labels[y0, x0] = cur
        n = 0
        while stack:
            y, x = stack.pop()
            n += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and m[yy, xx] and not labels[yy, xx]:
                        labels[yy, xx] = cur
                        stack.append((yy, xx))
        sizes.append(n)
    if seed is not None:
        sx, sy = int(round(seed[0])), int(round(seed[1]))
        if 0 <= sy < h and 0 <= sx < w and labels[sy, sx]:
            return labels == labels[sy, sx]
    return labels == int(np.argmax(sizes))


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill enclosed holes (background not connected to the border)."""
    m = mask.astype(bool)
    h, w = m.shape
    outside = np.zeros((h + 2, w + 2), bool)
    free = np.pad(~m, 1, constant_values=True)
    outside[0, 0] = True
    frontier = [(0, 0)]
    while frontier:
        y, x = frontier.pop()
        for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= yy < h + 2 and 0 <= xx < w + 2 and free[yy, xx] and not outside[yy, xx]:
                outside[yy, xx] = True
                frontier.append((yy, xx))
    return ~outside[1:-1, 1:-1]


def trace_outline(mask: np.ndarray) -> list[Point]:
    """Outer boundary of a single blob as a polygon (pixel-corner coordinates),
    traced along pixel edges so the polygon encloses exactly the mask pixels."""
    m = np.pad(mask.astype(bool), 1)
    h, w = m.shape
    # Directed boundary edges with the filled pixel on the right-hand side.
    nxt: dict[tuple[int, int], tuple[int, int]] = {}
    ys, xs = np.nonzero(m)
    for y, x in zip(ys, xs):
        if not m[y - 1, x]:
            nxt[(x, y)] = (x + 1, y)
        if not m[y, x + 1]:
            nxt[(x + 1, y)] = (x + 1, y + 1)
        if not m[y + 1, x]:
            nxt[(x + 1, y + 1)] = (x, y + 1)
        if not m[y, x - 1]:
            nxt[(x, y + 1)] = (x, y)
    if not nxt:
        return []
    start = min(nxt)  # top-left-most vertex lies on the outer boundary
    poly = [start]
    p = nxt[start]
    guard = len(nxt) + 2
    while p != start and guard:
        poly.append(p)
        p = nxt.get(p, start)
        guard -= 1
    return [(x - 1.0, y - 1.0) for x, y in poly]


def simplify_polygon(points: list[Point], tol: float) -> list[Point]:
    """Ramer–Douglas–Peucker for a closed polygon."""
    if len(points) < 8:
        return list(points)

    def rdp(pts):
        if len(pts) < 3:
            return pts
        a, b = np.array(pts[0]), np.array(pts[-1])
        d = b - a
        L = np.hypot(*d)
        arr = np.array(pts[1:-1])
        if L == 0:
            dist = np.hypot(*(arr - a).T)
        else:
            dist = np.abs(d[0] * (arr[:, 1] - a[1]) - d[1] * (arr[:, 0] - a[0])) / L
        i = int(np.argmax(dist))
        if dist[i] > tol:
            return rdp(pts[:i + 2])[:-1] + rdp(pts[i + 1:])
        return [pts[0], pts[-1]]
    # Split at the farthest point from the first so both halves are open chains.
    arr = np.array(points)
    far = int(np.argmax(np.hypot(*(arr - arr[0]).T)))
    a = rdp(list(points[:far + 1]))
    b = rdp(list(points[far:]) + [points[0]])
    return a[:-1] + b[:-1]


def mask_to_polygon(mask: np.ndarray, seed: Optional[tuple[float, float]] = None,
                    tol: float = 0.8) -> list[Point]:
    blob = fill_holes(largest_component(mask, seed))
    return simplify_polygon(trace_outline(blob), tol)


def make_cutout_from_mask(frame: np.ndarray, mask: np.ndarray, fill: bool = True,
                          seed: Optional[tuple[float, float]] = None) -> Optional[tuple[Cutout, list[Point]]]:
    """Cut-out from a full-frame boolean mask (e.g. from AI segmentation).
    Returns the cut-out and an outline polygon for editing."""
    blob = fill_holes(largest_component(mask, seed))
    if blob.sum() < 12:
        return None
    poly = simplify_polygon(trace_outline(blob), 0.8)
    if len(poly) < 3:
        return None
    H, W = frame.shape[:2]
    ys, xs = np.nonzero(blob)
    pad = 6
    x0, y0 = max(0, xs.min() - pad), max(0, ys.min() - pad)
    x1, y1 = min(W, xs.max() + 1 + pad), min(H, ys.max() + 1 + pad)
    sub = blob[y0:y1, x0:x1]
    # Soft 1-px edge for a clean composite.
    cov = box_blur(sub.astype(np.float32), 1)
    cov = np.where(sub, np.maximum(cov, 0.6), cov * 0.8)
    h, w = sub.shape
    img = np.zeros((h, w, 4), np.uint8)
    img[..., :3] = frame[y0:y1, x0:x1, :3]
    img[..., 3] = np.clip(cov * 255, 0, 255).astype(np.uint8)
    patch = np.zeros((h, w, 4), np.uint8)
    if fill:
        hole = dilate(sub, 3)
        alpha = box_blur(dilate(hole, 1).astype(np.float32), 1)
        patch[..., :3] = fill_hole(frame, hole, int(x0), int(y0))
        patch[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    return Cutout(int(x0), int(y0), img, patch), poly
