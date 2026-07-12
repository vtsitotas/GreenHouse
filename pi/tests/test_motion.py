import io
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))
import motion


def _solid_jpeg(color: int, size=(320, 240)) -> bytes:
    img = Image.new('L', size, color=color).convert('RGB')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    return buf.getvalue()


def test_downscale_grayscale_returns_expected_byte_length():
    jpeg = _solid_jpeg(128)
    gray = motion.downscale_grayscale(jpeg, size=(80, 60))
    assert len(gray) == 80 * 60


def test_diff_score_zero_for_identical_frames():
    jpeg = _solid_jpeg(100)
    gray = motion.downscale_grayscale(jpeg, size=(80, 60))
    assert motion.diff_score(gray, gray) == 0.0


def test_diff_score_high_for_very_different_frames():
    dark = motion.downscale_grayscale(_solid_jpeg(10), size=(80, 60))
    bright = motion.downscale_grayscale(_solid_jpeg(250), size=(80, 60))
    assert motion.diff_score(dark, bright) > 100.0


def test_diff_score_returns_zero_when_prev_is_none():
    curr = motion.downscale_grayscale(_solid_jpeg(50), size=(80, 60))
    assert motion.diff_score(None, curr) == 0.0


def test_is_motion_true_above_threshold():
    assert motion.is_motion(20.0, threshold=12.0) is True


def test_is_motion_false_below_threshold():
    assert motion.is_motion(5.0, threshold=12.0) is False


def test_is_motion_true_at_exact_threshold():
    assert motion.is_motion(12.0, threshold=12.0) is True
