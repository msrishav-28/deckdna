"""Raster parser: PNG files or a folder of PNG slides as a DeckInventory.

A flat image has no text layer, no theme and no internal geometry: the only
honest facts are the image size and its measured pixel colours. Each image
becomes one slide whose single shape is a full-bleed picture, and the
palette is measured from the pixels (core.pixel_sampling). Images declare
no physical size, so EMU values are derived at 96 pixels per inch and
labelled as such. Everything a raster cannot carry stays absent with a
warning instead of a guess.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import List

import pymupdf

from core.pixel_sampling import measure_colors
from core.pptx_parser import DeckParseError
from core.schemas import (
    ColorInfo,
    DeckInventory,
    Geometry,
    ShapeRecord,
    SlideInventory,
    SlideSize,
)

CANVAS_WIDTH_PX = 1920.0
EMU_PER_PIXEL_96DPI = 9525  # 914400 EMU per inch / 96 pixels per inch
_MAX_MEASURED_COLORS = 6


def sorted_slide_images(directory: Path) -> List[Path]:
    """PNG files in a directory, ordered by the last number in each name."""
    def number_in(path: Path):
        digits = re.findall(r"\d+", path.stem)
        return (int(digits[-1]) if digits else 0, path.name.lower())

    return sorted(
        (p for p in directory.iterdir() if p.suffix.lower() == ".png"), key=number_in
    )


def _slide_size(width_px: int, height_px: int) -> SlideSize:
    width_emu = width_px * EMU_PER_PIXEL_96DPI
    height_emu = height_px * EMU_PER_PIXEL_96DPI
    divisor = math.gcd(width_emu, height_emu) or 1
    return SlideSize(
        width_emu=width_emu,
        height_emu=height_emu,
        width_px=CANVAS_WIDTH_PX,
        height_px=round(CANVAS_WIDTH_PX * height_px / width_px, 2),
        aspect_ratio=f"{width_emu // divisor}:{height_emu // divisor}",
    )


class RasterParser:
    def parse(self, source: Path) -> DeckInventory:
        path = Path(source)
        if not path.exists():
            raise DeckParseError(f"File not found: {path}")
        if path.is_dir():
            images = sorted_slide_images(path)
            if not images:
                raise DeckParseError(f"No .png images found in {path}.")
        else:
            if path.suffix.lower() != ".png":
                raise DeckParseError(
                    f"Unsupported file type '{path.suffix}'. This parser reads "
                    ".png images or a folder of .png slides."
                )
            images = [path]

        slides: List[SlideInventory] = []
        slide_sizes: List[SlideSize] = []
        for number, image in enumerate(images, start=1):
            pixmap = self._load(image)
            slide_size = _slide_size(pixmap.width, pixmap.height)
            slide_sizes.append(slide_size)
            slides.append(
                SlideInventory(
                    slide_number=number,
                    layout_name="image",
                    measured_colors=[
                        ColorInfo(hex=hex_color)
                        for hex_color, _ in measure_colors(
                            pixmap, max_colors=_MAX_MEASURED_COLORS
                        )
                    ],
                    shapes=[self._shape(image.stem, slide_size)],
                )
            )

        first = slide_sizes[0]
        for number, size in enumerate(slide_sizes, start=1):
            if (size.width_emu, size.height_emu) != (
                first.width_emu,
                first.height_emu,
            ):
                slides[number - 1].warnings.append(
                    f"Image {number} is "
                    f"{size.width_emu // EMU_PER_PIXEL_96DPI}x"
                    f"{size.height_emu // EMU_PER_PIXEL_96DPI}px, different from "
                    "image 1; the deck canvas follows image 1."
                )

        return DeckInventory(
            source_file=str(path),
            slide_count=len(slides),
            slide_size=first,
            theme=None,
            warnings=[
                "Raster input: text content, fonts and internal layout are not "
                "extractable from pixels; only measured colours and image "
                "dimensions are (EMU values are derived at 96 pixels per inch "
                "because images declare no physical size). Run with "
                "--use-vision for layout descriptions.",
                "Raster input: the palette is measured from image pixels rather "
                "than declared in a file; usage labels are heuristics.",
            ],
            slides=slides,
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _load(image_path: Path) -> pymupdf.Pixmap:
        try:
            pixmap = pymupdf.Pixmap(str(image_path))
        except Exception as exc:
            raise DeckParseError(
                f"'{image_path.name}' is not a readable PNG image: {exc}"
            ) from exc
        if pixmap.width <= 0 or pixmap.height <= 0:
            raise DeckParseError(f"'{image_path.name}' has no pixel data.")
        if pixmap.n < 3 or pixmap.colorspace is None:
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
        return pixmap

    @staticmethod
    def _shape(name: str, slide_size: SlideSize) -> ShapeRecord:
        return ShapeRecord(
            shape_id=1,
            name=name,
            shape_type="PICTURE",
            z_index=0,
            geometry=Geometry(
                x_emu=0,
                y_emu=0,
                width_emu=slide_size.width_emu,
                height_emu=slide_size.height_emu,
                x_px=0.0,
                y_px=0.0,
                width_px=slide_size.width_px,
                height_px=slide_size.height_px,
            ),
            is_picture=True,
            picture_name=name,
            extraction_notes=[
                "Flat image input: only measured colours and image dimensions "
                "are available; text, fonts and internal geometry are not "
                "extractable from pixels."
            ],
        )
