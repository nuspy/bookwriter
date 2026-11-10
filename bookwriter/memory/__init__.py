"""Memoria semantica: store vettoriale (LanceDB), embedding e ricerca."""

from bookwriter.memory.dedup import DedupReport, deduplicate_nodes, list_merges, restore_merge
from bookwriter.memory.embedder import (
    EmbedReport,
    SearchHit,
    embed_chunks,
    embed_nodes,
    search_chunks,
    search_nodes,
)
from bookwriter.memory.graph import GraphReport, build_graph, graph_stats
from bookwriter.memory.links import LinkReport, build_typed_links, contradictions, typed_links
from bookwriter.memory.vectors import LanceStore

__all__ = [
    "DedupReport",
    "EmbedReport",
    "GraphReport",
    "LanceStore",
    "LinkReport",
    "SearchHit",
    "build_graph",
    "build_typed_links",
    "contradictions",
    "deduplicate_nodes",
    "embed_chunks",
    "embed_nodes",
    "graph_stats",
    "list_merges",
    "restore_merge",
    "search_chunks",
    "search_nodes",
    "typed_links",
]
