"""AI player selection (MobileSAM). Model-dependent tests are skipped when the
model has not been downloaded (python scripts/fetch_models.py)."""
import os

import numpy as np
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from fc27studio.core import segment
from fc27studio.core.imaging import (fill_holes, largest_component, make_cutout_from_mask,
                                     mask_to_polygon, polygon_mask, trace_outline)
from fc27studio.core.model import Kind

MODEL_DIR = segment.find_model_dir()
needs_model = pytest.mark.skipif(MODEL_DIR is None or not segment.runtime_available(),
                                 reason="AI model not downloaded")


# --------------------------------------------------------- mask utilities
def test_outline_matches_mask_exactly():
    m = np.zeros((50, 40), bool)
    m[5:40, 10:25] = True
    m[40:47, 12:16] = True          # a leg
    m[15:20, 14:18] = False         # a hole (filled for cut-outs)
    poly = mask_to_polygon(m, tol=0.01)
    cov = polygon_mask(poly, 0, 0, 40, 50) > 0.5
    filled = fill_holes(m)
    assert (cov == filled).all()


def test_largest_component_and_seed():
    m = np.zeros((20, 40), bool)
    m[2:6, 2:6] = True              # small blob
    m[5:18, 20:35] = True           # big blob
    assert largest_component(m)[10, 25] and not largest_component(m)[3, 3]
    assert largest_component(m, seed=(3, 3))[3, 3]


def test_cutout_from_mask():
    rng = np.random.default_rng(0)
    frame = (rng.integers(0, 20, (120, 160, 3)) + np.array([40, 120, 45])).astype(np.uint8)
    frame[30:90, 70:90] = (230, 30, 30)
    mask = np.zeros((120, 160), bool)
    mask[30:90, 70:90] = True
    cut, poly = make_cutout_from_mask(frame, mask)
    assert len(poly) >= 4
    a = cut.fill[..., 3:] / 255.0
    h, w = cut.fill.shape[:2]
    comp = frame[cut.y:cut.y + h, cut.x:cut.x + w] * (1 - a) + cut.fill[..., :3] * a
    assert ((comp[..., 0] > 150) & (comp[..., 1] < 90)).sum() == 0


def test_pick_player_mask_prefers_whole_player():
    masks = np.zeros((3, 100, 100), np.float32) - 1
    masks[0, 40:50, 45:55] = 1      # shirt
    masks[1, 30:80, 42:58] = 1      # whole player
    masks[2, :, :] = 1              # the entire pitch
    m, _ = segment.pick_player_mask(masks, np.array([0.95, 0.93, 0.9]), 100 * 100)
    assert m.sum() == 50 * 16


def test_crop_stays_inside_frame():
    for x, y in ((0, 0), (1919, 1079), (960, 540)):
        cx, cy, w, h = segment.PlayerSegmenter.crop_for((1080, 1920), x, y)
        assert 0 <= cx and 0 <= cy and cx + w <= 1920 and cy + h <= 1080


# ------------------------------------------------------------ the model
def synthetic_player_frame():
    """Green striped pitch with a simple 'player': shirt, shorts, legs, head."""
    H, W = 540, 960
    f = np.zeros((H, W, 3), np.uint8)
    for y in range(H):
        f[y] = (45, 125 + 12 * ((y // 45) % 2), 50)
    cx, top = 480, 220
    f[top:top + 16, cx - 7:cx + 7] = (205, 160, 130)          # head
    f[top + 16:top + 56, cx - 14:cx + 14] = (220, 30, 40)     # shirt
    f[top + 56:top + 72, cx - 13:cx + 13] = (240, 240, 240)   # shorts
    f[top + 72:top + 110, cx - 11:cx - 3] = (20, 20, 20)      # legs
    f[top + 72:top + 110, cx + 3:cx + 11] = (20, 20, 20)
    truth = np.zeros((H, W), bool)
    truth[top:top + 16, cx - 7:cx + 7] = True
    truth[top + 16:top + 56, cx - 14:cx + 14] = True
    truth[top + 56:top + 72, cx - 13:cx + 13] = True
    truth[top + 72:top + 110, cx - 11:cx - 3] = True
    truth[top + 72:top + 110, cx + 3:cx + 11] = True
    return f, truth, (cx, top + 30)


@needs_model
def test_model_selects_player():
    seg = segment.PlayerSegmenter(MODEL_DIR)
    f, truth, (x, y) = synthetic_player_frame()
    sel = seg.start(f, x, y, frame_key=1)
    mask = seg.full_mask(f.shape, sel)
    iou = (mask & truth).sum() / (mask | truth).sum()
    assert iou > 0.7, iou
    # An exclude click on the shorts removes them; include adds them back.
    sel2 = seg.refine(f, sel, x, 220 + 64, include=False, frame_key=1)
    m2 = seg.full_mask(f.shape, sel2)
    assert m2[220 + 64, x - 8:x + 8].sum() < mask[220 + 64, x - 8:x + 8].sum()


@needs_model
def test_click_to_cut_out_in_app(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Discard))
    import av
    from fractions import Fraction
    f, truth, (x, y) = synthetic_player_frame()
    path = str(tmp_path / "player.mp4")
    out = av.open(path, "w")
    s = out.add_stream("libx264", rate=25)
    s.width, s.height, s.pix_fmt = 960, 540, "yuv420p"
    s.options = {"crf": "12"}
    s.time_base = Fraction(1, 25)
    for i in range(10):
        fr = av.VideoFrame.from_ndarray(f, format="rgb24")
        fr.pts = i
        for pk in s.encode(fr):
            out.mux(pk)
    for pk in s.encode():
        out.mux(pk)
    out.close()
    from fc27studio.ui.main_window import MainWindow
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.open_video(path)
    import time
    end = time.time() + 5
    while w.canvas.image is None and time.time() < end:
        qapp.processEvents()
    w.choose_tool(Kind.CUTOUT)
    cv = w.canvas
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, cv.to_widget(QPointF(x, y)).toPoint())
    assert cv.has_ai_selection()
    QTest.keyClick(cv, Qt.Key_Return)
    cuts = [a for a in w.doc.project.annotations if a.kind == Kind.CUTOUT]
    assert len(cuts) == 1
    x0, y0, x1, y1 = cuts[0].bbox()
    assert x0 < x - 8 and x1 > x + 8 and y0 < 230 and y1 > 320   # head to feet
    w.doc.mark_clean()
    w.close()
