"""Parte E: chiamate JSON senza la modalità che trattiene il flusso, validate qui; interruzione vera;
ripetizioni nel risultato. Server finto: streaming con pezzi di ragionamento e di contenuto."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
from pytest_httpx import HTTPXMock

from bookwriter.config import EmbeddingConfig, LLMConfig
from bookwriter.llm import jsoncheck
from bookwriter.llm.client import LLMCancelled, LLMError, LMStudioClient

URL = "http://localhost:1234/v1/chat/completions"
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"items": {"type": "array", "items": {"type": "string"}},
                   "note": {"type": "string"}},
    "required": ["items", "note"],
}


def _cliente(**kw) -> LMStudioClient:
    c = LMStudioClient(LLMConfig(**kw), EmbeddingConfig())
    c._caricati, c._caricati_letti_a = {c.config.models.reasoning: None}, time.monotonic()
    return c


def _risposta(httpx_mock: HTTPXMock, contenuto: str, finish: str = "stop") -> None:
    httpx_mock.add_response(url=URL, json={"choices": [{"message": {"content": contenuto},
                                                         "finish_reason": finish}]})


def _corpi(httpx_mock: HTTPXMock) -> list[dict]:
    return [json.loads(r.content) for r in httpx_mock.get_requests() if r.url.path == "/v1/chat/completions"]


# --------------------------------------------------------------------------- #
#  la richiesta
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("modo, formato, schema_nel_prompt", [
    ("json_schema", "json_schema", False),     # predefinito: lo schema sta nel formato
    ("json_object", "json_object", True),       # JSON qualsiasi, schema nel prompt
    ("text", None, True),                       # niente formato, solo il prompt
])
def test_chat_json_declares_the_format_according_to_the_setting(
    httpx_mock: HTTPXMock, modo: str, formato: str | None, schema_nel_prompt: bool,
) -> None:
    _risposta(httpx_mock, '{"items": ["a", "b"], "note": "ok"}')
    risultato = _cliente(structured_output=modo).chat_json(
        [{"role": "system", "content": "Sei un editor."}, {"role": "user", "content": "x"}],
        schema=SCHEMA, schema_name="prova")
    assert risultato == {"items": ["a", "b"], "note": "ok"}
    corpo = _corpi(httpx_mock)[0]
    assert "max_tokens" not in corpo
    assert corpo["stream"] is True, "in streaming come ogni altra chiamata"
    if formato is None:
        assert "response_format" not in corpo
    else:
        assert corpo["response_format"]["type"] == formato
    if formato == "json_schema":
        assert corpo["response_format"]["json_schema"] == {"name": "prova", "strict": True, "schema": SCHEMA}
    sistema = corpo["messages"][0]["content"]
    assert sistema.startswith("Sei un editor.") and "SOLO con un oggetto JSON" in sistema
    assert ('"required"' in sistema) is schema_nel_prompt


def test_the_default_mode_is_json_schema_and_a_custom_server_brings_its_own(temp_workspace) -> None:
    from bookwriter.llm import servers as llm_servers

    assert LLMConfig().structured_mode() == "json_schema"
    assert LLMConfig(structured_output="nonsense").structured_mode() == "json_schema"
    voce = llm_servers.add_custom("Mio", "http://127.0.0.1:1/v1", structured_output="json_object")
    assert LLMConfig(provider="openai", provider_id=voce["id"]).structured_mode() == "json_object"
    llm_servers.update_custom(voce["id"], name="Mio", base_url="http://127.0.0.1:1/v1", api_key="",
                              reasoning_level="inherit", structured_output="text")
    assert LLMConfig(provider="openai", provider_id=voce["id"]).structured_mode() == "text"


def test_valid_json_that_only_breaks_the_schema_is_sent_back_with_a_targeted_fix(
    httpx_mock: HTTPXMock,
) -> None:
    """Un JSON valido a cui manca una chiave: si rimanda la risposta e si chiede SOLO quella."""
    lungo = '{"items": ["' + "voce " * 400 + '"]}'                    # manca «note», testo lungo
    _risposta(httpx_mock, lungo)
    _risposta(httpx_mock, '{"items": ["a"], "note": "corretto"}')
    assert _cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)["note"] == "corretto"
    corpi = _corpi(httpx_mock)
    assert len(corpi) == 2
    messaggi = corpi[1]["messages"]
    assert messaggi[-2] == {"role": "assistant", "content": lungo}, "la risposta torna indietro"
    ultimo = messaggi[-1]["content"]
    assert "Aggiungi la chiave «note» in /" in ultimo and 'stringa vuota ("")' in ultimo
    assert "SOLO questo" in ultimo and "invariato tutto il resto" in ultimo
    assert not any(ch.isdigit() for ch in ultimo.split("Aggiungi")[0].split("schema:")[-1]), \
        "nessun conteggio di caratteri nell'istruzione"


def test_the_real_landscape_case_a_work_without_year(httpx_mock: HTTPXMock) -> None:
    """Il caso vero: il panorama omette `year` di un'opera (il prompt diceva «lascia vuoto»)."""
    from bookwriter.forge.research import LANDSCAPE_SCHEMA

    base = {"title": "T", "author": "A", "url": "", "draw_from": "x", "cited_sources": [],
            "different": ""}
    parziale = {"works": [{**base, "year": "2020"}, dict(base)], "sources": [], "ideas": [],
                "recent": ""}
    giusto = {**parziale, "works": [{**base, "year": "2020"}, {**base, "year": ""}]}
    import json as _json

    _risposta(httpx_mock, _json.dumps(parziale))
    _risposta(httpx_mock, _json.dumps(giusto))
    dati = _cliente().chat_json([{"role": "user", "content": "x"}], schema=LANDSCAPE_SCHEMA)
    assert dati["works"][1]["year"] == ""
    corpi = _corpi(httpx_mock)
    assert len(corpi) == 2, "una riparazione sola"
    istruzione = corpi[1]["messages"][-1]["content"]
    assert "Aggiungi la chiave «year» in /works/1" in istruzione
    assert 'stringa vuota ("")' in istruzione
    assert corpi[1]["messages"][-2]["role"] == "assistant"
    assert "Chiavi già complete" not in istruzione


