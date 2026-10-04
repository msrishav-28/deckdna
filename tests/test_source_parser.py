"""Tests for format dispatch: parse_source routes each source type to the
right parser and refuses legacy or unknown types with actionable errors."""

from __future__ import annotations

import pymupdf
import pytest

from core.pptx_parser import DeckParseError
from core.source_parser import parse_source


def _png(path, rgb_int: int = 0x123456) -> None:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 45))
    pix.clear_with(rgb_int)
    pix.save(str(path))


def _pdf(path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=960, height=540)
    page.insert_text((72, 72), "Dispatch check", fontsize=12)
    doc.save(str(path))
    doc.close()


def test_pptx_goes_to_deck_parser(fixture_deck):
    inventory = parse_source(fixture_deck)
    assert inventory.slide_count == 6
    assert inventory.theme is not None


def test_pdf_goes_to_pdf_parser(tmp_path):
    path = tmp_path / "doc.pdf"
    _pdf(path)
    inventory = parse_source(path)
    assert inventory.slide_count == 1
    assert any("PDF" in w for w in inventory.warnings)


def test_png_goes_to_raster_parser(tmp_path):
    path = tmp_path / "slide.png"
    _png(path)
    inventory = parse_source(path)
    assert inventory.slide_count == 1
    assert inventory.slides[0].shapes[0].is_picture


def test_folder_goes_to_raster_parser(tmp_path):
    folder = tmp_path / "slides"
    folder.mkdir()
    _png(folder / "slide-1.png", 0x0E0E10)
    _png(folder / "slide-2.png", 0x1D2BE0)
    inventory = parse_source(folder)
    assert inventory.slide_count == 2


def test_legacy_ppt_gets_conversion_guidance(tmp_path):
    legacy = tmp_path / "old deck.ppt"
    legacy.write_bytes(b"\xd0\xcf\x11\xe0 fake ole2 header")
    with pytest.raises(DeckParseError, match=r"Legacy \.ppt.*\.pptx"):
        parse_source(legacy)


def test_unknown_extension_lists_supported_types(tmp_path):
    docx = tmp_path / "notes.docx"
    docx.write_bytes(b"PK\x03\x04 fake zip")
    with pytest.raises(DeckParseError, match="Unsupported file type '.docx'"):
        parse_source(docx)


def test_missing_file_raises(tmp_path):
    with pytest.raises(DeckParseError, match="File not found"):
        parse_source(tmp_path / "absent.pdf")
