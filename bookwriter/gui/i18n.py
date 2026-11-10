"""Lingua dell'interfaccia.

Le stringhe nel codice sono in italiano e fanno da chiave: ``t("Importa testi")`` cerca la voce
nel catalogo della lingua scelta. I cataloghi sono file JSON in ``locales/``, uno per lingua,
modificabili senza toccare il codice.

Il desktop si mantiene in **italiano e inglese** (decisione dell'autore, 25/10/2026): ogni scritta
nuova ha la sua voce in ``en.json``; gli altri cataloghi restano con quello che hanno. Una voce
che manca in un'altra lingua ricade sull'**inglese**, non sull'italiano: chi ha scelto il
giapponese legge l'inglese prima di una lingua che magari non conosce. Le altre lingue della
versione web le fa il traduttore del browser (vedi ``SAAS_Bokwriter.md``).
"""

from __future__ import annotations

import json
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"

# Lingue dell'interfaccia, con il nome scritto nella lingua stessa.
UI_LANGUAGES: list[tuple[str, str]] = [
    ("it", "Italiano"),
    ("en", "English"),
    ("hu", "Magyar"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("pt", "Português"),
    ("ru", "Русский"),
    ("uk", "Українська"),
    ("zh", "中文"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("hi", "हिन्दी"),
]

_DEFAULT = "it"  # lingua in cui sono scritte le chiavi: non ha bisogno di catalogo
#: La lingua di ripiego per le voci che mancano negli altri cataloghi: l'unica, oltre
#: all'italiano, che il desktop mantiene completa.
FALLBACK = "en"
_current = _DEFAULT
_catalog: dict[str, str] = {}
_fallback: dict[str, str] = {}


def available_languages() -> list[tuple[str, str]]:
    return list(UI_LANGUAGES)


def current_language() -> str:
    return _current


def _load(code: str) -> dict[str, str] | None:
    try:
        data = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return {k: v for k, v in data.items() if isinstance(v, str) and v}


def set_language(code: str) -> str:
    """Carica il catalogo della lingua; ricade sull'italiano se il file manca."""
    global _current, _catalog, _fallback
    code = (code or _DEFAULT).strip() or _DEFAULT
    if code == _DEFAULT:
        _current, _catalog, _fallback = _DEFAULT, {}, {}
        return _current
    catalogo = _load(code)
    if catalogo is None:
        _current, _catalog, _fallback = _DEFAULT, {}, {}
        return _current
    _current, _catalog = code, catalogo
    _fallback = {} if code == FALLBACK else (_load(FALLBACK) or {})
    return _current


def t(text: str) -> str:
    """Traduce una stringa dell'interfaccia: la lingua scelta, poi l'inglese, poi l'originale."""
    return _catalog.get(text) or _fallback.get(text, text)
