"""Offline tests for the vision adapter: response parsing, configuration
gating, input validation, and the minimal .env reader. No network calls."""

from __future__ import annotations

import pytest

from core.vision import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_API_KEY_ENV,
    GEMINI_MODEL_ENV,
    GeminiVisionProvider,
    VisionProviderError,
    load_dotenv,
    provider_from_environment,
)


@pytest.fixture
def provider():
    return GeminiVisionProvider(api_key="dummy-key")


# -- response parsing ------------------------------------------------------


def test_parse_notes_clean_json(provider):
    notes = provider._parse_notes(
        '{"background_style": "gradient", '
        '"dominant_colors": ["#0b1f3a", "#18A999"], '
        '"notes": ["dense chart", "dark background"]}'
    )
    assert notes.background_style == "gradient"
    assert notes.dominant_colors == ["#0B1F3A", "#18A999"]
    assert notes.notes == ["dense chart", "dark background"]
    assert notes.raw_text.startswith("{")


def test_parse_notes_dedupes_uppercases_and_filters(provider):
    notes = provider._parse_notes(
        '{"background_style": "solid", '
        '"dominant_colors": ["#0b1f3a", "#0B1F3A", "red", "#18a999"], '
        '"notes": ["a", "", "b"]}'
    )
    assert notes.dominant_colors == ["#0B1F3A", "#18A999"]
    assert notes.notes == ["a", "b"]


def test_parse_notes_caps_lists_at_five(provider):
    payload = (
        '{"background_style": "unknown", '
        '"dominant_colors": ["#111111", "#222222", "#333333", "#444444", '
        '"#555555", "#666666"], '
        '"notes": ["1", "2", "3", "4", "5", "6"]}'
    )
    notes = provider._parse_notes(payload)
    assert len(notes.dominant_colors) == 5
    assert len(notes.notes) == 5


def test_parse_notes_rejects_unknown_background(provider):
    notes = provider._parse_notes('{"background_style": "neon"}')
    assert notes.background_style is None


def test_parse_notes_extracts_json_from_fenced_text(provider):
    text = '```json\n{"background_style": "image", "dominant_colors": []}\n```'
    notes = provider._parse_notes(text)
    assert notes.background_style == "image"


def test_parse_notes_without_json_raises(provider):
    with pytest.raises(VisionProviderError, match="no JSON object"):
        provider._parse_notes("the slide looks nice but there is no object")


def test_parse_notes_with_invalid_json_raises(provider):
    with pytest.raises(VisionProviderError, match="not valid JSON"):
        provider._parse_notes('{"background_style": }')


def test_extract_text_joins_parts(provider):
    body = {
        "candidates": [
            {"content": {"parts": [{"text": "hello "}, {"text": "world"}]}}
        ]
    }
    assert provider._extract_text(body) == "hello world"


def test_extract_text_missing_candidates_raises(provider):
    with pytest.raises(VisionProviderError, match="missing candidate text"):
        provider._extract_text({})


def test_extract_text_empty_response_raises(provider):
    body = {"candidates": [{"content": {"parts": [{"text": "  "}]}}]}
    with pytest.raises(VisionProviderError, match="empty description"):
        provider._extract_text(body)


# -- configuration and input validation (no network) ----------------------


def test_is_configured_without_key(monkeypatch):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    assert GeminiVisionProvider().is_configured() is False


def test_is_configured_with_key(monkeypatch):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    assert GeminiVisionProvider(api_key="k").is_configured() is True
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "env-key")
    assert GeminiVisionProvider().is_configured() is True


def test_provider_name_and_model_default(monkeypatch):
    monkeypatch.delenv(GEMINI_MODEL_ENV, raising=False)
    assert GeminiVisionProvider(api_key="k").model == DEFAULT_GEMINI_MODEL
    assert GeminiVisionProvider(api_key="k").name == f"gemini:{DEFAULT_GEMINI_MODEL}"


def test_analyze_slide_without_key_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    image = tmp_path / "slide_001.png"
    image.write_bytes(b"not actually sent anywhere")
    with pytest.raises(VisionProviderError, match="aistudio.google.com/apikey"):
        GeminiVisionProvider().analyze_slide(image, slide_number=1)


def test_analyze_slide_missing_file_fails(provider, tmp_path):
    with pytest.raises(VisionProviderError, match="not found"):
        provider.analyze_slide(tmp_path / "missing.png")


def test_analyze_slide_unsupported_type_fails(provider, tmp_path):
    image = tmp_path / "slide_001.txt"
    image.write_text("not an image")
    with pytest.raises(VisionProviderError, match="unsupported slide image type"):
        provider.analyze_slide(image)


# -- .env reader -----------------------------------------------------------


def test_load_dotenv_reads_pairs(monkeypatch, tmp_path):
    for key in ("DECKDNA_TEST_A", "DECKDNA_TEST_B", "DECKDNA_TEST_C"):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment line\n"
        "DECKDNA_TEST_A=plain\n"
        'DECKDNA_TEST_B="quoted"\n'
        "DECKDNA_TEST_C='single'\n"
        "\n"
        "line without an equals sign\n",
        encoding="utf-8",
    )
    loaded = load_dotenv(env_file)
    assert sorted(loaded) == ["DECKDNA_TEST_A", "DECKDNA_TEST_B", "DECKDNA_TEST_C"]
    import os

    assert os.environ["DECKDNA_TEST_A"] == "plain"
    assert os.environ["DECKDNA_TEST_B"] == "quoted"
    assert os.environ["DECKDNA_TEST_C"] == "single"


def test_load_dotenv_env_wins_over_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DECKDNA_TEST_A", "from-env")
    env_file = tmp_path / ".env"
    env_file.write_text("DECKDNA_TEST_A=from-file\n", encoding="utf-8")
    loaded = load_dotenv(env_file)
    import os

    assert loaded == []
    assert os.environ["DECKDNA_TEST_A"] == "from-env"


def test_load_dotenv_missing_file_returns_empty(tmp_path):
    assert load_dotenv(tmp_path / "absent.env") == []


def test_provider_from_environment(monkeypatch):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    assert provider_from_environment() is None
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "env-key")
    found = provider_from_environment()
    assert isinstance(found, GeminiVisionProvider)
    assert found.is_configured() is True
