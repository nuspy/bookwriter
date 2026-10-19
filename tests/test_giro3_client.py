"""Giro 3, il client: l'errore vero del server, l'attesa quando mancano le risorse, i due livelli
di ragionamento. Il server finto risponde come Basalt quando la RAM libera scende sotto la sua
soglia (verificato su ``serve/handler.py`` e ``serve/service.py`` di Basalt)."""

from __future__ import annotations

import json
import time

import pytest
from pytest_httpx import HTTPXMock

from bookwriter.config import EmbeddingConfig, LLMConfig
from bookwriter.llm.client import LLMCancelled, LLMError, LLMResource, LMStudioClient

URL = "http://localhost:1234/v1/chat/completions"
RAM_BASSA = ("RAM low (19589 MiB free < 19603 MiB guard): request stopped; "
             "lower --resident-budget-gib")
MSG = [{"role": "user", "content": "x"}]


def _client(**kw) -> LMStudioClient:
    cfg = LLMConfig(**kw)
    c = LMStudioClient(cfg, EmbeddingConfig())
    c._caricati, c._caricati_letti_a = {cfg.models.reasoning: None}, time.monotonic()
    return c


def _flusso(*eventi: dict) -> bytes:
    return ("".join(f"data: {json.dumps(e)}\n\n" for e in eventi) + "data: [DONE]\n\n").encode()


def _errore_nel_flusso() -> dict:
    """Come Basalt: HTTP 200, e l'errore arriva come evento ``data:`` prima di ``[DONE]``."""
    return {"error": {"type": "server_error", "message": RAM_BASSA}}


def _risposta_ok(testo: str = "Ciao") -> dict:
    return {"choices": [{"delta": {"content": testo}, "finish_reason": None}]}


def _sse(httpx_mock: HTTPXMock, *eventi: dict) -> None:
    httpx_mock.add_response(url=URL, headers={"content-type": "text/event-stream"},
                            content=_flusso(*eventi))


# --------------------------------------------------------------------------- #
#  l'errore vero del server
# --------------------------------------------------------------------------- #
def test_the_server_error_in_the_stream_is_reported_in_words_not_as_an_empty_answer(
    httpx_mock: HTTPXMock,
) -> None:
    _sse(httpx_mock, _errore_nel_flusso())
    c = _client(resource_wait_max_s=0)
    with pytest.raises(LLMError) as errore:
        c.chat(MSG)
    testo = str(errore.value)
    assert "19.589 MiB" in testo and "19.603 MiB" in testo and "poca RAM libera" in testo
    assert "non ha prodotto testo" not in testo
    assert "'length'" not in testo, "il testo sul limite del server non c'entra"


def test_an_empty_answer_with_no_reason_does_not_blame_the_length(httpx_mock: HTTPXMock) -> None:
    _sse(httpx_mock, {"choices": [{"delta": {}, "finish_reason": None}]})
    with pytest.raises(LLMError) as errore:
        _client().chat(MSG)
    testo = str(errore.value)
    assert "non ha prodotto testo" in testo and "'length'" not in testo
    assert "guarda il suo registro" in testo


def test_an_empty_answer_cut_by_length_still_says_to_check_the_server_limits(
    httpx_mock: HTTPXMock,
) -> None:
    _sse(httpx_mock, {"choices": [{"delta": {}, "finish_reason": "length"}]})
    with pytest.raises(LLMError) as errore:
        _client().chat(MSG)
    assert "token massimi" in str(errore.value)


# --------------------------------------------------------------------------- #
#  poca RAM sul server: non è un fallimento, si aspetta e si riprova
# --------------------------------------------------------------------------- #
def test_low_server_ram_makes_the_request_wait_and_retry_the_same_request(
    httpx_mock: HTTPXMock,
) -> None:
    _sse(httpx_mock, _errore_nel_flusso())
    _sse(httpx_mock, _risposta_ok("Fatto"))
    visti: list[tuple] = []
    c = _client(resource_wait_s=1)
    c.wait_hook = lambda errore, s: visti.append((errore, s))
    ripartenze: list[str] = []
    assert c.chat(MSG, on_delta=lambda tipo, p: ripartenze.append(tipo)) == "Fatto"
    assert len(httpx_mock.get_requests()) == 2
    assert json.loads(httpx_mock.get_requests()[0].content) == json.loads(
        httpx_mock.get_requests()[1].content), "la stessa richiesta"
    errori = [e for e, _ in visti if e is not None]
    assert errori and isinstance(errori[0], LLMResource) and errori[0].kind == "ram"
    assert (errori[0].free_mib, errori[0].guard_mib) == (19589, 19603)
    assert visti[-1] == (None, 0), "a fine attesa il segnale si spegne"
    assert 1 in [s for _, s in visti if s]
    assert "restart" in ripartenze, "chi mostrava il primo tentativo lo butta"


