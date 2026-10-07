# Architecture

DeckDNA is a single Python 3.11+ package split into two layers: `core/` holds the
deterministic domain logic and `app/` hosts a local FastAPI web product on top of
it. There is no network service beyond the local web server and the two optional,
opt-in Google Gemini adapters. Everything runs on the user's machine.

## System map

```mermaid
flowchart LR
    subgraph CLI["Command line"]
        parse["parse_deck.py"]
        render["render_deck.py"]
        extract["extract_style.py"]
        gen["generate_deck.py"]
        demo["demo.py"]
    end

    subgraph WEB["Local web app  (127.0.0.1:8000)"]
        pages["Browser pages<br/>(app/web.py)"]
        api["JSON API /v1<br/>(app/api)"]
        queue["Job queue<br/>(app/jobs.py)"]
        store["SQLite<br/>(app/db.py)"]
    end

    subgraph CORE["core/  -  deterministic domain logic"]
        source["source_parser"]
        parsers["pptx / pdf / raster<br/>parsers"]
        classify["slide_classifier"]
        templates["templates"]
        style["extractor<br/>(style guide)"]
        genpipe["generation"]
        layout["layout"]
        critique["critique"]
        html["html_renderer"]
        pptx["pptx_export"]
    end

    subgraph ADAPTERS["Optional adapters"]
        renderers["renderer<br/>LibreOffice / PowerPoint"]
        vision["vision<br/>Gemini (opt-in)"]
        llm["llm<br/>Gemini (opt-in)"]
    end

    subgraph DISK["Local files (gitignored)"]
        sg["style_guides/"]
        out["output/"]
    end

    CLI --> CORE
    WEB --> CORE
    pages --> api
    api --> queue
    api --> store
    queue --> CORE
    source --> parsers
    CORE --> ADAPTERS
    CORE --> sg
    CORE --> out
    WEB --> out
```

## Layer responsibilities

| Module | Responsibility | Key entry points |
|---|---|---|
| `core/source_parser.py` | Dispatches a source path to the right parser; rejects legacy `.ppt` with a clear message. | `parse_source(path) -> DeckInventory` |
| `core/pptx_parser.py` | Reads a `.pptx` into a structural inventory (shapes, runs, colours, theme). | `DeckParser.parse(path)` |
| `core/pdf_parser.py` | Reads a `.pdf` with a text layer; measured colours for pages that declare none. | `PdfParser.parse(path)` |
| `core/raster_parser.py` | Reads a `.png` or a folder of slide PNGs; full-bleed picture plus measured colours. | `RasterParser.parse(path)` |
| `core/pixel_sampling.py` | Exact colour counts from rendered pixels for PDF/PNG sources. | `measure_colors(...)` |
| `core/schemas.py` | Pydantic models for the structural inventory. | `DeckInventory`, `SlideInventory`, ... |
| `core/slide_classifier.py` | Labels every slide with a layout type, a reason and a confidence. | `SlideClassifier().classify_deck(...)` |
| `core/templates.py` | Learns reusable layout skeletons (slots as canvas fractions). | `build_templates(...)`, `find_templates(...)` |
| `core/style_guide.py` | Style guide models with per-field provenance. | `StyleGuide`, `PaletteEntry`, ... |
| `core/extractor.py` | Turns an inventory into a `StyleGuide` using native data plus measured colours. | `StyleGuideExtractor().extract(...)` |
| `core/vision.py` | Opt-in Gemini vision enrichment and the visual critic. | `provider_from_environment()`, `critic_from_environment()` |
| `core/generation.py` | Material analysis, deterministic outline and content planning, `generate_deck`. | `analyze_material(...)`, `generate_deck(...)` |
| `core/llm.py` | Opt-in Gemini text adapter; every draft re-validated, rejection falls back to deterministic. | `text_provider_from_environment()` |
| `core/layout.py` | Shared placement of elements on a slide; used by both renderers so they cannot drift. | `plan_slide(slide, template, guide)` |
| `core/critique.py` | Deterministic audit and bounded repair loop. | `run_critique_loop(deck, guide, templates)` |
| `core/html_renderer.py` | Self-contained HTML preview from planned boxes. | `render_deck_html(...)`, `render_report(...)` |
| `core/pptx_export.py` | Editable PowerPoint export (native text boxes and shapes). | `export_deck_pptx(...)` |
| `core/renderer.py` | Renders a `.pptx` to PNGs via LibreOffice or desktop PowerPoint. | `select_renderer()` |
| `app/main.py` | `create_app()` factory; wires storage, queue, routers. | `create_app()` |
| `app/api/` | Versioned JSON API under `/v1`. | see [web-app.md](web-app.md) |
| `app/web.py` | Server-rendered browser pages. | five page routes |
| `app/jobs.py` | Single background worker, strictly FIFO. | `JobQueue` |
| `app/db.py` | SQLite metadata store. | `init_db()`, row helpers |
| `app/extraction.py` | The extraction job (same pipeline as `extract_style.py`, minus vision). | `run_extraction(...)` |
| `app/generation.py` | The generation job; fills the approved outline and writes artifacts. | `run_generation(...)` |
| `app/config.py` | Paths and limits; runtime state lives only under gitignored dirs. | `UPLOADS_DIR`, `DB_PATH`, ... |

## The single layout source of truth

`core/layout.py` is the most important design decision in the codebase. Both the
HTML preview renderer and the PPTX exporter plan every element through
`plan_slide()`, so the two output formats share the same slots, fonts, geometry
and stable element ids. A critique fix recorded against an element id therefore
re-renders identically in both formats, and the exported file can be traced back
to the preview element by element.

## Honesty boundaries

Several modules deliberately record what they cannot know instead of guessing:

- Parsers record unextractable content (chart internals, table cells, vector
  drawings) as notes or warnings, never dropped.
- Raster and image-only PDF sources carry no typed text, so their learned
  templates hold only picture placeholders; generation needs typed text and the
  CLIs stop loudly instead of exporting an empty deck.
- The style extractor leaves uninferable fields empty with a warning and tags
  every fact with a provenance marker.

See [limitations](status-and-roadmap.md#limitations) for the full list.
