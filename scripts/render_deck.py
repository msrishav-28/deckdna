"""Render every slide of a .pptx deck to PNG images (1920px wide)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.renderer import (
    LibreOfficeRenderer,
    PowerPointComRenderer,
    RenderError,
    select_renderer,
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Render a .pptx file to one PNG image per slide."
    )
    ap.add_argument("--input", required=True, help="Path to a .pptx file")
    ap.add_argument(
        "--output-dir",
        default=None,
        help="Directory for slide_001.png, slide_002.png, ... "
        "(default: output/<deck name>_slides)",
    )
    ap.add_argument(
        "--renderer",
        choices=["auto", "libreoffice", "powerpoint"],
        default="auto",
        help="Rendering engine (default: auto — LibreOffice if installed, "
        "then desktop PowerPoint on Windows)",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        if args.renderer == "libreoffice":
            renderer = LibreOfficeRenderer()
        elif args.renderer == "powerpoint":
            renderer = PowerPointComRenderer()
        else:
            renderer = select_renderer()

        source = Path(args.input)
        out_dir = (
            Path(args.output_dir)
            if args.output_dir
            else Path("output") / f"{source.stem}_slides"
        )
        pages = renderer.render_pptx(source, out_dir)
    except RenderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Rendered {len(pages)} slide image(s) to {out_dir}")
    for page in pages:
        print(f"  {page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
