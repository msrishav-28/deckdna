"""Run the entire DeckDNA flow on one source with a single command -
the blueprint's end-to-end demo (§19 Task 3, §20 definition of success).

Parse the source (.pptx, .pdf, .png, or a folder of .png slide images),
learn its style guide and template library, plan and generate a deck on a
new topic, optionally run the bounded critique loop, then export an
editable .pptx plus an HTML preview. Every intermediate artifact is saved
under <output stem>_artifacts/ and an evidence panel (template chosen per
slide, style tokens used, critique improvements) is printed. The output
type is reported explicitly."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.critique import run_critique_loop
from core.extractor import StyleGuideExtractor
from core.generation import DeckBrief, generate_deck
from core.html_renderer import render_deck_html, render_report
from core.pptx_export import export_deck_pptx
from core.pptx_parser import DeckParseError
from core.slide_classifier import SlideClassifier
from core.source_parser import parse_source
from core.templates import build_templates, save_templates


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--input",
        required=True,
        help="Path to a .pptx, .pdf, or .png file, or a folder of .png slide images",
    )
    ap.add_argument("--topic", required=True, help="What the new deck is about")
    ap.add_argument(
        "--output",
        default="output/demo.pptx",
        help="Editable PowerPoint output path (default: output/demo.pptx)",
    )
    ap.add_argument("--audience", default=None, help="Who the deck is for")
    ap.add_argument("--goal", default=None, help="What the deck should achieve")
    ap.add_argument("--tone", default=None, help="Desired tone, e.g. 'confident'")
    ap.add_argument(
        "--slides",
        type=int,
        default=8,
        help="Number of slides to plan, 6-10 per the blueprint (default: 8)",
    )
    ap.add_argument(
        "--notes",
        action="append",
        default=[],
        help="A fact, number, quote or point to use; repeat for more",
    )
    ap.add_argument(
        "--critique",
        action="store_true",
        help="Audit and repair the generated layout with the bounded critique loop",
    )
    ap.add_argument(
        "--use-llm",
        action="store_true",
        help="Draft the outline and content with Gemini (needs GEMINI_API_KEY)",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    source = Path(args.input)
    output = Path(args.output)
    artifacts = output.parent / f"{output.stem}_artifacts"

    # 1. Parse the source into a structured inventory.
    try:
        inventory = parse_source(source)
    except DeckParseError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # 2. Learn the style: classify slides, build templates, extract the guide.
    classifications = SlideClassifier().classify_deck(inventory)
    templates = build_templates(inventory, classifications)
    style_guide = StyleGuideExtractor().extract(inventory)

    artifacts.mkdir(parents=True, exist_ok=True)
    raw_path = artifacts / "raw_deck.json"
    guide_path = artifacts / "style_guide.json"
    templates_path = artifacts / "templates.json"
    raw_path.write_text(inventory.model_dump_json(indent=2), encoding="utf-8")
    guide_path.write_text(style_guide.model_dump_json(indent=2), encoding="utf-8")
    save_templates(templates, templates_path)

    # 3. Plan and generate the new deck deterministically.
    provider = None
    if args.use_llm:
        from core.llm import text_provider_from_environment
        from core.vision import load_dotenv

        load_dotenv()
        provider = text_provider_from_environment()
        if provider is None:
            print(
                "ERROR: --use-llm needs a Gemini API key. Create a free key at "
                "https://aistudio.google.com/apikey and put GEMINI_API_KEY=your-key "
                "in a .env file at the repository root.",
                file=sys.stderr,
            )
            return 1

    try:
        brief = DeckBrief(
            topic=args.topic,
            audience=args.audience,
            goal=args.goal,
            slide_count=args.slides,
            tone=args.tone,
            notes=args.notes,
            style_guide_id=style_guide.deck_id,
        )
    except ValueError as exc:
        print(f"ERROR: invalid deck brief: {exc}", file=sys.stderr)
        return 1

    deck = generate_deck(brief, templates, style_guide, text_provider=provider)
    if not deck.slides:
        print(
            "ERROR: no slides could be planned from this source, so there is "
            "no deck to export.",
            file=sys.stderr,
        )
        for warning in deck.warnings:
            print(f"  {warning}", file=sys.stderr)
        print(
            f"The learned style guide and template library are still in "
            f"{artifacts}. Generating a deck needs typed text in the source: "
            "slide images (.png) and PDFs whose pages are images carry none, "
            "so every template learned from them holds only a picture "
            "placeholder. Learn the style from a .pptx, or from a PDF with a "
            "real text layer, to generate a deck.",
            file=sys.stderr,
        )
        return 1

    # 4. Bounded critique loop (optional, deterministic auditor only).
    critique_report = None
    if args.critique:
        deck, critique_report = run_critique_loop(deck, style_guide, templates)

    # 5. Export every artifact, including the editable PowerPoint.
    report = render_report(deck, style_guide, templates)
    html_path = output.with_suffix(".html")
    json_path = artifacts / "content.json"
    critique_path = artifacts / "critique.json"
    html_path.write_text(render_deck_html(deck, style_guide, templates), encoding="utf-8")
    json_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    if critique_report is not None:
        critique_path.write_text(
            critique_report.model_dump_json(indent=2), encoding="utf-8"
        )
    try:
        export_deck_pptx(deck, style_guide, templates, output)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot export {output}: {exc}", file=sys.stderr)
        return 1

    # Evidence panel (blueprint §20 item 7).
    print(f"Source: {source.name} ({inventory.slide_count} slide(s))")
    palette = ", ".join(
        f"{entry.hex} {entry.usage} [{entry.provenance}]" for entry in style_guide.palette
    )
    print(f"Style tokens - palette: {palette or 'none found'}")
    title_font = style_guide.typography.title
    if title_font is not None:
        print(
            f"Style tokens - title text: {title_font.font_family} "
            f"{title_font.font_size_pt:g}pt {title_font.font_weight or ''} "
            f"{title_font.color_hex or ''}"
        )
    else:
        print("Style tokens - title text: unknown (not declared by this source type)")
    print(f"Deck: {deck.plan.deck_title} ({len(deck.slides)} slides)")
    for slide in deck.slides:
        print(
            f"  slide {slide.slide_number:02d}: {slide.slide_type} "
            f"[{slide.template_id}] {slide.title or '(no title)'}"
        )
    if critique_report is not None:
        print(
            f"Critique: score {critique_report.score_before} -> "
            f"{critique_report.score_after} ({critique_report.stop_reason})"
        )
        for iteration in critique_report.iterations:
            for applied in iteration.applied:
                print(
                    f"  fixed: {applied.slide_id}/{applied.element_id} "
                    f"{applied.action} ({applied.detail})"
                )
        if critique_report.needs_manual_review:
            print("Flagged for manual review (final score below the threshold).")
    warnings = deck.warnings + report + style_guide.warnings
    if warnings:
        print(f"{len(warnings)} warning(s):")
        for warning in warnings:
            print(f"  {warning}")
    print(f"Output type: editable .pptx (native text boxes and shapes) at {output}")
    print(f"Output type: HTML preview at {html_path}")
    print(f"Intermediate artifacts in {artifacts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
