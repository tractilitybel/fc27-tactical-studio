import os
import sys
from fractions import Fraction

import av
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BITS = 16
W, H = 320, 180


def encode_frame_number(n: int, w: int = W, h: int = H) -> np.ndarray:
    """Draw *n* as a row of black/white blocks: survives lossy compression."""
    img = np.full((h, w, 3), 128, np.uint8)
    bw = w // BITS
    for b in range(BITS):
        val = 255 if (n >> b) & 1 else 0
        img[h // 4: 3 * h // 4, b * bw:(b + 1) * bw] = val
    return img


def decode_frame_number(img: np.ndarray) -> int:
    h, w = img.shape[:2]
    bw = w // BITS
    n = 0
    for b in range(BITS):
        block = img[h // 4 + 4: 3 * h // 4 - 4, b * bw + 3:(b + 1) * bw - 3]
        if block.mean() > 128:
            n |= 1 << b
    return n


def make_video(path: str, frames: int = 150, fps: int = 30, codec: str = "libx264",
               bframes: int = 3, gop: int = 30) -> str:
    out = av.open(path, "w")
    s = out.add_stream(codec, rate=fps)
    s.width, s.height, s.pix_fmt = W, H, "yuv420p"
    if codec == "libx264":
        s.options = {"bf": str(bframes), "g": str(gop), "crf": "18",
                     "preset": "ultrafast" if bframes == 0 else "fast",
                     "x264-params": f"bframes={bframes}:keyint={gop}:scenecut=0"}
    else:
        s.options = {"bf": str(bframes), "g": str(gop)}
    s.time_base = Fraction(1, fps)
    for i in range(frames):
        f = av.VideoFrame.from_ndarray(encode_frame_number(i), format="rgb24")
        f.pts = i
        for p in s.encode(f):
            out.mux(p)
    for p in s.encode():
        out.mux(p)
    out.close()
    return path


@pytest.fixture(scope="session")
def bframe_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("vid")
    return make_video(str(d / "bframes.mp4"), frames=150, bframes=3)


@pytest.fixture(scope="session")
def mpeg4_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("vid")
    return make_video(str(d / "mpeg4.avi"), frames=90, codec="mpeg4", bframes=2, gop=25)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
