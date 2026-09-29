"""Generation pipeline tests (blueprint §8, Milestone 4 acceptance).

The planner may only use slide types present in the learned template
library, never schedules identical layouts back to back, and the content
generator only uses the user's own words: every block must come from the
brief verbatim or be skipped with a warning, never truncated.
"""

from __future__ import annotations

from core.extractor import StyleGuideExtractor
from core.generation import (
    BulletBlock,
    ContentGenerator,
    DeckBrief,
    GeneratedDeck,
    OutlineGenerator,
    QuoteBlock,
    StatBlock,
    SubtitleBlock,
    analyze_material,
    available_lines,
    estimate_lines,
    generate_deck,
    slot_by_role,
)
from core.slide_classifier import SlideClassifier
from core.style_guide import ContentRules, LayoutGrid, StyleGuide
from core.templates import ROLE_BODY, TemplateRecord, TemplateSlot, build_templates
from tests.helpers import inventory, run, slide, text_shape


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


def _style_guide(max_bullets=None, max_words=None, max_title=None):
    return StyleGuide(
        deck_id="synthetic-deck",
        deck_name="synthetic",
        extracted_at="2026-01-01T00:00:00+00:00",
        source_file_type="pptx",
        source_slide_count=6,
        aspect_ratio="16:9",
        layout_grid=LayoutGrid(slide_width_px=1920.0, slide_height_px=1080.0),
        content_rules=ContentRules(
            max_bullets_per_slide=max_bullets,
            max_words_per_bullet=max_words,
            max_title_length=max_title,
        ),
    )


def _brief(notes, slide_count=5, topic="Quarterly Review"):
    return DeckBrief(
        topic=topic,
        audience="Leadership",
        goal="Align on next quarter",
        slide_count=slide_count,
        notes=list(notes),
    )


# -- material analysis --------------------------------------------------------


class TestAnalyzeMaterial:
    def test_quote_line_with_attribution_is_split(self):
        material = analyze_material(
            ["> We ship every week — the engineering team"], None
        )
        assert len(material.quotes) == 1
        assert material.quotes[0].text == "We ship every week"
        assert material.quotes[0].attribution == "the engineering team"

    def test_short_quote_line_stays_whole(self):
        material = analyze_material(["> Ship weekly"], None)
        assert len(material.quotes) == 1
        assert material.quotes[0].text == "Ship weekly"
        assert material.quotes[0].attribution is None

    def test_stat_line_is_value_and_context(self):
        material = analyze_material(["82% — of teams ship weekly"], None)
        assert len(material.stats) == 1
        assert material.stats[0].value == "82%"
        assert material.stats[0].context == "of teams ship weekly"

    def test_plain_sentence_without_separator_is_not_a_stat(self):
        material = analyze_material(["82% of teams ship weekly"], None)
        assert material.stats == []

    def test_short_digit_free_line_is_a_heading(self):
        material = analyze_material(["Revenue growth strategy"], None)
        assert material.headings == ["Revenue growth strategy"]

    def test_word_budget_line_is_a_bullet(self):
        material = analyze_material(
            ["Growth doubled after we changed the pricing model"], None
        )
        assert material.bullets == ["Growth doubled after we changed the pricing model"]

    def test_over_budget_line_is_unusable_never_shortened(self):
        long_line = " ".join(["word"] * 20)
        material = analyze_material([long_line], None)
        assert material.bullets == []
        assert material.unusable == [long_line]

    def test_blank_lines_are_ignored(self):
        material = analyze_material(["", "   ", "Real point"], None)
        assert material.headings == ["Real point"]
        assert material.bullets == []


# -- capacity estimation ------------------------------------------------------


class TestCapacity:
    def test_estimate_lines_wraps_long_text(self):
        slot = _slot(ROLE_BODY, w=0.2, h=0.5, size=16.0)
        # 384 px wide box, 16 pt font: ~32 chars per line
        assert estimate_lines("x" * 60, slot, 1920.0, 1080.0) == 2
        assert estimate_lines("x" * 30, slot, 1920.0, 1080.0) == 1

    def test_available_lines_from_slot_height(self):
        slot = _slot(ROLE_BODY, h=0.05, size=16.0)
        # 54 px tall box, 16 pt font: exactly one text line fits
        assert available_lines(slot, 1080.0) == 1

    def test_unknown_font_size_is_unknown_capacity(self):
        slot = TemplateSlot(
            role=ROLE_BODY, shape_type="TEXT_BOX", x=0.0, y=0.0, width=0.5,
            height=0.5,
        )
        assert estimate_lines("any text", slot, 1920.0, 1080.0) is None
        assert available_lines(slot, 1080.0) is None

    def test_slot_by_role_finds_first_match(self):
        template = _template(
            "bullets", "t_bullets", [_slot("title"), _slot(ROLE_BODY)]
        )
        assert slot_by_role(template, ROLE_BODY).role == ROLE_BODY
        assert slot_by_role(template, "picture") is None


# -- outline planning ---------------------------------------------------------


