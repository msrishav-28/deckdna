"""Generate a slide deck (outline + content + HTML preview, plus an
editable PowerPoint export with --pptx) from a topic and notes, in the
style learned from an earlier --input deck.

With --critique the generated deck passes through the bounded critique
loop (blueprint §8.5) that audits and repairs layout failures; with
--use-vision the loop also consults the Gemini visual critic on the
rendered slides (needs GEMINI_API_KEY). Planning and writing are
deterministic by default; with --use-llm and a configured Gemini API key
the drafts come from the model but must pass the same strict validation,
falling back to the deterministic pipeline with a recorded warning when
they do not."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.critique import run_critique_loop
from core.generation import DeckBrief, generate_deck
from core.html_renderer import render_deck_html, render_report
from core.pptx_export import export_deck_pptx
from core.renderer import RenderError, select_renderer
from core.style_guide import StyleGuide
from core.templates import load_templates
from core.vision import critic_from_environment, load_dotenv


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "deck"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate a deck from a topic and notes in a learned style."
    )
    ap.add_argument("--topic", required=True, help="What the deck is about")
    ap.add_argument(
        "--style-guide",
        required=True,
        help="Style guide JSON learned by scripts/extract_style.py",
    )
    ap.add_argument(
        "--templates",
        default=None,
        help="Template library JSON (default: <style guide stem>_templates.json)",
    )
    ap.add_argument("--audience", default=None, help="Who the deck is for")
    ap.add_argument("--goal", default=None, help="What the deck should achieve")
    ap.add_argument("--tone", default=None, help="Desired tone, e.g. 'confident'")
    ap.add_argument(
        "--slides", type=int, default=5, help="Number of slides (default: 5)"
    )
    ap.add_argument(
        "--notes",
        action="append",
        default=[],
        help="A fact, number, quote or point to use; repeat for more",
    )
    ap.add_argument(
        "--output-dir",
        default=None,
        help="Directory for the HTML preview and content JSON "
        "(default: output/generated)",
    )
    ap.add_argument(
        "--name",
        default=None,
        help="File name stem (default: slug of the topic)",
    )
    ap.add_argument(
        "--use-llm",
        action="store_true",
        help="Draft the outline and content with Gemini (needs GEMINI_API_KEY)",
    )
    ap.add_argument(
        "--critique",
        action="store_true",
        help="Audit and repair layout failures with the bounded critique "
        "loop; writes <name>_critique.json with before/after evidence",
    )
    ap.add_argument(
        "--use-vision",
        action="store_true",
        help="Also consult the Gemini visual critic inside the critique loop "
        "(needs --critique and GEMINI_API_KEY; slide images are sent to "
        "Google)",
    )
    ap.add_argument(
        "--pptx",
        action="store_true",
        help="Also export an editable PowerPoint file (native text and shapes)",
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.use_vision and not args.critique:
        print(
            "ERROR: --use-vision needs --critique (the visual critic runs "
            "inside the critique loop).",
            file=sys.stderr,
        )
        return 1

    style_guide_path = Path(args.style_guide)
    templates_path = (
        Path(args.templates)
        if args.templates
        else style_guide_path.with_name(f"{style_guide_path.stem}_templates.json")
    )
    try:
        style_guide = StyleGuide.model_validate_json(
            style_guide_path.read_text(encoding="utf-8")
        )
    except OSError as exc:
        print(f"ERROR: cannot read style guide: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ERROR: {style_guide_path} is not a valid style guide: {exc}", file=sys.stderr)
        return 1
    try:
        templates = load_templates(templates_path)
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot load templates: {exc}", file=sys.stderr)
        return 1

    if args.use_llm or args.use_vision:
        load_dotenv()

    provider = None
    if args.use_llm:
        from core.llm import text_provider_from_environment

        provider = text_provider_from_environment()
        if provider is None:
            print(
                "ERROR: --use-llm needs a Gemini API key. Create a free key at "
                "https://aistudio.google.com/apikey and put GEMINI_API_KEY=your-key "
                "in a .env file at the repository root.",
                file=sys.stderr,
            )
            return 1

    critic = None
    if args.use_vision:
        critic = critic_from_environment()
        if critic is None:
            print(
                "ERROR: --use-vision needs a Gemini API key. Create a free key at "
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
            "ERROR: no slides could be planned from these templates, so there "
            "is no deck to write.",
            file=sys.stderr,
        )
        for warning in deck.warnings:
            print(f"  {warning}", file=sys.stderr)
        print(
            "Generating a deck needs typed text in the source: slide images "
            "(.png) and PDFs whose pages are images carry none, so every "
            "template learned from them holds only a picture placeholder. "
            "Learn the style from a .pptx, or from a PDF with a real text "
            "layer, instead.",
            file=sys.stderr,
        )
        return 1

    out_dir = Path(args.output_dir) if args.output_dir else Path("output") / "generated"
    name = args.name or _slug(deck.plan.deck_title) or _slug(args.topic)
    out_dir.mkdir(parents=True, exist_ok=True)

    critique_report = None
    critique_renders = None
    if args.critique:
        images = None
        if critic is not None:
            try:
                renderer = select_renderer()
            except RenderError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            critique_renders = out_dir / f"{name}_critique" / "before"
            try:
                with tempfile.TemporaryDirectory(prefix="deckdna_critic_") as tmp:
                    source_pptx = export_deck_pptx(
                        deck, style_guide, templates, Path(tmp) / f"{name}.pptx"
                    )
                    pages = renderer.render_pptx(source_pptx, critique_renders)
            except (RenderError, OSError) as exc:
                print(
                    f"ERROR: cannot render the generated deck for the visual "
                    f"critic: {exc}",
                    file=sys.stderr,
                )
                return 1
            if len(pages) != len(deck.slides):
                print(
                    f"ERROR: rendered {len(pages)} slide image(s) but the deck "
                    f"has {len(deck.slides)} slides; refusing to guess the "
                    "mapping.",
                    file=sys.stderr,
                )
                return 1
            images = {
                slide.slide_id: page for slide, page in zip(deck.slides, pages)
            }
        deck, critique_report = run_critique_loop(
            deck, style_guide, templates, vision_provider=critic, images=images
        )

    report = render_report(deck, style_guide, templates)
    html_path = out_dir / f"{name}.html"
    json_path = out_dir / f"{name}_content.json"
    html_path.write_text(render_deck_html(deck, style_guide, templates), encoding="utf-8")
    json_path.write_text(deck.model_dump_json(indent=2), encoding="utf-8")
    pptx_path = None
    if args.pptx:
        pptx_path = export_deck_pptx(
            deck, style_guide, templates, out_dir / f"{name}.pptx"
        )

    print(f"Deck: {deck.plan.deck_title}")
    if deck.brief.audience:
        print(f"Audience: {deck.brief.audience}")
    print(f"Generator: {deck.generator}")
    for slide in deck.slides:
        title = slide.title or "(no title)"
        print(
            f"  slide {slide.slide_number:02d}: {slide.slide_type} "
            f"[{slide.template_id}] {title}"
        )
    warnings = deck.warnings + report
    if warnings:
        print(f"{len(warnings)} warning(s):")
        for warning in warnings:
            print(f"  {warning}")
    else:
        print("No warnings: every slide fits its template.")
    print(f"HTML preview written to {html_path}")
    print(f"Content JSON written to {json_path}")
    if pptx_path is not None:
        print(f"Editable PPTX written to {pptx_path}")
    if critique_report is not None:
        print(
            f"Critique: score {critique_report.score_before} -> "
            f"{critique_report.score_after} ({critique_report.stop_reason}, "
            f"threshold {critique_report.threshold})"
        )
        for iteration in critique_report.iterations:
            print(
                f"  iteration {iteration.iteration}: "
                f"{len(iteration.applied)} fix(es) applied, "
                f"{len(iteration.rejected)} rejected"
            )
            for applied in iteration.applied:
                print(
                    f"    [{applied.source}] {applied.slide_id}/"
                    f"{applied.element_id} {applied.action}: {applied.detail}"
                )
            for rejected in iteration.rejected:
                print(f"    rejected: {rejected}")
        for note in critique_report.vision_notes:
            print(f"  vision: {note}")
        if critique_renders is not None:
            print(f"Pre-critique renders (what the vision critic saw) in {critique_renders}")
        if critique_report.unresolved:
            print(
                f"{len(critique_report.unresolved)} unresolved high/medium "
                "finding(s) recorded in the report."
            )
        if critique_report.needs_manual_review:
            print("Flagged for manual review (final score below the threshold).")
        critique_path = out_dir / f"{name}_critique.json"
        critique_path.write_text(
            critique_report.model_dump_json(indent=2), encoding="utf-8"
        )
        print(f"Critique report written to {critique_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
