"""Groq provider (cloud, chat-only).

Groq's free tier is generous (~14,400 requests/day at the time of
writing) and the LPU hardware streams tokens noticeably faster than
Gemini Flash. Chat-only: Groq does not offer embeddings on the free
tier, and the citation-quality of the open models it hosts is below
Gemini 2.5 Flash on grounded RAG, so this is positioned as an
alternative for users who hit Gemini's lower daily request cap, not
as the recommended default.

The API is OpenAI-compatible. We deliberately use stdlib ``urllib``
(no ``openai`` package) to avoid adding a dep just for one cloud chat
provider, matching the pattern used in ``tomslab/updates.py``.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from tomslab import __version__
from tomslab.ai.base import AIProvider, ProviderError, ProviderUnavailable

log = logging.getLogger(__name__)

# Llama 3.3 70B Versatile is the best general-purpose model on Groq's
# free tier as of 2026-04 — 128K context, strong instruction-following.
DEFAULT_CHAT_MODEL = "llama-3.3-70b-versatile"

_API_BASE = "https://api.groq.com/openai/v1"
_TIMEOUT_SECONDS = 60
_USER_AGENT = f"TomsLab/{__version__}"


class GroqProvider(AIProvider):
    name = "groq"

    def __init__(
        self,
        api_key: str,
        chat_model: str = DEFAULT_CHAT_MODEL,
    ) -> None:
        if not api_key:
            raise ProviderUnavailable("no Groq API key configured")
        self._api_key = api_key
        self._chat_model = chat_model

    # ---- capabilities -------------------------------------------------
    def supports_chat(self) -> bool: return True

    # ---- health --------------------------------------------------------
    def ping(self) -> str:
        try:
            r = self._post(
                "/chat/completions",
                {
                    "model": self._chat_model,
                    "messages": [{"role": "user", "content": "Say the word pong and nothing else."}],
                    "max_tokens": 8,
                    "temperature": 0.0,
                },
            )
        except ProviderError as exc:
            raise ProviderUnavailable(f"groq unreachable: {exc}") from exc
        text = self._extract_text(r)
        return f"connected — {self._chat_model} said {text!r}".replace("\n", " ")

    # ---- chat ----------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        image_paths: list[str] | None = None,
    ) -> str:
        # Groq's free-tier text models do not accept images. If the caller
        # passed image_paths, ignore them rather than failing — the chat
        # fallback chain will still produce a useful (text-only) answer.
        if image_paths:
            log.info(
                "Groq chat dropping %d image(s) — model %s is text-only.",
                len(image_paths), self._chat_model,
            )

        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        for m in messages:
            role = "assistant" if m.get("role") == "assistant" else "user"
            msgs.append({"role": role, "content": m.get("content", "")})

        # Same retry rationale as Gemini: one fast retry on a transient
        # 429/5xx, then surrender so the chat fallback chain takes over.
        last_exc: Exception | None = None
        for attempt, delay in enumerate([0.0, 1.5]):
            if delay:
                time.sleep(delay)
            try:
                r = self._post(
                    "/chat/completions",
                    {
                        "model": self._chat_model,
                        "messages": msgs,
                        "temperature": 0.2,
                    },
                )
                return self._extract_text(r)
            except ProviderError as exc:
                last_exc = exc
                msg = str(exc)
                is_transient = any(
                    code in msg for code in ("429", "500", "502", "503", "504")
                )
                if not is_transient:
                    raise
                log.warning(
                    "Groq transient error (attempt %d/2): %s",
                    attempt + 1, msg[:200],
                )

        raise ProviderError(
            f"Groq is currently overloaded — falling back. Last error: {last_exc}"
        )

    # ---- internals -----------------------------------------------------
    def _post(self, path: str, body: dict) -> dict:
        url = f"{_API_BASE}{path}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            # Try to surface the JSON error message Groq returns so the
            # user sees "invalid_api_key" rather than just "401".
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise ProviderError(
                f"groq HTTP {exc.code}: {detail or exc.reason}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"groq network error: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(f"groq returned non-JSON: {exc}") from exc

    @staticmethod
    def _extract_text(payload: dict) -> str:
        try:
            choices = payload.get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            return (msg.get("content") or "").strip()
        except (AttributeError, IndexError, TypeError):
            return ""