def test_every_prompt_with_required_fields_says_to_use_empty_values_not_to_omit(
    httpx_mock: HTTPXMock,
) -> None:
    """Il prompt generico di ogni richiesta a schema dice cosa scrivere quando non si sa."""
    _risposta(httpx_mock, '{"items": ["a"], "note": "x"}')
    _cliente().chat_json([{"role": "system", "content": "Sei un editor."},
                          {"role": "user", "content": "x"}], schema=SCHEMA)
    sistema = _corpi(httpx_mock)[0]["messages"][0]["content"]
    assert "mai omesso" in sistema and 'stringa vuota ("")' in sistema


def test_no_schema_forbids_the_empty_value_that_the_prompts_ask_for() -> None:
    """Prompt e schema vanno d'accordo: nessun `minLength`/`minItems` impedisce "" o []."""
    from pathlib import Path as _P

    for f in _P("bookwriter").rglob("*.py"):
        if "bookgen" in f.parts:
            continue
        assert "minLength" not in f.read_text(encoding="utf-8"), f
        assert "minItems" not in f.read_text(encoding="utf-8"), f


def test_the_prompts_never_say_leave_the_field_empty() -> None:
    """«Lascia il campo vuoto» invita a omettere la chiave: si dice la stringa vuota."""
    from pathlib import Path as _P

    for f in _P("bookwriter").rglob("*.py"):
        if "bookgen" in f.parts or "gui" in f.parts:
            continue
        testo = f.read_text(encoding="utf-8")
        assert "lascia il campo" not in testo.lower(), f
        assert "campi vuoti" not in testo.lower(), f


def test_a_syntax_error_repair_quotes_only_the_spot(httpx_mock: HTTPXMock) -> None:
    rotto = '{"items": ["a", "b", ' + '"c" ' * 300 + 'oops "d"], "note": "x"}'
    _risposta(httpx_mock, rotto)
    _risposta(httpx_mock, '{"items": ["a"], "note": "x"}')
    _cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)
    ultimo = _corpi(httpx_mock)[1]["messages"][-1]["content"]
    assert "riga 1, colonna" in ultimo and len(ultimo) < 1200 and rotto not in ultimo


def test_twice_broken_json_is_an_explicit_error(httpx_mock: HTTPXMock) -> None:
    _risposta(httpx_mock, '{"items": "non una lista", "note": "x"}')
    _risposta(httpx_mock, '{"items": 3, "note": "x"}')
    with pytest.raises(LLMError, match="non conforme"):
        _cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)
    assert len(_corpi(httpx_mock)) == 2, "una riparazione sola"


def test_a_truncated_json_is_not_repaired_by_asking_again(httpx_mock: HTTPXMock) -> None:
    _risposta(httpx_mock, '{"items": ["a", "b', finish="length")
    with pytest.raises(LLMError, match="troncata"):
        _cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)
    assert len(_corpi(httpx_mock)) == 1


# --------------------------------------------------------------------------- #
#  ripetizioni nel risultato
# --------------------------------------------------------------------------- #
def test_duplicate_list_items_are_removed(httpx_mock: HTTPXMock) -> None:
    _risposta(httpx_mock, json.dumps({"items": ["Uno", "Due", "uno.", "Tre"], "note": "ok"}))
    assert _cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)["items"] == [
        "Uno", "Due", "Tre"]


