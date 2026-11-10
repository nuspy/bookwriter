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
from llmswitch import TYPES, Registry
from pydantic import BaseModel, Field

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.10
    import tomli as _toml  # type: ignore[no-redefine]


DEFAULT_BASE_URL = "http://localhost:1234/v1"
#: Il servizio dei giudizi rapidi (Jev, TypeSafe) e il suo modello: i valori predefiniti, che
#: si cambiano in Impostazioni per un servizio compatibile (anche locale).
JEV_DEFAULT_URL = "https://api.typesafe.ai/v1/systemone"
JEV_DEFAULT_MODEL = "jev-latest"
CONFIG_FILENAME = "config.toml"

# Fornitori del servizio di intelligenza artificiale, presi dal registro comune di ``llmswitch``:
# è lì che vivono indirizzi, chiavi e note di ogni fornitore, condivisi con gli altri progetti.
# Qui si tiene solo ciò che riguarda bookwriter — quali sanno parlare la lingua che il client
# usa, e come chiamarli in modo comprensibile a chi non conosce i nomi commerciali.
#
# Restano fuori i fornitori che questo programma non saprebbe interrogare: quelli che si usano
# lanciando un CLI (``api: "cli"``) invece di rispondere a un indirizzo, e il dialetto nativo di
# Anthropic, che non è quello di OpenAI. Elencarli servirebbe solo a farli scegliere per poi
# vederli fallire.
_API_COMPRESE = frozenset({"openai", "lmstudio", "vllm", "gemini"})


def _etichetta(spec: dict[str, Any]) -> str:
    """Nome del fornitore con l'indicazione di dove gira e se costa: è la prima cosa da capire."""
    base = str(spec.get("base_url") or "")
    in_casa = bool(spec.get("local")) or "localhost" in base or "127.0.0.1" in base
    if in_casa:
        # Anche in casa può servire una chiave: Unsloth ne stampa una all'avvio.
        dove = "sul tuo computer" + (", con chiave" if spec.get("auth") != "none" else " (gratis)")
    elif spec.get("auth") == "oauth":
        dove = "con il tuo abbonamento"
    else:
        dove = "nel cloud (a consumo)"
    return f"{spec.get('label', '?')} — {dove}"


