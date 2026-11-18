"""Milestone 4: player tracking and pitch calibration."""
import time
from fractions import Fraction

import av
import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from track_scene import scene
from fc27studio.core.tracking import CameraMotion, PlayerTracker


def centre(b):
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def run_tracker(frames, truth):
    t = PlayerTracker(frames[0], truth[0])
    errs, oks = [], []
    for f, tb in zip(frames[1:], truth[1:]):
        s = t.step(f)
        oks.append(s.ok)
        (ax, ay), (bx, by) = centre(s.box), centre(tb)
        errs.append(np.hypot(ax - bx, ay - by))
    return np.array(errs), oks


@pytest.mark.parametrize("kw", [{"distractor": False}, {"mate_in_front": False}, {"mate_in_front": True},
                                {"mate_in_front": True, "pan": 0.0}])
def test_tracker_follows_player(kw):
    frames, truth = scene(**kw)
    errs, oks = run_tracker(frames, truth)
    assert all(oks)
    assert errs[70:].max() < 8, errs[70:].max()      # on the right player after the crossing
    assert np.median(errs) < 2


def test_tracker_reports_lost_when_player_vanishes():
    frames, truth = scene(distractor=False, n=60)
    blank = [f.copy() for f in frames]
    for f in blank[20:]:
        f[:] = blank[0][0:1, 0:1]                       # player (and everything) gone
    t = PlayerTracker(blank[0], truth[0])
    t.MAX_COAST = 10
    oks = [t.step(f).ok for f in blank[1:]]
    assert oks[:15].count(False) == 0 and oks[-1] is False


def test_camera_motion_measures_pan():
    frames, _ = scene(distractor=False, n=12, pan=7.0)
    cm = CameraMotion()
    cm.start(frames[0])
    for f in frames[1:]:
        H = cm.step(f)
        assert H is not None and abs(H[0, 2] + 7.0) < 0.6 and abs(H[1, 2]) < 0.6


def test_box_drawing_roundtrip():
    from fc27studio.core.model import Annotation, Kind
    from fc27studio.ui.tracking_ui import box_from_annotation, make_tracked
    box = (300.0, 200.0, 34.0, 90.0)
    for style in ("ring", "spotlight", "label", "ring_label"):
        for a in make_tracked(style, box, 0, "#fff", "Saka", 4.0):
            a.track_boxes = {0: list(box)}
            b = box_from_annotation(a)
            assert np.allclose(centre(b), centre(box), atol=1.5), (style, a.kind, b)


# ------------------------------------------------------------------ GUI
@pytest.fixture(scope="module")
def track_video(tmp_path_factory):
    frames, truth = scene(n=90, mate_in_front=True)
    path = str(tmp_path_factory.mktemp("trk") / "scene.mp4")
    out = av.open(path, "w")
    s = out.add_stream("libx264", rate=30)
    s.width, s.height, s.pix_fmt = frames[0].shape[1], frames[0].shape[0], "yuv420p"
    s.options = {"crf": "16"}
    s.time_base = Fraction(1, 30)
    for i, f in enumerate(frames):
        vf = av.VideoFrame.from_ndarray(f, format="rgb24")
        vf.pts = i
        for p in s.encode(vf):
            out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()
    return path, truth


def wait_for(app, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.003)
    return False


@pytest.fixture
def win(qapp, monkeypatch, track_video):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    from fc27studio.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1300, 850)
    w.show()
    w.open_video(track_video[0])
    assert wait_for(qapp, lambda: w.canvas.image is not None)
    yield w
    w.doc.mark_clean()
    w.close()


def drag_box(cv, box):
    x, y, bw, bh = box
    a = cv.to_widget(QPointF(x, y)).toPoint()
    b = cv.to_widget(QPointF(x + bw, y + bh)).toPoint()
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(cv, (a + b) / 2)
    QTest.mouseMove(cv, b)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, b)


def test_track_tool_creates_following_ring(qapp, win, track_video):
    from fc27studio.core.animation import interpolate
    from fc27studio.core.model import Kind
    _path, truth = track_video
    win.properties.track_style.setCurrentIndex(win.properties.track_style.findData("ring_label"))
    win.properties.track_label.setText("Saka")
    win.properties.track_secs.setValue(2.5)
    win.choose_tool("track")
    drag_box(win.canvas, truth[0])
    anns = win.doc.project.annotations
    ring = next(a for a in anns if a.kind == Kind.CIRCLE)
    label = next(a for a in anns if a.kind == Kind.TEXT)
    assert label.text == "Saka" and ring.group_id == label.group_id
    assert ring.end_frame >= 70 and len(ring.keyframes) >= 70
    for f in (10, 40, 70):
        pts, _ = interpolate(ring, f)
        cx = (pts[0][0] + pts[1][0]) / 2
        tx = truth[f][0] + truth[f][2] / 2
        assert abs(cx - tx) < 10, (f, cx, tx)
    # Correct at frame 50 and track on from there
    win.player.seek(50)
    assert wait_for(qapp, lambda: win.canvas.frame == 50)
    win.canvas.set_selection({ring.id, label.id})
    win.properties.track_secs.setValue(1.0)
    win.track_continue()
    assert ring.keyframes[-1]["frame"] >= 75
    win.doc.undo.undo()
    assert ring.keyframes[-1]["frame"] < 75 or ring.end_frame >= 70
