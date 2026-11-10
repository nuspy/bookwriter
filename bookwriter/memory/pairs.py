"""Coppie di idee vicine, da far giudicare al modello: il pezzo comune a deduplica e legami.

Confrontare ogni idea con tutte le altre costerebbe n² chiamate. I vettori servono a scegliere
**quali** coppie meritano una domanda: per ogni idea si prendono i pochi vicini più simili nello
store vettoriale, come fa già il grafo tematico. La vicinanza però dice solo che due idee parlano
della stessa cosa, non *che cosa* dicono: «il testo cita X» e «X è vero» stanno vicinissime. Per
questo la decisione spetta sempre al modello, che legge le due idee; il vettore propone e basta.

Le coppie già giudicate restano registrate (:class:`NodePairCheck`) con il loro esito, anche
negativo: rieseguire il lavoro chiede solo le coppie nuove.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import Node, NodePairCheck, ensure_notes_tables, make_engine
from bookwriter.memory.vectors import LanceStore


def neighbour_pairs(
    vectors_path: Path,
    valid_ids: set[int],
    *,
    k: int,
    min_similarity: float,
) -> dict[tuple[int, int], float]:
    """Coppie ``(a, b)`` con ``a < b`` fra ciascuna idea e i suoi ``k`` vicini, con la similarità.

    Si scartano le righe dello store che non corrispondono più a un'idea del progetto (idee
    tolte o fuse dopo l'embedding) e le coppie sotto ``min_similarity``.
    """
    store = LanceStore(vectors_path)
    best: dict[tuple[int, int], float] = {}
    for nid, vec in store.all_vectors("node"):
        if nid not in valid_ids:
            continue
        for hit in store.search("node", vec, k=k + 1):
            oid = hit["id"]
            if oid == nid or oid not in valid_ids or hit["distance"] is None:
                continue
            similarity = 1.0 - float(hit["distance"])
            if similarity < min_similarity:
                continue
            pair = (nid, oid) if nid < oid else (oid, nid)
            if similarity > best.get(pair, -1.0):
                best[pair] = similarity
    return best


def node_brief(node: Node, limit: int = 1200) -> str:
    """Come si presenta un'idea al modello: sintesi e testo, il testo tagliato se lunghissimo.

    Il testo serve: la sintesi da sola appiattisce le differenze («cita X» e «sostiene X»
    possono avere la stessa sintesi). Il taglio tiene la domanda dentro un contesto ragionevole
    quando un'idea è una sezione di centinaia di parole.
    """
    text = " ".join((node.raw_text or "").split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + " […]"
    summary = (node.summary or "").strip()
    return f"{summary}\n   Testo: «{text}»" if summary else f"«{text}»"


def judged_pairs(db_path: Path, kind: str) -> dict[tuple[int, int], str]:
    """Coppie già giudicate per ``kind`` (``duplicate`` o ``link``) con il loro esito."""
    engine = make_engine(db_path)
    ensure_notes_tables(engine)
    with Session(engine) as ses:
        rows = ses.exec(select(NodePairCheck).where(NodePairCheck.kind == kind)).all()
    return {(min(r.node_a, r.node_b), max(r.node_a, r.node_b)): r.verdict for r in rows}


def record_pairs(db_path: Path, kind: str, verdicts: dict[tuple[int, int], str]) -> None:
    """Registra gli esiti, sostituendo un eventuale giudizio precedente sulla stessa coppia."""
    if not verdicts:
        return
    engine = make_engine(db_path)
    ensure_notes_tables(engine)
    with Session(engine) as ses:
        existing = {
            (min(r.node_a, r.node_b), max(r.node_a, r.node_b)): r
            for r in ses.exec(select(NodePairCheck).where(NodePairCheck.kind == kind)).all()
        }
        for (a, b), verdict in verdicts.items():
            pair = (min(a, b), max(a, b))
            row = existing.get(pair) or NodePairCheck(kind=kind, node_a=pair[0], node_b=pair[1])
            row.verdict = verdict
            ses.add(row)
        ses.commit()


def forget_pairs(ses: Session, node_ids: set[int], kinds: tuple[str, ...] | None = None) -> None:
    """Dimentica i giudizi che coinvolgono queste idee: sono cambiate, vanno rigiudicate."""
    if not node_ids:
        return
    query = select(NodePairCheck).where(
        NodePairCheck.node_a.in_(node_ids) | NodePairCheck.node_b.in_(node_ids)
    )
    if kinds:
        query = query.where(NodePairCheck.kind.in_(kinds))
    for row in ses.exec(query).all():
        ses.delete(row)


def batched(items: list, size: int) -> list[list]:
    """Lotti di ``size`` elementi: ogni risposta del modello resta corta."""
    size = max(1, size)
    return [items[i : i + size] for i in range(0, len(items), size)]
