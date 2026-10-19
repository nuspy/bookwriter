"""La fase «Revisione» nella finestra: la pagina, i salti, la conferma e il passaggio alla stesura."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from bookwriter.forge import research as rs
from bookwriter.forge import review as rv
from bookwriter.gui.main_window import MainWindow
from bookwriter.gui.review_view import status_words
from tests.test_forge import _quaderno_pieno
from tests.test_gui import _forge_inline
from tests.test_review import FintoRevisione


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def niente_rete(monkeypatch):
    monkeypatch.setattr(rs, "gather", lambda *a, **k: ([], []))


def _finestra(monkeypatch, modello=None) -> tuple[MainWindow, FintoRevisione]:
    modello = modello or FintoRevisione()
    win = MainWindow()
    win.pm.create("Revisione")
    monkeypatch.setattr(win, "_client", lambda slug: modello)
    _forge_inline(win, monkeypatch)
    win._refresh_forge()
    forge = win._forge(with_client=True)
    _quaderno_pieno(forge.state)
    forge.make_spec()
    forge.approve_spec()
    forge.research_landscape()
    forge.research_chapters()
    forge.go_to("research")
    win._forge_paint(forge)
    return win, modello


def test_the_phase_bar_has_a_review_button_between_research_and_writing(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win, _ = _finestra(monkeypatch)
    chiavi = list(win.fg_phase_buttons)
    assert chiavi.index("review") == chiavi.index("research") + 1
    assert "Revisione" in win.fg_phase_buttons["review"].text()
    win.close()


def test_advancing_from_research_opens_the_review_page_and_never_blocks(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win, _ = _finestra(monkeypatch)
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    win._forge_advance()
    forge = win._forge()
    assert forge.state.phase == "review"
    assert win.fg_center.currentWidget() is win.fg_review
    assert win.fg_cmds["advance"].text() == "Passa alla stesura"
    assert "facoltativa" in win.fg_todo.text()
    win._forge_advance()  # niente da chiudere: la revisione non è un requisito
    assert win._forge().state.phase == "writing"
    win.close()


def test_the_points_are_prepared_listed_discussed_and_decided(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win, modello = _finestra(monkeypatch)
    win._forge_go("review")
    win._review_generate()
    pagina = win.fg_review
    assert pagina.list.count() >= 2
    primo = pagina.list.item(0).text()
    assert status_words()["aperto"] in primo, "lo stato è scritto in parole, non solo a colori"

    # il materiale viene prima; per la scheda «tengo la mia posizione» serve un contrasto
    assert pagina.list.item(0).text().split("\n")[0].endswith("materiale trovato")
    tensione = next(p for p in win._forge().review_data().points if p.kind == rv.TENSIONE)
    pagina.select(tensione.id)
    assert pagina.current_id == tensione.id
    # l'app propone, ma non registra: serve il clic sulla scheda
    modello.proposta = {"outcome": "imposta", "text": "Così, e basta.", "note": "n", "source": "",
                        "url": "", "structure_note": ""}
    win._review_send("Voglio tenerla così.")
    ruoli = [b.role for b in pagina.chat.view.bubbles()]
    assert ruoli[-1] == "assistant" and "user" in ruoli
    assert modello.sistemi, "il modello ha ricevuto il prompt della revisione"
    assert not pagina.card.isHidden()
    assert pagina.card.text.toPlainText() == "Così, e basta."
    assert win._forge().review_data().point(pagina.current_id).decision is None

    deciso = pagina.current_id
    pagina.card._confirm()
    punto = win._forge().review_data().point(deciso)
    assert punto.decision and punto.decision.kind == "imposta"
    assert not punto.decision.keep_note, "le note negli appunti non sono il default"
    win.close()


def test_skip_point_and_skip_review_change_nothing_and_do_not_block(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win, _ = _finestra(monkeypatch)
    win._forge_go("review")
    win._review_generate()
    pagina = win.fg_review
    pagina.select(pagina.list.item(0).data(0x100))
    scelto = pagina.current_id
    win._review_skip_point()
    assert win._forge().review_data().point(scelto).status == rv.RIMANDATO
    win._review_skip_all()
    forge = win._forge()
    assert forge.state.phase == "writing"
    assert forge._review_for(forge.state.outline[0]) == ""
    win.close()


def test_the_free_chat_shows_the_perplexities(qapp, temp_workspace: Path, monkeypatch) -> None:
    win, modello = _finestra(monkeypatch)
    win._forge_go("review")
    win._review_generate()
    win._review_open_free()
    assert win.fg_review.free_mode
    win._review_send("Ho un dubbio sul finale.")
    assert "Le mie perplessità" in modello.sistemi[-1]
    win.close()


def test_the_card_needs_a_wording_unless_the_point_is_left_out(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    win, _ = _finestra(monkeypatch)
    carta = win.fg_review.card
    firmate: list = []
    carta.confirmed.connect(lambda *a: firmate.append(a))
    carta.open(by_author=True)
    carta.text.setPlainText("   ")
    carta._confirm()
    assert not firmate and not carta.isHidden(), "senza formulazione non si registra"
    carta.kind.setCurrentIndex(carta.kind.findData(rv.D_TRALASCIA))
    carta._confirm()
    assert firmate and firmate[0][0] == rv.D_TRALASCIA
    win.close()


def test_touch_targets_and_accessible_names_on_the_review_page(
    qapp, temp_workspace: Path, monkeypatch,
) -> None:
    from PySide6.QtWidgets import QPushButton

    win, _ = _finestra(monkeypatch)
    pagina = win.fg_review
    for b in pagina.findChildren(QPushButton):
        assert b.minimumHeight() >= 44 or b.minimumSize().height() >= 44, b.text()
    assert pagina.list.accessibleName() and pagina.card.text.accessibleName()
    win.close()
