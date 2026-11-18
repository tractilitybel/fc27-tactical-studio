"""Video canvas: displays the current frame and lets the user draw and edit
annotations (move, resize, rotate, edit points, rubber-band select).

Coordinates: annotations are stored in *video pixel* coordinates; the view
transform maps them to widget coordinates (fit-to-window or zoomed/panned).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QCursor, QFont, QImage, QKeyEvent, QMouseEvent,
                           QPainter, QPainterPath, QPen, QTransform, QWheelEvent)
from PySide6.QtWidgets import QInputDialog, QMenu, QWidget

from ..core.document import Document
from ..core.model import Annotation, Kind
from .render import center_of, default_control, paint_annotation, paint_order, precise_bbox

HANDLE = 5.0          # half size in screen px
ROT_OFFSET = 26.0     # rotation handle distance above bbox (screen px)
LINEAR = (Kind.ARROW, Kind.RUN, Kind.LINE, Kind.CURVED_ARROW, Kind.MEASURE)
POINT_EDIT = LINEAR + (Kind.ZONE,)
BOX_KINDS = (Kind.CIRCLE, Kind.RECT, Kind.SPOTLIGHT, Kind.TEXT, Kind.FREEHAND, Kind.CUTOUT)
UNIFORM_SCALE = (Kind.TEXT, Kind.CUTOUT, Kind.TOKEN, Kind.BALL)
NO_ROTATE = (Kind.TOKEN, Kind.BALL)
POLY_TOOLS = (Kind.ZONE, Kind.CUTOUT)
COMPARE_MODES = ("off", "split", "side")


@dataclass
class ToolStyle:
    color: str = "#ffd400"
    width: float = 5.0
    opacity: float = 1.0
    fill_opacity: float = 0.22
    font_size: float = 34.0
    hold_frames: int = 90


class Canvas(QWidget):
    selectionChanged = Signal()
    stepRequested = Signal(int)
    zoomChanged = Signal(float)
    toolChanged = Signal(str)
    annotationsEdited = Signal()
    statusMessage = Signal(str)
    tokenEditRequested = Signal(str)
    compareChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 180)
        self.setAcceptDrops(False)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.image: Optional[QImage] = None
        self.video_size = QSize(1920, 1080)
        self.doc: Optional[Document] = None
        self.frame = 0
        self.editable = False          # tactics/present
        self.telestrator = False       # present: drawings go to temp list
        self.persist_telestrator = False
        self.temp: list[Annotation] = []
        self.tool = "select"
        self.style = ToolStyle()
        self.selection: set[str] = set()
        self.show_drawings = True
        self.zoom: Optional[float] = None   # None = fit
        self.center = QPointF(960, 540)     # video coords at widget centre when zoomed
        self.placeholder = "Drop a match recording here, or press Ctrl+Shift+O to open a video."
        self.on_text_request: Optional[Callable[[str], Optional[str]]] = None
        # interaction state
        self._drag: Optional[str] = None
        self._press_v = QPointF()
        self._press_w = QPointF()
        self._last_v = QPointF()
        self._before: dict = {}
        self._originals: dict[str, Annotation] = {}
        self._handle = None
        self._drawing: Optional[Annotation] = None
        self._rubber: Optional[QRectF] = None
        self._hover_v: Optional[QPointF] = None
        self._clipboard: list[Annotation] = []
        self._pan_start_center = QPointF()
        # Before/after comparison
        self.compare = "off"
        self.split = 0.5
        # Tactics board mode: drawings are not tied to frames; distances in metres
        self.board_mode = False
        self.px_per_m: Optional[float] = None
        self.token_size = 22.0
        self._lasso_moved = False

    # ================================================================ API
    def set_document(self, doc: Optional[Document]) -> None:
        self.doc = doc
        self.selection.clear()
        self.temp.clear()
        self.selectionChanged.emit()
        self.update()

    def set_image(self, img: Optional[QImage]) -> None:
        if img is not None and (self.image is None or img.size() != self.video_size):
            self.video_size = img.size()
            if self.zoom is None:
                self.center = QPointF(img.width() / 2, img.height() / 2)
        self.image = img
        self.update()

    def set_frame(self, frame: int) -> None:
        if frame != self.frame:
            self.frame = frame
            # Drop selection of drawings that are no longer visible.
            vis = {a.id for a in self.visible_annotations()}
            if self.selection - vis:
                self.selection &= vis
                self.selectionChanged.emit()
        self.update()

    def set_tool(self, tool: str) -> None:
        self._finish_zone(commit=True)
        self.tool = tool
        self.setCursor(Qt.ArrowCursor if tool == "select" else Qt.CrossCursor)
        self.toolChanged.emit(tool)
        self.update()

    def set_editable(self, editable: bool, telestrator: bool = False) -> None:
        self.editable = editable
        self.telestrator = telestrator
        if not editable:
            self.set_tool("select")
        self.update()

    def clear_telestrator(self) -> None:
        if self.temp:
            ids = {a.id for a in self.temp}
            self.temp.clear()
            self.selection -= ids
            self.selectionChanged.emit()
            self.update()

    # ------------------------------------------------------- annotation sets
    def visible_annotations(self, side: str = "after") -> list[Annotation]:
        """Drawings on the current frame. *side* filters before/after layers."""
        out = []
        if self.doc is not None:
            out.extend(self.doc.annotations_at(self.frame))
        out.extend(a for a in self.temp if a.visible)
        hidden = "after" if side == "before" else "before"
        return [a for a in out if a.layer != hidden]

    def set_compare(self, mode: str) -> None:
        if mode not in COMPARE_MODES:
            mode = "off"
        self.compare = mode
        if mode == "side":
            self._finish_zone(commit=False)
        self.compareChanged.emit(mode)
        self.update()

    def cycle_compare(self) -> None:
        i = COMPARE_MODES.index(self.compare)
        self.set_compare(COMPARE_MODES[(i + 1) % len(COMPARE_MODES)])

    def _split_x(self) -> float:
        r = self._frame_widget_rect()
        return r.left() + r.width() * self.split

    def _frame_widget_rect(self) -> QRectF:
        return self.view_transform().mapRect(QRectF(0, 0, self.video_size.width(), self.video_size.height()))

    def find(self, aid: str) -> Optional[Annotation]:
        for a in self.temp:
            if a.id == aid:
                return a
        return self.doc.find_annotation(aid) if self.doc else None

    def is_temp(self, aid: str) -> bool:
        return any(a.id == aid for a in self.temp)

    def selected(self) -> list[Annotation]:
        return [a for a in (self.find(i) for i in self.selection) if a is not None]

    def prune_selection(self) -> None:
        """Forget selected ids whose drawings no longer exist (e.g. after undo)."""
        alive = {i for i in self.selection if self.find(i) is not None}
        if alive != self.selection:
            self.selection = alive
            self.selectionChanged.emit()

    def set_selection(self, ids) -> None:
        ids = set(ids)
        if self.doc is not None:
            ids = self.doc.expand_groups(ids)
        if ids != self.selection:
            self.selection = ids
            self.selectionChanged.emit()
        self.update()

    # ============================================================ transform
    def scale_factor(self) -> float:
        if self.zoom is not None:
            return self.zoom
        vw, vh = max(1, self.video_size.width()), max(1, self.video_size.height())
        return min(self.width() / vw, self.height() / vh)

    def view_transform(self) -> QTransform:
        s = self.scale_factor()
        vw, vh = self.video_size.width(), self.video_size.height()
        if self.zoom is None:
            ox = (self.width() - vw * s) / 2
            oy = (self.height() - vh * s) / 2
        else:
            ox = self.width() / 2 - self.center.x() * s
            oy = self.height() / 2 - self.center.y() * s
        return QTransform(s, 0, 0, s, ox, oy)

    def to_video(self, p: QPointF) -> QPointF:
        inv, _ = self.view_transform().inverted()
        return inv.map(QPointF(p))

    def to_widget(self, p: QPointF) -> QPointF:
        return self.view_transform().map(QPointF(p))

    def set_zoom(self, zoom: Optional[float], anchor_w: Optional[QPointF] = None) -> None:
        if zoom is not None:
            zoom = max(0.05, min(16.0, zoom))
            if anchor_w is not None:
                before = self.to_video(anchor_w)
                old_center = self.to_video(QPointF(self.width() / 2, self.height() / 2))
                self.zoom = zoom
                self.center = old_center
                after = self.to_video(anchor_w)
                self.center += before - after
            else:
                if self.zoom is None:
                    self.center = self.to_video(QPointF(self.width() / 2, self.height() / 2))
                self.zoom = zoom
        else:
            self.zoom = None
        self.zoomChanged.emit(self.scale_factor())
        self.update()

    def zoom_by(self, factor: float, anchor_w: Optional[QPointF] = None) -> None:
        self.set_zoom(self.scale_factor() * factor, anchor_w)

    # ============================================================= painting
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(14, 16, 20))
        if self.image is None:
            p.setPen(QColor(140, 150, 165))
            f = QFont(self.font())
            f.setPointSize(12)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.placeholder)
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.scale_factor() < 2.0)
        p.setRenderHint(QPainter.Antialiasing)
        frame_rect = QRectF(0, 0, self.video_size.width(), self.video_size.height())
        if self.compare == "side":
            self._paint_side_by_side(p, frame_rect)
            return
        vt = self.view_transform()
        if self.compare == "split":
            sx = self._split_x()
            p.setClipRect(QRectF(0, 0, sx, self.height()))
            self._paint_scene(p, vt, frame_rect, "before")
            p.setClipRect(QRectF(sx, 0, self.width() - sx, self.height()))
        self._paint_scene(p, vt, frame_rect, "after")
        p.setClipping(False)
        p.setTransform(vt)
        p.setClipRect(frame_rect)
        if self._drawing is not None:
            d = self._drawing
            if d.kind == Kind.CUTOUT:
                pen = QPen(QColor(255, 255, 255), 2 / max(self.scale_factor(), 0.01), Qt.DashLine)
                p.setPen(pen)
                p.setBrush(QColor(255, 255, 255, 40))
                from PySide6.QtGui import QPolygonF
                poly = QPolygonF([QPointF(*q) for q in d.points])
                if self._hover_v is not None and not self._lasso_moved:
                    poly.append(self._hover_v)
                p.drawPolygon(poly)
            else:
                paint_annotation(p, d, frame_rect, self.px_per_m)
                if d.kind == Kind.ZONE and self._hover_v is not None and d.points:
                    pen = QPen(QColor(d.color), d.width / 2, Qt.DashLine)
                    p.setPen(pen)
                    p.drawLine(QPointF(*d.points[-1]), self._hover_v)
        p.setClipping(False)
        p.resetTransform()
        if self.compare == "split":
            self._paint_divider(p)
        if self.editable or self.selection:
            self._paint_selection(p)
        if self._rubber is not None:
            r = QRectF(self.to_widget(self._rubber.topLeft()), self.to_widget(self._rubber.bottomRight()))
            p.setPen(QPen(QColor(90, 170, 255), 1, Qt.DashLine))
            p.setBrush(QColor(90, 170, 255, 40))
            p.drawRect(r.normalized())

    def _paint_scene(self, p: QPainter, t: QTransform, frame_rect: QRectF, side: str) -> None:
        p.save()
        p.setTransform(t)
        p.drawImage(frame_rect, self.image)
        p.setClipRect(frame_rect, Qt.IntersectClip)
        if self.show_drawings:
            for a in sorted(self.visible_annotations(side), key=paint_order):
                paint_annotation(p, a, frame_rect, self.px_per_m)
        p.restore()

    def _side_transforms(self) -> tuple[QTransform, QTransform]:
        vw, vh = max(1, self.video_size.width()), max(1, self.video_size.height())
        gap = 8
        half = (self.width() - gap) / 2
        s = min(half / vw, (self.height() - 30) / vh)
        oy = (self.height() - vh * s) / 2
        left = QTransform(s, 0, 0, s, (half - vw * s) / 2, oy)
        right = QTransform(s, 0, 0, s, half + gap + (half - vw * s) / 2, oy)
        return left, right

    def _paint_side_by_side(self, p: QPainter, frame_rect: QRectF) -> None:
        lt, rt = self._side_transforms()
        self._paint_scene(p, lt, frame_rect, "before")
        self._paint_scene(p, rt, frame_rect, "after")
        for t, label in ((lt, "BEFORE"), (rt, "AFTER")):
            r = t.mapRect(frame_rect)
            # Above the picture when there is room, so the label hides nothing.
            at = QPointF(r.left(), r.top() - 30) if r.top() >= 32 else r.topLeft() + QPointF(10, 10)
            self._paint_label(p, at, label)

    def _paint_label(self, p: QPainter, at: QPointF, label: str) -> None:
        f = QFont(self.font())
        f.setPointSize(10)
        f.setBold(True)
        p.setFont(f)
        r = QRectF(at, QSize(78, 24))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 170))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QColor(255, 255, 255))
        p.drawText(r, Qt.AlignCenter, label)

    def _paint_divider(self, p: QPainter) -> None:
        fr = self._frame_widget_rect()
        x = self._split_x()
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(QPointF(x, fr.top()), QPointF(x, fr.bottom()))
        c = QPointF(x, fr.center().y())
        p.setBrush(QColor(255, 255, 255))
        p.setPen(QPen(QColor(20, 24, 30), 1))
        p.drawEllipse(c, 12, 12)
        p.setPen(QPen(QColor(20, 24, 30), 2))
        p.drawLine(c + QPointF(-6, 0), c + QPointF(-2, -4))
        p.drawLine(c + QPointF(-6, 0), c + QPointF(-2, 4))
        p.drawLine(c + QPointF(6, 0), c + QPointF(2, -4))
        p.drawLine(c + QPointF(6, 0), c + QPointF(2, 4))
        top = fr.top() + 10
        self._paint_label(p, QPointF(max(fr.left() + 10, x - 88), top), "BEFORE")
        self._paint_label(p, QPointF(min(fr.right() - 88, x + 10), top), "AFTER")

    def _paint_selection(self, p: QPainter) -> None:
        sel = self.selected()
        if not sel or not self.show_drawings:
            return
        accent = QColor(90, 170, 255)
        p.setBrush(Qt.NoBrush)
        for a in sel:
            poly = self._outline_widget(a)
            p.setPen(QPen(accent, 1, Qt.DashLine))
            p.drawPolygon(poly)
        if not self.editable:
            return
        for kind, pos in self._handles():
            if kind == "rot":
                p.setPen(QPen(accent, 1))
                p.setBrush(QColor(20, 24, 30))
                p.drawEllipse(pos, HANDLE + 1, HANDLE + 1)
            elif kind.startswith("pt"):
                p.setPen(QPen(QColor(20, 24, 30), 1))
                p.setBrush(QColor(255, 255, 255) if kind != "pt1c" else QColor(255, 200, 60))
                p.drawEllipse(pos, HANDLE, HANDLE)
            else:
                p.setPen(QPen(QColor(20, 24, 30), 1))
                p.setBrush(accent)
                p.drawRect(QRectF(pos.x() - HANDLE, pos.y() - HANDLE, HANDLE * 2, HANDLE * 2))

    def _rot_transform(self, a: Annotation) -> QTransform:
        t = QTransform()
        if a.rotation:
            c = center_of(a)
            t.translate(c.x(), c.y())
            t.rotate(a.rotation)
            t.translate(-c.x(), -c.y())
        return t

    def _outline_widget(self, a: Annotation):
        x0, y0, x1, y1 = precise_bbox(a)
        pad = a.width / 2 + 2
        from PySide6.QtGui import QPolygonF
        poly = QPolygonF(QRectF(x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad))
        return (self._rot_transform(a) * self.view_transform()).map(poly)

    def _group_bbox(self, anns) -> QRectF:
        r = QRectF()
        for a in anns:
            x0, y0, x1, y1 = precise_bbox(a)
            br = self._rot_transform(a).mapRect(QRectF(x0, y0, x1 - x0, y1 - y0))
            r = br if r.isNull() else r.united(br)
        return r

    def _handles(self) -> list[tuple[str, QPointF]]:
        """Handles in widget coordinates: ('h:<i>', pos) scale, 'rot', 'pt<i>'."""
        sel = self.selected()
        if not sel:
            return []
        out = []
        if len(sel) == 1:
            a = sel[0]
            m = self._rot_transform(a) * self.view_transform()
            if a.kind in POINT_EDIT:
                for i, (x, y) in enumerate(a.points):
                    tag = f"pt{i}"
                    if a.kind == Kind.CURVED_ARROW and i == 1 and len(a.points) == 3:
                        tag = "pt1c"
                    out.append((tag, m.map(QPointF(x, y))))
                return out
            x0, y0, x1, y1 = precise_bbox(a)
            rect = QRectF(x0, y0, x1 - x0, y1 - y0)
            for i, pt in enumerate(_box_points(rect)):
                out.append((f"h:{i}", m.map(pt)))
            if a.kind not in NO_ROTATE:
                top = m.map(QPointF(rect.center().x(), rect.top()))
                ctr = m.map(rect.center())
                d = top - ctr
                L = math.hypot(d.x(), d.y()) or 1.0
                out.append(("rot", top + d / L * ROT_OFFSET))
            return out
        rect = self._group_bbox(sel)
        vt = self.view_transform()
        for i, pt in enumerate(_box_points(rect)):
            out.append((f"h:{i}", vt.map(pt)))
        return out

    # ================================================================ mouse
    def _hit_handle(self, pos_w: QPointF):
        for kind, pos in self._handles():
            if abs(pos.x() - pos_w.x()) <= HANDLE + 3 and abs(pos.y() - pos_w.y()) <= HANDLE + 3:
                return kind
        return None

    def annotation_at(self, pos_w: QPointF) -> Optional[Annotation]:
        v = self.to_video(pos_w)
        tol = 6 / max(self.scale_factor(), 0.01)
        for a in sorted(self.visible_annotations(), key=paint_order, reverse=True):
            if a.locked:
                continue
            if a.hit((v.x(), v.y()), tol):
                return a
        return None

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        self.setFocus()
        pos = ev.position()
        v = self.to_video(pos)
        self._press_v, self._press_w, self._last_v = v, pos, v
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.LeftButton and ev.modifiers() & Qt.AltModifier):
            if self.zoom is None:
                self.set_zoom(self.scale_factor())
            self._drag = "pan"
            self._pan_start_center = QPointF(self.center)
            self.setCursor(Qt.ClosedHandCursor)
            return
        if self.image is None:
            return
        if ev.button() == Qt.LeftButton and self.compare == "split" and abs(pos.x() - self._split_x()) <= 10:
            self._drag = "divider"
            self.setCursor(Qt.SplitHCursor)
            return
        if self.compare == "side":
            return  # side-by-side is view-only
        if ev.button() == Qt.RightButton:
            self._context_menu(ev)
            return
        if ev.button() != Qt.LeftButton:
            return
        if not self.editable:
            return
        if self.tool != "select":
            self._begin_draw(v, ev)
            return
        h = self._hit_handle(pos)
        if h:
            self._begin_edit(h)
            return
        hit = self.annotation_at(pos)
        if hit is not None:
            ids = self.doc.expand_groups({hit.id}) if self.doc else {hit.id}
            if ev.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                if ids <= self.selection:
                    self.set_selection(self.selection - ids)
                    return
                self.set_selection(self.selection | ids)
            elif not ids <= self.selection:
                self.set_selection(ids)
            self._begin_edit("move")
        else:
            if not ev.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
                self.set_selection(set())
            self._drag = "rubber"
            self._rubber = QRectF(v, v)

    def _begin_edit(self, mode: str) -> None:
        self._drag = mode
        self._handle = mode
        ids = list(self.selection)
        self._before = self.doc.snapshot([i for i in ids if not self.is_temp(i)]) if self.doc else {}
        self._originals = {a.id: copy.deepcopy(a) for a in self.selected()}

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:
        pos = ev.position()
        v = self.to_video(pos)
        self._hover_v = v
        if self._drag == "pan":
            s = self.scale_factor()
            d = (pos - self._press_w) / s
            self.center = self._pan_start_center - d
            self.update()
            return
        if self._drag == "divider":
            fr = self._frame_widget_rect()
            self.split = max(0.0, min(1.0, (pos.x() - fr.left()) / max(1.0, fr.width())))
            self.update()
            return
        if self._drag is None:
            if self.compare == "split" and abs(pos.x() - self._split_x()) <= 10:
                self.setCursor(Qt.SplitHCursor)
            else:
                self._update_hover_cursor(pos)
            if self._drawing is not None and self._drawing.kind in POLY_TOOLS:
                self.update()
            return
        if self._drag == "cutline":
            a = self._drawing
            last = a.points[-1]
            if math.hypot(v.x() - last[0], v.y() - last[1]) * self.scale_factor() >= 3:
                a.points.append((v.x(), v.y()))
                self._lasso_moved = True
            self.update()
            return
        if self._drag == "draw":
            self._update_draw(v, ev.modifiers())
        elif self._drag == "rubber":
            self._rubber = QRectF(self._press_v, v)
        elif self._drag == "move":
            d = v - self._press_v
            if ev.modifiers() & Qt.ShiftModifier:
                if abs(d.x()) > abs(d.y()):
                    d.setY(0)
                else:
                    d.setX(0)
            for aid, orig in self._originals.items():
                a = self.find(aid)
                if a is not None:
                    a.points = list(orig.points)
                    a.translate(d.x(), d.y())
        elif self._drag.startswith("h:"):
            self._apply_scale(int(self._drag[2:]), v, ev.modifiers())
        elif self._drag == "rot":
            self._apply_rotation(v, ev.modifiers())
        elif self._drag.startswith("pt"):
            self._apply_point(int(self._drag[2]), v)
        self._last_v = v
        self.update()

    def mouseReleaseEvent(self, ev: QMouseEvent) -> None:
        if self._drag == "divider":
            self._drag = None
            self.setCursor(Qt.ArrowCursor if self.tool == "select" else Qt.CrossCursor)
            return
        if self._drag == "cutline":
            self._drag = None
            if self._lasso_moved and len(self._drawing.points) >= 6:
                self._finish_zone(commit=True)  # lasso traced in one drag
            self.update()
            return
        if self._drag == "pan":
            self._drag = None
            self.setCursor(Qt.ArrowCursor if self.tool == "select" else Qt.CrossCursor)
            return
        if self._drag == "draw":
            # The release position is the final end point even without a last move event.
            self._update_draw(self.to_video(ev.position()), ev.modifiers())
            self._end_draw()
        elif self._drag == "rubber":
            r = self._rubber.normalized() if self._rubber else QRectF()
            self._rubber = None
            if r.width() > 1 or r.height() > 1:
                ids = set()
                for a in self.visible_annotations():
                    if a.locked:
                        continue
                    x0, y0, x1, y1 = precise_bbox(a)
                    br = self._rot_transform(a).mapRect(QRectF(x0, y0, x1 - x0, y1 - y0))
                    if r.intersects(br):
                        ids.add(a.id)
                self.set_selection(self.selection | ids)
        elif self._drag in ("move", "rot") or (self._drag or "").startswith(("h:", "pt")):
            self._commit_edit({"move": "Move drawing", "rot": "Rotate drawing"}.get(
                self._drag, "Resize drawing" if self._drag.startswith("h:") else "Edit points"))
        self._drag = None
        self.update()

    def mouseDoubleClickEvent(self, ev: QMouseEvent) -> None:
        if self._drawing is not None and self._drawing.kind in POLY_TOOLS:
            self._finish_zone(commit=True)
            return
        if not self.editable:
            return
        hit = self.annotation_at(ev.position())
        if hit is not None and hit.kind == Kind.TEXT:
            self.edit_text(hit)
        elif hit is not None and hit.kind == Kind.TOKEN:
            self.tokenEditRequested.emit(hit.id)

    def _commit_edit(self, label: str) -> None:
        if self.doc is not None and self._before:
            after = self.doc.snapshot(self._before.keys())
            self.doc.set_annotation_states(self._before, after, label)
        self._before = {}
        self._originals = {}
        self.annotationsEdited.emit()

    def _update_hover_cursor(self, pos: QPointF) -> None:
        if not self.editable or self.tool != "select":
            return
        h = self._hit_handle(pos)
        if h == "rot":
            self.setCursor(Qt.CrossCursor)
        elif h and h.startswith("h:"):
            i = int(h[2:])
            self.setCursor([Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor,
                            Qt.SizeFDiagCursor, Qt.SizeVerCursor, Qt.SizeBDiagCursor, Qt.SizeHorCursor][i])
        elif h:
            self.setCursor(Qt.PointingHandCursor)
        elif self.annotation_at(pos) is not None:
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # ------------------------------------------------------------ transform ops
    def _apply_scale(self, handle: int, v: QPointF, mods) -> None:
        origs = list(self._originals.values())
        if not origs:
            return
        single = len(origs) == 1
        if single:
            o = origs[0]
            x0, y0, x1, y1 = precise_bbox(o)
            rect = QRectF(x0, y0, x1 - x0, y1 - y0)
            inv, _ = self._rot_transform(o).inverted()
            local = inv.map(v)
        else:
            rect = self._group_bbox(origs)
            local = v
        pts = _box_points(rect)
        anchor = pts[(handle + 4) % 8]
        grab = pts[handle]
        sx = sy = 1.0
        if handle in (0, 2, 4, 6):
            dx0, dy0 = grab.x() - anchor.x(), grab.y() - anchor.y()
            sx = (local.x() - anchor.x()) / dx0 if dx0 else 1.0
            sy = (local.y() - anchor.y()) / dy0 if dy0 else 1.0
            if mods & Qt.ShiftModifier or (single and origs[0].kind in UNIFORM_SCALE):
                s = max(abs(sx), abs(sy))
                sx = math.copysign(s, sx)
                sy = math.copysign(s, sy)
        elif handle in (1, 5):
            dy0 = grab.y() - anchor.y()
            sy = (local.y() - anchor.y()) / dy0 if dy0 else 1.0
        else:
            dx0 = grab.x() - anchor.x()
            sx = (local.x() - anchor.x()) / dx0 if dx0 else 1.0
        sx = sx if abs(sx) > 0.02 else math.copysign(0.02, sx or 1)
        sy = sy if abs(sy) > 0.02 else math.copysign(0.02, sy or 1)
        for o in origs:
            a = self.find(o.id)
            if a is None:
                continue
            a.points = list(o.points)
            a.font_size = o.font_size
            if a.kind in UNIFORM_SCALE:
                sxy = abs(sy) if handle in (1, 5) else abs(sx)
                a.scale(sxy, sxy, (anchor.x(), anchor.y()))
            else:
                a.scale(sx, sy, (anchor.x(), anchor.y()))
            if single and a.rotation:
                # Keep the anchor fixed on screen despite the pivot moving.
                before = self._rot_transform(o).map(anchor)
                after = self._rot_transform(a).map(anchor)
                a.translate(before.x() - after.x(), before.y() - after.y())

    def _apply_rotation(self, v: QPointF, mods) -> None:
        o = next(iter(self._originals.values()), None)
        if o is None:
            return
        a = self.find(o.id)
        c = center_of(o)
        ang = math.degrees(math.atan2(v.y() - c.y(), v.x() - c.x())) + 90
        if mods & Qt.ShiftModifier:
            ang = round(ang / 15) * 15
        a.rotation = ((ang + 180) % 360) - 180

    def _apply_point(self, i: int, v: QPointF) -> None:
        o = next(iter(self._originals.values()), None)
        if o is None:
            return
        a = self.find(o.id)
        inv, _ = self._rot_transform(o).inverted()
        local = inv.map(v)
        pts = list(o.points)
        if i < len(pts):
            pts[i] = (local.x(), local.y())
        a.points = pts

    # ------------------------------------------------------------ drawing
    def _new_annotation(self, kind: str, pts) -> Annotation:
        st = self.style
        if self.board_mode:
            start, end = 0, 10 ** 9
        else:
            start, end = self.frame, self.frame + max(0, st.hold_frames - 1)
        a = Annotation(kind, pts, start, end, color=st.color, width=st.width, opacity=st.opacity,
                       fill_opacity=st.fill_opacity, font_size=st.font_size)
        if kind in (Kind.TOKEN, Kind.BALL):
            a.font_size = self.token_size
        elif kind == Kind.MEASURE:
            a.font_size = 30.0
            a.width = max(3.0, st.width * 0.6)
        return a

    def next_token_number(self, color: str) -> str:
        used = {a.text for a in (self.doc.annotations if self.doc else [])
                if a.kind == Kind.TOKEN and a.color.lower() == color.lower()}
        n = 1
        while str(n) in used:
            n += 1
        return str(n)

    def _image_array(self):
        import numpy as np
        img = self.image.convertToFormat(QImage.Format_RGB888)
        arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
        return arr[:, :img.width() * 3].reshape(img.height(), img.width(), 3).copy()

    def make_cutout(self, poly) -> Optional[Annotation]:
        """Lift the player inside *poly* (video coords) out of the current frame."""
        from ..core.imaging import encode_png, make_cutout, to_b64
        if self.image is None or len(poly) < 3:
            return None
        cut = make_cutout(self._image_array(), poly)
        if cut is None:
            return None
        a = self._new_annotation(Kind.CUTOUT, [tuple(q) for q in poly])
        a.source_points = [tuple(q) for q in poly]
        a.crop = [cut.x, cut.y, int(cut.image.shape[1]), int(cut.image.shape[0])]
        a.image = to_b64(encode_png(cut.image))
        a.fill_image = to_b64(encode_png(cut.fill))
        a.layer = "after"
        a.width = 3.0
        a.fill_opacity = 0.0
        return a

    def _begin_draw(self, v: QPointF, ev: QMouseEvent) -> None:
        p = (v.x(), v.y())
        if self.tool == Kind.TEXT:
            text = self._ask_text("")
            if text:
                a = self._new_annotation(Kind.TEXT, [p])
                a.text = text
                a.fill_opacity = max(a.fill_opacity, 0.25)
                self._commit_new(a)
            return
        if self.tool in (Kind.TOKEN, Kind.BALL):
            # Clicking an existing token/ball selects it (so it can be dragged or
            # deleted) instead of stacking a new one on top.
            hit = self.annotation_at(ev.position())
            if hit is not None and hit.kind in (Kind.TOKEN, Kind.BALL):
                self.set_selection({hit.id})
                self._begin_edit("move")
                return
            a = self._new_annotation(self.tool, [p])
            if self.tool == Kind.TOKEN:
                a.text = self.next_token_number(a.color)
            self._commit_new(a)
            return
        if self.tool == Kind.CUTOUT and self._drawing is None:
            # Press-and-drag traces a lasso; separate clicks place polygon points.
            self._drawing = self._new_annotation(Kind.CUTOUT, [p])
            self._drawing.width = 3.0
            self._lasso_moved = False
            self._drag = "cutline"
            self.statusMessage.emit("Trace around the player (drag), or click points and press Enter.")
            self.update()
            return
        if self.tool in POLY_TOOLS:
            if self._drawing is None:
                self._drawing = self._new_annotation(Kind.ZONE, [p])
            else:
                first = self.to_widget(QPointF(*self._drawing.points[0]))
                if len(self._drawing.points) >= 3 and \
                        math.hypot(first.x() - ev.position().x(), first.y() - ev.position().y()) < 10:
                    self._finish_zone(commit=True)
                    return
                self._drawing.points.append(p)
            self.update()
            return
        self._drawing = self._new_annotation(self.tool, [p, p])
        self._drag = "draw"

    def _update_draw(self, v: QPointF, mods) -> None:
        a = self._drawing
        if a is None:
            return
        p = (v.x(), v.y())
        if a.kind == Kind.FREEHAND:
            last = a.points[-1]
            if math.hypot(p[0] - last[0], p[1] - last[1]) * self.scale_factor() >= 2.5:
                a.points.append(p)
            return
        x0, y0 = a.points[0]
        if mods & Qt.ShiftModifier:
            dx, dy = p[0] - x0, p[1] - y0
            if a.kind in (Kind.CIRCLE, Kind.RECT, Kind.SPOTLIGHT):
                s = max(abs(dx), abs(dy))
                p = (x0 + math.copysign(s, dx or 1), y0 + math.copysign(s, dy or 1))
            else:
                L = math.hypot(dx, dy)
                ang = round(math.atan2(dy, dx) / math.radians(15)) * math.radians(15)
                p = (x0 + L * math.cos(ang), y0 + L * math.sin(ang))
        a.points = [a.points[0], p]

    def _end_draw(self) -> None:
        a = self._drawing
        self._drawing = None
        if a is None:
            return
        (x0, y0), (x1, y1) = a.points[0], a.points[-1]
        screen_len = math.hypot(x1 - x0, y1 - y0) * self.scale_factor()
        if a.kind == Kind.FREEHAND:
            if len(a.points) < 2:
                return
            a.points = _simplify(a.points, 0.8 / max(self.scale_factor(), 0.01))
        elif screen_len < 6:
            return
        if a.kind == Kind.CURVED_ARROW:
            c = default_control(QPointF(x0, y0), QPointF(x1, y1))
            a.points = [(x0, y0), (c.x(), c.y()), (x1, y1)]
        self._commit_new(a)

    def _finish_zone(self, commit: bool) -> None:
        a = self._drawing
        if a is None or a.kind not in POLY_TOOLS:
            return
        self._drawing = None
        self._lasso_moved = False
        if commit and len(a.points) >= 3:
            if a.kind == Kind.CUTOUT:
                pts = a.points if len(a.points) < 6 else _simplify(a.points, 0.6 / max(self.scale_factor(), 0.01))
                cut = self.make_cutout(pts if len(pts) >= 3 else a.points)
                if cut is None:
                    self.statusMessage.emit("Cut-out is too small — trace around the whole player.")
                else:
                    self._commit_new(cut)
                    self.set_tool("select")
                    self.set_selection({cut.id})
                    self.statusMessage.emit("Player cut out — drag it to where they should be.")
            else:
                self._commit_new(a)
        self.update()

    def _commit_new(self, a: Annotation) -> None:
        if self.telestrator and not self.persist_telestrator:
            a.z = max((t.z for t in self.temp), default=10_000) + 1
            a.start_frame, a.end_frame = 0, 10 ** 9  # stays until cleared
            self.temp.append(a)
        elif self.doc is not None:
            self.doc.add_annotation(a)
        self.set_selection({a.id})
        self.annotationsEdited.emit()
        self.update()

    def _ask_text(self, initial: str) -> Optional[str]:
        if self.on_text_request is not None:
            return self.on_text_request(initial)
        text, ok = QInputDialog.getMultiLineText(self, "Text", "Annotation text:", initial)
        return text.strip() if ok and text.strip() else None

    def edit_text(self, a: Annotation) -> None:
        text = self._ask_text(a.text)
        if text is None or text == a.text:
            return
        if self.is_temp(a.id):
            a.text = text
            self.update()
        elif self.doc:
            self.doc.modify_annotations([a.id], lambda x: setattr(x, "text", text), "Edit text")

    # ================================================================= keys
    def keyPressEvent(self, ev: QKeyEvent) -> None:
        k = ev.key()
        if self._drawing is not None and self._drawing.kind in POLY_TOOLS:
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self._finish_zone(commit=True)
                return
            if k == Qt.Key_Escape:
                self._finish_zone(commit=False)
                return
            if k == Qt.Key_Backspace and len(self._drawing.points) > 1:
                self._drawing.points.pop()
                self.update()
                return
        if k == Qt.Key_Escape:
            if self.tool != "select":
                self.set_tool("select")
            else:
                self.set_selection(set())
            return
        if ev.modifiers() & Qt.AltModifier and k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            step = 10 if ev.modifiers() & Qt.ShiftModifier else 1
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0)
            dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0)
            self.nudge(dx, dy)
            return
        super().keyPressEvent(ev)

    def wheelEvent(self, ev: QWheelEvent) -> None:
        dy = ev.angleDelta().y()
        if not dy:
            return
        if ev.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.15 if dy > 0 else 1 / 1.15, ev.position())
        else:
            self.stepRequested.emit(-1 if dy > 0 else 1)

    # ============================================================ edit ops
    def nudge(self, dx: float, dy: float) -> None:
        ids = [i for i in self.selection]
        if not ids:
            return
        for a in self.selected():
            if self.is_temp(a.id):
                a.translate(dx, dy)
        doc_ids = [i for i in ids if not self.is_temp(i)]
        if self.doc and doc_ids:
            self.doc.modify_annotations(doc_ids, lambda a: a.translate(dx, dy), "Nudge", merge_key="nudge")
        self.update()

    def delete_selection(self) -> None:
        if not self.selection:
            return
        self.temp = [a for a in self.temp if a.id not in self.selection]
        doc_ids = [i for i in self.selection if self.doc and self.doc.find_annotation(i)]
        if doc_ids:
            self.doc.remove_annotations(doc_ids)
        self.set_selection(set())
        self.annotationsEdited.emit()

    def copy_selection(self) -> int:
        self._clipboard = [copy.deepcopy(a) for a in sorted(self.selected(), key=lambda a: a.z)]
        return len(self._clipboard)

    def paste(self, offset: float = 20.0) -> None:
        if not self._clipboard or self.doc is None:
            return
        groups: dict[str, str] = {}
        new = []
        base_start = min(a.start_frame for a in self._clipboard)
        from ..core.model import new_id
        for src in self._clipboard:
            a = src.clone()
            a.translate(offset, offset)
            dur = a.end_frame - a.start_frame
            a.start_frame = self.frame + (src.start_frame - base_start) if dur < 10 ** 8 else self.frame
            a.end_frame = a.start_frame + (dur if dur < 10 ** 8 else self.style.hold_frames)
            if a.group_id:
                a.group_id = groups.setdefault(a.group_id, new_id())
            new.append(a)
        self.doc.add_annotations(new, "Paste")
        # Next paste lands further offset so copies don't stack exactly.
        self._clipboard = [copy.deepcopy(a) for a in new]
        self.set_selection({a.id for a in new})
        self.annotationsEdited.emit()

    def set_range_edge(self, ids, edge: str) -> None:
        f = self.frame
        if self.doc is None or not ids:
            return

        def fn(a):
            if edge == "start":
                a.start_frame = min(f, a.end_frame)
            else:
                a.end_frame = max(f, a.start_frame)
        self.doc.modify_annotations(ids, fn, "Change visibility range")

    def duplicate(self) -> None:
        if self.copy_selection():
            self.paste()

    def select_all(self) -> None:
        self.set_selection({a.id for a in self.visible_annotations() if not a.locked})

    def has_clipboard(self) -> bool:
        return bool(self._clipboard)

    # ------------------------------------------------------------- context
    def _context_menu(self, ev: QMouseEvent) -> None:
        if not self.editable:
            return
        hit = self.annotation_at(ev.position())
        if hit is not None and hit.id not in self.selection:
            self.set_selection({hit.id})
        m = QMenu(self)
        if self.selection:
            if hit is not None and hit.kind == Kind.TEXT:
                m.addAction("Edit text…", lambda: self.edit_text(hit))
            m.addAction("Duplicate", self.duplicate)
            m.addAction("Copy", self.copy_selection)
            m.addSeparator()
            if self.doc:
                ids = [i for i in self.selection if not self.is_temp(i)]
                m.addAction("Bring to front", lambda: self.doc.reorder(ids, "front"))
                m.addAction("Send to back", lambda: self.doc.reorder(ids, "back"))
                if len(ids) > 1:
                    m.addAction("Group", lambda: self.doc.group(ids))
                if any(self.find(i).group_id for i in ids if self.find(i)):
                    m.addAction("Ungroup", lambda: self.doc.ungroup(ids))
                m.addSeparator()
                m.addAction("Visible from this frame", lambda: self.set_range_edge(ids, "start"))
                m.addAction("Visible until this frame", lambda: self.set_range_edge(ids, "end"))
            m.addAction("Delete", self.delete_selection)
        if self._clipboard:
            m.addAction("Paste here", self.paste)
        if not m.isEmpty():
            m.exec(QCursor.pos())


def _box_points(r: QRectF) -> list[QPointF]:
    """8 handle positions clockwise from top-left."""
    cx, cy = r.center().x(), r.center().y()
    return [QPointF(r.left(), r.top()), QPointF(cx, r.top()), QPointF(r.right(), r.top()),
            QPointF(r.right(), cy), QPointF(r.right(), r.bottom()), QPointF(cx, r.bottom()),
            QPointF(r.left(), r.bottom()), QPointF(r.left(), cy)]


def _simplify(points, tol: float):
    """Ramer–Douglas–Peucker polyline simplification."""
    if len(points) < 3:
        return list(points)
    from ..core.model import segment_distance
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        d = segment_distance(points[i], points[0], points[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > tol:
        left = _simplify(points[:idx + 1], tol)
        right = _simplify(points[idx:], tol)
        return left[:-1] + right
    return [points[0], points[-1]]
