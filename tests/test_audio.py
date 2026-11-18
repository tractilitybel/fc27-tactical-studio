"""Sound: decoding at any position, pitch-preserving speed, YouTube-style rules, sync."""
import time
from fractions import Fraction

import av
import numpy as np
import pytest

from conftest import encode_frame_number
from fc27studio.core.audio import RATE, AudioDecoder, AudioEngine, TimeStretcher, list_audio_tracks


def tone_freq(second: int) -> float:
    return 300.0 + 100.0 * second


def make_av_video(path, seconds=6, fps=30, tracks=1):
    """Video with frame numbers in the picture; audio pitch changes every second."""
    out = av.open(path, "w")
    v = out.add_stream("libx264", rate=fps)
    v.width, v.height, v.pix_fmt = 320, 180, "yuv420p"
    v.time_base = Fraction(1, fps)
    auds = []
    for _ in range(tracks):
        a = out.add_stream("aac", rate=RATE)
        a.layout = "stereo"
        auds.append(a)
    for i in range(seconds * fps):
        f = av.VideoFrame.from_ndarray(encode_frame_number(i, 320, 180), format="rgb24")
        f.pts = i
        for p in v.encode(f):
            out.mux(p)
    for ti, a in enumerate(auds):
        n = 1024
        total = seconds * RATE
        for start in range(0, total, n):
            t = (np.arange(start, min(start + n, total))) / RATE
            freq = np.array([tone_freq(int(x)) * (1 + ti) for x in t])
            phase = 2 * np.pi * np.cumsum(freq) / RATE
            sig = (0.4 * np.sin(phase)).astype(np.float32)
            data = np.stack([sig, sig]).astype(np.float32)
            fr = av.AudioFrame.from_ndarray(data, format="fltp", layout="stereo")
            fr.sample_rate = RATE
            fr.pts = start
            for p in a.encode(fr):
                out.mux(p)
    for s in [v] + auds:
        for p in s.encode():
            out.mux(p)
    out.close()
    return path


def dominant(block):
    x = block[:, 0] * np.hanning(len(block))
    f = np.abs(np.fft.rfft(x))
    return np.argmax(f) * RATE / len(block)


@pytest.fixture(scope="module")
def av_video(tmp_path_factory):
    return make_av_video(str(tmp_path_factory.mktemp("av") / "match.mp4"), tracks=2)


def read_seconds(dec, secs):
    out, need = [], int(secs * RATE)
    while need > 0:
        b = dec.read()
        if b is None:
            break
        out.append(b[:need])
        need -= len(b[:need])
    return np.concatenate(out)


def test_tracks_listed(av_video):
    tracks = list_audio_tracks(av_video)
    assert [t.index for t in tracks] == [0, 1]


def test_decoder_seeks_exactly(av_video):
    dec = AudioDecoder(av_video)
    for start in (0.0, 2.5, 4.25):
        dec.seek(start)
        data = read_seconds(dec, 1.5)
        # The first 0.2 s after the start must carry that second's tone.
        assert abs(dominant(data[int(0.05 * RATE):int(0.2 * RATE)]) - tone_freq(int(start))) < 25
        # And the tone changes exactly at the next whole second.
        nxt = int(start) + 1
        off = int((nxt - start + 0.05) * RATE)
        assert abs(dominant(data[off:off + int(0.15 * RATE)]) - tone_freq(nxt)) < 25
    dec.close()


def test_second_track(av_video):
    dec = AudioDecoder(av_video, track=1)
    dec.seek(1.0)
    d = read_seconds(dec, 0.5)
    assert abs(dominant(d[2400:]) - 2 * tone_freq(1)) < 30
    dec.close()


@pytest.mark.parametrize("speed", [0.25, 0.5, 1.5, 2.0, 4.0])
def test_stretch_keeps_pitch_and_changes_length(speed):
    t = np.arange(RATE * 3) / RATE
    x = np.stack([np.sin(2 * np.pi * 440 * t)] * 2, 1).astype(np.float32) * 0.5
    ts = TimeStretcher()
    y = np.concatenate([ts.process(x[i:i + 4800], speed) for i in range(0, len(x), 4800)])
    assert abs(len(y) / RATE - 3 / speed) < 0.1
    mid = len(y) // 2
    assert abs(dominant(y[mid - 4096:mid + 4096]) - 440) < 8


