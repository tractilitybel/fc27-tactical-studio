"""Milestone 2: player cut-outs, before/after comparison, tactics boards."""
import time

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from fc27studio.core import storage
from fc27studio.core.document import Document, ScopedDocument
from fc27studio.core.imaging import decode_png, encode_png, make_cutout, polygon_mask
from fc27studio.core.model import Annotation, Board, Kind, Project


# ---------------------------------------------------------------- imaging
def striped_pitch(h=360, w=640, seed=0):
    f = np.zeros((h, w, 3), np.uint8)
    for x in range(w):
        f[:, x] = (40, 120 + 15 * ((x // 40) % 2), 45)
    f[:, 300:303] = 230  # a pitch line
    rng = np.random.default_rng(seed)
    return np.clip(f.astype(int) + rng.integers(-5, 6, f.shape), 0, 255).astype(np.uint8)


def test_png_roundtrip():
    rng = np.random.default_rng(1)
    for shape in ((7, 5, 4), (13, 9, 3)):
        a = rng.integers(0, 256, shape, dtype=np.uint8)
        assert (decode_png(encode_png(a)) == a).all()
    q = QImage.fromData(encode_png(rng.integers(0, 256, (10, 12, 4), dtype=np.uint8)), "PNG")
    assert (q.width(), q.height()) == (12, 10)


def test_polygon_mask_area():
    m = polygon_mask([(10, 10), (30, 10), (30, 20), (10, 20)], 0, 0, 40, 30)
    assert abs(m.sum() - 200) < 2
    tri = polygon_mask([(0, 0), (20, 0), (0, 20)], 0, 0, 20, 20)
    assert abs(tri.sum() - 200) < 6


def test_cutout_removes_player_and_continues_line():
    bg = striped_pitch()
    f = bg.copy()
    f[120:200, 285:320] = (220, 40, 40)  # "player" standing on the line
    c = make_cutout(f, [(280, 115), (325, 115), (325, 205), (280, 205)])
    h, w = c.fill.shape[:2]
    a = c.fill[..., 3:] / 255.0
    region = f[c.y:c.y + h, c.x:c.x + w].astype(float)
    comp = region * (1 - a) + c.fill[..., :3] * a
    truth = bg[c.y:c.y + h, c.x:c.x + w].astype(float)
    assert np.abs(comp - truth).mean() < 8
    assert ((comp[..., 0] > 150) & (comp[..., 1] < 90)).sum() == 0
    line_col = 300 - c.x + 1
    assert comp[h // 2, line_col].min() > 190  # the white line runs through the gap
    # The cut-out image holds the player with an alpha mask.
    assert c.image[h // 2, w // 2, 3] == 255 and tuple(c.image[h // 2, w // 2, :3]) == (220, 40, 40)
    assert c.image[0, 0, 3] == 0


def test_cutout_rejects_tiny():
    assert make_cutout(striped_pitch(), [(10, 10), (11, 10), (10, 11)]) is None


# ------------------------------------------------------------------ model
def cutout_annotation():
    a = Annotation(Kind.CUTOUT, [(10, 10), (30, 10), (30, 50), (10, 50)], 0, 10, layer="after")
    a.source_points = list(a.points)
    a.crop = [4, 4, 32, 52]
    a.image = "aGVsbG8="
    a.fill_image = "aGVsbG8="
    return a


def test_cutout_transform_follows_edits():
    a = cutout_annotation()
    a.translate(100, 20)
    assert a.source_transform() == pytest.approx((1, 1, 100, 20))
    a.scale(2, 2, (110, 30))
    kx, ky, dx, dy = a.source_transform()
    assert (kx, ky) == pytest.approx((2, 2))
    assert a.hit((130, 60), 1) and not a.hit((20, 20), 1)


def test_project_roundtrip_with_boards_and_cutouts(tmp_path):
    p = Project(name="M2")
    p.annotations.append(cutout_annotation())
    b = Board(name="Press shape", linked_frame=42)
    b.annotations.append(Annotation(Kind.TOKEN, [(100, 100)], 0, 10 ** 9, text="8", name="Rice"))
    b.annotations.append(Annotation(Kind.MEASURE, [(0, 0), (100, 0)], 0, 10 ** 9))
    p.boards.append(b)
    path = str(tmp_path / "m2.fc27studio")
    storage.save_project(p, path)
    q = storage.load_project(path)
    assert q.to_dict() == p.to_dict()
    assert q.boards[0].annotations[0].name == "Rice"
    assert q.annotations[0].source_points == p.annotations[0].source_points


def test_non_cutout_files_stay_small():
    d = Annotation(Kind.ARROW, [(0, 0), (1, 1)], 0, 1).to_dict()
    assert "image" not in d and "fill_image" not in d


def test_scoped_document_shares_undo():
    doc = Document()
    board = Board()
    doc.add_board(board)
    sdoc = ScopedDocument(doc, board)
    sdoc.add_annotation(Annotation(Kind.TOKEN, [(5, 5)], 0, 10 ** 9, text="1"))
    assert len(board.annotations) == 1 and doc.project.annotations == []
    events = []
    doc.listeners.append(events.append)
    doc.undo.undo()
    assert board.annotations == [] and "board" in events
    doc.undo.undo()
    assert doc.project.boards == []
    doc.undo.redo()
    doc.undo.redo()
    assert doc.project.boards == [board] and len(board.annotations) == 1


def test_formations_are_on_the_pitch():
    from fc27studio.ui.board import FORMATIONS, NUMBERS, PITCH_L, PITCH_W
    for name, pos in FORMATIONS.items():
        assert len(pos) == 11 and len(NUMBERS[name]) == 11 and len(set(NUMBERS[name])) == 11
        assert all(0 < x < PITCH_L / 2 + 5 and 0 < y < PITCH_W for x, y in pos), name


# -------------------------------------------------------------------- GUI
def pump(app, t=0.1):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def win(qapp, monkeypatch, bframe_video):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    from fc27studio.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.open_video(bframe_video)
    assert wait_for(qapp, lambda: w.canvas.image is not None)
    w.player.seek(40)
    assert wait_for(qapp, lambda: w.canvas.frame == 40)
    yield w
    w.doc.mark_clean()
    w.close()


def lasso(cv, poly):
    pts = [cv.to_widget(QPointF(*p)).toPoint() for p in poly]
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, pts[0])
    for a, b in zip(pts, pts[1:] + pts[:1]):
        for i in range(1, 5):
            q = a + (b - a) * i / 5
            QTest.mouseMove(cv, QPoint(int(q.x()), int(q.y())))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, pts[0])


def drag(cv, a, b):
    pa, pb = cv.to_widget(QPointF(*a)).toPoint(), cv.to_widget(QPointF(*b)).toPoint()
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, pa)
    for i in range(1, 6):
        q = pa + (pb - pa) * i / 6
        QTest.mouseMove(cv, QPoint(int(q.x()), int(q.y())))
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, pb)


def make_cut(qapp, win):
    win.choose_tool(Kind.CUTOUT)
    lasso(win.canvas, [(60, 50), (110, 50), (110, 130), (60, 130)])
    pump(qapp)
    cuts = [a for a in win.doc.project.annotations if a.kind == Kind.CUTOUT]
    assert len(cuts) == 1
    return cuts[0]


def test_cutout_lasso_move_ghost_and_reset(qapp, win):
    c = make_cut(qapp, win)
    assert c.layer == "after" and c.start_frame == 40 and win.canvas.tool == "select"
    assert c.id in win.canvas.selection
    drag(win.canvas, (85, 90), (185, 70))
    cx, cy = c.center()
    assert cx > 150
    assert win.properties.cut_box.isVisibleTo(win.properties)
    win.properties.ghost_s.setValue(0)
    assert c.ghost_opacity == 0
    win.properties._reset_cutout()
    assert c.points == c.source_points
    win.doc.undo.undo()
    assert c.points != c.source_points


def test_cutout_by_clicking_points(qapp, win):
    win.choose_tool(Kind.CUTOUT)
    cv = win.canvas
    cv.segmenter_provider = lambda: None   # without the AI model, clicks place outline points
    for p in ((60, 50), (110, 50), (110, 130), (60, 130)):
        QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, cv.to_widget(QPointF(*p)).toPoint())
    QTest.keyClick(cv, Qt.Key_Return)
    assert [a.kind for a in win.doc.project.annotations] == [Kind.CUTOUT]


