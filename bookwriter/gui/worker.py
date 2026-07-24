"""Esecuzione di operazioni (spesso LLM) in un thread di background, per non bloccare la GUI."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)  # (done, total)


class Worker(QRunnable):
    def __init__(self, fn: Callable[..., Any], *, progressable: bool = False) -> None:
        super().__init__()
        self.fn = fn
        self.progressable = progressable
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self.progressable:
                result = self.fn(lambda done, total: self.signals.progress.emit(done, total))
            else:
                result = self.fn()
        except Exception as exc:  # noqa: BLE001 - riportato alla GUI
            self.signals.error.emit(str(exc))
        else:
            self.signals.finished.emit(result)
