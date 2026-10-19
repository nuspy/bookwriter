"""Giro 3, la finestra: uno stato che non mente, riprendere/rifare/cancellare, il blocco che dice
quale operazione c'è, la specifica sempre leggibile, i due livelli di ragionamento."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QTextBrowser  # noqa: E402

from bookwriter.forge import research as rs  # noqa: E402
from bookwriter.forge.websearch import Page, SearchResult  # noqa: E402
from bookwriter.gui.main_window import MainWindow  # noqa: E402
from bookwriter.llm.client import LLMResource  # noqa: E402
from tests.test_gui import _forge_inline, _fino_alla_specifica, _scheda_libro  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FintoLavoro:
    """Un lavoro di sfondo che non finisce da solo: lo si fa finire a mano."""

    def __init__(self, busy: str = "Cerco le fonti dei capitoli…", stoppable: bool = True) -> None:
        self.busy, self.stoppable, self.progressable = busy, stoppable, True


def _ferma_le_esecuzioni(win, monkeypatch) -> list[dict]:
    """``_run_bg`` che non esegue: registra i lavori e i loro callback."""
    avviati: list[dict] = []

    def run(fn, on_done, busy="", progressable=False, on_error=None, on_note=None,
            stoppable=False):
        avviati.append({"fn": fn, "done": on_done, "error": on_error, "busy": busy})

    monkeypatch.setattr(win, "_run_bg", run)
    return avviati


# --------------------------------------------------------------------------- #
#  A — lo stato non mente
# --------------------------------------------------------------------------- #
def test_a_short_job_during_the_research_does_not_clear_what_the_status_box_tells(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    avviati = _ferma_le_esecuzioni(win, monkeypatch)

    win._forge_job(lambda p, n: None, lambda r: None, "Cerco le fonti dei capitoli…",
                   activity="Sto cercando le fonti dei capitoli…", kind="research",
                   progressable=True, stoppable=True)
    assert win._fg_activity is not None
    win._workers.add(FintoLavoro())  # la ricerca è viva

    # un lavoro breve (non a progresso) parte e finisce nel frattempo
    win._forge_job(lambda: None, lambda r: None, "Leggo la pagina…",
                   activity="Sto leggendo la pagina…")
    assert win._fg_activity["text"] == "Sto cercando le fonti dei capitoli…"
    avviati[-1]["done"](None)  # il lavoro breve finisce
    assert win._fg_activity is not None, "la ricerca è ancora lì"
    testo = win.fg_doing.text()
    assert testo.startswith("●") and "cercando le fonti" in testo and "Fermo" not in testo
    assert win.fg_status_resume.isHidden() and win.fg_status_redo.isHidden() \
        and win.fg_status_fresh.isHidden(), "mentre lavora non si offre di ricominciare"
    win._workers.clear()
    win.close()


def test_the_status_never_says_idle_while_a_worker_is_alive(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    win._workers.add(FintoLavoro("Elaborazione…"))
    assert win._fg_activity is None
    assert "Fermo" not in win._forge_doing_text()
    assert "operazione in corso" in win._forge_doing_text()
    win._forge_status_update()
    assert "Fermo" not in win.fg_doing.text()
    assert "Aspetta" in win.fg_todo.text()
    win._workers.clear()
    assert "Fermo" in win._forge_doing_text()
    win.close()


def test_the_status_says_when_the_work_waits_for_the_server(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _ferma_le_esecuzioni(win, monkeypatch)
    win._forge_job(lambda p, n: None, lambda r: None, "Cerco le fonti dei capitoli…",
                   activity="Sto cercando le fonti dei capitoli…", kind="research",
                   progressable=True, stoppable=True)
    errore = LLMResource("x", kind="ram", detail="RAM low", free_mib=19589, guard_mib=19603)
    win._on_model_wait(errore, 17)
    testo = win._forge_doing_text()
    assert "In attesa" in testo and "19.589 MiB" in testo and "19.603 MiB" in testo
    assert "riprovo tra 17 s" in testo
    win._on_model_wait(None, 0)
    assert "In attesa" not in win._forge_doing_text()
    win._on_model_wait(LLMResource("y", detail="GPU occupata"), 5)
    assert "GPU occupata" in win._forge_doing_text()
    win.close()


# --------------------------------------------------------------------------- #
#  il blocco «operazione in corso» dice quale
# --------------------------------------------------------------------------- #
def _cattura_dialogo(monkeypatch, scelta: str | None = None) -> dict:
    visto: dict = {}

    def esegui(box):
        visto["testo"] = box.text()
        visto["pulsanti"] = [b.text() for b in box.buttons()]
        if scelta is not None:
            for b in box.buttons():
                if b.text() == scelta:
                    b.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", esegui)
    return visto


def test_the_busy_notice_names_the_operation_and_offers_to_stop_it(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _ferma_le_esecuzioni(win, monkeypatch)
    win._forge_job(lambda p, n: None, lambda r: None, "Cerco le fonti dei capitoli…",
                   activity="Sto cercando le fonti dei capitoli…", kind="research",
                   progressable=True, stoppable=True)
    win._workers.add(FintoLavoro())
    fermate: list[int] = []
    monkeypatch.setattr(win, "_request_stop", lambda: fermate.append(1))

    visto = _cattura_dialogo(monkeypatch, scelta="Ferma l'operazione in corso")
    win._busy_notice()
    assert "Sto cercando le fonti dei capitoli" in visto["testo"]
    assert visto["pulsanti"] == ["Aspetta", "Ferma l'operazione in corso"]
    assert fermate == [1], "il pulsante ferma davvero l'operazione"

    fermate.clear()
    _cattura_dialogo(monkeypatch, scelta="Aspetta")
    win._busy_notice()
    assert fermate == []
    win._workers.clear()
    win.close()


def test_the_busy_notice_does_not_offer_to_stop_what_cannot_be_stopped(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    win._workers.add(FintoLavoro("Importo i testi…", stoppable=False))
    visto = _cattura_dialogo(monkeypatch)
    win._busy_notice()
    assert "Importo i testi" in visto["testo"] and visto["pulsanti"] == ["Aspetta"]
    win._workers.clear()
    win.close()


def test_the_include_and_discard_commands_wait_for_the_research(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Riscrivono lo stesso file del capitolo che la ricerca sta scrivendo."""
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    win._workers.add(FintoLavoro())
    visto = _cattura_dialogo(monkeypatch)
    chiamate: list[str] = []
    win._forge().discard_item = lambda *a, **k: chiamate.append("scarta")
    win._forge_include(1, 0, "https://esempio.org/x")
    win._forge_discard(1, "url", "https://esempio.org/x", True)
    win._forge_discard_old()
    assert chiamate == [] and "pulsanti" in visto
    win._workers.clear()
    win.close()


