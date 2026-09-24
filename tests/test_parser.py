"""Tests for the PPTX structural parser against the synthetic fixture deck."""

from __future__ import annotations

import zipfile

import pytest

from core.pptx_parser import DeckParseError, DeckParser


def shape_by_name(inventory, slide_number: int, name: str):
    slide = inventory.slides[slide_number - 1]
    matches = [s for s in slide.shapes if s.name == name]
    assert len(matches) == 1, f"expected exactly one shape '{name}' on slide {slide_number}"
    return matches[0]


# -- deck level -----------------------------------------------------------


def test_slide_count_and_size(inventory):
    assert inventory.slide_count == 6
    size = inventory.slide_size
    assert size.width_emu == 12192000
    assert size.height_emu == 6858000
    assert size.aspect_ratio == "16:9"
    assert size.width_px == 1920.0
    assert size.height_px == 1080.0


def test_theme_extraction(inventory):
    theme = inventory.theme
    assert theme is not None
    assert theme.colors.get("dk1") == "#000000"
    assert theme.colors.get("lt1") == "#FFFFFF"
    assert "accent1" in theme.colors
    assert theme.major_font
    assert theme.minor_font


def test_deck_has_no_unexpected_warnings(inventory):
    assert inventory.warnings == []
    warned_slides = [s.slide_number for s in inventory.slides if s.warnings]
    assert warned_slides == [3]  # slide 3 carries the chart warning


# -- geometry -------------------------------------------------------------


def test_geometry_emu_and_normalized_px(inventory):
    title = shape_by_name(inventory, 1, "deck_title")
    geo = title.geometry
    assert geo is not None
    assert geo.x_emu == 120 * 6350
    assert geo.y_emu == 280 * 6350
    assert geo.width_emu == 1200 * 6350
    assert geo.height_emu == 200 * 6350
    assert geo.x_px == 120.0
    assert geo.y_px == 280.0
    assert geo.width_px == 1200.0
    assert geo.height_px == 200.0
    assert title.geometry_note is None


def test_z_index_recorded(inventory):
    slide1 = inventory.slides[0]
    assert [s.name for s in slide1.shapes] == ["deck_title", "deck_subtitle", "logo_placeholder"]
    assert [s.z_index for s in slide1.shapes] == [0, 1, 2]


# -- text -----------------------------------------------------------------


def test_text_run_explicit_properties(inventory):
    title = shape_by_name(inventory, 1, "deck_title")
    run = title.text.paragraphs[0].runs[0]
    assert run.text == "Q3 Product Roadmap"
    assert run.font_family == "Inter"
    assert run.font_size_pt == 44.0
    assert run.bold is True
    assert run.italic is None
    assert run.color is not None
    assert run.color.hex == "#0B1F3A"
    assert run.color.theme_role is None


def test_inherited_properties_recorded_as_none(inventory):
    header = shape_by_name(inventory, 5, "next_header")
    run = header.text.paragraphs[0].runs[0]
    assert run.text == "Next"
    assert run.font_family == "Inter"
    assert run.font_size_pt is None
    assert run.bold is None
    assert run.italic is None
    assert run.color is not None
    assert run.color.hex == "#18A999"


def test_multiline_text_joined(inventory):
    body = shape_by_name(inventory, 2, "priorities_body")
    assert body.text.text.split("\n") == [
        "Improve activation through guided onboarding",
        "Launch the analytics workflow for enterprise teams",
        "Reduce platform incident response time by 30%",
    ]
    assert len(body.text.paragraphs) == 3


def test_theme_color_run_resolved_against_theme(inventory):
    slide6 = inventory.slides[5]
    subtitle = next(s for s in slide6.shapes if s.placeholder_type == "SUBTITLE")
    run = subtitle.text.paragraphs[0].runs[0]
    assert run.text == "Data behind the roadmap"
    assert run.color is not None
    assert run.color.theme_role == "accent1"
    assert run.color.hex == inventory.theme.colors["accent1"]


# -- shapes ----------------------------------------------------------------


def test_picture_recorded(inventory):
    logo = shape_by_name(inventory, 1, "logo_placeholder")
    assert logo.is_picture is True
    assert logo.shape_type == "PICTURE"
    assert logo.geometry is not None
    assert logo.geometry.width_px == 190.0
    # The fixture image was added from an in-memory stream, so the library
    # assigns its default internal part name rather than a user filename.
    assert logo.picture_name == "image.png"


def test_table_recorded_without_cells(inventory):
    table = shape_by_name(inventory, 2, "priorities_table")
    assert table.has_table is True
    assert table.shape_type == "TABLE"
    assert table.table_dimensions == "2 x 2"
    assert any("not extracted" in note for note in table.extraction_notes)


def test_chart_recorded_without_internals(inventory):
    chart = shape_by_name(inventory, 3, "response_time_chart")
    assert chart.has_chart is True
    assert any("chart internals" in note for note in chart.extraction_notes)
    assert any(
        "chart" in warning.lower() for warning in inventory.slides[2].warnings
    )


def test_group_and_nested_shape_depth(inventory):
    group = shape_by_name(inventory, 4, "quote_footer_group")
    assert group.shape_type == "GROUP"
    assert group.depth == 0
    nested = shape_by_name(inventory, 4, "quote_footer")
    assert nested.depth == 1
    assert any("group" in note.lower() for note in nested.extraction_notes)


def test_fill_and_line_colors(inventory):
    card = shape_by_name(inventory, 5, "card_now")
    assert card.fill_color is not None
    assert card.fill_color.hex == "#F5F7FA"
    assert card.fill_note is None
    # Line was explicitly set to "no line" in the fixture.
    assert card.line_color is None
    assert card.line_note is None


def test_placeholder_recorded(inventory):
    slide6 = inventory.slides[5]
    # On the "Title Slide" layout the title placeholder is typed CENTER_TITLE.
    title = next(s for s in slide6.shapes if s.placeholder_type == "CENTER_TITLE")
    assert title.is_placeholder is True
    assert title.text.text == "Appendix"
    run = title.text.paragraphs[0].runs[0]
    assert run.font_size_pt is None
    assert run.font_family is None


# -- error paths -----------------------------------------------------------


def test_missing_file_raises(tmp_path):
    with pytest.raises(DeckParseError, match="File not found"):
        DeckParser().parse(tmp_path / "nope.pptx")


def test_legacy_ppt_rejected_with_hint(tmp_path, fixture_deck):
    legacy = tmp_path / "deck.ppt"
    legacy.write_bytes(fixture_deck.read_bytes())
    with pytest.raises(DeckParseError, match="converted to .pptx"):
        DeckParser().parse(legacy)


def test_corrupt_file_raises(tmp_path):
    corrupt = tmp_path / "corrupt.pptx"
    corrupt.write_bytes(b"this is not a zip archive")
    with pytest.raises(DeckParseError, match="corrupt"):
        DeckParser().parse(corrupt)


def test_zip_without_presentation_raises(tmp_path):
    imposter = tmp_path / "imposter.pptx"
    with zipfile.ZipFile(imposter, "w") as zf:
        zf.writestr("hello.txt", "not a deck")
    with pytest.raises(DeckParseError, match="not a readable PowerPoint file"):
        DeckParser().parse(imposter)


def test_parse_to_file_writes_json(tmp_path, fixture_deck):
    out = tmp_path / "nested" / "raw_deck.json"
    result = DeckParser().parse_to_file(fixture_deck, out)
    assert result == out
    content = out.read_text(encoding="utf-8")
    assert '"slide_count": 6' in content
    assert "Q3 Product Roadmap" in content
