"""Style guide models (blueprint §5.1): the deck-level design system learned
from a user's decks. Every field carries its provenance so downstream code
knows what came from native file data and what came from vision inference."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

PROVENANCE_NATIVE = "native"
PROVENANCE_VISION = "vision"
PROVENANCE_DEFAULT = "default"


class PaletteEntry(BaseModel):
    hex: str = Field(pattern=r"^#[0-9A-F]{6}$")
    usage: str  # primary | background | accent | text
    frequency: int = Field(ge=0)
    provenance: str = PROVENANCE_NATIVE


class TypographySpec(BaseModel):
    font_family: Optional[str] = None
    font_size_pt: Optional[float] = None
    font_weight: Optional[str] = None  # bold | normal | None when not stated
    color_hex: Optional[str] = Field(default=None, pattern=r"^#[0-9A-F]{6}$")
    sample_count: int = Field(ge=0)
    provenance: str = PROVENANCE_NATIVE


class Typography(BaseModel):
    title: Optional[TypographySpec] = None
    body: Optional[TypographySpec] = None


class Margins(BaseModel):
    top_px: Optional[float] = None
    right_px: Optional[float] = None
    bottom_px: Optional[float] = None
    left_px: Optional[float] = None


class LayoutGrid(BaseModel):
    slide_width_px: float
    slide_height_px: float
    margins_px: Margins = Field(default_factory=Margins)
    column_count: Optional[int] = None
    gutter_px: Optional[float] = None
    provenance: str = PROVENANCE_NATIVE


class ElementTreatments(BaseModel):
    card_corner_radius_px: Optional[float] = None
    shadow_intensity: Optional[str] = None
    border_width_px: Optional[float] = None
    background_style: Optional[str] = None
    provenance: str = PROVENANCE_NATIVE


class ContentRules(BaseModel):
    max_bullets_per_slide: Optional[int] = None
    max_words_per_bullet: Optional[int] = None
    max_title_length: Optional[int] = None
    preferred_density: Optional[str] = None  # low | medium | high
    avoid_repeating_layouts: bool = True
    provenance: str = PROVENANCE_NATIVE


class StyleGuide(BaseModel):
    deck_id: str
    deck_name: str
    extracted_at: str  # ISO 8601 UTC
    source_file_type: str
    source_slide_count: int
    aspect_ratio: str
    palette: List[PaletteEntry] = Field(default_factory=list)
    typography: Typography = Field(default_factory=Typography)
    layout_grid: LayoutGrid
    element_treatments: ElementTreatments = Field(default_factory=ElementTreatments)
    content_rules: ContentRules = Field(default_factory=ContentRules)
    warnings: List[str] = Field(default_factory=list)
