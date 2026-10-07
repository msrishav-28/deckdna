# Status and roadmap

This page tracks what is implemented, what is a working prototype, and what is
planned, per blueprint section 21. Status is grounded in the running code and
test suite, not in intentions.

## Milestones

| Milestone | Blueprint | Status | Evidence |
|---|---|---|---|
| 0 - Repository hygiene | sec. 12 | Implemented | gitignore rules, pinned deps, clean README, pytest runs |
| 1 - Parser-only extraction | sec. 12 | Implemented | `core/pptx_parser.py`, `test_parser.py` (20 tests) |
| 2 - Slide rendering + style guide | sec. 12 | Implemented | `core/renderer.py`, `core/extractor.py`, `test_renderer.py`, `test_extractor.py` |
| 3 - Template library | sec. 12 | Implemented | `core/templates.py`, `test_templates.py`, `test_slide_classifier.py` |
| 4 - Generation without PPTX | sec. 12 | Implemented | `core/generation.py`, `core/html_renderer.py`, `test_generation.py`, `test_html_renderer.py` |
| 5 - Editable PPTX export | sec. 12 | Implemented | `core/pptx_export.py`, `test_pptx_export.py` (native editable shapes) |
| 6 - Critique loop | sec. 12 | Implemented | `core/critique.py`, `test_critique.py` (37 tests) |
| 7 - Minimal web product | sec. 12 | Implemented | `app/`, `test_web_app.py` (29 tests), all nine `/v1` endpoints |
| 8 - Multi-format sources | post-MVP | Implemented | `core/pdf_parser.py`, `core/raster_parser.py`, `core/source_parser.py` and tests |
| sec. 19 Task 3 - end-to-end demo | sec. 19 | Implemented | `scripts/demo.py`, proven on the fixture deck |
| sec. 20 - definition of success | sec. 20 | In progress | see below |

## Implemented

Everything in the table above. The full flow - upload a deck, watch it learn
the style, inspect and edit the guide, describe a deck, approve an outline,
generate, preview and download an editable `.pptx` - works end to end from both
the web app and the CLI. 295 tests pass.

## Prototype

Working but deliberately bounded, so treat as a prototype surface:

- **Vision enrichment and critic.** Real and tested with mocked responses, but
  it depends on one external model (`gemini-2.0-flash`) whose behaviour can
  drift. Changes it proposes are always re-validated before application.
- **LLM outline/content drafting.** Real, validated and falling back to
  deterministic on rejection; quality varies with the model.
- **PPTX export fidelity.** Native editable text and shapes with exact shared
  geometry; complex decorative effects do not map and render as placeholders.

## Planned

- **Real-deck validation at full fidelity.** Blueprint section 20 asks for the
  flow on 5-10 visually coherent decks and the reaction "this looks like the
  source company made it". The two real design sets available so far are
  image-only (PNG), so they teach palette and geometry but cannot drive
  generation; full-fidelity validation needs the original `.pptx` files.
- **Vision-recovered text slots** for image-only sources (a new feature).
- **Delete/export-data controls and authentication** before any public launch
  (blueprint section 16).
- **Local-model path** for privacy-sensitive users.
- **Fine-tuning evaluation** only after retrieval plus constrained generation
  is validated (blueprint section 9).

## Limitations

Honest edges of the current system; none is hidden.

- **Image-only sources cannot generate.** PNG files and image-only PDFs carry
  no typed text, so their templates hold only picture placeholders. Style
  learning works; generation needs a `.pptx` or a text-layer PDF, and the tools
  stop loudly rather than writing an empty deck.
- **Fonts of PDF/PNG sources are unknown.** Recorded as unknown with a warning.
- **Chart internals, table cells and vector drawings** are noted, not
  extracted.
- **Column count and gutter width** are not inferred; they stay empty.
- **No authentication, delete or export controls** in the web app; it is a
  local single-user tool.
- **One background worker**, FIFO; large batch jobs are processed serially.
- **Slide titles on stat slides** reuse your stat context verbatim; the wording
  is yours, not the system's.
