"""Shared pytest fixtures: repo path setup and the synthetic sample deck."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.make_fixture_deck import build_deck  # noqa: E402


@pytest.fixture(scope="session")
def fixture_deck(tmp_path_factory):
    """Build the synthetic sample deck once per test session."""
    path = tmp_path_factory.mktemp("fixture") / "sample_deck.pptx"
    build_deck().save(str(path))
    return path


@pytest.fixture(scope="session")
def inventory(fixture_deck):
    from core.pptx_parser import DeckParser

    return DeckParser().parse(fixture_deck)
