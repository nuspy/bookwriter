"""Ordinamenti alternativi: cronologico, narrativo (arco) e personalizzato (criterio libero).

Tutti producono un :class:`OrganizeReport` persistendo un Outline con l'``ordering`` corrispondente,
riusando :func:`persist_outline`. Le date sono già estratte nei nodi (``event_date``).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from bookwriter.db.project_schema import Node
from bookwriter.memory.vectors import LanceStore
from bookwriter.organize.clustering import cluster_ids
from bookwriter.organize.common import (
    OrganizeReport,
    chunk_positions,
    load_nodes,
    order_key,
    persist_outline,
)
from bookwriter.organize.thematic import chapter_title, style_note


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"group": {"type": "integer"}, "title": {"type": "string"}},
                "required": ["group", "title"],
            },
        }
    },
    "required": ["chapters"],
}

CUSTOM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "node_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "node_ids"],
            },
        }
    },
    "required": ["chapters"],
}


def _year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    match = re.search(r"\d{4}", date_str)
    return int(match.group()) if match else None


def _split_contiguous(items: list, k: int) -> list[list]:
    n = len(items)
    k = max(1, min(k, n))
    out = []
    for i in range(k):
        start, end = round(i * n / k), round((i + 1) * n / k)
        if items[start:end]:
            out.append(items[start:end])
    return out


# --------------------------------------------------------------------------- #
#  cronologico
# --------------------------------------------------------------------------- #
def organize_chronological(
    db_path: Path,
    *,
    name: str = "Outline cronologico",
    chapters: int | None = None,
) -> OrganizeReport:
    nodes = load_nodes(db_path)
    if not nodes:
        raise ValueError("Nessun nodo: esegui prima `atomize`.")
    chunk_pos = chunk_positions(db_path)
    dated = [(n, _year(n.event_date)) for n in nodes]
    have = sorted(((y, n) for n, y in dated if y is not None), key=lambda x: (x[0], order_key(x[1], chunk_pos)))
    undated = [n for n, y in dated if y is None]

    groups: list[tuple[str, list[Node]]] = []
    if have:
        ordered_nodes = [n for _, n in have]
        k = chapters or min(6, len(ordered_nodes))
        for chunk in _split_contiguous(list(zip([y for y, _ in have], ordered_nodes)), k):
            years = [y for y, _ in chunk]
            members = [n for _, n in chunk]
            title = f"{years[0]}" if years[0] == years[-1] else f"{years[0]}–{years[-1]}"
            groups.append((title, members))
    if undated:
        undated.sort(key=lambda n: order_key(n, chunk_pos))
        groups.append(("Senza collocazione temporale", undated))
    return persist_outline(db_path, name, "chronological", groups)


# --------------------------------------------------------------------------- #
#  narrativo (arco costruito dall'LLM)
# --------------------------------------------------------------------------- #
def organize_narrative(
    db_path: Path,
    vectors_path: Path,
    client: SupportsChatJson,
    *,
    name: str = "Outline narrativo",
    chapters: int | None = None,
    model: str | None = None,
    style_contract: str = "",
    style_profile_id: int | None = None,
) -> OrganizeReport:
    nodes = load_nodes(db_path)
    if not nodes:
        raise ValueError("Nessun nodo: esegui prima `atomize` e `memory graph`.")
    chunk_pos = chunk_positions(db_path)
    node_by_id = {n.id: n for n in nodes}

    vectors = LanceStore(vectors_path).all_vectors("node")
    if vectors:
        id_groups = cluster_ids([i for i, _ in vectors], [v for _, v in vectors], chapters=chapters)
    else:
        id_groups = [[n.id for n in nodes]]
    clusters = [[node_by_id[i] for i in g if i in node_by_id] for g in id_groups]
    clusters = [c for c in clusters if c]

    desc = [{"index": idx, "summary": "; ".join(n.summary for n in c[:5] if n.summary)} for idx, c in enumerate(clusters)]
    result = client.chat_json(
        [
            {
                "role": "system",
                "content": style_note(style_contract, "i titoli") + (
                    "Ordina i seguenti gruppi tematici in un ARCO NARRATIVO (impostazione → sviluppo "
                    "→ culmine → risoluzione). Per ciascuno indica 'group' (l'indice) e un 'title' evocativo."
                ),
            },
            {"role": "user", "content": str(desc)},
        ],
        schema=NARRATIVE_SCHEMA,
        model=model,
    )

    groups: list[tuple[str, list[Node]]] = []
    used: set[int] = set()
    for ch in result.get("chapters", []):
        gi = ch.get("group")
        if isinstance(gi, int) and 0 <= gi < len(clusters) and gi not in used:
            used.add(gi)
            members = sorted(clusters[gi], key=lambda n: order_key(n, chunk_pos))
            groups.append((str(ch.get("title") or chapter_title(members, None, None)), members))
    for idx, c in enumerate(clusters):  # eventuali gruppi non ordinati dall'LLM
        if idx not in used:
            members = sorted(c, key=lambda n: order_key(n, chunk_pos))
            groups.append((chapter_title(members, None, None), members))
    return persist_outline(db_path, name, "narrative", groups, style_profile_id=style_profile_id)


# --------------------------------------------------------------------------- #
#  personalizzato (criterio in linguaggio naturale)
# --------------------------------------------------------------------------- #
def organize_custom(
    db_path: Path,
    client: SupportsChatJson,
    criterion: str,
    *,
    name: str = "Outline personalizzato",
    model: str | None = None,
    style_contract: str = "",
    style_profile_id: int | None = None,
) -> OrganizeReport:
    nodes = load_nodes(db_path)
    if not nodes:
        raise ValueError("Nessun nodo: esegui prima `atomize`.")
    node_by_id = {n.id: n for n in nodes}
    chunk_pos = chunk_positions(db_path)
    node_list = [{"id": n.id, "summary": (n.summary or n.raw_text)[:100]} for n in nodes]

    result = client.chat_json(
        [
            {
                "role": "system",
                "content": style_note(style_contract, "i titoli dei capitoli") + (
                    f"Raggruppa e ordina i nodi in capitoli secondo questo criterio: «{criterion}». "
                    f"Ogni nodo in UN solo capitolo, usando gli 'id' forniti; dai a ogni capitolo un 'title'. "
                    f"L'ordine dei node_ids nel capitolo definisce la sequenza."
                ),
            },
            {"role": "user", "content": str(node_list)},
        ],
        schema=CUSTOM_SCHEMA,
        model=model,
    )

    groups: list[tuple[str, list[Node]]] = []
    used: set[int] = set()
    for ch in result.get("chapters", []):
        ids = [i for i in ch.get("node_ids", []) if i in node_by_id and i not in used]
        used.update(ids)
        members = [node_by_id[i] for i in ids]  # ordine dettato dall'LLM (criterio)
        if members:
            groups.append((str(ch.get("title") or "Capitolo"), members))
    leftover = [node_by_id[i] for i in node_by_id if i not in used]
    if leftover:
        leftover.sort(key=lambda n: order_key(n, chunk_pos))
        groups.append(("Altro", leftover))
    return persist_outline(db_path, name, "custom", groups, style_profile_id=style_profile_id)
