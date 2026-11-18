"""Pitch calibration UI: landmark picker, per-frame homographies that follow
the camera, and player statistics from tracked runs."""
from __future__ import annotations

import threading
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from ..core.calibration import LANDMARKS, Calibration, apply_h, pitch_lines, point_errors, solve
from ..core.tracking import CameraMotion
from ..core.video import VideoSource


# ------------------------------------------------------------ camera path
class _PathWorker(QObject):
    done = Signal(object)          # {frame: step H}

    def __init__(self, path, index, a: int, b: int):
        super().__init__()
        self.path, self.index, self.a, self.b = path, index, a, b

    @Slot()
    def run(self):
        steps = {}
        try:
            src = VideoSource(self.path, self.index, cache_frames=4)
            cam = CameraMotion()
            cam.start(src.frame(self.a))
            for f in range(self.a + 1, self.b + 1):
                H = cam.step(src.frame(f))
                if H is None:
                    break
                steps[f] = H
            src.close()
        except Exception:
            pass
        from PySide6.QtWidgets import QApplication
        self.moveToThread(QApplication.instance().thread())
        self.done.emit(steps)


class CalibrationService(QObject):
    """Image→pitch homography for any frame near a calibration. Camera motion
    between frames (step homographies) is measured in the background and
    chained from the nearest calibrated frame."""
    updated = Signal()
    MAX_GAP_SECONDS = 30.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.calibs: list[Calibration] = []
        self.steps: dict[int, np.ndarray] = {}     # f -> H(img_{f-1} -> img_f)
        self.cache: dict[int, np.ndarray] = {}
        self.video = None                          # (path, index, fps, frame_count)
        self._busy = False
        self._pending: Optional[tuple[int, int]] = None
        self._thread = None
        self._worker = None

    def set_video(self, path, index, fps, frame_count) -> None:
        self.video = (path, index, fps, frame_count)
        self.steps.clear()
        self.cache.clear()

    def set_calibrations(self, calibs: list[Calibration]) -> None:
        self.calibs = [c for c in calibs if c.H is not None]
        self.cache.clear()
        self.updated.emit()

    def add_steps(self, steps) -> None:
        for f, H in steps:
            self.steps[int(f)] = np.asarray(H, np.float64)
        self.cache.clear()

    def nearest(self, frame: int) -> Optional[Calibration]:
        if not self.calibs or not self.video:
            return None
        c = min(self.calibs, key=lambda c: abs(c.frame - frame))
        if abs(c.frame - frame) > self.MAX_GAP_SECONDS * self.video[2]:
            return None
        return c

    def get(self, frame: int) -> Optional[np.ndarray]:
        frame = int(frame)
        if frame in self.cache:
            return self.cache[frame]
        c = self.nearest(frame)
        if c is None:
            return None
        if c.frame == frame:
            self.cache[frame] = c.H
            return c.H
        H = self._chain(c, frame)
        if H is not None:
            self.cache[frame] = H
        return H

    def _chain(self, c: Calibration, f: int) -> Optional[np.ndarray]:
        M = np.eye(3)                       # maps img_c -> img_f
        if f > c.frame:
            for t in range(c.frame + 1, f + 1):
                S = self.steps.get(t)
                if S is None:
                    return None
                M = S @ M
        else:
            for t in range(c.frame, f, -1):
                S = self.steps.get(t)       # img_{t-1} -> img_t
                if S is None:
                    return None
                M = np.linalg.inv(S) @ M
        return c.H @ np.linalg.inv(M)

    def snapshot(self) -> Callable[[int], Optional[np.ndarray]]:
        """A thread-safe, frozen copy for background work (e.g. video export)."""
        frozen = CalibrationService.__new__(CalibrationService)
        frozen.calibs = list(self.calibs)
        frozen.steps = dict(self.steps)
        frozen.cache = dict(self.cache)
        frozen.video = self.video
        return lambda f: CalibrationService.get(frozen, f)

    def ensure(self, frame: int, to: Optional[int] = None) -> None:
        """Measure camera motion between the nearest calibration and frame(s) in the background."""
        if self.video is None:
            return
        lo_f, hi_f = (frame, frame) if to is None else (min(frame, to), max(frame, to))
        c = self.nearest(lo_f) or self.nearest(hi_f)
        if c is None:
            return
        a, b = min(c.frame, lo_f), max(c.frame, hi_f)
        missing = [t for t in range(a + 1, b + 1) if t not in self.steps]
        if not missing:
            return
        req = (min(missing) - 1, max(missing))
        if self._busy:
            self._pending = req
            return
        self._start(*req)

    def _start(self, a: int, b: int) -> None:
        path, index, _fps, _n = self.video
        self._busy = True
        self._thread = QThread()
        self._worker = _PathWorker(path, index, a, b)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_done, Qt.QueuedConnection)
        self._thread.start()

    @Slot(object)
    def _on_done(self, steps) -> None:
        self.steps.update(steps)
        self.cache.clear()
        self._thread.quit()
        self._thread.wait()
        self._worker.deleteLater()
        self._busy = False
        self.updated.emit()
        if self._pending:
            a, b = self._pending
            self._pending = None
            if any(t not in self.steps for t in range(a + 1, b + 1)):
                self._start(a, b)

    def stop(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)