def test_before_after_layers_and_modes(qapp, win):
    c = make_cut(qapp, win)
    cv = win.canvas
    assert c in cv.visible_annotations("after")
    assert c not in cv.visible_annotations("before")
    for mode in ("split", "side", "off"):
        cv.set_compare(mode)
        pump(qapp, 0.05)
        assert win.compare_combo.currentData() == mode
    win.actions["view.compare"].trigger()
    assert cv.compare == "split"


def test_compare_export(qapp, win):
    from fc27studio.ui.export import compose_compare
    from fc27studio.ui.render import ndarray_to_qimage
    make_cut(qapp, win)
    frame = ndarray_to_qimage(win._source_for_export().frame(40))
    anns = win.doc.annotations_at(40)
    side = compose_compare(frame, anns, "side")
    assert side.height() == 180 and side.width() > 640
    split = compose_compare(frame, anns, "split", 0.5)
    assert (split.width(), split.height()) == (320, 180)


def test_board_workflow(qapp, win, tmp_path):
    win.set_workspace("board")
    win.new_board()
    board = win.current_board()
    tokens = [a for a in board.annotations if a.kind == Kind.TOKEN]
    assert len(tokens) == 22 and board.linked_frame == 40
    win.place_formation(True, "3-5-2")
    assert len([a for a in board.annotations if a.kind == Kind.TOKEN]) == 22
    win.doc.undo.undo()  # the whole formation change is one undo step
    assert len([a for a in board.annotations if a.kind == Kind.TOKEN]) == 22
    win.set_team_color(False, "#00ff00")
    assert board.away_color == "#00ff00"
    assert len([a for a in board.annotations if a.color == "#00ff00"]) == 11
    win.clear_team(True)
    assert len([a for a in board.annotations if a.kind == Kind.TOKEN]) == 11
    # Tools on the board
    from fc27studio.ui.board import to_px
    bc = win.board_canvas
    win.choose_tool(Kind.MEASURE)
    drag(bc, to_px(0, 34), to_px(52.5, 34))
    m = [a for a in board.annotations if a.kind == Kind.MEASURE][0]
    from fc27studio.ui.render import measure_text
    assert abs(float(measure_text(m, bc.px_per_m).split()[0]) - 52.5) < 0.3
    win.choose_tool(Kind.TOKEN)
    QTest.mouseClick(bc, Qt.LeftButton, Qt.NoModifier, bc.to_widget(QPointF(*to_px(30, 30))).toPoint())
    new = [a for a in board.annotations if a.kind == Kind.TOKEN][-1]
    assert new.text  # numbered automatically
    win.choose_tool(Kind.BALL)
    QTest.mouseClick(bc, Qt.LeftButton, Qt.NoModifier, bc.to_widget(QPointF(*to_px(40, 30))).toPoint())
    assert any(a.kind == Kind.BALL for a in board.annotations)
    # Video-only tools are not offered on the board
    win.choose_tool(Kind.SPOTLIGHT)
    assert win.workspace == "tactics"
    # Persist
    win.set_workspace("board")
    path = str(tmp_path / "b.fc27studio")
    assert win._save_to(path)
    q = storage.load_project(path)
    assert len(q.boards) == 1 and len(q.boards[0].annotations) == len(board.annotations)
    # Export the board image
    from fc27studio.ui.render import render_frame
    img = render_frame(win._pitch, board.annotations, bc.px_per_m)
    assert img.width() == win._pitch.width()


