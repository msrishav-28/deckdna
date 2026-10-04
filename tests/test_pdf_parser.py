"""Tests for the PDF parser: one page becomes one slide, text comes from the
PDF text layer, colours are measured from rendered pixels, and every honesty
boundary surfaces as a warning or a DeckParseError."""

from __future__ import annotations

import pymupdf
import pytest

from core.pdf_parser import PdfParser
from core.pptx_parser import DeckParseError

PAGE_W_PT = 960.0
PAGE_H_PT = 540.0
PX_PER_PT = 1920.0 / PAGE_W_PT  # 2.0


def _rgb(value: int) -> tuple:
    """0-1 float components, the range pymupdf text/drawing APIs expect."""
    return (
        ((value >> 16) & 0xFF) / 255.0,
        ((value >> 8) & 0xFF) / 255.0,
        (value & 0xFF) / 255.0,
    )


def _orange_pixmap(size: int = 100) -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, size, size))
    # set_rect takes int channels 0-255 (clear_with would take only the low
    # byte and replicate it across channels).
    pix.set_rect(pix.irect, (0xF2, 0x6A, 0x1B))
    return pix


def make_pdf(path) -> pymupdf.Document:
    """Two pages: page 1 has a coloured title line, page 2 has one image."""
    doc = pymupdf.open()
    page1 = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    page1.insert_text(
        (72, 100),
        "Quarterly Review",
        fontsize=24,
        fontname="hebo",
        color=_rgb(0x3366CC),
    )
    page1.insert_text((72, 140), "Q3 results summary", fontsize=12, fontname="helv")
    page2 = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    page2.insert_image(pymupdf.Rect(96, 96, 196, 196), pixmap=_orange_pixmap())
    doc.save(str(path))
    doc.close()
    return pymupdf.open(str(path))


@pytest.fixture()
def pdf_path(tmp_path):
    path = tmp_path / "sample.pdf"
    make_pdf(path).close()
    return path


@pytest.fixture()
def inventory(pdf_path):
    return PdfParser().parse(pdf_path)


# -- deck level -----------------------------------------------------------


def test_two_slides_follow_page_count(inventory):
    assert inventory.slide_count == 2
    assert [s.slide_number for s in inventory.slides] == [1, 2]
    assert inventory.slides[0].layout_name == "pdf page 1"
    assert inventory.slides[1].layout_name == "pdf page 2"


def test_slide_size_matches_first_page(inventory):
    size = inventory.slide_size
    assert size.width_emu == int(round(PAGE_W_PT * 12700))  # 12192000
    assert size.height_emu == int(round(PAGE_H_PT * 12700))  # 6858000
    assert size.aspect_ratio == "16:9"
    assert size.width_px == 1920.0
    assert size.height_px == 1080.0


def test_deck_declares_its_honesty_boundaries(inventory):
    assert len(inventory.warnings) == 3
    joined = " ".join(inventory.warnings)
    assert "measured" in joined
    assert "vector drawings" in joined
    assert "text layer" not in joined  # text IS from the text layer


def test_theme_is_absent_not_fabricated(inventory):
    assert inventory.theme is None


# -- text layer -----------------------------------------------------------


def _block_containing(slide, needle: str):
    for shape in slide.shapes:
        if shape.text is not None and needle in shape.text.text:
            return shape
    raise AssertionError(f"no shape on slide {slide.slide_number} contains {needle!r}")


def test_title_text_survives_from_text_layer(inventory):
    slide = inventory.slides[0]
    block = _block_containing(slide, "Quarterly Review")
    assert block.shape_type == "PDF_TEXT_BLOCK"
    assert block.text is not None
    runs = [run for para in block.text.paragraphs for run in para.runs]
    title_run = next(run for run in runs if "Quarterly Review" in run.text)
    assert title_run.bold is True
    assert title_run.italic is False
    assert title_run.font_size_pt == 24.0
    assert title_run.color is not None
    assert title_run.color.hex == "#3366CC"


def test_text_geometry_scales_from_points(inventory):
    block = _block_containing(inventory.slides[0], "Quarterly Review")
    geo = block.geometry
    assert geo is not None
    assert geo.x_emu == pytest.approx(72 * 12700, abs=2540)  # 0.2 pt tolerance
    assert geo.x_px == pytest.approx(72 * PX_PER_PT, abs=0.5)
    # The vertical bbox depends on font ascent metrics, so assert placement
    # relative to the 100pt baseline instead of an exact top.
    baseline_px = 100 * PX_PER_PT
    assert geo.y_px < baseline_px < geo.y_px + geo.height_px + 1.0
    assert geo.height_px > 0
    # EMU and px are two independent conversions of the same points value.
    assert geo.width_px == pytest.approx(geo.width_emu / 12700 * PX_PER_PT, abs=0.1)
    assert geo.x_px == pytest.approx(geo.x_emu / 12700 * PX_PER_PT, abs=0.1)


# -- images ---------------------------------------------------------------


def test_page_image_recorded_at_placement(inventory):
    slide = inventory.slides[1]
    pictures = [s for s in slide.shapes if s.is_picture]
    assert len(pictures) == 1
    picture = pictures[0]
    assert picture.shape_type == "PICTURE"
    geo = picture.geometry
    assert geo is not None
    assert geo.x_emu == pytest.approx(96 * 12700, abs=2540)
    assert geo.x_px == pytest.approx(96 * PX_PER_PT, abs=0.5)
    assert geo.width_px == pytest.approx(100 * PX_PER_PT, abs=1.0)


def test_images_come_before_text_blocks(inventory):
    slide = inventory.slides[1]
    assert slide.shapes[0].is_picture
    assert all(s.is_picture for s in slide.shapes[:1])


# -- measured colours -----------------------------------------------------


def test_measured_colours_are_present_and_dominant(inventory):
    page1_hexes = [c.hex for c in inventory.slides[0].measured_colors]
    assert "#FFFFFF" in page1_hexes
    page2_hexes = [c.hex for c in inventory.slides[1].measured_colors]
    assert "#F26A1B" in page2_hexes


def test_declared_slide_background_stays_absent(inventory):
    for slide in inventory.slides:
        assert slide.background_color is None


# -- failures -------------------------------------------------------------


def test_missing_file_raises():
    with pytest.raises(DeckParseError, match="File not found"):
        PdfParser().parse("does-not-exist.pdf")


def test_corrupt_bytes_raise(tmp_path):
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"this is not a pdf at all")
    with pytest.raises(DeckParseError, match="not a readable PDF"):
        PdfParser().parse(corrupt)


def test_encrypted_pdf_raises(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    locked = tmp_path / "locked.pdf"
    doc.save(str(locked), encryption=pymupdf.PDF_ENCRYPT_AES_256,
             owner_pw="owner", user_pw="user")
    doc.close()
    with pytest.raises(DeckParseError, match="password-protected"):
        PdfParser().parse(locked)


def test_zero_page_pdf_raises(tmp_path, monkeypatch):
    class FakeEmptyDoc:
        needs_pass = False
        page_count = 0

        def close(self):
            pass

    # pymupdf refuses to save a zero-page file, so stub the open call; the
    # parser must still fail loudly on a document with no pages. The path
    # must exist because the parser checks for the file before opening it.
    target = tmp_path / "empty.pdf"
    target.write_bytes(b"%PDF-1.4 placeholder")
    monkeypatch.setattr(pymupdf, "open", lambda path: FakeEmptyDoc())
    with pytest.raises(DeckParseError, match="no pages"):
        PdfParser().parse(target)
