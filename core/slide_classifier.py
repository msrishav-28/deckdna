"""Slide-type classification (blueprint §7.2).

Classifies each slide into a layout taxonomy from native inventory data
only, with reasons and a confidence level so downstream template matching
knows how much to trust each label. First matching rule wins; rules are
ordered from most structurally certain (a chart is a chart) to weakest.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field

from core.schemas import DeckInventory, ShapeRecord, SlideInventory, SlideSize

SLIDE_TYPES = [
    "title",
    "agenda",
    "section_divider",
    "bullets",
    "comparison",
    "stat_callout",
    "quote",
    "image_focus",
    "chart",
    "table",
    "timeline",
    "process",
    "team",
    "product_feature",
    "case_study",
    "closing",
    "other",
]

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

_TABLE_MIN_AREA_FRACTION = 0.15
_IMAGE_FOCUS_AREA_FRACTION = 0.40
_TEAM_MIN_PICTURES = 3
_COMPARISON_MIN_AREA_FRACTION = 0.05
_COMPARISON_AREA_RATIO_MIN = 0.5
_COMPARISON_MAX_BLOCKS = 3
_STAT_SIZE_RATIO = 1.6
_QUOTE_MIN_WORDS = 8
_TITLE_MAX_WORDS = 25
_DIVIDER_MAX_WORDS = 8
_LIST_MIN_PARAGRAPHS = 3
_ROW_TOLERANCE_FRACTION = 0.1

_AGENDA_RE = re.compile(
    r"\b(agenda|outline|contents|what we'?ll cover|we will cover)\b", re.IGNORECASE
)
_CLOSING_RE = re.compile(r"\b(thank(s| you)?|questions?|q&a|contact)\b", re.IGNORECASE)
_PROCESS_RE = re.compile(
    r"\b(process|steps?|phases?|workflow|how it works|journey)\b", re.IGNORECASE
)
_TIMELINE_RE = re.compile(r"\btimeline\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


class SlideClassification(BaseModel):
    slide_number: int
    slide_type: str
    confidence: str
    reasons: List[str] = Field(default_factory=list)


class SlideClassifier:
    def classify(
        self, slide: SlideInventory, slide_count: int, slide_size: SlideSize
    ) -> SlideClassification:
        slide_area = slide_size.width_px * slide_size.height_px
        candidates = [
            self._chart(slide),
            self._table(slide, slide_area),
            self._team(slide),
            self._image_focus(slide, slide_area),
            self._quote(slide),
            self._stat_callout(slide),
            self._timeline(slide),
            self._process(slide, slide_size),
            self._comparison(slide, slide_area),
            self._agenda(slide),
            self._closing(slide),
            self._title(slide),
            self._section_divider(slide),
            self._product_feature(slide),
            self._bullets(slide),
        ]
        for candidate in candidates:
            if candidate is not None:
                slide_type, confidence, reasons = candidate
                return SlideClassification(
                    slide_number=slide.slide_number,
                    slide_type=slide_type,
                    confidence=confidence,
                    reasons=reasons,
                )
        return SlideClassification(
            slide_number=slide.slide_number,
            slide_type="other",
            confidence=CONFIDENCE_LOW,
            reasons=["no distinctive layout features detected in native data"],
        )

    def classify_deck(self, inventory: DeckInventory) -> List[SlideClassification]:
        return [
            self.classify(slide, inventory.slide_count, inventory.slide_size)
            for slide in inventory.slides
        ]

    # -- rules ---------------------------------------------------------------

    @staticmethod
    def _chart(slide: SlideInventory):
        charts = [shape for shape in slide.shapes if shape.has_chart]
        if charts:
            return "chart", CONFIDENCE_HIGH, [f"chart shape '{charts[0].name}' present"]
        return None

    @staticmethod
    def _table(slide: SlideInventory, slide_area: float):
        tables = [shape for shape in slide.shapes if shape.has_table]
        if not tables:
            return None
        area = SlideClassifier._area_fraction(tables[0], slide_area)
        if area >= _TABLE_MIN_AREA_FRACTION:
            return "table", CONFIDENCE_HIGH, [f"table covers {area:.0%} of the slide"]
        return None

    @staticmethod
    def _team(slide: SlideInventory):
        pictures = [shape for shape in slide.shapes if shape.is_picture]
        if len(pictures) >= _TEAM_MIN_PICTURES:
            return "team", CONFIDENCE_HIGH, [f"{len(pictures)} pictures on one slide"]
        return None

    @staticmethod
    def _image_focus(slide: SlideInventory, slide_area: float):
        total = sum(
            SlideClassifier._area_fraction(shape, slide_area)
            for shape in slide.shapes
            if shape.is_picture
        )
        if total >= _IMAGE_FOCUS_AREA_FRACTION:
            return "image_focus", CONFIDENCE_HIGH, [f"pictures cover {total:.0%} of the slide"]
        return None

    @staticmethod
    def _quote(slide: SlideInventory):
        passages = [
            shape for shape in slide.shapes
            if shape.text is not None and len(shape.text.text.split()) >= _QUOTE_MIN_WORDS
        ]
        if not passages:
            return None
        for shape in slide.shapes:
            if shape.text is None:
                continue
            for paragraph in shape.text.paragraphs:
                text = "".join(run.text for run in paragraph.runs).strip()
                if not text:
                    continue
                if any(run.italic for run in paragraph.runs) and len(
                    text.split()
                ) >= _QUOTE_MIN_WORDS:
                    return "quote", CONFIDENCE_HIGH, [
                        f"long italic passage ({len(text.split())} words)"
                    ]
                if text.startswith(("\u2014", "--")) and len(text.split()) <= 6:
                    return "quote", CONFIDENCE_HIGH, [
                        "attribution line below a long passage"
                    ]
        return None

    @staticmethod
    def _stat_callout(slide: SlideInventory):
        runs = [
            (run, shape)
            for shape in slide.shapes
            if shape.text is not None
            for paragraph in shape.text.paragraphs
            for run in paragraph.runs
            if run.font_size_pt is not None
        ]
        if len(runs) < 3:
            return None
        sizes = sorted(run.font_size_pt for run, _ in runs)
        median = sizes[len(sizes) // 2]
        for run, shape in runs:
            text = run.text.strip()
            alnum = re.sub(r"[^0-9A-Za-z]", "", text)
            if (
                run.font_size_pt >= _STAT_SIZE_RATIO * median
                and re.search(r"\d", text)
                and len(alnum) <= 6
                and len(text.split()) <= 3
            ):
                return "stat_callout", CONFIDENCE_HIGH, [
                    f"'{text}' at {run.font_size_pt:g}pt vs {median:g}pt median "
                    f"on shape '{shape.name}'"
                ]
        return None

    @staticmethod
    def _timeline(slide: SlideInventory):
        year_shapes = sum(
            1
            for shape in slide.shapes
            if shape.text is not None and _YEAR_RE.search(shape.text.text)
        )
        if year_shapes >= 3:
            return "timeline", CONFIDENCE_HIGH, [
                f"{year_shapes} shapes carry year-like labels"
            ]
        texts = [
            shape.text.text
            for shape in slide.shapes
            if shape.text is not None and shape.text.text.strip()
        ]
        if any(_TIMELINE_RE.search(text) for text in texts):
            return "timeline", CONFIDENCE_MEDIUM, ["'timeline' in text"]
        return None

    @staticmethod
    def _process(slide: SlideInventory, slide_size: SlideSize):
        arrow_shapes = [
            shape
            for shape in slide.shapes
            if "ARROW" in shape.shape_type or "CHEVRON" in shape.shape_type
        ]
        if len(arrow_shapes) >= 2:
            return "process", CONFIDENCE_HIGH, [
                f"{len(arrow_shapes)} arrow/chevron shapes"
            ]
        texts = [
            shape.text.text
            for shape in slide.shapes
            if shape.text is not None and shape.text.text.strip()
        ]
        if any(_PROCESS_RE.search(text) for text in texts):
            row = SlideClassifier._row_shapes(slide, slide_size)
            if len(row) >= 3:
                return "process", CONFIDENCE_MEDIUM, [
                    f"process wording with {len(row)} shapes in a row"
                ]
        return None

    @staticmethod
    def _comparison(slide: SlideInventory, slide_area: float):
        cards = [
            shape
            for shape in slide.shapes
            if shape.depth == 0
            and shape.shape_type.startswith("AUTO_SHAPE")
            and (shape.text is None or not shape.text.text.strip())
        ]
        if not 2 <= len(cards) <= _COMPARISON_MAX_BLOCKS:
            return None
        types = {shape.shape_type for shape in cards}
        if len(types) != 1:
            return None
        areas = [SlideClassifier._area_fraction(shape, slide_area) for shape in cards]
        if min(areas) < _COMPARISON_MIN_AREA_FRACTION:
            return None
        if min(areas) / max(areas) < _COMPARISON_AREA_RATIO_MIN:
            return None
        spans = []
        for shape in cards:
            if shape.geometry is None:
                return None
            spans.append(
                (shape.geometry.x_px, shape.geometry.x_px + shape.geometry.width_px)
            )
        spans.sort()
        for earlier, later in zip(spans, spans[1:]):
            if later[0] < earlier[1]:
                return None
        return "comparison", CONFIDENCE_HIGH, [
            f"{len(cards)} side-by-side '{sorted(types)[0]}' blocks"
        ]

    @staticmethod
    def _agenda(slide: SlideInventory):
        texts = [
            shape.text.text
            for shape in slide.shapes
            if shape.text is not None and shape.text.text.strip()
        ]
        matched = next((text for text in texts if _AGENDA_RE.search(text)), None)
        if matched is not None:
            return "agenda", CONFIDENCE_MEDIUM, [
                f"'{matched.strip()[:40]}' matches agenda wording"
            ]
        return None

    @staticmethod
    def _closing(slide: SlideInventory):
        texts = [
            shape.text.text
            for shape in slide.shapes
            if shape.text is not None and shape.text.text.strip()
        ]
        matched = next((text for text in texts if _CLOSING_RE.search(text)), None)
        if matched is not None:
            return "closing", CONFIDENCE_HIGH, [
                f"'{matched.strip()[:40]}' matches closing wording"
            ]
        return None

    @staticmethod
    def _title(slide: SlideInventory):
        if slide.slide_number != 1:
            return None
        words = sum(
            len(shape.text.text.split())
            for shape in slide.shapes
            if shape.text is not None
        )
        if 0 < words <= _TITLE_MAX_WORDS:
            return "title", CONFIDENCE_HIGH, [f"first slide with only {words} words"]
        return None

    @staticmethod
    def _section_divider(slide: SlideInventory):
        text_shapes = [shape for shape in slide.shapes if shape.text is not None]
        if not 1 <= len(text_shapes) <= 2:
            return None
        words = sum(len(shape.text.text.split()) for shape in text_shapes)
        if words <= _DIVIDER_MAX_WORDS:
            return "section_divider", CONFIDENCE_MEDIUM, [
                f"sparse slide: {words} words across {len(text_shapes)} text shape(s)"
            ]
        return None

    @staticmethod
    def _product_feature(slide: SlideInventory):
        if not any(shape.is_picture for shape in slide.shapes):
            return None
        for shape in slide.shapes:
            if shape.text is None:
                continue
            non_empty = [
                "".join(run.text for run in paragraph.runs).strip()
                for paragraph in shape.text.paragraphs
            ]
            non_empty = [text for text in non_empty if text]
            if len(non_empty) >= _LIST_MIN_PARAGRAPHS:
                return "product_feature", CONFIDENCE_MEDIUM, [
                    "picture beside a multi-point list"
                ]
        return None

    @staticmethod
    def _bullets(slide: SlideInventory):
        for shape in slide.shapes:
            if shape.text is None:
                continue
            non_empty = [
                "".join(run.text for run in paragraph.runs).strip()
                for paragraph in shape.text.paragraphs
            ]
            non_empty = [text for text in non_empty if text]
            if len(non_empty) >= _LIST_MIN_PARAGRAPHS:
                return "bullets", CONFIDENCE_HIGH, [
                    f"list of {len(non_empty)} lines on '{shape.name}'"
                ]
        return None

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _area_fraction(shape: ShapeRecord, slide_area: float) -> float:
        if shape.geometry is None or slide_area <= 0:
            return 0.0
        return (
            shape.geometry.width_px * shape.geometry.height_px
        ) / slide_area

    @staticmethod
    def _row_shapes(slide: SlideInventory, slide_size: SlideSize) -> List[ShapeRecord]:
        positioned = [
            shape
            for shape in slide.shapes
            if shape.depth == 0 and shape.geometry is not None
        ]
        tolerance = _ROW_TOLERANCE_FRACTION * slide_size.height_px
        rows: List[ShapeRecord] = []
        for shape in positioned:
            center = shape.geometry.y_px + shape.geometry.height_px / 2
            neighbors = [
                other
                for other in positioned
                if other is not shape
                and abs(
                    (other.geometry.y_px + other.geometry.height_px / 2) - center
                )
                <= tolerance
            ]
            if len(neighbors) >= 2:
                rows.append(shape)
        return rows
