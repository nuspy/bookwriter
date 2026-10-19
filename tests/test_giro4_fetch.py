"""Giro 4, «Recupera le pagine in coda»: ``forge research blocked fetch`` e il pulsante della GUI.

Il servizio /v1 è sempre finto (a livello HTTP) e quello vero non si avvia mai."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookwriter.forge import blocked
from tests.test_giro3_research import Spia
from tests.test_giro4_blocked import (  # noqa: F401  (le fixture si importano per nome)
    PAGINA,
    SERVIZIO,
    _ricerca_con_una_pagina_bloccata,
    bloccata,
)

VERIFICA = "<html><body><h1>Just a moment...</h1></body></html>"


def _servizio_risponde(httpx_mock, url: str, **corpo) -> None:
    httpx_mock.add_response(
        url=SERVIZIO, method="POST",
        match_json={"cmd": "request.get", "url": url, "maxTimeout": 60000}, **corpo)


@pytest.fixture()
def avvio(monkeypatch):
    """Il servizio vero non si avvia mai: si registra solo la richiesta di avvio."""
    from bookwriter.forge import browser_service

    chiamate: list[tuple] = []
    monkeypatch.setattr(browser_service, "start_trawl",
                        lambda port=8191, wait_ready=True, timeout_s=25.0:
                        chiamate.append((port, wait_ready)) or True)
    return chiamate


def _con_servizio(f) -> None:
    f.cfg.research.external_fetcher_url = SERVIZIO


def test_fetch_without_the_service_address_says_so_and_does_nothing(
    tmp_path: Path, bloccata, avvio,
) -> None:
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path)
    with pytest.raises(RuntimeError) as errore:
        f.fetch_blocked_pages()
    assert "Imposta l'indirizzo del servizio esterno in Impostazioni → Ricerca" in str(errore.value)
    assert avvio == [] and f.blocked_pages()[0].status == "in_coda"


def test_fetch_asks_the_service_and_imports_through_the_normal_path(
    tmp_path: Path, bloccata, avvio, httpx_mock,
) -> None:
    modello = Spia()
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path, modello)
    _con_servizio(f)
    voce = f.blocked_pages()[0]
    _servizio_risponde(httpx_mock, voce.url, json={
        "status": "ok", "solution": {"url": voce.url, "status": 200, "response": PAGINA}})
    prima = len(modello.prompt.get("estrazione", []))

    esito = f.fetch_blocked_pages()
    assert esito["total"] == 1 and esito["fetched"] == 1 and esito["still_blocked"] == {}
    assert avvio == [(8191, True)], "servizio locale e avvio automatico: lo avvia e aspetta"
    assert f.blocked_pages()[0].status == "ricevuta"
    pagine = f.chapter_data(1).points[0].pages
    assert len(pagine) == 1 and pagine[0].status == "" and "cerimonia numero 3" in pagine[0].text
    assert len(modello.prompt["estrazione"]) == prima + 1, "estrazione nel punto giusto"
    assert (blocked.ricevute_dir(f.project.research_dir) / f"{voce.id}.html").exists()

    # di nuovo: niente in coda, niente richieste
    assert f.fetch_blocked_pages()["total"] == 0
    assert len(httpx_mock.get_requests(method="POST")) == 1


def test_fetch_keeps_failures_in_queue_updates_the_reason_and_never_stops(
    tmp_path: Path, bloccata, avvio, httpx_mock,
) -> None:
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path)
    _con_servizio(f)
    ricerca = f.project.research_dir
    blocked.enqueue_blocked_page(ricerca, "https://b.org/verifica", 1, 1, "q", "captcha")
    blocked.enqueue_blocked_page(ricerca, "https://c.org/giu", 1, 1, "q", "http_403")
    blocked.enqueue_blocked_page(ricerca, "https://d.org/ok", 1, 1, "q", "http_429")
    prima_url = f.blocked_pages()[0].url
    _servizio_risponde(httpx_mock, prima_url, json={
        "status": "error", "message": "boom", "solution": {"status": 0, "response": ""}})
    _servizio_risponde(httpx_mock, "https://b.org/verifica", json={
        "status": "ok", "solution": {"url": "u", "status": 200, "response": VERIFICA}})
    _servizio_risponde(httpx_mock, "https://c.org/giu", status_code=500, text="giù")
    _servizio_risponde(httpx_mock, "https://d.org/ok", json={
        "status": "ok", "solution": {"url": "u", "status": 200, "response": PAGINA}})

    esito = f.fetch_blocked_pages()
    assert (esito["total"], esito["fetched"], esito["no_answer"]) == (4, 1, 2)
    assert esito["still_blocked"] == {"challenge": 1}, "la pagina mostra ancora una verifica"
    righe = {it.url: it for it in f.blocked_pages()}
    assert righe["https://d.org/ok"].status == "ricevuta"
    assert righe["https://b.org/verifica"].status == "in_coda"
    assert righe["https://b.org/verifica"].reason == "challenge", "motivo aggiornato"
    assert righe[prima_url].status == "in_coda" and righe[prima_url].reason == "captcha"
    assert righe["https://c.org/giu"].status == "in_coda"


def test_fetch_counts_the_pages_the_model_finds_irrelevant(
    tmp_path: Path, bloccata, avvio, httpx_mock,
) -> None:
    class Severo(Spia):
        def chat_json(self, messages, *, schema, model=None, **kw):
            if "score" in set(schema.get("properties", {})):
                return {"score": 0.0, "reason": "parla d'altro"}
            return super().chat_json(messages, schema=schema, model=model)

    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path, Severo())
    _con_servizio(f)
    f.cfg.research.min_relevance = 0.25
    voce = f.blocked_pages()[0]
    _servizio_risponde(httpx_mock, voce.url, json={
        "status": "ok", "solution": {"url": "u", "status": 200, "response": PAGINA}})
    esito = f.fetch_blocked_pages()
    assert esito["fetched"] == 1 and esito["irrelevant"] == 1


def test_fetch_does_not_start_a_service_that_is_not_local_or_when_autostart_is_off(
    tmp_path: Path, bloccata, avvio, httpx_mock,
) -> None:
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path)
    f.cfg.research.external_fetcher_url = "http://servizio.esempio.net:8191/v1"
    httpx_mock.add_response(url="http://servizio.esempio.net:8191/v1", method="POST",
                            json={"status": "error", "solution": {"status": 0, "response": ""}})
    f.fetch_blocked_pages()
    assert avvio == []
    f.cfg.research.external_fetcher_url, f.cfg.research.auto_start_browser = SERVIZIO, False
    httpx_mock.add_response(url=SERVIZIO, method="POST",
                            json={"status": "error", "solution": {"status": 0, "response": ""}})
    f.fetch_blocked_pages()
    assert avvio == []


def test_fetch_stops_when_asked_and_skips_pages_already_in_ricevute(
    tmp_path: Path, bloccata, avvio, httpx_mock,
) -> None:
    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path)
    _con_servizio(f)
    voce = f.blocked_pages()[0]
    ricevute = blocked.ricevute_dir(f.project.research_dir)
    (ricevute / f"{voce.id}.html").write_text(PAGINA, encoding="utf-8")
    esito = f.fetch_blocked_pages()
    assert esito["skipped"] == 1 and httpx_mock.get_requests() == []
    assert f.fetch_blocked_pages(should_stop=lambda: True)["stopped"] is True


def test_the_cli_fetch_command(tmp_path: Path, bloccata, avvio, httpx_mock, monkeypatch) -> None:
    from typer.testing import CliRunner

    from bookwriter import cli

    f, _ = _ricerca_con_una_pagina_bloccata(tmp_path)
    monkeypatch.setattr(cli, "_forge", lambda **k: f)
    esegui = CliRunner()

    senza = esegui.invoke(cli.forge_app, ["research", "blocked", "fetch"])
    assert senza.exit_code == 1
    assert "Imposta l'indirizzo del servizio esterno in Impostazioni → Ricerca" in senza.output

    _con_servizio(f)
    voce = f.blocked_pages()[0]
    _servizio_risponde(httpx_mock, voce.url, json={
        "status": "ok", "solution": {"url": "u", "status": 200, "response": PAGINA}})
    ok = esegui.invoke(cli.forge_app, ["research", "blocked", "fetch"])
    assert ok.exit_code == 0, ok.output
    assert "Recuperate 1 di 1" in ok.output and "Non pertinenti: 0" in ok.output
    vuoto = esegui.invoke(cli.forge_app, ["research", "blocked", "fetch"])
    assert vuoto.exit_code == 0 and "Nessuna pagina in coda da recuperare" in vuoto.output


def test_the_fetch_button_in_the_blocked_pages_tab(temp_workspace, monkeypatch, avvio) -> None:
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from bookwriter.gui.main_window import MainWindow
    from tests.test_gui import _scheda_libro

    QApplication.instance() or QApplication([])
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    pulsante = win.fg_explorer.btn_fetch_blocked
    assert pulsante.text() == "Recupera le pagine in coda"
    from PySide6.QtWidgets import QPushButton

    ordine = [b.text() for b in pulsante.parentWidget().findChildren(QPushButton)]
    assert ordine[:3] == ["Importa le pagine ricevute", "Recupera le pagine in coda",
                          "Apri la cartella della coda"]

    dialoghi: list[tuple[str, str]] = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(
        lambda p, titolo, testo, *a: dialoghi.append((titolo, testo))))
    pulsante.click()  # nessun indirizzo: lo dice e non parte niente
    assert dialoghi == [("Servizio esterno non impostato",
                         "Imposta l'indirizzo del servizio esterno in Impostazioni → Ricerca.")]

    forge = win._forge(with_client=True)
    forge.cfg.research.external_fetcher_url = SERVIZIO
    monkeypatch.setattr(win, "_forge", lambda with_client=False: forge)
    monkeypatch.setattr(forge, "fetch_blocked_pages", lambda progress=None, should_stop=None: {
        "total": 3, "fetched": 1, "still_blocked": {"captcha": 1}, "irrelevant": 1,
        "no_answer": 0, "skipped": 0, "service_ready": True, "stopped": False})
    dialoghi.clear()
    pulsante.click()
    assert dialoghi[-1][0] == "Pagine in coda"
    assert dialoghi[-1][1] == ("Recuperate 1 di 3. Ancora bloccate: 1 captcha. Non pertinenti: 1. "
                               "Senza risposta dal servizio: 0.")
    win.close()
