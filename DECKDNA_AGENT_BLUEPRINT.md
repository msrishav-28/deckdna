# DeckDNA — Agent Blueprint and Build Specification

> **Purpose:** This document is the canonical implementation brief for AI coding agents and human contributors building DeckDNA. Treat it as product requirements, architecture specification, technical roadmap, engineering constraints, and research notes.
>
> **Product status:** Pre-MVP scaffold.
> **Owner:** M S Rishav Subhin.
> **Primary constraint:** Stay free/open-source until there is clear user traction; spend only where necessary.

---

## 1. Product Thesis

**DeckDNA is a self-hosted AI presentation generator that learns a user's visual language from their existing slide decks, extracts reusable “design DNA,” and produces new, editable presentations in that style.**

The product is inspired by Chronicle-style presentation tools, but the wedge is different from a generic “prompt to slides” product:

> **Input:** a user's existing PPTX/PDF decks + notes/topic/outline.
>
> **Output:** a new deck using the user's colors, typography, spatial rules, recurring layouts, template families, and content-density conventions.

The central promise is **“make a new deck that feels like our existing decks.”**

### 1.1 User problem

Most AI deck generators produce presentations that are coherent but visually generic. Organizations and individuals already have recognizable visual habits: specific title placement, colors, font pairings, whitespace, comparison layouts, image crops, metrics cards, timeline patterns, and content density. Recreating this manually costs time, while generic AI output often fails brand-review quality.

### 1.2 Product differentiation

- Generic generators: **topic -> generic deck**
- DeckDNA: **existing decks -> reusable design system -> topic -> on-style deck**

The moat is not a custom foundation model in the first version. The moat is a quality dataset of user-approved template structures, a robust extraction process, template retrieval, deterministic rendering, and iterative visual critique.

### 1.3 Non-goals for the MVP

Do **not** build these initially:

- Real-time collaboration
- Complex multi-user workspace permissions
- Publish-to-web analytics
- A full Canva/Figma-like editor
- Widget marketplace
- Custom foundation-model training
- Training an image model on copyrighted third-party template libraries
- Perfect reproduction of every chart, animation, SmartArt object, and PowerPoint transition

The MVP must first prove: **upload 5–10 decks -> extract style -> generate a useful, visually consistent new deck.**

---

## 2. Core Product Principles

1. **Style is structured, not mystical.** Extract tokens and reusable layout rules instead of relying only on vague prompts.
2. **Never make the LLM invent everything from scratch.** Use a constrained template/component library.
3. **Separate content from layout.** LLMs produce structured content JSON; deterministic rendering produces visual output.
4. **Show the outline before rendering slides.** Storyline quality precedes slide quality.
5. **Retrieval before training.** Use examples, templates, few-shot prompts, and visual critique before considering fine-tuning.
6. **Editable output matters.** Prefer native text/shapes in PPTX rather than flat screenshots where practical.
7. **Use human correction as data.** A user-approved or edited layout becomes a reusable template/version.
8. **Free-first architecture.** Local/open-source tools and free tiers should work for early development.

---

## 3. How “Learning Design” Works

### 3.1 What not to assume

Do not assume the initial product needs to train a bespoke model. “Learning from decks” does not initially mean gradient descent or model-weight updates.

Fine-tuning requires a sizable, clean, paired dataset such as:

```text
input topic/content + desired slide type -> layout JSON / HTML / PPTX drawing instructions
```

A few decks are insufficient for reliable weight-level learning. Premature training creates cost, complexity, overfitting, poor debuggability, and unclear evaluation.

### 3.2 The recommended mechanism: retrieval + constrained generation

At MVP stage, design learning should work as follows:

1. Parse source PPTX/PDF decks.
2. Render each slide into an image for visual analysis.
3. Extract a style guide and layout description from every slide.
4. Categorize each slide by type: title, agenda, section divider, text/bullets, comparison, timeline, quote, image focus, metric, chart, table, closing, etc.
5. Store a template record containing:
   - Source slide image
   - Extracted design tokens
   - Structural layout JSON
   - Editable element metadata where available
   - Text/content density profile
   - Semantic description
   - Embeddings for retrieval
6. Generate a narrative outline for a new deck.
7. Assign each requested slide a slide type and retrieve the best style-compatible template.
8. Have an LLM generate structured content constrained by that template’s capacity.
9. Render with deterministic HTML/CSS or PPTX shape code.
10. Render the output back into a PNG.
11. Use a vision-capable model to critique visual alignment with the stored style guide/template.
12. Apply bounded fixes and export editable PPTX.

This is **extract once, retrieve and match forever**.

### 3.3 Design quality loop

The core generation loop:

```text
source deck
  -> style extraction
  -> style guide + template library
  -> topic / documents / outline
  -> deck storyline
  -> template retrieval per slide
  -> structured content generation
  -> deterministic render
  -> visual evaluation
  -> fix instructions
  -> rerender (max 1–2 retries)
  -> editable PPTX / HTML output
```

