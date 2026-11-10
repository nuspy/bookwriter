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
    style_profile_id: int | None = None,
) -> OrganizeReport:
    """Crea un Outline con i capitoli indicati. ``groups`` = lista di ``(titolo, nodi ordinati)``.

    ``style_profile_id`` ricorda con quale stile sono stati scritti titoli e motivazioni: la
    stesura può riproporlo come scelta iniziale.
    """
    groups = [(t, ns) for t, ns in groups if ns]
    engine = make_engine(db_path)
    with Session(engine) as ses:
        outline = Outline(
            name=name, ordering=ordering, render_mode=render_mode, status="draft",
            style_profile_id=style_profile_id,
        )
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


def style_contract_for(db_path: Path, style_id: int | str | None) -> str:
    """Il contratto di stile di un profilo del progetto, o ``""`` se non c'è o non si legge.

    È lo stesso testo che la stesura usa come istruzione di sistema: riordinare e scrivere con
    lo stesso contratto tiene titoli, transizioni e prosa nello stesso registro.
    """
    if style_id in (None, ""):
        return ""
    from bookwriter.db.project_schema import get_style_row
    from bookwriter.style import StyleProfile, build_style_contract

    row = get_style_row(db_path, style_id)
    if not row:
        return ""
    try:
        return build_style_contract(StyleProfile.model_validate(row["data"]))
    except Exception:  # noqa: BLE001 - un profilo illeggibile non deve fermare il riordino
        return ""
