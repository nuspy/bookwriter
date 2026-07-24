"""Client per LM Studio (API OpenAI-compatibile su ``/v1``, più API nativa ``/api/v0``).

Fornisce:
- ``health`` / ``list_models`` (stato e contesto dei modelli, via API nativa di LM Studio);
- ``chat`` (completamento testuale);
- ``chat_json`` (output strutturato guidato da JSON Schema via ``response_format``);
- ``embed`` (embeddings con i prefissi task richiesti da nomic).

Include retry con backoff sulle 5xx e sugli errori di rete.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from bookwriter.config import EmbeddingConfig, LLMConfig


class LLMError(RuntimeError):
    """Errore nell'interazione con LM Studio."""


@dataclass
class ModelInfo:
    id: str
    type: str  # llm | vlm | embeddings
    state: str  # loaded | not-loaded
    max_context_length: int | None = None
    loaded_context_length: int | None = None
    capabilities: list[str] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return self.state == "loaded"


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json(text: str) -> Any:
    """Interpreta un JSON eventualmente incorniciato in ```json ... ``` o con testo attorno."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _FENCE_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Ultimo tentativo: dal primo '{' o '[' fino all'ultimo '}' o ']'.
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    end = max(text.rfind("}"), text.rfind("]"))
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise LLMError(f"Risposta non interpretabile come JSON: {text[:200]!r}")


class LMStudioClient:
    def __init__(self, config: LLMConfig, embedding: EmbeddingConfig | None = None) -> None:
        self.config = config
        self.embedding = embedding or EmbeddingConfig()
        base = config.base_url.rstrip("/")
        self._v1 = base
        # L'API nativa vive sotto la radice del server, non sotto /v1.
        self._api_root = base[:-3].rstrip("/") if base.endswith("/v1") else base
        self._client = httpx.Client(timeout=config.request_timeout_s)
        # Contatore token (thread-safe) per stimare costi/tempi.
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._usage_lock = threading.Lock()

    def _track(self, usage: dict | None) -> None:
        if not usage:
            return
        with self._usage_lock:
            for key in self.usage:
                self.usage[key] += int(usage.get(key, 0) or 0)

    # ---- ciclo di vita -------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LMStudioClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- HTTP con retry ------------------------------------------------
    def _request(self, method: str, url: str, *, json_body: dict | None = None) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                resp = self._client.request(method, url, json=json_body)
                if resp.status_code >= 500:
                    raise LLMError(f"{resp.status_code} da {url}: {resp.text[:200]}")
                resp.raise_for_status()
                return resp
            except (httpx.HTTPError, LLMError) as exc:
                last_exc = exc
                if attempt < self.config.max_retries - 1:
                    time.sleep(0.5 * (2**attempt))
        raise LLMError(f"Richiesta a {url} fallita dopo {self.config.max_retries} tentativi: {last_exc}")

    # ---- introspezione modelli ----------------------------------------
    def list_models(self) -> list[ModelInfo]:
        """Elenca i modelli con stato e contesto (API nativa; fallback a /v1/models)."""
        try:
            resp = self._request("GET", f"{self._api_root}/api/v0/models")
            data = resp.json().get("data", [])
            return [
                ModelInfo(
                    id=m["id"],
                    type=m.get("type", "?"),
                    state=m.get("state", "?"),
                    max_context_length=m.get("max_context_length"),
                    loaded_context_length=m.get("loaded_context_length"),
                    capabilities=m.get("capabilities", []),
                )
                for m in data
            ]
        except LLMError:
            resp = self._request("GET", f"{self._v1}/models")
            data = resp.json().get("data", [])
            return [ModelInfo(id=m["id"], type="?", state="?") for m in data]

    def health(self) -> bool:
        """True se il server risponde ed espone almeno un modello."""
        return len(self.list_models()) > 0

    # ---- chat ----------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": model or self.config.models.reasoning,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if extra:
            body.update(extra)
        resp = self._request("POST", f"{self._v1}/chat/completions", json_body=body)
        data = resp.json()
        self._track(data.get("usage"))
        return data["choices"][0]["message"]["content"]

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
        schema_name: str = "output",
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> Any:
        """Chat con output vincolato a un JSON Schema (structured output di LM Studio)."""
        extra = {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        }
        content = self.chat(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra=extra,
        )
        return _extract_json(content)

    # ---- embeddings ----------------------------------------------------
    def embed(
        self,
        texts: list[str],
        *,
        kind: Literal["document", "query"] = "document",
        model: str | None = None,
    ) -> list[list[float]]:
        """Embeddings con prefisso task nomic (``search_document:`` / ``search_query:``)."""
        if not texts:
            return []
        prefix = self.embedding.doc_prefix if kind == "document" else self.embedding.query_prefix
        body = {
            "model": model or self.config.models.embedding,
            "input": [prefix + t for t in texts],
        }
        resp = self._request("POST", f"{self._v1}/embeddings", json_body=body)
        data = resp.json()
        self._track(data.get("usage"))
        rows = sorted(data["data"], key=lambda r: r.get("index", 0))
        return [r["embedding"] for r in rows]
