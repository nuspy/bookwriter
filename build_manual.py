"""Genera il PDF del manuale a partire da ``docs/MANUAL.md``.

Il generatore incluso nel programma (``exporter.markdown_to_pdf``) impagina libri: conosce titoli e
paragrafi, che è tutto ciò che serve a un libro. Un manuale ha invece tabelle, blocchi di codice,
elenchi puntati e numerati, citazioni e grassetto in linea: questo modulo li rende, sempre con
reportlab e senza dipendenze aggiuntive.

    python build_manual.py [sorgente.md] [destinazione.pdf]
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_ACCENTO = colors.HexColor("#1a4f7a")
_GRIGIO = colors.HexColor("#f2f2f2")
_BORDO = colors.HexColor("#cccccc")


def _stili() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "titolo": ParagraphStyle("Titolo", parent=base["Title"], fontSize=26, leading=32,
                                 textColor=_ACCENTO, spaceAfter=6),
        "sottotitolo": ParagraphStyle("Sottotitolo", parent=base["Normal"], fontSize=12,
                                      textColor=colors.HexColor("#555555"), alignment=1, spaceAfter=28),
        "h1": ParagraphStyle("H1", parent=base["Heading1"], fontSize=19, leading=24,
                             textColor=_ACCENTO, spaceBefore=22, spaceAfter=10),
        "h2": ParagraphStyle("H2", parent=base["Heading2"], fontSize=14, leading=19,
                             textColor=_ACCENTO, spaceBefore=16, spaceAfter=8),
        "h3": ParagraphStyle("H3", parent=base["Heading3"], fontSize=11.5, leading=16,
                             spaceBefore=12, spaceAfter=6),
        "corpo": ParagraphStyle("Corpo", parent=base["BodyText"], fontSize=10, leading=15,
                                alignment=TA_JUSTIFY, spaceAfter=7),
        "codice": ParagraphStyle("Codice", parent=base["Code"], fontName="Courier", fontSize=8.5,
                                 leading=12, textColor=colors.HexColor("#222222"),
                                 leftIndent=0, firstLineIndent=0),
        "cella": ParagraphStyle("Cella", parent=base["BodyText"], fontSize=9, leading=12.5, spaceAfter=0),
        "intestazione": ParagraphStyle("Intestazione", parent=base["BodyText"], fontSize=9,
                                       leading=12.5, spaceAfter=0, textColor=colors.white),
        "citazione": ParagraphStyle("Citazione", parent=base["BodyText"], fontSize=9.5, leading=14,
                                    leftIndent=12, textColor=colors.HexColor("#444444"),
                                    borderPadding=(6, 6, 6, 10), backColor=colors.HexColor("#f7f7f7"),
                                    spaceAfter=8),
    }


_GRASSETTO = re.compile(r"\*\*(.+?)\*\*")
_CORSIVO = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_CODICE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"&lt;(https?://[^&]+)&gt;")


def _inline(testo: str) -> str:
    """Converte il Markdown in linea nei tag accettati da reportlab."""
    testo = html.escape(testo)
    testo = _CODICE.sub(r'<font face="Courier" size="9" color="#7a1a1a">\1</font>', testo)
    testo = _GRASSETTO.sub(r"<b>\1</b>", testo)
    testo = _CORSIVO.sub(r"<i>\1</i>", testo)
    return _LINK.sub(r'<link href="\1" color="#1a4f7a">\1</link>', testo)


def _tabella(righe: list[str], stili: dict, larghezza: float) -> Table:
    """Costruisce una tabella dal blocco Markdown (la riga dei trattini è saltata)."""
    dati = []
    for indice, riga in enumerate(righe):
        celle = [c.strip() for c in riga.strip().strip("|").split("|")]
        if indice == 1 and all(set(c) <= set("-: ") for c in celle):
            continue
        stile = stili["intestazione"] if indice == 0 else stili["cella"]
        dati.append([Paragraph(_inline(c), stile) for c in celle])
    colonne = max(len(r) for r in dati)
    dati = [r + [Paragraph("", stili["cella"])] * (colonne - len(r)) for r in dati]
    # Prima colonna più larga: di solito contiene l'etichetta, le altre la spiegazione.
    prima = larghezza * (0.34 if colonne > 2 else 0.30)
    resto = (larghezza - prima) / (colonne - 1) if colonne > 1 else larghezza
    tabella = Table(dati, colWidths=[prima] + [resto] * (colonne - 1), repeatRows=1, hAlign="LEFT")
    tabella.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), _ACCENTO),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _GRIGIO]),
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDO),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tabella


def _blocco_codice(righe: list[str], stili: dict, larghezza: float) -> Table:
    """Il codice va in una tabella a una cella: dà lo sfondo e il bordo senza tagliare le righe."""
    testo = "<br/>".join(html.escape(r) or "&nbsp;" for r in righe)
    cella = Paragraph(testo, stili["codice"])
    tabella = Table([[cella]], colWidths=[larghezza], hAlign="LEFT")
    tabella.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f7f7")),
        ("BOX", (0, 0), (-1, -1), 0.5, _BORDO),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return tabella


def _elenco(voci: list[tuple[int, str]], stili: dict, *, numerato: bool) -> ListFlowable:
    """Costruisce l'elenco; le voci di livello 1 diventano un sotto-elenco della voce che le precede."""
    elementi: list[ListItem] = []
    i = 0
    while i < len(voci):
        livello, testo = voci[i]
        contenuto: list = [Paragraph(_inline(testo), stili["corpo"])]
        figli: list[ListItem] = []
        i += 1
        while i < len(voci) and voci[i][0] > livello:
            figli.append(ListItem(Paragraph(_inline(voci[i][1]), stili["corpo"]), leftIndent=12))
            i += 1
        if figli:
            contenuto.append(ListFlowable(figli, bulletType="bullet", bulletFontSize=7,
                                          leftIndent=14, spaceBefore=2, spaceAfter=2))
        elementi.append(ListItem(contenuto, leftIndent=14))
    return ListFlowable(
        elementi, bulletType="1" if numerato else "bullet",
        bulletFontSize=8, leftIndent=16, spaceAfter=8,
    )


