"""OCR for image-only PDF pages.

Two providers:
  * OllamaOCR — local LLaVA via Ollama.  No rate limits, runs on CUDA.
    Default — suitable for bulk runs (hundreds of pages).
  * GeminiOCR — Google's vision model.  Higher quality on dense text
    but free-tier limits (~10 RPM) make it unworkable for batches.

make_ocr() picks based on the ``ai_provider_vision`` setting.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from tomslab import db as dbmod, secret_store

log = logging.getLogger(__name__)

# Lazy — google-genai hangs on import on some systems (cert / metadata
# probe); we only need it when OCR is actually invoked, not at app load.
genai = None
genai_errors = None
genai_types = None


def _ensure_genai() -> None:
    global genai, genai_errors, genai_types
    if genai is not None:
        return
    try:
        from google import genai as _genai
        from google.genai import errors as _err
        from google.genai import types as _types
        genai = _genai
        genai_errors = _err
        genai_types = _types
    except ImportError:
        pass

try:
    import ollama as _ollama
except ImportError:  # pragma: no cover
    _ollama = None

try:
    import easyocr as _easyocr
except ImportError:  # pragma: no cover
    _easyocr = None


OCR_PROMPT = (
    "You are transcribing a trading education slide. Extract ALL visible text "
    "verbatim — headings, body text, labels on charts, numbers, every word. "
    "Preserve order top-to-bottom, left-to-right. Do not paraphrase, summarise, "
    "or add explanations. If text is illegible, write [illegible]. "
    "If the slide is blank, return an empty string. Output plain text only."
)


class OCRError(RuntimeError):
    pass


class OCRRateLimited(OCRError):
    pass


class GeminiOCR:
    def __init__(self, conn: sqlite3.Connection, model: str = "gemini-2.5-flash") -> None:
        if genai is None:
            raise RuntimeError("google-genai not installed")
        api_key = secret_store.load_api_key(conn, "gemini")
        if not api_key:
            raise RuntimeError("no Gemini API key configured (Settings → AI Providers)")
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def ocr_image(
        self,
        png_path: Path,
        prompt: str = OCR_PROMPT,
        max_retries: int = 4,
    ) -> str:
        data = Path(png_path).read_bytes()
        image_part = genai_types.Part.from_bytes(data=data, mime_type="image/png")

        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                r = self._client.models.generate_content(
                    model=self._model,
                    contents=[image_part, prompt],
                )
                return (r.text or "").strip()
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                is_rate_limit = (
                    "429" in msg
                    or "RESOURCE_EXHAUSTED" in msg
                    or "rate" in msg.lower()
                )
                if not is_rate_limit:
                    log.warning("Gemini OCR non-retryable failure on %s: %s",
                                png_path.name, msg[:200])
                    return ""
                # exponential backoff: 15s, 30s, 60s, 120s
                backoff = 15 * (2 ** attempt)
                log.warning("Gemini OCR rate-limited on %s (attempt %d); sleeping %ds",
                            png_path.name, attempt + 1, backoff)
                time.sleep(backoff)

        # all retries exhausted
        raise OCRRateLimited(f"Gemini OCR gave up after retries on {png_path.name}: {last_exc}")


class OllamaOCR:
    """LLaVA-based OCR via local Ollama. Default for bulk runs."""

    def __init__(self, conn: sqlite3.Connection, model: str | None = None) -> None:
        if _ollama is None:
            raise RuntimeError("ollama python package not installed")
        self._client = _ollama.Client()
        self._model = model or (
            dbmod.get_setting(conn, "vision_model_ollama", "llava:13b") or "llava:13b"
        )

    def ocr_image(
        self,
        png_path: Path,
        prompt: str = OCR_PROMPT,
        max_retries: int = 2,
    ) -> str:
        path = str(Path(png_path))
        for attempt in range(max_retries):
            try:
                resp = self._client.chat(
                    model=self._model,
                    messages=[{"role": "user", "content": prompt, "images": [path]}],
                    options={"temperature": 0.1},   # deterministic transcription
                )
            except Exception as exc:
                msg = str(exc)
                if attempt + 1 < max_retries:
                    log.warning("LLaVA OCR transient error on %s: %s — retrying",
                                png_path.name, msg[:200])
                    time.sleep(2)
                    continue
                log.warning("LLaVA OCR failed on %s: %s", png_path.name, msg[:200])
                return ""
            return (resp.message.content or "").strip()
        return ""


class EasyOCR:
    """Classical text-recognition OCR via CRAFT+CRNN (easyocr package).

    Fast, GPU-accelerated (reuses our torch install), and — unlike chat
    vision models — it actually transcribes instead of describing.
    ~2-3s per rendered page on a 3080 Ti.
    """

    _reader = None   # shared across instances, lazy-loaded

    def __init__(self, conn: sqlite3.Connection, languages: list[str] | None = None) -> None:
        if _easyocr is None:
            raise RuntimeError("easyocr package not installed")
        self._languages = languages or ["en"]

    def _get_reader(self):
        if EasyOCR._reader is None:
            # gpu=True falls back to CPU silently if CUDA isn't available
            EasyOCR._reader = _easyocr.Reader(self._languages, gpu=True, verbose=False)
        return EasyOCR._reader

    def ocr_image(
        self,
        png_path: Path,
        prompt: str | None = None,   # unused — EasyOCR has no prompt concept
        max_retries: int = 1,
    ) -> str:
        reader = self._get_reader()
        try:
            lines = reader.readtext(
                str(Path(png_path)), detail=0, paragraph=True,
            )
        except Exception as exc:
            log.warning("EasyOCR failed on %s: %s", png_path.name, exc)
            return ""
        return "\n".join(lines).strip()


def make_ocr(conn: sqlite3.Connection):
    """Factory — default is EasyOCR (fast, local, no rate limits).

    Override via the ``ocr_provider`` setting: one of
    'easyocr' | 'ollama' | 'gemini'.
    """
    which = (dbmod.get_setting(conn, "ocr_provider", "easyocr") or "easyocr").lower()
    if which == "gemini":
        return GeminiOCR(conn)
    if which == "ollama":
        return OllamaOCR(conn)
    return EasyOCR(conn)
