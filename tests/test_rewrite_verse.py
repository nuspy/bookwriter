"""Dai versi alla prosa e modernizzazione della lingua (es. la Commedia in prosa moderna)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlmodel import Session

from bookwriter.db.project_schema import Chunk, Document, init_project_db, make_engine
from bookwriter.rewrite.engine import _instruction, assemble_layout, rewrite_document, run_record
from bookwriter.rewrite.literary import (
    BUDGET_USCITA_TOKEN,
    FORM_PROSE_FROM_VERSE,
    espansione,
    unita_di_lavoro,
)
from bookwriter.text_utils import count_tokens

CANTO_I = [
    "Nel mezzo del cammin di nostra vita",
    "mi ritrovai per una selva oscura,",
    "ché la diritta via era smarrita.",
    "Ahi quanto a dir qual era è cosa dura",
    "esta selva selvaggia e aspra e forte",
    "che nel pensier rinova la paura!",
]
CANTO_II = [
    "Lo giorno se n'andava, e l'aere bruno",
    "toglieva li animai che sono in terra",
    "da le fatiche loro; e io sol uno",
]


class Prosatore:
    """Volge in prosa: i titoli restano, i versi di ogni sezione diventano un solo capoverso."""

    def __init__(self) -> None:
        self.sistemi: list[str] = []
        self.utenti: list[str] = []
        self.giudici: list[str] = []

    def chat_json(self, messages, *, schema, model=None, reasoning_effort=""):
        if "conforms" in schema["properties"]:
            self.giudici.append(messages[-1]["content"])
            return {"conforms": True, "issues": []}
        if "rifinito" in schema["properties"]:
            return {"rifinito": messages[-1]["content"].split("TRADUZIONE:\n", 1)[1]}
        if "rewritten" not in schema["properties"]:
            return {}
        self.sistemi.append(messages[0]["content"])
        testo = messages[-1]["content"]
        self.utenti.append(testo)
        fuori, corrente = [], []
        for p in re.split(r"\n\s*\n", testo):
            p = p.strip()
            if not p:
                continue
            if p.startswith("#"):
                if corrente:
                    fuori.append("PROSA(" + " ".join(corrente) + ")")
                    corrente = []
                fuori.append(p)
            else:
                corrente.append(" ".join(r.strip() for r in p.split("\n")))
        if corrente:
            fuori.append("PROSA(" + " ".join(corrente) + ")")
        return {"rewritten": "\n\n".join(fuori), "glossary": [], "summary": "s"}


def _commedia(db_path: Path, target_tokens: int = 1500) -> int:
    """Due canti impaginati un verso per paragrafo, come in molti DOCX."""
    from bookwriter.ingest.chunker import chunk_document
    from bookwriter.ingest.structure import parse_structure
    from bookwriter.layout.model import HEADING, PARAGRAPH, DocumentLayout, Segment

    segmenti = [Segment(kind=HEADING, text="Canto I", level=2)]
    segmenti += [Segment(kind=PARAGRAPH, text=v) for v in CANTO_I]
    segmenti += [Segment(kind=HEADING, text="Canto II", level=2)]
    segmenti += [Segment(kind=PARAGRAPH, text=v) for v in CANTO_II]
    layout = DocumentLayout(segments=segmenti, source_format="txt", title="Commedia")
    testo = layout.to_text()
    init_project_db(db_path)
    with Session(make_engine(db_path)) as ses:
        doc = Document(path="c.txt", format="txt", sha256="d", title="Commedia",
                       char_count=len(testo), meta={"layout": layout.to_dict()})
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        for c in chunk_document(testo, parse_structure(testo), target_tokens=target_tokens):
            ses.add(Chunk(document_id=doc.id, book_pos=c.book_pos, chapter_ref=c.chapter_ref,
                          span_start=c.span_start, span_end=c.span_end, text=c.text,
                          token_count=c.token_count, sha256=f"c{c.book_pos}"))
        ses.commit()
        return doc.id


def test_verse_to_prose_drops_the_paragraph_count_but_keeps_the_cantos(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _commedia(db)
    client = Prosatore()
    report = rewrite_document(db, doc_id, "Stile di Dante.", client, critic=False,
                              form=FORM_PROSE_FROM_VERSE)

    assert all("CONSERVA L'IMPAGINAZIONE" not in s and "DA VERSI A PROSA" in s
               for s in client.sistemi)
    # I versi isolati arrivano al modello raccolti in terzine.
    assert any("Nel mezzo del cammin di nostra vita\nmi ritrovai" in u for u in client.utenti)

    montato = assemble_layout(db, report.run_id)
    assert montato is not None and montato.misaligned == 0
    segmenti = montato.layout.segments
    assert [s.kind for s in segmenti] == ["heading", "paragraph", "heading", "paragraph"]
    assert [s.text for s in segmenti if s.kind == "heading"] == ["Canto I", "Canto II"]
    prosa = [s.text for s in segmenti if s.kind == "paragraph"]
    assert all(v in prosa[0] for v in CANTO_I), "nessun verso perso nel capoverso del canto I"
    assert all(v in prosa[1] for v in CANTO_II)
    assert report.form == FORM_PROSE_FROM_VERSE and "dai versi alla prosa" in report.summary()
    assert run_record(db, report.run_id)["opzioni"]["form"] == FORM_PROSE_FROM_VERSE


def test_cantos_split_across_small_chunks_still_keep_their_titles(tmp_path: Path) -> None:
    """Con brani piccoli un canto attraversa più blocchi: l'allineamento è per sezione."""
    db = tmp_path / "p.db"
    doc_id = _commedia(db, target_tokens=20)
    report = rewrite_document(db, doc_id, "C", Prosatore(), critic=False,
                              form=FORM_PROSE_FROM_VERSE)
    montato = assemble_layout(db, report.run_id)
    titoli = [s.text for s in montato.layout.segments if s.kind == "heading"]
    assert titoli == ["Canto I", "Canto II"]
    intero = " ".join(s.text for s in montato.layout.segments)
    assert all(v in intero for v in CANTO_I + CANTO_II)


