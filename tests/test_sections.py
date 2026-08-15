"""Test della segmentazione in sezioni (taglio deterministico + etichettatura senza rete)."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.atomize.sections import (
    build_label_prompt,
    build_sections,
    plan_sections,
    split_into_sections,
)
from bookwriter.db.project_schema import Chunk, Document, Node, init_project_db, make_engine

PAR = ["Primo paragrafo con alcune parole dentro."] * 4


def _seed(db_path: Path, testi: list[str]) -> None:
    init_project_db(db_path)
    with Session(make_engine(db_path)) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc); ses.commit(); ses.refresh(doc)
        for i, testo in enumerate(testi):
            ses.add(Chunk(document_id=doc.id, book_pos=i, text=testo, sha256=f"c{i}"))
        ses.commit()


def test_split_never_breaks_a_paragraph_and_keeps_all_text() -> None:
    testo = "\n\n".join(PAR)
    sezioni = split_into_sections(testo, target_words=12)
    assert len(sezioni) >= 2
    # Nessun paragrafo spezzato e nessuna parola persa o duplicata.
    assert "\n\n".join(sezioni).split() == testo.split()
    for s in sezioni:
        for blocco in s.split("\n\n"):
            assert blocco in PAR


def test_split_attaches_a_short_tail_instead_of_leaving_it_alone() -> None:
    testo = "\n\n".join(PAR[:3] + ["Coda."])
    sezioni = split_into_sections(testo, target_words=12)
    assert not any(s.strip() == "Coda." for s in sezioni)
    assert sezioni[-1].endswith("Coda.")


def test_plan_removes_the_sentence_shared_between_two_blocks(tmp_path: Path) -> None:
    """Il chunking sovrappone una frase: non deve comparire due volte nelle sezioni."""
    primo = "Alfa uno. Beta due. Gamma tre."
    secondo = "Gamma tre. Delta quattro. Epsilon cinque."
    db = tmp_path / "p.db"
    _seed(db, [primo, secondo])

    planned = plan_sections(db, target_words=3)
    testo = " ".join(t for _, t in planned)
    assert testo.count("Gamma tre.") == 1
    assert "Delta quattro." in testo and "Alfa uno." in testo


class LabelClient:
    """Descrive la sezione senza mai restituirne il testo."""

    def __init__(self) -> None:
        self.visti: list[str] = []

    def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
        self.visti.append(messages[1]["content"])
        return {"title": "Titolo", "summary": "Di cosa parla.", "themes": ["  Tema ", "tema", "Altro"],
                "entities": ["Anna"], "event_date": None, "salience": 0.8}


def test_build_sections_stores_original_text_and_labels(tmp_path: Path) -> None:
    testo = "\n\n".join(PAR)
    db = tmp_path / "p.db"
    _seed(db, [testo])
    client = LabelClient()

    rep = build_sections(db, client, target_words=12)
    assert rep.sections == rep.labelled >= 2
    assert rep.coverage_pct == 100
    assert "copertura=100%" in rep.summary()

    with Session(make_engine(db)) as ses:
        nodi = ses.exec(select(Node)).all()
    assert len(nodi) == rep.labelled
    # Il testo salvato è quello originale, non quello del modello.
    assert " ".join(n.raw_text for n in nodi).split() == testo.split()
    assert nodi[0].summary.startswith("Titolo — ")
    assert nodi[0].themes == ["tema", "altro"]  # minuscolo, senza doppioni
    assert nodi[0].entities == ["Anna"]

    # Seconda esecuzione: i blocchi già fatti vengono saltati.
    assert build_sections(db, client, target_words=12).sections == 0


def test_label_prompt_forbids_rewriting_and_fixes_the_language() -> None:
    prompt = build_label_prompt("hu")
    assert "NON riscriverla" in prompt
    assert "ungherese (magyar)" in prompt
    assert "nella stessa lingua del testo" in build_label_prompt("")
