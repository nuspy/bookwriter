"""DOCX export: the editable original, laid out like the print edition.

Styles are named, the table of contents is a TOC field Word recalculates, and
the recto running head uses a STYLEREF field so it follows the current chapter
by itself after any edit.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

from .config import Config
from .inline import to_runs
from .model import Block, Book, Section
from .pdf import colophon_lines

INK = RGBColor(0x11, 0x11, 0x11)
INK_SOFT = RGBColor(0x4A, 0x4A, 0x4A)
GREY = RGBColor(0x6E, 0x6E, 0x6E)
RULE = "B4B4B4"

# Style names are exposed to the author in Word, so they follow the book's
# language rather than the code's.
STYLE_NAMES = {
    "it": {"body": "Corpo", "body_first": "Corpo iniziale",
           "chapter": "Titolo capitolo", "chapter_sub": "Sottotitolo capitolo",
           "chapter_label": "Etichetta capitolo", "part": "Titolo parte",
           "apparatus": "Titolo apparato", "h2": "Sezione 1",
           "h3": "Sezione 2", "h4": "Sezione 3", "bullet": "Elenco puntato",
           "number": "Elenco numerato", "reference": "Voce bibliografica",
           "quote": "Citazione", "cell": "Cella", "cell_head": "Cella intestazione",
           "title": "Frontespizio titolo", "subtitle": "Frontespizio sottotitolo",
           "author": "Frontespizio autore", "imprint": "Colophon",
           "dedication": "Dedica", "runhead": "Testatina"},
    "en": {"body": "Body", "body_first": "Body first",
           "chapter": "Chapter title", "chapter_sub": "Chapter subtitle",
           "chapter_label": "Chapter label", "part": "Part title",
           "apparatus": "Apparatus title", "h2": "Section 1",
           "h3": "Section 2", "h4": "Section 3", "bullet": "Bullet list",
           "number": "Numbered list", "reference": "Bibliography entry",
           "quote": "Quotation", "cell": "Cell", "cell_head": "Cell heading",
           "title": "Title page title", "subtitle": "Title page subtitle",
           "author": "Title page author", "imprint": "Colophon",
           "dedication": "Dedication", "runhead": "Running head"},
}


def style_names(language: str):
    return STYLE_NAMES.get((language or "en")[:2].lower(), STYLE_NAMES["en"])


# ------------------------------------------------------------- XML helpers ---

def _el(tag, **attrs):
    e = OxmlElement(tag)
    for k, v in attrs.items():
        e.set(qn(f"w:{k}"), str(v))
    return e


def add_field(paragraph, instruction, default_text=""):
    run = paragraph.add_run()
    run._r.append(_el("w:fldChar", fldCharType="begin"))
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    paragraph.add_run()._r.append(instr)
    paragraph.add_run()._r.append(_el("w:fldChar", fldCharType="separate"))
    paragraph.add_run(default_text)
    paragraph.add_run()._r.append(_el("w:fldChar", fldCharType="end"))


def set_page_numbering(section, fmt="decimal", start=None):
    sectPr = section._sectPr
    for old in sectPr.findall(qn("w:pgNumType")):
        sectPr.remove(old)
    attrs = {"fmt": fmt}
    if start is not None:
        attrs["start"] = start
    sectPr.append(_el("w:pgNumType", **attrs))


def clear_page_numbering(section):
    sectPr = section._sectPr
    for old in sectPr.findall(qn("w:pgNumType")):
        sectPr.remove(old)


def enable_mirror_and_even_odd(document):
    settings = document.settings.element
    for tag in ("w:mirrorMargins", "w:evenAndOddHeaders"):
        if settings.find(qn(tag)) is None:
            settings.append(OxmlElement(tag))


def set_hyphenation(document, enabled=True):
    if not enabled:
        return
    settings = document.settings.element
    if settings.find(qn("w:autoHyphenation")) is None:
        settings.append(OxmlElement("w:autoHyphenation"))
        settings.append(_el("w:hyphenationZone", val=284))
        settings.append(_el("w:consecutiveHyphenLimit", val=3))


def set_paragraph_border(paragraph, edge="bottom", size=6, color=RULE, space=6):
    pPr = paragraph._p.get_or_add_pPr()
    borders = pPr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        pPr.append(borders)
    borders.append(_el(f"w:{edge}", val="single", sz=size, space=space,
                       color=color))


def set_table_borders(table, color=RULE, size=4):
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        borders.append(_el(f"w:{edge}", val="single", sz=size, space=0,
                           color=color))
    table._tbl.tblPr.append(borders)


def set_table_width_full(table):
    tblPr = table._tbl.tblPr
    for tag in ("w:tblW", "w:tblLayout"):
        for old in tblPr.findall(qn(tag)):
            tblPr.remove(old)
    tblPr.append(_el("w:tblW", w=5000, type="pct"))
    tblPr.append(_el("w:tblLayout", type="autofit"))


def shade_cell(cell, fill):
    cell._tc.get_or_add_tcPr().append(_el("w:shd", val="clear", color="auto",
                                          fill=fill))


def set_outline_level(style, level):
    """Make the style visible to Word's generated table of contents."""
    pPr = style.element.get_or_add_pPr()
    for old in pPr.findall(qn("w:outlineLvl")):
        pPr.remove(old)
    pPr.append(_el("w:outlineLvl", val=level))


