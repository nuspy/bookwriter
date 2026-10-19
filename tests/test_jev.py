"""Giudizi rapidi con Jev (TypeSafe): il servizio è sempre finto, a livello HTTP, e riproduce il
formato vero (``POST /v1/systemone`` con ``state`` e ``questions``; risposta con ``answers`` e
``usage``). La chiave di prova è inventata: quella vera non sta in nessun file."""

from __future__ import annotations

import json
import logging

from pathlib import Path

import httpx
import pytest

from bookwriter.config import AppConfig, ResearchSettings
from bookwriter.forge import jev as jevmod
from bookwriter.forge import pagetext
from bookwriter.forge import research as rs
from bookwriter.forge import research_store as store
from bookwriter.forge.websearch import Page, SearchResult
from tests.test_giro3_research import Spia, _forge

CHIAVE = "chiave-di-prova-0123456789"
URL = jevmod.URL


def _impostazioni(**kw) -> ResearchSettings:
    return ResearchSettings(enabled=True, jev_enabled=True, jev_api_key=CHIAVE, **kw)


def _risposta(punteggio: float, confidenza: float = 0.9, token: int = 123) -> dict:
    """Il formato vero di una risposta Score a quattro livelli."""
    p = [0.0, 0.0, 0.0, 0.0]
    p[min(3, int(punteggio))] = 1.0
    return {"model": "jev-1.13.0",
            "answers": {"utile": {"type": "score", "score": punteggio, "confidence": confidenza,
                                  "probabilities": {str(i): v for i, v in enumerate(p)},
                                  "legend": {str(i): c for i, c in enumerate(jevmod.CRITERIA)}}},
            "usage": {"input_tokens": token, "output_tokens": 4}}


def _stati(httpx_mock) -> list[str]:
    return [json.loads(r.content)["state"] for r in httpx_mock.get_requests()]


# --------------------------------------------------------------------------- #
#  spento per impostazione
# --------------------------------------------------------------------------- #
def test_jev_is_off_by_default_and_without_a_key(monkeypatch) -> None:
    monkeypatch.setenv(jevmod.KEY_ENV, CHIAVE)
    assert ResearchSettings().jev_enabled is False and ResearchSettings().jev_api_key == ""
    assert jevmod.session(ResearchSettings()) is None, "la chiave in ambiente non lo accende"
    monkeypatch.delenv(jevmod.KEY_ENV)
    assert jevmod.session(ResearchSettings(jev_enabled=True)) is None, "acceso ma senza chiave"
    assert jevmod.session(ResearchSettings(jev_enabled=True, jev_api_key=CHIAVE)) is not None
    monkeypatch.setenv(jevmod.KEY_ENV, "dall-ambiente")
    assert jevmod.api_key_for(ResearchSettings(jev_enabled=True)) == "dall-ambiente"
    assert jevmod.api_key_for(ResearchSettings(jev_api_key=CHIAVE)) == CHIAVE


def test_nothing_goes_out_while_jev_is_off(tmp_path: Path, httpx_mock, monkeypatch) -> None:
    monkeypatch.setattr(rs, "gather", lambda q, s, **k: (
        [SearchResult("T", "https://e.org/a", "e")], [Page("https://e.org/a", "A", "x" * 5000)]))
    f = _forge(tmp_path, Spia())
    f.research_landscape()
    f.research_chapters()
    assert httpx_mock.get_requests() == [], "nessuna richiesta a nessuno"


