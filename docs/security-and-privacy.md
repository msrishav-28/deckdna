# Security and privacy

Decks may contain confidential business material, so privacy is a core design
constraint, not an afterthought.

## Local by default

The deterministic pipeline - parsing, classification, template and style
learning, generation, layout, critique, HTML preview and PPTX export - runs
entirely on your machine. No data leaves the machine for any of it. The web app
binds to `127.0.0.1` (localhost only) and is not exposed to a network.

## What leaves the machine, and only when you opt in

The only network calls are the opt-in Google Gemini adapters, and each is
explicit:

| Adapter | Sent to Google | Trigger |
|---|---|---|
| Vision style enrichment | rendered slide images | `--use-vision` on `extract_style.py` |
| Visual critic | rendered slide images of the generated deck | `--use-vision` with `--critique` |
| LLM drafting | your topic, notes and material summary | `--use-llm` |

The CLI help and README state that slide images or content are sent to Google.
Without `GEMINI_API_KEY`, all three are skipped and the pipeline runs locally
unchanged.

## Secrets and repository hygiene

- `.env` is gitignored and never committed; the key is read at runtime only.
- `.gitignore` excludes `sample_decks/`, `style_guides/`, `output/` and
  `temp/` (keeping `.gitkeep` placeholders), so user decks and extracted output
  never reach the repository.
- No tool logs full source content.
- Uploaded file content is stored on disk under `output/uploads/`, never in
  the SQLite database, which holds only identifiers, statuses, counts and
  artifact paths.

## Fail-closed behaviour

- Tools exit with a clear error rather than fabricating output: no renderer
  installed, an unparseable source, or a source with no typed text all fail
  loudly.
- API requests with unknown fields, oversized uploads or disallowed suffixes
  are rejected.
- Ids are validated against a strict pattern before they are used to build
  file paths.

## Known gaps before any public launch

- The web app has no authentication or delete/export-data controls; it is a
  local single-user tool. Do not expose it to a network without adding both.
- The opt-in Gemini features send data to a third party; a local-model path is
  a planned alternative for privacy-sensitive users.
- Obtain the rights to ingest or learn from any client deck or third-party
  template before doing so.

These track blueprint section 16 (Security, Privacy, and Rights).
