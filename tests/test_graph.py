"""Test della costruzione del grafo top-k (embedding fittizio, senza rete)."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from bookwriter.db.project_schema import Document, Node, init_project_db, make_engine
from bookwriter.memory.embedder import embed_nodes
from bookwriter.memory.graph import build_graph, graph_stats


class FakeEmbedClient:
    def embed(self, texts, *, kind="document", model=None):
        return [
            [float(t.count("a") + 1), float(t.count("b") + 1), float(t.count("c") + 1)]
            for t in texts
        ]


def _seed_nodes(db_path: Path, texts: list[str]) -> None:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        for t in texts:
            ses.add(Node(document_id=doc.id, raw_text=t, summary=t))
        ses.commit()


def test_build_graph_and_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    vectors = tmp_path / "v.lance"
    _seed_nodes(db, ["aaaa uno", "aaaa due", "bbbb uno", "bbbb due", "cccc uno", "cccc due"])
    client = FakeEmbedClient()
    embed_nodes(db, vectors, client)

    r1 = build_graph(db, vectors, k=2, min_weight=0.0)
    assert r1.nodes == 6
    assert r1.edges > 0
    stats = graph_stats(db)
    assert stats["nodes"] == 6
    assert stats["edges"] == r1.edges

    # Rebuild idempotente: non raddoppia gli archi.
    r2 = build_graph(db, vectors, k=2, min_weight=0.0)
    assert graph_stats(db)["edges"] == r2.edges == r1.edges
