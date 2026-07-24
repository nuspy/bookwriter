"""Smoke test della GUI: costruzione della finestra in modalità offscreen (nessun display)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from pathlib import Path

from PySide6.QtWidgets import QApplication

from bookwriter.gui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_mainwindow_builds(qapp, temp_workspace: Path) -> None:
    win = MainWindow()
    assert win.tabs.count() == 10
    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    # Titoli funzionali (orientati al compito dell'utente), non ai nomi interni del motore.
    for expected in (
        "Importa testi",
        "Prepara contenuti",
        "Componi il libro",
        "Struttura (modifica)",
        "Stile di scrittura",
        "Completa i contenuti",
        "Riepilogo progetto",
        "Riscrivi in altro stile",
        "Traduci",
        "Scrivi da zero",
    ):
        assert any(expected in t for t in titles), expected
    win.close()


def test_project_bar_reflects_projects(qapp, temp_workspace: Path) -> None:
    win = MainWindow()
    win.pm.create("Progetto GUI")
    win._reload_projects()
    assert win.project_combo.count() >= 1
    # Nessun crash nel calcolo delle info di progetto.
    win._refresh_project_info()
    win.close()
