"""Tests for the raster parser: PNG files and folders of PNG slides become
inventories whose only facts are image size and measured pixel colours."""

from __future__ import annotations

import pymupdf
import pytest

from core.extractor import StyleGuideExtractor
from core.pptx_parser import DeckParseError
from core.raster_parser import RasterParser
from core.style_guide import PROVENANCE_MEASURED

WIDTH_PX = 800
HEIGHT_PX = 450


def _solid_png(path, rgb_int: int, width: int = WIDTH_PX, height: int = HEIGHT_PX) -> None:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height))
    # set_rect takes int channels 0-255 (clear_with would take only the low
    # byte and replicate it across channels).
    pix.set_rect(
        pix.irect,
        ((rgb_int >> 16) & 0xFF, (rgb_int >> 8) & 0xFF, rgb_int & 0xFF),
    )
    pix.save(str(path))


@pytest.fixture()
def slides_dir(tmp_path):
    folder = tmp_path / "design"
    folder.mkdir()
    _solid_png(folder / "slide-2.png", 0x1D2BE0)
    _solid_png(folder / "slide-10.png", 0xF2F2F2)
    _solid_png(folder / "slide-1.png", 0x0E0E10)
    return folder


@pytest.fixture()
def inventory(slides_dir):
    return RasterParser().parse(slides_dir)


# -- folder ordering ------------------------------------------------------


def test_numeric_name_ordering_not_lexicographic(inventory):
    names = [slide.shapes[0].name for slide in inventory.slides]
    assert names == ["slide-1", "slide-2", "slide-10"]


def test_slide_numbers_are_sequential(inventory):
    assert [s.slide_number for s in inventory.slides] == [1, 2, 3]
    assert all(s.layout_name == "image" for s in inventory.slides)


# -- geometry -------------------------------------------------------------


def test_slide_size_derived_at_96_dpi(inventory):
    size = inventory.slide_size
    assert size.width_emu == WIDTH_PX * 9525  # 7620000
    assert size.height_emu == HEIGHT_PX * 9525  # 4286250
    assert size.aspect_ratio == "16:9"
    assert size.width_px == 1920.0
    assert size.height_px == 1080.0


def test_single_full_bleed_picture_per_slide(inventory):
    for slide in inventory.slides:
        assert len(slide.shapes) == 1
        shape = slide.shapes[0]
        assert shape.is_picture and shape.shape_type == "PICTURE"
        geo = shape.geometry
        assert geo is not None
        assert geo.x_emu == 0 and geo.y_emu == 0
        assert geo.width_emu == inventory.slide_size.width_emu
        assert geo.width_px == 1920.0 and geo.height_px == 1080.0
        assert geo.width_px == inventory.slide_size.width_px


# -- measured colours -----------------------------------------------------


def test_solid_image_yields_its_own_colour(inventory):
    expected = {"slide-1": "#0E0E10", "slide-2": "#1D2BE0", "slide-10": "#F2F2F2"}
    for slide in inventory.slides:
        hexes = [c.hex for c in slide.measured_colors]
        assert hexes == [expected[slide.shapes[0].name]]


def test_no_declared_background_or_text(inventory):
    for slide in inventory.slides:
        assert slide.background_color is None
        assert all(shape.text is None for shape in slide.shapes)


def test_deck_warnings_label_the_limitations(inventory):
    assert len(inventory.warnings) == 2
    joined = " ".join(inventory.warnings)
    assert "96 pixels per inch" in joined
    assert "heuristics" in joined


# -- extractor integration ------------------------------------------------


def test_palette_is_measured_provenance(inventory):
    guide = StyleGuideExtractor().extract(inventory)
    assert guide.palette, "expected a palette from measured colours"
    assert all(entry.provenance == PROVENANCE_MEASURED for entry in guide.palette)
    assert guide.source_file_type == "image_folder"
    assert any("measured from rendered pixels" in w for w in guide.warnings)


# -- single file ----------------------------------------------------------


def test_single_png_is_a_one_slide_deck(tmp_path):
    image = tmp_path / "hero.png"
    _solid_png(image, 0xF26A1B)
    inv = RasterParser().parse(image)
    assert inv.slide_count == 1
    assert inv.source_file == str(image)
    assert inv.slides[0].measured_colors[0].hex == "#F26A1B"


# -- failures -------------------------------------------------------------


def test_missing_source_raises(tmp_path):
    with pytest.raises(DeckParseError, match="File not found"):
        RasterParser().parse(tmp_path / "nope")


def test_empty_folder_raises(tmp_path):
    with pytest.raises(DeckParseError, match="No .png images found"):
        RasterParser().parse(tmp_path)


def test_non_png_file_raises(tmp_path):
    other = tmp_path / "photo.jpg"
    other.write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    with pytest.raises(DeckParseError, match="Unsupported file type"):
        RasterParser().parse(other)


def test_mixed_sizes_warn_and_follow_first(tmp_path):
    folder = tmp_path / "mixed"
    folder.mkdir()
    _solid_png(folder / "a.png", 0x102030, width=800, height=450)
    _solid_png(folder / "b.png", 0x405060, width=400, height=225)
    inv = RasterParser().parse(folder)
    assert inv.slide_size.width_emu == 800 * 9525
    assert not inv.slides[0].warnings
    assert any("different from image 1" in w for w in inv.slides[1].warnings)
