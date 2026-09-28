"""Style-guide extraction (blueprint §6): turn a parsed DeckInventory into a
StyleGuide using native file data only.

All rules are deterministic and documented where they are not obvious.
Anything the native data cannot support stays None with a warning instead of
a guess. Vision notes, when supplied, are merged in with explicit vision
provenance — never silently mixed with native facts.
"""

from __future__ import annotations

import colorsys
import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from core.schemas import ColorInfo, DeckInventory, ShapeRecord, TextRun
from core.style_guide import (
    PROVENANCE_NATIVE,
    PROVENANCE_VISION,
    ContentRules,
    ElementTreatments,
    LayoutGrid,
    Margins,
    PaletteEntry,
    StyleGuide,
    Typography,
    TypographySpec,
)
from core.vision import SlideVisionNotes

# HSV thresholds separating neutral grays from saturated brand colors and
# light surfaces from dark marks. Common slate/gray text colors sit below
# 0.35 saturation; brand blues/teals/greens sit well above it.
NEUTRAL_SATURATION_MAX = 0.35
LIGHT_VALUE_MIN = 0.85

# A shape covering at least this fraction of the slide is treated as a
# background plate, not content, so it cannot collapse the margins to zero.
FULL_BLEED_AREA_FRACTION = 0.9

LIST_MIN_PARAGRAPHS = 2

DENSITY_LOW_MAX_WORDS = 40
DENSITY_MEDIUM_MAX_WORDS = 90

USAGE_BACKGROUND = "background"
USAGE_PRIMARY = "primary"
USAGE_TEXT = "text"
USAGE_ACCENT = "accent"

_WORD_RE = re.compile(r"\S+")


