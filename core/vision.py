"""Vision inference plug-in point (blueprint §6.5).

Native PPTX data is the primary truth. Vision providers describe rendered
slide images only to fill gaps native data cannot (image backgrounds,
gradients, overall look). Providers must fail loudly — never fabricate a
description — and every downstream value they produce is tagged with
vision provenance by the extractor.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Protocol, Tuple, runtime_checkable

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
            raise VisionProviderError(
                "Gemini API key not set. Create a free key at "
                "https://aistudio.google.com/apikey, then put "
                "GEMINI_API_KEY=your-key in a .env file (or the environment)."
            )
        path = Path(image_path)
        if not path.is_file():
            raise VisionProviderError(f"slide image not found: {path}")
        mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
        if mime is None:
            raise VisionProviderError(
                f"unsupported slide image type '{path.suffix}' (expected png/jpg/webp)"
            )

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": _PROMPT},
                        {
                            "inline_data": {
                                "mime_type": mime,
                                "data": base64.b64encode(path.read_bytes()).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"temperature": 0},
        }
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
                body = json.loads(response.read().decode("utf-8"))
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

        text = self._extract_text(body)
        notes = self._parse_notes(text)
        notes.slide_number = slide_number
        return notes

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


def provider_from_environment() -> Optional[GeminiVisionProvider]:
    """Return the configured vision provider, or None when no key is set."""
    provider = GeminiVisionProvider()
    return provider if provider.is_configured() else None
