"""Il carattere giusto per ogni scrittura.

Un libro tradotto in giapponese, in arabo o in geroglifico non si può stampare col carattere
dell'italiano: quei segni lì dentro non ci sono, e al loro posto compaiono i quadratini vuoti.
Qui si riconosce **da quali scritture è fatto un testo** — guardando i punti di codice, non la
lingua dichiarata — e si sceglie fra i caratteri installati uno che sappia disegnarle.

Il riconoscimento per punto di codice, e non per lingua, serve perché un paragrafo è quasi sempre
misto: una frase italiana con una parola in geroglifico, un testo ebraico con una data in cifre
occidentali. Ogni tratto riceve il suo carattere e il paragrafo resta uno solo.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

# Scritture riconosciute, con i caratteri di sistema che le disegnano, in ordine di preferenza.
# Ogni voce è (nome del file, indice dentro la collezione ``.ttc``).
_CARATTERI: dict[str, tuple[tuple[str, int], ...]] = {
    "latin": (("arial.ttf", 0), ("segoeui.ttf", 0)),
    "latinext": (("arial.ttf", 0), ("segoeui.ttf", 0), ("DejaVuSans.ttf", 0)),
    "cyrillic": (("arial.ttf", 0), ("segoeui.ttf", 0)),
    "greek": (("arial.ttf", 0), ("segoeui.ttf", 0)),
    "hebrew": (("arial.ttf", 0), ("segoeui.ttf", 0)),
    "arabic": (("arial.ttf", 0), ("segoeui.ttf", 0)),
    "thai": (("LEELAWAD.TTF", 0), ("Leelawadee.ttf", 0), ("arial.ttf", 0)),
    "devanagari": (("Nirmala.ttc", 0), ("NirmalaS.ttf", 0)),
    "han": (("msyh.ttc", 0), ("YuGothR.ttc", 0), ("simsun.ttc", 0)),  # cinese e kanji
    "hiragana": (("YuGothR.ttc", 0), ("msgothic.ttc", 0), ("malgun.ttf", 0)),
    "katakana": (("YuGothR.ttc", 0), ("msgothic.ttc", 0), ("malgun.ttf", 0)),
    "hangul": (("malgun.ttf", 0), ("gulim.ttc", 0)),
    # Noto prima di Segoe: copre l'intero blocco (1072 segni su 1072) ed è ridistribuibile
    # con licenza SIL OFL, mentre quello di Windows è proprietario e non si può impacchettare.
    "egyptian": (("NotoSansEgyptianHieroglyphs-Regular.ttf", 0), ("seguihis.ttf", 0)),
    "coptic": (("NotoSansCoptic-Regular.ttf", 0), ("seguihis.ttf", 0), ("seguisym.ttf", 0)),
}

# Dove cercare i caratteri: prima quelli portati col progetto, poi quelli di sistema.
_CARTELLE = (
    Path.home() / ".bookwriter" / "fonts",
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts"),
    Path("/Library/Fonts"),
)

# Le scritture che i caratteri incorporati nel PDF sanno già disegnare: per loro non si tocca
# niente, e il file resta leggero come prima. Solo il latino **di base**: i font standard del PDF
# arrivano a Latin-1, e già la ő dell'ungherese sta oltre.
_GIA_COPERTE = {"latin", "common", "inherited", "unknown"}

# Segni senza scrittura propria — spazi, virgole, parentesi — che vanno disegnati col carattere
# del tratto che accompagnano. Il latino **non** è qui dentro: se lo fosse, dopo una parola in
# geroglifico tutto il resto della frase finirebbe nel carattere egizio, che le lettere latine
# non le ha, e sparirebbe dalla pagina.
_SENZA_SCRITTURA = {"common", "inherited", "unknown"}


def script_of(carattere: str) -> str:
    """La scrittura di un singolo carattere, dedotta dal suo punto di codice."""
    punto = ord(carattere)
    if 0x13000 <= punto <= 0x1345F:
        return "egyptian"
    if 0x2C80 <= punto <= 0x2CFF or 0x102E0 <= punto <= 0x102FF:
        return "coptic"
    if 0x0590 <= punto <= 0x05FF:
        return "hebrew"
    if 0x0600 <= punto <= 0x06FF or 0x0750 <= punto <= 0x077F:
        return "arabic"
    if 0x0900 <= punto <= 0x097F:
        return "devanagari"
    if 0x0E00 <= punto <= 0x0E7F:
        return "thai"
    if 0x3040 <= punto <= 0x309F:
        return "hiragana"
    if 0x30A0 <= punto <= 0x30FF:
        return "katakana"
    if 0xAC00 <= punto <= 0xD7AF or 0x1100 <= punto <= 0x11FF:
        return "hangul"
    if 0x4E00 <= punto <= 0x9FFF or 0x3400 <= punto <= 0x4DBF or 0xF900 <= punto <= 0xFAFF:
        return "han"
    if 0x0400 <= punto <= 0x052F:
        return "cyrillic"
    if 0x0370 <= punto <= 0x03FF or 0x1F00 <= punto <= 0x1FFF:
        return "greek"
    if carattere.isspace() or not carattere.isalnum():
        return "common"
    # I caratteri incorporati nel PDF arrivano a Latin-1 e non oltre. Sopra ci sono la ő e la ű
    # dell'ungherese, la ș del romeno e i segni della traslitterazione egittologica — ꜣ ꜥ ḥ ḫ ẖ ṯ ḏ —
    # che senza un carattere adatto diventano quadratini.
    return "latin" if punto <= 0x00FF else "latinext"


def scripts_in(testo: str) -> set[str]:
    """Le scritture presenti nel testo, escluse quelle che non hanno bisogno di un carattere proprio."""
    return {script_of(c) for c in testo} - _GIA_COPERTE


def _trova_file(nome: str) -> Path | None:
    for cartella in _CARTELLE:
        percorso = cartella / nome
        if percorso.exists():
            return percorso
        # I nomi dei file di sistema cambiano di maiuscole fra un'installazione e l'altra.
        if cartella.exists():
            for candidato in cartella.glob("*"):
                if candidato.name.lower() == nome.lower():
                    return candidato
    return None


@lru_cache(maxsize=None)
def font_for_script(scrittura: str) -> str | None:
    """Registra in reportlab un carattere che sa disegnare la scrittura e ne ritorna il nome.

    ``None`` se sulla macchina non c'è nessun carattere adatto: chi chiama deve poterlo dire
    all'utente invece di stampare quadratini senza avvertire.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for nome_file, indice in _CARATTERI.get(scrittura, ()):
        percorso = _trova_file(nome_file)
        if percorso is None:
            continue
        etichetta = f"bw-{scrittura}"
        if etichetta in pdfmetrics.getRegisteredFontNames():
            return etichetta
        try:
            pdfmetrics.registerFont(TTFont(etichetta, str(percorso), subfontIndex=indice))
        except Exception:  # noqa: BLE001 - un carattere illeggibile non deve fermare il libro
            continue
        return etichetta
    return None


