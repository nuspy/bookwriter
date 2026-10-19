"""Giro 4, pagine bloccate: la strada intera dalla pagina bloccata alla pagina importata."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookwriter.config import ResearchSettings
from bookwriter.forge import blocked
from bookwriter.forge import research as rs
from bookwriter.forge import websearch as ws
from bookwriter.forge.websearch import Page, SearchResult
from tests.test_giro3_research import Spia, _forge

TESTO = "".join(
    f"<p>La cerimonia numero {i} si svolge all'alba, con il fumo dell'incenso che sale lento tra "
    "le colonne e i partecipanti seduti in cerchio, in silenzio, mentre qualcuno canta.</p>"
    for i in range(12))
PAGINA = f"<html><head><title>Vera</title></head><body><article>{TESTO}</article></body></html>"


@pytest.fixture()
def bloccata(monkeypatch):
    """Ogni interrogazione trova una pagina che il sito non lascia leggere (CAPTCHA)."""

    def gather(query, settings, *, read=None, page=0, skip=None):
        return ([SearchResult("T", "https://blocco.org/a", "e")],
                [Page("https://blocco.org/a", "A", "", readable=False, note="verifica",
                      block_reason="captcha")])

    monkeypatch.setattr(rs, "gather", gather)


def _ricerca_con_una_pagina_bloccata(tmp_path: Path, modello=None):
    modello = modello or Spia()
    f = _forge(tmp_path, modello)
    f.research_landscape()
    f.research_chapters(should_stop=lambda: modello.fonti >= 1)
    return f, modello


def test_a_blocked_page_is_queued_once_with_the_exact_fields(tmp_path: Path, bloccata) -> None:
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path)
    righe = (f.project.research_dir / "bloccate" / "coda.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(righe) == 1, "nessun duplicato, anche se più interrogazioni la ritrovano"
    riga = json.loads(righe[0])
    assert set(riga) == {"id", "url", "chapter", "point", "query", "reason", "status", "queued_at"}
    assert riga["reason"] == "captcha" and riga["status"] == "in_coda"
    assert (riga["chapter"], riga["point"]) == (1, 1)
    assert riga["id"].startswith("c01p1-") and riga["url"] == "https://blocco.org/a"


def test_a_received_file_replaces_the_blocked_record_and_goes_through_the_pipeline(
    tmp_path: Path, bloccata,
) -> None:
    modello = Spia()
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path, modello)
    voce = f.blocked_pages()[0]
    dati = f.chapter_data(1)
    assert [p.status for p in dati.points[0].pages] == ["non_leggibile"], "registrata come illeggibile"
    prima = len(modello.prompt.get("estrazione", []))

    ricevute = blocked.ricevute_dir(f.project.research_dir)
    (ricevute / f"{voce.id}.html").write_text(PAGINA, encoding="utf-8")
    (ricevute / "senza-id.html").write_text("<p>x</p>", encoding="utf-8")
    (ricevute / f"{voce.id}.docx").write_text("x", encoding="utf-8")
    esiti = f.import_blocked_pages()
    stati = {e["file"]: e["status"] for e in esiti}
    assert stati == {f"{voce.id}.html": "ok", "senza-id.html": "non_riconosciuta",
                     f"{voce.id}.docx": "non_riconosciuta"}
    assert f.blocked_pages()[0].status == "ricevuta"
    assert (ricevute / "senza-id.html").exists(), "i file non riconosciuti restano"

    pagine = f.chapter_data(1).points[0].pages
    assert len(pagine) == 1 and pagine[0].status == "" and "cerimonia numero 3" in pagine[0].text
    assert len(modello.prompt["estrazione"]) == prima + 1, "l'estrazione è passata dal modello"
    assert "cerimonia numero 3" in modello.prompt["estrazione"][-1]

    # una seconda importazione non rifà niente
    f.import_blocked_pages()
    assert len(modello.prompt["estrazione"]) == prima + 1
    assert len(f.chapter_data(1).points[0].pages) == 1


def test_an_imported_page_the_model_finds_irrelevant_is_recorded_and_not_used(
    tmp_path: Path, bloccata,
) -> None:
    class Severo(Spia):
        def chat_json(self, messages, *, schema, model=None, **kw):
            if "score" in set(schema.get("properties", {})):
                return {"score": 0.0, "reason": "parla d'altro"}
            return super().chat_json(messages, schema=schema, model=model)

    modello = Severo()
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path, modello)
    f.cfg.research.min_relevance = 0.25
    voce = f.blocked_pages()[0]
    (blocked.ricevute_dir(f.project.research_dir) / f"{voce.id}.html").write_text(
        PAGINA, encoding="utf-8")
    prima = len(modello.prompt.get("estrazione", []))
    f.import_blocked_pages()
    pagina = f.chapter_data(1).points[0].pages[0]
    assert pagina.status == "non_pertinente"
    assert len(modello.prompt.get("estrazione", [])) == prima, "non va all'estrazione"


def test_a_pdf_and_a_text_file_are_read(tmp_path: Path, bloccata) -> None:
    fitz = pytest.importorskip("fitz")
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path)
    voce = f.blocked_pages()[0]
    ricevute = blocked.ricevute_dir(f.project.research_dir)
    doc = fitz.open()
    pagina = doc.new_page()
    riga = "La cerimonia si svolge all'alba con il fumo dell'incenso che sale lento. " * 3
    pagina.insert_textbox(fitz.Rect(40, 40, 550, 800), riga * 8)
    doc.save(str(ricevute / f"{voce.id}.pdf"))
    doc.close()
    esiti = f.import_blocked_pages()
    assert esiti[0]["status"] == "ok", esiti
    assert "cerimonia" in esiti[0]["page"].text


def test_a_received_page_that_is_still_a_verification_stays_in_queue(
    tmp_path: Path, bloccata,
) -> None:
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path)
    voce = f.blocked_pages()[0]
    (blocked.ricevute_dir(f.project.research_dir) / f"{voce.id}.html").write_text(
        "<html><body><h1>Just a moment...</h1></body></html>", encoding="utf-8")
    esiti = f.import_blocked_pages()
    assert esiti[0]["status"] == "non_leggibile"
    assert f.blocked_pages()[0].status == "in_coda"


def test_resuming_the_research_imports_the_received_pages_first(
    tmp_path: Path, bloccata,
) -> None:
    f, modello = _ricerca_con_una_pagina_bloccata(tmp_path)
    voce = f.blocked_pages()[0]
    (blocked.ricevute_dir(f.project.research_dir) / f"{voce.id}.html").write_text(
        PAGINA, encoding="utf-8")
    f.research_chapters(should_stop=lambda: True)  # niente da cercare: conta solo l'importazione
    assert f.blocked_pages()[0].status == "ricevuta"


# --------------------------------------------------------------------------- #
#  Interfaccia A con un servizio /v1 (finto, a livello HTTP)
# --------------------------------------------------------------------------- #
SITO = "https://esempio.org/articolo"
SERVIZIO = "http://127.0.0.1:8191/v1"


def _impostazioni() -> ResearchSettings:
    return ResearchSettings(enabled=True, external_fetcher_url=SERVIZIO)


def test_the_v1_service_gets_the_documented_request_and_its_html_goes_through_the_pipeline(
    httpx_mock,
) -> None:
    httpx_mock.add_response(url=SITO, status_code=403, text="no")
    httpx_mock.add_response(url=SERVIZIO, method="POST", json={
        "status": "ok", "solution": {"url": SITO, "status": 200, "response": PAGINA}})
    pagina = ws.fetch(SITO, _impostazioni())
    assert pagina.readable and "cerimonia numero 3" in pagina.text
    richiesta = httpx_mock.get_requests(method="POST")[0]
    assert richiesta.headers["content-type"] == "application/json"
    assert json.loads(richiesta.content) == {"cmd": "request.get", "url": SITO,
                                             "maxTimeout": 60000}


def test_the_v1_service_is_also_asked_for_a_verification_page_that_came_with_http_200(
    httpx_mock,
) -> None:
    httpx_mock.add_response(url=SITO, headers={"content-type": "text/html"},
                            text="<html><body>Just a moment...</body></html>")
    httpx_mock.add_response(url=SERVIZIO, method="POST", json={
        "status": "ok", "solution": {"url": SITO, "status": 200, "response": PAGINA}})
    pagina = ws.fetch(SITO, _impostazioni())
    assert pagina.readable


@pytest.mark.parametrize("risposta", [
    {"status": "error", "message": "boom", "solution": {"status": 0, "response": ""}},
    {"status": "ok", "solution": {"status": 403, "response": PAGINA}},
    {"status": "ok", "solution": {"status": 200,
                                  "response": "<html><body>Just a moment...</body></html>"}},
])
def test_a_failing_service_leaves_the_page_blocked_with_its_reason(httpx_mock, risposta) -> None:
    httpx_mock.add_response(url=SITO, status_code=403, text="no")
    httpx_mock.add_response(url=SERVIZIO, method="POST", json=risposta)
    pagina = ws.fetch(SITO, _impostazioni())
    assert not pagina.readable and pagina.block_reason == "http_403"


def test_an_unreachable_service_does_not_stop_the_research(httpx_mock) -> None:
    import httpx

    httpx_mock.add_response(url=SITO, status_code=403, text="no")
    httpx_mock.add_exception(httpx.ConnectError("rifiutata"), url=SERVIZIO, method="POST")
    pagina = ws.fetch(SITO, _impostazioni())
    assert not pagina.readable and pagina.block_reason == "http_403"


def test_without_a_service_the_blocked_page_just_carries_its_reason(httpx_mock) -> None:
    httpx_mock.add_response(url=SITO, status_code=429, text="no")
    pagina = ws.fetch(SITO, ResearchSettings(enabled=True))
    assert not pagina.readable and pagina.block_reason == "http_429"
    assert len(httpx_mock.get_requests()) == 1, "nessuna chiamata a un servizio che non c'è"


def test_the_external_service_address_and_timeout_are_saved_from_the_settings(
    temp_workspace, monkeypatch,
) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from bookwriter.gui.main_window import MainWindow
    from tests.test_providers_settings import _finestra

    QApplication.instance() or QApplication([])
    win = _finestra(monkeypatch)
    assert win.set_external_timeout.value() == 60, "predefinito"
    win.set_external_fetcher.setText("http://127.0.0.1:8191/v1")
    win.set_external_timeout.setValue(90)
    win._save_settings()
    cfg = win.pm.config_for(win.pm.active_slug()).research
    assert cfg.external_fetcher_url == "http://127.0.0.1:8191/v1"
    assert cfg.external_fetcher_timeout_s == 90.0
    win._refresh_settings()
    assert win.set_external_timeout.value() == 90
    win.close()
