"""Organizzazione dei nodi in una struttura (outline) secondo diversi ordinamenti."""

from bookwriter.organize.common import OrganizeReport, style_contract_for
from bookwriter.organize.edit import list_outlines, outline_tree, rebuild_outline, rename_outline
from bookwriter.organize.orderings import (
    organize_chronological,
    organize_custom,
    organize_narrative,
)
from bookwriter.organize.purpose import (
    PurposeReport,
    chapter_guidance,
    order_by_links,
    organize_purpose,
    outline_plan,
)
from bookwriter.organize.sections import subdivide_sections
from bookwriter.organize.thematic import organize_thematic, retitle_outline

__all__ = [
    "OrganizeReport",
    "PurposeReport",
    "chapter_guidance",
    "list_outlines",
    "order_by_links",
    "organize_chronological",
    "organize_custom",
    "organize_narrative",
    "organize_purpose",
    "organize_thematic",
    "outline_plan",
    "outline_tree",
    "rebuild_outline",
    "rename_outline",
    "retitle_outline",
    "style_contract_for",
    "subdivide_sections",
]
