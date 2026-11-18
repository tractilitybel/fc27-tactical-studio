"""End-to-end tests of the real main window (runs offscreen)."""
import os
import time

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from conftest import decode_frame_number
from fc27studio.core.model import Kind


def pump(app, t=0.2):
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
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    from fc27studio.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.open_video(bframe_video)
    assert wait_for(qapp, lambda: w.canvas.image is not None)
    yield w
    w.doc.mark_clean()
    w.close()


def drag(app, w, tool, a, b, mods=Qt.NoModifier):
    cv = w.canvas
    w.choose_tool(tool)
    pa = cv.to_widget(QPointF(*a)).toPoint()
    pb = cv.to_widget(QPointF(*b)).toPoint()
    QTest.mousePress(cv, Qt.LeftButton, mods, pa)
    for i in range(1, 6):
        p = pa + (pb - pa) * i / 6
        QTest.mouseMove(cv, QPoint(int(p.x()), int(p.y())))
    QTest.mouseMove(cv, pb)
    QTest.mouseRelease(cv, Qt.LeftButton, mods, pb)
    pump(app, 0.02)


def shown_frame(w):
    img = w.canvas.image.convertToFormat(QImage.Format_RGB888)
    import numpy as np
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return decode_frame_number(arr[:, :img.width() * 3].reshape(img.height(), img.width(), 3))


def test_seek_shows_exact_frame(qapp, win):
    for f in (0, 77, 31, 149, 60, 59):
        win.player.seek(f)
        assert wait_for(qapp, lambda: win.canvas.frame == f)
        assert shown_frame(win) == f


def test_step_keys(qapp, win):
    win.player.seek(10)
    wait_for(qapp, lambda: win.canvas.frame == 10)
    win.canvas.setFocus()
    QTest.keyClick(win, Qt.Key_Period)
    assert wait_for(qapp, lambda: win.canvas.frame == 11)
    QTest.keyClick(win, Qt.Key_Comma)
    QTest.keyClick(win, Qt.Key_Comma)
    assert wait_for(qapp, lambda: win.canvas.frame == 9)
    assert shown_frame(win) == 9
    # Left/Right jump one second (30 frames at 30 fps)
    QTest.keyClick(win, Qt.Key_Right)
    assert wait_for(qapp, lambda: win.canvas.frame == 39)
    QTest.keyClick(win, Qt.Key_Left)
    assert wait_for(qapp, lambda: win.canvas.frame == 9)
    assert shown_frame(win) == 9


def test_jkl(qapp, win):
    win.player.seek(50)
    win.player.jkl("l")
    assert win.player.playing and win.player.direction == 1 and win.player.speed == 1.0
    win.player.jkl("l")
    assert win.player.speed == 2.0
    win.player.jkl("k")
    assert not win.player.playing
    win.player.jkl("j")
    assert win.player.playing and win.player.direction == -1
    pump(qapp, 0.3)
    win.player.pause()
    assert win.player.current < 50


def test_draw_all_tools_and_undo(qapp, win):
    win.set_workspace("tactics")
    win.player.seek(30)
    wait_for(qapp, lambda: win.canvas.frame == 30)
    for kind in (Kind.ARROW, Kind.CURVED_ARROW, Kind.RUN, Kind.LINE, Kind.FREEHAND,
                 Kind.CIRCLE, Kind.RECT, Kind.SPOTLIGHT):
        drag(qapp, win, kind, (40, 40), (150, 120))
    win.choose_tool(Kind.ZONE)
    for p in ((20, 20), (100, 20), (60, 90)):
        QTest.mouseClick(win.canvas, Qt.LeftButton, Qt.NoModifier, win.canvas.to_widget(QPointF(*p)).toPoint())
    QTest.keyClick(win.canvas, Qt.Key_Return)
    win.canvas.on_text_request = lambda init: "Hello"
    win.choose_tool(Kind.TEXT)
    QTest.mouseClick(win.canvas, Qt.LeftButton, Qt.NoModifier, win.canvas.to_widget(QPointF(200, 100)).toPoint())
    kinds = sorted(a.kind for a in win.doc.project.annotations)
    assert kinds == sorted(Kind.ALL)
    for a in win.doc.project.annotations:
        assert a.start_frame == 30
    for _ in Kind.ALL:
        win.doc.undo.undo()
    assert win.doc.project.annotations == []