### 3.4 Why visual critique is essential

Text-based LLM instructions cannot reliably predict final layout behavior. Rendered output exposes:

- Overflowing text
- Overlaps
- Incorrect line wrapping
- Insufficient whitespace
- Bad hierarchy
- Misaligned components
- Repeated visual compositions
- Off-brand color use
- Poor image crop choices

A vision model should receive the rendered slide, the template screenshot/reference, and the structured style guide. It should return **bounded machine-readable fixes**, not freeform redesign instructions.

---

## 4. System Architecture

## 4.1 Major services

```text
+-------------------+
| Web UI / API client|
+---------+---------+
          |
          v
+-------------------+
| FastAPI backend   |
| auth, jobs, API   |
+---------+---------+
          |
          +------------------------------+
          |                              |
          v                              v
+-------------------+           +---------------------+
| Extraction worker |           | Generation worker   |
| PPTX/PDF -> DNA   |           | outline -> deck     |
+---------+---------+           +----------+----------+
          |                                |
          v                                v
+-------------------+           +---------------------+
| Postgres/Supabase |           | Renderer             |
| templates/tokens  |           | HTML/CSS -> PPTX     |
| pgvector metadata |           | / python-pptx        |
+-------------------+           +----------+----------+
                                             |
                                             v
                                  +---------------------+
                                  | visual critic       |
                                  | PNG -> fixes JSON   |
                                  +---------------------+
```

## 4.2 Suggested free-first stack

| Concern | MVP recommendation | Notes |
|---|---|---|
| Backend | Python + FastAPI | Lightweight, easy async job APIs |
| PPTX parsing | `python-pptx` + OOXML/ZIP parsing | Extract shapes, text, theme, coordinates |
| PDF parsing | PyMuPDF | Render and extract images/text from PDFs |
| Slide rendering | LibreOffice headless initially | Cross-platform; use PowerPoint COM on Windows for higher fidelity if needed |
| Vision/content LLM | Gemini Flash free tier initially | Use structured JSON output; provider abstraction required |
| Renderer | HTML/CSS -> PPTX via `html-to-pptx`-style implementation | Editable output is a key requirement |
| Storage | Supabase free tier or local SQLite/Postgres in dev | Store metadata, files, style guides, embeddings |
| Embeddings | Provider embeddings or local sentence-transformers | Slide/template retrieval |
| Frontend | Next.js/React on Vercel free tier | Defer until pipeline works |
| Editor later | tldraw, Fabric.js, or Konva | Do not build editor first |
| Background jobs | Local queue first; later Redis/Celery/RQ | Rendering/LLM calls are slow |
| Testing | pytest + fixture decks | Add regression image checks later |

## 4.3 Repository structure

```text
deckdna/
├── README.md
├── LICENSE
├── requirements.txt
├── .env.example
├── .gitignore
├── getting_started.md
├── docs/
│   ├── roadmap.md
│   ├── architecture.md
│   ├── design-learning.md
│   ├── open-source-landscape.md
│   └── agent-blueprint.md                 # this document
├── app/
│   ├── __init__.py
│   ├── main.py                            # FastAPI entrypoint
│   ├── config.py
│   ├── api/
│   │   ├── decks.py
│   │   ├── templates.py
│   │   └── generation.py
│   └── db.py
├── core/
│   ├── __init__.py
│   ├── extractor.py                       # PPTX/PDF -> style guide
│   ├── pptx_parser.py                     # native PPTX inspection
│   ├── renderer.py                        # renderer abstraction
│   ├── vision.py                          # provider abstraction
│   ├── template_classifier.py
│   ├── template_retriever.py
│   ├── outline_generator.py
│   ├── generator.py                       # slide plan + content JSON
│   ├── critic.py                          # render -> vision critique
│   ├── exporter.py                        # pptx/html export
│   └── schemas.py                         # Pydantic models
├── scripts/
│   ├── extract_style.py
│   ├── generate_deck.py
│   ├── render_deck.py
│   └── seed_templates.py
├── schemas/
│   ├── style_guide_schema.json
│   ├── slide_content_schema.json
│   ├── template_schema.json
│   └── critique_schema.json
├── templates/
│   ├── slide.html
│   ├── title.html
│   ├── bullets.html
│   ├── comparison.html
│   ├── stat.html
│   ├── quote.html
│   └── timeline.html
├── tests/
│   ├── fixtures/
│   │   ├── sample_deck.pptx
│   │   └── expected_style_guide.json
│   ├── test_extractor.py
│   ├── test_parser.py
│   ├── test_generator.py
│   ├── test_renderer.py
│   └── test_critic.py
├── sample_decks/
│   └── .gitkeep
├── style_guides/
│   └── .gitkeep
├── output/
│   └── .gitkeep
└── temp/
```

---

## 5. Data Model

## 5.1 Style guide

A deck-level style guide captures global rules. It must be editable by the user after extraction.

