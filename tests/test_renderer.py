"""Tests for slide rendering: .pptx -> one PNG per slide.

The LibreOffice path is tested without LibreOffice by substituting a fake
soffice command that produces a real two-page PDF. The PowerPoint COM path
runs as a real integration test only when desktop PowerPoint is installed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from core.renderer import (
    LibreOfficeRenderer,
    PowerPointComRenderer,
    RenderError,
    SlideRenderer,
    _pdf_to_pngs,
    select_renderer,
)


def make_pdf(path, pages=2, width_pt=960.0, height_pt=540.0):
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=width_pt, height=height_pt)
        page.draw_rect(pymupdf.Rect(0, 0, width_pt, height_pt), color=None, fill=(0.8, 0.8, 0.8))
    doc.save(str(path))
    doc.close()
    return path


def png_size(path):
    from PIL import Image

    with Image.open(path) as image:
        return image.size


# -- input validation -------------------------------------------------------


def test_missing_file_raises(fixture_deck, tmp_path):
    with pytest.raises(RenderError, match="File not found"):
        LibreOfficeRenderer().render_pptx(tmp_path / "nope.pptx", tmp_path)


def test_non_pptx_rejected(fixture_deck, tmp_path):
    fake = tmp_path / "deck.pdf"
    fake.write_bytes(b"not a deck")
    with pytest.raises(RenderError, match="only .pptx"):
        LibreOfficeRenderer().render_pptx(fake, tmp_path)


# -- LibreOffice path -------------------------------------------------------


def test_unavailable_libreoffice_fails_loudly(monkeypatch, fixture_deck, tmp_path):
    monkeypatch.setattr("shutil.which", lambda name: None)
    renderer = LibreOfficeRenderer()
    assert renderer.available() is False
    with pytest.raises(RenderError, match="LibreOffice"):
        renderer.render_pptx(fixture_deck, tmp_path / "slides")


def test_explicit_soffice_path_must_exist():
    renderer = LibreOfficeRenderer(soffice_path="Z:/nowhere/soffice.exe")
    assert renderer.available() is False


def test_libreoffice_render_via_fake_soffice(monkeypatch, fixture_deck, tmp_path):
    """Verify the soffice command is built correctly and the PDF-to-PNG
    stage runs end to end, without needing LibreOffice installed."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        outdir = Path(cmd[cmd.index("--outdir") + 1])
        make_pdf(outdir / (fixture_deck.stem + ".pdf"), pages=2)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("shutil.which", lambda name: "C:/fake/soffice.exe")
    monkeypatch.setattr("core.renderer.subprocess.run", fake_run)

    out_dir = tmp_path / "slides"
    pages = LibreOfficeRenderer().render_pptx(fixture_deck, out_dir)

    cmd = captured["cmd"]
    assert cmd[0] == "C:/fake/soffice.exe"
    assert "--headless" in cmd
    assert "--convert-to" in cmd
    assert cmd[cmd.index("--convert-to") + 1] == "pdf"
    assert str(fixture_deck) in cmd

    assert [p.name for p in pages] == ["slide_001.png", "slide_002.png"]
    for page in pages:
        assert page.is_file()
        assert png_size(page) == (1920, 1080)


def test_libreoffice_conversion_failure_raises(monkeypatch, fixture_deck, tmp_path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr("shutil.which", lambda name: "C:/fake/soffice.exe")
    monkeypatch.setattr("core.renderer.subprocess.run", fake_run)
    with pytest.raises(RenderError, match="failed to convert"):
        LibreOfficeRenderer().render_pptx(fixture_deck, tmp_path / "slides")


def test_empty_pdf_raises(monkeypatch, tmp_path):
    import pymupdf

    class FakeEmptyDoc:
        page_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(pymupdf, "open", lambda path: FakeEmptyDoc())
    with pytest.raises(RenderError, match="no pages"):
        _pdf_to_pngs(tmp_path / "empty.pdf", tmp_path)


# -- PowerPoint COM path ----------------------------------------------------


def test_powerpoint_com_availability_is_boolean():
    assert isinstance(PowerPointComRenderer().available(), bool)


@pytest.mark.skipif(
    sys.platform != "win32" or not PowerPointComRenderer().available(),
    reason="desktop PowerPoint not available on this machine",
)
def test_powerpoint_com_renders_fixture_deck(fixture_deck, tmp_path):
    renderer = PowerPointComRenderer()
    pages = renderer.render_pptx(fixture_deck, tmp_path / "slides")
    assert [p.name for p in pages] == [f"slide_{i:03d}.png" for i in range(1, 7)]
    for page in pages:
        assert page.is_file()
        assert png_size(page) == (1920, 1080)
    # The temporary export staging directory must be cleaned up.
    assert not (tmp_path / "slides" / "_pptx_export").exists()


# -- renderer selection -----------------------------------------------------


def test_select_renderer_prefers_libreoffice(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "C:/fake/soffice.exe")
    assert isinstance(select_renderer(), LibreOfficeRenderer)


def test_select_renderer_raises_when_nothing_available(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("core.renderer.PowerPointComRenderer.available", lambda self: False)
    with pytest.raises(RenderError, match="No slide renderer available"):
        select_renderer()


def test_renderers_satisfy_protocol():
    assert isinstance(LibreOfficeRenderer(), SlideRenderer)
    assert isinstance(PowerPointComRenderer(), SlideRenderer)