# ---------------------------------------------------------- player stats
def run_stats(boxes: dict, fps: float, homography_at: Callable[[int], Optional[np.ndarray]]):
    """Distance covered and speeds of a tracked player (feet position on the pitch).
    Returns (frames, pitch_xy, distance_m, top_kmh, avg_kmh) or None."""
    frames, pos = [], []
    for f in sorted(int(k) for k in boxes):
        H = homography_at(f)
        if H is None:
            continue
        x, y, w, h = boxes[f] if f in boxes else boxes[str(f)]
        p = apply_h(H, [(x + w / 2, y + h)])[0]
        frames.append(f)
        pos.append(p)
    if len(pos) < max(3, int(fps * 0.5)):
        return None
    P = np.array(pos)
    k = max(1, int(fps * 0.2))                        # smooth tracking jitter (0.2 s)
    ker = np.ones(k) / k
    Ps = np.column_stack([np.convolve(P[:, i], ker, mode="same") for i in (0, 1)])
    Ps[:k], Ps[-k:] = P[:k], P[-k:]
    seg = np.linalg.norm(np.diff(Ps, axis=0), axis=1)
    dist = float(seg.sum())
    t = (frames[-1] - frames[0]) / fps
    win = max(1, int(fps * 0.5))
    speeds = [np.linalg.norm(Ps[i + win] - Ps[i]) / (win / fps) for i in range(0, len(Ps) - win)]
    top = float(max(speeds) * 3.6) if speeds else 0.0
    avg = float(dist / t * 3.6) if t > 0 else 0.0
    return frames, Ps, dist, top, avg


# --------------------------------------------------------------- picker UI
class PitchPicker(QWidget):
    """Mini pitch showing the landmarks; click one to choose it."""
    picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.selected: Optional[str] = None
        self.placed: dict[str, float] = {}       # name -> error px
        self.setMouseTracking(True)
        self._hover = None

    def _geom(self):
        m = 10
        w = self.width() - 2 * m
        s = min(w / 105.0, (self.height() - 2 * m) / 68.0)
        ox = (self.width() - 105 * s) / 2
        oy = (self.height() - 68 * s) / 2
        return s, ox, oy

    def _to_w(self, x, y):
        s, ox, oy = self._geom()
        return QPointF(ox + x * s, oy + y * s)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(23, 26, 32))
        s, ox, oy = self._geom()
        p.fillRect(QRectF(ox, oy, 105 * s, 68 * s), QColor(30, 96, 54))
        p.setPen(QPen(QColor(230, 235, 230, 200), 1))
        for poly in pitch_lines(40):
            for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
                p.drawLine(self._to_w(x0, y0), self._to_w(x1, y1))
        for name, (x, y) in LANDMARKS.items():
            c = self._to_w(x, y)
            if name == self.selected:
                col, r = QColor(255, 210, 0), 6
            elif name in self.placed:
                err = self.placed[name]
                col, r = (QColor(60, 220, 120) if err < 5 else QColor(255, 140, 40)), 5
            elif name == self._hover:
                col, r = QColor(255, 255, 255), 5
            else:
                col, r = QColor(200, 210, 220, 160), 3.2
            p.setPen(QPen(QColor(10, 12, 16), 1))
            p.setBrush(col)
            p.drawEllipse(c, r, r)
        if self._hover:
            p.setPen(QColor(255, 255, 255))
            f = QFont(self.font())
            f.setPointSizeF(8.5)
            p.setFont(f)
            p.drawText(QRectF(4, self.height() - 18, self.width() - 8, 16), Qt.AlignCenter, self._hover)

    def _nearest(self, pos):
        best, bd = None, 12.0
        for name, (x, y) in LANDMARKS.items():
            c = self._to_w(x, y)
            d = ((c.x() - pos.x()) ** 2 + (c.y() - pos.y()) ** 2) ** 0.5
            if d < bd:
                best, bd = name, d
        return best

    def mouseMoveEvent(self, e):
        h = self._nearest(e.position())
        if h != self._hover:
            self._hover = h
            self.update()

    def mousePressEvent(self, e):
        n = self._nearest(e.position())
        if n:
            self.selected = n
            self.picked.emit(n)
            self.update()


