"""Test della configurazione: default, save/load, merge di progetto."""

from __future__ import annotations

from pathlib import Path

from bookwriter.config import AppConfig, workspace_root


def test_workspace_root_uses_env(temp_workspace: Path) -> None:
    assert workspace_root() == temp_workspace


def test_defaults_match_lmstudio_setup() -> None:
    cfg = AppConfig()
    assert cfg.llm.models.reasoning == "qwen3-8b-instruct"
    assert cfg.llm.models.embedding == "text-embedding-nomic-embed-text-v1.5"
    assert cfg.embedding.max_tokens == 2048
    assert cfg.chunking.target_tokens < cfg.embedding.max_tokens
    assert cfg.embedding.doc_prefix.startswith("search_document")


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    cfg = AppConfig()
    cfg.llm.models.reasoning = "modello-x"
    path = tmp_path / "config.toml"
    cfg.save(path)
    loaded = AppConfig.load(path)
    assert loaded.llm.models.reasoning == "modello-x"


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    loaded = AppConfig.load(tmp_path / "assente.toml")
    assert loaded.llm.models.embedding == "text-embedding-nomic-embed-text-v1.5"


def test_merge_project_override_wins() -> None:
    global_cfg = AppConfig()
    override = AppConfig()
    override.llm.models.reasoning = "override-model"
    override.chunking.target_tokens = 900
    merged = global_cfg.merged_with(override)
    assert merged.llm.models.reasoning == "override-model"
    assert merged.chunking.target_tokens == 900
    # I campi non toccati restano ai default globali.
    assert merged.llm.models.embedding == "text-embedding-nomic-embed-text-v1.5"


def test_embedding_profile_matches_model_family() -> None:
    """I prefissi seguono la famiglia del modello: usarne altri peggiora la ricerca."""
    from bookwriter.config import embedding_profile

    nomic = embedding_profile("text-embedding-nomic-embed-text-v1.5")
    assert nomic is not None and nomic.doc_prefix == "search_document: "

    bge = embedding_profile("bge-m3")
    assert bge is not None and bge.doc_prefix == "" and bge.max_tokens == 8192

    e5 = embedding_profile("intfloat/multilingual-e5-large")
    assert e5 is not None and e5.doc_prefix == "passage: " and e5.query_prefix == "query: "

    qwen = embedding_profile("Qwen/Qwen3-Embedding-0.6B-GGUF")
    assert qwen is not None and qwen.doc_prefix == "" and "Query:" in qwen.query_prefix

    assert embedding_profile("modello-sconosciuto") is None
