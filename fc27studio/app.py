"""Application entry point: theme, crash handling, startup."""
from __future__ import annotations

import faulthandler
import os
import sys
import time
import traceback

from . import APP_NAME, __version__

DARK_QSS = """
* { font-family: "Segoe UI", "Inter", sans-serif; font-size: 10pt; }
QMainWindow, QWidget { background: #171a20; color: #dfe4ea; }
QMenuBar { background: #12151a; }
QMenuBar::item:selected, QMenu::item:selected { background: #2b5f99; }
QMenu { background: #1d2128; border: 1px solid #333a45; }
QMenu::separator { height: 1px; background: #333a45; margin: 4px 8px; }
#header { background: #12151a; border-bottom: 1px solid #262b34; }
#brand { font-size: 12pt; color: #ffffff; }
#projectLabel { color: #9aa6b4; }
QPushButton#workspaceButton { background: transparent; border: none; padding: 6px 16px;
    color: #9aa6b4; font-weight: 600; border-bottom: 2px solid transparent; }
QPushButton#workspaceButton:checked { color: #ffffff; border-bottom: 2px solid #3d8bfd; }
QPushButton#workspaceButton:hover { color: #ffffff; }
QPushButton { background: #252a33; border: 1px solid #343b47; border-radius: 4px; padding: 5px 10px; }
QPushButton:hover { background: #2d3440; }
QPushButton:pressed { background: #1f242c; }
QPushButton:disabled { color: #5f6875; }
QPushButton#primary { background: #2f6fd6; border-color: #3d8bfd; color: white; font-weight: 600; }
QToolButton { background: transparent; border: 1px solid transparent; border-radius: 4px; padding: 3px 6px;
    font-size: 11pt; }
QToolButton:hover { background: #2a303a; border-color: #3a424f; }
QToolButton:checked { background: #2b5f99; border-color: #3d8bfd; }
QToolBar { background: #12151a; border: none; border-right: 1px solid #262b34; spacing: 2px; padding: 4px; }
QToolBar QToolButton { font-size: 14pt; min-width: 34px; min-height: 30px; }
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QKeySequenceEdit {
    background: #0f1216; border: 1px solid #343b47; border-radius: 4px; padding: 3px 5px;
    selection-background-color: #2b5f99; }
QComboBox QAbstractItemView { background: #1d2128; selection-background-color: #2b5f99; }
QListWidget, QTableWidget, QTextBrowser { background: #12151a; border: 1px solid #2a3039; border-radius: 4px; }
QListWidget::item { padding: 4px; border-bottom: 1px solid #1f242b; }
QListWidget::item:selected, QTableWidget::item:selected { background: #24466e; }
QHeaderView::section { background: #1d2128; border: none; padding: 4px; }
QTabWidget::pane { border: none; border-top: 1px solid #262b34; }
QTabBar::tab { background: transparent; padding: 7px 14px; color: #9aa6b4; }
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid #3d8bfd; }
QGroupBox { border: 1px solid #2a3039; border-radius: 5px; margin-top: 12px; padding-top: 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; color: #9aa6b4; }
QSlider::groove:horizontal { height: 4px; background: #2a303a; border-radius: 2px; }
QSlider::handle:horizontal { background: #3d8bfd; width: 14px; margin: -6px 0; border-radius: 7px; }
QSplitter::handle { background: #262b34; width: 2px; }
QStatusBar { background: #12151a; color: #9aa6b4; }
QScrollArea { border: none; }
QLabel#panelTitle { font-weight: 700; color: #ffffff; font-size: 10.5pt; padding: 4px 0; }
QLabel#hint { color: #8d98a6; }
QLabel#warning { color: #ff8a65; }
QLabel#timecode { font-family: "Consolas", "Menlo", monospace; font-size: 11pt; color: #ffffff; }
QProgressDialog { min-width: 320px; }
QCheckBox::indicator, QListWidget::indicator { width: 14px; height: 14px; border: 1px solid #56606e;
    border-radius: 3px; background: #0f1216; }
QCheckBox::indicator:checked, QListWidget::indicator:checked { background: #3d8bfd; border-color: #3d8bfd;
    image: none; }
QToolButton#swatch { padding: 0; font-size: 9pt; }
QToolTip { background: #1d2128; color: #dfe4ea; border: 1px solid #3a424f; }
"""