PROVIDERS: dict[str, dict[str, Any]] = {
    nome: {
        "label": _etichetta(spec),
        # Gemini parla la lingua di OpenAI solo sul suo endpoint dedicato.
        "base_url": spec.get("chat_base_url") or spec.get("base_url") or "",
        "needs_key": spec.get("auth") in ("api_key", "either"),
        "key_env": spec.get("key_env", ""),
        "console": spec.get("console", ""),
        "note": spec.get("note", ""),
    }
    for nome, spec in TYPES.items()
    if spec.get("api") in _API_COMPRESE
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
    #: Il modello dei **controlli** della stesura (continuità del piano, registro degli eventi,
    #: etichette del significato, griglia): vuoto = lo stesso che scrive. Un giudizio dello
    #: stesso modello che ha scritto non è una verifica indipendente; un altro modello sì, se
    #: l'autore ne ha uno. L'applicazione non lo carica: lo chiede al server, come gli altri.
    review: str = ""

    @property
    def checker(self) -> str:
        """Il modello dei controlli: ``review`` se impostato, altrimenti quello che scrive."""
        return self.review or self.reasoning

    @property
    def quality(self) -> str:
        """Modello per i passaggi di qualità (coerenza, critic): ``heavy`` se impostato."""
        return self.heavy or self.reasoning


#: Come si chiede il JSON: lo schema nel formato della richiesta, un JSON qualsiasi, o solo testo.
STRUCTURED_MODES = ("json_schema", "json_object", "text")


class LLMConfig(BaseModel):
    provider: str = "lmstudio"  # chiave di PROVIDERS (per un server personalizzato: il suo tipo)
    #: L'id di un server personalizzato nel registro di ``llmswitch`` (vuoto = fornitore predefinito).
    #: Indirizzo e chiave di quel server si prendono da lì (vedi :mod:`bookwriter.llm.servers`).
    provider_id: str = ""
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""  # richiesta dai fornitori cloud; vince la variabile d'ambiente se presente
    models: ModelRoles = Field(default_factory=ModelRoles)
    # Una risposta lunga (es. atomizzazione a copertura piena) può richiedere molti minuti,
    # tanto più con più richieste in parallelo sulla stessa GPU: timeout generoso.
    request_timeout_s: float = 900.0
    max_retries: int = 3
    concurrency: int = 4  # richieste LLM parallele (embedding/atomizzazione)
    # Manager delle istanze vLLM (vllm_gui). Serve a sapere se un modello è già vivo lì: in quel
    # caso si parla con lui invece di svegliarne un altro. Vuoto = non lo si interroga.
    vllm_manager_url: str = "http://127.0.0.1:7860"
    # --- campionamento: lo decide l'utente, non il programma ------------------------------
    # Vuoto (None) = non si manda: vale quello che il server ha configurato per il suo modello
    # (Basalt, LM Studio…). Se un valore è scritto qui vale per TUTTE le richieste. Il codice non
    # impone nessuna temperatura: fino a oggi ogni richiesta «a schema» partiva con
    # ``temperature: 0.0``, che un server serio legge come decodifica greedy e che sovrascrive il
    # suo valore (Basalt: temperature=1.0, top_p=0.95, top_k=20), e scrittura e conversazione
    # avevano temperature fisse (0,3-0,8). Per i modelli che ragionano la greedy è tra le cause
    # note del ciclo senza fine (qui non provata con un confronto: lo mostrano solo i log del
    # server). Chi vuole la greedy scrive ``temperature = 0``.
    temperature: float | None = Field(default=None, ge=0, le=5)
    top_p: float | None = Field(default=None, gt=0, le=1)
    top_k: int | None = Field(default=None, ge=1)
    min_p: float | None = Field(default=None, ge=0, le=1)
    presence_penalty: float | None = Field(default=None, ge=0, le=5)
    repetition_penalty: float | None = Field(default=None, gt=0, le=5)
    # --- l'applicazione non carica mai un modello -----------------------------------------
    # Si guarda cosa c'è in memoria e si manda la richiesta; se è vuota la carica il server
    # (Basalt, LM Studio con il caricamento a richiesta…). Per questo non ci sono qui né
    # ``load_models`` né i parametri di un caricamento (``context_length``, ``parallel``): un
    # vecchio config.toml che li contiene si legge lo stesso, e vengono ignorati.
    # Quanto far ragionare i modelli che ne sono capaci: "low", "medium", "high", oppure "" per
    # lasciarli liberi. **Medium**, e non è una scelta di velocità: misurato su brani veri del
    # libro, a ragionamento libero il modello esaurisce i token disponibili in ventimila
    # caratteri di riflessione e non consegna la traduzione — tre volte su cinque. Con medium
    # consegna sempre, in un terzo del tempo, e la resa regge il confronto parola per parola.
    # I modelli che non conoscono il parametro lo ignorano, e per loro non cambia nulla.
    reasoning_effort: str = "medium"
    #: Quando il server non ha le risorse per rispondere (RAM libera sotto la sua soglia, GPU
    #: occupata, modello in caricamento) la richiesta non è fallita: si aspetta e si ripete.
    #: Ogni quanti secondi si riprova, e dopo quanti secondi in tutto ci si arrende.
    resource_wait_s: float = Field(default=30.0, ge=1)
    resource_wait_max_s: float = Field(default=1800.0, ge=0)
    #: Quanto ragionano i **compiti rapidi** (giudizio di pertinenza di una pagina, interrogazioni
    #: di ricerca, riformulazioni, intento di ricerca nella chat): ``"same"`` (predefinito) = lo
    #: stesso livello di ``reasoning_effort``, cioè l'app non cambia niente da sola; altrimenti
    #: un livello ("low", "medium", "high"…) o "" per lasciar decidere al server. Il livello
    #: ``reasoning_effort`` resta quello di estrazione, conversazione e stesura.
    reasoning_effort_quick: str = "same"
    #: Quanto ragiona la **stesura** (piano delle scene, scrittura, passaggi editoriali, registro
    #: della continuità): ``"same"`` (predefinito) = lo stesso livello di ``reasoning_effort``;
    #: altrimenti un livello, o "" per lasciar decidere al server. Serve a chi tiene basso il
    #: livello generale per la ricerca ma vuole che il libro si scriva ragionando.
    reasoning_effort_writing: str = "same"
    #: Come si dichiara al server che la risposta deve essere JSON: ``json_schema`` (lo schema
    #: nel ``response_format``, predefinito), ``json_object`` (JSON qualsiasi, lo schema nel
    #: prompt) o ``text`` (solo il prompt). Per un server personalizzato vale quello della sua
    #: voce nel registro (vedi :meth:`structured_mode`). In ogni caso il JSON si valida qui.
    structured_output: str = "json_schema"
    # Nessun tetto ai token generati: non si imposta da qui. Quanto può produrre un modello lo
    # decide chi governa il server, dove quel limite si vede accanto al contesto e alla memoria
    # che costa. Un tetto messo dall'applicazione taglia anche le risposte buone — è costato un
    # libro fallito a due brani dalla fine — e un `max_tokens` rimasto in un vecchio
    # config.toml viene semplicemente ignorato.

    def reasoning_for(self, quick: bool = False, writing: bool = False) -> str:
        """Il livello di ragionamento di un compito: quello rapido o quello della stesura, se è
        un compito di quel tipo e ne è stato scelto uno suo; altrimenti quello generale. ``""`` =
        lascia decidere al server."""
        if writing and self.reasoning_effort_writing != "same":
            return self.reasoning_effort_writing
        if quick and self.reasoning_effort_quick != "same":
            return self.reasoning_effort_quick
        return self.reasoning_effort

    def structured_mode(self) -> str:
        """Il modo in cui chiedere il JSON a questo fornitore, tra ``STRUCTURED_MODES``.

        Un server personalizzato lo dice nella sua voce del registro (così una modifica fatta
        nella finestra dei server vale subito); per gli altri vale il campo della configurazione.
        """
        valore = self.structured_output
        if self.provider_id:
            try:
                voce = next((p for p in Registry().load().get("providers", [])
                             if p.get("id") == self.provider_id), None)
                valore = str((voce or {}).get("structured_output") or valore)
            except Exception:
                pass
        return valore if valore in STRUCTURED_MODES else "json_schema"

    def resolved_api_key(self) -> str:
        """Chiave in uso, cercata in tre posti nell'ordine che rispetta chi decide cosa.

        1. la variabile d'ambiente del fornitore: chi la imposta lo fa per questa esecuzione,
           e deve vincere su tutto;
        2. il file di configurazione del progetto: è dove le chiavi stavano prima, e chi ce
           l'ha scritta continua a usarla senza dover fare niente;
        3. il registro comune di ``llmswitch``: unico posto per tutti i progetti, fuori dalle
           loro cartelle e quindi fuori dai commit. È qui che finiscono le chiavi nuove.
        """
        if self.provider_id:
            # Un server personalizzato ha la sua chiave, nella sua voce del registro: la
            # variabile d'ambiente del tipo (OPENAI_API_KEY…) è di un altro servizio e non deve
            # finire in un server che non c'entra.
            if self.api_key.strip():
                return self.api_key.strip()
            try:
                voce = next((p for p in Registry().load().get("providers", [])
                             if p.get("id") == self.provider_id), None)
                return str((voce or {}).get("api_key") or "").strip()
            except Exception:
                return ""
        env_name = str(PROVIDERS.get(self.provider, {}).get("key_env") or "")
        from_env = (os.environ.get(env_name) or "").strip() if env_name else ""
        if from_env or self.api_key.strip():
            return from_env or self.api_key.strip()
        try:
            return Registry().api_key_for(self.provider)
        except Exception:
            # Il registro è una comodità, non una dipendenza: se è illeggibile si resta senza
            # chiave, e chi la richiede lo dirà con parole sue.
            return ""


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
    #: dove gira la trascrizione dei file audio importati: «cpu» o «cuda». Lo sceglie l'autore
    #: nel passo Importa; la GPU è molto più veloce ma occupa memoria accanto al modello.
    audio_device: str = "cpu"


#: Motori di ricerca utilizzabili nella fase di ricerca. Il primo non chiede nulla a nessuno;
#: gli altri vogliono una chiave, e in cambio danno risultati migliori e nessun blocco.
SEARCH_ENGINES: list[tuple[str, str]] = [
    ("duckduckgo", "DuckDuckGo — senza chiave, va subito"),
    ("brave", "Brave Search — con chiave (BRAVE_API_KEY)"),
    ("tavily", "Tavily — con chiave (TAVILY_API_KEY), pensato per gli assistenti"),
    ("searxng", "SearXNG — la tua istanza, indirizzo da indicare"),
]


#: L'indirizzo di TRAWL quando lo avvia l'applicazione.
LOCAL_FETCHER = "http://127.0.0.1:8191/v1"


class ResearchSettings(BaseModel):
    """Ricerca sul web: la fase 3 va in rete, e questo programma finora non ci andava mai.

    Resta **spenta** finché non la si accende: chi lavora su una macchina staccata, o su
    materiale che non deve uscire di lì, non deve scoprire il traffico di rete dopo.
    """

    enabled: bool = False
    engine: str = "duckduckgo"  # chiave di SEARCH_ENGINES
    api_key: str = ""  # vince la variabile d'ambiente del motore, se c'è
    instance_url: str = ""  # solo per searxng: l'indirizzo della propria istanza
    max_results: int = 8  # risultati per interrogazione
    max_pages: int = 4  # quante pagine leggere per intero fra quelle trovate
    #: Giri di approfondimento per ogni questione rimasta aperta: a ogni giro interrogazioni
    #: riformulate e più risultati. Ci si ferma prima se un giro non porta niente di nuovo.
    deepen_rounds: int = 3
    #: Quanta prosa deve restare, dopo la pulizia, perché una pagina sia «leggibile». Sotto, la
    #: pagina si registra come «non leggibile» e non va mai al modello.
    min_page_chars: int = 400
    #: Soglia (0-1) del giudizio del modello sulla pertinenza di una pagina: una breve chiamata per
    #: ogni pagina pulita («serve a questo punto?», in qualunque lingua). Sotto, la pagina si
    #: registra come «non pertinente» e non si usa. Se il modello sbaglia, la pagina resta. 0 =
    #: nessun filtro.
    min_relevance: float = Field(default=0.25, ge=0, le=1)
    #: Domini da cui non si scarica niente (il risultato si registra come «escluso»). ``amazon.*``
    #: vale per ogni paese. Si modifica in Impostazioni, accanto alla ricerca.
    excluded_domains: list[str] = Field(default_factory=lambda: [
        "youtube.com", "youtu.be", "youtube-nocookie.com", "amazon.*"])
    timeout_s: float = 20.0
    user_agent: str = "bookwriter/0.1 (ricerca editoriale)"
    #: Servizio esterno compatibile con FlareSolverr (/v1) per le pagine bloccate.
    #: Vuoto = spento. Si attiva impostando l'indirizzo (es. http://127.0.0.1:8191/v1).
    external_fetcher_url: str = ""
    external_fetcher_timeout_s: float = 60.0
    #: Giudizi rapidi con Jev (TypeSafe, typesafe.ai): pertinenza delle pagine e scelta delle sezioni
    #: migliori nelle pagine lunghe e nei PDF. **Spento** per impostazione. Fuori dal computer
    #: vanno solo estratti (al più ``jev_max_chars`` caratteri) e il punto del capitolo.
    #: La chiave si può anche dare con la variabile d'ambiente ``TYPESAFE_API_KEY``.
    jev_enabled: bool = False
    jev_api_key: str = ""
    #: Il servizio dei giudizi rapidi: l'API «System One» di TypeSafe (``state`` + domande
    #: tipizzate → punteggi). Indirizzo e modello si cambiano qui per usare un servizio
    #: compatibile, anche locale: con un indirizzo locale la chiave non è obbligatoria.
    jev_url: str = JEV_DEFAULT_URL
    jev_model: str = JEV_DEFAULT_MODEL
    #: Richieste insieme a Jev (le sezioni di una pagina lunga si giudicano in parallelo).
    jev_parallel: int = Field(default=4, ge=1, le=16)
    #: Sotto questo punteggio (scala 0-3 di Jev: 0 fuori tema, 1 appena legata, 2 utile, 3 molto
    #: utile) una pagina è «non pertinente».
    jev_min_score: float = Field(default=0.8, ge=0, le=3)
    jev_max_chars: int = Field(default=6000, ge=200, le=50000)
    jev_timeout_s: float = Field(default=20.0, ge=1)
    #: Avvia automaticamente il servizio browser locale (TRAWL) all'avvio dell'app.
    auto_start_browser: bool = True


class UISettings(BaseModel):
    """Preferenze dell'interfaccia, valide per tutti i progetti."""

    language: str = "it"  # codice di UI_LANGUAGES
    last_export_dir: str = ""  # cartella dell'ultimo salvataggio, riproposta la volta dopo
    #: l'aspetto: ``auto`` (segue il sistema), ``light`` (giorno) o ``dark`` (notte)
    theme: str = "auto"


class ProjectSettings(BaseModel):
    """Preferenze di contenuto del progetto."""

    language: str = ""  # codice ISO; vuoto = stessa lingua dei testi importati


#: Misure di taglio offerte davvero dalla stampa su richiesta, con il nome che usa chi stampa.
#: A4 è in fondo e con l'avvertenza: non è un formato da libro, è un foglio.
TRIM_SIZES: list[tuple[str, str]] = [
    ("a5", "A5 · 148×210 mm — manuali e saggi, il formato europeo"),
    ("b-format", "B-format · 129×198 mm — tascabile inglese, narrativa"),
    ("5x8", "5×8 pollici · 127×203 mm — tascabile americano"),
    ("5.5x8.5", "5,5×8,5 pollici · 140×216 mm — saggistica KDP"),
    ("6x9", "6×9 pollici · 152×229 mm — il formato KDP più sicuro"),
    ("7x10", "7×10 pollici · 178×254 mm — eserciziari, tabelle"),
    ("a4", "A4 · 210×297 mm — non è un formato da libro: è un PDF da stampare"),
]


class ProductionSettings(BaseModel):
    """Come si fa il libro come oggetto: misura, caratteri, marchio.

    Vale per ogni schermata che produce un libro nuovo — la stesura da un indice, la riscrittura,
    la traduzione, le sette fasi. Un progetto che deve uscire in 6×9 pollici lo dice una volta
    qui, e ogni file esportato esce di quella misura.
    """

    trim: str = "a5"  # chiave di TRIM_SIZES
    serif: str = ""  # famiglia del testo; vuota = la prima installata fra le preferite
    sans: str = ""  # famiglia dei titoli correnti e del display
    hyphenation: str = "auto"  # auto | it_IT | en_GB | off
    justify: bool = True
    publisher: str = ""  # marchio editoriale, o «Edizione dell'autore»
    author: str = ""  # nome dell'autore come va stampato
    rights: str = ""  # vuoto = la formula standard della lingua

    def overrides(self) -> dict[str, Any]:
        """Le stesse decisioni nella forma di ``book.yaml``, per il compositore."""
        tipografia: dict[str, Any] = {"justify": self.justify}
        if self.serif:
            tipografia["serif"] = self.serif
        if self.sans:
            tipografia["sans"] = self.sans
        if self.hyphenation and self.hyphenation != "auto":
            tipografia["hyphenation"] = None if self.hyphenation == "off" else self.hyphenation
        libro = {k: v for k, v in (("publisher", self.publisher), ("rights", self.rights)) if v}
        return {"book": libro, "format": {"preset": self.trim}, "typography": tipografia}


class WritingSettings(BaseModel):
    """Come si scrive un capitolo lungo."""

    #: Oltre questo budget un capitolo si scrive a sezioni, una chiamata ciascuna: una risposta
    #: del modello non basta per settemila parole.
    section_words: int = 1500


class QualitySettings(BaseModel):
    """La verifica della qualità (vedi :mod:`bookwriter.forge.quality`)."""

    #: Il voto globale (0-10) da cui un capitolo è accettato.
    min_global: float = Field(default=7.0, ge=0, le=10)
    #: Il voto minimo di ogni criterio critico per il tipo di libro.
    min_critical: float = Field(default=6.0, ge=0, le=10)
    #: I giri di correzione automatici; oltre, ogni giro lo autorizza l'autore.
    max_rounds: int = Field(default=3, ge=0, le=20)


class AppConfig(BaseModel):
    """Configurazione completa (globale o di progetto)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    production: ProductionSettings = Field(default_factory=ProductionSettings)
    research: ResearchSettings = Field(default_factory=ResearchSettings)
    writing: WritingSettings = Field(default_factory=WritingSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
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
            # I valori vuoti (None, p.es. il campionamento «lascia al server») non si scrivono:
            # TOML non ha il nulla, e assente vuol dire proprio questo.
            tomli_w.dump(self.model_dump(mode="python", exclude_none=True), fh)

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
