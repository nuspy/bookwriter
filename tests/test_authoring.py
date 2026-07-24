"""Test del motore di authoring (start brief + generazione idee), client fittizio."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.authoring.engine import expand_topic, start_authoring
from bookwriter.db.project_schema import Node, get_bookspec, init_project_db, make_engine
from bookwriter.interact.store import list_intents
from bookwriter.memory.vectors import LanceStore


class FakeAuthorClient:
    def chat_json(self, messages, *, schema, model=None):
        return {"ideas": [{"text": f"Idea {i}", "summary": f"s{i}", "themes": ["mare"]} for i in range(3)]}

    def embed(self, texts, *, kind="document", model=None):
        return [[float(len(t)), 1.0, 0.0] for t in texts]


def test_start_authoring_records_brief(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_project_db(db)
    info = start_authoring(db, theme="Il mare", audience="ragazzi", goal="ispirare", target_words=5000)
    assert info.session_id >= 1
    assert get_bookspec(db).target_words == 5000
    kinds = {i["kind"] for i in list_intents(db)}
    assert {"scope", "audience", "goal"} <= kinds


def test_expand_topic_creates_nodes(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    vectors = tmp_path / "v.lance"
    init_project_db(db)
    count = expand_topic(db, vectors, FakeAuthorClient(), topic="le onde", n=3)
    assert count == 3
    engine = make_engine(db)
    with Session(engine) as ses:
        nodes = ses.exec(select(Node)).all()
    assert len(nodes) == 3
    assert all(n.source == "dialogue" for n in nodes)
    assert LanceStore(vectors).existing_ids("node") == {n.id for n in nodes}