# --------------------------------------------------------------------------- #
#  il formato della richiesta e della risposta
# --------------------------------------------------------------------------- #
def test_the_request_has_the_documented_shape_and_the_answer_is_read(httpx_mock) -> None:
    httpx_mock.add_response(url=URL, method="POST", json=_risposta(1.9, 0.77, token=321))
    sessione = jevmod.session(_impostazioni())
    punteggio, confidenza = sessione.judge("Capitolo 1. Punto: la cerimonia", "Testo della pagina")
    assert (punteggio, confidenza) == (1.9, 0.77)
    r = httpx_mock.get_requests()[0]
    assert r.headers["authorization"] == f"Bearer {CHIAVE}"
    assert r.headers["content-type"] == "application/json"
    corpo = json.loads(r.content)
    assert corpo["model"] == "jev-latest"
    assert corpo["state"] == ("BOOK PASSAGE: Capitolo 1. Punto: la cerimonia\n\n"
                              "WEB PAGE:\nTesto della pagina")
    domanda = corpo["questions"]["utile"]
    assert domanda["type"] == "score"
    assert domanda["criteria"] == ["Off-topic, unrelated", "Barely related", "Useful", "Very useful"]
    assert sessione.meter.as_dict() == {"calls": 1, "input_tokens": 321, "output_tokens": 4,
                                        "seconds": sessione.meter.as_dict()["seconds"],
                                        "failures": 0, "fallbacks": 0}


def test_never_more_text_than_the_limit_goes_out(httpx_mock) -> None:
    httpx_mock.add_response(url=URL, method="POST", json=_risposta(2.0))
    sessione = jevmod.session(_impostazioni(jev_max_chars=1000))
    sessione.judge("punto", "A" * 50_000)
    stato = _stati(httpx_mock)[0]
    pagina = stato.split("WEB PAGE:\n", 1)[1]
    assert pagina == "A" * 1000 and len(stato) < 1100


# --------------------------------------------------------------------------- #
#  pertinenza
# --------------------------------------------------------------------------- #
def _pagina(testo: str = "una cerimonia con l'ayahuasca descritta da chi c'era") -> Page:
    return Page("https://e.org/p", "P", testo, readable=True)


def test_a_page_below_the_threshold_is_not_relevant_with_the_jev_reason(httpx_mock) -> None:
    httpx_mock.add_response(url=URL, method="POST", json=_risposta(0.02, 0.98))
    modello = Spia()
    pagine = [_pagina()]
    rs.assess_pages(pagine, pagetext.Topic.of("x", ""), _impostazioni(min_relevance=0.0),
                    client=modello, context="Punto: la cerimonia")
    assert pagine[0].relevant is False
    assert pagine[0].note == "non pertinente (giudizio Jev 0.02, confidenza 0.98)"
    assert "pertinenza" not in modello.prompt, "il modello locale non è stato chiamato"


def test_a_page_above_the_threshold_stays(httpx_mock) -> None:
    httpx_mock.add_response(url=URL, method="POST", json=_risposta(1.9))
    modello = Spia()
    pagine = [_pagina()]
    rs.assess_pages(pagine, pagetext.Topic.of("x", ""), _impostazioni(), client=modello,
                    context="Punto")
    assert pagine[0].relevant is True and pagine[0].relevance == pytest.approx(1.9 / 3)
    assert "pertinenza" not in modello.prompt


@pytest.mark.parametrize("guasto", ["http500", "timeout", "formato", "rete"])
def test_when_jev_fails_the_local_model_judges_as_before(httpx_mock, guasto: str) -> None:
    if guasto == "http500":
        httpx_mock.add_response(url=URL, method="POST", status_code=500, text="giù")
    elif guasto == "timeout":
        httpx_mock.add_exception(httpx.ReadTimeout("lento"), url=URL, method="POST")
    elif guasto == "rete":
        httpx_mock.add_exception(httpx.ConnectError(f"no ({CHIAVE})"), url=URL, method="POST")
    else:
        httpx_mock.add_response(url=URL, method="POST", json={"model": "x", "risposte": 1})
    modello = Spia()
    sessione = jevmod.session(_impostazioni())
    pagine = [_pagina()]
    rs.assess_pages(pagine, pagetext.Topic.of("x", ""),
                    _impostazioni(min_relevance=0.25), client=modello, context="Punto",
                    jev=sessione)
    assert len(modello.prompt["pertinenza"]) == 1, "ripiego sul giudizio del modello locale"
    assert pagine[0].relevant is True, "il modello locale ha detto 0.9"
    assert sessione.meter.fallbacks == 1 and sessione.meter.failures == 1


