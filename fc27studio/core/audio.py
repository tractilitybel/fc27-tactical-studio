"""Audio for playback, YouTube style: sound at every speed with normal pitch.

* :class:`AudioDecoder` reads one audio stream with PyAV, resampled to
  48 kHz stereo float32, starting exactly at any position.
* :class:`TimeStretcher` changes speed without changing pitch (WSOLA:
  overlapping windows re-aligned by cross-correlation), streaming.
* :class:`AudioEngine` runs decode + stretch in a background thread and
  hands out sample blocks to the sound card callback. It tracks the media
  time of what has been handed out, so the player can keep picture and
  sound in sync.

Qt-free; the sound card side lives in ui/audio_out.py.
"""
from __future__ import annotations

import collections
import threading
from dataclasses import dataclass
from typing import Optional

import av
import numpy as np

RATE = 48000
CHANNELS = 2
MIN_SPEED = 0.25    # like YouTube: below this, playback is silent
MAX_SPEED = 4.0


@dataclass
class AudioTrack:
    index: int          # position among the file's audio streams
    title: str
    channels: int
    rate: int


def list_audio_tracks(path: str) -> list[AudioTrack]:
    try:
        with av.open(path) as c:
            out = []
            for i, s in enumerate(c.streams.audio):
                meta = s.metadata or {}
                name = meta.get("title") or meta.get("handler_name") or ""
                lang = meta.get("language", "")
                label = f"Track {i + 1}"
                extra = " ".join(x for x in (name, f"({lang})" if lang and lang != "und" else "") if x)
                out.append(AudioTrack(i, f"{label} {extra}".strip(), s.codec_context.channels or 2,
                                      s.codec_context.sample_rate or RATE))
            return out
    except Exception:
        return []


class AudioDecoder:
    def __init__(self, path: str, track: int = 0):
        self.container = av.open(path)
        if not self.container.streams.audio:
            self.container.close()
            raise ValueError("no audio stream")
        self.stream = self.container.streams.audio[min(track, len(self.container.streams.audio) - 1)]
        self.stream.thread_type = "AUTO"
        self._resampler = None
        self._iter = None

    def close(self) -> None:
        self.container.close()

    def seek(self, seconds: float) -> None:
        tb = self.stream.time_base
        target = max(0.0, seconds)
        try:
            self.container.seek(int(max(0.0, target - 0.2) / tb), stream=self.stream, backward=True)
        except av.error.FFmpegError:
            self.container.seek(0, stream=self.stream)
        self._resampler = av.AudioResampler(format="flt", layout="stereo", rate=RATE)
        self._iter = self._gen(target)

    def _gen(self, start: float):
        started = False
        for frame in self.container.decode(self.stream):
            t = float(frame.pts * self.stream.time_base) if frame.pts is not None else None
            for rf in self._resampler.resample(frame):
                block = rf.to_ndarray().reshape(-1, CHANNELS).astype(np.float32, copy=False)
                if not started:
                    if t is None:
                        started = True
                    else:
                        skip = int(round((start - t) * RATE))
                        if skip >= len(block):
                            t += len(block) / RATE
                            continue
                        if skip > 0:
                            block = block[skip:]
                        elif skip < -RATE // 10:
                            # Seek landed late (gap/edit): pad silence to stay aligned.
                            block = np.concatenate([np.zeros((-skip, CHANNELS), np.float32), block])
                        started = True
                yield block

    def read(self) -> Optional[np.ndarray]:
        """Next block of samples, or None at the end of the stream."""
        try:
            return next(self._iter)
        except (StopIteration, av.error.EOFError):
            return None
        except av.error.FFmpegError:
            return None


class TimeStretcher:
    """Streaming WSOLA time-scale modification (pitch preserved)."""

    def __init__(self, frame: int = 1024, tolerance: int = 256):
        self.N = frame
        self.Hs = frame // 2
        self.tol = tolerance
        n = np.arange(frame)
        self.win = (0.5 - 0.5 * np.cos(2 * np.pi * n / frame)).astype(np.float32)[:, None]
        self.reset()

    def reset(self) -> None:
        self.buf = np.zeros((0, CHANNELS), np.float32)
        self.base = 0              # absolute index of buf[0]
        self.pos = float(self.tol)  # ideal analysis position (absolute)
        self.prev = None           # absolute start of the previous chosen frame
        self.acc = np.zeros((self.N, CHANNELS), np.float32)
        self.primed = False

    def process(self, x: np.ndarray, speed: float) -> np.ndarray:
        if x is not None and len(x):
            if not self.primed:
                # Lead-in so the first window can search backwards.
                x = np.concatenate([np.zeros((self.tol, CHANNELS), np.float32), x])
                self.primed = True
            self.buf = np.concatenate([self.buf, x])
        out = []
        N, Hs, tol = self.N, self.Hs, self.tol
        while True:
            ideal = int(round(self.pos))
            if ideal + tol + N - self.base > len(self.buf):
                break
            if self.prev is None:
                start = ideal
            else:
                nat = self.prev + Hs - self.base       # natural continuation of the last frame
                target = self.buf[nat:nat + N].mean(axis=1)[::4]
                lo = ideal - tol - self.base
                seg = self.buf[max(0, lo):ideal + tol + N - self.base].mean(axis=1)[::4]
                if len(seg) >= len(target) and len(target):
                    corr = np.correlate(seg, target, "valid")
                    start = max(0, lo) + 4 * int(np.argmax(corr)) + self.base
                else:
                    start = ideal
            fr = self.buf[start - self.base:start - self.base + N]
            if len(fr) < N:
                break
            self.acc += fr * self.win
            out.append(self.acc[:Hs].copy())
            self.acc = np.concatenate([self.acc[Hs:], np.zeros((Hs, CHANNELS), np.float32)])
            self.prev = start
            self.pos += Hs * speed
            drop = min(int(self.pos) - tol, self.prev) - self.base - 8
            if drop > 0:
                self.buf = self.buf[drop:]
                self.base += drop
        return np.concatenate(out) if out else np.zeros((0, CHANNELS), np.float32)


