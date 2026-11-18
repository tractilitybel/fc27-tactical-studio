"""MP4 export: sections or highlight reels with drawings, animations, freeze
frames, title cards, game sound and commentary.

Video: H.264 (yuv420p, CRF quality) — plays everywhere (Windows, phones,
WhatsApp, Discord, YouTube). Audio: AAC 48 kHz stereo.

Audio is laid down per output frame so it can never drift: every written
video frame adds exactly its share of samples — real game sound for media
frames, silence for freeze holds and title cards.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Optional

import av
import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from ..core.animation import posed
from ..core.audio import RATE, AudioDecoder
from ..core.model import Annotation, Bookmark, Project
from ..core.video import VideoSource, format_timecode
from .render import ndarray_to_qimage, render_frame


@dataclass
class Segment:
    start: int
    end: int                      # inclusive
    title: str = ""               # title card shown before the segment (empty = none)
    subtitle: str = ""


@dataclass
class ExportOptions:
    path: str
    height: Optional[int] = None  # None = source resolution
    drawings: bool = True
    freezes: bool = True
    sound: bool = True
    commentary: bool = True
    crf: int = 20
    title_seconds: float = 2.5
    audio_track: int = 0


class Cancelled(Exception):
    pass


def bookmark_segments(bookmarks: list[Bookmark], fps: float, frame_count: int,
                      before: float = 3.0, after: float = 5.0, titles: bool = True,
                      category_of: Callable[[Bookmark], str] = lambda b: b.category) -> list[Segment]:
    segs = []
    for i, b in enumerate(sorted(bookmarks, key=lambda b: b.frame)):
        if b.end_frame is not None and b.end_frame > b.frame:
            s, e = b.frame, b.end_frame
        else:
            s = b.frame - int(round(before * fps))
            e = b.frame + int(round(after * fps))
        s, e = max(0, s), min(frame_count - 1, e)
        tc = format_timecode(b.frame / fps, fps, show_frames=False)
        sub = " · ".join(x for x in (category_of(b), tc, ", ".join(b.players)) if x)
        segs.append(Segment(s, e, (b.title or f"Incident {i + 1}") if titles else "", sub if titles else ""))
    return segs


def title_card(w: int, h: int, title: str, subtitle: str, index: int, total: int) -> QImage:
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(14, 18, 26))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    # accent bar
    p.fillRect(QRectF(w * 0.08, h * 0.40, w * 0.006, h * 0.20), QColor(61, 139, 253))
    f = QFont("Segoe UI")
    f.setBold(True)
    f.setPixelSize(max(16, int(h * 0.075)))
    p.setFont(f)
    p.setPen(QColor(255, 255, 255))
    p.drawText(QRectF(w * 0.1, h * 0.36, w * 0.82, h * 0.16), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, title)
    f2 = QFont("Segoe UI")
    f2.setPixelSize(max(12, int(h * 0.035)))
    p.setFont(f2)
    p.setPen(QColor(160, 172, 188))
    p.drawText(QRectF(w * 0.1, h * 0.52, w * 0.82, h * 0.08), Qt.AlignLeft | Qt.AlignVCenter, subtitle)
    if total > 1:
        p.drawText(QRectF(w * 0.1, h * 0.84, w * 0.8, h * 0.06), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{index} / {total}")
    f3 = QFont("Segoe UI")
    f3.setPixelSize(max(10, int(h * 0.025)))
    p.setFont(f3)
    p.setPen(QColor(90, 100, 115))
    p.drawText(QRectF(w * 0.1, h * 0.84, w * 0.8, h * 0.06), Qt.AlignRight | Qt.AlignVCenter,
               "FC27 Tactical Studio")
    p.end()
    return img


def _qimage_to_rgb(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGB888)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return a[:, :img.width() * 3].reshape(img.height(), img.width(), 3).copy()


class VideoExporter:
    """Renders segments into one MP4. ``progress(fraction, text)`` may raise Cancelled."""

    def __init__(self, project: Project, video_path: str, index_data, opts: ExportOptions,
                 commentary_mixer=None, progress: Optional[Callable[[float, str], None]] = None,
                 homography_at: Optional[Callable[[int], object]] = None):
        self.project = project
        self.video_path = video_path
        self.index_data = index_data
        self.opts = opts
        self.mixer = commentary_mixer      # callable(t0, n) -> float32 (n, 2) or None
        self.progress = progress or (lambda f, t: None)
        self.homography_at = homography_at

    # ------------------------------------------------------------ helpers
    def _out_size(self, w: int, h: int) -> tuple[int, int]:
        if self.opts.height and self.opts.height < h:
            oh = self.opts.height
            ow = int(round(w * oh / h))
        else:
            ow, oh = w, h
        return ow - ow % 2, oh - oh % 2     # H.264 needs even sizes

    def run(self, segments: list[Segment]) -> str:
        src = VideoSource(self.video_path, self.index_data, cache_frames=8)
        try:
            return self._run(src, segments)
        finally:
            src.close()

    def _run(self, src: VideoSource, segments: list[Segment]) -> str:
        o = self.opts
        fps_frac = Fraction(src.fps).limit_denominator(1001)
        fps = float(fps_frac)
        W, H = self._out_size(src.info.width, src.info.height)
        tmp = o.path + ".part.mp4"
        if os.path.exists(tmp):
            os.unlink(tmp)                       # leftover from an interrupted export
        out = av.open(tmp, "w", options={"movflags": "+faststart"})
        vs = out.add_stream("libx264" if "libx264" in av.codecs_available else "mpeg4", rate=fps_frac)
        vs.width, vs.height, vs.pix_fmt = W, H, "yuv420p"
        vs.time_base = Fraction(1, 1) / fps_frac
        if vs.codec_context.name == "libx264":
            # "fast" is ~2x quicker than "medium" for a few % larger files at the same quality
            vs.options = {"crf": str(o.crf), "preset": "fast"}
        has_audio = o.sound or (o.commentary and self.mixer is not None)
        aus = None
        if has_audio:
            aus = out.add_stream("aac", rate=RATE)
            aus.layout = "stereo"
            aus.time_base = Fraction(1, RATE)
        state = {"vpts": 0, "apts": 0, "abuf": np.zeros((0, 2), np.float32)}
        total_frames = sum(s.end - s.start + 1 for s in segments) + \
            sum(int(o.title_seconds * fps) for s in segments if s.title)
        freezes = {f.frame: f.seconds for f in self.project.freezes} if o.freezes else {}
        total_frames += sum(int(sec * fps) for fr, sec in freezes.items()
                            if any(s.start <= fr <= s.end for s in segments))
        done = 0

        def write_video(rgb: np.ndarray, media_t: Optional[float]):
            """One output frame; adds its exact share of audio (media sound or silence)."""
            nonlocal done
            vf = av.VideoFrame.from_ndarray(rgb, format="rgb24")
            vf.pts = state["vpts"]
            for pkt in vs.encode(vf):
                out.mux(pkt)
            state["vpts"] += 1
            if aus is not None:
                need = int(round(state["vpts"] * RATE / fps)) - state["apts"] - len(state["abuf"])
                if need > 0:
                    if media_t is None:
                        chunk = np.zeros((need, 2), np.float32)
                    else:
                        chunk = self._media_audio(need, media_t)
                    state["abuf"] = np.concatenate([state["abuf"], chunk])
                flush_audio(final=False)
            done += 1
            if done % 5 == 0:
                self.progress(min(0.999, done / max(1, total_frames)), "")

        def flush_audio(final: bool):
            buf = state["abuf"]
            n = 1024
            while len(buf) >= n or (final and len(buf)):
                block = buf[:n]
                buf = buf[n:]
                if len(block) < n:
                    block = np.concatenate([block, np.zeros((n - len(block), 2), np.float32)])
                af = av.AudioFrame.from_ndarray(np.ascontiguousarray(block.T), format="fltp", layout="stereo")
                af.sample_rate = RATE
                af.pts = state["apts"]
                state["apts"] += n
                for pkt in aus.encode(af):
                    out.mux(pkt)
            state["abuf"] = buf

        try:
            for si, seg in enumerate(segments):
                if seg.title:
                    card = _qimage_to_rgb(title_card(W, H, seg.title, seg.subtitle, si + 1, len(segments)))
                    for _ in range(int(o.title_seconds * fps)):
                        write_video(card, None)
                self._start_audio(seg.start / fps)
                for f in range(seg.start, seg.end + 1):
                    img = ndarray_to_qimage(src.frame(f))
                    if o.drawings:
                        anns = [posed(a, f) for a in self.project.annotations_at(f)]
                        homog = self.homography_at(f) if self.homography_at else None
                        img = render_frame(img, anns, None, homog)
                    if (img.width(), img.height()) != (W, H):
                        img = img.scaled(W, H, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    rgb = _qimage_to_rgb(img)
                    t = f / fps
                    write_video(rgb, t)
                    if f in freezes:
                        for _ in range(int(freezes[f] * fps)):
                            write_video(rgb, None)
                        self._start_audio((f + 1) / fps)   # resume sound after the pause
                self._stop_audio()
            for pkt in vs.encode():
                out.mux(pkt)
            if aus is not None:
                flush_audio(final=True)
                for pkt in aus.encode():
                    out.mux(pkt)
            out.close()
        except BaseException:
            try:
                out.close()
            except Exception:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        os.replace(tmp, o.path)
        self.progress(1.0, "done")
        return o.path

    # ------------------------------------------------------------- audio
    _dec: Optional[AudioDecoder] = None
    _pending: np.ndarray = np.zeros((0, 2), np.float32)
    _media_pos: float = 0.0

    def _start_audio(self, t: float) -> None:
        self._stop_audio()
        self._media_pos = t
        self._pending = np.zeros((0, 2), np.float32)
        if self.opts.sound:
            try:
                self._dec = AudioDecoder(self.video_path, self.opts.audio_track)
                self._dec.seek(t)
            except Exception:
                self._dec = None

    def _stop_audio(self) -> None:
        if self._dec is not None:
            self._dec.close()
            self._dec = None

    def _media_audio(self, n: int, media_t: float) -> np.ndarray:
        """Next *n* samples of game sound (+ commentary) continuing from the last position."""
        game = np.zeros((n, 2), np.float32)
        if self._dec is not None:
            while len(self._pending) < n:
                b = self._dec.read()
                if b is None:
                    break
                self._pending = np.concatenate([self._pending, b])
            k = min(n, len(self._pending))
            game[:k] = self._pending[:k]
            self._pending = self._pending[k:]
        out = game
        if self.mixer is not None and self.opts.commentary:
            out = self.mixer(self._media_pos, game)
        self._media_pos += n / RATE
        return np.clip(out, -1.0, 1.0)
