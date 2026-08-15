"""Lingua dell'interfaccia.

Le stringhe nel codice sono in italiano e fanno da chiave: ``t("Importa testi")`` cerca la voce
nel catalogo della lingua scelta e, se manca, restituisce l'italiano originale. I cataloghi sono
file JSON in ``locales/``, uno per lingua, modificabili senza toccare il codice.
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
_current = _DEFAULT
_catalog: dict[str, str] = {}


def available_languages() -> list[tuple[str, str]]:
    return list(UI_LANGUAGES)


def current_language() -> str:
    return _current


def set_language(code: str) -> str:
    """Carica il catalogo della lingua; ricade sull'italiano se il file manca."""
    global _current, _catalog
    code = (code or _DEFAULT).strip() or _DEFAULT
    if code == _DEFAULT:
        _current, _catalog = _DEFAULT, {}
        return _current
    path = LOCALES_DIR / f"{code}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _current, _catalog = _DEFAULT, {}
        return _current
    _current = code
    _catalog = {k: v for k, v in data.items() if isinstance(v, str) and v}
    return _current


def t(text: str) -> str:
    """Traduce una stringa dell'interfaccia; senza traduzione resta l'originale."""
    return _catalog.get(text, text)
