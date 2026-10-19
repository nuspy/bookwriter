"""Client per LM Studio (API OpenAI-compatibile su ``/v1``, più API nativa ``/api/v0``).

Fornisce:
- ``health`` / ``list_models`` (stato e contesto dei modelli, via API nativa di LM Studio);
- ``chat`` (completamento testuale);
- ``chat_json`` (JSON chiesto nel prompt, con lo schema, ricevuto in streaming e **validato
  qui**: niente ``response_format``, che certi server trattengono e ritentano in silenzio);
- ``embed`` (embeddings con i prefissi task richiesti da nomic).

Include retry con backoff sulle 5xx e sugli errori di rete.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from llmswitch import TYPES, LmStudioConfig, LocalEngines, Resolution, VllmConfig, same_model
from llmswitch import reasoning as _reasoning

from bookwriter.config import EmbeddingConfig, LLMConfig
from bookwriter.llm import jsoncheck


class LLMError(RuntimeError):
    """Errore nell'interazione con LM Studio."""


class LLMCancelled(BaseException):
    """L'autore ha premuto «Interrompi»: la connessione è stata chiusa, la richiesta non finisce.

    Deriva da ``BaseException`` di proposito: i tanti ``except Exception`` che fanno proseguire un
    lavoro dopo un guasto (una pagina che non si legge, un'estrazione fallita) non devono
    scambiare un'interruzione voluta per un guasto e andare avanti.
    """


class LLMSilence(LLMError):
    """Dal server non è arrivato niente per troppo tempo. Non si ritenta: sarebbe uguale."""


class LLMResource(LLMError):
    """Il server non ha potuto servire la richiesta **per una mancanza di risorse sua**: RAM
    libera sotto la sua soglia, GPU occupata da un altro programma, modello che si sta
    caricando, troppe richieste. Non è un guasto della richiesta né del punto che si sta
    lavorando: la stessa richiesta, più tardi, andrà a buon fine. Per questo ``chat`` aspetta e
    la ripete (vedi ``LMStudioClient._with_resource_wait``) invece di contarla come fallita.

    ``kind``: ``"ram"``, ``"gpu"``, ``"busy"`` o ``"unavailable"``; ``detail``: il messaggio
    del server così com'è; ``args[0]``: la stessa cosa in parole.
    """

    def __init__(self, text: str, *, kind: str = "unavailable", detail: str = "",
                 free_mib: int | None = None, guard_mib: int | None = None) -> None:
        super().__init__(text)
        self.kind = kind
        self.detail = detail or text
        #: per ``kind == "ram"``: la RAM libera e la soglia sotto cui il server si ferma, in MiB
        self.free_mib = free_mib
        self.guard_mib = guard_mib


class LLMDegenerate(LLMError):
    """Il modello è entrato in ripetizione senza fine **nel testo che scrive**: la risposta non
    arriverà mai.

    Succede sui passaggi privi di contenuto linguistico (l'indice di un PDF, con le righe di
    puntini di guida): il modello continua a produrre la stessa sequenza finché non esaurisce il
    contesto. Aspettarlo costa l'intero timeout, e ritentare a temperatura 0 dà lo stesso esito.
    Il ragionamento non fa scattare questo errore: quanto e come un modello ragioni lo governa
    chi gestisce il server, non il programma che gli parla.
    """


# Una generazione sana avanza; una impazzita ripete. Si guarda solo la coda del testo prodotto,
# così il controllo costa uguale che siano cento o centomila token.
_TAIL_CHARS = 4000  # quanto testo recente si tiene per riconoscere la ripetizione
_WINDOW_CHARS = 600  # ampiezza della finestra osservata
_CHECK_EVERY = 64  # ogni quanti frammenti ricontrollare
# Oltre questa quantità di testo prodotto (ragionamento e risposta insieme) la ripetizione
# letterale nel testo scritto non è più spiegabile: chi lima una frase la riformula («Revised:
# …», «Let's make it punchier»), ma lo fa per qualche migliaio di caratteri, non per centomila.
_RUNAWAY_CHARS = 80_000
# Quanto a lungo un modello possa ragionare prima di rispondere, qui non si guarda: il pensiero
# lungo è il valore di questi modelli, e chi decide quanto ne vale la pena è chi governa il
# server — non il programma che gli parla. Un tetto imposto da qui toglieva la risposta anche a
# chi ci sarebbe arrivato. Resta la sola sorveglianza sulla ripetizione letterale del testo
# SCRITTO, che non è lentezza ma un flusso bloccato; il ragionamento, anche se si ripete, non
# chiude mai la richiesta: se serve fermarlo lo fa l'autore con «Interrompi».

# Ogni quanto rileggere quale modello è caricato. Cambia per un gesto dell'utente, non da solo,
# e un passaggio di traduzione dura minuti: mezzo minuto di memoria basta e avanza.
_SCOPERTA_TTL_S = 30.0

# Fornitori che girano su questa macchina, e per i quali «già in memoria» vuol dire qualcosa.
_MOTORI_LOCALI = frozenset({"lmstudio", "vllm"})


_RAM_BASSA = re.compile(r"RAM low\s*\((\d+)\s*MiB free\s*<\s*(\d+)\s*MiB guard\)", re.I)
_RISORSE = re.compile(
    r"RAM low|out of memory|\bOOM\b|VRAM|GPU is in use|GPU busy|model_busy|still running|"
    r"overloaded|rate.?limit|no model is loaded|standby|is loading|loading the model|"
    r"engine is starting", re.I)


