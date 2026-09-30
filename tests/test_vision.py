"""Offline tests for the vision adapters: response parsing, configuration
gating, input validation, the minimal .env reader, and the bounded visual
critic. No network calls — the HTTP-level critic tests use a fake urlopen."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from core.critique import SlideCritic
from core.vision import (
    DEFAULT_GEMINI_MODEL,
    GEMINI_API_KEY_ENV,
    GEMINI_MODEL_ENV,
    GeminiSlideCritic,
    GeminiVisionProvider,
    VisionProviderError,
    critic_from_environment,
    load_dotenv,
    provider_from_environment,
)


@pytest.fixture
def provider():
    return GeminiVisionProvider(api_key="dummy-key")


@pytest.fixture
def critic():
    return GeminiSlideCritic(api_key="dummy-key")


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


# -- visual critic adapter (blueprint §8.5) ---------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _gemini_body(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _critique_json(suggestions, approved=False, scores=None):
    return json.dumps(
        {
            "scores": scores if scores is not None else {"no_overflow": 70},
            "approved": approved,
            "suggestions": suggestions,
        }
    )


def test_parse_critique_clean_json(critic):
    payload = critic._parse_critique(
        '{"scores": {"no_overflow": 88, "no_overlap": 92.4, "alignment": 75}, '
        '"approved": false, '
        '"suggestions": [{"element_id": "slide_01-body", '
        '"action": "reduce_font_size", "scale": 0.9, "reason": "cramped"}]}'
    )
    assert payload["approved"] is False
    assert payload["scores"] == {"no_overflow": 88, "no_overlap": 92, "alignment": 75}
    assert payload["suggestions"] == [
        {
            "element_id": "slide_01-body",
            "action": "reduce_font_size",
            "scale": 0.9,
            "reason": "cramped",
        }
    ]


def test_parse_critique_filters_and_clamps_scores(critic):
    payload = critic._parse_critique(
        '{"scores": {"no_overflow": 150, "alignment": -5, "density": true, '
        '"spacing": "high", "made_up": 80}, "suggestions": []}'
    )
    assert payload["scores"] == {"no_overflow": 100, "alignment": 0}


def test_parse_critique_approved_defaults_to_false(critic):
    payload = critic._parse_critique('{"suggestions": []}')
    assert payload["approved"] is False
    assert payload["scores"] == {}


def test_parse_critique_extracts_json_from_fenced_text(critic):
    text = '```json\n{"approved": true, "suggestions": []}\n```'
    assert critic._parse_critique(text)["approved"] is True


def test_parse_critique_without_json_raises(critic):
    with pytest.raises(VisionProviderError, match="no JSON object"):
        critic._parse_critique("the slide looks fine, really")


def test_parse_critique_with_invalid_json_raises(critic):
    with pytest.raises(VisionProviderError, match="not valid JSON"):
        critic._parse_critique('{"suggestions": }')


def test_parse_critique_missing_suggestions_raises(critic):
    with pytest.raises(VisionProviderError, match="suggestions"):
        critic._parse_critique('{"approved": true}')


def test_parse_critique_suggestions_not_a_list_raises(critic):
    with pytest.raises(VisionProviderError, match="suggestions"):
        critic._parse_critique('{"suggestions": {"element_id": "x"}}')


def test_parse_critique_keeps_malformed_suggestions_for_the_loop(critic):
    payload = critic._parse_critique(
        '{"suggestions": [42, {"element_id": "slide_01-body"}]}'
    )
    assert payload["suggestions"] == [42, {"element_id": "slide_01-body"}]


def test_critique_slide_posts_images_and_parses(monkeypatch, tmp_path):
    monkeypatch.delenv(GEMINI_MODEL_ENV, raising=False)
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        return _FakeResponse(
            _gemini_body(
                _critique_json(
                    [
                        {
                            "element_id": "slide_01-body",
                            "action": "resize_element",
                            "dh": 0.05,
                            "dy": 0.0,
                            "reason": "text is cramped",
                        }
                    ]
                )
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    critic = GeminiSlideCritic(api_key="dummy-key")
    generated = tmp_path / "slide_01.png"
    generated.write_bytes(b"generated")
    reference = tmp_path / "slide_01_reference.png"
    reference.write_bytes(b"reference")
    payload = critic.critique_slide(
        generated,
        reference,
        {
            "deck_title": "Synthetic deck",
            "slide_id": "slide_01",
            "slide_type": "bullets",
            "elements": [
                {"element_id": "slide_01-body", "kind": "bullets", "text": "One"}
            ],
        },
    )

    assert payload["scores"] == {"no_overflow": 70}
    assert payload["approved"] is False
    assert payload["suggestions"] == [
        {
            "element_id": "slide_01-body",
            "action": "resize_element",
            "dh": 0.05,
            "dy": 0.0,
            "reason": "text is cramped",
        }
    ]

    request = requests[0]
    assert DEFAULT_GEMINI_MODEL in request.full_url
    assert request.get_header("X-goog-api-key") == "dummy-key"
    body = json.loads(request.data.decode("utf-8"))
    assert body["generationConfig"]["temperature"] == 0
    parts = body["contents"][0]["parts"]
    prompt = parts[0]["text"]
    assert "slide_01-body" in prompt
    assert "Synthetic deck" in prompt
    for phrase in (
        "reduce_font_size",
        "resize_element",
        "remove_low_priority_bullet",
        "no_overflow",
        "template_fidelity",
        "variety",
        "0.50 and 0.98",
        "0.01 and 0.30",
        "never propose rewrites",
    ):
        assert phrase in prompt
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert parts[2]["text"] == "Reference template image:"
    assert parts[3]["inline_data"]["mime_type"] == "image/png"


def test_critique_slide_without_reference_posts_one_image(monkeypatch, tmp_path):
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        return _FakeResponse(
            _gemini_body(_critique_json([], approved=True, scores={}))
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    generated = tmp_path / "slide_01.png"
    generated.write_bytes(b"generated")
    payload = GeminiSlideCritic(api_key="k").critique_slide(
        generated, None, {"elements": []}
    )
    assert payload == {"approved": True, "scores": {}, "suggestions": []}
    parts = json.loads(requests[0].data.decode("utf-8"))["contents"][0]["parts"]
    assert len(parts) == 2


def test_critique_slide_without_key_fails_loudly(monkeypatch, tmp_path):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    image = tmp_path / "slide_001.png"
    image.write_bytes(b"not actually sent anywhere")
    with pytest.raises(VisionProviderError, match="aistudio.google.com/apikey"):
        GeminiSlideCritic().critique_slide(image, None, {})


def test_critique_slide_missing_image_fails(critic, tmp_path):
    with pytest.raises(VisionProviderError, match="not found"):
        critic.critique_slide(tmp_path / "missing.png", None, {})


def test_critique_slide_missing_reference_fails(critic, tmp_path):
    generated = tmp_path / "slide_01.png"
    generated.write_bytes(b"generated")
    with pytest.raises(VisionProviderError, match="not found"):
        critic.critique_slide(generated, tmp_path / "missing_ref.png", {})


def test_critique_slide_unsupported_type_fails(critic, tmp_path):
    image = tmp_path / "slide_01.bmp"
    image.write_bytes(b"not an image")
    with pytest.raises(VisionProviderError, match="unsupported slide image type"):
        critic.critique_slide(image, None, {})


def test_critique_slide_http_error_fails_loudly(monkeypatch, tmp_path):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError("http://x", 429, "rate limited", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    image = tmp_path / "slide_01.png"
    image.write_bytes(b"generated")
    with pytest.raises(VisionProviderError, match="HTTP 429"):
        GeminiSlideCritic(api_key="k").critique_slide(image, None, {})


def test_critic_conforms_to_the_loop_protocol():
    assert isinstance(GeminiSlideCritic(api_key="k"), SlideCritic)


def test_critic_name_and_model_default(monkeypatch):
    monkeypatch.delenv(GEMINI_MODEL_ENV, raising=False)
    critic = GeminiSlideCritic(api_key="k")
    assert critic.model == DEFAULT_GEMINI_MODEL
    assert critic.name == f"gemini-critic:{DEFAULT_GEMINI_MODEL}"


def test_critic_from_environment(monkeypatch):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    assert critic_from_environment() is None
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "env-key")
    found = critic_from_environment()
    assert isinstance(found, GeminiSlideCritic)
    assert found.is_configured() is True
