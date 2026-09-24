"""Parse a .pptx deck into a structural inventory (raw_deck.json)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.pptx_parser import DeckParseError, DeckParser


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Parse a .pptx file into a machine-readable structural inventory."
    )
    ap.add_argument("--input", required=True, help="Path to a .pptx file")
    ap.add_argument(
        "--output",
        default="output/raw_deck.json",
        help="Output JSON path (default: output/raw_deck.json)",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        inventory = DeckParser().parse(Path(args.input))
    except DeckParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(inventory.model_dump_json(indent=2), encoding="utf-8")

    total_shapes = sum(len(slide.shapes) for slide in inventory.slides)
    total_warnings = len(inventory.warnings) + sum(
        len(slide.warnings) for slide in inventory.slides
    )
    print(f"Parsed {inventory.slide_count} slides and {total_shapes} shapes from {Path(args.input).name}.")
    if total_warnings:
        print(f"{total_warnings} warning(s) recorded in the output (partial or unsupported extractions).")
    print(f"Inventory written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
