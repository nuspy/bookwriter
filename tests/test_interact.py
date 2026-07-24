"""Test di persistenza intenti/sessioni e motore di interazione (senza rete)."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import Node, init_project_db, make_engine, set_bookspec
from bookwriter.interact.engine import analyze_coverage, integrate_answer, propose_questions
from bookwriter.interact.store import (
    add_question,
    build_brief,
    format_brief,
    list_intents,
    list_open_questions,
    record_intent,
    start_session,
)
from bookwriter.memory.vectors import LanceStore


class FakeClient:
    def chat_json(self, messages, *, schema, model=None):
        return {"questions": ["Qual è un esempio concreto?", "Che periodo storico?"]}

    def embed(self, texts, *, kind="document", model=None):
        return [[float(len(t)), 1.0, 0.0] for t in texts]


def _seed_nodes(db_path: Path) -> None:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        for t, th in [("Roma antica", ["storia"]), ("La cucina romana", ["cucina"])]:
            ses.add(Node(raw_text=t, summary=t, themes=th, source="ingest"))
        ses.commit()


def test_intents_and_questions_persist(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_project_db(db)
    sid = start_session(db, mode="compose")
    assert sid >= 1
    iid = record_intent(db, "style", {"tone": "ironico"}, rationale="scelta utente")
    assert iid >= 1
    qid = add_question(db, "Che pubblico?", session_id=sid)
    assert list_open_questions(db)[0]["id"] == qid
    assert list_intents(db, status="open")[0]["kind"] == "style"


def test_build_and_format_brief(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed_nodes(db)
    set_bookspec(db, target_words=30000)
    record_intent(db, "audience", {"who": "studenti"})
    add_question(db, "Serve un glossario?")
    brief = build_brief(db)
    assert brief["target_words"] == 30000
    assert brief["node_count"] == 2
    assert brief["open_intents"] and brief["open_questions"]
    text = format_brief(brief)
    assert "Brief di ripresa" in text and "studenti" in text


def test_analyze_and_propose_and_integrate(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    vectors = tmp_path / "v.lance"
    _seed_nodes(db)
    client = FakeClient()

    cov = analyze_coverage(db)
    assert cov["node_count"] == 2
    assert "storia" in cov["theme_counts"]

    questions = propose_questions(db, client, n=2)
    assert len(questions) == 2
    assert len(list_open_questions(db)) == 2
    assert any(i["kind"] == "content-gap" for i in list_intents(db))

    qid = questions[0]["id"]
    node_id = integrate_answer(db, vectors, client, qid, "Nel Medioevo, con esempi concreti.")
    engine = make_engine(db)
    with Session(engine) as ses:
        node = ses.get(Node, node_id)
    assert node.source == "dialogue"
    assert LanceStore(vectors).existing_ids("node") == {node_id}
    # La domanda risolta non è più aperta.
    assert all(q["id"] != qid for q in list_open_questions(db))
