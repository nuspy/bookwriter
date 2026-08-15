"""Test dell'atomizzazione chunk→nodi (client chat fittizio, senza rete)."""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, select

from bookwriter.atomize.nodes import _snap_verbatim, atomize_chunks, build_system_prompt
from bookwriter.db.project_schema import Chunk, Document, Node, init_project_db, make_engine

CHUNK_TEXT = "Il cielo è azzurro oggi. Domani pioverà molto forte."


class FakeChatClient:
    """Ritorna due nodi con excerpt noti (uno esatto, uno con spazi alterati)."""

    def chat_json(self, messages, *, schema, model=None):
        return {
            "nodes": [
                {
                    "excerpt": "Il cielo è azzurro oggi.",
                    "summary": "meteo di oggi",
                    "type": "fatto",
                    "themes": ["meteo"],
                    "entities": [],
                    "event_date": None,
                    "salience": 0.7,
                },
                {
                    "excerpt": "Domani   pioverà molto forte.",  # doppi spazi apposta
                    "summary": "previsione domani",
                    "type": "fatto",
                    "themes": ["meteo"],
                    "entities": [],
                    "event_date": None,
                    "salience": 0.6,
                },
            ]
        }


def _seed_chunk(db_path: Path) -> None:
    init_project_db(db_path)
    engine = make_engine(db_path)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        ses.add(Chunk(document_id=doc.id, book_pos=0, text=CHUNK_TEXT, sha256="c0"))
        ses.commit()


def test_snap_verbatim() -> None:
    assert _snap_verbatim(CHUNK_TEXT, "Il cielo è azzurro oggi.") == "Il cielo è azzurro oggi."
    # Con spazi diversi, aggancia comunque al testo originale.
    assert _snap_verbatim(CHUNK_TEXT, "Domani   pioverà molto forte.") == "Domani pioverà molto forte."
    # Parafrasi (non presente): ripiega sulla frase originale più sovrapposta.
    assert _snap_verbatim(CHUNK_TEXT, "Il cielo appare azzurro") == "Il cielo è azzurro oggi."


def test_atomize_creates_nodes_and_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed_chunk(db)
    client = FakeChatClient()

    r1 = atomize_chunks(db, client)
    assert r1.chunks_done == 1 and r1.nodes_created == 2

    engine = make_engine(db)
    with Session(engine) as ses:
        nodes = ses.exec(select(Node)).all()
    assert len(nodes) == 2
    assert all(n.chunk_id is not None for n in nodes)
    # Testo verbatim agganciato all'originale.
    assert nodes[1].raw_text == "Domani pioverà molto forte."

    r2 = atomize_chunks(db, client)  # già atomizzato → saltato
    assert r2.chunks_done == 0 and r2.chunks_skipped == 1 and r2.nodes_created == 0
    assert r2.chunks_failed == 0


class BrokenChatClient:
    """Simula un modello che non risponde (es. non caricato in LM Studio)."""

    def chat_json(self, messages, *, schema, model=None):
        raise RuntimeError("modello non disponibile")


def test_atomize_reports_failed_chunks(tmp_path: Path) -> None:
    """Un chunk fallito non deve sparire in silenzio: va contato e spiegato."""
    db = tmp_path / "p.db"
    _seed_chunk(db)

    rep = atomize_chunks(db, BrokenChatClient())
    assert rep.chunks_done == 0 and rep.nodes_created == 0
    assert rep.chunks_failed == 1
    assert "modello non disponibile" in rep.last_error
    assert "falliti=1" in rep.summary()


class CapturingClient:
    """Registra il prompt di sistema ricevuto e restituisce due nodi consecutivi."""

    def __init__(self) -> None:
        self.system = ""

    def chat_json(self, messages, *, schema, model=None):
        self.system = messages[0]["content"]
        return {
            "nodes": [
                {
                    "excerpt": "Il cielo è azzurro oggi.",
                    "summary": "s1",
                    "type": "fatto",
                    "themes": ["  Meteo ", "meteo", "Cielo"],  # doppioni e maiuscole apposta
                    "entities": [],
                    "event_date": None,
                    "salience": 0.7,
                },
                {
                    "excerpt": "Domani pioverà molto forte.",
                    "summary": "s2",
                    "type": "fatto",
                    "themes": ["meteo"],
                    "entities": [],
                    "event_date": None,
                    "salience": 0.6,
                },
            ]
        }


