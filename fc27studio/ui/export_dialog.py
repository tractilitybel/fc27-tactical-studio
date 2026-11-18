"""Export video dialog + background export thread."""
from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton,
                               QRadioButton, QSpinBox, QVBoxLayout, QWidget)

from ..core.video import format_timecode
from .export import safe_name
from .video_export import Cancelled, ExportOptions, Segment, VideoExporter, bookmark_segments


def export_log():
    """Append-only export log in %APPDATA%/FC27TacticalStudio/logs/export.log."""
    import time as _t
    from .settings import app_data_dir
    path = os.path.join(app_data_dir(), "logs", "export.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def log(msg: str) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{_t.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
        except OSError:
            pass
    return log


class ExportWorker(QObject):
    progressed = Signal(float)
    finished = Signal(list, str)   # written paths, error message ("" = ok, "cancelled")

    def __init__(self, jobs, project, video_path, index_data, mixer, homography_at=None):
        super().__init__()
        self.jobs = jobs           # list of (ExportOptions, [Segment])
        self.project, self.video_path, self.index_data, self.mixer = project, video_path, index_data, mixer
        self.homography_at = homography_at
        self.cancel = False

    @Slot()
    def run(self):
        written = []
        log = export_log()
        log(f"start: {len(self.jobs)} job(s) from {self.video_path}")
        try:
            n = len(self.jobs)
            for i, (opts, segs) in enumerate(self.jobs):
                def prog(f, _t, i=i):
                    if self.cancel:
                        raise Cancelled()
                    self.progressed.emit((i + f) / n)
                written.append(VideoExporter(self.project, self.video_path, self.index_data, opts,
                                             self.mixer, prog, self.homography_at).run(segs))
            log(f"done: {written}")
            self._finish(written, "")
        except Cancelled:
            log("cancelled")
            self._finish(written, "cancelled")
        except Exception as exc:
            import traceback
            log("FAILED\n" + traceback.format_exc())
            self._finish(written, f"{type(exc).__name__}: {exc}")

    def _finish(self, written, err):
        # Hand this object back to the main thread before it is released there
        # (deleting a QObject from a foreign thread can crash on Windows).
        from PySide6.QtWidgets import QApplication
        self.moveToThread(QApplication.instance().thread())
        self.finished.emit(written, err)


class ExportVideoDialog(QDialog):
    def __init__(self, win, reel_bookmarks: Optional[list] = None):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Export video")
        self.setMinimumWidth(520)
        pl = win.player
        self.fps = pl.fps
        self.frame_count = pl.frame_count
        lay = QVBoxLayout(self)

        # ---- what
        what = QGroupBox("What to export")
        wl = QVBoxLayout(what)
        self.mode_section = QRadioButton("A section of the video")
        self.mode_reel = QRadioButton("Highlights reel from bookmarks")
        grp = QButtonGroup(self)
        grp.addButton(self.mode_section)
        grp.addButton(self.mode_reel)
        wl.addWidget(self.mode_section)
        sec = QWidget()
        sl = QFormLayout(sec)
        sl.setContentsMargins(24, 0, 0, 0)
        self.range = QComboBox()
        self.range.addItem("Whole video", "all")
        if pl.loop_a is not None and pl.loop_b is not None:
            self.range.addItem(f"A–B loop ({self._tc(pl.loop_a)} – {self._tc(pl.loop_b)})", "loop")
        self.range.addItem("Custom", "custom")
        self.from_s = QSpinBox()
        self.to_s = QSpinBox()
        for s in (self.from_s, self.to_s):
            s.setRange(0, max(0, self.frame_count - 1))
        self.from_s.setValue(pl.current)
        self.to_s.setValue(min(self.frame_count - 1, pl.current + int(10 * self.fps)))
        cr = QHBoxLayout()
        cr.addWidget(QLabel("from frame"))
        cr.addWidget(self.from_s)
        cr.addWidget(QLabel("to"))
        cr.addWidget(self.to_s)
        self.custom_row = QWidget()
        self.custom_row.setLayout(cr)
        sl.addRow("Range", self.range)
        sl.addRow("", self.custom_row)
        wl.addWidget(sec)
        wl.addWidget(self.mode_reel)
        reel = QWidget()
        rl = QFormLayout(reel)
        rl.setContentsMargins(24, 0, 0, 0)
        self.bookmarks = reel_bookmarks if reel_bookmarks is not None else win.bookmarks.filtered()
        self.reel_info = QLabel(f"{len(self.bookmarks)} bookmark(s) — the ones shown in the Incidents list "
                                "(use its search and filters to choose).")
        self.reel_info.setWordWrap(True)
        rl.addRow(self.reel_info)
        pad = QHBoxLayout()
        self.before = QDoubleSpinBox()
        self.after = QDoubleSpinBox()
        for s, v in ((self.before, 3.0), (self.after, 5.0)):
            s.setRange(0, 60)
            s.setSuffix(" s")
            s.setValue(v)
        pad.addWidget(QLabel("before"))
        pad.addWidget(self.before)
        pad.addWidget(QLabel("after"))
        pad.addWidget(self.after)
        rl.addRow("Around each bookmark", pad)
        self.titles = QCheckBox("Title card before each incident")
        self.titles.setChecked(True)
        rl.addRow(self.titles)
        self.separate = QCheckBox("One file per bookmark (instead of one video)")
        rl.addRow(self.separate)
        wl.addWidget(reel)
        lay.addWidget(what)

        # ---- options
        opt = QGroupBox("Options")
        ol = QFormLayout(opt)
        self.res = QComboBox()
        h = win.player.source.info.height if win.player.source else 1080
        self.res.addItem(f"Original ({h}p)", None)
        for hh in (1080, 720, 480):
            if hh < h:
                self.res.addItem(f"{hh}p", hh)
        self.quality = QComboBox()
        for label, crf in (("High", 18), ("Standard", 21), ("Small file", 26)):
            self.quality.addItem(label, crf)
        self.quality.setCurrentIndex(1)
        ol.addRow("Resolution", self.res)
        ol.addRow("Quality", self.quality)
        self.cb_draw = QCheckBox("Drawings and animations")
        self.cb_freeze = QCheckBox("Freeze frames (pause the video)")
        self.cb_sound = QCheckBox("Game sound")
        self.cb_comm = QCheckBox("Voice commentary")
        for cb in (self.cb_draw, self.cb_freeze, self.cb_sound, self.cb_comm):
            cb.setChecked(True)
            ol.addRow(cb)
        has_audio = getattr(win, "audio", None) is not None and win.audio.has_audio
        self.cb_sound.setEnabled(has_audio)
        self.cb_sound.setChecked(has_audio)
        has_comm = bool(win.doc.project.commentary)
        self.cb_comm.setEnabled(has_comm)
        self.cb_comm.setChecked(has_comm)
        lay.addWidget(opt)

        # ---- where
        where = QHBoxLayout()
        self.path = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        where.addWidget(QLabel("Save to"))
        where.addWidget(self.path, 1)
        where.addWidget(browse)
        lay.addLayout(where)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.mode_section.toggled.connect(lambda on: (sec.setEnabled(on), reel.setEnabled(not on),
                                                      self._default_path()))
        self.range.currentIndexChanged.connect(lambda _i: self.custom_row.setVisible(
            self.range.currentData() == "custom"))
        self.separate.toggled.connect(lambda _v: self._default_path())
        if reel_bookmarks is not None:
            self.mode_reel.setChecked(True)
            sec.setEnabled(False)
        else:
            self.mode_section.setChecked(True)
            reel.setEnabled(False)
            if pl.loop_a is not None and pl.loop_b is not None:
                self.range.setCurrentIndex(1)
        self.mode_reel.setEnabled(bool(self.bookmarks))
        self.custom_row.setVisible(self.range.currentData() == "custom")
        self._default_path()

    def _tc(self, f: int) -> str:
        return format_timecode(f / self.fps, self.fps, show_frames=False)

    def _default_path(self) -> None:
        base = self.win._last_dir()
        name = safe_name(self.win.doc.project.name, "match")
        if self.mode_reel.isChecked() and self.separate.isChecked():
            self.path.setText(os.path.join(base, f"{name} clips"))
        elif self.mode_reel.isChecked():
            self.path.setText(os.path.join(base, f"{name} highlights.mp4"))
        else:
            self.path.setText(os.path.join(base, f"{name} analysis.mp4"))

    def _browse(self) -> None:
        if self.mode_reel.isChecked() and self.separate.isChecked():
            d = QFileDialog.getExistingDirectory(self, "Folder for the clips", os.path.dirname(self.path.text()))
            if d:
                self.path.setText(d)
        else:
            p, _ = QFileDialog.getSaveFileName(self, "Save video", self.path.text(), "MP4 video (*.mp4)")
            if p:
                self.path.setText(p if p.lower().endswith(".mp4") else p + ".mp4")

    def jobs(self) -> list:
        def opts(path):
            return ExportOptions(path, height=self.res.currentData(), drawings=self.cb_draw.isChecked(),
                                 freezes=self.cb_freeze.isChecked(),
                                 sound=self.cb_sound.isChecked() and self.cb_sound.isEnabled(),
                                 commentary=self.cb_comm.isChecked() and self.cb_comm.isEnabled(),
                                 crf=self.quality.currentData(),
                                 audio_track=getattr(self.win.audio, "track", 0))
        path = self.path.text().strip()
        if self.mode_section.isChecked():
            r = self.range.currentData()
            pl = self.win.player
            if r == "loop":
                s, e = pl.loop_a, pl.loop_b
            elif r == "custom":
                s, e = sorted((self.from_s.value(), self.to_s.value()))
            else:
                s, e = 0, self.frame_count - 1
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            return [(opts(path), [Segment(s, e)])]
        cat = self.win.doc.project
        segs = bookmark_segments(self.bookmarks, self.fps, self.frame_count, self.before.value(),
                                 self.after.value(), self.titles.isChecked())
        if self.separate.isChecked():
            os.makedirs(path, exist_ok=True)
            jobs = []
            for i, (b, seg) in enumerate(zip(sorted(self.bookmarks, key=lambda b: b.frame), segs)):
                tc = format_timecode(b.frame / self.fps, self.fps, show_frames=False).replace(":", "-")
                fn = safe_name(f"{i + 1:02d} {tc} {b.category} {b.title}") + ".mp4"
                jobs.append((opts(os.path.join(path, fn)), [seg]))
            return jobs
        if not path.lower().endswith(".mp4"):
            path += ".mp4"
        return [(opts(path), segs)]

    def _accept(self) -> None:
        if not self.path.text().strip():
            return
        d = os.path.dirname(self.path.text().strip()) if not (self.mode_reel.isChecked() and self.separate.isChecked()) \
            else self.path.text().strip()
        if d and not os.path.isdir(d) and not (self.mode_reel.isChecked() and self.separate.isChecked()):
            QMessageBox.warning(self, "Export", "That folder does not exist.")
            return
        self.accept()


class _MainThreadBridge(QObject):
    """Receives the worker's signals on the main (GUI) thread.

    Signals connected to plain Python functions run in the *emitting* thread
    — the export thread — and touching widgets from there crashes on Windows.
    Connecting to slots of an object that lives on the main thread makes Qt
    queue the calls to the GUI thread instead."""

    def __init__(self, on_progress, on_done):
        super().__init__()
        self._p, self._d = on_progress, on_done

    @Slot(float)
    def progress(self, f):
        self._p(f)

    @Slot(list, str)
    def done(self, paths, err):
        self._d(paths, err)


def run_export(win, jobs) -> Optional[list]:
    """Run export jobs in a background thread with a progress dialog."""
    pl = win.player
    mixer = win.commentary_mixer() if hasattr(win, "commentary_mixer") else None
    homog = win.calib.snapshot() if hasattr(win, "calib") else None
    worker = ExportWorker(jobs, win.doc.project, pl.source.path, pl.index_data, mixer, homog)
    thread = QThread()
    worker.moveToThread(thread)
    dlg = QProgressDialog("Rendering video…", "Cancel", 0, 1000, win)
    dlg.setWindowTitle("Export video")
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    result = {}

    def on_done(paths, err):
        result["paths"], result["err"] = paths, err
        thread.quit()

    import time as _t
    t0 = _t.perf_counter()

    def on_progress(f):
        dlg.setValue(int(f * 1000))
        el = _t.perf_counter() - t0
        if f > 0.02 and el > 2:
            left = el * (1 - f) / f
            mins, secs = divmod(int(left + 0.5), 60)
            dlg.setLabelText(f"Rendering video… {f * 100:.0f}%  —  about "
                             f"{f'{mins} min ' if mins else ''}{secs} s left")

    bridge = _MainThreadBridge(on_progress, on_done)
    worker.progressed.connect(bridge.progress, Qt.QueuedConnection)
    worker.finished.connect(bridge.done, Qt.QueuedConnection)
    dlg.canceled.connect(lambda: setattr(worker, "cancel", True))
    thread.started.connect(worker.run)
    thread.start()
    from PySide6.QtWidgets import QApplication
    while thread.isRunning() or "err" not in result:
        QApplication.processEvents()
        thread.wait(20)
        if "err" in result and not thread.isRunning():
            break
    thread.wait()
    dlg.close()
    worker.deleteLater()
    err = result.get("err", "")
    paths = result.get("paths", [])
    if err == "cancelled":
        win.statusBar().showMessage("Export cancelled.", 4000)
        return None
    if err:
        QMessageBox.critical(win, "Export failed", err)
        return None
    if getattr(win, "_quiet_exports", False):
        return paths
    box = QMessageBox(win)
    box.setWindowTitle("Export finished")
    box.setText(f"Saved {len(paths)} video{'s' if len(paths) != 1 else ''}:\n" +
                "\n".join(os.path.basename(p) for p in paths[:8]) + ("\n…" if len(paths) > 8 else ""))
    open_btn = box.addButton("Open folder", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Ok)
    box.exec()
    if box.clickedButton() is open_btn and paths:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(paths[0])))
    return paths