def test_move_resize_rotate(qapp, win):
    win.set_workspace("tactics")
    drag(qapp, win, Kind.RECT, (100, 50), (200, 130))
    a = win.doc.project.annotations[0]
    cv = win.canvas
    win.choose_tool("select")
    # Move
    drag(qapp, win, "select", (150, 90), (170, 100))
    assert a.points[0] == pytest.approx((120, 60), abs=1.5)
    # Resize from bottom-right handle
    handles = dict(cv._handles())
    br = handles["h:4"]
    target = cv.to_widget(QPointF(240, 170))
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, br.toPoint())
    QTest.mouseMove(cv, target.toPoint())
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, target.toPoint())
    x0, y0, x1, y1 = a.bbox()
    assert (x0, y0) == pytest.approx((120, 60), abs=1.5)
    assert (x1, y1) == pytest.approx((240, 170), abs=3)
    # Rotate with the rotation handle
    rot = dict(cv._handles())["rot"]
    c = cv.to_widget(QPointF(*a.center()))
    right = QPoint(int(c.x() + 100), int(c.y()))
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, rot.toPoint())
    QTest.mouseMove(cv, right)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, right)
    assert abs(a.rotation - 90) < 3
    win.doc.undo.undo()
    assert a.rotation == 0


def test_copy_paste_group(qapp, win):
    win.set_workspace("tactics")
    drag(qapp, win, Kind.ARROW, (10, 10), (100, 50))
    drag(qapp, win, Kind.CIRCLE, (150, 60), (200, 110))
    win.canvas.select_all()
    win.actions["edit.group"].trigger()
    gid = win.doc.project.annotations[0].group_id
    assert gid and all(a.group_id == gid for a in win.doc.project.annotations)
    win.actions["edit.copy"].trigger()
    win.player.seek(100)
    wait_for(qapp, lambda: win.canvas.frame == 100)
    win.actions["edit.paste"].trigger()
    anns = win.doc.project.annotations
    assert len(anns) == 4
    pasted = [a for a in anns if a.start_frame == 100]
    assert len(pasted) == 2 and pasted[0].group_id == pasted[1].group_id != gid
    win.actions["edit.delete"].trigger()
    assert len(win.doc.project.annotations) == 2


def test_bookmarks_navigation(qapp, win):
    for f in (20, 80, 140):
        win.player.seek(f)
        win.quick_bookmark()
    assert len(win.doc.project.bookmarks) == 3
    assert len(win.bookmarks.list.selectedItems()) == 1
    win.player.seek(0)
    win.jump_bookmark(1)
    assert win.player.current == 20
    win.jump_bookmark(1)
    assert win.player.current == 80
    win.jump_bookmark(-1)
    assert win.player.current == 20
    b = win.doc.project.sorted_bookmarks()[1]
    win.doc.update_bookmark(b.id, title="Pressing trap", category="Pressing", players=["Rice"])
    win.bookmarks.search.setText("rice")
    assert win.bookmarks.list.count() == 1
    win.bookmarks.search.setText("")
    win.bookmarks.player_filter.setCurrentText("Rice")
    assert win.bookmarks.list.count() == 1


def test_ab_loop(qapp, win):
    win.player.seek(30)
    win.set_loop_a()
    win.player.seek(45)
    win.set_loop_b()
    assert win.player.loop_enabled
    win.player.seek(30)
    win.player.play()
    pump(qapp, 1.2)
    win.player.pause()
    assert 30 <= win.player.current <= 45


def test_telestrator_temporary(qapp, win):
    win.set_workspace("present")
    drag(qapp, win, Kind.FREEHAND, (10, 10), (200, 150))
    assert len(win.canvas.temp) == 1 and win.doc.project.annotations == []
    win.player.play()
    pump(qapp, 0.1)
    win.player.pause()
    assert win.canvas.temp == []
    win.telestrator.keep.setChecked(True)
    drag(qapp, win, Kind.ARROW, (10, 10), (200, 150))
    assert len(win.doc.project.annotations) == 1


def test_save_reopen_and_relink(qapp, win, tmp_path, bframe_video):
    import shutil
    win.set_workspace("tactics")
    drag(qapp, win, Kind.ARROW, (10, 10), (100, 50))
    win.player.seek(70)
    win.quick_bookmark()
    vid = tmp_path / "match.mp4"
    shutil.copy(bframe_video, vid)
    assert win.load_video(str(vid))
    win.doc.project.video.path = str(vid)
    win.player.seek(70)
    proj = str(tmp_path / "game.fc27studio")
    assert win._save_to(proj)
    assert not win.doc.dirty
    # Close it (Windows cannot move open files), then move project + video
    # together: the video should be found next to the project.
    win.new_project()
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.move(proj, moved / "game.fc27studio")
    shutil.move(str(vid), moved / "match.mp4")
    assert win.open_project(str(moved / "game.fc27studio"))
    assert win.player.is_open
    assert len(win.doc.project.annotations) == 1 and len(win.doc.project.bookmarks) == 1
    assert wait_for(qapp, lambda: win.canvas.frame == 70)


