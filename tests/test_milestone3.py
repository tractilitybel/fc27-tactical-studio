"""Milestone 3: freeze frames, animated drawings, video export, commentary."""
import time

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from fc27studio.core import storage
from fc27studio.core.animation import ease, interpolate, posed, reveal_progress, set_keyframe
from fc27studio.core.document import Document
from fc27studio.core.model import Annotation, Commentary, Freeze, Kind, Project


# ------------------------------------------------------------------ core
def test_roundtrip_new_fields(tmp_path):
    p = Project()
    a = Annotation(Kind.SPOTLIGHT, [(0, 0), (10, 10)], 0, 100, reveal_frames=12)
    set_keyframe(a, 0)
    a.points = [(50, 0), (60, 10)]
    set_keyframe(a, 60)
    p.annotations.append(a)
    p.freezes.append(Freeze(40, 2.5, "gap"))
    p.commentary.append(Commentary(10, "voice/clip1.wav", 3.2))
    path = str(tmp_path / "m3.fc27studio")
    storage.save_project(p, path)
    q = storage.load_project(path)
    assert q.to_dict() == p.to_dict()
    assert q.annotations[0].keyframes[1]["points"][0] == (50, 0)


def test_interpolation_and_easing():
    a = Annotation(Kind.CIRCLE, [(0, 0), (10, 10)], 0, 100)
    set_keyframe(a, 10)
    a.points = [(100, 0), (110, 10)]
    a.rotation = 90
    set_keyframe(a, 30)
    assert interpolate(a, 0)[0][0] == (0, 0)          # holds before the first keyframe
    assert interpolate(a, 99)[0][0] == (100, 0)       # and after the last
    pts, rot = interpolate(a, 20)
    assert pts[0] == pytest.approx((50, 0)) and rot == pytest.approx(45)
    assert interpolate(a, 12)[0][0][0] < 10 * 2 / 20 * 100  # ease-in: slower at the start
    assert ease(0) == 0 and ease(1) == 1 and ease(0.5) == 0.5


def test_rotation_takes_short_way():
    a = Annotation(Kind.RECT, [(0, 0), (1, 1)], 0, 10, rotation=170)
    set_keyframe(a, 0)
    a.rotation = -170
    set_keyframe(a, 10)
    assert abs(interpolate(a, 5)[1]) == pytest.approx(180)


def test_reveal():
    a = Annotation(Kind.ARROW, [(0, 0), (100, 0)], 10, 100, reveal_frames=20)
    assert reveal_progress(a, 10) < 0.05
    mid = posed(a, 20)
    assert 30 < mid.points[-1][0] < 70
    assert posed(a, 40).points[-1] == (100, 0)
    assert a.points[-1] == (100, 0)                   # the original is untouched
    c = Annotation(Kind.CIRCLE, [(0, 0), (10, 10)], 0, 100, reveal_frames=10, opacity=0.8)
    assert posed(c, 0).opacity < 0.2 and posed(c, 20).opacity == 0.8


def test_editing_animated_drawing_sets_keyframe():
    d = Document()
    a = Annotation(Kind.RECT, [(0, 0), (10, 10)], 0, 100)
    set_keyframe(a, 0)
    d.add_annotation(a)
    d.keyframe_frame = 50
    d.modify_annotations([a.id], lambda x: x.translate(40, 0), "Move")
    assert [k["frame"] for k in a.keyframes] == [0, 50]
    d.undo.undo()
    assert [k["frame"] for k in a.keyframes] == [0]


def test_freeze_document_ops():
    d = Document()
    f = Freeze(30, 3.0)
    d.add_freeze(f)
    d.update_freeze(f.id, seconds=5.0)
    assert d.project.freezes[0].seconds == 5.0
    d.remove_freeze(f.id)
    assert d.project.freezes == []
    d.undo.undo()
    d.undo.undo()
    assert d.project.freezes[0].seconds == 3.0


