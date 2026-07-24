"""Avvio dell'applicazione grafica."""

from __future__ import annotations

import sys


def run() -> int:
    from PySide6.QtWidgets import QApplication

    from bookwriter.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
