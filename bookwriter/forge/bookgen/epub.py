"""EPUB 3 export, accepted by Amazon KDP and by every current reader.

The text reflows: no fixed measures, no embedded fonts, sizes in em. The cover
is declared both as an OPF property and as an XHTML page, which is what the
Amazon converters look for.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from .inline import to_html
from .model import Block, Book, Chapter, Part, Section
from .pdf import colophon_lines

CSS = """@charset "utf-8";

body { margin: 0 5%; padding: 0; font-family: Georgia, "Times New Roman", serif;
       line-height: 1.45; text-align: justify; widows: 2; orphans: 2; }

h1, h2, h3, h4 { font-weight: bold; text-align: left; page-break-after: avoid;
                 -webkit-hyphens: none; hyphens: none; adobe-hyphenate: none; }

h1.chapter { font-size: 1.6em; line-height: 1.2; margin: 0 0 0.1em 0; }
p.chapter-label { font-size: 0.75em; letter-spacing: 0.16em;
                  text-transform: uppercase; color: #666;
                  margin: 0 0 0.6em 0; text-align: left; text-indent: 0;
                  border-bottom: 1px solid #ccc; padding-bottom: 0.4em; }
p.chapter-sub { font-size: 1.05em; font-style: italic; color: #444;
                margin: 0.2em 0 1.6em 0; text-align: left; text-indent: 0; }

h2 { font-size: 1.18em; margin: 1.6em 0 0.4em 0; }
h3 { font-size: 1.05em; font-style: italic; margin: 1.3em 0 0.3em 0; color: #333; }
h4 { font-size: 0.92em; font-family: "Helvetica Neue", Arial, sans-serif;
     margin: 1.1em 0 0.25em 0; color: #333; }

p { margin: 0; text-indent: 1.2em; }
p.first, h1 + p, h2 + p, h3 + p, h4 + p, ul + p, ol + p, table + p,
blockquote + p { text-indent: 0; }
p.first { margin-top: 0.3em; }

ul, ol { margin: 0.5em 0 0.6em 1.1em; padding-left: 0.9em; }
li { margin: 0.25em 0; text-align: justify; }

blockquote { margin: 1em 1.2em; font-style: italic; color: #444; }
blockquote p { text-indent: 0; }

table { width: 100%; border-collapse: collapse; margin: 1em 0; font-size: 0.82em; }
th { background: #eee; text-align: left; font-weight: bold; }
th, td { border: 1px solid #bbb; padding: 0.35em 0.45em; vertical-align: top;
         text-align: left; }

p.ornament { text-align: center; color: #999; margin: 1.3em 0; text-indent: 0;
             letter-spacing: 0.5em; }

section.part { text-align: center; }
section.part h1 { text-align: center; font-size: 1.9em; margin: 3em 0 0.2em 0; }
section.part p.part-label { text-align: center; font-size: 0.85em; color: #666;
                            letter-spacing: 0.2em; text-transform: uppercase;
                            text-indent: 0; margin: 0; }
section.part p { text-align: justify; }
section.part hr { width: 18%; margin: 1.2em auto 2em auto; border: 0;
                  border-top: 1px solid #bbb; }

.title-page { text-align: center; margin-top: 22%; }
.title-page h1 { text-align: center; font-size: 2.1em; margin: 0.6em 0 0.3em 0; }
.title-page p { text-indent: 0; text-align: center; }
.title-page .author { font-size: 1.1em; letter-spacing: 0.1em; color: #222; }
.title-page .subtitle { font-style: italic; color: #444; margin-top: 0.8em; }
.title-page .imprint { margin-top: 3em; font-size: 0.85em; color: #666; }

.copyright p { font-size: 0.8em; text-align: center; text-indent: 0;
               color: #444; margin: 0.35em 0; }
.dedication { margin-top: 25%; text-align: center; }
.dedication p { text-align: center; text-indent: 0; font-style: italic; }
.epigraph { margin-top: 2.5em; font-size: 1.05em; }

nav#toc ol { list-style: none; margin: 0; padding: 0; }
nav#toc ol ol { margin-left: 1.1em; }
nav#toc li { margin: 0.35em 0; text-align: left; }
nav#toc a { text-decoration: none; color: #111; }
nav#toc li.part { margin-top: 1.1em; font-variant: small-caps;
                  font-weight: bold; }

p.reference { text-indent: -1.2em; margin: 0 0 0.7em 1.2em; text-align: left;
              font-size: 0.92em; word-wrap: break-word; }
p.reference a { color: #333; text-decoration: none; }

.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; height: auto; }
"""

HEAD = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      lang="{lang}" xml:lang="{lang}">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
"""

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles>
<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles>
</container>
"""


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def doc(title: str, body: str, lang: str) -> str:
    return HEAD.format(title=esc(title), lang=lang) + body + "</body>\n</html>\n"


# -------------------------------------------------------------- rendering ---

def blocks_to_html(blocks: List[Block]) -> str:
    out, list_open, prev = [], None, None
    no_indent_after = (None, "h2", "h3", "h4", "bullet", "number", "table",
                       "rule", "gap", "quote")

    def close_list():
        nonlocal list_open
        if list_open:
            out.append(f"</{list_open}>")
            list_open = None

    for b in blocks:
        if b.kind in ("bullet", "number"):
            want = "ul" if b.kind == "bullet" else "ol"
            if list_open != want:
                close_list()
                out.append(f"<{want}>")
                list_open = want
            out.append(f"<li>{to_html(b.text)}</li>")
            prev = b.kind
            continue
        close_list()

        if b.kind in ("h2", "h3", "h4"):
            out.append(f"<{b.kind}>{to_html(b.text)}</{b.kind}>")
        elif b.kind == "para":
            cls = ' class="first"' if prev in no_indent_after else ""
            out.append(f"<p{cls}>{to_html(b.text)}</p>")
        elif b.kind == "quote":
            out.append(f"<blockquote><p>{to_html(b.text)}</p></blockquote>")
        elif b.kind == "table":
            out.append(table_to_html(b))
        elif b.kind == "rule":
            out.append('<p class="ornament">◆ ◆ ◆</p>')
        elif b.kind == "gap":
            out.append('<p class="ornament">&#160;</p>')
        elif b.kind == "reference":
            out.append(f'<p class="reference">{to_html(b.text)}</p>')
        prev = b.kind

    close_list()
    return "\n".join(out)


def table_to_html(block: Block) -> str:
    rows = block.rows
    ncols = max(len(r) for r in rows)
    parts = ["<table>"]
    for i, row in enumerate(rows):
        cells = list(row) + [""] * (ncols - len(row))
        tag = "th" if i == 0 else "td"
        parts.append("<tr>" + "".join(
            f"<{tag}>{to_html(c, links=False)}</{tag}>" for c in cells) + "</tr>")
    parts.append("</table>")
    return "\n".join(parts)


def chapter_doc(ch: Chapter, cfg: Config) -> str:
    body = ['<section epub:type="chapter">',
            f'<p class="chapter-label">{esc(cfg.label("chapter", "Chapter"))} '
            f'{ch.number}</p>',
            f'<h1 class="chapter">{esc(ch.title)}</h1>']
    if ch.subtitle:
        body.append(f'<p class="chapter-sub">{esc(ch.subtitle)}</p>')
    body.append(blocks_to_html(ch.blocks))
    body.append("</section>")
    return doc(f"{ch.number}. {ch.title}", "\n".join(body), cfg.language)


def part_doc(part: Part, cfg: Config) -> str:
    label = f'{cfg.label("part", "Part")} {part.roman}'.strip()
    body = ['<section class="part" epub:type="part">',
            f'<p class="part-label">{esc(label)}</p>',
            f'<h1>{esc(part.title)}</h1>', "<hr/>",
            blocks_to_html(part.intro), "</section>"]
    return doc(f"{label} — {part.title}", "\n".join(body), cfg.language)


def section_doc(section: Section, cfg: Config) -> str:
    epub_type = {"bibliography": "bibliography", "glossary": "glossary",
                 "introduction": "introduction", "preface": "preface",
                 "acknowledgements": "acknowledgments",
                 "appendix": "appendix"}.get(section.ident, "preamble")
    body = [f'<section epub:type="{epub_type}">',
            f'<h1 class="chapter">{esc(section.title)}</h1>']
    if section.subtitle:
        body.append(f'<p class="chapter-sub">{esc(section.subtitle)}</p>')
    body.append(blocks_to_html(section.blocks))
    body.append("</section>")
    return doc(section.title, "\n".join(body), cfg.language)


def title_doc(book: Book, cfg: Config) -> str:
    body = ['<section class="title-page" epub:type="titlepage">']
    if book.author:
        body.append(f'<p class="author">{esc(book.author.upper())}</p>')
    body.append(f"<h1>{esc(book.title)}</h1>")
    if book.subtitle:
        body.append(f'<p class="subtitle">{esc(book.subtitle)}</p>')
    if cfg.publisher:
        body.append(f'<p class="imprint">{esc(cfg.publisher)}</p>')
    body.append("</section>")
    return doc(book.title, "\n".join(body), cfg.language)


def copyright_doc(cfg: Config) -> str:
    lines = "\n".join(f"<p>{to_html(l)}</p>" if l else "<p>&#160;</p>"
                      for l in colophon_lines(cfg))
    return doc("Copyright",
               f'<section class="copyright" epub:type="copyright-page">\n'
               f"{lines}\n</section>", cfg.language)


def dedication_doc(cfg: Config) -> str:
    body = ['<section class="dedication" epub:type="dedication">']
    for line in (cfg.dedication or "").split("\n"):
        if line.strip():
            body.append(f"<p>{esc(line.strip())}</p>")
    if cfg.dedication and cfg.epigraph:
        body.append('<p class="ornament">◆ ◆ ◆</p>')
    if cfg.epigraph:
        body.append(f'<p class="epigraph">«{esc(cfg.epigraph)}»</p>')
        if cfg.epigraph_source:
            body.append(f'<p class="imprint">{esc(cfg.epigraph_source)}</p>')
    body.append("</section>")
    return doc("Dedica", "\n".join(body), cfg.language)


def cover_doc(cfg: Config) -> str:
    return doc("Cover", '<section class="cover" epub:type="cover">\n'
                        '<img src="cover.jpg" alt="Cover"/>\n</section>',
               cfg.language)


def nav_doc(items: List[Dict], cfg: Config, present: set) -> str:
    lines = [f'<nav epub:type="toc" id="toc">'
             f'<h1 class="chapter">{esc(cfg.label("contents", "Contents"))}</h1>',
             "<ol>"]
    for it in items:
        if it["kind"] == "part":
            lines.append(f'<li class="part"><a href="{it["href"]}">'
                         f'{esc(it["label"])}</a>')
            lines.append("<ol>")
        elif it["kind"] == "end-part":
            lines.append("</ol></li>")
        else:
            lines.append(f'<li><a href="{it["href"]}">{esc(it["label"])}</a></li>')
    lines.append("</ol></nav>")

    start = next((i["href"] for i in items if i["kind"] in ("part", "chapter")),
                 "title.xhtml")
    # only point at pages that were actually written
    marks = []
    if "cover.xhtml" in present:
        marks.append('<li><a epub:type="cover" href="cover.xhtml">Cover</a></li>')
    if "title.xhtml" in present:
        marks.append('<li><a epub:type="titlepage" href="title.xhtml">'
                     'Title page</a></li>')
    if start in present:
        marks.append(f'<li><a epub:type="bodymatter" href="{start}">'
                     f'Start of content</a></li>')
    landmarks = ("""<nav epub:type="landmarks" hidden="hidden">
<h2>Landmarks</h2>
<ol>
""" + "\n".join(marks) + """
</ol>
</nav>""") if marks else ""
    return doc(cfg.label("contents", "Contents"),
               "\n".join(lines) + "\n" + landmarks, cfg.language)


def ncx_doc(book: Book, items: List[Dict], uid: str) -> str:
    points, n = [], 0
    for it in items:
        if it["kind"] == "end-part":
            continue
        n += 1
        points.append(f"""<navPoint id="np{n}" playOrder="{n}">
<navLabel><text>{esc(it['label'])}</text></navLabel>
<content src="{it['href']}"/>
</navPoint>""")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head>
<meta name="dtb:uid" content="{uid}"/>
<meta name="dtb:depth" content="2"/>
<meta name="dtb:totalPageCount" content="0"/>
<meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>{esc(book.title)}</text></docTitle>
<navMap>
{"".join(points)}
</navMap>
</ncx>
"""


def _file_as(name: str) -> str:
    parts = name.split()
    return f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else name


def opf_doc(book: Book, cfg: Config, manifest: List[Dict], spine: List[str],
            uid: str) -> str:
    items = "\n".join(
        f'<item id="{i["id"]}" href="{i["href"]}" media-type="{i["type"]}"'
        + (f' properties="{i["properties"]}"' if i.get("properties") else "")
        + "/>" for i in manifest)
    refs = "\n".join(f'<itemref idref="{s}"/>' for s in spine)
    subjects = "\n".join(f"<dc:subject>{esc(k)}</dc:subject>"
                         for k in cfg.keywords)
    date = f"{cfg.year}-01-01" if cfg.year else "2026-01-01"
    identifier = f"urn:isbn:{cfg.isbn}" if cfg.isbn else f"urn:uuid:{uid}"
    subtitle = (f'<dc:title id="title-sub">{esc(book.subtitle)}</dc:title>\n'
                f'<meta refines="#title-sub" property="title-type">subtitle</meta>'
                if book.subtitle else "")
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0"
         unique-identifier="bookid" xml:lang="{cfg.language}">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{identifier}</dc:identifier>
<dc:title id="title-main">{esc(book.title)}</dc:title>
{subtitle}
<dc:creator id="author">{esc(book.author)}</dc:creator>
<dc:language>{cfg.language}</dc:language>
<dc:publisher>{esc(cfg.publisher)}</dc:publisher>
<dc:date>{date}</dc:date>
<dc:description>{esc(cfg.description)}</dc:description>
{subjects}
<dc:rights>{esc(cfg.rights or '')}</dc:rights>
<meta property="dcterms:modified">{date}T00:00:00Z</meta>
<meta refines="#title-main" property="title-type">main</meta>
<meta refines="#author" property="role" scheme="marc:relators">aut</meta>
<meta refines="#author" property="file-as">{esc(_file_as(book.author))}</meta>
<meta name="cover" content="cover-image"/>
</metadata>
<manifest>
{items}
</manifest>
<spine toc="ncx">
{refs}
</spine>
</package>
"""


# ------------------------------------------------------------------ build ---

def build_epub(book: Book, path: Path, cfg: Config,
               cover: Optional[Path] = None) -> Path:
    seed = f"{book.title}|{book.author}|{cfg.isbn}".encode()
    h = hashlib.sha1(seed).hexdigest()
    uid = f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    files: Dict[str, object] = {}
    manifest: List[Dict] = []
    spine: List[str] = []
    nav_items: List[Dict] = []

    def add(name, content, ident=None, mtype="application/xhtml+xml",
            properties=None, in_spine=True):
        ident = ident or name.split(".")[0].replace("_", "-")
        files[f"OEBPS/{name}"] = content
        entry = {"id": ident, "href": name, "type": mtype}
        if properties:
            entry["properties"] = properties
        manifest.append(entry)
        if in_spine:
            spine.append(ident)
        return ident

    if cover and Path(cover).exists():
        data = Path(cover).read_bytes()
        mtype = "image/jpeg" if Path(cover).suffix.lower() in (".jpg", ".jpeg") \
            else "image/png"
        files["OEBPS/cover.jpg"] = data
        manifest.append({"id": "cover-image", "href": "cover.jpg",
                         "type": mtype, "properties": "cover-image"})
        add("cover.xhtml", cover_doc(cfg), ident="cover-page")

    add("title.xhtml", title_doc(book, cfg), ident="titlepage")
    add("copyright.xhtml", copyright_doc(cfg), ident="copyright")
    if cfg.dedication or cfg.epigraph:
        add("dedication.xhtml", dedication_doc(cfg), ident="dedication")

    files["OEBPS/nav.xhtml"] = ""          # replaced once the items are known
    manifest.append({"id": "nav", "href": "nav.xhtml",
                     "type": "application/xhtml+xml", "properties": "nav"})
    spine.append("nav")

    for fs in book.front:
        name = f"{fs.ident}.xhtml"
        add(name, section_doc(fs, cfg))
        nav_items.append({"kind": "front", "href": name, "label": fs.title})

    multipart = len(book.parts) > 1 or bool(book.parts[0].title)
    for part in book.parts:
        if multipart:
            pname = f"part-{part.number}.xhtml"
            add(pname, part_doc(part, cfg), ident=f"part-{part.number}")
            nav_items.append({
                "kind": "part", "href": pname,
                "label": f"{cfg.label('part', 'Part')} {part.roman} — "
                         f"{part.title}"})
        for ch in part.chapters:
            cname = f"cap-{ch.number:02d}.xhtml"
            add(cname, chapter_doc(ch, cfg), ident=f"cap-{ch.number:02d}")
            nav_items.append({"kind": "chapter", "href": cname,
                              "label": f"{ch.number}. {ch.title}"})
        if multipart:
            nav_items.append({"kind": "end-part"})

    for fs in book.back:
        name = f"{fs.ident}.xhtml"
        add(name, section_doc(fs, cfg))
        nav_items.append({"kind": "back", "href": name, "label": fs.title})

    written = {n[len("OEBPS/"):] for n in files if n.startswith("OEBPS/")}
    files["OEBPS/nav.xhtml"] = nav_doc(nav_items, cfg, written)
    files["OEBPS/toc.ncx"] = ncx_doc(book, nav_items, uid)
    manifest.append({"id": "ncx", "href": "toc.ncx",
                     "type": "application/x-dtbncx+xml"})
    files["OEBPS/style.css"] = CSS
    manifest.append({"id": "css", "href": "style.css", "type": "text/css"})

    files["OEBPS/content.opf"] = opf_doc(book, cfg, manifest, spine, uid)
    files["META-INF/container.xml"] = CONTAINER

    if path.exists():
        path.unlink()
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        for name, data in files.items():
            if isinstance(data, str):
                data = data.encode("utf-8")
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return path
