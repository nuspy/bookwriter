"""Atomizzazione: da chunk a nodi-idea con metadati (via LLM, structured output)."""

from bookwriter.atomize.nodes import NODES_SCHEMA, AtomizeReport, atomize_chunks

__all__ = ["NODES_SCHEMA", "AtomizeReport", "atomize_chunks"]
