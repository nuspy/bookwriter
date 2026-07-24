"""Organizzazione **tematica**: raggruppa i nodi in capitoli per argomento.

Clustering per embedding (KMeans, §clustering) o community detection sul grafo. I titoli derivano
dai temi dominanti, opzionalmente rifiniti dall'LLM. L'ordine interno rispetta la posizione originale.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Protocol

import networkx as nx
from sqlmodel import Session, select

from bookwriter.db.project_schema import Edge, Node, make_engine
from bookwriter.memory.vectors import LanceStore
from bookwriter.organize.clustering import cluster_ids
from bookwriter.organize.common import (
    OrganizeReport,
    chunk_positions,
    load_nodes,
    order_key,
    persist_outline,
)


class SupportsChat(Protocol):
    def chat(self, messages: list[dict[str, str]], *, model: str | None = ..., max_tokens: int | None = ...) -> str:
        ...


def _detect_communities(node_ids: list[int], edges: list[tuple[int, int, float]]) -> list[list[int]]:
    graph = nx.Graph()
    graph.add_nodes_from(node_ids)
    for a, b, w in edges:
        graph.add_edge(a, b, weight=max(w, 1e-6))
    if graph.number_of_edges() == 0 or graph.number_of_nodes() <= 2:
        return [list(node_ids)]
    try:
        communities = nx.community.greedy_modularity_communities(graph, weight="weight")
        return [sorted(c) for c in communities]
    except Exception:  # noqa: BLE001 - fallback robusto
        return [list(node_ids)]


def _group_node_ids(
    node_ids: list[int],
    edges: list[tuple[int, int, float]],
    vectors_path: Path | None,
    *,
    method: str,
    chapters: int | None,
) -> list[list[int]]:
    if method == "embedding" and vectors_path is not None:
        vectors = LanceStore(vectors_path).all_vectors("node")
        if vectors:
            vec_ids = [i for i, _ in vectors]
            groups = cluster_ids(vec_ids, [v for _, v in vectors], chapters=chapters)
            leftover = [i for i in node_ids if i not in set(vec_ids)]
            if leftover:
                groups.append(leftover)
            return groups
    return _detect_communities(node_ids, edges)


def chapter_title(nodes: list[Node], client: SupportsChat | None, model: str | None) -> str:
    themes = Counter(t for n in nodes for t in (n.themes or []))
    base = " / ".join(t for t, _ in themes.most_common(2)) or "Varie"
    if client is None:
        return base
    summaries = "; ".join(n.summary for n in nodes[:8] if n.summary)
    if not summaries:
        return base
    try:
        title = client.chat(
            [
                {"role": "system", "content": "Genera un titolo di capitolo breve (max 6 parole), in italiano, senza virgolette."},
                {"role": "user", "content": summaries},
            ],
            model=model,
            max_tokens=24,
        )
        return title.strip().strip('"').splitlines()[0][:80] or base
    except Exception:  # noqa: BLE001
        return base


def organize_thematic(
    db_path: Path,
    vectors_path: Path | None = None,
    *,
    method: str = "embedding",
    chapters: int | None = None,
    name: str = "Outline tematico",
    client: SupportsChat | None = None,
    model: str | None = None,
) -> OrganizeReport:
    nodes = load_nodes(db_path)
    if not nodes:
        raise ValueError("Nessun nodo: esegui prima `atomize` e `memory graph`.")
    chunk_pos = chunk_positions(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        edges = [
            (e.src_node, e.dst_node, e.weight)
            for e in ses.exec(select(Edge).where(Edge.source == "vector")).all()
        ]

    node_by_id = {n.id: n for n in nodes}
    id_groups = _group_node_ids([n.id for n in nodes], edges, vectors_path, method=method, chapters=chapters)

    groups: list[tuple[str, list[Node]]] = []
    for group in id_groups:
        members = [node_by_id[i] for i in group if i in node_by_id]
        members.sort(key=lambda n: order_key(n, chunk_pos))
        if members:
            groups.append((chapter_title(members, client, model), members))
    # Capitoli ordinati per salienza totale decrescente.
    groups.sort(key=lambda g: sum(n.salience for n in g[1]), reverse=True)
    return persist_outline(db_path, name, "thematic", groups)
