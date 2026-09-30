"""The deck-extraction job: the same pipeline as scripts/extract_style.py
(parse, classify, build templates, extract the style guide), minus the
optional vision pass. Writes style_guides/<deck_id>.json and
style_guides/<deck_id>_templates.json."""

from __future__ import annotations

from pathlib import Path

from core.extractor import StyleGuideExtractor
from core.pptx_parser import DeckParser
from core.slide_classifier import SlideClassifier
from core.templates import build_templates, save_templates

from app import config, db
from app.jobs import JobQueue, ProgressReporter


def run_extraction(deck_id: str, source_path: Path, report: ProgressReporter) -> dict:
    report("parsing", 10)
    inventory = DeckParser().parse(source_path)
    report("classifying", 40)
    classifications = SlideClassifier().classify_deck(inventory)
    report("building_templates", 65)
    templates = build_templates(inventory, classifications)
    report("extracting_style", 85)
    style_guide = StyleGuideExtractor().extract(inventory, vision_notes=None)

    config.STYLE_GUIDES_DIR.mkdir(parents=True, exist_ok=True)
    (config.STYLE_GUIDES_DIR / f"{deck_id}.json").write_text(
        style_guide.model_dump_json(indent=2), encoding="utf-8"
    )
    save_templates(templates, config.STYLE_GUIDES_DIR / f"{deck_id}_templates.json")

    return {
        "slide_count": inventory.slide_count,
        "style_guide_id": deck_id,
        "template_count": len(templates),
    }


def submit_extraction(
    queue: JobQueue, deck_id: str, source_path: Path, job_id: str
) -> None:
    """Queue an extraction job and keep the deck row in step with it."""

    def job(job_id: str, report: ProgressReporter) -> dict:
        db.update_deck_status(deck_id, "running")
        try:
            result = run_extraction(deck_id, source_path, report)
        except Exception as exc:
            db.fail_deck(deck_id, str(exc))
            raise
        db.complete_deck(
            deck_id,
            result["slide_count"],
            result["style_guide_id"],
            result["template_count"],
        )
        return result

    queue.enqueue(job_id, job)