```json
{
  "deck_id": "acme-001",
  "deck_name": "Acme Investor Deck",
  "extracted_at": "2026-09-24T00:00:00Z",
  "source": {
    "file_type": "pptx",
    "slide_count": 14,
    "aspect_ratio": "16:9"
  },
  "palette": [
    {"hex": "#0B1F3A", "usage": "primary", "frequency": 14},
    {"hex": "#FFFFFF", "usage": "background", "frequency": 14},
    {"hex": "#18A999", "usage": "accent", "frequency": 7}
  ],
  "typography": {
    "title": {
      "font_family": "Inter",
      "font_size_pt": 44,
      "font_weight": "bold",
      "color_hex": "#0B1F3A"
    },
    "body": {
      "font_family": "Inter",
      "font_size_pt": 18,
      "font_weight": "normal",
      "color_hex": "#263238",
      "line_height": 1.35
    }
  },
  "layout_grid": {
    "slide_width_px": 1920,
    "slide_height_px": 1080,
    "margins_px": {"top": 80, "right": 100, "bottom": 80, "left": 100},
    "column_count": 12,
    "gutter_px": 24
  },
  "element_treatments": {
    "card_corner_radius_px": 16,
    "shadow_intensity": "subtle",
    "border_width_px": 1,
    "background_style": "solid"
  },
  "content_rules": {
    "max_bullets_per_slide": 5,
    "max_words_per_bullet": 14,
    "max_title_length": 56,
    "preferred_density": "medium",
    "avoid_repeating_layouts": true
  }
}
```

## 5.2 Template record

A template is an extracted source slide or an approved user-created variation.

```json
{
  "template_id": "tmpl_hero_001",
  "deck_id": "acme-001",
  "slide_type": "title",
  "name": "Dark hero with lower-right logo",
  "source_slide_index": 0,
  "preview_image_path": "storage://templates/tmpl_hero_001.png",
  "semantic_description": "Minimal dark title slide with one large left-aligned heading and a logo at the bottom right.",
  "layout_json": {
    "canvas": {"width": 1920, "height": 1080},
    "elements": [
      {
        "id": "title",
        "role": "title",
        "x": 120,
        "y": 300,
        "width": 1200,
        "height": 190,
        "max_lines": 3,
        "font_size_pt": 52
      },
      {
        "id": "logo",
        "role": "logo",
        "x": 1620,
        "y": 900,
        "width": 190,
        "height": 60
      }
    ]
  },
  "content_capacity": {
    "title_max_chars": 80,
    "subtitle_max_chars": 120,
    "max_bullets": 0,
    "images": 0
  },
  "style_guide_id": "acme-001",
  "embedding": "vector stored separately",
  "quality_score": 0.91,
  "user_approved": true
}
```

## 5.3 Generation plan

The LLM must produce this before it creates individual slide content.

```json
{
  "deck_title": "Q3 Product Roadmap",
  "audience": "Leadership team",
  "tone": "confident, concise, strategic",
  "slides": [
    {
      "slide_number": 1,
      "intent": "Set context and promise",
      "slide_type": "title",
      "template_id": "tmpl_hero_001",
      "title": "Q3 Product Roadmap",
      "subtitle": "From platform stability to growth acceleration"
    },
    {
      "slide_number": 2,
      "intent": "Summarize the story",
      "slide_type": "agenda",
      "template_id": "tmpl_agenda_004"
    }
  ]
}
```

## 5.4 Critique response

The critic must emit bounded fixes, never raw HTML or a full redesign.

```json
{
  "score": 0.76,
  "issues": [
    {
      "severity": "high",
      "category": "overflow",
      "element_id": "bullet_4",
      "reason": "Text extends beyond the designated card area.",
      "fix": {"action": "reduce_font_size", "value": 2}
    },
    {
      "severity": "medium",
      "category": "spacing",
      "element_id": "content_grid",
      "reason": "Whitespace below the title is insufficient relative to the reference template.",
      "fix": {"action": "increase_margin_top", "value": 24}
    }
  ],
  "approved": false
}
```

---

## 6. Extraction Pipeline

## 6.1 Input support priority

1. **PPTX** — primary input, best source of native editable data.
2. **PDF** — secondary, visual extraction only; text/layout inference is weaker.
3. **PNG/JPG reference images** — support later for style matching only.
4. **Google Slides/Keynote** — defer; import via export to PPTX/PDF.

## 6.2 PPTX parsing responsibilities

Use `python-pptx` for basic object traversal; inspect raw OOXML where the library does not expose enough information.

Extract:

- Slide size/aspect ratio
- Slide master and layout relationships
- Theme color scheme and font scheme
- Background fills
- Shapes: type, coordinates, dimensions, z-order
- Text boxes/placeholders: content, fonts, runs, font size, color, paragraph properties, alignment
- Images: crop, placement, aspect ratio
- Lines, fills, borders, rounded rectangles
- Tables and charts metadata where possible
- Notes/slide titles where useful

