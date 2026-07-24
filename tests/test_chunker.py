"""Test del chunking deterministico."""

from __future__ import annotations

from bookwriter.ingest.chunker import chunk_document
from bookwriter.ingest.structure import parse_structure
from bookwriter.text_utils import normalize_text

SAMPLE = normalize_text(
    """# Capitolo 1

Questo è il primo paragrafo del capitolo uno. Contiene un paio di frasi utili.
Serve a riempire un po' di spazio testuale per il test.

Secondo paragrafo del capitolo uno, con altre parole ancora, così da superare
il budget e forzare più di un chunk quando il target è basso.

# Capitolo 2

Il secondo capitolo inizia qui. Deve stare in un chunk separato dal primo.
"""
)


def _chunks(target: int = 40):
    blocks = parse_structure(SAMPLE)
    return chunk_document(SAMPLE, blocks, target_tokens=target)


def test_deterministic() -> None:
    a = _chunks()
    b = _chunks()
    assert [(c.span_start, c.span_end, c.text) for c in a] == [
        (c.span_start, c.span_end, c.text) for c in b
    ]


def test_no_overlap_and_ordered() -> None:
    chunks = _chunks()
    assert [c.book_pos for c in chunks] == list(range(len(chunks)))
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev.span_end <= nxt.span_start  # tiling non sovrapposto


def test_headings_start_new_chunk_and_set_chapter() -> None:
    chunks = _chunks()
    # Deve esistere almeno un chunk assegnato al Capitolo 2.
    chapters = {c.chapter_ref for c in chunks}
    assert "Capitolo 1" in chapters
    assert "Capitolo 2" in chapters
    # Il chunk del capitolo 2 non deve contenere testo del capitolo 1.
    cap2 = [c for c in chunks if c.chapter_ref == "Capitolo 2"]
    assert cap2 and "primo paragrafo" not in " ".join(c.text for c in cap2)


def test_budget_respected() -> None:
    target = 40
    chunks = _chunks(target)
    # Tolleranza per gli spazi tra blocchi inglobati.
    assert all(c.token_count <= target + 30 for c in chunks)
    assert len(chunks) >= 3  # almeno: cap1-p1, cap1-p2, cap2


def test_full_coverage_of_blocks() -> None:
    blocks = parse_structure(SAMPLE)
    chunks = _chunks()
    joined = "\n".join(c.text for c in chunks)
    for block in blocks:
        assert block.text in joined
