"""Gestione multi-progetto: creazione, elenco, selezione, cancellazione, cartelle e config."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from bookwriter.config import (
    CONFIG_FILENAME,
    AppConfig,
    global_config_path,
    load_global_config,
    workspace_root,
)
from bookwriter.db import project_schema, registry
from bookwriter.db.registry import Project, Setting
from bookwriter.ingest import pipeline

ACTIVE_KEY = "active_project"


@dataclass
class ProjectPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "project.db"

    @property
    def vectors(self) -> Path:
        return self.root / "vectors.lance"

    @property
    def sources(self) -> Path:
        return self.root / "sources"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def config(self) -> Path:
        return self.root / CONFIG_FILENAME


class ProjectError(RuntimeError):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "progetto"


class ProjectManager:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace or workspace_root()
        self.projects_dir = self.workspace / "projects"
        self.registry_path = self.workspace / "registry.db"
        self._engine = None

    # ---- setup ---------------------------------------------------------
    def ensure(self) -> None:
        """Crea workspace, cartella progetti, registry e config globale se assenti."""
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self._engine = registry.init_registry(self.registry_path)
        cfg_path = global_config_path()
        if not cfg_path.exists():
            AppConfig().save(cfg_path)

    @property
    def engine(self):
        if self._engine is None:
            self.ensure()
        return self._engine

    # ---- CRUD progetti -------------------------------------------------
    def create(self, name: str, *, target_words: int | None = None) -> Project:
        self.ensure()
        slug = self._unique_slug(slugify(name))
        paths = self.paths(slug)
        for d in (paths.root, paths.sources, paths.exports):
            d.mkdir(parents=True, exist_ok=True)
        project_schema.init_project_db(paths.db)
        # Config di progetto = copia dei default globali (poi personalizzabile).
        load_global_config().save(paths.config)
        if target_words is not None:
            project_schema.set_bookspec(paths.db, target_words=target_words)

        project = Project(slug=slug, name=name)
        with Session(self.engine) as ses:
            ses.add(project)
            ses.commit()
            ses.refresh(project)
        self.set_active(slug)
        return project

    def list(self) -> list[Project]:
        with Session(self.engine) as ses:
            return list(ses.exec(select(Project).order_by(Project.created_at)))

    def get(self, slug: str) -> Project | None:
        with Session(self.engine) as ses:
            return ses.exec(select(Project).where(Project.slug == slug)).first()

    def delete(self, slug: str, *, purge_files: bool = True) -> None:
        project = self.get(slug)
        if project is None:
            raise ProjectError(f"Progetto '{slug}' inesistente.")
        with Session(self.engine) as ses:
            obj = ses.exec(select(Project).where(Project.slug == slug)).first()
            if obj is not None:
                ses.delete(obj)
            ses.commit()
        if self.active_slug() == slug:
            self._clear_active()
        if purge_files:
            shutil.rmtree(self.paths(slug).root, ignore_errors=True)

    # ---- progetto attivo ----------------------------------------------
    def set_active(self, slug: str) -> None:
        if self.get(slug) is None:
            raise ProjectError(f"Progetto '{slug}' inesistente.")
        with Session(self.engine) as ses:
            setting = ses.get(Setting, ACTIVE_KEY)
            if setting is None:
                ses.add(Setting(key=ACTIVE_KEY, value=slug))
            else:
                setting.value = slug
                ses.add(setting)
            ses.commit()

    def active_slug(self) -> str | None:
        with Session(self.engine) as ses:
            setting = ses.get(Setting, ACTIVE_KEY)
            return setting.value if setting else None

    def active(self) -> Project | None:
        slug = self.active_slug()
        return self.get(slug) if slug else None

    def _clear_active(self) -> None:
        with Session(self.engine) as ses:
            setting = ses.get(Setting, ACTIVE_KEY)
            if setting is not None:
                ses.delete(setting)
                ses.commit()

    # ---- utilità -------------------------------------------------------
    def paths(self, slug: str) -> ProjectPaths:
        return ProjectPaths(self.projects_dir / slug)

    def config_for(self, slug: str) -> AppConfig:
        """Config globale fusa con l'override di progetto."""
        return load_global_config().merged_with(AppConfig.load(self.paths(slug).config))

    def info(self, slug: str) -> dict:
        project = self.get(slug)
        if project is None:
            raise ProjectError(f"Progetto '{slug}' inesistente.")
        paths = self.paths(slug)
        cfg = self.config_for(slug)
        spec = project_schema.get_bookspec(paths.db)
        stats = pipeline.memory_stats(paths.db)
        return {
            "slug": project.slug,
            "name": project.name,
            "created_at": project.created_at,
            "active": self.active_slug() == slug,
            "root": str(paths.root),
            "db_exists": paths.db.exists(),
            "reasoning_model": cfg.llm.models.reasoning,
            "embedding_model": cfg.llm.models.embedding,
            "target_words": spec.target_words,
            "documents": stats["documents"],
            "chunks": stats["chunks"],
            "total_tokens_est": stats["total_tokens_est"],
        }

    def _unique_slug(self, base: str) -> str:
        existing = {p.slug for p in self.list()}
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"
