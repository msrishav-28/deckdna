"""Offline tests for the HTML deck renderer (blueprint §8.4). The artifact
under test is the preview document itself: fixed-size slide sections,
slot-driven geometry from template fractions, escaped text, stable
element ids the critique loop can target, and a render report that flags
overflow instead of hiding it.
"""

from __future__ import annotations

import re

from core.generation import (
    BulletBlock,
    DeckBrief,
    GeneratedDeck,
    GenerationPlan,
    OutlineSlide,
    SlideContent,
    StatBlock,
    generate_deck,
)
from core.html_renderer import render_deck_html, render_report
from core.style_guide import (
    ContentRules,
    LayoutGrid,
    PaletteEntry,
    StyleGuide,
    Typography,
    TypographySpec,
)
from core.templates import (
    ROLE_BODY,
    ROLE_LABEL,
    ROLE_PICTURE,
    TemplateRecord,
    TemplateSlot,
)


# -- builders ---------------------------------------------------------------


def _slot(role, x=0.05, y=0.1, w=0.9, h=0.3, size=18.0):
    return TemplateSlot(
        role=role, shape_type="TEXT_BOX", x=x, y=y, width=w, height=h,
        font_size_pt=size,
    )


def _template(slide_type, template_id, slots):
    return TemplateRecord(
        template_id=template_id,
        slide_type=slide_type,
        source_deck="synthetic",
        source_slide_number=1,
        aspect_ratio="16:9",
        slots=slots,
    )


def _library():
    return [
        _template("title", "tpl_title", [_slot("title", h=0.25)]),
        _template(
            "bullets",
            "tpl_bullets",
            [_slot("title", y=0.05, h=0.15), _slot(ROLE_BODY, y=0.3, h=0.5)],
        ),
        _template("stat", "tpl_stat", [_slot("stat", h=0.4)]),
        _template("quote", "tpl_quote", [_slot("quote", h=0.4)]),
        _template("section_divider", "tpl_divider", [_slot("title", h=0.3)]),
    ]


def _chart_template():
    # mirrors the caption pattern learned from sample_deck slide 3: a big
    # number slot with a small label slot underneath for its context
    return _template(
        "chart",
        "tpl_chart",
        [
            _slot("stat", x=0.0625, y=0.2778, w=0.3125, h=0.2037, size=96.0),
            _slot(ROLE_LABEL, x=0.0646, y=0.5, w=0.3125, h=0.0741, size=18.0),
        ],
    )


def _style_guide(palette=None, typo=None):
    return StyleGuide(
        deck_id="synthetic-deck",
        deck_name="synthetic",
        extracted_at="2026-01-01T00:00:00+00:00",
        source_file_type="pptx",
        source_slide_count=6,
        aspect_ratio="16:9",
        palette=palette or [],
        typography=typo or Typography(),
        layout_grid=LayoutGrid(slide_width_px=1920.0, slide_height_px=1080.0),
        content_rules=ContentRules(),
    )


NOTES = [
    "Revenue growth strategy",
    "Retention plans for next year",
    "Growth doubled after we changed the pricing model",
    "Support load fell with self serve docs",
    "> We ship every week \u2014 the engineering team",
    "82% \u2014 of teams ship weekly",
]


def _brief(notes=NOTES, slide_count=5):
    return DeckBrief(
        topic="Quarterly Review",
        audience="Leadership",
        goal="Align on next quarter",
        slide_count=slide_count,
        notes=list(notes),
    )


def _deck_from(slides):
    plan = GenerationPlan(
        deck_title="Synthetic deck",
        slides=[
            OutlineSlide(
                slide_number=slide.slide_number,
                intent="test slide",
                slide_type=slide.slide_type,
                template_id=slide.template_id,
                title=slide.title,
            )
            for slide in slides
        ],
    )
    return GeneratedDeck(brief=DeckBrief(topic="Synthetic deck"), plan=plan, slides=slides)


def _deterministic_deck():
    # material order: title -> quote -> stat -> bullets -> section_divider
    return generate_deck(_brief(), _library(), _style_guide())


# -- document under test ----------------------------------------------------