# ------------------------------------------------------------------ styles ---

def make_styles(doc, cfg: Config, serif: str, sans: str):
    N = style_names(cfg.language)
    size = cfg.typography.body_size

    normal = doc.styles["Normal"]
    normal.font.name = serif
    normal.font.size = Pt(size)
    normal.font.color.rgb = INK
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), serif)
    pf = normal.paragraph_format
    pf.line_spacing = round(cfg.typography.leading / size, 2)
    pf.space_after = Pt(0)
    pf.alignment = (WD_ALIGN_PARAGRAPH.JUSTIFY if cfg.typography.justify
                    else WD_ALIGN_PARAGRAPH.LEFT)

    def new(name, base="Normal"):
        st = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        st.base_style = doc.styles[base]
        return st

    s = new(N["body"]);            s.paragraph_format.first_line_indent = Mm(
        cfg.typography.first_line_indent_mm)
    s = new(N["body_first"])
    s.paragraph_format.first_line_indent = Mm(0)
    s.paragraph_format.space_before = Pt(1.5)

    s = new(N["chapter"])
    s.font.name, s.font.size, s.font.bold = serif, Pt(size * 1.9), True
    s.font.color.rgb = INK
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    s.paragraph_format.space_before, s.paragraph_format.space_after = Pt(6), Pt(2)
    s.paragraph_format.keep_with_next = True
    set_outline_level(s, 1)

    s = new(N["apparatus"], base=N["chapter"])
    set_outline_level(s, 9)          # 9 = body text: stays out of the TOC

    s = new(N["chapter_sub"])
    s.font.name, s.font.size, s.font.italic = serif, Pt(size * 1.19), True
    s.font.color.rgb = INK_SOFT
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    s.paragraph_format.space_after = Pt(16)
    s.paragraph_format.keep_with_next = True

    s = new(N["chapter_label"])
    s.font.name, s.font.size, s.font.color.rgb = sans, Pt(size * 0.86), GREY
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    s.paragraph_format.space_after = Pt(3)
    s.paragraph_format.keep_with_next = True

    s = new(N["part"])
    s.font.name, s.font.size, s.font.bold = serif, Pt(size * 2.1), True
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    s.paragraph_format.space_before, s.paragraph_format.space_after = Pt(8), Pt(14)
    set_outline_level(s, 0)

    for key, factor, bold, italic, font in (
            ("h2", 1.19, True, False, serif),
            ("h3", 1.05, True, True, serif),
            ("h4", 0.90, True, False, sans)):
        s = new(N[key])
        s.font.name, s.font.size = font, Pt(size * factor)
        s.font.bold, s.font.italic = bold, italic
        if key != "h2":
            s.font.color.rgb = INK_SOFT
        s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        s.paragraph_format.space_before = Pt(14 if key == "h2" else
                                             11 if key == "h3" else 9)
        s.paragraph_format.space_after = Pt(4 if key == "h2" else
                                            3 if key == "h3" else 2)
        s.paragraph_format.keep_with_next = True

    s = new(N["bullet"])
    s.paragraph_format.left_indent = Mm(7)
    s.paragraph_format.first_line_indent = Mm(-3.5)
    s.paragraph_format.space_before = Pt(2)

    s = new(N["number"])
    s.paragraph_format.left_indent = Mm(8)
    s.paragraph_format.first_line_indent = Mm(-4.5)
    s.paragraph_format.space_before = Pt(2)

    s = new(N["quote"])
    s.font.italic, s.font.size, s.font.color.rgb = True, Pt(size * 0.94), INK_SOFT
    s.paragraph_format.left_indent = Mm(8)
    s.paragraph_format.right_indent = Mm(5)
    s.paragraph_format.first_line_indent = Mm(0)
    s.paragraph_format.space_before = s.paragraph_format.space_after = Pt(6)

    s = new(N["reference"])
    s.font.size = Pt(size * 0.9)
    s.paragraph_format.left_indent = Mm(5)
    s.paragraph_format.first_line_indent = Mm(-5)
    s.paragraph_format.space_after = Pt(5)
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

    s = new(N["cell"])
    s.font.size = Pt(size * 0.81)
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
    s.paragraph_format.line_spacing = 1.0
    s.paragraph_format.first_line_indent = Mm(0)
    s.paragraph_format.space_before = s.paragraph_format.space_after = Pt(1.5)

    s = new(N["cell_head"], base=N["cell"])
    s.font.name, s.font.bold = sans, True

    s = new(N["title"])
    s.font.name, s.font.size, s.font.bold = serif, Pt(size * 2.85), True
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    s.paragraph_format.space_after = Pt(10)

    s = new(N["subtitle"])
    s.font.name, s.font.size, s.font.italic = serif, Pt(size * 1.19), True
    s.font.color.rgb = INK_SOFT
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER

    s = new(N["author"])
    s.font.name, s.font.size = sans, Pt(size * 1.28)
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER

    s = new(N["imprint"])
    s.font.size, s.font.color.rgb = Pt(size * 0.81), INK_SOFT
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    s.paragraph_format.line_spacing = 1.15
    s.paragraph_format.first_line_indent = Mm(0)

    s = new(N["dedication"])
    s.font.italic, s.font.size = True, Pt(size * 1.1)
    s.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    s.paragraph_format.line_spacing = 1.3

    s = new(N["runhead"])
    s.font.name, s.font.size, s.font.color.rgb = sans, Pt(size * 0.76), GREY
    s.paragraph_format.space_after = Pt(0)
    return N


