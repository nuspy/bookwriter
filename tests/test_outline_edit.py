"""Test dell'editing manuale dell'outline (tree + rebuild)."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, func, select

from bookwriter.db.project_schema import (
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    init_project_db,
    make_engine,
)
from bookwriter.organize.edit import list_outlines, outline_tree, rebuild_outline


def _seed(db_path: Path) -> tuple[int, list[int]]:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        nodes = [Node(raw_text=f"Nodo {i}", summary=f"Nodo {i}") for i in range(4)]
        ses.add_all(nodes)
        ses.commit()
        for n in nodes:
            ses.refresh(n)
        outline = Outline(name="O")
        ses.add(outline)
        ses.commit()
        ses.refresh(outline)
        ch1 = OutlineChapter(outline_id=outline.id, position=0, title="Cap A")
        ch2 = OutlineChapter(outline_id=outline.id, position=1, title="Cap B")
        ses.add_all([ch1, ch2])
        ses.commit()
        for c in (ch1, ch2):
            ses.refresh(c)
        ses.add_all([
            OutlineItem(chapter_id=ch1.id, position=0, node_id=nodes[0].id),
            OutlineItem(chapter_id=ch1.id, position=1, node_id=nodes[1].id),
            OutlineItem(chapter_id=ch2.id, position=0, node_id=nodes[2].id),
            OutlineItem(chapter_id=ch2.id, position=1, node_id=nodes[3].id),
        ])
        ses.commit()
        return outline.id, [n.id for n in nodes]


def test_outline_tree(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    oid, node_ids = _seed(db)
    tree = outline_tree(db, oid)
    assert [c["title"] for c in tree] == ["Cap A", "Cap B"]
    assert [n["node_id"] for n in tree[0]["nodes"]] == node_ids[:2]
    assert list_outlines(db)[0]["name"] == "O"


def test_rebuild_reorder_move_remove_rename(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    oid, node_ids = _seed(db)
    # Nuova struttura: rinomina, riordina capitoli, sposta un nodo, rimuovine uno.
    rebuild_outline(db, oid, [
        {"title": "Nuovo B", "node_ids": [node_ids[2], node_ids[0]]},  # sposta nodo 0 qui
        {"title": "Nuovo A", "node_ids": [node_ids[1]]},              # nodo 3 rimosso
    ])
    tree = outline_tree(db, oid)
    assert [c["title"] for c in tree] == ["Nuovo B", "Nuovo A"]
    assert [n["node_id"] for n in tree[0]["nodes"]] == [node_ids[2], node_ids[0]]
    assert [n["node_id"] for n in tree[1]["nodes"]] == [node_ids[1]]

    engine = make_engine(db)
    with Session(engine) as ses:
        # Il nodo rimosso dall'outline esiste ancora in memoria.
        assert ses.exec(select(func.count()).select_from(Node)).one() == 4
        # Nessun capitolo orfano.
        assert ses.exec(select(func.count()).select_from(OutlineChapter)).one() == 2
