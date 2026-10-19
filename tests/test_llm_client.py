"""Test del client LM Studio (HTTP mockato con pytest-httpx)."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from pytest_httpx import HTTPXMock

from bookwriter.config import EmbeddingConfig, LLMConfig
from bookwriter.llm.client import LMStudioClient, _extract_json


@pytest.fixture()
def client() -> LMStudioClient:
    """Client che dà già per saputo cosa c'è in memoria: niente.

    Prima di ogni chat il client chiede a llmswitch quale modello è vivo (vedi
    ``resolve_target``). Questi test guardano la conversazione, non quella scelta: dando la
    risposta per già ottenuta e recente, la domanda non parte e le attese restano quelle di
    prima. La scelta del modello ha i suoi test più sotto, con un client vergine.
    """
    return _senza_memoria(LMStudioClient(LLMConfig(), EmbeddingConfig()))


def _senza_memoria(c: LMStudioClient) -> LMStudioClient:
    """Client che crede di aver già guardato, e di non aver trovato nulla di caricato."""
    c._caricati, c._caricati_letti_a = {c.config.models.reasoning: None}, time.monotonic()
    return c


def test_extract_json_plain() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced() -> None:
    assert _extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_extract_json_with_noise() -> None:
    assert _extract_json('Ecco: {"x": [1, 2]} fine') == {"x": [1, 2]}


def test_list_models(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/api/v0/models",
        json={
            "data": [
                {"id": "m1", "type": "llm", "state": "loaded", "max_context_length": 131072},
                {"id": "emb", "type": "embeddings", "state": "not-loaded", "max_context_length": 2048},
            ]
        },
    )
    models = client.list_models()
    assert {m.id for m in models} == {"m1", "emb"}
    assert models[0].loaded is True
    assert models[1].loaded is False


def test_chat(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": "ciao"}}]},
    )
    assert client.chat([{"role": "user", "content": "hi"}]) == "ciao"


def test_usage_tracked(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
    )
    client.chat([{"role": "user", "content": "hi"}])
    assert client.usage["total_tokens"] == 8
    assert client.usage["prompt_tokens"] == 5


def test_chat_json_parses_fenced(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": '```json\n{"lingua": "it"}\n```'}}]},
    )
    out = client.chat_json([{"role": "user", "content": "x"}], schema={"type": "object"})
    assert out == {"lingua": "it"}


def test_embed_applies_prefix(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/embeddings",
        json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]},
    )
    vecs = client.embed(["testo"], kind="document")
    assert vecs == [[0.1, 0.2, 0.3]]
    request = httpx_mock.get_request()
    assert request is not None
    import json as _json

    body = _json.loads(request.content)
    assert body["input"] == ["search_document: testo"]


def test_embed_empty_shortcuts(client: LMStudioClient) -> None:
    assert client.embed([]) == []


def test_empty_answer_reports_why_instead_of_a_broken_json(monkeypatch) -> None:
    """Se il tetto di token finisce nel ragionamento, l'errore deve dirlo."""
    from bookwriter.config import LLMConfig
    from bookwriter.llm.client import LLMError, LMStudioClient

    payload = {
        "choices": [{
            "finish_reason": "length",
            "message": {"content": "", "reasoning_content": "sto pensando" * 100},
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4000, "total_tokens": 4010},
    }
    # Senza `_senza_memoria` il client andrebbe a chiedere davvero cosa c'è caricato, e il test
    # direbbe cose diverse a seconda di cosa è acceso sulla macchina.
    client = _senza_memoria(LMStudioClient(LLMConfig(models={"reasoning": "modello-x"})))
    monkeypatch.setattr(client, "_chat_request", lambda *a, **k: payload)
    with pytest.raises(LLMError) as errore:
        client.chat([{"role": "user", "content": "ciao"}])
    messaggio = str(errore.value)
    assert "modello-x" in messaggio
    assert "length" in messaggio and "4000 token" in messaggio
    # I consumi vengono comunque registrati.
    assert client.usage["completion_tokens"] == 4000


def _sse(*events: dict) -> bytes:
    import json as _json

    righe = [f"data: {_json.dumps(e)}\n\n" for e in events]
    return ("".join(righe) + "data: [DONE]\n\n").encode()


def _delta(testo: str = "", pensiero: str = "") -> dict:
    delta = {}
    if testo:
        delta["content"] = testo
    if pensiero:
        delta["reasoning_content"] = pensiero
    return {"choices": [{"delta": delta, "finish_reason": None}]}


def test_streamed_answer_is_reassembled(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(
            _delta(pensiero="rifletto"),
            _delta("Ciao "),
            _delta("mondo"),
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}},
        ),
    )
    assert client.chat([{"role": "user", "content": "hi"}]) == "Ciao mondo"
    assert client.usage["total_tokens"] == 9


