"""Test della configurazione: default, save/load, merge di progetto."""

from __future__ import annotations

from pathlib import Path

import pytest

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


# ---- fornitori presi dal registro comune (llmswitch) --------------------------------------


def test_providers_come_from_the_shared_registry() -> None:
    """I fornitori non si riscrivono qui: vengono dalla libreria comune, con i suoi indirizzi."""
    from llmswitch import TYPES

    from bookwriter.config import PROVIDERS

    assert PROVIDERS, "senza fornitori l'applicazione non sa a chi parlare"
    assert set(PROVIDERS) <= set(TYPES), "nessun fornitore inventato qui dentro"
    assert PROVIDERS["lmstudio"]["base_url"] == TYPES["lmstudio"]["base_url"]


def test_providers_bookwriter_cannot_speak_to_are_left_out() -> None:
    """Chi si usa lanciando un CLI, o parla il dialetto di Anthropic, non va in elenco.

    Metterceli significherebbe farli scegliere per poi vederli fallire alla prima richiesta:
    il client di bookwriter manda HTTP in stile OpenAI e non sa fare altro.
    """
    from bookwriter.config import PROVIDERS

    assert "codex_cli" not in PROVIDERS
    assert "claude_cli" not in PROVIDERS
    assert "antigravity" not in PROVIDERS
    assert "claude" not in PROVIDERS  # dialetto nativo Anthropic, non OpenAI


def test_gemini_uses_its_openai_compatible_endpoint() -> None:
    """Gemini parla la lingua di OpenAI solo sull'indirizzo dedicato, non su quello di serie."""
    from bookwriter.config import PROVIDERS

    assert PROVIDERS["gemini"]["base_url"].endswith("/openai")


def test_every_paid_provider_says_where_to_get_the_key() -> None:
    """Se serve una chiave, l'utente dev'essere in grado di trovarla senza cercare a caso."""
    from bookwriter.config import PROVIDERS

    senza_indicazioni = [
        nome for nome, spec in PROVIDERS.items()
        if spec["needs_key"] and not (spec["console"] or spec["key_env"])
    ]
    assert senza_indicazioni == ["openai_compat"], "il generico non ha una console sua"


def test_the_label_says_where_it_runs_and_what_it_costs() -> None:
    from bookwriter.config import PROVIDERS

    assert "sul tuo computer" in PROVIDERS["lmstudio"]["label"]
    assert "a consumo" in PROVIDERS["deepseek"]["label"]
    assert "abbonamento" in PROVIDERS["qwen_oauth"]["label"]


def test_the_key_environment_variable_still_wins(monkeypatch) -> None:
    """La variabile d'ambiente del fornitore ha la precedenza sulla chiave scritta nel file."""
    from bookwriter.config import LLMConfig

    monkeypatch.setenv("DEEPSEEK_API_KEY", "quella-di-ambiente")
    cfg = LLMConfig(provider="deepseek", api_key="quella-del-file")
    assert cfg.resolved_api_key() == "quella-di-ambiente"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    assert cfg.resolved_api_key() == "quella-del-file"


# ---- chiavi: tre posti, un ordine che rispetta chi decide cosa ----------------------------


@pytest.fixture()
def registro_isolato(tmp_path, monkeypatch):
    """Registro comune finto: i test non devono leggere né scrivere quello vero dell'utente."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from llmswitch import Registry

    return Registry()


def test_the_key_can_come_from_the_shared_registry(registro_isolato, monkeypatch) -> None:
    """Chiave messa una volta, valida per tutti i progetti e per gli altri programmi."""
    from bookwriter.config import LLMConfig

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    registro_isolato.add({"type": "deepseek", "api_key": "sk-dal-registro"})
    assert LLMConfig(provider="deepseek").resolved_api_key() == "sk-dal-registro"


def test_the_project_file_still_wins_over_the_registry(registro_isolato, monkeypatch) -> None:
    """Chi ha già una chiave nel suo config.toml non deve accorgersi di nulla."""
    from bookwriter.config import LLMConfig

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    registro_isolato.add({"type": "deepseek", "api_key": "sk-dal-registro"})
    cfg = LLMConfig(provider="deepseek", api_key="sk-dal-file")
    assert cfg.resolved_api_key() == "sk-dal-file"


def test_the_environment_wins_over_everything(registro_isolato, monkeypatch) -> None:
    """Chi imposta la variabile lo fa per questa esecuzione: deve avere l'ultima parola."""
    from bookwriter.config import LLMConfig

    registro_isolato.add({"type": "deepseek", "api_key": "sk-dal-registro"})
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-di-ambiente")
    cfg = LLMConfig(provider="deepseek", api_key="sk-dal-file")
    assert cfg.resolved_api_key() == "sk-di-ambiente"


def test_a_key_for_another_provider_is_not_used(registro_isolato, monkeypatch) -> None:
    """Le chiavi non sono intercambiabili: quella di DeepSeek su Kimi darebbe solo un 401."""
    from bookwriter.config import LLMConfig

    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    registro_isolato.add({"type": "deepseek", "api_key": "sk-dal-registro"})
    assert LLMConfig(provider="kimi").resolved_api_key() == ""


def test_an_unreadable_registry_does_not_break_anything(tmp_path, monkeypatch) -> None:
    """Il registro è una comodità: se è rotto si resta senza chiave, non si esplode."""
    from bookwriter.config import LLMConfig

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    rovinato = tmp_path / "llmswitch" / "llm_providers.json"
    rovinato.parent.mkdir(parents=True)
    rovinato.write_text("{ questo non è JSON", encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert LLMConfig(provider="deepseek").resolved_api_key() == ""
