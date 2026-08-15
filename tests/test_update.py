"""Test dell'aggiornamento di una bozza (client finto: nessuna rete)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from sqlmodel import Session

from bookwriter.db.project_schema import Node, init_project_db, make_engine
from bookwriter.update import revise_draft, split_draft, write_revision_docx, write_revision_html
from bookwriter.update.revise import NEW_CHAPTER


class VettoriFinti:
    """Embedding deterministici: ogni tema ha il suo asse, così le somiglianze sono prevedibili."""

    TEMI = ("mare", "montagna", "deserto")

    def _vettore(self, testo: str) -> list[float]:
        basso = testo.lower()
        parole = {"mare": ("mare", "onde"), "montagna": ("montagna", "neve"), "deserto": ("deserto", "stelle")}
        return [1.0 if any(p in basso for p in parole[t]) else 0.0 for t in self.TEMI] + [0.05]

    def embed(self, texts, *, kind="document", model=None):
        return [self._vettore(t) for t in texts]

    def chat_json(self, messages, *, schema, model=None):
        return {"text": "versione integrata dal modello"}


def _progetto(tmp_path: Path, testi: list[str]) -> Path:
    db = tmp_path / "p.db"
    init_project_db(db)
    with Session(make_engine(db)) as ses:
        ses.add_all([Node(raw_text=t, summary=t[:20]) for t in testi])
        ses.commit()
    return db


def test_split_draft_recognises_chapters_and_paragraphs() -> None:
    bozza = "# Primo\n\nUno.\n\nDue.\n\n# Secondo\n\nTre."
    capitoli = split_draft(bozza)
    assert [c.title for c in capitoli] == ["Primo", "Secondo"]
    assert capitoli[0].paragraphs == ["Uno.", "Due."]
    assert capitoli[1].paragraphs == ["Tre."]
    # Senza titoli resta un capitolo solo, non zero.
    assert len(split_draft("Solo un paragrafo.")) == 1


def test_missing_material_is_added_and_repetitions_removed(tmp_path: Path) -> None:
    db = _progetto(tmp_path, [
        "Il mare è calmo stamattina e le onde arrivano piano.",
        "La montagna d'inverno resta silenziosa sotto la neve.",
    ])
    bozza = "# Paesaggi\n\nIl mare è calmo e le onde arrivano piano.\n\nIl mare è calmo e le onde arrivano piano."

    report = revise_draft(db, bozza, VettoriFinti())
    conteggi = report.counts()
    assert conteggi["remove"] == 1, "la ripetizione va tolta"
    assert conteggi["add"] == 1, "la montagna manca dalla bozza"
    assert conteggi["keep"] == 1

    # Il risultato è il libro intero, in ordine di lettura.
    assert [c.operation for c in report.changes] == ["keep", "remove", "add"]
    aggiunta = next(c for c in report.changes if c.operation == "add")
    assert "montagna" in aggiunta.text and aggiunta.reason


def test_material_without_a_home_becomes_a_new_chapter(tmp_path: Path) -> None:
    db = _progetto(tmp_path, ["Il deserto di notte diventa freddo e pieno di stelle."])
    report = revise_draft(db, "# Il mare\n\nIl mare è calmo e le onde arrivano piano.", VettoriFinti())
    nuovo = next(c for c in report.changes if c.operation == "add")
    assert nuovo.chapter == NEW_CHAPTER


def test_partial_coverage_is_merged_into_one_paragraph(tmp_path: Path) -> None:
    """Un passaggio affine ma incompleto viene integrato, non accodato."""

    class QuasiUguale(VettoriFinti):
        def embed(self, texts, *, kind="document", model=None):
            # Coseno ~0.71 fra i due: sopra la soglia di affinità, sotto quella di copertura.
            return [[1.0, 1.0 if "onde" in t.lower() else 0.0, 0.0, 0.0] for t in texts]

    db = _progetto(tmp_path, ["Il mare è calmo stamattina e le onde arrivano piano sulla riva."])
    report = revise_draft(db, "# Il mare\n\nIl mare è calmo.", QuasiUguale())
    integrato = next(c for c in report.changes if c.operation == "replace")
    assert integrato.previous == "Il mare è calmo."
    assert integrato.text == "versione integrata dal modello"

    # Disattivando l'integrazione, il contenuto viene accodato come aggiunta.
    senza = revise_draft(db, "# Il mare\n\nIl mare è calmo.", QuasiUguale(), integrate=False)
    assert senza.counts()["replace"] == 0 and senza.counts()["add"] == 1


def test_export_marks_additions_removals_and_replacements(tmp_path: Path) -> None:
    """Le marcature richieste devono finire davvero nel DOCX e nell'HTML."""
    db = _progetto(tmp_path, [
        "Il mare è calmo stamattina e le onde arrivano piano.",
        "La montagna d'inverno resta silenziosa sotto la neve.",
    ])
    bozza = "# Paesaggi\n\nIl mare è calmo e le onde arrivano piano.\n\nIl mare è calmo e le onde arrivano piano."
    report = revise_draft(db, bozza, VettoriFinti())

    docx = write_revision_docx(report, tmp_path / "rev.docx", title="Prova")
    xml = zipfile.ZipFile(docx).read("word/document.xml").decode("utf-8")
    assert "C62828" in xml, "manca il rosso"
    assert "1B5E20" in xml, "manca il verde della legenda"
    assert "w:strike" in xml, "manca il barrato"
    assert "w:pBdr" in xml, "manca la cornice del contenuto nuovo"

    html = write_revision_html(report, tmp_path / "rev.html", title="Prova").read_text(encoding="utf-8")
    assert "class='add'" in html and "class='del'" in html
    assert "line-through" in html