def test_the_answer_can_be_watched_while_it_arrives(
    client: LMStudioClient, httpx_mock: HTTPXMock
) -> None:
    """La conversazione sul libro mostra la risposta mentre il modello la scrive."""
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(_delta(pensiero="rifletto"), _delta("Ciao "), _delta("mondo")),
    )
    visti: list[tuple[str, str]] = []
    risposta = client.chat([{"role": "user", "content": "hi"}],
                           on_delta=lambda tipo, pezzo: visti.append((tipo, pezzo)))
    assert risposta == "Ciao mondo"
    assert visti == [("reasoning", "rifletto"), ("content", "Ciao "), ("content", "mondo")]


def test_whoever_watches_can_stop_the_answer(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    """Fermare una risposta è sollevare un'eccezione dal callback: risale così com'è, senza
    ritentativi — non è un guasto, è l'autore che ha cambiato idea."""

    class Basta(Exception):
        pass

    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(_delta("Ciao "), _delta("mondo")),
    )

    def ferma(_tipo: str, _pezzo: str) -> None:
        raise Basta()

    with pytest.raises(Basta):
        client.chat([{"role": "user", "content": "hi"}], on_delta=ferma)
    assert len(httpx_mock.get_requests()) == 1


def test_a_retried_answer_tells_the_watcher_to_start_over(httpx_mock: HTTPXMock) -> None:
    """Dopo un errore di rete la richiesta riparte: chi mostrava il primo tentativo lo butta."""
    from bookwriter.config import LLMConfig

    httpx_mock.add_response(url="http://localhost:1234/v1/chat/completions", status_code=503)
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(_delta("Ciao")),
    )
    cliente = _senza_memoria(LMStudioClient(LLMConfig(max_retries=2)))
    visti: list[tuple[str, str]] = []
    assert cliente.chat([{"role": "user", "content": "hi"}],
                        on_delta=lambda tipo, pezzo: visti.append((tipo, pezzo))) == "Ciao"
    assert visti == [("restart", ""), ("content", "Ciao")]


def test_a_looping_model_is_cut_off_instead_of_waited_out(
    client: LMStudioClient, httpx_mock: HTTPXMock
) -> None:
    """Sui punti di guida di un indice il modello ripete all'infinito: va fermato, non atteso."""
    from bookwriter.llm.client import LLMDegenerate

    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(*[_delta(testo=" . ") for _ in range(400)]),
    )
    with pytest.raises(LLMDegenerate) as errore:
        client.chat([{"role": "user", "content": "indice"}])
    assert "ripetere" in str(errore.value)
    # Il messaggio dice cosa è successo, senza dare la colpa al testo di partenza: la stessa
    # eccezione arriva anche dalla ricerca, dove un «testo di partenza» non c'è.
    assert "testo di partenza" not in str(errore.value)
    assert "nel testo che stava scrivendo" in str(errore.value)


def test_a_repeating_thought_never_closes_the_request(
    client: LMStudioClient, httpx_mock: HTTPXMock
) -> None:
    """Il ragionamento lo governa chi gestisce il server: il programma non chiude la richiesta.

    Caso reale: «suggests the book is a compilation? It might be a book by…» ripetuto per
    ottantamila caratteri. Il controllo era sul ragionamento e chiudeva la connessione; ora
    guarda solo il testo scritto. Qui il pensiero è sia un alfabeto poverissimo sia una frase
    lunga ripetuta ben oltre la soglia, e la risposta arriva comunque.
    """
    frase = ("suggests the book is a compilation? It might be a book by an author who collects "
             "essays about the subject, and then again it suggests the book is a compilation. ")
    pensiero = [_delta(pensiero=" . ") for _ in range(400)]
    pensiero += [_delta(pensiero=frase) for _ in range(600)]  # >80.000 caratteri, tutti uguali
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(*pensiero, _delta("Ecco la risposta.")),
    )
    assert client.chat([{"role": "user", "content": "riassumi"}]) == "Ecco la risposta."


