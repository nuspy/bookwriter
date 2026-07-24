"""Embedding dei chunk e ricerca semantica.

L'embedding è **incrementale/idempotente**: si calcolano solo i chunk non ancora presenti nello
store vettoriale (join per ``id``), a batch. La ricerca embedda la query con il prefisso nomic
``search_query:`` e recupera i chunk più vicini, arricchendoli coi metadati da project.db.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlmodel import Session, select

from bookwriter.concurrency import imap_unordered
from bookwriter.db.project_schema import Chunk, Document, Node, make_engine
from bookwriter.memory.vectors import LanceStore


class SupportsEmbed(Protocol):
    def embed(self, texts: list[str], *, kind: str = ..., model: str | None = ...) -> list[list[float]]:
        ...


@dataclass
class EmbedReport:
    embedded: int
    already: int
    batches: int

    def summary(self) -> str:
        return f"embeddati={self.embedded} · già presenti={self.already} · batch={self.batches}"


@dataclass
class SearchHit:
    chunk_id: int
    distance: float | None
    text: str
    document_title: str | None
    chapter_ref: str | None


def _embed_items(
    store: LanceStore,
    kind: str,
    items: list[tuple[int, str]],
    client: SupportsEmbed,
    batch_size: int,
    concurrency: int,
    progress,
) -> EmbedReport:
    existing = store.existing_ids(kind)
    todo = [(i, t) for i, t in items if i not in existing]
    total = len(todo)
    if not total:
        return EmbedReport(embedded=0, already=len(existing), batches=0)

    batches = [todo[s : s + batch_size] for s in range(0, total, batch_size)]
    done = 0
    # Le chiamate embed girano in parallelo; la scrittura su LanceDB resta nel thread principale.
    for batch, vectors in imap_unordered(
        lambda b: client.embed([text for _, text in b], kind="document"), batches, concurrency
    ):
        store.add(kind, [{"id": i, "text": text, "vector": vec} for (i, text), vec in zip(batch, vectors)])
        done += len(batch)
        if progress:
            progress(done, total)
    return EmbedReport(embedded=total, already=len(existing), batches=len(batches))


def embed_chunks(
    db_path: Path,
    vectors_path: Path,
    client: SupportsEmbed,
    *,
    batch_size: int = 32,
    concurrency: int = 4,
    progress=None,
) -> EmbedReport:
    store = LanceStore(vectors_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        items = [(cid, text) for cid, text in ses.exec(select(Chunk.id, Chunk.text)).all()]
    return _embed_items(store, "chunk", items, client, batch_size, concurrency, progress)


def _node_representation(node: Node) -> str:
    """Testo su cui embeddare un nodo: sintesi + temi + testo → clustering per argomento."""
    themes = " ".join(node.themes or [])
    parts = [node.summary or "", themes, node.raw_text or ""]
    return " ".join(p for p in parts if p).strip() or node.raw_text


def embed_nodes(
    db_path: Path,
    vectors_path: Path,
    client: SupportsEmbed,
    *,
    batch_size: int = 32,
    concurrency: int = 4,
    progress=None,
) -> EmbedReport:
    """Embedding dei nodi (sintesi+temi+testo) per clustering tematico e grafo."""
    store = LanceStore(vectors_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        items = [(n.id, _node_representation(n)) for n in ses.exec(select(Node)).all()]
    return _embed_items(store, "node", items, client, batch_size, concurrency, progress)


def search_nodes(
    db_path: Path,
    vectors_path: Path,
    client: SupportsEmbed,
    query: str,
    *,
    k: int = 8,
) -> list[dict]:
    """Ricerca semantica sui nodi (idee atomiche). Ritorna dict con sintesi/testo/temi."""
    store = LanceStore(vectors_path)
    hits = store.search("node", client.embed([query], kind="query")[0], k=k)
    if not hits:
        return []
    ids = [h["id"] for h in hits]
    engine = make_engine(db_path)
    with Session(engine) as ses:
        node_map = {n.id: n for n in ses.exec(select(Node).where(Node.id.in_(ids)))}
    out = []
    for hit in hits:
        node = node_map.get(hit["id"])
        if node is None:
            continue
        out.append(
            {"node_id": node.id, "distance": hit["distance"], "summary": node.summary,
             "text": node.raw_text, "themes": node.themes}
        )
    return out


def search_chunks(
    db_path: Path,
    vectors_path: Path,
    client: SupportsEmbed,
    query: str,
    *,
    k: int = 8,
) -> list[SearchHit]:
    store = LanceStore(vectors_path)
    query_vec = client.embed([query], kind="query")[0]
    hits = store.search("chunk", query_vec, k=k)
    if not hits:
        return []

    ids = [h["id"] for h in hits]
    engine = make_engine(db_path)
    with Session(engine) as ses:
        chunk_map = {c.id: c for c in ses.exec(select(Chunk).where(Chunk.id.in_(ids)))}
        doc_titles = {d.id: d.title for d in ses.exec(select(Document))}

    out: list[SearchHit] = []
    for hit in hits:
        chunk = chunk_map.get(hit["id"])
        if chunk is None:
            continue
        out.append(
            SearchHit(
                chunk_id=chunk.id,
                distance=hit["distance"],
                text=chunk.text,
                document_title=doc_titles.get(chunk.document_id),
                chapter_ref=chunk.chapter_ref,
            )
        )
    return out
