"""Style guide inspection and editing endpoints (blueprint section 13.2):

GET   /v1/style-guides/{id}  the learned style guide plus its edit history
PATCH /v1/style-guides/{id}  user overrides to fonts, palette, spacing and
                             content limits

Overrides are merge-only: they change exactly the values the user named and
leave everything else as learned. Learned provenance fields (where a value
came from) are never rewritten, and every applied patch is appended to an
audit trail, so the guide can always be traced back to its sources. The
guide file itself always holds the current, effective values.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import config, db
from core.style_guide import PROVENANCE_DEFAULT, StyleGuide

router = APIRouter()


# -- override models --------------------------------------------------------
# Bounds mirror what the generation pipeline can honor: content limits match
# the planner's accepted range, sizes stay within physically plausible
# slides, and colors use the same hex format as the style guide itself.


class TextStylePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    font_family: Optional[str] = Field(default=None, min_length=1, max_length=80)
    font_size_pt: Optional[float] = Field(default=None, gt=0, le=200)
    font_weight: Optional[str] = Field(default=None, pattern=r"^(bold|normal)$")
    color_hex: Optional[str] = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("color_hex")
    @classmethod
    def _normalize_hex(cls, value: Optional[str]) -> Optional[str]:
        return value.upper() if value else value


class TypographyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[TextStylePatch] = None
    body: Optional[TextStylePatch] = None


class MarginsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_px: Optional[float] = Field(default=None, ge=0, le=1000)
    right_px: Optional[float] = Field(default=None, ge=0, le=1000)
    bottom_px: Optional[float] = Field(default=None, ge=0, le=1000)
    left_px: Optional[float] = Field(default=None, ge=0, le=1000)


class LayoutPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    margins_px: Optional[MarginsPatch] = None


class ContentRulesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_bullets_per_slide: Optional[int] = Field(default=None, ge=1, le=12)
    max_words_per_bullet: Optional[int] = Field(default=None, ge=1, le=40)
    max_title_length: Optional[int] = Field(default=None, ge=1, le=200)
    preferred_density: Optional[str] = Field(
        default=None, pattern=r"^(low|medium|high)$"
    )
    avoid_repeating_layouts: Optional[bool] = None


class PaletteEntryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    usage: str = Field(min_length=1, max_length=60)

    @field_validator("hex")
    @classmethod
    def _normalize_hex(cls, value: str) -> str:
        return value.upper()


class StyleGuidePatch(BaseModel):
    """A merge-only edit: only the fields present (and not null) are applied."""

    model_config = ConfigDict(extra="forbid")

    palette: Optional[List[PaletteEntryPatch]] = Field(
        default=None, min_length=1, max_length=32
    )
    typography: Optional[TypographyPatch] = None
    layout_grid: Optional[LayoutPatch] = None
    content_rules: Optional[ContentRulesPatch] = None


# -- merge ------------------------------------------------------------------


def _apply_patch(doc: dict, patch: StyleGuidePatch) -> dict:
    """Merge the override values into a copy of the guide document. Learned
    statistics (sample counts, frequencies, provenance) stay untouched; a
    style or palette entry that was never learned is created fresh, marked
    as a default rather than as extracted from the deck."""
    merged = copy.deepcopy(doc)

    if patch.typography is not None:
        typography = merged.setdefault("typography", {})
        for kind, spec_patch in (
            ("title", patch.typography.title),
            ("body", patch.typography.body),
        ):
            if spec_patch is None:
                continue
            fields = spec_patch.model_dump(exclude_none=True)
            if not fields:
                continue
            spec = typography.get(kind)
            if spec is None:
                spec = {"sample_count": 0, "provenance": PROVENANCE_DEFAULT}
                typography[kind] = spec
            spec.update(fields)

    if patch.palette is not None:
        entries = merged.setdefault("palette", [])
        for entry_patch in patch.palette:
            matched = [e for e in entries if e.get("usage") == entry_patch.usage]
            if matched:
                # The renderer picks the most frequent entry per usage, so
                # every entry of that usage moves together or the override
                # could be shadowed.
                for entry in matched:
                    entry["hex"] = entry_patch.hex
            else:
                entries.append(
                    {
                        "hex": entry_patch.hex,
                        "usage": entry_patch.usage,
                        "frequency": 0,
                        "provenance": PROVENANCE_DEFAULT,
                    }
                )

    if patch.layout_grid is not None and patch.layout_grid.margins_px is not None:
        margins = patch.layout_grid.margins_px.model_dump(exclude_none=True)
        if margins:
            grid = merged.setdefault("layout_grid", {})
            grid.setdefault("margins_px", {}).update(margins)

    if patch.content_rules is not None:
        rules = patch.content_rules.model_dump(exclude_none=True)
        if rules:
            merged.setdefault("content_rules", {}).update(rules)

    return merged


def _load_guide(style_guide_id: str) -> Tuple[dict, Path]:
    if not config.is_safe_id(style_guide_id):
        raise HTTPException(
            status_code=404, detail=f"No style guide '{style_guide_id}' exists."
        )
    path = config.STYLE_GUIDES_DIR / f"{style_guide_id}.json"
    if not path.is_file():
        raise HTTPException(
            status_code=404, detail=f"No style guide '{style_guide_id}' exists."
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    guide = StyleGuide.model_validate(doc)  # fail loudly on a corrupt store
    return guide.model_dump(mode="json"), path


# -- endpoints --------------------------------------------------------------


@router.get("/v1/style-guides/{style_guide_id}")
def get_style_guide(style_guide_id: str) -> dict:
    doc, _ = _load_guide(style_guide_id)
    return {
        "style_guide": doc,
        "overrides": db.list_style_guide_overrides(style_guide_id),
    }


@router.patch("/v1/style-guides/{style_guide_id}")
def patch_style_guide(style_guide_id: str, patch: StyleGuidePatch) -> dict:
    doc, path = _load_guide(style_guide_id)
    applied = patch.model_dump(exclude_unset=True, exclude_none=True)
    if not applied:
        raise HTTPException(
            status_code=400, detail="No style guide fields were provided to change."
        )

    merged = _apply_patch(doc, patch)
    guide = StyleGuide.model_validate(merged)  # a merge must never break the schema
    path.write_text(guide.model_dump_json(indent=2), encoding="utf-8")
    db.insert_style_guide_override(style_guide_id, applied)

    return {
        "style_guide": guide.model_dump(mode="json"),
        "overrides": db.list_style_guide_overrides(style_guide_id),
        "applied": applied,
    }
