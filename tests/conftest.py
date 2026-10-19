"""Fixture condivise: workspace e registro delle chiavi isolati per i test.

``workspace_root()`` legge ``BOOKWRITER_HOME`` a ogni chiamata, quindi basta impostare l'env var:
niente reload di moduli.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def temp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    ws = tmp_path / "ws"
    monkeypatch.setenv("BOOKWRITER_HOME", str(ws))
    return ws


@pytest.fixture(autouse=True)
def registro_chiavi_isolato(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Il registro comune delle chiavi punta a una cartella usa e getta, per ogni test.

    Senza questo, un test che salva una chiave la scrive nel registro **vero** dell'utente —
    condiviso con tutti gli altri programmi che usano llmswitch. È già successo: un test della
    GUI ci ha lasciato dentro un fornitore con una chiave finta. Vale per tutti i test, non
    solo per quelli che se lo aspettano: nessuno deve poter sporcare la macchina di chi li
    esegue.
    """
    fuori_dai_piedi = tmp_path / "appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(fuori_dai_piedi))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fuori_dai_piedi))  # per le macchine non Windows
    return fuori_dai_piedi


@pytest.fixture(autouse=True)
def nessun_test_resta_appeso():
    """Un test che non finisce in cinque minuti fa terminare il processo con la traccia di dove sta.

    Meglio una suite che si ferma e dice dove, di una che resta appesa senza dire niente: è quel
    che succede con un server finto che non risponde o con un thread che non si chiude.
    """
    import faulthandler

    faulthandler.dump_traceback_later(300, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def nessuna_finestra_modale_inattesa(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una finestra modale non attesa fa fallire il test invece di bloccarlo per sempre.

    Nessun utente la chiuderebbe: il test resterebbe appeso. I test che si aspettano una finestra
    sostituiscono il metodo con il loro (``monkeypatch.setattr(QMessageBox, "warning", …)``) e
    questo non li tocca.
    """
    try:
        from PySide6.QtWidgets import QDialog, QMessageBox
    except ImportError:
        return

    def inattesa(nome: str):
        def _solleva(*args, **kwargs):
            testo = " | ".join(str(a) for a in args[1:3]) if len(args) > 2 else ""
            raise AssertionError(f"finestra modale inattesa: {nome} {testo}")
        return staticmethod(_solleva)

    for nome in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, nome, inattesa(nome))
    monkeypatch.setattr(QDialog, "exec", lambda self: (_ for _ in ()).throw(
        AssertionError("finestra modale inattesa: QDialog.exec")))
