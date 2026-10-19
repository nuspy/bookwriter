"""Format-independent representation of a book."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Block:
    """One paragraph-level typographic unit.

    kind:
        h2 h3 h4    section headings, in decreasing rank
        para        running paragraph
        bullet      unordered list item
        number      ordered list item (level holds the printed number)
        table       rows[0] is the header row
        rule        thematic break
        gap         vertical space
        quote       block quotation, set apart from the running text
        reference   bibliography entry, hanging indent
    """
    kind: str
    text: str = ""
    level: int = 0
    rows: List[List[str]] = field(default_factory=list)


@dataclass
class Chapter:
    number: int
    title: str
    subtitle: str = ""
    blocks: List[Block] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    source: str = ""

    @property
    def full_title(self) -> str:
        return f"{self.title} — {self.subtitle}" if self.subtitle else self.title


@dataclass
class Part:
    number: int
    roman: str
    title: str
    intro: List[Block] = field(default_factory=list)
    chapters: List[Chapter] = field(default_factory=list)


@dataclass
class Section:
    """Front- or back-matter section (introduction, notice, glossary...)."""
    ident: str
    title: str
    blocks: List[Block] = field(default_factory=list)
    subtitle: str = ""
    in_toc: bool = True


@dataclass
class Book:
    title: str
    subtitle: str
    author: str
    language: str = "it"
    parts: List[Part] = field(default_factory=list)
    front: List[Section] = field(default_factory=list)
    back: List[Section] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    config: Optional[object] = None          # bookgen.config.Config

    @property
    def chapters(self) -> List[Chapter]:
        return [c for p in self.parts for c in p.chapters]

    @property
    def word_count(self) -> int:
        n = 0
        for s in self.front + self.back:
            n += sum(len(b.text.split()) for b in s.blocks)
        for p in self.parts:
            n += sum(len(b.text.split()) for b in p.intro)
            for c in p.chapters:
                n += sum(len(b.text.split()) for b in c.blocks)
                n += sum(len(cell.split()) for b in c.blocks
                         for row in b.rows for cell in row)
        return n
