"""Test dei checkpoint Job (progresso persistito)."""

from __future__ import annotations

from pathlib import Path

from bookwriter.db.project_schema import (
    finish_job,
    init_project_db,
    list_jobs,
    start_job,
    update_job,
)


def test_job_lifecycle(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_project_db(db)
    jid = start_job(db, "atomize")
    assert jid >= 1
    update_job(db, jid, 0.5)
    row = list_jobs(db)[0]
    assert row["kind"] == "atomize" and row["state"] == "running" and row["progress"] == 0.5
    finish_job(db, jid)
    row = list_jobs(db)[0]
    assert row["state"] == "done" and row["progress"] == 1.0


def test_retitle_does_not_hold_the_database_during_model_calls(tmp_path) -> None:
    """Durante le chiamate al modello il database deve restare scrivibile.

    È il difetto che faceva fallire il contatore dei token ogni 10 secondi: la titolazione
    teneva una transazione aperta per tutti i capitoli, cioè per minuti.
    """
    from sqlmodel import Session, select

    from bookwriter.db.project_schema import (
        Node,
        Outline,
        OutlineChapter,
        OutlineItem,
        init_project_db,
        make_engine,
        save_usage,
    )
    from bookwriter.organize import retitle_outline

    db = tmp_path / "p.db"
    init_project_db(db)
    engine = make_engine(db)
    with Session(engine) as ses:
        outline = Outline(name="o", ordering="thematic"); ses.add(outline); ses.commit(); ses.refresh(outline)
        for pos in range(3):
            ch = OutlineChapter(outline_id=outline.id, position=pos, title=f"vecchio {pos}")
            ses.add(ch); ses.commit(); ses.refresh(ch)
            nodo = Node(raw_text=f"testo {pos}", summary=f"sintesi {pos}", themes=["t"])
            ses.add(nodo); ses.commit(); ses.refresh(nodo)
            ses.add(OutlineItem(chapter_id=ch.id, position=0, node_id=nodo.id)); ses.commit()
        outline_id = outline.id

    scritture = []

    class ClientCheScrive:
        """Mentre 'genera' il titolo, registra i consumi: come fa il timer della GUI."""

        def chat(self, messages, *, model=None, max_tokens=None, extra=None):
            scritture.append(
                save_usage(db, row_id=None, activity="Rigenero i titoli", model="m",
                           prompt_tokens=10, completion_tokens=20, calls=1)
            )
            return "Titolo nuovo"

    assert retitle_outline(db, outline_id, ClientCheScrive(), model="m") == 3
    assert len(scritture) == 3 and all(r is not None for r in scritture)

    with Session(engine) as ses:
        titoli = [c.title for c in ses.exec(select(OutlineChapter).order_by(OutlineChapter.position))]
    assert titoli == ["Titolo nuovo"] * 3


def test_wal_mode_is_active(tmp_path) -> None:
    """La modalità WAL è ciò che permette a un lettore di non bloccare uno scrittore."""
    from bookwriter.db.project_schema import init_project_db, make_engine

    db = tmp_path / "p.db"
    init_project_db(db)
    with make_engine(db).connect() as conn:
        modo = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
    assert str(modo).lower() == "wal"


def test_connection_survives_a_database_held_by_another_process(tmp_path) -> None:
    """Se un altro processo tiene il file, il passaggio a WAL non deve impedire la connessione.

    Si riproduce con un secondo collegamento che apre una transazione esclusiva: il PRAGMA
    journal_mode non può riuscire, ma la connessione deve comunque funzionare.
    """
    import sqlite3

    from bookwriter.db.project_schema import init_project_db, make_engine

    db = tmp_path / "p.db"
    init_project_db(db)
    with make_engine(db).connect() as primo:
        primo.exec_driver_sql("PRAGMA journal_mode=DELETE")

    occupante = sqlite3.connect(str(db), timeout=0.1)
    occupante.execute("BEGIN EXCLUSIVE")
    try:
        with make_engine(db).connect() as conn:
            # Il passaggio a WAL fallisce, ma leggere resta possibile.
            assert conn.exec_driver_sql("SELECT 1").scalar() == 1
    finally:
        occupante.rollback()
        occupante.close()