def test_a_list_dominated_by_repetition_is_repaired_then_reported(httpx_mock: HTTPXMock) -> None:
    sporca = json.dumps({"items": ["La città come personaggio"] * 8 + ["Altro"], "note": "ok"})
    _risposta(httpx_mock, sporca)
    pulita = json.dumps({"items": ["La città", "Il porto", "I testimoni"], "note": "ok"})
    _risposta(httpx_mock, pulita)
    assert len(_cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)["items"]) == 3
    ultimo = _corpi(httpx_mock)[1]["messages"][-1]["content"]
    assert "ripetizioni" in ultimo
    # la voce che si ripete compare UNA volta, col conteggio; non l'elenco intero
    assert "compare 8 volte" in ultimo and ultimo.count("La città come personaggio") <= 2

    httpx_mock.reset()
    _risposta(httpx_mock, sporca)
    _risposta(httpx_mock, sporca)
    with pytest.raises(LLMError, match="degenerata"):
        _cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)


def test_a_field_repeating_the_same_sentence_is_degenerate() -> None:
    frase = "Il modello ripete sempre la stessa frase lunga e inutile. "
    _, problemi = jsoncheck.clean_repetitions({"note": frase * 4})
    assert problemi and "ripete la stessa frase" in problemi[0]
    _, problemi = jsoncheck.clean_repetitions({"note": "Una frase sola. " + frase})
    assert not problemi


def test_prose_with_repeated_paragraphs_is_flagged() -> None:
    p = "Questo è un paragrafo abbastanza lungo da non essere una battuta, e compare due volte."
    assert jsoncheck.repeated_text(f"# Titolo\n\n{p}\n\nAltro testo diverso.\n\n{p}")
    assert not jsoncheck.repeated_text("# T\n\nUn paragrafo lungo e unico per il primo punto del capitolo.\n\n"
                                       "Un altro paragrafo, anch'esso unico, sul secondo punto del capitolo.")
    assert jsoncheck.dedupe_paragraphs(f"{p}\n\nAltro.\n\n{p}").count(p) == 1


def test_validate_catches_the_usual_shape_errors() -> None:
    assert jsoncheck.validate({"items": [], "note": ""}, SCHEMA) is None
    assert "extra" in jsoncheck.validate({"items": [], "note": "", "extra": 1}, SCHEMA)
    assert "items[1]" in jsoncheck.validate({"items": ["a", 2], "note": ""}, SCHEMA)
    assert jsoncheck.validate({"n": True}, {"type": "object", "properties": {"n": {"type": "integer"}}})


# --------------------------------------------------------------------------- #
#  streaming: interruzione e conteggio del ragionamento
# --------------------------------------------------------------------------- #
class _Lento(httpx.SyncByteStream):
    """Un server che manda un pezzo di ragionamento ogni 0,2 s, per trenta secondi."""

    def __init__(self, quanti: int = 150) -> None:
        self.quanti = quanti
        self.chiuso = threading.Event()

    def __iter__(self):
        for i in range(self.quanti):
            if self.chiuso.is_set():
                return
            time.sleep(0.2)
            yield ("data: " + json.dumps({"choices": [{"delta": {
                "reasoning_content": f"penso al punto numero {i}. "}}]}) + "\n\n").encode()

    def close(self) -> None:
        self.chiuso.set()


def _cliente_lento(flusso: _Lento) -> LMStudioClient:
    c = _cliente()
    c._client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=flusso)))
    return c


def test_interrupt_closes_the_connection_in_a_few_seconds() -> None:
    flusso = _Lento()
    c = _cliente_lento(flusso)
    threading.Timer(0.6, c.cancel).start()
    inizio = time.monotonic()
    with pytest.raises(LLMCancelled):
        c.chat([{"role": "user", "content": "x"}])
    assert time.monotonic() - inizio < 5, "non aspetta la fine (30 s)"
    assert flusso.chiuso.is_set(), "la connessione è stata chiusa"


def test_cancel_is_not_swallowed_by_a_generic_except() -> None:
    """Chi fa proseguire un lavoro dopo un guasto con ``except Exception`` non deve ingoiarla."""
    flusso = _Lento()
    c = _cliente_lento(flusso)
    c.cancel()
    try:
        try:
            c.chat([{"role": "user", "content": "x"}])
        except Exception:  # noqa: BLE001
            pytest.fail("l'interruzione è stata scambiata per un guasto")
    except LLMCancelled:
        pass


