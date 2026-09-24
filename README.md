# DeckDNA

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![python-pptx](https://img.shields.io/badge/parsing-python--pptx%201.0.2-6E8E3D)](https://python-pptx.readthedocs.io/)
[![Pydantic](https://img.shields.io/badge/schemas-Pydantic%202.10-E92063?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![PyMuPDF](https://img.shields.io/badge/rendering-PyMuPDF%201.28-5A6ABF)](https://pymupdf.readthedocs.io/)
[![pytest](https://img.shields.io/badge/tests-pytest%208.3-0A9EDC?logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A self-hosted AI presentation generator that learns your design style from your existing decks and generates new slides in that exact style as fully editable `.pptx` files.

**Status: early build (pre-MVP).** The full specification and roadmap live in [DECKDNA_AGENT_BLUEPRINT.md](DECKDNA_AGENT_BLUEPRINT.md). This repository is being built milestone by milestone; the sections below describe what works today.

## What works today

- **`scripts/parse_deck.py`** — reads a `.pptx` deck and writes `output/raw_deck.json`: a structured inventory of every slide's shapes, text runs, fonts, colors, and theme (raw material for style learning). Anything the parser cannot fully extract (chart internals, table cells) is recorded explicitly as a note or warning rather than dropped.
- **`scripts/render_deck.py`** — turns every slide of a `.pptx` deck into a 1920px-wide PNG image, using desktop PowerPoint (Windows) or LibreOffice (any platform) when installed. Fails with a clear message when neither is available; never fakes a render.
- **`scripts/make_fixture_deck.py`** — generates a small synthetic sample deck (no real content) used by the automated tests.
- **`tests/`** — an automated test suite run with pytest.

## What is planned next

Style-guide extraction (palette, typography, layout rules), a searchable template library, AI-generated outlines and decks, a visual critique loop, editable PPTX export, and a web UI. See the blueprint for the complete architecture and milestone order.

## Setup

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Generate the synthetic sample deck, parse it, and render it to images
python scripts/make_fixture_deck.py
python scripts/parse_deck.py --input tests/fixtures/sample_deck.pptx
python scripts/render_deck.py --input tests/fixtures/sample_deck.pptx

# Run the tests
pytest
```

Cloud AI features (later milestones) need a Gemini API key; copy `.env.example` to `.env` and add your key when you get there. The key is never committed.

## Project layout

```text
core/                 Parsing and domain logic (schemas, PPTX parser)
scripts/              Command-line entry points
tests/                Pytest suite and synthetic fixtures
sample_decks/         Your decks go here (never committed)
output/               Generated JSON and decks (never committed)
style_guides/         Extracted style guides (never committed)
temp/                 Scratch space (never committed)
```

## License

MIT — see [LICENSE](LICENSE). Built by M S Rishav Subhin.
