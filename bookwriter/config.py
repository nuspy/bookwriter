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

# Fornitori del servizio di intelligenza artificiale. Tutti espongono l'API in stile OpenAI
# (``/v1/chat/completions``, ``/v1/embeddings``, ``/v1/models``), quindi cambia solo l'indirizzo,
# la chiave e il modo in cui si legge l'elenco dei modelli.
PROVIDERS: dict[str, dict[str, Any]] = {
    "lmstudio": {
        "label": "LM Studio — sul tuo computer (gratis)",
        "base_url": DEFAULT_BASE_URL,
        "needs_key": False,
        "key_env": "",
        "console": "",
    },
    "nebius": {
        "label": "Nebius Token Factory — nel cloud (a consumo)",
        "base_url": "https://api.tokenfactory.nebius.com/v1",
        "needs_key": True,
        "key_env": "NEBIUS_API_KEY",
        "console": "https://tokenfactory.nebius.com/",
    },
}

# Lingue in cui il modello deve produrre i contenuti generati (sintesi, temi, prosa).
# Selezione delle principali fra quelle padroneggiate da Qwen 3.5; la stringa vuota
# significa "usa la stessa lingua del testo di partenza".
SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    ("", "come il testo di partenza"),
    ("it", "italiano"),
    ("en", "inglese (English)"),
    ("hu", "ungherese (magyar)"),
    ("de", "tedesco (Deutsch)"),
    ("fr", "francese (français)"),
    ("es", "spagnolo (español)"),
    ("pt", "portoghese (português)"),
    ("nl", "olandese (Nederlands)"),
    ("pl", "polacco (polski)"),
    ("cs", "ceco (čeština)"),
    ("sk", "slovacco (slovenčina)"),
    ("sl", "sloveno (slovenščina)"),
    ("hr", "croato (hrvatski)"),
    ("sr", "serbo (srpski)"),
    ("ro", "rumeno (română)"),
    ("bg", "bulgaro (български)"),
    ("el", "greco (ελληνικά)"),
    ("ru", "russo (русский)"),
    ("uk", "ucraino (українська)"),
    ("tr", "turco (Türkçe)"),
    ("sv", "svedese (svenska)"),
    ("da", "danese (dansk)"),
    ("no", "norvegese (norsk)"),
    ("fi", "finlandese (suomi)"),
    ("et", "estone (eesti)"),
    ("lv", "lettone (latviešu)"),
    ("lt", "lituano (lietuvių)"),
    ("ar", "arabo (العربية)"),
    ("he", "ebraico (עברית)"),
    ("hi", "hindi (हिन्दी)"),
    ("zh", "cinese semplificato (简体中文)"),
    ("ja", "giapponese (日本語)"),
    ("ko", "coreano (한국어)"),
    ("vi", "vietnamita (Tiếng Việt)"),
    ("th", "thai (ไทย)"),
    ("id", "indonesiano (Bahasa Indonesia)"),
]


def language_label(code: str) -> str:
    """Nome leggibile della lingua (usato nei prompt); il codice stesso se sconosciuto."""
    return dict(SUPPORTED_LANGUAGES).get(code, code)