### 6.2.1 Do not overclaim support

Initial extraction may not fully preserve:

- SmartArt
- Complex PowerPoint animations
- Embedded Excel charts
- Advanced effects
- Certain master/layout inheritance details

Record unsupported object types in extraction logs rather than silently dropping them.

## 6.3 Rendering source slides to PNG

`python-pptx` cannot render slides. Implement a renderer adapter:

```python
class SlideRenderer(Protocol):
    def render_pptx(self, pptx_path: Path, output_dir: Path) -> list[Path]: ...
```

Implementations:

1. **LibreOfficeRenderer** (default cross-platform)
   - Use `soffice --headless --convert-to pdf` and convert PDF pages to PNG via PyMuPDF.
   - Validate LibreOffice availability at startup.
2. **PowerPointComRenderer** (Windows optional)
   - Use `comtypes`/`pywin32` if desktop Microsoft PowerPoint is installed.
   - Better rendering fidelity but Windows-only.
3. **NoRenderFallback**
   - For initial parser-only testing.
   - Must mark visual fields as low-confidence; do not fake vision extraction.

## 6.4 Vision analysis prompt

A strong prompt should force JSON, distinguish observation from inference, and include the input dimensions.

```text
You are a presentation design analyst.

Analyze the attached 1920x1080 slide image. Return valid JSON only.

Tasks:
1. Classify slide_type using one of:
   title, agenda, section_divider, bullets, comparison, stat_callout,
   quote, image_focus, chart, table, timeline, closing, other.
2. Identify all visible design tokens:
   - dominant colors in HEX (estimate only if source metadata absent)
   - title/body/caption typographic hierarchy
   - content density and maximum visible text capacity
   - grid: margins, columns, gutters, alignment anchors
   - card/border/shadow/radius treatments
   - image treatment/cropping conventions
3. Describe major elements as bounding boxes normalized 0..1:
   role, x, y, width, height, alignment, visual treatment.
4. Infer content constraints needed to avoid overflow.
5. State confidence values per major inference.

Do not invent logos, font names, or exact pixel measurements if they are not inferable.
```

## 6.5 Merge strategy

Use a trust hierarchy:

| Attribute | Primary truth source | Fallback |
|---|---|---|
| Font family/size/color | PPTX XML | Vision inference |
| Shape x/y/w/h | PPTX XML | Vision bounding boxes |
| Theme palette | PPTX theme XML | Vision color extraction |
| Card radius/shadow | XML when available | Vision inference |
| Semantic slide type | Vision model + heuristics | Rules based on shapes/text |
| Content capacity | Layout geometry + vision | Conservative defaults |
| Design intent | Vision description | User labeling |

Never replace exact native metadata with a vision estimate when native metadata exists.

---

## 7. Template Classification and Retrieval

## 7.1 Slide type taxonomy

Use a finite, extensible taxonomy:

```text
title
agenda
section_divider
bullets
comparison
stat_callout
quote
image_focus
chart
table
timeline
process
team
product_feature
case_study
closing
other
```

## 7.2 Classifier strategy

Start with hybrid rules + LLM classification:

- Text count and size
- Number of image shapes
- Presence of charts/tables
- Large numeric strings
- Repeated columns/cards
- Vision model classification

Store classification confidence and permit user correction. User corrections should override inference.

## 7.3 Retrieval strategy

For a planned slide, retrieve candidates filtered by:

1. Same selected style guide / brand kit
2. Requested slide type
3. Content capacity compatibility
4. Semantic similarity to planned intent
5. Diversity score: avoid repeat layout used in last N slides
6. Quality score / user-approved flag

Simple scoring:

```text
score =
  0.35 * style_match +
  0.25 * slide_type_match +
  0.20 * content_capacity_match +
  0.15 * semantic_similarity +
  0.05 * quality_score -
  repetition_penalty
```

For MVP, deterministic rules are acceptable. Do not introduce complex vector retrieval until there is enough template data to justify it.

---

## 8. Generation Pipeline

## 8.1 Step A: deck brief normalization

Inputs may include:

- A topic
- Raw notes
- Uploaded documents
- Existing outline
- Target audience
- Presentation duration
- Desired slide count
- Selected design library/style guide

Normalize to a `DeckBrief` object.

```json
{
  "topic": "Q3 Product Roadmap",
  "audience": "Leadership",
  "goal": "Secure roadmap alignment",
  "slide_count": 10,
  "tone": "strategic and concise",
  "source_material": ["notes.md"],
  "selected_style_guide": "acme-001"
}
```

## 8.2 Step B: storyline planning

Generate an outline **before** generating visual slides. Show this for approval in the UI later.

Rules:

- Every slide has one communication job.
- Do not use more slides than the content requires.
- Include visual rhythm: do not schedule identical layouts in sequence.
- Avoid text-heavy slides where a statistic, comparison, timeline, or visual template is available.
- Choose templates only from the approved/retrieved library.

