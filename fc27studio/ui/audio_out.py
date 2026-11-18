"""Sound output and picture/sound synchronisation (YouTube-style behaviour).

* Sound plays while the video plays forward at 0.25×–4×, pitch preserved.
* Silent while paused, scrubbing, stepping frames or playing in reverse.
* The picture follows the sound card's clock (small, invisible nudges), so
  they never drift apart; seeks and loop jumps restart the sound instantly.
* If the PC has no usable audio device, everything else keeps working.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from ..core.audio import CHANNELS, MAX_SPEED, MIN_SPEED, RATE, AudioEngine, list_audio_tracks


class SoundDeviceStream:
    """Real sound card output via PortAudio (sounddevice)."""

    def __init__(self, callback: Callable[[int], np.ndarray]):
        import sounddevice as sd
        self._cb = callback

        def cb(outdata, frames, _time, _status):
            data = self._cb(frames)
            n = len(data)
            outdata[:n] = data
            if n < frames:
                outdata[n:] = 0
        self._stream = sd.OutputStream(samplerate=RATE, channels=CHANNELS, dtype="float32",
                                       callback=cb, latency="low")
        self._stream.start()

    @property
    def latency(self) -> float:
        lat = self._stream.latency
        return float(lat[1] if isinstance(lat, (tuple, list)) else lat)

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class ClockedNullStream:
    """Consumes audio in real time without a sound card (tests, no device)."""

    def __init__(self, callback: Callable[[int], np.ndarray]):
        self._cb = callback
        self._stop = False
        self.latency = 0.0
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self) -> None:
        block = 480
        nxt = time.perf_counter()
        while not self._stop:
            self._cb(block)
            nxt += block / RATE
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)

    def close(self) -> None:
        self._stop = True


def default_stream_factory():
    try:
        import sounddevice as sd
        sd.query_devices(kind="output")
        return SoundDeviceStream
    except Exception:
        return None


class AudioOutput(QObject):
    stateChanged = Signal()

    def __init__(self, player, stream_factory: Optional[Callable] = "default", parent=None):
        super().__init__(parent)
        self.player = player
        self._factory = default_stream_factory() if stream_factory == "default" else stream_factory
        self.device_available = self._factory is not None
        self.engine: Optional[AudioEngine] = None
        self.stream = None
        self.tracks = []
        self.track = 0
        self.volume = 1.0
        self.muted = False
        self._latency_samples = 0
        self._started_at = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._sync)
        player.stateChanged.connect(self._sync)
        if hasattr(player, "seeked"):
            player.seeked.connect(self._restart)

    # ----------------------------------------------------------- media
    def open(self, path: str) -> None:
        self.close()
        self.tracks = list_audio_tracks(path)
        self.track = 0
        if self.tracks:
            self.engine = AudioEngine(path, 0)
        self.stateChanged.emit()

    def close(self) -> None:
        if self.engine is not None:
            self.engine.stop()
        self.engine = None
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.timer.stop()

    def set_track(self, index: int) -> None:
        if self.engine is None or index == self.track:
            return
        self.engine.stop()
        self.engine = AudioEngine(self.engine.path, index)
        self.track = index
        self._sync()

    @property
    def has_audio(self) -> bool:
        return self.engine is not None

    # ----------------------------------------------------------- volume
    def set_volume(self, v: float) -> None:
        self.volume = max(0.0, min(1.0, v))
        if self.volume > 0 and self.muted:
            self.muted = False
        self.stateChanged.emit()
        self._sync()

    def set_muted(self, m: bool) -> None:
        self.muted = m
        self.stateChanged.emit()
        self._sync()

    def toggle_mute(self) -> None:
        self.set_muted(not self.muted)

    # ----------------------------------------------------------- engine
    def _callback(self, frames: int) -> np.ndarray:
        eng = self.engine
        if eng is None or not eng.running:
            return np.zeros((0, CHANNELS), np.float32)
        data = eng.read(frames)
        gain = 0.0 if self.muted else self.volume ** 2   # perceptual volume curve
        return data * gain if gain != 1.0 else data

    def should_play(self) -> bool:
        p = self.player
        return (self.engine is not None and self.device_available and p.playing and p.direction > 0
                and not getattr(p, "holding", False)
                and MIN_SPEED <= p.speed <= MAX_SPEED and not self.muted and self.volume > 0)

    def _ensure_stream(self) -> bool:
        if self.stream is None and self._factory is not None:
            try:
                self.stream = self._factory(self._callback)
                self._latency_samples = int(getattr(self.stream, "latency", 0.0) * RATE)
            except Exception:
                self._factory = None
                self.device_available = False
                self.stateChanged.emit()
                return False
        return self.stream is not None

    def _restart(self) -> None:
        """Start the sound at the picture's position (after play, seek or loop)."""
        if not self.should_play() or not self._ensure_stream():
            if self.engine is not None and self.engine.running:
                self.engine.stop()
            return
        self.engine.start(self.player.seconds(), self.player.speed)
        self._started_at = time.perf_counter()
        if not self.timer.isActive():
            self.timer.start()

    def _sync(self) -> None:
        eng = self.engine
        if eng is None:
            return
        if not self.should_play():
            if eng.running:
                eng.stop()
            self.timer.stop()
            return
        if not eng.running or abs(eng.speed - self.player.speed) > 1e-6:
            self._restart()
            return
        if time.perf_counter() - self._started_at < 0.3:
            return  # let the sound card settle before measuring drift
        heard = eng.media_time(self._latency_samples)
        drift = self.player.seconds_precise() - heard   # >0: picture ahead of sound
        if abs(drift) > 0.25:
            self._restart()                              # jumped: resync the sound
        elif abs(drift) > 0.015:
            self.player.nudge(-drift * 0.5)              # picture follows the sound clock
