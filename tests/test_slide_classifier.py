"""Slide-type classifier tests: the fixture deck exercises the main rules,
synthetic inventories cover the rest."""

from __future__ import annotations

import pytest

from core.slide_classifier import (
    CONFIDENCE_HIGH,
    SlideClassifier,
)
from helpers import inventory as make_inventory
from helpers import picture_shape, run, slide, text_shape


@pytest.fixture(scope="module")
def classifications(inventory):
    return SlideClassifier().classify_deck(inventory)


def test_fixture_deck_slide_types(classifications):
    assert [c.slide_type for c in classifications] == [
        "title",
        "bullets",
        "chart",
        "quote",
        "comparison",
        "section_divider",
    ]


def test_fixture_confidences(classifications):
    by_type = {c.slide_type: c for c in classifications}
    assert by_type["title"].confidence == CONFIDENCE_HIGH
    assert by_type["bullets"].confidence == CONFIDENCE_HIGH
    assert by_type["chart"].confidence == CONFIDENCE_HIGH
    assert by_type["quote"].confidence == CONFIDENCE_HIGH
    assert by_type["comparison"].confidence == CONFIDENCE_HIGH


def test_every_classification_has_reasons(classifications):
    assert all(c.reasons for c in classifications)


def _classify_one(shapes, number=2, slide_count=3):
    deck = make_inventory(
        [
            slide(1, [text_shape("s1", [[run("x")]])]),
            slide(number, shapes),
            slide(3, [text_shape("s3", [[run("y")]])]),
        ]
    )
    target = deck.slides[number - 1]
    return SlideClassifier().classify(target, slide_count, deck.slide_size)


def test_team_detected_from_three_pictures():
    shapes = [
        picture_shape("p1", 100, 100, 400, 400),
        picture_shape("p2", 700, 100, 400, 400),
        picture_shape("p3", 1300, 100, 400, 400),
    ]
    assert _classify_one(shapes).slide_type == "team"


def test_image_focus_detected_from_large_picture():
    shapes = [picture_shape("hero", 200, 100, 1500, 900), text_shape("cap", [[run("A caption")]], y=1000, h=60)]
    assert _classify_one(shapes).slide_type == "image_focus"


def test_agenda_beats_title_on_first_slide():
    shapes = [
        text_shape("agenda_title", [[run("Agenda", size=32, bold=True)]]),
        text_shape("items", [[run("Intro")], [run("Details")], [run("Wrap up")]], y=300),
    ]
    deck = make_inventory([slide(1, shapes)])
    result = SlideClassifier().classify(deck.slides[0], 1, deck.slide_size)
    assert result.slide_type == "agenda"


def test_closing_detected_by_wording():
    shapes = [text_shape("thanks", [[run("Thank you", size=40, bold=True)]])]
    assert _classify_one(shapes).slide_type == "closing"


def test_stat_callout_detected():
    shapes = [
        text_shape("ctx", [[run("Uptime last quarter", size=20)]]),
        text_shape("stat", [[run("99.9%", size=96, bold=True)]], y=300),
        text_shape("label", [[run("measured monthly", size=18)]], y=600),
    ]
    result = _classify_one(shapes)
    assert result.slide_type == "stat_callout"
    assert "99.9%" in result.reasons[0]


def test_stat_callout_requires_numbers():
    shapes = [
        text_shape("ctx", [[run("Our biggest win", size=20)]]),
        text_shape("big", [[run("Momentum", size=96, bold=True)]], y=300),
        text_shape("label", [[run("why it matters", size=18)]], y=600),
    ]
    assert _classify_one(shapes).slide_type != "stat_callout"


def test_dominant_table_detected():
    shapes = [
        text_shape("title", [[run("Pricing", size=32, bold=True)]]),
        text_shape(
            "table",
            [[run("")]],
            x=100,
            y=200,
            w=1600,
            h=600,
            shape_type="TABLE",
            extra={"has_table": True},
        ),
    ]
    assert _classify_one(shapes).slide_type == "table"


def test_process_detected_from_arrow_shapes():
    shapes = [
        text_shape(
            f"step{i}",
            [[run("Step")]],
            x=100 + i * 500,
            y=400,
            w=400,
            h=200,
            shape_type="AUTO_SHAPE/RIGHT_ARROW",
            shape_id=i + 1,
        )
        for i in range(3)
    ]
    assert _classify_one(shapes).slide_type == "process"


def test_timeline_detected_from_year_labels():
    shapes = [
        text_shape(
            f"year{i}",
            [[run(year)]],
            x=150 + i * 550,
            y=400,
            w=300,
            h=120,
            shape_id=i + 1,
        )
        for i, year in enumerate(["2023", "2024", "2025", "2026"])
    ]
    assert _classify_one(shapes).slide_type == "timeline"


def test_undistinguished_slide_falls_back_to_other():
    shapes = [
        text_shape(
            "paragraph",
            [[run("This is one long paragraph of ordinary prose that fits no rule.")]],
        )
    ]
    result = _classify_one(shapes)
    assert result.slide_type == "other"


def test_comparison_requires_disjoint_blocks():
    shapes = [
        text_shape(
            name,
            [[run("")]],
            x=x,
            y=260,
            w=800,
            h=620,
            shape_type="AUTO_SHAPE/ROUNDED_RECTANGLE",
            shape_id=i + 1,
        )
        for i, (name, x) in enumerate((("left", 120), ("right", 1000)))
    ]
    shapes.append(text_shape("t", [[run("Compare these", size=24, bold=True)]], y=60, h=80, shape_id=3))
    assert _classify_one(shapes).slide_type == "comparison"