# --------------------------------------------------------------------------- #
#  F — Riprendi, Rifai, Cancella e rifai
# --------------------------------------------------------------------------- #
def _con_ricerca_a_meta(win, monkeypatch):
    """Alla fase Ricerca, con il panorama fatto e i capitoli no (come dopo un'interruzione)."""
    monkeypatch.setattr(rs, "gather", lambda query, settings, **k: ([], []))
    _fino_alla_specifica(win)
    win._forge_make_spec()
    forge = win._forge(with_client=True)
    forge.cfg.research.enabled = True
    return forge


def test_after_an_interruption_the_resume_button_is_the_one_offered(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    forge = _con_ricerca_a_meta(win, monkeypatch)
    forge.approve_spec()
    forge.research_landscape()      # il panorama c'è, i capitoli no: la ricerca è a metà
    win._forge_paint(forge)
    assert forge.state.phase == "research" and not forge.state.research_done
    assert not win.fg_status_resume.isHidden()
    assert not win.fg_status_redo.isHidden() and not win.fg_status_fresh.isHidden()
    assert "Riprendi la ricerca" in win.fg_todo.text()
    assert "Riprenderebbe dal capitolo 1, punto 1 di 3" in win.fg_todo.text()
    assert "bold" in win.fg_status_resume.styleSheet(), "la scelta consigliata"
    tre = [win.fg_status_resume.text(), win.fg_status_redo.text(), win.fg_status_fresh.text()]
    assert tre == ["Riprendi la ricerca", "Rifai la ricerca", "Cancella e rifai"]
    # le tre dicono cosa fanno, e la differenza tra «rifai» e «cancella e rifai» è nei testi
    assert "tenendo" in win.fg_status_redo.toolTip()
    assert "non lo cancella" in win.fg_status_fresh.toolTip()
    win.close()


def test_a_finished_research_offers_redo_and_fresh_but_not_resume(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    monkeypatch.setattr(rs, "gather", lambda query, settings, **k: (
        [SearchResult("T", "https://e.org/" + query[:5], "e")],
        [Page("https://e.org/" + query[:5], "P", "testo")]))
    _fino_alla_specifica(win)
    win._forge_make_spec()
    win._forge_approve_spec()
    forge = win._forge()
    assert forge.state.research_done
    assert win.fg_status_resume.isHidden()
    assert not win.fg_status_redo.isHidden() and not win.fg_status_fresh.isHidden()
    win.close()


def test_the_three_buttons_start_the_three_modes(qapp, temp_workspace: Path, monkeypatch) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    forge = _con_ricerca_a_meta(win, monkeypatch)
    forge.approve_spec()
    win._forge_paint(forge)
    avviati: list[str] = []
    monkeypatch.setattr(win, "_forge_landscape", lambda then=None: avviati.append("panorama"))
    monkeypatch.setattr(win, "_forge_chapter_research", lambda: avviati.append("capitoli"))
    chiamate: list[str] = []
    monkeypatch.setattr(forge, "prepare_redo", lambda: chiamate.append("azzera") or 0)
    monkeypatch.setattr(forge, "set_aside_research", lambda: chiamate.append("da parte") or None)
    monkeypatch.setattr(win, "_forge", lambda with_client=False: forge)

    # Riprendi: il panorama c'è già (nessun file lo dice qui: lo simula la funzione) -> solo capitoli
    monkeypatch.setattr(forge, "landscape_data", lambda: object())
    win.fg_status_resume.click()
    assert avviati == ["capitoli"] and chiamate == []

    avviati.clear()
    win.fg_status_redo.click()
    assert chiamate == ["azzera"] and avviati == ["panorama"], "rifare riscrive anche il panorama"

    # Cancella e rifai chiede conferma: «No» non fa niente
    chiamate.clear(); avviati.clear()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    win.fg_status_fresh.click()
    assert chiamate == [] and avviati == []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win.fg_status_fresh.click()
    assert chiamate == ["da parte"] and avviati == ["panorama"]
    win.close()


def test_the_confirmation_of_fresh_says_nothing_is_deleted(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    forge = _con_ricerca_a_meta(win, monkeypatch)
    forge.approve_spec()
    testi: list[str] = []

    def chiedi(parent, titolo, testo, *a, **k):
        testi.append(testo)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(chiedi))
    win._forge_research_all(mode="fresh")
    assert "non si cancellano" in testi[0] and ".superati" in testi[0]
    win.close()


# --------------------------------------------------------------------------- #
#  C — la specifica si legge sempre
# --------------------------------------------------------------------------- #
def _leggi_dialogo(monkeypatch) -> dict:
    visto: dict = {}

    def esegui(dialogo):
        visto["titolo"] = dialogo.windowTitle()
        vista = dialogo.findChild(QTextBrowser)
        visto["testo"] = vista.toPlainText() if vista else ""
        visto["modificabile"] = bool(vista) and not vista.isReadOnly()
        return 0

    monkeypatch.setattr(QDialog, "exec", esegui)
    return visto


def test_the_specification_stays_readable_after_the_approval(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(rs, "gather", lambda query, settings, **k: ([], []))
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _fino_alla_specifica(win)
    win._forge_make_spec()
    assert win.fg_read_spec.isEnabled()
    win._forge_approve_spec()
    forge = win._forge()
    assert forge.state.phase == "research" and forge.state.spec_approved

    visto = _leggi_dialogo(monkeypatch)
    win._forge_read_spec()
    assert visto["titolo"] == "Specifica del libro"
    assert "Il passaggio 1" in visto["testo"], "il testo della specifica"
    assert not visto["modificabile"], "in sola lettura"
    win.close()


def test_entering_the_specification_phase_shows_the_specification(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    """Prima il centro mostrava la specifica solo subito dopo averla scritta."""
    monkeypatch.setattr(rs, "gather", lambda query, settings, **k: ([], []))
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    _fino_alla_specifica(win)
    win._forge_make_spec()
    win._forge_approve_spec()
    win._forge_show("Un resoconto qualsiasi")   # qualcos'altro occupa il centro
    win._forge_go("spec")
    assert win.fg_center.currentWidget() is win.fg_log
    assert "Il passaggio 1" in win.fg_log.toPlainText()
    # un riavvio: finestra nuova sullo stesso libro, già approvato
    altra = MainWindow()
    altra.pm.set_active(win.pm.active_slug())
    monkeypatch.setattr(altra, "_client", lambda slug: win._client(slug))
    _forge_inline(altra, monkeypatch)
    altra._refresh_forge()
    altra._forge_go("spec")
    assert "Il passaggio 1" in altra.fg_log.toPlainText()
    win.close()
    altra.close()


def test_there_is_nothing_to_read_before_the_specification_exists(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    assert not win.fg_read_spec.isEnabled()
    mostrati: list[str] = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda p, titolo, testo, *a: mostrati.append(titolo)))
    win._forge_read_spec()
    assert mostrati == ["Niente da leggere"]
    win.close()


# --------------------------------------------------------------------------- #
#  i due livelli di ragionamento nelle impostazioni
# --------------------------------------------------------------------------- #
def test_the_quick_reasoning_level_is_saved_and_read_back(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from tests.test_providers_settings import _finestra

    win = _finestra(monkeypatch)
    assert win.set_effort_quick.currentData() == "same", "predefinito: come l'altro livello"
    assert win.set_effort_quick.itemText(0) == "Come l'altro livello"
    for livello in ("low", "", "same"):
        win.set_effort.setCurrentIndex(win.set_effort.findData("high"))
        win.set_effort_quick.setCurrentIndex(win.set_effort_quick.findData(livello))
        win._save_settings()
        cfg = win.pm.config_for(win.pm.active_slug()).llm
        assert cfg.reasoning_effort == "high" and cfg.reasoning_effort_quick == livello
        win._refresh_settings()
        assert win.set_effort_quick.currentData() == livello
    win.close()


def test_the_default_quick_level_leaves_the_config_file_unchanged_in_meaning() -> None:
    from bookwriter.config import LLMConfig

    c = LLMConfig(reasoning_effort="high")
    assert c.reasoning_for(True) == c.reasoning_for(False) == "high"
