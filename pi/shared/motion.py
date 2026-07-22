"""Grayscale-downscale frame diffing for camera motion detection.

Deliberately simple (mean absolute pixel difference over a small downscaled
grayscale frame) rather than any real computer-vision library — this project
runs on a Pi Zero W and only needs to answer "did something change enough to
be worth an alert," not track objects or classify motion.
"""
from io import BytesIO

from PIL import Image, ImageChops, ImageStat


def downscale_grayscale(jpeg_bytes: bytes, size: tuple[int, int] = (80, 60)) -> bytes:
    """Decode a JPEG and return its raw grayscale pixel bytes at `size`."""
    img = Image.open(BytesIO(jpeg_bytes)).convert('L').resize(size)
    return img.tobytes()


def diff_score(prev: bytes | None, curr: bytes, size: tuple[int, int] = (80, 60)) -> float:
    """Mean absolute pixel difference between two same-sized grayscale frames.

    `size` is the (width, height) the frames were downscaled to via
    `downscale_grayscale()` -- defaults to that function's own default
    (80, 60), matching how every caller in this codebase uses it today.

    Uses PIL (`ImageChops.difference` + `ImageStat.Stat`) instead of a
    per-pixel Python loop -- same "mean absolute difference per pixel"
    result, computed in C rather than interpreted Python, which matters on
    a Pi Zero W's single-core ARMv6.

    Returns 0.0 (no motion) when there's no previous frame yet, or if the
    frame sizes don't match (e.g. right after a resolution change).
    """
    if prev is None or len(prev) != len(curr):
        return 0.0
    if len(curr) != size[0] * size[1]:
        # curr isn't shaped like `size` (e.g. downscale_grayscale was called
        # with a non-default size but the caller didn't tell us) -- same
        # "can't meaningfully compare" case as a length mismatch above.
        return 0.0
    prev_img = Image.frombytes('L', size, prev)
    curr_img = Image.frombytes('L', size, curr)
    diff = ImageChops.difference(prev_img, curr_img)
    return ImageStat.Stat(diff).mean[0]


def is_motion(score: float, threshold: float = 12.0) -> bool:
    return score >= threshold