class AudioEngine:
    """Background decode/stretch with a small ready-to-play buffer."""

    AHEAD = 0.5  # seconds of audio kept ready

    def __init__(self, path: str, track: int = 0):
        self.path = path
        self.track = track
        self._lock = threading.Condition()
        self._chunks: "collections.deque[np.ndarray]" = collections.deque()
        self._buffered = 0
        self._gen = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._eof = False
        self.speed = 1.0
        self.start_time = 0.0
        self.samples_out = 0      # samples handed to the sound card since start()
        self._fade = 0

    # ---------------------------------------------------------------- control
    def start(self, seconds: float, speed: float) -> None:
        self.stop()
        with self._lock:
            self._gen += 1
            gen = self._gen
            self.speed = speed
            self.start_time = max(0.0, seconds)
            self.samples_out = 0
            self._chunks.clear()
            self._buffered = 0
            self._eof = False
            self._fade = 240     # 5 ms fade-in: no click when sound starts or jumps
            self._running = True
        self._thread = threading.Thread(target=self._run, args=(gen, self.start_time, speed),
                                        name="audio-decode", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._gen += 1
            self._chunks.clear()
            self._buffered = 0
            self._lock.notify_all()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=1.0)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._running

    def media_time(self, latency_samples: int = 0) -> float:
        """Media position of the sound currently coming out of the speakers."""
        heard = max(0, self.samples_out - latency_samples)
        return self.start_time + heard * self.speed / RATE

    def wait_ready(self, timeout: float = 1.0) -> bool:
        with self._lock:
            return self._lock.wait_for(lambda: self._buffered > 0 or self._eof, timeout)

    # ------------------------------------------------------------ producing
    def _run(self, gen: int, seconds: float, speed: float) -> None:
        try:
            dec = AudioDecoder(self.path, self.track)
        except Exception:
            with self._lock:
                self._eof = True
                self._lock.notify_all()
            return
        stretch = None if abs(speed - 1.0) < 1e-3 else TimeStretcher()
        try:
            dec.seek(seconds)
            while True:
                with self._lock:
                    self._lock.wait_for(lambda: gen != self._gen or self._buffered < self.AHEAD * RATE)
                    if gen != self._gen:
                        return
                block = dec.read()
                if block is None:
                    if stretch is not None:
                        block = stretch.process(np.zeros((stretch.N * 2, CHANNELS), np.float32), speed)
                        self._push(gen, block)
                    with self._lock:
                        self._eof = True
                        self._lock.notify_all()
                    return
                if stretch is not None:
                    block = stretch.process(block, speed)
                self._push(gen, block)
        finally:
            dec.close()

    def _push(self, gen: int, block: np.ndarray) -> None:
        if not len(block):
            return
        with self._lock:
            if gen != self._gen:
                return
            self._chunks.append(block)
            self._buffered += len(block)
            self._lock.notify_all()

    # ------------------------------------------------------------ consuming
    def read(self, n: int) -> np.ndarray:
        """Up to *n* samples (float32, n×2); fewer (or zero) when not ready.
        Called from the sound card thread — never blocks."""
        out = []
        got = 0
        with self._lock:
            while got < n and self._chunks:
                c = self._chunks[0]
                take = min(n - got, len(c))
                out.append(c[:take])
                if take == len(c):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = c[take:]
                got += take
            self._buffered -= got
            self.samples_out += got
            self._lock.notify_all()
        if not out:
            return np.zeros((0, CHANNELS), np.float32)
        data = np.concatenate(out)
        if self._fade > 0:
            k = min(self._fade, len(data))
            done = 240 - self._fade
            ramp = (np.arange(done, done + k, dtype=np.float32) / 240.0)[:, None]
            data = data.copy()
            data[:k] *= ramp
            self._fade -= k
        return data

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._eof and self._buffered == 0
