"""Dal file d'origine allo scheletro: titoli, paragrafi, stacchi, corsivi.

Ogni formato conserva la struttura a modo suo e va letto con le sue regole. Il DOCX la dichiara
(stili dei paragrafi, run, interruzioni esplicite) e si legge quasi alla lettera. L'EPUB è HTML e
la struttura sta nei tag. Il PDF non ha struttura affatto: è un disegno di caratteri su una pagina,
e va **ricostruita** — è qui che stanno le uniche euristiche del modulo, tenute prudenti e
dichiarate una per una.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median

from bookwriter.layout import inline
from bookwriter.layout.model import (
    BLANK,
    HEADING,
    OPAQUE,
    PAGEBREAK,
    PARAGRAPH,
    DocumentLayout,
    Segment,
    layout_from_text,
)
from bookwriter.text_utils import looks_like_heading, normalize_text


class ExtractError(RuntimeError):
    pass


def _missing_dep(fmt: str, package: str) -> ExtractError:
    return ExtractError(
        f"Per importare file {fmt} serve la dipendenza '{package}'. "
        f"Installa gli extra: pip install -e \".[ingest]\""
    )


def extract_layout(path: str | Path) -> DocumentLayout:
    """Legge il documento e ne restituisce la struttura completa."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".docx":
        layout = _from_docx(path)
    elif ext == ".pdf":
        layout = _from_pdf(path)
    elif ext == ".epub":
        layout = _from_epub(path)
    elif ext in (".txt", ".text", ".md", ".markdown"):
        layout = layout_from_text(path.read_text(encoding="utf-8", errors="replace"))
    else:
        raise ExtractError(f"Formato non supportato: {ext} ({path.name})")
    layout.source_format = ext.lstrip(".")
    layout.source_path = str(path)
    layout.title = layout.title or path.stem
    return _tidy(layout)


def _tidy(layout: DocumentLayout) -> DocumentLayout:
    """Normalizza i testi e riduce gli stacchi consecutivi a uno solo."""
    fuori: list[Segment] = []
    for seg in layout.segments:
        if seg.kind in (HEADING, PARAGRAPH):
            seg.text = normalize_text(seg.text)
            if not seg.text:
                seg.kind, seg.text = BLANK, ""
        if seg.kind == BLANK and fuori and fuori[-1].kind in (BLANK, PAGEBREAK):
            continue
        fuori.append(seg)
    while fuori and fuori[0].kind == BLANK:
        fuori.pop(0)
    while fuori and fuori[-1].kind in (BLANK, PAGEBREAK):
        fuori.pop()
    layout.segments = fuori
    return layout


# --------------------------------------------------------------------------- #
#  DOCX
# --------------------------------------------------------------------------- #
def _heading_level(style_name: str) -> int:
    """Profondità del titolo dal nome dello stile («Heading 2», «Titolo 2», «Title»)."""
    nome = (style_name or "").strip().lower()
    if nome in ("title", "titolo"):
        return 1
    cifre = "".join(c for c in nome if c.isdigit())
    return int(cifre) + 1 if cifre else 2


def _is_heading_style(style_name: str) -> bool:
    nome = (style_name or "").strip().lower()
    return nome.startswith(("heading", "titolo", "intestazione")) or nome in ("title", "subtitle")


def _paragraph_markup(paragraph) -> str:
    """Testo del paragrafo con corsivo e grassetto in notazione Markdown."""
    pezzi = [inline.wrap(run.text, bold=bool(run.bold), italic=bool(run.italic))
             for run in paragraph.runs if run.text]
    return "".join(pezzi) or paragraph.text


def _has_page_break(paragraph) -> bool:
    """Vero se il paragrafo contiene un'interruzione di pagina esplicita."""
    from docx.oxml.ns import qn

    for br in paragraph._p.iter(qn("w:br")):
        if br.get(qn("w:type")) == "page":
            return True
    return False


_ALIGN = {0: "left", 1: "center", 2: "right", 3: "justify"}


