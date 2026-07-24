"""Editing manuale degli outline: lettura ad albero e ricostruzione della struttura.

``outline_tree`` fornisce la struttura per la GUI; ``rebuild_outline`` la riscrive da una lista di
capitoli (titolo + node_ids ordinati) → copre riordino, spostamento nodi tra capitoli, rimozione,
rinomina e aggiunta capitoli in un'unica operazione. Rimuovere un nodo dall'outline NON cancella il
nodo dalla memoria.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    OutlineSection,
    make_engine,
)


def list_outlines(db_path: Path) -> list[dict]:
    with Session(make_engine(db_path)) as ses:
        rows = ses.exec(select(Outline).order_by(Outline.id)).all()
    return [
        {"id": r.id, "name": r.name, "ordering": r.ordering, "render_mode": r.render_mode, "status": r.status}
        for r in rows
    ]


def outline_tree(db_path: Path, outline_id: int) -> list[dict]:
    """Struttura ``[{chapter_id, title, nodes:[{node_id, summary}]}]`` per la visualizzazione."""
    engine = make_engine(db_path)
    with Session(engine) as ses:
        chapters = ses.exec(
            select(OutlineChapter).where(OutlineChapter.outline_id == outline_id).order_by(OutlineChapter.position)
        ).all()
        tree = []
        for ch in chapters:
            items = ses.exec(
                select(OutlineItem).where(OutlineItem.chapter_id == ch.id).order_by(OutlineItem.position)
            ).all()
            nodes = []
            for it in items:
                node = ses.get(Node, it.node_id)
                summary = (node.summary or node.raw_text)[:80] if node else "(nodo mancante)"
                nodes.append({"node_id": it.node_id, "summary": summary})
            tree.append({"chapter_id": ch.id, "title": ch.title, "nodes": nodes})
    return tree


def rebuild_outline(db_path: Path, outline_id: int, chapters: list[dict]) -> None:
    """Riscrive capitoli e nodi dell'outline da ``[{title, node_ids}]`` (posizioni dall'ordine)."""
    engine = make_engine(db_path)
    with Session(engine) as ses:
        existing = ses.exec(select(OutlineChapter).where(OutlineChapter.outline_id == outline_id)).all()
        for ch in existing:
            for it in ses.exec(select(OutlineItem).where(OutlineItem.chapter_id == ch.id)).all():
                ses.delete(it)
            for sec in ses.exec(select(OutlineSection).where(OutlineSection.chapter_id == ch.id)).all():
                ses.delete(sec)
            ses.delete(ch)
        ses.commit()

        for pos, ch in enumerate(chapters):
            chapter = OutlineChapter(outline_id=outline_id, position=pos, title=ch.get("title") or "Capitolo")
            ses.add(chapter)
            ses.commit()
            ses.refresh(chapter)
            items = [
                OutlineItem(chapter_id=chapter.id, position=i, node_id=nid)
                for i, nid in enumerate(ch.get("node_ids") or [])
            ]
            if items:
                ses.add_all(items)
                ses.commit()
