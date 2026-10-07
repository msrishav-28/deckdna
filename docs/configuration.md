# Configuration

DeckDNA needs no configuration to run its deterministic core. Two optional
capabilities read a Google Gemini key from the environment at runtime.

## Environment

Copy `.env.example` to `.env` at the repository root and add your key:

```bash
GEMINI_API_KEY=your-key-here
```

`core/vision.load_dotenv()` loads `.env` when a tool needs it. The key is read
only at runtime, is never committed, and the repository never logs it. You can
also override the vision model:

```bash
GEMINI_MODEL=gemini-2.0-flash   # default
```

The key is needed only for the opt-in features:

| Feature | Needs |
|---|---|
| Vision style enrichment (`--use-vision` on `extract_style.py`) | `GEMINI_API_KEY` |
| Visual critic in the critique loop (`--use-vision` with `--critique`) | `GEMINI_API_KEY` |
| LLM outline/content drafting (`--use-llm`) | `GEMINI_API_KEY` |

Without a key all three are skipped and the deterministic pipeline runs
unchanged. Both adapters read the key through `provider_from_environment()`,
`critic_from_environment()` and `text_provider_from_environment()`.

## Rendering backends

`scripts/render_deck.py` renders `.pptx` slides to PNGs and needs one real
renderer:

- **LibreOffice** (`soffice --headless`) - cross-platform default; converts to
  PDF and rasterizes with PyMuPDF.
- **Desktop PowerPoint** (Windows) - higher fidelity via COM automation.

`select_renderer()` returns the first available adapter and raises a clear
`RenderError` when neither is installed; there is deliberately no fake
fallback.

## Paths and runtime state

All runtime state lives under gitignored directories (`app/config.py` and
`.gitignore`):

| Path | Contents | Committed |
|---|---|---|
| `sample_decks/` | your source decks | no (`.gitkeep` only) |
| `style_guides/` | learned style guides and template libraries | no |
| `output/` | parsed JSON, rendered PNGs, generated decks, web uploads and SQLite | no |
| `temp/` | scratch space, including pytest's `--basetemp` | no |
| `.env` | secrets | never |

The web app stores uploads under `output/uploads/`, generated decks under
`output/generated/`, and its SQLite database at `output/deckdna.sqlite3`.

## Limits

| Limit | Value | Where |
|---|---|---|
| Web upload size | 64 MB | `app/config.py` |
| Outline `topic` | 200 chars | `app/api/generation.py` |
| Outline notes | 200 notes, 500 chars each | `app/api/generation.py` |
| `slide_count` | 1-50 | `core/generation.py` |
| Critique threshold | 85 | `core/critique.py` |
| Critique iterations | 2 | `core/critique.py` |
| Minimum font | 12pt (8pt hard floor for recorded fixes) | `core/critique.py`, `core/layout.py` |

## Dependencies

Pinned in `requirements.txt` for reproducible installs (Python 3.11+):
python-pptx, pydantic, lxml, pymupdf, FastAPI, uvicorn, python-multipart,
Jinja2, pytest, and the `comtypes` Windows-only adapter. Transitive versions
are resolved by pip from these pins.
