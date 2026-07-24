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
