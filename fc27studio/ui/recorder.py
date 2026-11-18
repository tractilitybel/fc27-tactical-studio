"""Microphone recording for voice commentary (sounddevice / PortAudio)."""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import numpy as np

from ..core.audio import RATE


class SoundDeviceInput:
    def __init__(self, callback: Callable[[np.ndarray], None]):
        import sounddevice as sd
        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                      callback=lambda data, frames, t, status: callback(data[:, 0].copy()))
        self._stream.start()

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class FakeInput:
    """Test microphone: a quiet 220 Hz voice-like tone in real time."""

    def __init__(self, callback):
        self._cb = callback
        self._stop = False
        self._n = 0
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        block = 480
        nxt = time.perf_counter()
        while not self._stop:
            t = (np.arange(self._n, self._n + block)) / RATE
            self._cb((0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))
            self._n += block
            nxt += block / RATE
            d = nxt - time.perf_counter()
            if d > 0:
                time.sleep(d)

    def close(self):
        self._stop = True


def default_input_factory():
    try:
        import sounddevice as sd
        sd.query_devices(kind="input")
        return SoundDeviceInput
    except Exception:
        return None


class Recorder:
    def __init__(self, factory="default"):
        self.factory = default_input_factory() if factory == "default" else factory
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = None
        self.level = 0.0          # recent peak, for a level meter

    @property
    def available(self) -> bool:
        return self.factory is not None

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def _on_data(self, data: np.ndarray) -> None:
        with self._lock:
            self._chunks.append(data)
        self.level = float(np.abs(data).max()) if len(data) else 0.0

    def start(self) -> None:
        self._chunks = []
        self._stream = self.factory(self._on_data)

    def stop(self) -> np.ndarray:
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        with self._lock:
            data = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
        return data

    def seconds(self) -> float:
        with self._lock:
            return sum(len(c) for c in self._chunks) / RATE
