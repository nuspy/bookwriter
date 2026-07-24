"""Test della libreria di stili globale (workspace)."""

from __future__ import annotations

from pathlib import Path

from bookwriter.style.library import get_global_style, list_global_styles, save_global_style
from bookwriter.style.models import StyleProfile


def _profile(name: str) -> StyleProfile:
    return StyleProfile(name=name, origin="abstract", dimensions={"sintassi": "breve"}, exemplars=[], constraints=[])


def test_save_list_get_global(temp_workspace: Path) -> None:
    assert list_global_styles() == []
    path = save_global_style(_profile("Stile Epico"))
    assert path.exists()
    rows = list_global_styles()
    assert len(rows) == 1 and rows[0]["name"] == "Stile Epico"
    # Recupero per nome e per slug.
    by_name = get_global_style("Stile Epico")
    by_slug = get_global_style("stile-epico")
    assert by_name is not None and by_name.dimensions["sintassi"] == "breve"
    assert by_slug is not None
    assert get_global_style("inesistente") is None
