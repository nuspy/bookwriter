"""Test della pipeline di ingestione (file/directory, dedup, ricorsione) e BookSpec."""

from __future__ import annotations

from pathlib import Path

from bookwriter.db.project_schema import get_bookspec, init_project_db, set_bookspec
from bookwriter.ingest.pipeline import ingest_paths, iter_files, memory_stats


def _make_corpus(root: Path) -> None:
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("# Titolo A\n\nParagrafo uno. Frase due.\n\nAltro paragrafo.", encoding="utf-8")
    (root / "b.md").write_text("# B\n\nContenuto di B con qualche frase.", encoding="utf-8")
    (root / "sub" / "c.txt").write_text("Testo annidato nella sottocartella.", encoding="utf-8")
    (root / "ignora.log").write_text("non supportato", encoding="utf-8")


def test_iter_files_recursive_and_filters(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    top = iter_files([corpus], recursive=False)
    assert {p.name for p in top} == {"a.txt", "b.md"}  # niente sub, niente .log
    deep = iter_files([corpus], recursive=True)
    assert {p.name for p in deep} == {"a.txt", "b.md", "c.txt"}
    only_txt = iter_files([corpus], recursive=True, include=["*.txt"])
    assert {p.name for p in only_txt} == {"a.txt", "c.txt"}
    no_b = iter_files([corpus], recursive=True, exclude=["b.*"])
    assert "b.md" not in {p.name for p in no_b}


def test_ingest_and_dedup(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    _make_corpus(corpus)
    db = tmp_path / "project.db"
    init_project_db(db)

    report = ingest_paths([corpus], db_path=db, target_tokens=40, recursive=True)
    assert len(report.imported) == 3
    assert report.chunks > 0
    stats = memory_stats(db)
    assert stats["documents"] == 3
    assert stats["chunks"] == report.chunks
    assert stats["total_tokens_est"] > 0
    assert set(stats["by_format"]) == {"txt", "md"}

    # Re-ingestione: tutto duplicato → nessun nuovo documento.
    again = ingest_paths([corpus], db_path=db, target_tokens=40, recursive=True)
    assert again.imported == []
    assert len(again.skipped) == 3
    assert memory_stats(db)["documents"] == 3


def test_bookspec_get_set(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_project_db(db)
    assert get_bookspec(db).target_words is None
    set_bookspec(db, target_words=50000, tolerance_pct=8)
    spec = get_bookspec(db)
    assert spec.target_words == 50000
    assert spec.tolerance_pct == 8


def test_running_heads_are_recognised_without_eating_the_text() -> None:
    """Il titolo corrente di un PDF va tolto; una frase che torna nel racconto no."""
    from bookwriter.layout.extract import running_heads as _running_heads

    pagine = [
        f"IL MIO LIBRO\nCapitolo {i}: qui comincia il racconto della giornata.\n{i}"
        for i in range(1, 13)
    ]
    # Un ritornello ripetuto dentro a una sola pagina non è impaginazione.
    pagine.append("IL MIO LIBRO\n«Sì.»\n«Sì.»\n«Sì.»\n«Sì.»\n«Sì.»\n13")
    # E una battuta che torna in due pagine su tredici nemmeno.
    pagine[0] += "\n«Perché?»"
    pagine[1] += "\n«Perché?»"

    teste = _running_heads(pagine)

    assert "IL MIO LIBRO" in teste
    assert "«Sì.»" not in teste, "ripetuta in una pagina sola: è testo"
    assert "«Perché?»" not in teste, "due pagine su tredici: è testo"
    assert not any(t.startswith("Capitolo") for t in teste)


def test_short_documents_keep_everything() -> None:
    """Su tre pagine non c'è cadenza da riconoscere: meglio non togliere nulla."""
    from bookwriter.layout.extract import running_heads as _running_heads

    assert _running_heads(["TITOLO\nuno", "TITOLO\ndue", "TITOLO\ntre"]) == set()


# --------------------------------------------------------------------------- #
#  documenti di lavoro: vivono quanto la sessione
# --------------------------------------------------------------------------- #
def test_a_book_opened_to_be_translated_is_not_a_source_of_the_project(tmp_path) -> None:
    """Il libro che si traduce è il pezzo su cui si lavora, non materiale del progetto."""
    from bookwriter.db.project_schema import (
        init_project_db,
        list_documents,
        purge_transient_documents,
    )
    from bookwriter.ingest import pipeline

    db = tmp_path / "p.db"
    init_project_db(db)
    fonte = tmp_path / "appunti.md"
    fonte.write_text("# Appunti\n\nUna nota vera.\n", encoding="utf-8")
    da_tradurre = tmp_path / "romanzo.md"
    da_tradurre.write_text("# Romanzo\n\nCapitolo primo.\n", encoding="utf-8")

    pipeline.ingest_paths([fonte], db_path=db)
    pipeline.ingest_paths([da_tradurre], db_path=db, transient=True)
    assert {d["title"]: d["transient"] for d in list_documents(db)} == {
        "appunti": False, "romanzo": True
    }

    # Riapertura del programma.
    tolti = purge_transient_documents(db)

    assert tolti == 1
    assert [d["title"] for d in list_documents(db)] == ["appunti"]


def test_the_working_document_takes_its_chunks_and_its_run_with_it(tmp_path) -> None:
    """Cancellare il documento e lasciarne i pezzi in giro sarebbe peggio che non cancellarlo."""
    from sqlmodel import Session, func, select

    from bookwriter.db.project_schema import (
        Chunk,
        RewriteChunk,
        RewriteRun,
        init_project_db,
        list_documents,
        make_engine,
        purge_transient_documents,
    )
    from bookwriter.ingest import pipeline
    from bookwriter.rewrite import rewrite_document

    class ClienteFinto:
        def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
            if "conforms" in schema["properties"]:
                return {"conforms": True, "issues": []}
            return {"rewritten": "tradotto", "glossary": [], "summary": ""}

    db = tmp_path / "p.db"
    init_project_db(db)
    libro = tmp_path / "libro.md"
    libro.write_text("# Libro\n\nUn capoverso lungo abbastanza da contare.\n", encoding="utf-8")
    pipeline.ingest_paths([libro], db_path=db, transient=True)
    doc_id = list_documents(db)[0]["id"]
    rewrite_document(db, doc_id, "C", ClienteFinto(), mode="translate", target_language="en")

    purge_transient_documents(db)

    with Session(make_engine(db)) as ses:
        for tabella in (Chunk, RewriteRun, RewriteChunk):
            assert ses.exec(select(func.count()).select_from(tabella)).one() == 0


def test_importing_for_real_a_file_already_open_promotes_it(tmp_path) -> None:
    """Se poi lo si importa davvero, quel libro diventa una fonte e smette di essere volatile."""
    from bookwriter.db.project_schema import (
        init_project_db,
        list_documents,
        purge_transient_documents,
    )
    from bookwriter.ingest import pipeline

    db = tmp_path / "p.db"
    init_project_db(db)
    libro = tmp_path / "libro.md"
    libro.write_text("# Libro\n\nTesto.\n", encoding="utf-8")

    pipeline.ingest_paths([libro], db_path=db, transient=True)
    pipeline.ingest_paths([libro], db_path=db)  # stesso file, importato dalla memoria

    assert purge_transient_documents(db) == 0
    assert [d["title"] for d in list_documents(db)] == ["libro"]


def test_the_structure_of_an_imported_book_is_kept_for_later(tmp_path) -> None:
    """Senza lo scheletro salvato all'importazione non si potrebbe riconsegnare il libro com'era."""
    from bookwriter.db.project_schema import (
        get_document_layout,
        init_project_db,
        list_documents,
    )
    from bookwriter.ingest import pipeline

    db = tmp_path / "p.db"
    init_project_db(db)
    libro = tmp_path / "libro.md"
    libro.write_text("# Libro\n\nPrimo.\n\n## Capitolo 1\n\nSecondo.\n", encoding="utf-8")
    pipeline.ingest_paths([libro], db_path=db)

    layout = get_document_layout(db, list_documents(db)[0]["id"])

    assert layout is not None
    assert [(s.kind, s.level) for s in layout.segments] == [
        ("heading", 1), ("paragraph", 0), ("heading", 2), ("paragraph", 0)
    ]