# --------------------------------------------------------------------------- #
#  sezioni migliori nelle pagine lunghe e nei PDF
# --------------------------------------------------------------------------- #
def _documento(n: int = 30, utili: tuple = (7, 21, 22)) -> str:
    return "\n\n".join(
        ("Racconto di chi ha bevuto l'ayahuasca: fumo, canti, buio. " if i in utili
         else "Pagina di servizio senza rapporto con il resto. ") * 8 + f"[sezione {i}]"
        for i in range(n))


def _callback_per_contenuto(richieste: list[str]):
    def risposta(request: httpx.Request) -> httpx.Response:
        stato = json.loads(request.content)["state"]
        richieste.append(stato)
        return httpx.Response(200, json=_risposta(2.5 if "ayahuasca" in stato else 0.1))
    return risposta


def test_the_best_sections_are_chosen_whole_in_order_and_within_the_budget(httpx_mock) -> None:
    richieste: list[str] = []
    httpx_mock.add_callback(_callback_per_contenuto(richieste), url=URL, method="POST",
                            is_reusable=True)
    testo = _documento()
    sessione = jevmod.session(_impostazioni(jev_max_chars=2000))
    estratto = sessione.best_excerpt("Punto: la cerimonia", testo, 3000)
    assert len(estratto) <= 3000
    assert estratto != testo[:3000]
    for numero in (7, 21, 22):
        assert f"[sezione {numero}]" in estratto, "le sezioni utili ci sono"
    assert estratto.index("[sezione 7]") < estratto.index("[sezione 21]") < estratto.index(
        "[sezione 22]"), "nell'ordine del documento"
    for parte in estratto.split("\n\n[…]\n\n"):
        assert parte in testo, "sezioni intere, mai tagliate a metà"
    # a Jev una sezione alla volta: mai il documento intero, mai oltre il limite
    assert len(richieste) > 1
    assert all(len(s) < 2000 + 100 for s in richieste)
    assert max(len(s) for s in richieste) < len(testo) / 5
    assert sessione.meter.calls == len(richieste)


def test_a_text_that_fits_the_budget_costs_nothing(httpx_mock) -> None:
    sessione = jevmod.session(_impostazioni())
    assert sessione.best_excerpt("punto", "poco testo", 3000) == "poco testo"
    assert httpx_mock.get_requests() == []


def test_a_failure_in_the_middle_falls_back_to_the_start_of_the_text(httpx_mock) -> None:
    httpx_mock.add_response(url=URL, method="POST", status_code=503, text="occupato",
                            is_reusable=True)
    testo = _documento()
    sessione = jevmod.session(_impostazioni())
    assert sessione.best_excerpt("punto", testo, 3000) == testo[:3000]
    assert sessione.meter.fallbacks == 1


def test_a_huge_document_scores_only_a_bounded_number_of_sections(httpx_mock) -> None:
    richieste: list[str] = []
    httpx_mock.add_callback(_callback_per_contenuto(richieste), url=URL, method="POST",
                            is_reusable=True)
    testo = _documento(400)
    jevmod.session(_impostazioni()).best_excerpt("punto", testo, 3000)
    assert len(richieste) <= jevmod.MAX_SECTIONS


def test_sections_are_whole_and_lose_nothing() -> None:
    testo = "\n\n".join(["Frase uno. Frase due. Frase tre." * 30, "Corto.", "X" * 5000])
    sezioni = jevmod.split_sections(testo, 800)
    assert all(len(s) <= 800 for s in sezioni)
    assert "".join(s.replace("\n\n", "").replace(" ", "") for s in sezioni) == \
        testo.replace("\n\n", "").replace(" ", "")


