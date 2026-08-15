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


class LLMDegenerate(LLMError):
    """Il modello è entrato in ripetizione senza fine: la risposta non arriverà mai.

    Succede sui passaggi privi di contenuto linguistico (l'indice di un PDF, con le righe di
    puntini di guida): il modello continua a produrre la stessa sequenza finché non esaurisce il
    contesto. Aspettarlo costa l'intero timeout, e ritentare a temperatura 0 dà lo stesso esito.
    """


# Una generazione sana avanza; una impazzita ripete. Si guarda solo la coda del testo prodotto,
# così il controllo costa uguale che siano cento o centomila token.
_TAIL_CHARS = 4000  # quanto testo recente si tiene per riconoscere la ripetizione
_WINDOW_CHARS = 600  # ampiezza della finestra osservata
_CHECK_EVERY = 64  # ogni quanti frammenti ricontrollare
# Oltre questa quantità di testo prodotto la ripetizione letterale non è più spiegabile: un
# ragionamento sano riformula la stessa frase mentre la lima («Revised: …», «Let's make it
# punchier»), ma lo fa per qualche migliaio di caratteri, non per centomila.
_RUNAWAY_CHARS = 80_000
# Pensiero massimo prima di scrivere la prima parola di risposta. Il ragionamento è il valore di
# questi modelli e non va compresso: la soglia sta cinque volte sopra il più lungo mai osservato
# su questi compiti (~19.000 caratteri). Oltre, non sta ragionando: non sta concludendo — caso
# reale, un giudizio su 2.928 token che ha prodotto 128.144 token di pensiero in 26 minuti,
# saturato il contesto e restituito il nulla.
_THINKING_LIMIT = 100_000


