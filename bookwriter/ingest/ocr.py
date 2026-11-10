"""Lettura dei documenti scansionati: foto di appunti, scansioni, PDF fatti di sole immagini.

Gli appunti di un autore non stanno solo in file di testo: c'è il quaderno fotografato col
telefono, la pagina scansionata, il PDF uscito dallo scanner che sembra un libro ma è un disegno.
Senza OCR quel materiale entra vuoto — e un documento vuoto, importato, è peggio di un errore:
risulta presente, il suo sha256 lo fa saltare alla prossima importazione, e nessuno se ne accorge.

Il motore è RapidOCR (pacchetto ``rapidocr``, extra ``ingest``): gira su ONNX Runtime, solo
pip, senza Tesseract né servizi esterni, e porta con sé i modelli PP-OCR, quindi funziona offline
dal primo avvio. I modelli inclusi riconoscono gli accenti italiani («città», «perché»), che il
vecchio modello cinese-inglese perdeva: è la ragione della scelta.

L'OCR restituisce righe sparse con la loro posizione; il lavoro di questo modulo è ricomporle in
capoversi (``righe_a_segmenti``), con regole dichiarate e prudenti come quelle del PDF in
``bookwriter.layout.extract``: l'interlinea che si apre, la riga corta che chiude una frase, il
rientro di prima riga. È una funzione pura, provata senza motore.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from bookwriter.ingest.errors import LoaderError, dipendenza_mancante
from bookwriter.layout.model import HEADING, PAGEBREAK, PARAGRAPH, Segment

#: Estensioni delle immagini lette con l'OCR.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}

#: Risoluzione a cui si disegna una pagina di PDF scansionato prima dell'OCR. A 200 dpi un A4 è
#: circa 1650×2340 pixel: il corpo 10-12 del testo corrente è ben leggibile, e il motore non
#: deve ridurre di molto l'immagine (lavora fino a 2000 pixel sul lato lungo).
PDF_DPI = 200

#: Una pagina di PDF con meno caratteri di così nello strato di testo, ma con immagini, è una
#: scansione: qualche carattere sparso (un numero di pagina timbrato) non è un testo.
MIN_CARATTERI_STRATO = 20

#: Confidenza minima di una riga riconosciuta: sotto, è quasi sempre una macchia o un disegno.
SOGLIA_CONFIDENZA = 0.5

Progress = Callable[[int, int, str], None]

_motore = None
_lock = threading.Lock()


@dataclass
class RigaOCR:
    """Un pezzo di testo riconosciuto con il suo riquadro (pixel, origine in alto a sinistra)."""

    x0: float
    y0: float
    x1: float
    y1: float
    testo: str
    confidenza: float = 1.0

    @property
    def altezza(self) -> float:
        return max(1.0, self.y1 - self.y0)

    @property
    def centro_y(self) -> float:
        return (self.y0 + self.y1) / 2


# --------------------------------------------------------------------------- #
#  il motore
# --------------------------------------------------------------------------- #
def motore():
    """Il motore OCR, caricato una volta sola (il caricamento costa circa un secondo)."""
    global _motore
    with _lock:
        if _motore is None:
            try:
                from rapidocr import RapidOCR
            except ModuleNotFoundError as exc:
                raise dipendenza_mancante(
                    "immagini e PDF scansionati (OCR)", "rapidocr", "ingest") from exc
            try:
                _motore = RapidOCR(params={"Global.log_level": "warning"})
            except ModuleNotFoundError as exc:  # onnxruntime è un requisito a parte
                raise dipendenza_mancante(
                    "immagini e PDF scansionati (OCR)", "onnxruntime", "ingest") from exc
        return _motore


def riconosci(immagine) -> list[RigaOCR]:
    """Le righe di testo di un'immagine (``PIL.Image``), con riquadro e confidenza."""
    eng = motore()
    with _lock:
        risultato = eng(immagine.convert("RGB"))
    if risultato is None or risultato.boxes is None or not risultato.txts:
        return []
    righe: list[RigaOCR] = []
    for box, testo, conf in zip(risultato.boxes, risultato.txts, risultato.scores):
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        righe.append(RigaOCR(min(xs), min(ys), max(xs), max(ys), str(testo), float(conf)))
    return righe


