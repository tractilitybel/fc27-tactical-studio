"""Timeline with scrubbing, bookmark markers, A/B loop, drawing ranges,
zoom/scroll for long matches and hover thumbnails; plus the transport bar."""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QSpinBox, QToolButton,
                               QWidget, QSizePolicy)

from ..core.video import format_timecode
from .playback import SPEEDS


@dataclass
class Marker:
    id: str
    frame: int
    end: Optional[int]
    color: str
    title: str


class ThumbPopup(QLabel):
    def __init__(self, parent):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setStyleSheet("background:#0e1014; border:1px solid #3a4250; color:#dfe6ee; padding:2px;")
        self.setAlignment(Qt.AlignCenter)


class Timeline(QWidget):
    seekRequested = Signal(int)
    scrubStarted = Signal()
    scrubFinished = Signal()
    hoverFrame = Signal(int)
    markerClicked = Signal(str)
    markerDoubleClicked = Signal(str)

    RULER_H = 20
    MARK_H = 18
    ANN_H = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(self.RULER_H + self.MARK_H + self.ANN_H + 14)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.frame_count = 0
        self.fps = 30.0
        self.current = 0
        self.v0 = 0.0
        self.v1 = 1.0
        self.markers: list[Marker] = []
        self.ranges: list[tuple[int, int, str]] = []
        self.loop_a: Optional[int] = None
        self.loop_b: Optional[int] = None
        self.loop_enabled = False
        self.show_frames = True
        self.thumbnails_enabled = True
        self._scrubbing = False
        self._hover_x: Optional[float] = None
        self._thumbs: "OrderedDict[int, QImage]" = OrderedDict()
        self._popup = ThumbPopup(self)
        self._popup_frame = -1
        self.thumb_bucket = 15

    def sizeHint(self) -> QSize:
        return QSize(800, self.RULER_H + self.MARK_H + self.ANN_H + 14)

    # ----------------------------------------------------------------- data
    def set_video(self, frame_count: int, fps: float) -> None:
        self.frame_count = frame_count
        self.fps = fps or 30.0
        self.v0, self.v1 = 0.0, float(max(1, frame_count - 1))
        self.thumb_bucket = max(1, int(round(self.fps / 2)))
        self._thumbs.clear()
        self.update()

    def set_current(self, frame: int) -> None:
        self.current = frame
        # Keep playhead visible when zoomed.
        span = self.v1 - self.v0
        if self.frame_count and (frame < self.v0 or frame > self.v1):
            self.v0 = max(0.0, min(frame - span * 0.1, self.frame_count - 1 - span))
            self.v1 = self.v0 + span
        self.update()

    def set_markers(self, markers: list[Marker]) -> None:
        self.markers = markers
        self.update()

    def set_ranges(self, ranges: list[tuple[int, int, str]]) -> None:
        self.ranges = ranges
        self.update()

    def set_loop(self, a, b, enabled) -> None:
        self.loop_a, self.loop_b, self.loop_enabled = a, b, enabled
        self.update()

    def add_thumbnail(self, frame: int, img: QImage) -> None:
        key = frame // self.thumb_bucket
        self._thumbs[key] = img
        self._thumbs.move_to_end(key)
        while len(self._thumbs) > 400:
            self._thumbs.popitem(last=False)
        if self._popup.isVisible() and self._popup_frame // self.thumb_bucket == key:
            self._show_popup(self._popup_frame)

    # ----------------------------------------------------------- geometry
    def _x_of(self, frame: float) -> float:
        span = max(1e-9, self.v1 - self.v0)
        return 8 + (frame - self.v0) / span * (self.width() - 16)

    def _frame_at(self, x: float) -> int:
        span = self.v1 - self.v0
        f = self.v0 + (x - 8) / max(1, self.width() - 16) * span
        return max(0, min(self.frame_count - 1, int(round(f))))

    def zoom(self, factor: float, around_x: Optional[float] = None) -> None:
        if not self.frame_count:
            return
        total = float(max(1, self.frame_count - 1))
        span = self.v1 - self.v0
        new_span = max(min(total, span / factor), min(total, self.fps * 2))
        anchor = self._frame_at(around_x) if around_x is not None else self.current
        rel = (anchor - self.v0) / span if span else 0.5
        v0 = anchor - rel * new_span
        v0 = max(0.0, min(v0, total - new_span))
        self.v0, self.v1 = v0, v0 + new_span
        self.update()

    def reset_zoom(self) -> None:
        self.v0, self.v1 = 0.0, float(max(1, self.frame_count - 1))
        self.update()

    # ------------------------------------------------------------- paint
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), QColor(22, 25, 31))
        if not self.frame_count:
            p.setPen(QColor(110, 118, 130))
            p.drawText(self.rect(), Qt.AlignCenter, "No video loaded")
            return
        track_top = self.RULER_H
        mark_top = track_top
        ann_top = mark_top + self.MARK_H + 2
        bottom = H - 4
        # Track background
        p.fillRect(QRectF(8, track_top, W - 16, bottom - track_top), QColor(30, 34, 42))
        # Ruler ticks
        self._paint_ruler(p)
        # Loop region
        if self.loop_a is not None:
            xa = self._x_of(self.loop_a)
            xb = self._x_of(self.loop_b) if self.loop_b is not None else xa
            col = QColor(70, 160, 255, 70 if self.loop_enabled else 30)
            p.fillRect(QRectF(xa, track_top, max(2, xb - xa), bottom - track_top), col)
            p.setPen(QPen(QColor(70, 160, 255), 1))
            p.drawLine(QPointF(xa, track_top), QPointF(xa, bottom))
            if self.loop_b is not None:
                p.drawLine(QPointF(xb, track_top), QPointF(xb, bottom))
        # Drawing ranges
        for s, e, color in self.ranges:
            if e < self.v0 or s > self.v1:
                continue
            x0, x1 = self._x_of(max(s, self.v0)), self._x_of(min(e, self.v1))
            c = QColor(color)
            c.setAlpha(170)
            p.fillRect(QRectF(x0, ann_top, max(2, x1 - x0), self.ANN_H), c)
        # Bookmarks
        for m in self.markers:
            if m.frame < self.v0 - 1 or m.frame > self.v1 + 1:
                continue
            x = self._x_of(m.frame)
            c = QColor(m.color)
            if m.end is not None and m.end > m.frame:
                xe = self._x_of(min(m.end, self.v1))
                cc = QColor(c)
                cc.setAlpha(80)
                p.fillRect(QRectF(x, mark_top + 4, max(1, xe - x), self.MARK_H - 8), cc)
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawPolygon(QPolygonF([QPointF(x - 5, mark_top + 1), QPointF(x + 5, mark_top + 1),
                                     QPointF(x + 5, mark_top + 9), QPointF(x, mark_top + 14),
                                     QPointF(x - 5, mark_top + 9)]))
            p.setPen(QPen(c, 1))
            p.drawLine(QPointF(x, mark_top + 14), QPointF(x, bottom))
        # Hover line
        if self._hover_x is not None and not self._scrubbing:
            p.setPen(QPen(QColor(255, 255, 255, 60), 1))
            p.drawLine(QPointF(self._hover_x, track_top), QPointF(self._hover_x, bottom))
        # Playhead
        x = self._x_of(self.current)
        if 0 <= x <= W:
            p.setPen(QPen(QColor(255, 70, 70), 2))
            p.drawLine(QPointF(x, 2), QPointF(x, bottom))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 70, 70))
            p.drawPolygon(QPolygonF([QPointF(x - 6, 0), QPointF(x + 6, 0), QPointF(x, 8)]))
        # Zoom indicator
        total = max(1, self.frame_count - 1)
        if self.v1 - self.v0 < total - 0.5:
            p.fillRect(QRectF(8, H - 3, W - 16, 3), QColor(45, 50, 60))
            a = 8 + self.v0 / total * (W - 16)
            b = 8 + self.v1 / total * (W - 16)
            p.fillRect(QRectF(a, H - 3, max(4, b - a), 3), QColor(120, 130, 150))

    def _paint_ruler(self, p: QPainter) -> None:
        span_s = (self.v1 - self.v0) / self.fps
        px_per_s = (self.width() - 16) / max(span_s, 1e-6)
        steps = [1 / self.fps, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800]
        step = next((s for s in steps if s * px_per_s >= 70), steps[-1])
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() - 1.5))
        p.setFont(f)
        t = math.floor(self.v0 / self.fps / step) * step
        end_t = self.v1 / self.fps
        while t <= end_t + step:
            x = self._x_of(t * self.fps)
            if 8 <= x <= self.width() - 8:
                p.setPen(QColor(90, 98, 112))
                p.drawLine(QPointF(x, self.RULER_H - 6), QPointF(x, self.RULER_H))
                p.setPen(QColor(150, 158, 170))
                label = format_timecode(t, self.fps, show_frames=step < 1)
                p.drawText(QRectF(x + 3, 2, 90, self.RULER_H - 6), Qt.AlignLeft | Qt.AlignVCenter, label)
                sub = step / 5
                if sub * px_per_s > 6:
                    p.setPen(QColor(60, 66, 78))
                    for i in range(1, 5):
                        xs = self._x_of((t + sub * i) * self.fps)
                        p.drawLine(QPointF(xs, self.RULER_H - 3), QPointF(xs, self.RULER_H))
            t += step

    # ------------------------------------------------------------- mouse
    def _marker_at(self, pos: QPointF) -> Optional[Marker]:
        if not (self.RULER_H <= pos.y() <= self.RULER_H + self.MARK_H):
            return None
        best, bd = None, 7.0
        for m in self.markers:
            d = abs(self._x_of(m.frame) - pos.x())
            if d < bd:
                best, bd = m, d
        return best

    def mousePressEvent(self, ev) -> None:
        if not self.frame_count or ev.button() != Qt.LeftButton:
            return
        m = self._marker_at(ev.position())
        if m is not None:
            self.markerClicked.emit(m.id)
            self.seekRequested.emit(m.frame)
            return
        self._scrubbing = True
        self._popup.hide()
        self.scrubStarted.emit()
        self.seekRequested.emit(self._frame_at(ev.position().x()))

    def mouseDoubleClickEvent(self, ev) -> None:
        m = self._marker_at(ev.position())
        if m is not None:
            self.markerDoubleClicked.emit(m.id)

    def mouseMoveEvent(self, ev) -> None:
        if not self.frame_count:
            return
        x = ev.position().x()
        self._hover_x = x
        f = self._frame_at(x)
        if self._scrubbing:
            self.seekRequested.emit(f)
        else:
            m = self._marker_at(ev.position())
            self.setToolTip(f"{m.title}" if m else "")
            if self.thumbnails_enabled:
                self._show_popup(f)
                self.hoverFrame.emit(f)
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        if self._scrubbing:
            self._scrubbing = False
            self.scrubFinished.emit()

    def leaveEvent(self, _ev) -> None:
        self._hover_x = None
        self._popup.hide()
        self.update()

    def wheelEvent(self, ev) -> None:
        dy = ev.angleDelta().y()
        if not dy:
            return
        if ev.modifiers() & Qt.ControlModifier:
            self.zoom(1.25 if dy > 0 else 0.8, ev.position().x())
        else:
            span = self.v1 - self.v0
            total = max(1, self.frame_count - 1)
            shift = -span * 0.1 * (1 if dy > 0 else -1)
            self.v0 = max(0.0, min(self.v0 + shift, total - span))
            self.v1 = self.v0 + span
            self.update()

    def thumb_cached(self, frame: int) -> Optional[QImage]:
        return self._thumbs.get(frame // self.thumb_bucket)

    def _show_popup(self, frame: int) -> None:
        self._popup_frame = frame
        img = self.thumb_cached(frame)
        tc = format_timecode(frame / self.fps, self.fps, frame=frame, show_frames=self.show_frames)
        if img is not None:
            pm = QPixmap.fromImage(img).scaledToWidth(180, Qt.SmoothTransformation)
            canvas = QPixmap(pm.width(), pm.height() + 18)
            canvas.fill(QColor(14, 16, 20))
            pp = QPainter(canvas)
            pp.drawPixmap(0, 0, pm)
            pp.setPen(QColor(230, 235, 240))
            pp.drawText(QRectF(0, pm.height(), pm.width(), 18), Qt.AlignCenter, tc)
            pp.end()
            self._popup.setPixmap(canvas)
        else:
            self._popup.setText(tc)
        self._popup.adjustSize()
        x = int(self._x_of(frame) - self._popup.width() / 2)
        gp = self.mapToGlobal(QPoint(x, -self._popup.height() - 6))
        self._popup.move(gp)
        self._popup.show()


def _tb(text: str, tip: str) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.setMinimumWidth(30)
    return b


class Transport(QWidget):
    """Playback buttons, timecode and speed controls."""
    frameEntered = Signal(int)
    speedChosen = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(2)
        self.btn_start = _tb("⏮", "Go to start (Home)")
        self.btn_prev_bm = _tb("◆◀", "Previous bookmark (Ctrl+Left)")
        self.btn_back = _tb("◀|", "Previous frame (Left)")
        self.btn_rev = _tb("◀", "Play reverse (J)")
        self.btn_play = _tb("▶", "Play / pause (Space)")
        self.btn_fwd = _tb("|▶", "Next frame (Right)")
        self.btn_next_bm = _tb("▶◆", "Next bookmark (Ctrl+Right)")
        self.btn_end = _tb("⏭", "Go to end (End)")
        for b in (self.btn_start, self.btn_prev_bm, self.btn_back, self.btn_rev, self.btn_play,
                  self.btn_fwd, self.btn_next_bm, self.btn_end):
            lay.addWidget(b)
        self.btn_play.setMinimumWidth(40)
        lay.addSpacing(10)
        self.timecode = QLabel("00:00.00 / 00:00.00")
        self.timecode.setObjectName("timecode")
        self.timecode.setMinimumWidth(170)
        lay.addWidget(self.timecode)
        lay.addWidget(QLabel("Frame"))
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 0)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.setMinimumWidth(90)
        self.frame_spin.setObjectName("frameBox")  # arrow buttons hidden via the stylesheet
        self.frame_spin.setToolTip("Exact frame number of the picture on screen.\n"
                                   "Type a number and press Enter to jump to it.")
        self.frame_spin.valueChanged.connect(self._frame_typed)
        lay.addWidget(self.frame_spin)
        lay.addStretch(1)
        self.btn_a = _tb("A", "Set loop start ([)")
        self.btn_b = _tb("B", "Set loop end (])")
        self.btn_loop = _tb("⟲ Loop", "Toggle A/B loop (Ctrl+L)")
        self.btn_loop.setCheckable(True)
        for b in (self.btn_a, self.btn_b, self.btn_loop):
            lay.addWidget(b)
        lay.addSpacing(10)
        lay.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"{s:g}×", s)
        self.speed.setCurrentIndex(SPEEDS.index(1.0))
        self.speed.activated.connect(lambda i: self.speedChosen.emit(self.speed.itemData(i)))
        lay.addWidget(self.speed)
        self._updating = False

    def _frame_typed(self, v: int) -> None:
        if not self._updating:
            self.frameEntered.emit(v)

    def update_state(self, current: int, total: int, fps: float, playing: bool, speed: float,
                     loop_enabled: bool, show_frames: bool = True) -> None:
        self._updating = True
        self.frame_spin.setRange(0, max(0, total - 1))
        if not self.frame_spin.hasFocus():
            self.frame_spin.setValue(current)
        self._updating = False
        cur = format_timecode(current / fps if fps else 0, fps, frame=current, show_frames=show_frames)
        tot = format_timecode(max(0, total - 1) / fps if fps else 0, fps, frame=max(0, total - 1),
                              show_frames=show_frames)
        self.timecode.setText(f"{cur} / {tot}")
        self.btn_play.setText("⏸" if playing else "▶")
        idx = self.speed.findData(speed)
        if idx >= 0:
            self.speed.setCurrentIndex(idx)
        else:
            self.speed.setEditText(f"{speed:g}×")
        self.btn_loop.setChecked(loop_enabled)