# ---------------------------------------------------------------- player
def pump(app, t):
    end = time.time() + t
    while time.time() < end:
        app.processEvents()
        time.sleep(0.003)


def wait_for(app, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.003)
    return False


def test_player_holds_on_freeze(qapp, bframe_video):
    from fc27studio.ui.playback import Player
    pl = Player()
    pl.open(bframe_video)
    pl.freezes = {15: 0.6}
    events = []
    pl.freezeChanged.connect(events.append)
    pl.seek(0)
    pl.play()
    assert wait_for(qapp, lambda: pl.holding, 2)
    assert pl.current == 15
    pump(qapp, 0.35)
    assert pl.holding and pl.current == 15            # still frozen
    assert wait_for(qapp, lambda: not pl.holding, 2)
    assert wait_for(qapp, lambda: pl.current > 20, 2)  # continued
    assert events[-1] == 0.0 or any(e == 0.0 for e in events)
    # A freeze triggers once per pass; seeking back re-arms it.
    pl.seek(10)
    assert wait_for(qapp, lambda: pl.holding, 2)
    pl.skip_freeze()
    assert not pl.holding
    pl.freezes_enabled = False
    pl.seek(10)
    pump(qapp, 0.4)
    assert not pl.holding and pl.current > 15
    pl.pause()
    pl.close()


# -------------------------------------------------------------------- GUI
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


def drag(cv, a, b):
    pa, pb = cv.to_widget(QPointF(*a)).toPoint(), cv.to_widget(QPointF(*b)).toPoint()
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, pa)
    for i in range(1, 6):
        q = pa + (pb - pa) * i / 6
        QTest.mouseMove(cv, QPoint(int(q.x()), int(q.y())))
    QTest.mouseMove(cv, pb)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, pb)


def test_freeze_key_marker_and_space_continues(qapp, win):
    win.player.seek(30)
    wait_for(qapp, lambda: win.canvas.frame == 30)
    win.canvas.setFocus()
    QTest.keyClick(win, Qt.Key_F)
    assert [f.frame for f in win.doc.project.freezes] == [30]
    assert any(m.kind == "freeze" and m.frame == 30 for m in win.timeline.markers)
    assert win.player.freezes == {30: win.prefs.freeze_seconds}
    win.player.seek(20)
    win.player.play()
    assert wait_for(qapp, lambda: win.player.holding, 2)
    assert win.canvas.freeze_badge
    win.toggle_play()                                  # Space: carry on
    assert not win.player.holding and win.player.playing
    win.player.pause()
    win.doc.undo.undo()
    assert win.player.freezes == {}


def test_keyframe_workflow(qapp, win):
    win.set_workspace("tactics")
    win.player.seek(30)
    wait_for(qapp, lambda: win.canvas.frame == 30)
    win.choose_tool(Kind.RECT)
    drag(win.canvas, (20, 20), (60, 60))
    r = win.doc.project.annotations[0]
    win.choose_tool("select")
    win.canvas.set_selection({r.id})
    win.properties._add_keyframe()
    assert [k["frame"] for k in r.keyframes] == [30]
    win.player.seek(70)
    wait_for(qapp, lambda: win.canvas.frame == 70)
    drag(win.canvas, (40, 40), (140, 40))             # move it 100 px right at frame 70
    assert [k["frame"] for k in r.keyframes] == [30, 70]
    win.player.seek(50)
    wait_for(qapp, lambda: win.canvas.frame == 50)
    assert 60 < r.points[0][0] < 80                   # halfway (eased) between 20 and 120
    assert any(s.keyframes == (30, 70) for s in win.timeline.ranges)
    win.doc.undo.undo()
    assert [k["frame"] for k in r.keyframes] == [30]