class TestOutlineGenerator:
    def test_plan_uses_only_library_types_without_adjacent_repeats(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("bullets", "t_bullets", [_slot(ROLE_BODY)]),
            _template("quote", "t_quote", [_slot(ROLE_BODY)]),
        ]
        material = analyze_material(
            [
                "Ship the new release every single week",
                "Support load fell with self serve docs",
                "> Ship weekly",
            ],
            None,
        )
        plan = OutlineGenerator().plan(_brief([], slide_count=3), library, material)
        types = [s.slide_type for s in plan.slides]
        assert types[0] == "title"
        assert all(
            types[i] != types[i + 1] for i in range(len(types) - 1)
        ), f"adjacent repeat in {types}"
        assert set(types) <= {"title", "bullets", "quote"}
        assert len(types) == 3

    def test_plan_honors_slide_count_with_closer(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("bullets", "t_bullets", [_slot(ROLE_BODY)]),
            _template("quote", "t_quote", [_slot(ROLE_BODY)]),
        ]
        material = analyze_material(
            ["Ship the new release every single week", "> Ship weekly"], None
        )
        plan = OutlineGenerator().plan(_brief([], slide_count=3), library, material)
        types = [s.slide_type for s in plan.slides]
        assert types == ["title", "bullets", "quote"]

    def test_single_content_type_repeats_with_warning(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("bullets", "t_bullets", [_slot(ROLE_BODY)]),
        ]
        material = analyze_material(["Ship the new release every single week"], None)
        plan = OutlineGenerator().plan(_brief([], slide_count=4), library, material)
        types = [s.slide_type for s in plan.slides]
        assert types == ["title", "bullets", "bullets", "bullets"]
        assert any("layouts repeat" in w for w in plan.warnings)

    def test_unfillable_library_reports_shortfall(self):
        library = [_template("title", "t_title", [_slot("title")])]
        material = analyze_material(["Ship the new release every single week"], None)
        plan = OutlineGenerator().plan(_brief([], slide_count=3), library, material)
        assert [s.slide_type for s in plan.slides] == ["title"]
        assert any("could be planned" in w for w in plan.warnings)

    def test_stat_material_schedules_chart_type(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("chart", "t_chart", [_slot(ROLE_BODY)]),
        ]
        material = analyze_material(["82% — of teams ship weekly"], None)
        plan = OutlineGenerator().plan(_brief([], slide_count=2), library, material)
        assert [s.slide_type for s in plan.slides] == ["title", "chart"]


# -- content generation -------------------------------------------------------