def test_a_project_without_sections_says_so(tmp_path: Path) -> None:
    db = tmp_path / "vuoto.db"
    init_project_db(db)
    with pytest.raises(ValueError, match="Nessuna sezione"):
        revise_draft(db, "# Capitolo\n\nTesto.", VettoriFinti())


def test_embeddings_go_in_batches_and_report_progress(tmp_path: Path) -> None:
    """Una sola richiesta con centinaia di testi butta via tutta l'analisi se fallisce."""

    class ContaLotti(VettoriFinti):
        def __init__(self) -> None:
            self.lotti: list[int] = []

        def embed(self, texts, *, kind="document", model=None):
            self.lotti.append(len(texts))
            return super().embed(texts, kind=kind, model=model)

    db = _progetto(tmp_path, [f"Il mare numero {i} con le sue onde." for i in range(70)])
    bozza = "\n\n".join(f"La montagna {i} sotto la neve." for i in range(50))
    client = ContaLotti()
    avanzamenti: list[tuple[int, int]] = []

    revise_draft(db, bozza, client, progress=lambda d, t: avanzamenti.append((d, t)))

    assert max(client.lotti) <= 32, "richieste di embedding troppo grandi"
    assert len(avanzamenti) > 3, "l'avanzamento deve seguire il lavoro, non fare tre scatti"
    assert all(0 <= d <= t for d, t in avanzamenti)


def test_failed_merges_are_reported_instead_of_passing_unnoticed(tmp_path: Path) -> None:
    """Col modello irraggiungibile il contenuto viene accodato invece che fuso: va detto."""

    class FusioneRotta(VettoriFinti):
        def embed(self, texts, *, kind="document", model=None):
            return [[1.0, 1.0 if "onde" in t.lower() else 0.0, 0.0, 0.0] for t in texts]

        def chat_json(self, messages, *, schema, model=None):
            raise RuntimeError("modello irraggiungibile")

    db = _progetto(tmp_path, ["Il mare è calmo stamattina e le onde arrivano piano sulla riva."])
    report = revise_draft(db, "# Il mare\n\nIl mare è calmo.", FusioneRotta())

    assert report.merges_failed == 1
    assert "integrazioni non riuscite" in report.summary()
    assert report.counts()["add"] == 1, "il contenuto non va perso"


def test_the_updated_book_can_be_had_in_any_of_the_three_formats(tmp_path: Path) -> None:
    """Anche il libro aggiornato è un libro: il formato lo sceglie chi lo riceve."""
    from bookwriter.update import write_revision

    db = _progetto(tmp_path, [
        "Il mare è calmo stamattina e le onde arrivano piano.",
        "La montagna d'inverno resta silenziosa sotto la neve.",
    ])
    bozza = "# Paesaggi\n\nIl mare è calmo e le onde arrivano piano."
    report = revise_draft(db, bozza, VettoriFinti())

    for fmt in ("docx", "pdf", "md"):
        out = write_revision(report, tmp_path / f"rev.{fmt}", fmt, title="Prova")
        assert out.exists() and out.stat().st_size > 0

    # Le marcature restano leggibili anche fuori da Word.
    markdown = (tmp_path / "rev.md").read_text(encoding="utf-8")
    assert "~~" in markdown or "[nuovo]" in markdown
    assert markdown.startswith("# Prova")


def test_an_unknown_output_format_is_refused_instead_of_writing_nothing(tmp_path: Path) -> None:
    from bookwriter.update import write_revision

    db = _progetto(tmp_path, ["Il mare è calmo stamattina."])
    report = revise_draft(db, "# P\n\nIl mare è calmo.", VettoriFinti())
    with pytest.raises(ValueError, match="Formato non supportato"):
        write_revision(report, tmp_path / "rev.epub", "epub", title="Prova")
