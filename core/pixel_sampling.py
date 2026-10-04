"""Exact colour measurement from rendered pixels (multi-format input).

PDF pages and PNG slides declare no palette in native data, so their colours
are measured from the rendered raster instead. Counting is exact, not
quantized: flat vector designs and their renders repeat the same 8-bit
colours verbatim, so counting raw pixel values recovers the true colour
frequencies. Sampling is strided (deterministic), not random.

Measured colours are reported separately from authored facts: parsers store
them in SlideInventory.measured_colors and the extractor labels their palette
provenance as "measured".
"""

from __future__ import annotations

from collections import Counter
from typing import List, Tuple

import pymupdf


def measure_colors(
    pixmap: pymupdf.Pixmap,
    max_colors: int = 6,
    sample_target: int = 60_000,
) -> List[Tuple[str, int]]:
    """Return the most common exact colours as (#RRGGBB, sampled count).

    Reads the pixmap's raw samples in its own component layout (8- or 16-bit
    channels, with or without alpha; the high byte is used for 16-bit).
    Zero-copy: no quantization, no colour-space conversion. The stride keeps
    wide images cheap while every row still contributes.
    """
    if pixmap.width <= 0 or pixmap.height <= 0:
        return []
    samples = pixmap.samples
    total = pixmap.width * pixmap.height
    pixel_bytes = len(samples) // total if total else 0
    channels = pixmap.n
    if pixel_bytes < channels or channels < 3:
        return []
    chan_step = pixel_bytes // channels

    stride = max(1, total // sample_target)
    counter: Counter = Counter()
    for index in range(0, total, stride):
        offset = index * pixel_bytes
        red = samples[offset]
        green = samples[offset + chan_step]
        blue = samples[offset + 2 * chan_step]
        counter[f"#{red:02X}{green:02X}{blue:02X}"] += 1
    return counter.most_common(max_colors)