def test_reveal_setting_from_panel(qapp, win):
    win.set_workspace("tactics")
    win.choose_tool(Kind.ARROW)
    drag(win.canvas, (10, 10), (200, 100))
    a = win.doc.project.annotations[0]
    win.properties.reveal_s.setValue(1.0)
    assert a.reveal_frames == 30


# ------------------------------------------------------------------ export
from conftest import decode_frame_number  # noqa: E402


@pytest.fixture(scope="module")
def av_video(tmp_path_factory):
    from test_audio import make_av_video
    return make_av_video(str(tmp_path_factory.mktemp("m3") / "src.mp4"), seconds=4)


def read_video(path):
    import av
    c = av.open(path)
    frames = [f.to_ndarray(format="rgb24") for f in c.decode(c.streams.video[0])]
    c.close()
    c = av.open(path)
    audio = np.concatenate([f.to_ndarray().T.reshape(-1, 2) if f.format.is_packed else f.to_ndarray().T
                            for f in c.decode(c.streams.audio[0])]) if c.streams.audio else None
    c.close()
    return frames, audio


def dominant(x, rate=48000):
    f = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.argmax(f) * rate / len(x)


def test_export_section_with_drawing_freeze_and_sound(qapp, av_video, tmp_path):
    from fc27studio.core.video import build_index
    from fc27studio.ui.video_export import ExportOptions, Segment, VideoExporter
    p = Project()
    p.freezes.append(Freeze(45, 1.0))
    p.annotations.append(Annotation(Kind.RECT, [(200, 20), (300, 160)], 30, 60, color="#ff0000",
                                    fill_opacity=0.6))
    out = str(tmp_path / "sec.mp4")
    VideoExporter(p, av_video, build_index(av_video), ExportOptions(out)).run([Segment(30, 59)])
    frames, audio = read_video(out)
    assert len(frames) == 30 + 30
    nums = [decode_frame_number(f) for f in frames]
    assert nums[:16] == list(range(30, 46)) and nums[16:46] == [45] * 30 and nums[46] == 46
    assert frames[0][90, 250, 0] > 150                 # red drawing burned in
    assert abs(len(audio) / 48000 - 2.0) < 0.05        # sound as long as the video
    # silence during the freeze, game sound before it
    assert np.abs(audio[int(0.6 * 48000):int(1.4 * 48000)]).max() < 0.02
    assert np.abs(audio[int(0.1 * 48000):int(0.4 * 48000)]).max() > 0.2


def test_export_reel_with_title_cards(qapp, av_video, tmp_path):
    from fc27studio.core.model import Bookmark
    from fc27studio.core.video import build_index
    from fc27studio.ui.video_export import ExportOptions, VideoExporter, bookmark_segments
    bms = [Bookmark(30, "Press", "Pressing", end_frame=44), Bookmark(90, "Goal", "Goal")]
    segs = bookmark_segments(bms, 30.0, 120, before=0.5, after=0.5)
    assert [(s.start, s.end) for s in segs] == [(30, 44), (75, 105)]
    out = str(tmp_path / "reel.mp4")
    VideoExporter(Project(), av_video, build_index(av_video),
                  ExportOptions(out, title_seconds=1.0, sound=False)).run(segs)
    frames, audio = read_video(out)
    assert audio is None
    assert len(frames) == 30 + 15 + 30 + 31
    assert decode_frame_number(frames[30]) == 30 and decode_frame_number(frames[75]) == 75


def test_export_downscale(qapp, av_video, tmp_path):
    from fc27studio.core.video import build_index
    from fc27studio.ui.video_export import ExportOptions, Segment, VideoExporter
    out = str(tmp_path / "small.mp4")
    VideoExporter(Project(), av_video, build_index(av_video),
                  ExportOptions(out, height=90, sound=False)).run([Segment(0, 9)])
    frames, _ = read_video(out)
    assert frames[0].shape[:2] == (90, 160)


