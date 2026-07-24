"""Parsing strutturale: dal testo normalizzato a una sequenza di blocchi con capitolo.

Ogni blocco è un titolo (heading) o un paragrafo, con lo ``span`` assoluto sul testo normalizzato
(per la ricomposizione esatta) e il ``chapter_ref`` corrente (il titolo più recente).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bookwriter.text_utils import looks_like_heading

_BLANK_LINE = re.compile(r"\n\s*\n")


@dataclass
class Block:
    text: str
    is_heading: bool
    chapter_ref: str | None
    span_start: int
    span_end: int


def parse_structure(normalized_text: str) -> list[Block]:
    blocks: list[Block] = []
    current_chapter: str | None = None
    cursor = 0
    for raw in _BLANK_LINE.split(normalized_text):
        block_text = raw.strip()
        if not block_text:
            continue
        # Span assoluto: i blocchi compaiono in ordine, li localizziamo progressivamente.
        idx = normalized_text.find(block_text, cursor)
        if idx == -1:  # fallback difensivo (non dovrebbe accadere)
            idx = cursor
        span_start = idx
        span_end = idx + len(block_text)
        cursor = span_end

        is_heading = False
        if "\n" not in block_text:
            is_heading, title = looks_like_heading(block_text)
            if is_heading:
                current_chapter = title

        blocks.append(
            Block(
                text=block_text,
                is_heading=is_heading,
                chapter_ref=current_chapter,
                span_start=span_start,
                span_end=span_end,
            )
        )
    return blocks