def _docx_paragraph_segments(paragraph, ref: str) -> list[Segment]:
    """Uno (o due) segmenti per un paragrafo DOCX: l'eventuale stacco di pagina, poi il testo."""
    fuori: list[Segment] = []
    formato = paragraph.paragraph_format
    if getattr(formato, "page_break_before", False):
        fuori.append(Segment(kind=PAGEBREAK))
    testo = _paragraph_markup(paragraph).strip()
    stile = paragraph.style.name if paragraph.style else None
    allineamento = _ALIGN.get(int(paragraph.alignment)) if paragraph.alignment is not None else None
    if not testo:
        # Paragrafo vuoto: nel DOCX è uno stacco voluto (o una pagina bianca, se porta il salto).
        fuori.append(Segment(kind=PAGEBREAK if _has_page_break(paragraph) else BLANK, ref=ref))
        return fuori
    fuori.append(
        Segment(
            kind=HEADING if _is_heading_style(stile or "") else PARAGRAPH,
            text=testo,
            level=_heading_level(stile or "") if _is_heading_style(stile or "") else 0,
            style=stile,
            align=allineamento,
            ref=ref,
        )
    )
    if _has_page_break(paragraph):
        fuori.append(Segment(kind=PAGEBREAK))
    return fuori


def iter_docx_paragraphs(document):
    """I paragrafi del documento nell'ordine di lettura, celle di tabella comprese.

    Restituisce ``(riferimento, paragrafo)``. Il riferimento è ricalcolato allo stesso modo in
    fase di scrittura: è così che il testo elaborato ritrova il proprio paragrafo nel file
    originale, senza confronti sul contenuto (che la traduzione ha cambiato).
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    body = document.element.body
    n_par = n_tab = 0
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield f"p{n_par}", Paragraph(child, document)
            n_par += 1
        elif child.tag.endswith("}tbl"):
            tabella = Table(child, document)
            for i, riga in enumerate(tabella.rows):
                for j, cella in enumerate(riga.cells):
                    for k, par in enumerate(cella.paragraphs):
                        yield f"t{n_tab}r{i}c{j}p{k}", par
            n_tab += 1


def _from_docx(path: Path) -> DocumentLayout:
    try:
        import docx  # python-docx
    except ModuleNotFoundError as exc:  # pragma: no cover - dipende dall'ambiente
        raise _missing_dep("DOCX", "python-docx") from exc
    document = docx.Document(str(path))
    segmenti: list[Segment] = []
    for ref, paragrafo in iter_docx_paragraphs(document):
        segmenti.extend(_docx_paragraph_segments(paragrafo, ref))
    titolo = next((s.text for s in segmenti if s.kind == HEADING), None)
    return DocumentLayout(segments=segmenti, title=titolo)


# --------------------------------------------------------------------------- #
#  PDF
# --------------------------------------------------------------------------- #
def running_heads(pages: list[str], *, min_share: float = 0.25) -> set[str]:
    """Righe che tornano su un quarto delle pagine: intestazioni, piè di pagina, numeri.

    In un PDF impaginato il titolo del libro sta in cima (o in fondo) a ogni pagina. Estratto
    come testo, finisce **in mezzo alle frasi**: viene tradotto una volta per pagina, spezza il
    discorso e presenta ai modelli una ripetizione che non c'è nel libro. Non è contenuto: è
    impaginazione. Si riconosce dal fatto che ricorre a cadenza di pagina, non dal suo testo.
    """
    if len(pages) < 4:
        return set()
    counts: Counter[str] = Counter()
    for page in pages:
        # Una riga per pagina conta una volta: un ritornello dentro al testo non è un piè di pagina.
        counts.update({line.strip() for line in page.splitlines() if line.strip()})
    soglia = max(3, int(len(pages) * min_share))
    return {
        line
        for line, n in counts.items()
        # Corte come un titolo corrente; le righe di prosa non ricorrono identiche.
        if n >= soglia and len(line) <= 90
    }


@dataclass
class _Riga:
    """Una riga di PDF con quel che serve a capire che ruolo ha nella pagina."""

    text: str  # già in notazione Markdown per corsivo e grassetto
    plain: str  # senza marcatori, per i confronti
    size: float  # corpo del carattere
    bold: bool
    width: float  # larghezza occupata, in punti
    block: int  # blocco di testo cui appartiene secondo il PDF


def _dehyphenate(coda: str, testa: str) -> str | None:
    """Giunge due righe spezzate a metà parola, se la sillabazione è quella tipografica.

    Solo minuscola-trattino-a capo-minuscola: così «all'in-\\nferno» torna una parola sola e
    «Roma-\\nNapoli» resta come l'autore l'ha scritto.
    """
    if coda.endswith("-") and len(coda) > 1 and coda[-2].islower() and testa[:1].islower():
        return coda[:-1] + testa
    return None


def _pdf_page_lines(page) -> list[_Riga]:
    """Righe della pagina con corpo, grassetto, corsivo e larghezza occupata.

    Sono queste misure a distinguere un titolo da un capoverso: nel PDF non c'è nessun tag che
    lo dica, ma un titolo è scritto più grande, e l'ultima riga di un capoverso è più corta delle
    altre perché non arriva al margine. Il testo nudo, da solo, non lo direbbe mai.
    """
    righe: list[_Riga] = []
    dati = page.get_text("dict")
    for n_blocco, blocco in enumerate(dati.get("blocks", [])):
        for riga in blocco.get("lines", []):
            pezzi, nudo, corpo, grassetti, totale = [], [], 0.0, 0, 0
            for span in riga.get("spans", []):
                testo = span.get("text", "")
                if not testo:
                    continue
                flags = int(span.get("flags", 0))
                # Bit di PyMuPDF: 1 = corsivo, 16 = grassetto.
                pezzi.append(inline.wrap(testo, bold=bool(flags & 16), italic=bool(flags & 2)))
                nudo.append(testo)
                corpo = max(corpo, float(span.get("size", 0.0)))
                grassetti += bool(flags & 16)
                totale += 1
            if not nudo or not "".join(nudo).strip():
                continue
            x0, _y0, x1, _y1 = riga.get("bbox", (0, 0, 0, 0))
            righe.append(
                _Riga(
                    text="".join(pezzi).strip(),
                    plain="".join(nudo).strip(),
                    size=corpo,
                    bold=totale > 0 and grassetti == totale,
                    width=float(x1) - float(x0),
                    block=n_blocco,
                )
            )
    return righe


def _from_pdf(path: Path) -> DocumentLayout:
    try:
        import fitz  # PyMuPDF
    except ModuleNotFoundError as exc:  # pragma: no cover - dipende dall'ambiente
        raise _missing_dep("PDF", "pymupdf") from exc
    with fitz.open(path) as doc:
        pagine = [_pdf_page_lines(page) for page in doc]
    return pdf_lines_to_layout(pagine)


def _heading_levels(corpi: list[float]) -> dict[float, int]:
    """Dai corpi di carattere presenti ai livelli di titolo: il più grande è il più alto."""
    distinti = sorted({round(c, 1) for c in corpi}, reverse=True)
    return {corpo: min(livello + 1, 6) for livello, corpo in enumerate(distinti)}


def pdf_lines_to_layout(pagine: list[list[_Riga]]) -> DocumentLayout:
    """Righe misurate → scheletro: titoli per corpo del carattere, capoversi per larghezza."""
    testi_pagina = ["\n".join(r.plain for r in pagina) for pagina in pagine]
    ripetute = running_heads(testi_pagina)
    pulite = [[r for r in pagina if r.plain not in ripetute] for pagina in pagine]

    # Prima il numero di pagina, poi i titoli correnti: tolto il numero, il titolo corrente
    # diventa l'ultima riga, e va riconosciuto in quella posizione.
    pulite = [_senza_numero_di_pagina(pagina) for pagina in pulite]
    ricorrenti = titoli_correnti_di_bordo(pulite)
    pulite = [_senza_titoli_correnti(pagina, ricorrenti) for pagina in pulite]
    tutte = [r for pagina in pulite for r in pagina]
    if not tutte:
        return DocumentLayout(segments=[Segment(kind=BLANK) for _ in pagine][:1])
    corpo_normale = median([r.size for r in tutte]) or 1.0
    # Larghezza della gabbia: le righe piene, non le ultime di capoverso, che sono più corte.
    piene = sorted((r.width for r in tutte), reverse=True)
    gabbia = median(piene[: max(1, len(piene) // 2)]) or 1.0
    grandi = [r.size for r in tutte if r.size > corpo_normale * 1.12]
    livelli = _heading_levels(grandi)

    segmenti: list[Segment] = []
    for numero, righe in enumerate(pulite):
        if numero:
            segmenti.append(Segment(kind=PAGEBREAK))
        if not righe:
            # Pagina bianca: nel libro stampato è una scelta (fine parte, pagina a fronte).
            segmenti.append(Segment(kind=BLANK))
            continue
        segmenti.extend(_righe_a_segmenti(righe, corpo_normale=corpo_normale, gabbia=gabbia, livelli=livelli))
    titolo = next((s.text for s in segmenti if s.kind == HEADING), None)
    return DocumentLayout(segments=segmenti, title=titolo)


def titoli_correnti_di_bordo(pagine: list[list[_Riga]], *, minimo: int = 3) -> Counter[str]:
    """Righe che tornano **in cima o in fondo** a più pagine: i titoli correnti per capitolo.

    ``running_heads`` prende il titolo del libro, che ricorre su tutte le pagine. Non prende i
    titoli correnti che cambiano a ogni capitolo — «L'OFFERTA» sulle pagine del primo, «IL
    PROTOCOLLO» su quelle del quinto — perché nessuno di loro arriva alla soglia sull'intero
    volume. Presi per titoli veri, finirebbero tradotti e stampati in mezzo al racconto.

    Il segnale che li distingue non è il testo ma la **posizione**: nessun capoverso di prosa si
    ripete identico in cima a tre pagine. Contarli qui serve a toglierli solo da lì, lasciando
    intatta l'occorrenza in cui quel titolo è davvero il titolo del capitolo.
    """
    ai_bordi: Counter[str] = Counter()
    for righe in pagine:
        if not righe:
            continue
        ai_bordi.update({righe[0].plain} | {righe[-1].plain})
    return Counter({testo: n for testo, n in ai_bordi.items() if n >= minimo and testo})


def _senza_titoli_correnti(righe: list[_Riga], ricorrenti: Counter[str]) -> list[_Riga]:
    """Toglie la prima e l'ultima riga della pagina se sono titoli correnti."""
    inizio = 1 if righe and righe[0].plain in ricorrenti else 0
    fine = len(righe) - 1 if len(righe) > inizio and righe[-1].plain in ricorrenti else len(righe)
    return righe[inizio:fine]


