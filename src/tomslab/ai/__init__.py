"""AI provider layer.

Exposes a uniform interface over Gemini / Ollama / (later) Claude & OpenAI.
Pick the active provider per-task (embed, chat, vision) via settings.
"""
from tomslab.ai.base import AIProvider, ProviderError, ProviderUnavailable
from tomslab.ai.registry import get_embed_provider, get_chat_provider, get_vision_provider

__all__ = [
    "AIProvider",
    "ProviderError",
    "ProviderUnavailable",
    "get_embed_provider",
    "get_chat_provider",
    "get_vision_provider",
]