def test_atomize_measures_coverage_and_normalizes_themes(tmp_path: Path) -> None:
    """La copertura dice quanta parte del testo finisce nelle idee; i temi si ripetono."""
    db = tmp_path / "p.db"
    _seed_chunk(db)
    client = CapturingClient()

    rep = atomize_chunks(db, client)
    # I due excerpt coprono l'intero chunk tranne lo spazio che li separa.
    assert rep.coverage_pct == 98
    assert "copertura=" in rep.summary()

    with Session(make_engine(db)) as ses:
        nodes = ses.exec(select(Node)).all()
    assert nodes[0].themes == ["meteo", "cielo"]  # minuscolo, senza doppioni, ordine conservato


def test_atomize_prompt_forces_language_and_coverage(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    _seed_chunk(db)
    client = CapturingClient()

    atomize_chunks(db, client, language="hu")
    assert "ungherese (magyar)" in client.system
    assert "COPRILO INTERAMENTE" in client.system

    # Senza lingua scelta: i metadati seguono la lingua del testo di partenza.
    assert "nella stessa lingua del testo" in build_system_prompt("")


class NestedChatClient:
    """Come i modelli reali a copertura piena: un passaggio ampio più le sue singole frasi."""

    def chat_json(self, messages, *, schema, model=None):
        def node(excerpt, summary):
            return {"excerpt": excerpt, "summary": summary, "type": "fatto", "themes": ["meteo"],
                    "entities": [], "event_date": None, "salience": 0.6}
        return {"nodes": [
            node(CHUNK_TEXT, "il meteo di oggi e domani"),          # passaggio intero
            node("Il cielo è azzurro oggi.", "oggi"),                # contenuto nel precedente
            node("Domani pioverà molto forte.", "domani"),           # idem
            node("Il cielo è azzurro oggi.", "doppione esatto"),
        ]}


def test_atomize_keeps_only_maximal_excerpts(tmp_path: Path) -> None:
    """Gli excerpt contenuti in uno più ampio vanno scartati: stessa copertura, niente ripetizioni."""
    db = tmp_path / "p.db"
    _seed_chunk(db)

    rep = atomize_chunks(db, NestedChatClient())
    assert rep.nodes_created == 1 and rep.nodes_dropped == 3
    assert rep.coverage_pct == 100
    assert "ridondanti scartati=3" in rep.summary()

    with Session(make_engine(db)) as ses:
        texts = [n.raw_text for n in ses.exec(select(Node)).all()]
    assert texts == [CHUNK_TEXT]


def test_merge_adjacent_nodes_builds_passages(tmp_path: Path) -> None:
    """Le idee consecutive diventano un passaggio continuo, recuperando le frasi di raccordo."""
    from bookwriter.atomize.nodes import merge_adjacent_nodes

    source = "Prima frase. Frase di raccordo. Seconda frase. Terza frase."
    db = tmp_path / "p.db"
    init_project_db(db)
    engine = make_engine(db)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc); ses.commit(); ses.refresh(doc)
        chunk = Chunk(document_id=doc.id, book_pos=0, text=source, sha256="c0")
        ses.add(chunk); ses.commit(); ses.refresh(chunk)
        ses.add_all([
            Node(document_id=doc.id, chunk_id=chunk.id, raw_text="Prima frase.", summary="s1",
                 themes=["a"], entities=["X"], salience=0.4),
            Node(document_id=doc.id, chunk_id=chunk.id, raw_text="Seconda frase.", summary="s2",
                 themes=["b"], entities=["Y"], salience=0.9),
            Node(document_id=doc.id, chunk_id=chunk.id, raw_text="Terza frase.", summary="s3",
                 themes=["a"], entities=[], salience=0.5),
        ])
        ses.commit()

    created, absorbed = merge_adjacent_nodes(db, target_words=6)
    assert (created, absorbed) == (1, 3)

    with Session(engine) as ses:
        rows = ses.exec(select(Node)).all()
    assert len(rows) == 1
    passage = rows[0]
    # Il testo riprende il blocco per intero: torna dentro anche la frase di raccordo.
    assert passage.raw_text == source
    assert passage.raw_text in source  # sempre verbatim
    assert passage.summary == "s1 s2 s3"
    assert passage.themes == ["a", "b"] and passage.entities == ["X", "Y"]
    assert passage.salience == 0.9  # la più alta del gruppo


