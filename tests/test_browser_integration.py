"""Test per l'integrazione del browser e la gestione delle pagine bloccate.

Copre:
- browser_service (chiamate FlareSolverr /v1, rilevamento Bun e directory TRAWL)
- blocked (rilevamento motivi di blocco, coda.jsonl, importazione da ricevute/)
- websearch.fetch con escalation a FlareSolverr
- comandi CLI forge research blocked
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from bookwriter.config import ResearchSettings
from bookwriter.forge import blocked, browser_service
from bookwriter.forge.websearch import Page, fetch


def test_find_bun_and_trawl_dir() -> None:
    """Verifica che bun e la cartella tools/trawl siano rintracciabili."""
    t_dir = browser_service.trawl_dir()
    assert t_dir.exists()
    assert (t_dir / "apps" / "api" / "src" / "index.ts").exists()

    bun = browser_service.find_bun()
    assert bun is not None
    assert "bun" in bun.lower()


def test_detect_block_reason() -> None:
    """Verifica il riconoscimento di tutti i tipi di blocco di docs/browser_integration.md."""
    assert blocked.detect_block_reason("https://x.com", http_code=403) == "http_403"
    assert blocked.detect_block_reason("https://x.com", http_code=429) == "http_429"
    assert blocked.detect_block_reason("https://x.com", http_code=503) == "http_503"

    assert blocked.detect_block_reason("https://x.com", html="<div class='cf-turnstile'></div>") == "captcha"
    assert blocked.detect_block_reason("https://x.com", html="<div id='g-recaptcha'></div>") == "captcha"
    assert blocked.detect_block_reason("https://x.com", html="Just a moment... checking your browser") == "challenge"
    assert blocked.detect_block_reason("https://x.com", html="DDoS-GUARD protection") == "challenge"

    assert blocked.detect_block_reason("https://x.com", clean_reason="muro di cookie o di consenso") == "cookie_wall"
    assert blocked.detect_block_reason("https://x.com", clean_reason="muro di accesso o pagina che richiede JavaScript") == "login_wall"
    assert blocked.detect_block_reason("https://x.com", clean_reason="solo JavaScript") == "javascript_only"


def test_make_page_id() -> None:
    pid1 = blocked.make_page_id(1, 2, "https://example.com/art1")
    pid2 = blocked.make_page_id(1, 2, "https://example.com/art1?utm_source=foo")
    # norm_url toglie utm_source, quindi l'id deve essere identico
    assert pid1 == pid2
    assert pid1.startswith("c01p2-")


def test_coda_operations(tmp_path: Path) -> None:
    res_dir = tmp_path / "research"

    item1 = blocked.enqueue_blocked_page(res_dir, "https://example.com/1", 1, 1, "query 1", "captcha")
    assert item1.status == blocked.STATUS_IN_CODA
    assert (res_dir / "bloccate" / "coda.jsonl").exists()

    # Inserimento duplicato non crea una seconda riga
    item2 = blocked.enqueue_blocked_page(res_dir, "https://example.com/1", 1, 1, "query 1", "captcha")
    assert item2.id == item1.id

    coda = blocked.load_coda(res_dir)
    assert len(coda) == 1
    assert coda[0].reason == "captcha"


def test_import_ricevute(tmp_path: Path) -> None:
    res_dir = tmp_path / "research"
    item = blocked.enqueue_blocked_page(res_dir, "https://example.com/art", 1, 1, "ayahuasca", "captcha")

    # Mettiamo un file con testo sufficiente nella cartella ricevute
    r_dir = blocked.ricevute_dir(res_dir)
    html_file = r_dir / f"{item.id}.html"
    body_text = " ".join(["Questa è una frase completa sul tema dell'articolo con abbastanza parole per superare la soglia."] * 10)
    html_file.write_text(f"<html><head><title>Articolo Valido</title></head><body><p>{body_text}</p></body></html>", encoding="utf-8")

    # Mettiamo anche un file non riconosciuto
    (r_dir / "estraneo.html").write_text("<p>Test</p>", encoding="utf-8")

    esiti = blocked.import_ricevute(res_dir, min_chars=100)
    assert len(esiti) == 2

    ok_item = next(e for e in esiti if e["id"] == item.id)
    assert ok_item["status"] == "ok"
    assert ok_item["page"].readable is True

    estraneo = next(e for e in esiti if e["file"] == "estraneo.html")
    assert estraneo["status"] == blocked.STATUS_NON_RICONOSCIUTA

    # Verifichiamo che la riga in coda.jsonl sia passata a 'ricevuta'
    coda = blocked.load_coda(res_dir)
    assert coda[0].status == blocked.STATUS_RICEVUTA


def test_fetch_via_flaresolverr_mock() -> None:
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ok",
            "solution": {
                "url": "https://example.com",
                "status": 200,
                "response": "<html><body><p>Risolto da FlareSolverr</p></body></html>",
            },
        }
        mock_post.return_value = mock_resp

        res = browser_service.fetch_via_flaresolverr("https://example.com", endpoint="http://127.0.0.1:8191/v1")
        assert res is not None
        code, html = res
        assert code == 200
        assert "Risolto da FlareSolverr" in html


def test_websearch_fetch_escalation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica che fetch() scali a FlareSolverr se il download diretto riceve HTTP 403."""
    settings = ResearchSettings(
        enabled=True,
        external_fetcher_url="http://127.0.0.1:8191/v1",
        min_page_chars=50,
    )

    import httpx

    # Mock del client httpx che restituisce 403 sul fetch diretto
    orig_get = httpx.Client.get

    def fake_get(self, url, *args, **kwargs):
        req = httpx.Request("GET", url)
        resp = httpx.Response(403, request=req)
        resp.raise_for_status()
        return resp

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    # Mock del browser_service che risolve la pagina
    prosa = " ".join(["Testo lungo e leggibile estratto con successo dal browser esterno Camoufox."] * 6)
    solved_html = f"<html><head><title>Sito Protetto</title></head><body><article><p>{prosa}</p></article></body></html>"

    monkeypatch.setattr(
        browser_service,
        "fetch_via_flaresolverr",
        lambda url, endpoint, timeout_s: (200, solved_html),
    )

    page = fetch("https://protetto.org/doc", settings)
    assert page is not None
    assert page.readable is True
    assert "Testo lungo e leggibile" in page.text
    assert page.title == "Sito Protetto"


