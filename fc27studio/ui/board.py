"""2D tactics board: pitch rendering, formations and the board side panel."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget, QColorDialog, QCheckBox)

from ..core.model import Annotation, Board, Kind
from ..core.video import format_timecode

PITCH_L, PITCH_W = 105.0, 68.0      # metres
PX_PER_M = 10.0
MARGIN_M = 5.0


def board_size() -> tuple[int, int]:
    return (int((PITCH_L + 2 * MARGIN_M) * PX_PER_M), int((PITCH_W + 2 * MARGIN_M) * PX_PER_M))


def to_px(x_m: float, y_m: float) -> tuple[float, float]:
    """Pitch metres (0..105, 0..68 from the top-left corner) → board pixels."""
    return ((x_m + MARGIN_M) * PX_PER_M, (y_m + MARGIN_M) * PX_PER_M)


def render_pitch() -> QImage:
    w, h = board_size()
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.fillRect(0, 0, w, h, QColor(28, 92, 50))
    stripe = PITCH_L / 14
    for i in range(14):
        if i % 2:
            x0, _ = to_px(i * stripe, 0)
            p.fillRect(QRectF(x0, 0, stripe * PX_PER_M, h), QColor(32, 101, 55))
    pen = QPen(QColor(235, 240, 235), 0.12 * PX_PER_M)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = PX_PER_M

    def rect(x, y, rw, rh):
        x0, y0 = to_px(x, y)
        p.drawRect(QRectF(x0, y0, rw * s, rh * s))

    def circle(x, y, r):
        p.drawEllipse(QPointF(*to_px(x, y)), r * s, r * s)

    rect(0, 0, PITCH_L, PITCH_W)
    p.drawLine(QPointF(*to_px(PITCH_L / 2, 0)), QPointF(*to_px(PITCH_L / 2, PITCH_W)))
    circle(PITCH_L / 2, PITCH_W / 2, 9.15)
    cy = PITCH_W / 2
    for side in (0, 1):
        x_pa = 0 if side == 0 else PITCH_L - 16.5
        x_ga = 0 if side == 0 else PITCH_L - 5.5
        rect(x_pa, cy - 20.16, 16.5, 40.32)
        rect(x_ga, cy - 9.16, 5.5, 18.32)
        spot = 11 if side == 0 else PITCH_L - 11
        p.setBrush(QColor(235, 240, 235))
        circle(spot, cy, 0.25)
        p.setBrush(Qt.NoBrush)
        # Penalty arc: the part of the 9.15 m circle outside the box.
        cx, cyp = to_px(spot, cy)
        r = 9.15 * s
        arc = QRectF(cx - r, cyp - r, 2 * r, 2 * r)
        span = 2 * 53.13
        start = -53.13 if side == 0 else 180 - 53.13
        p.drawArc(arc, int(start * 16), int(span * 16))
        gx = -2 if side == 0 else PITCH_L
        rect(gx, cy - 3.66, 2, 7.32)
    p.setBrush(QColor(235, 240, 235))
    circle(PITCH_L / 2, cy, 0.25)
    p.setBrush(Qt.NoBrush)
    for (x, y, a0) in ((0, 0, 270), (PITCH_L, 0, 180), (0, PITCH_W, 0), (PITCH_L, PITCH_W, 90)):
        cx, cyy = to_px(x, y)
        p.drawArc(QRectF(cx - s, cyy - s, 2 * s, 2 * s), a0 * 16, 90 * 16)
    p.end()
    return img


# Positions in metres for a team attacking left → right (x: own goal → opponent, y: top → bottom).
FORMATIONS: dict[str, list[tuple[float, float]]] = {
    "4-3-3": [(5, 34), (22, 10), (20, 26), (20, 42), (22, 58), (38, 20), (35, 34), (38, 48),
              (50, 12), (53, 34), (50, 56)],
    "4-2-3-1": [(5, 34), (22, 10), (20, 26), (20, 42), (22, 58), (33, 26), (33, 42), (45, 12),
                (45, 34), (45, 56), (55, 34)],
    "4-4-2": [(5, 34), (22, 10), (20, 26), (20, 42), (22, 58), (37, 10), (35, 27), (35, 41),
              (37, 58), (52, 27), (52, 41)],
    "4-1-2-1-2": [(5, 34), (22, 10), (20, 26), (20, 42), (22, 58), (31, 34), (38, 22), (38, 46),
                  (45, 34), (54, 27), (54, 41)],
    # Two defensive midfielders behind a #10, as in FC's 3-5-2.
    "3-5-2": [(5, 34), (20, 20), (19, 34), (20, 48), (38, 8), (30, 27), (30, 41), (44, 34),
              (38, 60), (54, 27), (54, 41)],
    "3-4-3": [(5, 34), (20, 20), (19, 34), (20, 48), (36, 10), (34, 28), (34, 40), (36, 58),
              (50, 14), (53, 34), (50, 54)],
    "5-3-2": [(5, 34), (25, 6), (19, 20), (18, 34), (19, 48), (25, 62), (36, 22), (34, 34),
              (36, 46), (51, 27), (51, 41)],
    "4-5-1": [(5, 34), (22, 10), (20, 26), (20, 42), (22, 58), (38, 9), (34, 24), (32, 34),
              (34, 44), (38, 59), (52, 34)],
}
NUMBERS = {
    "4-3-3": ["1", "2", "5", "4", "3", "8", "6", "10", "7", "9", "11"],
    "4-2-3-1": ["1", "2", "5", "4", "3", "6", "8", "7", "10", "11", "9"],
    "4-4-2": ["1", "2", "5", "4", "3", "7", "8", "6", "11", "9", "10"],
    "4-1-2-1-2": ["1", "2", "5", "4", "3", "6", "8", "7", "10", "9", "11"],
    "3-5-2": ["1", "4", "5", "15", "2", "6", "8", "10", "3", "9", "11"],
    "3-4-3": ["1", "5", "4", "6", "2", "8", "7", "3", "11", "9", "10"],
    "5-3-2": ["1", "2", "5", "4", "6", "3", "8", "10", "7", "9", "11"],
    "4-5-1": ["1", "2", "5", "4", "3", "7", "8", "6", "10", "11", "9"],
}


def formation_tokens(name: str, home: bool, color: str, size: float = 22.0) -> list[Annotation]:
    out = []
    for (x, y), num in zip(FORMATIONS[name], NUMBERS[name]):
        if not home:  # away team attacks right → left
            x, y = PITCH_L - x, PITCH_W - y
        out.append(Annotation(Kind.TOKEN, [to_px(x, y)], 0, 10 ** 9, color=color, text=num,
                              font_size=size, name=""))
    return out


class BoardPanel(QWidget):
    """Board list + team setup. Emits requests; the main window applies them."""
    boardSelected = Signal(str)
    addRequested = Signal()
    duplicateRequested = Signal()
    renameRequested = Signal()
    deleteRequested = Signal()
    formationRequested = Signal(bool, str)    # home?, formation
    clearTeamRequested = Signal(bool)
    colorChanged = Signal(bool, str)
    linkRequested = Signal()
    gotoLinkRequested = Signal()
    exportRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        t = QLabel("Tactics boards")
        t.setObjectName("panelTitle")
        lay.addWidget(t)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setMaximumHeight(130)
        self.list.currentItemChanged.connect(
            lambda cur, _p: cur is not None and self.boardSelected.emit(cur.data(Qt.UserRole)))
        self.list.itemDoubleClicked.connect(lambda _it: self.renameRequested.emit())
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for label, sig in (("New", self.addRequested), ("Duplicate", self.duplicateRequested),
                           ("Rename", self.renameRequested), ("Delete", self.deleteRequested)):
            b = QPushButton(label)
            b.clicked.connect(sig)
            row.addWidget(b)
        lay.addLayout(row)

        teams = QGroupBox("Teams")
        g = QGridLayout(teams)
        self.formation = {}
        self.color_btn = {}
        for r, (home, label) in enumerate(((True, "Home"), (False, "Away"))):
            cb = QPushButton()
            cb.setFixedSize(26, 22)
            cb.setToolTip(f"{label} team colour")
            cb.clicked.connect(lambda _c=False, h=home: self._pick_color(h))
            self.color_btn[home] = cb
            combo = QComboBox()
            combo.addItems(list(FORMATIONS))
            self.formation[home] = combo
            apply = QPushButton("Place")
            apply.setToolTip(f"Place the {label.lower()} team in this formation (replaces its tokens)")
            apply.clicked.connect(lambda _c=False, h=home: self.formationRequested.emit(h, self.formation[h].currentText()))
            clear = QPushButton("Clear")
            clear.setToolTip(f"Remove the {label.lower()} team's tokens")
            clear.clicked.connect(lambda _c=False, h=home: self.clearTeamRequested.emit(h))
            g.addWidget(QLabel(label), r, 0)
            g.addWidget(cb, r, 1)
            g.addWidget(combo, r, 2)
            g.addWidget(apply, r, 3)
            g.addWidget(clear, r, 4)
        hint = QLabel("Drag tokens to move players. Double-click a token to set its number and name. "
                      "P adds a token in the current colour, O adds the ball, M measures distances.")
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        g.addWidget(hint, 2, 0, 1, 5)
        lay.addWidget(teams)

        link = QGroupBox("Video link")
        lg = QVBoxLayout(link)
        self.link_label = QLabel("Not linked to a video moment.")
        self.link_label.setWordWrap(True)
        lg.addWidget(self.link_label)
        lrow = QHBoxLayout()
        b1 = QPushButton("Link to current frame")
        b1.clicked.connect(self.linkRequested)
        self.goto_btn = QPushButton("Show video moment")
        self.goto_btn.clicked.connect(self.gotoLinkRequested)
        lrow.addWidget(b1)
        lrow.addWidget(self.goto_btn)
        lg.addLayout(lrow)
        lay.addWidget(link)
        ex = QPushButton("Export board image…")
        ex.clicked.connect(self.exportRequested)
        lay.addWidget(ex)
        lay.addStretch(1)
        self.formation[False].setCurrentText("4-4-2")
        self._colors = {True: "#e63946", False: "#3a86ff"}

    def _pick_color(self, home: bool) -> None:
        c = QColorDialog.getColor(QColor(self._colors[home]), self, "Team colour")
        if c.isValid():
            self.colorChanged.emit(home, c.name())

    def show_boards(self, boards: list[Board], current: Optional[str]) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for b in boards:
            it = QListWidgetItem(b.name)
            it.setData(Qt.UserRole, b.id)
            self.list.addItem(it)
            if b.id == current:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)

    def show_board(self, board: Optional[Board], fps: float) -> None:
        for home in (True, False):
            col = (board.home_color if home else board.away_color) if board else self._colors[home]
            self._colors[home] = col
            self.color_btn[home].setStyleSheet(f"background:{col}; border:1px solid #556; border-radius:3px;")
        if board is None or board.linked_frame is None:
            self.link_label.setText("Not linked to a video moment.")
            self.goto_btn.setEnabled(False)
        else:
            f = board.linked_frame
            self.link_label.setText(f"Linked to {format_timecode(f / fps, fps, frame=f)} (frame {f}).")
            self.goto_btn.setEnabled(True)