def _server_error(message: str, *, status: int | None = None, kind: str = "") -> LLMError:
    """L'errore che il server ha dichiarato, in parole. Una mancanza di risorse sua è un
    :class:`LLMResource` (si aspetta); ogni altra cosa un :class:`LLMError` che riporta il
    messaggio vero, senza supposizioni sul motivo."""
    message = " ".join(str(message or "").split())
    ram = _RAM_BASSA.search(message)
    if ram:
        libera, soglia = (f"{int(n):,}".replace(",", ".") for n in ram.groups())
        return LLMResource(
            f"Il server dei modelli ha fermato la richiesta: ha poca RAM libera ({libera} MiB, "
            f"sotto la sua soglia di {soglia} MiB).", kind="ram", detail=message,
            free_mib=int(ram.group(1)), guard_mib=int(ram.group(2)))
    if status == 429 or _RISORSE.search(f"{kind} {message}"):
        gpu = bool(re.search(r"VRAM|GPU", message))
        return LLMResource(
            f"Il server dei modelli non può rispondere adesso: {message or 'occupato'}.",
            kind="gpu" if gpu else "busy", detail=message)
    return LLMError(f"Il server dei modelli ha risposto con un errore: {message or '(senza testo)'}")


def _error_in(body: Any) -> tuple[str, str] | None:
    """``(messaggio, tipo)`` se il corpo JSON (o un evento del flusso) è un errore del server."""
    if not isinstance(body, dict) or not body.get("error"):
        return None
    err = body["error"]
    if isinstance(err, dict):
        return str(err.get("message") or err.get("code") or ""), str(err.get("type") or "")
    return str(err), ""


class _Rapido:
    """Il client visto dai compiti rapidi: ogni ``chat`` e ``chat_json`` porta ``quick=True``."""

    def __init__(self, client: LMStudioClient) -> None:
        self._client = client

    def chat(self, messages, **kw):
        return self._client.chat(messages, quick=True, **kw)

    def chat_json(self, messages, **kw):
        return self._client.chat_json(messages, quick=True, **kw)

    def __getattr__(self, nome: str):
        return getattr(self._client, nome)


def quick(client):
    """``client`` per i compiti rapidi, se è il client vero; ogni altro (i finti dei test) com'è."""
    return client.quick() if isinstance(client, LMStudioClient) else client


@dataclass
class Destinazione:
    """Dove mandare la richiesta e con quale nome chiamare il modello."""

    base_url: str  # endpoint OpenAI-compatibile, già comprensivo di /v1
    model: str
    backend: str  # "lmstudio" | "vllm" | "" se lo dice solo la configurazione


def _looks_degenerate(tail: str, generated: int) -> bool:
    """True se il modello sta ripetendo senza fine invece di procedere.

    Si applica al testo scritto dal modello, mai al suo ragionamento (vedi ``_chat_request``).
    Due segnali, tarati per non toccare mai una generazione legittima:

    - alfabeto poverissimo nella finestra osservata (« .  .  .  . »): non è prosa in nessuna
      lingua, e vale subito, perché è il caso che blocca davvero (gli indici dei PDF);
    - stessa sequenza letterale ripetuta otto volte, ma **solo** quando il testo prodotto ha già
      passato ogni misura ragionevole: sotto quella soglia la ripetizione è il modo normale in
      cui questi modelli affinano una frase, e interromperli lì rovinerebbe il risultato.
    """
    if len(tail) < _WINDOW_CHARS:
        return False
    window = tail[-_WINDOW_CHARS:]
    if len(set(window)) <= 4:
        return True
    if generated < _RUNAWAY_CHARS:
        return False
    return tail.count(window[-200:]) >= 8


@dataclass
class ModelInfo:
    id: str
    type: str  # llm | vlm | embeddings
    state: str  # loaded | not-loaded
    max_context_length: int | None = None
    loaded_context_length: int | None = None
    capabilities: list[str] = field(default_factory=list)
    # Prezzi in dollari per milione di token (solo fornitori a consumo).
    price_in: float | None = None
    price_out: float | None = None
    # Peso e quantizzazione dei modelli locali, da llmswitch. Lo stesso modello sta su disco in
    # più versioni che pesano diversamente, e su una scheda da 32 GB la differenza fra 17 e 22
    # decide se il modello ci sta o finisce a metà nella memoria condivisa.
    size_h: str = ""
    quant: str = ""

    @property
    def loaded(self) -> bool:
        return self.state == "loaded"

    def size_label(self) -> str:
        """Peso e quantizzazione in forma leggibile; vuoto se non si sanno."""
        return " · ".join(p for p in (self.quant, self.size_h) if p)

    def price_label(self) -> str:
        """Costo leggibile, vuoto se il fornitore non espone prezzi (es. modelli locali)."""
        if self.price_in is None and self.price_out is None:
            return ""
        parts = []
        if self.price_in is not None:
            parts.append(f"in ${self.price_in:.2f}")
        if self.price_out:
            parts.append(f"out ${self.price_out:.2f}")
        return f"{' / '.join(parts)} per 1M token"


