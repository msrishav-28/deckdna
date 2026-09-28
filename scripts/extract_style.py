"""Learn a deck's style: parse a .pptx, classify its slides, extract a
style guide and a template library. Optional vision enrichment when
rendered slide images and a Gemini API key are available."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.extractor import StyleGuideExtractor
from core.pptx_parser import DeckParseError, DeckParser
from core.slide_classifier import SlideClassifier
from core.templates import build_templates, save_templates
from core.vision import (
    GeminiVisionProvider,
    SlideVisionNotes,
    VisionProviderError,
    load_dotenv,
)


def _sorted_slide_images(slides_dir: Path) -> list:
    def number_in(path: Path):
        digits = re.findall(r"\d+", path.stem)
        return (int(digits[-1]) if digits else 0, path.name.lower())

    return sorted(
        (p for p in slides_dir.iterdir() if p.suffix.lower() == ".png"), key=number_in
    )


def _vision_notes(
    inventory, slides_dir: Path, provider: GeminiVisionProvider
) -> List[SlideVisionNotes]:
    images = _sorted_slide_images(slides_dir)
    if len(images) != inventory.slide_count:
        raise VisionProviderError(
            f"{slides_dir} holds {len(images)} PNG image(s) but the deck has "
            f"{inventory.slide_count} slides; re-render with scripts/render_deck.py."
        )
    return [
        provider.analyze_slide(image, slide_number=index + 1)
        for index, image in enumerate(images)
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="Path to a .pptx file")
    ap.add_argument(
        "--output",
        default=None,
        help="Style guide JSON path (default: style_guides/<deck>.json)",
    )
    ap.add_argument(
        "--templates-output",
        default=None,
        help="Template library JSON path (default: style_guides/<deck>_templates.json)",
    )
    ap.add_argument(
        "--slides-dir",
        default=None,
        help="Directory of rendered slide PNGs (required for --use-vision)",
    )
    ap.add_argument(
        "--use-vision",
        action="store_true",
        help="Enrich the style guide with Gemini vision descriptions",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    source = Path(args.input)
    output = Path(args.output) if args.output else Path("style_guides") / f"{source.stem}.json"
    templates_output = (
        Path(args.templates_output)
        if args.templates_output
        else output.with_name(f"{output.stem}_templates.json")
    )

    try:
        inventory = DeckParser().parse(source)
    except DeckParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    notes = None
    if args.use_vision:
        if not args.slides_dir:
            print(
                "ERROR: --use-vision needs --slides-dir (render first with "
                "scripts/render_deck.py).",
                file=sys.stderr,
            )
            return 1
        load_dotenv()
        provider = GeminiVisionProvider()
        if not provider.is_configured():
            print(
                "ERROR: --use-vision needs a Gemini API key. Create a free key at "
                "https://aistudio.google.com/apikey and put GEMINI_API_KEY=your-key "
                "in a .env file at the repository root.",
                file=sys.stderr,
            )
            return 1
        try:
            notes = _vision_notes(inventory, Path(args.slides_dir), provider)
        except VisionProviderError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    classifications = SlideClassifier().classify_deck(inventory)
    templates = build_templates(inventory, classifications)
    style_guide = StyleGuideExtractor().extract(inventory, vision_notes=notes)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(style_guide.model_dump_json(indent=2), encoding="utf-8")
    save_templates(templates, templates_output)

    palette_summary = ", ".join(
        f"{entry.hex} ({entry.usage})" for entry in style_guide.palette
    )
    print(f"Learned style from {source.name} ({inventory.slide_count} slides).")
    print(f"Palette: {palette_summary or 'none found'}")
    if style_guide.typography.title is not None:
        title = style_guide.typography.title
        print(
            f"Title text: {title.font_family} {title.font_size_pt:g}pt "
            f"{title.font_weight or 'unspecified weight'} {title.color_hex or ''}"
        )
    body = style_guide.typography.body
    if body is not None:
        print(
            f"Body text: {body.font_family} {body.font_size_pt:g}pt "
            f"{body.font_weight or 'unspecified weight'} {body.color_hex or ''}"
        )
    for classification in classifications:
        print(
            f"  slide {classification.slide_number}: {classification.slide_type} "
            f"({classification.confidence}) - {'; '.join(classification.reasons)}"
        )
    print(f"Style guide written to {output}")
    print(f"Template library ({len(templates)} templates) written to {templates_output}")
    if style_guide.warnings:
        print(f"{len(style_guide.warnings)} warning(s) recorded in the style guide.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