def _senza_numero_di_pagina(righe: list[_Riga]) -> list[_Riga]:
    """Toglie il numero di pagina in testa o in coda: è impaginazione, non testo.

    Non lo prende ``running_heads``, perché su ogni pagina è diverso. Si riconosce dal fatto che
    è una riga fatta di sole cifre (o di soli numeri romani) al bordo della pagina: dentro un
    libro nessun capoverso è così.
    """
    def numero(riga: _Riga) -> bool:
        testo = riga.plain.strip()
        return bool(testo) and (
            (testo.isdigit() and len(testo) <= 4)
            or (len(testo) <= 7 and set(testo.upper()) <= set("IVXLCM"))
        )

    inizio = 1 if righe and numero(righe[0]) else 0
    fine = len(righe) - 1 if len(righe) > inizio and numero(righe[-1]) else len(righe)
    return righe[inizio:fine]


def _righe_a_segmenti(
    righe: list[_Riga], *, corpo_normale: float, gabbia: float, livelli: dict[float, int]
) -> list[Segment]:
    segmenti: list[Segment] = []
    corrente: list[_Riga] = []
    ultimo_titolo: tuple[int, float, int] | None = None  # (indice riga, corpo, blocco)
    per_blocco = Counter(r.block for r in righe)

    def chiudi() -> None:
        if not corrente:
            return
        testo = corrente[0].text
        for riga in corrente[1:]:
            unito = _dehyphenate(testo, riga.text)
            testo = unito if unito is not None else f"{testo} {riga.text}"
        segmenti.append(Segment(kind=PARAGRAPH, text=testo.strip()))
        corrente.clear()

    for indice, riga in enumerate(righe):
        piu_grande = riga.size > corpo_normale * 1.12
        titolo_dichiarato, ripulito = looks_like_heading(riga.plain)
        # Titolo per misura (corpo maggiore), per forma (tutto maiuscolo, «Capitolo IV») o
        # perché è una riga breve tutta in grassetto che il PDF tiene per conto suo. L'ultimo
        # caso è il più incerto e ha quindi le condizioni più strette: da solo nel proprio
        # blocco, con l'iniziale maiuscola. Senza, ogni termine in neretto a inizio capoverso
        # diventerebbe un capitolo.
        breve_in_grassetto = (
            riga.bold
            and riga.width < 0.6 * gabbia
            and not corrente
            and per_blocco[riga.block] == 1
            and riga.plain[:1].isupper()
        )
        if piu_grande or titolo_dichiarato or breve_in_grassetto:
            chiudi()
            livello = livelli.get(round(riga.size, 1), 2) if piu_grande else 2
            # Nel titolo il grassetto è lo stile del titolo, non un'enfasi sulle parole: non
            # serve portarselo dietro come marcatore.
            testo = riga.plain if riga.bold else riga.text
            if titolo_dichiarato and ripulito and not piu_grande:
                testo = ripulito
            # Un titolo lungo sta su più righe: righe adiacenti scritte con lo stesso corpo
            # sono lo stesso titolo, e spezzarle darebbe due capitoli dove ce n'è uno.
            if ultimo_titolo and ultimo_titolo[0] == indice - 1 and ultimo_titolo[1] == riga.size:
                precedente = segmenti[-1]
                unito = _dehyphenate(precedente.text, testo)
                precedente.text = unito if unito is not None else f"{precedente.text} {testo}"
            else:
                segmenti.append(Segment(kind=HEADING, text=testo, level=livello))
            ultimo_titolo = (indice, riga.size, riga.block)
            continue
        corrente.append(riga)
        prossima = righe[indice + 1] if indice + 1 < len(righe) else None
        fine_blocco = prossima is None or prossima.block != riga.block
        # Ultima riga del capoverso: non arriva al margine destro.
        riga_corta = riga.width < 0.85 * gabbia
        if fine_blocco or riga_corta:
            chiudi()
    chiudi()
    return segmenti


