"""Persistenza condivisa degli outline e utilità comuni agli ordinamenti."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Chunk,
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    make_engine,
)


@dataclass
class OrganizeReport:
    outline_id: int
    chapters: int
    items: int

    def summary(self) -> str:
        return f"outline #{self.outline_id}: capitoli={self.chapters} · voci={self.items}"


def chunk_positions(db_path: Path) -> dict[int, int]:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        return {cid: pos for cid, pos in ses.exec(select(Chunk.id, Chunk.book_pos)).all()}


def order_key(node: Node, chunk_pos: dict[int, int]) -> tuple[int, int, int]:
    """Ordine 'naturale' di un nodo: documento → posizione del chunk → id."""
    return (node.document_id or 0, chunk_pos.get(node.chunk_id or -1, 0), node.id or 0)


def load_nodes(db_path: Path) -> list[Node]:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        return list(ses.exec(select(Node)))


def persist_outline(
    db_path: Path,
    name: str,
    ordering: str,
    groups: list[tuple[str, list[Node]]],
    *,
    render_mode: str = "verbatim",
) -> OrganizeReport:
    """Crea un Outline con i capitoli indicati. ``groups`` = lista di ``(titolo, nodi ordinati)``."""
    groups = [(t, ns) for t, ns in groups if ns]
    engine = make_engine(db_path)
    with Session(engine) as ses:
        outline = Outline(name=name, ordering=ordering, render_mode=render_mode, status="draft")
        ses.add(outline)
        ses.commit()
        ses.refresh(outline)
        outline_id = outline.id
        n_items = 0
        for pos, (title, nodes) in enumerate(groups):
            chapter = OutlineChapter(outline_id=outline_id, position=pos, title=title)
            ses.add(chapter)
            ses.commit()
            ses.refresh(chapter)
            ses.add_all(
                [OutlineItem(chapter_id=chapter.id, position=i, node_id=n.id) for i, n in enumerate(nodes)]
            )
            ses.commit()
            n_items += len(nodes)
    return OrganizeReport(outline_id=outline_id, chapters=len(groups), items=n_items)