def _log_dir() -> str:
    from .ui.settings import app_data_dir
    d = os.path.join(app_data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def selfcheck() -> int:
    """Smoke test usable from a packaged build: `FC27TacticalStudio.exe --selfcheck`."""
    import tempfile
    from fractions import Fraction

    import av
    import numpy as np
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from .core.video import VideoSource
    from .ui.main_window import MainWindow
    from .ui.render import ndarray_to_qimage, render_frame
    from .core.model import Annotation, Kind
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "check.mp4")
        out = av.open(path, "w")
        codec = "libx264" if "libx264" in av.codecs_available else "mpeg4"
        s = out.add_stream(codec, rate=25)
        s.width, s.height, s.pix_fmt = 160, 90, "yuv420p"
        s.time_base = Fraction(1, 25)
        for i in range(50):
            arr = np.full((90, 160, 3), (i * 5) % 255, np.uint8)
            f = av.VideoFrame.from_ndarray(arr, format="rgb24")
            f.pts = i
            for pk in s.encode(f):
                out.mux(pk)
        for pk in s.encode():
            out.mux(pk)
        out.close()
        with VideoSource(path) as v:
            assert v.frame_count == 50, v.frame_count
            img = ndarray_to_qimage(v.frame(37))
            comp = render_frame(img, [Annotation(Kind.ARROW, [(10, 10), (100, 60)], 0, 10)])
            assert comp.width() == 160
        w = MainWindow()
        w.close()
    print(f"{APP_NAME} {__version__} selfcheck OK (codec {codec}, PyAV {av.__version__})")
    return 0


def _run_selfcheck(argv) -> int:
    """Windowed builds have no console, so results can also go to a log file."""
    log = None
    if "--selfcheck-log" in argv:
        i = argv.index("--selfcheck-log")
        log = argv[i + 1] if i + 1 < len(argv) else None
    try:
        code, msg = selfcheck(), "OK"
    except Exception:
        code, msg = 1, traceback.format_exc()
    if log:
        with open(log, "w", encoding="utf-8") as f:
            f.write(f"exit={code}\n{msg}\n")
    if code:
        sys.stderr.write(msg)
    return code


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--selfcheck" in argv:
        return _run_selfcheck(argv)
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FC27TacticalStudio.App")
        except Exception:
            pass
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("FC27TacticalStudio")
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_QSS)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icon.png")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    log_dir = _log_dir()
    try:
        crash_file = open(os.path.join(log_dir, "faults.log"), "a", encoding="utf-8")
        faulthandler.enable(crash_file)
    except OSError:
        pass

    from .ui.main_window import MainWindow
    win = MainWindow()

    def excepthook(etype, value, tb):
        text = "".join(traceback.format_exception(etype, value, tb))
        try:
            with open(os.path.join(log_dir, "errors.log"), "a", encoding="utf-8") as f:
                f.write(f"\n--- {time.ctime()} ---\n{text}")
        except OSError:
            pass
        win.emergency_autosave()
        QMessageBox.critical(win, "Unexpected error",
                             "Something went wrong. Your work has been autosaved.\n\n"
                             f"{etype.__name__}: {value}\n\nDetails were written to:\n{log_dir}")
    sys.excepthook = excepthook

    win.show()
    files = [a for a in argv[1:] if not a.startswith("-") and os.path.isfile(a)]

    def startup():
        win.check_recovery()
        if files:
            f = files[0]
            if f.lower().endswith(".fc27studio"):
                win.open_project(f)
            else:
                win.open_video(f)
    QTimer.singleShot(0, startup)
    return app.exec()
