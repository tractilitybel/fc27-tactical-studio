"""Pitch calibration: map video pixels to pitch metres with a homography.

Pitch coordinates: metres from the top-left corner as seen in a broadcast
view (x along the 105 m length, y along the 68 m width; "top" = far
touchline, "bottom" = near touchline).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

L, W = 105.0, 68.0
_PA_HALF = 20.16          # penalty area half width (40.32 m)
_GA_HALF = 9.16           # goal area half width (18.32 m)
_ARC = math.sqrt(9.15 ** 2 - 5.5 ** 2)   # where the penalty arc meets the box edge


def _side(prefix: str, x0: float, d: float) -> dict:
    """Landmarks of one half. x0 = goal line x, d = +1 (left half) / -1 (right half)."""
    c = W / 2
    return {
        f"{prefix} box – top goal-line corner": (x0, c - _PA_HALF),
        f"{prefix} box – bottom goal-line corner": (x0, c + _PA_HALF),
        f"{prefix} box – top front corner": (x0 + d * 16.5, c - _PA_HALF),
        f"{prefix} box – bottom front corner": (x0 + d * 16.5, c + _PA_HALF),
        f"{prefix} six-yard box – top goal-line corner": (x0, c - _GA_HALF),
        f"{prefix} six-yard box – bottom goal-line corner": (x0, c + _GA_HALF),
        f"{prefix} six-yard box – top front corner": (x0 + d * 5.5, c - _GA_HALF),
        f"{prefix} six-yard box – bottom front corner": (x0 + d * 5.5, c + _GA_HALF),
        f"{prefix} penalty spot": (x0 + d * 11.0, c),
        f"{prefix} penalty arc – top end": (x0 + d * 16.5, c - _ARC),
        f"{prefix} penalty arc – bottom end": (x0 + d * 16.5, c + _ARC),
    }


LANDMARKS: dict[str, tuple[float, float]] = {
    "Top-left corner": (0.0, 0.0),
    "Bottom-left corner": (0.0, W),
    "Top-right corner": (L, 0.0),
    "Bottom-right corner": (L, W),
    "Halfway line – top": (L / 2, 0.0),
    "Halfway line – bottom": (L / 2, W),
    "Centre spot": (L / 2, W / 2),
    "Centre circle – top": (L / 2, W / 2 - 9.15),
    "Centre circle – bottom": (L / 2, W / 2 + 9.15),
    "Centre circle – left": (L / 2 - 9.15, W / 2),
    "Centre circle – right": (L / 2 + 9.15, W / 2),
    **_side("Left", 0.0, 1.0),
    **_side("Right", L, -1.0),
}


@dataclass
class Calibration:
    frame: int
    points: list = field(default_factory=list)   # [{"name": str, "x": px, "y": px}]
    h: list = field(default_factory=list)        # 9 floats, image -> pitch (row-major)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            from .model import new_id
            self.id = new_id()

    @property
    def H(self) -> Optional[np.ndarray]:
        return np.array(self.h, np.float64).reshape(3, 3) if len(self.h) == 9 else None

    def to_dict(self) -> dict:
        return {"frame": self.frame, "points": self.points, "h": list(self.h), "id": self.id}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(int(d["frame"]), list(d.get("points", [])), list(d.get("h", [])), d.get("id", ""))


def apply_h(H: np.ndarray, pts) -> np.ndarray:
    p = np.asarray(pts, np.float64).reshape(-1, 2)
    q = np.hstack([p, np.ones((len(p), 1))]) @ H.T
    return q[:, :2] / q[:, 2:3]


def solve(points: list[dict]) -> tuple[Optional[np.ndarray], float, str]:
    """Homography image->pitch from named points. Returns (H, mean error px, message)."""
    pts = [p for p in points if p.get("name") in LANDMARKS]
    if len(pts) < 4:
        return None, 0.0, f"Place at least 4 points ({len(pts)} so far)."
    img = np.array([[p["x"], p["y"]] for p in pts], np.float64)
    pit = np.array([LANDMARKS[p["name"]] for p in pts], np.float64)
    # Degenerate layouts: all points (nearly) on one pitch line give no solution.
    centred = pit - pit.mean(0)
    if np.linalg.svd(centred, compute_uv=False)[-1] < 1.0:
        return None, 0.0, "These points lie on one line — add a point off that line."
    H = _dlt(img, pit)
    if H is None:
        return None, 0.0, "Could not solve — check the points."
    # Error measured back in the image (pixels) — what the user can see.
    Hinv = np.linalg.inv(H)
    back = apply_h(Hinv, pit)
    err = float(np.mean(np.linalg.norm(back - img, axis=1)))
    if not np.isfinite(err):
        return None, 0.0, "Could not solve — check the points."
    if len(pts) == 4:
        msg = "Calibrated with 4 points (exact fit). A 5th point lets the app check accuracy."
    elif err < 3:
        msg = f"Calibrated — accuracy {err:.1f} px. ✓"
    elif err < 10:
        msg = f"Calibrated — accuracy {err:.1f} px (check the points marked in orange)."
    else:
        msg = f"Points don't agree ({err:.0f} px) — one is probably on the wrong landmark."
    return H, err, msg


def point_errors(points: list[dict], H: np.ndarray) -> list[float]:
    Hinv = np.linalg.inv(H)
    out = []
    for p in points:
        if p.get("name") not in LANDMARKS:
            out.append(0.0)
            continue
        b = apply_h(Hinv, [LANDMARKS[p["name"]]])[0]
        out.append(float(np.hypot(b[0] - p["x"], b[1] - p["y"])))
    return out


def _dlt(src: np.ndarray, dst: np.ndarray) -> Optional[np.ndarray]:
    """Normalised direct linear transform (least squares for > 4 points)."""
    def norm(p):
        c = p.mean(0)
        s = math.sqrt(2) / max(1e-9, np.mean(np.linalg.norm(p - c, axis=1)))
        T = np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])
        return apply_h(T, p), T
    a, Ta = norm(src)
    b, Tb = norm(dst)
    A = []
    for (x, y), (u, v) in zip(a, b):
        A.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        A.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, S, Vt = np.linalg.svd(np.array(A))
    Hn = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(Tb) @ Hn @ Ta
    if abs(H[2, 2]) < 1e-12:
        return None
    return H / H[2, 2]


def pitch_lines(samples: int = 48) -> list[list[tuple[float, float]]]:
    """Pitch markings as polylines in metres (for the calibration overlay)."""
    c = W / 2
    lines = [[(0, 0), (L, 0), (L, W), (0, W), (0, 0)], [(L / 2, 0), (L / 2, W)]]
    for x0, d in ((0.0, 1.0), (L, -1.0)):
        lines.append([(x0, c - _PA_HALF), (x0 + d * 16.5, c - _PA_HALF), (x0 + d * 16.5, c + _PA_HALF),
                      (x0, c + _PA_HALF)])
        lines.append([(x0, c - _GA_HALF), (x0 + d * 5.5, c - _GA_HALF), (x0 + d * 5.5, c + _GA_HALF),
                      (x0, c + _GA_HALF)])
        a0 = math.acos(5.5 / 9.15)
        arc = [(x0 + d * (11 + 9.15 * math.cos(t)), c + 9.15 * math.sin(t))
               for t in np.linspace(-a0, a0, samples // 3)]
        lines.append(arc)
    lines.append([(L / 2 + 9.15 * math.cos(t), c + 9.15 * math.sin(t))
                  for t in np.linspace(0, 2 * math.pi, samples)])
    return lines


def lines_in_image(H: np.ndarray, steps: int = 12) -> list[list[tuple[float, float]]]:
    """Project pitch markings into the image (straight lines are subdivided so
    points behind the camera can be dropped)."""
    Hinv = np.linalg.inv(H)
    out = []
    for poly in pitch_lines():
        dense = []
        for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
            for t in np.linspace(0, 1, steps, endpoint=False):
                dense.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        dense.append(poly[-1])
        p = np.asarray(dense, np.float64)
        q = np.hstack([p, np.ones((len(p), 1))]) @ Hinv.T
        seg = []
        for (X, Y, Z) in q:
            if Z <= 1e-9:            # behind the camera: break the line here
                if len(seg) > 1:
                    out.append(seg)
                seg = []
                continue
            seg.append((X / Z, Y / Z))
        if len(seg) > 1:
            out.append(seg)
    return out


def distance_m(H: np.ndarray, a, b) -> float:
    pa, pb = apply_h(H, [a, b])
    return float(np.hypot(*(pb - pa)))
