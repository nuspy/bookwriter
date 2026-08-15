"""Esportazione della bozza rivista con le modifiche in evidenza.

Le marcature richieste:

- **aggiunto**   → paragrafo incorniciato di rosso;
- **rimosso**    → testo barrato in rosso;
- **sostituito** → il vecchio barrato, il nuovo in verde subito sotto.

Il DOCX è costruito con ``python-docx`` invece che con pandoc: pandoc conserva il barrato ma
**perde i colori**, e qui il colore è metà dell'informazione. L'HTML serve per una lettura
rapida nel browser senza aprire Word.
"""

from __future__ import annotations

import html as _html
from pathlib import Path

from bookwriter.update.revise import Change, RevisionReport

# python-docx è una dipendenza opzionale (extra 'ingest'/'all'): l'import sta dentro alle funzioni
# che ne hanno bisogno, così l'HTML e l'avvio della GUI restano possibili anche senza.
_ROSSO_RGB = (0xC6, 0x28, 0x28)
_VERDE_RGB = (0x1B, 0x5E, 0x20)
_ROSSO_HTML = "#c62828"
_VERDE_HTML = "#1b5e20"


def _docx():
    try:
        import docx
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import RGBColor
    except ImportError as exc:  # pragma: no cover - dipende dall'installazione
        raise RuntimeError(
            "Per esportare la revisione in DOCX serve la dipendenza 'python-docx'. "
            'Installa gli extra: pip install -e ".[ingest]"'
        ) from exc
    return docx, OxmlElement, qn, RGBColor


def _frame(paragraph, OxmlElement, qn, colore: str = "C62828") -> None:
    """Cornice attorno al paragrafo: è così che si segnala il contenuto nuovo."""
    borders = OxmlElement("w:pBdr")
    for lato in ("top", "left", "bottom", "right"):
        edge = OxmlElement(f"w:{lato}")
        edge.set(qn("w:val"), "single")
        edge.set(qn("w:sz"), "8")
        edge.set(qn("w:space"), "4")
        edge.set(qn("w:color"), colore)
        borders.append(edge)
    paragraph._p.get_or_add_pPr().append(borders)


def _run(paragraph, testo: str, *, colore=None, barrato: bool = False) -> None:
    run = paragraph.add_run(testo)
    if colore is not None:
        run.font.color.rgb = colore
    run.font.strike = barrato


def _ordina(report: RevisionReport) -> dict[str, list[Change]]:
    """Modifiche raggruppate per capitolo, nell'ordine in cui compaiono."""
    per_capitolo: dict[str, list[Change]] = {}
    for change in report.changes:
        per_capitolo.setdefault(change.chapter, []).append(change)
    return per_capitolo


def write_revision_docx(report: RevisionReport, out_path: Path, *, title: str = "Bozza aggiornata") -> Path:
    """Scrive il DOCX con le modifiche marcate. Ritorna il percorso."""
    docx, OxmlElement, qn, RGBColor = _docx()
    _ROSSO, _VERDE = RGBColor(*_ROSSO_RGB), RGBColor(*_VERDE_RGB)
    document = docx.Document()
    document.add_heading(title, level=0)
    legenda = document.add_paragraph()
    _run(legenda, "Legenda: ")
    _run(legenda, "nuovo (incorniciato)", colore=_ROSSO)
    _run(legenda, " · ")
    _run(legenda, "rimosso", colore=_ROSSO, barrato=True)
    _run(legenda, " · ")
    _run(legenda, "sostituito", colore=_ROSSO, barrato=True)
    _run(legenda, " → ")
    _run(legenda, "nuova versione", colore=_VERDE)

    for capitolo, modifiche in _ordina(report).items():
        document.add_heading(capitolo or "Senza capitolo", level=1)
        for change in modifiche:
            if change.operation == "keep":
                document.add_paragraph(change.text)
            elif change.operation == "add":
                paragrafo = document.add_paragraph()
                _run(paragrafo, change.text, colore=_ROSSO)
                _frame(paragrafo, OxmlElement, qn)
            elif change.operation == "remove":
                _run(document.add_paragraph(), change.text, colore=_ROSSO, barrato=True)
            elif change.operation == "replace":
                _run(document.add_paragraph(), change.previous, colore=_ROSSO, barrato=True)
                _run(document.add_paragraph(), change.text, colore=_VERDE)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(out_path))
    return out_path


