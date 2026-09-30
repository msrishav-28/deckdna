"""Vision inference plug-in point (blueprint §6.5).

Native PPTX data is the primary truth. Vision providers describe rendered
slide images only to fill gaps native data cannot (image backgrounds,
gradients, overall look). Providers must fail loudly — never fabricate a
description — and every downstream value they produce is tagged with
vision provenance by the extractor.

The same module hosts the bounded visual critic (blueprint §8.5) that
core.critique's loop drives: it scores a rendered slide against explicit
criteria and returns machine-readable repair suggestions, which the loop
re-validates before anything is applied.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Tuple, runtime_checkable

from pydantic import BaseModel, Field

GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_MODEL_ENV = "GEMINI_MODEL"
DEFAULT_GEMINI_MODEL = "gemini-2.0-flash"

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

_ALLOWED_BACKGROUND_STYLES = {"solid", "gradient", "image", "unknown"}
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MAX_COLORS = 5
_MAX_NOTES = 5

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

_PROMPT = (
    "You are analyzing one rendered presentation slide image. Describe only what "
    "is visually present; never guess about content you cannot see. Respond with "
    "ONLY a JSON object, no markdown fences, no explanation, in this exact shape:\n"
    '{"background_style": "solid" | "gradient" | "image" | "unknown",\n'
    ' "dominant_colors": ["#RRGGBB", ...],\n'
    ' "notes": ["short visual observations"]}\n'
    "Rules: dominant_colors holds at most 5 uppercase hex colors that dominate the "
    "slide; notes holds at most 5 short strings about visual style (density, "
    "imagery, texture). Use \"unknown\" when the background style is unclear."
)

_MISSING_KEY_MESSAGE = (
    "Gemini API key not set. Create a free key at "
    "https://aistudio.google.com/apikey, then put "
    "GEMINI_API_KEY=your-key in a .env file (or the environment)."
)

_CRITIC_CRITERIA = (
    "no_overflow",
    "no_overlap",
    "alignment",
    "typography",
    "spacing",
    "palette",
    "density",
    "template_fidelity",
    "readability",
    "variety",
)


class VisionProviderError(Exception):
    """Raised when a vision provider cannot produce an honest description."""


class SlideVisionNotes(BaseModel):
    slide_number: Optional[int] = None
    background_style: Optional[str] = None
    dominant_colors: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    raw_text: Optional[str] = None


@runtime_checkable
class VisionProvider(Protocol):
    name: str

    def is_configured(self) -> bool: ...

    def analyze_slide(
        self, image_path: Path, slide_number: Optional[int] = None
    ) -> SlideVisionNotes: ...


def load_dotenv(path: Optional[Path] = None) -> List[str]:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Existing environment values always win so a real env var is never
    overridden by a file. Minimal reader on purpose: no dependency needed.
    """
    env_path = Path(path) if path is not None else Path.cwd() / ".env"
    if not env_path.is_file():
        return []
    loaded: List[str] = []
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def _inline_image(path: Path) -> dict:
    """A validated base64 inline_data part for one slide image."""
    path = Path(path)
    if not path.is_file():
        raise VisionProviderError(f"slide image not found: {path}")
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
    if mime is None:
        raise VisionProviderError(
            f"unsupported slide image type '{path.suffix}' (expected png/jpg/webp)"
        )
    return {
        "inline_data": {
            "mime_type": mime,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    }


def _critique_prompt(context: dict) -> str:
    elements = context.get("elements")
    if not isinstance(elements, list):
        elements = []
    return (
        "You are a visual design critic for one presentation slide of the "
        f"deck '{context.get('deck_title', '')}'. The first image is the "
        "rendered slide under critique; if a second image follows, it is the "
        "reference template for style comparison. Judge only what is visibly "
        "present; never guess. Score 0-100 for each criterion: "
        f"{', '.join(_CRITIC_CRITERIA)}. Identify visible, actionable defects "
        "only, and propose at most one bounded repair per element using ONLY "
        "these actions:\n"
        '- "reduce_font_size": scale between 0.50 and 0.98\n'
        '- "resize_element": dh between 0.01 and 0.30 (grows the box '
        "downward), dy between -0.30 and 0.00 (shifts it up); dh and dy are "
        "fractions of the slide height\n"
        '- "remove_low_priority_bullet": only for bullets elements that hold '
        "two or more bullets\n"
        f"Slide: {context.get('slide_id', '')} "
        f"({context.get('slide_type', '')}). Elements you may target: "
        f"{json.dumps(elements, ensure_ascii=False)}\n"
        "Text content is fixed: never propose rewrites, shorten_text, or any "
        "factual change. Respond with ONLY a JSON object, no markdown fences, "
        "no explanation, in this exact shape:\n"
        '{"scores": {"no_overflow": 90, "no_overlap": 90, "alignment": 90, '
        '"typography": 90, "spacing": 90, "palette": 90, "density": 90, '
        '"template_fidelity": 90, "readability": 90, "variety": 90},\n'
        ' "approved": false,\n'
        ' "suggestions": [{"element_id": "slide_01-body", "action": '
        '"reduce_font_size", "scale": 0.9, "reason": "short reason"}]}\n'
        'Set "approved" to true only when no material change is needed, with '
        'an empty "suggestions" list.'
    )


class GeminiVisionProvider:
    """Gemini REST client. Needs a free API key (GEMINI_API_KEY in .env or env)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_s: int = 60,
    ) -> None:
        self.api_key = api_key
        self.model = model or os.environ.get(GEMINI_MODEL_ENV) or DEFAULT_GEMINI_MODEL
        self.timeout_s = timeout_s

    @property
    def name(self) -> str:
        return f"gemini:{self.model}"

    def _key(self) -> Optional[str]:
        return self.api_key or os.environ.get(GEMINI_API_KEY_ENV)

    def is_configured(self) -> bool:
        return bool(self._key())

    def analyze_slide(
        self, image_path: Path, slide_number: Optional[int] = None
    ) -> SlideVisionNotes:
        api_key = self._key()
        if not api_key:
            raise VisionProviderError(_MISSING_KEY_MESSAGE)
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": _PROMPT},
                        _inline_image(Path(image_path)),
                    ]
                }
            ],
            "generationConfig": {"temperature": 0},
        }
        body = self._request(payload, api_key)
        text = self._extract_text(body)
        notes = self._parse_notes(text)
        notes.slide_number = slide_number
        return notes

    def _request(self, payload: dict, api_key: str) -> dict:
        request = urllib.request.Request(
            _GEMINI_URL.format(model=self.model),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise VisionProviderError(
                f"Gemini API returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise VisionProviderError(f"Gemini API request failed: {exc}") from exc

    def _extract_text(self, body: dict) -> str:
        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise VisionProviderError(
                f"Gemini API response missing candidate text: {str(body)[:300]}"
            ) from exc
        chunks = [part.get("text", "") for part in parts if isinstance(part, dict)]
        text = "".join(chunks).strip()
        if not text:
            raise VisionProviderError("Gemini API returned an empty description")
        return text

    def _parse_notes(self, text: str) -> SlideVisionNotes:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            raise VisionProviderError(
                f"model response contained no JSON object: {text[:200]}"
            )
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise VisionProviderError(
                f"model response was not valid JSON: {text[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise VisionProviderError("model response JSON was not an object")

        background_style = data.get("background_style")
        if background_style not in _ALLOWED_BACKGROUND_STYLES:
            background_style = None

        colors: List[str] = []
        raw_colors = data.get("dominant_colors")
        if isinstance(raw_colors, list):
            for entry in raw_colors:
                if isinstance(entry, str) and _HEX_RE.match(entry):
                    value = entry.upper()
                    if value not in colors:
                        colors.append(value)
                    if len(colors) >= _MAX_COLORS:
                        break

        note_texts: List[str] = []
        raw_notes = data.get("notes")
        if isinstance(raw_notes, list):
            for entry in raw_notes:
                if isinstance(entry, str) and entry.strip():
                    note_texts.append(entry.strip())
                if len(note_texts) >= _MAX_NOTES:
                    break

        return SlideVisionNotes(
            background_style=background_style,
            dominant_colors=colors,
            notes=note_texts,
            raw_text=text,
        )


class GeminiSlideCritic(GeminiVisionProvider):
    """Gemini-backed visual critic for core.critique's loop (blueprint §8.5).

    Shares the transport, key handling and response parsing of
    GeminiVisionProvider; adds the bounded critique contract. The returned
    payload carries per-criterion 0-100 scores, an ``approved`` flag, and a
    ``suggestions`` list of machine-readable repair actions. Structure is
    strict (a JSON object with a ``suggestions`` list, or a loud
    VisionProviderError); values inside the payload are filtered like the
    extraction parser's, and every suggestion is re-validated by the
    critique loop before anything is applied.
    """

    @property
    def name(self) -> str:
        return f"gemini-critic:{self.model}"

    def critique_slide(
        self,
        generated: Path,
        reference: Optional[Path] = None,
        context: Optional[dict] = None,
    ) -> dict:
        api_key = self._key()
        if not api_key:
            raise VisionProviderError(_MISSING_KEY_MESSAGE)
        parts: List[dict] = [{"text": _critique_prompt(context or {})}]
        parts.append(_inline_image(generated))
        if reference is not None:
            parts.append({"text": "Reference template image:"})
            parts.append(_inline_image(reference))
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0},
        }
        body = self._request(payload, api_key)
        return self._parse_critique(self._extract_text(body))

    def _parse_critique(self, text: str) -> dict:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            raise VisionProviderError(
                f"model response contained no JSON object: {text[:200]}"
            )
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise VisionProviderError(
                f"model response was not valid JSON: {text[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise VisionProviderError("model response JSON was not an object")
        suggestions = data.get("suggestions")
        if not isinstance(suggestions, list):
            raise VisionProviderError("model response had no 'suggestions' list")

        scores: Dict[str, int] = {}
        raw_scores = data.get("scores")
        if isinstance(raw_scores, dict):
            for criterion in _CRITIC_CRITERIA:
                value = raw_scores.get(criterion)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                scores[criterion] = max(0, min(100, int(round(value))))
        return {
            "approved": data.get("approved") is True,
            "scores": scores,
            "suggestions": suggestions,
        }


def provider_from_environment() -> Optional[GeminiVisionProvider]:
    """Return the configured vision provider, or None when no key is set."""
    provider = GeminiVisionProvider()
    return provider if provider.is_configured() else None


def critic_from_environment() -> Optional[GeminiSlideCritic]:
    """Return the configured visual critic, or None when no key is set."""
    critic = GeminiSlideCritic()
    return critic if critic.is_configured() else None
