import os
import random

import pytest

from conftest import decode_frame_number
from fc27studio.core.video import VideoSource, VideoError, format_timecode


def test_info(bframe_video):
    with VideoSource(bframe_video) as v:
        assert v.frame_count == 150
        assert v.info.width == 320 and v.info.height == 180
        assert abs(v.fps - 30) < 0.01
        assert abs(v.info.duration - 5.0) < 0.01


@pytest.mark.parametrize("fixture", ["bframe_video", "mpeg4_video"])
def test_sequential_frames_exact(fixture, request):
    path = request.getfixturevalue(fixture)
    with VideoSource(path) as v:
        for i in range(v.frame_count):
            assert decode_frame_number(v.frame(i)) == i


@pytest.mark.parametrize("fixture", ["bframe_video", "mpeg4_video"])
def test_random_access_exact(fixture, request):
    path = request.getfixturevalue(fixture)
    rng = random.Random(7)
    with VideoSource(path, cache_frames=4) as v:
        for _ in range(120):
            i = rng.randrange(v.frame_count)
            assert decode_frame_number(v.frame(i)) == i


def test_backward_stepping(bframe_video):
    with VideoSource(bframe_video, cache_frames=4) as v:
        for i in range(v.frame_count - 1, -1, -1):
            assert decode_frame_number(v.frame(i)) == i


def test_keyframe_boundaries(bframe_video):
    with VideoSource(bframe_video, cache_frames=4) as v:
        for i in (0, 29, 30, 31, 59, 60, 119, 120, 148, 149):
            assert decode_frame_number(v.frame(i)) == i


def test_clamping(bframe_video):
    with VideoSource(bframe_video) as v:
        assert decode_frame_number(v.frame(-5)) == 0
        assert decode_frame_number(v.frame(10_000)) == 149


def test_time_conversion(bframe_video):
    with VideoSource(bframe_video) as v:
        assert v.seconds_to_frame(1.0) == 30
        assert v.frame_to_seconds(45) == pytest.approx(1.5)


def test_thumbnail(bframe_video):
    with VideoSource(bframe_video) as v:
        t = v.thumbnail(75, max_width=100)
        assert t.shape[1] <= 100


def test_missing_file():
    with pytest.raises(VideoError):
        VideoSource("/nonexistent/video.mp4")


def test_timecode():
    assert format_timecode(65.5, 30, frame=1965) == "01:05.15"
    assert format_timecode(3725, 60, frame=3725 * 60) == "01:02:05.00"
    assert format_timecode(5.0, show_frames=False) == "00:05"


def test_index_cache(bframe_video, tmp_path):
    from fc27studio.core.video import build_index, load_or_build_index
    cache = str(tmp_path / "idx")
    a = load_or_build_index(bframe_video, cache)
    assert len(os.listdir(cache)) == 1
    b = load_or_build_index(bframe_video, cache)
    ref = build_index(bframe_video)
    assert a.pts == b.pts == ref.pts and a.keyframe_pts == b.keyframe_pts == ref.keyframe_pts


def test_fast_forward_skipping_stays_exact(bframe_video):
    with VideoSource(bframe_video, cache_frames=8) as v:
        for i in range(0, v.frame_count, 7):   # like 4x playback dropping frames
            assert decode_frame_number(v.frame(i)) == i
        for i in range(3, v.frame_count, 23):
            assert decode_frame_number(v.frame(i)) == i
