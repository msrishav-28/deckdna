"""Deck generation schemas and deterministic planning (blueprint §8, §5.3).

The pipeline separates storyline planning from content: the outline planner
selects slide types and templates deterministically from the learned
template library, and the content generator fills slides using only the
user's topic and notes — never fabricated facts. Style-guide capacity
constraints are enforced by selecting material that fits and reporting
what was left out; the user's words are never truncated to force a fit.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Dict, List, Literal, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, Field

from core.extractor import _word_count
from core.style_guide import StyleGuide
from core.templates import (
    ROLE_ATTRIBUTION,
    ROLE_BODY,
    ROLE_LABEL,
    ROLE_STAT,
    ROLE_TITLE,
    TemplateRecord,
    TemplateSlot,
)

DEFAULT_MAX_BULLETS = 3
DEFAULT_MAX_WORDS_PER_BULLET = 12
DEFAULT_AGENDA_ITEMS = 5

_HEADING_MAX_WORDS = 6

PT_TO_PX = 96.0 / 72.0
AVG_CHAR_WIDTH_FACTOR = 0.55
LINE_HEIGHT_FACTOR = 1.3

_STAT_LINE_RE = re.compile(r"^(?P<value>\$?\d[\d,.]*%?)\s*[:\u2014-]\s*(?P<context>\S.+)$")
_QUOTE_PREFIX_RE = re.compile(r"^>\s*(?P<body>.+)$")
_ATTRIBUTION_RE = re.compile(r"^(?P<text>.+?)\s*[\u2014-]\s*(?P<who>\S[\S\s]*\S)\s*$")

_INTENT_BY_TYPE = {
    "title": "Set context with the deck title",
    "bullets": "Present key points as a scannable list",
    "stat": "Highlight one decisive number",
    "chart": "Show the number that matters with context",
    "quote": "Anchor the story in a voice",
    "comparison": "Contrast two options side by side",
    "agenda": "Preview the storyline",
    "section_divider": "Mark a section boundary or close the story",
    "closing": "End with thanks and the ask",
}

_CLOSER_PREFERENCE = ("closing", "section_divider", "quote")
_GENERIC_TYPES = ("section_divider", "closing", "agenda")


# -- schemas ---------------------------------------------------------------


class DeckBrief(BaseModel):
    topic: str = Field(min_length=1)
    audience: Optional[str] = None
    goal: Optional[str] = None
    slide_count: int = Field(default=5, ge=1, le=50)
    tone: Optional[str] = None
    notes: List[str] = Field(default_factory=list)
    style_guide_id: Optional[str] = None


class OutlineSlide(BaseModel):
    slide_number: int
    intent: str
    slide_type: str
    template_id: str
    title: Optional[str] = None
    subtitle: Optional[str] = None


class GenerationPlan(BaseModel):
    deck_title: str
    audience: Optional[str] = None
    tone: Optional[str] = None
    planner: str = "deterministic"
    slides: List[OutlineSlide] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class BulletBlock(BaseModel):
    type: Literal["bullet"] = "bullet"
    text: str


class StatBlock(BaseModel):
    type: Literal["stat"] = "stat"
    value: str
    context: Optional[str] = None
    label: Optional[str] = None


class QuoteBlock(BaseModel):
    type: Literal["quote"] = "quote"
    text: str
    attribution: Optional[str] = None


class SubtitleBlock(BaseModel):
    type: Literal["subtitle"] = "subtitle"
    text: str


ContentBlock = Annotated[
    Union[BulletBlock, StatBlock, QuoteBlock, SubtitleBlock],
    Field(discriminator="type"),
]


class SlideFix(BaseModel):
    """One bounded correction to a planned element (critique loop).

    Geometry deltas are canvas fractions; font_scale may only shrink text.
    Fixes persist with the deck, so a fixed deck re-renders identically
    in every format.
    """

    element_id: str
    font_scale: float = Field(default=1.0, gt=0.0, le=1.0)
    dx: float = Field(default=0.0, ge=-0.5, le=0.5)
    dy: float = Field(default=0.0, ge=-0.5, le=0.5)
    dw: float = Field(default=0.0, ge=-0.5, le=0.5)
    dh: float = Field(default=0.0, ge=-0.5, le=0.5)


class SlideContent(BaseModel):
    slide_id: str
    slide_number: int
    slide_type: str
    template_id: str
    title: Optional[str] = None
    content: List[ContentBlock] = Field(default_factory=list)
    fixes: List[SlideFix] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class GeneratedDeck(BaseModel):
    brief: DeckBrief
    plan: GenerationPlan
    slides: List[SlideContent] = Field(default_factory=list)
    style_guide_id: Optional[str] = None
    generator: str = "deterministic"
    warnings: List[str] = Field(default_factory=list)


# -- material analysis ------------------------------------------------------


class StatCandidate(BaseModel):
    value: str
    context: str


class QuoteCandidate(BaseModel):
    text: str
    attribution: Optional[str] = None


class DeckMaterial(BaseModel):
    headings: List[str] = Field(default_factory=list)
    bullets: List[str] = Field(default_factory=list)
    stats: List[StatCandidate] = Field(default_factory=list)
    quotes: List[QuoteCandidate] = Field(default_factory=list)
    unusable: List[str] = Field(default_factory=list)


def analyze_material(
    notes: Sequence[str], max_words_per_bullet: Optional[int]
) -> DeckMaterial:
    """Classify note lines into the material the generator may use.

    Lines that exceed the bullet word budget are excluded and reported,
    never shortened: the user's facts stay whole or stay out.
    """
    limit = max_words_per_bullet or DEFAULT_MAX_WORDS_PER_BULLET
    material = DeckMaterial()
    for raw in notes:
        line = raw.strip()
        if not line:
            continue
        quote_match = _QUOTE_PREFIX_RE.match(line)
        if quote_match:
            body = quote_match.group("body").strip().strip('"').strip("'")
            attribution_match = _ATTRIBUTION_RE.match(body)
            if attribution_match and _word_count(body) > 4:
                material.quotes.append(
                    QuoteCandidate(
                        text=attribution_match.group("text").strip(),
                        attribution=attribution_match.group("who").strip(),
                    )
                )
            else:
                material.quotes.append(QuoteCandidate(text=body))
            continue
        stat_match = _STAT_LINE_RE.match(line)
        if stat_match:
            material.stats.append(
                StatCandidate(
                    value=stat_match.group("value"),
                    context=stat_match.group("context").strip(),
                )
            )
            continue
        if _word_count(line) <= _HEADING_MAX_WORDS and not re.search(r"\d", line):
            material.headings.append(line)
            continue
        if _word_count(line) <= limit:
            material.bullets.append(line)
        else:
            material.unusable.append(line)
    return material


# -- capacity estimation ----------------------------------------------------


def _font_px(slot: TemplateSlot) -> Optional[float]:
    if slot.font_size_pt is None:
        return None
    return slot.font_size_pt * PT_TO_PX


def estimate_lines(
    text: str, slot: TemplateSlot, width_px: float, height_px: float
) -> Optional[int]:
    """Estimate rendered line count of text in a slot box, or None unknown."""
    font_px = _font_px(slot)
    if font_px is None or slot.width <= 0:
        return None
    chars_per_line = max(1, math.floor(slot.width * width_px / (font_px * AVG_CHAR_WIDTH_FACTOR)))
    lines = 0
    for paragraph in text.split("\n"):
        lines += max(1, math.ceil(len(paragraph) / chars_per_line))
    return lines


def available_lines(slot: TemplateSlot, height_px: float) -> Optional[int]:
    font_px = _font_px(slot)
    if font_px is None or slot.height <= 0:
        return None
    return max(0, math.floor(slot.height * height_px / (font_px * LINE_HEIGHT_FACTOR)))


def slot_by_role(template: TemplateRecord, role: str) -> Optional[TemplateSlot]:
    for slot in template.slots:
        if slot.role == role:
            return slot
    return None


# -- outline planning -------------------------------------------------------


class OutlineGenerator:
    """Deterministic storyline planner: picks types and templates from the
    learned library, schedules no identical layouts back to back, and uses
    only slide types the material can actually fill."""

    def plan(
        self, brief: DeckBrief, templates: Sequence[TemplateRecord], material: DeckMaterial
    ) -> GenerationPlan:
        warnings: List[str] = []
        by_type: Dict[str, TemplateRecord] = {}
        for record in templates:
            by_type.setdefault(record.slide_type, record)

        schedulable = self._schedulable_types(by_type, material)
        if brief.slide_count > 1 and not schedulable:
            warnings.append(
                "No schedulable content slide types for this material; the "
                "deck contains only structural slides."
            )

        slides: List[OutlineSlide] = []
        if "title" in by_type:
            slides.append(
                OutlineSlide(
                    slide_number=1,
                    intent=_INTENT_BY_TYPE["title"],
                    slide_type="title",
                    template_id=by_type["title"].template_id,
                    title=brief.topic,
                    subtitle=brief.goal,
                )
            )

        closer_type = None
        if brief.slide_count >= 3:
            closer_type = next(
                (t for t in _CLOSER_PREFERENCE if t in by_type), None
            )
        middle_needed = brief.slide_count - len(slides) - (1 if closer_type else 0)

        middle = self._middle_types(schedulable, middle_needed, closer_type, warnings)
        for pick in middle:
            slides.append(
                OutlineSlide(
                    slide_number=len(slides) + 1,
                    intent=_INTENT_BY_TYPE.get(pick, "Present content"),
                    slide_type=pick,
                    template_id=by_type[pick].template_id,
                )
            )

        if closer_type is not None:
            slides.append(
                OutlineSlide(
                    slide_number=len(slides) + 1,
                    intent=_INTENT_BY_TYPE[closer_type],
                    slide_type=closer_type,
                    template_id=by_type[closer_type].template_id,
                )
            )
        if len(slides) < brief.slide_count:
            warnings.append(
                f"Requested {brief.slide_count} slides but only {len(slides)} "
                "could be planned from the available templates."
            )

        return GenerationPlan(
            deck_title=brief.topic,
            audience=brief.audience,
            tone=brief.tone,
            planner="deterministic",
            slides=slides,
            warnings=warnings,
        )

    @staticmethod
    def _middle_types(
        schedulable: List[str],
        needed: int,
        closer_type: Optional[str],
        warnings: List[str],
    ) -> List[str]:
        """Pick middle slide types so no two adjacent layouts match —
        including against the closer that follows the last one. The walk
        runs backward from the closer, rotating through available types
        for variety; a repeat is forced only when a single type is
        schedulable, and that is warned about."""
        picked: List[str] = []
        forbidden: Optional[str] = closer_type
        pointer = 0
        repeats_warned = False
        while len(picked) < needed and schedulable:
            choice = None
            for _ in range(len(schedulable)):
                candidate = schedulable[pointer % len(schedulable)]
                pointer += 1
                if candidate != forbidden:
                    choice = candidate
                    break
            if choice is None:
                if not repeats_warned:
                    warnings.append(
                        "Only one schedulable content type; layouts repeat."
                    )
                    repeats_warned = True
                choice = schedulable[0]
            picked.append(choice)
            forbidden = choice
        picked.reverse()
        return picked

    @staticmethod
    def _schedulable_types(
        by_type: Dict[str, TemplateRecord], material: DeckMaterial
    ) -> List[str]:
        schedulable: List[str] = []
        if material.bullets and "bullets" in by_type:
            schedulable.append("bullets")
        if material.stats:
            if "stat" in by_type:
                schedulable.append("stat")
            if "chart" in by_type:
                schedulable.append("chart")
        if material.quotes and "quote" in by_type:
            schedulable.append("quote")
        if "comparison" in by_type and (
            len(material.bullets) >= 2 or len(material.headings) >= 2
        ):
            schedulable.append("comparison")
        if "agenda" in by_type and (material.bullets or material.headings):
            schedulable.append("agenda")
        for generic in _GENERIC_TYPES:
            if generic in by_type:
                schedulable.append(generic)
        return schedulable


# -- content generation -----------------------------------------------------


class ContentGenerator:
    """Fills planned slides with material from the brief. Every piece of
    factual content comes from the user's topic or notes; anything that does
    not fit a slot's capacity is skipped with a warning, never truncated."""

    def generate(
        self,
        brief: DeckBrief,
        plan: GenerationPlan,
        templates: Sequence[TemplateRecord],
        material: DeckMaterial,
        style_guide: StyleGuide,
    ) -> Tuple[List[SlideContent], List[str]]:
        """Fill planned slides. Returns the slides plus deck-level warnings;
        per-slide warnings ride on the slide they belong to so nothing is
        silently dropped even when no slides could be produced."""
        rules = style_guide.content_rules
        max_bullets = rules.max_bullets_per_slide or DEFAULT_MAX_BULLETS
        max_title = rules.max_title_length
        width = style_guide.layout_grid.slide_width_px
        height = style_guide.layout_grid.slide_height_px
        by_id = {record.template_id: record for record in templates}

        headings = list(material.headings)
        bullets = list(material.bullets)
        stats = list(material.stats)
        quotes = list(material.quotes)
        warnings: List[str] = []

        slides: List[SlideContent] = []
        for outline in plan.slides:
            template = by_id.get(outline.template_id)
            if template is None:
                warnings.append(
                    f"slide {outline.slide_number}: template "
                    f"'{outline.template_id}' missing from the library"
                )
                continue
            slide_warnings: List[str] = []
            slide, consumed = self._fill(
                outline, template, brief, headings, bullets, stats, quotes,
                max_bullets, max_title, width, height, slide_warnings,
            )
            headings, bullets, stats, quotes = consumed
            slides.append(slide)
            if max_title and slide.title and len(slide.title) > max_title:
                slide.warnings.append(
                    f"slide title exceeds the learned max of {max_title} characters"
                )

        leftovers = (
            ("heading", len(headings)),
            ("bullet", len(bullets)),
            ("stat", len(stats)),
            ("quote", len(quotes)),
        )
        unused = ", ".join(f"{count} {name}(s)" for name, count in leftovers if count)
        if unused:
            warnings.append(
                f"Material left unused after planning: {unused}. Add slides or "
                "shorten notes to include it."
            )
        if material.unusable:
            warnings.append(
                f"{len(material.unusable)} note line(s) exceeded the bullet word "
                "budget and were excluded: "
                + "; ".join(f"'{line[:40]}'" for line in material.unusable)
            )

        return slides, warnings

    # -- per-type fillers ---------------------------------------------------

    def _fill(
        self,
        outline: OutlineSlide,
        template: TemplateRecord,
        brief: DeckBrief,
        headings: List[str],
        bullets: List[str],
        stats: List[StatCandidate],
        quotes: List[QuoteCandidate],
        max_bullets: int,
        max_title: Optional[int],
        width: float,
        height: float,
        warnings: List[str],
    ) -> Tuple[SlideContent, Tuple[List[str], List[str], List[StatCandidate], List[QuoteCandidate]]]:
        filler = getattr(self, f"_fill_{outline.slide_type}", None)
        if filler is None:
            warnings.append(
                f"no content rule for slide type '{outline.slide_type}'; "
                "structural fill used"
            )
            slide = SlideContent(
                slide_id=f"slide_{outline.slide_number:02d}",
                slide_number=outline.slide_number,
                slide_type=outline.slide_type,
                template_id=outline.template_id,
                title=headings.pop(0) if headings else brief.topic,
                content=[SubtitleBlock(text=brief.topic)],
                warnings=warnings,
            )
            return slide, (headings, bullets, stats, quotes)

        title, blocks = filler(
            outline, template, brief, headings, bullets, stats, quotes,
            max_bullets, width, height, warnings,
        )
        slide = SlideContent(
            slide_id=f"slide_{outline.slide_number:02d}",
            slide_number=outline.slide_number,
            slide_type=outline.slide_type,
            template_id=outline.template_id,
            title=title,
            content=blocks,
            warnings=warnings,
        )
        return slide, (headings, bullets, stats, quotes)

    @staticmethod
    def _next_heading(headings: List[str], fallback: str) -> str:
        return headings.pop(0) if headings else fallback

    @staticmethod
    def _take_bullets(
        bullets: List[str],
        slot: Optional[TemplateSlot],
        limit: int,
        width: float,
        height: float,
        warnings: List[str],
    ) -> List[str]:
        """Take bullets that fit the slot capacity, stopping at the limit."""
        taken: List[str] = []
        capacity = available_lines(slot, height) if slot is not None else None
        used_lines = 0
        for candidate in bullets:
            if len(taken) >= limit:
                break
            lines = estimate_lines(candidate, slot, width, height) if slot is not None else 1
            if lines is None:
                lines = 1
            if capacity is not None and used_lines + lines > capacity:
                warnings.append(
                    f"bullet '{candidate[:40]}' skipped: body slot capacity reached"
                )
                continue
            taken.append(candidate)
            used_lines += lines
        for text in taken:
            bullets.remove(text)
        return taken

    def _fill_title(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        blocks = []
        subtitle = outline.subtitle or (
            f"Prepared for {brief.audience}" if brief.audience else None
        )
        if subtitle:
            blocks.append(SubtitleBlock(text=subtitle))
        return brief.topic, blocks

    def _fill_bullets(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        title = self._next_heading(headings, "Key points")
        body_slot = slot_by_role(template, ROLE_BODY)
        taken = self._take_bullets(bullets, body_slot, max_bullets, width, height, warnings)
        if not taken:
            warnings.append("no bullet material left for this slide")
        return title, [BulletBlock(text=text) for text in taken]

    def _fill_stat(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        if not stats:
            warnings.append("no stat material left for this slide")
            return self._next_heading(headings, brief.topic), []
        stat = stats.pop(0)
        return stat.context, [StatBlock(value=stat.value, context=stat.context)]

    def _fill_chart(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        return self._fill_stat(
            outline, template, brief, headings, bullets, stats, quotes,
            max_bullets, width, height, warnings,
        )

    def _fill_quote(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        if not quotes:
            warnings.append("no quote material left for this slide")
            return None, []
        quote = quotes.pop(0)
        return None, [QuoteBlock(text=quote.text, attribution=quote.attribution)]

    def _fill_comparison(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        title = self._next_heading(headings, "Side by side")
        body_slots = [s for s in template.slots if s.role == ROLE_BODY]
        taken = self._take_bullets(
            bullets,
            body_slots[0] if body_slots else None,
            max(len(body_slots), 1),
            width,
            height,
            warnings,
        )
        if not taken:
            warnings.append("no comparison material left for this slide")
        return title, [BulletBlock(text=text) for text in taken]

    def _fill_agenda(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        title = self._next_heading(headings, "Agenda")
        pool = headings + bullets
        body_slot = slot_by_role(template, ROLE_BODY)
        taken = self._take_bullets(
            pool, body_slot, DEFAULT_AGENDA_ITEMS, width, height, warnings
        )
        for text in taken:
            if text in headings:
                headings.remove(text)
            elif text in bullets:
                bullets.remove(text)
        return title, [BulletBlock(text=text) for text in taken]

    def _fill_section_divider(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        title = self._next_heading(headings, brief.topic)
        blocks = []
        if brief.goal:
            blocks.append(SubtitleBlock(text=brief.goal))
        return title, blocks

    def _fill_closing(
        self, outline, template, brief, headings, bullets, stats, quotes,
        max_bullets, width, height, warnings,
    ):
        return "Thank you", [SubtitleBlock(text=brief.topic)]


# -- orchestration ----------------------------------------------------------


def generate_deck(
    brief: DeckBrief,
    templates: Sequence[TemplateRecord],
    style_guide: StyleGuide,
    text_provider=None,
) -> GeneratedDeck:
    """Run the full generation pipeline. With text_provider (a configured
    Gemini text client) the outline and content wording come from the model,
    validated against the same constraints; otherwise generation is fully
    deterministic from the brief's own material."""
    material = analyze_material(
        brief.notes, style_guide.content_rules.max_words_per_bullet
    )
    plan_warnings: List[str] = []
    content_warnings: List[str] = []
    content_from_llm = False
    if text_provider is not None:
        from core.llm import content_with_llm, plan_with_llm

        plan, plan_warnings = plan_with_llm(text_provider, brief, templates, material)
        slides, content_from_llm, content_warnings = content_with_llm(
            text_provider, brief, plan, templates, material, style_guide
        )
    else:
        plan = OutlineGenerator().plan(brief, templates, material)
        slides, content_warnings = ContentGenerator().generate(
            brief, plan, templates, material, style_guide
        )
    return GeneratedDeck(
        brief=brief,
        plan=plan,
        slides=slides,
        style_guide_id=style_guide.deck_id,
        generator="gemini" if content_from_llm else "deterministic",
        warnings=plan_warnings + content_warnings,
    )
