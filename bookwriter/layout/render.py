"""Dallo scheletro al file finito: Markdown, DOCX, PDF.

La regola è una sola: **quel che non è testo non si tocca**. Titoli, livelli, interruzioni di
pagina, pagine bianche, corsivi e grassetti arrivano fin qui come li ha scritti l'autore e
vengono riscritti nel formato scelto.

Quando il libro di partenza era un DOCX e si chiede un DOCX, si fa meglio ancora: si riapre il
file originale e si sostituisce il testo **dentro** i suoi paragrafi. Font, dimensioni, margini,
intestazioni, immagini e tabelle restano quelli del libro, perché non li ha mai lasciati nessuno.
"""

from __future__ import annotations

import html as _html
from pathlib import Path

from bookwriter.layout import fonts, inline
from bookwriter.layout.model import (
    BLANK,
    HEADING,
    OPAQUE,
    PAGEBREAK,
    PARAGRAPH,
    DocumentLayout,
)

SUPPORTED_OUTPUTS = ("docx", "pdf", "md", "epub")


def layout_to_markdown(layout: DocumentLayout) -> str:
    """Markdown: i titoli con i loro cancelletti, le interruzioni di pagina come regola."""
    righe: list[str] = []
    for seg in layout.segments:
        if seg.kind == HEADING:
            righe += ["#" * max(1, min(seg.level or 2, 6)) + f" {seg.text}", ""]
        elif seg.kind == PAGEBREAK:
            righe += ["---", ""]
        elif seg.kind == BLANK:
            righe.append("")
        elif seg.text.strip():
            righe += [seg.text, ""]
    return "\n".join(righe).rstrip() + "\n"