def _per_million(value: object) -> float | None:
    """Da prezzo per token (stringa) a prezzo per milione di token."""
    try:
        return round(float(str(value)) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


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
        key = config.resolved_api_key()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = httpx.Client(timeout=config.request_timeout_s, headers=headers)
        # Con un abbonamento non c'è una chiave fissa da mettere qui: il token scade e va
        # rinnovato, quindi le intestazioni si rifanno prima di ogni richiesta.
        self._ad_abbonamento = TYPES.get(config.provider, {}).get("auth") == "oauth"
        # Contatori token (thread-safe) per seguire i consumi anche mentre il lavoro è in corso.
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        self._usage_by_model: dict[str, dict[str, int]] = {}
        self._usage_lock = threading.Lock()
        # Cosa è in memoria adesso, per modello preferito, e da quando lo sappiamo: vedi
        # ``resolve_target``. La domanda la fa llmswitch, qui si ricorda la risposta.
        self._motori: LocalEngines | None = None
        self._motori_lock = threading.Lock()
        self._caricati: dict[str, Resolution | None] = {}
        self._caricati_letti_a = 0.0
        self._caricati_lock = threading.Lock()
        # Sostituzioni fatte: {nome chiesto: nome usato}. Da mostrare, non da nascondere.
        self.model_swaps: dict[str, str] = {}
        # «Interrompi»: chi lo preme alza questo segnale e la richiesta in corso chiude la
        # connessione invece di aspettare la fine (vedi ``_lines``).
        self.cancel_event = threading.Event()
        # Chi vuole sapere cosa fa il modello mentre lavora (la scheda «Scrivi da zero» lo mostra):
        # riceve ``(caratteri di ragionamento, caratteri di risposta)``, con parsimonia, e
        # ``(0, 0)`` quando comincia una richiesta. Viene chiamato dal thread del lavoro.
        self.activity_hook: Callable[[int, int], None] | None = None
        # Chi vuole sapere che il lavoro è fermo ad aspettare il server (poca RAM, GPU occupata):
        # riceve ``(errore, secondi che mancano al prossimo tentativo)`` ogni secondo, e
        # ``(None, 0)`` quando l'attesa finisce. Viene chiamato dal thread del lavoro.
        self.wait_hook: Callable[[LLMResource | None, int], None] | None = None
        #: perché si è fermato l'ultimo completamento («stop», «length»…), per chi lo vuole sapere.
        self.last_finish_reason = ""

    def _track(self, usage: dict | None, model: str) -> None:
        with self._usage_lock:
            self.usage["calls"] += 1
            per = self._usage_by_model.setdefault(
                model, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
            )
            per["calls"] += 1
            if not usage:
                return
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = int(usage.get(key, 0) or 0)
                self.usage[key] += value
                per[key] += value

    def usage_by_model(self) -> dict[str, dict[str, int]]:
        """Copia dei consumi divisi per modello: leggibile mentre le richieste sono in corso."""
        with self._usage_lock:
            return {model: dict(counts) for model, counts in self._usage_by_model.items()}

    # ---- ciclo di vita -------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LMStudioClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- HTTP con retry ------------------------------------------------
    def _retrying(self, attempt_fn, url: str):
        """Esegue ``attempt_fn`` ritentando solo ciò che ha davvero senso ritentare.

        Un errore di rete o un 5xx sono transitori: si riprova. Un'attesa scaduta o una
        generazione impazzita no: le richieste partono a temperatura 0, quindi il tentativo
        successivo produce esattamente la stessa risposta interminabile. Ritentarlo costava
        l'intero timeout moltiplicato per il numero di tentativi (con i valori di serie:
        tre quarti d'ora per non ottenere nulla).
        """
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                return attempt_fn()
            except (LLMDegenerate, LLMSilence, LLMResource):
                raise
            except httpx.ReadTimeout as exc:
                raise LLMSilence(
                    f"Da {url} non è arrivato niente per {self.config.request_timeout_s:.0f} "
                    "secondi di fila. Può darsi che il server trattenga la risposta fino alla "
                    "fine, o che sia bloccato. Guarda il suo stato (modello caricato, richieste "
                    "in corso) e riprova: aspettare ancora non cambia niente."
                ) from exc
            except (httpx.HTTPError, LLMError) as exc:
                last_exc = exc
                if attempt < self.config.max_retries - 1:
                    time.sleep(0.5 * (2**attempt))
        raise LLMError(
            f"Richiesta a {url} fallita dopo {self.config.max_retries} tentativi "
            f"({type(last_exc).__name__}): {last_exc}"
        )

    def _with_resource_wait(self, fn):
        """Esegue ``fn``; se il server dice di non avere le risorse per rispondere adesso,
        aspetta ``resource_wait_s`` secondi e riprova **la stessa richiesta**, fino a
        ``resource_wait_max_s`` in tutto. «Interrompi» ferma l'attesa. Oltre il limite l'errore
        sale così com'è, col motivo vero e con quanto si è aspettato."""
        attesa = max(1.0, float(self.config.resource_wait_s))
        limite = float(self.config.resource_wait_max_s)
        aspettato = 0.0
        while True:
            try:
                return fn()
            except LLMResource as exc:
                if aspettato >= limite:
                    raise LLMError(
                        f"{exc} Ho aspettato {aspettato / 60:.0f} minuti senza che il server si "
                        "liberasse: controllalo (memoria libera, altri programmi) e riprova."
                    ) from exc
                pausa = min(attesa, max(1.0, limite - aspettato))
                self._attendi(exc, pausa)
                aspettato += pausa

    def _attendi(self, motivo: LLMResource, secondi: float) -> None:
        if self.wait_hook is None:
            # Da riga di comando nessuno ascolta l'hook: l'attesa va detta, o sembra un blocco.
            import sys

            print(f"In attesa: {motivo} Riprovo tra {int(secondi)} s.", file=sys.stderr, flush=True)
        fine = time.monotonic() + secondi
        ultimo = -1
        try:
            while True:
                if self.cancel_event.is_set():
                    raise LLMCancelled()
                resto = fine - time.monotonic()
                if resto <= 0:
                    return
                if self.wait_hook is not None and int(resto) != ultimo:
                    ultimo = int(resto)
                    self.wait_hook(motivo, int(resto) + 1)
                time.sleep(min(0.25, resto))
        finally:
            if self.wait_hook is not None:
                self.wait_hook(None, 0)

    def quick(self) -> _Rapido:
        """Lo stesso client per i **compiti rapidi** (giudizio di pertinenza, interrogazioni,
        riformulazioni, intento di ricerca): le sue richieste ragionano al livello scelto per
        loro nelle impostazioni (``reasoning_effort_quick``). Condivide connessione, consumi e
        «Interrompi» con questo."""
        return _Rapido(self)

    def cancel(self) -> None:
        """«Interrompi»: la richiesta in corso chiude la connessione e solleva ``LLMCancelled``."""
        self.cancel_event.set()

    def _lines(self, resp):
        """Le righe della risposta in streaming, sorvegliando l'interruzione.

        La lettura vera sta in un thread a parte: se il server non manda niente (certi server
        trattengono il flusso fino a risposta completa) il thread principale non resta bloccato
        sulla lettura, e chi preme «Interrompi» ottiene la chiusura della connessione entro
        mezzo secondo invece di aspettare la fine.
        """
        coda: queue.Queue = queue.Queue()

        def pompa() -> None:
            try:
                for riga in resp.iter_lines():
                    coda.put(("riga", riga))
                coda.put(("fine", None))
            except BaseException as exc:  # noqa: BLE001 - torna a chi aspetta
                coda.put(("errore", exc))

        threading.Thread(target=pompa, daemon=True).start()
        while True:
            try:
                tipo, valore = coda.get(timeout=0.25)
            except queue.Empty:
                if self.cancel_event.is_set():
                    resp.close()
                    raise LLMCancelled() from None
                continue
            if self.cancel_event.is_set():
                resp.close()
                raise LLMCancelled()
            if tipo == "riga":
                yield valore
            elif tipo == "errore":
                raise valore
            else:
                return

    def _aggiorna_credenziale(self) -> None:
        """Rinfresca l'autorizzazione quando si va ad abbonamento. Silenzioso se non serve.

        Un token d'abbonamento dura ore, non per sempre: su un libro che ne richiede quattro
        scadrebbe a metà. Si chiede a llmswitch prima di ogni richiesta — legge un file, e
        rinnova soltanto se manca poco alla scadenza. Se non c'è modo di ottenerlo si prosegue
        con quello che si ha: sarà il servizio a dire che non basta, con un errore che parla
        del permesso e non di una libreria.
        """
        if not self._ad_abbonamento:
            return
        try:
            from llmswitch import intestazioni

            self._client.headers.update(intestazioni(self.config.provider, consenti_login=False))
        except Exception:
            pass

    def _request(self, method: str, url: str, *, json_body: dict | None = None) -> httpx.Response:
        self._aggiorna_credenziale()

        def attempt() -> httpx.Response:
            resp = self._client.request(method, url, json=json_body)
            if resp.status_code >= 500:
                raise LLMError(f"{resp.status_code} da {url}: {resp.text[:200]}")
            resp.raise_for_status()
            return resp

        return self._retrying(attempt, url)

    # ---- introspezione modelli ----------------------------------------
    def list_models(self) -> list[ModelInfo]:
        """Elenca i modelli disponibili presso il fornitore configurato.

        LM Studio espone stato di caricamento e contesto via API nativa; i fornitori cloud
        espongono i prezzi con ``/v1/models?verbose=true``.
        """
        if self.config.provider != "lmstudio":
            return self._list_models_openai(verbose=True)
        try:
            resp = self._request("GET", f"{self._api_root}/api/v0/models")
            data = resp.json().get("data", [])
            modelli = [
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
            return self._con_pesi(modelli)
        except LLMError:
            return self._list_models_openai(verbose=False)

    def _con_pesi(self, modelli: list[ModelInfo]) -> list[ModelInfo]:
        """Aggiunge peso e quantizzazione ai modelli locali, chiedendoli a llmswitch.

        Le due fonti dicono cose diverse e servono entrambe: l'API di LM Studio sa il tipo — e
        senza quello si finirebbe a proporre un vettorizzatore come scrittore — mentre solo il
        catalogo su disco sa quanto pesa ciascuna versione. Se il catalogo non risponde si
        prosegue con quello che c'è: sapere il peso è comodo, non indispensabile.
        """
        motori = self._engines()
        if motori is None:
            return modelli
        try:
            pesi = {v["id"]: v for v in motori.menu("lmstudio")}
        except Exception:
            return modelli
        for m in modelli:
            voce = pesi.get(m.id)
            if voce:
                m.size_h, m.quant = str(voce.get("size_h") or ""), str(voce.get("quant") or "")
        return modelli

    def _list_models_openai(self, *, verbose: bool) -> list[ModelInfo]:
        """Elenco via endpoint standard ``/v1/models``; con ``verbose`` legge anche i prezzi.

        I prezzi arrivano per singolo token (es. ``"0.00000003"``): li riportiamo per milione.
        """
        url = f"{self._v1}/models" + ("?verbose=true" if verbose else "")
        data = self._request("GET", url).json().get("data", [])
        out: list[ModelInfo] = []
        for m in data:
            pricing = m.get("pricing") or {}
            price_in = _per_million(pricing.get("prompt"))
            price_out = _per_million(pricing.get("completion"))
            # Un modello di embedding non genera testo: niente prezzo in uscita. Il nome
            # (embedding, bge, e5) è il secondo indizio, perché l'API non dichiara il tipo.
            name = str(m.get("id", "")).lower()
            looks_embed = any(tag in name for tag in ("embed", "/bge-", "/e5-"))
            kind = "embeddings" if looks_embed or (price_in is not None and not price_out) else "?"
            out.append(
                ModelInfo(
                    id=m["id"],
                    type=kind,
                    state="?",
                    max_context_length=m.get("context_length"),
                    price_in=price_in,
                    price_out=price_out,
                )
            )
        return out

    def health(self) -> bool:
        """True se il server risponde ed espone almeno un modello."""
        return len(self.list_models()) > 0

    # ---- scelta del modello --------------------------------------------
    def _engines(self) -> LocalEngines | None:
        """I motori locali secondo llmswitch, o ``None`` se il fornitore non è locale."""
        if self.config.provider not in _MOTORI_LOCALI:
            return None  # sui fornitori a consumo «caricato» non vuol dire niente
        if self._motori is None:
            self._motori = LocalEngines(
                LmStudioConfig(
                    base_url=self._v1,
                    model=self.config.models.reasoning,
                    autostart=False,  # niente si avvia da solo: la macchina è dell'utente
                ),
                VllmConfig(manager_url=self.config.vllm_manager_url),
                prefer="vllm" if self.config.provider == "vllm" else "lmstudio",
            )
        return self._motori

    def resolve_target(self, requested: str | None = None) -> Destinazione:
        """Dove e con chi parlare davvero: **il modello già in memoria**, se ce n'è uno.

        L'inference si fa con ciò che sta in VRAM in quel momento. Chiedere un nome diverso da
        quello caricato non è una preferenza che il server esaudisce senza conseguenze: o fa
        comparire un secondo modello in memoria — e il primo scivola nella shared memory, dove
        va dieci volte più piano — o, con il caricamento a richiesta disattivato, fa fallire la
        chiamata. Il nome scritto nella configurazione vale finché nessuno ha caricato altro a
        mano; da quel momento comanda la scheda.

        La regola è quella di ``llmswitch``, che guarda sia LM Studio sia le istanze vLLM del
        manager: se il modello vivo sta su vLLM, cambia anche l'indirizzo, non solo il nome. Si
        usa ``warm()`` e non ``resolve()`` perché la prima si limita a guardare, mentre la
        seconda, a memoria vuota, caricherebbe un modello di sua iniziativa.

        Se il modello chiesto è già in memoria si rispetta la richiesta (è il caso di ``heavy``
        accanto a ``reasoning``). Se non si riesce a sapere cosa c'è, si va con la
        configurazione: non saperlo non deve impedire di provare.
        """
        preferito = requested or self.config.models.reasoning
        casa = Destinazione(self._v1, preferito, "")
        motori = self._engines()
        if motori is None:
            return casa
        adesso = time.monotonic()
        with self._caricati_lock:
            if adesso - self._caricati_letti_a >= _SCOPERTA_TTL_S:
                self._caricati.clear()
                self._caricati_letti_a = adesso
            noto = preferito in self._caricati
            caldo = self._caricati.get(preferito)
        if not noto:
            # La preferenza è di questa richiesta, non del file di configurazione: chi chiede il
            # modello pesante deve ottenerlo, se è in memoria. Il lock serve perché le chiamate
            # qui sono concorrenti e la preferenza si scrive dentro l'oggetto condiviso.
            with self._motori_lock:
                motori.lm.model = preferito
                try:
                    # Si guarda e basta: mai caricare. Se la memoria è vuota si manda la
                    # richiesta e il server fa quel che sa fare (Basalt e LM Studio con il
                    # caricamento a richiesta caricano da sé); caricare un modello è una
                    # decisione dell'utente, non dell'applicazione.
                    caldo = motori.warm()
                except Exception:  # llmswitch parla con la rete: un guasto suo non ferma il libro
                    caldo = None
            with self._caricati_lock:
                self._caricati[preferito] = caldo
        if caldo is None or not caldo.model:
            return casa
        if same_model(caldo.model, preferito):
            return Destinazione(caldo.base_url, caldo.model, caldo.backend)
        self.model_swaps[preferito] = caldo.model
        return Destinazione(caldo.base_url, caldo.model, caldo.backend)

    def _reasoning_dialect(self) -> str:
        """Come si dice il ragionamento a questo fornitore (vedi ``llmswitch.reasoning``)."""
        tipo = self.config.provider
        return _reasoning.dialect(tipo, str(TYPES.get(tipo, {}).get("api") or ""))

    def resolve_model(self, requested: str | None = None) -> str:
        """Il nome del modello con cui si parlerà davvero. Vedi ``resolve_target``."""
        return self.resolve_target(requested).model

    # ---- chat ----------------------------------------------------------
    def _chat_request(self, body: dict, base_url: str = "",
                      on_delta: Callable[[str, str], None] | None = None) -> dict:
        """Esegue la richiesta di chat in streaming e ricompone la risposta.

        Lo streaming non serve a mostrare il testo mentre arriva: serve a distinguere un modello
        che sta lavorando da uno che si è impantanato. Finché i token arrivano l'attesa non
        scade (il timeout diventa "silenzio massimo" invece di "durata massima", e una risposta
        lunga e legittima non viene più troncata a metà); se invece il modello comincia a
        ripetersi si chiude subito, invece di aspettare l'intero timeout una risposta che non
        arriverà. Nessun tetto ai token: sui modelli che ragionano il pensiero fa parte della
        qualità, e limitarlo lascerebbe la risposta vuota.

        ``on_delta(tipo, testo)`` riceve i pezzi man mano che arrivano — «content» la risposta,
        «reasoning» il pensiero, «restart» quando un errore di rete fa ripartire la richiesta —
        per chi li vuole mostrare. Un'eccezione sollevata lì dentro
        chiude il flusso e risale così com'è: è il modo in cui l'utente ferma una risposta.
        """
        url = f"{base_url or self._v1}/chat/completions"
        payload = dict(body, stream=True, stream_options={"include_usage": True})
        self._aggiorna_credenziale()

        tentativi = 0

        def attempt() -> dict:
            nonlocal tentativi
            # Un secondo tentativo riparte da capo: chi stava mostrando il primo deve buttarlo,
            # o vedrebbe la risposta due volte, la seconda attaccata ai pezzi della prima.
            if tentativi and on_delta is not None:
                on_delta("restart", "")
            tentativi += 1
            if self.cancel_event.is_set():
                raise LLMCancelled()
            if self.activity_hook is not None:
                self.activity_hook(0, 0)
            ultimo_avviso = 0.0
            pieces: list[str] = []
            tail = {"content": "", "reasoning": ""}
            reasoning_chars = 0
            content_chars = 0
            usage: dict = {}
            finish_reason = ""
            deltas = 0
            with self._client.stream("POST", url, json=payload) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    try:
                        dichiarato = _error_in(resp.json())
                    except ValueError:
                        dichiarato = None
                    if dichiarato is not None:
                        errore = _server_error(*dichiarato[:1], status=resp.status_code,
                                               kind=dichiarato[1])
                        if isinstance(errore, LLMResource) or resp.status_code < 500:
                            raise errore
                    if resp.status_code >= 500:
                        raise LLMError(f"{resp.status_code} da {url}: {resp.text[:200]}")
                    resp.raise_for_status()
                # Un fornitore (o un test) può rispondere in blocco ignorando 'stream': allora
                # non c'è nessun flusso da sorvegliare e vale la risposta così com'è.
                if "event-stream" not in resp.headers.get("content-type", ""):
                    resp.read()
                    corpo = resp.json()
                    dichiarato = _error_in(corpo) if not corpo.get("choices") else None
                    if dichiarato is not None:
                        raise _server_error(dichiarato[0], kind=dichiarato[1])
                    return corpo
                for line in self._lines(resp):
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    # Un errore dopo l'inizio del flusso arriva come evento, con HTTP 200
                    # (Basalt: «RAM low…» come ``data: {"error": {...}}``): non è una risposta
                    # vuota del modello, ed è il server a dire perché.
                    dichiarato = _error_in(event)
                    if dichiarato is not None and not event.get("choices"):
                        raise _server_error(dichiarato[0], kind=dichiarato[1])
                    if event.get("usage"):
                        usage = event["usage"]
                    for choice in event.get("choices", []):
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}
                        text = delta.get("content") or ""
                        thought = delta.get("reasoning_content") or ""
                        if on_delta is not None:
                            if thought:
                                on_delta("reasoning", thought)
                            if text:
                                on_delta("content", text)
                        if (self.activity_hook is not None and (thought or text)
                                and time.monotonic() - ultimo_avviso >= 0.5):
                            ultimo_avviso = time.monotonic()
                            self.activity_hook(reasoning_chars + len(thought),
                                               content_chars + len(text))
                        if text:
                            pieces.append(text)
                            content_chars += len(text)
                            tail["content"] = (tail["content"] + text)[-_TAIL_CHARS:]
                        if thought:
                            reasoning_chars += len(thought)
                            tail["reasoning"] = (tail["reasoning"] + thought)[-_TAIL_CHARS:]
                        deltas += 1
                    if deltas >= _CHECK_EVERY:
                        deltas = 0
                        # I caratteri si contano man mano. Rifare la somma su tutta la lista a
                        # ogni controllo costava quanto il quadrato della risposta: su una
                        # traduzione lunga sono milioni di operazioni buttate, proprio mentre
                        # bisogna svuotare il flusso in fretta per non far aspettare la scheda.
                        prodotti = reasoning_chars + content_chars
                        # Si guarda solo il testo SCRITTO. Il ragionamento può ripetersi, anche
                        # a lungo, e non per questo è perso: il modello a volte ne esce da solo,
                        # e se non ne esce è l'autore che lo ferma («Interrompi») o chi governa
                        # il server. Chiudere da qui la connessione toglieva la risposta a chi
                        # ci sarebbe arrivato — e non lo si può sapere da fuori.
                        recent = tail["content"]
                        if _looks_degenerate(recent, prodotti):
                            # I token bruciati dalla ripetizione sono stati prodotti davvero:
                            # vanno nel conto, altrimenti il pannello dei consumi tace proprio
                            # sulle richieste che costano di più. Stima da caratteri (~4:1).
                            spesi = prodotti // 4
                            self._track(
                                {"completion_tokens": spesi, "total_tokens": spesi},
                                str(body.get("model", "?")),
                            )
                            raise LLMDegenerate(
                                f"Il modello «{body.get('model', '?')}» ha cominciato a "
                                "ripetere sempre la stessa sequenza nel testo che stava "
                                f"scrivendo ({recent[-60:].strip()!r}) e la richiesta è stata "
                                "chiusa: non sarebbe finita. Rifarla uguale darebbe lo "
                                "stesso esito."
                            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": "".join(pieces),
                            "reasoning_content": tail["reasoning"],
                            "reasoning_chars": reasoning_chars,
                        },
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage,
            }

        return self._with_resource_wait(lambda: self._retrying(attempt, url))

    def _sampling(self) -> dict[str, Any]:
        """I parametri di campionamento da mandare: solo quelli scritti dall'utente in configurazione.

        Lo decide l'utente (``[llm]`` in config.toml o la riga «Campionamento» delle Impostazioni,
        vedi :class:`LLMConfig`): ogni valore scritto lì vale per tutte le richieste. Quello che
        non è scritto non si manda, e allora decide il server. Il codice non impone nessuna
        temperatura: né quella zero (decodifica greedy, imposta sopra il valore del server, e nei
        modelli che ragionano compatibile con il ciclo senza fine: log di Basalt, richieste da
        160-260 mila token con il 99% di bozze speculative accettate) né quelle «da scrittura».
        """
        c = self.config
        fuori: dict[str, Any] = {}
        for chiave in ("temperature", "top_p", "top_k", "min_p", "presence_penalty",
                       "repetition_penalty"):
            valore = getattr(c, chiave)
            if valore is not None:
                fuori[chiave] = valore
        return fuori

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        extra: dict[str, Any] | None = None,
        on_delta: Callable[[str, str], None] | None = None,
        quick: bool = False,
    ) -> str:
        """Un completamento testuale. ``quick``: è un compito rapido (vedi
        :meth:`LLMConfig.reasoning_for`), che ragiona al livello scelto per quelli."""
        dove = self.resolve_target(model)
        model_id = dove.model
        # Nessun tetto ai token generati: quanto può produrre un modello lo decide chi governa
        # il server, dove quel limite si vede accanto al contesto e alla memoria che costa. Un
        # tetto messo da qui tagliava risposte buone — un libro fallito a due brani dalla fine.
        body: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            **self._sampling(),
        }
        if extra:
            body.update(extra)
        # Quanto ragionare: il livello della richiesta, se c'è; altrimenti quello scelto nelle
        # impostazioni; se nessuno dei due, niente (decide il server). Lo si dice nel dialetto
        # del fornitore — ``reasoning_effort`` per OpenAI e compatibili, un'altra forma per NVIDIA
        # o Anthropic — passando da llmswitch, il punto unico in cui un livello diventa parametri.
        richiesto = body.pop("reasoning_effort", None)
        livello = _reasoning.resolve(self.config.reasoning_for(quick) or _reasoning.INHERIT,
                                     richiesto or _reasoning.INHERIT)
        body = _reasoning.params_for(livello, self._reasoning_dialect(), body)
        data = self._chat_request(body, dove.base_url, on_delta)
        self._track(data.get("usage"), model_id)
        choice = data["choices"][0]
        # Perché il modello si è fermato dell'ultima risposta («stop», «length»…): chi scrive
        # testi lunghi lo legge per sapere se è finita da sé o è stata tagliata. Per chi non lo
        # legge non cambia niente.
        self.last_finish_reason = choice.get("finish_reason") or ""
        content = choice["message"].get("content") or ""
        if not content.strip():
            # I modelli che ragionano mettono il pensiero in un campo separato: se il tetto di
            # token finisce lì, 'content' resta vuoto. Meglio dirlo che lasciare un JSON illeggibile.
            reason = choice.get("finish_reason") or "?"
            generated = (data.get("usage") or {}).get("completion_tokens", 0)
            thought = choice["message"].get("reasoning_chars") or len(
                choice["message"].get("reasoning_content") or ""
            )
            detto = (
                f"Il modello «{model_id}» non ha prodotto testo (motivo: {reason}, "
                f"{generated} token generati, di cui {thought} caratteri di ragionamento)."
            )
            if reason == "length":
                detto += (" Il limite l'ha imposto il server dei modelli: controlla là la "
                          "lunghezza di contesto e i token massimi in risposta.")
            elif reason in ("?", "") and not generated:
                detto += (" Il server non ha detto perché e non ha generato niente: guarda il "
                          "suo registro.")
            raise LLMError(detto)
        return content

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
        schema_name: str = "output",
        model: str | None = None,
        reasoning_effort: str = "",
        on_delta: Callable[[str, str], None] | None = None,
        quick: bool = False,
    ) -> Any:
        """Una risposta JSON conforme a ``schema``: il formato si dichiara, e si controlla qui.

        Come lo si dichiara lo dice l'impostazione «Output strutturato» del fornitore
        (:meth:`LLMConfig.structured_mode`):

        * ``json_schema`` (predefinito): ``response_format: json_schema`` con lo schema;
        * ``json_object``: ``response_format: json_object`` e lo schema nel prompt;
        * ``text``: niente ``response_format``, solo lo schema nel prompt.

        In **ogni** caso la risposta si valida qui — forma
        (:func:`~bookwriter.llm.jsoncheck.validate`) e ripetizioni
        (:func:`~bookwriter.llm.jsoncheck.clean_repetitions`) — perché non tutti i server fanno
        quello che dichiarano: alcuni ignorano il formato, altri lo trattengono e ritentano da sé.
        Le voci doppie si tolgono; se la risposta non va si fa **una** riparazione mirata: prima
        l'errore, poi solo il punto dell'oggetto dove sta il guasto (e la voce ripetuta una volta,
        col conteggio), mai il testo rotto intero. Se non va nemmeno quella, un errore esplicito.
        Una risposta tagliata dal server (``length``) non si ripara rifacendo la stessa richiesta.

        ``reasoning_effort`` è il livello chiesto da chi chiama (vince su quello configurato).
        Nessun tetto ai token: né qui né sul ragionamento.
        """
        modo = self.config.structured_mode()
        extra: dict[str, Any] = {}
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort
        if modo == "json_schema":
            extra["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
        elif modo == "json_object":
            extra["response_format"] = {"type": "json_object"}
        completo = self._with_schema(messages, schema, in_prompt=modo != "json_schema")
        testo = self.chat(completo, model=model, extra=extra or None,
                          on_delta=on_delta, quick=quick)
        tagliata = self.last_finish_reason == "length"
        dati, errore, dettaglio = self._check_json(testo, schema)
        if errore is None:
            return dati
        if tagliata:
            # Il server ha interrotto la risposta: rifare la stessa richiesta darebbe lo stesso.
            raise LLMError(f"{errore} (la risposta è stata troncata dal server: controlla là i "
                           "token massimi in risposta)")
        valido = self._valid_json_wrong_shape(testo, schema)
        if valido is not None:
            # La risposta è JSON valido e viola solo lo schema (di solito: una chiave omessa). Si
            # rimanda la risposta e si chiede di correggere SOLO quel punto: un riassunto senza il
            # testo inviterebbe il modello a ricostruire tutto a memoria, e a sbagliare altrove.
            riparazione = [
                *completo,
                {"role": "assistant", "content": testo},
                {"role": "user", "content": (
                    f"La tua risposta è JSON valido ma non rispetta lo schema: {valido}\n"
                    "Correggi SOLO questo, lascia invariato tutto il resto, e rispondi con "
                    "l'INTERO oggetto JSON corretto: solo il JSON, senza testo attorno.")},
            ]
            testo = self.chat(riparazione, model=model, extra=extra or None, on_delta=on_delta, quick=quick)
            dati, errore, _ = self._check_json(testo, schema)
            if errore is None:
                return dati
            raise LLMError(f"{errore}. Era già una riparazione: la risposta non è utilizzabile.")
        riparazione = [
            *completo,
            {"role": "user", "content": (
                f"La tua risposta non andava: {errore}.\n{dettaglio}\n"
                "Rispondi di nuovo con l'INTERO oggetto JSON corretto: solo il JSON, conforme "
                "allo schema, senza testo attorno, senza voci ripetute negli elenchi e senza "
                "ripetere la stessa frase.")},
        ]
        testo = self.chat(riparazione, model=model,
                          extra=extra or None, on_delta=on_delta, quick=quick)
        dati, errore, _ = self._check_json(testo, schema)
        if errore is None:
            return dati
        raise LLMError(f"{errore}. Era già una riparazione: la risposta non è utilizzabile.")

    @staticmethod
    def _with_schema(messages: list[dict[str, str]], schema: dict[str, Any], *,
                     in_prompt: bool = True) -> list[dict[str, str]]:
        """I messaggi con, nel prompt di sistema, l'istruzione di rispondere in JSON e — quando il
        formato non è dichiarato al server con lo schema — lo schema stesso."""
        istruzione = (
            "## Formato della risposta\n"
            "Rispondi SOLO con un oggetto JSON valido: niente testo prima o dopo, niente blocchi "
            "di codice. Ogni voce di un elenco compare una volta sola: non ripetere voci né frasi. "
            "Ogni campo previsto compare sempre, mai omesso: quando un valore non è noto o non c'è "
            "scrivi una stringa vuota (\"\") per i testi, 0 per i numeri, un elenco vuoto ([]) per "
            "gli elenchi — non inventarlo e non saltare la chiave."
        )
        if in_prompt:
            istruzione += (
                " Deve rispettare questo JSON Schema: tutte le proprietà elencate in `required` "
                "sono obbligatorie e non se ne aggiungono altre.\n```json\n"
                + json.dumps(schema, ensure_ascii=False) + "\n```")
        copia = [dict(m) for m in messages]
        if copia and copia[0].get("role") == "system":
            copia[0]["content"] = f"{copia[0]['content']}\n\n{istruzione}"
        else:
            copia.insert(0, {"role": "system", "content": istruzione})
        return copia

    @staticmethod
    def _valid_json_wrong_shape(testo: str, schema: dict[str, Any]) -> str | None:
        """Se il testo è JSON valido che viola solo lo schema, l'istruzione mirata; altrimenti None."""
        try:
            dato = _extract_json(testo)
        except LLMError:
            return None
        errore = jsoncheck.validate(dato, schema)
        return jsoncheck.fix_instruction(errore, schema) if errore else None

    @staticmethod
    def _check_json(testo: str, schema: dict[str, Any]) -> tuple[Any, str | None, str]:
        """``(dato, None, "")`` se la risposta va; ``(None, motivo, dove)`` se no.

        ``dove`` è il punto del guasto, per la riparazione: mai il testo intero. Le voci doppie
        si tolgono senza far rumore.
        """
        try:
            dato = _extract_json(testo)
        except LLMError:
            # Niente citazioni del testo rotto: l'errore in parole, poi il campo, la voce che si
            # ripete (una volta) e il conteggio, dal prefisso valido (vedi ``analyze_broken``).
            rotto = jsoncheck.analyze_broken(testo)
            return None, f"Risposta non interpretabile come JSON: {rotto.message}", rotto.hint
        errore = jsoncheck.validate(dato, schema)
        if errore:
            percorso = errore.split(":", 1)[0]
            valore = jsoncheck.value_at(dato, percorso)
            punto = (f"Il guasto sta in {percorso}: {jsoncheck.snippet(valore)}"
                     if valore is not None else f"Il guasto sta in {percorso}.")
            return None, f"JSON non conforme allo schema: {errore}", punto
        dato, problemi = jsoncheck.clean_repetitions(dato)
        if problemi:
            p = problemi[0]
            dove = f"Il campo è {getattr(p, 'path', '')}."
            if getattr(p, "repeated", ""):
                dove += f" La voce che si ripete (una volta sola): {p.repeated} — compare {p.count} volte."
            return None, "risposta degenerata, piena di ripetizioni (" + "; ".join(problemi[:3]) + ")", dove
        return dato, None, ""

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
        model_id = model or self.config.models.embedding
        body = {"model": model_id, "input": [prefix + t for t in texts]}
        resp = self._request("POST", f"{self._v1}/embeddings", json_body=body)
        data = resp.json()
        self._track(data.get("usage"), model_id)
        rows = sorted(data["data"], key=lambda r: r.get("index", 0))
        return [r["embedding"] for r in rows]
