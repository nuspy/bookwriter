"""Test dell'export: la catena di composizione unica, e il wiki/archivio (HTML + JSON).

Ogni schermata che produce un libro nuovo passa dallo stesso compositore delle sette fasi. Qui
si verifica che il ponte fra il Markdown di bookwriter e il modello di libro non perda niente
per strada: capitoli, sezioni, note bibliografiche, apertura.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session

from bookwriter.db.project_schema import Document, Edge, Node, init_project_db, make_engine
from bookwriter.exporter import compose_output, export_wiki, markdown_to_pdf, write_output

MD = "# Titolo\n\n## Capitolo 1\n\nUn paragrafo di prova.\n\n## Capitolo 2\n\nAltro paragrafo.\n"


def test_markdown_to_pdf(tmp_path: Path) -> None:
    out = markdown_to_pdf(MD, tmp_path / "libro.pdf")
    assert out.exists()
    data = out.read_bytes()
    assert data[:4] == b"%PDF" and len(data) > 500


def test_write_output_formats(tmp_path: Path) -> None:
    md = write_output(MD, tmp_path / "a.md", "md")
    assert md.read_text(encoding="utf-8").startswith("# Titolo")
    pdf = write_output(MD, tmp_path / "a.pdf", "pdf")
    assert pdf.read_bytes()[:4] == b"%PDF"


def test_every_format_comes_out_of_one_pipeline(tmp_path: Path) -> None:
    """DOCX, PDF ed EPUB dallo stesso modello di libro: una catena sola, non tre."""
    for fmt, firma in (("docx", b"PK"), ("pdf", b"%PDF"), ("epub", b"PK")):
        composto = compose_output(MD, tmp_path / f"libro.{fmt}", fmt,
                                  author="A. Taini", language="it")
        assert composto.path.read_bytes()[:4].startswith(firma)
        assert composto.chapters == 2
    with pytest.raises(ValueError, match="Formato non supportato"):
        write_output(MD, tmp_path / "libro.txt", "txt")


def test_the_epub_declares_a_cover_slot_and_a_navigation(tmp_path: Path) -> None:
    """L'EPUB è nuovo: prima questa schermata non lo produceva affatto."""
    path = write_output(MD, tmp_path / "libro.epub", "epub")
    with zipfile.ZipFile(path) as z:
        nomi = z.namelist()
        assert nomi[0] == "mimetype"  # non compresso e per primo, o Kindle lo rifiuta
        assert z.read("mimetype") == b"application/epub+zip"
    assert any(n.endswith("nav.xhtml") for n in nomi)
    assert any(n.endswith("toc.ncx") for n in nomi)


def test_the_composed_pdf_is_a_book_and_not_a_printout(tmp_path: Path) -> None:
    """Misura reale al posto dell'A4, indice, capitoli in recto: è la differenza col vecchio."""
    fitz = pytest.importorskip("fitz")
    from bookwriter.config import ProductionSettings

    composto = compose_output(MD, tmp_path / "libro.pdf", "pdf", language="it",
                              production=ProductionSettings(trim="a5"))
    with fitz.open(str(composto.path)) as doc:
        larghezza, altezza = doc[0].rect.width, doc[0].rect.height
        testo = "\n".join(p.get_text() for p in doc)
        pagine = doc.page_count
    assert abs(larghezza - 148 * 2.834) < 3 and abs(altezza - 210 * 2.834) < 3
    assert "Indice" in testo
    assert pagine % 2 == 0  # la brossura non sa fare un numero dispari di facciate


