"""HTML deck renderer (blueprint §8.4): turns a GeneratedDeck into one
self-contained HTML document — inline CSS, no external assets, no
scripts. Slide geometry comes from core.layout (shared with the PPTX
exporter) so the two outputs cannot drift; this module only serializes
planned boxes to positioned HTML. Text is escaped, colors come only from
validated hex values, and render_report() re-checks the geometry against
the same capacity estimators the generator used so overflow is reported
instead of hidden.
"""

from __future__ import annotations

import html
from typing import List, Optional, Sequence

from core.generation import (
    GeneratedDeck,
    PT_TO_PX,
    SlideContent,
    available_lines,
    estimate_lines,
)
from core.layout import (
    DEFAULT_BACKGROUND,
    RenderBox,
    canvas_size,
    font_family,
    palette_color,
    plan_slide,
)
from core.style_guide import StyleGuide
from core.templates import ROLE_CARD, TemplateRecord

_GENERIC_FONT_STACK = "'Segoe UI', 'Helvetica Neue', Arial, sans-serif"

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


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


def _px(value: float) -> str:
    return f"{value:.1f}"


def _font_stack(name: Optional[str]) -> str:
    if not name:
        return _GENERIC_FONT_STACK
    return f"'{name}', {_GENERIC_FONT_STACK}"


def _style_extras(box: RenderBox, style_guide: StyleGuide) -> str:
    if box.kind == "shape":
        extras = [f"background: {box.fill_hex}"]
        if box.base == ROLE_CARD:
            radius = style_guide.element_treatments.card_corner_radius_px
            if radius is not None and radius > 0:
                extras.append(f"border-radius: {_px(radius)}px")
        return "; ".join(extras)
    extras: List[str] = []
    if box.slot.bold is True:
        extras.append("font-weight: 700")
    elif box.slot.bold is False and box.kind == "title":
        extras.append("font-weight: 400")
    if box.slot.italic is True:
        extras.append("font-style: italic")
    return "; ".join(extras)


# -- serialization ----------------------------------------------------------


def _inner_html(box: RenderBox) -> str:
    if box.kind == "bullets":
        return "".join(f"<li>{_esc(part.text)}</li>" for part in box.parts)
    if box.kind == "stat":
        chunks: List[str] = []
        for part in box.parts:
            if part.part_kind == "stat-label":
                chunks.append(
                    f'<div class="stat-label" '
                    f'style="font-size: {_px(part.font_pt * PT_TO_PX)}px">'
                    f"{_esc(part.text)}</div>"
                )
            elif part.part_kind == "stat-value":
                chunks.append(f'<div class="stat-value">{_esc(part.text)}</div>')
            elif part.part_kind == "stat-context":
                chunks.append(
                    f'<div class="stat-context" '
                    f'style="font-size: {_px(part.font_pt * PT_TO_PX)}px">'
                    f"{_esc(part.text)}</div>"
                )
        return "".join(chunks)
    if box.kind == "quote":
        chunks = []
        for part in box.parts:
            if part.part_kind == "quote-text":
                chunks.append(f'<p class="quote-text">{_esc(part.text)}</p>')
            elif part.part_kind == "quote-attribution":
                chunks.append(
                    f'<footer class="quote-attribution" '
                    f'style="font-size: {_px(part.font_pt * PT_TO_PX)}px">'
                    f"\u2014 {_esc(part.text)}</footer>"
                )
        return "".join(chunks)
    return "".join(_esc(part.text) for part in box.parts)


def _render_box(
    box: RenderBox, style_guide: StyleGuide, canvas_w: float, canvas_h: float
) -> str:
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
            _style_extras(box, style_guide),
        )
        if part
    )
    attrs = (
        f'id="{_esc(box.element_id)}" class="{box.css_classes}" '
        f'data-role="{_esc(box.base)}" data-kind="{box.kind}"'
    )
    return f'<{box.tag} {attrs} style="{style}">{_inner_html(box)}</{box.tag}>'


def _render_section(
    slide: SlideContent,
    boxes: Sequence[RenderBox],
    style_guide: StyleGuide,
    canvas_w: float,
    canvas_h: float,
) -> str:
    style = (
        f"width: {_px(canvas_w)}px; height: {_px(canvas_h)}px; "
        f"background: {palette_color(style_guide, 'background', DEFAULT_BACKGROUND)};"
    )
    accent = palette_color(style_guide, "accent", None)
    if accent:
        style += f" --accent: {accent};"
    parts = [
        f'<section class="slide" id="{_esc(slide.slide_id)}" '
        f'data-slide-type="{_esc(slide.slide_type)}" '
        f'data-template-id="{_esc(slide.template_id)}" '
        f'style="{style}">'
    ]
    parts.extend(_render_box(box, style_guide, canvas_w, canvas_h) for box in boxes)
    parts.append("</section>")
    return "\n".join(parts)


def _stylesheet(style_guide: StyleGuide) -> str:
    return _CSS_TEMPLATE % {
        "title": _font_stack(font_family(style_guide, "title")),
        "body": _font_stack(font_family(style_guide, "body")),
    }


def render_deck_html(
    deck: GeneratedDeck,
    style_guide: StyleGuide,
    templates: Sequence[TemplateRecord],
) -> str:
    canvas_w, canvas_h = canvas_size(style_guide)
    by_id = {record.template_id: record for record in templates}
    sections: List[str] = []
    for slide in deck.slides:
        boxes, _ = plan_slide(slide, by_id.get(slide.template_id), style_guide)
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
    canvas_w, canvas_h = canvas_size(style_guide)
    by_id = {record.template_id: record for record in templates}
    report: List[str] = []
    for slide in deck.slides:
        boxes, warnings = plan_slide(
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
