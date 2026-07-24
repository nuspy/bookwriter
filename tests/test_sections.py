"""Test della suddivisione dei capitoli in sezioni."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    init_project_db,
    make_engine,
)
from bookwriter.exporter import outline_to_markdown
from bookwriter.memory.vectors import LanceStore
from bookwriter.organize.sections import subdivide_sections


def _seed(db_path: Path, vectors: Path) -> int:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        nodes = [Node(raw_text=f"Nodo {i}", summary=f"Nodo {i}") for i in range(6)]
        ses.add_all(nodes)
        ses.commit()
        for n in nodes:
            ses.refresh(n)
        o = Outline(name="O")
        ses.add(o)
        ses.commit()
        ses.refresh(o)
        ch = OutlineChapter(outline_id=o.id, position=0, title="Cap")
        ses.add(ch)
        ses.commit()
        ses.refresh(ch)
        for pos, n in enumerate(nodes):
            ses.add(OutlineItem(chapter_id=ch.id, position=pos, node_id=n.id))
        ses.commit()
        node_ids = [n.id for n in nodes]
        outline_id = o.id
    # Vettori: due sotto-gruppi netti.
    store = LanceStore(vectors)
    store.add("node", [
        {"id": node_ids[0], "text": "a", "vector": [1.0, 0.0]},
        {"id": node_ids[1], "text": "a", "vector": [0.95, 0.05]},
        {"id": node_ids[2], "text": "a", "vector": [0.9, 0.1]},
        {"id": node_ids[3], "text": "b", "vector": [0.0, 1.0]},
        {"id": node_ids[4], "text": "b", "vector": [0.05, 0.95]},
        {"id": node_ids[5], "text": "b", "vector": [0.1, 0.9]},
    ])
    return outline_id


def test_subdivide_and_render(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    vectors = tmp_path / "v.lance"
    oid = _seed(db, vectors)
    report = subdivide_sections(db, vectors, oid, client=None, max_per_chapter=2)
    assert report["sections"] >= 2

    engine = make_engine(db)
    with Session(engine) as ses:
        items = ses.exec(select(OutlineItem)).all()
    assert all(it.section_id is not None for it in items)  # tutti assegnati a una sezione

    md = outline_to_markdown(db, oid)
    assert "### " in md  # le sezioni compaiono come sotto-intestazioni
    assert md.count("Nodo") == 6  # tutti i nodi presenti