def language_tag(value: str) -> str:
    """Sigla breve per i nomi dei file: «ungherese (magyar)» → HU, «en» → EN, «inglese» → EN."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    for code, label in SUPPORTED_LANGUAGES:
        if code and (text == code or text in label.lower()):
            return code.upper()
    letters = "".join(ch for ch in text if ch.isalpha())
    return letters[:3].upper()


def workspace_root() -> Path:
    """Cartella radice del workspace (multi-progetto).

    Override con la variabile d'ambiente ``BOOKWRITER_HOME`` (utile per i test).
    Default: ``~/.bookwriter``.
    """
    env = os.environ.get("BOOKWRITER_HOME")
    return Path(env).expanduser() if env else Path.home() / ".bookwriter"


class ModelRoles(BaseModel):
    """Modelli LM Studio assegnati per ruolo. ``heavy`` è opzionale (job di alta qualità)."""

    reasoning: str = "qwen3-8b-instruct"
    embedding: str = "text-embedding-nomic-embed-text-v1.5"
    heavy: str = ""

    @property
    def quality(self) -> str:
        """Modello per i passaggi di qualità (coerenza, critic): ``heavy`` se impostato."""
        return self.heavy or self.reasoning


class LLMConfig(BaseModel):
    provider: str = "lmstudio"  # chiave di PROVIDERS
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""  # richiesta dai fornitori cloud; vince la variabile d'ambiente se presente
    models: ModelRoles = Field(default_factory=ModelRoles)
    # Una risposta lunga (es. atomizzazione a copertura piena) può richiedere molti minuti,
    # tanto più con più richieste in parallelo sulla stessa GPU: timeout generoso.
    request_timeout_s: float = 900.0
    max_retries: int = 3
    concurrency: int = 4  # richieste LLM parallele (embedding/atomizzazione)

    def resolved_api_key(self) -> str:
        """Chiave in uso: la variabile d'ambiente del fornitore ha la precedenza sul file."""
        env_name = str(PROVIDERS.get(self.provider, {}).get("key_env") or "")
        from_env = (os.environ.get(env_name) or "").strip() if env_name else ""
        return from_env or self.api_key.strip()


class EmbeddingConfig(BaseModel):
    """nomic-embed-text-v1.5: 768 dim, contesto max 2048 token, richiede prefissi task."""

    max_tokens: int = 2048
    doc_prefix: str = "search_document: "
    query_prefix: str = "search_query: "


# Ogni famiglia di modelli di embedding vuole i suoi prefissi: usare quelli di un'altra
# famiglia peggiora i risultati, perché il prefisso finisce dentro al testo da rappresentare.
# La chiave è un pezzo del nome del modello (confronto in minuscolo).
EMBEDDING_PROFILES: list[tuple[str, EmbeddingConfig]] = [
    ("nomic-embed-text", EmbeddingConfig(max_tokens=2048, doc_prefix="search_document: ", query_prefix="search_query: ")),
    ("bge-m3", EmbeddingConfig(max_tokens=8192, doc_prefix="", query_prefix="")),
    ("bge-", EmbeddingConfig(max_tokens=512, doc_prefix="", query_prefix="Represent this sentence for searching relevant passages: ")),
    ("multilingual-e5", EmbeddingConfig(max_tokens=512, doc_prefix="passage: ", query_prefix="query: ")),
    ("e5-", EmbeddingConfig(max_tokens=512, doc_prefix="passage: ", query_prefix="query: ")),
    ("qwen3-embedding", EmbeddingConfig(
        max_tokens=8192, doc_prefix="",
        query_prefix="Instruct: Given a search query, retrieve relevant passages\nQuery: ",
    )),
    ("gte-", EmbeddingConfig(max_tokens=8192, doc_prefix="", query_prefix="")),
    ("jina-embeddings", EmbeddingConfig(max_tokens=8192, doc_prefix="", query_prefix="")),
]


def embedding_profile(model_id: str) -> EmbeddingConfig | None:
    """Prefissi e finestra adatti al modello di embedding scelto; ``None`` se sconosciuto."""
    name = (model_id or "").lower()
    for marker, profile in EMBEDDING_PROFILES:
        if marker in name:
            return profile.model_copy()
    return None


class ChunkingConfig(BaseModel):
    """Budget del chunking deterministico. Sotto il cap embedding (2048) con margine."""

    target_tokens: int = 1500
    overlap_sentences: int = 1


class UISettings(BaseModel):
    """Preferenze dell'interfaccia, valide per tutti i progetti."""

    language: str = "it"  # codice di UI_LANGUAGES
    last_export_dir: str = ""  # cartella dell'ultimo salvataggio, riproposta la volta dopo


class ProjectSettings(BaseModel):
    """Preferenze di contenuto del progetto."""

    language: str = ""  # codice ISO; vuoto = stessa lingua dei testi importati


class AppConfig(BaseModel):
    """Configurazione completa (globale o di progetto)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    ui: UISettings = Field(default_factory=UISettings)

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
