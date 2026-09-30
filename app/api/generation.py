"""Generation endpoints (blueprint sections 13.3 and 13.4):

POST /v1/generations/outline  plan a deterministic storyline from a topic,
                              the learned style guide and its templates

The outline is planned synchronously: it is pure computation over the
learned template library, fast enough to answer in the request. The plan is
persisted so a later generation job can be tied to the approved outline.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import config, db
from core.generation import DeckBrief, OutlineGenerator, analyze_material
from core.style_guide import StyleGuide
from core.templates import load_templates

router = APIRouter()

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
