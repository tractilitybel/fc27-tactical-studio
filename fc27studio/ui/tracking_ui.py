"""Background player tracking and the drawings that follow a tracked player."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from ..core.model import Annotation, Kind, new_id
from ..core.tracking import Box, CameraMotion, PlayerTracker
from ..core.video import VideoSource

STYLES = {
    "ring": "Ring at the feet",
    "spotlight": "Spotlight",
    "label": "Name label",
    "ring_label": "Ring + name label",
}


@dataclass
class TrackResult:
    start: int
    boxes: list = field(default_factory=list)      # [(frame, (x, y, w, h), confidence)]
    camera: list = field(default_factory=list)     # [(frame, 3x3 list)] homography from frame-1 to frame
    lost_at: Optional[int] = None


class TrackWorker(QObject):
    progressed = Signal(float)
    finished = Signal(object, str)                 # TrackResult, error ("" ok, "cancelled")

    def __init__(self, path, index, start: int, box: Box, end: int):
        super().__init__()
        self.path, self.index, self.start, self.box, self.end = path, index, start, box, end
        self.cancel = False

    @Slot()
    def run(self):
        res = TrackResult(self.start)
        err = ""
        try:
            src = VideoSource(self.path, self.index, cache_frames=4)
            try:
                frame = src.frame(self.start)
                tr = PlayerTracker(frame, self.box)
                cam = CameraMotion()
                cam.start(frame)
                res.boxes.append((self.start, tuple(self.box), 1.0))
                n = max(1, self.end - self.start)
                for f in range(self.start + 1, self.end + 1):
                    if self.cancel:
                        err = "cancelled"
                        break
                    frame = src.frame(f)
                    step = tr.step(frame)
                    H = cam.step(frame, exclude=[step.box])
                    if H is not None:
                        res.camera.append((f, H.tolist()))
                    if not step.ok:
                        res.lost_at = f
                        break
                    res.boxes.append((f, tuple(step.box), step.confidence))
                    if (f - self.start) % 4 == 0:
                        self.progressed.emit((f - self.start) / n)
            finally:
                src.close()
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
        from PySide6.QtWidgets import QApplication
        self.moveToThread(QApplication.instance().thread())
        self.finished.emit(res, err)


# ----------------------------------------------------------- box <-> drawing
def ring_points(box: Box) -> list:
    x, y, w, h = box
    cx, feet = x + w / 2, y + h
    rx = max(10.0, w * 0.85)
    ry = rx * 0.32
    return [(cx - rx, feet - ry * 0.9), (cx + rx, feet + ry * 1.1)]


def spot_points(box: Box) -> list:
    x, y, w, h = box
    px, py = w * 0.6, h * 0.18
    return [(x - px, y - py), (x + w + px, y + h + py)]


def label_point(box: Box, font: float) -> list:
    x, y, w, h = box
    return [(x + w / 2 - font * 1.2, y - font * 2.0)]


def box_from_annotation(a: Annotation) -> Optional[Box]:
    """Inverse of the mappings above: the player box implied by a tracked drawing
    (used when the user corrected it and wants to track on from there)."""
    style = a.track_style
    if not a.points:
        return None
    size = None
    if a.track_boxes:
        # player size from the nearest tracked frame (more reliable than guessing from the drawing)
        near = min(a.track_boxes, key=lambda k: abs(int(k) - a.start_frame))
        size = a.track_boxes[near][2], a.track_boxes[near][3]
    if style in ("ring", "ring_label") and len(a.points) >= 2:
        (x0, y0), (x1, y1) = a.points[0], a.points[-1]
        rx = (x1 - x0) / 2
        w = rx / 0.85
        h = w * 2.6
        cx = (x0 + x1) / 2
        feet = (y0 + y1) / 2 - (rx * 0.32 * 0.1)
        return (cx - w / 2, feet - h, w, h)
    if style == "spotlight" and len(a.points) >= 2:
        (x0, y0), (x1, y1) = a.points[0], a.points[-1]
        w = (x1 - x0) / 2.2
        h = (y1 - y0) / 1.36
        return (x0 + w * 0.6, y0 + h * 0.18, w, h)
    if style == "label":
        fx, fy = a.points[0]
        w, h = size or (a.font_size * 1.5, a.font_size * 3.9)
        cx = fx + a.font_size * 1.2
        return (cx - w / 2, fy + a.font_size * 2.0, w, h)
    return None


def apply_track(a: Annotation, res: TrackResult, step: int = 1) -> None:
    """Turn tracked boxes into per-frame keyframes for drawing *a*."""
    kfs = [k for k in a.keyframes if k["frame"] < res.start]   # keep earlier manual/tracked part
    for i, (f, box, _c) in enumerate(res.boxes):
        if i % step and i != len(res.boxes) - 1:
            continue
        if a.track_style in ("ring", "ring_label") and a.kind == Kind.CIRCLE:
            pts = ring_points(box)
        elif a.kind == Kind.SPOTLIGHT:
            pts = spot_points(box)
        else:
            pts = label_point(box, a.font_size)
        kfs.append({"frame": int(f), "points": [tuple(p) for p in pts], "rotation": 0.0})
    a.keyframes = sorted(kfs, key=lambda k: k["frame"])
    last = res.boxes[-1][0] if res.boxes else res.start
    a.end_frame = max(last, a.start_frame)
    a.track_boxes = {int(f): [float(v) for v in box] for f, box, _ in res.boxes} | \
        {k: v for k, v in (a.track_boxes or {}).items() if int(k) < res.start}


def make_tracked(style: str, box: Box, frame: int, color: str, label: str, width: float) -> list[Annotation]:
    out = []
    gid = new_id() if style == "ring_label" else None
    if style in ("ring", "ring_label"):
        r = Annotation(Kind.CIRCLE, ring_points(box), frame, frame, color=color, width=max(3.0, width * 0.8),
                       fill_opacity=0.18, name="Tracked ring")
        r.track_style = style
        r.group_id = gid
        out.append(r)
    if style == "spotlight":
        s = Annotation(Kind.SPOTLIGHT, spot_points(box), frame, frame, color=color, width=2.0,
                       fill_opacity=0.25, name="Tracked spotlight")
        s.track_style = style
        out.append(s)
    if style in ("label", "ring_label"):
        t = Annotation(Kind.TEXT, label_point(box, 24.0), frame, frame, color=color, text=label or "Player",
                       font_size=24.0, fill_opacity=0.3, name="Tracked label")
        t.track_style = "label"
        t.group_id = gid
        out.append(t)
    return out