# --------------------------------------------------------------- commentary
def test_mixer_ducks_and_mixes(tmp_path):
    from fc27studio.core.commentary import DUCK_LEVEL, CommentaryMixer, read_wav, write_wav
    voice = (0.5 * np.sin(2 * np.pi * 220 * np.arange(48000) / 48000)).astype(np.float32)
    write_wav(str(tmp_path / "v.wav"), voice)
    assert np.allclose(read_wav(str(tmp_path / "v.wav")), voice, atol=1e-3)
    p = Project()
    p.commentary.append(Commentary(30, "v.wav", 1.0))
    mix = CommentaryMixer(p, str(tmp_path), 30.0)
    game = np.full((48000 * 3, 2), 0.2, np.float32)
    out = mix(0.0, game)
    assert abs(out[24000, 0] - 0.2) < 1e-6                      # before: untouched
    mid = out[48000 + 24000 - 200:48000 + 24000 + 200, 0] - 0.2 * DUCK_LEVEL
    assert abs(dominant(mid) - 220) < 130 or np.abs(mid).max() > 0.3   # voice present
    assert abs(out[int(1.5 * 48000) + 1, 0] - (0.2 * DUCK_LEVEL + voice[24001] * 1.0)) < 1e-4
    assert abs(out[int(2.9 * 48000), 0] - 0.2) < 1e-6           # after: back to normal
    p.duck_game_audio = False
    assert abs(mix(0.0, game)[int(1.5 * 48000) + 1, 0] - (0.2 + voice[24001])) < 1e-4


def test_commentary_in_export(qapp, av_video, tmp_path):
    from fc27studio.core.commentary import CommentaryMixer, write_wav
    from fc27studio.core.video import build_index
    from fc27studio.ui.video_export import ExportOptions, Segment, VideoExporter
    voice = (0.5 * np.sin(2 * np.pi * 1500 * np.arange(48000) / 48000)).astype(np.float32)
    write_wav(str(tmp_path / "c.wav"), voice)
    p = Project()
    p.commentary.append(Commentary(30, "c.wav", 1.0))
    out = str(tmp_path / "comm.mp4")
    VideoExporter(p, av_video, build_index(av_video), ExportOptions(out),
                  CommentaryMixer(p, str(tmp_path), 30.0)).run([Segment(0, 89)])
    _, audio = read_video(out)
    during = audio[int(1.3 * 48000):int(1.6 * 48000), 0]
    after = audio[int(2.4 * 48000):int(2.7 * 48000), 0]
    assert abs(dominant(during) - 1500) < 40          # the voice is in the export
    assert abs(dominant(after) - 500) < 40            # game tone (2 s → 500 Hz) after it


def test_record_commentary_in_app(qapp, win, tmp_path):
    from fc27studio.ui.recorder import FakeInput, Recorder
    (tmp_path / "proj").mkdir()
    assert win._save_to(str(tmp_path / "proj" / "game.fc27studio"))
    win.recorder = Recorder(FakeInput)
    win.player.seek(10)
    win.toggle_record()
    assert win.player.playing and win.transport.btn_rec.isChecked()
    pump(qapp, 1.0)
    win.toggle_record()
    assert not win.player.playing and not win.audio.suppress
    clips = win.doc.project.commentary
    assert len(clips) == 1 and clips[0].frame == 10 and 0.7 < clips[0].seconds < 1.5
    from fc27studio.core.commentary import resolve
    assert (tmp_path / "proj" / clips[0].file).is_file()
    assert any(m.kind == "voice" for m in win.timeline.markers)
    assert win.audio.mixer is not None
    # Save As elsewhere carries the recording along
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert win._save_to(str(other / "copy.fc27studio"))
    assert (other / win.doc.project.commentary[0].file).is_file()
    # Pausing also stops a recording
    win.toggle_record()
    pump(qapp, 0.5)
    win.player.pause()
    assert getattr(win, "_rec", None) is None and len(win.doc.project.commentary) == 2
