"""Print-ready PDF composition."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from reportlab.lib import enums
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Frame,
                                KeepTogether, NextPageTemplate, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)
from reportlab.platypus.doctemplate import ActionFlowable
from reportlab.platypus.tableofcontents import TableOfContents

from .config import Config
from .inline import to_rl
from .model import Block, Book, Chapter, Part, Section
from .typography import SYMBOL, Theme, register_fonts, theme_from_config

ROMAN_FOLIO = ["", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
               "xi", "xii", "xiii", "xiv", "xv", "xvi", "xvii", "xviii", "xix",
               "xx", "xxi", "xxii", "xxiii", "xxiv", "xxv", "xxvi", "xxvii",
               "xxviii", "xxix", "xxx"]


# ------------------------------------------------------------- flowables ---

class OddPageBreak(ActionFlowable):
    """Move composition to the next recto, leaving a blank verso if needed."""

    def __init__(self):
        ActionFlowable.__init__(self, ("oddPageBreak",))

    def apply(self, doc):
        doc.handle_pageBreak()
        # doc.page still reports the page just closed: if it is odd, the next
        # one would be a verso and must be left blank.
        if doc.page % 2 == 1:
            doc.handle_pageBreak()


class MarkBodyStart(ActionFlowable):
    """Record where arabic numbering begins."""

    def __init__(self):
        ActionFlowable.__init__(self, ("markBodyStart",))

    def apply(self, doc):
        doc.body_start_page = doc.page


class SectionState(ActionFlowable):
    """Update what the running heads show."""

    def __init__(self, running_head, numbered=True, opening=False):
        ActionFlowable.__init__(self, ("sectionState",))
        self.running_head = running_head
        self.numbered = numbered
        self.opening = opening

    def apply(self, doc):
        doc.running_head = self.running_head
        doc.show_number = self.numbered
        doc.suppress_head_once = self.opening


class Rule(Spacer):
    def __init__(self, width=None, thickness=0.5, space=6, color=None,
                 align="center"):
        Spacer.__init__(self, 1, space * 2 + thickness)
        self.rule_w = width
        self.thickness = thickness
        self.color = color
        self.align = align

    def draw(self):
        w = self.rule_w or self.width
        x = 0 if self.align == "left" else (self.width - w) / 2.0
        self.canv.saveState()
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        y = self.height / 2.0
        self.canv.line(x, y, x + w, y)
        self.canv.restoreState()


class Ornament(Spacer):
    """Break between passages, drawn rather than set from a font glyph."""

    def __init__(self, color, size=3.1, space=9, triple=False):
        Spacer.__init__(self, 1, space * 2 + size * 2)
        self.size = size
        self.triple = triple
        self.color = color

    def _diamond(self, cx, cy, r):
        p = self.canv.beginPath()
        p.moveTo(cx, cy + r)
        p.lineTo(cx + r * 0.62, cy)
        p.lineTo(cx, cy - r)
        p.lineTo(cx - r * 0.62, cy)
        p.close()
        self.canv.drawPath(p, stroke=0, fill=1)

    def draw(self):
        self.canv.saveState()
        self.canv.setFillColor(self.color)
        cx, cy = self.width / 2.0, self.height / 2.0
        if self.triple:
            gap = self.size * 3.4
            for dx in (-gap, 0, gap):
                self._diamond(cx + dx, cy, self.size)
        else:
            self._diamond(cx, cy, self.size)
        self.canv.restoreState()


# ----------------------------------------------------------- doc template ---

class BookDocTemplate(BaseDocTemplate):

    def __init__(self, filename, book: Book, theme: Theme, **kw):
        self.book = book
        self.theme = theme
        self.running_head = ""
        self.show_number = False
        self.suppress_head_once = False
        self.body_start_page = None
        self.body_start_known = None
        BaseDocTemplate.__init__(self, filename, **kw)

        t = theme
        frame_recto = Frame(t.margin_inner, t.margin_bottom, t.frame_w,
                            t.frame_h, id="recto", leftPadding=0,
                            rightPadding=0, topPadding=0, bottomPadding=0)
        frame_verso = Frame(t.margin_outer, t.margin_bottom, t.frame_w,
                            t.frame_h, id="verso", leftPadding=0,
                            rightPadding=0, topPadding=0, bottomPadding=0)

        # Running heads are drawn when the page closes: only then is it known
        # which chapter occupies it.
        self.addPageTemplates([
            PageTemplate(id="recto", frames=[frame_recto],
                         onPageEnd=lambda c, d: self._decorate(c, d, True),
                         pagesize=(t.page_w, t.page_h)),
            PageTemplate(id="verso", frames=[frame_verso],
                         onPageEnd=lambda c, d: self._decorate(c, d, False),
                         pagesize=(t.page_w, t.page_h)),
        ])

    def build(self, *args, **kw):
        if self.body_start_page:
            self.body_start_known = self.body_start_page
        self.running_head = ""
        self.show_number = False
        self.suppress_head_once = False
        self.body_start_page = None
        return BaseDocTemplate.build(self, *args, **kw)

    # -- folios ----------------------------------------------------------

    @staticmethod
    def _roman(page):
        return ROMAN_FOLIO[page] if page < len(ROMAN_FOLIO) else str(page)

    def folio(self, page):
        start = self.body_start_page
        if start is None or page < start:
            return self._roman(page)
        return str(page - start + 1)

    def folio_for_toc(self, page):
        start = self.body_start_page or self.body_start_known
        if not start:
            return str(page)
        return self._roman(page) if page < start else str(page - start + 1)

    # -- running heads ---------------------------------------------------

    @staticmethod
    def _tracked_width(text, font, size, tracking):
        from reportlab.pdfbase import pdfmetrics
        return pdfmetrics.stringWidth(text, font, size) + tracking * len(text)

    def _draw_tracked(self, canvas, x, y, text, font, size, tracking, color,
                      align="left"):
        w = self._tracked_width(text, font, size, tracking)
        if align == "right":
            x -= w
        to = canvas.beginText(x, y)
        to.setFont(font, size)
        to.setFillColor(color)
        to.setCharSpace(tracking)
        to.textOut(text)
        canvas.drawText(to)

    def _decorate(self, canvas, doc, recto):
        if not self.show_number:
            return
        t = self.theme
        canvas.saveState()
        left = t.margin_inner if recto else t.margin_outer
        right = left + t.frame_w
        y = t.page_h - t.margin_top + 5.6 * mm
        folio = self.folio(doc.page)

        if self.suppress_head_once:
            canvas.setFont(t.sans_family, 8.5)
            canvas.setFillColor(t.GREY)
            canvas.drawCentredString(left + t.frame_w / 2.0,
                                     max(t.margin_bottom - 9 * mm, 6 * mm),
                                     folio)
            canvas.restoreState()
            self.suppress_head_once = False
            return

        head = (self.book.title if not recto else self.running_head).upper()
        max_w = t.frame_w - 14 * mm
        while (self._tracked_width(head, t.sans_family, 7.6, 1.1) > max_w
               and len(head) > 12):
            head = head[:-2].rstrip(" ,;:—-") + "…"

        if recto:
            self._draw_tracked(canvas, right - 9 * mm, y, head,
                               t.sans_family, 7.6, 1.1, t.GREY, align="right")
            canvas.setFont(t.sans_family, 8.4)
            canvas.setFillColor(t.INK_SOFT)
            canvas.drawRightString(right, y, folio)
        else:
            self._draw_tracked(canvas, left + 9 * mm, y, head,
                               t.sans_family, 7.6, 1.1, t.GREY)
            canvas.setFont(t.sans_family, 8.4)
            canvas.setFillColor(t.INK_SOFT)
            canvas.drawString(left, y, folio)

        canvas.setStrokeColor(t.RULE_LIGHT)
        canvas.setLineWidth(0.4)
        canvas.line(left, y - 2.6 * mm, right, y - 2.6 * mm)
        canvas.restoreState()

    def afterFlowable(self, flowable):
        entry = getattr(flowable, "toc_entry", None)
        if not entry:
            return
        level, text = entry
        key = str(id(flowable))
        self.notify("TOCEntry", (level, text, self.page, key))
        self.canv.bookmarkPage(key)


# ---------------------------------------------------------------- styles ---

def build_styles(theme: Theme):
    t = theme
    serif, sans = t.serif_family, t.sans_family
    align = enums.TA_JUSTIFY if t.justify else enums.TA_LEFT
    ps = {}

    ps["body"] = ParagraphStyle(
        "body", fontName=serif, fontSize=t.body_size, leading=t.leading,
        alignment=align, textColor=t.INK, firstLineIndent=t.indent,
        hyphenationLang=t.hyphen_lang, embeddedHyphenation=1,
        allowWidows=0, allowOrphans=0)
    ps["body_first"] = ParagraphStyle("body_first", parent=ps["body"],
                                      firstLineIndent=0)
    ps["body_first_after"] = ParagraphStyle("body_first_after",
                                            parent=ps["body"],
                                            firstLineIndent=0, spaceBefore=1.5)

    ps["h2"] = ParagraphStyle(
        "h2", fontName=f"{serif}-Bold", fontSize=t.body_size * 1.19,
        leading=t.leading * 1.06, textColor=t.INK, spaceBefore=15,
        spaceAfter=4.5, keepWithNext=1)
    ps["h3"] = ParagraphStyle(
        "h3", fontName=f"{serif}-BoldItalic", fontSize=t.body_size * 1.05,
        leading=t.leading * 0.95, textColor=t.INK_SOFT, spaceBefore=11,
        spaceAfter=3, keepWithNext=1)
    ps["h4"] = ParagraphStyle(
        "h4", fontName=f"{sans}-Bold", fontSize=t.body_size * 0.86,
        leading=t.leading * 0.82, textColor=t.INK_SOFT, spaceBefore=8.5,
        spaceAfter=2.2, keepWithNext=1)

    ps["bullet"] = ParagraphStyle(
        "bullet", parent=ps["body"], firstLineIndent=0, leftIndent=6.5 * mm,
        bulletIndent=2 * mm, spaceBefore=1.6, bulletFontName=serif,
        bulletFontSize=t.body_size * 0.78)
    ps["number"] = ParagraphStyle(
        "number", parent=ps["bullet"], bulletIndent=0.6 * mm,
        bulletFontName=f"{sans}-Bold", bulletFontSize=t.body_size * 0.86)

    ps["quote"] = ParagraphStyle(
        "quote", parent=ps["body"], firstLineIndent=0, leftIndent=8 * mm,
        rightIndent=5 * mm, fontSize=t.body_size * 0.94,
        leading=t.leading * 0.94, textColor=t.INK_SOFT, spaceBefore=7,
        spaceAfter=7, fontName=f"{serif}-Italic")

    ps["cell"] = ParagraphStyle(
        "cell", fontName=serif, fontSize=t.body_size * 0.81,
        leading=t.body_size * 1.04, textColor=t.INK, alignment=enums.TA_LEFT,
        hyphenationLang=t.hyphen_lang, embeddedHyphenation=1, splitLongWords=1)
    ps["cell_head"] = ParagraphStyle("cell_head", parent=ps["cell"],
                                     fontName=f"{sans}-Bold",
                                     fontSize=t.body_size * 0.77)

    ps["reference"] = ParagraphStyle(
        "reference", fontName=serif, fontSize=t.body_size * 0.87,
        leading=t.leading * 0.84, textColor=t.INK, alignment=enums.TA_LEFT,
        leftIndent=5 * mm, firstLineIndent=-5 * mm, spaceAfter=5.2,
        hyphenationLang=t.hyphen_lang, splitLongWords=1)

    ps["chapter_label"] = ParagraphStyle(
        "chapter_label", fontName=sans, fontSize=t.body_size * 0.81,
        leading=11, textColor=t.GREY)
    ps["chapter_title"] = ParagraphStyle(
        "chapter_title", fontName=f"{serif}-Bold", fontSize=t.body_size * 1.84,
        leading=t.body_size * 2.17, textColor=t.INK, spaceBefore=5)
    ps["chapter_sub"] = ParagraphStyle(
        "chapter_sub", fontName=f"{serif}-Italic", fontSize=t.body_size * 1.15,
        leading=t.body_size * 1.46, textColor=t.INK_SOFT, spaceBefore=4)

    ps["part_label"] = ParagraphStyle(
        "part_label", fontName=sans, fontSize=t.body_size * 0.94, leading=13,
        textColor=t.GREY, alignment=enums.TA_CENTER)
    ps["part_title"] = ParagraphStyle(
        "part_title", fontName=f"{serif}-Bold", fontSize=t.body_size * 1.98,
        leading=t.body_size * 2.36, textColor=t.INK,
        alignment=enums.TA_CENTER, spaceBefore=6)
    ps["part_body"] = ParagraphStyle("part_body", parent=ps["body"],
                                     firstLineIndent=0, spaceBefore=5)

    ps["title_main"] = ParagraphStyle(
        "title_main", fontName=f"{serif}-Bold", fontSize=t.body_size * 2.83,
        leading=t.body_size * 3.2, textColor=t.INK, alignment=enums.TA_CENTER)
    ps["title_sub"] = ParagraphStyle(
        "title_sub", fontName=f"{serif}-Italic", fontSize=t.body_size * 1.17,
        leading=t.body_size * 1.6, textColor=t.INK_SOFT,
        alignment=enums.TA_CENTER)
    ps["title_author"] = ParagraphStyle(
        "title_author", fontName=sans, fontSize=t.body_size * 1.27,
        leading=17, textColor=t.INK, alignment=enums.TA_CENTER)
    ps["halftitle"] = ParagraphStyle(
        "halftitle", fontName=serif, fontSize=t.body_size * 1.5, leading=21,
        textColor=t.INK_SOFT, alignment=enums.TA_CENTER)
    ps["imprint"] = ParagraphStyle(
        "imprint", fontName=serif, fontSize=t.body_size * 0.79,
        leading=t.body_size * 1.1, textColor=t.INK_SOFT,
        alignment=enums.TA_CENTER)
    ps["dedication"] = ParagraphStyle(
        "dedication", fontName=f"{serif}-Italic", fontSize=t.body_size * 1.09,
        leading=t.body_size * 1.6, textColor=t.INK, alignment=enums.TA_CENTER)
    ps["epigraph"] = ParagraphStyle(
        "epigraph", fontName=f"{serif}-Italic", fontSize=t.body_size * 1.22,
        leading=t.body_size * 1.7, textColor=t.INK, alignment=enums.TA_CENTER)
    ps["front_title"] = ParagraphStyle(
        "front_title", fontName=f"{serif}-Bold", fontSize=t.body_size * 1.84,
        leading=t.body_size * 2.17, textColor=t.INK)
    return ps


def toc_styles(theme: Theme):
    t = theme
    return [
        ParagraphStyle("toc_part", fontName=f"{t.sans_family}-Bold",
                       fontSize=t.body_size * 0.85, leading=13.5,
                       textColor=t.INK, spaceBefore=11, spaceAfter=2.5),
        ParagraphStyle("toc_chap", fontName=t.serif_family,
                       fontSize=t.body_size * 0.94, leading=13.6,
                       textColor=t.INK, leftIndent=6.5 * mm,
                       firstLineIndent=-6.5 * mm, spaceBefore=1.2),
        ParagraphStyle("toc_front", fontName=f"{t.serif_family}-Italic",
                       fontSize=t.body_size * 0.94, leading=13.6,
                       textColor=t.INK_SOFT, spaceBefore=3),
    ]


def toc_paragraph(text, style, level, key):
    p = Paragraph(text, style)
    p.toc_entry = (level, key)
    return p


# ------------------------------------------------------------- rendering ---

def _flatten(items):
    out = []
    for it in items:
        out.extend(_flatten(it)) if isinstance(it, list) else out.append(it)
    return out


def make_table(block: Block, ps, theme: Theme):
    rows = block.rows
    ncols = max(len(r) for r in rows)
    data = []
    for i, row in enumerate(rows):
        cells = list(row) + [""] * (ncols - len(row))
        style = ps["cell_head"] if i == 0 else ps["cell"]
        data.append([Paragraph(to_rl(c, theme), style) for c in cells])

    weights = []
    for c in range(ncols):
        longest = max(len(rows[r][c]) if c < len(rows[r]) else 0
                      for r in range(len(rows)))
        weights.append(max(longest, 6) ** 0.72)
    total = sum(weights)
    widths = [theme.frame_w * w / total for w in weights]

    t = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT",
              splitByRow=1)
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), theme.serif_family),
        ("BACKGROUND", (0, 0), (-1, 0), theme.TABLE_HEAD),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, theme.RULE),
        ("LINEABOVE", (0, 0), (-1, 0), 0.6, theme.RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, theme.RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 3.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for r in range(2, len(data), 2):
        style.append(("BACKGROUND", (0, r), (-1, r), theme.TABLE_ZEBRA))
    t.setStyle(TableStyle(style))
    # A table starting in the last centimetres of a page leaves its header
    # stranded: push it to the next one.
    return [Spacer(1, 6), CondPageBreak(34 * mm), t, Spacer(1, 8)]


def blocks_to_flowables(blocks: List[Block], ps, theme, first_style="body_first",
                        body_style="body"):
    out, pending = [], []
    prev = None
    no_indent_after = (None, "h2", "h3", "h4", "rule", "bullet", "number",
                       "table", "quote", "gap")

    for b in blocks:
        if b.kind in ("h2", "h3", "h4"):
            pending.append(Paragraph(to_rl(b.text, theme), ps[b.kind]))
            prev = b.kind
            continue

        flow = None
        if b.kind == "para":
            if prev in no_indent_after:
                style = ps[first_style] if prev in (None, "h2", "h3", "h4") \
                    else ps["body_first_after"]
            else:
                style = ps[body_style]
            flow = Paragraph(to_rl(b.text, theme), style)
        elif b.kind == "bullet":
            flow = Paragraph(to_rl(b.text, theme), ps["bullet"], bulletText="•")
        elif b.kind == "number":
            flow = Paragraph(to_rl(b.text, theme), ps["number"],
                             bulletText=f"{b.level}.")
        elif b.kind == "quote":
            flow = Paragraph(to_rl(b.text, theme), ps["quote"])
        elif b.kind == "table":
            flow = make_table(b, ps, theme)
        elif b.kind == "rule":
            flow = Ornament(theme.RULE)
        elif b.kind == "gap":
            flow = Spacer(1, 7 * mm)
        elif b.kind == "reference":
            flow = Paragraph(to_rl(b.text, theme), ps["reference"])

        if flow is None:
            continue
        flow = _flatten([flow])
        if pending:
            out.append(KeepTogether(pending + flow[:1]))
            out.extend(flow[1:])
            pending = []
        else:
            out.extend(flow)
        prev = b.kind

    out.extend(pending)
    return out


# ----------------------------------------------------------- front matter ---

def colophon_lines(cfg: Config) -> List[str]:
    if cfg.colophon_lines:
        return list(cfg.colophon_lines)
    L = cfg.labels
    lines = [cfg.title]
    if cfg.subtitle:
        lines.append(cfg.subtitle)
    lines.append("")
    if cfg.author:
        lines.append(f"© {cfg.year or ''} {cfg.author}".strip())
    lines.append(cfg.rights or L.get("all_rights", "All rights reserved."))
    lines.append("")
    if cfg.edition:
        lines.append(cfg.edition)
    if cfg.isbn:
        lines.append(f"ISBN {cfg.isbn}")
    if cfg.publisher:
        lines += ["", cfg.publisher]
    return lines


def front_matter(book: Book, ps, theme: Theme, cfg: Config):
    s = []
    fh = theme.frame_h

    s.append(Spacer(1, fh * 0.33))
    s.append(Paragraph(book.title, ps["halftitle"]))
    s.append(PageBreak())
    s.append(Spacer(1, 2))
    s.append(PageBreak())

    s.append(Spacer(1, fh * 0.20))
    if book.author:
        s.append(Paragraph(book.author.upper(), ps["title_author"]))
        s.append(Spacer(1, 16 * mm))
    s.append(Paragraph(book.title, ps["title_main"]))
    if book.subtitle:
        s.append(Spacer(1, 5 * mm))
        s.append(Rule(width=34 * mm, thickness=0.8, space=4, color=theme.RULE))
        s.append(Spacer(1, 5 * mm))
        s.append(Paragraph(book.subtitle, ps["title_sub"]))
    s.append(Spacer(1, fh * 0.20))
    if cfg.publisher:
        s.append(Paragraph(cfg.publisher, ps["imprint"]))
    s.append(PageBreak())

    s.append(Spacer(1, fh * 0.42))
    for line in colophon_lines(cfg):
        s.append(Paragraph(to_rl(line, theme), ps["imprint"]) if line
                 else Spacer(1, 6))
    s.append(PageBreak())

    if cfg.dedication or cfg.epigraph:
        s.append(Spacer(1, fh * 0.30))
        for line in (cfg.dedication or "").split("\n"):
            if line.strip():
                s.append(Paragraph(line.strip(), ps["dedication"]))
        if cfg.dedication and cfg.epigraph:
            s.append(Spacer(1, 26 * mm))
            s.append(Ornament(theme.RULE, size=3.4, space=4, triple=True))
            s.append(Spacer(1, 12 * mm))
        if cfg.epigraph:
            quote = cfg.epigraph
            if not quote.startswith(("«", "“", '"')):
                quote = f"«{quote}»" if cfg.language.startswith("it") \
                    else f"“{quote}”"
            s.append(Paragraph(quote, ps["epigraph"]))
            if cfg.epigraph_source:
                s.append(Spacer(1, 4 * mm))
                s.append(Paragraph(cfg.epigraph_source, ps["imprint"]))
        s.append(OddPageBreak())
    return s


def toc_flowables(book, ps, theme, doc, cfg):
    toc = TableOfContents()
    toc.levelStyles = toc_styles(theme)
    toc.dotsMinLevel = 1
    toc.rightColumnWidth = 14 * mm
    toc.formatter = doc.folio_for_toc          # printed folio, not sheet number
    toc.tableStyle = TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, 0), (-1, -1), theme.serif_family),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ])
    return [Paragraph(cfg.label("contents", "Indice"), ps["front_title"]),
            Rule(width=theme.frame_w, thickness=0.8, space=7, align="left",
                 color=theme.RULE),
            Spacer(1, 4 * mm), toc]


def section_flowables(section: Section, ps, theme, level=2):
    s = [SectionState(section.title, numbered=True, opening=True),
         Spacer(1, theme.frame_h * 0.16)]
    if section.in_toc:
        s.append(toc_paragraph(section.title, ps["front_title"], level,
                               section.title))
    else:
        s.append(Paragraph(section.title, ps["front_title"]))
    if section.subtitle:
        s.append(Spacer(1, 2 * mm))
        s.append(Paragraph(section.subtitle, ps["chapter_sub"]))
    s.append(Rule(width=theme.frame_w, thickness=0.8, space=8, align="left",
                 color=theme.RULE))
    s.append(Spacer(1, 4 * mm))
    s.extend(_flatten(blocks_to_flowables(section.blocks, ps, theme)))
    return s


def part_flowables(part: Part, ps, theme, cfg):
    label = f"{cfg.label('part', 'Parte')} {part.roman}".strip()
    s = [SectionState(label, numbered=False),
         Spacer(1, theme.frame_h * 0.22),
         toc_paragraph(label, ps["part_label"], 0,
                       f"{label.upper()} — {part.title}" if part.title
                       else label.upper()),
         Spacer(1, 3 * mm),
         Paragraph(part.title, ps["part_title"]),
         Spacer(1, 6 * mm),
         Rule(width=26 * mm, thickness=0.8, space=4, color=theme.RULE),
         Spacer(1, 9 * mm)]
    s.extend(_flatten(blocks_to_flowables(part.intro, ps, theme,
                                          first_style="part_body",
                                          body_style="part_body")))
    return s


def chapter_flowables(chapter: Chapter, ps, theme, cfg):
    label = f"{cfg.label('chapter', 'Capitolo')} {chapter.number}".upper()
    s = [SectionState(chapter.title, numbered=True, opening=True),
         Spacer(1, theme.frame_h * 0.13),
         Paragraph(label, ps["chapter_label"]),
         Rule(width=theme.frame_w, thickness=0.7, space=5, align="left",
              color=theme.RULE),
         toc_paragraph(chapter.title, ps["chapter_title"], 1,
                       f"{chapter.number}. {chapter.title}")]
    if chapter.subtitle:
        s.append(Paragraph(chapter.subtitle, ps["chapter_sub"]))
    s.append(Spacer(1, 9 * mm))
    s.extend(_flatten(blocks_to_flowables(chapter.blocks, ps, theme)))
    return s


# ------------------------------------------------------------------ build ---

def build_pdf(book: Book, path: Path, cfg: Config,
              cover: Optional[Path] = None,
              interior_copy: Optional[Path] = None) -> dict:
    theme = register_fonts(theme_from_config(cfg), cfg)
    ps = build_styles(theme)

    doc = BookDocTemplate(
        str(path), book, theme,
        pagesize=(theme.page_w, theme.page_h),
        title=f"{book.title} — {book.subtitle}" if book.subtitle else book.title,
        author=book.author,
        subject=cfg.description or "",
        creator="bookgen",
        leftMargin=theme.margin_inner, rightMargin=theme.margin_outer,
        topMargin=theme.margin_top, bottomMargin=theme.margin_bottom)

    story = [NextPageTemplate(["verso", "recto"])]
    story += front_matter(book, ps, theme, cfg)

    story.append(SectionState(cfg.label("contents", "Indice"), numbered=True,
                              opening=True))
    story += toc_flowables(book, ps, theme, doc, cfg)

    for section in book.front:
        story.append(OddPageBreak())
        story += section_flowables(section, ps, theme)

    multipart = len(book.parts) > 1 or bool(book.parts[0].title)
    for i, part in enumerate(book.parts):
        story.append(OddPageBreak())
        if i == 0:
            story.append(MarkBodyStart())
        if multipart:
            story += part_flowables(part, ps, theme, cfg)
        for j, chapter in enumerate(part.chapters):
            if multipart or j or i:
                story.append(OddPageBreak())
            story += chapter_flowables(chapter, ps, theme, cfg)

    for section in book.back:
        story.append(OddPageBreak())
        story += section_flowables(section, ps, theme)

    story += closing_note(book, ps, theme, cfg)

    doc.multiBuild(story)
    pages = doc.page

    if cfg.page.pad_to_even:
        pages = _pad_to_even(path, pages, theme)
    if interior_copy:
        import shutil
        shutil.copyfile(path, interior_copy)
    if cover and Path(cover).exists():
        pages = prepend_cover(path, cover, theme)

    return {"pages": pages, "fonts": theme.font_report,
            "trim_mm": (cfg.page.width_mm, cfg.page.height_mm)}


def closing_note(book, ps, theme, cfg):
    L = cfg.labels
    s = [OddPageBreak(), SectionState("", numbered=False),
         Spacer(1, theme.frame_h * 0.72),
         Ornament(theme.RULE, size=3.2, space=6, triple=True),
         Spacer(1, 8 * mm)]
    lines = [book.title]
    if book.author:
        lines.append(f"{L.get('by', 'di')} {book.author}")
    lines.append("")
    if cfg.edition:
        lines.append(f"{cfg.edition}{' — ' + cfg.year if cfg.year else ''}")
    fonts = [f.split(": ", 1)[-1].split(" (")[0] for f in theme.font_report]
    if fonts and all(f for f in fonts):
        lines.append(" · ".join(dict.fromkeys(fonts)))
    for line in lines:
        s.append(Paragraph(line, ps["imprint"]) if line
                 else Spacer(1, 6))
    return s


def _pad_to_even(path: Path, pages: int, theme: Theme) -> int:
    """Perfect binding needs an even number of sides."""
    if pages % 2 == 0:
        return pages
    import fitz
    doc = fitz.open(str(path))
    doc.new_page(width=theme.page_w, height=theme.page_h)
    doc.saveIncr()
    doc.close()
    return pages + 1


def prepend_cover(path: Path, cover_image: Path, theme: Theme) -> int:
    """Put the cover first, followed by its blank verso.

    Two sides, not one: with a single extra page every recto would become a
    verso and the mirrored margins would fall on the wrong side of the fold.
    """
    import fitz
    interior = fitz.open(str(path))
    out = fitz.open()
    page = out.new_page(width=theme.page_w, height=theme.page_h)
    page.insert_image(fitz.Rect(0, 0, theme.page_w, theme.page_h),
                      filename=str(cover_image), keep_proportion=False)
    out.new_page(width=theme.page_w, height=theme.page_h)
    out.insert_pdf(interior)
    out.set_metadata(interior.metadata)

    tmp = Path(str(path) + ".tmp")
    out.save(str(tmp), garbage=3, deflate=True)
    out.close()
    interior.close()
    tmp.replace(path)
    doc = fitz.open(str(path))
    n = doc.page_count
    doc.close()
    return n