def missing_scripts(testo: str) -> set[str]:
    """Scritture presenti nel testo per cui non esiste un carattere sulla macchina."""
    return {s for s in scripts_in(testo) if font_for_script(s) is None}


@lru_cache(maxsize=4096)
def _disegna(scrittura: str, carattere: str) -> bool:
    """Vero se il carattere scelto per quella scrittura sa disegnare questo segno."""
    from reportlab.pdfbase import pdfmetrics

    nome = font_for_script(scrittura)
    if nome is None:
        return False
    try:
        return pdfmetrics.getFont(nome).face.charToGlyph.get(ord(carattere)) is not None
    except Exception:  # noqa: BLE001 - in dubbio si preferisce non affidargli il segno
        return False


def _tratti(testo: str) -> list[tuple[str, str]]:
    """Spezza il testo in tratti omogenei di scrittura, tenendo la punteggiatura col vicino."""
    fuori: list[tuple[str, str]] = []
    for carattere in testo:
        scrittura = script_of(carattere)
        if scrittura in _SENZA_SCRITTURA and fuori:
            vicino = fuori[-1][0]
            # Virgole e parentesi seguono il tratto che accompagnano, ma solo se quel carattere
            # sa disegnarle: Noto per i geroglifici non ha le parentesi, e una parentesi
            # affidata a lui sparirebbe dalla pagina senza che nessuno se ne accorga.
            scrittura = (
                vicino
                if vicino in _GIA_COPERTE or carattere.isspace() or _disegna(vicino, carattere)
                else "latin"
            )
        elif scrittura in _SENZA_SCRITTURA:
            scrittura = "latin"
        if fuori and fuori[-1][0] == scrittura:
            fuori[-1] = (scrittura, fuori[-1][1] + carattere)
        else:
            fuori.append((scrittura, carattere))
    return fuori


def with_fonts(markup: str) -> str:
    """Avvolge ogni tratto del markup nel carattere che sa disegnarlo.

    Il markup è quello di reportlab (``<b>``, ``<i>``): i tag restano fuori dai tratti, perché
    dividere un tag a metà produrrebbe un paragrafo illeggibile.
    """
    import re

    fuori: list[str] = []
    for pezzo in re.split(r"(<[^>]+>)", markup):
        if not pezzo:
            continue
        if pezzo.startswith("<"):
            fuori.append(pezzo)
            continue
        for scrittura, tratto in _tratti(pezzo):
            nome = font_for_script(scrittura) if scrittura not in _GIA_COPERTE else None
            fuori.append(f'<font name="{nome}">{tratto}</font>' if nome else tratto)
    return "".join(fuori)


def docx_font_for(testo: str) -> str | None:
    """Nome del carattere di sistema per un testo in DOCX, o ``None`` se va bene quello del documento.

    Word cerca da solo un carattere di ripiego per i segni che non trova, ma solo se il carattere
    del paragrafo non li ha **e** ne conosce uno migliore: dirglielo esplicitamente evita di
    dipendere da quel ripiego, che cambia da un computer all'altro.
    """
    nomi = {
        "egyptian": "Segoe UI Historic", "coptic": "Segoe UI Historic",
        "han": "Microsoft YaHei", "hiragana": "Yu Gothic", "katakana": "Yu Gothic",
        "hangul": "Malgun Gothic", "devanagari": "Nirmala UI", "thai": "Leelawadee UI",
        "hebrew": "Arial", "arabic": "Arial", "latinext": "Arial",
    }
    presenti = scripts_in(testo)
    for scrittura, nome in nomi.items():
        if scrittura in presenti:
            return nome
    return None


def describe(scrittura: str) -> str:
    """Nome leggibile di una scrittura, per i messaggi all'utente."""
    return {
        "egyptian": "geroglifici egizi", "coptic": "copto", "han": "cinese",
        "hiragana": "giapponese", "katakana": "giapponese", "hangul": "coreano",
        "devanagari": "devanagari", "thai": "thai", "hebrew": "ebraico",
        "arabic": "arabo", "cyrillic": "cirillico", "greek": "greco",
        "latinext": "latino esteso (ő, ű, ꜣ, ḥ)",
    }.get(scrittura, unicodedata.name(scrittura[0], scrittura) if scrittura else scrittura)
