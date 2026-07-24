"""Chunking deterministico: tiling non sovrapposto del documento in chunk gestibili dall'LLM.

Regole:
- i chunk **tassellano** il testo senza sovrapposizione → ricomposizione esatta (`book_pos`);
- non si spezza mai una frase; si preferiscono i confini di paragrafo;
- un titolo (heading) apre sempre un nuovo chunk (i capitoli non si fondono);
- un paragrafo più grande del budget viene diviso per frasi; una frase enorme, per parole;
- budget in token = euristica deterministica (vedi ``text_utils.count_tokens``), sotto il cap 2048.
Puro e ripetibile: stesso input → stessi chunk.
"""

from __future__ import annotations

from dataclasses import dataclass

from bookwriter.ingest.structure import Block
from bookwriter.text_utils import count_tokens, split_sentences


@dataclass
class Chunk:
    book_pos: int
    chapter_ref: str | None
    span_start: int
    span_end: int
    text: str
    token_count: int


@dataclass
class _Unit:
    span_start: int
    span_end: int
    chapter_ref: str | None
    is_heading: bool
    tokens: int


def _hard_split_words(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Divide una singola frase troppo lunga in gruppi di parole entro il budget."""
    words = text[start:end].split()
    spans: list[tuple[int, int]] = []
    cursor = start
    group_start: int | None = None
    group_last_end = start
    acc = ""
    for word in words:
        i = text.find(word, cursor)
        if i == -1:
            i = cursor
        j = i + len(word)
        cursor = j
        candidate = f"{acc} {word}".strip()
        if acc and count_tokens(candidate) > target:
            spans.append((group_start if group_start is not None else i, group_last_end))
            group_start = i
            acc = word
        else:
            if group_start is None:
                group_start = i
            acc = candidate
        group_last_end = j
    if group_start is not None:
        spans.append((group_start, group_last_end))
    return spans


def _sentence_spans(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Span delle frasi in [start,end); una frase oltre budget viene divisa per parole."""
    spans: list[tuple[int, int]] = []
    cursor = start
    for sentence in split_sentences(text[start:end]):
        i = text.find(sentence, cursor)
        if i == -1:
            i = cursor
        j = i + len(sentence)
        cursor = j
        if count_tokens(sentence) > target:
            spans.extend(_hard_split_words(text, i, j, target))
        else:
            spans.append((i, j))
    return spans


def _blocks_to_units(text: str, blocks: list[Block], target: int) -> list[_Unit]:
    units: list[_Unit] = []
    for block in blocks:
        block_tokens = count_tokens(block.text)
        if block.is_heading or block_tokens <= target:
            units.append(
                _Unit(block.span_start, block.span_end, block.chapter_ref, block.is_heading, block_tokens)
            )
        else:  # paragrafo troppo grande → dividi per frasi
            for s, e in _sentence_spans(text, block.span_start, block.span_end, target):
                units.append(_Unit(s, e, block.chapter_ref, False, count_tokens(text[s:e])))
    return units


def chunk_document(
    normalized_text: str,
    blocks: list[Block],
    *,
    target_tokens: int = 1500,
) -> list[Chunk]:
    units = _blocks_to_units(normalized_text, blocks, target_tokens)
    chunks: list[Chunk] = []
    current: list[_Unit] = []
    current_tokens = 0

    def flush() -> None:
        if not current:
            return
        span_start = current[0].span_start
        span_end = current[-1].span_end
        text = normalized_text[span_start:span_end]
        chunks.append(
            Chunk(
                book_pos=len(chunks),
                chapter_ref=current[0].chapter_ref,
                span_start=span_start,
                span_end=span_end,
                text=text,
                token_count=count_tokens(text),
            )
        )

    for unit in units:
        starts_new = current and (unit.is_heading or current_tokens + unit.tokens > target_tokens)
        if starts_new:
            flush()
            current = []
            current_tokens = 0
        current.append(unit)
        current_tokens += unit.tokens
    flush()
    return chunks
