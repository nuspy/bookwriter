"""Test della memoria semantica: LanceStore, embedding idempotente e ricerca.

Usa un client di embedding **fittizio e deterministico** (nessuna rete): ogni testo diventa un
vettore [#a, #b, #c] sul conteggio delle lettere, così la vicinanza coseno è prevedibile.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from bookwriter.db.project_schema import Chunk, Document, init_project_db, make_engine
from bookwriter.memory.embedder import embed_chunks, search_chunks
from bookwriter.memory.vectors import LanceStore


class FakeEmbedClient:
    """embed(text) -> [conteggio 'a', 'b', 'c'] (+1 per evitare vettori nulli)."""

    def embed(self, texts, *, kind="document", model=None):
        return [
            [float(t.count("a") + 1), float(t.count("b") + 1), float(t.count("c") + 1)]
            for t in texts
        ]


def _seed_chunks(db_path: Path, texts: list[str]) -> None:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="deadbeef", title="Doc")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        for i, text in enumerate(texts):
            ses.add(Chunk(document_id=doc.id, book_pos=i, text=text, sha256=f"h{i}"))
        ses.commit()


def test_lancestore_add_search(tmp_path: Path) -> None:
    store = LanceStore(tmp_path / "v.lance")
    assert store.existing_ids("chunk") == set()
    store.add(
        "chunk",
        [
            {"id": 1, "text": "aaa", "vector": [3.0, 0.0, 0.0]},
            {"id": 2, "text": "bbb", "vector": [0.0, 3.0, 0.0]},
        ],
    )
    assert store.existing_ids("chunk") == {1, 2}
    assert store.count("chunk") == 2
    hits = store.search("chunk", [5.0, 0.0, 0.0], k=1)
    assert hits[0]["id"] == 1


def test_embed_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    vectors = tmp_path / "v.lance"
    _seed_chunks(db, ["aaaa", "bbbb", "cccc"])
    client = FakeEmbedClient()

    r1 = embed_chunks(db, vectors, client)
    assert r1.embedded == 3 and r1.already == 0
    assert LanceStore(vectors).count("chunk") == 3

    r2 = embed_chunks(db, vectors, client)  # nulla di nuovo
    assert r2.embedded == 0 and r2.already == 3
    assert LanceStore(vectors).count("chunk") == 3


def test_search_returns_relevant(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    vectors = tmp_path / "v.lance"
    _seed_chunks(db, ["aaaaa molto a", "bbbbb molto b", "ccccc molto c"])
    client = FakeEmbedClient()
    embed_chunks(db, vectors, client)

    hits = search_chunks(db, vectors, client, "aaaaaaa", k=3)
    assert hits, "attesi risultati"
    assert "a" in hits[0].text  # il chunk ricco di 'a' è il più vicino
    assert hits[0].document_title == "Doc"


def test_embedding_rebuilds_index_when_model_changes(tmp_path) -> None:
    """Cambiando modello i vettori vecchi non sono confrontabili: l'indice va rifatto."""
    from bookwriter.memory.embedder import _embed_items
    from bookwriter.memory.vectors import LanceStore

    class FixedDim:
        def __init__(self, dim: int) -> None:
            self.dim = dim

        def embed(self, texts, *, kind="document", model=None):
            return [[0.1] * self.dim for _ in texts]

    vectors = tmp_path / "v.lance"
    store = LanceStore(vectors)
    items = [(1, "uno"), (2, "due")]

    first = _embed_items(store, "chunk", items, FixedDim(768), 32, 1, None, vectors, "nomic-v1")
    assert first.embedded == 2 and first.rebuilt is False
    assert store.dimension("chunk") == 768

    # Stesso modello: niente da rifare.
    again = _embed_items(store, "chunk", items, FixedDim(768), 32, 1, None, vectors, "nomic-v1")
    assert again.embedded == 0 and again.rebuilt is False

    # Modello diverso ma stessa dimensione: si riconosce dal nome registrato.
    renamed = _embed_items(store, "chunk", items, FixedDim(768), 32, 1, None, vectors, "nomic-v2")
    assert renamed.rebuilt is True and renamed.embedded == 2

    # Dimensione diversa: rete di sicurezza anche senza nome del modello.
    switched = _embed_items(store, "chunk", items, FixedDim(1024), 32, 1, None, vectors)
    assert switched.rebuilt is True and switched.embedded == 2
    assert store.dimension("chunk") == 1024
    assert "indice rifatto" in switched.summary()