# -------------------------------------------------------------- rendering ---

def add_runs(paragraph, text):
    for chunk, bold, italic in to_runs(text):
        run = paragraph.add_run(chunk)
        run.bold, run.italic = bold, italic
    return paragraph


def add_para(doc, text, style):
    return add_runs(doc.add_paragraph(style=style), text)


def blank_lines(doc, n, size=11):
    for _ in range(n):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(0)
        p.add_run("").font.size = Pt(size)


def add_table(doc, block: Block, N):
    rows = block.rows
    ncols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=ncols)
    table.autofit = True
    set_table_width_full(table)
    set_table_borders(table)
    for i, row in enumerate(rows):
        cells = list(row) + [""] * (ncols - len(row))
        for j, text in enumerate(cells):
            cell = table.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.style = doc.styles[N["cell_head"] if i == 0 else N["cell"]]
            add_runs(p, text)
            if i == 0:
                shade_cell(cell, "ECECEC")
            elif i % 2 == 0:
                shade_cell(cell, "F7F7F7")
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    doc.add_paragraph()


def add_blocks(doc, blocks: List[Block], N, body_style=None):
    body_style = body_style or N["body"]
    prev = None
    no_indent_after = (None, "h2", "h3", "h4", "bullet", "number", "table",
                       "rule", "gap", "quote")
    for b in blocks:
        if b.kind in ("h2", "h3", "h4"):
            add_para(doc, b.text, N[b.kind])
        elif b.kind == "para":
            style = N["body_first"] if prev in no_indent_after else body_style
            add_para(doc, b.text, style)
        elif b.kind == "bullet":
            p = doc.add_paragraph(style=N["bullet"])
            p.add_run("•\t")
            add_runs(p, b.text)
        elif b.kind == "number":
            p = doc.add_paragraph(style=N["number"])
            p.add_run(f"{b.level}.\t")
            add_runs(p, b.text)
        elif b.kind == "quote":
            add_para(doc, b.text, N["quote"])
        elif b.kind == "table":
            add_table(doc, b, N)
        elif b.kind == "reference":
            add_para(doc, b.text, N["reference"])
        elif b.kind == "rule":
            p = add_para(doc, "* * *", N["imprint"])
            p.paragraph_format.space_before = p.paragraph_format.space_after = Pt(8)
        elif b.kind == "gap":
            doc.add_paragraph()
        prev = b.kind


# ---------------------------------------------------------------- sections ---

def _clear_part(part):
    part.is_linked_to_previous = False
    for p in list(part.paragraphs[1:]):
        p._p.getparent().remove(p._p)
    p = part.paragraphs[0]
    for r in list(p.runs):
        r._r.getparent().remove(r._r)
    pPr = p._p.find(qn("w:pPr"))
    if pPr is not None:
        for b in pPr.findall(qn("w:pBdr")):
            pPr.remove(b)
    return p


