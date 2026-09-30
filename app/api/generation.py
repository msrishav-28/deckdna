"""Generation endpoints (blueprint sections 13.3 and 13.4):

POST /v1/generations/outline            plan a deterministic storyline from
                                        a topic, the learned style guide and
                                        its templates
POST /v1/generations                    generate a deck from an approved
                                        outline as a background job
GET  /v1/generations/{id}/download/{a}  the finished artifacts (pptx, html,
                                        json, critique)

The outline is planned synchronously: it is pure computation over the
learned template library, fast enough to answer in the request. The plan is
persisted so a later generation job can be tied to the approved outline.
"""

from __future__ import annotations

import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import config, db
from app.generation import ARTIFACT_FILES, run_generation
from app.jobs import ProgressReporter
from core.generation import (
    DeckBrief,
    GenerationPlan,
    OutlineGenerator,
    analyze_material,
)
from core.style_guide import StyleGuide
from core.templates import load_templates

router = APIRouter()

_DOWNLOAD_MEDIA_TYPES = {
    "pptx": (
        "application/vnd.openxmlformats-officedocument"
        ".presentationml.presentation"
    ),
    "html": "text/html",
    "json": "application/json",
    "critique": "application/json",
}

_DOWNLOAD_NAMES = {
    "pptx": "{gen}.pptx",
    "html": "{gen}.html",
    "json": "{gen}.json",
    "critique": "{gen}_critique.json",
}

_MAX_NOTE_CHARS = 500
_MAX_NOTES = 200


class OutlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1, max_length=200)
    style_guide_id: str = Field(min_length=1, max_length=100)
    audience: Optional[str] = Field(default=None, max_length=200)
    goal: Optional[str] = Field(default=None, max_length=200)
    tone: Optional[str] = Field(default=None, max_length=80)
    notes: List[str] = Field(default_factory=list, max_length=_MAX_NOTES)
    slide_count: int = Field(default=5, ge=1, le=50)

    @field_validator("topic", "audience", "goal", "tone")
    @classmethod
    def _clean_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("notes")
    @classmethod
    def _notes_within_budget(cls, notes: List[str]) -> List[str]:
        for i, note in enumerate(notes):
            if len(note) > _MAX_NOTE_CHARS:
                raise ValueError(
                    f"note {i + 1} is longer than {_MAX_NOTE_CHARS} characters"
                )
        return notes


@router.post("/v1/generations/outline")
def create_outline(request: OutlineRequest) -> dict:
    if not config.is_safe_id(request.style_guide_id):
        raise HTTPException(
            status_code=404,
            detail=f"No style guide '{request.style_guide_id}' exists.",
        )
    guide_path = config.STYLE_GUIDES_DIR / f"{request.style_guide_id}.json"
    templates_path = config.STYLE_GUIDES_DIR / f"{request.style_guide_id}_templates.json"
    if not guide_path.is_file() or not templates_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"No style guide '{request.style_guide_id}' has been learned "
                "yet; upload a deck first."
            ),
        )

    style_guide = StyleGuide.model_validate_json(
        guide_path.read_text(encoding="utf-8")
    )
    templates = load_templates(templates_path)

    brief = DeckBrief(
        topic=request.topic,
        audience=request.audience,
        goal=request.goal,
        slide_count=request.slide_count,
        tone=request.tone,
        notes=request.notes,
        style_guide_id=request.style_guide_id,
    )
    material = analyze_material(
        brief.notes, style_guide.content_rules.max_words_per_bullet
    )
    plan = OutlineGenerator().plan(brief, templates, material)

    outline_id = f"outline_{uuid.uuid4().hex[:12]}"
    db.insert_outline(
        outline_id,
        request.style_guide_id,
        request.style_guide_id,
        brief.model_dump(),
        plan.model_dump(),
    )
    return {
        "outline_id": outline_id,
        "style_guide_id": request.style_guide_id,
        "deck_title": plan.deck_title,
        "audience": plan.audience,
        "tone": plan.tone,
        "slide_count": len(plan.slides),
        "slides": [slide.model_dump() for slide in plan.slides],
        "warnings": plan.warnings,
    }


# -- deck generation --------------------------------------------------------


class GenerationRequest(BaseModel):
    """The approval step: generate from a stored outline. output_format is
    the editable PowerPoint export, the blueprint's MVP format."""

    model_config = ConfigDict(extra="forbid")

    style_guide_id: str = Field(min_length=1, max_length=100)
    approved_outline_id: str = Field(min_length=1, max_length=100)
    output_format: Literal["pptx"] = "pptx"
    enable_critique: bool = True


def _load_style_guide(style_guide_id: str) -> tuple:
    try:
        style_guide = StyleGuide.model_validate_json(
            (config.STYLE_GUIDES_DIR / f"{style_guide_id}.json").read_text(
                encoding="utf-8"
            )
        )
        templates = load_templates(
            config.STYLE_GUIDES_DIR / f"{style_guide_id}_templates.json"
        )
    except OSError:
        raise HTTPException(
            status_code=404, detail=f"No style guide '{style_guide_id}' exists."
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"The stored style guide '{style_guide_id}' is unreadable: {exc}"
            ),
        )
    return style_guide, templates


@router.post("/v1/generations", status_code=202)
def create_generation(generation: GenerationRequest, request: Request) -> dict:
    outline = db.get_outline(generation.approved_outline_id)
    if outline is None:
        raise HTTPException(
            status_code=404,
            detail=f"No outline '{generation.approved_outline_id}' exists.",
        )
    if outline["style_guide_id"] != generation.style_guide_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"The approved outline was planned for style guide "
                f"'{outline['style_guide_id']}', not "
                f"'{generation.style_guide_id}'."
            ),
        )
    try:
        brief = DeckBrief.model_validate(outline["brief"])
        plan = GenerationPlan.model_validate(outline["plan"])
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"The stored outline '{generation.approved_outline_id}' is "
                f"unreadable: {exc}"
            ),
        )

    style_guide, templates = _load_style_guide(outline["style_guide_id"])
    generation_id = f"gen_{uuid.uuid4().hex[:12]}"

    def job(job_id: str, report: ProgressReporter) -> dict:
        return run_generation(
            generation_id,
            generation.approved_outline_id,
            brief,
            plan,
            style_guide,
            templates,
            generation.enable_critique,
            report,
        )

    queue = request.app.state.queue
    job_id = queue.create_job("generation")
    try:
        queue.enqueue(job_id, job)
    except Exception as exc:
        db.update_job(job_id, status="failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail="The generation worker could not be started."
        )
    return {"generation_id": generation_id, "status": "queued", "job_id": job_id}


@router.get("/v1/generations/{generation_id}/download/{artifact}")
def download_generation(generation_id: str, artifact: str) -> FileResponse:
    if not config.is_safe_id(generation_id) or artifact not in ARTIFACT_FILES:
        raise HTTPException(
            status_code=404,
            detail=f"No generated deck '{generation_id}' exists.",
        )
    path = config.GENERATED_DIR / generation_id / ARTIFACT_FILES[artifact]
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"No generated deck '{generation_id}' exists.",
        )
    return FileResponse(
        path,
        media_type=_DOWNLOAD_MEDIA_TYPES[artifact],
        filename=_DOWNLOAD_NAMES[artifact].format(gen=generation_id),
    )
