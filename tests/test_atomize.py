"""Test dell'atomizzazione chunk→nodi (client chat fittizio, senza rete)."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.atomize.nodes import _snap_verbatim, atomize_chunks
from bookwriter.db.project_schema import Chunk, Document, Node, init_project_db, make_engine

CHUNK_TEXT = "Il cielo è azzurro oggi. Domani pioverà molto forte."


class FakeChatClient:
    """Ritorna due nodi con excerpt noti (uno esatto, uno con spazi alterati)."""

    def chat_json(self, messages, *, schema, model=None):
        return {
            "nodes": [
                {
                    "excerpt": "Il cielo è azzurro oggi.",
                    "summary": "meteo di oggi",
                    "type": "fatto",
                    "themes": ["meteo"],
                    "entities": [],
                    "event_date": None,
                    "salience": 0.7,
                },
                {
                    "excerpt": "Domani   pioverà molto forte.",  # doppi spazi apposta
                    "summary": "previsione domani",
                    "type": "fatto",
                    "themes": ["meteo"],
                    "entities": [],
                    "event_date": None,
                    "salience": 0.6,
                },
            ]
        }


def _seed_chunk(db_path: Path) -> None:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        ses.add(Chunk(document_id=doc.id, book_pos=0, text=CHUNK_TEXT, sha256="c0"))
        ses.commit()


def test_snap_verbatim() -> None:
    assert _snap_verbatim(CHUNK_TEXT, "Il cielo è azzurro oggi.") == "Il cielo è azzurro oggi."
    # Con spazi diversi, aggancia comunque al testo originale.
    assert _snap_verbatim(CHUNK_TEXT, "Domani   pioverà molto forte.") == "Domani pioverà molto forte."
    # Parafrasi (non presente): ripiega sulla frase originale più sovrapposta.
    assert _snap_verbatim(CHUNK_TEXT, "Il cielo appare azzurro") == "Il cielo è azzurro oggi."


def test_atomize_creates_nodes_and_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed_chunk(db)
    client = FakeChatClient()

    r1 = atomize_chunks(db, client)
    assert r1.chunks_done == 1 and r1.nodes_created == 2

    engine = make_engine(db)
    with Session(engine) as ses:
        nodes = ses.exec(select(Node)).all()
    assert len(nodes) == 2
    assert all(n.chunk_id is not None for n in nodes)
    # Testo verbatim agganciato all'originale.
    assert nodes[1].raw_text == "Domani pioverà molto forte."

    r2 = atomize_chunks(db, client)  # già atomizzato → saltato
    assert r2.chunks_done == 0 and r2.chunks_skipped == 1 and r2.nodes_created == 0
