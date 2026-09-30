"""The browser pages (blueprint Milestone 7).

Upload -> extraction progress -> inspect the style guide -> enter a topic
-> approve the outline -> generate -> preview -> download.

The pages are server-rendered with small vanilla-JS polling; all real work
happens through the JSON API, which stays the single source of truth. No
page holds state of its own beyond what the store already knows, so a
reload (or a server restart) never shows stale progress.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app import config, db
from core.generation import DeckBrief, GenerationPlan
from core.style_guide import StyleGuide
from core.templates import load_templates

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Friendly labels for a non-technical reader; the raw stage name is shown
# next to the label so the blueprint's stage contract stays visible.
EXTRACTION_STAGE_LABELS = {
    "queued": "Queued",
    "parsing": "Reading the slides",
    "classifying": "Classifying slide types",
    "building_templates": "Building the template library",
    "extracting_style": "Learning the style guide",
}

GENERATION_STAGE_LABELS = {
    "queued": "Queued",
    "generating_content": "Writing the slide content",
    "rendering_preview": "Rendering the HTML preview",
    "exporting_pptx": "Building the PowerPoint file",
    "finalizing": "Finishing up",
}

CRITIQUE_STOP_REASONS = {
    "threshold_met": "quality threshold met",
    "no_fixes_applied": "no further automatic repair available",
    "max_iterations_reached": "iteration budget reached",
}


def _page(
    request: Request, name: str, context: dict, status_code: int = 200
):
    return templates.TemplateResponse(
        request, name, context, status_code=status_code
    )


def _not_found(request: Request, message: str):
    return _page(request, "error.html", {"message": message}, status_code=404)


def _palette_groups(doc: dict) -> list:
    """One editable row per usage: the renderer picks the most frequent
    entry of a usage, so the group shows that entry's color and the total
    count, and a save moves every entry of the usage together."""
    grouped: dict = {}
    order: list = []
    for entry in doc.get("palette", []):
        usage = entry["usage"]
        if usage not in grouped:
            grouped[usage] = []
            order.append(usage)
        grouped[usage].append(entry)
    groups = []
    for usage in order:
        entries = grouped[usage]
        best = max(entries, key=lambda e: e.get("frequency", 0))
        groups.append(
            {"usage": usage, "hex": best["hex"], "count": len(entries)}
        )
    return groups


@router.get("/")
def home(request: Request):
    return _page(request, "index.html", {"decks": db.list_decks()})


@router.get("/decks/{deck_id}")
def deck_progress(request: Request, deck_id: str):
    if not config.is_safe_id(deck_id):
        return _not_found(request, f"No deck '{deck_id}' was uploaded.")
    deck = db.get_deck(deck_id)
    if deck is None:
        return _not_found(request, f"No deck '{deck_id}' was uploaded.")
    job = db.get_job(deck["job_id"]) if deck["job_id"] else None
    return _page(
        request,
        "deck_progress.html",
        {"deck": deck, "job": job, "stage_labels": EXTRACTION_STAGE_LABELS},
    )


@router.get("/style-guides/{style_guide_id}")
def style_guide_page(request: Request, style_guide_id: str):
    if not config.is_safe_id(style_guide_id):
        return _not_found(
            request, f"No style guide '{style_guide_id}' exists."
        )
    guide_path = config.STYLE_GUIDES_DIR / f"{style_guide_id}.json"
    templates_path = config.STYLE_GUIDES_DIR / f"{style_guide_id}_templates.json"
    if not guide_path.is_file() or not templates_path.is_file():
        return _not_found(
            request,
            f"No style guide '{style_guide_id}' has been learned yet; "
            "upload a deck first.",
        )
    try:
        guide = StyleGuide.model_validate_json(
            guide_path.read_text(encoding="utf-8")
        )
        records = load_templates(templates_path)
    except (OSError, ValueError) as exc:
        return _page(
            request,
            "error.html",
            {"message": f"The stored style guide is unreadable: {exc}"},
            status_code=500,
        )
    deck = db.get_deck(style_guide_id)
    return _page(
        request,
        "style_guide.html",
        {
            "style_guide_id": style_guide_id,
            "guide": guide.model_dump(mode="json"),
            "palette_groups": _palette_groups(guide.model_dump(mode="json")),
            "templates": records,
            "overrides": db.list_style_guide_overrides(style_guide_id),
            "source_name": deck["source_name"] if deck else None,
        },
    )


@router.get("/outlines/{outline_id}")
def outline_page(request: Request, outline_id: str):
    if not config.is_safe_id(outline_id):
        return _not_found(request, f"No outline '{outline_id}' exists.")
    outline = db.get_outline(outline_id)
    if outline is None:
        return _not_found(request, f"No outline '{outline_id}' exists.")
    try:
        brief = DeckBrief.model_validate(outline["brief"])
        plan = GenerationPlan.model_validate(outline["plan"])
    except ValueError as exc:
        return _page(
            request,
            "error.html",
            {"message": f"The stored outline is unreadable: {exc}"},
            status_code=500,
        )
    return _page(
        request,
        "outline.html",
        {"outline": outline, "brief": brief, "plan": plan},
    )


@router.get("/generations/{generation_id}")
def generation_page(
    request: Request, generation_id: str, job: Optional[str] = None
):
    if not config.is_safe_id(generation_id):
        return _not_found(request, f"No generation '{generation_id}' exists.")
    job_row = None
    if job is not None:
        if not config.is_safe_id(job):
            return _not_found(request, f"No job '{job}' exists.")
        job_row = db.get_job(job)
        if job_row is None:
            return _not_found(request, f"No job '{job}' exists.")
    return _page(
        request,
        "generation.html",
        {
            "generation_id": generation_id,
            "job": job_row,
            "stage_labels": GENERATION_STAGE_LABELS,
            "stop_reasons": CRITIQUE_STOP_REASONS,
        },
    )
