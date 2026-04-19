"""Ollama local-model provider."""
from __future__ import annotations

import logging

from tomslab.ai.base import AIProvider, ProviderError, ProviderUnavailable

log = logging.getLogger(__name__)

# Lazy — `import ollama` has been observed to hang on some systems
# (likely probing the Ollama daemon at module load). Defer until a
# provider instance is actually created so the app can launch even
# when ollama-daemon is offline.
_ollama = None


def _ensure_ollama_loaded():
    """Import the ollama Python package with a hard timeout. Some systems
    hang indefinitely inside `import ollama` (antivirus scan, DLL loader
    deadlock, GPU-driver bad state). Without a timeout that hang shows
    up in the UI as 'Thinking…' forever with no error. Better to fail
    visibly so the user knows to reboot or switch providers."""
    global _ollama
    if _ollama is not None:
        return _ollama

    import threading
    result = {}

    def _do_import():
        try:
            import ollama as o
            result["mod"] = o
        except Exception as exc:
            result["err"] = exc

    t = threading.Thread(target=_do_import, daemon=True)
    t.start()
    t.join(timeout=10.0)
    if t.is_alive():
        raise ProviderUnavailable(
            "The `ollama` Python package is hanging on import. This usually "
            "means a stuck antivirus scan, a Windows DLL-loader deadlock, or "
            "CUDA driver in a bad state. Try: (1) reboot the machine, "
            "(2) switch Ask Tom to Gemini (cloud) for now."
        )
    if "err" in result:
        raise ProviderUnavailable(
            f"ollama python package failed to import: {result['err']}"
        )
    _ollama = result["mod"]
    return _ollama


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
        chat_timeout: float = 180.0,
    ) -> None:
        _ensure_ollama_loaded()
        # Ollama client takes an optional timeout via httpx — pass through so
        # a hung local call fails cleanly instead of spinning forever.
        if host:
            self._client = _ollama.Client(host=host, timeout=chat_timeout)
        else:
            self._client = _ollama.Client(timeout=chat_timeout)
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
    # Known-multimodal model names. If chat_model is one of these we trust
    # it to actually see images; otherwise we route image turns through
    # vision_model (llava) to avoid silently dropping the chart.
    _MULTIMODAL_PREFIXES = ("llava", "bakllava", "moondream", "llama3.2-vision", "minicpm-v")

    def _is_multimodal(self, model_name: str) -> bool:
        n = (model_name or "").lower()
        return any(n.startswith(pref) for pref in self._MULTIMODAL_PREFIXES)

    def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        image_paths: list[str] | None = None,
    ) -> str:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)

        # If the user attached an image, we MUST use a multimodal model —
        # otherwise the model answers blindly against retrieved text context
        # and fabricates prices from that context. Switch to vision_model
        # (llava) for this call if the configured chat_model is text-only.
        model = self._chat_model
        if image_paths:
            if not self._is_multimodal(self._chat_model):
                if self._is_multimodal(self._vision_model):
                    log.warning(
                        "Ollama chat: %s is text-only but image attached — "
                        "routing through vision model %s for this turn",
                        self._chat_model, self._vision_model,
                    )
                    model = self._vision_model
                else:
                    raise ProviderError(
                        f"An image was attached but the Ollama chat model "
                        f"({self._chat_model}) is text-only and no multimodal "
                        f"vision model is configured. Either switch chat to "
                        f"Gemini, change chat_model_ollama to a multimodal "
                        f"model (e.g. llava), or remove the image."
                    )
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get("role") == "user":
                    msgs[i] = {**msgs[i], "images": list(image_paths)}
                    break

        try:
            r = self._client.chat(model=model, messages=msgs)
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
