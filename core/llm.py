"""LLM text adapter and validated generation paths (blueprint §8, §14).

When a Gemini key is configured the model drafts the outline and the slide
copy; every response must then pass the same strict validation the
deterministic pipeline guarantees — slide types exist in the learned
template library, no identical layouts run back to back, word and capacity
limits hold, and every number, stat, and quote is grounded in the user's
own material. Any rejection falls back to the deterministic generator with
a warning that says why: model output never reaches a deck unvalidated.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import (
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    runtime_checkable,
)

from core.extractor import _word_count
from core.generation import (
    DEFAULT_MAX_BULLETS,
    DEFAULT_MAX_WORDS_PER_BULLET,
    BulletBlock,
    ContentBlock,
    ContentGenerator,
    DeckBrief,
    DeckMaterial,
    GenerationPlan,
    OutlineGenerator,
    OutlineSlide,
    QuoteBlock,
    SlideContent,
    StatBlock,
    SubtitleBlock,
    _INTENT_BY_TYPE,
    available_lines,
    estimate_lines,
    slot_by_role,
)
from core.style_guide import StyleGuide
from core.templates import ROLE_BODY, TemplateRecord
from core.vision import DEFAULT_GEMINI_MODEL, GEMINI_API_KEY_ENV, GEMINI_MODEL_ENV

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%?")

_BLOCK_MODELS = {
    "bullet": BulletBlock,
    "stat": StatBlock,
    "quote": QuoteBlock,
    "subtitle": SubtitleBlock,
}

_COMPATIBLE_BLOCKS = {
    "title": ("subtitle",),
    "section_divider": ("subtitle",),
    "closing": ("subtitle",),
    "bullets": ("bullet",),
    "comparison": ("bullet",),
    "agenda": ("bullet",),
    "stat": ("stat",),
    "chart": ("stat",),
    "quote": ("quote",),
}

_CONTENT_TYPES = ("bullets", "comparison", "agenda", "stat", "chart", "quote")


class TextProviderError(Exception):
    """Raised when a text provider cannot produce a response."""


class LLMValidationError(Exception):
    """Raised when model output does not meet the deterministic guarantees."""

    def __init__(self, problems: List[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@runtime_checkable
class TextProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    def generate_text(self, prompt: str) -> str: ...


def _summarize(problems: List[str], max_shown: int = 4) -> str:
    summary = "; ".join(problems[:max_shown])
    extra = len(problems) - max_shown
    if extra > 0:
        summary += f" (+{extra} more)"
    return summary


class GeminiTextProvider:
    """Gemini REST client for text. Needs GEMINI_API_KEY in .env or env."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get(GEMINI_MODEL_ENV) or DEFAULT_GEMINI_MODEL
        self.timeout_s = timeout_s

    @property
    def name(self) -> str:
        return f"gemini:{self.model}"

    def _key(self) -> Optional[str]:
        return self.api_key or os.environ.get(GEMINI_API_KEY_ENV)

    def is_configured(self) -> bool:
        return bool(self._key())

    def generate_text(self, prompt: str) -> str:
        api_key = self._key()
        if not api_key:
            raise TextProviderError(
                "Gemini API key not set. Create a free key at "
                "https://aistudio.google.com/apikey, then put "
                "GEMINI_API_KEY=your-key in a .env file (or the environment)."
            )
        if not prompt or not prompt.strip():
            raise TextProviderError("cannot send an empty prompt to the Gemini API")

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0},
        }
        request = urllib.request.Request(
            _GEMINI_URL.format(model=self.model),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise TextProviderError(
                f"Gemini API returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise TextProviderError(f"Gemini API request failed: {exc}") from exc

        return self._extract_text(body)

    def _extract_text(self, body: dict) -> str:
        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TextProviderError(
                f"Gemini API response missing candidate text: {str(body)[:300]}"
            ) from exc
        chunks = [part.get("text", "") for part in parts if isinstance(part, dict)]
        text = "".join(chunks).strip()
        if not text:
            raise TextProviderError("Gemini API returned an empty response")
        return text


def text_provider_from_environment() -> Optional[GeminiTextProvider]:
    """Return the configured text provider, or None when no key is set."""
    provider = GeminiTextProvider()
    return provider if provider.is_configured() else None


# -- prompts ----------------------------------------------------------------


def _material_summary(material: DeckMaterial) -> str:
    lines: List[str] = []
    if material.headings:
        lines.append("Headings: " + "; ".join(material.headings))
    if material.bullets:
        lines.append("Points: " + "; ".join(material.bullets))
    for stat in material.stats:
        lines.append(f"Stat: {stat.value} — {stat.context}")
    for quote in material.quotes:
        who = f" — {quote.attribution}" if quote.attribution else ""
        lines.append(f"Quote: {quote.text}{who}")
    if material.unusable:
        lines.append(
            "Excluded (over the word budget): " + "; ".join(material.unusable)
        )
    return "\n".join(lines)


def _brief_context(brief: DeckBrief) -> str:
    lines = [f"Topic: {brief.topic}"]
    if brief.audience:
        lines.append(f"Audience: {brief.audience}")
    if brief.goal:
        lines.append(f"Goal: {brief.goal}")
    if brief.tone:
        lines.append(f"Tone: {brief.tone}")
    return "\n".join(lines)


def _allowed_block_types(slide_type: str) -> Optional[Tuple[str, ...]]:
    return _COMPATIBLE_BLOCKS.get(slide_type)


def _outline_prompt(
    brief: DeckBrief,
    templates: Sequence[TemplateRecord],
    material: DeckMaterial,
) -> str:
    types = sorted({record.slide_type for record in templates})
    lines = [
        "You are planning the storyline of one presentation. Give every slide a "
        "single purpose. Respond with ONLY a JSON object, no markdown fences, no "
        "explanation, in this exact shape:",
        '{"slides": [{"slide_type": "...", "title": "...", "subtitle": "..."}]}',
        f"Plan exactly {brief.slide_count} slides.",
        "Allowed slide_type values: " + ", ".join(types) + ".",
        "Never place the same slide_type on two slides in a row.",
        "Use only the facts in the material; never invent statistics, sources, "
        "customers, or business facts.",
        _brief_context(brief),
        "Material:\n" + _material_summary(material),
    ]
    return "\n".join(lines)


def _content_prompt(
    brief: DeckBrief,
    plan: GenerationPlan,
    templates: Sequence[TemplateRecord],
    material: DeckMaterial,
    style_guide: StyleGuide,
) -> str:
    rules = style_guide.content_rules
    max_bullets = rules.max_bullets_per_slide or DEFAULT_MAX_BULLETS
    max_words = rules.max_words_per_bullet or DEFAULT_MAX_WORDS_PER_BULLET
    width = style_guide.layout_grid.slide_width_px
    height = style_guide.layout_grid.slide_height_px
    by_id = {record.template_id: record for record in templates}

    lines = [
        "You are writing the slide copy for one presentation. Respond with ONLY "
        "a JSON object, no markdown fences, no explanation, in this exact shape:",
        '{"slides": [{"title": "...", "content": [{"type": "bullet", '
        '"text": "..."}]}]}',
        f"Return exactly {len(plan.slides)} slides, in the order given below.",
        "Every number you write must appear in the material. Never invent "
        "statistics, sources, customers, or business facts.",
        f"At most {max_bullets} bullets per slide, at most {max_words} words "
        "per bullet.",
    ]
    if rules.max_title_length:
        lines.append(
            f"Slide titles are at most {rules.max_title_length} characters."
        )
    lines.append("Planned slides:")
    for slide in plan.slides:
        allowed = _allowed_block_types(slide.slide_type)
        hint = (
            "blocks: " + ", ".join(allowed) + "."
            if allowed
            else "block type of your choice."
        )
        detail = f"- Slide {slide.slide_number} ({slide.slide_type}): {hint}"
        template = by_id.get(slide.template_id)
        body = slot_by_role(template, ROLE_BODY) if template is not None else None
        capacity = available_lines(body, height) if body is not None else None
        if capacity is not None and body is not None and body.font_size_pt:
            detail += (
                f" Body slot fits about {capacity} lines at "
                f"{body.font_size_pt:g}pt."
            )
        lines.append(detail)
    lines.append(_brief_context(brief))
    lines.append("Material (the only allowed facts):\n" + _material_summary(material))
    return "\n".join(lines)


# -- parsing and validation -------------------------------------------------


def _extract_json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        raise LLMValidationError(
            [f"model response contained no JSON object: {text[:200]}"]
        )
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        raise LLMValidationError(
            [f"model response was not valid JSON: {text[:200]}"]
        ) from None
    if not isinstance(data, dict):
        raise LLMValidationError(["model response JSON was not an object"])
    return data


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _number_tokens(text: str) -> Set[str]:
    tokens: Set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0)
        tokens.add(token)
        if token.endswith("%"):
            tokens.add(token[:-1])
    return tokens


def _parse_outline(
    text: str, brief: DeckBrief, templates: Sequence[TemplateRecord]
) -> GenerationPlan:
    data = _extract_json_object(text)
    raw_slides = data.get("slides")
    if not isinstance(raw_slides, list):
        raise LLMValidationError(["response has no 'slides' list"])
    if len(raw_slides) != brief.slide_count:
        raise LLMValidationError(
            [f"expected {brief.slide_count} slides, got {len(raw_slides)}"]
        )

    by_type: Dict[str, TemplateRecord] = {}
    for record in templates:
        by_type.setdefault(record.slide_type, record)

    problems: List[str] = []
    slides: List[OutlineSlide] = []
    previous: Optional[str] = None
    for index, raw in enumerate(raw_slides):
        number = index + 1
        if not isinstance(raw, dict):
            problems.append(f"slide {number} is not an object")
            continue
        slide_type = raw.get("slide_type")
        if not isinstance(slide_type, str) or slide_type not in by_type:
            problems.append(f"slide {number} has unknown slide_type '{slide_type}'")
            continue
        if slide_type == previous:
            problems.append(
                f"slides {index} and {number} both use '{slide_type}' in a row"
            )
        previous = slide_type
        title = raw.get("title")
        if title is not None and not isinstance(title, str):
            problems.append(f"slide {number} has a non-text title")
            title = None
        subtitle = raw.get("subtitle")
        if subtitle is not None and not isinstance(subtitle, str):
            problems.append(f"slide {number} has a non-text subtitle")
            subtitle = None
        slides.append(
            OutlineSlide(
                slide_number=number,
                intent=_INTENT_BY_TYPE.get(slide_type, "Present content"),
                slide_type=slide_type,
                template_id=by_type[slide_type].template_id,
                title=title,
                subtitle=subtitle,
            )
        )
    if problems:
        raise LLMValidationError(problems)
    return GenerationPlan(
        deck_title=brief.topic,
        audience=brief.audience,
        tone=brief.tone,
        planner="llm",
        slides=slides,
    )


def _block_text(block: ContentBlock) -> Optional[str]:
    if isinstance(block, BulletBlock):
        return block.text
    if isinstance(block, StatBlock):
        return " ".join(part for part in (block.value, block.context) if part)
    if isinstance(block, QuoteBlock):
        return " ".join(part for part in (block.text, block.attribution) if part)
    if isinstance(block, SubtitleBlock):
        return block.text
    return None


def _build_block(
    raw: dict,
    block_type: str,
    number: int,
    stat_values: Set[str],
    normalized_notes: str,
    problems: List[str],
) -> Optional[ContentBlock]:
    text = raw.get("text")
    if block_type in ("bullet", "subtitle"):
        if not isinstance(text, str) or not text.strip():
            problems.append(f"slide {number} has a {block_type} without text")
            return None
        if block_type == "bullet":
            return BulletBlock(text=text.strip())
        return SubtitleBlock(text=text.strip())
    if block_type == "stat":
        value = raw.get("value")
        if not isinstance(value, str) or not value.strip():
            problems.append(f"slide {number} has a stat without a value")
            return None
        context = raw.get("context")
        if context is not None and not isinstance(context, str):
            problems.append(f"slide {number} has a stat with a non-text context")
            context = None
        if value.strip().lower() not in stat_values:
            problems.append(
                f"slide {number} stat value '{value.strip()}' does not appear in "
                "the material"
            )
        return StatBlock(
            value=value.strip(), context=context.strip() if context else None
        )
    if not isinstance(text, str) or not text.strip():
        problems.append(f"slide {number} has a quote without text")
        return None
    attribution = raw.get("attribution")
    if attribution is not None and not isinstance(attribution, str):
        problems.append(f"slide {number} has a quote with a non-text attribution")
        attribution = None
    if _normalize(text) not in normalized_notes:
        problems.append(
            f"slide {number} quote '{text.strip()[:60]}' not found in the notes"
        )
    if attribution and _normalize(attribution) not in normalized_notes:
        problems.append(
            f"slide {number} quote attribution '{attribution.strip()[:60]}' not "
            "found in the notes"
        )
    return QuoteBlock(
        text=text.strip(),
        attribution=attribution.strip() if attribution else None,
    )


def _parse_content(
    text: str,
    brief: DeckBrief,
    plan: GenerationPlan,
    templates: Sequence[TemplateRecord],
    material: DeckMaterial,
    style_guide: StyleGuide,
) -> List[SlideContent]:
    data = _extract_json_object(text)
    raw_slides = data.get("slides")
    if not isinstance(raw_slides, list):
        raise LLMValidationError(["response has no 'slides' list"])
    if len(raw_slides) != len(plan.slides):
        raise LLMValidationError(
            [f"expected {len(plan.slides)} slides, got {len(raw_slides)}"]
        )

    rules = style_guide.content_rules
    max_bullets = rules.max_bullets_per_slide or DEFAULT_MAX_BULLETS
    max_words = rules.max_words_per_bullet or DEFAULT_MAX_WORDS_PER_BULLET
    max_title = rules.max_title_length
    width = style_guide.layout_grid.slide_width_px
    height = style_guide.layout_grid.slide_height_px
    by_id = {record.template_id: record for record in templates}
    corpus = _number_tokens(
        " ".join(
            [brief.topic, brief.audience or "", brief.goal or ""] + list(brief.notes)
        )
    )
    stat_values = {stat.value.lower() for stat in material.stats}
    normalized_notes = _normalize(" ".join(brief.notes))

    problems: List[str] = []
    slides: List[SlideContent] = []
    for index, outline in enumerate(plan.slides):
        number = index + 1
        raw = raw_slides[index]
        if not isinstance(raw, dict):
            problems.append(f"slide {number} is not an object")
            continue

        title = raw.get("title")
        if title is not None and not isinstance(title, str):
            problems.append(f"slide {number} has a non-text title")
            title = None
        if title and max_title and len(title) > max_title:
            problems.append(
                f"slide {number} title '{title}' exceeds the learned max of "
                f"{max_title} characters"
            )
        if outline.slide_type == "title" and not title:
            problems.append(f"slide {number} is the title slide but has no title")

        raw_blocks = raw.get("content")
        if raw_blocks is None:
            raw_blocks = []
        if not isinstance(raw_blocks, list):
            problems.append(f"slide {number} has a non-list content field")
            raw_blocks = []

        allowed = _allowed_block_types(outline.slide_type)
        blocks: List[ContentBlock] = []
        for raw_block in raw_blocks:
            if not isinstance(raw_block, dict):
                problems.append(f"slide {number} has a malformed content block")
                continue
            block_type = raw_block.get("type")
            if block_type not in _BLOCK_MODELS:
                problems.append(
                    f"slide {number} has unknown block type '{block_type}'"
                )
                continue
            if allowed is not None and block_type not in allowed:
                problems.append(
                    f"slide {number} of type '{outline.slide_type}' cannot "
                    f"display a '{block_type}' block"
                )
                continue
            block = _build_block(
                raw_block, block_type, number, stat_values, normalized_notes,
                problems,
            )
            if block is not None:
                blocks.append(block)

        bullet_texts = [b.text for b in blocks if isinstance(b, BulletBlock)]
        if len(bullet_texts) > max_bullets:
            problems.append(
                f"slide {number} has {len(bullet_texts)} bullets (max {max_bullets})"
            )
        for bullet_text in bullet_texts:
            count = _word_count(bullet_text)
            if count > max_words:
                problems.append(
                    f"slide {number} bullet has {count} words (max {max_words})"
                )
        template = by_id.get(outline.template_id)
        if bullet_texts and template is not None:
            body = slot_by_role(template, ROLE_BODY)
            capacity = available_lines(body, height) if body is not None else None
            if capacity is not None:
                needed = sum(
                    estimate_lines(candidate, body, width, height) or 1
                    for candidate in bullet_texts
                )
                if needed > capacity:
                    problems.append(
                        f"slide {number} bullets need about {needed} lines but "
                        f"the body slot fits {capacity}"
                    )
        if not title and not blocks:
            problems.append(f"slide {number} has no title and no content")
        elif not blocks and outline.slide_type in _CONTENT_TYPES:
            problems.append(f"slide {number} has no content")

        texts = [
            text
            for text in [title] + [_block_text(block) for block in blocks]
            if text
        ]
        unknown = sorted(
            {token for text in texts for token in (_number_tokens(text) - corpus)}
        )
        if unknown:
            problems.append(
                f"slide {number} writes number(s) {', '.join(unknown)} that do "
                "not appear in the material"
            )

        slides.append(
            SlideContent(
                slide_id=f"slide_{number:02d}",
                slide_number=number,
                slide_type=outline.slide_type,
                template_id=outline.template_id,
                title=title,
                content=blocks,
            )
        )

    if problems:
        raise LLMValidationError(problems)
    return slides


# -- orchestration ----------------------------------------------------------


def plan_with_llm(
    provider: TextProvider,
    brief: DeckBrief,
    templates: Sequence[TemplateRecord],
    material: DeckMaterial,
) -> Tuple[GenerationPlan, List[str]]:
    """Ask the provider for the outline; return the validated plan or the
    deterministic fallback plus a warning that says why."""
    fallback = OutlineGenerator().plan(brief, templates, material)
    if not templates:
        return fallback, [
            "LLM outline skipped: no templates in the library; deterministic "
            "planning used instead."
        ]
    try:
        text = provider.generate_text(_outline_prompt(brief, templates, material))
        return _parse_outline(text, brief, templates), []
    except (TextProviderError, LLMValidationError) as exc:
        problems = exc.problems if isinstance(exc, LLMValidationError) else [str(exc)]
        return fallback, [
            f"LLM outline rejected ({_summarize(problems)}); deterministic "
            "planning used instead."
        ]


def content_with_llm(
    provider: TextProvider,
    brief: DeckBrief,
    plan: GenerationPlan,
    templates: Sequence[TemplateRecord],
    material: DeckMaterial,
    style_guide: StyleGuide,
) -> Tuple[List[SlideContent], bool, List[str]]:
    """Ask the provider for the slide copy; on any failure return the
    deterministic deck for the whole plan with used_llm False and a warning
    that says why."""
    try:
        text = provider.generate_text(
            _content_prompt(brief, plan, templates, material, style_guide)
        )
        slides = _parse_content(text, brief, plan, templates, material, style_guide)
        return slides, True, []
    except (TextProviderError, LLMValidationError) as exc:
        problems = exc.problems if isinstance(exc, LLMValidationError) else [str(exc)]
        slides, deck_warnings = ContentGenerator().generate(
            brief, plan, templates, material, style_guide
        )
        return slides, False, [
            f"LLM content rejected ({_summarize(problems)}); deterministic "
            "content used for the whole deck."
        ] + deck_warnings
