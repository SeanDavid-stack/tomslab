"""Pick the right AI provider based on current settings.

The registry caches provider instances so repeat calls inside one
process don't re-negotiate HTTP clients or re-probe embedding dims.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable

from tomslab import db as dbmod
from tomslab import secret_store
from tomslab.ai.base import AIProvider, ProviderUnavailable
from tomslab.ai.gemini import GeminiProvider
from tomslab.ai.groq import GroqProvider
from tomslab.ai.ollama_provider import OllamaProvider

log = logging.getLogger(__name__)

# Cache keyed by (provider-name, role) — role can affect model selection.
_cache: dict[tuple[str, str], AIProvider] = {}


def reset_cache() -> None:
    _cache.clear()


def _build_ollama(conn: sqlite3.Connection, role: str) -> AIProvider:
    if role == "embed":
        model = dbmod.get_setting(conn, "embed_model_ollama", "nomic-embed-text")
    elif role == "chat":
        model = dbmod.get_setting(conn, "chat_model_ollama", "llama3.1:8b")
    elif role == "vision":
        model = dbmod.get_setting(conn, "vision_model_ollama", "llava:13b")
    else:
        model = None
    kwargs = {}
    if role == "embed":
        kwargs["embed_model"] = model
    elif role == "chat":
        kwargs["chat_model"] = model
    elif role == "vision":
        kwargs["vision_model"] = model
    return OllamaProvider(**kwargs)


def _build_gemini(conn: sqlite3.Connection, role: str) -> AIProvider:
    api_key = secret_store.load_api_key(conn, "gemini")
    if not api_key:
        raise ProviderUnavailable("no Gemini API key configured (Settings → AI Providers)")
    if role == "embed":
        model = dbmod.get_setting(conn, "embed_model_gemini", "gemini-embedding-001")
        return GeminiProvider(api_key=api_key, embed_model=model)
    # chat and vision both use the chat model
    model = dbmod.get_setting(conn, "chat_model_gemini", "gemini-2.5-flash")
    return GeminiProvider(api_key=api_key, chat_model=model)


def _build_groq(conn: sqlite3.Connection, role: str) -> AIProvider:
    if role != "chat":
        raise ProviderUnavailable(
            "Groq only supports chat — pick Ollama or Gemini for embed/vision."
        )
    api_key = secret_store.load_api_key(conn, "groq")
    if not api_key:
        raise ProviderUnavailable("no Groq API key configured (Settings → AI Providers)")
    from tomslab.ai.groq import DEFAULT_CHAT_MODEL as _GROQ_DEFAULT
    model = dbmod.get_setting(conn, "chat_model_groq", _GROQ_DEFAULT) or _GROQ_DEFAULT
    return GroqProvider(api_key=api_key, chat_model=model)


_BUILDERS: dict[str, Callable[[sqlite3.Connection, str], AIProvider]] = {
    "ollama": _build_ollama,
    "gemini": _build_gemini,
    "groq": _build_groq,
}


def _provider_for(conn: sqlite3.Connection, role: str) -> AIProvider:
    setting_key = f"ai_provider_{role}"
    name = dbmod.get_setting(conn, setting_key, "ollama") or "ollama"
    cache_key = (name, role)
    if cache_key in _cache:
        return _cache[cache_key]
    builder = _BUILDERS.get(name)
    if builder is None:
        raise ProviderUnavailable(f"unknown provider: {name}")
    prov = builder(conn, role)
    _cache[cache_key] = prov
    return prov


def get_embed_provider(conn: sqlite3.Connection) -> AIProvider:
    return _provider_for(conn, "embed")


def get_chat_provider(conn: sqlite3.Connection) -> AIProvider:
    return _provider_for(conn, "chat")


def get_chat_fallback(conn: sqlite3.Connection) -> AIProvider | None:
    """Optional secondary chat provider. Used automatically when the
    primary provider fails with a rate-limit / outage / auth error."""
    name = dbmod.get_setting(conn, "ai_provider_chat_fallback", "") or ""
    name = name.strip().lower()
    if not name:
        return None
    primary = dbmod.get_setting(conn, "ai_provider_chat", "gemini") or "gemini"
    if name == primary.strip().lower():
        return None    # no point in falling back to the same thing
    try:
        return build_provider(conn, name, "chat")
    except Exception as exc:
        log.warning("chat fallback %s unavailable: %s", name, exc)
        return None


def get_vision_provider(conn: sqlite3.Connection) -> AIProvider:
    return _provider_for(conn, "vision")


def build_provider(conn: sqlite3.Connection, name: str, role: str) -> AIProvider:
    """Build a provider by explicit name (used by Settings 'Test connection')."""
    builder = _BUILDERS.get(name)
    if builder is None:
        raise ProviderUnavailable(f"unknown provider: {name}")
    return builder(conn, role)
