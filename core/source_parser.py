"""Format dispatch: pick the right parser for a source path.

Supported sources: .pptx decks, .pdf files with a text layer, .png images,
or folders of .png slide images. Legacy binary .ppt is not supported and
the error says how to convert instead of failing obscurely.
"""

from __future__ import annotations

from pathlib import Path

from core.pptx_parser import DeckParseError, DeckParser
from core.schemas import DeckInventory


def parse_source(source: Path) -> DeckInventory:
    path = Path(source)
    if path.is_dir():
        return _raster().parse(path)
    suffix = path.suffix.lower()
    if suffix == ".pptx":
        return DeckParser().parse(path)
    if suffix == ".pdf":
        return _pdf().parse(path)
    if suffix == ".png":
        return _raster().parse(path)
    if suffix == ".ppt":
        raise DeckParseError(
            "Legacy .ppt files are not supported; save the deck as .pptx "
            "(or export it to PDF) first."
        )
    raise DeckParseError(
        f"Unsupported file type '{path.suffix}'. Supported: .pptx, .pdf, "
        ".png, or a folder of .png slide images."
    )


# Lazy: the pdf/raster parsers pull in PyMuPDF; pptx-only runs skip that cost.
def _pdf():
    from core.pdf_parser import PdfParser

    return PdfParser()


def _raster():
    from core.raster_parser import RasterParser

    return RasterParser()