## 8.3 Step C: content generation

Generate schema-valid JSON, never raw freeform PowerPoint instructions.

Example for bullet slide:

```json
{
  "slide_id": "slide_03",
  "slide_number": 3,
  "slide_type": "bullets",
  "title": "Three Q3 priorities",
  "content": [
    {"type": "bullet", "text": "Improve activation through guided onboarding"},
    {"type": "bullet", "text": "Launch the analytics workflow for enterprise teams"},
    {"type": "bullet", "text": "Reduce platform incident response time by 30%"}
  ]
}
```

Hard constraints must reflect the selected template:

- Max title characters
- Max bullets
- Max words per bullet
- Max cards/columns
- Image availability requirements

If content does not fit, the generator must simplify, split, or select a different template. It must not overflow.

## 8.4 Step D: rendering

### Preferred rendering model

```text
structured slide JSON + template layout JSON + style guide -> HTML/CSS -> editable PPTX
```

HTML/CSS helps because:

- It is expressive for modern layout.
- Coding models are effective at producing/adjusting CSS.
- Browser layout engines calculate boxes.
- It can be screenshot-tested.

Editable PPTX conversion must map:

- Text to native text boxes
- Images to native pictures
- Rectangles/cards to native shapes
- Basic lines/icons to native shapes/SVG when possible

### Rendering constraints

- Use the 16:9 canvas unless source deck uses another aspect ratio.
- All text must be native editable text where feasible.
- Use only whitelisted CSS/layout patterns supported by the PPTX converter.
- Limit output to known component types at MVP.
- Preserve element IDs so critique fixes can target elements deterministically.

## 8.5 Step E: critic loop

Pipeline:

```text
rendered output image + source template preview + style guide + slide content
  -> vision critic
  -> critique JSON
  -> constrained transform functions
  -> rerender
```

### Critic criteria

Score 0–100 across:

- No overflow/cropping
- No unwanted overlap
- Alignment/grid adherence
- Typography hierarchy
- Spacing/whitespace
- Palette and treatment consistency
- Content density
- Template fidelity
- Readability
- Visual variety across the deck

### Bounded correction actions

Allowed actions should include:

```text
reduce_font_size
increase_font_size
shorten_text
increase_margin_top
increase_margin_bottom
increase_gap
reduce_gap
move_element
resize_element
change_column_count
switch_to_alternate_template
remove_low_priority_bullet
```

Never let the critic silently alter facts, numbers, or substantive content. Content changes must be conservative and traceable.

### Stop conditions

- At most 2 critic iterations per slide for MVP.
- Stop early if score >= threshold, e.g. 85.
- If score remains low, flag the slide for manual review rather than looping indefinitely.

---

## 9. Training / Fine-Tuning Policy

## 9.1 MVP decision

**Do not train a model at the start.**

Use:

- Template retrieval
- Few-shot examples
- Structured style guides
- Prompt constraints
- Deterministic renderer
- Vision critique loop
- User feedback / approval labels

This is materially cheaper, works with as few as 5–10 source decks, is explainable, and gives direct product value.

## 9.2 When fine-tuning becomes justified

Consider a LoRA or supervised layout model only when all conditions are true:

- At least 50+ decks or 300+ high-quality, permissioned slides from one coherent design family
- Extracted and manually verified layout JSON
- Clear baseline metric showing retrieval+critique fails on a recurring task
- Held-out test set exists
- Budget/time for training and evaluation exists
- A concrete target behavior exists (e.g. content -> layout JSON)

## 9.3 Potential future training tasks

1. **Slide-type classifier**
   - Input: slide image + parsed metadata
   - Output: taxonomy label
   - Easy and low-cost once data grows.

2. **Layout-slot predictor**
   - Input: slide type + content schema + style guide
   - Output: component selection and normalized coordinates.

3. **Design critic/ranker**
   - Input: generated slide image + template reference
   - Output: quality score and structured violations.

4. **Visual style LoRA**
   - Only for image-generation elements; not the main PPTX structure.
   - Must use assets/decks you own or have permission to train on.

## 9.4 Dataset requirements for LoRA

If experimenting later:

- 30–60+ diverse slides for an initial style experiment
- Rendered PNG per slide
- Layout JSON per slide
- Slide category label
- Text content redacted or permissioned
- Train/validation/test split by deck, not only by slide
- Hold out 10–20% decks to detect memorization

Do not train on third-party paid template packs, competitor decks, or customer confidential decks without explicit rights.

---

## 10. Open-Source Strategy and Licensing

## 10.1 Useful open-source projects to study or compose

| Project category | Example | License preference | Role |
|---|---|---|---|
| Full self-hosted deck generator | Presenton | Apache-2.0 | Reference architecture / potential component source |
| AI-native slide generator | banana-slides | AGPL-3.0 | Learn from only; license risk for proprietary SaaS |
| HTML/web slide generation | frontend-slides | MIT | Study web-native slide composition |
| HTML -> editable PPTX | html-to-pptx variants | MIT | Preferred rendering primitive |
| Coding-agent slide tools | codex-slides | MIT | Study workflows and parallel rendering |
| Prompt -> PPTX framework | ai-forever/slides_generator | MIT | Reference / experiments |