def test_a_repeating_answer_after_a_long_thought_is_still_cut(
    client: LMStudioClient, httpx_mock: HTTPXMock
) -> None:
    """Il testo SCRITTO che si ripete letteralmente, dopo moltissimo testo prodotto, si ferma."""
    from bookwriter.llm.client import LLMDegenerate

    frase = ("Il capitolo ricomincia da capo e ripete esattamente le stesse parole di prima, "
             "senza aggiungere nulla di nuovo al discorso che aveva avviato. ")
    eventi = [_delta(pensiero=f"Ragiono sul punto numero {i} con una frase sempre diversa. ")
              for i in range(1500)]
    eventi += [_delta(testo=frase) for _ in range(300)]
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(*eventi),
    )
    with pytest.raises(LLMDegenerate):
        client.chat([{"role": "user", "content": "scrivi"}])


def test_prose_is_not_mistaken_for_a_loop(client: LMStudioClient, httpx_mock: HTTPXMock) -> None:
    """Il controllo anti-ripetizione non deve scattare su un testo lungo e legittimo."""
    frasi = [
        f"Il viaggio del giorno {i} portò con sé una scoperta nuova e un dubbio antico. "
        for i in range(400)
    ]
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(*[_delta(f) for f in frasi]),
    )
    assert client.chat([{"role": "user", "content": "scrivi"}]).startswith("Il viaggio")


def test_a_timeout_is_not_retried(httpx_mock: HTTPXMock) -> None:
    """A temperatura 0 il tentativo dopo un timeout è identico: ritentarlo è tempo regalato."""
    import httpx

    from bookwriter.config import LLMConfig
    from bookwriter.llm.client import LLMError, LMStudioClient

    httpx_mock.add_exception(
        httpx.ReadTimeout("timed out"), url="http://localhost:1234/v1/chat/completions"
    )
    cliente = _senza_memoria(LMStudioClient(LLMConfig(max_retries=3)))
    with pytest.raises(LLMError) as errore:
        cliente.chat([{"role": "user", "content": "ciao"}])
    assert "non è arrivato niente" in str(errore.value)
    assert "smesso di produrre token" not in str(errore.value)
    assert len(httpx_mock.get_requests()) == 1  # un solo tentativo, non tre


def test_a_model_polishing_its_answer_is_not_mistaken_for_a_loop(
    client: LMStudioClient, httpx_mock: HTTPXMock
) -> None:
    """Questi modelli riformulano più volte la stessa frase mentre la limano: è lavoro sano.

    Caso reale osservato: il ragionamento ripeteva «reinterpreta l'Inferno dantesco come una
    moderna struttura turistica all-inclusive» in cinque varianti («Revised: …», «Let's make it
    punchier») e concludeva da solo in 16 secondi. Troncarlo lì rovinerebbe la risposta.
    """
    frase = (
        "Il testo reinterpreta l'Inferno dantesco come una moderna struttura turistica "
        "all-inclusive, usando le pene per criticare i comportamenti contemporanei. "
    )
    limatura = [
        _delta(pensiero=f"Revised: {frase}Let's make it punchier. Okay. ") for _ in range(30)
    ]
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(*limatura, _delta("Il testo è un'opera satirica.")),
    )
    assert client.chat([{"role": "user", "content": "riassumi"}]) == "Il testo è un'opera satirica."


def test_a_long_thinker_is_left_alone_until_it_answers(
    client: LMStudioClient, httpx_mock: HTTPXMock
) -> None:
    """Il pensiero lungo non si interrompe: quanto ne vale la pena lo decide chi governa il server.

    C'era un tetto qui — centomila caratteri di ragionamento senza una parola di risposta — e
    toglieva la risposta anche a chi ci sarebbe arrivato. Ora si aspetta: il programma sorveglia
    la ripetizione letterale, che è un flusso bloccato, non la lentezza di chi sta pensando.
    """
    # Ragionamento sempre diverso (nessuna ripetizione letterale da riconoscere), molto lungo:
    # oltre centomila caratteri, ben oltre il vecchio tetto.
    pensieri = [
        _delta(pensiero=f"Considero ora l'aspetto numero {i} della questione, che merita attenzione. ")
        for i in range(1500)
    ]
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(*pensieri, _delta("Ecco la risposta.")),
    )

    assert client.chat([{"role": "user", "content": "giudica"}]) == "Ecco la risposta."


# ---- scelta del modello: comanda la scheda, non il file di configurazione ----------------
#
# La regola («un modello già in memoria si usa e basta») è di llmswitch, la libreria comune.
# Qui non la si riscrive: si verifica che bookwriter la interroghi e ne rispetti la risposta,
# fingendo solo lo strato più basso — cosa dice LM Studio di avere in memoria.