def setup_headers(section, book: Book, cfg: Config, N, running=True):
    """Verso carries the book title, recto the current chapter.

    The first page of every Word section is an opening page and carries no
    running head, only the folio centred at the foot.
    """
    section.different_first_page_header_footer = True
    _clear_part(section.first_page_header)
    p = _clear_part(section.first_page_footer)
    p.style = N["runhead"]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_field(p, "PAGE", "1")

    if not running:
        for part in (section.header, section.even_page_header,
                     section.footer, section.even_page_footer):
            _clear_part(part)
        return

    text_w = cfg.page.width_mm - cfg.page.margin_inner_mm - cfg.page.margin_outer_mm

    p = _clear_part(section.even_page_header)
    p.style = N["runhead"]
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p.paragraph_format.tab_stops.add_tab_stop(Mm(text_w), WD_TAB_ALIGNMENT.RIGHT)
    add_field(p, "PAGE", "1")
    p.add_run("\t" + book.title.upper())
    set_paragraph_border(p, "bottom", size=4, color="D8D8D8")

    p = _clear_part(section.header)
    p.style = N["runhead"]
    p.paragraph_format.tab_stops.add_tab_stop(Mm(text_w), WD_TAB_ALIGNMENT.RIGHT)
    add_field(p, f'STYLEREF "{N["chapter"]}" \\* MERGEFORMAT', "")
    p.add_run("\t")
    add_field(p, "PAGE", "1")
    set_paragraph_border(p, "bottom", size=4, color="D8D8D8")

    for part in (section.footer, section.even_page_footer):
        _clear_part(part)


def configure_section(section, cfg: Config):
    p = cfg.page
    section.page_width = Mm(p.width_mm)
    section.page_height = Mm(p.height_mm)
    section.left_margin = Mm(p.margin_inner_mm)
    section.right_margin = Mm(p.margin_outer_mm)
    section.top_margin = Mm(p.margin_top_mm)
    section.bottom_margin = Mm(p.margin_bottom_mm)
    section.header_distance = Mm(max(p.margin_top_mm - 7, 6))
    section.footer_distance = Mm(max(p.margin_bottom_mm - 8, 6))
    clear_page_numbering(section)
    return section


def opening(doc, section: Section, N, rule=True):
    add_para(doc, section.title, N["chapter"])
    if section.subtitle:
        add_para(doc, section.subtitle, N["chapter_sub"])
    if rule:
        p = doc.add_paragraph()
        set_paragraph_border(p, "bottom", size=8)
        p.paragraph_format.space_after = Pt(10)


# ------------------------------------------------------------------- build ---

#: Il limite di Word per le proprietà del documento (titolo, oggetto, autore, parole chiave).
CORE_PROPERTY_MAX = 255


def _core_text(value) -> str:
    """Il testo di una proprietà del documento, accorciato entro il limite di Word."""
    testo = " ".join(str(value or "").split())
    if len(testo) <= CORE_PROPERTY_MAX:
        return testo
    return testo[:CORE_PROPERTY_MAX - 1].rstrip() + "…"