def test_the_chapter_sources_become_one_bibliography(tmp_path: Path) -> None:
    """Le note di ogni capitolo si raccolgono in fondo, deduplicate: le scrive il compositore."""
    from bookwriter.forge.compose import book_config, book_from_markdown

    testo = (
        "# Il libro\n\n"
        "## Capitolo 1\n\nTesto.\n\n### Note bibliografiche\n\n"
        "Rossi, M. (2024). *Un'opera lunga abbastanza da contare*. Editore.\n\n"
        "## Capitolo 2\n\nAltro testo.\n\n### Note bibliografiche\n\n"
        "Rossi, M. (2024). *Un'opera lunga abbastanza da contare*. Editore.\n"
        "Bianchi, L. (2023). *Un secondo titolo altrettanto lungo*. Altro editore.\n"
    )
    cfg = book_config(tmp_path, language="it")
    libro = book_from_markdown(testo, cfg)
    assert [c.title for c in libro.chapters] == ["Capitolo 1", "Capitolo 2"]
    assert len(libro.references) == 2  # il doppione è sparito
    assert [s.ident for s in libro.back] == ["bibliography"]


def test_what_precedes_the_first_chapter_is_an_opening_not_a_chapter(tmp_path: Path) -> None:
    from bookwriter.forge.compose import book_config, book_from_markdown

    cfg = book_config(tmp_path, language="it")
    libro = book_from_markdown(
        "# Il libro\n\nUn'apertura che sta prima di tutto.\n\n## Capitolo 1\n\nTesto.\n", cfg)
    assert [s.ident for s in libro.front] == ["introduction"]
    assert len(libro.chapters) == 1
    assert cfg.title == "Il libro"


def test_a_text_without_headings_is_still_a_book(tmp_path: Path) -> None:
    """Una traduzione di testo semplice non ha titoli: esce comunque un volume, con un capitolo."""
    composto = compose_output("Solo prosa, nessun titolo.\n\nUn secondo capoverso.",
                              tmp_path / "libro.docx", "docx", title="Senza titoli")
    assert composto.path.exists() and composto.chapters == 1


def _seed_graph(db_path: Path) -> None:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        nodes = [
            Node(document_id=doc.id, raw_text="Il gatto dorme", summary="Gatto", themes=["animali"]),
            Node(document_id=doc.id, raw_text="Marte è rosso", summary="Marte", themes=["spazio"]),
        ]
        ses.add_all(nodes)
        ses.commit()
        for n in nodes:
            ses.refresh(n)
        ses.add(Edge(src_node=nodes[0].id, dst_node=nodes[1].id, type="thematic", weight=0.5, source="vector"))
        ses.commit()


def test_export_wiki(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed_graph(db)
    result = export_wiki(db, tmp_path / "wiki")
    html = result["html"].read_text(encoding="utf-8")
    assert "Il gatto dorme" in html and "Marte è rosso" in html
    assert "animali" in html and 'id="q"' in html  # ricerca presente
    assert "<svg" in html and 'href="#n' in html  # grafo SVG con link ai nodi
    data = json.loads(result["json"].read_text(encoding="utf-8"))
    assert len(data["nodes"]) == 2 and len(data["edges"]) == 1


def test_export_filename_describes_the_variant() -> None:
    """Ogni variante prodotta ha un nome diverso: non si sovrascrivono a vicenda."""
    from bookwriter.exporter import export_filename

    assert export_filename("quaderno", style="Hemingway Doyle", language="hu", words=80000) == (
        "quaderno_Hemingway-Doyle_HU_80k.docx"
    )
    assert export_filename("quaderno", qualifier="antologia", language="hu", ext="md") == (
        "quaderno_antologia_HU.md"
    )
    # Tradurre sostituisce la sigla di lingua invece di accodarne una seconda.
    assert export_filename("appunti_prosa_IT", language="en") == "appunti_prosa_EN.docx"
    # Caratteri vietati dal filesystem e spazi non passano.
    assert export_filename('Il filo: del/racconto', style="Hemingway Doyle", language="it") == (
        "Il-filo-delracconto_Hemingway-Doyle_IT.docx"
    )
    # Senza dettagli resta il nome nudo.
    assert export_filename("quaderno") == "quaderno.docx"


def test_language_tag_from_free_text() -> None:
    from bookwriter.config import language_tag

    assert language_tag("inglese") == "EN"
    assert language_tag("en") == "EN"
    assert language_tag("ungherese (magyar)") == "HU"
    assert language_tag("magyar") == "HU"
    assert language_tag("") == ""
