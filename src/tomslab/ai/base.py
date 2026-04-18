"""Abstract interface every AI provider implements.

A provider may implement a subset of capabilities — e.g. Ollama supports
embed/chat/vision, Gemini supports all three, Groq (later) only chat.
Callers should check `supports_*()` before calling.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class ProviderError(RuntimeError):
    """Recoverable provider-side failure (rate limit, bad request, etc.)."""


class ProviderUnavailable(ProviderError):
    """The provider can't be reached at all (no network, daemon down, etc.)."""


class AIProvider(ABC):
    """Uniform AI interface — see PRD §10 for the conceptual model."""

    name: str = "abstract"

    # capability flags
    def supports_embed(self) -> bool:
        return False

    def supports_chat(self) -> bool:
        return False

    def supports_vision(self) -> bool:
        return False

    # ------------------------------------------------------------------
    # quick health check — should be cheap, raise ProviderUnavailable on fail
    # ------------------------------------------------------------------
    @abstractmethod
    def ping(self) -> str:
        """Return a short human-readable status line, or raise."""

    # ------------------------------------------------------------------
    # embeddings
    # ------------------------------------------------------------------
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(f"{self.name} does not support embeddings")

    def embedding_dim(self) -> int:
        raise NotImplementedError(f"{self.name} does not support embeddings")

    def embedding_model_name(self) -> str:
        raise NotImplementedError(f"{self.name} does not support embeddings")

    # ------------------------------------------------------------------
    # chat (Phase 5)
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        image_paths: list[str] | None = None,
    ) -> str:
        """Send a chat turn. ``image_paths`` optionally attaches one or more
        images to the LAST user message for multimodal providers."""
        raise NotImplementedError(f"{self.name} does not support chat")

    # ------------------------------------------------------------------
    # vision (Phase 6)
    # ------------------------------------------------------------------
    def describe_image(self, image_path: str, prompt: str) -> str:
        raise NotImplementedError(f"{self.name} does not support vision")
