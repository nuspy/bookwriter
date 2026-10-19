"""Test della stesura narrativa (Compose → prosa), con client fittizio."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, func, select

from bookwriter.compose.narrative import compose_outline, compose_to_markdown
from bookwriter.db.project_schema import (
    ComposedChapter,
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    init_project_db,
    make_engine,
)


class FakeComposeClient:
    def __init__(self, words: int = 30) -> None:
        self.words = words

    def chat_json(self, messages, *, schema, model=None, temperature=0.0, max_tokens=None):
        return {
            "text": " ".join(["parola"] * self.words),
            "glossary": [{"term": "Faro", "rendering": "Lighthouse"}],
            "summary": "riassunto",
        }


def _seed_outline(db_path: Path) -> int:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        nodes = [Node(raw_text=t, summary=t) for t in ["Idea A1", "Idea A2", "Idea B1", "Idea B2"]]
        ses.add_all(nodes)
        ses.commit()
        for n in nodes:
            ses.refresh(n)
        outline = Outline(name="Bozza")
        ses.add(outline)
        ses.commit()
        ses.refresh(outline)
        chapters = [
            OutlineChapter(outline_id=outline.id, position=0, title="Cap A"),
            OutlineChapter(outline_id=outline.id, position=1, title="Cap B"),
        ]
        ses.add_all(chapters)
        ses.commit()
        for c in chapters:
            ses.refresh(c)
        ses.add_all([
            OutlineItem(chapter_id=chapters[0].id, position=0, node_id=nodes[0].id),
            OutlineItem(chapter_id=chapters[0].id, position=1, node_id=nodes[1].id),
            OutlineItem(chapter_id=chapters[1].id, position=0, node_id=nodes[2].id),
            OutlineItem(chapter_id=chapters[1].id, position=1, node_id=nodes[3].id),
        ])
        ses.commit()
        return outline.id


def test_compose_and_markdown(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    oid = _seed_outline(db)
    report = compose_outline(db, oid, "CONTRATTO", FakeComposeClient(words=30))
    assert report.chapters == 2
    assert report.words == 60  # 2 capitoli × 30 parole
    md = compose_to_markdown(db, oid)
    assert "# Bozza" in md and "## Cap A" in md and "## Cap B" in md and "parola" in md


def test_compose_resume_no_duplicates(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    oid = _seed_outline(db)
    compose_outline(db, oid, "C", FakeComposeClient())
    compose_outline(db, oid, "C", FakeComposeClient())  # resume: non rigenera
    engine = make_engine(db)
    with Session(engine) as ses:
        count = ses.exec(select(func.count()).select_from(ComposedChapter)).one()
    assert count == 2


def test_compose_with_target(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    oid = _seed_outline(db)
    report = compose_outline(db, oid, "C", FakeComposeClient(words=20), target_words=100)
    assert report.target_words == 100
    assert report.chapters == 2


class ShortThenLongerClient:
    """Scrive sotto il budget e allunga solo se glielo si chiede di nuovo."""

    def __init__(self) -> None:
        self.chiamate = 0

    def chat_json(self, messages, *, schema, model=None, temperature=0.0, max_tokens=None):
        self.chiamate += 1
        self.ultima_temperatura = temperature
        parole = 20 if self.chiamate == 1 else 200
        return {"text": " ".join(["parola"] * parole), "glossary": [], "summary": "s"}


def test_compose_insists_until_the_chapter_reaches_its_budget(tmp_path: Path) -> None:
    """Un solo tentativo non basta: il modello scrive sistematicamente meno del richiesto."""
    db = tmp_path / "p.db"
    outline_id = _seed_outline(db)
    client = ShortThenLongerClient()

    rep = compose_outline(db, outline_id, "contratto", client, target_words=200, tolerance_pct=5)
    assert client.chiamate > 1  # ha riprovato
    assert rep.words >= 190  # è rientrato nel bersaglio
    assert client.ultima_temperatura == 0.0, \
        "il codice non impone nessuna temperatura (il fake riceve solo il suo default)"


def test_compose_replaces_a_chapter_instead_of_duplicating_it(tmp_path: Path) -> None:
    """Rigenerando lo stesso capitolo non deve comparire due volte nel libro."""
    from sqlmodel import Session, select

    from bookwriter.db.project_schema import ComposedChapter, make_engine

    db = tmp_path / "p.db"
    outline_id = _seed_outline(db)

    compose_outline(db, outline_id, "contratto", FakeComposeClient(40), target_words=None)
    compose_outline(db, outline_id, "contratto", FakeComposeClient(40), target_words=None, resume=False)

    with Session(make_engine(db)) as ses:
        rows = ses.exec(select(ComposedChapter).where(ComposedChapter.outline_id == outline_id)).all()
    assert len(rows) == len({r.chapter_id for r in rows})


def test_changing_style_or_target_forces_a_new_writing(tmp_path: Path) -> None:
    """Cambiando stile o lunghezza il libro va riscritto, non riesportato uguale."""
    db = tmp_path / "p.db"
    outline_id = _seed_outline(db)

    primo = compose_outline(db, outline_id, "stile A", FakeComposeClient(40), target_words=None)
    assert primo.rewritten > 0

    # Stessi parametri: i capitoli già scritti si riusano (ripresa dopo un'interruzione).
    ripresa = compose_outline(db, outline_id, "stile A", FakeComposeClient(40), target_words=None)
    assert ripresa.rewritten == 0

    # Stile diverso: tutti i capitoli vanno riscritti.
    nuovo_stile = compose_outline(db, outline_id, "stile B", FakeComposeClient(40), target_words=None)
    assert nuovo_stile.rewritten == primo.rewritten

    # Anche cambiare solo il bersaglio di lunghezza impone la riscrittura.
    nuovo_target = compose_outline(db, outline_id, "stile B", FakeComposeClient(40), target_words=500)
    assert nuovo_target.rewritten == primo.rewritten


def test_progress_counts_parts_so_a_big_chapter_does_not_freeze_the_bar(tmp_path: Path) -> None:
    """Un capitolo da 8.000 parole vale dieci parti: la barra deve muoversi durante la stesura."""
    db = tmp_path / "p.db"
    outline_id = _seed_outline(db)
    letture: list[tuple[int, int]] = []

    compose_outline(
        db, outline_id, "contratto", FakeComposeClient(900), target_words=9000,
        reconcile=False, progress=lambda done, total: letture.append((done, total)),
    )

    assert len(letture) > 2, "la barra si è mossa una volta sola"
    # Avanzamento monotono e mai oltre il totale dichiarato.
    assert all(a[0] <= b[0] for a, b in zip(letture, letture[1:]))
    assert all(done <= total for done, total in letture)
    # Il primo aggiornamento non è zero: arriva appena finita la prima parte.
    assert letture[0][0] == 1
    assert letture[-1][0] == letture[-1][1]  # si chiude al 100%


def test_faithful_mode_tells_the_model_to_keep_the_original_words(tmp_path: Path) -> None:
    """In modo «fedele» il modello deve comporre, non riscrivere."""
    from bookwriter.compose.narrative import FIDELITY_FAITHFUL, FIDELITY_FREE

    class SpyClient(FakeComposeClient):
        def __init__(self) -> None:
            super().__init__(40)
            self.istruzioni: list[str] = []

        def chat_json(self, messages, *, schema, model=None, temperature=0.0, max_tokens=None):
            self.istruzioni.append(messages[0]["content"])
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    oid = _seed_outline(db)

    fedele = SpyClient()
    compose_outline(db, oid, "contratto", fedele, target_words=None, fidelity=FIDELITY_FAITHFUL)
    testo = fedele.istruzioni[0]
    assert "MASSIMA ADERENZA" in testo
    assert "parole esatte dei materiali" in testo
    assert "elimina le ripetizioni" in testo
    assert "puoi riformularli" not in testo

    db2 = tmp_path / "p2.db"
    oid2 = _seed_outline(db2)
    libera = SpyClient()
    compose_outline(db2, oid2, "contratto", libera, target_words=None, fidelity=FIDELITY_FREE)
    assert "puoi riformularli" in libera.istruzioni[0]
    assert "MASSIMA ADERENZA" not in libera.istruzioni[0]


def test_fidelity_is_part_of_the_signature(tmp_path: Path) -> None:
    """Cambiare resa deve far riscrivere il libro, non riesportare quello di prima."""
    from bookwriter.compose.narrative import FIDELITY_FAITHFUL, FIDELITY_FREE

    db = tmp_path / "p.db"
    oid = _seed_outline(db)
    primo = compose_outline(db, oid, "c", FakeComposeClient(40), target_words=None, fidelity=FIDELITY_FREE)
    riuso = compose_outline(db, oid, "c", FakeComposeClient(40), target_words=None, fidelity=FIDELITY_FREE)
    cambio = compose_outline(db, oid, "c", FakeComposeClient(40), target_words=None, fidelity=FIDELITY_FAITHFUL)
    assert riuso.rewritten == 0
    assert cambio.rewritten == primo.rewritten


def test_faithful_reduction_says_how_much_to_keep_and_how(tmp_path: Path) -> None:
    """Ridurre conservando la voce: si toglie e si accorpa, con una quota dichiarata."""
    from bookwriter.compose.narrative import FIDELITY_FAITHFUL

    class SpyClient(FakeComposeClient):
        def __init__(self) -> None:
            super().__init__(40)
            self.istruzioni: list[str] = []

        def chat_json(self, messages, *, schema, model=None, temperature=0.0, max_tokens=None):
            self.istruzioni.append(messages[0]["content"])
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    oid = _seed_outline(db)

    # Budget molto sotto il materiale (2 parole richieste contro 4 disponibili per capitolo):
    # deve comparire la consegna di riduzione.
    stretto = SpyClient()
    compose_outline(db, oid, "c", stretto, target_words=4, fidelity=FIDELITY_FAITHFUL, reconcile=False)
    testo = stretto.istruzioni[0]
    assert "Riduci per SOTTRAZIONE" in testo and "mai per parafrasi" in testo
    assert "ripetono un concetto già espresso" in testo
    assert "%" in testo

    # Budget capiente: nessuna riduzione da chiedere.
    db2 = tmp_path / "p2.db"
    oid2 = _seed_outline(db2)
    largo = SpyClient()
    compose_outline(db2, oid2, "c", largo, target_words=100000, fidelity=FIDELITY_FAITHFUL, reconcile=False)
    assert "Riduci per SOTTRAZIONE" not in largo.istruzioni[0]
