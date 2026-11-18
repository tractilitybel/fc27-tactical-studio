"""Main application window."""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from PySide6.QtCore import QByteArray, QSettings, QSize, Qt, QThread, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QGuiApplication, QImage
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QProgressDialog, QPushButton, QScrollArea,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget, QListWidgetItem)

from .. import APP_NAME, __version__
from ..core import storage
from ..core.document import Document
from ..core.model import Bookmark, Kind, Project
from ..core.video import IndexData, VideoError, VideoSource, format_timecode, load_or_build_index
from .bookmarks import BookmarkDialog, BookmarkPanel, CategoriesDialog
from .canvas import Canvas
from .dialogs import (GotoDialog, PreferencesDialog, ShortcutCard, ShortcutsDialog, about_text)
from .export import export_bookmark_images, export_bookmarks_csv, export_frame, compose, safe_name
from .panels import PropertiesPanel, TelestratorBar, ToolPalette
from .playback import Player, ThumbnailWorker
from .settings import (app_data_dir, qsettings, SHORTCUTS, Preferences, load_shortcuts, recovery_dir,
                       save_shortcuts)
from .timeline import Marker, Timeline, Transport

VIDEO_FILTER = ("Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.flv *.wmv);;"
                "All files (*)")
PROJECT_FILTER = f"FC27 Studio project (*{storage.EXTENSION})"
WORKSPACES = ["review", "tactics", "present"]


