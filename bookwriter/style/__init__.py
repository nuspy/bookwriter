"""Motore di stile: profili, estrazione, fusione autori, applicazione (§6)."""

from bookwriter.style.engine import (
    abstract_style,
    apply_style,
    authors_style,
    blend_styles,
    build_style_contract,
    extract_style,
)
from bookwriter.style.models import STYLE_DIMENSIONS, STYLE_SCHEMA, StyleProfile

__all__ = [
    "STYLE_DIMENSIONS",
    "STYLE_SCHEMA",
    "StyleProfile",
    "abstract_style",
    "apply_style",
    "authors_style",
    "blend_styles",
    "build_style_contract",
    "extract_style",
]
