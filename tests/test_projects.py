"""Test della gestione multi-progetto."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookwriter.projects import ProjectError, ProjectManager, slugify


def test_slugify() -> None:
    assert slugify("Il Mio Libro!") == "il-mio-libro"
    assert slugify("   ") == "progetto"


def test_create_list_active(temp_workspace: Path) -> None:
    pm = ProjectManager()
    p = pm.create("Il mio libro")
    assert p.slug == "il-mio-libro"
    assert [x.slug for x in pm.list()] == ["il-mio-libro"]
    assert pm.active_slug() == "il-mio-libro"  # nuovo progetto diventa attivo
    paths = pm.paths(p.slug)
    assert paths.db.exists()
    assert paths.config.exists()
    assert paths.sources.is_dir()


def test_unique_slug(temp_workspace: Path) -> None:
    pm = ProjectManager()
    a = pm.create("Diario")
    b = pm.create("Diario")
    assert a.slug == "diario"
    assert b.slug == "diario-2"


def test_switch_and_info(temp_workspace: Path) -> None:
    pm = ProjectManager()
    pm.create("Uno")
    pm.create("Due")
    assert pm.active_slug() == "due"
    pm.set_active("uno")
    assert pm.active_slug() == "uno"
    info = pm.info("uno")
    assert info["active"] is True
    assert info["reasoning_model"] == "gemma-4-12b-obliterated"


def test_switch_unknown_raises(temp_workspace: Path) -> None:
    pm = ProjectManager()
    with pytest.raises(ProjectError):
        pm.set_active("inesistente")


def test_delete_purges(temp_workspace: Path) -> None:
    pm = ProjectManager()
    p = pm.create("Effimero")
    root = pm.paths(p.slug).root
    assert root.exists()
    pm.delete(p.slug)
    assert not root.exists()
    assert pm.get(p.slug) is None
    assert pm.active_slug() is None  # era attivo → azzerato
