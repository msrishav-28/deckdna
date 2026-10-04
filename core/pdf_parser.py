"""PDF structural parser (multi-format input).

Turns a PDF into a DeckInventory: one page becomes one slide. Text is read
from the page's own text layer (get_text("dict")), so font name, size,
colour, bold and italic survive whenever the PDF carries them. Images are
recorded at their exact placement from the page's image objects.

Honesty boundaries, recorded as warnings rather than guessed away:
- PDFs declare no palette and no page fill. Colours are measured from the
  rendered page pixels (core.pixel_sampling) and stored in
  SlideInventory.measured_colors, never as authored facts.
- Vector drawings (rules, filled panels, arrows) are not extracted in this
  milestone; text blocks and images only.
- Text is grouped by PDF text blocks and lines, not by authored paragraphs.
  Themes, layout roles and placeholder types do not exist in PDF.
- Shape order is reconstructed as images-then-text (z_index is an ordinal
  display hint, not the original content-stream order).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import List, Tuple

import pymupdf

from core.pixel_sampling import measure_colors
from core.pptx_parser import DeckParseError
from core.schemas import (
    ColorInfo,
    DeckInventory,
    Geometry,
    ParagraphInfo,
    ShapeRecord,
    SlideInventory,
    SlideSize,
    TextInfo,
    TextRun,
)

CANVAS_WIDTH_PX = 1920.0
EMU_PER_PT = 12700
_MEASURE_RENDER_WIDTH_PX = 480.0
_MAX_MEASURED_COLORS = 6
_BOLD_FLAG = 16
_ITALIC_FLAG = 2
_SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")


def _geometry(bbox: Tuple[float, float, float, float], scale: float) -> Geometry:
    x0, y0, x1, y1 = bbox
    width_pt = max(0.0, x1 - x0)
    height_pt = max(0.0, y1 - y0)
    return Geometry(
        x_emu=int(round(x0 * EMU_PER_PT)),
        y_emu=int(round(y0 * EMU_PER_PT)),
        width_emu=int(round(width_pt * EMU_PER_PT)),
        height_emu=int(round(height_pt * EMU_PER_PT)),
        x_px=round(x0 * scale, 2),
        y_px=round(y0 * scale, 2),
        width_px=round(width_pt * scale, 2),
        height_px=round(height_pt * scale, 2),
    )


def _slide_size(rect: pymupdf.Rect) -> SlideSize:
    width_emu = int(round(rect.width * EMU_PER_PT))
    height_emu = int(round(rect.height * EMU_PER_PT))
    divisor = math.gcd(width_emu, height_emu) or 1
    return SlideSize(
        width_emu=width_emu,
        height_emu=height_emu,
        width_px=CANVAS_WIDTH_PX,
        height_px=round(CANVAS_WIDTH_PX * rect.height / rect.width, 2),
        aspect_ratio=f"{width_emu // divisor}:{height_emu // divisor}",
    )


def _run(span: dict) -> TextRun:
    font = span.get("font") or None
    if font:
        font = _SUBSET_PREFIX_RE.sub("", font)
    flags = span.get("flags", 0)
    color = None
    if span.get("color") is not None:
        color = ColorInfo(hex=f"#{span['color'] & 0xFFFFFF:06X}")
    return TextRun(
        text=span.get("text", ""),
        font_family=font,
        font_size_pt=float(span["size"]) if span.get("size") is not None else None,
        bold=bool(flags & _BOLD_FLAG),
        italic=bool(flags & _ITALIC_FLAG),
        color=color,
    )


class PdfParser:
    def parse(self, pdf_path: Path) -> DeckInventory:
        path = Path(pdf_path)
        if not path.is_file():
            raise DeckParseError(f"File not found: {path}")
        try:
            document = pymupdf.open(str(path))
        except Exception as exc:
            raise DeckParseError(
                f"'{path.name}' is not a readable PDF: {exc}"
            ) from exc
        try:
            if document.needs_pass:
                raise DeckParseError(
                    f"'{path.name}' is password-protected; remove the password "
                    "and try again."
                )
            if document.page_count == 0:
                raise DeckParseError(f"'{path.name}' contains no pages.")
            return self._build_inventory(path, document)
        finally:
            document.close()

    # -- inventory ----------------------------------------------------------

    def _build_inventory(self, path: Path, document: pymupdf.Document) -> DeckInventory:
        warnings: List[str] = [
            "PDF source: colours are measured from rendered page pixels rather "
            "than declared in the file; palette usage labels are heuristics.",
            "PDF source: vector drawings (lines, panels, arrows) are not "
            "extracted; text blocks and image placements are.",
            "PDF source: text is grouped by PDF text blocks and lines, not by "
            "authored paragraphs; themes, layout roles and placeholder types "
            "do not exist in PDF.",
        ]
        slides: List[SlideInventory] = []
        deck_size: SlideSize | None = None
        for page_number, page in enumerate(document, start=1):
            slide, page_size = self._slide(page, page_number)
            if deck_size is None:
                deck_size = page_size
            elif (page_size.width_emu, page_size.height_emu) != (
                deck_size.width_emu,
                deck_size.height_emu,
            ):
                slide.warnings.append(
                    f"Page {page_number} is {page.rect.width:g}x{page.rect.height:g}pt, "
                    "different from page 1; the deck canvas follows page 1."
                )
            slides.append(slide)
        if deck_size is None:
            raise DeckParseError(f"'{path.name}' contains no pages.")
        return DeckInventory(
            source_file=str(path),
            slide_count=len(slides),
            slide_size=deck_size,
            theme=None,
            warnings=warnings,
            slides=slides,
        )

    # -- one page -----------------------------------------------------------

    def _slide(
        self, page: pymupdf.Page, page_number: int
    ) -> Tuple[SlideInventory, SlideSize]:
        rect = page.rect
        scale = CANVAS_WIDTH_PX / rect.width
        shapes: List[ShapeRecord] = []
        shape_id = 1

        for info in page.get_image_info(xrefs=True):
            xref = info.get("xref", 0)
            name = f"pdf-image-{xref}" if xref else "pdf-inline-image"
            shapes.append(
                ShapeRecord(
                    shape_id=shape_id,
                    name=name,
                    shape_type="PICTURE",
                    z_index=len(shapes),
                    geometry=_geometry(tuple(info["bbox"]), scale),
                    is_picture=True,
                    picture_name=name,
                    extraction_notes=[
                        "Image recorded at its placement; pixel content is not "
                        "read by this parser."
                    ],
                )
            )
            shape_id += 1

        text_blocks = [
            block
            for block in page.get_text("dict")["blocks"]
            if block.get("type") == 0
        ]
        for index, block in enumerate(text_blocks, start=1):
            paragraphs: List[ParagraphInfo] = []
            for line in block.get("lines", []):
                runs = [
                    _run(span)
                    for span in line.get("spans", [])
                    if span.get("text")
                ]
                if runs:
                    paragraphs.append(ParagraphInfo(alignment=None, runs=runs))
            if not paragraphs:
                continue
            shapes.append(
                ShapeRecord(
                    shape_id=shape_id,
                    name=f"text-block-{index}",
                    shape_type="PDF_TEXT_BLOCK",
                    z_index=len(shapes),
                    geometry=_geometry(tuple(block["bbox"]), scale),
                    text=TextInfo(
                        text="\n".join(
                            "".join(run.text for run in paragraph.runs)
                            for paragraph in paragraphs
                        ),
                        paragraphs=paragraphs,
                    ),
                    extraction_notes=[
                        "Text read from the PDF text layer; one paragraph per "
                        "PDF line."
                    ],
                )
            )
            shape_id += 1

        warnings: List[str] = []
        if not shapes:
            warnings.append(
                f"Page {page_number} has no extractable text or images; it may "
                "be a scan or an empty page."
            )

        zoom = _MEASURE_RENDER_WIDTH_PX / rect.width
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        measured = measure_colors(pixmap, max_colors=_MAX_MEASURED_COLORS)
        measured_colors = [ColorInfo(hex=hex_color) for hex_color, _ in measured]

        return (
            SlideInventory(
                slide_number=page_number,
                layout_name=f"pdf page {page_number}",
                measured_colors=measured_colors,
                shapes=shapes,
                warnings=warnings,
            ),
            _slide_size(rect),
        )
