"""Test del motore di riscrittura deterministico (client fittizio)."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Chunk,
    Document,
    RewriteChunk,
    RewriteRun,
    init_project_db,
    make_engine,
)
from bookwriter.rewrite.engine import assemble_markdown, preview_pairs, rewrite_document


class FakeRewriteClient:
    def chat_json(self, messages, *, schema, model=None):
        user = messages[-1]["content"]
        return {
            "rewritten": f"RW[{user[:15]}]",
            "glossary": [{"term": "Luca", "rendering": "Luke"}],
            "summary": "riassunto progressivo",
        }


def _seed_book(db_path: Path) -> int:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="b.txt", format="txt", sha256="d", title="Libro")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        specs = [(0, "Cap 1", "Primo brano."), (1, "Cap 1", "Secondo brano."), (2, "Cap 2", "Terzo brano.")]
        for pos, ch, text in specs:
            ses.add(Chunk(document_id=doc.id, book_pos=pos, chapter_ref=ch, text=text, sha256=f"c{pos}"))
        ses.commit()
        return doc.id


def test_full_rewrite(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(db, doc_id, "CONTRATTO", FakeRewriteClient())
    assert report.total == 3 and report.processed == 3 and report.status == "done"

    md = assemble_markdown(db, report.run_id)
    assert "# Libro" in md  # titolo del documento
    assert md.count("RW[") == 3  # tutti i chunk riscritti presenti in ordine
    # Continuità persistita (glossario).
    engine = make_engine(db)
    with Session(engine) as ses:
        run = ses.get(RewriteRun, report.run_id)
    assert run.continuity["glossary"]["Luca"] == "Luke"


def test_translate_mode(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(
        db, doc_id, "C", FakeRewriteClient(), mode="translate", target_language="inglese"
    )
    assert report.status == "done"
    engine = make_engine(db)
    with Session(engine) as ses:
        run = ses.get(RewriteRun, report.run_id)
    assert run.mode == "translate" and run.target_language == "inglese"


def test_preview_only_first_chapter(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    report = rewrite_document(db, doc_id, "C", FakeRewriteClient(), kind="preview", resume=False)
    assert report.total == 2  # solo i due brani del Cap 1
    pairs = preview_pairs(db, report.run_id)
    assert len(pairs) == 2
    assert pairs[0]["original"] == "Primo brano." and pairs[0]["rewritten"].startswith("RW[")


def test_resume_skips_done_chunks(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _seed_book(db)
    engine = make_engine(db)
    # Simula una run interrotta: run 'running' con il primo chunk già fatto.
    with Session(engine) as ses:
        chunks = list(ses.exec(select(Chunk).order_by(Chunk.book_pos)))
        run = RewriteRun(document_id=doc_id, kind="full", mode="rewrite", status="running", continuity={})
        ses.add(run)
        ses.commit()
        ses.refresh(run)
        ses.add(RewriteChunk(run_id=run.id, chunk_id=chunks[0].id, book_pos=0, chapter_ref="Cap 1", text="GIÀ"))
        ses.commit()
        run_id = run.id

    report = rewrite_document(db, doc_id, "C", FakeRewriteClient(), resume=True)
    assert report.run_id == run_id  # ha ripreso la stessa run
    assert report.processed == 3 and report.status == "done"
    with Session(engine) as ses:
        rows = list(ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id)))
    assert len(rows) == 3  # nessun duplicato del primo chunk
    assert any(r.text == "GIÀ" for r in rows)  # quello già fatto è rimasto