def _looks_degenerate(tail: str, generated: int) -> bool:
    """True se il modello sta ripetendo senza fine invece di procedere.

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

    @property
    def loaded(self) -> bool:
        return self.state == "loaded"

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
        # Contatori token (thread-safe) per seguire i consumi anche mentre il lavoro è in corso.
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
        self._usage_by_model: dict[str, dict[str, int]] = {}
        self._usage_lock = threading.Lock()

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
            except LLMDegenerate:
                raise
            except httpx.ReadTimeout as exc:
                raise LLMError(
                    f"Nessuna risposta da {url} per {self.config.request_timeout_s:.0f}s di fila: "
                    "il modello ha smesso di produrre token. Controlla che sia ancora caricato "
                    "in LM Studio."
                ) from exc
            except (httpx.HTTPError, LLMError) as exc:
                last_exc = exc
                if attempt < self.config.max_retries - 1:
                    time.sleep(0.5 * (2**attempt))
        raise LLMError(
            f"Richiesta a {url} fallita dopo {self.config.max_retries} tentativi "
            f"({type(last_exc).__name__}): {last_exc}"
        )

    def _request(self, method: str, url: str, *, json_body: dict | None = None) -> httpx.Response:
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
            return self._list_models_openai(verbose=False)

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

    # ---- chat ----------------------------------------------------------
    def _chat_request(self, body: dict) -> dict:
        """Esegue la richiesta di chat in streaming e ricompone la risposta.

        Lo streaming non serve a mostrare il testo mentre arriva: serve a distinguere un modello
        che sta lavorando da uno che si è impantanato. Finché i token arrivano l'attesa non
        scade (il timeout diventa "silenzio massimo" invece di "durata massima", e una risposta
        lunga e legittima non viene più troncata a metà); se invece il modello comincia a
        ripetersi si chiude subito, invece di aspettare l'intero timeout una risposta che non
        arriverà. Nessun tetto ai token: sui modelli che ragionano il pensiero fa parte della
        qualità, e limitarlo lascerebbe la risposta vuota.
        """
        url = f"{self._v1}/chat/completions"
        payload = dict(body, stream=True, stream_options={"include_usage": True})

        def attempt() -> dict:
            pieces: list[str] = []
            tail = {"content": "", "reasoning": ""}
            reasoning_chars = 0
            usage: dict = {}
            finish_reason = ""
            deltas = 0
            with self._client.stream("POST", url, json=payload) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    if resp.status_code >= 500:
                        raise LLMError(f"{resp.status_code} da {url}: {resp.text[:200]}")
                    resp.raise_for_status()
                # Un fornitore (o un test) può rispondere in blocco ignorando 'stream': allora
                # non c'è nessun flusso da sorvegliare e vale la risposta così com'è.
                if "event-stream" not in resp.headers.get("content-type", ""):
                    resp.read()
                    return resp.json()
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        event = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if event.get("usage"):
                        usage = event["usage"]
                    for choice in event.get("choices", []):
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}
                        text = delta.get("content") or ""
                        thought = delta.get("reasoning_content") or ""
                        if text:
                            pieces.append(text)
                            tail["content"] = (tail["content"] + text)[-_TAIL_CHARS:]
                        if thought:
                            reasoning_chars += len(thought)
                            tail["reasoning"] = (tail["reasoning"] + thought)[-_TAIL_CHARS:]
                        deltas += 1
                    if deltas >= _CHECK_EVERY:
                        deltas = 0
                        prodotti = reasoning_chars + sum(len(p) for p in pieces)
                        if not pieces and reasoning_chars > _THINKING_LIMIT:
                            self._track(
                                {"completion_tokens": prodotti // 4, "total_tokens": prodotti // 4},
                                str(body.get("model", "?")),
                            )
                            raise LLMDegenerate(
                                f"Il modello «{body.get('model', '?')}» ha ragionato per "
                                f"{reasoning_chars // 1000} mila caratteri senza cominciare a "
                                "rispondere: il pensiero non sta convergendo. Aspettarlo "
                                "significa arrivare al limite del contesto e non ottenere nulla."
                            )
                        for where, recent in tail.items():
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
                                    f"Il modello «{body.get('model', '?')}» si è bloccato a "
                                    f"ripetere sempre la stessa sequenza ({where}): "
                                    f"{recent[-60:].strip()!r}. Il testo di partenza non ha "
                                    "contenuto su cui lavorare."
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

        return self._retrying(attempt, url)

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> str:
        model_id = model or self.config.models.reasoning
        body: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if extra:
            body.update(extra)
        data = self._chat_request(body)
        self._track(data.get("usage"), model_id)
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if not content.strip():
            # I modelli che ragionano mettono il pensiero in un campo separato: se il tetto di
            # token finisce lì, 'content' resta vuoto. Meglio dirlo che lasciare un JSON illeggibile.
            reason = choice.get("finish_reason") or "?"
            generated = (data.get("usage") or {}).get("completion_tokens", 0)
            thought = choice["message"].get("reasoning_chars") or len(
                choice["message"].get("reasoning_content") or ""
            )
            raise LLMError(
                f"Il modello «{model_id}» non ha prodotto testo (motivo: {reason}, "
                f"{generated} token generati, di cui {thought} caratteri di ragionamento). "
                "Se il motivo è 'length', il tetto di token è troppo basso per questo modello."
            )
        return content

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any],
        schema_name: str = "output",
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        reasoning_effort: str = "",
    ) -> Any:
        """Chat con output vincolato a un JSON Schema (structured output di LM Studio).

        ``reasoning_effort`` ("low"/"medium"/"high") limita il ragionamento dei modelli che lo
        producono: su compiti di sola classificazione fa risparmiare tempo e token.
        """
        extra: dict[str, Any] = {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            }
        }
        if reasoning_effort:
            extra["reasoning_effort"] = reasoning_effort
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
        model_id = model or self.config.models.embedding
        body = {"model": model_id, "input": [prefix + t for t in texts]}
        resp = self._request("POST", f"{self._v1}/embeddings", json_body=body)
        data = resp.json()
        self._track(data.get("usage"), model_id)
        rows = sorted(data["data"], key=lambda r: r.get("index", 0))
        return [r["embedding"] for r in rows]
