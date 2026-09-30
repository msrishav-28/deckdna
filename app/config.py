"""Paths and limits for the web app.

Everything the app stores at runtime lives under the repository's
git-ignored directories (output/, style_guides/), matching the blueprint's
security rules: uploaded decks and extracted output are never committed.
Tests monkeypatch these attributes to run against temporary directories.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

UPLOADS_DIR = REPO_ROOT / "output" / "uploads"
GENERATED_DIR = REPO_ROOT / "output" / "generated"
STYLE_GUIDES_DIR = REPO_ROOT / "style_guides"
DB_PATH = REPO_ROOT / "output" / "deckdna.sqlite3"

# Decks are small files; anything beyond this is rejected instead of
# silently buffered in memory.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
