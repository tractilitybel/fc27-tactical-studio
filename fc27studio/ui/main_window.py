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
                               QSplitter, QTabWidget, QVBoxLayout, QWidget, QListWidgetItem,
                               QStackedWidget, QComboBox, QDialog, QDialogButtonBox, QInputDialog)

from .. import APP_NAME, __version__
from ..core import storage
from ..core.document import Document, ScopedDocument
from ..core.model import Board, Bookmark, Kind, Project
from ..core.video import IndexData, VideoError, VideoSource, format_timecode, load_or_build_index
from .bookmarks import BookmarkDialog, BookmarkPanel, CategoriesDialog
from .canvas import Canvas
from .dialogs import (GotoDialog, PreferencesDialog, ShortcutCard, ShortcutsDialog, about_text)
from .board import BoardPanel, PX_PER_M, formation_tokens, render_pitch
from .export import (compose, compose_compare, export_bookmark_images, export_bookmarks_csv,
                     export_frame, safe_name, save_image)
from .panels import PropertiesPanel, TelestratorBar, ToolPalette
from .playback import Player, ThumbnailWorker
from .settings import (app_data_dir, qsettings, SHORTCUTS, Preferences, load_shortcuts, recovery_dir,
                       save_shortcuts)
from .timeline import Marker, Span, Timeline, Transport

VIDEO_FILTER = ("Video files (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.flv *.wmv);;"
                "All files (*)")
PROJECT_FILTER = f"FC27 Studio project (*{storage.EXTENSION})"
WORKSPACES = ["review", "tactics", "board", "present"]
VIDEO_TOOLS = ["select"] + Kind.VIDEO_TOOLS
BOARD_TOOLS = ["select"] + Kind.BOARD_TOOLS
PRESENT_TOOLS = ["select"] + Kind.ALL