def write_revision_pdf(report: RevisionReport, out_path: Path, *, title: str = "Bozza aggiornata") -> Path:
    """Stessa revisione in PDF: le marcature diventano colore e barratura del testo."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    base = getSampleStyleSheet()
    corpo = ParagraphStyle("Body", parent=base["BodyText"], fontSize=11, leading=16, spaceAfter=8)
    nuovo = ParagraphStyle(
        "Nuovo", parent=corpo, textColor=_ROSSO_HTML, borderColor=_ROSSO_HTML,
        borderWidth=0.7, borderPadding=5, spaceBefore=4,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4, topMargin=2 * cm, bottomMargin=2 * cm,
        leftMargin=2.2 * cm, rightMargin=2.2 * cm, title=title,
    )
    barrato = f'<strike><font color="{_ROSSO_HTML}">{{0}}</font></strike>'
    flusso: list = [
        Paragraph(_html.escape(title), base["Title"]),
        Paragraph(
            "Legenda: <font color='%s'>nuovo (incorniciato)</font> · %s → <font color='%s'>nuova versione</font>"
            % (_ROSSO_HTML, barrato.format("rimosso"), _VERDE_HTML),
            corpo,
        ),
    ]
    for capitolo, modifiche in _ordina(report).items():
        flusso.append(Paragraph(_html.escape(capitolo or "Senza capitolo"), base["Heading1"]))
        for change in modifiche:
            testo = _html.escape(change.text)
            if change.operation == "keep":
                flusso.append(Paragraph(testo, corpo))
            elif change.operation == "add":
                flusso.append(Paragraph(testo, nuovo))
            elif change.operation == "remove":
                flusso.append(Paragraph(barrato.format(testo), corpo))
            elif change.operation == "replace":
                flusso.append(Paragraph(barrato.format(_html.escape(change.previous)), corpo))
                flusso.append(Paragraph(f'<font color="{_VERDE_HTML}">{testo}</font>', corpo))
    doc.build(flusso or [Spacer(1, 1)])
    return out_path


def write_revision_markdown(report: RevisionReport, out_path: Path, *, title: str = "Bozza aggiornata") -> Path:
    """Stessa revisione in Markdown: il rimosso barrato, il nuovo citato in blocco."""
    righe = [
        f"# {title}",
        "",
        "> Legenda: **nuovo** in blocco citato · ~~rimosso~~ barrato · ~~sostituito~~ seguito dalla nuova versione",
        "",
    ]
    for capitolo, modifiche in _ordina(report).items():
        righe += [f"## {capitolo or 'Senza capitolo'}", ""]
        for change in modifiche:
            if change.operation == "keep":
                righe += [change.text, ""]
            elif change.operation == "add":
                righe += ["> **[nuovo]** " + change.text.replace("\n", "\n> "), ""]
            elif change.operation == "remove":
                righe += [f"~~{change.text}~~", ""]
            elif change.operation == "replace":
                righe += [f"~~{change.previous}~~", "", change.text, ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(righe).rstrip() + "\n", encoding="utf-8")
    return out_path


def write_revision(
    report: RevisionReport, out_path: Path, fmt: str = "docx", *, title: str = "Bozza aggiornata"
) -> Path:
    """Scrive la revisione nel formato richiesto, con le stesse marcature in tutti e tre."""
    scrittori = {
        "docx": write_revision_docx,
        "pdf": write_revision_pdf,
        "md": write_revision_markdown,
    }
    if fmt not in scrittori:
        raise ValueError(f"Formato non supportato: {fmt} (usa {'|'.join(scrittori)})")
    return scrittori[fmt](report, out_path, title=title)


def write_revision_html(report: RevisionReport, out_path: Path, *, title: str = "Bozza aggiornata") -> Path:
    """Stessa revisione in HTML, per rileggerla nel browser."""
    stile = (
        "body{font-family:Georgia,serif;max-width:46rem;margin:2rem auto;padding:0 1rem;line-height:1.6}"
        f".add{{border:1px solid {_ROSSO_HTML};color:{_ROSSO_HTML};padding:.6rem;border-radius:4px}}"
        f".del{{color:{_ROSSO_HTML};text-decoration:line-through}}"
        f".new{{color:{_VERDE_HTML}}}"
        ".legenda{font-size:.9rem;color:#555;border-bottom:1px solid #ddd;padding-bottom:.6rem}"
    )
    parti = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>{_html.escape(title)}</title><style>{stile}</style>",
        f"<h1>{_html.escape(title)}</h1>",
        (
            "<p class='legenda'>Legenda: <span class='add'>nuovo</span> · "
            "<span class='del'>rimosso</span> · <span class='del'>sostituito</span> "
            "<span class='new'>nuova versione</span></p>"
        ),
    ]
    for capitolo, modifiche in _ordina(report).items():
        parti.append(f"<h2>{_html.escape(capitolo or 'Senza capitolo')}</h2>")
        for change in modifiche:
            testo = _html.escape(change.text)
            if change.operation == "keep":
                parti.append(f"<p>{testo}</p>")
            elif change.operation == "add":
                parti.append(f"<p class='add'>{testo}</p>")
            elif change.operation == "remove":
                parti.append(f"<p class='del'>{testo}</p>")
            elif change.operation == "replace":
                parti.append(f"<p class='del'>{_html.escape(change.previous)}</p>")
                parti.append(f"<p class='new'>{testo}</p>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parti), encoding="utf-8")
    return out_path
