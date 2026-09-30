"""The deck-generation job: fill the approved outline with content from the
brief's own material, run the bounded critique loop when asked, and write
the downloadable artifacts under output/generated/<generation_id>/.

The approved outline is honored exactly: the plan stored by the outline
endpoint is filled as-is, never re-planned. Style-guide edits made after
approval still apply to capacity and appearance, because the content
generator and the renderers read the guide at generation time.
"""

from __future__ import annotations

from typing import Sequence

from core.critique import run_critique_loop
from core.generation import (
    ContentGenerator,
    DeckBrief,
    GeneratedDeck,
    GenerationPlan,
    analyze_material,
)
from core.html_renderer import render_deck_html, render_report
from core.pptx_export import export_deck_pptx
from core.style_guide import StyleGuide
from core.templates import TemplateRecord

from app import config
from app.jobs import ProgressReporter

ARTIFACT_FILES = {
    "pptx": "deck.pptx",
    "html": "preview.html",
    "json": "content.json",
    "critique": "critique.json",
}


def build_deck(
    brief: DeckBrief,
    plan: GenerationPlan,
    style_guide: StyleGuide,
    templates: Sequence[TemplateRecord],
) -> GeneratedDeck:
    """Fill an approved plan with content, deterministically."""
    material = analyze_material(
        brief.notes, style_guide.content_rules.max_words_per_bullet
    )
    slides, content_warnings = ContentGenerator().generate(
        brief, plan, templates, material, style_guide
    )
    return GeneratedDeck(
        brief=brief,
        plan=plan,
        slides=slides,
        # the client-facing id is the web deck id the brief was planned
        # with, not the extractor's internal source-file-derived id
        style_guide_id=brief.style_guide_id,
        generator="deterministic",
        # the plan object already carries its own planning warnings
        warnings=list(plan.warnings) + content_warnings,
    )


def run_generation(
    generation_id: str,
    outline_id: str,
    brief: DeckBrief,
    plan: GenerationPlan,
    style_guide: StyleGuide,
    templates: Sequence[TemplateRecord],
    enable_critique: bool,
    report: ProgressReporter,
) -> dict:
    report("generating_content", 20)
    deck = build_deck(brief, plan, style_guide, templates)

    critique_summary = {"requested": enable_critique}
    critique_report = None
    if enable_critique:
        def on_iteration(number: int) -> None:
            report(f"critic_iteration_{number}", min(30 + 10 * number, 55))

        deck, critique_report = run_critique_loop(
            deck, style_guide, templates, on_iteration=on_iteration
        )
        critique_summary = {
            "requested": True,
            "score_before": critique_report.score_before,
            "score_after": critique_report.score_after,
            "threshold": critique_report.threshold,
            "stop_reason": critique_report.stop_reason,
            "iterations": len(critique_report.iterations),
            "fixes_applied": sum(
                len(iteration.applied)
                for iteration in critique_report.iterations
            ),
            "unresolved": len(critique_report.unresolved),
            "needs_manual_review": critique_report.needs_manual_review,
        }

    report("rendering_preview", 70)
    out_dir = config.GENERATED_DIR / generation_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / ARTIFACT_FILES["html"]).write_text(
        render_deck_html(deck, style_guide, templates), encoding="utf-8"
    )
    (out_dir / ARTIFACT_FILES["json"]).write_text(
        deck.model_dump_json(indent=2), encoding="utf-8"
    )
    if critique_report is not None:
        (out_dir / ARTIFACT_FILES["critique"]).write_text(
            critique_report.model_dump_json(indent=2), encoding="utf-8"
        )

    report("exporting_pptx", 88)
    export_deck_pptx(deck, style_guide, templates, out_dir / ARTIFACT_FILES["pptx"])

    report("finalizing", 95)
    return {
        "generation_id": generation_id,
        "outline_id": outline_id,
        "style_guide_id": brief.style_guide_id,
        "output_format": "pptx",
        "deck_title": deck.plan.deck_title,
        "slide_count": len(deck.slides),
        "generator": deck.generator,
        "warnings": deck.warnings + render_report(deck, style_guide, templates),
        "critique": critique_summary,
        "artifacts": {
            kind: name
            for kind, name in ARTIFACT_FILES.items()
            if (out_dir / name).is_file()
        },
        "output_url": f"/v1/generations/{generation_id}/download/pptx",
    }