def test_cli_blocked_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bookwriter import cli

    runner = CliRunner()
    res = runner.invoke(cli.app, ["forge", "research", "blocked", "--help"])
    assert res.exit_code == 0
    assert "list" in res.output
    assert "export" in res.output
    assert "import" in res.output
    assert "update-trawl" in res.output
    assert "start-trawl" in res.output



def test_update_trawl_service_and_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica che update_trawl esegua le operazioni e sia invocabile da CLI."""
    t_dir = tmp_path / "tools" / "trawl"
    t_dir.mkdir(parents=True)

    calls = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        class Res:
            returncode = 0
            stdout = "abc1234\n"
            stderr = ""
        return Res()

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(browser_service, "find_bun", lambda: "fake_bun")
    monkeypatch.setattr(browser_service, "trawl_dir", lambda: t_dir)
    monkeypatch.setattr("shutil.which", lambda prog: f"/bin/{prog}")

    res = browser_service.update_trawl()
    assert res["status"] == "ok"
    assert (t_dir / ".env").exists()
    assert "PORT=8191" in (t_dir / ".env").read_text(encoding="utf-8")

    from bookwriter import cli
    runner = CliRunner()
    cli_res = runner.invoke(cli.app, ["forge", "update-trawl"])
    assert cli_res.exit_code == 0
    assert "TRAWL aggiornato con successo" in cli_res.output

