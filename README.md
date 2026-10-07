# DeckDNA

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![python-pptx](https://img.shields.io/badge/parsing-python--pptx%201.0.2-6E8E3D)](https://python-pptx.readthedocs.io/)
[![Pydantic](https://img.shields.io/badge/schemas-Pydantic%202.10-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![PyMuPDF](https://img.shields.io/badge/pdf%2Brender-PyMuPDF%201.28-5A6ABF)](https://pymupdf.readthedocs.io/)
[![Gemini API](https://img.shields.io/badge/vision-Gemini%20API-8E75B2?logo=googlegemini&logoColor=white)](https://ai.google.dev/)
[![FastAPI](https://img.shields.io/badge/web-FastAPI%200.142-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Jinja2](https://img.shields.io/badge/pages-Jinja2%203.1-B41717)](https://jinja.palletsprojects.com/)
[![pytest](https://img.shields.io/badge/tests-pytest%208.3-0A9EDC?logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted AI presentation generator that learns your design style from your existing decks and generates new slides in that exact style as fully editable `.pptx` files.

**Status: MVP feature-complete.** All seven milestones of [DECKDNA_AGENT_BLUEPRINT.md](DECKDNA_AGENT_BLUEPRINT.md) are implemented: the full flow — upload a deck, watch it learn the style, inspect and edit the style guide, describe a new deck, approve an outline, generate, preview and download — works end to end from a local web page, and every step of the underlying pipeline is also available as a command-line tool. On top of that, sources are multi-format: a `.pptx`, a `.pdf`, a single `.png`, or a folder of `.png` slide images all work as input, in both the web app and the CLI. The sections below describe what works today.

## What works today

- **`python -m app.main`** — the local web app (blueprint Milestone 7): upload a `.pptx`, `.pdf`, or `.png` source, watch live extraction progress, inspect and edit the learned style guide with a change history, describe the deck you want and approve the proposed outline, then generate and download the result (editable PowerPoint, HTML preview, content JSON, quality report). A background queue and a local SQLite store keep progress and results across page reloads; the JSON API is versioned under `/v1`. Everything runs and stays on your machine, and runtime state is never committed.
- **`scripts/parse_deck.py`** — reads a source (`.pptx`, `.pdf`, `.png`, or a folder of `.png` slide images) and writes `output/raw_deck.json`: a structured inventory of every slide's shapes, text runs, fonts, colors, and theme (raw material for style learning). Anything the parser cannot fully extract (chart internals, table cells) is recorded explicitly as a note or warning rather than dropped.
- **`scripts/render_deck.py`** — turns every slide of a `.pptx` deck into a 1920px-wide PNG image, using desktop PowerPoint (Windows) or LibreOffice (any platform) when installed. Fails with a clear message when neither is available; never fakes a render.
- **`scripts/extract_style.py`** — learns a source's design DNA end to end (`.pptx`, `.pdf`, `.png`, or a folder of `.png` slide images): parses it, classifies every slide by type (title, bullets, chart, quote, comparison, and more), and writes a style guide (`style_guides/<deck>.json`) with the palette, typography, margins, and content rules, plus a reusable template library (`style_guides/<deck>_templates.json`) recording each slide's layout as canvas-fraction geometry. Every fact carries a `provenance` marker saying whether it came from the file itself or was inferred, and anything not derivable stays empty with a warning instead of a guess. PDFs and images carry no native design data, so their colors are measured from rendered pixels and their fonts are recorded as unknown, both flagged with warnings. Because slide images carry no typed text, templates learned from a `.png` source (or a PDF whose pages are images) hold only picture placeholders: style learning works, but generating a deck needs a `.pptx` or a text-layer PDF, and the CLIs stop with an explicit error rather than writing an empty deck. Optionally enriches the guide with Gemini vision descriptions of rendered slides (`--use-vision --slides-dir ...` with a `GEMINI_API_KEY` in `.env`).
- **`scripts/generate_deck.py`** — turns a topic plus your notes into a finished deck in a learned style: plans an outline from the template library, fills each slide with your material verbatim (it never invents facts), and writes a self-contained HTML preview (`output/generated/<name>.html`) plus the structured deck content (`<name>_content.json`). With `--pptx` it also exports an editable PowerPoint file (native text boxes, bullets, and shapes, with the exact geometry the HTML preview uses). Material that exceeds a slot's capacity is skipped with a warning rather than truncated; every slide and element carries a stable ID, reused as each shape's name in the exported file so future fixes can target elements deterministically. Media slots the generator has no real asset for render as labeled dashed placeholders — it never fabricates charts or tables. With `--use-llm` (and a `GEMINI_API_KEY` in `.env`) Gemini drafts the outline and slide text, but every draft must pass the same capacity and vocabulary validation as the deterministic path — anything that fails falls back to the deterministic result with a warning. The pipeline never ships unvalidated model output. With `--critique` the deck then passes through a bounded visual critique loop: deterministic layout checks score every slide 0 to 100 (text overflowing its box, colliding elements, text below the 12pt floor), and overflowing text is repaired automatically where a safe bounded fix exists — the box grows or shifts, the font shrinks by half-point steps, or, only once the font is already at the floor, the longest bullet of a list is dropped (at most two per slide, always recorded in the report with its text) — for up to 2 rounds, stopping early at a score of 85 or better. Wording is never changed; anything the loop will not repair automatically (such as two overlapping boxes) is left exactly as it is, listed in `output/generated/<name>_critique.json` with before/after scores and every applied or rejected fix, and the deck is flagged for manual review when the final score stays below the threshold. With `--use-vision` (needs `--critique` and a `GEMINI_API_KEY` in `.env`; the rendered slide images are sent to Google) a Gemini visual critic also reviews each slide once from the pre-critique render, and its suggestions pass the same bounded-safety checks; where it and the deterministic planner propose a fix for the same element, the deterministic one wins.
- **`scripts/demo.py`** — the whole flow in one command (the blueprint's end-to-end demo): parse a source, learn its style guide and template library, plan and generate a deck on your topic, optionally run the critique loop (`--critique`), and export an editable `.pptx` plus an HTML preview. Every intermediate artifact is kept under `output/<name>_artifacts/` (parsed deck, style guide, templates, deck content, critique report), and an evidence panel prints the palette and typography actually used with their provenance, the template chosen for each slide, and every critique fix. The output type is always reported explicitly, and a source that cannot carry text stops the run with an error instead of producing an empty file.
- **`scripts/make_fixture_deck.py`** — generates a small synthetic sample deck (no real content) used by the automated tests.
- **`tests/`** — an automated test suite run with pytest.

## What is planned next

All MVP milestones (0 through 7) are implemented, along with multi-format source ingestion (`.pdf`, `.png`, and folders of slide images, in both the web app and the CLI) and a single-command end-to-end demo (`scripts/demo.py`) that saves every intermediate artifact and prints an evidence panel. Validation on real designs is under way: the two real design sets available so far are image-only (folders of `.png` slides), so they yield a measured palette and layout geometry but no typed text slots, which is not enough to generate from — full-fidelity validation needs the original `.pptx` files. What remains is that validation — the definition-of-success demo in the blueprint: run the full flow on real client decks, confirm the generated decks are coherent and the exported `.pptx` is editable, and use what that surfaces to drive hardening and packaging. The full project documentation lives in [docs/](docs/README.md). See the blueprint for the complete architecture and milestone order.

## Setup

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Generate the synthetic sample deck, parse it, render it to images,
# and learn its style (style guide + template library)
python scripts/make_fixture_deck.py
python scripts/parse_deck.py --input tests/fixtures/sample_deck.pptx
python scripts/render_deck.py --input tests/fixtures/sample_deck.pptx
python scripts/extract_style.py --input tests/fixtures/sample_deck.pptx

# Generate a deck from a topic and notes, in that learned style
# (HTML preview + content JSON; add --pptx for an editable PowerPoint file)
python scripts/generate_deck.py --topic "Quarterly Review" \
  --style-guide style_guides/sample_deck.json \
  --audience "the leadership team" --notes "Revenue grew 40 percent this quarter" \
  --notes "Support load fell after we shipped self-serve docs" --slides 5 --pptx

# Or run the entire flow on one source with a single command: learn the
# style, generate a deck, critique it, export .pptx + HTML, keep artifacts
python scripts/demo.py --input tests/fixtures/sample_deck.pptx \
  --topic "AI adoption roadmap for a university" --slides 8 --critique \
  --notes "Pilot results" --notes "62% - of faculty want AI tools" \
  --output output/demo.pptx

# Run the local web app, then open http://127.0.0.1:8000 in your browser
python -m app.main

# Run the tests
pytest
```

Add `--critique` to the generate command to audit the generated layout, automatically repair overflows, and write `output/generated/<name>_critique.json` with before/after scores and every applied or rejected fix. Add `--use-vision` on top of it (requires a Gemini key; slide images are sent to Google) to include the model-based visual critic in the loop.

Optional vision enrichment of the style guide needs a free Gemini API key; copy `.env.example` to `.env` and add your key, then pass `--use-vision --slides-dir output/slides`. Without a key the tool skips vision and works from file data alone. The key is never committed.

## Project layout

```text
app/                  Web app: FastAPI JSON API under /v1, local job queue, SQLite store, browser pages and templates
core/                 Domain logic (schemas, PPTX parser, style extractor, slide classifier, templates, vision adapter, outline/content generation, shared layout planner, bounded critique loop, HTML preview renderer, editable PPTX exporter)
scripts/              Command-line entry points
tests/                Pytest suite and synthetic fixtures
sample_decks/         Your decks go here (never committed)
output/               Generated JSON and decks (never committed)
style_guides/         Extracted style guides (never committed)
temp/                 Scratch space (never committed)
```

## Documentation

The full project documentation is in [docs/](docs/README.md):

- [Architecture](docs/architecture.md) — system map, layer responsibilities, and where each rule lives
- [Pipeline](docs/pipeline.md) — the nine stages from source file to finished deck
- [Data model](docs/data-model.md) — every stored artifact, its fields, and provenance markers
- [Using the CLI](docs/cli.md) — one page per script, with real flags and defaults
- [Using the web app](docs/web-app.md) — pages and the `/v1` JSON API
- [Style learning](docs/style-learning.md) — what is learned from each source type and what is not
- [Generation and critique](docs/generation-and-critique.md) — content guarantees and the bounded repair loop
- [Configuration](docs/configuration.md) — environment variables, paths, and limits
- [Testing](docs/testing.md) — suite map and how to run it
- [Security and privacy](docs/security-and-privacy.md) — what leaves the machine and when
- [Status and roadmap](docs/status-and-roadmap.md) — what is implemented, prototyped, or planned

## License

MIT — see [LICENSE](LICENSE). Built by M S Rishav Subhin.