def test_waiting_for_the_server_gives_up_after_the_limit_with_the_real_reason(
    httpx_mock: HTTPXMock,
) -> None:
    for _ in range(2):
        _sse(httpx_mock, _errore_nel_flusso())
    c = _client(resource_wait_s=1, resource_wait_max_s=1)
    with pytest.raises(LLMError) as errore:
        c.chat(MSG)
    assert not isinstance(errore.value, LLMResource)
    assert "poca RAM libera" in str(errore.value) and "Ho aspettato" in str(errore.value)
    assert len(httpx_mock.get_requests()) == 2, "una sola attesa, poi si arrende"


def test_stop_ends_the_wait(httpx_mock: HTTPXMock) -> None:
    _sse(httpx_mock, _errore_nel_flusso())
    c = _client(resource_wait_s=30)
    c.wait_hook = lambda errore, s: c.cancel() if errore is not None else None
    t0 = time.monotonic()
    with pytest.raises(LLMCancelled):
        c.chat(MSG)
    assert time.monotonic() - t0 < 5, "«Interrompi» non aspetta i 30 secondi"
    assert len(httpx_mock.get_requests()) == 1


def test_an_http_503_with_a_resource_error_body_waits_too(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=503, json={
        "error": {"type": "server_error",
                  "message": "the GPU is in use by another program: 800 MiB of VRAM free"}})
    _sse(httpx_mock, _risposta_ok("ok"))
    assert _client(resource_wait_s=1).chat(MSG) == "ok"
    assert len(httpx_mock.get_requests()) == 2


def test_a_non_stream_error_body_is_read_too(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, json={"error": {"type": "server_error", "message": RAM_BASSA}})
    httpx_mock.add_response(url=URL, json={"choices": [{"message": {"content": "ok"}}]})
    assert _client(resource_wait_s=1).chat(MSG) == "ok"


def test_an_error_that_is_not_about_resources_is_reported_without_waiting(
    httpx_mock: HTTPXMock,
) -> None:
    for _ in range(3):  # come ogni errore del server, si ritenta qualche volta (senza attese lunghe)
        _sse(httpx_mock, {"error": {"type": "invalid_request_error",
                                    "message": "modello sconosciuto"}})
    t0 = time.monotonic()
    with pytest.raises(LLMError) as errore:
        _client(resource_wait_s=30).chat(MSG)
    assert "modello sconosciuto" in str(errore.value)
    assert not isinstance(errore.value, LLMResource) and time.monotonic() - t0 < 5


def test_a_plain_503_without_a_body_is_still_retried_quickly(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=503)
    _sse(httpx_mock, _risposta_ok("ok"))
    t0 = time.monotonic()
    assert _client(max_retries=2, resource_wait_s=30).chat(MSG) == "ok"
    assert time.monotonic() - t0 < 5


def test_the_request_tells_the_json_chat_the_same_way(httpx_mock: HTTPXMock) -> None:
    """``chat_json`` passa da ``chat``: l'attesa vale anche per le risposte strutturate."""
    _sse(httpx_mock, _errore_nel_flusso())
    _sse(httpx_mock, _risposta_ok('{"a": 1}'))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
    assert _client(resource_wait_s=1).chat_json(MSG, schema=schema) == {"a": 1}


# --------------------------------------------------------------------------- #
#  due livelli di ragionamento
# --------------------------------------------------------------------------- #
def _livello_inviato(httpx_mock: HTTPXMock, cfg: dict, quick: bool, *, json_call: bool = False):
    httpx_mock.add_response(url=URL, json={"choices": [{"message": {"content": '{"a": 1}'}}]})
    c = _client(**cfg)
    if json_call:
        schema = {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]}
        (c.quick() if quick else c).chat_json(MSG, schema=schema)
    else:
        c.chat(MSG, quick=quick)
    corpo = json.loads(httpx_mock.get_requests()[-1].content)
    return corpo.get("reasoning_effort", "(niente)")


def test_by_default_quick_tasks_reason_like_everything_else(httpx_mock: HTTPXMock) -> None:
    assert LLMConfig().reasoning_effort_quick == "same"
    for _ in range(2):
        assert _livello_inviato(httpx_mock, {"reasoning_effort": "high"}, True) == "high"
        assert _livello_inviato(httpx_mock, {"reasoning_effort": "high"}, False) == "high"


def test_a_separate_quick_level_applies_only_to_quick_tasks(httpx_mock: HTTPXMock) -> None:
    cfg = {"reasoning_effort": "high", "reasoning_effort_quick": "low"}
    assert _livello_inviato(httpx_mock, cfg, True) == "low"
    assert _livello_inviato(httpx_mock, cfg, False) == "high"
    assert _livello_inviato(httpx_mock, cfg, True, json_call=True) == "low"
    assert _livello_inviato(httpx_mock, cfg, False, json_call=True) == "high"


def test_the_quick_level_can_be_left_to_the_server(httpx_mock: HTTPXMock) -> None:
    cfg = {"reasoning_effort": "high", "reasoning_effort_quick": ""}
    assert _livello_inviato(httpx_mock, cfg, True) == "(niente)"
    assert _livello_inviato(httpx_mock, cfg, False) == "high"


def test_the_quick_proxy_shares_cancel_and_usage_with_the_real_client() -> None:
    c = _client()
    rapido = c.quick()
    rapido.cancel()
    assert c.cancel_event.is_set()
    assert rapido.usage is c.usage