class MatchInfoPanel(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        t = QLabel("Match")
        t.setObjectName("panelTitle")
        lay.addWidget(t)
        form = QFormLayout()
        self.name = QLineEdit()
        self.home = QLineEdit()
        self.away = QLineEdit()
        form.addRow("Project name", self.name)
        form.addRow("Home team", self.home)
        form.addRow("Away team", self.away)
        lay.addLayout(form)
        lay.addWidget(QLabel("Squad / players (one per line) — used for bookmark suggestions"))
        self.players = QPlainTextEdit()
        lay.addWidget(self.players, 1)
        lay.addWidget(QLabel("Match notes"))
        self.notes = QPlainTextEdit()
        lay.addWidget(self.notes, 1)
        apply = QPushButton("Apply")
        apply.clicked.connect(self.apply)
        lay.addWidget(apply)
        self.info = QLabel("")
        self.info.setObjectName("hint")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

    def load(self) -> None:
        p = self.win.doc.project
        self.name.setText(p.name)
        self.home.setText(p.home_team)
        self.away.setText(p.away_team)
        self.players.setPlainText("\n".join(p.players))
        self.notes.setPlainText(p.notes)
        src = self.win.player.source
        if src is not None:
            i = src.info
            self.info.setText(f"Video: {os.path.basename(i.path)}\n{i.width}×{i.height}, {i.fps:.3f} fps, "
                              f"{i.codec}, {i.frame_count} frames, "
                              f"{format_timecode(i.duration, show_frames=False)}")
        else:
            self.info.setText("No video loaded.")

    def apply(self) -> None:
        p = self.win.doc.project
        players = [l.strip() for l in self.players.toPlainText().splitlines() if l.strip()]
        changes = {}
        for k, v in (("name", self.name.text().strip() or "Untitled"), ("home_team", self.home.text().strip()),
                     ("away_team", self.away.text().strip()), ("players", players),
                     ("notes", self.notes.toPlainText().strip())):
            if getattr(p, k) != v:
                changes[k] = v
        if changes:
            self.win.doc.set_project_fields("Edit match info", **changes)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.resize(1500, 900)
        self.prefs = Preferences.load()
        self.shortcut_map = load_shortcuts()
        self.recovery = storage.Recovery(recovery_dir())
        self.doc = Document()
        self.player = Player(self)
        self.player.drop_frames = self.prefs.drop_frames
        self.workspace = "review"
        self.presenting = False
        self._export_src: Optional[VideoSource] = None
        self._thumb_thread: Optional[QThread] = None
        self._thumb_worker: Optional[ThumbnailWorker] = None
        self._bm_thumbs: dict[int, QImage] = {}
        self._bm_thumb_pending: set[int] = set()
        self._last_category = "Other"
        self._last_autosave = 0.0
        self._build_ui()
        self._build_actions()
        self._build_menus()
        self._connect()
        self._attach_document(self.doc)
        self.set_workspace("review")
        self._restore_geometry()
        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self._autosave)
        self.autosave_timer.start(5000)

    # ================================================================ UI
    def _build_ui(self) -> None:
        self.canvas = Canvas()
        self.timeline = Timeline()
        self.transport = Transport()
        self.palette = ToolPalette(self)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        # Header: workspace switcher
        self.header = QWidget()
        self.header.setObjectName("header")
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(10, 4, 10, 4)
        brand = QLabel("FC27 <b>Tactical Studio</b>")
        brand.setObjectName("brand")
        hl.addWidget(brand)
        hl.addSpacing(24)
        self.ws_group = QButtonGroup(self)
        self.ws_buttons = {}
        for i, (ws, label) in enumerate((("review", "Review"), ("tactics", "Tactics"), ("present", "Present"))):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("workspaceButton")
            b.clicked.connect(lambda _c=False, w=ws: self.set_workspace(w))
            self.ws_group.addButton(b, i)
            self.ws_buttons[ws] = b
            hl.addWidget(b)
        hl.addStretch(1)
        self.project_label = QLabel("")
        self.project_label.setObjectName("projectLabel")
        hl.addWidget(self.project_label)
        root.addWidget(self.header)

        self.splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QHBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        ll.addWidget(self.palette)
        video_area = QWidget()
        vl = QVBoxLayout(video_area)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        vl.addWidget(self.canvas, 1)
        vl.addWidget(self.timeline)
        vl.addWidget(self.transport)
        ll.addWidget(video_area, 1)
        self.splitter.addWidget(left)

        self.side = QTabWidget()
        self.side.setMinimumWidth(300)
        self.bookmarks = BookmarkPanel()
        self.bookmarks.thumb_lookup = lambda f: self._bm_thumbs.get(f)
        self.properties = PropertiesPanel(self.canvas)
        self.properties.hold_s.setValue(self.prefs.default_hold_seconds)
        self.props_scroll = QScrollArea()
        self.props_scroll.setWidget(self.properties)
        self.props_scroll.setWidgetResizable(True)
        self.props_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.telestrator = TelestratorBar(self.canvas)
        self.telestrator.clear_on_play.setChecked(self.prefs.telestrator_clear_on_play)
        self.match_info = MatchInfoPanel(self)
        self.splitter.addWidget(self.side)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([1150, 350])
        root.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        sb = self.statusBar()
        self.status_video = QLabel("")
        self.status_zoom = QLabel("")
        self.status_save = QLabel("")
        sb.addPermanentWidget(self.status_save)
        sb.addPermanentWidget(self.status_video)
        sb.addPermanentWidget(self.status_zoom)

    def _act(self, aid: str, slot, checkable: bool = False, text: Optional[str] = None) -> QAction:
        label = text or SHORTCUTS[aid][1]
        a = QAction(label, self)
        a.setCheckable(checkable)
        seq = self.shortcut_map.get(aid, "")
        if seq:
            a.setShortcut(QKeySequence(seq))
        a.setShortcutContext(Qt.WindowShortcut)
        # Ignore triggered(bool) so it never leaks into slot default arguments.
        a.triggered.connect(lambda _checked=False, fn=slot: fn())
        self.addAction(a)
        self.actions[aid] = a
        return a

    def _build_actions(self) -> None:
        self.actions: dict[str, QAction] = {}
        A = self._act
        A("file.new", self.new_project)
        A("file.open", self.open_project_dialog)
        A("file.open_video", self.open_video_dialog)
        A("file.save", self.save)
        A("file.save_as", self.save_as)
        A("file.export_frame", self.export_frame_dialog)
        A("file.copy_frame", self.copy_frame)
        A("file.quit", self.close)
        A("edit.undo", lambda: self.doc.undo.undo())
        A("edit.redo", lambda: self.doc.undo.redo())
        A("edit.cut", self.cut)
        A("edit.copy", lambda: self.canvas.copy_selection())
        A("edit.paste", lambda: self.canvas.paste())
        A("edit.duplicate", lambda: self.canvas.duplicate())
        A("edit.delete", self.delete_selection)
        A("edit.select_all", lambda: self.canvas.select_all())
        A("edit.group", lambda: self.doc.group(self._sel_doc_ids()))
        A("edit.ungroup", lambda: self.doc.ungroup(self._sel_doc_ids()))
        for m in ("front", "back", "forward", "backward"):
            A(f"edit.{m}", lambda _c=False, mode=m: self.doc.reorder(self._sel_doc_ids(), mode))
        A("edit.prefs", self.preferences)
        A("play.toggle", self.toggle_play)
        A("play.reverse", lambda: self.player.jkl("j"))
        A("play.pause", lambda: self.player.jkl("k"))
        A("play.forward", lambda: self.player.jkl("l"))
        A("play.next_frame", lambda: self.player.step(1))
        A("play.prev_frame", lambda: self.player.step(-1))
        A("play.big_forward", lambda: self.player.seek(self.player.current + self._big_step()))
        A("play.big_back", lambda: self.player.seek(self.player.current - self._big_step()))
        A("play.start", lambda: self.player.seek(0))
        A("play.end", lambda: self.player.seek(self.player.frame_count - 1))
        A("play.slower", self.player.slower)
        A("play.faster", self.player.faster)
        A("play.normal_speed", lambda: self.player.set_speed(1.0))
        A("play.set_a", self.set_loop_a)
        A("play.set_b", self.set_loop_b)
        A("play.toggle_loop", self.toggle_loop, checkable=True)
        A("play.clear_loop", self.clear_loop)
        A("play.goto", self.goto_dialog)
        A("bm.add", self.quick_bookmark)
        A("bm.add_detailed", self.detailed_bookmark)
        A("bm.next", lambda: self.jump_bookmark(1))
        A("bm.prev", lambda: self.jump_bookmark(-1))
        for tool in ["select"] + Kind.ALL:
            A(f"tool.{tool}", lambda _c=False, t=tool: self.choose_tool(t))
        A("tool.clear_telestrator", self.canvas.clear_telestrator)
        A("view.review", lambda: self.set_workspace("review"))
        A("view.tactics", lambda: self.set_workspace("tactics"))
        A("view.present", lambda: self.set_workspace("present"))
        A("view.presentation", self.toggle_presentation, checkable=True)
        A("view.fullscreen", self.toggle_fullscreen, checkable=True)
        A("view.zoom_fit", lambda: self.canvas.set_zoom(None))
        A("view.zoom_100", lambda: self.canvas.set_zoom(1.0))
        A("view.zoom_in", lambda: self.canvas.zoom_by(1.25))
        A("view.zoom_out", lambda: self.canvas.zoom_by(0.8))
        A("view.hide_drawings", self.toggle_drawings, checkable=True)
        self.actions["view.hide_drawings"].setChecked(True)
        self.actions["view.hide_drawings"].setText("Show drawings")
        A("help.card", self.shortcut_card)
        self.act_exit_present = QAction("Exit presentation", self)
        self.act_exit_present.setShortcut(QKeySequence(Qt.Key_Escape))
        self.act_exit_present.triggered.connect(lambda: self.toggle_presentation(False))
        self.act_exit_present.setEnabled(False)
        self.addAction(self.act_exit_present)
        self.palette.set_tooltips(self.shortcut_map)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        mb.clear()
        a = self.actions
        f = mb.addMenu("&File")
        for k in ("file.new", "file.open"):
            f.addAction(a[k])
        self.recent_menu = f.addMenu("Open recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        f.addAction(a["file.open_video"])
        self.act_relink = f.addAction("Relink video…", self.relink_video_dialog)
        f.addSeparator()
        f.addAction(a["file.save"])
        f.addAction(a["file.save_as"])
        f.addSeparator()
        ex = f.addMenu("Export")
        ex.addAction(a["file.export_frame"])
        ex.addAction("Export clean frame (no drawings)…", lambda: self.export_frame_dialog(False))
        ex.addAction(a["file.copy_frame"])
        ex.addSeparator()
        ex.addAction("Export bookmark images…", self.export_bookmark_images_dialog)
        ex.addAction("Export bookmark list (CSV)…", self.export_csv_dialog)
        f.addSeparator()
        f.addAction(a["file.quit"])

        e = mb.addMenu("&Edit")
        e.addAction(a["edit.undo"])
        e.addAction(a["edit.redo"])
        e.addSeparator()
        for k in ("edit.cut", "edit.copy", "edit.paste", "edit.duplicate", "edit.delete", "edit.select_all"):
            e.addAction(a[k])
        e.addSeparator()
        arr = e.addMenu("Arrange")
        for k in ("edit.front", "edit.forward", "edit.backward", "edit.back", "edit.group", "edit.ungroup"):
            arr.addAction(a[k])
        e.addSeparator()
        e.addAction("Bookmark categories…", self.edit_categories)
        e.addAction("Keyboard shortcuts…", self.edit_shortcuts)
        e.addAction(a["edit.prefs"])

        v = mb.addMenu("&View")
        for k in ("view.review", "view.tactics", "view.present"):
            v.addAction(a[k])
        v.addSeparator()
        for k in ("view.presentation", "view.fullscreen"):
            v.addAction(a[k])
        v.addSeparator()
        for k in ("view.zoom_fit", "view.zoom_100", "view.zoom_in", "view.zoom_out", "view.hide_drawings"):
            v.addAction(a[k])
        v.addSeparator()
        v.addAction("Zoom timeline in", lambda: self.timeline.zoom(2.0))
        v.addAction("Zoom timeline out", lambda: self.timeline.zoom(0.5))
        v.addAction("Show whole timeline", self.timeline.reset_zoom)

        p = mb.addMenu("&Playback")
        for k in ("play.toggle", "play.reverse", "play.pause", "play.forward"):
            p.addAction(a[k])
        p.addSeparator()
        for k in ("play.prev_frame", "play.next_frame", "play.big_back", "play.big_forward",
                  "play.start", "play.end", "play.goto"):
            p.addAction(a[k])
        p.addSeparator()
        for k in ("play.slower", "play.faster", "play.normal_speed"):
            p.addAction(a[k])
        p.addSeparator()
        for k in ("play.set_a", "play.set_b", "play.toggle_loop", "play.clear_loop"):
            p.addAction(a[k])

        b = mb.addMenu("&Bookmarks")
        for k in ("bm.add", "bm.add_detailed", "bm.prev", "bm.next"):
            b.addAction(a[k])
        b.addSeparator()
        b.addAction("Edit categories…", self.edit_categories)

        t = mb.addMenu("&Tools")
        for tool in ["select"] + Kind.ALL:
            t.addAction(a[f"tool.{tool}"])
        t.addSeparator()
        t.addAction(a["tool.clear_telestrator"])

        h = mb.addMenu("&Help")
        h.addAction(a["help.card"])
        h.addAction("User guide", self.user_guide)
        h.addAction("About", lambda: QMessageBox.about(self, "About", about_text()))

    def _connect(self) -> None:
        pl = self.player
        pl.imageReady.connect(self._on_image)
        pl.frameChanged.connect(self._on_frame_changed)
        pl.stateChanged.connect(self._on_state)
        pl.error.connect(lambda m: self.statusBar().showMessage(m, 5000))
        tl = self.timeline
        tl.seekRequested.connect(self._timeline_seek)
        tl.hoverFrame.connect(self._request_hover_thumb)
        tl.markerClicked.connect(self.bookmarks.select)
        tl.markerDoubleClicked.connect(self.edit_bookmark)
        tr = self.transport
        tr.btn_start.clicked.connect(lambda: pl.seek(0))
        tr.btn_end.clicked.connect(lambda: pl.seek(pl.frame_count - 1))
        tr.btn_back.clicked.connect(lambda: pl.step(-1))
        tr.btn_fwd.clicked.connect(lambda: pl.step(1))
        tr.btn_play.clicked.connect(self.toggle_play)
        tr.btn_rev.clicked.connect(lambda: pl.play(-1))
        tr.btn_prev_bm.clicked.connect(lambda: self.jump_bookmark(-1))
        tr.btn_next_bm.clicked.connect(lambda: self.jump_bookmark(1))
        tr.btn_a.clicked.connect(self.set_loop_a)
        tr.btn_b.clicked.connect(self.set_loop_b)
        tr.btn_loop.clicked.connect(self.toggle_loop)
        tr.frameEntered.connect(lambda f: pl.seek(f))
        tr.speedChosen.connect(pl.set_speed)
        cv = self.canvas
        cv.stepRequested.connect(lambda d: pl.step(d))
        cv.zoomChanged.connect(lambda s: self.status_zoom.setText(f"{s * 100:.0f}%"))
        cv.toolChanged.connect(self.palette.set_tool)
        cv.annotationsEdited.connect(self._refresh_ranges)
        self.palette.toolSelected.connect(self.choose_tool)
        bp = self.bookmarks
        bp.jumpRequested.connect(lambda f: (pl.pause(), pl.seek(f)))
        bp.editRequested.connect(self.edit_bookmark)
        bp.addRequested.connect(self.detailed_bookmark)
        bp.deleteRequested.connect(self.delete_bookmarks)
        bp.thumbnailWanted.connect(self._request_bm_thumb)
        bp.categoriesRequested.connect(self.edit_categories)
        te = self.telestrator
        te.clearRequested.connect(cv.clear_telestrator)
        te.keepToggled.connect(lambda v: setattr(cv, "persist_telestrator", v))
        te.presentationRequested.connect(lambda: self.toggle_presentation(True))
        te.playlist.itemClicked.connect(lambda it: (pl.pause(), pl.seek(it.data(Qt.UserRole))))

    # ========================================================== document
    def _attach_document(self, doc: Document) -> None:
        self.doc = doc
        doc.listeners.append(self._on_doc_changed)
        doc.undo.listeners.append(self._on_undo_changed)
        self.canvas.set_document(doc)
        self.bookmarks.set_document(doc, self.player.fps)
        self.match_info.load()
        self._on_doc_changed("bookmarks")
        self._on_doc_changed("annotations")
        self._on_undo_changed()

    def _on_doc_changed(self, what: str) -> None:
        if what in ("bookmarks", "project"):
            self.bookmarks.refresh_filters()
            self.bookmarks.refresh()
            self._refresh_markers()
            self._refresh_playlist()
        if what in ("annotations", "project"):
            self.canvas.prune_selection()
            self.canvas.update()
            self.properties.sync()
            self._refresh_ranges()
        if what == "project":
            self.match_info.load()
        self._update_title()

    def _on_undo_changed(self) -> None:
        u = self.doc.undo
        a = self.actions
        a["edit.undo"].setEnabled(u.can_undo())
        a["edit.redo"].setEnabled(u.can_redo())
        a["edit.undo"].setText(f"Undo {u.undo_label()}".strip())
        a["edit.redo"].setText(f"Redo {u.redo_label()}".strip())
        self._update_title()

    def _update_title(self) -> None:
        name = os.path.basename(self.doc.path) if self.doc.path else "Untitled"
        dirty = "• " if self.doc.dirty else ""
        self.setWindowTitle(f"{dirty}{name} — {APP_NAME}")
        p = self.doc.project
        teams = f"{p.home_team} vs {p.away_team}" if p.home_team or p.away_team else ""
        self.project_label.setText("  ·  ".join(x for x in (p.name, teams) if x))

    def _refresh_markers(self) -> None:
        p = self.doc.project
        self.timeline.set_markers([Marker(b.id, b.frame, b.end_frame, p.category_color(b.category),
                                          f"{b.category}: {b.title}") for b in p.bookmarks])

    def _refresh_ranges(self) -> None:
        sel = self.canvas.selection
        anns = self.doc.project.annotations
        shown = [a for a in anns if a.id in sel] if sel else anns
        self.timeline.set_ranges([(a.start_frame, a.end_frame, a.color) for a in shown[:400]])

    def _refresh_playlist(self) -> None:
        pl = self.telestrator.playlist
        pl.clear()
        fps = self.player.fps
        for b in self.doc.project.sorted_bookmarks():
            it = QListWidgetItem(f"{format_timecode(b.frame / fps, fps, frame=b.frame)}  {b.title or b.category}")
            it.setData(Qt.UserRole, b.frame)
            pl.addItem(it)

    def _sel_doc_ids(self) -> list[str]:
        return [a.id for a in self.canvas.selected() if not self.canvas.is_temp(a.id)]

    # ========================================================= playback
    def _on_image(self, index: int, img: QImage) -> None:
        self.canvas.set_image(img)
        self.canvas.set_frame(index)
        if self.workspace == "tactics" and not self.player.playing:
            self.properties.layers.refresh()

    def _on_frame_changed(self, index: int) -> None:
        self.timeline.set_current(index)
        self._update_transport()

    def _on_state(self) -> None:
        pl = self.player
        if pl.playing and self.workspace == "present" and self.telestrator.clear_on_play.isChecked():
            self.canvas.clear_telestrator()
        self.timeline.set_loop(pl.loop_a, pl.loop_b, pl.loop_enabled)
        self.actions["play.toggle_loop"].setChecked(pl.loop_enabled)
        self._update_transport()

    def _update_transport(self) -> None:
        pl = self.player
        self.transport.update_state(pl.current, pl.frame_count, pl.fps, pl.playing, pl.speed,
                                    pl.loop_enabled, self.prefs.show_frame_numbers)

    def _timeline_seek(self, frame: int) -> None:
        self.player.pause()
        self.player.seek(frame)

    def toggle_play(self) -> None:
        self.player.toggle()

    def _big_step(self) -> int:
        return max(1, int(round(self.prefs.big_step_seconds * self.player.fps)))

    def set_loop_a(self) -> None:
        pl = self.player
        b = pl.loop_b if pl.loop_b is not None and pl.loop_b > pl.current else None
        pl.set_loop(pl.current, b, enabled=b is not None)
        self._store_loop()

    def set_loop_b(self) -> None:
        pl = self.player
        a = pl.loop_a if pl.loop_a is not None else max(0, pl.current - int(pl.fps * 5))
        pl.set_loop(a, pl.current, enabled=True)
        self._store_loop()

    def toggle_loop(self) -> None:
        pl = self.player
        if pl.loop_a is None or pl.loop_b is None:
            self.statusBar().showMessage("Set loop start ([) and end (]) first.", 4000)
            pl.set_loop(pl.loop_a, pl.loop_b, enabled=False)
            return
        pl.set_loop(pl.loop_a, pl.loop_b, enabled=not pl.loop_enabled)
        if pl.loop_enabled and not (pl.loop_a <= pl.current <= pl.loop_b):
            pl.seek(pl.loop_a)

    def clear_loop(self) -> None:
        self.player.set_loop(None, None, enabled=False)
        self._store_loop()

    def _store_loop(self) -> None:
        p = self.doc.project
        if (p.loop_a, p.loop_b) != (self.player.loop_a, self.player.loop_b):
            p.loop_a, p.loop_b = self.player.loop_a, self.player.loop_b
            self.doc.mark_dirty()

    def goto_dialog(self) -> None:
        if not self.player.is_open:
            return
        d = GotoDialog(self, self.player.fps, self.player.current, self.player.frame_count)
        if d.exec():
            self.player.seek(d.result_frame)

    # ========================================================= workspaces
    def set_workspace(self, ws: str) -> None:
        self.workspace = ws
        self.ws_buttons[ws].setChecked(True)
        self.side.blockSignals(True)
        while self.side.count():
            self.side.removeTab(0)
        if ws == "review":
            self.canvas.set_editable(False)
            self.palette.hide()
            self.side.addTab(self.bookmarks, "Incidents")
            self.side.addTab(self.match_info, "Match")
        elif ws == "tactics":
            self.canvas.set_editable(True, telestrator=False)
            self.palette.show()
            self.side.addTab(self.props_scroll, "Drawing")
            self.side.addTab(self.bookmarks, "Incidents")
            self.side.addTab(self.match_info, "Match")
        else:
            self.canvas.set_editable(True, telestrator=True)
            self.canvas.persist_telestrator = self.telestrator.keep.isChecked()
            self.palette.show()
            self.side.addTab(self.telestrator, "Present")
            self.choose_tool(Kind.FREEHAND if self.canvas.tool == "select" else self.canvas.tool)
        self.side.blockSignals(False)
        for tool in ["select"] + Kind.ALL:
            self.actions[f"tool.{tool}"].setEnabled(ws != "review")
        self.properties.sync()

    def choose_tool(self, tool: str) -> None:
        if self.workspace == "review":
            self.set_workspace("tactics")
        self.canvas.set_tool(tool)
        self.palette.set_tool(tool)

    def toggle_presentation(self, on: Optional[bool] = None) -> None:
        if on is None:
            on = not self.presenting
        if on == self.presenting:
            self.actions["view.presentation"].setChecked(on)
            return
        self.presenting = on
        if on:
            self._pre_present_state = (self.isFullScreen(), self.workspace)
            if self.workspace != "present":
                self.set_workspace("present")
            self.menuBar().hide()
            self.header.hide()
            self.side.hide()
            self.statusBar().hide()
            self.showFullScreen()
        else:
            self.menuBar().show()
            self.header.show()
            self.side.show()
            self.statusBar().show()
            was_full, ws = getattr(self, "_pre_present_state", (False, "present"))
            if not was_full:
                self.showNormal()
        self.act_exit_present.setEnabled(on)
        self.actions["view.presentation"].setChecked(on)
        self.canvas.setFocus()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self.actions["view.fullscreen"].setChecked(self.isFullScreen())

    def toggle_drawings(self) -> None:
        self.canvas.show_drawings = not self.canvas.show_drawings
        self.actions["view.hide_drawings"].setChecked(self.canvas.show_drawings)
        self.canvas.update()

    # ============================================================ editing
    def delete_selection(self) -> None:
        if self.canvas.selection:
            self.canvas.delete_selection()
        elif self.bookmarks.list.hasFocus():
            self.bookmarks._delete_selected()

    def cut(self) -> None:
        if self.canvas.copy_selection():
            self.canvas.delete_selection()

    # ========================================================== bookmarks
    def _need_video(self) -> bool:
        if not self.player.is_open:
            self.statusBar().showMessage("Open a video first (Ctrl+Shift+O).", 4000)
            return False
        return True

    def quick_bookmark(self) -> None:
        if not self._need_video():
            return
        n = len(self.doc.project.bookmarks) + 1
        cats = [c["name"] for c in self.doc.project.categories]
        cat = self._last_category if self._last_category in cats else cats[-1]
        bm = Bookmark(self.player.current, f"Incident {n}", cat)
        self.doc.add_bookmark(bm)
        self.bookmarks.select(bm.id)
        self.statusBar().showMessage(
            f"Bookmark added at {format_timecode(self.player.seconds(), self.player.fps, frame=bm.frame)} "
            "— double-click it to add details.", 4000)

    def detailed_bookmark(self) -> None:
        if not self._need_video():
            return
        self.player.pause()
        cats = [c["name"] for c in self.doc.project.categories]
        bm = Bookmark(self.player.current, "", self._last_category if self._last_category in cats else cats[-1])
        d = BookmarkDialog(self, bm, self.doc, self.player.fps, self.player.frame_count,
                           self.player.current, "Add bookmark")
        if d.exec():
            for k, v in d.values().items():
                setattr(bm, k, v)
            if not bm.title:
                bm.title = f"Incident {len(self.doc.project.bookmarks) + 1}"
            self._last_category = bm.category
            self.doc.add_bookmark(bm)
            self.bookmarks.select(bm.id)

    def edit_bookmark(self, bid: str) -> None:
        bm = self.doc.project.bookmark(bid)
        if bm is None:
            return
        self.player.pause()
        d = BookmarkDialog(self, bm, self.doc, self.player.fps, self.player.frame_count,
                           self.player.current, "Edit bookmark")
        if d.exec():
            vals = d.values()
            self._last_category = vals["category"]
            self.doc.update_bookmark(bid, **vals)

    def delete_bookmarks(self, ids: list) -> None:
        if len(ids) > 1 and QMessageBox.question(self, "Delete bookmarks",
                                                 f"Delete {len(ids)} bookmarks?") != QMessageBox.Yes:
            return
        self.doc.remove_bookmarks(ids)

    def jump_bookmark(self, direction: int) -> None:
        bms = self.doc.project.sorted_bookmarks()
        cur = self.player.current
        if direction > 0:
            nxt = next((b for b in bms if b.frame > cur), None)
        else:
            nxt = next((b for b in reversed(bms) if b.frame < cur), None)
        if nxt is not None:
            self.player.pause()
            self.player.seek(nxt.frame)
            self.bookmarks.select(nxt.id)
            self.statusBar().showMessage(f"{nxt.category}: {nxt.title}", 3000)

    def edit_categories(self) -> None:
        d = CategoriesDialog(self, self.doc.project.categories)
        if d.exec():
            self.doc.set_categories(d.categories())

    # ======================================================== thumbnails
    def _start_thumbs(self, path: str, index: IndexData) -> None:
        self._stop_thumbs()
        self._bm_thumbs.clear()
        self._bm_thumb_pending.clear()
        self._thumb_thread = QThread()
        self._thumb_worker = ThumbnailWorker(path, index)
        self._thumb_worker.moveToThread(self._thumb_thread)
        self._thumb_thread.started.connect(self._thumb_worker.run)
        self._thumb_worker.thumbReady.connect(self._on_thumb, Qt.QueuedConnection)
        self._thumb_thread.start(QThread.LowPriority)

    def _stop_thumbs(self) -> None:
        if self._thumb_worker is not None:
            self._thumb_worker.stop()
            self._thumb_thread.quit()
            self._thumb_thread.wait(3000)
            self._thumb_worker = None
            self._thumb_thread = None

    def _request_hover_thumb(self, frame: int) -> None:
        if self._thumb_worker is None or self.timeline.thumb_cached(frame) is not None:
            return
        b = self.timeline.thumb_bucket
        self._thumb_worker.request((frame // b) * b, 200, front=True)

    def _request_bm_thumb(self, frame: int) -> None:
        if self._thumb_worker is None or frame in self._bm_thumb_pending:
            return
        self._bm_thumb_pending.add(frame)
        self._thumb_worker.request(frame, 224, exact=True)

    def _on_thumb(self, frame: int, img: QImage) -> None:
        if frame in self._bm_thumb_pending:
            self._bm_thumb_pending.discard(frame)
            self._bm_thumbs[frame] = img
            self.bookmarks.update_thumbnail(frame)
        else:
            self.timeline.add_thumbnail(frame, img)

    # ============================================================ files
    def _confirm_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes",
                                 "Save changes to the current project?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def _replace_document(self, doc: Document) -> None:
        old = self.doc
        if old is not None and not old.dirty:
            self.recovery.discard(old.project)
        self._attach_document(doc)

    def new_project(self) -> None:
        if not self._confirm_discard():
            return
        self.recovery.discard(self.doc.project)
        self._close_video()
        self._replace_document(Document())

    def _close_video(self) -> None:
        self._stop_thumbs()
        self.player.close()
        if self._export_src is not None:
            self._export_src.close()
            self._export_src = None
        self.canvas.set_image(None)
        self.timeline.set_video(0, 30)
        self.status_video.setText("")
        self._update_transport()

    def _run_busy(self, label: str, fn, *args):
        """Run *fn* in a worker thread while showing a busy dialog."""
        dlg = QProgressDialog(label, None, 0, 0, self)
        dlg.setWindowTitle(APP_NAME)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(400)
        dlg.setCancelButton(None)
        with ThreadPoolExecutor(1) as ex:
            fut = ex.submit(fn, *args)
            while not fut.done():
                QApplication.processEvents()
                time.sleep(0.01)
        dlg.close()
        return fut.result()

    def load_video(self, path: str) -> bool:
        """Open *path* into the player for the current document."""
        try:
            index = self._run_busy(f"Indexing {os.path.basename(path)}…", load_or_build_index,
                                   path, os.path.join(app_data_dir(), "index_cache"))
            src = self.player.open(path, self.prefs.cache_mb, self.prefs.decoder_threads, index=index)
        except (VideoError, OSError) as exc:
            QMessageBox.critical(self, "Cannot open video", str(exc))
            return False
        except Exception as exc:  # FFmpeg errors
            QMessageBox.critical(self, "Cannot open video", f"{type(exc).__name__}: {exc}")
            return False
        if self._export_src is not None:
            self._export_src.close()
            self._export_src = None
        i = src.info
        self.timeline.set_video(i.frame_count, i.fps)
        self.timeline.show_frames = self.prefs.show_frame_numbers
        self.timeline.thumbnails_enabled = self.prefs.timeline_thumbnails
        self.properties.set_fps(i.fps)
        self.bookmarks.fps = i.fps
        self.canvas.set_zoom(None)
        self.status_video.setText(f"{i.width}×{i.height} · {i.fps:.2f} fps · {i.codec}")
        self._start_thumbs(path, index)
        self.match_info.load()
        self._refresh_playlist()
        self.bookmarks.refresh()
        return True

    def open_video_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open match recording", self._last_dir(), VIDEO_FILTER)
        if path:
            self.open_video(path)

    def open_video(self, path: str) -> None:
        start_new = True
        if self.doc.project.video.path or self.doc.project.bookmarks or self.doc.project.annotations:
            r = QMessageBox.question(
                self, "Open video",
                "Start a new project for this video?\n\n"
                "Yes = new project.  No = replace the video in the current project "
                "(bookmarks and drawings keep their frame numbers).",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            start_new = r == QMessageBox.Yes
        if start_new:
            if not self._confirm_discard():
                return
            self.recovery.discard(self.doc.project)
            self._close_video()
            doc = Document(Project(name=os.path.splitext(os.path.basename(path))[0]))
            self._replace_document(doc)
        if self.load_video(path):
            info = self.player.source.info
            self.doc.project.video.path = info.path
            self.doc.project.video.fingerprint = info.fingerprint()
            self.doc.mark_dirty()
            self._remember_dir(path)

    def open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open project", self._last_dir(), PROJECT_FILTER)
        if path:
            self.open_project(path)

    def open_project(self, path: str, recovered: Optional[Project] = None) -> bool:
        if recovered is None and not self._confirm_discard():
            return False
        if recovered is not None:
            proj, used_backup = recovered, False
        else:
            try:
                proj, used_backup = storage.load_with_backup_fallback(path)
            except storage.ProjectError as exc:
                QMessageBox.critical(self, "Cannot open project", str(exc))
                return False
        self.recovery.discard(self.doc.project)
        self._close_video()
        doc = Document(proj, path)
        self._replace_document(doc)
        if used_backup:
            QMessageBox.warning(self, "Project restored from backup",
                                "The project file was damaged, so the last backup (.bak) was opened. "
                                "Save the project to repair the file.")
            doc.mark_dirty()
        if path and recovered is None:
            self.prefs.add_recent(path)
            self._remember_dir(path)
        if proj.video.path or proj.video.rel_path:
            vpath = storage.resolve_video(proj, path)
            if vpath is None:
                vpath = self._ask_relink(proj.video.path)
            if vpath is not None:
                if self.load_video(vpath):
                    info = self.player.source.info
                    if not storage.fingerprint_matches(proj, info):
                        QMessageBox.warning(self, "Different video?",
                                            "This video does not match the one the project was created with "
                                            "(different length or resolution). Bookmarks and drawings may "
                                            "not line up.")
                    if os.path.normcase(vpath) != os.path.normcase(proj.video.path or ""):
                        proj.video.path = info.path
                        proj.video.fingerprint = info.fingerprint()
                        doc.mark_dirty()
                    self.player.set_loop(proj.loop_a, proj.loop_b, enabled=False)
                    self.player.seek(proj.last_frame)
        if recovered is None and not used_backup:
            doc.mark_clean()
        self._update_title()
        return True

    def _ask_relink(self, old_path: str) -> Optional[str]:
        box = QMessageBox(self)
        box.setWindowTitle("Video not found")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"The project's video could not be found:\n{old_path}")
        box.setInformativeText("Locate the file to relink it. Bookmarks and drawings are kept.")
        locate = box.addButton("Locate video…", QMessageBox.AcceptRole)
        box.addButton("Continue without video", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is locate:
            start = os.path.dirname(old_path) if old_path and os.path.isdir(os.path.dirname(old_path)) \
                else self._last_dir()
            name = os.path.basename((old_path or "").replace("\\", "/"))
            path, _ = QFileDialog.getOpenFileName(self, f"Locate {name}", start, VIDEO_FILTER)
            return path or None
        return None

    def relink_video_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Relink video", self._last_dir(), VIDEO_FILTER)
        if not path:
            return
        proj = self.doc.project
        if self.load_video(path):
            info = self.player.source.info
            if not storage.fingerprint_matches(proj, info):
                QMessageBox.warning(self, "Different video?",
                                    "This video has a different length or resolution than the original.")
            proj.video.path = info.path
            proj.video.fingerprint = info.fingerprint()
            self.doc.mark_dirty()

    def save(self) -> bool:
        if not self.doc.path:
            return self.save_as()
        return self._save_to(self.doc.path)

    def save_as(self) -> bool:
        default = os.path.join(self._last_dir(), safe_name(self.doc.project.name, "match") + storage.EXTENSION)
        path, _ = QFileDialog.getSaveFileName(self, "Save project", default, PROJECT_FILTER)
        if not path:
            return False
        if not path.lower().endswith(storage.EXTENSION):
            path += storage.EXTENSION
        return self._save_to(path)

    def _save_to(self, path: str) -> bool:
        p = self.doc.project
        p.last_frame = self.player.current
        p.loop_a, p.loop_b = self.player.loop_a, self.player.loop_b
        try:
            storage.save_project(p, path)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", f"Could not save the project:\n{exc}")
            return False
        self.doc.path = path
        self.doc.mark_clean()
        self.recovery.discard(p)
        self.prefs.add_recent(path)
        self._remember_dir(path)
        self.status_save.setText(f"Saved {time.strftime('%H:%M')}")
        self._update_title()
        return True

    def _autosave(self) -> None:
        secs = self.prefs.autosave_seconds
        if not secs or not self.doc.dirty:
            return
        if time.time() - self._last_autosave < secs:
            return
        self._last_autosave = time.time()
        self.doc.project.last_frame = self.player.current
        try:
            self.recovery.write(self.doc.project, self.doc.path)
            self.status_save.setText(f"Autosaved {time.strftime('%H:%M')}")
        except OSError as exc:
            self.status_save.setText(f"Autosave failed: {exc}")

    def emergency_autosave(self) -> None:
        try:
            if self.doc.dirty:
                self.recovery.write(self.doc.project, self.doc.path)
        except Exception:
            pass

    def check_recovery(self) -> None:
        entries = self.recovery.entries()
        if not entries:
            return
        e = entries[0]
        when = time.strftime("%d %b %Y %H:%M", time.localtime(e.saved))
        where = e.project_path or "an unsaved project"
        r = QMessageBox.question(
            self, "Recover work?",
            f"FC27 Tactical Studio did not close normally.\n\nUnsaved changes to “{e.name}” "
            f"({where}) from {when} can be recovered.\n\nRecover them now?",
            QMessageBox.Yes | QMessageBox.No)
        if r == QMessageBox.Yes:
            try:
                proj = self.recovery.load(e)
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Recovery failed", str(exc))
                return
            self.open_project(e.project_path or "", recovered=proj)
            if not e.project_path:
                self.doc.path = None
            self.doc.mark_dirty()
            self.recovery.discard_file(e.file)
            self.recovery.write(self.doc.project, self.doc.path)
        else:
            self.recovery.discard_file(e.file)
        for other in entries[1:]:
            self.recovery.discard_file(other.file)

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        recents = [r for r in self.prefs.recents() if os.path.isfile(r)]
        if not recents:
            self.recent_menu.addAction("(none)").setEnabled(False)
        for r in recents:
            self.recent_menu.addAction(r, lambda p=r: self.open_project(p))

    def _last_dir(self) -> str:
        d = qsettings().value("last_dir", "")
        return d if d and os.path.isdir(d) else os.path.expanduser("~")

    def _remember_dir(self, path: str) -> None:
        qsettings().setValue("last_dir", os.path.dirname(os.path.abspath(path)))

    # ============================================================ export
    def _source_for_export(self) -> Optional[VideoSource]:
        if not self.player.is_open:
            self._need_video()
            return None
        if self._export_src is None:
            self._export_src = VideoSource(self.player.source.path, self.player.index_data, cache_frames=4)
        return self._export_src

    def _current_composite(self, with_drawings: bool = True) -> Optional[QImage]:
        src = self._source_for_export()
        if src is None:
            return None
        anns = self.canvas.visible_annotations() if self.canvas.show_drawings else []
        return compose(src, self.player.current, anns, with_drawings)

    def export_frame_dialog(self, with_drawings: bool = True) -> None:
        if not self._need_video():
            return
        self.player.pause()
        fps = self.player.fps
        tc = format_timecode(self.player.seconds(), fps, frame=self.player.current).replace(":", "-")
        default = os.path.join(self._last_dir(), safe_name(f"{self.doc.project.name} {tc}") + ".png")
        path, flt = QFileDialog.getSaveFileName(self, "Export frame", default,
                                                "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".jpg" if "JPEG" in flt else ".png"
        try:
            src = self._source_for_export()
            anns = self.canvas.visible_annotations() if self.canvas.show_drawings else []
            export_frame(src, self.player.current, anns, path, with_drawings, self.prefs.jpeg_quality)
            self.statusBar().showMessage(f"Exported {path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def copy_frame(self) -> None:
        img = self._current_composite()
        if img is not None:
            QGuiApplication.clipboard().setImage(img)
            self.statusBar().showMessage("Frame copied to clipboard.", 3000)

    def export_bookmark_images_dialog(self) -> None:
        src = self._source_for_export()
        if src is None:
            return
        bms = self.bookmarks.filtered()
        if not bms:
            QMessageBox.information(self, "Export", "No bookmarks to export (check the filters).")
            return
        folder = QFileDialog.getExistingDirectory(self, f"Export {len(bms)} bookmark images to…", self._last_dir())
        if not folder:
            return
        dlg = QProgressDialog("Exporting images…", "Cancel", 0, len(bms), self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)

        def progress(i, n):
            dlg.setValue(i)
            QApplication.processEvents()
            return not dlg.wasCanceled()
        try:
            out = export_bookmark_images(src, self.doc.project, bms, folder, "png", True,
                                         self.prefs.jpeg_quality, progress)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        finally:
            dlg.close()
        self.statusBar().showMessage(f"Exported {len(out)} images to {folder}", 6000)

    def export_csv_dialog(self) -> None:
        default = os.path.join(self._last_dir(), safe_name(self.doc.project.name, "bookmarks") + " bookmarks.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export bookmark list", default, "CSV (*.csv)")
        if path:
            try:
                export_bookmarks_csv(self.doc.project, self.player.fps, path)
                self.statusBar().showMessage(f"Exported {path}", 5000)
            except OSError as exc:
                QMessageBox.critical(self, "Export failed", str(exc))

    # ======================================================= preferences
    def preferences(self) -> None:
        d = PreferencesDialog(self, self.prefs)
        if d.exec():
            d.apply()
            self.player.drop_frames = self.prefs.drop_frames
            self.player.set_cache_mb(self.prefs.cache_mb)
            self.timeline.show_frames = self.prefs.show_frame_numbers
            self.timeline.thumbnails_enabled = self.prefs.timeline_thumbnails
            self.telestrator.clear_on_play.setChecked(self.prefs.telestrator_clear_on_play)
            self.properties.hold_s.setValue(self.prefs.default_hold_seconds)
            self._update_transport()

    def edit_shortcuts(self) -> None:
        d = ShortcutsDialog(self, self.shortcut_map)
        if d.exec():
            self.shortcut_map = d.mapping
            save_shortcuts(self.shortcut_map)
            for aid, act in self.actions.items():
                seq = self.shortcut_map.get(aid, "")
                act.setShortcut(QKeySequence(seq) if seq else QKeySequence())
            self.palette.set_tooltips(self.shortcut_map)

    def shortcut_card(self) -> None:
        ShortcutCard(self, self.shortcut_map).exec()

    def user_guide(self) -> None:
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (os.path.join(here, "..", "..", "docs", "USER_GUIDE.md"),
                     os.path.join(here, "..", "docs", "USER_GUIDE.md")):
            if os.path.isfile(cand):
                from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout as VB
                dlg = QDialog(self)
                dlg.setWindowTitle("User guide")
                dlg.resize(820, 760)
                tb = QTextBrowser()
                with open(cand, encoding="utf-8") as f:
                    tb.setMarkdown(f.read())
                VB(dlg).addWidget(tb)
                dlg.exec()
                return
        self.shortcut_card()

    # ========================================================== window
    def _restore_geometry(self) -> None:
        s = qsettings()
        g = s.value("window/geometry")
        if isinstance(g, QByteArray):
            self.restoreGeometry(g)
        sp = s.value("window/splitter")
        if isinstance(sp, QByteArray):
            self.splitter.restoreState(sp)

    def closeEvent(self, ev: QCloseEvent) -> None:
        if self.presenting:
            self.toggle_presentation(False)
        if not self._confirm_discard():
            ev.ignore()
            return
        self.recovery.discard(self.doc.project)
        s = qsettings()
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/splitter", self.splitter.saveState())
        self._stop_thumbs()
        self.player.close()
        if self._export_src is not None:
            self._export_src.close()
        ev.accept()

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        urls = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if not urls:
            return
        path = urls[0]
        if path.lower().endswith(storage.EXTENSION):
            self.open_project(path)
        else:
            self.open_video(path)