class TestRenderDeckHtml:
    def test_document_is_self_contained_and_has_every_slide(self):
        html = render_deck_html(_deterministic_deck(), _style_guide(), _library())
        assert html.startswith("<!DOCTYPE html>")
        assert html.rstrip().endswith("</html>")
        assert "<link" not in html
        assert "<script" not in html
        assert "None" not in html
        assert html.count('<section class="slide"') == 5
        for number in range(1, 6):
            assert f'id="slide_{number:02d}"' in html

    def test_element_ids_are_stable_and_unique(self):
        html = render_deck_html(_deterministic_deck(), _style_guide(), _library())
        ids = re.findall(r'id="([^"]+)"', html)
        assert len(ids) == len(set(ids))
        assert {"slide_01", "slide_01-title", "slide_01-subtitle"} <= set(ids)
        assert "slide_02-quote" in ids
        assert "slide_03-stat" in ids
        assert "slide_04-body" in ids
        assert "slide_05-title" in ids

    def test_text_is_escaped(self):
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_bullets",
            title="Ship <b>fast</b> weekly",
            content=[
                BulletBlock(text='Use <script>alert("x")</script> carefully')
            ],
        )
        html = render_deck_html(_deck_from([slide]), _style_guide(), _library())
        assert "<b>fast</b>" not in html
        assert "&lt;b&gt;fast&lt;/b&gt;" in html
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html

    def test_geometry_follows_template_fractions(self):
        html = render_deck_html(_deterministic_deck(), _style_guide(), _library())
        # tpl_bullets body slot: x=0.05, y=0.3, w=0.9, h=0.5 at 1920x1080
        assert (
            "left: 96.0px; top: 324.0px; width: 1728.0px; height: 540.0px"
            in html
        )
        assert "font-size: 24.0px" in html  # 18pt on the 96px/72pt scale

    def test_palette_and_typography_are_applied(self):
        guide = _style_guide(
            palette=[
                PaletteEntry(hex="#101820", usage="background", frequency=5),
                PaletteEntry(hex="#F5F5F5", usage="text", frequency=4),
                PaletteEntry(hex="#FFC857", usage="accent", frequency=2),
            ],
            typo=Typography(
                title=TypographySpec(
                    font_family="Trebuchet MS", color_hex="#FFD166",
                    sample_count=3,
                ),
                body=TypographySpec(font_family="Georgia", sample_count=3),
            ),
        )
        html = render_deck_html(_deterministic_deck(), guide, _library())
        assert "background: #101820" in html
        assert "--accent: #FFC857" in html
        assert "font-family: 'Trebuchet MS', 'Segoe UI'" in html
        assert "font-family: 'Georgia', 'Segoe UI'" in html
        assert "color: #FFD166" in html

    def test_stat_quote_and_subtitle_markup(self):
        html = render_deck_html(_deterministic_deck(), _style_guide(), _library())
        assert '<div class="stat-value">82%</div>' in html
        assert "of teams ship weekly" in html
        assert '<p class="quote-text">We ship every week</p>' in html
        assert "the engineering team" in html
        assert "Align on next quarter" in html

    def test_stat_context_moves_to_caption_slot_below(self):
        library = [_chart_template()]
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="chart",
            template_id="tpl_chart",
            title="Weekly shipping cadence",
            content=[StatBlock(value="82%", context="of teams ship weekly")],
        )
        deck = _deck_from([slide])
        html = render_deck_html(deck, _style_guide(), library)
        ids = re.findall(r'id="([^"]+)"', html)
        assert "slide_01-stat" in ids
        assert "slide_01-stat-caption" in ids
        assert re.search(
            r'<p id="slide_01-stat-caption"[^>]*top: 540\.0px[^>]*>'
            r"of teams ship weekly</p>",
            html,
        )
        assert '<div class="stat-context"' not in html
        assert '<div class="stat-value">82%</div>' in html
        assert render_report(deck, _style_guide(), library) == []

    def test_stat_context_matching_title_is_not_duplicated(self):
        library = [_chart_template()]
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="chart",
            template_id="tpl_chart",
            title="of teams ship weekly",
            content=[StatBlock(value="82%", context="of teams ship weekly")],
        )
        deck = _deck_from([slide])
        html = render_deck_html(deck, _style_guide(), library)
        ids = re.findall(r'id="([^"]+)"', html)
        assert "slide_01-stat-caption" not in ids
        assert '<div class="stat-context"' not in html
        assert '<div class="stat-value">82%</div>' in html
        assert "of teams ship weekly" in html
        assert render_report(deck, _style_guide(), library) == []

    def test_picture_slots_render_placeholders(self):
        template = _template(
            "bullets",
            "tpl_pic",
            [_slot("title", h=0.15), _slot(ROLE_PICTURE, y=0.3, h=0.6)],
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_pic",
            title="Has a picture",
            content=[BulletBlock(text="One point")],
        )
        html = render_deck_html(_deck_from([slide]), _style_guide(), [template])
        assert 'class="el placeholder"' in html
        assert 'data-role="picture"' in html
        assert ">Picture<" in html

    def test_multiple_body_slots_split_bullets_with_suffixed_ids(self):
        template = _template(
            "comparison",
            "tpl_cmp",
            [
                _slot(ROLE_BODY, x=0.05, y=0.1, w=0.4, h=0.8),
                _slot(ROLE_BODY, x=0.55, y=0.1, w=0.4, h=0.8),
            ],
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="comparison",
            template_id="tpl_cmp",
            content=[
                BulletBlock(text="Option A is cheaper"),
                BulletBlock(text="Option B is faster"),
                BulletBlock(text="Both need a pilot"),
            ],
        )
        html = render_deck_html(_deck_from([slide]), _style_guide(), [template])
        assert 'id="slide_01-body-1"' in html
        assert 'id="slide_01-body-2"' in html
        first = html.index("Option A is cheaper")
        second = html.index("Option B is faster")
        third = html.index("Both need a pilot")
        assert first < second < third

    def test_render_is_deterministic(self):
        deck = _deterministic_deck()
        first = render_deck_html(deck, _style_guide(), _library())
        second = render_deck_html(deck, _style_guide(), _library())
        assert first == second


# -- render report ----------------------------------------------------------


class TestRenderReport:
    def test_deterministic_simple_case_reports_no_overflow(self):
        report = render_report(_deterministic_deck(), _style_guide(), _library())
        assert report == []

    def test_overflow_is_reported(self):
        template = _template(
            "bullets",
            "tpl_small",
            [_slot("title", y=0.02, h=0.1), _slot(ROLE_BODY, y=0.2, w=0.3, h=0.06)],
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_small",
            title="Tight box",
            content=[BulletBlock(text=" ".join(["word"] * 90))],
        )
        report = render_report(_deck_from([slide]), _style_guide(), [template])
        assert any(
            "slide_01" in warning
            and "needs about" in warning
            and "fits" in warning
            for warning in report
        )

    def test_missing_template_is_reported(self):
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_ghost",
            title="No template",
            content=[BulletBlock(text="One point")],
        )
        report = render_report(_deck_from([slide]), _style_guide(), [])
        assert any("tpl_ghost" in warning for warning in report)