# --------------------------------------------------------------------------- #
#  EPUB
# --------------------------------------------------------------------------- #
def _html_markup(tag) -> str:
    """Contenuto di un tag HTML con corsivo e grassetto tradotti in Markdown."""
    pezzi: list[str] = []
    for figlio in tag.descendants:
        if getattr(figlio, "name", None) is not None:
            continue
        testo = str(figlio)
        if not testo:
            continue
        genitori = {p.name for p in figlio.parents if getattr(p, "name", None)}
        pezzi.append(
            inline.wrap(
                testo,
                bold=bool(genitori & {"b", "strong"}),
                italic=bool(genitori & {"i", "em"}),
            )
        )
    return "".join(pezzi)


def _from_epub(path: Path) -> DocumentLayout:
    try:
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT, epub
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise _missing_dep("EPUB", "ebooklib e beautifulsoup4") from exc
    book = epub.read_epub(str(path))
    segmenti: list[Segment] = []
    for n, item in enumerate(book.get_items_of_type(ITEM_DOCUMENT)):
        if n:
            segmenti.append(Segment(kind=PAGEBREAK))
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "table"]):
            if tag.find_parent(["p", "li", "blockquote", "table"]) is not None:
                continue  # già reso dal blocco che lo contiene
            if tag.name == "table":
                segmenti.append(Segment(kind=OPAQUE, text=tag.get_text(" ", strip=True)))
                continue
            testo = _html_markup(tag).strip()
            if not testo:
                continue
            if tag.name.startswith("h") and tag.name[1:].isdigit():
                segmenti.append(Segment(kind=HEADING, text=testo, level=int(tag.name[1])))
            else:
                segmenti.append(Segment(kind=PARAGRAPH, text=testo, style=tag.name))
    titolo = next((s.text for s in segmenti if s.kind == HEADING), None)
    return DocumentLayout(segments=segmenti, title=titolo)
