"""Modello dello stile: le dimensioni controllabili e il :class:`StyleProfile` (§6.1).

Uno stile è scomposto in **dimensioni** misurabili, così è estraibile, fondibile (blend fra autori)
e verificabile. Ogni dimensione è un descrittore testuale che guida la generazione.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Le 9 dimensioni stilistiche (PLAN §6.1). Chiave → descrizione di cosa cattura.
STYLE_DIMENSIONS: dict[str, str] = {
    "sintassi": "lunghezza media frasi, para/ipotassi, subordinate, incisi, frasi nominali",
    "punteggiatura": "uso di ; : — parentesi, ritmo dei punti",
    "lessico": "registro (aulico↔colloquiale), ricchezza, tecnicismi, arcaismi",
    "figure": "metafore, similitudini, allitterazioni, anafore, densità figurativa",
    "ritmo": "cadenza, variazione di lunghezza, respiro del paragrafo",
    "voce": "persona (I/III), tempo verbale, distanza narrativa, discorso indiretto libero",
    "tono": "ironico / solenne / intimo / saggistico / lirico",
    "coesione": "connettivi, riferimenti anaforici, topic sentence",
    "paragrafo": "lunghezza, digressioni, aperture/chiusure",
}

# JSON Schema per l'output strutturato dell'LLM (estrazione/definizione di una StyleCard).
STYLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "dimensions": {
            "type": "object",
            "additionalProperties": False,
            "properties": {dim: {"type": "string"} for dim in STYLE_DIMENSIONS},
            "required": list(STYLE_DIMENSIONS),
        },
        "exemplars": {"type": "array", "items": {"type": "string"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["dimensions", "exemplars", "constraints"],
}


class AuthorWeight(BaseModel):
    """Contributo di un autore al blend: peso e ambiti (dimensioni) coperti."""

    author: str
    weight: float = 1.0
    dims: list[str] = Field(default_factory=list)  # vuoto = tutte le dimensioni


class StyleProfile(BaseModel):
    """Profilo di stile completo, salvabile e applicabile."""

    name: str
    origin: str = "extracted"  # extracted | abstract | authors
    dimensions: dict[str, str] = Field(default_factory=dict)
    exemplars: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    author_blend: list[AuthorWeight] = Field(default_factory=list)

    @classmethod
    def from_card(cls, name: str, origin: str, card: dict[str, Any]) -> StyleProfile:
        return cls(
            name=name,
            origin=origin,
            dimensions=card.get("dimensions", {}) or {},
            exemplars=card.get("exemplars", []) or [],
            constraints=card.get("constraints", []) or [],
        )