# --------------------------------------------------------------------------- #
#  dalle righe ai capoversi
# --------------------------------------------------------------------------- #
_FINE_FRASE = (".", "!", "?", ":", "…", "»", "\"", "”", ")")


def _unisci_sulla_stessa_riga(righe: list[RigaOCR]) -> list[RigaOCR]:
    """Il motore spezza a volte una riga in più riquadri (uno spazio largo, una tabulazione):
    quelli che stanno alla stessa altezza tornano una riga sola, letti da sinistra a destra."""
    if not righe:
        return []
    h = median(r.altezza for r in righe)
    gruppi: list[list[RigaOCR]] = []
    for r in sorted(righe, key=lambda r: r.centro_y):
        if gruppi and abs(r.centro_y - median(x.centro_y for x in gruppi[-1])) < 0.5 * h:
            gruppi[-1].append(r)
        else:
            gruppi.append([r])
    unite: list[RigaOCR] = []
    for gruppo in gruppi:
        gruppo.sort(key=lambda r: r.x0)
        unite.append(RigaOCR(
            x0=min(r.x0 for r in gruppo), y0=min(r.y0 for r in gruppo),
            x1=max(r.x1 for r in gruppo), y1=max(r.y1 for r in gruppo),
            testo=" ".join(r.testo.strip() for r in gruppo),
            confidenza=min(r.confidenza for r in gruppo),
        ))
    return unite


def _giunta(coda: str, testa: str) -> str:
    """Unisce due righe dello stesso capoverso; la sillabazione tipografica torna una parola."""
    if coda.endswith("-") and len(coda) > 1 and coda[-2].islower() and testa[:1].islower():
        return coda[:-1] + testa
    return f"{coda} {testa}"


def righe_a_segmenti(righe: list[RigaOCR], *, soglia: float = SOGLIA_CONFIDENZA) -> list[Segment]:
    """Righe riconosciute → capoversi (e qualche titolo), nell'ordine di lettura.

    Le regole, una per una:

    - una riga sotto la soglia di confidenza si scarta (macchie, disegni, bordi della pagina);
    - un capoverso si chiude quando l'interlinea si apre (più di tre quarti dell'altezza di una
      riga), quando la riga precedente è corta e finisce come finisce una frase (l'ultima riga di
      un capoverso non arriva al margine), o quando la riga rientra rispetto al margine sinistro
      (il rientro di prima riga);
    - una riga isolata, breve e scritta molto più grande delle altre (una volta e mezza) è un
      titolo. Il testo stesso potrà poi essere riconosciuto come titolo anche dalla sua forma
      («CAPITOLO 3»), a valle, dal parser strutturale.

    Le pagine a due colonne non si ricompongono: si leggono riga per riga da sinistra a destra.
    """
    buone = [r for r in righe if r.testo.strip() and r.confidenza >= soglia]
    linee = _unisci_sulla_stessa_riga(buone)
    if not linee:
        return []
    h = median(r.altezza for r in linee)
    margine = min(r.x0 for r in linee)
    larghezza = max(r.x1 for r in linee) - margine or 1.0

    segmenti: list[Segment] = []
    corrente: list[RigaOCR] = []

    def chiudi() -> None:
        if not corrente:
            return
        testo = corrente[0].testo.strip()
        for r in corrente[1:]:
            testo = _giunta(testo, r.testo.strip())
        isolata = len(corrente) == 1
        grande = corrente[0].altezza >= 1.5 * h
        if isolata and grande and len(testo) <= 80:
            segmenti.append(Segment(kind=HEADING, text=testo, level=2))
        else:
            segmenti.append(Segment(kind=PARAGRAPH, text=testo))
        corrente.clear()

    for riga in linee:
        if corrente:
            prec = corrente[-1]
            spazio = riga.y0 - prec.y1
            corta = (prec.x1 - margine) < 0.7 * larghezza
            finita = prec.testo.rstrip().endswith(_FINE_FRASE)
            rientro = (riga.x0 - margine) > 1.2 * h and (prec.x0 - margine) <= 1.2 * h
            cambio_corpo = max(riga.altezza, prec.altezza) >= 1.5 * min(riga.altezza, prec.altezza)
            if spazio > 0.75 * h or (corta and finita) or rientro or cambio_corpo:
                chiudi()
        corrente.append(riga)
    chiudi()
    return segmenti