class TokenDialog(QDialog):
    def __init__(self, parent, number: str, name: str):
        super().__init__(parent)
        self.setWindowTitle("Player token")
        form = QFormLayout(self)
        self.number = QLineEdit(number)
        self.number.setMaxLength(3)
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("optional, e.g. Rodri")
        form.addRow("Number", self.number)
        form.addRow("Name", self.name)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


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
        from .audio_out import AudioOutput
        self.audio = AudioOutput(self.player, parent=self)
        self.audio.volume = self.prefs.volume
        self.audio.muted = self.prefs.muted
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
        for i, (ws, label) in enumerate((("review", "Review"), ("tactics", "Tactics"),
                                         ("board", "Board"), ("present", "Present"))):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setObjectName("workspaceButton")
            b.clicked.connect(lambda _c=False, w=ws: self.set_workspace(w))
            self.ws_group.addButton(b, i)
            self.ws_buttons[ws] = b
            hl.addWidget(b)
        hl.addStretch(1)
        self.compare_combo = QComboBox()
        for label, val in (("Before/after: off", "off"), ("Before/after: split", "split"),
                           ("Before/after: side by side", "side")):
            self.compare_combo.addItem(label, val)
        self.compare_combo.setToolTip("Compare the original frame with your corrections (Y)")
        hl.addWidget(self.compare_combo)
        hl.addSpacing(16)
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
        self._segmenter = None
        self._segmenter_error: Optional[str] = None
        self.canvas.segmenter_provider = self._get_segmenter
        self.board_canvas = Canvas()
        self.board_canvas.board_mode = True
        self.board_canvas.editable = True
        self.board_canvas.px_per_m = PX_PER_M
        self.board_canvas.style.color = "#ffffff"
        self.board_canvas.placeholder = "Create a tactics board with “New” in the side panel."
        self._pitch = render_pitch()
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(video_area)
        self.center_stack.addWidget(self.board_canvas)
        ll.addWidget(self.center_stack, 1)
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
        self.board_panel = BoardPanel()
        self.board_props = PropertiesPanel(self.board_canvas, board=True)
        self.board_props_scroll = QScrollArea()
        self.board_props_scroll.setWidget(self.board_props)
        self.board_props_scroll.setWidgetResizable(True)
        self.board_props_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.current_board_id: Optional[str] = None
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
        A("file.export_video", self.export_video_dialog)
        A("file.quit", self.close)
        A("edit.undo", lambda: self.doc.undo.undo())
        A("edit.redo", lambda: self.doc.undo.redo())
        A("edit.cut", self.cut)
        A("edit.copy", lambda: self.cv.copy_selection())
        A("edit.paste", lambda: self.cv.paste())
        A("edit.duplicate", lambda: self.cv.duplicate())
        A("edit.delete", self.delete_selection)
        A("edit.select_all", lambda: self.cv.select_all())
        A("edit.group", lambda: self.cur_doc.group(self._sel_doc_ids()))
        A("edit.ungroup", lambda: self.cur_doc.ungroup(self._sel_doc_ids()))
        for m in ("front", "back", "forward", "backward"):
            A(f"edit.{m}", lambda _c=False, mode=m: self.cur_doc.reorder(self._sel_doc_ids(), mode))
        A("edit.prefs", self.preferences)
        A("play.toggle", self.toggle_play)
        A("play.reverse", lambda: self.player.jkl("j"))
        A("play.pause", lambda: self.player.jkl("k"))
        A("play.forward", lambda: self.player.jkl("l"))
        A("play.next_frame", lambda: self._nav(lambda: self.player.step(1)))
        A("play.prev_frame", lambda: self._nav(lambda: self.player.step(-1)))
        A("play.big_forward", lambda: self._nav(lambda: self.player.seek(self.player.current + self._big_step())))
        A("play.big_back", lambda: self._nav(lambda: self.player.seek(self.player.current - self._big_step())))
        A("play.jump_forward", lambda: self.jump_seconds(1))
        A("play.jump_back", lambda: self.jump_seconds(-1))
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
        A("play.mute", self.toggle_mute)
        A("play.add_freeze", self.add_freeze)
        A("play.record", self.toggle_record)
        self.act_play_freezes = QAction("Play freeze frames", self)
        self.act_play_freezes.setCheckable(True)
        self.act_play_freezes.setChecked(self.prefs.play_freezes)
        self.act_play_freezes.toggled.connect(self._set_play_freezes)
        self.player.freezes_enabled = self.prefs.play_freezes
        A("bm.add", self.quick_bookmark)
        A("bm.add_detailed", self.detailed_bookmark)
        A("bm.next", lambda: self.jump_bookmark(1))
        A("bm.prev", lambda: self.jump_bookmark(-1))
        for tool in ["select"] + Kind.EVERY:
            A(f"tool.{tool}", lambda _c=False, t=tool: self.choose_tool(t))
        A("tool.clear_telestrator", self.canvas.clear_telestrator)
        A("view.review", lambda: self.set_workspace("review"))
        A("view.tactics", lambda: self.set_workspace("tactics"))
        A("view.present", lambda: self.set_workspace("present"))
        A("view.board", lambda: self.set_workspace("board"))
        A("view.compare", self.cycle_compare)
        A("file.export_compare", self.export_compare_dialog)
        A("view.presentation", self.toggle_presentation, checkable=True)
        A("view.fullscreen", self.toggle_fullscreen, checkable=True)
        A("view.zoom_fit", lambda: self.cv.set_zoom(None))
        A("view.zoom_100", lambda: self.cv.set_zoom(1.0))
        A("view.zoom_in", lambda: self.cv.zoom_by(1.25))
        A("view.zoom_out", lambda: self.cv.zoom_by(0.8))
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
        self._update_jump_labels()

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
        ex.addAction(a["file.export_video"])
        ex.addSeparator()
        ex.addAction(a["file.export_frame"])
        ex.addAction("Export clean frame (no drawings)…", lambda: self.export_frame_dialog(False))
        ex.addAction(a["file.export_compare"])
        ex.addAction("Export tactics board image…", self.export_board_dialog)
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
        for k in ("view.review", "view.tactics", "view.board", "view.present"):
            v.addAction(a[k])
        v.addAction(a["view.compare"])
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
                  "play.jump_back", "play.jump_forward",
                  "play.start", "play.end", "play.goto"):
            p.addAction(a[k])
        p.addSeparator()
        for k in ("play.slower", "play.faster", "play.normal_speed"):
            p.addAction(a[k])
        p.addSeparator()
        for k in ("play.set_a", "play.set_b", "play.toggle_loop", "play.clear_loop"):
            p.addAction(a[k])
        p.addSeparator()
        p.addAction(a["play.add_freeze"])
        p.addAction(self.act_play_freezes)
        p.addSeparator()
        p.addAction(a["play.record"])
        self.act_mute_rec = QAction("Mute game sound while recording", self)
        self.act_mute_rec.setCheckable(True)
        self.act_mute_rec.setChecked(self.prefs.mute_game_while_recording)
        self.act_mute_rec.toggled.connect(lambda v: (setattr(self.prefs, "mute_game_while_recording", v),
                                                     self.prefs.save()))
        p.addAction(self.act_mute_rec)
        self.act_duck = QAction("Lower game sound under commentary", self)
        self.act_duck.setCheckable(True)
        self.act_duck.triggered.connect(lambda v: self.doc.set_project_fields(
            "Commentary ducking", duck_game_audio=bool(v)))
        p.addAction(self.act_duck)
        p.addSeparator()
        p.addAction(a["play.mute"])
        self.track_menu = p.addMenu("Audio track")
        self.track_menu.aboutToShow.connect(self._fill_track_menu)

        b = mb.addMenu("&Bookmarks")
        for k in ("bm.add", "bm.add_detailed", "bm.prev", "bm.next"):
            b.addAction(a[k])
        b.addSeparator()
        b.addAction("Edit categories…", self.edit_categories)

        t = mb.addMenu("&Tools")
        for tool in ["select"] + Kind.ALL + [Kind.CUTOUT]:
            t.addAction(a[f"tool.{tool}"])
        tb = t.addMenu("Tactics board")
        for tool in (Kind.TOKEN, Kind.BALL, Kind.MEASURE):
            tb.addAction(a[f"tool.{tool}"])
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
        tl.markerClicked.connect(self._on_marker_clicked)
        tl.markerDoubleClicked.connect(self._on_marker_double_clicked)
        tl.spanClicked.connect(self._on_span_clicked)
        self.canvas.selectionChanged.connect(self._refresh_ranges)
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
        pl.freezeChanged.connect(self._on_freeze_changed)
        self.properties.seek_request = lambda f: (pl.pause(), pl.seek(f))
        tr.btn_rec.clicked.connect(self.toggle_record)
        pl.stateChanged.connect(self._stop_record_if_paused)
        tr.btn_mute.clicked.connect(self.toggle_mute)
        tr.volume.valueChanged.connect(self._volume_changed)
        tr.volume.sliderReleased.connect(self.prefs.save)
        self.audio.stateChanged.connect(self._update_audio_ui)
        cv = self.canvas
        cv.stepRequested.connect(lambda d: pl.step(d))
        cv.zoomChanged.connect(lambda s: self.status_zoom.setText(f"{s * 100:.0f}%"))
        cv.toolChanged.connect(self.palette.set_tool)
        cv.annotationsEdited.connect(self._refresh_ranges)
        cv.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 6000))
        cv.compareChanged.connect(self._on_compare_changed)
        self.compare_combo.activated.connect(
            lambda i: self.canvas.set_compare(self.compare_combo.itemData(i)))
        bc = self.board_canvas
        bc.toolChanged.connect(self.palette.set_tool)
        bc.statusMessage.connect(lambda m: self.statusBar().showMessage(m, 6000))
        bc.stepRequested.connect(lambda _d: None)
        bc.zoomChanged.connect(lambda s: self.status_zoom.setText(f"{s * 100:.0f}%"))
        bc.tokenEditRequested.connect(self.edit_token)
        bp = self.board_panel
        bp.boardSelected.connect(self.select_board)
        bp.addRequested.connect(self.new_board)
        bp.duplicateRequested.connect(self.duplicate_board)
        bp.renameRequested.connect(self.rename_board)
        bp.deleteRequested.connect(self.delete_board)
        bp.formationRequested.connect(self.place_formation)
        bp.clearTeamRequested.connect(self.clear_team)
        bp.colorChanged.connect(self.set_team_color)
        bp.linkRequested.connect(self.link_board)
        bp.gotoLinkRequested.connect(self.goto_board_link)
        bp.exportRequested.connect(self.export_board_dialog)
        self.palette.toolSelected.connect(self.choose_tool)
        bp = self.bookmarks
        bp.jumpRequested.connect(lambda f: (pl.pause(), pl.seek(f)))
        bp.editRequested.connect(self.edit_bookmark)
        bp.addRequested.connect(self.detailed_bookmark)
        bp.deleteRequested.connect(self.delete_bookmarks)
        bp.thumbnailWanted.connect(self._request_bm_thumb)
        bp.categoriesRequested.connect(self.edit_categories)
        bp.exportVideoRequested.connect(self.export_reel)
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
        self.current_board_id = None
        self.select_board(doc.project.boards[0].id if doc.project.boards else None)
        self._sync_freezes()
        self._refresh_mixer()
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
            self.canvas.sync_animation()
            self.canvas.prune_selection()
            self.canvas.update()
            self.properties.sync()
            self._refresh_ranges()
        if what == "project":
            self.match_info.load()
        if what == "boards":
            boards = self.doc.project.boards
            if self.current_board_id is None or self.doc.project.board(self.current_board_id) is None:
                self.select_board(boards[0].id if boards else None)
            else:
                self.board_panel.show_boards(boards, self.current_board_id)
                self.board_panel.show_board(self.doc.project.board(self.current_board_id), self.player.fps)
                self.board_canvas.update()
        if what == "boards":
            self._refresh_markers()
        if what in ("freezes", "project"):
            self._refresh_markers()
            self._sync_freezes()
        if what in ("commentary", "project"):
            self._refresh_markers()
            self._refresh_mixer()
        if what in ("board", "boards"):
            self.board_canvas.prune_selection()
            self.board_canvas.update()
            self.board_props.sync()
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
        markers = [Marker(b.id, b.frame, b.end_frame, p.category_color(b.category),
                          f"{b.category}: {b.title}") for b in p.bookmarks]
        fps = self.player.fps or 30.0
        markers += [Marker(c.id, c.frame, c.frame + int(c.seconds * fps), "#b46cff",
                           f"Commentary: {c.name or 'voice'} ({c.seconds:.1f} s) — double-click to edit", "voice")
                    for c in p.commentary]
        markers += [Marker(f.id, f.frame, None, "#00c7ff",
                           f"Freeze {f.seconds:g} s" + (f" — {f.note}" if f.note else "") +
                           " (double-click to edit)", "freeze") for f in p.freezes]
        markers += [Marker(bd.id, bd.linked_frame, None, "#ffffff",
                           f"Tactics board: {bd.name} (double-click to open)", "board")
                    for bd in p.boards if bd.linked_frame is not None]
        self.timeline.set_markers(markers)

    def _refresh_ranges(self) -> None:
        sel = self.canvas.selection
        spans = []
        for a in self.doc.project.annotations[:600]:
            label = a.name or (a.text.split("\n")[0][:30] if a.kind == Kind.TEXT and a.text
                               else Kind.LABELS.get(a.kind, a.kind))
            spans.append(Span(a.id, a.start_frame, a.end_frame, a.color, label, a.id in sel,
                              keyframes=tuple(k["frame"] for k in a.keyframes)))
        self.timeline.set_ranges(spans)

    def _on_span_clicked(self, aid: str) -> None:
        """Timeline drawing bar: jump to the drawing's first frame and select it."""
        a = self.doc.project.annotation(aid)
        if a is None:
            return
        self.player.pause()
        if self.canvas.frame == a.start_frame and self.canvas.image is not None:
            self.canvas.set_selection({aid})
        else:
            self._pending_select = (a.start_frame, aid)
        name = Kind.LABELS.get(a.kind, a.kind)
        self.statusBar().showMessage(f"{name} — first frame "
                                     f"{format_timecode(a.start_frame / self.player.fps, self.player.fps, frame=a.start_frame)}",
                                     4000)

    def _on_marker_clicked(self, mid: str) -> None:
        if any(f.id == mid for f in self.doc.project.freezes) or \
                any(c.id == mid for c in self.doc.project.commentary):
            self.player.pause()
            return
        if self.doc.project.board(mid) is not None:
            self.player.pause()
            self.select_board(mid)
        else:
            self.bookmarks.select(mid)

    def _on_marker_double_clicked(self, mid: str) -> None:
        fz = next((f for f in self.doc.project.freezes if f.id == mid), None)
        if fz is not None:
            self.edit_freeze(fz)
            return
        cm = next((c for c in self.doc.project.commentary if c.id == mid), None)
        if cm is not None:
            self.edit_commentary(cm)
            return
        if self.doc.project.board(mid) is not None:
            self.select_board(mid)
            self.set_workspace("board")
        else:
            self.edit_bookmark(mid)

    def _refresh_playlist(self) -> None:
        pl = self.telestrator.playlist
        pl.clear()
        fps = self.player.fps
        for b in self.doc.project.sorted_bookmarks():
            it = QListWidgetItem(f"{format_timecode(b.frame / fps, fps, frame=b.frame)}  {b.title or b.category}")
            it.setData(Qt.UserRole, b.frame)
            pl.addItem(it)

    def _sel_doc_ids(self) -> list[str]:
        return [a.id for a in self.cv.selected() if not self.cv.is_temp(a.id)]

    def _get_segmenter(self):
        """Load the AI player-selection model on first use (None if unavailable)."""
        if self._segmenter is not None or self._segmenter_error is not None:
            return self._segmenter
        from ..core import segment
        model_dir = segment.find_model_dir([os.path.join(app_data_dir(), "models")])
        if model_dir is None or not segment.runtime_available():
            self._segmenter_error = "AI model not installed"
            self.statusBar().showMessage("AI player selection is not available in this build — "
                                         "drag a lasso around the player instead.", 6000)
            return None
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._segmenter = segment.PlayerSegmenter(model_dir)
        except Exception as exc:
            self._segmenter_error = str(exc)
            self.statusBar().showMessage(f"AI player selection could not start: {exc}", 8000)
        finally:
            QApplication.restoreOverrideCursor()
        return self._segmenter

    @property
    def cv(self) -> Canvas:
        """The canvas of the current workspace (video or tactics board)."""
        return self.board_canvas if self.workspace == "board" else self.canvas

    @property
    def cur_doc(self) -> Document:
        if self.workspace == "board" and self.board_canvas.doc is not None:
            return self.board_canvas.doc
        return self.doc

    # ========================================================= playback
    def _on_image(self, index: int, img: QImage) -> None:
        self.canvas.set_image(img)
        self.canvas.set_frame(index)
        pending = getattr(self, "_pending_select", None)
        if pending and pending[0] == index:
            self._pending_select = None
            self.canvas.set_selection({pending[1]})
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
                                    pl.loop_enabled, self.prefs.show_frame_numbers,
                                    pl.loop_a, pl.loop_b)

    def _timeline_seek(self, frame: int) -> None:
        self.player.pause()
        self.player.seek(frame)

    def toggle_play(self) -> None:
        if self.player.holding:
            self.player.skip_freeze()      # Space during a freeze: carry on now
            return
        self.player.toggle()

    # ========================================================= commentary
    def project_dir(self) -> Optional[str]:
        return os.path.dirname(os.path.abspath(self.doc.path)) if self.doc.path else None

    def commentary_mixer(self):
        if not self.doc.project.commentary:
            return None
        from ..core.commentary import CommentaryMixer
        return CommentaryMixer(self.doc.project, self.project_dir(), self.player.fps)

    def _refresh_mixer(self) -> None:
        if not hasattr(self, "audio"):
            return
        self.act_duck.setChecked(self.doc.project.duck_game_audio)
        mixer = self.commentary_mixer()
        self.audio.mixer = mixer
        if self.audio.engine is not None:
            self.audio.engine.mixer = mixer
            if self.audio.engine.running:
                self.audio._restart()

    def toggle_record(self) -> None:
        if getattr(self, "_rec", None) is not None:
            self.stop_recording()
        else:
            self.start_recording()
        self.transport.btn_rec.setChecked(getattr(self, "_rec", None) is not None)

    def start_recording(self) -> None:
        from .recorder import Recorder
        if not self._need_video():
            return
        rec = getattr(self, "recorder", None) or Recorder()
        self.recorder = rec
        if not rec.available:
            QMessageBox.warning(self, "Commentary", "No microphone was found on this PC.")
            return
        if not self.doc.path:
            QMessageBox.information(self, "Commentary",
                                    "Save the project first — voice recordings are stored next to it.")
            if not self.save_as():
                return
        self.player.set_speed(1.0)
        self._rec = {"frame": self.player.current}
        rec.start()
        self.audio.suppress = self.prefs.mute_game_while_recording
        self.player.play()
        self._rec_timer = QTimer(self)
        self._rec_timer.timeout.connect(self._update_rec_badge)
        self._rec_timer.start(200)
        self._update_rec_badge()
        self.statusBar().showMessage("Recording… talk over the video. Press R (or pause) to stop.", 5000)

    def _update_rec_badge(self) -> None:
        if getattr(self, "_rec", None) is None:
            return
        self.canvas.freeze_badge = f"● REC {self.recorder.seconds():.0f}s"
        self.canvas.update()

    def _stop_record_if_paused(self) -> None:
        if getattr(self, "_rec", None) is not None and not self.player.playing:
            self.stop_recording()
            self.transport.btn_rec.setChecked(False)

    def stop_recording(self) -> None:
        info, self._rec = getattr(self, "_rec", None), None
        if info is None:
            return
        if getattr(self, "_rec_timer", None) is not None:
            self._rec_timer.stop()
        data = self.recorder.stop()
        self.audio.suppress = False
        self.player.pause()
        self.canvas.freeze_badge = None
        self.canvas.update()
        from ..core.audio import RATE
        if len(data) < RATE * 0.3:
            self.statusBar().showMessage("Recording too short — nothing saved.", 4000)
            return
        from ..core.commentary import write_wav
        from ..core.model import Commentary
        n = len(self.doc.project.commentary) + 1
        folder = safe_name(os.path.splitext(os.path.basename(self.doc.path))[0]) + "_commentary"
        rel = os.path.join(folder, f"voice_{n:03d}_{int(time.time())}.wav")
        write_wav(os.path.join(self.project_dir(), rel), data)
        clip = Commentary(info["frame"], rel.replace("\\", "/"), len(data) / RATE, name=f"Voice {n}")
        self.doc.add_commentary(clip)
        self.statusBar().showMessage(f"Commentary saved ({clip.seconds:.1f} s).", 4000)

    def edit_commentary(self, clip) -> None:
        from PySide6.QtWidgets import QSlider
        d = QDialog(self)
        d.setWindowTitle("Voice commentary")
        form = QFormLayout(d)
        name = QLineEdit(clip.name)
        gain = QSlider(Qt.Horizontal)
        gain.setRange(0, 200)
        gain.setValue(int(round(clip.gain * 100)))
        gain_lbl = QLabel(f"{gain.value()}%")
        gain.valueChanged.connect(lambda v: gain_lbl.setText(f"{v}%"))
        row = QHBoxLayout()
        row.addWidget(gain, 1)
        row.addWidget(gain_lbl)
        form.addRow("Name", name)
        form.addRow("Volume", row)
        form.addRow(QLabel(f"Length {clip.seconds:.1f} s, starts at "
                           f"{format_timecode(clip.frame / self.player.fps, self.player.fps, frame=clip.frame)}"))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        delete = bb.addButton("Delete", QDialogButtonBox.DestructiveRole)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        delete.clicked.connect(lambda: d.done(2))
        form.addRow(bb)
        r = d.exec()
        if r == 2:
            self.doc.remove_commentary(clip.id)
        elif r:
            self.doc.update_commentary(clip.id, name=name.text().strip(), gain=gain.value() / 100)

    # ============================================================ freezes
    def _sync_freezes(self) -> None:
        self.player.freezes = {f.frame: f.seconds for f in self.doc.project.freezes}

    def _set_play_freezes(self, on: bool) -> None:
        self.player.freezes_enabled = on
        self.prefs.play_freezes = on
        self.prefs.save()

    def add_freeze(self) -> None:
        if not self._need_video():
            return
        self.player.pause()
        f = self.player.current
        existing = self.doc.project.freeze_at(f)
        if existing is not None:
            self.edit_freeze(existing)
            return
        from ..core.model import Freeze
        self.doc.add_freeze(Freeze(f, self.prefs.freeze_seconds))
        self.statusBar().showMessage(f"Freeze frame added: playback will pause here for "
                                     f"{self.prefs.freeze_seconds:g} s (double-click the blue marker to change).",
                                     5000)

    def edit_freeze(self, fz) -> None:
        from PySide6.QtWidgets import QDoubleSpinBox
        d = QDialog(self)
        d.setWindowTitle("Freeze frame")
        form = QFormLayout(d)
        secs = QDoubleSpinBox()
        secs.setRange(0.5, 120)
        secs.setDecimals(1)
        secs.setSuffix(" s")
        secs.setValue(fz.seconds)
        note = QLineEdit(fz.note)
        note.setPlaceholderText("optional, e.g. 'look at the gap'")
        form.addRow("Pause for", secs)
        form.addRow("Note", note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        delete = bb.addButton("Delete", QDialogButtonBox.DestructiveRole)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        delete.clicked.connect(lambda: d.done(2))
        form.addRow(bb)
        r = d.exec()
        if r == 2:
            self.doc.remove_freeze(fz.id)
        elif r:
            self.doc.update_freeze(fz.id, seconds=secs.value(), note=note.text().strip())
            self.prefs.freeze_seconds = secs.value()

    def _on_freeze_changed(self, left: float) -> None:
        self.canvas.freeze_badge = f"❚❚ {left:.0f}" if left > 0 else None
        self.canvas.update()

    # ================================================================ sound
    def _update_audio_ui(self) -> None:
        a = self.audio
        self.transport.show_audio(a.has_audio, a.device_available, a.muted, a.volume)

    def toggle_mute(self) -> None:
        self.audio.toggle_mute()
        self.prefs.muted = self.audio.muted
        self.prefs.save()
        self._update_audio_ui()
        self.statusBar().showMessage("Sound muted" if self.audio.muted else "Sound on", 2000)

    def _volume_changed(self, value: int) -> None:
        self.audio.set_volume(value / 100)
        self.prefs.volume = self.audio.volume
        self.prefs.muted = self.audio.muted
        self._update_audio_ui()

    def _fill_track_menu(self) -> None:
        m = self.track_menu
        m.clear()
        if not self.audio.tracks:
            m.addAction("(no sound in this video)").setEnabled(False)
            return
        for t in self.audio.tracks:
            act = m.addAction(t.title, lambda i=t.index: self.audio.set_track(i))
            act.setCheckable(True)
            act.setChecked(t.index == self.audio.track)

    def _nav(self, move) -> None:
        """Keyboard navigation (, . arrows) always returns playback speed to 1x."""
        if self.player.is_open and self.player.speed != 1.0:
            self.player.set_speed(1.0)
        move()

    def jump_seconds(self, direction: int) -> None:
        """Up/Down: jump by the configured number of seconds (keeps playing if playing)."""
        if not self.player.is_open:
            return
        if self.player.speed != 1.0:
            self.player.set_speed(1.0)
        step = max(1, int(round(self.prefs.jump_seconds * self.player.fps)))
        self.player.seek(self.player.current + direction * step)

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
        if ws == "board" and self.presenting:
            return
        self.workspace = ws
        self.ws_buttons[ws].setChecked(True)
        self.side.blockSignals(True)
        while self.side.count():
            self.side.removeTab(0)
        self.center_stack.setCurrentIndex(1 if ws == "board" else 0)
        self.compare_combo.setEnabled(ws != "board")
        if ws == "board":
            self.player.pause()
            self.palette.show()
            self.palette.set_available(BOARD_TOOLS)
            self.side.addTab(self.board_panel, "Board")
            self.side.addTab(self.board_props_scroll, "Drawing")
            if self.board_canvas.tool not in BOARD_TOOLS:
                self.board_canvas.set_tool("select")
            self.palette.set_tool(self.board_canvas.tool)
            if not self.doc.project.boards:
                self.statusBar().showMessage("Click “New” to create a tactics board.", 5000)
        elif ws == "review":
            self.canvas.set_editable(False)
            self.palette.hide()
            self.side.addTab(self.bookmarks, "Incidents")
            self.side.addTab(self.match_info, "Match")
        elif ws == "tactics":
            self.canvas.set_editable(True, telestrator=False)
            self.palette.show()
            self.palette.set_available(VIDEO_TOOLS)
            self.side.addTab(self.props_scroll, "Drawing")
            self.side.addTab(self.bookmarks, "Incidents")
            self.side.addTab(self.match_info, "Match")
            if self.canvas.tool not in VIDEO_TOOLS:
                self.canvas.set_tool("select")
            self.palette.set_tool(self.canvas.tool)
        else:
            self.canvas.set_editable(True, telestrator=True)
            self.canvas.persist_telestrator = self.telestrator.keep.isChecked()
            self.palette.show()
            self.palette.set_available(PRESENT_TOOLS)
            self.side.addTab(self.telestrator, "Present")
            keep = self.canvas.tool if self.canvas.tool in PRESENT_TOOLS and self.canvas.tool != "select" \
                else Kind.FREEHAND
            self.choose_tool(keep)
        self.side.blockSignals(False)
        self.properties.sync()
        self.board_props.sync()

    def choose_tool(self, tool: str) -> None:
        board_only = tool in (Kind.TOKEN, Kind.BALL, Kind.MEASURE)
        video_only = tool in (Kind.CUTOUT, Kind.SPOTLIGHT)
        if board_only and self.workspace != "board":
            self.set_workspace("board")
        elif video_only and self.workspace in ("board", "review", "present"):
            self.set_workspace("tactics")
        elif self.workspace == "review":
            self.set_workspace("tactics")
        allowed = {"board": BOARD_TOOLS, "present": PRESENT_TOOLS}.get(self.workspace, VIDEO_TOOLS)
        if tool not in allowed:
            return
        self.cv.set_tool(tool)
        self.palette.set_tool(tool)

    # ================================================== before / after
    def cycle_compare(self) -> None:
        if self.workspace == "board":
            return
        if not self._need_video():
            return
        self.canvas.cycle_compare()

    def _on_compare_changed(self, mode: str) -> None:
        i = self.compare_combo.findData(mode)
        self.compare_combo.setCurrentIndex(max(0, i))
        names = {"off": "Before/after comparison off",
                 "split": "Split view — drag the divider. Left: before, right: after.",
                 "side": "Side by side — left: before, right: after (view only)."}
        self.statusBar().showMessage(names[mode], 4000)

    def export_video_dialog(self, reel_bookmarks=None) -> None:
        if not self._need_video():
            return
        self.player.pause()
        from .export_dialog import ExportVideoDialog, run_export
        d = ExportVideoDialog(self, reel_bookmarks)
        if d.exec():
            jobs = d.jobs()
            self._remember_dir(jobs[0][0].path)
            run_export(self, jobs)

    def export_reel(self, ids: list) -> None:
        bms = [b for b in self.doc.project.bookmarks if b.id in set(ids)]
        if not bms:
            QMessageBox.information(self, "Export video", "There are no incidents to export.")
            return
        self.export_video_dialog(bms)

    def export_compare_dialog(self) -> None:
        src = self._source_for_export()
        if src is None:
            return
        self.player.pause()
        mode = "split" if self.canvas.compare == "split" else "side"
        fps = self.player.fps
        tc = format_timecode(self.player.seconds(), fps, frame=self.player.current).replace(":", "-")
        default = os.path.join(self._last_dir(), safe_name(f"{self.doc.project.name} {tc} before-after") + ".png")
        path, flt = QFileDialog.getSaveFileName(self, "Export before/after image", default,
                                                "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".jpg" if "JPEG" in flt else ".png"
        try:
            from .render import ndarray_to_qimage
            frame = ndarray_to_qimage(src.frame(self.player.current))
            anns = self.doc.annotations_at(self.player.current) + [a for a in self.canvas.temp if a.visible]
            img = compose_compare(frame, anns, mode, self.canvas.split)
            save_image(img, path, self.prefs.jpeg_quality)
            self.statusBar().showMessage(f"Exported {path}", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    # ====================================================== tactics boards
    def current_board(self) -> Optional[Board]:
        return self.doc.project.board(self.current_board_id) if self.current_board_id else None

    def select_board(self, bid: Optional[str]) -> None:
        board = self.doc.project.board(bid) if bid else None
        self.current_board_id = board.id if board else None
        bc = self.board_canvas
        if board is None:
            bc.set_document(None)
            bc.set_image(None)
        else:
            bc.set_document(ScopedDocument(self.doc, board))
            if bc.image is None:
                bc.set_image(self._pitch)
        self.board_panel.show_boards(self.doc.project.boards, self.current_board_id)
        self.board_panel.show_board(board, self.player.fps)
        self.board_props.sync()

    def new_board(self) -> None:
        n = len(self.doc.project.boards) + 1
        b = Board(name=f"Board {n}")
        b.annotations = formation_tokens("4-3-3", True, b.home_color) + \
            formation_tokens("4-4-2", False, b.away_color)
        for i, a in enumerate(b.annotations):
            a.z = i + 1
        if self.player.is_open:
            b.linked_frame = self.player.current
        self.doc.add_board(b)
        self.select_board(b.id)
        self.set_workspace("board")

    def duplicate_board(self) -> None:
        cur = self.current_board()
        if cur is None:
            return
        import copy
        from ..core.model import new_id
        b = copy.deepcopy(cur)
        b.id = new_id()
        b.name = f"{cur.name} (copy)"
        groups: dict = {}
        for a in b.annotations:
            a.id = new_id()
            if a.group_id:
                a.group_id = groups.setdefault(a.group_id, new_id())
        self.doc.add_board(b, self.doc.project.boards.index(cur) + 1)
        self.select_board(b.id)

    def rename_board(self) -> None:
        cur = self.current_board()
        if cur is None:
            return
        name, ok = QInputDialog.getText(self, "Rename board", "Name:", text=cur.name)
        if ok and name.strip():
            self.doc.update_board(cur.id, "Rename board", name=name.strip())

    def delete_board(self) -> None:
        cur = self.current_board()
        if cur is None:
            return
        if QMessageBox.question(self, "Delete board", f"Delete “{cur.name}”?") != QMessageBox.Yes:
            return
        self.doc.remove_board(cur.id)

    def _team_tokens(self, board: Board, home: bool) -> list:
        col = (board.home_color if home else board.away_color).lower()
        return [a for a in board.annotations if a.kind == Kind.TOKEN and a.color.lower() == col]

    def place_formation(self, home: bool, name: str) -> None:
        board = self.current_board()
        if board is None:
            self.new_board()
            board = self.current_board()
        sdoc = self.board_canvas.doc
        old = [a.id for a in self._team_tokens(board, home)]
        tokens = formation_tokens(name, home, board.home_color if home else board.away_color,
                                  self.board_canvas.token_size)
        sdoc.undo.begin_macro(f"Place {name}")
        sdoc.remove_annotations(old)
        sdoc.add_annotations(tokens, f"Place {name}")
        sdoc.undo.end_macro()

    def clear_team(self, home: bool) -> None:
        board = self.current_board()
        if board is not None:
            self.board_canvas.doc.remove_annotations([a.id for a in self._team_tokens(board, home)])

    def set_team_color(self, home: bool, color: str) -> None:
        board = self.current_board()
        if board is None:
            return
        ids = [a.id for a in self._team_tokens(board, home)]
        u = self.doc.undo
        u.begin_macro("Change team colour")
        if ids:
            self.board_canvas.doc.modify_annotations(ids, lambda a: setattr(a, "color", color), "Recolour")
        self.doc.update_board(board.id, **({"home_color": color} if home else {"away_color": color}))
        u.end_macro()
        self.board_panel.show_board(board, self.player.fps)

    def link_board(self) -> None:
        board = self.current_board()
        if board is None or not self._need_video():
            return
        self.doc.update_board(board.id, "Link board to video", linked_frame=self.player.current)

    def goto_board_link(self) -> None:
        board = self.current_board()
        if board is None or board.linked_frame is None or not self.player.is_open:
            return
        self.set_workspace("tactics")
        self.player.seek(board.linked_frame)

    def edit_token(self, aid: str) -> None:
        sdoc = self.board_canvas.doc
        a = sdoc.find_annotation(aid) if sdoc else None
        if a is None:
            return
        d = TokenDialog(self, a.text, a.name)
        if d.exec():
            num, name = d.number.text().strip(), d.name.text().strip()
            sdoc.modify_annotations([aid], lambda x: (setattr(x, "text", num), setattr(x, "name", name)),
                                    "Edit player token")

    def export_board_dialog(self) -> None:
        board = self.current_board()
        if board is None:
            QMessageBox.information(self, "Export", "Create a tactics board first.")
            return
        default = os.path.join(self._last_dir(), safe_name(f"{self.doc.project.name} {board.name}") + ".png")
        path, flt = QFileDialog.getSaveFileName(self, "Export tactics board", default,
                                                "PNG image (*.png);;JPEG image (*.jpg *.jpeg)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".jpg" if "JPEG" in flt else ".png"
        from .render import render_frame
        img = render_frame(self._pitch, [a for a in board.annotations if a.visible], PX_PER_M)
        try:
            save_image(img, path, self.prefs.jpeg_quality)
            self.statusBar().showMessage(f"Exported {path}", 5000)
        except OSError as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

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
        if self.cv.selection:
            self.cv.delete_selection()
        elif self.bookmarks.list.hasFocus():
            self.bookmarks._delete_selected()

    def cut(self) -> None:
        if self.cv.copy_selection():
            self.cv.delete_selection()

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
        self.audio.close()
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
        self.audio.open(path)
        self._update_audio_ui()
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

    def _carry_commentary(self, new_path: str) -> None:
        """Save As to another folder: copy voice recordings next to the new project."""
        import shutil
        from ..core.commentary import resolve
        old_dir = self.project_dir()
        new_dir = os.path.dirname(os.path.abspath(new_path))
        if not self.doc.project.commentary or (old_dir and os.path.normcase(old_dir) == os.path.normcase(new_dir)):
            return
        folder = safe_name(os.path.splitext(os.path.basename(new_path))[0]) + "_commentary"
        for c in self.doc.project.commentary:
            src = resolve(c, old_dir)
            if not os.path.isfile(src):
                continue
            rel = os.path.join(folder, os.path.basename(src))
            dst = os.path.join(new_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
                shutil.copy2(src, dst)
            c.file = rel.replace("\\", "/")

    def _save_to(self, path: str) -> bool:
        try:
            self._carry_commentary(path)
        except OSError as exc:
            QMessageBox.warning(self, "Save", f"Could not copy the voice recordings: {exc}")
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
        self._refresh_mixer()
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
    def _update_jump_labels(self) -> None:
        b = f"{self.prefs.big_step_seconds:g} s"
        self.actions["play.big_forward"].setText(f"Jump forward {b}")
        self.actions["play.big_back"].setText(f"Jump back {b}")
        j = f"{self.prefs.jump_seconds:g} s"
        self.actions["play.jump_forward"].setText(f"Jump forward {j}")
        self.actions["play.jump_back"].setText(f"Jump back {j}")

    def preferences(self) -> None:
        d = PreferencesDialog(self, self.prefs)
        if d.exec():
            d.apply()
            self._update_jump_labels()
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
        self.audio.close()
        self.player.close()
        if self._export_src is not None:
            self._export_src.close()
        self.prefs.save()
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
