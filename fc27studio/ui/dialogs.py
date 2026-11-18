"""Preferences, shortcut editor, shortcut reference card, go-to, about."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QKeySequenceEdit, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox,
                               QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout)

from .. import __version__
from ..core.video import format_timecode
from .settings import SHORTCUTS, Preferences, find_conflicts


class PreferencesDialog(QDialog):
    def __init__(self, parent, prefs: Preferences):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.prefs = prefs
        form = QFormLayout()
        self.cache = QSpinBox()
        self.cache.setRange(64, 16384)
        self.cache.setSingleStep(128)
        self.cache.setSuffix(" MB")
        self.cache.setValue(prefs.cache_mb)
        self.cache.setToolTip("Memory for decoded frames. More = faster backward stepping. "
                              "4K frames use ~25 MB each.")
        form.addRow("Frame cache", self.cache)
        self.threads = QSpinBox()
        self.threads.setRange(0, 64)
        self.threads.setSpecialValueText("Automatic")
        self.threads.setValue(prefs.decoder_threads)
        form.addRow("Decoder threads", self.threads)
        self.drop = QCheckBox("Drop frames to keep real-time speed")
        self.drop.setChecked(prefs.drop_frames)
        self.drop.setToolTip("Off = every frame is shown, playback may run slower than real time "
                             "on very high resolutions.")
        form.addRow("Playback", self.drop)
        self.autosave = QSpinBox()
        self.autosave.setRange(0, 3600)
        self.autosave.setSuffix(" s")
        self.autosave.setSpecialValueText("Off")
        self.autosave.setValue(prefs.autosave_seconds)
        form.addRow("Autosave every", self.autosave)
        self.hold = QDoubleSpinBox()
        self.hold.setRange(0.04, 3600)
        self.hold.setSuffix(" s")
        self.hold.setValue(prefs.default_hold_seconds)
        form.addRow("New drawings visible for", self.hold)
        self.step = QDoubleSpinBox()
        self.step.setRange(0.1, 600)
        self.step.setSuffix(" s")
        self.step.setValue(prefs.big_step_seconds)
        form.addRow("Shift+Arrow jump", self.step)
        self.jump = QDoubleSpinBox()
        self.jump.setRange(0.5, 600)
        self.jump.setSuffix(" s")
        self.jump.setValue(prefs.jump_seconds)
        form.addRow("Up / Down jump", self.jump)
        self.frames = QCheckBox("Show frame numbers in timecode")
        self.frames.setChecked(prefs.show_frame_numbers)
        form.addRow("Timecode", self.frames)
        self.thumbs = QCheckBox("Show thumbnails when hovering the timeline")
        self.thumbs.setChecked(prefs.timeline_thumbnails)
        form.addRow("Timeline", self.thumbs)
        self.clear_play = QCheckBox("Clear telestrator drawings when playback starts")
        self.clear_play.setChecked(prefs.telestrator_clear_on_play)
        form.addRow("Telestrator", self.clear_play)
        self.jpeg = QSpinBox()
        self.jpeg.setRange(50, 100)
        self.jpeg.setValue(prefs.jpeg_quality)
        form.addRow("JPG export quality", self.jpeg)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def apply(self) -> None:
        p = self.prefs
        p.cache_mb = self.cache.value()
        p.decoder_threads = self.threads.value()
        p.drop_frames = self.drop.isChecked()
        p.autosave_seconds = self.autosave.value()
        p.default_hold_seconds = self.hold.value()
        p.big_step_seconds = self.step.value()
        p.jump_seconds = self.jump.value()
        p.show_frame_numbers = self.frames.isChecked()
        p.timeline_thumbnails = self.thumbs.isChecked()
        p.telestrator_clear_on_play = self.clear_play.isChecked()
        p.jpeg_quality = self.jpeg.value()
        p.save()


class ShortcutsDialog(QDialog):
    def __init__(self, parent, mapping: dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(620, 620)
        self.mapping = dict(mapping)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter…")
        self.filter.textChanged.connect(self._filter)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Category", "Action", "Shortcut"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ids = list(SHORTCUTS)
        for aid in self.ids:
            cat, label, _d = SHORTCUTS[aid]
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(cat))
            self.table.setItem(r, 1, QTableWidgetItem(label))
            self.table.setItem(r, 2, QTableWidgetItem(self._display(self.mapping.get(aid, ""))))
        self.table.currentCellChanged.connect(lambda r, *_: self._load_row(r))
        self.editor = QKeySequenceEdit()
        self.editor.setMaximumSequenceLength(1)
        self.editor.editingFinished.connect(self._assign)
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self._set_current(""))
        default = QPushButton("Default")
        default.clicked.connect(lambda: self._set_current(SHORTCUTS[self.ids[self.table.currentRow()]][2])
                                if self.table.currentRow() >= 0 else None)
        reset_all = QPushButton("Reset all")
        reset_all.clicked.connect(self._reset_all)
        self.warning = QLabel("")
        self.warning.setObjectName("warning")
        self.warning.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Press new shortcut:"))
        row.addWidget(self.editor, 1)
        row.addWidget(clear)
        row.addWidget(default)
        row.addWidget(reset_all)
        lay.addLayout(row)
        lay.addWidget(self.warning)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._check()

    @staticmethod
    def _display(seq: str) -> str:
        return QKeySequence(seq).toString(QKeySequence.NativeText) if seq else ""

    def _filter(self, text: str) -> None:
        t = text.lower()
        for r in range(self.table.rowCount()):
            hay = " ".join(self.table.item(r, c).text().lower() for c in range(3))
            self.table.setRowHidden(r, t not in hay)

    def _load_row(self, r: int) -> None:
        if r >= 0:
            self.editor.setKeySequence(QKeySequence(self.mapping.get(self.ids[r], "")))

    def _assign(self) -> None:
        seq = self.editor.keySequence().toString(QKeySequence.PortableText)
        self._set_current(seq)

    def _set_current(self, seq: str) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        self.mapping[self.ids[r]] = seq
        self.table.item(r, 2).setText(self._display(seq))
        self.editor.setKeySequence(QKeySequence(seq))
        self._check()

    def _reset_all(self) -> None:
        for r, aid in enumerate(self.ids):
            self.mapping[aid] = SHORTCUTS[aid][2]
            self.table.item(r, 2).setText(self._display(self.mapping[aid]))
        self._check()

    def _check(self) -> bool:
        conflicts = find_conflicts(self.mapping)
        if conflicts:
            msgs = [f"{self._display(k)}: " + ", ".join(SHORTCUTS[a][1] for a in v)
                    for k, v in conflicts.items()]
            self.warning.setText("Conflicting shortcuts — " + "; ".join(msgs))
        else:
            self.warning.setText("")
        return not conflicts

    def _accept(self) -> None:
        if not self._check():
            QMessageBox.warning(self, "Shortcut conflict",
                                "Some shortcuts are assigned to more than one action. "
                                "Please resolve the conflicts first.")
            return
        self.accept()


def shortcut_card_html(mapping: dict[str, str]) -> str:
    cats: dict[str, list[tuple[str, str]]] = {}
    for aid, (cat, label, _d) in SHORTCUTS.items():
        seq = mapping.get(aid, "")
        if seq:
            cats.setdefault(cat, []).append((label, QKeySequence(seq).toString(QKeySequence.NativeText)))
    extra = [
        ("Drawing", [("Constrain angle / square shape / straight move", "Shift + drag"),
                     ("Add to selection", "Shift/Ctrl + click"),
                     ("Nudge selected drawing", "Alt + Arrow keys (Shift = 10 px)"),
                     ("Finish zone", "Double-click / Enter / click first point"),
                     ("Remove last zone point", "Backspace"),
                     ("Cancel / back to Select tool", "Esc"),
                     ("Edit text", "Double-click a text label"),
                     ("Cut out a player", "X, then drag around them (or click points + Enter)"),
                     ("Before/after split", "Drag the white divider")]),
        ("Tactics board", [("Edit a player token", "Double-click it"),
                           ("Move players", "Drag tokens (Shift = straight line)"),
                           ("Resize tokens", "Select, then drag a corner handle")]),
        ("Navigation", [("Step frames", "Mouse wheel over video"),
                        ("Zoom video", "Ctrl + mouse wheel"),
                        ("Pan video", "Middle-drag or Alt + drag"),
                        ("Zoom timeline", "Ctrl + wheel on timeline"),
                        ("Scroll zoomed timeline", "Wheel on timeline")]),
    ]
    parts = ["<html><body style='font-family:Segoe UI, sans-serif; font-size:10pt'>",
             "<h2 style='margin-bottom:2px'>FC27 Tactical Studio — Shortcuts</h2>"]
    groups = list(cats.items()) + extra
    parts.append("<table width='100%' cellspacing='12'><tr>")
    for i, (cat, rows) in enumerate(groups):
        if i and i % 3 == 0:
            parts.append("</tr><tr>")
        parts.append("<td valign='top' width='33%'>")
        parts.append(f"<h3 style='color:#4aa3ff; margin:0'>{html.escape(cat)}</h3><table cellspacing='2'>")
        for label, key in rows:
            parts.append(f"<tr><td><b><code>{html.escape(key)}</code></b>&nbsp;&nbsp;</td>"
                         f"<td>{html.escape(label)}</td></tr>")
        parts.append("</table></td>")
    parts.append("</tr></table></body></html>")
    return "".join(parts)


class ShortcutCard(QDialog):
    def __init__(self, parent, mapping: dict[str, str]):
        super().__init__(parent)
        self.setWindowTitle("Shortcut reference (F1)")
        self.resize(1000, 700)
        view = QTextBrowser()
        view.setHtml(shortcut_card_html(mapping))
        lay = QVBoxLayout(self)
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)


def parse_time_or_frame(text: str, fps: float) -> int | None:
    """Accept '1234' (frame), '12:34', '1:02:03', '12:34.15' (frames) or '75.5s'."""
    t = text.strip().lower()
    if not t:
        return None
    try:
        if t.endswith("s") and ":" not in t:
            return int(round(float(t[:-1]) * fps))
        if ":" not in t:
            return int(t)
        frac_frames = 0
        if "." in t.split(":")[-1]:
            main, ff = t.rsplit(".", 1)
            frac_frames = int(ff)
        else:
            main = t
        parts = [int(x) for x in main.split(":")]
        secs = 0
        for x in parts:
            secs = secs * 60 + x
        return int(round(secs * fps)) + frac_frames
    except ValueError:
        return None


class GotoDialog(QDialog):
    def __init__(self, parent, fps: float, current: int, frame_count: int):
        super().__init__(parent)
        self.setWindowTitle("Go to")
        self.fps = fps
        self.frame_count = frame_count
        self.edit = QLineEdit(format_timecode(current / fps, fps, frame=current))
        self.edit.selectAll()
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Time (mm:ss, h:mm:ss, mm:ss.ff), seconds (75.5s) or frame number:"))
        lay.addWidget(self.edit)
        self.err = QLabel("")
        self.err.setObjectName("warning")
        lay.addWidget(self.err)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.result_frame = current

    def _ok(self) -> None:
        f = parse_time_or_frame(self.edit.text(), self.fps)
        if f is None:
            self.err.setText("Could not understand that time.")
            return
        self.result_frame = max(0, min(self.frame_count - 1, f))
        self.accept()


def about_text() -> str:
    return (f"<h3>FC27 Tactical Studio {__version__}</h3>"
            "<p>Tactical analysis workstation for EA SPORTS FC 27 match recordings.</p>"
            "<p>Built with Qt for Python (PySide6, LGPLv3) and FFmpeg via PyAV (LGPL). "
            "See docs/LICENSING.md for details.</p>"
            "<p>Not affiliated with or endorsed by Electronic Arts.</p>")
