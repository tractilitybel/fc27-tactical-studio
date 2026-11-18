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


# ------------------------------------------------------------ calibration
from fc27studio.core.calibration import LANDMARKS, apply_h, distance_m, lines_in_image, solve  # noqa: E402

PX_PER_M = 10.0
CALIB_NAMES = ["Centre spot", "Halfway line – top", "Left penalty spot", "Left box – top front corner",
               "Left box – bottom front corner"]


def calib_points(px_per_m=PX_PER_M, offset=(0.0, 0.0), noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    return [{"name": n, "x": LANDMARKS[n][0] * px_per_m + offset[0] + rng.normal(0, noise),
             "y": LANDMARKS[n][1] * px_per_m + offset[1] + rng.normal(0, noise)} for n in CALIB_NAMES]


def test_solve_perspective_camera():
    Ht = np.array([[14.0, -3.0, 120.0], [0.4, 4.5, 260.0], [0.0, 0.0045, 1.0]])   # pitch -> image
    names = CALIB_NAMES + ["Centre circle – bottom"]
    pts = [{"name": n, "x": apply_h(Ht, [LANDMARKS[n]])[0][0], "y": apply_h(Ht, [LANDMARKS[n]])[0][1]}
           for n in names]
    H, err, msg = solve(pts)
    assert err < 0.01 and "✓" in msg
    a, b = apply_h(Ht, [LANDMARKS["Left penalty spot"], LANDMARKS["Centre spot"]])
    assert distance_m(H, a, b) == pytest.approx(41.5, abs=0.01)
    assert lines_in_image(H)


def test_solve_rejects_bad_input():
    assert solve(calib_points()[:3])[0] is None
    line = [{"name": n, "x": i * 10.0, "y": 5.0} for i, n in enumerate(
        ["Halfway line – top", "Centre circle – top", "Centre spot", "Halfway line – bottom"])]
    H, _e, msg = solve(line)
    assert H is None and "one line" in msg
    pts = calib_points()
    pts[0]["x"] += 80                                   # one point on the wrong spot
    assert "wrong" in solve(pts)[2] or solve(pts)[1] > 5


def test_service_follows_camera_and_player_stats(qapp, track_video):
    """Calibrate frame 0, let the service measure the camera pan, and check the
    tracked player's speed against the known synthetic motion."""
    from fc27studio.core.calibration import Calibration
    from fc27studio.core.video import build_index
    from fc27studio.ui.calibration_ui import CalibrationService, run_stats
    path, truth = track_video
    H0, _e, _m = solve(calib_points())
    svc = CalibrationService()
    svc.set_video(path, build_index(path), 30.0, 90)
    svc.set_calibrations([Calibration(0, calib_points(), list(H0.ravel()))])
    svc.ensure(0, 60)
    assert wait_for(qapp, lambda: svc.get(60) is not None, 20)
    # The camera panned 5 px/frame: a world point keeps its pitch position across frames.
    p0 = apply_h(svc.get(0), [(400.0, 300.0)])[0]
    p60 = apply_h(svc.get(60), [(400.0 - 5 * 60, 300.0)])[0]
    assert np.allclose(p0, p60, atol=0.5)
    boxes = {f: list(truth[f]) for f in range(0, 61)}
    st = run_stats(boxes, 30.0, svc.get)
    _f, _P, dist, top, avg = st
    # Player runs 6 px/frame in the world = 0.6 m/frame = 18 m/s (+ a small sideways wave).
    assert 64 <= top <= 75, top
    assert 33 <= dist <= 40, dist
    svc.stop()


def test_calibrate_in_app_then_measure_and_stats(qapp, win, track_video):
    from fc27studio.core.model import Kind
    from fc27studio.ui.render import measure_text
    _path, truth = track_video
    win.start_calibration()
    assert win.calibrating and win.canvas.tool == "calibrate"
    cv = win.canvas
    for pt in calib_points():
        win.calib_panel.picker.selected = pt["name"]
        win.calib_panel.picker.picked.emit(pt["name"])
        QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, cv.to_widget(QPointF(pt["x"], pt["y"])).toPoint())
    assert len(win.calib_panel.points) == 5 and win.calib_panel.H is not None
    assert win.canvas.show_pitch_lines
    win.save_calibration()
    assert len(win.doc.project.calibrations) == 1
    assert any(m.kind == "calib" for m in win.timeline.markers)
    win.end_calibration()
    # Measure on the video now reads metres (centre spot to penalty spot = 41.5 m)
    win.choose_tool(Kind.MEASURE)
    a = cv.to_widget(QPointF(110, 340)).toPoint()
    b = cv.to_widget(QPointF(525, 340)).toPoint()
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseMove(cv, (a + b) / 2)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, b)
    m = next(x for x in win.doc.project.annotations if x.kind == Kind.MEASURE)
    txt = measure_text(m, None, win.calib.get(0))
    assert abs(float(txt.split()[0]) - 41.5) < 1.0, txt
    # Track a player: the panel shows distance/speed and can draw the run on a board
    win.properties.track_style.setCurrentIndex(win.properties.track_style.findData("ring"))
    win.properties.track_secs.setValue(2.0)
    win.choose_tool("track")
    drag_box(cv, truth[0])
    ring = next(x for x in win.doc.project.annotations if x.track_style == "ring")
    win.canvas.set_selection({ring.id})
    assert wait_for(qapp, lambda: "km/h" in win.properties.track_stats.text(), 20)
    win.run_to_board()
    assert win.workspace == "board"
    kinds = [x.kind for x in win.current_board().annotations]
    assert Kind.FREEHAND in kinds


def test_numpy_numbers_are_saved(tmp_path):
    """Regression: float32/int64 values (from tracking/OpenCV) broke saving and autosave."""
    from fc27studio.core import storage
    from fc27studio.core.model import Annotation, Kind, Project
    p = Project()
    a = Annotation(Kind.CIRCLE, [(np.float32(1.5), np.float32(2.5)), (np.float64(3), np.int64(4))], 0, 10)
    a.keyframes = [{"frame": np.int64(0), "points": [(np.float32(1), np.float32(2))] * 2, "rotation": np.float32(0)}]
    a.track_style = "ring"
    a.track_boxes = {0: [np.float32(1), np.float32(2), np.float32(3), np.float32(4)]}
    p.annotations.append(a)
    path = str(tmp_path / "np.fc27studio")
    storage.save_project(p, path)
    q = storage.load_project(path)
    assert q.annotations[0].keyframes[0]["points"][0] == (1.0, 2.0)
    rec = storage.Recovery(str(tmp_path / "rec"))
    rec.write(p, path)                                   # autosave path too
    assert rec.entries()


def test_tracked_project_saves_and_reloads(qapp, win, track_video, tmp_path):
    from fc27studio.core import storage
    _path, truth = track_video
    win.properties.track_style.setCurrentIndex(win.properties.track_style.findData("ring_label"))
    win.properties.track_secs.setValue(1.5)
    win.choose_tool("track")
    drag_box(win.canvas, truth[0])
    assert win.doc.project.annotations
    out = str(tmp_path / "tracked.fc27studio")
    assert win._save_to(out)                             # used to fail with float32
    win.prefs.autosave_seconds = 1
    win._last_autosave = 0
    win.doc.mark_dirty()
    win._autosave()
    assert "failed" not in win.status_save.text().lower()
    q = storage.load_project(out)
    ring = next(a for a in q.annotations if a.track_style)
    assert len(ring.keyframes) > 30 and ring.track_boxes
