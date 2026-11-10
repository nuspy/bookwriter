"""Ingestione: caricamento documenti, parsing strutturale, chunking, pipeline."""

from bookwriter.ingest.chunker import Chunk, chunk_document
from bookwriter.ingest.loaders import (
    FORMAT_GROUPS,
    SUPPORTED_EXTENSIONS,
    LoadedDoc,
    LoaderError,
    load_document,
)
from bookwriter.ingest.structure import Block, parse_structure

__all__ = [
    "FORMAT_GROUPS",
    "SUPPORTED_EXTENSIONS",
    "Block",
    "Chunk",
    "LoadedDoc",
    "LoaderError",
    "chunk_document",
    "load_document",
    "parse_structure",
]
