"""Grayscale-downscale frame diffing for camera motion detection.

Deliberately simple (mean absolute pixel difference over a small downscaled
grayscale frame) rather than any real computer-vision library — this project
runs on a Pi Zero W and only needs to answer "did something change enough to
be worth an alert," not track objects or classify motion.
"""
from io import BytesIO

from PIL import Image


def downscale_grayscale(jpeg_bytes: bytes, size: tuple[int, int] = (80, 60)) -> bytes:
    """Decode a JPEG and return its raw grayscale pixel bytes at `size`."""
    img = Image.open(BytesIO(jpeg_bytes)).convert('L').resize(size)
    return img.tobytes()


def diff_score(prev: bytes | None, curr: bytes) -> float:
    """Mean absolute pixel difference between two same-sized grayscale frames.

    Returns 0.0 (no motion) when there's no previous frame yet, or if the
    frame sizes don't match (e.g. right after a resolution change).
    """
    if prev is None or len(prev) != len(curr):
        return 0.0
    total = sum(abs(p - c) for p, c in zip(prev, curr))
    return total / len(curr)


def is_motion(score: float, threshold: float = 12.0) -> bool:
    return score >= threshold
