"""Test dell'export: PDF (reportlab) e wiki/archivio (HTML + JSON)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlmodel import Session

from bookwriter.db.project_schema import Document, Edge, Node, init_project_db, make_engine
from bookwriter.exporter import export_wiki, markdown_to_pdf, write_output

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
