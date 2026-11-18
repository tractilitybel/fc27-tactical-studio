"""Synthetic broadcast-like scene for tracking tests."""
import numpy as np


def player_sprite(h=90, w=34, shirt=(220, 30, 40), number=7, seed=0):
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.uint8)
    mask = np.zeros((h, w), bool)
    head = (slice(0, 14), slice(w // 2 - 6, w // 2 + 6))
    img[head] = (200, 160, 130); mask[head] = True
    body = (slice(14, 50), slice(2, w - 2))
    img[body] = shirt; mask[body] = True
    # shirt number / texture: gives the tracker something to hold on to
    img[22:40, w // 2 - 6:w // 2 + 6] = 255
    img[26:36, w // 2 - 2:w // 2 + 2] = (30, 30, 30) if number % 2 else (120, 20, 20)
    img[14:50, 2:w - 2] = np.clip(img[14:50, 2:w - 2].astype(int) + rng.integers(-25, 25, (36, w - 4, 3)), 0, 255)
    shorts = (slice(50, 62), slice(3, w - 3))
    img[shorts] = (245, 245, 245); mask[shorts] = True
    for lx in (6, w - 14):
        leg = (slice(62, h), slice(lx, lx + 8))
        img[leg] = (25, 25, 25); mask[leg] = True
    return img, mask


def background(W, H, seed=1):
    rng = np.random.default_rng(seed)
    bg = np.zeros((H, W, 3), np.float32)
    for x in range(W):
        bg[:, x] = (40, 118 + 14 * ((x // 70) % 2), 48)
    noise = rng.normal(0, 9, (H // 4 + 1, W // 4 + 1, 1)).repeat(4, 0).repeat(4, 1)[:H, :W]
    bg += noise
    bg[:, ::300] = 225       # pitch lines
    bg[::260, :] = 225
    return np.clip(bg, 0, 255).astype(np.uint8)


def scene(n=120, W=960, H=540, pan=5.0, distractor=True, mate_in_front=False):
    """Returns (frames, true_boxes) — the camera pans right while the tracked
    player runs; a team-mate in the same kit crosses in front of him."""
    world = background(W + int(pan * n) + 200, H)
    me, mm = player_sprite(seed=3)
    mate, mmask = player_sprite(seed=9, number=8)
    frames, boxes = [], []
    for i in range(n):
        cam = int(round(pan * i))
        f = world[:, cam:cam + W].copy()
        px = 300 + 6.0 * i - cam           # screen x of my player
        py = 220 + 40 * np.sin(i / 25)
        if distractor:
            qx = 700 - 4.0 * i - cam + pan * i * 0  # team-mate running the other way (screen space)
            qx = 760 - 7.0 * i
            qy = 230
            if not mate_in_front:
                _blit(f, mate, mmask, qx, qy)
        _blit(f, me, mm, px, py)
        if distractor and mate_in_front:
            _blit(f, mate, mmask, qx, qy)
        frames.append(f)
        boxes.append((px, py, me.shape[1], me.shape[0]))
    return frames, boxes


def _blit(f, spr, mask, x, y):
    x, y = int(round(x)), int(round(y))
    h, w = mask.shape
    H, W = f.shape[:2]
    if x >= W or y >= H or x + w <= 0 or y + h <= 0:
        return
    sx0, sy0 = max(0, -x), max(0, -y)
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    sub = mask[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    f[y0:y1, x0:x1][sub] = spr[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)][sub]