class CalibrationPanel(QWidget):
    closeRequested = Signal()
    saveRequested = Signal()
    deleteRequested = Signal()
    pointsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        t = QLabel("Calibrate the pitch")
        t.setObjectName("panelTitle")
        lay.addWidget(t)
        hint = QLabel("1. Click a point on this mini pitch.\n2. Click the same spot on the video.\n"
                      "Repeat for at least 4 points spread over the visible pitch (box corners, the "
                      "halfway line, the centre circle…). 5–6 points give the best accuracy.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        lay.addWidget(hint)
        self.picker = PitchPicker()
        lay.addWidget(self.picker)
        self.current = QLabel("Pick a point on the mini pitch.")
        self.current.setWordWrap(True)
        lay.addWidget(self.current)
        self.list = QListWidget()
        self.list.setMaximumHeight(150)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        rm = QPushButton("Remove point")
        rm.clicked.connect(self._remove)
        clear = QPushButton("Clear")
        clear.clicked.connect(self._clear)
        row.addWidget(rm)
        row.addWidget(clear)
        lay.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row2 = QHBoxLayout()
        self.save_btn = QPushButton("Save calibration")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.saveRequested)
        self.del_btn = QPushButton("Delete for this frame")
        self.del_btn.clicked.connect(self.deleteRequested)
        row2.addWidget(self.save_btn)
        row2.addWidget(self.del_btn)
        lay.addLayout(row2)
        done = QPushButton("Done")
        done.clicked.connect(self.closeRequested)
        lay.addWidget(done)
        lay.addStretch(1)
        self.points: list[dict] = []
        self.picker.picked.connect(lambda n: self.current.setText(f"Now click <b>{n}</b> on the video."))
        self.H = None

    def load(self, points: list[dict]) -> None:
        self.points = [dict(p) for p in points]
        self.picker.selected = None
        self.current.setText("Pick a point on the mini pitch.")
        self.refresh()

    def place(self, x: float, y: float) -> bool:
        name = self.picker.selected
        if not name:
            self.current.setText("First pick which point this is on the mini pitch.")
            return False
        self.points = [p for p in self.points if p["name"] != name] + [{"name": name, "x": x, "y": y}]
        # advance to a sensible next point: nothing selected until the user picks
        self.picker.selected = None
        self.current.setText("Pick the next point on the mini pitch.")
        self.refresh()
        self.pointsChanged.emit()
        return True

    def _remove(self) -> None:
        it = self.list.currentItem()
        if it is None:
            return
        name = it.data(Qt.UserRole)
        self.points = [p for p in self.points if p["name"] != name]
        self.refresh()
        self.pointsChanged.emit()

    def _clear(self) -> None:
        self.points = []
        self.refresh()
        self.pointsChanged.emit()

    def refresh(self) -> None:
        H, err, msg = solve(self.points)
        self.H = H
        errs = point_errors(self.points, H) if H is not None else [0.0] * len(self.points)
        self.picker.placed = {p["name"]: e for p, e in zip(self.points, errs)}
        self.list.clear()
        for p, e in zip(self.points, errs):
            it = QListWidgetItem(f"{p['name']}" + (f"   ({e:.1f} px)" if H is not None and len(self.points) > 4 else ""))
            it.setData(Qt.UserRole, p["name"])
            self.list.addItem(it)
        self.status.setText(msg)
        self.save_btn.setEnabled(H is not None)
        self.picker.update()
