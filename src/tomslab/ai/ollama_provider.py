"""Ollama local-model provider.

**HTTP-first implementation.** Historically this module used the
`ollama` Python SDK. That package has been observed to hang on
`import` on some Windows boxes (antivirus, DLL loader, or CUDA
driver state), producing silent 'Thinking…' forever with no error.
Since Ollama's REST API is stable and trivial (3 endpoints), we now
talk to the local daemon via stdlib urllib. No third-party package
required, no import-time hang possible.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path

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

    DEFAULT_HOST = "http://127.0.0.1:11434"

    def __init__(
        self,
        host: str | None = None,
        embed_model: str = DEFAULT_EMBED_MODEL,
        chat_model: str = DEFAULT_CHAT_MODEL,
        vision_model: str = DEFAULT_VISION_MODEL,
        chat_timeout: float = 180.0,
    ) -> None:
        # NOTE: no ollama-package import here. We speak the REST API
        # directly via urllib so a broken / hung `import ollama` can't
        # take the app down.
        self._host = (host or self.DEFAULT_HOST).rstrip("/")
        self._embed_model = embed_model
        self._chat_model = chat_model
        self._vision_model = vision_model
        self._chat_timeout = chat_timeout
        self._dim: int | None = None

    # ---- capability flags ---------------------------------------------
    def supports_embed(self) -> bool: return True
    def supports_chat(self) -> bool: return True
    def supports_vision(self) -> bool: return True

    # ---- HTTP plumbing -------------------------------------------------
    def _post(self, path: str, payload: dict, *, timeout: float) -> dict:
        url = f"{self._host}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.URLError as exc:
            raise ProviderUnavailable(f"ollama daemon unreachable: {exc}") from exc
        except Exception as exc:
            raise ProviderError(f"ollama request failed: {exc}") from exc
        try:
            return json.loads(body)
        except Exception as exc:
            raise ProviderError(f"ollama returned non-JSON: {exc}") from exc

    def _get(self, path: str, *, timeout: float = 10.0) -> dict:
        url = f"{self._host}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise ProviderUnavailable(f"ollama daemon unreachable: {exc}") from exc
        except Exception as exc:
            raise ProviderError(f"ollama request failed: {exc}") from exc

    # ---- health --------------------------------------------------------
    def ping(self) -> str:
        try:
            data = self._get("/api/tags")
        except ProviderUnavailable:
            raise
        models = data.get("models") or []
        model_names = sorted(m.get("name") or m.get("model") or "" for m in models)
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
            vecs = self.embed_texts(["probe"])
            self._dim = len(vecs[0]) if vecs else 0
        return self._dim

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = self._post(
            "/api/embed",
            {"model": self._embed_model, "input": texts},
            timeout=120.0,
        )
        vecs = data.get("embeddings") or []
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return [list(v) for v in vecs]

    # ---- chat ----------------------------------------------------------
    # Known-multimodal model names. If chat_model is one of these we trust
    # it to actually see images; otherwise we route image turns through
    # vision_model (llava) to avoid silently dropping the chart.
    _MULTIMODAL_PREFIXES = ("llava", "bakllava", "moondream", "llama3.2-vision", "minicpm-v")

    def _is_multimodal(self, model_name: str) -> bool:
        n = (model_name or "").lower()
        return any(n.startswith(pref) for pref in self._MULTIMODAL_PREFIXES)

    @staticmethod
    def _image_b64(path: str) -> str:
        """Ollama's REST API expects images as base64 strings in the
        message's 'images' array."""
        return base64.b64encode(Path(path).read_bytes()).decode("ascii")

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
            encoded = [self._image_b64(p) for p in image_paths]
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get("role") == "user":
                    msgs[i] = {**msgs[i], "images": encoded}
                    break

        data = self._post(
            "/api/chat",
            {"model": model, "messages": msgs, "stream": False},
            timeout=self._chat_timeout,
        )
        return ((data.get("message") or {}).get("content") or "").strip()

    # ---- vision --------------------------------------------------------
    def describe_image(self, image_path: str, prompt: str) -> str:
        data = self._post(
            "/api/chat",
            {
                "model": self._vision_model,
                "messages": [{
                    "role": "user",
                    "content": prompt,
                    "images": [self._image_b64(image_path)],
                }],
                "stream": False,
            },
            timeout=self._chat_timeout,
        )
        return ((data.get("message") or {}).get("content") or "").strip()
