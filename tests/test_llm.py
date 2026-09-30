"""Offline tests for the text provider and the LLM generation paths
(blueprint §8, §14). No network calls: a scripted provider stands in for
Gemini, and the Gemini client itself is tested against a fake urlopen.

The contract under test: model output is accepted only when it passes the
same strict validation the deterministic path guarantees (schema, library
templates, word/capacity limits, grounded numbers and quotes); any
rejection falls back deterministically with a warning that says why.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from core.generation import (
    BulletBlock,
    DeckBrief,
    QuoteBlock,
    StatBlock,
    SubtitleBlock,
    analyze_material,
    generate_deck,
)
from core.llm import (
    GeminiTextProvider,
    TextProvider,
    TextProviderError,
    content_with_llm,
    plan_with_llm,
    text_provider_from_environment,
)
from core.style_guide import ContentRules, LayoutGrid, StyleGuide
from core.templates import ROLE_BODY, TemplateRecord, TemplateSlot
from core.vision import DEFAULT_GEMINI_MODEL, GEMINI_API_KEY_ENV, GEMINI_MODEL_ENV


# -- builders ---------------------------------------------------------------


def _slot(role, x=0.05, y=0.1, w=0.9, h=0.3, size=18.0):
    return TemplateSlot(
        role=role, shape_type="TEXT_BOX", x=x, y=y, width=w, height=h,
        font_size_pt=size,
    )


def _template(slide_type, template_id, slots):
    return TemplateRecord(
        template_id=template_id,
        slide_type=slide_type,
        source_deck="synthetic",
        source_slide_number=1,
        aspect_ratio="16:9",
        slots=slots,
    )


def _library():
    return [
        _template("title", "tpl_title", [_slot("title", h=0.25)]),
        _template(
            "bullets",
            "tpl_bullets",
            [_slot("title", y=0.05, h=0.15), _slot(ROLE_BODY, y=0.3, h=0.5)],
        ),
        _template("stat", "tpl_stat", [_slot("stat", h=0.4)]),
        _template("quote", "tpl_quote", [_slot("quote", h=0.4)]),
        _template("section_divider", "tpl_divider", [_slot("title", h=0.3)]),
    ]


def _style_guide(max_bullets=None, max_words=None, max_title=None):
    return StyleGuide(
        deck_id="synthetic-deck",
        deck_name="synthetic",
        extracted_at="2026-01-01T00:00:00+00:00",
        source_file_type="pptx",
        source_slide_count=6,
        aspect_ratio="16:9",
        layout_grid=LayoutGrid(slide_width_px=1920.0, slide_height_px=1080.0),
        content_rules=ContentRules(
            max_bullets_per_slide=max_bullets,
            max_words_per_bullet=max_words,
            max_title_length=max_title,
        ),
    )


def _brief(notes, slide_count=5, topic="Quarterly Review"):
    return DeckBrief(
        topic=topic,
        audience="Leadership",
        goal="Align on next quarter",
        slide_count=slide_count,
        notes=list(notes),
    )


NOTES = [
    "Revenue growth strategy",
    "Retention plans for next year",
    "Growth doubled after we changed the pricing model",
    "Support load fell with self serve docs",
    "> We ship every week — the engineering team",
    "82% — of teams ship weekly",
]


class ScriptedProvider:
    """Offline stand-in for a real text provider: returns canned responses
    in order and records the prompts it was asked. Never touches network."""

    name = "scripted"

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.prompts = []

    def is_configured(self):
        return True

    def generate_text(self, prompt):
        self.prompts.append(prompt)
        if not self.responses:
            raise TextProviderError("scripted provider ran out of responses")
        return self.responses.pop(0)


OUTLINE_OK = json.dumps(
    {
        "slides": [
            {"slide_type": "title", "title": "Quarterly Review",
             "subtitle": "Align on next quarter"},
            {"slide_type": "bullets", "title": "What moved"},
            {"slide_type": "stat", "title": "The number that matters"},
            {"slide_type": "quote", "title": "In their words"},
            {"slide_type": "section_divider", "title": "Next steps"},
        ]
    }
)

CONTENT_OK = json.dumps(
    {
        "slides": [
            {"title": "Quarterly Review",
             "content": [{"type": "subtitle", "text": "Align on next quarter"}]},
            {"title": "What moved",
             "content": [
                 {"type": "bullet",
                  "text": "Growth doubled after we changed the pricing model"},
                 {"type": "bullet",
                  "text": "Support load fell with self serve docs"},
             ]},
            {"title": "The number that matters",
             "content": [{"type": "stat", "value": "82%",
                          "context": "of teams ship weekly"}]},
            {"content": [{"type": "quote", "text": "We ship every week",
                          "attribution": "the engineering team"}]},
            {"title": "Next steps",
             "content": [{"type": "subtitle", "text": "Align on next quarter"}]},
        ]
    }
)

# The deterministic planner orders this material as:
# title -> quote -> stat -> bullets -> section_divider.
CONTENT_DETERMINISTIC_ORDER = json.dumps(
    {
        "slides": [
            {"title": "Quarterly Review",
             "content": [{"type": "subtitle", "text": "Align on next quarter"}]},
            {"content": [{"type": "quote", "text": "We ship every week",
                          "attribution": "the engineering team"}]},
            {"content": [{"type": "stat", "value": "82%",
                          "context": "of teams ship weekly"}]},
            {"title": "Key points",
             "content": [
                 {"type": "bullet",
                  "text": "Growth doubled after we changed the pricing model"},
                 {"type": "bullet",
                  "text": "Support load fell with self serve docs"},
             ]},
            {"title": "Next steps",
             "content": [{"type": "subtitle", "text": "Align on next quarter"}]},
        ]
    }
)


def _material(max_words=None):
    return analyze_material(NOTES, max_words)


# -- provider contract ------------------------------------------------------


def test_scripted_and_gemini_satisfy_the_text_provider_protocol():
    assert isinstance(ScriptedProvider(), TextProvider)
    assert isinstance(GeminiTextProvider(api_key="k"), TextProvider)


# -- Gemini client (offline, fake urlopen) ----------------------------------


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


def test_generate_text_sends_prompt_and_returns_text(monkeypatch):
    monkeypatch.delenv(GEMINI_MODEL_ENV, raising=False)
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        return _FakeResponse(_gemini_body("model says hi"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = GeminiTextProvider(api_key="dummy-key")
    assert provider.generate_text("hello") == "model says hi"

    request = requests[0]
    assert DEFAULT_GEMINI_MODEL in request.full_url
    assert ":generateContent" in request.full_url
    assert request.get_header("X-goog-api-key") == "dummy-key"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["contents"][0]["parts"][0]["text"] == "hello"
    assert payload["generationConfig"]["temperature"] == 0


def test_generate_text_without_key_fails_loudly(monkeypatch):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    with pytest.raises(TextProviderError, match="aistudio.google.com/apikey"):
        GeminiTextProvider().generate_text("hello")


def test_generate_text_empty_prompt_fails():
    with pytest.raises(TextProviderError, match="empty prompt"):
        GeminiTextProvider(api_key="k").generate_text("   ")


def test_generate_text_http_error_fails_loudly(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError("http://x", 429, "rate limited", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(TextProviderError, match="HTTP 429"):
        GeminiTextProvider(api_key="k").generate_text("hello")


def test_generate_text_connection_error_fails_loudly(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(TextProviderError, match="request failed"):
        GeminiTextProvider(api_key="k").generate_text("hello")


def test_extract_text_missing_candidates_raises():
    with pytest.raises(TextProviderError, match="missing candidate text"):
        GeminiTextProvider(api_key="k")._extract_text({})


def test_extract_text_empty_response_raises():
    body = {"candidates": [{"content": {"parts": [{"text": "  "}]}}]}
    with pytest.raises(TextProviderError, match="empty response"):
        GeminiTextProvider(api_key="k")._extract_text(body)


def test_text_provider_from_environment(monkeypatch):
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)
    assert text_provider_from_environment() is None
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "env-key")
    found = text_provider_from_environment()
    assert isinstance(found, GeminiTextProvider)
    assert found.is_configured() is True


# -- outline planning via LLM ------------------------------------------------


class TestPlanWithLlm:
    def test_valid_outline_is_used(self):
        provider = ScriptedProvider([OUTLINE_OK])
        plan, warnings = plan_with_llm(
            provider, _brief(NOTES), _library(), _material()
        )
        assert warnings == []
        assert plan.planner == "llm"
        assert [s.slide_type for s in plan.slides] == [
            "title", "bullets", "stat", "quote", "section_divider",
        ]
        assert [s.template_id for s in plan.slides] == [
            "tpl_title", "tpl_bullets", "tpl_stat", "tpl_quote", "tpl_divider",
        ]
        assert plan.slides[0].title == "Quarterly Review"
        assert plan.slides[1].intent == "Present key points as a scannable list"
        assert [s.slide_number for s in plan.slides] == [1, 2, 3, 4, 5]
        prompt = provider.prompts[0]
        assert "5 slides" in prompt
        assert "section_divider" in prompt
        assert "82%" in prompt

    def test_unknown_slide_type_falls_back(self):
        bad = json.dumps({"slides": [
            {"slide_type": "title", "title": "T"},
            {"slide_type": "timeline", "title": "Nope"},
        ]})
        plan, warnings = plan_with_llm(
            ScriptedProvider([bad]), _brief(NOTES, slide_count=2), _library(),
            _material(),
        )
        assert plan.planner == "deterministic"
        assert any("rejected" in w and "timeline" in w for w in warnings)

    def test_adjacent_repeats_fall_back(self):
        bad = json.dumps({"slides": [
            {"slide_type": "bullets", "title": "One"},
            {"slide_type": "bullets", "title": "Two"},
        ]})
        plan, warnings = plan_with_llm(
            ScriptedProvider([bad]), _brief(NOTES, slide_count=2), _library(),
            _material(),
        )
        assert plan.planner == "deterministic"
        assert any("rejected" in w and "in a row" in w for w in warnings)

    def test_wrong_slide_count_falls_back(self):
        bad = json.dumps({"slides": [
            {"slide_type": "title", "title": "Only one"},
        ]})
        plan, warnings = plan_with_llm(
            ScriptedProvider([bad]), _brief(NOTES, slide_count=5), _library(),
            _material(),
        )
        assert plan.planner == "deterministic"
        assert any("rejected" in w and "expected 5" in w for w in warnings)

    def test_malformed_json_falls_back(self):
        plan, warnings = plan_with_llm(
            ScriptedProvider(["I could not produce JSON today"]),
            _brief(NOTES), _library(), _material(),
        )
        assert plan.planner == "deterministic"
        assert any("no JSON object" in w for w in warnings)

    def test_provider_failure_falls_back(self):
        plan, warnings = plan_with_llm(
            ScriptedProvider([]), _brief(NOTES), _library(), _material()
        )
        assert plan.planner == "deterministic"
        assert any("ran out of responses" in w for w in warnings)

    def test_empty_library_never_calls_the_provider(self):
        provider = ScriptedProvider([OUTLINE_OK])
        plan, warnings = plan_with_llm(
            provider, _brief(NOTES), [], _material()
        )
        assert provider.prompts == []
        assert provider.responses == [OUTLINE_OK]
        assert plan.planner == "deterministic"
        assert any("no templates" in w for w in warnings)


# -- content generation via LLM ----------------------------------------------


class TestContentWithLlm:
    def _plan(self):
        plan, _ = plan_with_llm(
            ScriptedProvider([OUTLINE_OK]), _brief(NOTES), _library(),
            _material(),
        )
        return plan

    def _run(self, response):
        return content_with_llm(
            ScriptedProvider([response]), _brief(NOTES), self._plan(),
            _library(), _material(), _style_guide(),
        )

    def test_valid_content_is_used(self):
        slides, used_llm, warnings = self._run(CONTENT_OK)
        assert used_llm is True
        assert warnings == []
        assert [s.slide_type for s in slides] == [
            "title", "bullets", "stat", "quote", "section_divider",
        ]
        assert isinstance(slides[1].content[0], BulletBlock)
        assert slides[1].content[0].text == (
            "Growth doubled after we changed the pricing model"
        )
        assert isinstance(slides[2].content[0], StatBlock)
        assert slides[2].content[0].value == "82%"
        assert isinstance(slides[3].content[0], QuoteBlock)
        assert slides[3].title is None
        assert isinstance(slides[4].content[0], SubtitleBlock)

    def test_fabricated_number_falls_back_for_whole_deck(self):
        bad = CONTENT_OK.replace("Support load fell with self serve docs",
                                 "Cut churn by 37% this year")
        slides, used_llm, warnings = self._run(bad)
        assert used_llm is False
        assert any("rejected" in w and "37%" in w for w in warnings)
        # the deck is the deterministic one: it reuses note material verbatim
        assert any(
            "Growth doubled after we changed the pricing model" in b.text
            for s in slides for b in s.content if isinstance(b, BulletBlock)
        )

    def test_ungrounded_quote_falls_back(self):
        bad = CONTENT_OK.replace('"We ship every week"',
                                 '"We move fast and break things"')
        _, used_llm, warnings = self._run(bad)
        assert used_llm is False
        assert any("rejected" in w and "not found in the notes" in w for w in warnings)

    def test_ungrounded_stat_value_falls_back(self):
        bad = CONTENT_OK.replace('"value": "82%"', '"value": "95%"')
        _, used_llm, warnings = self._run(bad)
        assert used_llm is False
        assert any("rejected" in w and "95%" in w for w in warnings)

    def test_bullet_over_word_limit_falls_back(self):
        too_long = " ".join(["word"] * 13)
        bad = CONTENT_OK.replace("Support load fell with self serve docs", too_long)
        _, used_llm, warnings = self._run(bad)
        assert used_llm is False
        assert any("rejected" in w and "13 words" in w for w in warnings)

    def test_too_many_bullets_falls_back(self):
        bad = json.loads(CONTENT_OK)
        bad["slides"][1]["content"] = [
            {"type": "bullet", "text": "One small fix"} for _ in range(4)
        ]
        _, used_llm, warnings = self._run(json.dumps(bad))
        assert used_llm is False
        assert any("rejected" in w and "4 bullets" in w for w in warnings)

    def test_wrong_slide_count_falls_back(self):
        _, used_llm, warnings = self._run('{"slides": []}')
        assert used_llm is False
        assert any("rejected" in w and "expected 5" in w for w in warnings)

    def test_title_slide_without_title_falls_back(self):
        bad = json.loads(CONTENT_OK)
        del bad["slides"][0]["title"]
        _, used_llm, warnings = self._run(json.dumps(bad))
        assert used_llm is False
        assert any("rejected" in w and "title slide" in w for w in warnings)

    def test_grounded_number_in_bullet_is_accepted(self):
        ok = json.loads(CONTENT_OK)
        ok["slides"][1]["content"].append(
            {"type": "bullet", "text": "82% of teams ship weekly"}
        )
        _, used_llm, warnings = self._run(json.dumps(ok))
        assert used_llm is True
        assert warnings == []

    def test_max_title_violation_falls_back(self):
        slides, used_llm, warnings = content_with_llm(
            ScriptedProvider([CONTENT_OK]), _brief(NOTES), self._plan(),
            _library(), _material(), _style_guide(max_title=8),
        )
        assert used_llm is False
        assert any("rejected" in w and "exceeds the learned max" in w for w in warnings)

    def test_provider_failure_falls_back(self):
        _, used_llm, warnings = content_with_llm(
            ScriptedProvider([]), _brief(NOTES), self._plan(),
            _library(), _material(), _style_guide(),
        )
        assert used_llm is False
        assert any("ran out of responses" in w for w in warnings)


# -- end to end through generate_deck ---------------------------------------


class TestGenerateDeckWithLlm:
    def test_both_stages_llm_marks_the_deck_gemini(self):
        deck = generate_deck(
            _brief(NOTES), _library(), _style_guide(),
            text_provider=ScriptedProvider([OUTLINE_OK, CONTENT_OK]),
        )
        assert deck.generator == "gemini"
        assert deck.plan.planner == "llm"
        assert len(deck.slides) == 5
        assert deck.warnings == []
        library_ids = {t.template_id for t in _library()}
        assert all(s.template_id in library_ids for s in deck.slides)

    def test_content_fallback_keeps_llm_plan_but_deterministic_content(self):
        deck = generate_deck(
            _brief(NOTES), _library(), _style_guide(),
            text_provider=ScriptedProvider(
                [OUTLINE_OK, CONTENT_OK.replace("82%", "95%")]
            ),
        )
        assert deck.generator == "deterministic"
        assert deck.plan.planner == "llm"
        assert any("rejected" in w for w in deck.warnings)

    def test_plan_fallback_still_allows_llm_content_on_deterministic_plan(self):
        bad_outline = json.dumps({"slides": [
            {"slide_type": "timeline", "title": "Nope"},
        ]})
        deck = generate_deck(
            _brief(NOTES), _library(), _style_guide(),
            text_provider=ScriptedProvider(
                [bad_outline, CONTENT_DETERMINISTIC_ORDER]
            ),
        )
        assert deck.generator == "gemini"
        assert deck.plan.planner == "deterministic"
        assert [s.slide_type for s in deck.slides] == [
            "title", "quote", "stat", "bullets", "section_divider",
        ]
        assert any("rejected" in w for w in deck.warnings)
