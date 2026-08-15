"""Test del passaggio globale di coerenza."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.coherence import apply_canonical_map, coherence_pass_compose, extract_canonical_map
from bookwriter.db.project_schema import (
    ComposedChapter,
    Outline,
    init_project_db,
    make_engine,
)


class FakeCoherenceClient:
    def chat_json(self, messages, *, schema, model=None):
        return {"mappings": [{"canonical": "Luca", "variants": ["Luca", "Luka", "Lucas"]}]}


def test_apply_canonical_map() -> None:
    text = "Luka corre. Lucas dorme. Luca legge."
    new, n = apply_canonical_map(text, {"Luka": "Luca", "Lucas": "Luca"})
    assert new == "Luca corre. Luca dorme. Luca legge."
    assert n == 2


def test_extract_canonical_map() -> None:
    mapping = extract_canonical_map("qualunque testo", FakeCoherenceClient())
    assert mapping == {"Luka": "Luca", "Lucas": "Luca"}


def test_coherence_pass_compose(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    init_project_db(db)
    engine = make_engine(db)
    with Session(engine) as ses:
        o = Outline(name="O")
        ses.add(o)
        ses.commit()
        ses.refresh(o)
        ses.add(ComposedChapter(outline_id=o.id, chapter_id=1, position=0, title="C1", text="Luka arriva.", words=2))
        ses.add(ComposedChapter(outline_id=o.id, chapter_id=2, position=1, title="C2", text="Lucas parte.", words=2))
        ses.commit()
        oid = o.id

    report = coherence_pass_compose(db, oid, FakeCoherenceClient())
    assert report["replacements"] == 2
    with Session(engine) as ses:
        texts = [r.text for r in ses.exec(select(ComposedChapter).order_by(ComposedChapter.position))]
    assert texts == ["Luca arriva.", "Luca parte."]


def test_the_whole_book_is_read_not_just_its_opening() -> None:
    """Le sostituzioni finiscono su ogni pagina: decidere leggendo solo l'inizio è arbitrario."""

    class WindowSpyClient:
        def __init__(self) -> None:
            self.letto = 0

        def chat_json(self, messages, *, schema, model=None):
            testo = messages[-1]["content"]
            self.letto += len(testo)
            # Ogni finestra segnala una variante diversa: solo leggendole tutte si trovano.
            canonica = "Fine" if "epilogo" in testo else "Luca"
            return {"mappings": [{"canonical": canonica, "variants": [f"{canonica}-var"]}]}

    testo = "Luka cammina nel bosco.\n" * 4000 + "\nQui comincia l'epilogo del libro.\n"
    client = WindowSpyClient()
    mapping = extract_canonical_map(testo, client)

    assert client.letto >= len(testo) * 0.99  # letto tutto, non i primi 8.000 caratteri
    assert "Fine-var" in mapping  # anche la variante che compare solo in fondo
