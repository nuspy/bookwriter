"""Pre-flight checks on the generated files.

These are the mistakes that are cheap to make and expensive to discover after
the print run: an odd page count, a font that was never embedded, text sitting
too close to the trim, a chapter opening on a verso, an EPUB whose manifest
does not match its files.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from xml.etree import ElementTree as ET

MM = 72 / 25.4
SAFE_MARGIN_MM = 6.4          # KDP minimum distance from the trim edge


@dataclass
class Finding:
    level: str                # "error" | "warning" | "note"
    where: str
    message: str


@dataclass
class Report:
    findings: List[Finding] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)

    def add(self, level, where, message):
        self.findings.append(Finding(level, where, message))

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self):
        return [f for f in self.findings if f.level == "warning"]

    def ok(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = []
        for fact in self.facts:
            lines.append(f"  · {fact}")
        for f in self.findings:
            mark = {"error": "ERROR  ", "warning": "WARNING",
                    "note": "NOTE   "}[f.level]
            lines.append(f"  {mark} [{f.where}] {f.message}")
        if not self.findings:
            lines.append("  no issues found")
        return "\n".join(lines)


# ------------------------------------------------------------------- PDF ---

def check_pdf(path: Path, trim_mm=None, expect_recto_chapters=True,
              report: Optional[Report] = None, opener_labels=None) -> Report:
    report = report or Report()
    try:
        import fitz
    except ImportError:
        report.add("warning", path.name,
                   "PyMuPDF not installed: PDF checks skipped")
        return report

    doc = fitz.open(str(path))
    pages = doc.page_count
    report.facts.append(f"{path.name}: {pages} pages")

    if pages % 2:
        report.add("warning", path.name,
                   f"{pages} sides: perfect binding needs an even count")

    if trim_mm:
        want = (round(trim_mm[0] * MM, 1), round(trim_mm[1] * MM, 1))
        got = (round(doc[0].rect.width, 1), round(doc[0].rect.height, 1))
        if abs(got[0] - want[0]) > 1.5 or abs(got[1] - want[1]) > 1.5:
            report.add("error", path.name,
                       f"page size {got} pt differs from the requested trim "
                       f"{want} pt")
        sizes = {(round(p.rect.width), round(p.rect.height)) for p in doc}
        if len(sizes) > 1:
            report.add("error", path.name,
                       f"mixed page sizes in one file: {sorted(sizes)}")

    not_embedded = set()
    for page in doc:
        for f in page.get_fonts(full=True):
            name, ftype = f[3], f[2]
            if ftype in ("Type1", "TrueType") and "+" not in name:
                not_embedded.add(name)
    if not_embedded:
        report.add("warning", path.name,
                   f"fonts declared but not embedded: "
                   f"{', '.join(sorted(not_embedded))}. Most printers reject "
                   f"these; the base-14 names are usually harmless but "
                   f"preflight will flag them.")

    safe = SAFE_MARGIN_MM * MM
    tight = []
    for i, page in enumerate(doc, 1):
        w, h = page.rect.width, page.rect.height
        for b in page.get_text("blocks"):
            x0, y0, x1, y1 = b[:4]
            if x0 < safe - 1 or y0 < safe - 1 or x1 > w - safe + 1 \
                    or y1 > h - safe + 1:
                tight.append(i)
                break
    if tight:
        report.add("warning", path.name,
                   f"text closer than {SAFE_MARGIN_MM} mm to the trim on "
                   f"{len(tight)} page(s), first at {tight[0]}")

    if expect_recto_chapters:
        # The opening label is set in capitals, which distinguishes it from a
        # running heading such as "Capitolo 12 — Recap" inside the text.
        #
        # The label itself is named rather than matched as "any short capitalised
        # word": a one-word title — LIBERTÀ, ORIGINI — is set in capitals in the
        # verso running head, right above the folio, and used to trip this check
        # on every left-hand page of a perfectly sound book.
        etichette = [re.escape(l.upper()) for l in
                     (opener_labels or ("CAPITOLO", "CHAPTER", "PARTE", "PART",
                                        "CAPÍTULO", "CHAPITRE", "KAPITEL"))]
        opener = re.compile(rf"^\s*(?:{'|'.join(etichette)})\s+[\dIVXLC]+\s*$",
                            re.MULTILINE | re.IGNORECASE)
        wrong = [i for i, p in enumerate(doc, 1)
                 if opener.match(p.get_text()[:40].strip()) and i % 2 == 0]
        if wrong:
            report.add("warning", path.name,
                       f"chapter opening on a verso at page(s) "
                       f"{wrong[:5]}: check whether a cover was prepended "
                       f"without its blank back")

    doc.close()
    return report


# ------------------------------------------------------------------ EPUB ---

def check_epub(path: Path, report: Optional[Report] = None) -> Report:
    report = report or Report()
    z = zipfile.ZipFile(path)
    names = z.namelist()
    report.facts.append(f"{path.name}: {len(names)} entries")

    if names[0] != "mimetype":
        report.add("error", path.name, "mimetype is not the first entry")
    elif z.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
        report.add("error", path.name, "mimetype must be stored uncompressed")
    if z.read("mimetype").decode().strip() != "application/epub+zip":
        report.add("error", path.name, "wrong mimetype content")

    if "META-INF/container.xml" not in names:
        report.add("error", path.name, "META-INF/container.xml is missing")
        return report

    for n in names:
        if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
            try:
                ET.fromstring(z.read(n))
            except ET.ParseError as e:
                report.add("error", n, f"malformed XML: {e}")

    root = ET.fromstring(z.read("META-INF/container.xml"))
    ns_c = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    rootfile = root.find(".//c:rootfile", ns_c)
    opf_path = rootfile.get("full-path") if rootfile is not None else None
    if not opf_path or opf_path not in names:
        report.add("error", path.name, "the OPF referenced by container.xml "
                                       "is missing")
        return report

    opf = ET.fromstring(z.read(opf_path))
    ns = {"opf": "http://www.idpf.org/2007/opf",
          "dc": "http://purl.org/dc/elements/1.1/"}
    base = opf_path.rsplit("/", 1)[0]
    manifest = {i.get("id"): i.get("href")
                for i in opf.findall(".//opf:manifest/opf:item", ns)}
    present = {n[len(base) + 1:] for n in names if n.startswith(base + "/")}

    missing = [h for h in manifest.values() if h not in present]
    if missing:
        report.add("error", path.name,
                   f"manifest lists files that are not in the archive: "
                   f"{missing[:5]}")
    undeclared = [n for n in present
                  if n not in manifest.values() and n != opf_path.split("/")[-1]]
    if undeclared:
        report.add("warning", path.name,
                   f"files present but not declared in the manifest: "
                   f"{undeclared[:5]}")

    spine = [i.get("idref") for i in opf.findall(".//opf:spine/opf:itemref", ns)]
    unresolved = [s for s in spine if s not in manifest]
    if unresolved:
        report.add("error", path.name, f"spine references unknown ids: "
                                       f"{unresolved}")
    if not spine:
        report.add("error", path.name, "the spine is empty")

    if not any(i.get("properties") == "nav"
               for i in opf.findall(".//opf:manifest/opf:item", ns)):
        report.add("error", path.name, "no navigation document (EPUB 3 needs "
                                       "an item with properties=\"nav\")")
    if not any((i.get("properties") or "").find("cover-image") >= 0
               for i in opf.findall(".//opf:manifest/opf:item", ns)):
        report.add("warning", path.name,
                   "no cover image declared: the store will show a placeholder")

    for tag, label in (("dc:title", "title"), ("dc:creator", "author"),
                       ("dc:language", "language"),
                       ("dc:identifier", "identifier")):
        if opf.find(f".//{tag}", ns) is None:
            report.add("error", path.name, f"missing metadata: {label}")

    nav = next((h for i, h in manifest.items()
                if h.endswith("nav.xhtml")), None)
    if nav:
        content = z.read(f"{base}/{nav}").decode("utf-8", "replace")
        hrefs = set(re.findall(r'href="([^"#]+)', content))
        broken = [h for h in hrefs if h not in present]
        if broken:
            report.add("error", path.name, f"broken links in the navigation: "
                                           f"{broken[:5]}")
    z.close()
    return report


# ----------------------------------------------------------------- DOCX ---

def check_docx(path: Path, report: Optional[Report] = None) -> Report:
    report = report or Report()
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        report.add("error", path.name, "not a valid DOCX archive")
        return report
    names = z.namelist()
    report.facts.append(f"{path.name}: {len(names)} parts")
    if "word/document.xml" not in names:
        report.add("error", path.name, "word/document.xml is missing")
        return report
    doc = z.read("word/document.xml").decode("utf-8", "replace")
    if "TOC \\o" not in doc:
        report.add("warning", path.name, "no TOC field: the contents page will "
                                         "not update in Word")
    # STYLEREF lives in the headers, not in the body
    headers = "".join(z.read(n).decode("utf-8", "replace") for n in names
                      if n.startswith("word/header"))
    if "STYLEREF" not in headers:
        report.add("note", path.name, "no STYLEREF field: running heads will "
                                      "not follow the chapters")
    settings = ("word/settings.xml" in names
                and z.read("word/settings.xml").decode("utf-8", "replace"))
    if settings and "mirrorMargins" not in settings:
        report.add("warning", path.name, "mirror margins are off: the gutter "
                                         "will fall on the same side on every "
                                         "page")
    z.close()
    return report


def check_all(cfg, results: dict) -> Report:
    report = Report()
    trim = (cfg.page.width_mm, cfg.page.height_mm)
    for kind, path in results.items():
        path = Path(path)
        if not path.exists():
            report.add("error", path.name, "file not produced")
            continue
        if path.suffix == ".pdf":
            check_pdf(path, trim, report=report,
                      opener_labels=[cfg.label("chapter", "Capitolo"),
                                     cfg.label("part", "Parte")])
        elif path.suffix == ".epub":
            check_epub(path, report=report)
        elif path.suffix == ".docx":
            check_docx(path, report=report)
    return report
