"""Costruzione del grafo dei nodi via similarità vettoriale (top-k), senza confronti O(n²).

Per ogni nodo si cercano i ``k`` vicini semantici nello store vettoriale e si creano archi
``thematic`` con peso = similarità coseno. Idempotente: ricostruisce gli archi con ``source=vector``.
La classificazione fine del tipo di arco via LLM è una possibile evoluzione (vedi PLAN §4.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, func, select

from bookwriter.db.project_schema import Edge, Node, make_engine
from bookwriter.memory.vectors import LanceStore


@dataclass
class GraphReport:
    nodes: int
    edges: int

    def summary(self) -> str:
        return f"nodi={self.nodes} · archi={self.edges}"


def build_graph(
    db_path: Path,
    vectors_path: Path,
    *,
    k: int = 8,
    min_weight: float = 0.15,
) -> GraphReport:
    store = LanceStore(vectors_path)
    vectors = store.all_vectors("node")
    engine = make_engine(db_path)

    # Rebuild idempotente: rimuovi gli archi generati da vettori.
    with Session(engine) as ses:
        for edge in ses.exec(select(Edge).where(Edge.source == "vector")).all():
            ses.delete(edge)
        ses.commit()

    best: dict[tuple[int, int], float] = {}
    for nid, vec in vectors:
        for hit in store.search("node", vec, k=k + 1):
            oid = hit["id"]
            if oid == nid:
                continue
            a, b = (nid, oid) if nid < oid else (oid, nid)
            weight = 1.0 - float(hit["distance"]) if hit["distance"] is not None else 0.0
            if (a, b) not in best or weight > best[(a, b)]:
                best[(a, b)] = weight

    edges = [
        Edge(src_node=a, dst_node=b, type="thematic", weight=round(w, 4), source="vector")
        for (a, b), w in best.items()
        if w >= min_weight
    ]
    with Session(engine) as ses:
        ses.add_all(edges)
        ses.commit()
    return GraphReport(nodes=len(vectors), edges=len(edges))


def graph_stats(db_path: Path) -> dict:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        n_nodes = ses.exec(select(func.count()).select_from(Node)).one()
        n_edges = ses.exec(select(func.count()).select_from(Edge)).one()
    return {"nodes": int(n_nodes), "edges": int(n_edges)}
