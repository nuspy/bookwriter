"""Project configuration: everything that varies from book to book.

A book project is a directory containing `book.yaml` plus the chapter sources.
Nothing about a specific title is hard-coded anywhere else in this package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:                                          # pragma: no cover
    yaml = None

CONFIG_NAMES = ("book.yaml", "book.yml", "book.json")

# --------------------------------------------------------------- presets ---

# Trim sizes actually offered by print-on-demand services, in millimetres.
PAGE_PRESETS: Dict[str, Dict[str, float]] = {
    "a5":        {"width": 148.0, "height": 210.0},
    "a4":        {"width": 210.0, "height": 297.0},
    "b-format":  {"width": 129.0, "height": 198.0},   # UK paperback
    "5x8":       {"width": 127.0, "height": 203.2},
    "5.5x8.5":   {"width": 139.7, "height": 215.9},   # KDP digest
    "6x9":       {"width": 152.4, "height": 228.6},   # KDP trade
    "7x10":      {"width": 177.8, "height": 254.0},
    "8.5x11":    {"width": 215.9, "height": 279.4},   # US letter
    "square-21": {"width": 210.0, "height": 210.0},
}

# Margins scale with the page: these are ratios of the trim size, tuned so that
# the inner margin always exceeds the outer one (the gutter swallows part of it).
DEFAULT_MARGINS = {
    "inner_ratio": 0.122,
    "outer_ratio": 0.101,
    "top_ratio": 0.076,
    "bottom_ratio": 0.086,
}

# Font families, in order of preference. The first one present on the machine
# wins; if none is found the renderer falls back to the PDF base fonts.
FONT_STACKS = {
    "serif": [
        ("EB Garamond", "EBGaramond-Regular.ttf", "EBGaramond-Bold.ttf",
         "EBGaramond-Italic.ttf", "EBGaramond-BoldItalic.ttf"),
        ("Constantia", "constan.ttf", "constanb.ttf", "constani.ttf",
         "constanz.ttf"),
        ("Georgia", "georgia.ttf", "georgiab.ttf", "georgiai.ttf",
         "georgiaz.ttf"),
        ("Palatino Linotype", "pala.ttf", "palab.ttf", "palai.ttf",
         "palabi.ttf"),
        ("Times New Roman", "times.ttf", "timesbd.ttf", "timesi.ttf",
         "timesbi.ttf"),
    ],
    "sans": [
        ("Lato", "Lato-Regular.ttf", "Lato-Bold.ttf", "Lato-Italic.ttf",
         "Lato-BoldItalic.ttf"),
        ("Corbel", "corbel.ttf", "corbelb.ttf", "corbeli.ttf", "corbelz.ttf"),
        ("Candara", "Candara.ttf", "Candarab.ttf", "Candarai.ttf",
         "Candaraz.ttf"),
        ("Calibri", "calibri.ttf", "calibrib.ttf", "calibrii.ttf",
         "calibriz.ttf"),
        ("Arial", "arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    ],
}

ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
         "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"]


# ------------------------------------------------------------ structures ---

@dataclass
class PageSetup:
    width_mm: float = 148.0
    height_mm: float = 210.0
    margin_inner_mm: float = 18.0
    margin_outer_mm: float = 15.0
    margin_top_mm: float = 16.0
    margin_bottom_mm: float = 18.0
    bleed_mm: float = 3.0
    chapters_open_recto: bool = True
    pad_to_even: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PageSetup":
        preset = str(data.get("preset", "a5")).lower()
        base = PAGE_PRESETS.get(preset, PAGE_PRESETS["a5"])
        w = float(data.get("width_mm", base["width"]))
        h = float(data.get("height_mm", base["height"]))
        m = DEFAULT_MARGINS
        return cls(
            width_mm=w,
            height_mm=h,
            margin_inner_mm=float(data.get("margin_inner_mm",
                                           round(w * m["inner_ratio"], 1))),
            margin_outer_mm=float(data.get("margin_outer_mm",
                                           round(w * m["outer_ratio"], 1))),
            margin_top_mm=float(data.get("margin_top_mm",
                                         round(h * m["top_ratio"], 1))),
            margin_bottom_mm=float(data.get("margin_bottom_mm",
                                            round(h * m["bottom_ratio"], 1))),
            bleed_mm=float(data.get("bleed_mm", 3.0)),
            chapters_open_recto=bool(data.get("chapters_open_recto", True)),
            pad_to_even=bool(data.get("pad_to_even", True)),
        )

    @property
    def frame_width_mm(self) -> float:
        return self.width_mm - self.margin_inner_mm - self.margin_outer_mm

    @property
    def frame_height_mm(self) -> float:
        return self.height_mm - self.margin_top_mm - self.margin_bottom_mm


@dataclass
class Typography:
    serif: Optional[str] = None       # family name, or None to auto-select
    sans: Optional[str] = None
    body_size: float = 0.0            # 0 = derive from page width
    leading: float = 0.0
    first_line_indent_mm: float = 5.0
    hyphenation: Optional[str] = None  # e.g. "it_IT"; None disables it
    justify: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any], page: PageSetup,
                  language: str) -> "Typography":
        t = cls(
            serif=data.get("serif"),
            sans=data.get("sans"),
            body_size=float(data.get("body_size", 0) or 0),
            leading=float(data.get("leading", 0) or 0),
            first_line_indent_mm=float(data.get("first_line_indent_mm", 5.0)),
            hyphenation=data.get("hyphenation", "auto"),
            justify=bool(data.get("justify", True)),
        )
        if not t.body_size:
            # ~66 characters per line is the readable optimum; the size that
            # achieves it grows with the measure
            measure = page.frame_width_mm
            t.body_size = round(min(12.0, max(9.2, measure * 0.092)), 1)
        if not t.leading:
            t.leading = round(t.body_size * 1.38, 1)
        if t.hyphenation == "auto":
            t.hyphenation = _hyphen_lang(language)
        return t


def _hyphen_lang(language: str) -> Optional[str]:
    lang = (language or "").lower().replace("-", "_")
    table = {"it": "it_IT", "en": "en_GB", "es": "es", "fr": "fr",
             "de": "de_DE", "pt": "pt_PT", "nl": "nl_NL"}
    if lang in table:
        return table[lang]
    if "_" in lang:
        return lang
    return None


@dataclass
class SourceSpec:
    root: str = "."
    pattern: str = "**/*.md"
    exclude: List[str] = field(default_factory=lambda: ["output/**", "front/**",
                                                        "back/**", "README.md"])
    chapter_regex: str = (r"^#\s+(?:Capitolo|Chapter|Cap\.?|Chap\.?)\s*"
                          r"(\d+)\s*[:\-—–]\s*(.*)$")
    part_regex: str = r"^#\s+(?:PARTE|PART)\s+([IVXLC]+)\s*[—\-:–]?\s*(.*)$"
    title_split: str = "—"
    strip_production_noise: bool = True
    extract_chapter_references: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceSpec":
        d = dict(data or {})
        base = cls()
        for f in ("root", "pattern", "chapter_regex", "part_regex",
                  "title_split"):
            if f in d:
                setattr(base, f, d[f])
        if "exclude" in d:
            base.exclude = list(d["exclude"])
        base.strip_production_noise = bool(d.get("strip_production_noise", True))
        base.extract_chapter_references = bool(
            d.get("extract_chapter_references", True))
        return base


@dataclass
class PartSpec:
    number: int
    title: str
    roman: str = ""
    chapters: List[int] = field(default_factory=list)
    intro_file: Optional[str] = None

    def __post_init__(self):
        if not self.roman:
            self.roman = ROMAN[self.number] if self.number < len(ROMAN) \
                else str(self.number)


@dataclass
class CoverSpec:
    image: Optional[str] = None
    compose_title: bool = True
    tagline: str = ""
    title_color: str = "#D6B266"
    text_color: str = "#EEEAE2"
    band_background: str = "#060607"
    trim_bottom_ratio: float = 0.0
    include_in_pdf: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CoverSpec":
        d = dict(data or {})
        return cls(
            image=d.get("image"),
            compose_title=bool(d.get("compose_title", True)),
            tagline=d.get("tagline", "") or "",
            title_color=d.get("title_color", "#D6B266"),
            text_color=d.get("text_color", "#EEEAE2"),
            band_background=d.get("band_background", "#060607"),
            trim_bottom_ratio=float(d.get("trim_bottom_ratio", 0.0)),
            include_in_pdf=bool(d.get("include_in_pdf", True)),
        )


@dataclass
class OutputSpec:
    directory: str = "output"
    basename: str = ""
    formats: List[str] = field(default_factory=lambda: ["pdf", "docx", "epub", "md"])
    pdf_with_cover: bool = True
    pdf_interior_copy: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any], title: str) -> "OutputSpec":
        d = dict(data or {})
        base = cls()
        base.directory = d.get("directory", "output")
        base.basename = d.get("basename") or _slug(title)
        if "formats" in d:
            base.formats = [f.lower() for f in d["formats"]]
        base.pdf_with_cover = bool(d.get("pdf_with_cover", True))
        base.pdf_interior_copy = bool(d.get("pdf_interior_copy", True))
        return base


def _slug(text: str) -> str:
    out = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    out = re.sub(r"[\s]+", "_", out)
    return out or "book"


@dataclass
class Config:
    path: Path
    root: Path
    title: str
    subtitle: str = ""
    author: str = ""
    language: str = "it"
    publisher: str = ""
    year: str = ""
    isbn: str = ""
    edition: str = ""
    rights: str = ""
    dedication: str = ""
    epigraph: str = ""
    epigraph_source: str = ""
    keywords: List[str] = field(default_factory=list)
    description: str = ""

    page: PageSetup = field(default_factory=PageSetup)
    typography: Typography = field(default_factory=Typography)
    source: SourceSpec = field(default_factory=SourceSpec)
    parts: List[PartSpec] = field(default_factory=list)
    cover: CoverSpec = field(default_factory=CoverSpec)
    output: OutputSpec = field(default_factory=OutputSpec)

    front_matter: List[Dict[str, Any]] = field(default_factory=list)
    back_matter: List[Dict[str, Any]] = field(default_factory=list)
    labels: Dict[str, str] = field(default_factory=dict)
    colophon_lines: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    # -- derived ---------------------------------------------------------

    @property
    def out_dir(self) -> Path:
        d = self.root / self.output.directory
        d.mkdir(parents=True, exist_ok=True)
        return d

    def label(self, key: str, default: str) -> str:
        return self.labels.get(key, default)

    def resolve(self, relative: Optional[str]) -> Optional[Path]:
        if not relative:
            return None
        p = Path(relative)
        return p if p.is_absolute() else (self.root / p)


# ----------------------------------------------------------------- labels ---

# Section names printed in the book, per language. A project can override any
# of them under `labels:` in book.yaml.
LABELS = {
    "it": {
        "contents": "Indice", "part": "Parte", "chapter": "Capitolo",
        "bibliography": "Bibliografia", "introduction": "Introduzione",
        "preface": "Prefazione", "notice": "Avvertenza",
        "appendix": "Appendice", "glossary": "Glossario",
        "acknowledgements": "Ringraziamenti", "about_author": "L’autore",
        "colophon": "Colophon", "by": "di", "edition": "edizione",
        "all_rights": "Tutti i diritti riservati.",
        "printed": "Finito di stampare",
    },
    "en": {
        "contents": "Contents", "part": "Part", "chapter": "Chapter",
        "bibliography": "Bibliography", "introduction": "Introduction",
        "preface": "Preface", "notice": "Notice",
        "appendix": "Appendix", "glossary": "Glossary",
        "acknowledgements": "Acknowledgements", "about_author": "About the author",
        "colophon": "Colophon", "by": "by", "edition": "edition",
        "all_rights": "All rights reserved.",
        "printed": "Printed",
    },
    "es": {
        "contents": "Índice", "part": "Parte", "chapter": "Capítulo",
        "bibliography": "Bibliografía", "introduction": "Introducción",
        "preface": "Prefacio", "notice": "Advertencia",
        "appendix": "Apéndice", "glossary": "Glosario",
        "acknowledgements": "Agradecimientos", "about_author": "El autor",
        "colophon": "Colofón", "by": "de", "edition": "edición",
        "all_rights": "Todos los derechos reservados.",
        "printed": "Impreso",
    },
    "fr": {
        "contents": "Table des matières", "part": "Partie", "chapter": "Chapitre",
        "bibliography": "Bibliographie", "introduction": "Introduction",
        "preface": "Préface", "notice": "Avertissement",
        "appendix": "Annexe", "glossary": "Glossaire",
        "acknowledgements": "Remerciements", "about_author": "L’auteur",
        "colophon": "Colophon", "by": "par", "edition": "édition",
        "all_rights": "Tous droits réservés.",
        "printed": "Achevé d’imprimer",
    },
    "de": {
        "contents": "Inhalt", "part": "Teil", "chapter": "Kapitel",
        "bibliography": "Literaturverzeichnis", "introduction": "Einleitung",
        "preface": "Vorwort", "notice": "Hinweis",
        "appendix": "Anhang", "glossary": "Glossar",
        "acknowledgements": "Danksagung", "about_author": "Über den Autor",
        "colophon": "Kolophon", "by": "von", "edition": "Auflage",
        "all_rights": "Alle Rechte vorbehalten.",
        "printed": "Gedruckt",
    },
}


def labels_for(language: str) -> Dict[str, str]:
    return dict(LABELS.get((language or "it")[:2].lower(), LABELS["en"]))


# ----------------------------------------------------------------- loader ---

def find_config(start: Path) -> Optional[Path]:
    start = Path(start).resolve()
    for name in CONFIG_NAMES:
        candidate = start / name
        if candidate.exists():
            return candidate
    return None


def load(project_dir: Path) -> Config:
    project_dir = Path(project_dir).resolve()
    path = find_config(project_dir)
    if path is None:
        raise FileNotFoundError(
            f"No book.yaml found in {project_dir}. "
            f"Run `bookgen init` there first.")

    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        import json
        data = json.loads(text)
    else:
        if yaml is None:
            raise RuntimeError("PyYAML is required to read book.yaml "
                               "(pip install pyyaml)")
        data = yaml.safe_load(text) or {}

    return from_mapping(data, root=project_dir, path=path)


def from_mapping(data: Dict[str, Any], *, root: Path,
                 path: Optional[Path] = None) -> Config:
    """Build a Config from an already-parsed mapping.

    `load` reads `book.yaml` and calls this. Keeping the two apart lets a
    caller compose a book that has no project directory of its own — a draft
    assembled in memory still needs a trim size, a language and a type scale.
    """
    root = Path(root).resolve()
    data = dict(data or {})
    book = data.get("book", data)
    title = book.get("title") or "Untitled"
    language = str(book.get("language", "it"))

    page = PageSetup.from_dict(data.get("format", {}))
    cfg = Config(
        path=path if path is not None else root / "book.yaml",
        root=root,
        title=title,
        subtitle=book.get("subtitle", "") or "",
        author=book.get("author", "") or "",
        language=language,
        publisher=book.get("publisher", "") or "",
        year=str(book.get("year", "") or ""),
        isbn=book.get("isbn", "") or "",
        edition=book.get("edition", "") or "",
        rights=book.get("rights", "") or "",
        dedication=book.get("dedication", "") or "",
        epigraph=book.get("epigraph", "") or "",
        epigraph_source=book.get("epigraph_source", "") or "",
        keywords=list(book.get("keywords", []) or []),
        description=book.get("description", "") or "",
        page=page,
        typography=Typography.from_dict(data.get("typography", {}), page,
                                        language),
        source=SourceSpec.from_dict(data.get("source", {})),
        cover=CoverSpec.from_dict(data.get("cover", {})),
        output=OutputSpec.from_dict(data.get("output", {}), title),
        front_matter=list(data.get("front_matter", []) or []),
        back_matter=list(data.get("back_matter", []) or []),
        colophon_lines=list(data.get("colophon", []) or []),
        raw=data,
    )

    cfg.labels = labels_for(language)
    cfg.labels.update(data.get("labels", {}) or {})

    for i, p in enumerate(data.get("parts", []) or [], 1):
        cfg.parts.append(PartSpec(
            number=int(p.get("number", i)),
            title=p.get("title", f"{cfg.labels['part']} {i}"),
            roman=p.get("roman", ""),
            chapters=[int(c) for c in p.get("chapters", [])],
            intro_file=p.get("intro_file"),
        ))
    return cfg
