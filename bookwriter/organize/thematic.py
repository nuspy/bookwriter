"""Organizzazione **tematica**: raggruppa i nodi in capitoli per argomento.

Clustering per embedding (KMeans, §clustering) o community detection sul grafo. I titoli derivano
dai temi dominanti, opzionalmente rifiniti dall'LLM. L'ordine interno rispetta la posizione originale.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Protocol

import networkx as nx
from sqlmodel import Session, select

from bookwriter.config import language_label
from bookwriter.db.project_schema import (
    Edge,
    Node,
    OutlineChapter,
    OutlineItem,
    make_engine,
)
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
    def chat(self, messages: list[dict[str, str]], *, model: str | None = ...) -> str:
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


def _clean_title(raw: str, base: str) -> str:
    """Estrae il titolo dalla risposta: i modelli che ragionano premettono un blocco <think>."""
    text = re.sub(r"<think>.*?</think>", " ", raw or "", flags=re.DOTALL)
    lines = [line.strip().strip('"').strip("«»").strip() for line in text.splitlines()]
    candidates = [line for line in lines if line and not line.startswith(("<", "#"))]
    return candidates[-1][:80] if candidates else base


def chapter_title(
    nodes: list[Node], client: SupportsChat | None, model: str | None, language: str = ""
) -> str:
    """Titolo del capitolo: dal modello se disponibile, altrimenti i due temi dominanti."""
    themes = Counter(t for n in nodes for t in (n.themes or []))
    base = " / ".join(t for t, _ in themes.most_common(2)) or "Varie"
    if client is None:
        return base
    summaries = "; ".join(n.summary for n in nodes[:8] if n.summary)
    if not summaries:
        return base
    lingua = language_label(language) if language else "la stessa lingua dei testi"
    try:
        title = client.chat(
            [
                {
                    "role": "system",
                    "content": (
                        f"Genera un titolo di capitolo breve (max 6 parole) in {lingua}, senza "
                        "virgolette e senza spiegazioni: rispondi solo con il titolo."
                    ),
                },
                {"role": "user", "content": summaries},
            ],
            model=model,
            # Nessun tetto ai token e ragionamento pieno: il titolo è la prima cosa che il
            # lettore vede di un capitolo, e un limite stretto lascerebbe la risposta vuota.
        )
        return _clean_title(title, base)
    except Exception:  # noqa: BLE001
        return base


def retitle_outline(
    db_path: Path, outline_id: int, client: SupportsChat, *, model: str | None = None,
    language: str = "", progress=None,
) -> int:
    """Rigenera i titoli dei capitoli di una struttura esistente, senza toccarne il contenuto."""
    engine = make_engine(db_path)
    # Prima si legge tutto e si chiude: tenere una transazione aperta durante chiamate lunghe
    # al modello terrebbe bloccato il database per minuti.
    with Session(engine) as ses:
        chapters = list(
            ses.exec(
                select(OutlineChapter)
                .where(OutlineChapter.outline_id == outline_id)
                .order_by(OutlineChapter.position)
            )
        )
        materiale: list[tuple[int, list[Node]]] = []
        for chapter in chapters:
            node_ids = [
                item.node_id
                for item in ses.exec(select(OutlineItem).where(OutlineItem.chapter_id == chapter.id))
            ]
            nodes = list(ses.exec(select(Node).where(Node.id.in_(node_ids)))) if node_ids else []
            materiale.append((chapter.id, nodes))

    for done, (chapter_id, nodes) in enumerate(materiale, start=1):
        if nodes:
            titolo = chapter_title(nodes, client, model, language)
            # Una transazione breve per capitolo: il titolo è salvato appena pronto.
            with Session(engine) as ses:
                chapter = ses.get(OutlineChapter, chapter_id)
                if chapter is not None:
                    chapter.title = titolo
                    ses.add(chapter)
                    ses.commit()
        if progress:
            progress(done, len(materiale))
    return len(chapters)


def organize_thematic(
    db_path: Path,
    vectors_path: Path | None = None,
    *,
    method: str = "embedding",
    chapters: int | None = None,
    name: str = "Outline tematico",
    client: SupportsChat | None = None,
    model: str | None = None,
    language: str = "",
    progress=None,
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
    for done, group in enumerate(id_groups, start=1):
        members = [node_by_id[i] for i in group if i in node_by_id]
        members.sort(key=lambda n: order_key(n, chunk_pos))
        if members:
            groups.append((chapter_title(members, client, model, language), members))
        if progress:
            progress(done, len(id_groups))
    # Capitoli ordinati per salienza totale decrescente.
    groups.sort(key=lambda g: sum(n.salience for n in g[1]), reverse=True)
    return persist_outline(db_path, name, "thematic", groups)