def _in_memoria(client: LMStudioClient, *voci: tuple[str, str], vllm: tuple = ()) -> dict:
    """Finge lo stato dei motori locali. ``voci`` sono coppie (id, tipo) di modelli caricati.

    Ritorna un contatore delle volte in cui lo stato è stato davvero chiesto.
    """
    conta = {"letture": 0}
    eng = client._engines()
    eng.lmstudio_up = lambda *a, **k: bool(voci)

    def meta() -> dict[str, tuple[str, str]]:
        conta["letture"] += 1
        return {i: ("loaded", t) for i, t in voci}

    eng._meta = meta
    eng.vllm_instances = lambda: list(vllm)
    eng._alive = staticmethod(lambda url: bool(vllm))
    return conta


def _client(**modelli) -> LMStudioClient:
    modelli.setdefault("reasoning", "scritto-nel-file")
    return LMStudioClient(LLMConfig(models=modelli), EmbeddingConfig())


def test_the_loaded_model_wins_over_the_configured_one() -> None:
    """Se in memoria c'è un altro modello, si parla con quello: è lui che sta in VRAM."""
    client = _client()
    _in_memoria(client, ("caricato-a-mano", "llm"))
    assert client.resolve_model() == "caricato-a-mano"
    assert client.model_swaps == {"scritto-nel-file": "caricato-a-mano"}


def test_the_configured_model_is_kept_when_it_is_the_loaded_one() -> None:
    client = _client()
    _in_memoria(client, ("scritto-nel-file", "llm"))
    assert client.resolve_model() == "scritto-nel-file"
    assert client.model_swaps == {}


def test_a_different_quantization_of_the_same_model_is_not_a_swap() -> None:
    """`modello` e `modello@q4` sono lo stesso modello: si usa quello vero, senza segnalare."""
    client = _client()
    _in_memoria(client, ("scritto-nel-file@q4_k_m", "llm"))
    assert client.resolve_model() == "scritto-nel-file@q4_k_m"
    assert client.model_swaps == {}


def test_an_explicitly_asked_model_is_respected_if_loaded() -> None:
    """Con due modelli in memoria, chi chiede il pesante ottiene il pesante."""
    client = _client(heavy="il-pesante")
    _in_memoria(client, ("scritto-nel-file", "llm"), ("il-pesante", "llm"))
    assert client.resolve_model("il-pesante") == "il-pesante"
    assert client.resolve_model() == "scritto-nel-file"


def test_an_embedding_model_is_never_taken_for_generation() -> None:
    """In memoria c'è solo un vettorizzatore: non genera testo, non ci si conversa."""
    client = _client()
    _in_memoria(client, ("nomic-embed-text", "embeddings"))
    assert client.resolve_model() == "scritto-nel-file"


def test_the_embedder_next_to_the_model_does_not_confuse_the_choice() -> None:
    """Il caso normale di bookwriter: vettorizzatore e modello di testo in memoria insieme."""
    client = _client()
    _in_memoria(client, ("nomic-embed-text", "embeddings"), ("caricato-a-mano", "llm"))
    assert client.resolve_model() == "caricato-a-mano"


def test_with_nothing_loaded_the_configured_name_is_used() -> None:
    client = _client()
    _in_memoria(client)  # server spento: nessuna voce
    assert client.resolve_model() == "scritto-nel-file"


def test_a_live_vllm_instance_changes_the_address_too() -> None:
    """Se il modello vivo sta su un'istanza vLLM, cambia l'indirizzo, non solo il nome."""
    client = _client()
    _in_memoria(client, vllm=({"id": "i1", "port": 8000, "model": "/models/mistral"},))
    client._engines()._served = staticmethod(lambda url: "mistral-7b")
    dove = client.resolve_target()
    assert (dove.backend, dove.model, dove.base_url) == (
        "vllm", "mistral-7b", "http://127.0.0.1:8000/v1"
    )


def test_the_state_is_asked_once_then_remembered() -> None:
    """La domanda si fa una volta ogni mezzo minuto, non a ogni passaggio del libro."""
    client = _client()
    conta = _in_memoria(client, ("caricato-a-mano", "llm"))
    assert [client.resolve_model() for _ in range(5)] == ["caricato-a-mano"] * 5
    assert conta["letture"] == 1


def test_a_cloud_provider_is_left_alone(httpx_mock: HTTPXMock) -> None:
    """Sui fornitori a consumo «caricato» non vuol dire nulla: nessuna domanda, nessun cambio."""
    config = LLMConfig(provider="openrouter", models={"reasoning": "anthropic/claude-sonnet-4"})
    client = LMStudioClient(config, EmbeddingConfig())
    assert client.resolve_model() == "anthropic/claude-sonnet-4"
    assert client._engines() is None
    assert httpx_mock.get_requests() == []


