# Testing

The suite runs with pytest (Python 3.11+):

```bash
pytest
```

`pytest.ini` keeps pytest's scratch space inside the repo (`temp/pytest`) so
runs do not depend on the machine's system temp directory. The suite is
offline: no test calls a remote API; vision and LLM adapters are exercised
through deterministic scripted fakes. Exit code and the `N passed` summary
line are authoritative.

## Suite map (295 tests)

| File | Tests | Covers |
|---|---|---|
| `test_vision.py` | 39 | vision provider and critic, prompt building, key handling, provenance merging, mocked responses |
| `test_critique.py` | 37 | audit findings, bounded fix application and clamping, slot selection in the layout planner, the deterministic audit, loop stop conditions |
| `test_llm.py` | 30 | outline/content parsing and validation, grounded-number checks, fallback on rejection, end-to-end through `generate_deck` |
| `test_web_app.py` | 29 | upload limits and suffix checks, style guide patching, outline and generation endpoints, downloads, job polling |
| `test_generation.py` | 26 | material analysis, note formats, outline planning, capacity limits, warning preservation |
| `test_parser.py` | 20 | PPTX structural parsing, runs, colours, theme, honest notes |
| `test_extractor.py` | 15 | palette/typography/margins/content-rule derivation, provenance, warnings |
| `test_slide_classifier.py` | 14 | the 17-type taxonomy, rule ordering, confidence and reasons |
| `test_pdf_parser.py` | 14 | PDF text/geometry extraction, measured colours, honesty warnings |
| `test_html_renderer.py` | 14 | HTML output, escaping, stable element ids, render report |
| `test_templates.py` | 13 | slot roles and geometry, placeholder mapping, build/find/save/load |
| `test_raster_parser.py` | 13 | PNG and folder parsing, full-bleed picture, measured colours |
| `test_renderer.py` | 12 | renderer selection, LibreOffice/PowerPoint adapters, error when none |
| `test_pptx_export.py` | 12 | native editable text and shapes, geometry, placeholders |
| `test_source_parser.py` | 7 | format dispatch, `.ppt` rejection |

## Fixtures

`scripts/make_fixture_deck.py` writes a small synthetic deck
(`tests/fixtures/sample_deck.pptx`) with no real content - a title, bullets,
stat, quote and a two-column comparison slide - used across the parser,
extractor, generation and export tests. The file is generated, not committed;
rebuild it with the script. `tests/helpers.py` and `tests/conftest.py` share
builders and temporary directories.

## What the suite pins on purpose

- **Red/green for real fixes.** The layout slot-preference fix and the
  warning-preservation fix were each proven with a failing test before the
  change and a passing test after it.
- **No silent failure.** An empty or short deck must carry the planner's
  warnings, and a text-less source must exit loudly; both are asserted.
- **Validation before trust.** LLM output that fails validation falls back to
  deterministic, and the deck is marked accordingly.

To run a single area, target its file, for example `pytest tests/test_critique.py`.
