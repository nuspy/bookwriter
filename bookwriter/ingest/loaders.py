"""Caricamento del documento: il testo **e** la struttura che lo tiene insieme.

txt/md sono nativi (nessuna dipendenza). pdf/docx/epub usano dipendenze opzionali (extra
``ingest``), importate in modo lazy con messaggio d'errore utile se mancano. Il lavoro vero lo fa
``bookwriter.layout``: da qui si esce con uno scheletro di segmenti (titoli, paragrafi,
interruzioni di pagina, corsivi) e con il testo che ne deriva. Chi vuole solo il testo continua a
leggere ``raw_text`` come prima; chi deve riconsegnare un libro impaginato usa ``layout``.

Un libro scritto dagli appunti parte da materiale che non è mai solo testo: la foto del
quaderno, il PDF uscito dallo scanner, il memo vocale, la mail, la pagina salvata dal browser,
il foglio di calcolo con la cronologia. Ciascuno ha il suo lettore in un modulo a parte
(``ocr``, ``audio``, ``mail``, ``web``, ``sheets``) e tutti finiscono nello stesso
``LoadedDoc``: uno scheletro di segmenti e il testo che ne deriva, così a valle (dedup per
sha256, parser strutturale, chunking) nulla cambia. Le dipendenze di quei lettori sono opzionali
e si importano solo quando servono; se mancano, l'errore dice quale extra installare.

Il lavoro lungo — l'OCR pagina per pagina, la trascrizione a pezzi — riferisce l'avanzamento a
``progress(fatto, totale, nota)``. Quel che si è letto solo in parte non fallisce in silenzio:
finisce in ``LoadedDoc.avvisi``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from bookwriter.ingest.audio import AUDIO_EXTENSIONS
from bookwriter.ingest.errors import LoaderError
from bookwriter.ingest.mail import MAIL_EXTENSIONS
from bookwriter.ingest.ocr import IMAGE_EXTENSIONS
from bookwriter.ingest.sheets import SHEET_EXTENSIONS
from bookwriter.ingest.web import WEB_EXTENSIONS, ScaricaPagina
from bookwriter.layout import DocumentLayout, extract_layout
from bookwriter.layout.extract import ExtractError, _tidy
from bookwriter.layout.model import PAGEBREAK, Segment

__all__ = [
    "FORMAT_GROUPS",
    "SUPPORTED_EXTENSIONS",
    "LoadedDoc",
    "LoaderError",
    "load_document",
]

#: Testi e documenti impaginati: li legge ``bookwriter.layout``.
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}
DOCUMENT_EXTENSIONS = TEXT_EXTENSIONS | {".pdf", ".docx", ".epub"}

#: Le famiglie di formati, nell'ordine in cui offrirle in un dialogo di scelta dei file.
#: Le chiavi sono identificativi stabili; le etichette da mostrare le sceglie l'interfaccia.
FORMAT_GROUPS: dict[str, tuple[str, ...]] = {
    "documenti": tuple(sorted(DOCUMENT_EXTENSIONS)),
    "scansioni": tuple(sorted(IMAGE_EXTENSIONS)),  # più i PDF scansionati, già fra i documenti
    "audio": tuple(sorted(AUDIO_EXTENSIONS)),
    "posta": tuple(sorted(MAIL_EXTENSIONS)),
    "web": tuple(sorted(WEB_EXTENSIONS)),
    "fogli": tuple(sorted(SHEET_EXTENSIONS)),
}

SUPPORTED_EXTENSIONS = set().union(*map(set, FORMAT_GROUPS.values()))

Progress = Callable[[int, int, str], None]


@dataclass
class LoadedDoc:
    path: Path
    format: str  # estensione senza punto (txt, md, pdf, docx, epub, png, mp3, eml, html, xlsx…)
    raw_text: str  # testo normalizzato
    title: str | None = None
    layout: DocumentLayout | None = None  # struttura tipografica del documento
    #: quel che non si è potuto leggere del tutto (pagine senza OCR, pagina non scaricata…)
    avvisi: list[str] = field(default_factory=list)


def load_document(
    path: str | Path,
    *,
    progress: Progress | None = None,
    dispositivo_audio: str | None = None,
    scarica_pagina: ScaricaPagina | None = None,
) -> LoadedDoc:
    """Legge il documento, qualunque sia il formato supportato.

    ``progress(fatto, totale, nota)`` riceve l'avanzamento dei lavori lunghi (OCR, trascrizione,
    caselle di posta). ``dispositivo_audio`` sceglie dove gira la trascrizione («cpu», «cuda»;
    se non lo si dice, la CPU). ``scarica_pagina(url) -> html`` serve ai collegamenti .url e
    .webloc: senza, la pagina non si scarica e del collegamento restano titolo e indirizzo.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise LoaderError(f"Formato non supportato: {ext} ({path.name})")
    if ext == ".pdf":
        return _pdf(path, progress=progress)
    if ext in DOCUMENT_EXTENSIONS:
        return _finito(path, _estrai(path))

    avvisi: list[str] = []
    titolo: str | None = None
    if ext in IMAGE_EXTENSIONS:
        from bookwriter.ingest import ocr

        segmenti = ocr.segmenti_da_immagine(path, progress=progress)
        vuoto = "nessun testo riconosciuto nell'immagine"
    elif ext in AUDIO_EXTENSIONS:
        from bookwriter.ingest import audio

        segmenti = audio.trascrivi(path, dispositivo=dispositivo_audio, progress=progress)
        vuoto = "nessuna parola riconosciuta nella registrazione"
    elif ext in MAIL_EXTENSIONS:
        from bookwriter.ingest import mail

        if ext == ".mbox":
            titolo, segmenti, avvisi = mail.leggi_mbox(path, progress=progress)
        elif ext == ".msg":
            titolo, segmenti = mail.leggi_msg(path)
        else:
            titolo, segmenti = mail.leggi_eml(path)
        vuoto = "nessun messaggio leggibile"
    elif ext in WEB_EXTENSIONS:
        from bookwriter.ingest import web

        if ext in (".url", ".webloc"):
            titolo, segmenti, avvisi = web.leggi_collegamento(path, scarica_pagina=scarica_pagina)
        elif ext in (".mhtml", ".mht"):
            titolo, segmenti = web.leggi_mhtml(path)
        else:
            titolo, segmenti = web.leggi_html(path)
        vuoto = "nessun testo nella pagina"
    else:  # fogli di calcolo
        from bookwriter.ingest import sheets

        segmenti = sheets.leggi_foglio(path, progress=progress)
        vuoto = "il foglio è vuoto"
    layout = DocumentLayout(segments=segmenti, title=titolo or None)
    doc = _finito(path, layout, avvisi=avvisi)
    if not doc.raw_text.strip():
        raise LoaderError(f"{path.name}: {vuoto}.")
    return doc