def test_merge_leaves_nodes_used_in_an_outline(tmp_path: Path) -> None:
    """Un'idea già inserita in una struttura non va assorbita: romperebbe l'indice."""
    from bookwriter.atomize.nodes import merge_adjacent_nodes
    from bookwriter.db.project_schema import Outline, OutlineChapter, OutlineItem

    source = "Alfa uno. Beta due. Gamma tre."
    db = tmp_path / "p.db"
    init_project_db(db)
    engine = make_engine(db)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc); ses.commit(); ses.refresh(doc)
        chunk = Chunk(document_id=doc.id, book_pos=0, text=source, sha256="c0")
        ses.add(chunk); ses.commit(); ses.refresh(chunk)
        nodes = [
            Node(document_id=doc.id, chunk_id=chunk.id, raw_text="Alfa uno.", summary="a"),
            Node(document_id=doc.id, chunk_id=chunk.id, raw_text="Beta due.", summary="b"),
            Node(document_id=doc.id, chunk_id=chunk.id, raw_text="Gamma tre.", summary="c"),
        ]
        ses.add_all(nodes); ses.commit()
        for n in nodes: ses.refresh(n)
        outline = Outline(name="o", ordering="thematic"); ses.add(outline); ses.commit(); ses.refresh(outline)
        ch = OutlineChapter(outline_id=outline.id, position=0, title="c"); ses.add(ch); ses.commit(); ses.refresh(ch)
        ses.add(OutlineItem(chapter_id=ch.id, position=0, node_id=nodes[0].id)); ses.commit()
        protected = nodes[0].id

    merge_adjacent_nodes(db, target_words=4)
    with Session(engine) as ses:
        remaining = {n.id for n in ses.exec(select(Node)).all()}
    assert protected in remaining


def test_merge_splits_on_topic_change_and_crosses_blocks(tmp_path: Path) -> None:
    """Le sezioni si chiudono al cambio di argomento e non si fermano al confine del blocco."""
    from bookwriter.atomize.nodes import merge_adjacent_nodes

    primo = "Parlo del mare. Il mare è calmo stamattina. Le onde arrivano piano."
    secondo = "Le onde arrivano piano. E poi il discorso cambia del tutto. Adesso parlo di montagna."
    db = tmp_path / "p.db"
    init_project_db(db)
    engine = make_engine(db)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="D")
        ses.add(doc); ses.commit(); ses.refresh(doc)
        c1 = Chunk(document_id=doc.id, book_pos=0, text=primo, sha256="c0")
        c2 = Chunk(document_id=doc.id, book_pos=1, text=secondo, sha256="c1")
        ses.add_all([c1, c2]); ses.commit(); ses.refresh(c1); ses.refresh(c2)
        ses.add_all([
            Node(document_id=doc.id, chunk_id=c1.id, raw_text="Parlo del mare.", summary="a", themes=["mare"]),
            Node(document_id=doc.id, chunk_id=c1.id, raw_text="Le onde arrivano piano.", summary="b", themes=["mare"]),
            Node(document_id=doc.id, chunk_id=c2.id, raw_text="E poi il discorso cambia del tutto.", summary="c", themes=["mare"]),
            Node(document_id=doc.id, chunk_id=c2.id, raw_text="Adesso parlo di montagna.", summary="d", themes=["montagna"]),
        ])
        ses.commit()

    created, absorbed = merge_adjacent_nodes(db, target_words=100, min_words=8)
    assert created == 1 and absorbed == 3  # la quarta idea cambia tema e resta fuori

    with Session(engine) as ses:
        rows = sorted(ses.exec(select(Node)).all(), key=lambda n: len(n.raw_text), reverse=True)
    sezione = rows[0]
    # Ha attraversato i due blocchi, ricucendo senza ripetere la frase in comune.
    assert "Parlo del mare." in sezione.raw_text
    assert "E poi il discorso cambia del tutto." in sezione.raw_text
    assert sezione.raw_text.count("Le onde arrivano piano.") == 1
    assert rows[1].raw_text == "Adesso parlo di montagna."


def test_pages_without_prose_are_never_sent_to_the_model(tmp_path: Path) -> None:
    """Da un indice non si estrae nessuna idea, e sui punti di guida il modello ripete all'infinito."""

    class ContaChiamate(FakeChatClient):
        def __init__(self) -> None:
            self.visti: list[str] = []

        def chat_json(self, messages, *, schema, model=None):
            self.visti.append(messages[-1]["content"])
            return super().chat_json(messages, schema=schema, model=model)

    db = tmp_path / "p.db"
    init_project_db(db)
    engine = make_engine(db)
    with Session(engine) as ses:
        doc = Document(path="b.pdf", format="pdf", sha256="d", title="Libro")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        ses.add(Chunk(
            document_id=doc.id, book_pos=0, text="Indice\n1 .  .  .  .  .  .  . 7\n2 .  .  .  . 19\n",
            sha256="c0",
        ))
        ses.add(Chunk(
            document_id=doc.id, book_pos=1, sha256="c1",
            text="Il viaggio cominciò all'alba, quando la nebbia copriva ancora la valle.",
        ))
        ses.commit()

    client = ContaChiamate()
    report = atomize_chunks(db, client)

    assert len(client.visti) == 1 and "viaggio" in client.visti[0]
    assert report.chunks_without_text == 1
    assert "senza testo=1" in report.summary()
