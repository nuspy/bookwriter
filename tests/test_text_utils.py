"""Test delle utility di testo."""

from __future__ import annotations

from bookwriter.text_utils import (
    count_tokens,
    looks_like_heading,
    normalize_text,
    split_sentences,
)


def test_count_tokens_deterministic_and_zero() -> None:
    assert count_tokens("") == 0
    a = count_tokens("una frase di prova abbastanza lunga da contare")
    assert a == count_tokens("una frase di prova abbastanza lunga da contare")
    assert count_tokens("x" * 400) >= 100  # ~ caratteri/4


def test_normalize_collapses_blank_lines() -> None:
    assert normalize_text("a\r\n\r\n\r\n\r\nb") == "a\n\nb"
    assert normalize_text("  trailing   \nx") == "trailing\nx"


def test_split_sentences() -> None:
    s = split_sentences("Prima frase. Seconda frase! Terza? Quarta.")
    assert s == ["Prima frase.", "Seconda frase!", "Terza?", "Quarta."]
    assert split_sentences("") == []


def test_looks_like_heading() -> None:
    assert looks_like_heading("# Capitolo primo")[0] is True
    assert looks_like_heading("## Sezione")[0] is True
    assert looks_like_heading("CAPITOLO PRIMO")[0] is True
    assert looks_like_heading("Capitolo 3 — Il ritorno")[0] is True
    assert looks_like_heading("Questa è una frase normale con un punto.")[0] is False


def test_dot_leaders_of_an_index_are_collapsed() -> None:
    """I punti di guida di un indice PDF non portano contenuto e fanno ripetere i modelli."""
    riga = "Capitolo 1 . . . . . . . . . . . . . . . . 12"
    normalizzato = normalize_text(riga)
    assert ". . . ." not in normalizzato
    assert normalizzato.startswith("Capitolo 1") and normalizzato.endswith("12")
    # Una punteggiatura normale resta intatta.
    assert normalize_text("Ecco... e poi?") == "Ecco... e poi?"
