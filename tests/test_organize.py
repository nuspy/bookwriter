"""Test di organizzazione tematica ed export verbatim."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from bookwriter.db.project_schema import (
    Chunk,
    Document,
    Edge,
    Node,
    init_project_db,
    make_engine,
)
from bookwriter.exporter import export_outline, latest_outline_id, outline_to_markdown
from bookwriter.organize import (
    organize_chronological,
    organize_custom,
    organize_narrative,
    organize_thematic,
)


def _seed(db_path: Path) -> None:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        chunk = Chunk(document_id=doc.id, book_pos=0, text="c", sha256="c0")
        ses.add(chunk)
        ses.commit()
        ses.refresh(chunk)
        texts = ["Il gatto dorme.", "Il cane corre.", "Marte è rosso.", "Giove è grande."]
        themes = [["animali"], ["animali"], ["spazio"], ["spazio"]]
        nodes = []
        for t, th in zip(texts, themes):
            n = Node(document_id=doc.id, chunk_id=chunk.id, raw_text=t, summary=t, themes=th)
            ses.add(n)
            nodes.append(n)
        ses.commit()
        for n in nodes:
            ses.refresh(n)
        # Due community: (0,1) animali e (2,3) spazio.
        ses.add(Edge(src_node=nodes[0].id, dst_node=nodes[1].id, type="thematic", weight=0.9, source="vector"))
        ses.add(Edge(src_node=nodes[2].id, dst_node=nodes[3].id, type="thematic", weight=0.9, source="vector"))
        ses.commit()


def test_organize_creates_outline(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed(db)
    report = organize_thematic(db, client=None)
    assert report.items == 4
    assert report.chapters >= 1
    assert latest_outline_id(db) == report.outline_id


def test_markdown_is_verbatim(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed(db)
    report = organize_thematic(db, client=None)
    md = outline_to_markdown(db, report.outline_id)
    # Tutti i testi originali compaiono alla lettera.
    for text in ("Il gatto dorme.", "Il cane corre.", "Marte è rosso.", "Giove è grande."):
        assert text in md
    assert "## " in md  # almeno un capitolo


def test_organize_by_embedding(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    vectors = tmp_path / "v.lance"
    _seed(db)
    # Vettori dei nodi: due cluster netti (animali vs spazio).
    from bookwriter.memory.vectors import LanceStore

    store = LanceStore(vectors)
    store.add(
        "node",
        [
            {"id": 1, "text": "gatto", "vector": [1.0, 0.0]},
            {"id": 2, "text": "cane", "vector": [0.95, 0.05]},
            {"id": 3, "text": "marte", "vector": [0.0, 1.0]},
            {"id": 4, "text": "giove", "vector": [0.05, 0.95]},
        ],
    )
    report = organize_thematic(db, vectors, method="embedding", chapters=2, client=None)
    assert report.chapters == 2
    assert report.items == 4


class _FakeNarr:
    def chat_json(self, messages, *, schema, model=None):
        return {"chapters": [{"group": 0, "title": "Inizio"}, {"group": 1, "title": "Fine"}]}


class _FakeCustom:
    def chat_json(self, messages, *, schema, model=None):
        return {"chapters": [{"title": "Gruppo A", "node_ids": [1, 2]}, {"title": "Gruppo B", "node_ids": [3, 4]}]}


def _seed_dated(db_path: Path) -> None:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        for text, date in [("Nel 2019", "2019"), ("Nel 1999", "1999-05"), ("Nel 2010", "2010"), ("Senza data", None)]:
            ses.add(Node(document_id=doc.id, raw_text=text, summary=text, event_date=date))
        ses.commit()


def test_chronological(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed_dated(db)
    report = organize_chronological(db)
    assert report.items == 4
    md = outline_to_markdown(db, report.outline_id)
    assert "1999" in md and "Senza collocazione temporale" in md
    # Il primo capitolo è il più antico.
    assert md.index("1999") < md.index("2019")


def test_narrative(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    vectors = tmp_path / "v.lance"
    _seed(db)
    from bookwriter.memory.vectors import LanceStore

    LanceStore(vectors).add("node", [
        {"id": 1, "text": "a", "vector": [1.0, 0.0]},
        {"id": 2, "text": "a", "vector": [0.95, 0.05]},
        {"id": 3, "text": "b", "vector": [0.0, 1.0]},
        {"id": 4, "text": "b", "vector": [0.05, 0.95]},
    ])
    report = organize_narrative(db, vectors, _FakeNarr(), chapters=2)
    assert report.chapters == 2 and report.items == 4


def test_custom(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed(db)
    report = organize_custom(db, _FakeCustom(), "per lettera iniziale")
    assert report.chapters == 2 and report.items == 4
    md = outline_to_markdown(db, report.outline_id)
    assert "Gruppo A" in md and "Gruppo B" in md


def test_export_md_and_docx(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed(db)
    report = organize_thematic(db, client=None)

    md_path = export_outline(db, report.outline_id, tmp_path / "out.md", fmt="md")
    assert md_path.exists() and md_path.read_text(encoding="utf-8").strip()

    docx_path = export_outline(db, report.outline_id, tmp_path / "out.docx", fmt="docx")
    assert docx_path.exists() and docx_path.stat().st_size > 0
