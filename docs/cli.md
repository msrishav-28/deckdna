# Command-line reference

All commands run from the repository root with the virtual environment active.
Every tool exits `0` on success and `1` on a loud, explained failure; none of
them ever reports success for an empty or faked result.

## Common source argument

The tools that take `--input` accept one of:

- a `.pptx` file - full structural inventory (text, fonts, colours, theme)
- a `.pdf` file with a text layer - real text slots plus measured colours
- a single `.png` slide image
- a folder of `.png` slide images, sorted by name

Legacy binary `.ppt` is not supported; the tool says to save it as `.pptx` or
export a PDF. Image-only sources (PNG, or a PDF whose pages are pictures) carry
no typed text: style learning works, but a deck cannot be generated from them
and the tools stop with an explicit error instead of writing an empty file.

## scripts/parse_deck.py

Reads a source into a structural inventory.

```bash
python scripts/parse_deck.py --input <source> [--output output/raw_deck.json]
```

## scripts/render_deck.py

Renders every slide of a `.pptx` to a 1920px-wide PNG. Uses LibreOffice
(cross-platform) or desktop PowerPoint (Windows, higher fidelity); fails with a
clear message when neither is installed.

```bash
python scripts/render_deck.py --input deck.pptx \
  [--output-dir output/<deck>_slides] [--renderer auto|libreoffice|powerpoint]
```

## scripts/extract_style.py

Learns the style guide and template library end to end.

```bash
python scripts/extract_style.py --input <source> \
  [--output style_guides/<deck>.json] \
  [--templates-output style_guides/<deck>_templates.json] \
  [--slides-dir output/<deck>_slides] [--use-vision]
```

`--use-vision` enriches the guide with Gemini descriptions of the rendered
slides and needs `GEMINI_API_KEY` in `.env`. PDF and PNG sources record measured
colours and leave fonts unknown, both with warnings.

## scripts/generate_deck.py

Plans a deck from a topic and notes and fills it in a learned style.

```bash
python scripts/generate_deck.py --topic "..." --style-guide style_guides/<deck>.json \
  [--templates style_guides/<deck>_templates.json] \
  [--audience "..."] [--goal "..."] [--tone "..."] \
  [--slides 5] [--notes "a fact"] [--notes "82% - of faculty want AI tools"] \
  [--output-dir output/generated] [--name <slug>] \
  [--use-llm] [--critique] [--use-vision] [--pptx]
```

Flags:

| Flag | Effect |
|---|---|
| `--use-llm` | Gemini drafts the outline and content; every draft is validated and falls back to deterministic on rejection |
| `--critique` | runs the bounded critique loop and writes `<name>_critique.json` |
| `--use-vision` | adds the Gemini visual critic inside the loop; needs `--critique` and sends slide images to Google |
| `--pptx` | also exports the editable PowerPoint file |

## Note formats the generator understands

`analyze_material()` sorts each `--notes` line into headings, bullets, stats and
quotes. Lines that fit no category go to `unusable` and are reported, never
silently shortened.

| You write | It becomes |
|---|---|
| `62% - of faculty want AI tools` | a stat slide (value `62%`, context `of faculty want AI tools`) |
| `> "The quote text" - Name, Role` | a quote slide with attribution |
| a short line of six words or fewer, no digits | a candidate heading / agenda item |
| anything longer | a candidate bullet |

Words are used verbatim; the generator never invents facts and never truncates
text to force a fit - over-budget material is skipped with a warning.

## scripts/demo.py

The whole flow in one command (the blueprint's end-to-end demo): parse the
source, learn the style guide and template library, plan and generate a deck,
optionally critique it, then export an editable `.pptx` plus an HTML preview.

```bash
python scripts/demo.py --input <source> --topic "..." \
  [--output output/demo.pptx] [--audience "..."] [--goal "..."] [--tone "..."] \
  [--slides 8] [--notes "..."] [--critique] [--use-llm]
```

Every intermediate artifact is kept under `output/<stem>_artifacts/`, and an
evidence panel prints the palette and typography actually used (with
provenance), the template chosen per slide, and every critique fix applied.

## scripts/make_fixture_deck.py

Writes the synthetic sample deck used by the test suite.

```bash
python scripts/make_fixture_deck.py [--output tests/fixtures/sample_deck.pptx]
```
