"""Tests for template extraction (blueprint §7): end-to-end expectations on
the synthetic fixture deck plus focused synthetic slot-rule checks."""

from __future__ import annotations

import pytest

from core.slide_classifier import SlideClassification, SlideClassifier
from core.templates import (
    ROLE_ATTRIBUTION,
    ROLE_BODY,
    ROLE_CARD,
    ROLE_CHART,
    ROLE_CONTAINER,
    ROLE_LABEL,
    ROLE_PICTURE,
    ROLE_STAT,
    ROLE_TABLE,
    ROLE_TITLE,
    build_templates,
    find_templates,
    load_templates,
    save_templates,
)
from tests.helpers import inventory, run, slide, text_shape


@pytest.fixture(scope="module")
def classifications(inventory):
    return SlideClassifier().classify_deck(inventory)


@pytest.fixture(scope="module")
def records(inventory, classifications):
    return build_templates(inventory, classifications)


def test_record_count_ids_and_types(records):
    assert [r.template_id for r in records] == [
        "sample-deck-slide001-title",
        "sample-deck-slide002-bullets",
        "sample-deck-slide003-chart",
        "sample-deck-slide004-quote",
        "sample-deck-slide005-comparison",
        "sample-deck-slide006-section-divider",
    ]
    assert [r.slide_type for r in records] == [
        "title",
        "bullets",
        "chart",
        "quote",
        "comparison",
        "section_divider",
    ]
    assert [r.source_slide_number for r in records] == [1, 2, 3, 4, 5, 6]
    assert all(r.source_deck == "sample_deck" for r in records)
    assert all(r.aspect_ratio == "16:9" for r in records)


def test_title_slide_slots(records):
    record = records[0]
    assert [s.role for s in record.slots] == [ROLE_TITLE, ROLE_LABEL, ROLE_PICTURE]
    title = record.slots[0]
    assert title.font_size_pt == 44.0
    assert title.bold is True
    assert title.color_hex == "#0B1F3A"
    assert title.x == 0.0625
    assert title.width == 0.625
    picture = record.slots[2]
    assert picture.shape_type == "PICTURE"
    assert picture.x == pytest.approx(1620 / 1920, abs=1e-4)
    assert picture.y == pytest.approx(900 / 1080, abs=1e-4)


def test_bullets_slide_slots(records):
    record = records[1]
    assert [s.role for s in record.slots] == [ROLE_TITLE, ROLE_BODY, ROLE_TABLE]
    body = record.slots[1]
    assert body.font_size_pt == 18.0
    assert body.color_hex == "#263238"


def test_stat_and_chart_slots(records):
    record = records[2]
    roles = [s.role for s in record.slots]
    assert roles.count(ROLE_STAT) == 1
    assert roles.count(ROLE_CHART) == 1
    stat = next(s for s in record.slots if s.role == ROLE_STAT)
    assert stat.font_size_pt == 96.0
    assert stat.bold is True
    assert stat.color_hex == "#18A999"
    chart = next(s for s in record.slots if s.role == ROLE_CHART)
    assert chart.x == pytest.approx(700 / 1920, abs=1e-4)
    assert chart.y == pytest.approx(250 / 1080, abs=1e-4)
    assert chart.width == pytest.approx(900 / 1920, abs=1e-4)


def test_quote_slide_slots(records):
    record = records[3]
    assert [s.role for s in record.slots] == [
        ROLE_BODY,
        ROLE_ATTRIBUTION,
        ROLE_CONTAINER,
    ]
    quote = record.slots[0]
    assert quote.italic is True
    assert quote.font_size_pt == 28.0
    assert quote.color_hex == "#0B1F3A"
    attribution = record.slots[1]
    assert attribution.font_size_pt == 16.0
    assert attribution.color_hex == "#263238"
    # The footer nested inside the group is skipped, with a warning saying so.
    assert any("nested in groups" in warning for warning in record.warnings)


def test_comparison_slide_slots(records):
    record = records[4]
    cards = [s for s in record.slots if s.role == ROLE_CARD]
    assert len(cards) == 2
    assert all(card.fill_hex == "#F5F7FA" for card in cards)
    assert all(card.shape_type == "AUTO_SHAPE/ROUNDED_RECTANGLE" for card in cards)
    roles = [s.role for s in record.slots]
    # Symmetric card interiors: one body slot per card.
    assert roles.count(ROLE_BODY) == 2
    assert any(s.role == ROLE_TITLE and s.font_size_pt == 24.0 for s in record.slots)


def test_placeholder_slots(records):
    record = records[5]
    assert [s.role for s in record.slots] == [ROLE_TITLE, ROLE_BODY]
    # Subtitle theme color (accent1) is resolved through the theme palette.
    assert record.slots[1].color_hex == "#4F81BD"


def test_geometry_fractions_within_canvas(records):
    for record in records:
        for slot in record.slots:
            assert 0.0 <= slot.x <= 1.0
            assert 0.0 <= slot.y <= 1.0
            assert 0.0 <= slot.width <= 1.0
            assert 0.0 <= slot.height <= 1.0


def test_save_load_round_trip(records, tmp_path):
    path = save_templates(records, tmp_path / "templates.json")
    loaded = load_templates(path)
    assert loaded == records


def test_count_mismatch_raises(inventory, classifications):
    with pytest.raises(ValueError, match="classifications"):
        build_templates(inventory, classifications[:-1])


def test_find_templates_by_type(records):
    titles = find_templates(records, "title")
    assert [t.template_id for t in titles] == ["sample-deck-slide001-title"]
    bullets = find_templates(records, "bullets")
    assert [t.template_id for t in bullets] == ["sample-deck-slide002-bullets"]
    assert find_templates(records, "no-such-type") == []


# -- synthetic slot-rule checks -------------------------------------------


def _single_slide_record(shapes):
    deck = inventory([slide(1, shapes)])
    classification = SlideClassification(
        slide_number=1, slide_type="bullets", confidence="high", reasons=["synthetic"]
    )
    return build_templates(deck, [classification])[0]


def test_subtitle_placeholder_is_body_not_title():
    shapes = [
        text_shape(
            "title",
            [[run("Big Title", size=44, bold=True)]],
            y=0,
            extra={"is_placeholder": True, "placeholder_type": "CENTER_TITLE"},
        ),
        text_shape(
            "subtitle",
            [[run("Supporting line")]],
            y=200,
            extra={"is_placeholder": True, "placeholder_type": "SUBTITLE"},
        ),
    ]
    record = _single_slide_record(shapes)
    assert [s.role for s in record.slots] == [ROLE_TITLE, ROLE_BODY]


def test_two_paragraph_frame_is_body():
    shapes = [
        text_shape(
            "list",
            [
                [run("First point", size=16)],
                [run("Second point", size=16)],
            ],
        )
    ]
    record = _single_slide_record(shapes)
    assert record.slots[0].role == ROLE_BODY
