"""Test del client LM Studio (HTTP mockato con pytest-httpx)."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from bookwriter.config import EmbeddingConfig, LLMConfig
from bookwriter.llm.client import LMStudioClient, _extract_json


@pytest.fixture()
def client() -> LMStudioClient:
    return LMStudioClient(LLMConfig(), EmbeddingConfig())


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
    client = LMStudioClient(LLMConfig(models={"reasoning": "modello-x"}))
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


def test_a_looping_model_is_cut_off_instead_of_waited_out(
    client: LMStudioClient, httpx_mock: HTTPXMock
) -> None:
    """Sui punti di guida di un indice il modello ripete all'infinito: va fermato, non atteso."""
    from bookwriter.llm.client import LLMDegenerate

    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(*[_delta(pensiero=" . ") for _ in range(400)]),
    )
    with pytest.raises(LLMDegenerate) as errore:
        client.chat([{"role": "user", "content": "indice"}])
    assert "ripetere" in str(errore.value)


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

    httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
    cliente = LMStudioClient(LLMConfig(max_retries=3))
    with pytest.raises(LLMError) as errore:
        cliente.chat([{"role": "user", "content": "ciao"}])
    assert "smesso di produrre token" in str(errore.value)
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


def test_endless_thinking_is_stopped_before_it_fills_the_context(
    client: LMStudioClient, httpx_mock: HTTPXMock
) -> None:
    """Un pensiero che non arriva mai alla risposta satura il contesto e restituisce il nulla."""
    from bookwriter.llm.client import LLMDegenerate

    # Ragionamento sempre diverso (nessuna ripetizione letterale da riconoscere), molto lungo.
    pensieri = [
        _delta(pensiero=f"Considero ora l'aspetto numero {i} della questione, che merita attenzione. ")
        for i in range(1500)
    ]
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        headers={"content-type": "text/event-stream"},
        content=_sse(*pensieri, _delta("Ecco la risposta.")),
    )
    with pytest.raises(LLMDegenerate) as errore:
        client.chat([{"role": "user", "content": "giudica"}])
    assert "senza cominciare a rispondere" in str(errore.value)
    # I token bruciati finiscono comunque nel conto dei consumi.
    assert client.usage["completion_tokens"] > 0
