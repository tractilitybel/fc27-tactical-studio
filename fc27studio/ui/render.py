"""Annotation rendering with QPainter, in *video pixel* coordinates.

Used by the on-screen canvas and by image export so both are identical.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter,
                           QPainterPath, QPen, QPolygonF, QTransform)

from ..core.model import Annotation, Kind, quad_bezier_points


def ndarray_to_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    img = QImage(arr.data, w, h, arr.strides[0], QImage.Format_RGB888)
    return img.copy()  # detach from numpy buffer


def qcolor(hex_color: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_color)
    if not c.isValid():
        c = QColor("#ffd400")
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def text_font(size: float) -> QFont:
    f = QFont("Segoe UI")
    f.setPixelSize(max(6, int(round(size))))
    f.setBold(True)
    return f


def text_rect(ann: Annotation) -> QRectF:
    fm = QFontMetricsF(text_font(ann.font_size))
    lines = (ann.text or " ").split("\n")
    w = max(fm.horizontalAdvance(l) for l in lines)
    h = fm.lineSpacing() * len(lines)
    pad = ann.font_size * 0.3
    x, y = ann.points[0]
    return QRectF(x, y, w + 2 * pad, h + 2 * pad)


def precise_bbox(ann: Annotation) -> tuple[float, float, float, float]:
    if ann.kind == Kind.TEXT and ann.points:
        r = text_rect(ann)
        return r.left(), r.top(), r.right(), r.bottom()
    return ann.bbox()


def center_of(ann: Annotation) -> QPointF:
    x0, y0, x1, y1 = precise_bbox(ann)
    return QPointF((x0 + x1) / 2, (y0 + y1) / 2)


def _arrow_head(p: QPainter, tip: QPointF, frm: QPointF, size: float, color: QColor) -> None:
    ang = math.atan2(tip.y() - frm.y(), tip.x() - frm.x())
    a1 = ang + math.radians(152)
    a2 = ang - math.radians(152)
    poly = QPolygonF([tip,
                      QPointF(tip.x() + size * math.cos(a1), tip.y() + size * math.sin(a1)),
                      QPointF(tip.x() + size * math.cos(a2), tip.y() + size * math.sin(a2))])
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(poly)
    p.restore()


def _shorten(a: QPointF, b: QPointF, by: float) -> QPointF:
    dx, dy = b.x() - a.x(), b.y() - a.y()
    L = math.hypot(dx, dy)
    if L <= by or L == 0:
        return a
    return QPointF(b.x() - dx / L * by, b.y() - dy / L * by)


def paint_annotation(p: QPainter, ann: Annotation, frame_rect: QRectF) -> None:
    if not ann.points:
        return
    color = qcolor(ann.color, 1.0)
    p.save()
    p.setOpacity(max(0.0, min(1.0, ann.opacity)))
    if ann.rotation:
        c = center_of(ann)
        p.translate(c)
        p.rotate(ann.rotation)
        p.translate(-c)
    pts = [QPointF(x, y) for x, y in ann.points]
    w = max(0.5, ann.width)
    pen = QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    head = max(12.0, w * 3.6)
    k = ann.kind

    if k in (Kind.ARROW, Kind.RUN, Kind.LINE) and len(pts) >= 2:
        a, b = pts[0], pts[-1]
        if k == Kind.RUN:
            pen.setStyle(Qt.CustomDashLine)
            pen.setDashPattern([2.2, 1.6])
        p.setPen(pen)
        end = b if k == Kind.LINE else _shorten(a, b, head * 0.7)
        p.drawLine(a, end)
        if k != Kind.LINE:
            _arrow_head(p, b, a, head, color)
        else:
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            for q in (a, b):
                p.drawEllipse(q, w * 0.9, w * 0.9)

    elif k == Kind.CURVED_ARROW and len(pts) >= 2:
        if len(pts) == 2:
            pts = [pts[0], default_control(pts[0], pts[1]), pts[1]]
        a, c, b = pts[0], pts[1], pts[2]
        path = QPainterPath(a)
        # Stop the curve a little before the tip so the head covers it.
        samples = quad_bezier_points((a.x(), a.y()), (c.x(), c.y()), (b.x(), b.y()), 40)
        trimmed = [QPointF(*s) for s in samples]
        total = 0.0
        cut = len(trimmed) - 1
        for i in range(len(trimmed) - 1, 0, -1):
            total += math.hypot(trimmed[i].x() - trimmed[i - 1].x(), trimmed[i].y() - trimmed[i - 1].y())
            if total >= head * 0.7:
                cut = i - 1
                break
        for q in trimmed[1:cut + 1]:
            path.lineTo(q)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        tail = trimmed[max(0, len(trimmed) - 6)]
        _arrow_head(p, b, tail, head, color)

    elif k == Kind.FREEHAND:
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if len(pts) == 1:
            p.setBrush(color)
            p.setPen(Qt.NoPen)
            p.drawEllipse(pts[0], w / 2, w / 2)
        else:
            path = QPainterPath(pts[0])
            for i in range(1, len(pts) - 1):
                mid = (pts[i] + pts[i + 1]) / 2
                path.quadTo(pts[i], mid)
            path.lineTo(pts[-1])
            p.drawPath(path)

    elif k in (Kind.CIRCLE, Kind.RECT) and len(pts) >= 2:
        r = QRectF(pts[0], pts[-1]).normalized()
        p.setPen(pen)
        p.setBrush(qcolor(ann.color, ann.fill_opacity))
        if k == Kind.CIRCLE:
            p.drawEllipse(r)
        else:
            p.drawRoundedRect(r, w, w)

    elif k == Kind.ZONE:
        poly = QPolygonF(pts)
        p.setPen(pen)
        p.setBrush(qcolor(ann.color, ann.fill_opacity))
        if len(pts) >= 3:
            p.drawPolygon(poly)
        else:
            p.drawPolyline(poly)

    elif k == Kind.TEXT:
        r = text_rect(ann)
        bg = QColor(0, 0, 0)
        bg.setAlphaF(max(0.0, min(1.0, ann.fill_opacity * 2.4)))
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, ann.font_size * 0.2, ann.font_size * 0.2)
        p.setPen(color)
        p.setFont(text_font(ann.font_size))
        pad = ann.font_size * 0.3
        p.drawText(r.adjusted(pad, pad, -pad, -pad), Qt.AlignLeft | Qt.AlignTop, ann.text)

    elif k == Kind.SPOTLIGHT and len(pts) >= 2:
        r = QRectF(pts[0], pts[-1]).normalized()
        # Darken the whole frame except the ellipse.  The frame rectangle is
        # mapped into the (possibly rotated) local space so it stays upright.
        rot = QTransform()
        if ann.rotation:
            c = center_of(ann)
            rot.translate(c.x(), c.y())
            rot.rotate(ann.rotation)
            rot.translate(-c.x(), -c.y())
        inv, _ok = rot.inverted()
        outer = QPainterPath()
        outer.addPolygon(inv.map(QPolygonF(frame_rect)))
        outer.closeSubpath()
        hole = QPainterPath()
        hole.addEllipse(r)
        shade = QColor(0, 0, 0)
        shade.setAlphaF(max(0.0, min(0.95, 0.2 + ann.fill_opacity * 1.6)))
        p.fillPath(outer.subtracted(hole), shade)
        ring = QPen(color, w)
        p.setPen(ring)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r)
    p.restore()


def default_control(a: QPointF, b: QPointF) -> QPointF:
    mx, my = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
    dx, dy = b.x() - a.x(), b.y() - a.y()
    return QPointF(mx - dy * 0.25, my + dx * 0.25)


def paint_annotations(p: QPainter, anns: Iterable[Annotation], frame_rect: QRectF) -> None:
    for a in sorted(anns, key=lambda a: a.z):
        paint_annotation(p, a, frame_rect)


def render_frame(frame: QImage, anns: Iterable[Annotation]) -> QImage:
    """Full-resolution composite of a frame and its annotations."""
    out = frame.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    paint_annotations(p, anns, QRectF(0, 0, out.width(), out.height()))
    p.end()
    return out.convertToFormat(QImage.Format_RGB32)
