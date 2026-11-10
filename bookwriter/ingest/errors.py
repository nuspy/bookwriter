"""L'errore dei loader, in un modulo a sé.

Sta qui e non in ``loaders`` perché i lettori dei formati aggiuntivi (OCR, audio, posta, pagine
web, fogli di calcolo) devono poterlo sollevare senza importare ``loaders``, che a sua volta li
importa: senza questo modulo il cerchio si chiuderebbe.
"""

from __future__ import annotations


class LoaderError(RuntimeError):
    """Il documento non si può leggere: il messaggio dice perché, in parole dell'autore."""


def dipendenza_mancante(formato: str, pacchetto: str, extra: str) -> LoaderError:
    """L'errore per una dipendenza opzionale assente: quale pacchetto manca e come averlo."""
    return LoaderError(
        f"Per importare {formato} serve il pacchetto '{pacchetto}', che non è installato. "
        f"Installa gli extra: pip install -e \".[{extra}]\""
    )
