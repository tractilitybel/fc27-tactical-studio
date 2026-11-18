"""Frame-accurate video access built on PyAV (FFmpeg).

Design
------
* On open, the container is *demuxed* (not decoded) once to collect the
  presentation timestamp (PTS) of every video packet.  Sorting those PTS
  values gives the exact presentation order of every frame, which makes
  frame indices stable even for streams with B-frames (where decode order
  differs from display order).
* ``frame(i)`` seeks to the nearest keyframe at or before frame *i* and
  decodes forward until the frame whose PTS matches ``pts[i]`` comes out
  of the decoder.  Frames decoded along the way are cached, so stepping
  backwards over a GOP is cheap.
* Sequential access (``frame(i + 1)`` after ``frame(i)``) continues decoding
  without seeking.

The module is Qt-free so it can be unit tested and used from worker
threads.  Each thread should use its own :class:`VideoSource`.
"""
from __future__ import annotations

import bisect
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterator, Optional

import av
import numpy as np


class VideoError(Exception):
    pass


@dataclass(frozen=True)
class VideoInfo:
    path: str
    width: int
    height: int
    frame_count: int
    fps: float
    duration: float  # seconds
    codec: str
    file_size: int

    def fingerprint(self) -> dict:
        """Data used to recognise the same file after it was moved."""
        return {
            "file_size": self.file_size,
            "frame_count": self.frame_count,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class IndexData:
    pts: list[int]
    keyframe_pts: list[int]  # sorted


def build_index(path: str) -> IndexData:
    """Demux every packet and return the sorted PTS list."""
    try:
        container = av.open(path)
    except Exception as exc:  # pragma: no cover - depends on FFmpeg msgs
        raise VideoError(f"Cannot open video: {exc}") from exc
    try:
        if not container.streams.video:
            raise VideoError("File contains no video stream")
        stream = container.streams.video[0]
        pts: list[int] = []
        keys: list[int] = []
        fallback = 0
        for packet in container.demux(stream):
            if packet.size == 0:  # flush packet
                continue
            p = packet.pts if packet.pts is not None else packet.dts
            if p is None:
                p = fallback
            fallback = p + 1
            pts.append(p)
            if packet.is_keyframe:
                keys.append(p)
        if not pts:
            raise VideoError("Video stream contains no frames")
        pts.sort()
        # Remove duplicates (rare, broken muxers) so indices stay unique.
        dedup = [pts[0]]
        for p in pts[1:]:
            if p != dedup[-1]:
                dedup.append(p)
        keys.sort()
        if not keys:
            keys = [dedup[0]]
        return IndexData(dedup, keys)
    finally:
        container.close()


class VideoSource:
    """Random-access, frame-exact reader for one video file."""

    def __init__(self, path: str, index: Optional[IndexData] = None,
                 cache_frames: int = 48, threads: int = 0):
        if not os.path.isfile(path):
            raise VideoError(f"File not found: {path}")
        self.path = path
        self._lock = threading.RLock()
        self._index = index or build_index(path)
        self._pts = self._index.pts
        self._pts_to_index = {p: i for i, p in enumerate(self._pts)}
        self._cache: "OrderedDict[int, np.ndarray]" = OrderedDict()
        self._cache_limit = max(4, cache_frames)
        self._container = None
        self._stream = None
        self._decoder: Optional[Iterator] = None
        self._last_decoded = -10  # index of last frame emitted by decoder
        self._threads = threads
        self._open()
        s = self._stream
        rate = s.average_rate or s.guessed_rate or Fraction(30, 1)
        self._fps = float(rate)
        self._time_base = s.time_base or Fraction(1, 90000)
        cc = s.codec_context
        self.info = VideoInfo(
            path=os.path.abspath(path),
            width=cc.width,
            height=cc.height,
            frame_count=len(self._pts),
            fps=self._fps,
            duration=len(self._pts) / self._fps if self._fps else 0.0,
            codec=cc.name,
            file_size=os.path.getsize(path),
        )

    # ------------------------------------------------------------------ setup
    def _open(self) -> None:
        self._container = av.open(self.path)
        self._stream = self._container.streams.video[0]
        cc = self._stream.codec_context
        if self._threads:
            cc.thread_count = self._threads
        cc.thread_type = "AUTO"
        self._decoder = None
        self._last_decoded = -10

    def close(self) -> None:
        with self._lock:
            if self._container is not None:
                self._container.close()
                self._container = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def index_data(self) -> IndexData:
        return self._index

    # --------------------------------------------------------------- helpers
    @property
    def frame_count(self) -> int:
        return len(self._pts)

    @property
    def fps(self) -> float:
        return self._fps

    def frame_to_seconds(self, index: int) -> float:
        return index / self._fps if self._fps else 0.0

    def seconds_to_frame(self, seconds: float) -> int:
        return self.clamp(int(round(seconds * self._fps)))

    def clamp(self, index: int) -> int:
        return max(0, min(self.frame_count - 1, int(index)))

    def pts_of(self, index: int) -> int:
        return self._pts[self.clamp(index)]

    def set_cache_size(self, frames: int) -> None:
        with self._lock:
            self._cache_limit = max(4, frames)
            self._trim_cache()

    def _trim_cache(self) -> None:
        while len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)

    def _store(self, idx: int, img: np.ndarray) -> None:
        self._cache[idx] = img
        self._cache.move_to_end(idx)
        self._trim_cache()

    def _index_for_frame(self, frame) -> Optional[int]:
        p = frame.pts
        if p is None:
            p = getattr(frame, "dts", None)
        if p is None:
            return None
        idx = self._pts_to_index.get(p)
        if idx is None:
            # Timestamp not in the index (should not happen); map to nearest.
            idx = min(bisect.bisect_left(self._pts, p), self.frame_count - 1)
        return idx

    def _seek_to(self, index: int) -> None:
        target = self._pts[index]
        k = bisect.bisect_right(self._index.keyframe_pts, target) - 1
        key_pts = self._index.keyframe_pts[max(0, k)]
        try:
            self._container.seek(key_pts, stream=self._stream, backward=True,
                                 any_frame=False)
        except av.error.FFmpegError:
            self._container.seek(0, stream=self._stream)
        self._decoder = self._container.decode(self._stream)
        self._last_decoded = -10

    # ------------------------------------------------------------ main API
    def frame(self, index: int, format: str = "rgb24") -> np.ndarray:
        """Return frame *index* (presentation order) as an HxWx3 uint8 array."""
        with self._lock:
            index = self.clamp(index)
            cached = self._cache.get(index)
            if cached is not None:
                self._cache.move_to_end(index)
                return cached
            if self._container is None:
                self._open()
            # Continue decoding if the target is just ahead of the decoder.
            ahead = self._decoder is not None and \
                self._last_decoded < index <= self._last_decoded + 12
            if not ahead:
                self._seek_to(index)
            for attempt in range(2):
                result = self._decode_until(index, format)
                if result is not None:
                    return result
                # Seek landed after the target (bad index); restart from 0.
                self._container.seek(0, stream=self._stream)
                self._decoder = self._container.decode(self._stream)
                self._last_decoded = -10
            raise VideoError(f"Could not decode frame {index}")

    def _decode_until(self, index: int, format: str) -> Optional[np.ndarray]:
        try:
            for frame in self._decoder:
                idx = self._index_for_frame(frame)
                if idx is None:
                    continue
                self._last_decoded = idx
                img = frame.to_ndarray(format=format)
                self._store(idx, img)
                if idx >= index:
                    # Either the target, or we overshot (seek landed late).
                    return self._cache.get(index)
        except (av.error.EOFError, av.error.FFmpegError):
            pass
        # End of stream reached before the target.
        self._decoder = None
        return self._cache.get(index)

    def iter_frames(self, start: int, end: Optional[int] = None,
                    format: str = "rgb24") -> Iterator[tuple[int, np.ndarray]]:
        """Yield (index, image) sequentially; efficient for playback."""
        end = self.frame_count - 1 if end is None else self.clamp(end)
        i = self.clamp(start)
        while i <= end:
            yield i, self.frame(i, format)
            i += 1

    def thumbnail(self, index: int, max_width: int = 240) -> np.ndarray:
        """Fast approximate preview: nearest keyframe at or before *index*."""
        with self._lock:
            index = self.clamp(index)
            target = self._pts[index]
            k = bisect.bisect_right(self._index.keyframe_pts, target) - 1
            key_pts = self._index.keyframe_pts[max(0, k)]
            key_idx = self._pts_to_index.get(key_pts, index)
            cached = self._cache.get(index)
            if cached is None:
                cached = self._cache.get(key_idx)
            if cached is None:
                cached = self.frame(key_idx)
        return downscale(cached, max_width)


def downscale(img: np.ndarray, max_width: int) -> np.ndarray:
    h, w = img.shape[:2]
    if w <= max_width:
        return img
    step = int(np.ceil(w / max_width))
    return np.ascontiguousarray(img[::step, ::step])


def format_timecode(seconds: float, fps: float = 0.0, frame: Optional[int] = None,
                    show_frames: bool = True) -> str:
    """HH:MM:SS.ff style timecode (ff = frame within the second)."""
    if seconds < 0:
        seconds = 0
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    base = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    if show_frames and fps > 0:
        if frame is not None:
            ff = int(frame - int(total * fps + 1e-6))
            ff = max(0, min(ff, int(np.ceil(fps)) - 1))
        else:
            ff = int((seconds - total) * fps)
        width = len(str(max(1, int(np.ceil(fps)) - 1)))
        return f"{base}.{ff:0{width}d}"
    return base
