"""Google Gemini provider (cloud).

Free tier has aggressive RPD limits on embeddings, so we reserve Gemini
primarily for chat (gemini-2.5-flash has a ~1.5K/day free tier) and
allow embedding as an option for users who've enabled billing.
"""
from __future__ import annotations

import logging
import time

from tomslab.ai.base import AIProvider, ProviderError, ProviderUnavailable  # noqa: F401

log = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types as genai_types
except ImportError:  # pragma: no cover
    genai = None


DEFAULT_EMBED_MODEL = "gemini-embedding-001"
DEFAULT_CHAT_MODEL = "gemini-2.5-flash"


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        embed_model: str = DEFAULT_EMBED_MODEL,
        chat_model: str = DEFAULT_CHAT_MODEL,
    ) -> None:
        if genai is None:
            raise ProviderUnavailable("google-genai package not installed")
        if not api_key:
            raise ProviderUnavailable("no Gemini API key configured")
        self._client = genai.Client(api_key=api_key)
        self._embed_model = embed_model
        self._chat_model = chat_model
        self._dim: int | None = None

    # ---- capabilities -------------------------------------------------
    def supports_embed(self) -> bool: return True
    def supports_chat(self) -> bool: return True
    def supports_vision(self) -> bool: return True  # via multimodal chat

    # ---- health --------------------------------------------------------
    def ping(self) -> str:
        try:
            r = self._client.models.generate_content(
                model=self._chat_model,
                contents="Say the word pong and nothing else.",
            )
        except genai_errors.ClientError as exc:
            raise ProviderUnavailable(f"gemini auth/client error: {exc}") from exc
        except Exception as exc:
            raise ProviderUnavailable(f"gemini unreachable: {exc}") from exc
        return f"connected — {self._chat_model} said {r.text!r}".replace("\n", " ")

    # ---- embeddings ----------------------------------------------------
    def embedding_model_name(self) -> str:
        return self._embed_model

    def embedding_dim(self) -> int:
        if self._dim is None:
            r = self._client.models.embed_content(
                model=self._embed_model, contents=["probe"]
            )
            self._dim = len(r.embeddings[0].values) if r.embeddings else 0
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            r = self._client.models.embed_content(
                model=self._embed_model, contents=texts
            )
        except genai_errors.ClientError as exc:
            # 429 = rate limit; surface as ProviderError so the pipeline can pause
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                raise ProviderError(f"gemini rate limited: {msg[:200]}") from exc
            raise ProviderError(f"gemini embed failed: {msg[:200]}") from exc
        except Exception as exc:
            raise ProviderError(f"gemini embed failed: {exc}") from exc

        if self._dim is None and r.embeddings:
            self._dim = len(r.embeddings[0].values)
        return [list(e.values) for e in r.embeddings]

    # ---- chat ----------------------------------------------------------
    def chat(self, messages: list[dict], system: str | None = None) -> str:
        # Gemini takes a single flat 'contents' string for simple turns;
        # for multi-turn with roles use a Content list.
        contents = [genai_types.Content(
            role="user" if m["role"] != "assistant" else "model",
            parts=[genai_types.Part(text=m["content"])],
        ) for m in messages]
        config = None
        if system:
            config = genai_types.GenerateContentConfig(system_instruction=system)

        # Auto-retry on transient 503 / 429. Google's free tier intermittently
        # returns UNAVAILABLE under load; most calls succeed on the next try.
        last_exc: Exception | None = None
        delays = [1.5, 4.0, 10.0]
        for attempt, delay in enumerate([0.0] + delays):
            if delay:
                time.sleep(delay)
            try:
                r = self._client.models.generate_content(
                    model=self._chat_model,
                    contents=contents,
                    config=config,
                )
                return (r.text or "").strip()
            except Exception as exc:
                msg = str(exc)
                last_exc = exc
                is_transient = (
                    "503" in msg or "UNAVAILABLE" in msg or "429" in msg
                    or "RESOURCE_EXHAUSTED" in msg or "INTERNAL" in msg
                )
                if not is_transient:
                    raise ProviderError(f"gemini chat failed: {msg[:300]}") from exc
                log.warning(
                    "Gemini transient error (attempt %d): %s",
                    attempt + 1, msg[:200],
                )

        # all retries exhausted
        # Surface a friendly, short message — the UI renders this verbatim.
        raise ProviderError(
            "Gemini is currently overloaded (HTTP 503). "
            "This is usually brief — please try again in a moment."
        )