## 10.2 License rules

- **MIT / Apache-2.0:** usually safe to use, modify, redistribute, and commercialize while retaining notices.
- **AGPL-3.0:** if you modify and provide the software over a network, it can require offering source code for the modified work. Do not build a closed/proprietary SaaS by copying AGPL code without understanding obligations.
- Always inspect the actual repository license before copying code.
- Do not copy proprietary visual assets or template data from competitors.

## 10.3 Recommended approach

**Compose primitives; do not fork a giant application initially.**

Reasoning:

- Large existing apps are often monolithic and expensive to untangle.
- The unique product thesis is the design-DNA extraction + retrieval + critique pipeline.
- Use permissively licensed rendering/parsing components; retain ownership of orchestration, schemas, extraction prompts, and UX.

---

## 11. Cost Plan

## 11.1 Free-first phase

| Resource | Plan |
|---|---|
| Development | Local Python environment |
| Vision/content inference | Gemini free tier or local experimentation |
| Parsing | `python-pptx`, PyMuPDF |
| Rendering | LibreOffice locally |
| Storage | Local filesystem / Supabase free tier |
| Database | Supabase free tier or local Postgres |
| Frontend hosting | Vercel Hobby |
| Source control | GitHub |

## 11.2 Spend only when necessary

Potential paid triggers:

- Vision/API free-tier rate limits become a real user bottleneck
- Need better multimodal fidelity for a validated workflow
- Storage/database exceeds free allocation
- Rendering jobs require production concurrency
- Custom domain/observability becomes necessary

### Cost control principles

- Cache style extraction by file hash.
- Do not re-analyze a slide if it has not changed.
- Batch visual inference where provider permits it.
- Run critic only for final slides, not every draft interaction.
- Cap critic iterations.
- Use lower-cost model for outline/content; reserve stronger vision model for difficult extraction/critique.
- Make provider/model selection configurable via environment variables.

---

## 12. MVP Milestones

## Milestone 0: Repository hygiene

**Definition of done:**

- README explains the thesis, scope, setup, and roadmap.
- `.env.example` contains no secrets.
- `requirements.txt` works in a fresh virtualenv.
- Tests pass.
- License added.
- Docs include this blueprint.

## Milestone 1: Parser-only extraction

**Goal:** Parse a PPTX into a useful machine-readable structural inventory without any LLM.

Deliverables:

- `core/pptx_parser.py`
- Native extraction of slide size, shapes, text boxes, basic fonts/colors/coordinates
- Persist `raw_deck.json`
- Fixture deck + tests

Acceptance criteria:

- A 10-slide basic deck parses without crashing.
- Text elements include x/y/w/h and font information where available.
- Unsupported shapes are logged.

## Milestone 2: Slide rendering and visual style guide

**Goal:** Render slides and combine native parse data with visual inference.

Deliverables:

- LibreOffice renderer adapter
- PNG outputs per slide
- Gemini/vision adapter interface
- `style_guide.json`
- Source/field confidence values

Acceptance criteria:

- Source deck yields one PNG per slide.
- Global palette/typography defaults are extracted.
- Each slide gets a type and layout summary.

## Milestone 3: Template library

**Goal:** Create reusable templates from source slides.

Deliverables:

- Template schema
- Slide classification
- Local JSON/SQLite persistence
- Simple retrieval by type

Acceptance criteria:

- User can inspect 5+ templates from one deck.
- Generator can retrieve title and bullets templates deterministically.

## Milestone 4: Generation without PPTX export

**Goal:** Generate an HTML deck from an approved style guide.

Deliverables:

- Deck brief schema
- Outline generation
- Slide content schema
- At least title, bullets, stat, quote templates
- HTML preview

Acceptance criteria:

- Topic -> 5-slide deck in selected style
- No text overflow in deterministic simple cases
- HTML screenshot is visually coherent

## Milestone 5: Editable PPTX export

**Goal:** Export the HTML/template output into editable PowerPoint.

Deliverables:

- Renderer/export adapter
- Native text boxes, shapes, images in PPTX
- Downloadable file

Acceptance criteria:

- PPTX opens in PowerPoint/LibreOffice.
- Text is editable.
- Basic title/bullets/stat slides retain acceptable layout fidelity.

## Milestone 6: Critique loop

**Goal:** Improve generated output visibly through one or two visual iterations.

Deliverables:

- Critique schema
- Rendered slide screenshot
- Vision evaluation
- Constrained transforms
- Before/after test examples

Acceptance criteria:

- At least 3 known layout failures are automatically improved.
- Loop terminates reliably.
- Content facts remain unchanged.

## Milestone 7: Minimal web product