def _hex_to_hsv(hex_color: str) -> Tuple[float, float, float]:
    value = hex_color.lstrip("#")
    r, g, b = (int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    return colorsys.rgb_to_hsv(r, g, b)


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _round(value: float) -> float:
    return round(value, 1)


def _resolved_hex(color: ColorInfo, inventory: DeckInventory) -> Optional[str]:
    if color.hex:
        return color.hex.upper()
    if color.theme_role and inventory.theme:
        value = inventory.theme.colors.get(color.theme_role)
        if value:
            return value.upper()
    return None


class StyleGuideExtractor:
    def extract(
        self,
        inventory: DeckInventory,
        vision_notes: Optional[Sequence[SlideVisionNotes]] = None,
    ) -> StyleGuide:
        warnings: List[str] = []

        palette, palette_warnings = self._palette(inventory)
        warnings.extend(palette_warnings)

        typography, typography_warnings = self._typography(inventory)
        warnings.extend(typography_warnings)

        margins, margin_warnings = self._margins(inventory)
        warnings.extend(margin_warnings)

        element_treatments, treatment_warnings = self._element_treatments(inventory)
        warnings.extend(treatment_warnings)

        content_rules, rule_warnings = self._content_rules(inventory, typography)
        warnings.extend(rule_warnings)

        source_path = Path(inventory.source_file)
        layout_grid = LayoutGrid(
            slide_width_px=inventory.slide_size.width_px,
            slide_height_px=inventory.slide_size.height_px,
            margins_px=margins,
            column_count=None,
            gutter_px=None,
            provenance=PROVENANCE_NATIVE,
        )
        warnings.append(
            "Column count and gutter width are not inferred from native data in "
            "this milestone."
        )

        if vision_notes is not None:
            vision_warnings = self._merge_vision(
                vision_notes, inventory, palette, element_treatments
            )
            warnings.extend(vision_warnings)

        return StyleGuide(
            deck_id=self._deck_id(source_path),
            deck_name=source_path.stem or "deck",
            extracted_at=datetime.now(timezone.utc).isoformat(),
            source_file_type=source_path.suffix.lower() or "unknown",
            source_slide_count=inventory.slide_count,
            aspect_ratio=inventory.slide_size.aspect_ratio,
            palette=palette,
            typography=typography,
            layout_grid=layout_grid,
            element_treatments=element_treatments,
            content_rules=content_rules,
            warnings=warnings,
        )

    # -- identity -----------------------------------------------------------

    def _deck_id(self, source_path: Path) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", source_path.stem.lower()).strip("-") or "deck"
        digest = hashlib.sha1(str(source_path.resolve()).encode("utf-8")).hexdigest()
        return f"{slug}-{digest[:8]}"

    # -- palette ------------------------------------------------------------

    def _palette(
        self, inventory: DeckInventory
    ) -> Tuple[List[PaletteEntry], List[str]]:
        warnings: List[str] = []
        backgrounds: Counter = Counter()
        fills: Counter = Counter()
        lines: Counter = Counter()
        texts: Counter = Counter()
        unresolved = 0

        for slide in inventory.slides:
            if slide.background_color is not None:
                hex_color = _resolved_hex(slide.background_color, inventory)
                if hex_color:
                    backgrounds[hex_color] += 1
                else:
                    unresolved += 1
            for shape in slide.shapes:
                for counter, color in (
                    (fills, shape.fill_color),
                    (lines, shape.line_color),
                ):
                    if color is not None:
                        hex_color = _resolved_hex(color, inventory)
                        if hex_color:
                            counter[hex_color] += 1
                        else:
                            unresolved += 1
                if shape.text is not None:
                    for paragraph in shape.text.paragraphs:
                        for run in paragraph.runs:
                            if run.color is not None:
                                hex_color = _resolved_hex(run.color, inventory)
                                if hex_color:
                                    texts[hex_color] += 1
                                else:
                                    unresolved += 1

        if unresolved:
            warnings.append(
                f"{unresolved} color reference(s) could not be resolved to hex "
                "(theme missing or incomplete); they are excluded from the palette."
            )
        if not inventory.slides:
            return [], ["Deck has no slides; palette is empty."]

        totals: Counter = Counter()
        for counter in (backgrounds, fills, lines, texts):
            totals.update(counter)

        assigned: Dict[str, str] = {}

        for hex_color in sorted(backgrounds):
            assigned[hex_color] = USAGE_BACKGROUND
        for hex_color in sorted(fills):
            if hex_color in assigned:
                continue
            _, saturation, value = _hex_to_hsv(hex_color)
            if saturation < NEUTRAL_SATURATION_MAX and value >= LIGHT_VALUE_MIN:
                assigned[hex_color] = USAGE_BACKGROUND

        unassigned = [hex_color for hex_color in totals if hex_color not in assigned]
        saturated = [
            hex_color
            for hex_color in unassigned
            if _hex_to_hsv(hex_color)[1] >= NEUTRAL_SATURATION_MAX
        ]
        if saturated:
            primary = sorted(saturated, key=lambda c: (-totals[c], c))[0]
        elif unassigned:
            primary = sorted(unassigned, key=lambda c: (-totals[c], c))[0]
        else:
            primary = None
        if primary is not None:
            assigned[primary] = USAGE_PRIMARY

        for hex_color in unassigned:
            if hex_color in assigned:
                continue
            _, saturation, _ = _hex_to_hsv(hex_color)
            if saturation < NEUTRAL_SATURATION_MAX and hex_color in texts:
                assigned[hex_color] = USAGE_TEXT

        for hex_color in unassigned:
            assigned.setdefault(hex_color, USAGE_ACCENT)

        if not any(slide.background_color is not None for slide in inventory.slides):
            warnings.append(
                "No slide declares an explicit background; the deck's effective "
                "white background is a viewer default, not a recorded fact."
            )

        usage_order = {
            USAGE_BACKGROUND: 0,
            USAGE_PRIMARY: 1,
            USAGE_TEXT: 2,
            USAGE_ACCENT: 3,
        }
        ordered = sorted(
            assigned.items(), key=lambda item: (usage_order[item[1]], -totals[item[0]], item[0])
        )
        palette = [
            PaletteEntry(
                hex=hex_color,
                usage=usage,
                frequency=totals[hex_color],
                provenance=PROVENANCE_NATIVE,
            )
            for hex_color, usage in ordered
        ]
        return palette, warnings

    # -- typography ---------------------------------------------------------

    def _typography(
        self, inventory: DeckInventory
    ) -> Tuple[Typography, List[str]]:
        warnings: List[str] = []
        sized_runs: List[TextRun] = []
        for slide in inventory.slides:
            for shape in slide.shapes:
                if shape.text is None:
                    continue
                for paragraph in shape.text.paragraphs:
                    for run in paragraph.runs:
                        if run.font_size_pt is not None:
                            sized_runs.append(run)

        if not sized_runs:
            warnings.append(
                "No run states an explicit font size; typography is not derivable "
                "from native data (inherited sizes live in layouts/masters)."
            )
            return Typography(), warnings

        def body_key(run: TextRun) -> tuple:
            return (
                run.font_family,
                run.bold,
                _resolved_hex(run.color, inventory) if run.color is not None else None,
                run.font_size_pt,
            )

        body_counts = Counter(body_key(run) for run in sized_runs)
        # Ties favor the larger size: footers and captions are smaller than body.
        body_best = sorted(
            body_counts.items(), key=lambda item: (-item[1], -item[0][3], str(item[0]))
        )[0]
        body_family, body_bold, body_color, body_size = body_best[0]
        body_spec = TypographySpec(
            font_family=body_family,
            font_size_pt=body_size,
            font_weight=self._weight(body_bold),
            color_hex=body_color,
            sample_count=body_best[1],
            provenance=PROVENANCE_NATIVE,
        )

        above_body = [run for run in sized_runs if run.font_size_pt > body_size]
        title_spec: Optional[TypographySpec] = None
        if not above_body:
            warnings.append(
                "All sized text shares one style; no distinct title typography found."
            )
        else:

            def title_key(run: TextRun) -> tuple:
                return (
                    run.font_family,
                    run.bold,
                    _resolved_hex(run.color, inventory) if run.color is not None else None,
                )

            title_groups: Dict[tuple, List[TextRun]] = {}
            for run in above_body:
                title_groups.setdefault(title_key(run), []).append(run)
            best_key, best_runs = sorted(
                title_groups.items(),
                key=lambda item: (-len(item[1]), -max(r.font_size_pt for r in item[1]), str(item[0])),
            )[0]
            title_spec = TypographySpec(
                font_family=best_key[0],
                font_size_pt=max(run.font_size_pt for run in best_runs),
                font_weight=self._weight(best_key[1]),
                color_hex=best_key[2],
                sample_count=len(best_runs),
                provenance=PROVENANCE_NATIVE,
            )

        return Typography(title=title_spec, body=body_spec), warnings

    @staticmethod
    def _weight(bold: Optional[bool]) -> Optional[str]:
        if bold is True:
            return "bold"
        if bold is False:
            return "normal"
        return None

    # -- margins ------------------------------------------------------------

    def _margins(self, inventory: DeckInventory) -> Tuple[Margins, List[str]]:
        width = inventory.slide_size.width_px
        height = inventory.slide_size.height_px
        slide_area = width * height
        geoms = []
        for slide in inventory.slides:
            for shape in slide.shapes:
                if shape.depth != 0 or shape.geometry is None:
                    continue
                geom = shape.geometry
                if geom.width_px * geom.height_px >= FULL_BLEED_AREA_FRACTION * slide_area:
                    continue
                geoms.append(geom)
        if not geoms:
            return Margins(), [
                "No positioned content shapes; margins are not derivable."
            ]
        return (
            Margins(
                top_px=_round(min(g.y_px for g in geoms)),
                right_px=_round(width - max(g.x_px + g.width_px for g in geoms)),
                bottom_px=_round(height - max(g.y_px + g.height_px for g in geoms)),
                left_px=_round(min(g.x_px for g in geoms)),
            ),
            [],
        )

    # -- element treatments -------------------------------------------------

    def _element_treatments(
        self, inventory: DeckInventory
    ) -> Tuple[ElementTreatments, List[str]]:
        warnings: List[str] = []
        rounded = sum(
            1
            for slide in inventory.slides
            for shape in slide.shapes
            if "ROUNDED_RECTANGLE" in shape.shape_type
        )
        if rounded:
            warnings.append(
                f"{rounded} rounded-rectangle shape(s) found but the parser does not "
                "record corner radius in this milestone."
            )
        all_solid = bool(inventory.slides) and all(
            slide.background_color is not None for slide in inventory.slides
        )
        treatments = ElementTreatments(
            background_style="solid" if all_solid else None,
            provenance=PROVENANCE_NATIVE,
        )
        if treatments == ElementTreatments():
            warnings.append(
                "Element treatments (corner radius, shadows, borders) are not "
                "derivable from native data in this milestone."
            )
        return treatments, warnings

    # -- content rules ------------------------------------------------------

    def _content_rules(
        self, inventory: DeckInventory, typography: Typography
    ) -> Tuple[ContentRules, List[str]]:
        warnings: List[str] = []

        list_paragraphs: List[str] = []
        for slide in inventory.slides:
            for shape in slide.shapes:
                if shape.text is None:
                    continue
                paragraphs = [
                    "".join(run.text for run in paragraph.runs).strip()
                    for paragraph in shape.text.paragraphs
                ]
                non_empty = [text for text in paragraphs if text]
                if len(non_empty) >= LIST_MIN_PARAGRAPHS:
                    list_paragraphs.extend(non_empty)

        max_bullets = None
        max_words_per_bullet = None
        if list_paragraphs:
            per_frame = self._list_frame_sizes(inventory)
            max_bullets = max(per_frame) if per_frame else None
            max_words_per_bullet = max(_word_count(text) for text in list_paragraphs)
        else:
            warnings.append(
                "No multi-paragraph text frames found; bullet rules not measured."
            )

        max_title_length = None
        if typography.title is not None:
            title_texts = self._title_like_texts(inventory, typography)
            if title_texts:
                max_title_length = max(len(text) for text in title_texts)
            else:
                warnings.append("Title style found but no matching title text measured.")
        else:
            warnings.append("No title typography; max title length not measured.")

        density = None
        if inventory.slides:
            total_words = sum(
                _word_count(shape.text.text)
                for slide in inventory.slides
                for shape in slide.shapes
                if shape.text is not None
            )
            average = total_words / len(inventory.slides)
            if average <= DENSITY_LOW_MAX_WORDS:
                density = "low"
            elif average <= DENSITY_MEDIUM_MAX_WORDS:
                density = "medium"
            else:
                density = "high"

        return (
            ContentRules(
                max_bullets_per_slide=max_bullets,
                max_words_per_bullet=max_words_per_bullet,
                max_title_length=max_title_length,
                preferred_density=density,
                provenance=PROVENANCE_NATIVE,
            ),
            warnings,
        )

    @staticmethod
    def _list_frame_sizes(inventory: DeckInventory) -> List[int]:
        sizes: List[int] = []
        for slide in inventory.slides:
            for shape in slide.shapes:
                if shape.text is None:
                    continue
                non_empty = [
                    "".join(run.text for run in paragraph.runs).strip()
                    for paragraph in shape.text.paragraphs
                ]
                non_empty = [text for text in non_empty if text]
                if len(non_empty) >= LIST_MIN_PARAGRAPHS:
                    sizes.append(len(non_empty))
        return sizes

    def _title_like_texts(self, inventory: DeckInventory, typography: Typography) -> List[str]:
        title = typography.title
        if title is None:
            return []
        texts: List[str] = []
        for slide in inventory.slides:
            for shape in slide.shapes:
                if shape.text is None:
                    continue
                for paragraph in shape.text.paragraphs:
                    for run in paragraph.runs:
                        if run.font_size_pt is None or run.font_size_pt <= (
                            typography.body.font_size_pt or 0
                        ):
                            continue
                        family_match = title.font_family in (None, run.font_family)
                        color_hex = (
                            _resolved_hex(run.color, inventory)
                            if run.color is not None
                            else None
                        )
                        color_match = title.color_hex in (None, color_hex)
                        weight = self._weight(run.bold)
                        weight_match = title.font_weight in (None, weight)
                        if family_match and color_match and weight_match:
                            paragraph_text = "".join(
                                r.text for r in paragraph.runs
                            ).strip()
                            if paragraph_text and paragraph_text not in texts:
                                texts.append(paragraph_text)
                            break
        return texts

    # -- vision merge -------------------------------------------------------

    def _merge_vision(
        self,
        vision_notes: Sequence[SlideVisionNotes],
        inventory: DeckInventory,
        palette: List[PaletteEntry],
        element_treatments: ElementTreatments,
    ) -> List[str]:
        warnings: List[str] = []
        if len(vision_notes) != inventory.slide_count:
            warnings.append(
                f"{len(vision_notes)} vision note(s) for {inventory.slide_count} "
                "slides; vision data ignored."
            )
            return warnings

        known_hexes = {entry.hex for entry in palette}
        added_colors = 0
        for notes in vision_notes:
            for hex_color in notes.dominant_colors:
                if hex_color not in known_hexes:
                    palette.append(
                        PaletteEntry(
                            hex=hex_color,
                            usage=USAGE_ACCENT,
                            frequency=0,
                            provenance=PROVENANCE_VISION,
                        )
                    )
                    known_hexes.add(hex_color)
                    added_colors += 1

        if element_treatments.background_style is None:
            styles = Counter(
                notes.background_style
                for notes in vision_notes
                if notes.background_style is not None
            )
            if styles:
                best = sorted(styles.items(), key=lambda item: (-item[1], item[0]))[0][0]
                element_treatments.background_style = best
                element_treatments.provenance = PROVENANCE_VISION
                warnings.append(
                    f"Background style '{best}' inferred by vision (not native data)."
                )

        if added_colors:
            warnings.append(
                f"{added_colors} dominant color(s) added from vision descriptions "
                "as accents; they were not present in the native file data."
            )
        for notes in vision_notes:
            for note in notes.notes:
                warnings.append(f"vision (slide {notes.slide_number}): {note}")
        return warnings
