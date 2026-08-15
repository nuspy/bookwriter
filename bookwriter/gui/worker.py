"""Esecuzione di operazioni (spesso LLM) in un thread di background, per non bloccare la GUI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)  # (done, total)
    note = Signal(object)  # aggiornamento intermedio da mostrare mentre il lavoro prosegue


class Worker(QRunnable):
    """Esegue ``fn`` fuori dal thread della finestra.

    Con ``progressable`` la funzione riceve un callback ``(fatti, totale)``; con ``narrating``
    ne riceve un secondo per raccontare cosa sta succedendo (a che punto è la coda, quale libro è
    finito). Passano entrambi dai segnali di Qt, che è l'unico modo lecito per toccare la finestra
    da un altro thread.
    """

    def __init__(
        self, fn: Callable[..., Any], *, progressable: bool = False, narrating: bool = False
    ) -> None:
        super().__init__()
        self.fn = fn
        self.progressable = progressable
        self.narrating = narrating
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            avanzamento = (lambda done, total: self.signals.progress.emit(done, total))
            if self.progressable and self.narrating:
                result = self.fn(avanzamento, lambda nota: self.signals.note.emit(nota))
            elif self.progressable:
                result = self.fn(avanzamento)
            else:
                result = self.fn()
        except Exception as exc:  # noqa: BLE001 - riportato alla GUI
            self._emit(self.signals.error, str(exc))
        else:
            self._emit(self.signals.finished, result)

    @staticmethod
    def _emit(signal, value: Any) -> None:
        """Se la finestra è stata chiusa mentre il lavoro girava, non c'è più nessuno da avvisare."""
        try:
            signal.emit(value)
        except RuntimeError:
            pass