def test_engine_media_time_at_double_speed(av_video):
    eng = AudioEngine(av_video)
    eng.start(1.0, 2.0)
    assert eng.wait_ready(2.0)
    got = []
    for _ in range(40):
        d = eng.read(2400)
        if len(d):
            got.append(d)
        else:
            time.sleep(0.01)
    data = np.concatenate(got)
    assert abs(eng.media_time() - (1.0 + len(data) * 2.0 / RATE)) < 1e-6
    # 0.5 s of output at 2x = 1 s of media: the tone moves from 400 Hz to 500 Hz, pitch unchanged.
    assert abs(dominant(data[2400:12000]) - tone_freq(1)) < 30
    assert abs(dominant(data[int(0.6 * RATE):int(0.75 * RATE)]) - tone_freq(2)) < 30
    eng.stop()


# ---------------------------------------------------------------- with the player
@pytest.fixture
def player_audio(qapp, av_video):
    from fc27studio.ui.audio_out import AudioOutput, ClockedNullStream
    from fc27studio.ui.playback import Player
    pl = Player()
    pl.open(av_video)
    out = AudioOutput(pl, stream_factory=ClockedNullStream)
    out.open(av_video)
    yield pl, out
    out.close()
    pl.close()


def pump(qapp, t):
    end = time.time() + t
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.005)


def test_youtube_rules(qapp, player_audio):
    pl, out = player_audio
    assert not out.engine.running                    # paused: silent
    pl.play()
    pump(qapp, 0.2)
    assert out.engine.running
    pl.set_speed(2.0)
    pump(qapp, 0.15)
    assert out.engine.running and out.engine.speed == 2.0   # sound at 2x (pitch kept)
    pl.set_speed(0.1)
    pump(qapp, 0.1)
    assert not out.engine.running                    # too slow: silent, like YouTube's minimum
    pl.set_speed(1.0)
    pump(qapp, 0.1)
    assert out.engine.running
    out.set_muted(True)
    assert not out.engine.running
    out.set_muted(False)
    assert out.engine.running
    pl.pause()
    assert not out.engine.running
    pl.play(-1)                                      # reverse: silent
    pump(qapp, 0.1)
    assert not out.engine.running
    pl.pause()
    pl.step(1)                                       # frame stepping: silent
    assert not out.engine.running


def test_seek_while_playing_restarts_sound_there(qapp, player_audio):
    pl, out = player_audio
    pl.play()
    pump(qapp, 0.3)
    pl.seek(int(4.0 * pl.fps))
    pump(qapp, 0.05)
    assert abs(out.engine.start_time - 4.0) < 0.05
    pl.pause()


def test_picture_and_sound_stay_in_sync(qapp, player_audio):
    pl, out = player_audio
    pl.seek(15)
    pl.play()
    pump(qapp, 2.0)
    drift = pl.seconds_precise() - out.engine.media_time(out._latency_samples)
    pl.pause()
    assert abs(drift) < 0.06, drift


def test_loop_wrap_resyncs_sound(qapp, player_audio):
    pl, out = player_audio
    pl.set_loop(30, 45, enabled=True)
    pl.seek(30)
    pl.play()
    pump(qapp, 1.0)                                   # 0.5 s loop wraps at least once
    t = out.engine.start_time
    pl.pause()
    assert 1.0 - 0.05 <= t <= 1.5


def test_no_device_is_harmless(qapp, av_video):
    from fc27studio.ui.audio_out import AudioOutput
    from fc27studio.ui.playback import Player
    pl = Player()
    pl.open(av_video)
    out = AudioOutput(pl, stream_factory=None)
    out.open(av_video)
    pl.play()
    pump(qapp, 0.2)
    assert not out.engine.running and pl.current > 0
    pl.pause()
    out.close()
    pl.close()


def test_mute_key_and_controls(qapp, monkeypatch, av_video):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    from fc27studio.ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    w.open_video(av_video)
    assert w.audio.has_audio and len(w.audio.tracks) == 2
    was = w.audio.muted
    QTest.keyClick(w, Qt.Key_M)
    assert w.audio.muted != was
    QTest.keyClick(w, Qt.Key_M)
    assert w.audio.muted == was
    w.transport.volume.setValue(40)
    assert abs(w.audio.volume - 0.4) < 1e-6 and abs(w.prefs.volume - 0.4) < 1e-6
    w.audio.set_track(1)
    assert w.audio.track == 1
    w.doc.mark_clean()
    w.close()