def test_the_activity_hook_reports_reasoning_then_content(httpx_mock: HTTPXMock) -> None:
    righe = [{"choices": [{"delta": {"reasoning_content": "ragiono " * 40}}]}] * 3
    righe.append({"choices": [{"delta": {"content": "Ecco."}}]})
    httpx_mock.add_response(url=URL, headers={"content-type": "text/event-stream"},
                            content="".join(f"data: {json.dumps(r)}\n\n" for r in righe) + "data: [DONE]\n\n")
    c = _cliente()
    visti: list[tuple[int, int]] = []
    c.activity_hook = lambda r, t: visti.append((r, t))
    assert c.chat([{"role": "user", "content": "x"}]) == "Ecco."
    assert visti[0] == (0, 0), "all'inizio della richiesta si azzera"
    assert max(r for r, _ in visti) > 0


def test_a_silence_is_not_retried_and_does_not_claim_the_model_stopped(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("t"), url=URL)
    with pytest.raises(LLMError) as errore:
        _cliente(max_retries=3).chat([{"role": "user", "content": "x"}])
    assert "non è arrivato niente" in str(errore.value) and "trattenga" in str(errore.value)
    assert "smesso di produrre" not in str(errore.value)
    assert len(httpx_mock.get_requests()) == 1


def test_an_empty_answer_cut_by_the_server_is_not_retried(httpx_mock: HTTPXMock) -> None:
    _risposta(httpx_mock, "", finish="length")
    with pytest.raises(LLMError, match="length"):
        _cliente(max_retries=3).chat([{"role": "user", "content": "x"}])
    assert len(httpx_mock.get_requests()) == 1


# --------------------------------------------------------------------------- #
#  il caso vero: un JSON troncato con un elenco pieno della stessa voce
# --------------------------------------------------------------------------- #
VOCE = "The acacia as a symbol of memory and the threshold between the living and the dead"


def _troncato_vero() -> str:
    return ('{"competitors": [{"title": "Il molo", "author": "Bianchi"}], "implicit_syllabus": ['
            + ", ".join(f'"{VOCE}"' for _ in range(43)) + ', "The acac')


def test_a_truncated_json_with_a_repeated_entry_is_described_not_quoted() -> None:
    from bookwriter.llm.client import LMStudioClient

    dato, errore, dove = LMStudioClient._check_json(_troncato_vero(), {"type": "object"})
    assert dato is None
    messaggio = f"{errore}\n{dove}"
    # PRIMA l'errore in parole, con riga e colonna, senza citare il testo
    assert errore.startswith("Risposta non interpretabile come JSON: il testo termina senza chiudere")
    assert "riga 1, colonna" in errore and VOCE not in errore and "competitors" not in errore
    # POI il campo, la voce ripetuta UNA volta e il conteggio
    assert "$.implicit_syllabus" in dove
    assert "compare 43 volte" in dove and "voci 1–43" in dove
    assert messaggio.count(VOCE) == 1, "la voce ripetuta compare una sola volta in tutto il messaggio"
    assert "Chiavi già complete: competitors" in dove
    assert len(messaggio) < 900


def test_a_broken_json_without_repetition_gets_only_a_tiny_excerpt() -> None:
    from bookwriter.llm.client import LMStudioClient

    rotto = ('{"items": [' + ", ".join(f'"voce numero {i} diversa dalle altre"' for i in range(40))
             + '] "note": "manca la virgola"}')
    _, errore, dove = LMStudioClient._check_json(rotto, {"type": "object"})
    assert "manca una virgola" in errore and "riga 1, colonna" in errore
    estratto = dove.split("Estratto: ")[1]
    assert len(estratto) <= 130 and rotto not in dove


def test_the_repair_request_for_the_real_case_names_field_and_count_and_never_resends_the_text(
    httpx_mock: HTTPXMock,
) -> None:
    _risposta(httpx_mock, _troncato_vero())
    _risposta(httpx_mock, '{"items": ["a"], "note": "ok"}')
    _cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)
    # la prima risposta non è "length" (il server ha detto stop): si ripara con la richiesta mirata
    ultimo = _corpi(httpx_mock)[1]["messages"][-1]["content"]
    assert "$.implicit_syllabus" in ultimo and "43 volte" in ultimo
    assert ultimo.count(VOCE) == 1
    assert all(m["role"] != "assistant" for m in _corpi(httpx_mock)[1]["messages"])


def test_repetition_in_a_valid_json_names_the_entry_once_with_the_count(httpx_mock: HTTPXMock) -> None:
    sporca = json.dumps({"items": [VOCE] * 9 + ["Altro"], "note": "ok"})
    _risposta(httpx_mock, sporca)
    _risposta(httpx_mock, json.dumps({"items": ["a", "b", "c"], "note": "ok"}))
    _cliente().chat_json([{"role": "user", "content": "x"}], schema=SCHEMA)
    ultimo = _corpi(httpx_mock)[1]["messages"][-1]["content"]
    assert ultimo.count(VOCE) == 1 and "compare 9 volte" in ultimo and "$.items" in ultimo
