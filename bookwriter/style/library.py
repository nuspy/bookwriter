"""Libreria di stili **globale** (workspace), riusabile tra progetti.

I profili globali vivono in ``<workspace>/styles/*.json`` (vedi PLAN §4.1). Uno stile "autore X" si
definisce una volta e si importa in qualunque progetto.
"""

from __future__ import annotations

import json
from pathlib import Path

from bookwriter.config import workspace_root
from bookwriter.projects import slugify
from bookwriter.style.models import StyleProfile


def styles_dir() -> Path:
    return workspace_root() / "styles"


def save_global_style(profile: StyleProfile) -> Path:
    d = styles_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slugify(profile.name)}.json"
    path.write_text(
        json.dumps({"name": profile.name, "origin": profile.origin, "data": profile.model_dump()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def list_global_styles() -> list[dict]:
    d = styles_dir()
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            out.append({"name": obj.get("name", path.stem), "origin": obj.get("origin", "?"), "slug": path.stem})
        except (json.JSONDecodeError, OSError):
            continue
    return out


def get_global_style(ref: str) -> StyleProfile | None:
    """Recupera un profilo globale per nome o slug."""
    d = styles_dir()
    if not d.exists():
        return None
    target = slugify(ref)
    for path in d.glob("*.json"):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if path.stem == target or slugify(obj.get("name", "")) == target:
            return StyleProfile.model_validate(obj["data"])
    return None