def test_export_frame_full_resolution(qapp, win, tmp_path):
    win.set_workspace("tactics")
    drag(qapp, win, Kind.RECT, (10, 10), (100, 100))
    path = str(tmp_path / "f.png")
    from fc27studio.ui.export import export_frame
    export_frame(win._source_for_export(), 0, win.doc.project.annotations_at(0), path)
    img = QImage(path)
    assert (img.width(), img.height()) == (320, 180)
    clean = str(tmp_path / "c.jpg")
    export_frame(win._source_for_export(), 0, [], clean, with_drawings=False)
    assert QImage(clean).width() == 320
    # Drawing changes pixels relative to the clean frame.
    assert QImage(path).pixel(10, 50) != QImage(clean).pixel(10, 50)


def test_autosave_and_recovery(qapp, win):
    win.set_workspace("tactics")
    drag(qapp, win, Kind.ARROW, (10, 10), (100, 50))
    win.prefs.autosave_seconds = 1
    win._last_autosave = 0
    win._autosave()
    entries = win.recovery.entries()
    assert entries and entries[0].name == win.doc.project.name
    proj = win.recovery.load(entries[0])
    assert len(proj.annotations) == 1
    win.recovery.discard(win.doc.project)


def test_shortcut_conflicts():
    from fc27studio.ui.settings import SHORTCUTS, find_conflicts
    defaults = {k: v[2] for k, v in SHORTCUTS.items()}
    assert find_conflicts(defaults) == {}
    defaults["tool.arrow"] = "Space"
    assert "space" in find_conflicts(defaults)


def test_parse_goto():
    from fc27studio.ui.dialogs import parse_time_or_frame
    assert parse_time_or_frame("1234", 30) == 1234
    assert parse_time_or_frame("01:00", 30) == 1800
    assert parse_time_or_frame("1:00:00", 25) == 90000
    assert parse_time_or_frame("00:10.15", 30) == 315
    assert parse_time_or_frame("2.5s", 60) == 150
    assert parse_time_or_frame("abc", 30) is None


def test_up_down_jump(qapp, win):
    assert win.prefs.jump_seconds == 5.0
    assert win.actions["play.jump_forward"].shortcut().toString() == "Up"
    assert win.actions["play.jump_back"].shortcut().toString() == "Down"
    win.prefs.jump_seconds = 1.0          # test video is only 5 s long (30 fps)
    win.player.seek(10)
    wait_for(qapp, lambda: win.canvas.frame == 10)
    win.canvas.setFocus()
    QTest.keyClick(win, Qt.Key_Up)
    assert wait_for(qapp, lambda: win.canvas.frame == 40)
    assert shown_frame(win) == 40
    QTest.keyClick(win, Qt.Key_Down)
    QTest.keyClick(win, Qt.Key_Down)
    assert wait_for(qapp, lambda: win.canvas.frame == 0)   # clamped at the start
    win.prefs.jump_seconds = 5.0
    QTest.keyClick(win, Qt.Key_Up)
    assert wait_for(qapp, lambda: win.player.current == 149)  # clamped at the end


def test_timeline_click_jumps_to_drawing_start(qapp, win):
    win.set_workspace("tactics")
    win.player.seek(60)
    wait_for(qapp, lambda: win.canvas.frame == 60)
    drag(qapp, win, Kind.RECT, (20, 20), (90, 90))
    rect = win.doc.project.annotations[0]
    win.player.seek(120)
    wait_for(qapp, lambda: win.canvas.frame == 120)
    tl = win.timeline
    sp = next(s for s in tl.ranges if s.id == rect.id)
    r = tl._span_rect(sp, tl.RULER_H + tl.MARK_H + 2)
    QTest.mouseClick(tl, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.left() + 3), int(r.center().y())))
    assert wait_for(qapp, lambda: win.canvas.frame == 60)
    assert wait_for(qapp, lambda: win.canvas.selection == {rect.id})


def test_board_marker_on_timeline(qapp, win):
    win.player.seek(90)
    win.new_board()
    board = win.current_board()
    assert any(m.kind == "board" and m.id == board.id and m.frame == 90 for m in win.timeline.markers)
    win.set_workspace("tactics")
    win._on_marker_double_clicked(board.id)
    assert win.workspace == "board"


def test_loop_box_shows_range(qapp, win):
    tr = win.transport
    assert not tr.loop_range.isVisibleTo(tr) and tr.btn_loop.text() == "Off"
    win.player.seek(30)
    win.set_loop_a()
    win.player.seek(90)
    win.set_loop_b()
    qapp.processEvents()
    assert tr.btn_loop.text() == "On"
    assert tr.loop_range.text() == "00:01 – 00:03"


def test_navigation_keys_reset_speed(qapp, win):
    for key in (Qt.Key_Period, Qt.Key_Comma, Qt.Key_Right, Qt.Key_Left, Qt.Key_Up, Qt.Key_Down):
        win.player.set_speed(2.0)
        win.canvas.setFocus()
        QTest.keyClick(win, key)
        assert win.player.speed == 1.0, key
    win.player.set_speed(0.5)
    win.player.play()
    QTest.keyClick(win, Qt.Key_Right)
    assert win.player.playing and win.player.speed == 1.0
    win.player.pause()