def test_a_broken_switch_does_not_stop_the_book() -> None:
    """Se llmswitch si rompe, si va avanti con la configurazione invece di fermarsi."""
    client = _client()
    client._engines().warm = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("guasto"))
    assert client.resolve_model() == "scritto-nel-file"


def test_chat_talks_to_the_loaded_model(httpx_mock: HTTPXMock) -> None:
    """La sostituzione arriva fino al corpo della richiesta, non resta una buona intenzione."""
    client = _client()
    _in_memoria(client, ("caricato-a-mano", "llm"))
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": "ok"}}]},
    )
    assert client.chat([{"role": "user", "content": "ciao"}]) == "ok"
    inviata = next(r for r in httpx_mock.get_requests() if r.url.path == "/v1/chat/completions")
    assert json.loads(inviata.content)["model"] == "caricato-a-mano"
    # Il consumo si registra sotto il nome vero, altrimenti i conti parlano di un modello assente.
    assert "caricato-a-mano" in client.usage_by_model()


def test_embeddings_are_not_redirected(httpx_mock: HTTPXMock) -> None:
    """I vettori già calcolati valgono solo per il loro modello: qui non si sostituisce nulla."""
    client = _client()
    _in_memoria(client, ("caricato-a-mano", "llm"))
    httpx_mock.add_response(
        url="http://localhost:1234/v1/embeddings",
        json={"data": [{"index": 0, "embedding": [0.1]}]},
    )
    client.embed(["testo"])
    inviata = next(r for r in httpx_mock.get_requests() if r.url.path == "/v1/embeddings")
    assert json.loads(inviata.content)["model"] == client.config.models.embedding


def test_local_models_carry_their_weight(httpx_mock: HTTPXMock) -> None:
    """Su una scheda da 32 GB, sapere se un modello ne pesa 17 o 22 decide se ci sta.

    Le due fonti dicono cose diverse e servono entrambe: LM Studio sa il TIPO — e senza quello
    si proporrebbe un vettorizzatore come scrittore — il catalogo su disco sa il PESO.
    """
    client = _client()
    httpx_mock.add_response(
        url="http://localhost:1234/api/v0/models",
        json={"data": [
            {"id": "grosso", "type": "llm", "state": "loaded", "max_context_length": 131072},
            {"id": "senza-peso", "type": "llm", "state": "not-loaded"},
        ]},
    )
    client._engines().menu = lambda backend="lmstudio": [
        {"id": "grosso", "size_h": "22,1 GB", "quant": "Q4_K_M", "loaded": True},
    ]
    modelli = {m.id: m for m in client.list_models()}
    assert modelli["grosso"].size_label() == "Q4_K_M · 22,1 GB"
    assert modelli["grosso"].type == "llm", "il tipo resta quello di LM Studio"
    assert modelli["grosso"].max_context_length == 131072, "e il contesto pure"
    assert modelli["senza-peso"].size_label() == "", "chi non è in catalogo non inventa un peso"


def test_a_silent_catalog_does_not_hide_the_models(httpx_mock: HTTPXMock) -> None:
    """Sapere il peso è comodo, non indispensabile: se il catalogo tace si va avanti."""
    client = _client()
    httpx_mock.add_response(
        url="http://localhost:1234/api/v0/models",
        json={"data": [{"id": "uno", "type": "llm", "state": "loaded"}]},
    )
    client._engines().menu = lambda backend="lmstudio": (_ for _ in ()).throw(OSError("lms muto"))
    assert [m.id for m in client.list_models()] == ["uno"]


# ---- abbonamenti: il token si rinnova da solo, la chiave fissa non esiste ------------------


def test_a_subscription_authorizes_every_request_with_a_fresh_token(
    httpx_mock: HTTPXMock, monkeypatch
) -> None:
    """Un token d'abbonamento dura ore: su un libro lungo scadrebbe a metà.

    Si rifà l'autorizzazione prima di ogni richiesta invece di fissarla alla nascita del
    client, così un rinnovo avvenuto nel frattempo viene raccolto senza riavviare nulla.
    """
    import llmswitch

    tokens = iter(["token-primo", "token-secondo"])
    monkeypatch.setattr(
        llmswitch, "intestazioni",
        lambda nome, consenti_login=True: {"Authorization": f"Bearer {next(tokens)}",
                                           "ChatGPT-Account-Id": "acct-1"},
    )
    config = LLMConfig(provider="codex_oauth", base_url="http://localhost:1234/v1",
                       models={"reasoning": "gpt-5"})
    client = _senza_memoria(LMStudioClient(config, EmbeddingConfig()))
    for _ in range(2):
        httpx_mock.add_response(
            url="http://localhost:1234/v1/chat/completions",
            json={"choices": [{"message": {"content": "ok"}}]},
        )
        client.chat([{"role": "user", "content": "ciao"}])

    inviate = [r for r in httpx_mock.get_requests() if r.url.path == "/v1/chat/completions"]
    assert inviate[0].headers["authorization"] == "Bearer token-primo"
    assert inviate[1].headers["authorization"] == "Bearer token-secondo"
    assert inviate[0].headers["chatgpt-account-id"] == "acct-1"


