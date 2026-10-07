# Style learning

This page explains how DeckDNA turns a source file into a reusable design
system, what it can learn from each source type, and what it honestly cannot.

## The mechanism

DeckDNA does not train a model on your decks. It reads each deck's structure,
classifies every slide, records each slide's layout as a template, and derives
a style guide from the file's own data. Generation later retrieves those
templates and fills them with your content - retrieval plus constrained
generation, not fine-tuning.

## What each source type contributes

| Capability | `.pptx` | `.pdf` (text layer) | `.png` / PNG folder |
|---|---|---|---|
| Typed text slots | yes | yes | no |
| Native fonts | yes | partial | no |
| Authored colours | yes (theme + shapes) | no | no |
| Measured colours | n/a | yes (pixels) | yes (pixels) |
| Exact geometry | yes | yes | full-bleed image only |
| Theme / margins | yes | no | no |
| Can drive generation | yes | yes | no |

The measured colours of PDF/PNG sources are counted exactly from rendered
pixels by `core/pixel_sampling.py`, not sampled randomly, so flat vector
designs reproduce their true colour frequencies. Because image-only sources
carry no typed text, every template learned from them holds only a picture
placeholder; style learning works, but generating a deck needs a `.pptx` or a
text-layer PDF, and the tools stop loudly rather than producing an empty file.

## How the style guide is derived

`StyleGuideExtractor.extract()` runs one deterministic rule per section:

- **Palette.** Counts shape fills, lines, background and text colours, then
  assigns each a usage (primary, background, accent, text) from saturation and
  lightness thresholds. Measured colours are reported separately with their own
  provenance.
- **Typography.** Reads font family, size, weight and colour for title and body
  text from the source's own runs. Fonts that cannot be read (PDF/PNG) stay
  `None` with a warning.
- **Margins and layout grid.** Derived from the outermost content extents.
  Column count and gutter width are not inferred and stay empty with a warning.
- **Element treatments.** Card corner radius, shadow, border width and
  background style are recorded when the source declares them.
- **Content rules.** Density is classified low/medium/high from the word counts
  of real slides, and bullets-per-slide and words-per-bullet limits are learned
  from the observed maximums (clamped to safe floors).

Every rule is documented in `core/extractor.py`; anything the data cannot
support stays empty with a warning instead of a guess.

## Optional vision enrichment

With `GEMINI_API_KEY` set, `--use-vision` sends rendered slide images to
Gemini and merges the descriptions into the guide. Vision facts are tagged
`vision` and merged only into fields native data cannot fill (image
backgrounds, gradients, overall look); they never overwrite a native fact. The
key is read from `.env` at runtime and is never committed.

## Editing the learned guide

In the web app, `PATCH /v1/style-guides/{id}` applies merge-only overrides and
appends each patch to an audit trail, so the guide can always be traced back to
its sources. Learned provenance fields are never rewritten by an override.

## What stays unknown

- Fonts of PDF and PNG sources.
- Column count and gutter width.
- Chart internals, table cells and vector drawings inside any source.
- The physical intent behind a colour (usage labels are heuristic).
