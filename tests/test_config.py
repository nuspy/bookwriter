"""Test della configurazione: default, save/load, merge di progetto."""

from __future__ import annotations

from pathlib import Path

from bookwriter.config import AppConfig, workspace_root


def test_workspace_root_uses_env(temp_workspace: Path) -> None:
    assert workspace_root() == temp_workspace


def test_defaults_match_lmstudio_setup() -> None:
    cfg = AppConfig()
    assert cfg.llm.models.reasoning == "gemma-4-12b-obliterated"
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
