"""Template records (blueprint §7): reusable layout skeletons learned from a
classified deck.

Each slot carries geometry as fractions of the slide canvas so a template
applies at any resolution, plus the native styling facts needed to rebuild
the look (font size, colors, weight). Only top-level shapes become slots;
shapes nested in groups keep group-relative coordinates that do not
translate to the canvas, so they are skipped with a warning.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from core.extractor import _resolved_hex, _word_count
from core.schemas import DeckInventory, ShapeRecord, SlideInventory, SlideSize
from core.slide_classifier import SlideClassification

ROLE_TITLE = "title"
ROLE_BODY = "body"
ROLE_STAT = "stat"
ROLE_LABEL = "label"
ROLE_ATTRIBUTION = "attribution"
ROLE_PICTURE = "picture"
ROLE_CHART = "chart"
ROLE_TABLE = "table"
ROLE_CARD = "card"
ROLE_DECORATIVE = "decorative"
ROLE_CONTAINER = "container"

_STAT_SIZE_RATIO = 1.6
_TITLE_MAX_WORDS = 12
_STAT_MAX_ALNUM = 6
_LABEL_MAX_WORDS = 6
_CARD_MIN_AREA_FRACTION = 0.05


class TemplateSlot(BaseModel):
    role: str
    shape_type: str
    x: float
    y: float
    width: float
    height: float
    font_size_pt: Optional[float] = None
    color_hex: Optional[str] = None
    fill_hex: Optional[str] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None


class TemplateRecord(BaseModel):
    template_id: str
    slide_type: str
    source_deck: str
    source_slide_number: int
    aspect_ratio: str
    slots: List[TemplateSlot] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


def build_templates(
    inventory: DeckInventory, classifications: List[SlideClassification]
) -> List[TemplateRecord]:
    if len(classifications) != inventory.slide_count:
        raise ValueError(
            f"{len(classifications)} classifications for "
            f"{inventory.slide_count} slides"
        )
    by_number = {c.slide_number: c for c in classifications}
    deck_name = Path(inventory.source_file).stem or "deck"
    records: List[TemplateRecord] = []
    for slide in inventory.slides:
        classification = by_number[slide.slide_number]
        records.append(_build_record(slide, classification, deck_name, inventory))
    return records


def _build_record(
    slide: SlideInventory,
    classification: SlideClassification,
    deck_name: str,
    inventory: DeckInventory,
) -> TemplateRecord:
    slide_size: SlideSize = inventory.slide_size
    warnings: List[str] = []
    nested = [shape for shape in slide.shapes if shape.depth > 0]
    if nested:
        warnings.append(
            f"{len(nested)} shape(s) nested in groups skipped "
            "(group-relative coordinates)"
        )

    sized = [
        (run, shape)
        for shape in slide.shapes
        if shape.text is not None
        for paragraph in shape.text.paragraphs
        for run in paragraph.runs
        if run.font_size_pt is not None
    ]
    sizes = sorted(run.font_size_pt for run, _ in sized)
    median = sizes[len(sizes) // 2] if sizes else None
    largest = sizes[-1] if sizes else None

    slots: List[TemplateSlot] = []
    for shape in slide.shapes:
        if shape.depth != 0:
            continue
        slot = _slot_for(
            shape, slide, inventory, slide_size, median, largest, sized
        )
        if slot is not None:
            slots.append(slot)

    slots.sort(key=lambda slot: (slot.y, slot.x))
    template_id = (
        f"{deck_name}-slide{slide.slide_number:03d}-{classification.slide_type}"
    )
    return TemplateRecord(
        template_id=re.sub(r"[^a-z0-9]+", "-", template_id.lower()),
        slide_type=classification.slide_type,
        source_deck=deck_name,
        source_slide_number=slide.slide_number,
        aspect_ratio=slide_size.aspect_ratio,
        slots=slots,
        warnings=warnings,
    )


_PLACEHOLDER_ROLES = {
    "TITLE": ROLE_TITLE,
    "CENTER_TITLE": ROLE_TITLE,
    "VERTICAL_TITLE": ROLE_TITLE,
    "SUBTITLE": ROLE_BODY,
    "BODY": ROLE_BODY,
    "CENTER_BODY": ROLE_BODY,
    "VERTICAL_BODY": ROLE_BODY,
    "OBJECT": ROLE_BODY,
    "VERTICAL_OBJECT": ROLE_BODY,
    "CONTENT": ROLE_BODY,
    "TEXT": ROLE_BODY,
}


def _placeholder_role(placeholder_type: str) -> str:
    """Map a PowerPoint placeholder type onto a template slot role."""
    return _PLACEHOLDER_ROLES.get(placeholder_type.upper(), ROLE_LABEL)


def _slot_for(
    shape: ShapeRecord,
    slide: SlideInventory,
    inventory: DeckInventory,
    slide_size: SlideSize,
    median: Optional[float],
    largest: Optional[float],
    sized,
) -> Optional[TemplateSlot]:
    geometry = shape.geometry
    base = TemplateSlot(
        role=ROLE_DECORATIVE,
        shape_type=shape.shape_type,
        x=round(geometry.x_px / slide_size.width_px, 4) if geometry else 0.0,
        y=round(geometry.y_px / slide_size.height_px, 4) if geometry else 0.0,
        width=round(geometry.width_px / slide_size.width_px, 4) if geometry else 0.0,
        height=round(geometry.height_px / slide_size.height_px, 4) if geometry else 0.0,
    )
    if geometry is None:
        return base

    if shape.has_chart:
        base.role = ROLE_CHART
        return base
    if shape.has_table:
        base.role = ROLE_TABLE
        return base
    if shape.is_picture:
        base.role = ROLE_PICTURE
        return base
    if shape.shape_type == "GROUP":
        base.role = ROLE_CONTAINER
        return base

    area_fraction = (
        geometry.width_px * geometry.height_px
    ) / (slide_size.width_px * slide_size.height_px)
    if shape.shape_type.startswith("AUTO_SHAPE"):
        base.role = ROLE_CARD if area_fraction >= _CARD_MIN_AREA_FRACTION else ROLE_DECORATIVE
        if shape.fill_color is not None:
            base.fill_hex = _resolved_hex(shape.fill_color, inventory)
        return base

    if shape.text is None or not shape.text.text.strip():
        return base

    shape_sizes = [run.font_size_pt for run, owner in sized if owner is shape]
    words = _word_count(shape.text.text)
    max_size = max(shape_sizes) if shape_sizes else None
    first_run = next(
        (
            run
            for paragraph in shape.text.paragraphs
            for run in paragraph.runs
        ),
        None,
    )

    if first_run is not None:
        base.font_size_pt = first_run.font_size_pt
        base.bold = first_run.bold
        base.italic = first_run.italic
        if first_run.color is not None:
            base.color_hex = _resolved_hex(first_run.color, inventory)

    text = shape.text.text.strip()
    if (
        max_size is not None
        and largest is not None
        and max_size == largest
        and median is not None
        and max_size >= _STAT_SIZE_RATIO * median
        and re.search(r"\d", text)
        and len(re.sub(r"[^0-9A-Za-z]", "", text)) <= _STAT_MAX_ALNUM
    ):
        base.role = ROLE_STAT
    elif len(shape.text.paragraphs) >= 2:
        base.role = ROLE_BODY
    elif max_size is not None and max_size == largest and words <= _TITLE_MAX_WORDS:
        base.role = ROLE_TITLE
    elif text.startswith(("\u2014", "--")):
        base.role = ROLE_ATTRIBUTION
    elif shape.is_placeholder and shape.placeholder_type:
        base.role = _placeholder_role(shape.placeholder_type)
    elif words <= _LABEL_MAX_WORDS:
        base.role = ROLE_LABEL
    else:
        base.role = ROLE_BODY
    return base


def find_templates(records: List[TemplateRecord], slide_type: str) -> List[TemplateRecord]:
    """Return templates of one slide type in build (source slide) order."""
    return [record for record in records if record.slide_type == slide_type]


def save_templates(records: List[TemplateRecord], output_path: Path) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "template_count": len(records),
        "templates": [record.model_dump() for record in records],
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def load_templates(input_path: Path) -> List[TemplateRecord]:
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    templates = data.get("templates") if isinstance(data, dict) else data
    if templates is None:
        raise ValueError(f"no templates found in {input_path}")
    return [TemplateRecord(**item) for item in templates]