def test_a_missing_subscription_lets_the_service_do_the_talking(
    httpx_mock: HTTPXMock, monkeypatch
) -> None:
    """Se il token manca non ci si ferma qui: l'errore del servizio è più utile del nostro."""
    import llmswitch
    from llmswitch import OAuthError

    def nessuna(nome, consenti_login=True):
        raise OAuthError("non collegato")

    monkeypatch.setattr(llmswitch, "intestazioni", nessuna)
    config = LLMConfig(provider="codex_oauth", base_url="http://localhost:1234/v1",
                       models={"reasoning": "gpt-5"})
    client = _senza_memoria(LMStudioClient(config, EmbeddingConfig()))
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": "risposta"}}]},
    )
    assert client.chat([{"role": "user", "content": "ciao"}]) == "risposta"


def test_an_ordinary_provider_is_not_bothered(httpx_mock: HTTPXMock, monkeypatch) -> None:
    """Chi usa una chiave normale non deve pagare una lettura di file a ogni richiesta."""
    import llmswitch

    monkeypatch.setattr(
        llmswitch, "intestazioni",
        lambda *a, **k: pytest.fail("non si cercano abbonamenti per un fornitore a chiave"),
    )
    client = _senza_memoria(LMStudioClient(LLMConfig(), EmbeddingConfig()))
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": "ok"}}]},
    )
    assert client.chat([{"role": "user", "content": "ciao"}]) == "ok"


# ---- l'applicazione non carica mai un modello ---------------------------------------------


def _mai_caricare(client: LMStudioClient) -> None:
    """Qualunque tentativo di caricare (o di avviare) fa fallire il test."""
    eng = client._engines()
    eng.resolve = lambda prefer="": pytest.fail("l'applicazione non carica mai un modello")
    eng._ensure_lmstudio = lambda: pytest.fail("l'applicazione non carica mai un modello")
    eng._ensure_vllm = lambda: pytest.fail("l'applicazione non avvia mai un'istanza")
    eng.start_lmstudio = lambda: pytest.fail("l'applicazione non avvia mai LM Studio")


def test_an_empty_card_never_makes_the_app_load_a_model() -> None:
    """Memoria vuota: si manda la richiesta con il nome configurato, il server carica da sé.

    È il caso di Basalt dopo il «model unloaded, the next request loads it»: caricare è una
    decisione dell'utente, non dell'applicazione.
    """
    client = _client()
    _in_memoria(client)  # niente in memoria
    _mai_caricare(client)
    assert client.resolve_model() == "scritto-nel-file"
    assert client.model_swaps == {}


def test_a_dead_server_never_makes_the_app_start_anything() -> None:
    client = _client()
    _in_memoria(client)
    _mai_caricare(client)
    assert client.resolve_target().base_url == "http://localhost:1234/v1"


def test_the_load_options_are_gone_and_an_old_config_still_reads() -> None:
    """`load_models`, `context_length`, `parallel` non esistono più; un vecchio file si legge."""
    cfg = LLMConfig.model_validate({"provider": "lmstudio", "load_models": True,
                                    "context_length": 131072, "parallel": 1})
    for vecchio in ("load_models", "context_length", "parallel"):
        assert not hasattr(cfg, vecchio)


def test_a_live_model_is_still_followed() -> None:
    """Se qualcosa è già in memoria si usa quello."""
    client = _client()
    _in_memoria(client, ("gia-dentro", "llm"))
    _mai_caricare(client)
    assert client.resolve_model() == "gia-dentro"


# ---- quanto far ragionare il modello -------------------------------------------------------


def test_the_configured_reasoning_effort_reaches_the_request(httpx_mock: HTTPXMock) -> None:
    """Senza freno il modello esaurisce i token pensando e non consegna: misurato 3 volte su 5.

    Il valore sta nella configurazione e deve arrivare fino al corpo della richiesta, sia per
    le risposte libere sia per quelle a schema — la traduzione usa le seconde.
    """
    client = _senza_memoria(LMStudioClient(LLMConfig(reasoning_effort="medium"), EmbeddingConfig()))
    for _ in range(2):
        httpx_mock.add_response(
            url="http://localhost:1234/v1/chat/completions",
            json={"choices": [{"message": {"content": '{"a": 1}'}}]},
        )
    client.chat([{"role": "user", "content": "x"}])
    client.chat_json([{"role": "user", "content": "x"}], schema={"type": "object"})

    inviate = [json.loads(r.content) for r in httpx_mock.get_requests()
               if r.url.path == "/v1/chat/completions"]
    assert all(c["reasoning_effort"] == "medium" for c in inviate)


