"""Shared slide layout planning (blueprint §8.4): turns a SlideContent plus
its learned template into positioned, styled boxes with stable element ids.

Both the HTML preview renderer and the PPTX exporter plan through this
module so the two outputs cannot drift apart. Boxes carry structured text
parts (plain strings with a meaning per part, never markup) plus canvas
fraction geometry; each renderer converts fractions and points to its own
native units. Element ids are stable (slide_02-title, slide_02-body-1) so
critique fixes can target elements deterministically in either format.
"""

from __future__ import annotations

import re
from collections import Counter
from math import ceil
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from core.generation import (
    BulletBlock,
    QuoteBlock,
    SlideContent,
    SlideFix,
    StatBlock,
    SubtitleBlock,
)
from core.style_guide import StyleGuide
from core.templates import (
    ROLE_BODY,
    ROLE_CARD,
    ROLE_CHART,
    ROLE_DECORATIVE,
    ROLE_LABEL,
    ROLE_PICTURE,
    ROLE_STAT,
    ROLE_TABLE,
    ROLE_TITLE,
    TemplateRecord,
    TemplateSlot,
)

ROLE_QUOTE = "quote"  # template libraries may label quote slots directly

DEFAULT_BACKGROUND = "#FFFFFF"
DEFAULT_TEXT = "#1F2937"
DEFAULT_CANVAS = (1920.0, 1080.0)

DEFAULT_FONT_PT = {
    "title": 40.0,
    "bullets": 20.0,
    "subtitle": 20.0,
    "stat": 54.0,
    "quote": 28.0,
    "placeholder": 12.0,
}

SLOT_PREFERENCES = {
    "title": (ROLE_TITLE,),
    "subtitle": (ROLE_BODY,),
    "stat": (ROLE_STAT, ROLE_BODY),
    "quote": (ROLE_QUOTE, ROLE_BODY, ROLE_LABEL),
}

# Relative font sizes inside a composed box (fraction of the box's font).
STAT_LABEL_SCALE = 0.3
STAT_CONTEXT_SCALE = 0.38
QUOTE_ATTRIBUTION_SCALE = 0.5
MIN_SUB_FONT_PT = 14.0

# Defensive clamps for critique fixes: text never renders below 8pt and a
# box never shrinks below 5% of the canvas in either dimension.
FIX_MIN_FONT_PT = 8.0
FIX_MIN_SLOT_FRACTION = 0.05

# Line-height factors the HTML stylesheet applies per part kind; the PPTX
# exporter reuses them so both outputs wrap text the same way.
LINE_HEIGHT = {
    "title": 1.15,
    "stat-value": 1.05,
    "default": 1.3,
}

_FALLBACK_SLOTS = {
    ROLE_TITLE: TemplateSlot(
        role=ROLE_TITLE, shape_type="TEXT_BOX",
        x=0.08, y=0.04, width=0.84, height=0.12,
    ),
    ROLE_BODY: TemplateSlot(
        role=ROLE_BODY, shape_type="TEXT_BOX",
        x=0.08, y=0.34, width=0.84, height=0.5,
    ),
    ROLE_STAT: TemplateSlot(
        role=ROLE_STAT, shape_type="TEXT_BOX",
        x=0.10, y=0.2, width=0.80, height=0.5,
    ),
    ROLE_QUOTE: TemplateSlot(
        role=ROLE_QUOTE, shape_type="TEXT_BOX",
        x=0.12, y=0.28, width=0.76, height=0.44,
    ),
}

PLACEHOLDER_LABELS = {
    ROLE_PICTURE: "Picture",
    ROLE_CHART: "Chart",
    ROLE_TABLE: "Table",
}

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FONT_NAME_RE = re.compile(r"[^A-Za-z0-9 \-_]")


class TextPart(NamedTuple):
    """One paragraph of a box: a meaning, plain text, and optionally its own
    font size (already resolved, in points)."""

    part_kind: str  # text | bullet | stat-* | quote-* | placeholder
    text: str
    font_pt: Optional[float] = None