def write_layout(layout: DocumentLayout, out_path: Path, fmt: str, **kwargs) -> Path:
    """Scrive il libro nel formato richiesto conservandone la struttura.

    L'EPUB fa eccezione, e non per pigrizia: un libro elettronico non ha pagine da conservare —
    il carattere e la misura li sceglie chi legge. Lì la struttura si consegna al compositore,
    che ne fa un volume rifluibile con indice e capitoli al posto giusto.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "md":
        out_path.write_text(layout_to_markdown(layout), encoding="utf-8")
    elif fmt == "docx":
        _write_docx(layout, out_path)
    elif fmt == "pdf":
        _write_pdf(layout, out_path)
    elif fmt == "epub":
        from bookwriter.forge.compose import compose_markdown

        compose_markdown(layout_to_markdown(layout), out_path, "epub",
                         title=layout.title or "", **kwargs)
    else:
        raise ValueError(f"Formato non supportato: {fmt} (usa {'|'.join(SUPPORTED_OUTPUTS)})")
    return out_path


# --------------------------------------------------------------------------- #
#  DOCX
# --------------------------------------------------------------------------- #
def _write_docx(layout: DocumentLayout, out_path: Path) -> None:
    origine = Path(layout.source_path) if layout.source_path else None
    if layout.source_format == "docx" and origine and origine.exists():
        _docx_in_place(layout, origine, out_path)
    else:
        _docx_from_scratch(layout, out_path)


def _imposta_carattere(run, nome: str) -> None:
    """Assegna il carattere al run, anche per le scritture asiatiche.

    Word tiene tre nomi distinti per lo stesso run — occidentale, asiatico e complesso — e usa
    quello che corrisponde alla scrittura dei caratteri. Impostarne uno solo lascerebbe i segni
    cinesi o giapponesi al carattere predefinito del documento.
    """
    from docx.oxml.ns import qn

    run.font.name = nome
    proprieta = run._element.get_or_add_rPr()
    caratteri = proprieta.find(qn("w:rFonts"))
    if caratteri is None:
        caratteri = proprieta.makeelement(qn("w:rFonts"), {})
        proprieta.append(caratteri)
    for attributo in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        caratteri.set(qn(attributo), nome)


def _formato(run) -> dict:
    """Il vestito di un run: quanto basta a rifarne uno identico attorno a un altro testo."""
    return {
        "name": run.font.name,
        "size": run.font.size,
        "color": run.font.color.rgb if run.font.color and run.font.color.type is not None else None,
        "all_caps": run.font.all_caps,
        "small_caps": run.font.small_caps,
        "underline": run.underline,
        "bold": run.bold,
        "italic": run.italic,
    }


def _set_paragraph_markup(paragraph, markup: str) -> None:
    """Sostituisce il testo del paragrafo ricostruendo i run dal markup.

    Il testo tradotto non ha la lunghezza dell'originale: i run vanno rifatti, non modificati.
    Per non perdere per strada il carattere di ciascun pezzo si tiene un modello **per ogni
    combinazione** di grassetto e corsivo trovata nel paragrafo di partenza: il pezzo in corsivo
    ritrova così anche il colore e il corpo che aveva il corsivo, non quelli del testo normale.
    Se una combinazione non c'era, si eredita dal primo run, che è pur sempre il carattere del
    paragrafo.
    """
    modelli: dict[tuple[bool, bool], dict] = {}
    for run in paragraph.runs:
        modelli.setdefault((bool(run.bold), bool(run.italic)), _formato(run))
    predefinito = _formato(paragraph.runs[0]) if paragraph.runs else None

    for run in list(paragraph.runs):
        run._element.getparent().remove(run._element)
    for testo, grassetto, corsivo in inline.parse(markup) or [(markup, False, False)]:
        run = paragraph.add_run(testo)
        modello = modelli.get((grassetto, corsivo), predefinito)
        if modello is not None:
            run.font.name = modello["name"]
            run.font.size = modello["size"]
            if modello["color"] is not None:
                run.font.color.rgb = modello["color"]
            run.font.all_caps = modello["all_caps"]
            run.font.small_caps = modello["small_caps"]
            run.underline = modello["underline"]
            run.bold = modello["bold"]
            run.italic = modello["italic"]
        # Dopo il modello, non prima: se il testo porta segni che il carattere del libro non ha
        # — geroglifici, cinese, devanagari — il carattere adatto deve vincere sull'eredità,
        # altrimenti si stampano quadratini col corpo e il colore giusti.
        adatto = fonts.docx_font_for(testo)
        if adatto:
            _imposta_carattere(run, adatto)
        if grassetto:
            run.bold = True
        if corsivo:
            run.italic = True


def _docx_in_place(layout: DocumentLayout, origine: Path, out_path: Path) -> None:
    """Riscrive il testo dentro il DOCX originale, lasciando intatto tutto il resto."""
    import copy

    import docx

    from bookwriter.layout.extract import iter_docx_paragraphs

    document = docx.Document(str(origine))
    testi: dict[str, list[str]] = {}
    for seg in layout.segments:
        if seg.ref and seg.kind in (HEADING, PARAGRAPH):
            testi.setdefault(seg.ref, []).append(seg.text)
    vivi = {s.ref for s in layout.segments if s.ref}
    for ref, paragrafo in list(iter_docx_paragraphs(document)):
        if ref in testi:
            primo, *seguenti = testi[ref]
            _set_paragraph_markup(paragrafo, primo)
            # Il modello ha diviso il paragrafo: i capoversi in più nascono come copie di
            # questo, così ereditano stile, rientri e spaziatura invece di apparire nudi.
            ultimo = paragrafo._element
            for testo in seguenti:
                clone = copy.deepcopy(paragrafo._element)
                ultimo.addnext(clone)
                ultimo = clone
                _set_paragraph_markup(type(paragrafo)(clone, paragrafo._parent), testo)
        elif ref not in vivi and paragrafo.text.strip():
            # Il paragrafo non esiste più nel risultato (l'elaborazione l'ha fuso con un altro):
            # va tolto, altrimenti nel file resterebbe il testo di partenza.
            paragrafo._element.getparent().remove(paragrafo._element)
    document.save(str(out_path))


def _docx_from_scratch(layout: DocumentLayout, out_path: Path) -> None:
    """Costruisce un DOCX nuovo dallo scheletro (origine PDF, EPUB o testo semplice)."""
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    allineamenti = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    document = docx.Document()
    for seg in layout.segments:
        if seg.kind == PAGEBREAK:
            document.add_page_break()
            continue
        if seg.kind == BLANK:
            document.add_paragraph("")
            continue
        if seg.kind == HEADING:
            paragrafo = document.add_heading("", level=max(0, min(seg.level or 2, 9)))
        else:
            paragrafo = document.add_paragraph("")
        _set_paragraph_markup(paragrafo, seg.text)
        if not paragrafo.runs and seg.text:
            _set_paragraph_markup(paragrafo, seg.text)
        if seg.align in allineamenti:
            paragrafo.alignment = allineamenti[seg.align]
    document.save(str(out_path))


# --------------------------------------------------------------------------- #
#  PDF
# --------------------------------------------------------------------------- #
def _write_pdf(layout: DocumentLayout, out_path: Path) -> None:
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

    base = getSampleStyleSheet()
    titolo = ParagraphStyle("BookTitle", parent=base["Title"], fontSize=24, spaceAfter=20)
    livelli = {
        1: titolo,
        2: ParagraphStyle("H2", parent=base["Heading1"], fontSize=16, spaceBefore=18, spaceAfter=10),
        3: ParagraphStyle("H3", parent=base["Heading2"], fontSize=13, spaceBefore=12, spaceAfter=8),
    }
    minore = ParagraphStyle("H4", parent=base["Heading3"], fontSize=11.5, spaceBefore=10, spaceAfter=6)
    corpo = ParagraphStyle("Body", parent=base["BodyText"], fontSize=11, leading=16, spaceAfter=8,
                           alignment=TA_JUSTIFY)
    allineamenti = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT, "justify": TA_JUSTIFY}

    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm,
        leftMargin=2.2 * cm, rightMargin=2.2 * cm,
        title=layout.title or "", author="",
    )
    flusso: list = []
    for seg in layout.segments:
        if seg.kind == PAGEBREAK:
            flusso.append(PageBreak())
        elif seg.kind == BLANK:
            flusso.append(Spacer(1, 14))
        elif seg.kind == HEADING:
            flusso.append(Paragraph(fonts.with_fonts(inline.to_html(seg.text)),
                                    livelli.get(seg.level or 2, minore)))
        elif seg.kind == OPAQUE:
            flusso.append(Paragraph(fonts.with_fonts(_html.escape(seg.text)), corpo))
        elif seg.text.strip():
            stile = corpo
            if seg.align in allineamenti and seg.align != "justify":
                stile = ParagraphStyle(f"Body{seg.align}", parent=corpo, alignment=allineamenti[seg.align])
            flusso.append(Paragraph(fonts.with_fonts(inline.to_html(seg.text)), stile))
    doc.build(flusso or [Spacer(1, 1)])