def build_docx(book: Book, path: Path, cfg: Config, serif="Constantia",
               sans="Corbel"):
    doc = Document()
    N = make_styles(doc, cfg, serif, sans)
    enable_mirror_and_even_odd(doc)
    set_hyphenation(doc, bool(cfg.typography.hyphenation))

    core = doc.core_properties
    # Word non accetta proprietà del documento oltre i 255 caratteri, e python-docx si ferma
    # con un errore: una descrizione lunga (la premessa del libro) bloccava l'intera composizione.
    core.title = _core_text(book.title)
    core.subject = _core_text(cfg.subtitle or cfg.description)
    core.author = _core_text(book.author)
    core.language = cfg.language
    if cfg.keywords:
        core.keywords = _core_text(", ".join(cfg.keywords))

    section = doc.sections[0]
    configure_section(section, cfg)
    set_page_numbering(section, "lowerRoman", 1)
    setup_headers(section, book, cfg, N, running=False)

    # -- front matter
    blank_lines(doc, 8)
    p = doc.add_paragraph(style=N["subtitle"])
    r = p.add_run(book.title)
    r.italic = False
    r.font.size = Pt(cfg.typography.body_size * 1.5)
    doc.add_page_break()
    doc.add_paragraph()
    doc.add_page_break()

    blank_lines(doc, 5)
    if book.author:
        add_para(doc, book.author.upper(), N["author"])
    blank_lines(doc, 3)
    add_para(doc, book.title, N["title"])
    if book.subtitle:
        add_para(doc, book.subtitle, N["subtitle"])
    blank_lines(doc, 10)
    if cfg.publisher:
        add_para(doc, cfg.publisher, N["imprint"])
    doc.add_page_break()

    blank_lines(doc, 12)
    for line in colophon_lines(cfg):
        add_para(doc, line, N["imprint"]) if line else blank_lines(doc, 1, size=6)
    doc.add_page_break()

    if cfg.dedication or cfg.epigraph:
        blank_lines(doc, 9)
        for line in (cfg.dedication or "").split("\n"):
            if line.strip():
                add_para(doc, line.strip(), N["dedication"])
        if cfg.dedication and cfg.epigraph:
            blank_lines(doc, 6)
            add_para(doc, "◆ ◆ ◆", N["imprint"])
            blank_lines(doc, 4)
        if cfg.epigraph:
            add_para(doc, f"«{cfg.epigraph}»", N["dedication"])

    # -- contents
    sec = doc.add_section(WD_SECTION.ODD_PAGE)
    configure_section(sec, cfg)
    setup_headers(sec, book, cfg, N)
    add_para(doc, cfg.label("contents", "Contents"), N["apparatus"])
    p = doc.add_paragraph()
    set_paragraph_border(p, "bottom", size=8)
    p.paragraph_format.space_after = Pt(10)
    add_field(doc.add_paragraph(), r'TOC \o "1-2" \h \z \u',
              "Ctrl+A, F9")
    doc.add_paragraph()

    for fs in book.front:
        sec = doc.add_section(WD_SECTION.ODD_PAGE)
        configure_section(sec, cfg)
        setup_headers(sec, book, cfg, N)
        opening(doc, fs, N)
        add_blocks(doc, fs.blocks, N)

    # -- body
    multipart = len(book.parts) > 1 or bool(book.parts[0].title)
    first_body = True
    for part in book.parts:
        if multipart:
            sec = doc.add_section(WD_SECTION.ODD_PAGE)
            configure_section(sec, cfg)
            setup_headers(sec, book, cfg, N, running=False)
            if first_body:
                set_page_numbering(sec, "decimal", 1)
                first_body = False
            blank_lines(doc, 6)
            # label and title share one paragraph so the TOC entry reads
            # "Part I — TITLE" instead of the bare title
            p = doc.add_paragraph(style=N["part"])
            label = p.add_run(f"{cfg.label('part', 'Part')} {part.roman}")
            label.font.name = sans
            label.font.size = Pt(cfg.typography.body_size)
            label.font.bold = False
            label.font.color.rgb = GREY
            label.add_break(WD_BREAK.LINE)
            p.add_run(part.title)
            blank_lines(doc, 1)
            add_blocks(doc, part.intro, N, body_style=N["body_first"])

        for chapter in part.chapters:
            sec = doc.add_section(WD_SECTION.ODD_PAGE)
            configure_section(sec, cfg)
            setup_headers(sec, book, cfg, N)
            if first_body:
                set_page_numbering(sec, "decimal", 1)
                first_body = False
            blank_lines(doc, 3)
            add_para(doc, f"{cfg.label('chapter', 'Chapter')} "
                          f"{chapter.number}".upper(), N["chapter_label"])
            p = doc.add_paragraph()
            set_paragraph_border(p, "bottom", size=6)
            p.paragraph_format.space_after = Pt(6)
            add_para(doc, chapter.title, N["chapter"])
            if chapter.subtitle:
                add_para(doc, chapter.subtitle, N["chapter_sub"])
            add_blocks(doc, chapter.blocks, N)

    for fs in book.back:
        sec = doc.add_section(WD_SECTION.ODD_PAGE)
        configure_section(sec, cfg)
        setup_headers(sec, book, cfg, N)
        opening(doc, fs, N)
        add_blocks(doc, fs.blocks, N)

    sec = doc.add_section(WD_SECTION.ODD_PAGE)
    configure_section(sec, cfg)
    setup_headers(sec, book, cfg, N, running=False)
    blank_lines(doc, 22)
    for line in (book.title, f"{cfg.label('by', 'by')} {book.author}", "",
                 f"{cfg.edition} — {cfg.year}".strip(" —")):
        add_para(doc, line, N["imprint"]) if line else blank_lines(doc, 1, size=6)

    doc.save(str(path))
    return path