def test_an_explicit_effort_wins_over_the_configured_one(httpx_mock: HTTPXMock) -> None:
    """Chi chiede una classificazione secca può volerne meno: la sua scelta ha la precedenza."""
    client = _senza_memoria(LMStudioClient(LLMConfig(reasoning_effort="medium"), EmbeddingConfig()))
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": '{"a": 1}'}}]},
    )
    client.chat_json([{"role": "user", "content": "x"}], schema={"type": "object"},
                     reasoning_effort="low")
    inviata = json.loads(httpx_mock.get_requests()[-1].content)
    assert inviata["reasoning_effort"] == "low"


def test_no_effort_configured_means_the_parameter_is_not_sent(httpx_mock: HTTPXMock) -> None:
    """Chi vuole il ragionamento libero svuota il campo, e la richiesta torna com'era."""
    client = _senza_memoria(LMStudioClient(LLMConfig(reasoning_effort=""), EmbeddingConfig()))
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": "ciao"}}]},
    )
    client.chat([{"role": "user", "content": "x"}])
    assert "reasoning_effort" not in json.loads(httpx_mock.get_requests()[-1].content)


def test_medium_is_what_ships_by_default() -> None:
    """La scelta predefinita è quella misurata, non una preferenza: vedi il commento in config."""
    assert LLMConfig().reasoning_effort == "medium"


# ---- nessun tetto ai token: lo mette il server, non il programma ---------------------------


def test_no_token_ceiling_is_ever_sent(httpx_mock: HTTPXMock) -> None:
    """Quanto può produrre un modello lo decide chi governa il server, non chi gli parla.

    C'era un tetto qui, cinquantamila token, e serviva a far fallire in fretta chi si perdeva.
    Ma tagliava anche chi stava lavorando bene — un libro fallito a due brani dalla fine — e il
    limite giusto si vede da dove si vedono contesto e memoria: il server dei modelli.
    """
    client = _senza_memoria(LMStudioClient(LLMConfig(), EmbeddingConfig()))
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": "ok"}}]},
    )
    client.chat([{"role": "user", "content": "x"}])
    assert "max_tokens" not in json.loads(httpx_mock.get_requests()[-1].content)


def test_an_old_config_with_a_ceiling_is_simply_ignored() -> None:
    """Chi ha `max_tokens` in un vecchio config.toml non deve vedere il programma rifiutarlo."""
    cfg = LLMConfig.model_validate({"provider": "lmstudio", "max_tokens": 50000})

    assert not hasattr(cfg, "max_tokens")
    assert cfg.provider == "lmstudio"


def test_a_truncated_answer_says_where_the_limit_came_from(monkeypatch) -> None:
    """Se il server tronca, chi legge deve sapere dove andare a cambiarlo."""
    from bookwriter.llm.client import LLMError

    payload = {"choices": [{"finish_reason": "length",
                            "message": {"content": "", "reasoning_content": "penso" * 3000}}],
               "usage": {"completion_tokens": 20000}}
    client = _senza_memoria(LMStudioClient(LLMConfig(models={"reasoning": "m"}), EmbeddingConfig()))
    monkeypatch.setattr(client, "_chat_request", lambda *a, **k: payload)
    with pytest.raises(LLMError) as errore:
        client.chat([{"role": "user", "content": "x"}])
    assert "length" in str(errore.value) and "server dei modelli" in str(errore.value)


def test_a_json_that_does_not_close_is_reported_not_retried(httpx_mock: HTTPXMock) -> None:
    """Il ritentativo «senza tetto» non ha più senso: la richiesta sarebbe identica alla prima."""
    from bookwriter.llm.client import LLMError

    client = _senza_memoria(LMStudioClient(LLMConfig(), EmbeddingConfig()))
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"finish_reason": "length",
                           "message": {"content": '{"rewritten": "The commandos immediately'}}]},
    )
    with pytest.raises(LLMError, match="non interpretabile"):
        client.chat_json([{"role": "user", "content": "x"}], schema={"type": "object"})

    inviate = [r for r in httpx_mock.get_requests() if r.url.path == "/v1/chat/completions"]
    assert len(inviate) == 1, "una sola richiesta: ripeterla identica non cambierebbe nulla"


