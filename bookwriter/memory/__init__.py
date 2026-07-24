"""Memoria semantica: store vettoriale (LanceDB), embedding e ricerca."""

from bookwriter.memory.embedder import (
    EmbedReport,
    SearchHit,
    embed_chunks,
    embed_nodes,
    search_chunks,
    search_nodes,
)
from bookwriter.memory.graph import GraphReport, build_graph, graph_stats
from bookwriter.memory.vectors import LanceStore

__all__ = [
    "EmbedReport",
    "GraphReport",
    "LanceStore",
    "SearchHit",
    "build_graph",
    "embed_chunks",
    "embed_nodes",
    "graph_stats",
    "search_chunks",
    "search_nodes",
]
