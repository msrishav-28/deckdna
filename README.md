# DeckDNA

A self-hosted AI presentation generator that learns your design style from existing decks and generates new slides in that exact style as fully editable `.pptx` files.

## What It Does

- **Extract Design DNA**: Feed it your existing PPTX/PDF decks -> it extracts colors, fonts, layouts, and content rules into reusable style guides.
- **Template Library**: Auto-categorizes slides by type (title, agenda, comparison, stat callout, quote) for retrieval at generation time.
- **Generate Decks**: Enter a topic -> AI creates outline -> picks matching templates -> fills content -> exports as editable `.pptx`.
- **Critique Loop**: Renders slides, feeds them back to a vision-LLM for alignment/overlap/hierarchy fixes before final export.

## Stack (All Free to Start)

| Layer | Tool |
|---|---|
| Vision + Text LLM | Gemini 2.0/2.5 Flash (free tier: 15 RPM / 1,500 RPD) |
| HTML -> PPTX | `html-to-pptx` (MIT, Design-Arena) |
| PPTX Parsing | `python-pptx` (MIT) |
| Database | Supabase Free (500MB, pgvector) |
| Frontend | Vercel Hobby (free) |
| Backend | FastAPI + Cloud Run free tier |

## Quick Start

```bash
git clone https://github.com/msrishav-28/deckdna.git
cd deckdna
pip install -r requirements.txt
cp .env.example .env
python scripts/extract_style.py --input ./sample_decks
python app/main.py
```

## Docs

- [Getting Started](getting_started.md)
- [Roadmap](docs/roadmap.md)
- [Architecture](docs/research/architecture.md)
- [Design Learning Strategy](docs/research/design-learning.md)
- [Open-Source Landscape](docs/research/open-source-landscape.md)

MIT License - Built by M S Rishav Subhin
