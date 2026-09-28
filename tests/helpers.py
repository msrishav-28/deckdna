"""Builders for synthetic inventories used by Phase C tests."""

from __future__ import annotations

from typing import List, Optional

from core.schemas import (
    ColorInfo,
    DeckInventory,
    Geometry,
    ParagraphInfo,
    ShapeRecord,
    SlideInventory,
    SlideSize,
    TextInfo,
    TextRun,
)

EMU_PER_PX = 6350

SLIDE_SIZE = SlideSize(
    width_emu=12192000,
    height_emu=6858000,
    width_px=1920.0,
    height_px=1080.0,
    aspect_ratio="16:9",
)


def geometry(x: float = 0.0, y: float = 0.0, w: float = 800.0, h: float = 200.0) -> Geometry:
    return Geometry(
        x_emu=int(x * EMU_PER_PX),
        y_emu=int(y * EMU_PER_PX),
        width_emu=int(w * EMU_PER_PX),
        height_emu=int(h * EMU_PER_PX),
        x_px=float(x),
        y_px=float(y),
        width_px=float(w),
        height_px=float(h),
    )


def run(
    text: str,
    size: Optional[float] = None,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    color: Optional[str] = None,
    family: Optional[str] = "Inter",
) -> TextRun:
    return TextRun(
        text=text,
        font_family=family,
        font_size_pt=size,
        bold=bold,
        italic=italic,
        color=ColorInfo(hex=color) if color else None,
    )


def text_shape(
    name: str,
    lines: List[List[TextRun]],
    x: float = 0.0,
    y: float = 0.0,
    w: float = 800.0,
    h: float = 200.0,
    shape_id: int = 1,
    z_index: int = 0,
    depth: int = 0,
    shape_type: str = "TEXT_BOX",
    fill: Optional[str] = None,
    extra: Optional[dict] = None,
) -> ShapeRecord:
    paragraphs = [ParagraphInfo(alignment=None, runs=runs) for runs in lines]
    joined = "\n".join("".join(r.text for r in runs) for runs in lines)
    fields = {
        "shape_id": shape_id,
        "name": name,
        "shape_type": shape_type,
        "z_index": z_index,
        "depth": depth,
        "geometry": geometry(x, y, w, h),
        "fill_color": ColorInfo(hex=fill) if fill else None,
        "text": TextInfo(text=joined, paragraphs=paragraphs),
    }
    if extra:
        fields.update(extra)
    return ShapeRecord(**fields)


def picture_shape(name: str, x: float, y: float, w: float, h: float, shape_id: int = 1) -> ShapeRecord:
    return ShapeRecord(
        shape_id=shape_id,
        name=name,
        shape_type="PICTURE",
        z_index=0,
        geometry=geometry(x, y, w, h),
        is_picture=True,
    )


def slide(number: int = 1, shapes: Optional[List[ShapeRecord]] = None) -> SlideInventory:
    return SlideInventory(
        slide_number=number,
        layout_name="Blank",
        shapes=shapes or [],
    )


def inventory(
    slides: List[SlideInventory],
    source: str = "synthetic.pptx",
    theme=None,
) -> DeckInventory:
    return DeckInventory(
        source_file=source,
        slide_count=len(slides),
        slide_size=SLIDE_SIZE,
        theme=theme,
        slides=slides,
    )
