"""Riscrittura deterministica di un libro (modalità B) e traduzione letteraria (C)."""

from bookwriter.rewrite.engine import (
    LayoutAssembly,
    RewriteReport,
    answer_idioms,
    assemble_layout,
    assemble_markdown,
    idiom_decisions,
    preview_pairs,
    rewrite_document,
    run_record,
    write_result,
)
from bookwriter.rewrite.literary import FORM_KEEP, FORM_PROSE_FROM_VERSE, FORMS

__all__ = [
    "FORMS",
    "FORM_KEEP",
    "FORM_PROSE_FROM_VERSE",
    "LayoutAssembly",
    "RewriteReport",
    "answer_idioms",
    "assemble_layout",
    "assemble_markdown",
    "idiom_decisions",
    "preview_pairs",
    "rewrite_document",
    "run_record",
    "write_result",
]
