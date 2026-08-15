"""Utility di testo deterministiche: normalizzazione, conteggio token, split in frasi.

Il conteggio token è un'**euristica** (nessuna dipendenza dal tokenizer del modello): serve solo a
rispettare il budget di chunking sotto il cap embedding (2048). È deterministica e stabile → stesso
testo, stesso conteggio.
"""

from __future__ import annotations

import re

# ~4 caratteri per token: euristica comune, buona per lingue latine e per stare sotto il cap.
_CHARS_PER_TOKEN = 4

# Fine frase: punteggiatura terminale + spazio + inizio di nuova frase (maiuscola/apice/cifra).
_SENTENCE_BOUNDARY = re.compile(
    r'(?<=[.!?…])["”»\')\]]*\s+(?=[A-ZÀ-ÖØ-Þ0-9"“«¿¡\'(\[])'
)

# Riconosce righe-titolo (heading) tipiche.
_HEADING_MD = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_HEADING_NUMBERED = re.compile(
    r"^\s*(cap(?:itolo|\.)?|parte|sezione|chapter|part|section)\s+[\dIVXLCM]+.*$",
    re.IGNORECASE,
)


# Punti di guida degli indici («Capitolo 1 . . . . . . 12», anche con trattini o underscore).
# Estratti da un PDF diventano righe lunghissime di sola punteggiatura: non portano contenuto,
# gonfiano i blocchi e mandano i modelli in ripetizione senza fine. Si riducono a un segno solo.
_DOT_LEADERS = re.compile(r"(?:[.·•_-][ \t]*){5,}")

# Riga separatrice di una tabella Markdown: `|---|---|`, `| :--- | ---: |`, e simili. È fatta
# di trattini, quindi la regola dei punti di guida la prenderebbe in pieno — e senza quella riga
# la tabella smette di essere una tabella per qualunque lettore di Markdown.
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$")


def _is_table_rule(line: str) -> bool:
    """Vero se la riga è la separatrice di intestazione di una tabella, non un fregio di puntini."""
    nuda = line.strip()
    return bool(nuda) and "|" in nuda and "-" in nuda and bool(_TABLE_RULE.match(nuda))


def normalize_text(text: str) -> str:
    """Uniforma i fine-riga, elimina spazi finali, comprime righe vuote e punti di guida.

    I punti di guida si riducono riga per riga, saltando le separatrici delle tabelle: quei
    trattini non sono un indice, sono ciò che tiene in piedi la tabella.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(
        riga if _is_table_rule(riga) else _DOT_LEADERS.sub(" … ", riga)
        for riga in text.split("\n")
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def has_prose(text: str) -> bool:
    """True se il passaggio contiene davvero del testo da far elaborare a un modello.

    L'indice di un PDF è fatto di punti di guida e numeri di pagina, il colophon di sigle e
    date: non c'è nulla da atomizzare, tradurre o riscrivere. Darli al modello non è solo
    lavoro sprecato, è il caso in cui entra in ripetizione senza fine. La prosa sta ben sopra
    il 60% di lettere; un indice molto sotto.
    """
    if not text.strip():
        return False
    return sum(ch.isalpha() for ch in text) >= 0.35 * len(text)


def count_tokens(text: str) -> int:
    """Stima deterministica del numero di token (~caratteri/4, minimo il numero di parole)."""
    if not text:
        return 0
    return max(len(text.split()), round(len(text) / _CHARS_PER_TOKEN))


def split_sentences(text: str) -> list[str]:
    """Divide un testo in frasi. Deterministico; robusto per italiano/inglese."""
    text = text.strip()
    if not text:
        return []
    parts = _SENTENCE_BOUNDARY.split(text)
    return [p.strip() for p in parts if p.strip()]


def looks_like_heading(line: str) -> tuple[bool, str | None]:
    """True se la riga sembra un titolo; ritorna anche il testo del titolo depurato."""
    stripped = line.strip()
    if not stripped:
        return False, None
    md = _HEADING_MD.match(line)
    if md:
        return True, md.group(2).strip()
    if _HEADING_NUMBERED.match(stripped):
        return True, stripped
    # Riga breve, senza punteggiatura terminale, in maiuscolo → probabile titolo.
    if (
        len(stripped) <= 80
        and not stripped.endswith((".", ",", ";", ":"))
        and stripped == stripped.upper()
        and any(c.isalpha() for c in stripped)
    ):
        return True, stripped
    return False, None