**Goal:** Allow one user to upload, generate, and download via browser.

Flow:

```text
Upload deck -> extraction progress -> inspect style guide -> enter topic -> approve outline -> generate -> preview -> download PPTX
```

---

## 13. API Contract (MVP)

## 13.1 Upload/extract deck

```http
POST /v1/decks
Content-Type: multipart/form-data
file: my_brand_deck.pptx
```

Response:

```json
{
  "deck_id": "deck_123",
  "status": "queued"
}
```

```http
GET /v1/decks/{deck_id}
```

```json
{
  "deck_id": "deck_123",
  "status": "completed",
  "slide_count": 14,
  "style_guide_id": "style_456",
  "template_count": 14
}
```

## 13.2 Read/update style guide

```http
GET /v1/style-guides/{style_guide_id}
PATCH /v1/style-guides/{style_guide_id}
```

Allow user overrides to fonts, palette, spacing, and content limits.

## 13.3 Generate outline

```http
POST /v1/generations/outline
```

```json
{
  "topic": "Q3 Product Roadmap",
  "audience": "Leadership",
  "slide_count": 10,
  "style_guide_id": "style_456"
}
```

## 13.4 Generate deck

```http
POST /v1/generations
```

```json
{
  "style_guide_id": "style_456",
  "approved_outline_id": "outline_789",
  "output_format": "pptx",
  "enable_critique": true
}
```

## 13.5 Job status

```http
GET /v1/jobs/{job_id}
```

```json
{
  "job_id": "job_1",
  "status": "running",
  "stage": "critic_iteration_1",
  "progress": 72,
  "output_url": null
}
```

---

## 14. Prompt Engineering Rules

## 14.1 General rules

- Request strict JSON adhering to an explicit schema.
- Include content capacity and hard constraints.
- Provide a short few-shot set of 1–3 template examples, not a huge unstructured deck dump.
- Ask for concise content by default.
- Explicitly prohibit fabricated statistics, sources, customers, or business facts.
- Separate factual content generation from visual layout selection.

## 14.2 Outline prompt requirements

The outline model must:

- Understand audience and goal
- Give each slide a single purpose
- Select slide types from allowed taxonomy
- Avoid repeated types/layouts when alternatives exist
- Respect requested slide count
- Make a clear narrative arc

## 14.3 Content prompt requirements

The content model must:

- Fill `slide_content_schema.json`
- Respect title/bullet limits
- Use only input facts unless explicitly asked to research
- Shorten before overflowing
- Return JSON only

## 14.4 Critic prompt requirements

The critic must:

- Compare generated image against reference template and style guide
- Identify visible, actionable defects only
- Return bounded actions from an allow-list
- Avoid changing factual claims
- Set `approved: true` if no material changes needed

---

## 15. Quality Evaluation

## 15.1 Objective checks

Run deterministically where possible:

- All elements lie within slide bounds
- No overlapping boxes unless explicitly allowed
- Minimum contrast threshold for text/background
- Maximum content capacity respected
- Font size above readable minimum
- Required logo/brand elements present where relevant
- Output file opens successfully
- Editable text exists in PPTX export

## 15.2 Visual checks

Use critic model + human rating:

- Brand/style fidelity
- Hierarchy
- Balance and whitespace
- Layout variety
- Readability
- Overall professional quality

## 15.3 Human evaluation rubric

Ask 3–5 reviewers to rate 1–5:

1. Does this look like it belongs to the source deck family?
2. Is the slide readable at presentation distance?
3. Is the visual hierarchy clear?
4. Does the layout look intentional rather than auto-generated?
5. Would you use this with only minor editing?

Track baseline vs retrieval-only vs retrieval+critique.

---

## 16. Security, Privacy, and Rights

Decks may include confidential business material. Treat this as a core product concern from day one.

### Requirements

- Never commit user decks or extracted output to Git.
- `.gitignore` must exclude `sample_decks/*`, `style_guides/*`, `output/*`, `temp/*`, while retaining `.gitkeep` placeholders.
- Do not log full source content in production logs.
- Add delete/export data controls before public launch.
- Make cloud AI calls opt-in and clearly disclose that slide images/content are sent to the configured provider.
- Plan a local-model/self-hosted path for privacy-sensitive users.
- Obtain rights before ingesting/training on client decks or third-party templates.

---

## 17. Known Technical Risks

| Risk | Reality | Mitigation |
|---|---|---|
| PPTX parsing gaps | OOXML is complex; library abstractions are incomplete | Keep raw XML fallback; log unsupported objects |
| Rendering mismatch | LibreOffice, PowerPoint, browser, and generated PPTX differ | Start simple; test on target viewer; use reference snapshots |
| HTML-to-PPTX fidelity | Some CSS cannot map cleanly to native shapes | Whitelist layout/CSS features; fallback to image only for unsupported decorative elements |
| LLM JSON failures | Models may wrap output in markdown or hallucinate fields | Strict schemas, robust parser, retry once with repair prompt |
| Visual critique inconsistency | Vision models vary | Keep changes bounded; save snapshots; compare metrics; allow manual review |
| Generic output | Template retrieval alone can get repetitive | Diversity penalty, template variants, user-approved examples |
| API costs | Vision calls can grow quickly | Cache, batch, cap retries, model routing |
| Copyright/brand misuse | Users may upload assets they do not own | Terms, privacy controls, avoid training without rights |

