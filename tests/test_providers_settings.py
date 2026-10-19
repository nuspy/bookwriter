"""Parte D: il ragionamento si imposta dalla scheda dei modelli, e i server personalizzati vivono
nel registro di llmswitch. Il registro è sempre quello isolato dei test (conftest)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from bookwriter.config import EmbeddingConfig, LLMConfig  # noqa: E402
from bookwriter.gui.main_window import MainWindow  # noqa: E402
from bookwriter.gui.servers_dialog import ServerDialog  # noqa: E402
from bookwriter.llm import ModelInfo  # noqa: E402
from bookwriter.llm import servers as llm_servers  # noqa: E402
from bookwriter.llm.client import LMStudioClient  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def niente_rete(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MainWindow, "_load_models",
                        lambda self, *, force=False: self._fill_model_combos())


def _client(cfg: LLMConfig) -> LMStudioClient:
    c = LMStudioClient(cfg, EmbeddingConfig())
    c._caricati, c._caricati_letti_a = {cfg.models.reasoning: None}, time.monotonic()
    return c


def _finestra(monkeypatch) -> MainWindow:
    win = MainWindow()
    win.pm.create("Impostazioni")
    win._models = [ModelInfo(id="qwen3-8b", type="llm", state="not-loaded"),
                   ModelInfo(id="text-embedding-nomic-embed-text-v1.5", type="embeddings",
                             state="not-loaded")]
    win._refresh_settings()
    win._fill_model_combos()
    return win


# --------------------------------------------------------------------------- #
#  D1 — il ragionamento
# --------------------------------------------------------------------------- #
def test_the_reasoning_dropdown_offers_every_level_and_the_server_choice(qapp, temp_workspace) -> None:
    win = MainWindow()
    valori = [win.set_effort.itemData(i) for i in range(win.set_effort.count())]
    assert valori == ["", "none", "minimal", "low", "medium", "high", "xhigh", "max"]
    assert win.set_effort.itemText(0) == "Lascia decidere al server"
    assert "token" in win.set_effort.toolTip()
    win.close()


@pytest.mark.parametrize("livello", ["high", "low", "xhigh", ""])
def test_the_reasoning_level_is_saved_and_read_back(qapp, temp_workspace, monkeypatch, livello) -> None:
    win = _finestra(monkeypatch)
    win.set_effort.setCurrentIndex(win.set_effort.findData(livello))
    win._save_settings()
    slug = win.pm.active_slug()
    assert win.pm.config_for(slug).llm.reasoning_effort == livello

    altra = MainWindow()                       # una finestra nuova rilegge il valore salvato
    altra.pm.set_active(slug)
    altra._refresh_settings()
    assert altra.set_effort.currentData() == livello
    win.close()
    altra.close()


def test_inherit_sends_nothing_and_a_level_arrives_in_the_dialect_of_the_provider(
    httpx_mock: HTTPXMock,
) -> None:
    risposta = {"choices": [{"message": {"content": "ok"}}]}
    # «lascia decidere al server»: nessun parametro
    for _ in range(2):
        httpx_mock.add_response(url="http://localhost:1234/v1/chat/completions", json=risposta)
    _client(LLMConfig(reasoning_effort="")).chat([{"role": "user", "content": "x"}])
    corpo = json.loads(httpx_mock.get_requests()[-1].content)
    assert not {"reasoning_effort", "reasoning_budget", "thinking", "chat_template_kwargs"} & set(corpo)

    # OpenAI e compatibili: reasoning_effort
    _client(LLMConfig(reasoning_effort="xhigh")).chat([{"role": "user", "content": "x"}])
    assert json.loads(httpx_mock.get_requests()[-1].content)["reasoning_effort"] == "xhigh"

    # NVIDIA NIM: un altro dialetto — enable_thinking e un budget, mai reasoning_effort
    httpx_mock.add_response(url="https://integrate.api.nvidia.com/v1/chat/completions", json=risposta)
    cfg = LLMConfig(provider="nvidia", base_url="https://integrate.api.nvidia.com/v1",
                    api_key="k", reasoning_effort="high")
    _client(cfg).chat([{"role": "user", "content": "x"}])
    corpo = json.loads(httpx_mock.get_requests()[-1].content)
    assert corpo["chat_template_kwargs"]["enable_thinking"] is True
    assert corpo["reasoning_budget"] > 0
    assert "reasoning_effort" not in corpo


def test_no_token_cap_is_ever_added_by_the_reasoning_setting(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="http://localhost:1234/v1/chat/completions",
                            json={"choices": [{"message": {"content": "ok"}}]})
    _client(LLMConfig(reasoning_effort="max")).chat([{"role": "user", "content": "x"}])
    assert "max_tokens" not in json.loads(httpx_mock.get_requests()[-1].content)


# --------------------------------------------------------------------------- #
#  D2 — server personalizzati
# --------------------------------------------------------------------------- #
def test_the_registry_under_test_is_the_isolated_one(tmp_path: Path) -> None:
    assert str(tmp_path) in str(llm_servers.registry().path)


def test_a_custom_server_is_added_to_the_registry_listed_and_marked(qapp, temp_workspace, monkeypatch) -> None:
    win = _finestra(monkeypatch)
    monkeypatch.setattr(win, "_server_dialog", lambda voce: {
        "name": "Il mio router", "base_url": "http://127.0.0.1:7232/v1", "tipo": "openai",
        "api_key": "segreta", "reasoning_level": "high"})
    win._server_add()

    voci = llm_servers.list_custom()
    assert len(voci) == 1 and voci[0]["name"] == "Il mio router"
    assert voci[0]["base_url"] == "http://127.0.0.1:7232/v1" and voci[0]["reasoning_level"] == "high"
    # compare nella tendina, distinguibile, ed è quello scelto
    etichette = [win.set_provider.itemText(i) for i in range(win.set_provider.count())]
    assert "Il mio router (personalizzato)" in etichette
    assert win.set_provider.currentData() == f"custom:{voci[0]['id']}"
    # indirizzo, chiave e ragionamento vengono da lui
    assert win.set_base_url.text() == "http://127.0.0.1:7232/v1"
    assert win.set_api_key.text() == "segreta"
    assert win.set_effort.currentData() == "high"
    assert win.set_server_edit.isEnabled() and win.set_server_remove.isEnabled()
    win.close()


def test_the_client_uses_the_url_and_key_of_the_chosen_server(
    qapp, temp_workspace, monkeypatch, httpx_mock: HTTPXMock,
) -> None:
    win = _finestra(monkeypatch)
    monkeypatch.setattr(win, "_server_dialog", lambda voce: {
        "name": "Router", "base_url": "http://127.0.0.1:7232/v1", "tipo": "openai",
        "api_key": "chiave-del-router", "reasoning_level": "high"})
    win._server_add()
    win.set_reasoning.setCurrentIndex(0)
    win._save_settings()
    cfg = win.pm.config_for(win.pm.active_slug())
    assert cfg.llm.provider_id.startswith("bw-router") and cfg.llm.provider == "openai"
    assert cfg.llm.api_key == "", "la chiave sta nel registro, non nel file del progetto"
    assert cfg.llm.resolved_api_key() == "chiave-del-router"
    assert cfg.llm.reasoning_effort == "high"

    httpx_mock.add_response(url="http://127.0.0.1:7232/v1/chat/completions",
                            json={"choices": [{"message": {"content": "ciao"}}]})
    client = _client(cfg.llm)
    assert client.chat([{"role": "user", "content": "x"}]) == "ciao"
    richiesta = httpx_mock.get_requests()[-1]
    assert richiesta.headers["Authorization"] == "Bearer chiave-del-router"
    assert json.loads(richiesta.content)["reasoning_effort"] == "high"
    win.close()


def test_the_openai_key_in_the_environment_does_not_leak_into_a_custom_server(
    qapp, temp_workspace, monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "chiave-di-openai")
    voce = llm_servers.add_custom("Mio", "http://127.0.0.1:1/v1", "openai", "la-sua")
    cfg = LLMConfig(provider="openai", provider_id=voce["id"], base_url="http://127.0.0.1:1/v1")
    assert cfg.resolved_api_key() == "la-sua"
    assert LLMConfig(provider="openai").resolved_api_key() == "chiave-di-openai"


def test_the_try_button_lists_the_models_or_says_why_not(qapp, temp_workspace, monkeypatch) -> None:
    from llmswitch import providers as lls_providers

    dialogo = ServerDialog(None)
    dialogo._threaded = False
    dialogo.url.setText("http://127.0.0.1:7232/v1")
    visti: list[str] = []

    def modelli(base_url, api_key, extra=None):
        visti.append(base_url)
        return [{"id": "qwen-flash", "label": "qwen-flash"}, {"id": "altro", "label": "altro"}]

    monkeypatch.setattr(lls_providers, "_openai_models", modelli)
    dialogo._try()
    assert "2 modelli" in dialogo.result.text() and "qwen-flash" in dialogo.result.text()
    assert visti == ["http://127.0.0.1:7232/v1"]
    assert llm_servers.list_custom() == [], "provare non scrive nel registro"

    def guasto(base_url, api_key, extra=None):
        raise ConnectionError("connessione rifiutata")

    monkeypatch.setattr(lls_providers, "_openai_models", guasto)
    dialogo._try()
    assert "non risponde" in dialogo.result.text() and "rifiutata" in dialogo.result.text()
    assert dialogo.try_button.minimumHeight() >= 44 and dialogo.ok.minimumHeight() >= 44


def test_the_dialog_refuses_bad_data_in_words(qapp, temp_workspace) -> None:
    d = ServerDialog(None)
    assert "nome" in d.problem()
    d.name.setText("X")
    d.url.setText("127.0.0.1:7232")
    assert "http" in d.problem()
    d.url.setText("http://127.0.0.1:7232/v1")
    assert d.problem() == ""
    tipi = [d.kind.itemData(i) for i in range(d.kind.count())]
    assert set(tipi) <= {"openai", "llamacpp"}, "solo i tipi compatibili con OpenAI"


def test_a_custom_server_can_be_edited_and_removed(qapp, temp_workspace, monkeypatch) -> None:
    win = _finestra(monkeypatch)
    monkeypatch.setattr(win, "_server_dialog", lambda voce: {
        "name": "Router", "base_url": "http://127.0.0.1:1/v1", "tipo": "openai",
        "api_key": "", "reasoning_level": "inherit"})
    win._server_add()
    pid = llm_servers.list_custom()[0]["id"]

    monkeypatch.setattr(win, "_server_dialog", lambda voce: {
        "name": "Router nuovo", "base_url": "http://127.0.0.1:2/v1", "tipo": "openai",
        "api_key": "k2", "reasoning_level": "low"})
    win._server_edit()
    voce = llm_servers.get_custom(pid)
    assert (voce["name"], voce["base_url"], voce["api_key"], voce["reasoning_level"]) == (
        "Router nuovo", "http://127.0.0.1:2/v1", "k2", "low")
    assert win.set_base_url.text() == "http://127.0.0.1:2/v1"

    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.No))
    win._server_remove()
    assert llm_servers.get_custom(pid) is not None, "con il no non si toglie niente"
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: QMessageBox.StandardButton.Yes))
    win._server_remove()
    assert llm_servers.get_custom(pid) is None
    assert win.set_provider.currentData() == "lmstudio"
    assert not win.set_server_remove.isEnabled()
    win.close()


def test_only_the_servers_this_program_made_are_listed_or_removable(qapp, temp_workspace) -> None:
    from bookwriter.llm.servers import registry

    altrui = registry().add({"type": "openai", "name": "Di un altro programma",
                             "base_url": "http://altro/v1"})
    mio = llm_servers.add_custom("Mio", "http://127.0.0.1:1/v1")
    assert [v["id"] for v in llm_servers.list_custom()] == [mio["id"]]
    with pytest.raises(KeyError):
        llm_servers.remove_custom(altrui["id"])
    assert any(p["id"] == altrui["id"] for p in registry().load()["providers"])


def test_the_status_line_names_the_chosen_provider(qapp, temp_workspace, monkeypatch) -> None:
    win = _finestra(monkeypatch)
    monkeypatch.setattr(win, "_server_dialog", lambda voce: {
        "name": "Il mio router", "base_url": "http://127.0.0.1:1/v1", "tipo": "openai",
        "api_key": "", "reasoning_level": "inherit"})
    win._server_add()
    win._models = []
    win._fill_model_combos()
    assert win.set_status.text() == "Nessun modello rilevato su «Il mio router»."
    win._models = [ModelInfo(id="m1", type="llm", state="not-loaded")]
    win._fill_model_combos()
    assert "su «Il mio router»" in win.set_status.text() and "LM Studio" not in win.set_status.text()
    win.set_provider.setCurrentIndex(win.set_provider.findData("lmstudio"))
    win._models = []
    win._fill_model_combos()
    assert win.set_status.text() == "Nessun modello rilevato su «LM Studio»."
    win.close()


# --------------------------------------------------------------------------- #
#  Output strutturato
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("modo", ["json_schema", "json_object", "text"])
def test_the_structured_output_is_saved_and_read_back_for_a_predefined_provider(
    qapp, temp_workspace, monkeypatch, modo,
) -> None:
    win = _finestra(monkeypatch)
    assert win.set_structured.currentData() == "json_schema", "predefinito"
    win.set_structured.setCurrentIndex(win.set_structured.findData(modo))
    win._save_settings()
    cfg = win.pm.config_for(win.pm.active_slug())
    assert cfg.llm.structured_output == modo and cfg.llm.structured_mode() == modo

    altra = MainWindow()
    altra.pm.set_active(win.pm.active_slug())
    altra._refresh_settings()
    assert altra.set_structured.currentData() == modo and altra.set_structured.isEnabled()
    win.close()
    altra.close()


def test_the_structured_output_of_a_custom_server_lives_in_its_dialog(qapp, temp_workspace, monkeypatch) -> None:
    d = ServerDialog(None)
    assert d.structured.currentData() == "json_schema"
    assert [d.structured.itemData(i) for i in range(d.structured.count())] == [
        "json_schema", "json_object", "text"]
    d.structured.setCurrentIndex(d.structured.findData("text"))
    assert d.values()["structured_output"] == "text"

    win = _finestra(monkeypatch)
    monkeypatch.setattr(win, "_server_dialog", lambda voce: {
        "name": "Router", "base_url": "http://127.0.0.1:1/v1", "tipo": "openai", "api_key": "",
        "reasoning_level": "inherit", "structured_output": "json_object"})
    win._server_add()
    voce = llm_servers.list_custom()[0]
    assert voce["structured_output"] == "json_object"
    assert win.set_structured.currentData() == "json_object"
    assert not win.set_structured.isEnabled(), "per un server personalizzato si cambia nella sua finestra"
    win._save_settings()
    cfg = win.pm.config_for(win.pm.active_slug())
    assert cfg.llm.structured_mode() == "json_object"

    monkeypatch.setattr(win, "_server_dialog", lambda v: {
        "name": "Router", "base_url": "http://127.0.0.1:1/v1", "tipo": "openai", "api_key": "",
        "reasoning_level": "inherit", "structured_output": "text"})
    win._server_edit()
    assert llm_servers.get_custom(voce["id"])["structured_output"] == "text"
    assert cfg.llm.structured_mode() == "text", "una modifica nella finestra vale subito"
    win.close()


# --------------------------------------------------------------------------- #
#  campionamento: visibile nelle impostazioni, vuoto = decide il server
# --------------------------------------------------------------------------- #
def test_sampling_fields_are_empty_by_default_and_saved_when_filled(
    qapp, temp_workspace, monkeypatch,
) -> None:
    win = _finestra(monkeypatch)
    assert all(campo.text() == "" for campo in win.set_sampling.values())
    win.set_sampling["temperature"].setText("1,0")      # la virgola decimale si capisce
    win.set_sampling["top_k"].setText("20")
    win._save_settings()
    slug = win.pm.active_slug()
    llm = win.pm.config_for(slug).llm
    assert (llm.temperature, llm.top_k, llm.top_p) == (1.0, 20, None)

    altra = MainWindow()
    altra.pm.set_active(slug)
    altra._refresh_settings()
    assert altra.set_sampling["temperature"].text() == "1"
    assert altra.set_sampling["top_p"].text() == ""
    win.close()
    altra.close()


def test_an_invalid_sampling_value_is_refused_and_nothing_is_saved(
    qapp, temp_workspace, monkeypatch,
) -> None:
    win = _finestra(monkeypatch)
    avvisi: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: avvisi.append(a[2])))
    win.set_sampling["top_p"].setText("7")               # top_p sta in (0, 1]
    win._save_settings()
    assert avvisi and "top_p" in avvisi[0]
    assert win.pm.config_for(win.pm.active_slug()).llm.top_p is None
    win.close()
