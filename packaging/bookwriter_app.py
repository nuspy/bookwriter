"""Punto d'ingresso dell'eseguibile compilato.

Un solo ``bookwriter.exe`` fa le due cose che oggi fa ``run.ps1``: senza argomenti apre la
finestra, con argomenti è la riga di comando (``bookwriter.exe forge status``). Compilato con
``--windows-console-mode=attach``: aperto con un doppio clic non mostra nessuna console, lanciato
da un terminale ci scrive dentro.
"""

import sys
from pathlib import Path


def _pyphen_with_real_paths() -> None:
    """Rimette percorsi veri nei dizionari di sillabazione di pyphen.

    Compilato, ``importlib.resources.files`` restituisce oggetti di Nuitka che non si possono
    usare come chiave di un dizionario; pyphen li mette proprio nella sua cache, e la
    composizione del PDF si ferma alla prima parola da sillabare con «unhashable type:
    'nuitka_resource_reader_files'». I file veri stanno accanto all'eseguibile: si usano quelli.
    """
    try:
        import pyphen
    except ImportError:
        return
    cartella = Path(pyphen.__file__).parent / "dictionaries"
    for lingua, percorso in list(pyphen.LANGUAGES.items()):
        if not isinstance(percorso, Path):
            pyphen.LANGUAGES[lingua] = cartella / percorso.name


def main() -> int:
    if "__compiled__" in globals():
        _pyphen_with_real_paths()
    if len(sys.argv) <= 1:
        from bookwriter.gui.app import run

        return run()
    from bookwriter.cli import app

    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
