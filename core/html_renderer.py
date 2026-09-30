"""HTML deck renderer (blueprint §8.4): turns a GeneratedDeck into one
self-contained HTML document — inline CSS, no external assets, no
scripts. Each slide is a fixed-size section at the learned canvas
resolution and every box is absolutely positioned from the template's
slot fractions. Element ids are stable (slide_02-title, slide_02-body-1)
so later critique fixes can target elements deterministically; text is
escaped, colors come only from validated hex values, and render_report()
re-checks the geometry against the same capacity estimators the generator
used so overflow is reported instead of hidden.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from math import ceil
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from core.generation import (
    BulletBlock,
    GeneratedDeck,
    PT_TO_PX,
    QuoteBlock,
    SlideContent,
    StatBlock,
    SubtitleBlock,
    available_lines,
    estimate_lines,
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

_ROLE_QUOTE = "quote"  # template libraries may label quote slots directly

_DEFAULT_BACKGROUND = "#FFFFFF"
_DEFAULT_TEXT = "#1F2937"
_DEFAULT_CANVAS = (1920.0, 1080.0)

_DEFAULT_FONT_PT = {
    "title": 40.0,
    "bullets": 20.0,
    "subtitle": 20.0,
    "stat": 54.0,
    "quote": 28.0,
    "placeholder": 12.0,
}

_SLOT_PREFERENCES = {
    "title": (ROLE_TITLE,),
    "subtitle": (ROLE_BODY,),
    "stat": (ROLE_STAT, ROLE_BODY),
    "quote": (_ROLE_QUOTE, ROLE_BODY, ROLE_LABEL),
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
    _ROLE_QUOTE: TemplateSlot(
        role=_ROLE_QUOTE, shape_type="TEXT_BOX",
        x=0.12, y=0.28, width=0.76, height=0.44,
    ),
}

_PLACEHOLDER_LABELS = {
    ROLE_PICTURE: "Picture",
    ROLE_CHART: "Chart",
    ROLE_TABLE: "Table",
}

_GENERIC_FONT_STACK = "'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FONT_NAME_RE = re.compile(r"[^A-Za-z0-9 \-_]")

_CSS_TEMPLATE = """
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { background: #1F2328; font-family: %(body)s; }
.deck { display: flex; flex-direction: column; align-items: center; gap: 32px; padding: 32px 0; width: max-content; margin: 0 auto; }
.slide { position: relative; overflow: hidden; flex: 0 0 auto; }
.el { position: absolute; margin: 0; padding: 0; overflow: hidden; overflow-wrap: break-word; line-height: 1.3; }
.el.text { display: flex; flex-direction: column; }
.el.vcenter { justify-content: center; }
h1.el { font-weight: 600; }
.el.title { font-family: %(title)s; line-height: 1.15; }
ul.bullets { list-style: none; }
ul.bullets li { position: relative; padding-left: 1.05em; }
ul.bullets li::before { content: "\u2022"; position: absolute; left: 0; color: var(--accent, currentColor); }
blockquote.el { border: 0; }
blockquote.el p { margin: 0; }
.quote-text { font-style: italic; }
.quote-attribution { margin: 0.5em 0 0; opacity: 0.72; }
.stat-value { font-weight: 700; line-height: 1.05; }
.stat-context { margin-top: 0.35em; opacity: 0.72; }
.stat-label { margin-bottom: 0.25em; opacity: 0.72; letter-spacing: 0.08em; text-transform: uppercase; }
.placeholder { display: flex; align-items: center; justify-content: center; border: 2px dashed rgba(128, 128, 128, 0.55); background: rgba(128, 128, 128, 0.12); color: rgba(96, 96, 96, 0.9); letter-spacing: 0.12em; text-transform: uppercase; }
"""


class _BoxSpec(NamedTuple):
    base: str
    kind: str
    tag: str
    slot: TemplateSlot
    font_pt: Optional[float]
    color: Optional[str]
    style_extra: str
    css_classes: str
    inner: str
    capacity_text: Optional[str]


class _RenderBox(NamedTuple):
    element_id: str
    base: str
    kind: str
    tag: str
    slot: TemplateSlot
    font_pt: Optional[float]
    color: Optional[str]
    style_extra: str
    css_classes: str
    inner: str
    capacity_text: Optional[str]


# -- value helpers ----------------------------------------------------------


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _px(value: float) -> str:
    return f"{value:.1f}"


def _safe_hex(value: Optional[str]) -> Optional[str]:
    if value and _HEX_RE.match(value):
        return value
    return None


def _palette_color(
    style_guide: StyleGuide, usage: str, fallback: Optional[str]
) -> Optional[str]:
    best = None
    for entry in style_guide.palette:
        if entry.usage != usage:
            continue
        if best is None or entry.frequency > best.frequency:
            best = entry
    return best.hex if best is not None else fallback


def _font_stack(name: Optional[str]) -> str:
    cleaned = _FONT_NAME_RE.sub("", name or "").strip()
    if not cleaned:
        return _GENERIC_FONT_STACK
    return f"'{cleaned}', {_GENERIC_FONT_STACK}"


def _canvas(style_guide: StyleGuide) -> Tuple[float, float]:
    grid = style_guide.layout_grid
    if grid.slide_width_px > 0 and grid.slide_height_px > 0:
        return float(grid.slide_width_px), float(grid.slide_height_px)
    return _DEFAULT_CANVAS


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
    return _DEFAULT_FONT_PT.get(kind, 20.0)


def _resolve_color(kind: str, slot: TemplateSlot, style_guide: StyleGuide) -> str:
    slot_hex = _safe_hex(slot.color_hex)
    if slot_hex:
        return slot_hex
    spec = _typography_spec(style_guide, kind)
    if spec is not None:
        spec_hex = _safe_hex(spec.color_hex)
        if spec_hex:
            return spec_hex
    return _palette_color(style_guide, "text", _DEFAULT_TEXT) or _DEFAULT_TEXT


# -- element planning -------------------------------------------------------


def _text_spec(
    base: str,
    kind: str,
    tag: str,
    slot: TemplateSlot,
    style_guide: StyleGuide,
    inner: str,
    capacity_text: Optional[str],
    mods: Sequence[str] = (),
) -> _BoxSpec:
    font_pt = _resolve_font_pt(kind, slot, style_guide)
    effective = slot.model_copy(update={"font_size_pt": font_pt})
    extras: List[str] = []
    if slot.bold is True:
        extras.append("font-weight: 700")
    elif slot.bold is False and kind == "title":
        extras.append("font-weight: 400")
    if slot.italic is True:
        extras.append("font-style: italic")
    classes = " ".join(("el", "text", *mods))
    return _BoxSpec(
        base=base,
        kind=kind,
        tag=tag,
        slot=effective,
        font_pt=font_pt,
        color=_resolve_color(kind, slot, style_guide),
        style_extra="; ".join(extras),
        css_classes=classes,
        inner=inner,
        capacity_text=capacity_text,
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


def _plan_slide(
    slide: SlideContent,
    template: Optional[TemplateRecord],
    style_guide: StyleGuide,
) -> Tuple[List[_RenderBox], List[str]]:
    warnings: List[str] = []
    specs: List[_BoxSpec] = []
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
        if slot.role in _PLACEHOLDER_LABELS:
            used.add(index)
            specs.append(
                _BoxSpec(
                    base=slot.role,
                    kind="placeholder",
                    tag="div",
                    slot=slot,
                    font_pt=_DEFAULT_FONT_PT["placeholder"],
                    color=None,
                    style_extra="",
                    css_classes="el placeholder",
                    inner=_esc(_PLACEHOLDER_LABELS[slot.role]),
                    capacity_text=None,
                )
            )
        elif slot.role in (ROLE_DECORATIVE, ROLE_CARD):
            fill = _safe_hex(slot.fill_hex)
            if fill is None:
                continue
            extras = [f"background: {fill}"]
            if slot.role == ROLE_CARD:
                radius = style_guide.element_treatments.card_corner_radius_px
                if radius is not None and radius > 0:
                    extras.append(f"border-radius: {_px(radius)}px")
            specs.append(
                _BoxSpec(
                    base=slot.role,
                    kind="shape",
                    tag="div",
                    slot=slot,
                    font_pt=None,
                    color=None,
                    style_extra="; ".join(extras),
                    css_classes="el shape",
                    inner="",
                    capacity_text=None,
                )
            )

    if slide.title:
        slot = take_slot(_SLOT_PREFERENCES["title"]) or _FALLBACK_SLOTS[ROLE_TITLE]
        specs.append(
            _text_spec(
                "title", "title", "h1", slot, style_guide,
                _esc(slide.title), slide.title, mods=("vcenter",),
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
            inner = "".join(f"<li>{_esc(text)}</li>" for text in chunk)
            specs.append(
                _text_spec(
                    "body", "bullets", "ul", body_slots[start // size],
                    style_guide, inner, "\n".join(chunk), mods=("bullets",),
                )
            )

    for block in slide.content:
        if isinstance(block, SubtitleBlock):
            slot = (
                take_slot(_SLOT_PREFERENCES["subtitle"])
                or _FALLBACK_SLOTS[ROLE_BODY]
            )
            specs.append(
                _text_spec(
                    "subtitle", "subtitle", "p", slot, style_guide,
                    _esc(block.text), block.text, mods=("vcenter",),
                )
            )
        elif isinstance(block, StatBlock):
            slot = (
                take_slot(_SLOT_PREFERENCES["stat"])
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
            parts: List[str] = []
            capacity_parts: List[str] = []
            if block.label:
                label_pt = max(font_pt * 0.3, 14.0)
                parts.append(
                    f'<div class="stat-label" '
                    f'style="font-size: {_px(label_pt * PT_TO_PX)}px">'
                    f"{_esc(block.label)}</div>"
                )
                capacity_parts.append(block.label)
            parts.append(f'<div class="stat-value">{_esc(block.value)}</div>')
            capacity_parts.append(block.value)
            if caption and caption_slot is None:
                context_pt = max(font_pt * 0.38, 14.0)
                parts.append(
                    f'<div class="stat-context" '
                    f'style="font-size: {_px(context_pt * PT_TO_PX)}px">'
                    f"{_esc(caption)}</div>"
                )
                capacity_parts.append(caption)
            specs.append(
                _text_spec(
                    "stat", "stat", "div", slot, style_guide,
                    "".join(parts), "\n".join(capacity_parts),
                    mods=("vcenter",),
                )
            )
            if caption and caption_slot is not None:
                specs.append(
                    _text_spec(
                        "stat-caption", "subtitle", "p", caption_slot,
                        style_guide, _esc(caption), caption, mods=("vcenter",),
                    )
                )
        elif isinstance(block, QuoteBlock):
            slot = (
                take_slot(_SLOT_PREFERENCES["quote"])
                or _FALLBACK_SLOTS[_ROLE_QUOTE]
            )
            font_pt = _resolve_font_pt("quote", slot, style_guide)
            inner = f'<p class="quote-text">{_esc(block.text)}</p>'
            capacity_parts = [block.text]
            if block.attribution:
                attribution_pt = max(font_pt * 0.5, 14.0)
                inner += (
                    f'<footer class="quote-attribution" '
                    f'style="font-size: {_px(attribution_pt * PT_TO_PX)}px">'
                    f"\u2014 {_esc(block.attribution)}</footer>"
                )
                capacity_parts.append(block.attribution)
            specs.append(
                _text_spec(
                    "quote", "quote", "blockquote", slot, style_guide,
                    inner, "\n".join(capacity_parts), mods=("vcenter",),
                )
            )

    if not specs:
        warnings.append(f"{slide.slide_id}: nothing to render")

    counts = Counter(spec.base for spec in specs)
    seen: Dict[str, int] = {}
    boxes: List[_RenderBox] = []
    for spec in specs:
        seen[spec.base] = seen.get(spec.base, 0) + 1
        if counts[spec.base] > 1:
            element_id = f"{slide.slide_id}-{spec.base}-{seen[spec.base]}"
        else:
            element_id = f"{slide.slide_id}-{spec.base}"
        boxes.append(_RenderBox(element_id=element_id, **spec._asdict()))
    return boxes, warnings


# -- serialization ----------------------------------------------------------


def _render_box(box: _RenderBox, canvas_w: float, canvas_h: float) -> str:
    slot = box.slot
    style = "; ".join(
        part
        for part in (
            f"left: {_px(slot.x * canvas_w)}px",
            f"top: {_px(slot.y * canvas_h)}px",
            f"width: {_px(slot.width * canvas_w)}px",
            f"height: {_px(slot.height * canvas_h)}px",
            f"font-size: {_px(box.font_pt * PT_TO_PX)}px"
            if box.font_pt is not None
            else "",
            f"color: {box.color}" if box.color else "",
            box.style_extra,
        )
        if part
    )
    attrs = (
        f'id="{_esc(box.element_id)}" class="{box.css_classes}" '
        f'data-role="{_esc(box.base)}" data-kind="{box.kind}"'
    )
    return f'<{box.tag} {attrs} style="{style}">{box.inner}</{box.tag}>'


def _render_section(
    slide: SlideContent,
    boxes: Sequence[_RenderBox],
    style_guide: StyleGuide,
    canvas_w: float,
    canvas_h: float,
) -> str:
    style = (
        f"width: {_px(canvas_w)}px; height: {_px(canvas_h)}px; "
        f"background: {_palette_color(style_guide, 'background', _DEFAULT_BACKGROUND)};"
    )
    accent = _palette_color(style_guide, "accent", None)
    if accent:
        style += f" --accent: {accent};"
    parts = [
        f'<section class="slide" id="{_esc(slide.slide_id)}" '
        f'data-slide-type="{_esc(slide.slide_type)}" '
        f'data-template-id="{_esc(slide.template_id)}" '
        f'style="{style}">'
    ]
    parts.extend(_render_box(box, canvas_w, canvas_h) for box in boxes)
    parts.append("</section>")
    return "\n".join(parts)


def _stylesheet(style_guide: StyleGuide) -> str:
    title_spec = style_guide.typography.title
    body_spec = style_guide.typography.body
    return _CSS_TEMPLATE % {
        "title": _font_stack(title_spec.font_family if title_spec else None),
        "body": _font_stack(body_spec.font_family if body_spec else None),
    }


def render_deck_html(
    deck: GeneratedDeck,
    style_guide: StyleGuide,
    templates: Sequence[TemplateRecord],
) -> str:
    canvas_w, canvas_h = _canvas(style_guide)
    by_id = {record.template_id: record for record in templates}
    sections: List[str] = []
    for slide in deck.slides:
        boxes, _ = _plan_slide(slide, by_id.get(slide.template_id), style_guide)
        sections.append(
            _render_section(slide, boxes, style_guide, canvas_w, canvas_h)
        )
    title = f"{deck.plan.deck_title} \u2014 {style_guide.deck_name}"
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            f"<title>{_esc(title)}</title>",
            f"<style>{_stylesheet(style_guide)}</style>",
            "</head>",
            "<body>",
            '<main class="deck">',
            *sections,
            "</main>",
            "</body>",
            "</html>",
            "",
        ]
    )


def render_report(
    deck: GeneratedDeck,
    style_guide: StyleGuide,
    templates: Sequence[TemplateRecord],
) -> List[str]:
    """Render-time warnings: missing templates and text that exceeds its
    slot's capacity under the same estimators the generator uses."""
    canvas_w, canvas_h = _canvas(style_guide)
    by_id = {record.template_id: record for record in templates}
    report: List[str] = []
    for slide in deck.slides:
        boxes, warnings = _plan_slide(
            slide, by_id.get(slide.template_id), style_guide
        )
        report.extend(warnings)
        for box in boxes:
            if box.capacity_text is None:
                continue
            check_slot = box.slot
            if box.kind == "bullets" and box.font_pt:
                marker_fraction = (1.05 * box.font_pt * PT_TO_PX) / canvas_w
                check_slot = check_slot.model_copy(
                    update={"width": max(check_slot.width - marker_fraction, 0.01)}
                )
            needed = estimate_lines(
                box.capacity_text, check_slot, canvas_w, canvas_h
            )
            capacity = available_lines(check_slot, canvas_h)
            if (
                needed is not None
                and capacity is not None
                and needed > capacity
            ):
                report.append(
                    f"{slide.slide_id} ({box.kind}): text needs about {needed} "
                    f"lines but the slot fits {capacity}"
                )
    return report