# --------------------------------------------------------------------------- #
#  nella ricerca di un capitolo: conti visibili, estrazione sulle sezioni migliori
# --------------------------------------------------------------------------- #
def test_the_chapter_records_the_jev_cost_and_extraction_reads_the_best_sections(
    tmp_path: Path, httpx_mock, monkeypatch,
) -> None:
    lunga = _documento(30, utili=(0, 21, 22))  # l'inizio serve: la pagina supera il giudizio
    monkeypatch.setattr(rs, "gather", lambda q, s, **k: (
        [SearchResult("T", "https://e.org/lunga", "e")],
        [Page("https://e.org/lunga", "Lunga", lunga, readable=True)]))
    richieste: list[str] = []
    httpx_mock.add_callback(_callback_per_contenuto(richieste), url=URL, method="POST",
                            is_reusable=True)
    modello = Spia()
    f = _forge(tmp_path, modello)
    f.cfg.research.jev_enabled, f.cfg.research.jev_api_key = True, CHIAVE
    f.cfg.research.jev_max_chars = 2000
    f.research_landscape()
    richieste.clear()  # il panorama ha le sue (non registrate nei capitoli)
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, f.state.outline[0],
                        should_stop=lambda: modello.fonti >= 1)
    dati = f.chapter_data(1)
    assert dati.jev["calls"] > 1 and dati.jev["input_tokens"] > 0 and "seconds" in dati.jev
    assert dati.jev["calls"] == len(richieste)
    md = store.chapter_md_path(f.project.research_dir, 1, dati.title).read_text(encoding="utf-8")
    assert "Giudizi rapidi con Jev:" in md and "token in ingresso" in md
    # l'estrazione ha ricevuto le sezioni utili, non l'inizio della pagina
    estrazione = modello.prompt["estrazione"][0]
    assert "[sezione 21]" in estrazione and "[sezione 5]" not in estrazione
    # riprendendo, i conti si sommano
    prima = dati.jev["calls"]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, f.state.outline[0])
    assert f.chapter_data(1).jev["calls"] > prima


# --------------------------------------------------------------------------- #
#  la chiave non esce mai
# --------------------------------------------------------------------------- #
def test_the_key_never_appears_in_errors_logs_or_repr(httpx_mock, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    httpx_mock.add_exception(httpx.ConnectError(f"rifiutata {CHIAVE}"), url=URL, method="POST")
    httpx_mock.add_response(url=URL, method="POST", status_code=401, text=f"chiave {CHIAVE}")
    sessione = jevmod.session(_impostazioni())
    errori = []
    for _ in range(2):
        with pytest.raises(jevmod.JevError) as e:
            sessione.judge("punto", "testo")
        errori.append(str(e.value))
    assert not any(CHIAVE in m for m in errori)
    assert CHIAVE not in repr(sessione.client) and CHIAVE not in caplog.text
    assert CHIAVE not in json.dumps(sessione.meter.as_dict())


# --------------------------------------------------------------------------- #
#  Impostazioni
# --------------------------------------------------------------------------- #
def test_the_settings_are_saved_and_read_back(temp_workspace, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from tests.test_providers_settings import _finestra

    QApplication.instance() or QApplication([])
    win = _finestra(monkeypatch)
    assert not win.set_jev.isChecked() and win.set_jev_key.text() == ""
    assert win.set_jev_min.value() == pytest.approx(0.8) and win.set_jev_chars.value() == 6000
    from PySide6.QtWidgets import QLineEdit

    assert win.set_jev_key.echoMode() == QLineEdit.EchoMode.Password, "campo password"
    win.set_jev.setChecked(True)
    win.set_jev_key.setText(CHIAVE)
    win.set_jev_min.setValue(1.2)
    win.set_jev_chars.setValue(4000)
    win._save_settings()
    cfg = win.pm.config_for(win.pm.active_slug()).research
    assert (cfg.jev_enabled, cfg.jev_api_key, cfg.jev_min_score, cfg.jev_max_chars) == (
        True, CHIAVE, 1.2, 4000)
    win._refresh_settings()
    assert win.set_jev.isChecked() and win.set_jev_min.value() == pytest.approx(1.2)
    win.close()
