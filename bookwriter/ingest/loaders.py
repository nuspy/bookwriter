"""Caricamento del documento: il testo **e** la struttura che lo tiene insieme.

txt/md sono nativi (nessuna dipendenza). pdf/docx/epub usano dipendenze opzionali (extra
``ingest``), importate in modo lazy con messaggio d'errore utile se mancano. Il lavoro vero lo fa
``bookwriter.layout``: da qui si esce con uno scheletro di segmenti (titoli, paragrafi,
interruzioni di pagina, corsivi) e con il testo che ne deriva. Chi vuole solo il testo continua a
leggere ``raw_text`` come prima; chi deve riconsegnare un libro impaginato usa ``layout``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bookwriter.layout import DocumentLayout, extract_layout
from bookwriter.layout.extract import ExtractError

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".text", ".pdf", ".docx", ".epub"}


@dataclass
class LoadedDoc:
    path: Path
    format: str  # estensione senza punto (txt, md, pdf, docx, epub)
    raw_text: str  # testo normalizzato
    title: str | None = None
    layout: DocumentLayout | None = None  # struttura tipografica del documento


class LoaderError(RuntimeError):
    pass


def load_document(path: str | Path) -> LoadedDoc:
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise LoaderError(f"Formato non supportato: {path.suffix.lower()} ({path.name})")
    try:
        layout = extract_layout(path)
    except ExtractError as exc:
        raise LoaderError(str(exc)) from exc
    return LoadedDoc(
        path=path,
        format=path.suffix.lower().lstrip("."),
        raw_text=layout.to_text(),
        title=path.stem,
        layout=layout,
    )
