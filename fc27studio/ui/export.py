"""Still-image and data exports."""
from __future__ import annotations

import csv
import os
import re
from typing import Callable, Iterable, Optional

from PySide6.QtGui import QImage

from ..core.model import Annotation, Bookmark, Project
from ..core.video import VideoSource, format_timecode
from .render import ndarray_to_qimage, render_frame


def safe_name(text: str, fallback: str = "frame") -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", text).strip()
    s = re.sub(r"\s+", " ", s)
    return (s[:80] or fallback).rstrip(". ")


def compose(src: VideoSource, frame: int, anns: Iterable[Annotation], with_drawings: bool = True) -> QImage:
    img = ndarray_to_qimage(src.frame(frame))
    if with_drawings:
        return render_frame(img, anns)
    return img


def save_image(img: QImage, path: str, quality: int = 95) -> None:
    ext = os.path.splitext(path)[1].lower()
    fmt = "JPG" if ext in (".jpg", ".jpeg") else "PNG"
    q = quality if fmt == "JPG" else -1
    if not img.save(path, fmt, q):
        raise OSError(f"Could not write image to {path}")


def export_frame(src: VideoSource, frame: int, anns: Iterable[Annotation], path: str,
                 with_drawings: bool = True, quality: int = 95) -> str:
    save_image(compose(src, frame, anns, with_drawings), path, quality)
    return path


def export_bookmark_images(src: VideoSource, project: Project, bookmarks: list[Bookmark],
                           folder: str, fmt: str = "png", with_drawings: bool = True,
                           quality: int = 95,
                           progress: Optional[Callable[[int, int], bool]] = None) -> list[str]:
    os.makedirs(folder, exist_ok=True)
    out = []
    fps = src.fps
    for i, b in enumerate(sorted(bookmarks, key=lambda b: b.frame)):
        if progress is not None and not progress(i, len(bookmarks)):
            break
        tc = format_timecode(b.frame / fps, fps, frame=b.frame).replace(":", "-")
        name = safe_name(f"{i + 1:02d} {tc} {b.category} {b.title}")
        path = os.path.join(folder, f"{name}.{fmt}")
        export_frame(src, b.frame, project.annotations_at(b.frame), path, with_drawings, quality)
        out.append(path)
    return out


def export_bookmarks_csv(project: Project, fps: float, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Start", "End", "Start frame", "End frame", "Category", "Title", "Players",
                    "Tags", "Importance", "Notes"])
        for b in project.sorted_bookmarks():
            end = format_timecode(b.end_frame / fps, fps, frame=b.end_frame) if b.end_frame is not None else ""
            w.writerow([format_timecode(b.frame / fps, fps, frame=b.frame), end, b.frame,
                        b.end_frame if b.end_frame is not None else "", b.category, b.title,
                        "; ".join(b.players), "; ".join(b.tags), b.rating, b.notes])