class BoxSpec(NamedTuple):
    base: str
    kind: str
    tag: str
    slot: TemplateSlot
    font_pt: Optional[float]
    color: Optional[str]
    fill_hex: Optional[str]
    css_classes: str
    parts: Tuple[TextPart, ...]
    capacity_text: Optional[str]


class RenderBox(NamedTuple):
    element_id: str
    base: str
    kind: str
    tag: str
    slot: TemplateSlot
    font_pt: Optional[float]
    color: Optional[str]
    fill_hex: Optional[str]
    css_classes: str
    parts: Tuple[TextPart, ...]
    capacity_text: Optional[str]


# -- value helpers ----------------------------------------------------------


def safe_hex(value: Optional[str]) -> Optional[str]:
    if value and _HEX_RE.match(value):
        return value
    return None


def palette_color(
    style_guide: StyleGuide, usage: str, fallback: Optional[str]
) -> Optional[str]:
    best = None
    for entry in style_guide.palette:
        if entry.usage != usage:
            continue
        if best is None or entry.frequency > best.frequency:
            best = entry
    return best.hex if best is not None else fallback


def font_family(style_guide: StyleGuide, kind: str) -> Optional[str]:
    """The learned font family for a box kind, cleaned for safe output."""
    spec = _typography_spec(style_guide, kind)
    cleaned = _FONT_NAME_RE.sub("", spec.font_family or "").strip() if spec else ""
    return cleaned or None


def canvas_size(style_guide: StyleGuide) -> Tuple[float, float]:
    grid = style_guide.layout_grid
    if grid.slide_width_px > 0 and grid.slide_height_px > 0:
        return float(grid.slide_width_px), float(grid.slide_height_px)
    return DEFAULT_CANVAS


def _typography_spec(style_guide: StyleGuide, kind: str):
    if kind == "title":
        return style_guide.typography.title
    return style_guide.typography.body


def _resolve_font_pt(kind: str, slot: TemplateSlot, style_guide: StyleGuide) -> float:
    if slot.font_size_pt is not None and slot.font_size_pt > 0:
        return float(slot.font_size_pt)
    spec = _typography_spec(style_guide, kind)
    if spec is not None and spec.font_size_pt is not None and spec.font_size_pt > 0:
        return float(spec.font_size_pt)
    return DEFAULT_FONT_PT.get(kind, 20.0)


def _resolve_color(kind: str, slot: TemplateSlot, style_guide: StyleGuide) -> str:
    slot_hex = safe_hex(slot.color_hex)
    if slot_hex:
        return slot_hex
    spec = _typography_spec(style_guide, kind)
    if spec is not None:
        spec_hex = safe_hex(spec.color_hex)
        if spec_hex:
            return spec_hex
    return palette_color(style_guide, "text", DEFAULT_TEXT) or DEFAULT_TEXT


# -- element planning -------------------------------------------------------


def _text_spec(
    base: str,
    kind: str,
    tag: str,
    slot: TemplateSlot,
    style_guide: StyleGuide,
    parts: Sequence[TextPart],
    mods: Sequence[str] = (),
) -> BoxSpec:
    font_pt = _resolve_font_pt(kind, slot, style_guide)
    effective = slot.model_copy(update={"font_size_pt": font_pt})
    classes = " ".join(("el", "text", *mods))
    return BoxSpec(
        base=base,
        kind=kind,
        tag=tag,
        slot=effective,
        font_pt=font_pt,
        color=_resolve_color(kind, slot, style_guide),
        fill_hex=None,
        css_classes=classes,
        parts=tuple(parts),
        capacity_text="\n".join(part.text for part in parts),
    )


def _caption_slot(
    slots: Sequence[TemplateSlot], used: Set[int], stat_slot: TemplateSlot
) -> Optional[TemplateSlot]:
    """Nearest unused label slot under the stat box (learned caption pattern).

    Templates learned from real decks place the stat's caption as a small
    label directly below the big number; rendering the context there keeps
    the number slot inside its capacity instead of overflowing it.
    """
    best_index: Optional[int] = None
    best: Optional[TemplateSlot] = None
    stat_bottom = stat_slot.y + stat_slot.height
    for index, slot in enumerate(slots):
        if index in used or slot.role != ROLE_LABEL:
            continue
        if slot.y + 1e-3 < stat_bottom:
            continue
        if best is None or slot.y < best.y:
            best_index, best = index, slot
    if best_index is not None:
        used.add(best_index)
    return best


