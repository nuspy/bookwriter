"""Registry globale: elenco progetti e progetto attivo (``<workspace>/registry.db``)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Field, SQLModel, create_engine


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Project(SQLModel, table=True):
    __tablename__ = "project"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    created_at: str = Field(default_factory=_now_iso)


class Setting(SQLModel, table=True):
    """Coppie chiave/valore globali (es. ``active_project`` = slug)."""

    __tablename__ = "setting"

    key: str = Field(primary_key=True)
    value: str


_REGISTRY_TABLES = [Project.__table__, Setting.__table__]  # type: ignore[attr-defined]


def make_engine(db_path: Path) -> Engine:
    # NullPool: evita di trattenere connessioni (file SQLite non lockato su Windows).
    return create_engine(f"sqlite:///{db_path}", poolclass=NullPool)


def init_registry(db_path: Path) -> Engine:
    """Crea (se assenti) solo le tabelle del registry nel DB indicato."""
    engine = make_engine(db_path)
    SQLModel.metadata.create_all(engine, tables=_REGISTRY_TABLES)
    return engine
