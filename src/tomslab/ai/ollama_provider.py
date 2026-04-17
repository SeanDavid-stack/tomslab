"""Ollama local-model provider."""
from __future__ import annotations

import logging

from tomslab.ai.base import AIProvider, ProviderError, ProviderUnavailable

log = logging.getLogger(__name__)

try:
    import ollama as _ollama
except ImportError:  # pragma: no cover — ollama is required in requirements.txt
    _ollama = None


DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_CHAT_MODEL = "llama3.1:8b"
DEFAULT_VISION_MODEL = "llava:13b"


class OllamaProvider(AIProvider):
    name = "ollama"

    def __init__(
        self,
        host: str | None = None,
        embed_model: str = DEFAULT_EMBED_MODEL,
        chat_model: str = DEFAULT_CHAT_MODEL,
        vision_model: str = DEFAULT_VISION_MODEL,
    ) -> None:
        if _ollama is None:
            raise ProviderUnavailable("ollama python package not installed")
        self._client = _ollama.Client(host=host) if host else _ollama.Client()
        self._embed_model = embed_model
        self._chat_model = chat_model
        self._vision_model = vision_model
        self._dim: int | None = None

    # ---- capability flags ---------------------------------------------
    def supports_embed(self) -> bool: return True
    def supports_chat(self) -> bool: return True
    def supports_vision(self) -> bool: return True

    # ---- health --------------------------------------------------------
    def ping(self) -> str:
        try:
            resp = self._client.list()
        except Exception as exc:
            raise ProviderUnavailable(f"ollama daemon unreachable: {exc}") from exc
        # ollama.ListResponse exposes .models; each has .model (name)
        model_names = sorted(m.model for m in (resp.models or []))
        need = [self._embed_model, self._chat_model, self._vision_model]
        missing = [m for m in need if not any(n.startswith(m) for n in model_names)]
        if missing:
            return f"connected ({len(model_names)} models) — missing: {missing}"
        return f"connected — {len(model_names)} models installed"

    # ---- embeddings ----------------------------------------------------
    def embedding_model_name(self) -> str:
        return self._embed_model

    def embedding_dim(self) -> int:
        if self._dim is None:
            # probe once
            r = self._client.embed(model=self._embed_model, input="probe")
            self._dim = len(r.embeddings[0]) if r.embeddings else 0
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            r = self._client.embed(model=self._embed_model, input=texts)
        except Exception as exc:
            raise ProviderError(f"ollama embed failed: {exc}") from exc
        if self._dim is None and r.embeddings:
            self._dim = len(r.embeddings[0])
        return [list(v) for v in r.embeddings]

    # ---- chat ----------------------------------------------------------
    def chat(self, messages: list[dict], system: str | None = None) -> str:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        try:
            r = self._client.chat(model=self._chat_model, messages=msgs)
        except Exception as exc:
            raise ProviderError(f"ollama chat failed: {exc}") from exc
        return (r.message.content or "").strip()

    # ---- vision --------------------------------------------------------
    def describe_image(self, image_path: str, prompt: str) -> str:
        try:
            r = self._client.chat(
                model=self._vision_model,
                messages=[{"role": "user", "content": prompt, "images": [image_path]}],
            )
        except Exception as exc:
            raise ProviderError(f"ollama vision failed: {exc}") from exc
        return (r.message.content or "").strip()
