"""Configurazione: workspace, config globale e per-progetto, ruoli modello, taratura.

I valori di default rispecchiano il setup LM Studio rilevato (vedi docs/PLAN.md §12.5).
Sono sovrascrivibili sia globalmente (``<workspace>/config.toml``) sia per progetto
(``<progetto>/config.toml``).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, Field

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.10
    import tomli as _toml  # type: ignore[no-redefine]


DEFAULT_BASE_URL = "http://localhost:1234/v1"
CONFIG_FILENAME = "config.toml"


def workspace_root() -> Path:
    """Cartella radice del workspace (multi-progetto).

    Override con la variabile d'ambiente ``BOOKWRITER_HOME`` (utile per i test).
    Default: ``~/.bookwriter``.
    """
    env = os.environ.get("BOOKWRITER_HOME")
    return Path(env).expanduser() if env else Path.home() / ".bookwriter"


class ModelRoles(BaseModel):
    """Modelli LM Studio assegnati per ruolo. ``heavy`` è opzionale (job di alta qualità)."""

    reasoning: str = "gemma-4-12b-obliterated"
    embedding: str = "text-embedding-nomic-embed-text-v1.5"
    heavy: str = ""

    @property
    def quality(self) -> str:
        """Modello per i passaggi di qualità (coerenza, critic): ``heavy`` se impostato."""
        return self.heavy or self.reasoning


class LLMConfig(BaseModel):
    base_url: str = DEFAULT_BASE_URL
    models: ModelRoles = Field(default_factory=ModelRoles)
    request_timeout_s: float = 120.0
    max_retries: int = 3
    concurrency: int = 4  # richieste LLM parallele (embedding/atomizzazione)


class EmbeddingConfig(BaseModel):
    """nomic-embed-text-v1.5: 768 dim, contesto max 2048 token, richiede prefissi task."""

    max_tokens: int = 2048
    doc_prefix: str = "search_document: "
    query_prefix: str = "search_query: "


class ChunkingConfig(BaseModel):
    """Budget del chunking deterministico. Sotto il cap embedding (2048) con margine."""

    target_tokens: int = 1500
    overlap_sentences: int = 1


class AppConfig(BaseModel):
    """Configurazione completa (globale o di progetto)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)

    @classmethod
    def load(cls, path: Path) -> AppConfig:
        """Carica da un file TOML; se assente ritorna i default."""
        if not path.exists():
            return cls()
        with path.open("rb") as fh:
            data: dict[str, Any] = _toml.load(fh)
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            tomli_w.dump(self.model_dump(mode="python"), fh)

    def merged_with(self, override: AppConfig | None) -> AppConfig:
        """Fonde una config di progetto sopra questa (globale). L'override vince campo per campo."""
        if override is None:
            return self
        base = self.model_dump()
        over = override.model_dump()
        return AppConfig.model_validate(_deep_merge(base, over))


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def global_config_path() -> Path:
    return workspace_root() / CONFIG_FILENAME


def load_global_config() -> AppConfig:
    return AppConfig.load(global_config_path())