def test_board_duplicate_rename_delete(qapp, win, monkeypatch):
    win.new_board()
    first = win.current_board()
    win.duplicate_board()
    assert len(win.doc.project.boards) == 2
    dup = win.current_board()
    assert dup.id != first.id and {a.id for a in dup.annotations}.isdisjoint({a.id for a in first.annotations})
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Counter-press", True)))
    win.rename_board()
    assert dup.name == "Counter-press"
    win.delete_board()
    assert win.doc.project.boards == [first] and win.current_board_id == first.id


def test_board_edits_do_not_touch_video_drawings(qapp, win):
    win.new_board()
    win.choose_tool(Kind.ARROW)
    drag(win.board_canvas, (100, 100), (300, 200))
    assert win.doc.project.annotations == []
    assert any(a.kind == Kind.ARROW for a in win.current_board().annotations)
    win.actions["edit.select_all"].trigger()
    assert len(win.board_canvas.selection) == len(win.current_board().annotations)
    win.actions["edit.delete"].trigger()
    assert win.current_board().annotations == []
    win.actions["edit.undo"].trigger()
    assert len(win.current_board().annotations) > 0


def test_352_has_two_dms_and_a_cam():
    from fc27studio.ui.board import FORMATIONS
    mids = sorted(FORMATIONS["3-5-2"][5:8])           # the three central midfielders
    dms = [p for p in mids if p[0] < 35]
    cam = [p for p in mids if p[0] > 40]
    assert len(dms) == 2 and len(cam) == 1 and abs(cam[0][1] - 34) < 1


def test_delete_key_on_board(qapp, win):
    from fc27studio.ui.board import to_px
    win.new_board()
    board = win.current_board()
    bc = win.board_canvas
    win.choose_tool(Kind.BALL)
    at = bc.to_widget(QPointF(*to_px(60, 20))).toPoint()
    QTest.mouseClick(bc, Qt.LeftButton, Qt.NoModifier, at)
    n = len(board.annotations)
    bc.set_selection(set())
    # Ball tool still active: clicking the ball selects it instead of adding another.
    QTest.mouseClick(bc, Qt.LeftButton, Qt.NoModifier, at)
    assert len(board.annotations) == n and len(bc.selection) == 1
    QTest.keyClick(bc, Qt.Key_Delete)
    assert not any(a.kind == Kind.BALL for a in board.annotations)
    # A token selected with the select tool is deleted by the Delete key too.
    win.choose_tool("select")
    tok = next(a for a in board.annotations if a.kind == Kind.TOKEN)
    QTest.mouseClick(bc, Qt.LeftButton, Qt.NoModifier, bc.to_widget(QPointF(*tok.points[0])).toPoint())
    QTest.keyClick(bc, Qt.Key_Delete)
    assert tok not in board.annotations
