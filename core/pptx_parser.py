"""PPTX structural parser (Milestone 1: parser-only extraction).

Opens a .pptx file and produces a DeckInventory: an honest structural record
of slides, shapes, text runs, colors, and theme definitions. No rendering,
no vision inference — native file data only.

Anything the parser cannot fully extract (charts, table cells, non-solid
fills, nested groups) is recorded in notes/warnings and logged, never
silently dropped.
"""

from __future__ import annotations

import logging
import math
import zipfile
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from lxml import etree
from pptx import Presentation
from pptx.exceptions import PackageNotFoundError

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
    ThemeInfo,
)

logger = logging.getLogger("deckdna.parser")

CANVAS_WIDTH_PX = 1920.0

_A_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}

_THEME_ROLE_BY_ENUM_NAME = {
    "DARK_1": "dk1",
    "LIGHT_1": "lt1",
    "DARK_2": "dk2",
    "LIGHT_2": "lt2",
    "ACCENT_1": "accent1",
    "ACCENT_2": "accent2",
    "ACCENT_3": "accent3",
    "ACCENT_4": "accent4",
    "ACCENT_5": "accent5",
    "ACCENT_6": "accent6",
    "HYPERLINK": "hlink",
    "FOLLOWED_HYPERLINK": "folHlink",
}


class DeckParseError(Exception):
    """Raised when a file cannot be parsed as a .pptx deck."""