def _apply_slide_fixes(
    boxes: List[RenderBox], fixes: Sequence[SlideFix]
) -> Tuple[List[RenderBox], List[str]]:
    """Apply the slide's stored fixes to planned boxes, clamped to the
    canvas. Returns the adjusted boxes and the ids of fixes that matched
    no element (reported, never silently dropped)."""
    by_element: Dict[str, List[SlideFix]] = {}
    for fix in fixes:
        by_element.setdefault(fix.element_id, []).append(fix)
    adjusted: List[RenderBox] = []
    for box in boxes:
        pending = by_element.get(box.element_id)
        if not pending:
            adjusted.append(box)
            continue
        by_element.pop(box.element_id)
        slot = box.slot
        font_pt = box.font_pt
        parts = box.parts
        for fix in pending:
            width = min(max(slot.width + fix.dw, FIX_MIN_SLOT_FRACTION), 1.0)
            height = min(max(slot.height + fix.dh, FIX_MIN_SLOT_FRACTION), 1.0)
            x = min(max(slot.x + fix.dx, 0.0), 1.0 - width)
            y = min(max(slot.y + fix.dy, 0.0), 1.0 - height)
            slot = slot.model_copy(
                update={"x": x, "y": y, "width": width, "height": height}
            )
            if fix.font_scale != 1.0 and font_pt is not None:
                font_pt = max(font_pt * fix.font_scale, FIX_MIN_FONT_PT)
                slot = slot.model_copy(update={"font_size_pt": font_pt})
                parts = tuple(
                    part._replace(
                        font_pt=max(
                            part.font_pt * fix.font_scale, FIX_MIN_FONT_PT
                        )
                    )
                    if part.font_pt is not None
                    else part
                    for part in parts
                )
        adjusted.append(box._replace(slot=slot, font_pt=font_pt, parts=parts))
    return adjusted, list(by_element)


