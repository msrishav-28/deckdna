"""Generate tests/fixtures/sample_deck.pptx — a small, non-confidential,
fully synthetic deck used by the parser tests. Deterministic output."""

from __future__ import annotations

import argparse
import io
import struct
import sys
import zlib
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Pt

EMU_PER_PX = 6350  # 12192000 EMU / 1920 px on a 16:9 deck
SLIDE_W_EMU = 12192000
SLIDE_H_EMU = 6858000

NAVY = RGBColor(0x0B, 0x1F, 0x3A)
TEAL = RGBColor(0x18, 0xA9, 0x99)
SLATE = RGBColor(0x26, 0x32, 0x38)
CARD = RGBColor(0xF5, 0xF7, 0xFA)


def px(value: int) -> Emu:
    return Emu(int(value) * EMU_PER_PX)


def make_png(rgb=(0x18, 0xA9, 0x99)) -> bytes:
    """Build a minimal valid PNG without external image dependencies."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00" + bytes(rgb)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def add_textbox(slide, name, x, y, w, h, lines):
    """Add a named textbox. Each line dict may set text/size/bold/italic/color;
    unset properties stay inherited (recorded as None by the parser)."""
    box = slide.shapes.add_textbox(px(x), px(y), px(w), px(h))
    box.name = name
    text_frame = box.text_frame
    for i, spec in enumerate(lines):
        paragraph = text_frame.paragraphs[0] if i == 0 else text_frame.add_paragraph()
        run = paragraph.add_run()
        run.text = spec["text"]
        font = run.font
        font.name = spec.get("font", "Inter")
        if spec.get("size") is not None:
            font.size = Pt(spec["size"])
        if spec.get("bold") is not None:
            font.bold = spec["bold"]
        if spec.get("italic") is not None:
            font.italic = spec["italic"]
        if spec.get("color") is not None:
            font.color.rgb = spec["color"]
        elif spec.get("theme_color") is not None:
            font.color.theme_color = spec["theme_color"]
    return box


def build_deck() -> Presentation:
    prs = Presentation()
    prs.slide_width = Emu(SLIDE_W_EMU)
    prs.slide_height = Emu(SLIDE_H_EMU)
    blank = prs.slide_layouts[6]

    # Slide 1 — title
    slide = prs.slides.add_slide(blank)
    add_textbox(
        slide, "deck_title", 120, 280, 1200, 200,
        [{"text": "Q3 Product Roadmap", "size": 44, "bold": True, "color": NAVY}],
    )
    add_textbox(
        slide, "deck_subtitle", 124, 500, 1000, 80,
        [{"text": "From platform stability to growth acceleration", "size": 20, "color": SLATE}],
    )
    logo = slide.shapes.add_picture(io.BytesIO(make_png()), px(1620), px(900), px(190), px(60))
    logo.name = "logo_placeholder"

    # Slide 2 — bullets + table
    slide = prs.slides.add_slide(blank)
    add_textbox(
        slide, "section_title", 120, 100, 1200, 90,
        [{"text": "Three Q3 priorities", "size": 32, "bold": True, "color": NAVY}],
    )
    add_textbox(
        slide, "priorities_body", 120, 260, 1300, 320,
        [
            {"text": "Improve activation through guided onboarding", "size": 18, "color": SLATE},
            {"text": "Launch the analytics workflow for enterprise teams", "size": 18, "color": SLATE},
            {"text": "Reduce platform incident response time by 30%", "size": 18, "color": SLATE},
        ],
    )
    table_shape = slide.shapes.add_table(2, 2, px(120), px(620), px(700), px(240))
    table_shape.name = "priorities_table"
    table = table_shape.table
    table.cell(0, 0).text = "Priority"
    table.cell(0, 1).text = "Owner"
    table.cell(1, 0).text = "Onboarding"
    table.cell(1, 1).text = "Platform"

    # Slide 3 — stat callout + chart
    slide = prs.slides.add_slide(blank)
    add_textbox(
        slide, "stat_context", 120, 100, 1200, 80,
        [{"text": "Response time, quarter over quarter", "size": 20, "color": SLATE}],
    )
    add_textbox(
        slide, "stat_number", 120, 300, 600, 220,
        [{"text": "30%", "size": 96, "bold": True, "color": TEAL}],
    )
    add_textbox(
        slide, "stat_label", 124, 540, 600, 80,
        [{"text": "Reduction in incident response time", "size": 18, "color": SLATE}],
    )
    chart_data = CategoryChartData()
    chart_data.categories = ["Q1", "Q2", "Q3"]
    chart_data.add_series("Incident response time (hours)", (12.0, 9.0, 8.4))
    chart_frame = slide.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, px(700), px(250), px(900), px(500), chart_data
    )
    chart_frame.name = "response_time_chart"

    # Slide 4 — quote + nested group
    slide = prs.slides.add_slide(blank)
    add_textbox(
        slide, "quote_text", 240, 300, 1440, 300,
        [{"text": "The fastest path to trust is a system that admits what it does not know.",
          "size": 28, "italic": True, "color": NAVY}],
    )
    add_textbox(
        slide, "quote_attribution", 240, 640, 800, 60,
        [{"text": "— DeckDNA engineering", "size": 16, "color": SLATE}],
    )
    group = slide.shapes.add_group_shape()
    group.name = "quote_footer_group"
    footer = group.shapes.add_textbox(px(240), px(760), px(800), px(80))
    footer.name = "quote_footer"
    footer_run = footer.text_frame.paragraphs[0].add_run()
    footer_run.text = "Quote layout v2"
    footer_run.font.name = "Inter"
    footer_run.font.size = Pt(12)
    footer_run.font.color.rgb = SLATE

    # Slide 5 — comparison cards
    slide = prs.slides.add_slide(blank)
    for name, x in (("card_now", 120), ("card_next", 1000)):
        card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, px(x), px(260), px(800), px(620))
        card.name = name
        card.fill.solid()
        card.fill.fore_color.rgb = CARD
        card.line.fill.background()
    add_textbox(
        slide, "now_header", 160, 300, 720, 80,
        [{"text": "Now", "size": 24, "bold": True, "color": NAVY}],
    )
    add_textbox(
        slide, "next_header", 1040, 300, 720, 80,
        [{"text": "Next", "color": TEAL}],
    )
    add_textbox(
        slide, "now_body", 160, 400, 720, 400,
        [
            {"text": "Manual deck assembly", "size": 16, "color": SLATE},
            {"text": "Brand review on every slide", "size": 16, "color": SLATE},
        ],
    )
    add_textbox(
        slide, "next_body", 1040, 400, 720, 400,
        [
            {"text": "Style learned once", "size": 16, "color": SLATE},
            {"text": "Decks generated on-brand", "size": 16, "color": SLATE},
        ],
    )

    # Slide 6 — placeholder-based appendix (tests inheritance recording)
    slide = prs.slides.add_slide(prs.slide_layouts[0])
    slide.shapes.title.text = "Appendix"
    subtitle = slide.placeholders[1]
    subtitle.text = "Data behind the roadmap"
    subtitle.text_frame.paragraphs[0].runs[0].font.color.theme_color = MSO_THEME_COLOR.ACCENT_1

    return prs


def main() -> int:
    default_out = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_deck.pptx"
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=default_out)
    args = ap.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    build_deck().save(str(args.output))
    print(f"Fixture deck written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
