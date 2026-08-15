"""Il documento come sequenza di segmenti tipografici.

Un segmento è un pezzo del libro con un ruolo: un titolo, un paragrafo, un'interruzione di pagina,
una pagina bianca, una tabella. Solo i segmenti con testo (titoli e paragrafi) vengono dati al
modello; tutti gli altri attraversano l'elaborazione intatti e ricompaiono al loro posto.

Il collegamento con il resto del programma è l'**offset**: ``to_text()`` produce il testo
normalizzato del libro concatenando i segmenti, e ``spans()`` dice dove ciascuno è finito. I chunk
salvati nel database portano già ``span_start``/``span_end`` sullo stesso testo: da lì si risale in
modo esatto ai segmenti che un blocco elaborato copre, senza doverli riconoscere a occhio.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# Ruoli dei segmenti.
HEADING = "heading"  # titolo (``level`` = profondità, 1 = titolo del libro)
PARAGRAPH = "paragraph"  # testo corrente
PAGEBREAK = "pagebreak"  # interruzione di pagina esplicita
BLANK = "blank"  # spazio voluto: riga vuota di stacco, pagina bianca
OPAQUE = "opaque"  # elemento non testuale conservato com'è (tabella, immagine)

# I due ruoli che contengono testo da elaborare.
TEXT_KINDS = (HEADING, PARAGRAPH)

# Giunzione fra i testi dei segmenti nel testo normalizzato del libro.
_JOIN = "\n\n"

# Cancelletti Markdown all'inizio di un titolo.
_MARKS = re.compile(r"^\s{0,3}#{1,6}\s*")


def unmark(text: str, kind: str) -> str:
    """Toglie i cancelletti dal titolo tornato dall'elaborazione, tenendo il titolo."""
    return _MARKS.sub("", text.strip()).strip() if kind == HEADING else text.strip()


@dataclass
class Segment:
    kind: str = PARAGRAPH
    text: str = ""
    level: int = 0  # solo per i titoli
    style: str | None = None  # nome dello stile nel documento d'origine (DOCX)
    align: str | None = None  # left|center|right|justify
    ref: str | None = None  # identificativo dell'elemento d'origine, per il riversamento in loco

    @property
    def has_text(self) -> bool:
        return self.kind in TEXT_KINDS and bool(self.text.strip())

    @property
    def marked_text(self) -> str:
        """Il testo come compare nel testo del libro: i titoli con i loro cancelletti.

        Serve perché a valle il parser strutturale e il chunker riconoscono i capitoli dal
        Markdown: senza il marcatore, «Introduzione» sarebbe un paragrafo qualsiasi e il libro
        perderebbe la divisione in capitoli appena passata la porta d'ingresso.
        """
        if self.kind == HEADING and self.text.strip():
            return "#" * max(1, min(self.level or 2, 6)) + f" {self.text}"
        return self.text


@dataclass
class DocumentLayout:
    segments: list[Segment] = field(default_factory=list)
    source_format: str = ""
    source_path: str = ""
    title: str | None = None

    # -- testo e posizioni ---------------------------------------------- #
    def to_text(self) -> str:
        """Il testo del libro: i soli segmenti testuali, separati da una riga vuota.

        È il testo su cui lavorano parser strutturale, chunker e memoria: già normalizzato,
        perché ogni segmento lo è stato all'estrazione.
        """
        return _JOIN.join(s.marked_text for s in self.segments if s.has_text)

    def spans(self) -> dict[int, tuple[int, int]]:
        """Per ogni segmento testuale, la sua posizione ``(inizio, fine)`` dentro ``to_text()``."""
        out: dict[int, tuple[int, int]] = {}
        cursor = 0
        for idx, seg in enumerate(self.segments):
            if not seg.has_text:
                continue
            if out:  # dal secondo in poi c'è la giunzione davanti
                cursor += len(_JOIN)
            lunghezza = len(seg.marked_text)
            out[idx] = (cursor, cursor + lunghezza)
            cursor += lunghezza
        return out

    def segments_in_span(self, start: int, end: int) -> list[int]:
        """Indici dei segmenti testuali che si sovrappongono all'intervallo indicato.

        Un blocco elaborato copre uno o più segmenti interi; un paragrafo lunghissimo, spezzato
        dal chunker, può invece essere coperto da più blocchi. Entrambi i casi si leggono qui.
        """
        return [i for i, (a, b) in self.spans().items() if a < end and start < b]

    def replace_texts(self, nuovi: dict[int, str | list[str]]) -> DocumentLayout:
        """Copia del layout con i testi indicati sostituiti; il resto resta identico.

        Un segmento associato a testo vuoto sparisce dal risultato: è il caso del paragrafo che
        l'elaborazione ha fuso con quello precedente. Se invece riceve **più** testi, si sdoppia
        in altrettanti segmenti dello stesso tipo: è il paragrafo lunghissimo che il modello ha
        legittimamente diviso in capoversi.
        """
        fuori: list[Segment] = []
        for idx, seg in enumerate(self.segments):
            if idx not in nuovi:
                fuori.append(Segment(**asdict(seg)))
                continue
            valore = nuovi[idx]
            testi = [valore] if isinstance(valore, str) else list(valore)
            for testo in testi:
                pulito = unmark(testo, seg.kind)
                if pulito:
                    fuori.append(Segment(**{**asdict(seg), "text": pulito}))
        return DocumentLayout(
            segments=fuori,
            source_format=self.source_format,
            source_path=self.source_path,
            title=self.title,
        )

    # -- serializzazione ------------------------------------------------ #
    def to_dict(self) -> dict:
        return {
            "source_format": self.source_format,
            "source_path": self.source_path,
            "title": self.title,
            "segments": [asdict(s) for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict) -> DocumentLayout:
        campi = {f for f in Segment.__dataclass_fields__}
        return cls(
            segments=[
                Segment(**{k: v for k, v in (s or {}).items() if k in campi})
                for s in data.get("segments", [])
            ],
            source_format=data.get("source_format", ""),
            source_path=data.get("source_path", ""),
            title=data.get("title"),
        )


def layout_from_text(text: str, *, source_format: str = "", title: str | None = None) -> DocumentLayout:
    """Scheletro minimo per un testo semplice: titoli Markdown e paragrafi.

    È la lettura di txt/md, ed è anche la rete di sicurezza per i documenti importati prima che
    esistesse il layout: si riparte dal testo e si riconosce quel che si può.
    """
    from bookwriter.text_utils import looks_like_heading, normalize_text

    segments: list[Segment] = []
    for blocco in normalize_text(text).split("\n\n"):
        blocco = blocco.strip()
        if not blocco:
            continue
        prima_riga = blocco.split("\n", 1)[0].lstrip()
        heading, titolo = looks_like_heading(prima_riga) if "\n" not in blocco else (False, None)
        if heading:
            livello = len(prima_riga) - len(prima_riga.lstrip("#"))
            segments.append(
                Segment(kind=HEADING, text=titolo or prima_riga, level=max(1, min(livello or 2, 6)))
            )
        else:
            segments.append(Segment(kind=PARAGRAPH, text=blocco))
    return DocumentLayout(segments=segments, source_format=source_format, title=title)