def test_modernizing_keeps_the_author_voice_and_combines_with_prose(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _commedia(db)
    client = Prosatore()
    report = rewrite_document(db, doc_id, "Stile di Dante.", client, critic=True,
                              form=FORM_PROSE_FROM_VERSE, modernize=True)

    sistema = client.sistemi[0]
    assert "MODERNIZZA LA LINGUA" in sistema and "VOCE dell'autore" in sistema
    assert "DA VERSI A PROSA" in sistema
    # Il giudice di stile sa che prosa e lingua moderna sono richieste, non difetti.
    assert client.giudici and all("NON sono violazioni" in g for g in client.giudici)
    assert report.modernized and "lingua modernizzata" in report.summary()


def test_modernizing_a_translation_speaks_of_the_target_language() -> None:
    testo = _instruction("translate", "inglese", None, modernize=True)
    assert "della lingua di arrivo (inglese) di oggi" in testo
    assert "CONSERVA L'IMPAGINAZIONE" in testo, "senza cambio di forma i paragrafi restano"


def test_prose_and_verse_of_the_same_book_are_two_separate_works(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _commedia(db)
    versi = rewrite_document(db, doc_id, "C", Prosatore(), critic=False,
                             should_stop=lambda: False)
    prosa = rewrite_document(db, doc_id, "C", Prosatore(), critic=False,
                             form=FORM_PROSE_FROM_VERSE)
    moderna = rewrite_document(db, doc_id, "C", Prosatore(), critic=False,
                               form=FORM_PROSE_FROM_VERSE, modernize=True)
    assert len({versi.run_id, prosa.run_id, moderna.run_id}) == 3


def test_an_unknown_form_is_refused(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    doc_id = _commedia(db)
    with pytest.raises(ValueError):
        rewrite_document(db, doc_id, "C", Prosatore(), form="sonetto")


# --------------------------------------------------------------------------- #
#  ogni risposta sta nel limite di uscita del modello
# --------------------------------------------------------------------------- #
def _poema(versi: int) -> str:
    righe = [f"verso numero {i} che canta la selva e la paura del viandante" for i in range(versi)]
    return "\n\n".join(righe)


def test_a_long_verse_passage_is_cut_into_units_that_fit_one_answer() -> None:
    testo = _poema(600)
    fattore = espansione("translate", FORM_PROSE_FROM_VERSE, True)
    unita = unita_di_lavoro(testo, fattore=fattore, form=FORM_PROSE_FROM_VERSE)

    assert len(unita) > 1
    assert all(count_tokens(u) * fattore <= BUDGET_USCITA_TOKEN for u in unita)
    # Nessun verso perso o duplicato, e i tagli cadono fra terzine.
    versi = [v for u in unita for v in u.replace("\n\n", "\n").split("\n")]
    assert versi == [v for v in testo.split("\n\n")]
    assert all(len(u.split("\n\n")[0].split("\n")) == 3 for u in unita)


def test_a_passage_that_fits_is_left_whole_when_the_layout_is_kept() -> None:
    testo = "Primo paragrafo.\n\nSecondo paragrafo."
    assert unita_di_lavoro(testo, fattore=1.25) == [testo]


def test_a_huge_prose_passage_is_cut_only_between_paragraphs() -> None:
    paragrafi = [("parola " * 400).strip() for _ in range(12)]
    testo = "\n\n".join(paragrafi)
    unita = unita_di_lavoro(testo, fattore=1.25)
    assert len(unita) > 1
    assert [p for u in unita for p in u.split("\n\n")] == paragrafi


def test_the_engine_sends_each_unit_separately_and_keeps_everything(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_project_db(db)
    testo = "## Canto I\n\n" + _poema(600)
    with Session(make_engine(db)) as ses:
        doc = Document(path="p.txt", format="txt", sha256="d", title="Poema")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        ses.add(Chunk(document_id=doc.id, book_pos=0, chapter_ref="Canto I", text=testo,
                      sha256="c0"))
        ses.commit()
        doc_id = doc.id

    client = Prosatore()
    report = rewrite_document(db, doc_id, "C", client, critic=False,
                              form=FORM_PROSE_FROM_VERSE, modernize=True)
    fattore = espansione("rewrite", FORM_PROSE_FROM_VERSE, True)
    assert len(client.utenti) > 1
    assert all(count_tokens(u) * fattore <= BUDGET_USCITA_TOKEN for u in client.utenti)
    from bookwriter.rewrite.engine import assemble_markdown

    md = assemble_markdown(db, report.run_id)
    assert md.count("## Canto I") == 1
    assert all(f"verso numero {i} " in md for i in range(600))
