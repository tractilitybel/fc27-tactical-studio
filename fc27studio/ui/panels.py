"""Tactics side panels: tool palette, drawing properties, layers; and the
Present workspace telestrator bar."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap, QActionGroup, QAction
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QDoubleSpinBox, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QSlider,
                               QSpinBox, QToolBar, QToolButton, QVBoxLayout, QWidget,
                               QAbstractItemView)

from ..core.model import Annotation, Kind
from .canvas import Canvas

PALETTE = ["#ffd400", "#ff3b30", "#ff9500", "#34c759", "#00c7ff", "#3478f6",
           "#af52de", "#ffffff", "#111111"]


def color_icon(hex_color: str, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(hex_color))
    return QIcon(pm)


TOOL_GLYPHS = {
    "select": "⬚", Kind.ARROW: "➜", Kind.CURVED_ARROW: "↷", Kind.RUN: "⇢", Kind.LINE: "╱",
    Kind.FREEHAND: "✎", Kind.CIRCLE: "◯", Kind.RECT: "▭", Kind.ZONE: "⬠", Kind.TEXT: "T",
    Kind.SPOTLIGHT: "◉",
}


class ToolPalette(QToolBar):
    toolSelected = Signal(str)

    def __init__(self, parent=None, shortcuts: Optional[dict] = None):
        super().__init__("Tools", parent)
        self.setOrientation(Qt.Vertical)
        self.setMovable(False)
        self.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.group = QActionGroup(self)
        self.actions_by_tool: dict[str, QAction] = {}
        for tool in ["select"] + Kind.ALL:
            label = "Select" if tool == "select" else Kind.LABELS[tool]
            act = QAction(TOOL_GLYPHS[tool], self)
            act.setCheckable(True)
            act.setData(tool)
            act.setToolTip(label)
            act.triggered.connect(lambda _c=False, t=tool: self.toolSelected.emit(t))
            self.group.addAction(act)
            self.addAction(act)
            self.actions_by_tool[tool] = act
            w = self.widgetForAction(act)
            if w is not None:
                w.setObjectName("toolButton")
                w.setMinimumSize(38, 34)
        self.actions_by_tool["select"].setChecked(True)

    def set_tool(self, tool: str) -> None:
        act = self.actions_by_tool.get(tool)
        if act:
            act.setChecked(True)

    def set_tooltips(self, keys: dict[str, str]) -> None:
        for tool, act in self.actions_by_tool.items():
            label = "Select" if tool == "select" else Kind.LABELS[tool]
            k = keys.get(f"tool.{tool}", "")
            act.setToolTip(f"{label} ({k})" if k else label)


class ColorRow(QWidget):
    colorChosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setHorizontalSpacing(2)
        lay.setVerticalSpacing(2)
        cols = 5
        for i, c in enumerate(PALETTE):
            b = QToolButton()
            b.setObjectName("swatch")
            b.setIcon(color_icon(c, 14))
            b.setToolTip(c)
            b.setAutoRaise(True)
            b.setFixedSize(24, 22)
            b.clicked.connect(lambda _c=False, col=c: self.colorChosen.emit(col))
            lay.addWidget(b, i // cols, i % cols)
        more = QToolButton()
        more.setText("…")
        more.setFixedSize(24, 22)
        more.setToolTip("Custom colour")
        more.clicked.connect(self._custom)
        lay.addWidget(more, len(PALETTE) // cols, len(PALETTE) % cols)
        self.current = QLabel()
        self.current.setFixedSize(22, 22)
        self.current.setToolTip("Current colour")
        lay.addWidget(self.current, 0, cols + 1, 2, 1, Qt.AlignVCenter)
        lay.setColumnStretch(cols, 1)

    def show_color(self, hex_color: str) -> None:
        self._hex = hex_color
        self.current.setStyleSheet(f"background:{hex_color}; border:1px solid #556; border-radius:3px;")

    def _custom(self) -> None:
        c = QColorDialog.getColor(QColor(getattr(self, "_hex", "#ffd400")), self, "Colour")
        if c.isValid():
            self.colorChosen.emit(c.name())


def _slider(lo, hi, val) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    return s


class PropertiesPanel(QWidget):
    """Edits the selected drawings, or the defaults for new drawings."""

    def __init__(self, canvas: Canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.fps = 30.0
        self._updating = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel("New drawing style")
        self.title.setObjectName("panelTitle")
        lay.addWidget(self.title)
        form = QFormLayout()
        self.colors = ColorRow()
        self.colors.colorChosen.connect(self._set_color)
        form.addRow("Colour", self.colors)
        self.width_s = _slider(1, 40, 5)
        self.width_s.valueChanged.connect(lambda v: self._set("width", float(v), "width"))
        form.addRow("Thickness", self.width_s)
        self.opacity_s = _slider(5, 100, 100)
        self.opacity_s.valueChanged.connect(lambda v: self._set("opacity", v / 100, "opacity"))
        form.addRow("Opacity", self.opacity_s)
        self.fill_s = _slider(0, 60, 22)
        self.fill_s.valueChanged.connect(lambda v: self._set("fill_opacity", v / 100, "fill"))
        form.addRow("Fill / shade", self.fill_s)
        self.font_s = QSpinBox()
        self.font_s.setRange(8, 300)
        self.font_s.setValue(34)
        self.font_s.valueChanged.connect(lambda v: self._set("font_size", float(v), "font"))
        form.addRow("Text size", self.font_s)
        self.rot_s = QDoubleSpinBox()
        self.rot_s.setRange(-180, 180)
        self.rot_s.setSuffix("°")
        self.rot_s.setWrapping(True)
        self.rot_s.valueChanged.connect(lambda v: self._set("rotation", float(v), "rot"))
        form.addRow("Rotation", self.rot_s)
        lay.addLayout(form)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setMaximumHeight(60)
        self.text_edit.setPlaceholderText("Text of selected label")
        self.text_edit.textChanged.connect(self._text_changed)
        lay.addWidget(self.text_edit)

        vis = QGroupBox("Visible on screen")
        g = QGridLayout(vis)
        self.hold_s = QDoubleSpinBox()
        self.hold_s.setRange(0.04, 3600)
        self.hold_s.setDecimals(2)
        self.hold_s.setSuffix(" s")
        self.hold_s.setValue(3.0)
        self.hold_s.setToolTip("How long new drawings stay visible from the frame they were drawn on")
        self.hold_s.valueChanged.connect(self._hold_changed)
        g.addWidget(QLabel("New drawings"), 0, 0)
        g.addWidget(self.hold_s, 0, 1, 1, 2)
        self.start_s = QSpinBox()
        self.end_s = QSpinBox()
        for s in (self.start_s, self.end_s):
            s.setRange(0, 10 ** 8)
            s.setKeyboardTracking(False)
        self.start_s.valueChanged.connect(lambda v: self._set("start_frame", int(v), None))
        self.end_s.valueChanged.connect(lambda v: self._set("end_frame", int(v), None))
        b1 = QPushButton("◀ Now")
        b1.setToolTip("Start at the current frame")
        b1.clicked.connect(lambda: self.canvas.set_range_edge(self._doc_ids(), "start"))
        b2 = QPushButton("Now ▶")
        b2.setToolTip("End at the current frame")
        b2.clicked.connect(lambda: self.canvas.set_range_edge(self._doc_ids(), "end"))
        g.addWidget(QLabel("From frame"), 1, 0)
        g.addWidget(self.start_s, 1, 1)
        g.addWidget(b1, 1, 2)
        g.addWidget(QLabel("To frame"), 2, 0)
        g.addWidget(self.end_s, 2, 1)
        g.addWidget(b2, 2, 2)
        self.range_widgets = [self.start_s, self.end_s, b1, b2]
        lay.addWidget(vis)

        arrange = QGroupBox("Arrange")
        ag = QGridLayout(arrange)
        self.btns = {}
        for i, (key, label) in enumerate([("front", "To front"), ("forward", "Forward"),
                                           ("backward", "Backward"), ("back", "To back"),
                                           ("group", "Group"), ("ungroup", "Ungroup"),
                                           ("duplicate", "Duplicate"), ("delete", "Delete")]):
            b = QPushButton(label)
            self.btns[key] = b
            ag.addWidget(b, i // 2, i % 2)
        lay.addWidget(arrange)
        self.btns["delete"].clicked.connect(self.canvas.delete_selection)
        self.btns["duplicate"].clicked.connect(self.canvas.duplicate)
        for mode in ("front", "forward", "backward", "back"):
            self.btns[mode].clicked.connect(lambda _c=False, m=mode: self._reorder(m))
        self.btns["group"].clicked.connect(lambda: self.canvas.doc and self.canvas.doc.group(self._doc_ids()))
        self.btns["ungroup"].clicked.connect(lambda: self.canvas.doc and self.canvas.doc.ungroup(self._doc_ids()))

        self.layers = LayersList(canvas)
        lab = QLabel("Drawings on this frame")
        lab.setObjectName("panelTitle")
        lay.addWidget(lab)
        lay.addWidget(self.layers, 1)
        canvas.selectionChanged.connect(self.sync)
        self.sync()

    def _doc_ids(self) -> list[str]:
        return [a.id for a in self.canvas.selected() if not self.canvas.is_temp(a.id)]

    def _reorder(self, mode: str) -> None:
        if self.canvas.doc and self._doc_ids():
            self.canvas.doc.reorder(self._doc_ids(), mode)

    def set_fps(self, fps: float) -> None:
        self.fps = fps or 30.0
        self._hold_changed(self.hold_s.value())

    def _hold_changed(self, secs: float) -> None:
        self.canvas.style.hold_frames = max(1, int(round(secs * self.fps)))

    def _set_color(self, c: str) -> None:
        self.colors.show_color(c)
        self._set("color", c, None)

    def _set(self, attr: str, value, merge: Optional[str]) -> None:
        if self._updating:
            return
        sel = self.canvas.selected()
        if not sel:
            if hasattr(self.canvas.style, attr):
                setattr(self.canvas.style, attr, value)
            return
        for a in sel:
            if self.canvas.is_temp(a.id):
                setattr(a, attr, value)
        ids = self._doc_ids()
        if ids and self.canvas.doc:
            if attr == "start_frame":
                fn = lambda a: (setattr(a, "start_frame", value), setattr(a, "end_frame", max(a.end_frame, value)))
            elif attr == "end_frame":
                fn = lambda a: (setattr(a, "end_frame", value), setattr(a, "start_frame", min(a.start_frame, value)))
            else:
                fn = lambda a: setattr(a, attr, value)
            self.canvas.doc.modify_annotations(ids, fn, f"Change {attr.replace('_', ' ')}",
                                               merge_key=f"{attr}:{','.join(sorted(ids))}" if merge else None)
        if attr in ("color", "width", "opacity", "fill_opacity", "font_size"):
            setattr(self.canvas.style, attr, value)
        self.canvas.update()

    def _text_changed(self) -> None:
        if self._updating:
            return
        texts = [a for a in self.canvas.selected() if a.kind == Kind.TEXT]
        if len(texts) == 1:
            t = self.text_edit.toPlainText()
            a = texts[0]
            if self.canvas.is_temp(a.id):
                a.text = t
                self.canvas.update()
            elif self.canvas.doc and t != a.text:
                self.canvas.doc.modify_annotations([a.id], lambda x: setattr(x, "text", t),
                                                   "Edit text", merge_key="text:" + a.id)

    def sync(self) -> None:
        self._updating = True
        sel = self.canvas.selected()
        st = self.canvas.style
        src = sel[0] if sel else None
        n = len(sel)
        self.title.setText("New drawing style" if not n else
                           (Kind.LABELS.get(src.kind, src.kind) if n == 1 else f"{n} drawings selected"))
        color = src.color if src else st.color
        self.colors.show_color(color)
        self.width_s.setValue(int(round(src.width if src else st.width)))
        self.opacity_s.setValue(int(round((src.opacity if src else st.opacity) * 100)))
        self.fill_s.setValue(int(round((src.fill_opacity if src else st.fill_opacity) * 100)))
        self.font_s.setValue(int(round(src.font_size if src else st.font_size)))
        self.rot_s.setValue(src.rotation if src else 0.0)
        self.rot_s.setEnabled(n == 1)
        is_text = n == 1 and src.kind == Kind.TEXT
        self.text_edit.setVisible(is_text)
        if is_text and self.text_edit.toPlainText() != src.text:
            self.text_edit.setPlainText(src.text)
        doc_sel = bool(self._doc_ids())
        for w in self.range_widgets:
            w.setEnabled(doc_sel)
        if doc_sel:
            self.start_s.setValue(min(a.start_frame for a in sel if not self.canvas.is_temp(a.id)))
            self.end_s.setValue(max(a.end_frame for a in sel if not self.canvas.is_temp(a.id)))
        for k, b in self.btns.items():
            b.setEnabled(n > 0 if k not in ("group",) else len(self._doc_ids()) > 1)
        self._updating = False
        self.layers.refresh()


class LayersList(QListWidget):
    def __init__(self, canvas: Canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.itemSelectionChanged.connect(self._sel_changed)
        self.itemChanged.connect(self._item_changed)
        self._updating = False

    def refresh(self) -> None:
        self._updating = True
        self.clear()
        anns = []
        if self.canvas.doc:
            anns = [a for a in self.canvas.doc.project.annotations
                    if a.start_frame <= self.canvas.frame <= a.end_frame]
        anns += self.canvas.temp
        for a in sorted(anns, key=lambda a: -a.z):
            name = a.name or (a.text.split("\n")[0][:24] if a.kind == Kind.TEXT else Kind.LABELS.get(a.kind, a.kind))
            flags = []
            if a.group_id:
                flags.append("grouped")
            if a.locked:
                flags.append("locked")
            if self.canvas.is_temp(a.id):
                flags.append("telestrator")
            it = QListWidgetItem(color_icon(a.color), name + (f"  ({', '.join(flags)})" if flags else ""))
            it.setData(Qt.UserRole, a.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if a.visible else Qt.Unchecked)
            it.setToolTip("Tick = visible. Right-click the canvas for more options.")
            self.addItem(it)
            if a.id in self.canvas.selection:
                it.setSelected(True)
        self._updating = False

    def _sel_changed(self) -> None:
        if self._updating:
            return
        ids = {it.data(Qt.UserRole) for it in self.selectedItems()}
        self.canvas.set_selection(ids)

    def _item_changed(self, it: QListWidgetItem) -> None:
        if self._updating:
            return
        aid = it.data(Qt.UserRole)
        visible = it.checkState() == Qt.Checked
        a = self.canvas.find(aid)
        if a is None or a.visible == visible:
            return
        if self.canvas.is_temp(aid):
            a.visible = visible
            self.canvas.update()
        elif self.canvas.doc:
            self.canvas.doc.modify_annotations([aid], lambda x: setattr(x, "visible", visible),
                                               "Show drawing" if visible else "Hide drawing")


class TelestratorBar(QWidget):
    """Compact controls for live drawing in the Present workspace."""
    clearRequested = Signal()
    keepToggled = Signal(bool)
    presentationRequested = Signal()

    def __init__(self, canvas: Canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        t = QLabel("Telestrator")
        t.setObjectName("panelTitle")
        lay.addWidget(t)
        info = QLabel("Draw live over the video. Drawings are temporary and are cleared "
                      "when playback starts (or press C). Tick “Keep drawings” to save them "
                      "into the project instead.")
        info.setWordWrap(True)
        info.setObjectName("hint")
        lay.addWidget(info)
        self.colors = ColorRow()
        self.colors.colorChosen.connect(self._color)
        self.colors.show_color(canvas.style.color)
        lay.addWidget(self.colors)
        row = QHBoxLayout()
        row.addWidget(QLabel("Thickness"))
        self.width_s = _slider(2, 30, int(canvas.style.width))
        self.width_s.valueChanged.connect(lambda v: setattr(canvas.style, "width", float(v)))
        row.addWidget(self.width_s)
        lay.addLayout(row)
        self.keep = QCheckBox("Keep drawings (save to project)")
        self.keep.toggled.connect(self.keepToggled)
        lay.addWidget(self.keep)
        self.clear_on_play = QCheckBox("Clear temporary drawings on play")
        self.clear_on_play.setChecked(True)
        lay.addWidget(self.clear_on_play)
        b = QPushButton("Clear drawings (C)")
        b.clicked.connect(self.clearRequested)
        lay.addWidget(b)
        p = QPushButton("Start presentation mode (F5)")
        p.setObjectName("primary")
        p.clicked.connect(self.presentationRequested)
        lay.addWidget(p)
        lay.addSpacing(10)
        bt = QLabel("Incident playlist")
        bt.setObjectName("panelTitle")
        lay.addWidget(bt)
        self.playlist = QListWidget()
        lay.addWidget(self.playlist, 1)

    def _color(self, c: str) -> None:
        self.colors.show_color(c)
        self.canvas.style.color = c
