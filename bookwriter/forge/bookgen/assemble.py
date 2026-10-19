"""Assemble the complete volume: front matter, body, back matter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from .config import Config
from .model import Block, Book, Part, Section
from .parse import dedupe_references, load_parts, parse_blocks

# Sections the generator can build on its own, without a source file.
GENERATED = {"bibliography", "colophon", "contents"}

# Conventional order, used when sections are discovered from the file system.
FRONT_ORDER = ["preface", "notice", "foreword", "introduction"]
BACK_ORDER = ["appendix", "glossary", "bibliography", "about_author",
              "acknowledgements"]

# File-name stems mapped to section identifiers, so a project can simply drop
# `front/introduzione.md` in place and be understood.
IDENT_HINTS = {
    "introduction": ["introduzione", "introduction", "introduccion",
                     "einleitung", "intro"],
    "preface": ["prefazione", "preface", "prefacio", "vorwort"],
    "foreword": ["premessa", "foreword", "prologo", "prologue"],
    "notice": ["avvertenza", "notice", "disclaimer", "avviso", "advertencia",
               "avertissement"],
    "glossary": ["glossario", "glossary", "glosario", "glossar"],
    "appendix": ["appendice", "appendix", "apendice", "anhang"],
    "bibliography": ["bibliografia", "bibliography", "bibliographie",
                     "references", "riferimenti"],
    "about_author": ["biografia", "autore", "author", "about_author",
                     "about-the-author", "biography", "biografia_autore"],
    "acknowledgements": ["ringraziamenti", "acknowledgements",
                         "acknowledgments", "agradecimientos", "danksagung"],
}


def _ident_from_name(stem: str) -> Optional[str]:
    s = re.sub(r"^\d+[_\-. ]*", "", stem).lower().replace(" ", "_")
    for ident, names in IDENT_HINTS.items():
        if any(s == n or s.startswith(n) for n in names):
            return ident
    return None


def _title_from_file(path: Path) -> Optional[str]:
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return m.group(1).strip()
    return None


def _blocks_from_file(path: Path, cfg: Config) -> List[Block]:
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^#\s+", line.strip()):
            lines = lines[i + 1:]
            break
    return parse_blocks(lines, cfg)


def _section_from_entry(entry: Dict, cfg: Config) -> Optional[Section]:
    ident = entry.get("id") or entry.get("ident") or ""
    title = entry.get("title") or ""
    file_ref = entry.get("file")

    if ident in GENERATED and not file_ref:
        return Section(ident=ident, title=title or cfg.label(ident, ident))

    path = cfg.resolve(file_ref) if file_ref else None
    if path is None or not path.exists():
        return None
    ident = ident or _ident_from_name(path.stem) or path.stem
    title = title or _title_from_file(path) or cfg.label(ident, ident.title())
    subtitle = entry.get("subtitle", "")
    if not subtitle and "—" in title:
        title, subtitle = [t.strip() for t in title.split("—", 1)]
    return Section(ident=ident, title=title, subtitle=subtitle,
                   blocks=_blocks_from_file(path, cfg),
                   in_toc=bool(entry.get("in_toc", True)))


def _discover(folder: Path, cfg: Config, order: List[str]) -> List[Section]:
    if not folder.exists():
        return []
    sections = []
    for path in sorted(folder.glob("*.md")):
        ident = _ident_from_name(path.stem) or path.stem
        title = _title_from_file(path) or cfg.label(ident, path.stem.title())
        subtitle = ""
        if "—" in title:
            title, subtitle = [t.strip() for t in title.split("—", 1)]
        sections.append(Section(ident=ident, title=title, subtitle=subtitle,
                                blocks=_blocks_from_file(path, cfg)))
    # numeric prefixes win; otherwise fall back to the conventional order
    if all(re.match(r"^\d+", p.stem) for p in sorted(folder.glob("*.md"))):
        return sections
    sections.sort(key=lambda s: order.index(s.ident) if s.ident in order
                  else len(order))
    return sections


def build_bibliography(refs: List[str], cfg: Config) -> List[Block]:
    blocks: List[Block] = []
    note = (cfg.raw.get("bibliography", {}) or {}).get("note")
    if note:
        blocks.append(Block("para", note))
        blocks.append(Block("gap"))
    blocks += [Block("reference", r) for r in refs]
    return blocks


def collect_external_references(cfg: Config) -> List[str]:
    """Read an optional standalone bibliography file kept by the author."""
    ref_cfg = cfg.raw.get("bibliography", {}) or {}
    path = cfg.resolve(ref_cfg.get("file"))
    if path and path.exists():
        from .parse import collect_reference_entries
        return collect_reference_entries(path.read_text(encoding="utf-8")
                                         .splitlines())
    return []


def build_book(cfg: Config) -> Book:
    parts = load_parts(cfg)

    refs = [r for p in parts for c in p.chapters for r in c.references]
    refs += collect_external_references(cfg)
    refs = dedupe_references(refs)

    front: List[Section] = []
    back: List[Section] = []

    if cfg.front_matter:
        front = [s for s in (_section_from_entry(e, cfg)
                             for e in cfg.front_matter) if s]
    else:
        front = _discover(cfg.root / "front", cfg, FRONT_ORDER)

    if cfg.back_matter:
        back = [s for s in (_section_from_entry(e, cfg)
                            for e in cfg.back_matter) if s]
    else:
        back = _discover(cfg.root / "back", cfg, BACK_ORDER)

    # The bibliography is generated unless the project supplied its own copy.
    if refs and not any(s.ident == "bibliography" for s in back):
        back.append(Section(ident="bibliography",
                            title=cfg.label("bibliography", "Bibliografia")))
    for s in back:
        if s.ident == "bibliography" and not s.blocks:
            s.blocks = build_bibliography(refs, cfg)

    # An author note belongs at the end; if the project has one, keep it last.
    back.sort(key=lambda s: 1 if s.ident in ("about_author",
                                             "acknowledgements") else 0)

    return Book(title=cfg.title, subtitle=cfg.subtitle, author=cfg.author,
                language=cfg.language, parts=parts, front=front, back=back,
                references=refs, config=cfg)


def stats(book: Book) -> Dict[str, object]:
    return {
        "parts": len([p for p in book.parts if p.title or len(book.parts) > 1]),
        "chapters": len(book.chapters),
        "words": book.word_count,
        "references": len(book.references),
        "front_sections": [s.ident for s in book.front],
        "back_sections": [s.ident for s in book.back],
    }
