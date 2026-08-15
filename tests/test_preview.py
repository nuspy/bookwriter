"""Test della lettura del libro mentre nasce."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session

from bookwriter import preview
from bookwriter.db.project_schema import (
    Chunk,
    ComposedChapter,
    Document,
    Outline,
    OutlineChapter,
    RewriteChunk,
    RewriteRun,
    init_project_db,
    make_engine,
)


def _progetto(tmp_path: Path) -> Path:
    db = tmp_path / "p.db"
    init_project_db(db)
    with Session(make_engine(db)) as ses:
        doc = Document(path="b.md", format="md", sha256="d", title="Il diario")
        ses.add(doc); ses.commit(); ses.refresh(doc)
        for pos in range(5):
            ses.add(Chunk(document_id=doc.id, book_pos=pos, text=f"Brano {pos}.", sha256=f"c{pos}"))
        run = RewriteRun(document_id=doc.id, kind="full", mode="translate",
                         target_language="Inglese", status="running", continuity={})
        ses.add(run); ses.commit(); ses.refresh(run)
        for pos in range(2):  # solo due brani su cinque: la lavorazione è a metà
            ses.add(RewriteChunk(run_id=run.id, chunk_id=pos + 1, book_pos=pos,
                                 text=f"## Chapter {pos}\n\nPassage {pos} in **English**."))
        ses.commit()
    return db


def test_a_book_can_be_read_while_it_is_being_written(tmp_path: Path) -> None:
    """Il file arriva alla fine, ma il testo esiste già: dev'essere leggibile prima."""
    db = _progetto(tmp_path)
    sorgenti = preview.sources(db)

    assert len(sorgenti) == 1
    s = sorgenti[0]
    assert s.running and s.done == 2 and s.total == 5
    assert s.progress == "2/5 (40%)"
    assert "traduzione" in s.label and "Il diario" in s.label and "Inglese" in s.label

    testo = preview.text_of(db, s)
    assert "Passage 0 in **English**" in testo and "Passage 1" in testo
    assert "Brano 2" not in testo, "quello che non è stato tradotto non compare"


def test_the_running_work_comes_first(tmp_path: Path) -> None:
    """Chi apre la scheda vuole vedere quello che sta succedendo adesso, non l'archivio."""
    db = _progetto(tmp_path)
    with Session(make_engine(db)) as ses:
        vecchia = RewriteRun(document_id=1, kind="full", mode="rewrite", status="done", continuity={})
        ses.add(vecchia); ses.commit(); ses.refresh(vecchia)
        ses.add(RewriteChunk(run_id=vecchia.id, chunk_id=1, book_pos=0, text="roba vecchia"))
        ses.commit()

    sorgenti = preview.sources(db)
    assert len(sorgenti) == 2
    assert sorgenti[0].running, "in cima quella in corso, anche se non è la più recente"


def test_prose_being_written_is_readable_too(tmp_path: Path) -> None:
    """Vale per la stesura in prosa come per la traduzione: si guarda mentre si scrive."""
    db = tmp_path / "q.db"
    init_project_db(db)
    with Session(make_engine(db)) as ses:
        outline = Outline(name="Bozza")
        ses.add(outline); ses.commit(); ses.refresh(outline)
        capitolo = OutlineChapter(outline_id=outline.id, position=0, title="Uno")
        ses.add(capitolo); ses.commit(); ses.refresh(capitolo)
        ses.add(ComposedChapter(outline_id=outline.id, chapter_id=capitolo.id, position=0,
                                title="Uno", text="Il primo capitolo, già scritto.", words=5))
        ses.commit()

    sorgenti = preview.sources(db)
    assert [s.kind for s in sorgenti] == ["compose"]
    assert "già scritto" in preview.text_of(db, sorgenti[0])


def test_nothing_written_yet_is_not_an_error(tmp_path: Path) -> None:
    db = tmp_path / "vuoto.db"
    init_project_db(db)
    assert preview.sources(db) == []
    assert preview.sources(tmp_path / "inesistente.db") == []


def test_the_latest_arrival_is_marked_so_it_can_be_found(tmp_path: Path) -> None:
    """Guardare un libro che si scrive ha senso solo se si vede dov'è arrivato."""
    html = preview.to_html("# Titolo\n\nVecchio.\n\nNuovissimo.", evidenzia_coda=len("Nuovissimo."))
    assert "<h1>Titolo</h1>" in html
    assert "background:#fff8e1" in html
    prima, dopo = html.split("background:#fff8e1", 1)
    assert "Vecchio" in prima and "Nuovissimo" in dopo


def test_the_markup_becomes_readable_text(tmp_path: Path) -> None:
    """Si legge come un libro, non come un file: niente asterischi e cancelletti in pagina."""
    html = preview.to_html("## Capitolo\n\nUn **grassetto** e un *corsivo*.\n\n- una voce\n\n---")
    assert "<h2>Capitolo</h2>" in html
    assert "<b>grassetto</b>" in html and "<i>corsivo</i>" in html
    assert "•" in html and "<hr>" in html
    assert "**" not in html and "## " not in html


def test_html_does_not_let_the_text_become_markup(tmp_path: Path) -> None:
    """Un libro può contenere < e >: devono restare testo, non diventare tag."""
    html = preview.to_html("Se x < y allora <b>attenzione</b> a <script>.")
    assert "&lt;script&gt;" in html and "<script>" not in html
