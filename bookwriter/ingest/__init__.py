"""Ingestione: caricamento documenti, parsing strutturale, chunking, pipeline."""

from bookwriter.ingest.chunker import Chunk, chunk_document
from bookwriter.ingest.loaders import SUPPORTED_EXTENSIONS, LoadedDoc, load_document
from bookwriter.ingest.structure import Block, parse_structure

__all__ = [
    "SUPPORTED_EXTENSIONS",
    "Block",
    "Chunk",
    "LoadedDoc",
    "chunk_document",
    "load_document",
    "parse_structure",
]
