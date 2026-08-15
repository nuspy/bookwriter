"""Struttura tipografica del documento: si traduce il testo, non l'impaginazione.

Fino a ieri l'import appiattiva tutto in una stringa: titoli, capitoli, interruzioni di pagina e
corsivi sparivano lì, e l'export ricostruiva un documento qualsiasi. Qui il documento viene invece
scomposto in **segmenti** (``layout.model``), estratti dal file d'origine (``layout.extract``) e
resi nel formato richiesto (``layout.render``). Al modello va solo il testo dei segmenti: la
struttura resta di competenza del programma e arriva intatta in fondo.
"""

from bookwriter.layout.extract import extract_layout
from bookwriter.layout.model import (
    BLANK,
    HEADING,
    OPAQUE,
    PAGEBREAK,
    PARAGRAPH,
    DocumentLayout,
    Segment,
)
from bookwriter.layout.render import layout_to_markdown, write_layout

__all__ = [
    "BLANK",
    "HEADING",
    "OPAQUE",
    "PAGEBREAK",
    "PARAGRAPH",
    "DocumentLayout",
    "Segment",
    "extract_layout",
    "layout_to_markdown",
    "write_layout",
]
