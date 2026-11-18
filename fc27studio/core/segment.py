"""AI player selection: click a player, get their exact outline.

Uses MobileSAM (MIT licence) — a compact "Segment Anything" model — through
ONNX Runtime on the CPU. Players in a broadcast frame are small, so instead
of segmenting the whole frame the model looks at a zoomed crop around the
click; this roughly doubles accuracy on footballers compared with whole-frame
segmentation. Extra clicks (include / exclude) refine the result and reuse
the cached image embedding, so they take only a few tens of milliseconds.
"""
from __future__ import annotations

import hashlib
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

ENCODER = "mobile_sam_image_encoder.onnx"
DECODER = "sam_mask_decoder_multi.onnx"
MODEL_FILES = {
    ENCODER: "https://huggingface.co/Acly/MobileSAM/resolve/main/mobile_sam_image_encoder.onnx",
    DECODER: "https://huggingface.co/Acly/MobileSAM/resolve/main/sam_mask_decoder_multi.onnx",
}
INPUT_SIZE = 1024


def bundled_model_dir() -> str:
    """Models shipped with the app (PyInstaller) or in the source tree."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "fc27studio", "models")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


def find_model_dir(extra: Optional[list[str]] = None) -> Optional[str]:
    for d in [bundled_model_dir()] + list(extra or []):
        if d and all(os.path.isfile(os.path.join(d, f)) for f in (ENCODER, DECODER)):
            return d
    return None


def runtime_available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def resize_rgb(img: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    """Bilinear resize (numpy only)."""
    h, w = img.shape[:2]
    if (w, h) == (out_w, out_h):
        return img.astype(np.float32)
    ys = (np.arange(out_h) + 0.5) * h / out_h - 0.5
    xs = (np.arange(out_w) + 0.5) * w / out_w - 0.5
    y0 = np.clip(np.floor(ys).astype(int), 0, h - 1)
    x0 = np.clip(np.floor(xs).astype(int), 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    wy = np.clip(ys - y0, 0, 1)[:, None, None]
    wx = np.clip(xs - x0, 0, 1)[None, :, None]
    f = img.astype(np.float32)
    top = f[y0][:, x0] * (1 - wx) + f[y0][:, x1] * wx
    bot = f[y1][:, x0] * (1 - wx) + f[y1][:, x1] * wx
    return (top * (1 - wy) + bot * wy).astype(np.float32)


@dataclass
class Selection:
    """An in-progress AI selection on one frame."""
    crop: tuple[int, int, int, int]          # x, y, w, h in frame pixels
    points: list[tuple[float, float, int]] = field(default_factory=list)  # x, y, 1=include 0=exclude
    mask: Optional[np.ndarray] = None        # full-frame bool mask
    score: float = 0.0


class PlayerSegmenter:
    def __init__(self, model_dir: str, threads: int = 0):
        import onnxruntime as ort
        so = ort.SessionOptions()
        if threads:
            so.intra_op_num_threads = threads
        so.log_severity_level = 3
        prov = ["CPUExecutionProvider"]
        self.encoder = ort.InferenceSession(os.path.join(model_dir, ENCODER), so, providers=prov)
        self.decoder = ort.InferenceSession(os.path.join(model_dir, DECODER), so, providers=prov)
        self._lock = threading.Lock()
        self._cache_key = None
        self._embedding = None
        self._scale = 1.0
        self._crop_hw = (0, 0)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def crop_for(frame_shape, x: float, y: float, size: Optional[int] = None) -> tuple[int, int, int, int]:
        """Square crop around a click; the click sits a little above centre
        because people click on the shirt, and legs extend further down."""
        H, W = frame_shape[:2]
        if size is None:
            size = int(np.clip(H * 0.26, 160, 640))
        size = min(size, H, W)
        x0 = int(np.clip(x - size / 2, 0, W - size))
        y0 = int(np.clip(y - size * 0.42, 0, H - size))
        return x0, y0, size, size

    def _embed(self, frame: np.ndarray, crop, frame_key) -> None:
        key = (frame_key, crop)
        if key == self._cache_key:
            return
        x, y, w, h = crop
        sub = frame[y:y + h, x:x + w, :3]
        s = INPUT_SIZE / max(w, h)
        rw, rh = int(round(w * s)), int(round(h * s))
        resized = resize_rgb(sub, rw, rh)
        self._embedding = self.encoder.run(None, {"input_image": resized})[0]
        self._scale = s
        self._crop_hw = (h, w)
        self._cache_key = key

    def _decode(self, sel: Selection) -> None:
        x0, y0, _w, _h = sel.crop
        pts = [(px - x0, py - y0) for px, py, _l in sel.points] + [(0.0, 0.0)]
        labels = [float(l) for _x, _y, l in sel.points] + [-1.0]
        coords = np.array([pts], np.float32) * self._scale
        masks, iou, _low = self.decoder.run(None, {
            "image_embeddings": self._embedding,
            "point_coords": coords,
            "point_labels": np.array([labels], np.float32),
            "mask_input": np.zeros((1, 1, 256, 256), np.float32),
            "has_mask_input": np.zeros(1, np.float32),
            "orig_im_size": np.array(self._crop_hw, np.float32),
        })
        masks, iou = masks[0], iou[0]
        crop_mask, sel.score = pick_player_mask(masks, iou, self._crop_hw[0] * self._crop_hw[1])
        sel.mask = crop_mask

    # ------------------------------------------------------------- public
    def start(self, frame: np.ndarray, x: float, y: float, frame_key=None) -> Selection:
        """First click on a player."""
        with self._lock:
            sel = Selection(self.crop_for(frame.shape, x, y), [(x, y, 1)])
            self._embed(frame, sel.crop, frame_key)
            self._decode(sel)
            return sel

    def refine(self, frame: np.ndarray, sel: Selection, x: float, y: float, include: bool,
               frame_key=None) -> Selection:
        """Add an include/exclude click. Enlarges the crop if the click is outside it."""
        with self._lock:
            pts = sel.points + [(x, y, 1 if include else 0)]
            cx, cy, cw, ch = sel.crop
            if not (cx <= x < cx + cw and cy <= y < cy + ch):
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                need = int(max(max(xs) - min(xs), max(ys) - min(ys)) * 1.6) + 40
                crop = self.crop_for(frame.shape, (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                                     max(cw, need))
            else:
                crop = sel.crop
            new = Selection(crop, pts)
            self._embed(frame, crop, frame_key)
            self._decode(new)
            return new

    @staticmethod
    def full_mask(frame_shape, sel: Selection) -> np.ndarray:
        H, W = frame_shape[:2]
        out = np.zeros((H, W), bool)
        if sel.mask is not None:
            x, y, w, h = sel.crop
            out[y:y + h, x:x + w] = sel.mask[:h, :w]
        return out


def pick_player_mask(masks: np.ndarray, iou: np.ndarray, crop_area: float) -> tuple[np.ndarray, float]:
    """SAM proposes several masks (shirt, upper body, whole player…). Take the
    largest confident one — the whole player — unless it spills onto the pitch
    (then it would cover far more than a person could)."""
    order = sorted(range(len(iou)), key=lambda k: -int((masks[k] > 0).sum()))
    for k in order:
        area = int((masks[k] > 0).sum())
        if area and iou[k] >= 0.75 and area < 0.2 * crop_area:
            return masks[k] > 0, float(iou[k])
    k = int(np.argmax(iou))
    return masks[k] > 0, float(iou[k])


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
