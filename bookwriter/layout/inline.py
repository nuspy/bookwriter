"""Corsivo e grassetto dentro un paragrafo, in una notazione che sopravvive al modello.

Il DOCX tiene la formattazione nei *run*: pezzi di paragrafo con font e attributi propri. Se al
modello si manda il solo testo, il corsivo dei titoli di libro e il grassetto dei termini si
perdono nella traduzione. Si convertono quindi nella notazione Markdown (``*corsivo*``,
``**grassetto**``), che i modelli conservano bene perché la incontrano ovunque, e si ricostruiscono
i run all'uscita.
"""

from __future__ import annotations

import html as _html
import re

# ``**grassetto**`` prima di ``*corsivo*``: l'ordine conta, altrimenti il doppio asterisco viene
# letto come un corsivo vuoto.
_INLINE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*|_(.+?)_", re.DOTALL)


def wrap(text: str, *, bold: bool = False, italic: bool = False) -> str:
    """Circonda il testo con i marcatori richiesti, lasciando gli spazi **fuori**.

    ``** parola **`` non è grassetto per nessun parser: gli spazi ai bordi vanno tenuti esterni.
    """
    if not text or not (bold or italic) or not text.strip():
        return text
    testa = text[: len(text) - len(text.lstrip())]
    coda = text[len(text.rstrip()) :]
    nucleo = text.strip()
    if bold:
        nucleo = f"**{nucleo}**"
    if italic:
        nucleo = f"*{nucleo}*"
    return f"{testa}{nucleo}{coda}"


def parse(markup: str) -> list[tuple[str, bool, bool]]:
    """Scompone il markup in ``(testo, grassetto, corsivo)`` nell'ordine di lettura."""
    pezzi: list[tuple[str, bool, bool]] = []
    cursore = 0
    for m in _INLINE.finditer(markup):
        if m.start() > cursore:
            pezzi.append((markup[cursore : m.start()], False, False))
        grassetto, corsivo_ast, corsivo_low = m.groups()
        if grassetto is not None:
            pezzi.append((grassetto, True, False))
        else:
            pezzi.append((corsivo_ast if corsivo_ast is not None else corsivo_low, False, True))
        cursore = m.end()
    if cursore < len(markup):
        pezzi.append((markup[cursore:], False, False))
    return [p for p in pezzi if p[0]]


def to_html(markup: str) -> str:
    """Markup → mini-HTML (``<b>``/``<i>``), il dialetto che capisce reportlab."""
    fuori = []
    for testo, grassetto, corsivo in parse(markup):
        pezzo = _html.escape(testo)
        if grassetto:
            pezzo = f"<b>{pezzo}</b>"
        if corsivo:
            pezzo = f"<i>{pezzo}</i>"
        fuori.append(pezzo)
    return "".join(fuori)


def to_plain(markup: str) -> str:
    """Markup → testo nudo, per i conteggi e per i formati che non hanno formattazione."""
    return "".join(t for t, _b, _i in parse(markup))
