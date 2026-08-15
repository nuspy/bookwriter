"""Suddivisione dei capitoli in **sezioni** (sotto-clustering dei nodi del capitolo).

Per ogni capitolo con abbastanza nodi, raggruppa i suoi nodi in sezioni (KMeans sugli embedding) e
assegna ``section_id`` alle voci. Rieseguibile: azzera le sezioni preesistenti dell'outline.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Node,
    OutlineChapter,
    OutlineItem,
    OutlineSection,
    make_engine,
)
from bookwriter.memory.vectors import LanceStore
from bookwriter.organize.clustering import cluster_ids
from bookwriter.organize.thematic import chapter_title


def subdivide_sections(
    db_path: Path,
    vectors_path: Path,
    outline_id: int,
    *,
    client=None,
    model: str | None = None,
    max_per_chapter: int | None = 4,
    min_nodes: int = 3,
    progress=None,
) -> dict:
    engine = make_engine(db_path)
    vec_map = dict(LanceStore(vectors_path).all_vectors("node"))

    with Session(engine) as ses:
        chapter_ids = list(
            ses.exec(
                select(OutlineChapter.id).where(OutlineChapter.outline_id == outline_id).order_by(OutlineChapter.position)
            )
        )
        # Azzera sezioni preesistenti.
        for it in ses.exec(select(OutlineItem).where(OutlineItem.chapter_id.in_(chapter_ids))).all():
            if it.section_id is not None:
                it.section_id = None
                ses.add(it)
        for sec in ses.exec(select(OutlineSection).where(OutlineSection.chapter_id.in_(chapter_ids))).all():
            ses.delete(sec)
        ses.commit()

    created = 0
    for done, ch_id in enumerate(chapter_ids, start=1):
        if progress:
            progress(done, len(chapter_ids))
        with Session(engine) as ses:
            items = ses.exec(
                select(OutlineItem.node_id, OutlineItem.position).where(OutlineItem.chapter_id == ch_id).order_by(OutlineItem.position)
            ).all()
        item_pos = {nid: pos for nid, pos in items}
        node_ids = [nid for nid, _ in items]
        vecs = [(nid, vec_map[nid]) for nid in node_ids if nid in vec_map]
        if len(vecs) < min_nodes:
            continue
        k = min(max_per_chapter, len(vecs)) if max_per_chapter else None
        groups = cluster_ids([i for i, _ in vecs], [v for _, v in vecs], chapters=k)
        if len(groups) <= 1:
            continue
        groups.sort(key=lambda g: min(item_pos.get(n, 0) for n in g))

        with Session(engine) as ses:
            for pos, group in enumerate(groups):
                nodes = [n for n in (ses.get(Node, nid) for nid in group) if n is not None]
                section = OutlineSection(chapter_id=ch_id, position=pos, title=chapter_title(nodes, client, model))
                ses.add(section)
                ses.commit()
                ses.refresh(section)
                section_id = section.id
                group_set = set(group)
                for it in ses.exec(select(OutlineItem).where(OutlineItem.chapter_id == ch_id)).all():
                    if it.node_id in group_set:
                        it.section_id = section_id
                        ses.add(it)
                ses.commit()
                created += 1
    return {"sections": created}
