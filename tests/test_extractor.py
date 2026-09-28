"""Style-guide extraction tests: the synthetic fixture deck must produce an
honest, deterministic style guide from native data only."""

from __future__ import annotations

import pytest

from core.extractor import StyleGuideExtractor
from core.style_guide import PROVENANCE_NATIVE, PROVENANCE_VISION
from core.vision import SlideVisionNotes
from helpers import inventory as make_inventory
from helpers import run, text_shape


@pytest.fixture(scope="module")
def style_guide(inventory):
    return StyleGuideExtractor().extract(inventory)


@pytest.fixture(scope="module")
def palette_by_hex(style_guide):
    return {entry.hex: entry for entry in style_guide.palette}


def test_palette_usage_classification(palette_by_hex):
    assert palette_by_hex["#F5F7FA"].usage == "background"
    assert palette_by_hex["#0B1F3A"].usage == "primary"
    assert palette_by_hex["#263238"].usage == "text"
    assert palette_by_hex["#18A999"].usage == "accent"


def test_palette_frequencies(palette_by_hex):
    assert palette_by_hex["#F5F7FA"].frequency == 2
    assert palette_by_hex["#0B1F3A"].frequency == 4
    assert palette_by_hex["#263238"].frequency == 12
    assert palette_by_hex["#18A999"].frequency == 2


def test_palette_provenance_is_native(style_guide):
    assert all(entry.provenance == PROVENANCE_NATIVE for entry in style_guide.palette)


def test_typography_title(style_guide):
    # The rule picks the most common style among runs larger than body size.
    # On this fixture that is the 18-20pt slate paragraph text (6 runs), not
    # the one-off 44pt navy cover title — an honest statistical result.
    title = style_guide.typography.title
    assert title is not None
    assert title.font_family == "Inter"
    assert title.font_size_pt == 20.0
    assert title.font_weight is None
    assert title.color_hex == "#263238"
    assert title.sample_count == 6


def test_typography_body(style_guide):
    body = style_guide.typography.body
    assert body is not None
    assert body.font_family == "Inter"
    assert body.font_size_pt == 16.0
    assert body.font_weight is None
    assert body.color_hex == "#263238"
    assert body.sample_count == 5


def test_margins(style_guide):
    margins = style_guide.layout_grid.margins_px
    assert margins.top_px == 100
    assert margins.right_px == 110
    assert margins.bottom_px == 120
    assert margins.left_px == 108


def test_layout_grid(style_guide):
    grid = style_guide.layout_grid
    assert grid.slide_width_px == 1920
    assert grid.slide_height_px == 1080
    assert style_guide.aspect_ratio == "16:9"
    assert grid.column_count is None
    assert grid.gutter_px is None


def test_content_rules(style_guide):
    rules = style_guide.content_rules
    assert rules.max_bullets_per_slide == 3
    assert rules.max_words_per_bullet == 7
    # Title-like texts are the paragraphs matching the title style above; the
    # longest is a 50-character 18pt slate paragraph, not the cover title.
    assert rules.max_title_length == 50
    assert rules.preferred_density == "low"


def test_warnings_are_honest(style_guide):
    joined = "\n".join(style_guide.warnings)
    assert "Column count and gutter width" in joined
    assert "explicit background" in joined
    assert "rounded-rectangle" in joined


def test_extraction_is_deterministic(inventory):
    first = StyleGuideExtractor().extract(inventory)
    second = StyleGuideExtractor().extract(inventory)
    first_dump = first.model_dump()
    second_dump = second.model_dump()
    first_dump.pop("extracted_at")
    second_dump.pop("extracted_at")
    assert first_dump == second_dump


def test_deck_metadata(style_guide):
    assert style_guide.deck_name == "sample_deck"
    assert style_guide.source_file_type == ".pptx"
    assert style_guide.source_slide_count == 6
    assert style_guide.deck_id.startswith("sample-deck-")
    assert len(style_guide.deck_id.rsplit("-", 1)[1]) == 8


def test_empty_deck_produces_warnings_not_guesses():
    guide = StyleGuideExtractor().extract(make_inventory([]))
    assert guide.palette == []
    assert guide.typography.title is None
    assert guide.typography.body is None
    assert guide.content_rules.preferred_density is None
    assert guide.content_rules.max_bullets_per_slide is None
    joined = "\n".join(guide.warnings)
    assert "no slides" in joined
    assert "not derivable" in joined


def test_vision_merge_adds_colors_and_background(inventory):
    notes = [
        SlideVisionNotes(
            slide_number=i + 1,
            background_style="gradient",
            dominant_colors=["#123456"],
            notes=["dark photographic background"],
        )
        for i in range(6)
    ]
    guide = StyleGuideExtractor().extract(inventory, vision_notes=notes)
    vision_entry = next(e for e in guide.palette if e.hex == "#123456")
    assert vision_entry.provenance == PROVENANCE_VISION
    assert vision_entry.frequency == 0
    assert guide.element_treatments.background_style == "gradient"
    assert guide.element_treatments.provenance == PROVENANCE_VISION
    joined = "\n".join(guide.warnings)
    assert "inferred by vision" in joined
    assert "vision (slide 1)" in joined


def test_vision_does_not_override_native_background():
    from core.schemas import ColorInfo
    from helpers import slide

    slides = [
        slide(
            1,
            [text_shape("t", [[run("Hello", size=30, bold=True, color="#0B1F3A")]])],
        )
    ]
    slides[0].background_color = ColorInfo(hex="#FFFFFF")
    deck = make_inventory(slides)
    notes = [SlideVisionNotes(slide_number=1, background_style="image")]
    guide = StyleGuideExtractor().extract(deck, vision_notes=notes)
    assert guide.element_treatments.background_style == "solid"
    assert guide.element_treatments.provenance == PROVENANCE_NATIVE


def test_vision_note_count_mismatch_is_ignored(inventory):
    notes = [SlideVisionNotes(slide_number=1, background_style="gradient")]
    guide = StyleGuideExtractor().extract(inventory, vision_notes=notes)
    assert guide.element_treatments.background_style is None
    assert all(e.provenance == PROVENANCE_NATIVE for e in guide.palette)
    assert any("ignored" in warning for warning in guide.warnings)
