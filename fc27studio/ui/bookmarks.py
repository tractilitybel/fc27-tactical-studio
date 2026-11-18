"""Incident bookmarks panel and editor dialog."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QCompleter, QDialog,
                               QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget, QCheckBox,
                               QColorDialog, QTableWidget, QTableWidgetItem, QHeaderView,
                               QMessageBox)

from ..core.document import Document
from ..core.model import Bookmark
from ..core.video import format_timecode


def split_list(text: str) -> list[str]:
    seen, out = set(), []
    for part in text.split(","):
        p = part.strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


class MultiCompleterEdit(QLineEdit):
    """Comma-separated entry with completion of the current item."""

    def __init__(self, words: list[str], parent=None):
        super().__init__(parent)
        self._completer = QCompleter(words, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setWidget(self)
        self._completer.activated.connect(self._insert)
        self.textEdited.connect(self._on_edit)

    def _on_edit(self, text: str) -> None:
        prefix = text.split(",")[-1].strip()
        if not prefix:
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount():
            self._completer.complete()

    def _insert(self, word: str) -> None:
        parts = self.text().split(",")
        parts[-1] = " " + word if len(parts) > 1 else word
        self.setText(",".join(parts) + ", ")


class BookmarkDialog(QDialog):
    def __init__(self, parent, bm: Bookmark, doc: Document, fps: float, frame_count: int,
                 current_frame: int, title: str = "Bookmark"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.bm = bm
        proj = doc.project
        form = QFormLayout()
        self.title = QLineEdit(bm.title)
        self.title.setPlaceholderText("e.g. Full-back caught too high")
        form.addRow("Title", self.title)
        self.category = QComboBox()
        for c in proj.categories:
            pm = QPixmap(12, 12)
            pm.fill(QColor(c["color"]))
            self.category.addItem(QIcon(pm), c["name"])
        i = self.category.findText(bm.category)
        self.category.setCurrentIndex(max(0, i))
        form.addRow("Category", self.category)
        self.tags = MultiCompleterEdit(proj.all_tags())
        self.tags.setText(", ".join(bm.tags))
        self.tags.setPlaceholderText("comma separated, e.g. counter, left wing")
        form.addRow("Tags", self.tags)
        self.players = MultiCompleterEdit(proj.all_players())
        self.players.setText(", ".join(bm.players))
        self.players.setPlaceholderText("comma separated player names")
        form.addRow("Players", self.players)
        self.rating = QComboBox()
        self.rating.addItems(["–", "★", "★★", "★★★", "★★★★", "★★★★★"])
        self.rating.setCurrentIndex(bm.rating)
        form.addRow("Importance", self.rating)
        row = QHBoxLayout()
        self.start = QSpinBox()
        self.start.setRange(0, max(0, frame_count - 1))
        self.start.setValue(bm.frame)
        self.has_end = QCheckBox("Clip end")
        self.end = QSpinBox()
        self.end.setRange(0, max(0, frame_count - 1))
        self.end.setValue(bm.end_frame if bm.end_frame is not None else min(frame_count - 1, bm.frame + int(fps * 5)))
        self.has_end.setChecked(bm.end_frame is not None)
        self.end.setEnabled(self.has_end.isChecked())
        self.has_end.toggled.connect(self.end.setEnabled)
        now = QPushButton("Current")
        now.setToolTip("Set the clip end to the current frame")
        now.clicked.connect(lambda: (self.has_end.setChecked(True), self.end.setValue(current_frame)))
        row.addWidget(self.start)
        row.addWidget(self.has_end)
        row.addWidget(self.end)
        row.addWidget(now)
        form.addRow("Start frame", row)
        self.time_label = QLabel()
        self.start.valueChanged.connect(lambda v: self.time_label.setText(format_timecode(v / fps, fps, frame=v)))
        self.time_label.setText(format_timecode(bm.frame / fps, fps, frame=bm.frame))
        form.addRow("Time", self.time_label)
        self.notes = QPlainTextEdit(bm.notes)
        self.notes.setPlaceholderText("What happened, what should have happened…")
        self.notes.setMinimumHeight(90)
        form.addRow("Notes", self.notes)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.title.setFocus()

    def values(self) -> dict:
        end = self.end.value() if self.has_end.isChecked() else None
        start = self.start.value()
        if end is not None and end < start:
            start, end = end, start
        return dict(title=self.title.text().strip(), category=self.category.currentText(),
                    tags=split_list(self.tags.text()), players=split_list(self.players.text()),
                    rating=self.rating.currentIndex(), frame=start, end_frame=end,
                    notes=self.notes.toPlainText().strip())


class CategoriesDialog(QDialog):
    def __init__(self, parent, categories: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("Bookmark categories")
        self.setMinimumSize(360, 360)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Name", "Colour"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        for c in categories:
            self._add(c["name"], c["color"])
        self.table.cellDoubleClicked.connect(self._pick)
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add("New category", "#cccccc", edit=True))
        rem = QPushButton("Remove")
        rem.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Double-click a colour to change it."))
        lay.addWidget(self.table)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(bb)

    def _add(self, name, color, edit=False):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name))
        it = QTableWidgetItem(color)
        it.setBackground(QColor(color))
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 1, it)
        if edit:
            self.table.editItem(self.table.item(r, 0))

    def _pick(self, row, col):
        if col != 1:
            return
        it = self.table.item(row, 1)
        c = QColorDialog.getColor(QColor(it.text()), self, "Category colour")
        if c.isValid():
            it.setText(c.name())
            it.setBackground(c)

    def categories(self) -> list[dict]:
        out, seen = [], set()
        for r in range(self.table.rowCount()):
            name = (self.table.item(r, 0).text() or "").strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append({"name": name, "color": self.table.item(r, 1).text()})
        return out or [{"name": "Other", "color": "#95a5a6"}]


class BookmarkPanel(QWidget):
    jumpRequested = Signal(int)
    editRequested = Signal(str)
    addRequested = Signal()
    deleteRequested = Signal(list)
    thumbnailWanted = Signal(int)
    categoriesRequested = Signal()
    exportVideoRequested = Signal(list)   # bookmark ids (selected, or all shown)

    SORTS = ["Time", "Category", "Importance", "Recently added", "Title"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: Optional[Document] = None
        self.fps = 30.0
        self.thumb_lookup: Callable[[int], Optional[QImage]] = lambda f: None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        head = QLabel("Incidents")
        head.setObjectName("panelTitle")
        lay.addWidget(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, tags, players, notes…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        lay.addWidget(self.search)
        row = QHBoxLayout()
        self.cat_filter = QComboBox()
        self.cat_filter.currentIndexChanged.connect(self.refresh)
        self.player_filter = QComboBox()
        self.player_filter.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.cat_filter, 1)
        row.addWidget(self.player_filter, 1)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Sort"))
        self.sort = QComboBox()
        self.sort.addItems(self.SORTS)
        self.sort.currentIndexChanged.connect(self.refresh)
        row2.addWidget(self.sort, 1)
        self.count = QLabel("")
        row2.addWidget(self.count)
        lay.addLayout(row2)
        self.list = QListWidget()
        self.list.setIconSize(QSize(112, 63))
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setSpacing(2)
        self.list.itemDoubleClicked.connect(lambda it: self.editRequested.emit(it.data(Qt.UserRole)))
        self.list.itemClicked.connect(self._clicked)
        self.list.currentItemChanged.connect(lambda cur, _p: cur and self._clicked(cur))
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        b_add = QPushButton("Add (B)")
        b_add.clicked.connect(self.addRequested)
        b_edit = QPushButton("Edit")
        b_edit.clicked.connect(self._edit_current)
        b_del = QPushButton("Delete")
        b_del.clicked.connect(self._delete_selected)
        b_cat = QPushButton("Categories…")
        b_cat.clicked.connect(self.categoriesRequested)
        for b in (b_add, b_edit, b_del, b_cat):
            btns.addWidget(b)
        lay.addLayout(btns)
        b_vid = QPushButton("Export video of these incidents…")
        b_vid.setToolTip("Make a highlights video (MP4) of the selected incidents, or of all incidents shown")
        b_vid.clicked.connect(self._export_video)
        lay.addWidget(b_vid)
        self._items: dict[str, QListWidgetItem] = {}

    def set_document(self, doc: Optional[Document], fps: float) -> None:
        self.doc = doc
        self.fps = fps or 30.0
        self.refresh_filters()
        self.refresh()

    def refresh_filters(self) -> None:
        for combo, items, label in ((self.cat_filter,
                                     [c["name"] for c in self.doc.project.categories] if self.doc else [],
                                     "All categories"),
                                    (self.player_filter, self.doc.project.all_players() if self.doc else [],
                                     "All players")):
            cur = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, None)
            for it in items:
                combo.addItem(it, it)
            i = combo.findText(cur)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)

    def filtered(self) -> list[Bookmark]:
        if self.doc is None:
            return []
        cat = self.cat_filter.currentData()
        player = self.player_filter.currentData()
        text = self.search.text()
        out = [b for b in self.doc.project.bookmarks
               if b.matches(text) and (cat is None or b.category == cat)
               and (player is None or player in b.players)]
        mode = self.sort.currentText()
        if mode == "Category":
            out.sort(key=lambda b: (b.category.lower(), b.frame))
        elif mode == "Importance":
            out.sort(key=lambda b: (-b.rating, b.frame))
        elif mode == "Recently added":
            out.sort(key=lambda b: -b.created)
        elif mode == "Title":
            out.sort(key=lambda b: (b.title.lower(), b.frame))
        else:
            out.sort(key=lambda b: (b.frame, b.created))
        return out

    def refresh(self) -> None:
        sel = {it.data(Qt.UserRole) for it in self.list.selectedItems()}
        self.list.clear()
        self._items.clear()
        bms = self.filtered()
        total = len(self.doc.project.bookmarks) if self.doc else 0
        self.count.setText(f"{len(bms)} / {total}")
        for b in bms:
            it = QListWidgetItem(self._label(b))
            it.setData(Qt.UserRole, b.id)
            it.setToolTip(b.notes or b.title)
            it.setIcon(QIcon(self._thumb_pixmap(b)))
            self.list.addItem(it)
            self._items[b.id] = it
            if b.id in sel:
                it.setSelected(True)

    def _label(self, b: Bookmark) -> str:
        tc = format_timecode(b.frame / self.fps, self.fps, frame=b.frame)
        if b.end_frame is not None:
            tc += f" → {format_timecode(b.end_frame / self.fps, self.fps, frame=b.end_frame)}"
        stars = " " + "★" * b.rating if b.rating else ""
        lines = [f"{tc}  ·  {b.category}{stars}", b.title or "(untitled)"]
        extra = []
        if b.players:
            extra.append("👤 " + ", ".join(b.players))
        if b.tags:
            extra.append("# " + ", ".join(b.tags))
        if extra:
            lines.append("   ".join(extra))
        return "\n".join(lines)

    def _thumb_pixmap(self, b: Bookmark) -> QPixmap:
        img = self.thumb_lookup(b.frame)
        pm = QPixmap(112, 63)
        pm.fill(QColor(30, 34, 42))
        p = QPainter(pm)
        if img is not None:
            p.drawPixmap(0, 0, QPixmap.fromImage(img).scaled(112, 63, Qt.KeepAspectRatioByExpanding,
                                                              Qt.SmoothTransformation))
        else:
            self.thumbnailWanted.emit(b.frame)
        color = QColor(self.doc.project.category_color(b.category)) if self.doc else QColor("#888")
        p.fillRect(0, 0, 5, 63, color)
        p.end()
        return pm

    def update_thumbnail(self, frame: int) -> None:
        if self.doc is None:
            return
        for b in self.doc.project.bookmarks:
            if b.frame == frame and b.id in self._items:
                self._items[b.id].setIcon(QIcon(self._thumb_pixmap(b)))

    def select(self, bid: str) -> None:
        it = self._items.get(bid)
        if it is not None:
            self.list.blockSignals(True)
            self.list.clearSelection()
            self.list.setCurrentItem(it)
            it.setSelected(True)
            self.list.blockSignals(False)
            self.list.scrollToItem(it)

    def _clicked(self, it: QListWidgetItem) -> None:
        b = self.doc.project.bookmark(it.data(Qt.UserRole)) if self.doc else None
        if b is not None:
            self.jumpRequested.emit(b.frame)

    def _export_video(self) -> None:
        sel = [it.data(Qt.UserRole) for it in self.list.selectedItems()]
        ids = sel if len(sel) > 1 else [b.id for b in self.filtered()]
        self.exportVideoRequested.emit(ids)

    def _edit_current(self) -> None:
        it = self.list.currentItem()
        if it is not None:
            self.editRequested.emit(it.data(Qt.UserRole))

    def _delete_selected(self) -> None:
        ids = [it.data(Qt.UserRole) for it in self.list.selectedItems()]
        if ids:
            self.deleteRequested.emit(ids)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Delete and self.list.hasFocus():
            self._delete_selected()
            return
        super().keyPressEvent(ev)
