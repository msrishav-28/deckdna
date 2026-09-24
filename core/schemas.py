"""Pydantic models for the PPTX structural inventory (Milestone 1).

Pixel coordinates are normalized to a 1920px-wide canvas that preserves the
deck's aspect ratio, matching the canvas convention in the blueprint (§5.1).
EMU values are the native OOXML units and are always recorded alongside.

Every optional field means "not present in the file or not inferable from
native data" — the parser never fabricates values to fill them.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class SlideSize(BaseModel):
    width_emu: int
    height_emu: int
    width_px: float
    height_px: float
    aspect_ratio: str


class ColorInfo(BaseModel):
    """A color exactly as found in the file: explicit hex, theme role, or a
    theme role with its best-effort resolved hex. Never invented."""

    hex: Optional[str] = Field(default=None, pattern=r"^#[0-9A-F]{6}$")
    theme_role: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "ColorInfo":
        if self.hex is None and self.theme_role is None:
            raise ValueError("ColorInfo must carry a hex value, a theme role, or both")
        return self


class TextRun(BaseModel):
    text: str
    font_family: Optional[str] = None
    font_size_pt: Optional[float] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    color: Optional[ColorInfo] = None


class ParagraphInfo(BaseModel):
    alignment: Optional[str] = None
    runs: List[TextRun] = Field(default_factory=list)


class TextInfo(BaseModel):
    text: str
    paragraphs: List[ParagraphInfo]


class Geometry(BaseModel):
    x_emu: int
    y_emu: int
    width_emu: int
    height_emu: int
    x_px: float
    y_px: float
    width_px: float
    height_px: float


class ShapeRecord(BaseModel):
    shape_id: int
    name: str
    shape_type: str
    z_index: int
    depth: int = 0
    geometry: Optional[Geometry] = None
    geometry_note: Optional[str] = None
    fill_color: Optional[ColorInfo] = None
    fill_note: Optional[str] = None
    line_color: Optional[ColorInfo] = None
    line_note: Optional[str] = None
    is_placeholder: bool = False
    placeholder_type: Optional[str] = None
    text: Optional[TextInfo] = None
    is_picture: bool = False
    picture_name: Optional[str] = None
    has_chart: bool = False
    has_table: bool = False
    table_dimensions: Optional[str] = None
    extraction_notes: List[str] = Field(default_factory=list)


class SlideInventory(BaseModel):
    slide_number: int
    layout_name: str
    background_color: Optional[ColorInfo] = None
    background_note: Optional[str] = None
    shapes: List[ShapeRecord] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ThemeInfo(BaseModel):
    colors: Dict[str, str] = Field(default_factory=dict)
    major_font: Optional[str] = None
    minor_font: Optional[str] = None


class DeckInventory(BaseModel):
    source_file: str
    slide_count: int
    slide_size: SlideSize
    theme: Optional[ThemeInfo] = None
    warnings: List[str] = Field(default_factory=list)
    slides: List[SlideInventory] = Field(default_factory=list)
