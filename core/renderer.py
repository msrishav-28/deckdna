"""Slide rendering adapters (Milestone 2): .pptx -> one PNG per slide.

Two real renderers exist:

- LibreOfficeRenderer: cross-platform default; converts the deck to PDF with
  ``soffice --headless`` and rasterizes each PDF page with PyMuPDF.
- PowerPointComRenderer: Windows-only; drives desktop PowerPoint through its
  COM automation interface for higher fidelity.

``select_renderer()`` returns the first available adapter. There is
deliberately no fake fallback: when no renderer is installed, callers get a
clear RenderError so downstream visual analysis can be skipped honestly
instead of pretending slides were rendered.

Rendered PNGs are normalized to a 1920px-wide canvas, matching the parser's
coordinate convention (core/pptx_parser.py).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Protocol, runtime_checkable

logger = logging.getLogger("deckdna.renderer")

TARGET_WIDTH_PX = 1920


class RenderError(Exception):
    """Raised when a deck cannot be rendered to slide images."""


@runtime_checkable
class SlideRenderer(Protocol):
    def render_pptx(self, pptx_path: Path, output_dir: Path) -> List[Path]: ...


def _validate_input(pptx_path: Path) -> Path:
    path = Path(pptx_path)
    if not path.is_file():
        raise RenderError(f"File not found: {path}")
    if path.suffix.lower() != ".pptx":
        raise RenderError(
            f"Unsupported file type '{path.suffix}'; only .pptx can be rendered."
        )
    # Absolute paths are required by PowerPoint COM and safer for soffice.
    return path.resolve()


def _validated_output_dir(output_dir: Path) -> Path:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    return out


class LibreOfficeRenderer:
    """soffice --headless --convert-to pdf, then PDF pages -> PNG via PyMuPDF.

    LibreOffice allows only one conversion process at a time per user
    profile; concurrent renders will fail. The pipeline renders decks
    sequentially, which is safe.
    """

    def __init__(self, soffice_path: Optional[str] = None, timeout_s: int = 300):
        self._soffice_path = soffice_path
        self._timeout_s = timeout_s

    def _soffice(self) -> Optional[str]:
        if self._soffice_path is not None:
            return self._soffice_path if Path(self._soffice_path).is_file() else None
        return shutil.which("soffice")

    def available(self) -> bool:
        return self._soffice() is not None

    def render_pptx(self, pptx_path: Path, output_dir: Path) -> List[Path]:
        source = _validate_input(pptx_path)
        out_dir = _validated_output_dir(output_dir)
        exe = self._soffice()
        if exe is None:
            raise RenderError(
                "LibreOffice (soffice) is not installed or not on PATH; "
                "install LibreOffice or select another renderer."
            )

        with tempfile.TemporaryDirectory(prefix="deckdna_lo_") as tmp:
            tmp_dir = Path(tmp)
            cmd = [
                exe,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_dir),
                str(source),
            ]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=self._timeout_s
                )
            except subprocess.TimeoutExpired as exc:
                raise RenderError(
                    f"LibreOffice timed out after {self._timeout_s}s while "
                    f"converting '{source.name}'."
                ) from exc
            pdf_path = tmp_dir / (source.stem + ".pdf")
            if result.returncode != 0 or not pdf_path.is_file():
                detail = (result.stderr or result.stdout or "").strip()
                raise RenderError(
                    f"LibreOffice failed to convert '{source.name}' to PDF. {detail}"
                )
            return _pdf_to_pngs(pdf_path, out_dir)


def _pdf_to_pngs(pdf_path: Path, out_dir: Path) -> List[Path]:
    """Rasterize every page of a PDF to slide_NNN.png at 1920px wide."""
    import pymupdf

    pages: List[Path] = []
    with pymupdf.open(str(pdf_path)) as doc:
        if doc.page_count == 0:
            raise RenderError(f"'{pdf_path.name}' contains no pages.")
        for index in range(doc.page_count):
            page = doc.load_page(index)
            zoom = TARGET_WIDTH_PX / page.rect.width
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            target = out_dir / f"slide_{index + 1:03d}.png"
            pixmap.save(str(target))
            pages.append(target)
    return pages


class PowerPointComRenderer:
    """Windows-only; renders via desktop PowerPoint COM automation.

    Attaches to an already-running PowerPoint instance when one exists and
    never closes it; otherwise starts a hidden instance and quits it when
    done, so the user's open presentations are never disturbed.
    """

    def available(self) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import comtypes  # noqa: F401
            import winreg
        except ImportError:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "PowerPoint.Application"):
                return True
        except OSError:
            return False

    def render_pptx(self, pptx_path: Path, output_dir: Path) -> List[Path]:
        source = _validate_input(pptx_path)
        out_dir = _validated_output_dir(output_dir)
        if not self.available():
            raise RenderError(
                "PowerPoint COM renderer requires Windows with desktop "
                "Microsoft PowerPoint installed."
            )

        import comtypes.client

        was_running = _powerpoint_process_running()
        app = None
        presentation = None
        try:
            try:
                app = comtypes.client.GetActiveObject(
                    "PowerPoint.Application", dynamic=True
                )
                logger.info("Attached to the running PowerPoint instance.")
            except Exception:
                app = comtypes.client.CreateObject(
                    "PowerPoint.Application", dynamic=True
                )
                logger.info("Started a hidden PowerPoint instance for rendering.")
            if app is None:
                raise RenderError("Could not start PowerPoint automation.")

            try:
                app.DisplayAlerts = 1  # ppAlertsNone
            except Exception:
                logger.debug("Could not suppress PowerPoint alerts.", exc_info=True)

            # Open(FileName, ReadOnly=msoTrue, Untitled=msoFalse, WithWindow=msoFalse)
            presentation = app.Presentations.Open(str(source), -1, 0, 0)
            if presentation is None:
                raise RenderError(f"PowerPoint could not open '{source.name}'.")

            slide_w = float(presentation.PageSetup.SlideWidth)
            slide_h = float(presentation.PageSetup.SlideHeight)
            if slide_w <= 0 or slide_h <= 0:
                raise RenderError("Deck reports an invalid slide size.")
            height_px = round(TARGET_WIDTH_PX * slide_h / slide_w)

            export_dir = out_dir / "_pptx_export"
            export_dir.mkdir(parents=True, exist_ok=True)
            # Export needs an absolute path: PowerPoint's working directory
            # is not ours.
            presentation.Export(
                str(export_dir.resolve()), "PNG", TARGET_WIDTH_PX, height_px
            )

            exported = _numbered_pngs(export_dir)
            if not exported:
                raise RenderError(
                    f"PowerPoint exported no slide images for '{source.name}'."
                )
            pages: List[Path] = []
            for index, path in enumerate(exported, start=1):
                target = out_dir / f"slide_{index:03d}.png"
                shutil.move(str(path), target)
                pages.append(target)
            try:
                export_dir.rmdir()
            except OSError:
                logger.warning(
                    "Temporary export directory is not empty; leaving it in place."
                )
            return pages
        finally:
            if presentation is not None:
                try:
                    presentation.Close()
                except Exception:
                    logger.warning(
                        "Could not close the rendered presentation in PowerPoint.",
                        exc_info=True,
                    )
            if app is not None and not was_running:
                try:
                    app.Quit()
                except Exception:
                    logger.warning(
                        "Could not quit the hidden PowerPoint instance.",
                        exc_info=True,
                    )


def _numbered_pngs(directory: Path) -> List[Path]:
    """List PNG files sorted by the number in their name (e.g. Slide7.PNG).

    Handles localized export names: any PNG whose stem contains digits is
    ordered numerically; unrecognized names trigger a warning and sort last.
    """
    files: List[tuple] = []
    seen = set()
    for pattern in ("*.png", "*.PNG"):
        for path in directory.glob(pattern):
            key = path.name.lower()
            if key in seen:
                continue
            seen.add(key)
            match = re.search(r"(\d+)", path.stem)
            if match:
                files.append((int(match.group(1)), path.name, path))
            else:
                logger.warning("Unrecognized exported image name: %s", path.name)
                files.append((10**9, path.name, path))
    return [path for _, _, path in sorted(files)]


def _powerpoint_process_running() -> bool:
    """True if POWERPNT.EXE is running. Defaults to True (conservative) when
    the check itself fails, so a live user instance is never quit."""
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq POWERPNT.EXE"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return "POWERPNT.EXE" in result.stdout.upper()


def select_renderer() -> SlideRenderer:
    """Return the first available renderer.

    LibreOffice is preferred (the blueprint's cross-platform default);
    desktop PowerPoint is used on Windows when LibreOffice is absent.
    """
    libreoffice = LibreOfficeRenderer()
    if libreoffice.available():
        return libreoffice
    powerpoint = PowerPointComRenderer()
    if powerpoint.available():
        return powerpoint
    raise RenderError(
        "No slide renderer available. Install LibreOffice "
        "(https://www.libreoffice.org) or, on Windows, desktop Microsoft "
        "PowerPoint, then try again."
    )