def costruisci(markdown: str, out_path: Path) -> Path:
    stili = _stili()
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4, topMargin=2.2 * cm, bottomMargin=2 * cm,
        leftMargin=2.2 * cm, rightMargin=2.2 * cm,
        title="bookwriter — Installation and User Manual", author="atain",
    )
    larghezza = doc.width
    flow: list = []
    righe = markdown.splitlines()
    i = 0
    paragrafo: list[str] = []
    voci: list[tuple[int, str]] = []
    numerato = False

    def chiudi_paragrafo() -> None:
        if paragrafo:
            flow.append(Paragraph(_inline(" ".join(paragrafo).strip()), stili["corpo"]))
            paragrafo.clear()

    def chiudi_elenco() -> None:
        if voci:
            flow.append(_elenco(list(voci), stili, numerato=numerato))
            voci.clear()

    def chiudi() -> None:
        chiudi_paragrafo()
        chiudi_elenco()

    while i < len(righe):
        riga = righe[i].rstrip()

        if riga.startswith("```"):  # blocco di codice
            chiudi()
            i += 1
            corpo: list[str] = []
            while i < len(righe) and not righe[i].startswith("```"):
                corpo.append(righe[i].rstrip())
                i += 1
            flow.extend([_blocco_codice(corpo, stili, larghezza), Spacer(1, 9)])
            i += 1
            continue

        if riga.startswith("|") and riga.endswith("|"):  # tabella
            chiudi()
            blocco = []
            while i < len(righe) and righe[i].strip().startswith("|"):
                blocco.append(righe[i])
                i += 1
            flow.extend([_tabella(blocco, stili, larghezza), Spacer(1, 10)])
            continue

        if not riga.strip():
            chiudi()
        elif riga.startswith("---") and set(riga.strip()) == {"-"}:
            # Linea orizzontale: separa senza saltare pagina, altrimenti sommandosi al salto
            # del titolo di parte che segue produrrebbe una pagina bianca.
            chiudi()
            flow.append(Spacer(1, 6))
        elif riga.startswith("> "):
            chiudi()
            flow.append(Paragraph(_inline(riga[2:]), stili["citazione"]))
        elif riga.startswith("# "):
            chiudi()
            if flow and not isinstance(flow[-1], PageBreak):
                flow.append(PageBreak())
            flow.append(Paragraph(_inline(riga[2:]), stili["h1"]))
        elif riga.startswith("## "):
            chiudi()
            flow.append(Paragraph(_inline(riga[3:]), stili["h2"]))
        elif riga.startswith("### "):
            chiudi()
            flow.append(Paragraph(_inline(riga[4:]), stili["h3"]))
        elif re.match(r"^\s*\d+\.\s", riga):
            chiudi_paragrafo()
            if not numerato:
                chiudi_elenco()
                numerato = True
            rientro = len(riga) - len(riga.lstrip())
            voci.append((rientro // 2, re.sub(r"^\s*\d+\.\s+", "", riga)))
        elif re.match(r"^\s*[-*]\s", riga):
            chiudi_paragrafo()
            rientro = len(riga) - len(riga.lstrip())
            if numerato and rientro == 0:
                chiudi_elenco()
                numerato = False
            voci.append((rientro // 2, riga.lstrip()[2:]))
        elif voci and riga.startswith("  "):  # continuazione della voce precedente
            livello, testo = voci[-1]
            voci[-1] = (livello, testo + " " + riga.strip())
        else:
            chiudi_elenco()
            paragrafo.append(riga.strip())
        i += 1
    chiudi()

    # Frontespizio: il titolo del documento diventa la copertina.
    titolo = next((r[2:] for r in righe if r.startswith("# ")), "Manual")
    copertina = [
        Spacer(1, 5 * cm),
        Paragraph(_inline(titolo), stili["titolo"]),
        Paragraph("Installation and user guide", stili["sottotitolo"]),
        PageBreak(),
    ]
    # Il primo titolo è già in copertina: si toglie dal corpo.
    while flow and isinstance(flow[0], Paragraph) and flow[0].style.name in {"Titolo", "H1"}:
        flow.pop(0)
        break

    def piede(canvas, documento) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawString(2.2 * cm, 1.2 * cm, "bookwriter — Installation and User Manual")
        canvas.drawRightString(A4[0] - 2.2 * cm, 1.2 * cm, str(documento.page))
        canvas.restoreState()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.build(copertina + flow, onFirstPage=lambda c, d: None, onLaterPages=piede)
    return out_path


def main() -> int:
    sorgente = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/MANUAL.md")
    destinazione = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs/bookwriter-manual.pdf")
    if not sorgente.exists():
        print(f"Sorgente non trovato: {sorgente}")
        return 1
    percorso = costruisci(sorgente.read_text(encoding="utf-8"), destinazione)
    print(f"Manuale generato: {percorso} ({percorso.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