# --------------------------------------------------------------------------- #
#  immagini
# --------------------------------------------------------------------------- #
def _immagine_rgb(frame):
    """Il fotogramma raddrizzato secondo l'EXIF (le foto del telefono sono spesso ruotate)."""
    from PIL import ImageOps

    return ImageOps.exif_transpose(frame).convert("RGB")


def segmenti_da_immagine(path: Path, *, progress: Progress | None = None) -> list[Segment]:
    """OCR di un file immagine. Un TIFF di più pagine dà più pagine, separate da un salto."""
    try:
        from PIL import Image, ImageSequence, UnidentifiedImageError
    except ModuleNotFoundError as exc:  # pragma: no cover - pillow è una dipendenza di base
        raise dipendenza_mancante("immagini", "pillow", "ingest") from exc
    motore()  # prima di aprire il file: se manca il motore lo si dice subito
    try:
        with Image.open(path) as img:
            fotogrammi = [_immagine_rgb(f.copy()) for f in ImageSequence.Iterator(img)]
    except (UnidentifiedImageError, OSError) as exc:
        raise LoaderError(f"Immagine non leggibile: {path.name} ({exc})") from exc
    segmenti: list[Segment] = []
    for n, fotogramma in enumerate(fotogrammi):
        if progress:
            progress(n, len(fotogrammi), f"OCR pagina {n + 1} di {len(fotogrammi)}")
        if n:
            segmenti.append(Segment(kind=PAGEBREAK))
        segmenti.extend(righe_a_segmenti(riconosci(fotogramma)))
    if progress:
        progress(len(fotogrammi), len(fotogrammi), "OCR finito")
    return segmenti


# --------------------------------------------------------------------------- #
#  PDF scansionati
# --------------------------------------------------------------------------- #
def pagine_scansionate(path: Path) -> list[int]:
    """Indici delle pagine senza strato di testo ma con immagini: quelle da leggere con l'OCR.

    Una pagina senza testo e senza immagini è bianca davvero e resta tale.
    """
    import fitz  # PyMuPDF, dipendenza di base

    fuori: list[int] = []
    with fitz.open(path) as doc:
        for numero, pagina in enumerate(doc):
            if not pagina.get_images(full=False):
                continue  # senza immagini non c'è niente da leggere (e il controllo costa poco)
            if len(pagina.get_text("text").strip()) < MIN_CARATTERI_STRATO:
                fuori.append(numero)
    return fuori


def segmenti_da_pagine_pdf(path: Path, pagine: list[int], *,
                           progress: Progress | None = None) -> dict[int, list[Segment]]:
    """OCR delle pagine indicate di un PDF: per ciascuna, i suoi segmenti."""
    import fitz
    from PIL import Image

    motore()
    fuori: dict[int, list[Segment]] = {}
    with fitz.open(path) as doc:
        for k, numero in enumerate(pagine):
            if progress:
                progress(k, len(pagine), f"OCR pagina {numero + 1} ({k + 1} di {len(pagine)})")
            pix = doc[numero].get_pixmap(dpi=PDF_DPI, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            fuori[numero] = righe_a_segmenti(riconosci(img))
    if progress and pagine:
        progress(len(pagine), len(pagine), "OCR finito")
    return fuori
