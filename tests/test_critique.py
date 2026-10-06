"""Offline tests for the critique loop (blueprint §8.5, Milestone 6).

The critique loop inspects a generated deck the way the estimators see
it, applies a small bounded set of layout fixes (never facts), and
persists before/after evidence. These tests pin: slot selection, fix
application and clamping in the shared layout planner, the deterministic
audit, the fix planner's safety rules, loop termination, fact
preservation, and the optional vision path with a mock provider and with
the real Gemini adapter over a fake HTTP transport.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from core.critique import (
    AppliedFix,
    CritiqueReport,
    FixDecision,
    apply_decisions,
    audit_deck,
    plan_fixes,
    run_critique_loop,
)
from core.generation import (
    BulletBlock,
    DeckBrief,
    GeneratedDeck,
    GenerationPlan,
    OutlineSlide,
    SlideContent,
    SlideFix,
    StatBlock,
    SubtitleBlock,
)
from core.html_renderer import render_deck_html
from core.layout import plan_slide
from core.style_guide import (
    ContentRules,
    LayoutGrid,
    StyleGuide,
    Typography,
)
from core.templates import (
    ROLE_BODY,
    ROLE_LABEL,
    TemplateRecord,
    TemplateSlot,
)
from core.vision import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_MODEL_ENV,
    GeminiSlideCritic,
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


def _style_guide():
    return StyleGuide(
        deck_id="synthetic-deck",
        deck_name="synthetic",
        extracted_at="2026-01-01T00:00:00+00:00",
        source_file_type="pptx",
        source_slide_count=6,
        aspect_ratio="16:9",
        palette=[],
        typography=Typography(),
        layout_grid=LayoutGrid(slide_width_px=1920.0, slide_height_px=1080.0),
        content_rules=ContentRules(),
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
    return GeneratedDeck(
        brief=DeckBrief(topic="Synthetic deck"), plan=plan, slides=slides
    )


def _bullet_slide(
    title, *texts, slide_id="slide_01", slide_number=1, template_id="tpl_bullets"
):
    return SlideContent(
        slide_id=slide_id,
        slide_number=slide_number,
        slide_type="bullets",
        template_id=template_id,
        title=title,
        content=[BulletBlock(text=text) for text in texts],
    )


def _grow_template(template_id="tpl_bullets"):
    return _template(
        "bullets",
        template_id,
        [
            _slot("title", y=0.05, h=0.15, size=40.0),
            _slot(ROLE_BODY, y=0.25, h=0.10, size=20.0),
        ],
    )


def _hemmed_template(template_id="tpl_bullets"):
    return _template(
        "bullets",
        template_id,
        [
            _slot("title", y=0.28, h=0.10, size=18.0),
            _slot(ROLE_BODY, y=0.40, h=0.12, size=20.0),
            _slot(ROLE_BODY, y=0.54, h=0.44, size=20.0),
        ],
    )


def _bottom_template(body_size, template_id="tpl_bullets"):
    return _template(
        "bullets",
        template_id,
        [
            _slot("title", y=0.79, h=0.06, size=18.0),
            _slot(ROLE_BODY, y=0.87, h=0.11, size=body_size),
        ],
    )


def _overlap_template(second_x, template_id="tpl_bullets"):
    return _template(
        "bullets",
        template_id,
        [
            _slot(ROLE_BODY, x=0.05, y=0.10, w=0.5, h=0.6, size=20.0),
            _slot(ROLE_BODY, x=second_x, y=0.10, w=0.5, h=0.6, size=20.0),
        ],
    )


# -- fix application in the layout planner ----------------------------------


class TestSlideFixes:
    def test_font_scale_and_geometry_apply_to_the_planned_box(self):
        template = _template(
            "bullets",
            "tpl_bullets",
            [
                _slot("title", y=0.05, h=0.15, size=40.0),
                _slot(ROLE_BODY, y=0.3, h=0.5, size=20.0),
            ],
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_bullets",
            title="Key points",
            content=[BulletBlock(text="One")],
            fixes=[
                SlideFix(
                    element_id="slide_01-body",
                    font_scale=0.8,
                    dy=-0.05,
                    dh=0.1,
                )
            ],
        )
        boxes, warnings = plan_slide(slide, template, _style_guide())
        assert warnings == []
        body = next(b for b in boxes if b.element_id == "slide_01-body")
        assert body.font_pt == pytest.approx(16.0)
        assert body.slot.font_size_pt == pytest.approx(16.0)
        assert body.slot.y == pytest.approx(0.25)
        assert body.slot.height == pytest.approx(0.6)
        title = next(b for b in boxes if b.element_id == "slide_01-title")
        assert title.font_pt == pytest.approx(40.0)
        assert title.slot.y == pytest.approx(0.05)

    def test_fixes_are_clamped_to_the_canvas(self):
        template = _template(
            "bullets",
            "tpl_bullets",
            [_slot(ROLE_BODY, x=0.55, y=0.3, w=0.4, h=0.3, size=20.0)],
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_bullets",
            content=[BulletBlock(text="One")],
            fixes=[
                SlideFix(
                    element_id="slide_01-body",
                    dx=0.5,
                    dy=-0.5,
                    dw=-0.5,
                )
            ],
        )
        boxes, _ = plan_slide(slide, template, _style_guide())
        body = boxes[0]
        assert body.slot.width == pytest.approx(0.05)
        assert body.slot.x == pytest.approx(0.95)
        assert body.slot.y == pytest.approx(0.0)

    def test_sub_part_fonts_scale_with_the_box(self):
        template = _template(
            "stat", "tpl_stat", [_slot("stat", h=0.4, size=60.0)]
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="stat",
            template_id="tpl_stat",
            content=[StatBlock(value="82%", context="of teams ship weekly")],
            fixes=[SlideFix(element_id="slide_01-stat", font_scale=0.5)],
        )
        boxes, _ = plan_slide(slide, template, _style_guide())
        stat = boxes[0]
        assert stat.font_pt == pytest.approx(30.0)
        context = next(
            part for part in stat.parts if part.part_kind == "stat-context"
        )
        assert context.font_pt == pytest.approx(max(60.0 * 0.38, 14.0) * 0.5)

    def test_fix_for_unknown_element_is_reported(self):
        template = _template(
            "bullets", "tpl_bullets", [_slot(ROLE_BODY, size=20.0)]
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_bullets",
            content=[BulletBlock(text="One")],
            fixes=[SlideFix(element_id="slide_01-ghost", font_scale=0.9)],
        )
        boxes, warnings = plan_slide(slide, template, _style_guide())
        assert boxes[0].font_pt == pytest.approx(20.0)
        assert any("slide_01-ghost" in warning for warning in warnings)

    def test_fixes_reach_the_html_preview(self):
        template = _template(
            "bullets",
            "tpl_bullets",
            [_slot(ROLE_BODY, x=0.05, y=0.3, w=0.9, h=0.5, size=20.0)],
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_bullets",
            content=[BulletBlock(text="One")],
            fixes=[SlideFix(element_id="slide_01-body", font_scale=0.8)],
        )
        html = render_deck_html(
            _deck_from([slide]), _style_guide(), [template]
        )
        # 20pt * 0.8 = 16pt -> 21.333px on the 96px/72pt scale
        assert "font-size: 21.3px" in html

    def test_fixes_round_trip_through_content_json(self):
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="bullets",
            template_id="tpl_bullets",
            content=[BulletBlock(text="One")],
            fixes=[SlideFix(element_id="slide_01-body", font_scale=0.9, dh=0.05)],
        )
        restored = SlideContent.model_validate_json(slide.model_dump_json())
        assert restored.fixes == slide.fixes

    def test_hand_edited_out_of_range_fixes_are_rejected(self):
        with pytest.raises(ValueError):
            SlideFix(element_id="slide_01-body", font_scale=0.0)
        with pytest.raises(ValueError):
            SlideFix(element_id="slide_01-body", font_scale=1.5)
        with pytest.raises(ValueError):
            SlideFix(element_id="slide_01-body", dx=0.9)


# -- slot selection in the layout planner -----------------------------------


class TestSubtitleSlotPreference:
    """Title templates in real decks often carry no body slot; the subtitle
    must land in the template's own label slot rather than a generic fallback
    box, which can collide with the learned title slot."""

    def test_subtitle_uses_the_label_slot_when_no_body_slot_exists(self):
        template = _template(
            "title",
            "tpl_title",
            [
                _slot("title", x=0.062, y=0.259, w=0.625, h=0.185, size=44.0),
                _slot(ROLE_LABEL, x=0.065, y=0.463, w=0.521, h=0.074, size=20.0),
            ],
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="title",
            template_id="tpl_title",
            title="AI adoption roadmap for a university",
            content=[SubtitleBlock(text="Approve a two-year plan")],
        )
        boxes, warnings = plan_slide(slide, template, _style_guide())
        assert warnings == []
        subtitle = next(b for b in boxes if b.element_id == "slide_01-subtitle")
        assert subtitle.slot.y == pytest.approx(0.463)
        assert subtitle.slot.height == pytest.approx(0.074)
        assert subtitle.font_pt == pytest.approx(20.0)
        title = next(b for b in boxes if b.element_id == "slide_01-title")
        assert subtitle.slot.y >= title.slot.y + title.slot.height

    def test_body_slot_still_wins_when_both_exist(self):
        template = _template(
            "title",
            "tpl_title",
            [
                _slot("title", y=0.05, h=0.15, size=40.0),
                _slot(ROLE_BODY, y=0.30, h=0.20, size=20.0),
                _slot(ROLE_LABEL, y=0.60, h=0.10, size=18.0),
            ],
        )
        slide = SlideContent(
            slide_id="slide_01",
            slide_number=1,
            slide_type="title",
            template_id="tpl_title",
            title="Topic",
            content=[SubtitleBlock(text="A subtitle")],
        )
        boxes, _ = plan_slide(slide, template, _style_guide())
        subtitle = next(b for b in boxes if b.element_id == "slide_01-subtitle")
        assert subtitle.slot.y == pytest.approx(0.30)


# -- the deterministic audit ------------------------------------------------


class TestAudit:
    def test_overflow_finding_is_exact(self):
        deck = _deck_from([_bullet_slide("Key points", "word " * 100)])
        audit = audit_deck(deck, _style_guide(), [_grow_template()])
        state = audit.slides[0]
        assert state.score == 70
        assert state.warnings == []
        assert len(state.findings) == 1
        finding = state.findings[0]
        assert finding.code == "overflow"
        assert finding.severity == "high"
        assert finding.element_id == "slide_01-body"
        assert finding.detail == "text needs about 5 lines but the slot fits 3"

    def test_overlapping_boxes_are_high_severity(self):
        deck = _deck_from([_bullet_slide(None, "One", "Two")])
        audit = audit_deck(
            deck, _style_guide(), [_overlap_template(second_x=0.35)]
        )
        state = audit.slides[0]
        assert state.score == 70
        finding = state.findings[0]
        assert finding.code == "overlap"
        assert finding.severity == "high"
        assert finding.detail == (
            "overlaps 'slide_01-body-2' on 40% of the smaller box"
        )

    def test_mild_overlap_is_medium_severity(self):
        deck = _deck_from([_bullet_slide(None, "One", "Two")])
        audit = audit_deck(
            deck, _style_guide(), [_overlap_template(second_x=0.50)]
        )
        state = audit.slides[0]
        assert state.score == 88
        finding = state.findings[0]
        assert finding.code == "overlap"
        assert finding.severity == "medium"
        assert finding.detail == (
            "overlaps 'slide_01-body-2' on 10% of the smaller box"
        )

    def test_tiny_fonts_are_flagged(self):
        template = _template(
            "bullets", "tpl_bullets", [_slot(ROLE_BODY, size=10.0)]
        )
        deck = _deck_from([_bullet_slide(None, "One")])
        audit = audit_deck(deck, _style_guide(), [template])
        state = audit.slides[0]
        assert state.score == 96
        assert len(state.findings) == 1
        finding = state.findings[0]
        assert finding.code == "tiny_font"
        assert finding.severity == "low"
        assert finding.detail == "font 10.0pt is below the 12pt floor"

    def test_deck_score_is_the_worst_slide(self):
        deck = _deck_from(
            [
                _bullet_slide(None, "One", "Two", template_id="tpl_overlap"),
                _bullet_slide(
                    "Key points",
                    "Short point",
                    slide_id="slide_02",
                    slide_number=2,
                    template_id="tpl_clean",
                ),
            ]
        )
        audit = audit_deck(
            deck,
            _style_guide(),
            [
                _overlap_template(second_x=0.35, template_id="tpl_overlap"),
                _grow_template(template_id="tpl_clean"),
            ],
        )
        assert [state.score for state in audit.slides] == [70, 100]
        assert audit.score == 70

    def test_a_clean_deck_audits_clean(self):
        deck = _deck_from([_bullet_slide("Key points", "Short point")])
        audit = audit_deck(deck, _style_guide(), [_bottom_template(12.0)])
        state = audit.slides[0]
        assert state.score == 100
        assert state.findings == []
        assert state.warnings == []


# -- loop behavior ----------------------------------------------------------


class TestCritiqueLoop:
    def test_growth_rescue(self):
        deck = _deck_from([_bullet_slide("Key points", "word " * 100)])
        template = _grow_template()
        fixes = plan_fixes(deck, _style_guide(), [template])
        assert len(fixes) == 1
        assert fixes[0].action == "resize_element"
        assert fixes[0].element_id == "slide_01-body"
        assert fixes[0].detail == "height 0.10 -> 0.17 of the canvas"
        assert fixes[0].dh == pytest.approx(0.068)
        assert fixes[0].font_scale == 1.0
        updated, report = run_critique_loop(deck, _style_guide(), [template])
        assert report.stop_reason == "threshold_met"
        assert report.score_before == 70
        assert report.score_after == 100
        assert len(report.iterations) == 1
        assert report.iterations[0].applied == [
            AppliedFix(
                slide_id="slide_01",
                element_id="slide_01-body",
                action="resize_element",
                source="deterministic",
                detail="height 0.10 -> 0.17 of the canvas",
            )
        ]
        fix = updated.slides[0].fixes[0]
        assert fix.element_id == "slide_01-body"
        assert fix.dh == pytest.approx(0.068)

    def test_shrink_rescue(self):
        deck = _deck_from(
            [_bullet_slide("Key points", "word " * 80, "Short second point")]
        )
        template = _hemmed_template()
        fixes = plan_fixes(deck, _style_guide(), [template])
        assert len(fixes) == 1
        assert fixes[0].element_id == "slide_01-body-1"
        assert fixes[0].action == "reduce_font_size"
        assert fixes[0].detail == "font 20.0pt -> 18.5pt"
        assert fixes[0].font_scale == pytest.approx(0.925)
        updated, report = run_critique_loop(deck, _style_guide(), [template])
        assert report.stop_reason == "threshold_met"
        assert report.score_before == 70
        assert report.score_after == 100
        assert len(report.iterations) == 1
        fix = updated.slides[0].fixes[0]
        assert fix.element_id == "slide_01-body-1"
        assert fix.font_scale == pytest.approx(0.925)

    def test_removal_rescue(self):
        big = "word " * 120
        small = "word " * 50
        deck = _deck_from([_bullet_slide("Key points", big, small)])
        template = _bottom_template(12.0)
        updated, report = run_critique_loop(deck, _style_guide(), [template])
        assert report.stop_reason == "threshold_met"
        assert report.score_before == 70
        assert report.score_after == 100
        assert len(report.iterations) == 1
        applied = report.iterations[0].applied
        assert len(applied) == 1
        assert applied[0].action == "remove_low_priority_bullet"
        assert applied[0].removed_text == big
        assert applied[0].detail == (
            f"removed bullet ({len(big)} chars): {big[:60]}"
        )
        assert [b.text for b in updated.slides[0].content] == [small]
        assert updated.slides[0].fixes == []

    def test_removals_are_bounded_and_the_loop_terminates(self):
        first = "word " * 440
        second = "word " * 400
        third = "word " * 360
        deck = _deck_from([_bullet_slide("Key points", first, second, third)])
        updated, report = run_critique_loop(
            deck, _style_guide(), [_bottom_template(12.0)]
        )
        assert report.stop_reason == "max_iterations_reached"
        assert len(report.iterations) == 2
        removed = [
            applied.removed_text
            for iteration in report.iterations
            for applied in iteration.applied
        ]
        assert removed == [first, second]
        assert [b.text for b in updated.slides[0].content] == [third]
        assert report.score_before == 70
        assert report.score_after == 70
        assert report.needs_manual_review is True

    def test_font_drops_to_the_floor_in_one_step(self):
        text = "word " * 600
        deck = _deck_from([_bullet_slide("Key points", text)])
        template = _bottom_template(20.0)
        fixes = plan_fixes(deck, _style_guide(), [template])
        assert len(fixes) == 1
        assert fixes[0].action == "reduce_font_size"
        assert fixes[0].detail == "font 20.0pt -> 12pt (to the floor)"
        assert fixes[0].font_scale == pytest.approx(0.6)
        updated, report = run_critique_loop(deck, _style_guide(), [template])
        assert report.stop_reason == "no_fixes_applied"
        assert len(report.iterations) == 2
        assert [
            (
                iteration.iteration,
                iteration.score_before,
                iteration.score_after,
                len(iteration.applied),
                len(iteration.rejected),
            )
            for iteration in report.iterations
        ] == [(1, 70, 70, 1, 0), (2, 70, 70, 0, 0)]
        assert updated.slides[0].fixes[0].font_scale == pytest.approx(0.6)
        assert report.needs_manual_review is True

    def test_the_last_bullet_is_never_removed(self):
        text = "word " * 400
        deck = _deck_from([_bullet_slide("Key points", text)])
        updated, report = run_critique_loop(
            deck, _style_guide(), [_bottom_template(12.0)]
        )
        assert report.stop_reason == "no_fixes_applied"
        assert len(report.iterations) == 1
        assert report.iterations[0].applied == []
        assert [b.text for b in updated.slides[0].content] == [text]
        assert report.needs_manual_review is True

    def test_a_clean_deck_stops_at_the_threshold(self):
        deck = _deck_from([_bullet_slide("Key points", "Short point")])
        updated, report = run_critique_loop(
            deck, _style_guide(), [_bottom_template(12.0)]
        )
        assert report.stop_reason == "threshold_met"
        assert report.score_before == 100
        assert report.score_after == 100
        assert len(report.iterations) == 1
        assert report.needs_manual_review is False
        assert updated.slides[0].fixes == []

    def test_facts_survive_the_loop(self):
        big = "word " * 120
        small = "word " * 50
        deck = _deck_from([_bullet_slide("Key points", big, small)])
        updated, report = run_critique_loop(
            deck, _style_guide(), [_bottom_template(12.0)]
        )
        assert [b.text for b in deck.slides[0].content] == [big, small]
        assert [b.text for b in updated.slides[0].content] == [small]
        assert report.iterations[0].applied[0].removed_text == big

    def test_the_loop_is_deterministic(self):
        def build():
            deck = _deck_from(
                [_bullet_slide("Key points", "word " * 120, "word " * 50)]
            )
            return deck, _bottom_template(12.0)

        deck_a, template_a = build()
        deck_b, template_b = build()
        updated_a, report_a = run_critique_loop(
            deck_a, _style_guide(), [template_a]
        )
        updated_b, report_b = run_critique_loop(
            deck_b, _style_guide(), [template_b]
        )
        assert updated_a.model_dump_json() == updated_b.model_dump_json()
        assert report_a.model_dump_json() == report_b.model_dump_json()

    def test_the_report_round_trips_through_json(self):
        deck = _deck_from(
            [_bullet_slide("Key points", "word " * 120, "word " * 50)]
        )
        _, report = run_critique_loop(
            deck, _style_guide(), [_bottom_template(12.0)]
        )
        restored = CritiqueReport.model_validate_json(report.model_dump_json())
        assert restored == report


# -- applying decisions directly --------------------------------------------


class TestApplyDecisions:
    def test_removing_the_same_text_twice_skips_the_second(self):
        text = "word " * 10
        deck = _deck_from([_bullet_slide("Key points", text)])
        decisions = [
            FixDecision(
                slide_id="slide_01",
                element_id="slide_01-body",
                action="remove_low_priority_bullet",
                detail="first removal",
                removed_text=text,
            ),
            FixDecision(
                slide_id="slide_01",
                element_id="slide_01-body",
                action="remove_low_priority_bullet",
                detail="second removal",
                removed_text=text,
            ),
        ]
        updated, applied, skipped = apply_decisions(deck, decisions)
        assert len(applied) == 1
        assert applied[0].removed_text == text
        assert skipped == [
            "slide_01/slide_01-body: bullet to remove was not found on the slide"
        ]
        assert [b.text for b in updated.slides[0].content] == []
        assert [b.text for b in deck.slides[0].content] == [text]

    def test_removing_without_recorded_text_is_skipped(self):
        text = "word " * 10
        deck = _deck_from([_bullet_slide("Key points", text)])
        decisions = [
            FixDecision(
                slide_id="slide_01",
                element_id="slide_01-body",
                action="remove_low_priority_bullet",
                detail="malformed removal",
            )
        ]
        updated, applied, skipped = apply_decisions(deck, decisions)
        assert applied == []
        assert skipped == [
            "slide_01/slide_01-body: removal carried no text to remove"
        ]
        assert [b.text for b in updated.slides[0].content] == [text]


# -- optional vision intake -------------------------------------------------


class _MockCritic:
    name = "mock-critic"

    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.calls = []

    def critique_slide(self, generated, reference, context):
        self.calls.append((generated, reference, context))
        if self.exc is not None:
            raise self.exc
        return self.payload


def _images():
    return {"slide_01": Path("slide_01.png")}


def _grow_scenario():
    deck = _deck_from([_bullet_slide("Key points", "word " * 100)])
    return deck, _grow_template()


class TestVisionIntake:
    def test_valid_suggestions_apply_alongside_deterministic_fixes(self):
        deck, template = _grow_scenario()
        critic = _MockCritic(
            payload={
                "suggestions": [
                    {
                        "element_id": "slide_01-title",
                        "action": "resize_element",
                        "dh": 0.04,
                        "reason": "title looks cramped",
                    }
                ]
            }
        )
        updated, report = run_critique_loop(
            deck, _style_guide(), [template],
            vision_provider=critic, images=_images(),
        )
        assert report.vision_used is True
        assert report.stop_reason == "threshold_met"
        assert report.vision_notes == [
            "slide_01: vision critic 'mock-critic' responded"
        ]
        assert [
            (a.element_id, a.source, a.detail)
            for a in report.iterations[0].applied
        ] == [
            (
                "slide_01-body",
                "deterministic",
                "height 0.10 -> 0.17 of the canvas",
            ),
            (
                "slide_01-title",
                "vision",
                "vision: height 0.15 -> 0.19 (title looks cramped)",
            ),
        ]
        title_fix = next(
            fix for fix in updated.slides[0].fixes
            if fix.element_id == "slide_01-title"
        )
        assert title_fix.dh == pytest.approx(0.04)
        assert len(critic.calls) == 1
        image, reference, context = critic.calls[0]
        assert image == Path("slide_01.png")
        assert reference is None
        assert context["deck_title"] == "Synthetic deck"
        assert context["slide_id"] == "slide_01"
        assert context["slide_type"] == "bullets"
        assert [
            element["element_id"] for element in context["elements"]
        ] == ["slide_01-title", "slide_01-body"]

    def test_unknown_or_out_of_bounds_suggestions_are_rejected(self):
        deck, template = _grow_scenario()
        critic = _MockCritic(
            payload={
                "suggestions": [
                    {
                        "element_id": "slide_01-ghost",
                        "action": "reduce_font_size",
                        "scale": 0.9,
                    },
                    {
                        "element_id": "slide_01-body",
                        "action": "reduce_font_size",
                        "scale": 0.3,
                    },
                    {"element_id": "slide_01-title", "action": "explode"},
                    {
                        "element_id": "slide_01-title",
                        "action": "resize_element",
                        "dh": 0.05,
                    },
                ]
            }
        )
        _, report = run_critique_loop(
            deck, _style_guide(), [template],
            vision_provider=critic, images=_images(),
        )
        assert report.iterations[0].rejected == [
            "slide_01: unknown element 'slide_01-ghost'",
            "slide_01: slide_01-body: scale 0.3 outside 0.50-0.98",
            "slide_01: slide_01-title: unsupported action 'explode'",
        ]
        assert (
            "slide_01-title", "vision", "vision: height 0.15 -> 0.20"
        ) in [
            (a.element_id, a.source, a.detail)
            for a in report.iterations[0].applied
        ]

    def test_a_superseded_suggestion_is_rejected(self):
        deck, template = _grow_scenario()
        critic = _MockCritic(
            payload={
                "suggestions": [
                    {
                        "element_id": "slide_01-body",
                        "action": "reduce_font_size",
                        "scale": 0.9,
                    }
                ]
            }
        )
        _, report = run_critique_loop(
            deck, _style_guide(), [template],
            vision_provider=critic, images=_images(),
        )
        assert report.iterations[0].rejected == [
            "slide_01/slide_01-body: suggestion superseded by the "
            "deterministic repair"
        ]
        sources = [a.source for a in report.iterations[0].applied]
        assert sources == ["deterministic"]

    def test_a_failing_provider_is_recorded_not_fatal(self):
        deck, template = _grow_scenario()
        critic = _MockCritic(exc=RuntimeError("boom"))
        _, report = run_critique_loop(
            deck, _style_guide(), [template],
            vision_provider=critic, images=_images(),
        )
        assert report.vision_used is False
        assert report.vision_notes == [
            "slide_01: vision critique failed (boom)"
        ]
        assert report.stop_reason == "threshold_met"
        assert report.score_after == 100

    def test_no_images_means_no_vision_calls(self):
        deck, template = _grow_scenario()
        critic = _MockCritic(payload={"suggestions": []})
        _, report = run_critique_loop(
            deck, _style_guide(), [template], vision_provider=critic
        )
        assert critic.calls == []
        assert report.vision_used is False
        assert report.vision_notes == []

    def test_a_response_without_suggestions_is_rejected(self):
        deck, template = _grow_scenario()
        critic = _MockCritic(payload={})
        _, report = run_critique_loop(
            deck, _style_guide(), [template],
            vision_provider=critic, images=_images(),
        )
        assert report.vision_used is True
        assert report.iterations[0].rejected == [
            "slide_01: response had no suggestion list"
        ]

    def test_vision_is_consulted_only_on_the_first_iteration(self):
        deck = _deck_from([_bullet_slide("Key points", "word " * 600)])
        critic = _MockCritic(payload={"suggestions": []})
        _, report = run_critique_loop(
            deck, _style_guide(), [_bottom_template(20.0)],
            vision_provider=critic, images=_images(),
        )
        assert len(report.iterations) == 2
        assert len(critic.calls) == 1
        assert report.vision_used is False
        assert report.vision_notes == [
            "slide_01: vision critic 'mock-critic' responded"
        ]
        assert report.stop_reason == "no_fixes_applied"

    def test_removal_suggestion_needs_several_bullets(self):
        deck = _deck_from([_bullet_slide("Key points", "word " * 400)])
        critic = _MockCritic(
            payload={
                "suggestions": [
                    {
                        "element_id": "slide_01-body",
                        "action": "remove_low_priority_bullet",
                    }
                ]
            }
        )
        _, report = run_critique_loop(
            deck, _style_guide(), [_bottom_template(12.0)],
            vision_provider=critic, images=_images(),
        )
        assert report.iterations[0].rejected == [
            "slide_01: slide_01-body: too few bullets to remove safely"
        ]
        assert report.needs_manual_review is True


# -- the real Gemini adapter through the loop (fake transport) --------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _critique_reply(suggestions, scores=None, approved=False):
    text = json.dumps(
        {
            "scores": scores or {},
            "approved": approved,
            "suggestions": suggestions,
        }
    )
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _slide_image(tmp_path):
    image = tmp_path / "slide_01.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    return image


class TestVisionAdapterThroughTheLoop:
    def test_real_gemini_critic_drives_the_loop_over_http(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv(GEMINI_MODEL_ENV, raising=False)
        deck, template = _grow_scenario()
        image = _slide_image(tmp_path)
        requests = []

        def fake_urlopen(request, timeout=None):
            requests.append(request)
            return _FakeResponse(
                _critique_reply(
                    [
                        {
                            "element_id": "slide_01-title",
                            "action": "resize_element",
                            "dh": 0.03,
                            "reason": "title looks cramped",
                        }
                    ],
                    scores={"no_overflow": 80, "variety": 60},
                )
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        critic = GeminiSlideCritic(api_key="dummy-key")
        updated, report = run_critique_loop(
            deck, _style_guide(), [template],
            vision_provider=critic, images={"slide_01": image},
        )
        assert len(requests) == 1
        assert report.vision_used is True
        assert report.stop_reason == "threshold_met"
        assert report.vision_notes == [
            f"slide_01: vision critic 'gemini-critic:{DEFAULT_GEMINI_MODEL}' "
            "responded"
        ]
        assert [
            (a.element_id, a.source, a.detail)
            for a in report.iterations[0].applied
        ] == [
            (
                "slide_01-body",
                "deterministic",
                "height 0.10 -> 0.17 of the canvas",
            ),
            (
                "slide_01-title",
                "vision",
                "vision: height 0.15 -> 0.18 (title looks cramped)",
            ),
        ]
        title_fix = next(
            fix for fix in updated.slides[0].fixes
            if fix.element_id == "slide_01-title"
        )
        assert title_fix.dh == pytest.approx(0.03)

        request = requests[0]
        assert DEFAULT_GEMINI_MODEL in request.full_url
        assert request.get_header("X-goog-api-key") == "dummy-key"
        parts = json.loads(request.data.decode("utf-8"))["contents"][0]["parts"]
        assert parts[0]["text"].startswith("You are a visual design critic")
        assert "slide_01-body" in parts[0]["text"]
        assert parts[1]["inline_data"]["mime_type"] == "image/png"

    def test_a_malformed_real_response_is_recorded_not_fatal(
        self, tmp_path, monkeypatch
    ):
        deck, template = _grow_scenario()
        image = _slide_image(tmp_path)

        def fake_urlopen(request, timeout=None):
            return _FakeResponse(
                {
                    "candidates": [
                        {"content": {"parts": [{"text": "I cannot comply."}]}}
                    ]
                }
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        critic = GeminiSlideCritic(api_key="dummy-key")
        _, report = run_critique_loop(
            deck, _style_guide(), [template],
            vision_provider=critic, images={"slide_01": image},
        )
        assert report.vision_used is False
        assert report.vision_notes == [
            "slide_01: vision critique failed (model response contained no "
            "JSON object: I cannot comply.)"
        ]
        assert report.stop_reason == "threshold_met"
        assert report.score_after == 100