class DeckParser:
    def parse(self, pptx_path: Path) -> DeckInventory:
        path = Path(pptx_path)
        if not path.is_file():
            raise DeckParseError(f"File not found: {path}")
        if path.suffix.lower() != ".pptx":
            raise DeckParseError(
                f"Unsupported file type '{path.suffix}'. Only .pptx is supported; "
                "legacy .ppt files must be converted to .pptx first."
            )
        try:
            prs = Presentation(str(path))
        except PackageNotFoundError as exc:
            raise DeckParseError(
                f"'{path.name}' is not a readable PowerPoint file: {exc}"
            ) from exc
        except zipfile.BadZipFile as exc:
            raise DeckParseError(
                f"'{path.name}' is corrupt or not a PowerPoint file: {exc}"
            ) from exc
        except KeyError as exc:
            # python-pptx raises KeyError when the zip lacks the OOXML manifest.
            raise DeckParseError(
                f"'{path.name}' is not a readable PowerPoint file: missing part {exc}"
            ) from exc

        slide_size = self._slide_size(prs)
        theme, deck_warnings = self._extract_theme(prs)
        slides: List[SlideInventory] = []
        for number, slide in enumerate(prs.slides, start=1):
            slides.append(self._parse_slide(slide, number, slide_size, theme))

        for warning in deck_warnings:
            logger.warning("%s", warning)
        for slide_inv in slides:
            for warning in slide_inv.warnings:
                logger.warning("slide %d: %s", slide_inv.slide_number, warning)

        return DeckInventory(
            source_file=str(path),
            slide_count=len(prs.slides),
            slide_size=slide_size,
            theme=theme,
            warnings=deck_warnings,
            slides=slides,
        )

    def parse_to_file(self, pptx_path: Path, output_path: Path) -> Path:
        inventory = self.parse(pptx_path)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(inventory.model_dump_json(indent=2), encoding="utf-8")
        return out

    # -- deck level ---------------------------------------------------------

    def _slide_size(self, prs) -> SlideSize:
        width, height = int(prs.slide_width), int(prs.slide_height)
        if width <= 0 or height <= 0:
            raise DeckParseError("Deck reports an invalid slide size.")
        divisor = math.gcd(width, height)
        scale = CANVAS_WIDTH_PX / width
        return SlideSize(
            width_emu=width,
            height_emu=height,
            width_px=round(width * scale, 1),
            height_px=round(height * scale, 1),
            aspect_ratio=f"{width // divisor}:{height // divisor}",
        )

    def _extract_theme(self, prs) -> Tuple[Optional[ThemeInfo], List[str]]:
        theme_parts = sorted(
            (
                part
                for part in prs.part.package.iter_parts()
                if str(part.partname).startswith("/ppt/theme/")
            ),
            key=lambda part: str(part.partname),
        )
        if not theme_parts:
            return None, ["No theme part found; theme colors and fonts are unavailable."]

        warnings: List[str] = []
        if len(theme_parts) > 1:
            warnings.append(
                f"{len(theme_parts)} theme parts found; using the first "
                f"({theme_parts[0].partname})."
            )

        root = etree.fromstring(theme_parts[0].blob)
        colors: dict = {}
        clr_scheme = root.find(".//a:clrScheme", _A_NS)
        if clr_scheme is not None:
            for element in clr_scheme:
                role = etree.QName(element).localname
                srgb = element.find("a:srgbClr", _A_NS)
                sys_clr = element.find("a:sysClr", _A_NS)
                if srgb is not None and srgb.get("val"):
                    colors[role] = f"#{srgb.get('val').upper()}"
                elif sys_clr is not None and sys_clr.get("lastClr"):
                    colors[role] = f"#{sys_clr.get('lastClr').upper()}"

        major_font = minor_font = None
        font_scheme = root.find(".//a:fontScheme", _A_NS)
        if font_scheme is not None:
            major_el = font_scheme.find("a:majorFont/a:latin", _A_NS)
            minor_el = font_scheme.find("a:minorFont/a:latin", _A_NS)
            if major_el is not None:
                major_font = major_el.get("typeface")
            if minor_el is not None:
                minor_font = minor_el.get("typeface")

        if not colors and major_font is None and minor_font is None:
            return None, ["Theme part present but no color or font scheme could be read."]
        return ThemeInfo(colors=colors, major_font=major_font, minor_font=minor_font), warnings

    # -- slide level --------------------------------------------------------

    def _parse_slide(self, slide, number: int, slide_size: SlideSize, theme) -> SlideInventory:
        warnings: List[str] = []
        shapes = list(
            self._walk_shapes(slide.shapes, slide_size, theme, warnings, depth=0)
        )
        background_color, background_note = self._slide_background(slide, theme)
        return SlideInventory(
            slide_number=number,
            layout_name=slide.slide_layout.name,
            background_color=background_color,
            background_note=background_note,
            shapes=shapes,
            warnings=warnings,
        )

    def _slide_background(self, slide, theme) -> Tuple[Optional[ColorInfo], Optional[str]]:
        try:
            fill = slide.background.fill
            fill_type = fill.type
        except Exception as exc:
            return None, f"background unavailable: {exc}"
        if fill_type is None:
            return None, None
        type_name = getattr(fill_type, "name", str(fill_type))
        if type_name == "SOLID":
            try:
                color = self._color(fill.fore_color, theme)
            except Exception as exc:
                return None, f"background color unavailable: {exc}"
            if color is not None:
                return color, None
            return None, "solid background present but its color could not be read"
        return None, f"background fill type {type_name} is not extracted in this milestone"

    # -- shape level --------------------------------------------------------

    def _walk_shapes(
        self, shapes, slide_size: SlideSize, theme, warnings: List[str], depth: int
    ) -> Iterator[ShapeRecord]:
        for z_index, shape in enumerate(shapes):
            yield self._parse_shape(shape, z_index, slide_size, theme, warnings, depth)
            if self._shape_kind(shape) == "GROUP":
                yield from self._walk_shapes(
                    shape.shapes, slide_size, theme, warnings, depth + 1
                )

    def _parse_shape(
        self, shape, z_index: int, slide_size: SlideSize, theme, warnings: List[str], depth: int
    ) -> ShapeRecord:
        notes: List[str] = []
        kind = self._shape_kind(shape)

        geometry, geometry_note = self._geometry(shape, slide_size)
        fill_color, fill_note = self._fill(shape, theme)
        line_color, line_note = self._line(shape, theme)

        placeholder_type = None
        is_placeholder = bool(getattr(shape, "is_placeholder", False))
        if is_placeholder:
            try:
                placeholder_type = shape.placeholder_format.type.name
            except Exception:
                placeholder_type = "UNKNOWN"

        text = self._text(shape, theme) if getattr(shape, "has_text_frame", False) else None

        is_picture = kind == "PICTURE"
        picture_name = None
        if is_picture:
            try:
                filename = shape.image.filename
                picture_name = filename or None
            except Exception as exc:
                notes.append(f"picture metadata unavailable: {exc}")

        has_chart = bool(getattr(shape, "has_chart", False))
        has_table = bool(getattr(shape, "has_table", False))
        table_dimensions = None
        if has_table:
            try:
                table_dimensions = f"{len(shape.table.rows)} x {len(shape.table.columns)}"
            except Exception as exc:
                notes.append(f"table dimensions unavailable: {exc}")
            notes.append("Table detected; cell contents are not extracted in this milestone.")
        if has_chart:
            notes.append("Chart detected; chart internals are not extracted in this milestone.")
            warnings.append(
                f"chart on shape '{shape.name}' recorded without internals"
            )
        if depth > 0:
            notes.append("Nested in a group; coordinates are group-relative.")

        return ShapeRecord(
            shape_id=int(getattr(shape, "shape_id", -1)),
            name=shape.name,
            shape_type=kind,
            z_index=z_index,
            depth=depth,
            geometry=geometry,
            geometry_note=geometry_note,
            fill_color=fill_color,
            fill_note=fill_note,
            line_color=line_color,
            line_note=line_note,
            is_placeholder=is_placeholder,
            placeholder_type=placeholder_type,
            text=text,
            is_picture=is_picture,
            picture_name=picture_name,
            has_chart=has_chart,
            has_table=has_table,
            table_dimensions=table_dimensions,
            extraction_notes=notes,
        )

    def _shape_kind(self, shape) -> str:
        try:
            shape_type = shape.shape_type
        except Exception:
            shape_type = None
        if shape_type is not None:
            name = getattr(shape_type, "name", str(shape_type))
            if name == "AUTO_SHAPE":
                try:
                    return f"AUTO_SHAPE/{shape.auto_shape_type.name}"
                except Exception:
                    return "AUTO_SHAPE"
            return name
        if getattr(shape, "has_chart", False):
            return "CHART"
        if getattr(shape, "has_table", False):
            return "TABLE"
        if getattr(shape, "shapes", None) is not None:
            return "GROUP"
        return "UNKNOWN"

    def _geometry(self, shape, slide_size: SlideSize) -> Tuple[Optional[Geometry], Optional[str]]:
        try:
            x, y, width, height = shape.left, shape.top, shape.width, shape.height
        except Exception as exc:
            return None, f"position unavailable: {exc}"
        if x is None or y is None or width is None or height is None:
            return None, "shape has no explicit position"
        scale = CANVAS_WIDTH_PX / slide_size.width_emu
        return (
            Geometry(
                x_emu=int(x),
                y_emu=int(y),
                width_emu=int(width),
                height_emu=int(height),
                x_px=round(int(x) * scale, 1),
                y_px=round(int(y) * scale, 1),
                width_px=round(int(width) * scale, 1),
                height_px=round(int(height) * scale, 1),
            ),
            None,
        )

    def _fill(self, shape, theme) -> Tuple[Optional[ColorInfo], Optional[str]]:
        if not hasattr(shape, "fill"):
            return None, None
        try:
            fill = shape.fill
            fill_type = fill.type
        except Exception as exc:
            return None, f"fill unavailable: {exc}"
        if fill_type is None:
            return None, None
        type_name = getattr(fill_type, "name", str(fill_type))
        if type_name == "BACKGROUND":
            return None, None
        if type_name == "SOLID":
            try:
                color = self._color(fill.fore_color, theme)
            except Exception as exc:
                return None, f"solid fill color unavailable: {exc}"
            if color is not None:
                return color, None
            return None, "solid fill present but its color could not be read"
        return None, f"fill type {type_name} is not extracted in this milestone"

    def _line(self, shape, theme) -> Tuple[Optional[ColorInfo], Optional[str]]:
        if not hasattr(shape, "line"):
            return None, None
        try:
            line = shape.line
            fill_type = line.fill.type
        except Exception as exc:
            return None, f"line unavailable: {exc}"
        if fill_type is None:
            return None, None
        type_name = getattr(fill_type, "name", str(fill_type))
        if type_name == "BACKGROUND":
            return None, None
        if type_name == "SOLID":
            try:
                color = self._color(line.color, theme)
            except Exception as exc:
                return None, f"line color unavailable: {exc}"
            if color is not None:
                return color, None
            return None, "solid line present but its color could not be read"
        return None, f"line fill type {type_name} is not extracted in this milestone"

    # -- text level ---------------------------------------------------------

    def _text(self, shape, theme) -> TextInfo:
        text_frame = shape.text_frame
        paragraphs: List[ParagraphInfo] = []
        for paragraph in text_frame.paragraphs:
            alignment = None
            if paragraph.alignment is not None:
                alignment = getattr(paragraph.alignment, "name", str(paragraph.alignment))
            paragraphs.append(
                ParagraphInfo(
                    alignment=alignment,
                    runs=[self._run(run, theme) for run in paragraph.runs],
                )
            )
        joined = "\n".join(
            "".join(run.text for run in paragraph.runs) for paragraph in paragraphs
        )
        return TextInfo(text=joined, paragraphs=paragraphs)

    def _run(self, run, theme) -> TextRun:
        font = run.font
        size_pt = None
        if font.size is not None:
            size_pt = round(float(font.size.pt), 1)
        return TextRun(
            text=run.text,
            font_family=font.name,
            font_size_pt=size_pt,
            bold=font.bold,
            italic=font.italic,
            color=self._color(font.color, theme),
        )

    def _color(self, color_format, theme: Optional[ThemeInfo]) -> Optional[ColorInfo]:
        try:
            color_type = color_format.type
        except Exception:
            return None
        if color_type is None:
            return None
        type_name = getattr(color_type, "name", str(color_type))
        if type_name in ("RGB", "SRGB"):
            return ColorInfo(hex=f"#{str(color_format.rgb).upper()}")
        if type_name == "SCHEME":
            try:
                theme_color = color_format.theme_color
            except Exception:
                return None
            enum_name = getattr(theme_color, "name", str(theme_color))
            role = _THEME_ROLE_BY_ENUM_NAME.get(enum_name, enum_name.lower())
            resolved = theme.colors.get(role) if theme is not None else None
            return ColorInfo(theme_role=role, hex=resolved)
        return None
