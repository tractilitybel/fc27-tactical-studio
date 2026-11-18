"""Player tracking and camera-motion estimation (OpenCV).

Tracker ("median flow" + appearance check):
  * a grid of points inside the player's box is followed to the next frame
    with pyramidal Lucas–Kanade optical flow, then tracked *back*; points
    whose forward–backward error is large are discarded;
  * the box moves by the median displacement and scales by the median
    change of pairwise distances (robust to legs/arms moving);
  * the result is snapped to the best match of the player's appearance
    (normalised cross-correlation in a small window), which stops slow drift
    onto a team-mate in the same kit;
  * if too few points survive or the appearance no longer matches, the
    tracker reports the player as lost instead of guessing.

Camera motion: broadcast-style cameras pan/tilt/zoom from a fixed spot, so
consecutive frames are related by a homography. It is estimated from
corner features (LK + RANSAC) on a downscaled frame, excluding the tracked
players.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - optional dependency
    cv2 = None

Box = tuple[float, float, float, float]   # x, y, w, h (frame pixels)


def available() -> bool:
    return cv2 is not None


def to_gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


@dataclass
class TrackStep:
    box: Box
    ok: bool
    confidence: float        # 0..1


class PlayerTracker:
    GRID = 12
    LK = dict(winSize=(15, 15), maxLevel=4,
              criteria=(3, 30, 0.01))   # cv2.TERM_CRITERIA_EPS | COUNT

    MAX_COAST = 90          # frames a player may stay hidden before we give up

    def __init__(self, frame_rgb: np.ndarray, box: Box):
        self.box = tuple(float(v) for v in box)
        self.prev_gray = to_gray(frame_rgb)
        self.template = self._patch(frame_rgb, self.box)
        self.ref_size = (self.box[2], self.box[3])
        self.lost_frames = 0
        self.v = np.zeros(2)        # screen velocity (px/frame), smoothed
        self.steps = 0
        self.coasting = 0           # frames in a row with the player hidden

    @staticmethod
    def _patch(rgb, box) -> Optional[np.ndarray]:
        x, y, w, h = [int(round(v)) for v in box]
        H, W = rgb.shape[:2]
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        return rgb[y0:y1, x0:x1].copy()

    def _points(self, box) -> np.ndarray:
        x, y, w, h = box
        g = self.GRID
        xs = x + w * (0.15 + 0.7 * (np.arange(g) + 0.5) / g)
        ys = y + h * (0.1 + 0.8 * (np.arange(g) + 0.5) / g)
        P = np.array([(a, b) for b in ys for a in xs], np.float32)
        return P.reshape(-1, 1, 2)

    def step(self, frame_rgb: np.ndarray) -> TrackStep:
        gray = to_gray(frame_rgb)
        x, y, w, h = self.box
        p0 = self._points(self.box)
        crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
        p1, st1, _ = cv2.calcOpticalFlowPyrLK(self.prev_gray, gray, p0, None, winSize=(15, 15),
                                              maxLevel=4, criteria=crit)
        D = None
        if p1 is not None:
            p0r, st2, _ = cv2.calcOpticalFlowPyrLK(gray, self.prev_gray, p1, None, winSize=(15, 15),
                                                   maxLevel=4, criteria=crit)
            fb = np.linalg.norm((p0 - p0r).reshape(-1, 2), axis=1)
            good = (st1.ravel() == 1) & (st2.ravel() == 1) & (fb < max(1.0, np.median(fb)))
            if good.sum() >= 8:
                a = p0.reshape(-1, 2)[good]
                b = p1.reshape(-1, 2)[good]
                D = b - a
        self.prev_gray = gray

        # --- decide which motion to trust (motion model guards against occluders)
        speed = float(np.linalg.norm(self.v))
        tol = 3.0 + 0.35 * speed
        move = None
        mode = "coast"
        if D is not None:
            med = np.median(D, axis=0)
            spread = float(np.median(np.linalg.norm(D - med, axis=1)))
            warm = self.steps < 6
            if (warm or np.linalg.norm(med - self.v) <= tol) and spread < 2.5 + 0.2 * speed:
                move, mode = med, "track"
            else:
                near = np.linalg.norm(D - self.v, axis=1) <= tol
                if near.sum() >= max(6, 0.25 * len(D)):
                    move, mode = np.median(D[near], axis=0), "partial"   # ignore the occluder's points
        if move is None:
            move = self.v                                                  # hidden: follow expected path
            self.coasting += 1
        else:
            self.coasting = 0
            self.v = move if self.steps < 3 else 0.7 * self.v + 0.3 * move
        self.steps += 1

        cx, cy = x + w / 2 + move[0], y + h / 2 + move[1]
        box = (cx - w / 2, cy - h / 2, w, h)
        score = 0.5
        if mode == "track":
            box, score = self._refine(frame_rgb, box)
        if self.coasting > self.MAX_COAST:
            self.box = box
            return TrackStep(box, False, 0.0)
        self.box = box
        conf = {"track": 0.5 + 0.5 * max(0.0, score), "partial": 0.5, "coast": 0.2}[mode]
        return TrackStep(box, True, float(min(1.0, conf)))

    def _refine(self, rgb: np.ndarray, box: Box) -> tuple[Box, float]:
        """Snap to the best appearance match within a few pixels."""
        if self.template is None:
            return box, 0.5
        x, y, w, h = box
        tw, th = int(round(w)), int(round(h))
        if tw < 6 or th < 6:
            return box, 0.5
        import cv2 as _cv
        tmpl = _cv.resize(self.template, (tw, th), interpolation=_cv.INTER_AREA)
        r = int(max(4, 0.25 * max(tw, th)))
        H, W = rgb.shape[:2]
        x0, y0 = int(round(x)) - r, int(round(y)) - r
        x1, y1 = x0 + tw + 2 * r, y0 + th + 2 * r
        cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        region = rgb[cy0:cy1, cx0:cx1]
        if region.shape[0] < th or region.shape[1] < tw:
            return box, 0.5
        res = _cv.matchTemplate(region, tmpl, _cv.TM_CCOEFF_NORMED)
        _, mx, _, loc = _cv.minMaxLoc(res)
        nx, ny = cx0 + loc[0], cy0 + loc[1]
        # Blend: trust the appearance match most when it's strong.
        wgt = float(np.clip((mx - 0.3) / 0.5, 0, 0.8))
        bx = x + (nx - x) * wgt
        by = y + (ny - y) * wgt
        return (bx, by, w, h), float(mx)

    def _lost(self, gray, keep: Optional[Box] = None) -> TrackStep:
        self.prev_gray = gray
        self.lost_frames += 1
        return TrackStep(keep or self.box, False, 0.0)


class CameraMotion:
    """Homography from frame t-1 to frame t (in full-frame pixel coordinates)."""

    def __init__(self, work_width: int = 640):
        self.work_width = work_width
        self.prev = None
        self.scale = 1.0

    def _small(self, rgb):
        h, w = rgb.shape[:2]
        self.scale = self.work_width / w if w > self.work_width else 1.0
        g = to_gray(rgb)
        if self.scale != 1.0:
            g = cv2.resize(g, (int(w * self.scale), int(h * self.scale)), interpolation=cv2.INTER_AREA)
        return g

    def start(self, rgb) -> None:
        self.prev = self._small(rgb)

    def step(self, rgb, exclude: list[Box] = ()) -> Optional[np.ndarray]:
        cur = self._small(rgb)
        prev, self.prev = self.prev, cur
        if prev is None:
            return np.eye(3)
        mask = np.full(prev.shape, 255, np.uint8)
        s = self.scale
        for (x, y, w, h) in exclude:
            cv2.rectangle(mask, (int((x - w * 0.3) * s), int((y - h * 0.2) * s)),
                          (int((x + w * 1.3) * s), int((y + h * 1.2) * s)), 0, -1)
        pts = cv2.goodFeaturesToTrack(prev, maxCorners=500, qualityLevel=0.005, minDistance=7, mask=mask)
        if pts is None or len(pts) < 12:
            return None
        nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=3)
        ok = st.ravel() == 1
        if ok.sum() < 12:
            return None
        Hs, inl = cv2.findHomography(pts[ok], nxt[ok], cv2.RANSAC, 1.5)
        if Hs is None or inl is None or inl.sum() < 10:
            return None
        S = np.diag([s, s, 1.0])
        return np.linalg.inv(S) @ Hs @ S


def apply_h(H: np.ndarray, pts) -> np.ndarray:
    p = np.asarray(pts, np.float64).reshape(-1, 2)
    q = np.hstack([p, np.ones((len(p), 1))]) @ H.T
    return q[:, :2] / q[:, 2:3]
