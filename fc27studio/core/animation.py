"""Animated drawings: keyframed movement and "draw-on" reveal.

An annotation with keyframes ([{frame, points, rotation}]) moves smoothly
between them (ease in/out); before the first / after the last keyframe it
holds still. ``reveal_frames`` makes a drawing draw itself from its start.
``posed(a, frame)`` returns what to draw at a frame (a copy when animated).
"""
from __future__ import annotations

import copy
import math
from typing import Optional

from .model import Annotation, Kind

REVEAL_KINDS = (Kind.ARROW, Kind.RUN, Kind.LINE, Kind.CURVED_ARROW, Kind.FREEHAND, Kind.MEASURE)


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def is_animated(a: Annotation) -> bool:
    return bool(a.keyframes) or a.reveal_frames > 0


def interpolate(a: Annotation, frame: int) -> tuple[list, float]:
    """(points, rotation) of a keyframed annotation at *frame*."""
    kfs = a.keyframes
    if not kfs:
        return list(a.points), a.rotation
    if frame <= kfs[0]["frame"]:
        k = kfs[0]
        return list(k["points"]), k.get("rotation", 0.0)
    if frame >= kfs[-1]["frame"]:
        k = kfs[-1]
        return list(k["points"]), k.get("rotation", 0.0)
    for k0, k1 in zip(kfs, kfs[1:]):
        if k0["frame"] <= frame <= k1["frame"]:
            span = max(1, k1["frame"] - k0["frame"])
            t = ease((frame - k0["frame"]) / span)
            p0, p1 = k0["points"], k1["points"]
            if len(p0) != len(p1):
                return list(p0 if t < 0.5 else p1), k0.get("rotation", 0.0)
            pts = [(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t) for (x0, y0), (x1, y1) in zip(p0, p1)]
            r0, r1 = k0.get("rotation", 0.0), k1.get("rotation", 0.0)
            d = (r1 - r0 + 180) % 360 - 180          # rotate the short way round
            return pts, r0 + d * t
    k = kfs[-1]
    return list(k["points"]), k.get("rotation", 0.0)


def reveal_progress(a: Annotation, frame: int) -> float:
    if a.reveal_frames <= 0:
        return 1.0
    return ease((frame - a.start_frame + 1) / a.reveal_frames)


def posed(a: Annotation, frame: int) -> Annotation:
    """The annotation as it should be drawn at *frame*."""
    if not is_animated(a):
        return a
    b = copy.copy(a)
    if a.keyframes:
        b.points, b.rotation = interpolate(a, frame)
    prog = reveal_progress(a, frame)
    if prog < 1.0:
        b = partial(b, prog)
    return b


def partial(a: Annotation, t: float) -> Annotation:
    """Shorten line-like drawings to fraction *t*; fade others in."""
    from .model import quad_bezier_points
    b = copy.copy(a)
    if a.kind in (Kind.ARROW, Kind.RUN, Kind.LINE, Kind.MEASURE) and len(a.points) >= 2:
        (x0, y0), (x1, y1) = a.points[0], a.points[-1]
        b.points = [(x0, y0), (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)]
    elif a.kind == Kind.CURVED_ARROW and len(a.points) == 3:
        pts = quad_bezier_points(a.points[0], a.points[1], a.points[2], 48)
        n = max(2, int(round(len(pts) * t)))
        sub = pts[:n]
        # Re-express the visible part as a quadratic through start, mid and end of the sub-curve.
        mid = sub[len(sub) // 2]
        s, e = sub[0], sub[-1]
        ctrl = (2 * mid[0] - (s[0] + e[0]) / 2, 2 * mid[1] - (s[1] + e[1]) / 2)
        b.points = [s, ctrl, e]
    elif a.kind == Kind.FREEHAND and len(a.points) >= 2:
        n = max(2, int(math.ceil(len(a.points) * t)))
        b.points = list(a.points[:n])
    else:
        b.opacity = a.opacity * t
    return b


def set_keyframe(a: Annotation, frame: int) -> None:
    """Record the annotation's current points/rotation as a keyframe at *frame*."""
    kfs = [k for k in a.keyframes if k["frame"] != frame]
    kfs.append({"frame": int(frame), "points": [tuple(p) for p in a.points], "rotation": a.rotation})
    a.keyframes = sorted(kfs, key=lambda k: k["frame"])


def remove_keyframe(a: Annotation, frame: int) -> bool:
    n = len(a.keyframes)
    a.keyframes = [k for k in a.keyframes if k["frame"] != frame]
    return len(a.keyframes) != n


def keyframe_at(a: Annotation, frame: int) -> Optional[dict]:
    for k in a.keyframes:
        if k["frame"] == frame:
            return k
    return None