def plan_slide(
    slide: SlideContent,
    template: Optional[TemplateRecord],
    style_guide: StyleGuide,
) -> Tuple[List[RenderBox], List[str]]:
    warnings: List[str] = []
    specs: List[BoxSpec] = []
    slots = list(template.slots) if template is not None else []
    if template is None:
        warnings.append(
            f"{slide.slide_id}: template '{slide.template_id}' is not in the "
            "library; fallback slots were used"
        )

    used: Set[int] = set()

    def take_slot(preferences: Sequence[str]) -> Optional[TemplateSlot]:
        for index, slot in enumerate(slots):
            if index in used:
                continue
            if slot.role in preferences:
                used.add(index)
                return slot
        return None

    # background shapes and media placeholders paint first
    for index, slot in enumerate(slots):
        if slot.role in PLACEHOLDER_LABELS:
            used.add(index)
            specs.append(
                BoxSpec(
                    base=slot.role,
                    kind="placeholder",
                    tag="div",
                    slot=slot,
                    font_pt=DEFAULT_FONT_PT["placeholder"],
                    color=None,
                    fill_hex=None,
                    css_classes="el placeholder",
                    parts=(TextPart("placeholder", PLACEHOLDER_LABELS[slot.role]),),
                    capacity_text=None,
                )
            )
        elif slot.role in (ROLE_DECORATIVE, ROLE_CARD):
            fill = safe_hex(slot.fill_hex)
            if fill is None:
                continue
            specs.append(
                BoxSpec(
                    base=slot.role,
                    kind="shape",
                    tag="div",
                    slot=slot,
                    font_pt=None,
                    color=None,
                    fill_hex=fill,
                    css_classes="el shape",
                    parts=(),
                    capacity_text=None,
                )
            )

    if slide.title:
        slot = take_slot(SLOT_PREFERENCES["title"]) or _FALLBACK_SLOTS[ROLE_TITLE]
        specs.append(
            _text_spec(
                "title", "title", "h1", slot, style_guide,
                [TextPart("text", slide.title)], mods=("vcenter",),
            )
        )

    bullet_texts = [
        block.text for block in slide.content if isinstance(block, BulletBlock)
    ]
    if bullet_texts:
        body_indexes = [
            index
            for index, slot in enumerate(slots)
            if index not in used and slot.role == ROLE_BODY
        ]
        for index in body_indexes:
            used.add(index)
        body_slots = [slots[index] for index in body_indexes]
        if not body_slots:
            body_slots = [_FALLBACK_SLOTS[ROLE_BODY]]
        size = ceil(len(bullet_texts) / len(body_slots))
        for start in range(0, len(bullet_texts), size):
            chunk = bullet_texts[start:start + size]
            specs.append(
                _text_spec(
                    "body", "bullets", "ul", body_slots[start // size],
                    style_guide,
                    [TextPart("bullet", text) for text in chunk],
                    mods=("bullets",),
                )
            )

    for block in slide.content:
        if isinstance(block, SubtitleBlock):
            slot = (
                take_slot(SLOT_PREFERENCES["subtitle"])
                or _FALLBACK_SLOTS[ROLE_BODY]
            )
            specs.append(
                _text_spec(
                    "subtitle", "subtitle", "p", slot, style_guide,
                    [TextPart("text", block.text)], mods=("vcenter",),
                )
            )
        elif isinstance(block, StatBlock):
            slot = (
                take_slot(SLOT_PREFERENCES["stat"])
                or _FALLBACK_SLOTS[ROLE_STAT]
            )
            font_pt = _resolve_font_pt("stat", slot, style_guide)
            caption = (block.context or "").strip()
            if (
                caption
                and slide.title
                and caption.casefold() == slide.title.strip().casefold()
            ):
                caption = ""
            caption_slot = _caption_slot(slots, used, slot) if caption else None
            parts: List[TextPart] = []
            if block.label:
                parts.append(
                    TextPart(
                        "stat-label", block.label,
                        max(font_pt * STAT_LABEL_SCALE, MIN_SUB_FONT_PT),
                    )
                )
            parts.append(TextPart("stat-value", block.value))
            if caption and caption_slot is None:
                parts.append(
                    TextPart(
                        "stat-context", caption,
                        max(font_pt * STAT_CONTEXT_SCALE, MIN_SUB_FONT_PT),
                    )
                )
            specs.append(
                _text_spec(
                    "stat", "stat", "div", slot, style_guide,
                    parts, mods=("vcenter",),
                )
            )
            if caption and caption_slot is not None:
                specs.append(
                    _text_spec(
                        "stat-caption", "subtitle", "p", caption_slot,
                        style_guide, [TextPart("text", caption)],
                        mods=("vcenter",),
                    )
                )
        elif isinstance(block, QuoteBlock):
            slot = (
                take_slot(SLOT_PREFERENCES["quote"])
                or _FALLBACK_SLOTS[ROLE_QUOTE]
            )
            font_pt = _resolve_font_pt("quote", slot, style_guide)
            parts = [TextPart("quote-text", block.text)]
            if block.attribution:
                parts.append(
                    TextPart(
                        "quote-attribution", block.attribution,
                        max(font_pt * QUOTE_ATTRIBUTION_SCALE, MIN_SUB_FONT_PT),
                    )
                )
            specs.append(
                _text_spec(
                    "quote", "quote", "blockquote", slot, style_guide,
                    parts, mods=("vcenter",),
                )
            )

    if not specs:
        warnings.append(f"{slide.slide_id}: nothing to render")

    counts = Counter(spec.base for spec in specs)
    seen: Dict[str, int] = {}
    boxes: List[RenderBox] = []
    for spec in specs:
        seen[spec.base] = seen.get(spec.base, 0) + 1
        if counts[spec.base] > 1:
            element_id = f"{slide.slide_id}-{spec.base}-{seen[spec.base]}"
        else:
            element_id = f"{slide.slide_id}-{spec.base}"
        boxes.append(RenderBox(element_id=element_id, **spec._asdict()))
    if slide.fixes:
        boxes, unmatched = _apply_slide_fixes(boxes, slide.fixes)
        warnings.extend(
            f"{slide.slide_id}: fix targets unknown element '{element_id}'"
            for element_id in unmatched
        )
    return boxes, warnings