---

## 18. Agent Execution Order

An AI coding agent should work in this order and avoid skipping validation.

### Phase 1 — Make the repository runnable

1. Inspect current repository state.
2. Add missing package structure and imports.
3. Pin or validate dependencies.
4. Add `pyproject.toml` or keep a clean `requirements.txt`.
5. Run lint/tests.
6. Do not claim unimplemented features are working.

### Phase 2 — Implement parser-first MVP

1. Create Pydantic models matching JSON schemas.
2. Implement `pptx_parser.py`.
3. Add fixture PPTX.
4. Write tests for parsing shapes/text/style.
5. Add clear unsupported-shape diagnostics.

### Phase 3 — Implement renderer adapter

1. Add LibreOffice detection.
2. Implement PPTX -> PDF -> PNG pipeline.
3. Add robust error messages when dependencies are missing.
4. Add test guarded by environment availability.

### Phase 4 — Add vision provider abstraction

1. Define provider interface:

```python
class VisionProvider(Protocol):
    def analyze_slide(self, image_path: Path, prompt: str) -> dict: ...
    def critique_slide(self, generated: Path, reference: Path, context: dict) -> dict: ...
```

2. Implement Gemini adapter.
3. Implement deterministic mock adapter for tests.
4. Never call remote APIs in unit tests.

### Phase 5 — Style guide/template extraction

1. Merge native and vision data with confidence metadata.
2. Persist style guide/template JSON locally.
3. Add CLI command and tests.
4. Add manual inspection output.

### Phase 6 — Generation

1. Implement deck plan schema.
2. Create mock deterministic content generator for tests.
3. Add LLM adapter behind interface.
4. Implement title/bullets/stat/quote HTML templates.
5. Render preview HTML first.

### Phase 7 — PPTX export

1. Choose one export primitive.
2. Support only the four basic slide types first.
3. Verify native editable text on export.
4. Add a visual regression comparison.

### Phase 8 — Critique loop

1. Add critic schema.
2. Add one safe transform at a time.
3. Use max 2 iterations.
4. Persist before/after state and critiques for debugging.

### Phase 9 — Web UI

Only after command-line pipeline works end-to-end.

---

## 19. Immediate Implementation Tasks

### Task 1: Fix the existing scaffold

The initial scaffold may contain placeholders. Replace them with clear, tested abstractions.

- Ensure `core/extractor.py` does not pretend slide rendering works when it returns nonexistent PNG paths.
- Introduce a renderer adapter with an explicit fallback/error state.
- Avoid broad `except Exception: pass`; log and return diagnostics.
- Avoid hardcoding only `gemini-2.0-flash`; use configuration.
- Parse actual theme colors/fonts rather than returning empty color arrays.
- Add structured Pydantic models instead of raw dictionaries.
- Separate infrastructure adapters from core logic.

### Task 2: Add parser and style-guide fixtures

- Add a non-confidential generated fixture deck in tests.
- Include title, bullets, stat, quote, and two-column comparison slides.
- Unit test extraction output.

### Task 3: Build a true local end-to-end demo

Command:

```bash
python scripts/demo.py \
  --input tests/fixtures/sample_deck.pptx \
  --topic "AI adoption roadmap for a university" \
  --output output/demo.pptx
```

Initial success can be HTML output if editable PPTX is not yet working, but output type must be explicitly reported.

---

## 20. Definition of Success

The first compelling demo is not a giant product. It is this:

1. Upload a small collection of 5–10 visually coherent decks.
2. The system identifies the brand palette, typography, recurring layout families, and content density.
3. Enter a new topic and short notes.
4. Approve a proposed story outline.
5. Receive a 6–10 slide deck that visibly resembles the original decks.
6. Download a PPTX where core text and shapes remain editable.
7. Show an evidence panel: selected template per slide, style tokens used, and critique improvements.

If the output makes a viewer say **“this looks like the source company made it”**, DeckDNA has proven the central thesis.

---

## 21. Final Engineering Guidance

- Build boring, inspectable systems before clever model training.
- Save every intermediate artifact: parsed deck JSON, rendered PNG, extracted style JSON, selected template IDs, generation plan, content JSON, critique JSON, final PPTX.
- This traceability will make debugging and quality improvements dramatically easier.
- Always distinguish **implemented**, **prototype**, and **planned** functionality in docs/UI.
- Prioritize a tight happy path over broad unsupported input formats.
- The best initial product is not “the best PPT generator.” It is **the best way to turn a user’s existing design language into a reliable generation system.**