def _estrai(path: Path) -> DocumentLayout:
    try:
        return extract_layout(path)
    except ExtractError as exc:
        raise LoaderError(str(exc)) from exc


def _finito(path: Path, layout: DocumentLayout, *, avvisi: list[str] | None = None) -> LoadedDoc:
    """Completa lo scheletro (formato, origine, testi normalizzati) e ne ricava il documento."""
    layout.source_format = path.suffix.lower().lstrip(".")
    layout.source_path = str(path)
    layout.title = layout.title or path.stem
    layout = _tidy(layout)
    return LoadedDoc(
        path=path,
        format=path.suffix.lower().lstrip("."),
        raw_text=layout.to_text(),
        # Come per i formati di sempre il titolo del documento è il nome del file; quello
        # interno (oggetto della mail, titolo della pagina) sta nello scheletro.
        title=path.stem,
        layout=layout,
        avvisi=list(avvisi or []),
    )


# --------------------------------------------------------------------------- #
#  PDF, con le pagine scansionate lette dall'OCR
# --------------------------------------------------------------------------- #
def _pagine_dello_scheletro(layout: DocumentLayout, n_pagine: int) -> list[list[Segment]] | None:
    """Lo scheletro di un PDF diviso per pagina: ``bookwriter.layout`` mette un solo salto fra
    una pagina e l'altra, e in coda toglie i salti delle pagine bianche finali. ``None`` se la
    divisione non torna (più gruppi che pagine): allora non si sa dove stia cosa."""
    pagine: list[list[Segment]] = [[]]
    for seg in layout.segments:
        if seg.kind == PAGEBREAK:
            pagine.append([])
        else:
            pagine[-1].append(seg)
    if len(pagine) > n_pagine:
        return None
    return pagine + [[] for _ in range(n_pagine - len(pagine))]


def _pdf(path: Path, *, progress: Progress | None = None) -> LoadedDoc:
    """Il PDF come lo legge ``bookwriter.layout``; le pagine senza strato di testo (scansioni)
    si leggono con l'OCR e prendono il loro posto nello scheletro.

    Se il motore OCR manca: un PDF tutto scansionato è un errore (sarebbe un documento vuoto),
    uno con qualche pagina scansionata si importa col testo che ha, e un avviso dice quali
    pagine mancano.
    """
    import fitz

    from bookwriter.ingest import ocr

    layout = _estrai(path)
    scansionate = ocr.pagine_scansionate(path)
    if not scansionate:
        return _finito(path, layout)
    with fitz.open(path) as pdf:
        n_pagine = pdf.page_count
    elenco = ", ".join(str(n + 1) for n in scansionate[:20]) + ("…" if len(scansionate) > 20 else "")
    try:
        letti = ocr.segmenti_da_pagine_pdf(path, scansionate, progress=progress)
    except LoaderError as exc:
        if len(scansionate) == n_pagine:
            raise LoaderError(f"{path.name} è un PDF scansionato, senza testo: {exc}") from exc
        return _finito(path, layout, avvisi=[
            f"{path.name}: le pagine {elenco} sono scansioni e non sono state lette ({exc})"])
    avvisi = []
    vuote = [n + 1 for n in scansionate if not letti.get(n)]
    if vuote:
        avvisi.append(f"{path.name}: nessun testo riconosciuto nelle pagine "
                      + ", ".join(map(str, vuote[:20])) + ("…" if len(vuote) > 20 else ""))
    pagine = _pagine_dello_scheletro(layout, n_pagine)
    if pagine is None:  # difensivo: le pagine lette dall'OCR vanno in fondo, in ordine
        segmenti = list(layout.segments)
        for n in scansionate:
            segmenti += [Segment(kind=PAGEBREAK), *letti.get(n, [])]
        avvisi.append(f"{path.name}: le pagine scansionate sono state aggiunte in fondo")
    else:
        for n, segmenti_ocr in letti.items():
            pagine[n] = segmenti_ocr
        segmenti = []
        for n, pagina in enumerate(pagine):
            if n:
                segmenti.append(Segment(kind=PAGEBREAK))
            segmenti.extend(pagina)
    nuovo = DocumentLayout(segments=segmenti, title=layout.title)
    doc = _finito(path, nuovo, avvisi=avvisi)
    if not doc.raw_text.strip() and len(scansionate) == n_pagine:
        raise LoaderError(f"{path.name}: nessun testo riconosciuto nelle pagine scansionate.")
    return doc
