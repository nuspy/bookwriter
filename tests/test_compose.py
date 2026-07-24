"""Test della stesura narrativa (Compose → prosa), con client fittizio."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, func, select

from bookwriter.compose.narrative import compose_outline, compose_to_markdown
from bookwriter.db.project_schema import (
    ComposedChapter,
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    init_project_db,
    make_engine,
)


class FakeComposeClient:
    def __init__(self, words: int = 30) -> None:
        self.words = words

    def chat_json(self, messages, *, schema, model=None):
        return {
            "text": " ".join(["parola"] * self.words),
            "glossary": [{"term": "Faro", "rendering": "Lighthouse"}],
            "summary": "riassunto",
        }


def _seed_outline(db_path: Path) -> int:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        nodes = [Node(raw_text=t, summary=t) for t in ["Idea A1", "Idea A2", "Idea B1", "Idea B2"]]
        ses.add_all(nodes)
        ses.commit()
        for n in nodes:
            ses.refresh(n)
        outline = Outline(name="Bozza")
        ses.add(outline)
        ses.commit()
        ses.refresh(outline)
        chapters = [
            OutlineChapter(outline_id=outline.id, position=0, title="Cap A"),
            OutlineChapter(outline_id=outline.id, position=1, title="Cap B"),
        ]
        ses.add_all(chapters)
        ses.commit()
        for c in chapters:
            ses.refresh(c)
        ses.add_all([
            OutlineItem(chapter_id=chapters[0].id, position=0, node_id=nodes[0].id),
            OutlineItem(chapter_id=chapters[0].id, position=1, node_id=nodes[1].id),
            OutlineItem(chapter_id=chapters[1].id, position=0, node_id=nodes[2].id),
            OutlineItem(chapter_id=chapters[1].id, position=1, node_id=nodes[3].id),
        ])
        ses.commit()
        return outline.id


def test_compose_and_markdown(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    oid = _seed_outline(db)
    report = compose_outline(db, oid, "CONTRATTO", FakeComposeClient(words=30))
    assert report.chapters == 2
    assert report.words == 60  # 2 capitoli × 30 parole
    md = compose_to_markdown(db, oid)
    assert "# Bozza" in md and "## Cap A" in md and "## Cap B" in md and "parola" in md


def test_compose_resume_no_duplicates(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    oid = _seed_outline(db)
    compose_outline(db, oid, "C", FakeComposeClient())
    compose_outline(db, oid, "C", FakeComposeClient())  # resume: non rigenera
    engine = make_engine(db)
    with Session(engine) as ses:
        count = ses.exec(select(func.count()).select_from(ComposedChapter)).one()
    assert count == 2


def test_compose_with_target(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    oid = _seed_outline(db)
    report = compose_outline(db, oid, "C", FakeComposeClient(words=20), target_words=100)
    assert report.target_words == 100
    assert report.chapters == 2
