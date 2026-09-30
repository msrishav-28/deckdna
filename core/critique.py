"""Deterministic critique loop (blueprint §8.5, Milestone 6).

The loop audits the planned layout of every slide the way the renderers
will draw it (overflow, overlap, tiny text), plans a small bounded set of
repairs, and applies them as recorded SlideFix entries and traceable
bullet removals — content facts are never rewritten. An optional vision
critic may add suggestions from the initial render; every suggestion is
validated against the same bounds before it is applied and rejections are
reported. The loop stops early at the quality threshold, when no further
repair is possible, or after the iteration budget, and flags the deck for
manual review when the final score stays below the threshold.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Literal, Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

from pydantic import BaseModel, Field

from core.generation import (
    BulletBlock,
    GeneratedDeck,
    PT_TO_PX,
    SlideFix,
    available_lines,
    estimate_lines,
    LINE_HEIGHT_FACTOR,
)
from core.layout import RenderBox, canvas_size, plan_slide
from core.style_guide import StyleGuide
from core.templates import TemplateRecord, TemplateSlot

FixAction = Literal[
    "reduce_font_size", "resize_element", "remove_low_priority_bullet"
]

CRITIQUE_THRESHOLD = 85
MAX_CRITIQUE_ITERATIONS = 2
MIN_FONT_PT = 12.0
MAX_BULLET_REMOVALS_PER_SLIDE = 2
EDGE_MARGIN_FRACTION = 0.02
NEIGHBOR_GAP_FRACTION = 0.02
RESIZE_MIN_GAIN_FRACTION = 0.02
OVERLAP_HIGH_RATIO = 0.25
OVERLAP_MEDIUM_RATIO = 0.08
SEVERITY_PENALTY = {"high": 30, "medium": 12, "low": 4}


@runtime_checkable
class SlideCritic(Protocol):
    """A vision critic that scores one rendered slide and may suggest
    bounded repairs (see core.vision)."""

    name: str

    def critique_slide(
        self, generated: Path, reference: Optional[Path], context: dict
    ) -> dict: ...


# -- schemas ----------------------------------------------------------------


class Finding(BaseModel):
    slide_id: str
    code: Literal["overflow", "overlap", "tiny_font"]
    severity: Literal["high", "medium", "low"]
    detail: str
    element_id: Optional[str] = None


class SlideState(BaseModel):
    slide_id: str
    score: int
    findings: List[Finding] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class DeckAudit(BaseModel):
    """A whole-deck snapshot. The deck score is the worst slide's score,
    so one broken slide cannot be averaged away."""

    score: int
    slides: List[SlideState] = Field(default_factory=list)


class FixDecision(BaseModel):
    slide_id: str
    element_id: str
    action: FixAction
    font_scale: float = 1.0
    dy: float = 0.0
    dh: float = 0.0
    detail: str = ""
    source: Literal["deterministic", "vision"] = "deterministic"
    removed_text: Optional[str] = None


class AppliedFix(BaseModel):
    slide_id: str
    element_id: str
    action: str
    source: str
    detail: str
    removed_text: Optional[str] = None


class CritiqueIteration(BaseModel):
    iteration: int
    score_before: int
    score_after: int
    applied: List[AppliedFix] = Field(default_factory=list)
    rejected: List[str] = Field(default_factory=list)


class CritiqueReport(BaseModel):
    deck_title: str
    threshold: int
    max_iterations: int
    stop_reason: Literal[
        "threshold_met", "no_fixes_applied", "max_iterations_reached"
    ]
    score_before: int
    score_after: int
    iterations: List[CritiqueIteration] = Field(default_factory=list)
    before: List[SlideState] = Field(default_factory=list)
    after: List[SlideState] = Field(default_factory=list)
    unresolved: List[Finding] = Field(default_factory=list)
    needs_manual_review: bool = False
    vision_used: bool = False
    vision_notes: List[str] = Field(default_factory=list)


# -- deterministic audit ----------------------------------------------------


def _capacity_slot(
    box: RenderBox, font_pt: float, canvas_w: float
) -> TemplateSlot:
    """The slot the estimators should see for this box rendered at
    font_pt: bullets slots are narrowed by their marker column."""
    slot = box.slot.model_copy(update={"font_size_pt": font_pt})
    if box.kind == "bullets":
        marker_fraction = (1.05 * font_pt * PT_TO_PX) / canvas_w
        slot = slot.model_copy(
            update={"width": max(slot.width - marker_fraction, 0.01)}
        )
    return slot


def _overflow_stats(
    box: RenderBox, canvas_w: float, canvas_h: float
) -> Optional[Tuple[int, int]]:
    if box.capacity_text is None or box.font_pt is None:
        return None
    slot = _capacity_slot(box, box.font_pt, canvas_w)
    needed = estimate_lines(box.capacity_text, slot, canvas_w, canvas_h)
    capacity = available_lines(slot, canvas_h)
    if needed is not None and capacity is not None and needed > capacity:
        return needed, capacity
    return None


def _fits(box: RenderBox, font_pt: float, canvas_w: float, canvas_h: float) -> bool:
    if box.capacity_text is None:
        return True
    slot = _capacity_slot(box, font_pt, canvas_w)
    needed = estimate_lines(box.capacity_text, slot, canvas_w, canvas_h)
    capacity = available_lines(slot, canvas_h)
    return needed is not None and capacity is not None and needed <= capacity


def _x_overlap(a: TemplateSlot, b: TemplateSlot) -> float:
    return min(a.x + a.width, b.x + b.width) - max(a.x, b.x)


def _overlap_ratio(a: TemplateSlot, b: TemplateSlot) -> float:
    x_overlap = _x_overlap(a, b)
    y_overlap = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    if x_overlap <= 0 or y_overlap <= 0:
        return 0.0
    smaller = min(a.width * a.height, b.width * b.height)
    if smaller <= 0:
        return 0.0
    return (x_overlap * y_overlap) / smaller


def _score(findings: Sequence[Finding]) -> int:
    penalty = sum(SEVERITY_PENALTY[finding.severity] for finding in findings)
    return max(0, 100 - penalty)


def audit_slide(
    slide,
    templates_by_id: Mapping[str, TemplateRecord],
    style_guide: StyleGuide,
    canvas: Tuple[float, float],
) -> SlideState:
    canvas_w, canvas_h = canvas
    boxes, warnings = plan_slide(
        slide, templates_by_id.get(slide.template_id), style_guide
    )
    findings: List[Finding] = []
    for box in boxes:
        stats = _overflow_stats(box, canvas_w, canvas_h)
        if stats is not None:
            needed, capacity = stats
            findings.append(
                Finding(
                    slide_id=slide.slide_id,
                    element_id=box.element_id,
                    code="overflow",
                    severity="high",
                    detail=(
                        f"text needs about {needed} lines but the slot "
                        f"fits {capacity}"
                    ),
                )
            )
        if box.font_pt is not None and box.font_pt < MIN_FONT_PT:
            findings.append(
                Finding(
                    slide_id=slide.slide_id,
                    element_id=box.element_id,
                    code="tiny_font",
                    severity="low",
                    detail=(
                        f"font {box.font_pt:.1f}pt is below the "
                        f"{MIN_FONT_PT:.0f}pt floor"
                    ),
                )
            )
    text_boxes = [box for box in boxes if box.kind != "shape"]
    for i, first in enumerate(text_boxes):
        for second in text_boxes[i + 1:]:
            ratio = _overlap_ratio(first.slot, second.slot)
            if ratio >= OVERLAP_HIGH_RATIO:
                severity = "high"
            elif ratio >= OVERLAP_MEDIUM_RATIO:
                severity = "medium"
            else:
                continue
            findings.append(
                Finding(
                    slide_id=slide.slide_id,
                    element_id=first.element_id,
                    code="overlap",
                    severity=severity,
                    detail=(
                        f"overlaps '{second.element_id}' on "
                        f"{ratio:.0%} of the smaller box"
                    ),
                )
            )
    return SlideState(
        slide_id=slide.slide_id,
        score=_score(findings),
        findings=findings,
        warnings=warnings,
    )


def audit_deck(
    deck: GeneratedDeck,
    style_guide: StyleGuide,
    templates: Sequence[TemplateRecord],
) -> DeckAudit:
    canvas = canvas_size(style_guide)
    by_id = {record.template_id: record for record in templates}
    states = [
        audit_slide(slide, by_id, style_guide, canvas) for slide in deck.slides
    ]
    score = min((state.score for state in states), default=100)
    return DeckAudit(score=score, slides=states)


# -- fix planning -----------------------------------------------------------


def _font_candidates(font: float) -> List[float]:
    candidates: List[float] = []
    candidate = font - 0.5
    while candidate > MIN_FONT_PT + 1e-9:
        candidates.append(round(candidate, 2))
        candidate -= 0.5
    if font > MIN_FONT_PT + 1e-9:
        candidates.append(MIN_FONT_PT)
    return candidates


def _shrink_repair(
    box: RenderBox, canvas_w: float, canvas_h: float
) -> Optional[Tuple[float, str]]:
    """The largest half-point shrink (never below the floor) at which the
    text fits, or None when even the floor font does not fit."""
    font = box.font_pt
    if font is None or font <= MIN_FONT_PT:
        return None
    for candidate in _font_candidates(font):
        if not _fits(box, candidate, canvas_w, canvas_h):
            continue
        note = " (to the floor)" if candidate == MIN_FONT_PT else ""
        return round(candidate / font, 4), (
            f"font {font:.1f}pt -> {candidate:.1f}pt{note}"
        )
    return None


def _growth_repair(
    box: RenderBox,
    boxes: Sequence[RenderBox],
    font_pt: float,
    canvas_w: float,
    canvas_h: float,
) -> Optional[Tuple[float, float]]:
    """The (dy, dh) that covers the line deficit at this font within the
    canvas margin and the gaps to horizontally overlapping neighbors, or
    None when the text already fits or no room exists."""
    if box.capacity_text is None or font_pt <= 0:
        return None
    slot = _capacity_slot(box, font_pt, canvas_w)
    needed = estimate_lines(box.capacity_text, slot, canvas_w, canvas_h)
    capacity = available_lines(slot, canvas_h)
    if needed is None or capacity is None or needed <= capacity:
        return None
    top = box.slot.y
    bottom = top + box.slot.height
    limit = 1.0 - EDGE_MARGIN_FRACTION
    top_limit = EDGE_MARGIN_FRACTION
    for other in boxes:
        if other is box or _x_overlap(other.slot, box.slot) <= 0:
            continue
        other_top = other.slot.y
        other_bottom = other.slot.y + other.slot.height
        if other_top >= bottom - 1e-6:
            limit = min(limit, other_top - NEIGHBOR_GAP_FRACTION)
        elif other_bottom <= top + 1e-6:
            top_limit = max(top_limit, other_bottom + NEIGHBOR_GAP_FRACTION)
    desired = (
        (needed - capacity) * font_pt * PT_TO_PX * LINE_HEIGHT_FACTOR * 1.05
        / canvas_h
    )
    bottom_room = limit - bottom
    up_room = top - top_limit
    if bottom_room >= desired and bottom_room >= RESIZE_MIN_GAIN_FRACTION:
        return 0.0, math.ceil(desired * 1000) / 1000
    dh = min(desired, bottom_room + up_room)
    if dh < RESIZE_MIN_GAIN_FRACTION:
        return None
    if dh <= bottom_room:
        return 0.0, math.ceil(dh * 1000) / 1000
    return round(-(dh - bottom_room), 3), math.ceil(dh * 1000) / 1000


def _plan_overflow(
    slide_id: str,
    box: RenderBox,
    boxes: Sequence[RenderBox],
    canvas_w: float,
    canvas_h: float,
    bullets: Sequence[str],
    removals_left: int,
) -> List[FixDecision]:
    """Bounded repairs for one overflowing element, best-first: grow the
    box if its text fits at the current font, else shrink the font to the
    largest half-point step at or above the floor, else drop to the floor
    font, else (bullets only, recorded) drop the longest bullet."""
    font = box.font_pt or box.slot.font_size_pt or 0.0

    def resize_decision(dy: float, dh: float) -> FixDecision:
        detail = (
            f"height {box.slot.height:.2f} -> "
            f"{box.slot.height + dh:.2f} of the canvas"
        )
        if dy:
            detail += f", shifted up {abs(dy):.2f}"
        return FixDecision(
            slide_id=slide_id,
            element_id=box.element_id,
            action="resize_element",
            dy=dy,
            dh=dh,
            detail=detail,
        )

    growth = _growth_repair(box, boxes, font, canvas_w, canvas_h)
    if growth is not None:
        return [resize_decision(*growth)]
    shrink = _shrink_repair(box, canvas_w, canvas_h)
    if shrink is not None:
        scale, detail = shrink
        return [
            FixDecision(
                slide_id=slide_id,
                element_id=box.element_id,
                action="reduce_font_size",
                font_scale=scale,
                detail=detail,
            )
        ]
    if font > MIN_FONT_PT:
        return [
            FixDecision(
                slide_id=slide_id,
                element_id=box.element_id,
                action="reduce_font_size",
                font_scale=round(MIN_FONT_PT / font, 4),
                detail=f"font {font:.1f}pt -> {MIN_FONT_PT:.0f}pt (to the floor)",
            )
        ]
    if box.kind == "bullets" and len(bullets) >= 2 and removals_left > 0:
        target = max(bullets, key=len)
        return [
            FixDecision(
                slide_id=slide_id,
                element_id=box.element_id,
                action="remove_low_priority_bullet",
                detail=f"removed bullet ({len(target)} chars): {target[:60]}",
                removed_text=target,
            )
        ]
    return []


def plan_fixes(
    deck: GeneratedDeck,
    style_guide: StyleGuide,
    templates: Sequence[TemplateRecord],
    canvas: Optional[Tuple[float, float]] = None,
    removals_used: Optional[Mapping[str, int]] = None,
) -> List[FixDecision]:
    """One repair plan per overflowing element, in element order."""
    canvas_w, canvas_h = canvas or canvas_size(style_guide)
    by_id = {record.template_id: record for record in templates}
    used = dict(removals_used or {})
    decisions: List[FixDecision] = []
    for slide in deck.slides:
        boxes, _ = plan_slide(
            slide, by_id.get(slide.template_id), style_guide
        )
        bullets = [
            block.text for block in slide.content if isinstance(block, BulletBlock)
        ]
        removals_left = MAX_BULLET_REMOVALS_PER_SLIDE - used.get(slide.slide_id, 0)
        for box in boxes:
            if _overflow_stats(box, canvas_w, canvas_h) is None:
                continue
            plan = _plan_overflow(
                slide.slide_id, box, boxes, canvas_w, canvas_h,
                bullets, removals_left,
            )
            for decision in plan:
                if decision.action == "remove_low_priority_bullet":
                    removals_left -= 1
                    used[slide.slide_id] = used.get(slide.slide_id, 0) + 1
            decisions.extend(plan)
    return decisions


# -- applying decisions -----------------------------------------------------


def apply_decisions(
    deck: GeneratedDeck, decisions: Sequence[FixDecision]
) -> Tuple[GeneratedDeck, List[AppliedFix], List[str]]:
    updated = deck.model_copy(deep=True)
    by_slide: Dict[str, List[FixDecision]] = {}
    for decision in decisions:
        by_slide.setdefault(decision.slide_id, []).append(decision)
    applied: List[AppliedFix] = []
    skipped: List[str] = []
    for slide in updated.slides:
        pending = by_slide.get(slide.slide_id)
        if not pending:
            continue
        new_fixes = list(slide.fixes)
        for decision in pending:
            if decision.action == "remove_low_priority_bullet":
                if decision.removed_text is None:
                    skipped.append(
                        f"{decision.slide_id}/{decision.element_id}: "
                        "removal carried no text to remove"
                    )
                    continue
                for index, block in enumerate(slide.content):
                    if (
                        isinstance(block, BulletBlock)
                        and block.text == decision.removed_text
                    ):
                        slide.content.pop(index)
                        break
                else:
                    skipped.append(
                        f"{decision.slide_id}/{decision.element_id}: "
                        "bullet to remove was not found on the slide"
                    )
                    continue
            else:
                new_fixes.append(
                    SlideFix(
                        element_id=decision.element_id,
                        font_scale=decision.font_scale,
                        dy=decision.dy,
                        dh=decision.dh,
                    )
                )
            applied.append(
                AppliedFix(
                    slide_id=slide.slide_id,
                    element_id=decision.element_id,
                    action=decision.action,
                    source=decision.source,
                    detail=decision.detail,
                    removed_text=decision.removed_text,
                )
            )
        slide.fixes = new_fixes
    return updated, applied, skipped


# -- vision intake ----------------------------------------------------------

_REASON_MAX = 200


def _validate_suggestion(
    suggestion: object,
    slide,
    by_element: Mapping[str, RenderBox],
    removal_budget: int,
) -> Tuple[Optional[FixDecision], str]:
    if not isinstance(suggestion, dict):
        return None, "suggestion is not an object"
    element_id = suggestion.get("element_id")
    box = by_element.get(element_id) if isinstance(element_id, str) else None
    if box is None:
        return None, f"unknown element '{element_id}'"
    action = suggestion.get("action")
    reason = str(suggestion.get("reason", ""))[:_REASON_MAX]
    suffix = f" ({reason})" if reason else ""
    if action == "reduce_font_size":
        scale = suggestion.get("scale")
        if not isinstance(scale, (int, float)) or not 0.5 <= scale <= 0.98:
            return None, f"{element_id}: scale {scale!r} outside 0.50-0.98"
        font = box.font_pt
        if font is None:
            return None, f"{element_id}: element has no font to scale"
        if font > MIN_FONT_PT and font * scale < MIN_FONT_PT:
            scale = MIN_FONT_PT / font
            if scale < 0.5:
                return None, f"{element_id}: would breach the {MIN_FONT_PT:.0f}pt floor"
        scale = round(float(scale), 2)
        detail = f"vision: font {font:.1f}pt -> {font * scale:.1f}pt{suffix}"
        return (
            FixDecision(
                slide_id=slide.slide_id,
                element_id=element_id,
                action="reduce_font_size",
                font_scale=scale,
                detail=detail,
                source="vision",
            ),
            "",
        )
    if action == "resize_element":
        dh = suggestion.get("dh")
        dy = suggestion.get("dy", 0.0)
        if not isinstance(dh, (int, float)) or not 0.01 <= dh <= 0.3:
            return None, f"{element_id}: dh {dh!r} outside 0.01-0.30"
        if not isinstance(dy, (int, float)) or not -0.3 <= dy <= 0.0:
            return None, f"{element_id}: dy {dy!r} outside -0.30-0.00"
        detail = (
            f"vision: height {box.slot.height:.2f} -> "
            f"{box.slot.height + dh:.2f}{suffix}"
        )
        return (
            FixDecision(
                slide_id=slide.slide_id,
                element_id=element_id,
                action="resize_element",
                dy=round(float(dy), 3),
                dh=round(float(dh), 3),
                detail=detail,
                source="vision",
            ),
            "",
        )
    if action == "remove_low_priority_bullet":
        bullets = [
            block.text for block in slide.content if isinstance(block, BulletBlock)
        ]
        if box.kind != "bullets":
            return None, f"{element_id}: not a bullets element"
        if len(bullets) < 2:
            return None, f"{element_id}: too few bullets to remove safely"
        if removal_budget <= 0:
            return None, f"{element_id}: bullet removal budget already spent"
        target = max(bullets, key=len)
        detail = f"vision: removed bullet: {target[:60]}{suffix}"
        return (
            FixDecision(
                slide_id=slide.slide_id,
                element_id=element_id,
                action="remove_low_priority_bullet",
                detail=detail,
                source="vision",
                removed_text=target,
            ),
            "",
        )
    return None, f"{element_id}: unsupported action '{action}'"


def _vision_decisions(
    provider: SlideCritic,
    deck: GeneratedDeck,
    templates_by_id: Mapping[str, TemplateRecord],
    style_guide: StyleGuide,
    images: Mapping[str, Path],
    removals_used: Mapping[str, int],
) -> Tuple[List[FixDecision], List[str], List[str]]:
    decisions: List[FixDecision] = []
    notes: List[str] = []
    rejected: List[str] = []
    provider_name = getattr(provider, "name", "unknown")
    for slide in deck.slides:
        image = images.get(slide.slide_id)
        if image is None:
            continue
        boxes, _ = plan_slide(
            slide, templates_by_id.get(slide.template_id), style_guide
        )
        by_element = {box.element_id: box for box in boxes}
        context = {
            "deck_title": deck.plan.deck_title,
            "slide_id": slide.slide_id,
            "slide_type": slide.slide_type,
            "elements": [
                {
                    "element_id": box.element_id,
                    "kind": box.kind,
                    "text": box.capacity_text,
                }
                for box in boxes
                if box.capacity_text is not None
            ],
        }
        try:
            payload = provider.critique_slide(image, None, context)
        except Exception as exc:
            notes.append(f"{slide.slide_id}: vision critique failed ({exc})")
            continue
        notes.append(f"{slide.slide_id}: vision critic '{provider_name}' responded")
        suggestions = payload.get("suggestions") if isinstance(payload, dict) else None
        if not isinstance(suggestions, list):
            rejected.append(f"{slide.slide_id}: response had no suggestion list")
            continue
        seen = {decision.element_id for decision in decisions}
        budget = MAX_BULLET_REMOVALS_PER_SLIDE - removals_used.get(
            slide.slide_id, 0
        )
        for suggestion in suggestions:
            decision, reason = _validate_suggestion(
                suggestion, slide, by_element, budget
            )
            if decision is None:
                rejected.append(f"{slide.slide_id}: {reason}")
                continue
            if decision.element_id in seen:
                rejected.append(
                    f"{slide.slide_id}: duplicate suggestion for "
                    f"'{decision.element_id}'"
                )
                continue
            seen.add(decision.element_id)
            if decision.action == "remove_low_priority_bullet":
                budget -= 1
            decisions.append(decision)
    return decisions, notes, rejected


# -- the loop ---------------------------------------------------------------


def run_critique_loop(
    deck: GeneratedDeck,
    style_guide: StyleGuide,
    templates: Sequence[TemplateRecord],
    vision_provider: Optional[SlideCritic] = None,
    images: Optional[Mapping[str, Path]] = None,
    max_iterations: int = MAX_CRITIQUE_ITERATIONS,
    threshold: int = CRITIQUE_THRESHOLD,
) -> Tuple[GeneratedDeck, CritiqueReport]:
    by_id = {record.template_id: record for record in templates}
    current = deck.model_copy(deep=True)
    removals_used: Dict[str, int] = {}
    iterations: List[CritiqueIteration] = []
    vision_notes: List[str] = []
    vision_used = False
    baseline: Optional[DeckAudit] = None
    stop_reason = "max_iterations_reached"
    for number in range(1, max_iterations + 1):
        audit = audit_deck(current, style_guide, templates)
        if baseline is None:
            baseline = audit
        decisions = plan_fixes(
            current, style_guide, templates, removals_used=removals_used
        )
        rejected: List[str] = []
        if number == 1 and vision_provider is not None and images:
            vision_decisions, notes, rejected = _vision_decisions(
                vision_provider, current, by_id, style_guide, images,
                removals_used,
            )
            vision_notes.extend(notes)
            if vision_decisions or rejected:
                vision_used = True
            planned = {(d.slide_id, d.element_id) for d in decisions}
            for decision in vision_decisions:
                key = (decision.slide_id, decision.element_id)
                if key in planned:
                    rejected.append(
                        f"{decision.slide_id}/{decision.element_id}: "
                        "suggestion superseded by the deterministic repair"
                    )
                    continue
                planned.add(key)
                decisions.append(decision)
        if not decisions:
            stop_reason = (
                "threshold_met" if audit.score >= threshold
                else "no_fixes_applied"
            )
            iterations.append(
                CritiqueIteration(
                    iteration=number,
                    score_before=audit.score,
                    score_after=audit.score,
                    rejected=rejected,
                )
            )
            break
        current, applied_now, skipped = apply_decisions(current, decisions)
        rejected.extend(skipped)
        for fix in applied_now:
            if fix.action == "remove_low_priority_bullet":
                removals_used[fix.slide_id] = removals_used.get(fix.slide_id, 0) + 1
        after = audit_deck(current, style_guide, templates)
        iterations.append(
            CritiqueIteration(
                iteration=number,
                score_before=audit.score,
                score_after=after.score,
                applied=applied_now,
                rejected=rejected,
            )
        )
        if after.score >= threshold:
            stop_reason = "threshold_met"
            break
    final = audit_deck(current, style_guide, templates)
    report = CritiqueReport(
        deck_title=deck.plan.deck_title,
        threshold=threshold,
        max_iterations=max_iterations,
        stop_reason=stop_reason,
        score_before=(baseline or final).score,
        score_after=final.score,
        iterations=iterations,
        before=(baseline or final).slides,
        after=final.slides,
        unresolved=[
            finding
            for state in final.slides
            for finding in state.findings
            if finding.severity in ("high", "medium")
        ],
        needs_manual_review=final.score < threshold,
        vision_used=vision_used,
        vision_notes=vision_notes,
    )
    return current, report