class TestContentGenerator:
    def _run_generate(self, library, notes, style_guide, plan=None, brief=None):
        material = analyze_material(notes, style_guide.content_rules.max_words_per_bullet)
        brief = brief or _brief(notes)
        plan = plan or OutlineGenerator().plan(brief, library, material)
        slides, warnings = ContentGenerator().generate(
            brief, plan, library, material, style_guide
        )
        return slides, warnings, material

    def test_bullets_come_verbatim_from_notes(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("bullets", "t_bullets", [_slot(ROLE_BODY, w=0.9, h=0.5)]),
        ]
        notes = [
            "Ship the new release every single week",
            "Support load fell with self serve docs",
            "The roadmap now covers three big initiatives",
        ]
        slides, _, _ = self._run_generate(library, notes, _style_guide())
        bullets = [
            b.text for s in slides for b in s.content if isinstance(b, BulletBlock)
        ]
        assert bullets == notes

    def test_bullet_over_capacity_is_skipped_not_truncated(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template(
                "bullets", "t_bullets", [_slot(ROLE_BODY, w=0.25, h=0.05, size=16.0)]
            ),
        ]
        short = "We ship every small fix each week"
        long_one = "A bullet far too long for this narrow single-line slot"
        slides, _, _ = self._run_generate(library, [short, long_one], _style_guide())
        bullets = [
            b.text for s in slides for b in s.content if isinstance(b, BulletBlock)
        ]
        assert bullets == [short]
        assert all(long_one != text for text in bullets)
        warnings = [w for s in slides for w in s.warnings]
        assert any("capacity" in w for w in warnings)

    def test_max_bullets_per_slide_is_respected(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("bullets", "t_bullets", [_slot(ROLE_BODY, w=0.9, h=0.5)]),
        ]
        notes = [
            "Ship the new release every single week",
            "Support load fell with self serve docs",
            "The roadmap now covers three big initiatives",
        ]
        brief = _brief(notes, slide_count=2)
        material = analyze_material(notes, 12)
        plan = OutlineGenerator().plan(brief, library, material)
        slides, warnings = ContentGenerator().generate(
            brief, plan, library, material, _style_guide(max_bullets=2, max_words=12)
        )
        bullets = [
            b.text for s in slides for b in s.content if isinstance(b, BulletBlock)
        ]
        assert bullets == notes[:2]
        assert any("unused" in w.lower() for w in warnings)

    def test_over_long_title_records_warning(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("bullets", "t_bullets", [_slot(ROLE_BODY, w=0.9, h=0.5)]),
        ]
        brief = _brief(["A point"], slide_count=2, topic="A topic title that is much too long for the guide")
        material = analyze_material(["A point"], None)
        plan = OutlineGenerator().plan(brief, library, material)
        slides, _ = ContentGenerator().generate(
            brief, plan, library, material, _style_guide(max_title=20)
        )
        assert any("exceeds the learned max" in w for s in slides for w in s.warnings)

    def test_missing_template_is_reported_not_crashed(self):
        library = [_template("title", "t_title", [_slot("title")])]
        brief = _brief(["A point"], slide_count=2)
        material = analyze_material(["A point"], None)
        plan = OutlineGenerator().plan(brief, library, material)
        plan.slides[0].template_id = "t_missing"
        slides, warnings = ContentGenerator().generate(
            brief, plan, library, material, _style_guide()
        )
        assert slides == []
        assert any("missing from the library" in w for w in warnings)

    def test_over_budget_note_is_reported_on_the_deck(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("bullets", "t_bullets", [_slot(ROLE_BODY, w=0.9, h=0.5)]),
        ]
        long_line = " ".join(["word"] * 20)
        slides, warnings, _ = self._run_generate(
            library, ["Fine point", long_line], _style_guide()
        )
        assert slides
        assert any("word budget" in w for w in warnings)

    def test_quote_and_stat_blocks_carry_the_users_words(self):
        library = [
            _template("title", "t_title", [_slot("title")]),
            _template("bullets", "t_bullets", [_slot(ROLE_BODY, w=0.9, h=0.5)]),
            _template("quote", "t_quote", [_slot(ROLE_BODY)]),
            _template("chart", "t_chart", [_slot(ROLE_BODY)]),
        ]
        notes = [
            "First point here",
            "Second point here",
            "82% — of teams ship weekly",
            "> We ship every week — the engineering team",
        ]
        slides, _, _ = self._run_generate(library, notes, _style_guide())
        blocks = [b for s in slides for b in s.content]
        stats = [b for b in blocks if isinstance(b, StatBlock)]
        quotes = [b for b in blocks if isinstance(b, QuoteBlock)]
        assert len(stats) == 1
        assert stats[0].value == "82%"
        assert stats[0].context == "of teams ship weekly"
        assert len(quotes) == 1
        assert quotes[0].text == "We ship every week"
        assert quotes[0].attribution == "the engineering team"


# -- end-to-end deterministic generation --------------------------------------


class TestGenerateDeck:
    def test_fixture_library_yields_five_slide_deck(self, inventory):
        classifications = SlideClassifier().classify_deck(inventory)
        templates = build_templates(inventory, classifications)
        style_guide = StyleGuideExtractor().extract(inventory)

        brief = DeckBrief(
            topic="Quarterly Review",
            audience="Leadership",
            goal="Align on the next quarter",
            slide_count=5,
            notes=[
                "Revenue grew every single quarter",
                "Retention improved after onboarding change",
                "Support load fell with self serve docs",
                "82% — of teams now ship weekly",
                "> We ship every week — the engineering team",
            ],
            style_guide_id=style_guide.deck_id,
        )
        deck = generate_deck(brief, templates, style_guide)

        assert isinstance(deck, GeneratedDeck)
        assert deck.generator == "deterministic"
        assert deck.plan.planner == "deterministic"
        assert len(deck.slides) == 5
        assert deck.slides[0].slide_type == "title"

        template_ids = {t.template_id for t in templates}
        assert all(s.template_id in template_ids for s in deck.slides)
        assert all(s.template_id in template_ids for s in deck.plan.slides)

        types = [s.slide_type for s in deck.slides]
        assert all(types[i] != types[i + 1] for i in range(len(types) - 1))

        # every factual block comes verbatim from the user's notes
        for slide in deck.slides:
            for block in slide.content:
                if isinstance(block, BulletBlock):
                    assert block.text in brief.notes
                elif isinstance(block, StatBlock):
                    assert f"{block.value} — {block.context}" in brief.notes
                elif isinstance(block, QuoteBlock):
                    assert block.text in " ".join(brief.notes)
                elif isinstance(block, SubtitleBlock):
                    assert block.text in (
                        brief.notes + [brief.goal, brief.topic, brief.audience]
                    )

        stats = [
            b for s in deck.slides for b in s.content if isinstance(b, StatBlock)
        ]
        assert len(stats) == 1
        assert stats[0].value == "82%"

        quotes = [
            b for s in deck.slides for b in s.content if isinstance(b, QuoteBlock)
        ]
        assert len(quotes) == 1

    def test_deck_without_templates_warns_instead_of_failing(self, inventory):
        classifications = SlideClassifier().classify_deck(inventory)
        style_guide = StyleGuideExtractor().extract(inventory)
        deck = generate_deck(
            _brief(["A single point"]), [], style_guide
        )
        assert deck.slides == []
        assert deck.plan.warnings