def test_a_genuinely_broken_answer_gets_one_repair_then_is_reported(httpx_mock: HTTPXMock) -> None:
    """Una risposta che non è JSON ha una sola riparazione, mirata; poi un errore esplicito."""
    from bookwriter.llm.client import LLMError

    client = _senza_memoria(LMStudioClient(LLMConfig(), EmbeddingConfig()))
    for _ in range(2):
        httpx_mock.add_response(
            url="http://localhost:1234/v1/chat/completions",
            json={"choices": [{"message": {"content": "questo non è JSON"}}]},
        )
    with pytest.raises(LLMError, match="non interpretabile"):
        client.chat_json([{"role": "user", "content": "x"}], schema={"type": "object"})
    # una richiesta e **una** riparazione mirata: non di più
    assert len([r for r in httpx_mock.get_requests()
                if r.url.path == "/v1/chat/completions"]) == 2


# ---- campionamento: lo decide l'utente -----------------------------------------------------


def _corpo(httpx_mock: HTTPXMock) -> dict:
    return json.loads(httpx_mock.get_requests()[-1].content)


def _risposta(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": '{"a": 1}'}}]},
    )


def test_a_greedy_request_from_the_code_is_not_sent(httpx_mock: HTTPXMock) -> None:
    """`temperature: 0.0` sovrascriveva il valore del server (Basalt: 1.0) e lo rendeva greedy."""
    client = _senza_memoria(LMStudioClient(LLMConfig(), EmbeddingConfig()))
    _risposta(httpx_mock)
    client.chat_json([{"role": "user", "content": "x"}], schema={"type": "object"})
    corpo = _corpo(httpx_mock)
    for chiave in ("temperature", "top_p", "top_k", "min_p", "presence_penalty",
                   "repetition_penalty"):
        assert chiave not in corpo, f"{chiave} lo decide il server, se l'utente non lo scrive"


def test_no_call_site_in_the_code_imposes_a_temperature() -> None:
    """Lo decide il server o l'utente: nel codice non resta nessuna `temperature=` cablata."""
    import re
    from pathlib import Path

    radice = Path(__file__).resolve().parent.parent / "bookwriter"
    trovati = []
    for f in radice.rglob("*.py"):
        if "bookgen" in f.parts or f.name == "config.py" or f.name == "main_window.py":
            continue  # bookgen è un altro programma; config e finestra parlano del campo dell'utente
        for n, riga in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\btemperature\s*[=:]", riga) and "client.py" not in f.name:
                trovati.append(f"{f.name}:{n}")
    assert not trovati, trovati


def test_the_configured_sampling_wins_and_reaches_the_request(httpx_mock: HTTPXMock) -> None:
    cfg = LLMConfig(temperature=1.0, top_p=0.95, top_k=20, min_p=0.05, presence_penalty=0.5,
                    repetition_penalty=1.05)
    client = _senza_memoria(LMStudioClient(cfg, EmbeddingConfig()))
    _risposta(httpx_mock)
    client.chat([{"role": "user", "content": "x"}])
    corpo = _corpo(httpx_mock)
    assert (corpo["temperature"], corpo["top_p"], corpo["top_k"], corpo["min_p"]) == (
        1.0, 0.95, 20, 0.05)
    assert (corpo["presence_penalty"], corpo["repetition_penalty"]) == (0.5, 1.05)


def test_greedy_can_still_be_chosen_by_the_user(httpx_mock: HTTPXMock) -> None:
    client = _senza_memoria(LMStudioClient(LLMConfig(temperature=0), EmbeddingConfig()))
    _risposta(httpx_mock)
    client.chat_json([{"role": "user", "content": "x"}], schema={"type": "object"})
    assert _corpo(httpx_mock)["temperature"] == 0


def test_sampling_survives_a_save_and_an_empty_one_is_not_written(tmp_path) -> None:
    from bookwriter.config import AppConfig

    vuota = AppConfig()
    vuota.save(tmp_path / "a.toml")  # None non si scrive: TOML non ha il nulla
    assert "temperature" not in (tmp_path / "a.toml").read_text(encoding="utf-8")
    assert AppConfig.load(tmp_path / "a.toml").llm.temperature is None

    scelta = AppConfig(llm=LLMConfig(temperature=0.7, top_k=40))
    scelta.save(tmp_path / "b.toml")
    letta = AppConfig.load(tmp_path / "b.toml")
    assert (letta.llm.temperature, letta.llm.top_k, letta.llm.top_p) == (0.7, 40, None)
