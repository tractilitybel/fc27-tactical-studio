"""Background decoding and real-time playback control.

* :class:`DecodeWorker` lives in its own QThread with its own VideoSource.
  Requests are coalesced ("latest wins"), so fast scrubbing never builds up
  a backlog — the UI always receives the most recently requested frame.
* :class:`Player` owns the current frame index, speed, direction and A/B
  loop. During playback a high-resolution timer computes which frame
  *should* be on screen from the wall clock and requests it; if decoding
  cannot keep up, frames are dropped rather than playback slowing down
  (configurable).
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage

from ..core.video import IndexData, VideoSource, build_index
from .render import ndarray_to_qimage

SPEEDS = [0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0]


class DecodeWorker(QObject):
    frameReady = Signal(int, QImage)
    error = Signal(str)

    def __init__(self, path: str, index: IndexData, cache_mb: int, threads: int):
        super().__init__()
        self._path = path
        self._index = index
        self._cache_mb = cache_mb
        self._threads = threads
        self._src: Optional[VideoSource] = None
        self._pending: Optional[int] = None
        self._cond = threading.Condition()
        self._stop = False

    def request(self, index: int) -> None:
        with self._cond:
            self._pending = index
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    def set_cache_mb(self, mb: int) -> None:
        self._cache_mb = mb
        if self._src is not None:
            self._src.set_cache_size(self._frames_for_budget())

    def _frames_for_budget(self) -> int:
        if self._src is None:
            return 32
        bpf = max(1, self._src.info.width * self._src.info.height * 3)
        return max(8, min(600, int(self._cache_mb * 1024 * 1024 / bpf)))

    @Slot()
    def run(self) -> None:
        try:
            self._src = VideoSource(self._path, self._index, threads=self._threads)
            self._src.set_cache_size(self._frames_for_budget())
        except Exception as exc:  # pragma: no cover
            self.error.emit(str(exc))
            return
        while True:
            with self._cond:
                while self._pending is None and not self._stop:
                    self._cond.wait()
                if self._stop:
                    break
                idx, self._pending = self._pending, None
            try:
                arr = self._src.frame(idx)
                self.frameReady.emit(idx, ndarray_to_qimage(arr))
            except Exception as exc:
                self.error.emit(f"Decode error at frame {idx}: {exc}")
        self._src.close()


class ThumbnailWorker(QObject):
    """Low-priority keyframe thumbnails for timeline hover and bookmarks."""
    thumbReady = Signal(int, QImage)

    def __init__(self, path: str, index: IndexData):
        super().__init__()
        self._path, self._index = path, index
        self._hover: list[tuple[int, int]] = []  # timeline hover: newest first, few kept
        self._queue: list[tuple[int, int]] = []  # exact requests (bookmarks): never dropped
        self._cond = threading.Condition()
        self._stop = False

    def request(self, frame: int, width: int = 200, exact: bool = False, front: bool = False) -> None:
        with self._cond:
            item = (frame, -width if exact else width)
            if exact:
                if item not in self._queue:
                    self._queue.append(item)
            else:
                if item in self._hover:
                    self._hover.remove(item)
                self._hover.insert(0, item)
                del self._hover[3:]
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify()

    @Slot()
    def run(self) -> None:
        try:
            src = VideoSource(self._path, self._index, cache_frames=6, threads=1)
        except Exception:  # pragma: no cover
            return
        while True:
            with self._cond:
                while not self._queue and not self._hover and not self._stop:
                    self._cond.wait()
                if self._stop:
                    break
                frame, width = (self._hover or self._queue).pop(0)
            try:
                if width < 0:
                    from ..core.video import downscale
                    arr = downscale(src.frame(frame), -width)
                else:
                    arr = src.thumbnail(frame, width)
                self.thumbReady.emit(frame, ndarray_to_qimage(arr))
            except Exception:
                pass
        src.close()


class Player(QObject):
    frameChanged = Signal(int)            # current index changed
    imageReady = Signal(int, QImage)      # decoded image for display
    stateChanged = Signal()               # playing / speed / loop changed
    seeked = Signal()                     # jump while playing (seek or loop wrap): resync sound
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source: Optional[VideoSource] = None  # metadata only (UI thread)
        self.index_data: Optional[IndexData] = None
        self.frame_count = 0
        self.fps = 30.0
        self.current = 0
        self.playing = False
        self.direction = 1
        self.speed = 1.0
        self.loop_a: Optional[int] = None
        self.loop_b: Optional[int] = None
        self.loop_enabled = False
        self.drop_frames = True
        self._shown = -1
        self._waiting = False
        self._play_t0 = 0.0
        self._play_f0 = 0
        self._thread: Optional[QThread] = None
        self._worker: Optional[DecodeWorker] = None
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    # ---------------------------------------------------------------- open
    def open(self, path: str, cache_mb: int = 768, threads: int = 0,
             index: Optional[IndexData] = None) -> VideoSource:
        self.close()
        index = index or build_index(path)
        src = VideoSource(path, index, cache_frames=4)
        self.source = src
        self.index_data = index
        self.frame_count = src.frame_count
        self.fps = src.fps or 30.0
        self.current = 0
        self._shown = -1
        self.loop_a = self.loop_b = None
        self.loop_enabled = False
        self._thread = QThread()
        self._worker = DecodeWorker(path, index, cache_mb, threads)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frameReady.connect(self._on_frame, Qt.QueuedConnection)
        self._worker.error.connect(self.error)
        self._thread.start()
        self.seek(0)
        return src

    def close(self) -> None:
        self.pause()
        if self._worker is not None:
            self._worker.stop()
            self._thread.quit()
            self._thread.wait(3000)
            self._worker = None
            self._thread = None
        if self.source is not None:
            self.source.close()
            self.source = None
        self.frame_count = 0

    @property
    def is_open(self) -> bool:
        return self.source is not None

    def set_cache_mb(self, mb: int) -> None:
        if self._worker:
            self._worker.set_cache_mb(mb)

    # ---------------------------------------------------------------- seek
    def clamp(self, i: int) -> int:
        return max(0, min(self.frame_count - 1, int(i))) if self.frame_count else 0

    def seek(self, index: int) -> None:
        if not self.is_open:
            return
        index = self.clamp(index)
        if self.playing:
            self._play_t0 = time.perf_counter()
            self._play_f0 = index
        self._set_current(index)
        if self.playing:
            self.seeked.emit()

    def _set_current(self, index: int) -> None:
        changed = index != self.current
        self.current = index
        self._request(index)
        if changed or self._shown == -1:
            self.frameChanged.emit(index)

    def _request(self, index: int) -> None:
        if self._worker:
            self._waiting = True
            self._worker.request(index)

    def step(self, delta: int) -> None:
        self.pause()
        self.seek(self.current + delta)

    def seconds_precise(self) -> float:
        """Media time of the playback clock (sub-frame precision while playing)."""
        if not self.playing or not self.fps:
            return self.seconds()
        elapsed = time.perf_counter() - self._play_t0
        return self._play_f0 / self.fps + elapsed * self.speed * self.direction

    def nudge(self, media_seconds: float) -> None:
        """Shift the playback clock (used to follow the sound card's clock)."""
        if self.playing and self.speed:
            self._play_t0 -= media_seconds / self.speed

    def seconds(self, index: Optional[int] = None) -> float:
        i = self.current if index is None else index
        return i / self.fps if self.fps else 0.0

    # ------------------------------------------------------------ playback
    def play(self, direction: int = 1) -> None:
        if not self.is_open:
            return
        if direction > 0 and self.current >= self.frame_count - 1 and not self._loop_active():
            self.seek(0)
        self.direction = 1 if direction >= 0 else -1
        self.playing = True
        self._play_t0 = time.perf_counter()
        self._play_f0 = self.current
        self._timer.start(max(4, int(500 / max(1.0, self.fps * self.speed))))
        self.stateChanged.emit()

    def pause(self) -> None:
        if self.playing:
            self.playing = False
            self._timer.stop()
            self.stateChanged.emit()

    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play(1)

    def set_speed(self, speed: float) -> None:
        speed = max(0.05, min(8.0, speed))
        if self.playing:
            self._play_f0 = self.current
            self._play_t0 = time.perf_counter()
            self._timer.setInterval(max(4, int(500 / max(1.0, self.fps * speed))))
        self.speed = speed
        self.stateChanged.emit()

    def faster(self) -> None:
        bigger = [s for s in SPEEDS if s > self.speed + 1e-6]
        self.set_speed(bigger[0] if bigger else SPEEDS[-1])

    def slower(self) -> None:
        smaller = [s for s in SPEEDS if s < self.speed - 1e-6]
        self.set_speed(smaller[-1] if smaller else SPEEDS[0])

    def jkl(self, key: str) -> None:
        """Editor-style J/K/L shuttle control."""
        if key == "k":
            self.pause()
            return
        want = 1 if key == "l" else -1
        if self.playing and self.direction == want:
            ladder = [1.0, 2.0, 4.0]
            nxt = [s for s in ladder if s > self.speed + 1e-6]
            self.set_speed(nxt[0] if nxt else ladder[-1])
        else:
            self.set_speed(1.0)
            self.play(want)

    # ----------------------------------------------------------------- loop
    def _loop_active(self) -> bool:
        return (self.loop_enabled and self.loop_a is not None and self.loop_b is not None
                and self.loop_b > self.loop_a)

    def set_loop(self, a: Optional[int], b: Optional[int], enabled: Optional[bool] = None):
        if a is not None and b is not None and b < a:
            a, b = b, a
        self.loop_a, self.loop_b = a, b
        if enabled is not None:
            self.loop_enabled = enabled
        elif a is not None and b is not None:
            self.loop_enabled = True
        if a is None or b is None:
            self.loop_enabled = self.loop_enabled and a is not None and b is not None
        self.stateChanged.emit()

    def _tick(self) -> None:
        if not self.playing:
            return
        elapsed = time.perf_counter() - self._play_t0
        target = self._play_f0 + int(elapsed * self.fps * self.speed) * self.direction
        if not self.drop_frames:
            # Never skip: advance at most one frame beyond what's displayed.
            if self._waiting:
                return
            target = self.current + self.direction if target != self.current else target
            self._play_f0, self._play_t0 = target, time.perf_counter()
        if self._loop_active():
            a, b = self.loop_a, self.loop_b
            if self.direction > 0 and target > b:
                target = a
                self._play_f0, self._play_t0 = a, time.perf_counter()
                self._set_current(target)
                self.seeked.emit()
                return
            elif self.direction < 0 and target < a:
                target = b
                self._play_f0, self._play_t0 = b, time.perf_counter()
        if target >= self.frame_count - 1 and self.direction > 0:
            self._set_current(self.frame_count - 1)
            self.pause()
            return
        if target <= 0 and self.direction < 0:
            self._set_current(0)
            self.pause()
            return
        if target != self.current:
            if self._waiting and self.drop_frames and abs(target - self.current) < 2:
                return  # still decoding the previous frame
            self._set_current(target)

    @Slot(int, QImage)
    def _on_frame(self, index: int, img: QImage) -> None:
        self._waiting = False
        self._shown = index
        self.imageReady.emit(index, img)
