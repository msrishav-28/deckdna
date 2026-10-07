# Data model and on-disk artifacts

Every structured value in DeckDNA is a Pydantic model, so the JSON written to
disk always matches the schema in code. This page groups the models by stage and
maps the files the tools actually write.

## Provenance

`core/style_guide.py` defines four provenance markers carried by style facts:

| Marker | Meaning |
|---|---|
| `native` | Read from the source file's own data |
| `measured` | Counted from rendered pixels (PDF/PNG sources) |
| `vision` | Supplied by the opt-in Gemini vision pass |
| `default` | A safe fallback the extractor chose |

Measured and vision facts are never silently mixed with native facts; each is
tagged and, where it matters, carries a warning.

## Stage 1 - structural inventory (`core/schemas.py`)

| Model | Fields |
|---|---|
| `SlideSize` | width/height in px and EMU |
| `ColorInfo` | rgb hex, theme index, lumMod/lumOff modifiers |
| `TextRun` | text, font family/size/bold/italic, colour |
| `ParagraphInfo` | list of runs, alignment, level, bullet marker |
| `TextInfo` | paragraphs plus a plain-text preview |
| `Geometry` | position/size in EMU and normalised px |
| `ShapeRecord` | name, shape/placeholder type, geometry, text, fill |
| `SlideInventory` | slide number, size, shapes, measured colours, notes |
| `ThemeInfo` | theme colours and font scheme |
| `DeckInventory` | deck id/name, source type, slides, canvas size, warnings |

Pixel coordinates are normalised to a 1920px-wide canvas that preserves the
deck's aspect ratio; EMU values are always recorded alongside.

## Stages 2-3 - classification and templates

`SlideClassification` (core/slide_classifier.py): slide number, `slide_type`,
`confidence` (high/medium/low), `reason`.

`TemplateSlot` (core/templates.py): `role`, `shape_type`, `x`, `y`, `width`,
`height` (all canvas fractions), plus optional `font_size_pt`, `color_hex`,
`fill_hex`, `bold`, `italic`.

`TemplateRecord`: `template_id`, `slide_type`, `source_deck`,
`source_slide_number`, `aspect_ratio`, `slots`, `warnings`.

The ten slot roles are `title`, `body`, `stat`, `label`, `attribution`,
`picture`, `chart`, `table`, `card`, `container`, `decorative`.

## Stage 4 - style guide (`core/style_guide.py`)

| Model | Fields |
|---|---|
| `PaletteEntry` | `hex`, `usage` (primary/background/accent/text), `frequency`, `provenance` |
| `TypographySpec` | font family, size pt, weight, colour, sample count, provenance |
| `Typography` | `title`, `body` |
| `Margins` | top/right/bottom/left px |
| `LayoutGrid` | slide width/height px, margins, columns, gutter, provenance |
| `ElementTreatments` | corner radius, shadow intensity, border width, background style, provenance |
| `ContentRules` | max bullets per slide, max words per bullet, max title length, preferred density, avoid repeating layouts, provenance |
| `StyleGuide` | deck id/name, extracted-at, source type and slide count, aspect ratio, palette, typography, layout grid, element treatments, content rules, warnings |

## Stages 5-7 - brief, plan, deck (`core/generation.py`)

| Model | Fields |
|---|---|
| `DeckBrief` | topic, audience, goal, slide count (1-50), tone, notes, style guide id |
| `OutlineSlide` | slide number, intent, slide type, template id, title, subtitle |
| `GenerationPlan` | deck title, audience, tone, planner name, slides, warnings |
| `BulletBlock` / `StatBlock` / `QuoteBlock` / `SubtitleBlock` | the four content block kinds (`type` discriminates) |
| `SlideFix` | element id, font scale, geometry deltas; persists with the deck |
| `SlideContent` | slide id/number/type/template, title, content blocks, fixes, warnings |
| `GeneratedDeck` | brief, plan, slides, style guide id, generator name, warnings |
| `DeckMaterial` | headings, bullets, stats, quotes, unusable lines |

## Stage 8 - critique (`core/critique.py`)

| Model | Fields |
|---|---|
| `Finding` | slide id, `code` (overflow/overlap/tiny_font), severity, detail, element id |
| `SlideState` | slide id, score, findings, warnings |
| `DeckAudit` | deck score (worst slide), per-slide states |
| `FixDecision` | slide/element, action, font scale, dy/dh, detail, source, removed text |
| `AppliedFix` | the applied subset, for the report |
| `CritiqueIteration` | iteration, before/after scores, applied and rejected fixes |
| `CritiqueReport` | threshold, max iterations, stop reason, before/after, iterations, before/after states, unresolved findings, manual-review flag, vision flags |

## On-disk artifacts

| Path | Written by | Contents |
|---|---|---|
| `output/raw_deck.json` | `parse_deck.py` | `DeckInventory` |
| `output/<deck>_slides/slide_001.png` ... | `render_deck.py` | rendered slide images |
| `style_guides/<deck>.json` | `extract_style.py`, web | `StyleGuide` |
| `style_guides/<deck>_templates.json` | `extract_style.py`, web | list of `TemplateRecord` |
| `output/generated/<name>.html` | `generate_deck.py` | self-contained preview |
| `output/generated/<name>_content.json` | `generate_deck.py` | `GeneratedDeck` |
| `output/generated/<name>_critique.json` | `--critique` | `CritiqueReport` |
| `output/generated/<name>.pptx` | `--pptx` | editable PowerPoint |
| `output/<stem>_artifacts/` | `demo.py` | raw inventory, style guide, templates, content, critique |
| `output/uploads/<deck_id>/` | web app | uploaded source copy |
| `output/generated/<gen_id>/` | web app | `deck.pptx`, `preview.html`, `content.json`, `critique.json` |
| `output/deckdna.sqlite3` | web app | deck, job, override and outline metadata |

## Identifier conventions

| Id prefix | Example | Scope |
|---|---|---|
| `deck_` | `deck_9f3c1a2b7d4e` | uploaded source and its learned guide |
| `job_` | `job_5e8d2c6a1b3f` | one background job |
| `outline_` | `outline_7a1f9c3e5b2d` | one proposed plan awaiting approval |
| `gen_` | `gen_4c8b6d2e9a1f` | one generation run |

Each id is its prefix plus twelve hex characters from `uuid4`. The web app uses
the style guide id as the deck id, so `style_guide_id` in the API names one
learned design. Element ids such as `slide_02-body-1` are stable across the
HTML preview, the PPTX shape names and the critique report.

## Git hygiene

`sample_decks/`, `style_guides/`, `output/` and `temp/` are gitignored (only
`.gitkeep` placeholders are tracked), and `.env` is never committed. Runtime
state never reaches the repository; see
[security-and-privacy.md](security-and-privacy.md).
