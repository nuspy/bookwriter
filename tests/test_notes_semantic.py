"""Lavorazione semantica degli appunti: doppioni, legami tipizzati, struttura per scopo, stile.

Nessuna rete e nessun modello: i vettori si scrivono a mano nello store, le risposte del modello
le danno client finti che leggono lo schema richiesto.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from bookwriter.compose import compose_outline
from bookwriter.db.project_schema import (
    Chunk,
    Document,
    Edge,
    Node,
    NodeMerge,
    NodePairCheck,
    Outline,
    OutlineChapter,
    OutlineItem,
    init_project_db,
    make_engine,
    save_style,
)
from bookwriter.memory.dedup import DEDUP_SCHEMA, deduplicate_nodes, list_merges, restore_merge
from bookwriter.memory.graph import build_graph
from bookwriter.memory.links import (
    LINKS_SCHEMA,
    build_typed_links,
    contradictions,
    normalize_relation,
    typed_links,
)
from bookwriter.memory.vectors import LanceStore
from bookwriter.organize import (
    order_by_links,
    organize_purpose,
    organize_thematic,
    outline_plan,
    outline_tree,
    retitle_outline,
    style_contract_for,
)
from bookwriter.organize.purpose import ASSIGN_SCHEMA, PLAN_SCHEMA, UNPLACED_TITLE

# --------------------------------------------------------------------------- #
#  materiale di prova
# --------------------------------------------------------------------------- #
TEXTS = [
    "La fermentazione richiede lieviti vivi.",  # 1
    "Senza lieviti vivi la fermentazione non parte, come scoprì Pasteur nel 1857.",  # 2: doppione di 1
    "Si dice che la fermentazione sia magia.",  # 3: vicino a 1, ma idea diversa
    "Il pane di segale ha crosta scura.",  # 4: lontano
]
VECTORS = [[1.0, 0.0, 0.0], [0.99, 0.05, 0.0], [0.95, 0.2, 0.0], [0.0, 0.0, 1.0]]


def _seed(db: Path, vectors: Path, texts=TEXTS, vecs=VECTORS) -> list[int]:
    init_project_db(db)
    engine = make_engine(db)
    with Session(engine) as ses:
        doc = Document(path="x", format="txt", sha256="d", title="Appunti")
        ses.add(doc)
        ses.commit()
        ses.refresh(doc)
        chunk = Chunk(document_id=doc.id, book_pos=0, text=" ".join(texts), sha256="c0")
        ses.add(chunk)
        ses.commit()
        ses.refresh(chunk)
        nodes = [
            Node(document_id=doc.id, chunk_id=chunk.id, raw_text=t, summary=t,
                 themes=[f"tema{i}"], entities=[f"ent{i}"], salience=0.5)
            for i, t in enumerate(texts)
        ]
        ses.add_all(nodes)
        ses.commit()
        ids = [n.id for n in nodes]
    LanceStore(vectors).add("node", [{"id": i, "text": t, "vector": v} for i, t, v in zip(ids, texts, vecs)])
    return ids


def _pairs(messages) -> list[tuple[str, str]]:
    """Ricava dal prompt le coppie (A, B) nell'ordine in cui il modello le vede."""
    user = messages[-1]["content"]
    out = []
    for block in user.split("Coppia ")[1:]:
        a = re.search(r"\nA: (.*)", block).group(1)
        b = re.search(r"\nB: (.*)", block).group(1)
        out.append((a, b))
    return out


class DedupClient:
    """Dice «stessa idea» solo per la coppia lieviti (1, 2), tenendo la versione con Pasteur."""

    def __init__(self) -> None:
        self.calls = 0
        self.systems: list[str] = []

    def chat_json(self, messages, *, schema, model=None):
        assert schema is DEDUP_SCHEMA
        self.calls += 1
        self.systems.append(messages[0]["content"])
        verdicts = []
        for number, (a, b) in enumerate(_pairs(messages), start=1):
            same = "lieviti" in a and "lieviti" in b
            keep = "B" if "Pasteur" in b else "A"
            verdicts.append({"pair": number, "same_idea": same, "keep": keep,
                             "adds_details": same, "reason": "stessa tesi" if same else "diverse"})
        return {"verdicts": verdicts}


# --------------------------------------------------------------------------- #
#  1. deduplicazione semantica
# --------------------------------------------------------------------------- #
def test_dedup_merges_confirmed_duplicates_and_keeps_a_full_trace(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    n1, n2, n3, _n4 = _seed(db, vec)
    client = DedupClient()

    report = deduplicate_nodes(db, vec, client, similarity=0.8, language="it")

    assert report.candidates >= 2  # (1,2), (1,3), (2,3): il 4 è lontano
    assert report.confirmed == 1 and report.merged == 1 and report.details_kept == 1
    assert "italiano" in client.systems[0].lower() or "Scrivi la motivazione" in client.systems[0]
    with Session(make_engine(db)) as ses:
        assert ses.get(Node, n1) is None  # assorbita
        kept = ses.get(Node, n2)
        # Il testo dell'assorbita è accodato alla lettera, temi ed entità uniti.
        assert TEXTS[1] in kept.raw_text and TEXTS[0] in kept.raw_text
        assert set(kept.themes) == {"tema0", "tema1"}
        assert set(kept.entities) == {"ent0", "ent1"}
        assert ses.get(Node, n3) is not None
        record = ses.exec(select(NodeMerge)).one()
        assert record.kept_node_id == n2 and record.merged_node_id == n1
        assert record.merged_snapshot["raw_text"] == TEXTS[0]
        assert record.kept_before["raw_text"] == TEXTS[1]
        distinct = ses.exec(select(NodePairCheck).where(NodePairCheck.verdict == "diversa")).all()
        assert distinct  # le coppie diverse sono registrate
    # Vettori: l'assorbita esce dallo store, la tenuta va rifatta (manca finché non si rifà).
    assert {n1, n2}.isdisjoint(LanceStore(vec).existing_ids("node"))

    merges = list_merges(db, node_id=n2)
    assert merges[0]["merged_text"] == TEXTS[0] and merges[0]["reason"] == "stessa tesi"

    # Seconda esecuzione: nessuna domanda ripetuta.
    client.calls = 0
    again = deduplicate_nodes(db, vec, client, similarity=0.8)
    assert client.calls == 0 and again.merged == 0


def test_restore_merge_puts_everything_back(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    n1, n2, _n3, _n4 = _seed(db, vec)
    report = deduplicate_nodes(db, vec, DedupClient(), similarity=0.8)
    merge_id = report.merges[0]["merge_id"]

    restored = restore_merge(db, merge_id, vec)

    assert restored == n1  # riprende il suo id
    with Session(make_engine(db)) as ses:
        assert ses.get(Node, n1).raw_text == TEXTS[0]
        assert ses.get(Node, n2).raw_text == TEXTS[1]
        assert ses.get(NodeMerge, merge_id).status == "restored"
    assert list_merges(db) == []
    with pytest.raises(ValueError):
        restore_merge(db, merge_id)


def test_dedup_never_absorbs_an_idea_used_in_a_structure(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    n1, n2, _n3, _n4 = _seed(db, vec)
    engine = make_engine(db)
    with Session(engine) as ses:
        outline = Outline(name="o")
        ses.add(outline)
        ses.commit()
        chapter = OutlineChapter(outline_id=outline.id, title="c")
        ses.add(chapter)
        ses.commit()
        # Il modello vorrebbe tenere la 2, ma è la 1 a essere già in una struttura.
        ses.add(OutlineItem(chapter_id=chapter.id, node_id=n1))
        ses.commit()

    report = deduplicate_nodes(db, vec, DedupClient(), similarity=0.8)

    assert report.merged == 1
    with Session(engine) as ses:
        assert ses.get(Node, n2) is None
        kept = ses.get(Node, n1)
        assert TEXTS[1] in kept.raw_text  # la versione più completa non va persa


def test_dedup_survives_a_failing_model(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    _seed(db, vec)

    class Broken:
        def chat_json(self, messages, *, schema, model=None):
            raise RuntimeError("server giù")

    report = deduplicate_nodes(db, vec, Broken(), similarity=0.8)

    assert report.failed_batches == 1 and report.merged == 0
    assert "server giù" in report.last_error
    with Session(make_engine(db)) as ses:
        assert len(ses.exec(select(Node)).all()) == 4
        assert ses.exec(select(NodePairCheck)).all() == []  # niente registrato: si richiede dopo


def test_dedup_repoints_typed_links_and_drops_vector_edges(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    n1, n2, n3, n4 = _seed(db, vec)
    with Session(make_engine(db)) as ses:
        ses.add(Edge(src_node=n1, dst_node=n4, type="causal", weight=0.9, source="llm"))
        ses.add(Edge(src_node=n1, dst_node=n3, type="thematic", weight=0.9, source="vector"))
        ses.commit()

    deduplicate_nodes(db, vec, DedupClient(), similarity=0.8)

    with Session(make_engine(db)) as ses:
        edges = ses.exec(select(Edge)).all()
    assert [(e.src_node, e.dst_node, e.type) for e in edges] == [(n2, n4, "causal")]


def test_existing_project_without_new_tables_still_works(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    _seed(db, vec)
    engine = make_engine(db)
    with engine.connect() as conn:  # un progetto nato prima di queste tabelle
        for table in ("node_merge", "node_pair_check", "outline_plan"):
            conn.execute(text(f"DROP TABLE {table}"))
        conn.commit()

    assert list_merges(db) == []
    assert deduplicate_nodes(db, vec, DedupClient(), similarity=0.8).merged == 1


# --------------------------------------------------------------------------- #
#  2. legami logici tipizzati
# --------------------------------------------------------------------------- #
LINK_TEXTS = [
    "Il lievito produce anidride carbonica.",  # 1
    "L'impasto lievita e aumenta di volume.",  # 2: effetto di 1
    "Il panettone di Milano lievita tre giorni.",  # 3: esempio di 2
    "Il lievito non produce gas.",  # 4: contraddice 1
]
LINK_VECTORS = [[1.0, 0.1, 0.0], [0.9, 0.3, 0.0], [0.8, 0.5, 0.0], [0.95, 0.0, 0.2]]


class LinkClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat_json(self, messages, *, schema, model=None):
        assert schema is LINKS_SCHEMA
        self.calls += 1
        links = []
        for number, (a, b) in enumerate(_pairs(messages), start=1):
            pair = {a.split("\n")[0], b.split("\n")[0]}
            rel, direction, conf = "nessuna", "A->B", 0.9
            if pair == {LINK_TEXTS[0], LINK_TEXTS[1]}:
                rel, direction = "Causa", "A->B" if LINK_TEXTS[0] in a else "B->A"
            elif pair == {LINK_TEXTS[1], LINK_TEXTS[2]}:
                rel, direction = "esempio", "A->B" if LINK_TEXTS[2] in a else "B->A"
            elif pair == {LINK_TEXTS[0], LINK_TEXTS[3]}:
                rel = "contraddizione"
            elif pair == {LINK_TEXTS[0], LINK_TEXTS[2]}:
                rel, conf = "sequenza", 0.2  # troppo incerto: non si scrive
            links.append({"pair": number, "relation": rel, "direction": direction,
                          "confidence": conf, "reason": f"motivo {number}"})
        return {"links": links}


def test_typed_links_are_written_with_type_direction_and_reason(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    n1, n2, n3, n4 = _seed(db, vec, LINK_TEXTS, LINK_VECTORS)
    build_graph(db, vec, k=3, min_weight=0.0)
    client = LinkClient()

    report = build_typed_links(db, vec, client, k=3, min_similarity=0.0)

    assert report.by_type == {"causa": 1, "esempio": 1, "contraddizione": 1}
    found = {(link["src"], link["dst"], link["relation"]) for link in typed_links(db)}
    assert (n1, n2, "causa") in found
    assert (n3, n2, "esempio") in found  # l'esempio punta all'idea che illustra
    assert any(r == "contraddizione" and {s, d} == {n1, n4} for s, d, r in found)
    assert all(link["reason"].startswith("motivo") and link["source"] == "llm" for link in typed_links(db))
    assert len(contradictions(db)) == 1

    # Idempotente: nessuna domanda ripetuta, nessun arco doppio; il grafo tematico non li tocca.
    client.calls = 0
    again = build_typed_links(db, vec, client, k=3, min_similarity=0.0)
    assert client.calls == 0 and again.links == 0
    build_graph(db, vec, k=3, min_weight=0.0)
    assert len(typed_links(db)) == 3

    # rebuild: si rifà tutto, sempre senza doppioni.
    rebuilt = build_typed_links(db, vec, client, k=3, min_similarity=0.0, rebuild=True)
    assert client.calls > 0 and rebuilt.links == 3 and len(typed_links(db)) == 3


def test_typed_links_survive_a_failing_batch(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    _seed(db, vec, LINK_TEXTS, LINK_VECTORS)

    class Flaky(LinkClient):
        def chat_json(self, messages, *, schema, model=None):
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("timeout")
            return super().chat_json(messages, schema=schema, model=model)

    report = build_typed_links(db, vec, Flaky(), k=3, min_similarity=0.0, batch_size=2, concurrency=1)
    assert report.failed_batches == 1 and report.last_error == "timeout"
    assert report.judged > 0


def test_normalize_relation_accepts_what_models_actually_write() -> None:
    assert normalize_relation("Causale") == "causa"
    assert normalize_relation("sequence") == "sequenza"
    assert normalize_relation("CONTRADDIZIONE") == "contraddizione"
    assert normalize_relation("example") == "esempio"
    assert normalize_relation("boh") == "nessuna"
    assert normalize_relation(None) == "nessuna"


# --------------------------------------------------------------------------- #
#  ordine dentro il capitolo
# --------------------------------------------------------------------------- #
def test_order_by_links_follows_cause_sequence_and_examples() -> None:
    nodes = [Node(id=i, document_id=1, chunk_id=1, raw_text=str(i)) for i in (1, 2, 3, 4)]
    links = [
        {"src": 4, "dst": 2, "relation": "causa"},  # 4 prima di 2
        {"src": 1, "dst": 2, "relation": "esempio"},  # 1 è esempio di 2: dopo
        {"src": 3, "dst": 4, "relation": "contraddizione"},  # non vincola
    ]
    assert [n.id for n in order_by_links(nodes, links, {1: 0})] == [3, 4, 2, 1]


def test_order_by_links_breaks_cycles_instead_of_failing() -> None:
    nodes = [Node(id=i, document_id=1, chunk_id=1, raw_text=str(i)) for i in (1, 2, 3)]
    links = [
        {"src": 1, "dst": 2, "relation": "sequenza"},
        {"src": 2, "dst": 1, "relation": "sequenza"},
    ]
    assert sorted(n.id for n in order_by_links(nodes, links, {1: 0})) == [1, 2, 3]


# --------------------------------------------------------------------------- #
#  3. struttura da scopo e lettore
# --------------------------------------------------------------------------- #
class PurposeClient:
    """Piano con due parti e tre capitoli (uno resterà senza materiale)."""

    def __init__(self, skip_first: int | None = None) -> None:
        self.systems: list[str] = []
        self.users: list[str] = []
        self.skip_first = skip_first  # id che il primo giro «dimentica»

    def chat_json(self, messages, *, schema, model=None):
        self.systems.append(messages[0]["content"])
        self.users.append(messages[-1]["content"])
        if schema is PLAN_SCHEMA:
            return {
                "rationale": "Dal concreto all'astratto.",
                "parts": [
                    {"title": "Le basi", "chapters": [
                        {"title": "Il lievito", "goal": "capire cosa fa il lievito",
                         "rationale": "serve a tutto il resto", "transition": ""},
                        {"title": "Storia", "goal": "conoscere le origini",
                         "rationale": "contesto", "transition": "Dopo il come, il quando."},
                    ]},
                    {"title": "La pratica", "chapters": [
                        {"title": "Il pane", "goal": "fare il pane",
                         "rationale": "applicazione", "transition": "Ora le mani in pasta."},
                    ]},
                ],
            }
        assert schema is ASSIGN_SCHEMA
        out = []
        for node_id in (int(m) for m in re.findall(r"^\[(\d+)\]", messages[-1]["content"], re.MULTILINE)):
            if node_id == self.skip_first:
                self.skip_first = None
                continue
            out.append({"id": node_id, "chapter": 3 if node_id == 4 else (0 if node_id == 3 else 1)})
        return {"assignments": out}


def test_organize_purpose_builds_parts_chapters_and_keeps_the_plan(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    n1, n2, _n3, n4 = _seed(db, vec, LINK_TEXTS, LINK_VECTORS)
    with Session(make_engine(db)) as ses:  # 2 viene prima di 1 per sequenza
        ses.add(Edge(src_node=n2, dst_node=n1, type="temporal", weight=0.9, source="llm"))
        ses.commit()
    client = PurposeClient(skip_first=n2)
    progress: list[tuple[int, int]] = []

    report = organize_purpose(
        db, client, "insegnare a fare il pane", "principianti curiosi",
        language="it", style_contract="Stile: ironico e asciutto.", style_profile_id=7,
        progress=lambda d, t: progress.append((d, t)),
    )

    assert report.parts == 2
    assert report.gaps == ["Storia"]  # capitolo previsto senza materiale
    assert report.unplaced == 1  # l'idea 3 non serve a nessun capitolo: va «da collocare»
    tree = outline_tree(db, report.outline_id)
    assert [c["title"] for c in tree] == ["Il lievito", "Il pane", UNPLACED_TITLE]
    assert [n["node_id"] for n in tree[0]["nodes"]] == [n2, n1]  # ordine dai legami
    assert [n["node_id"] for n in tree[1]["nodes"]] == [n4]
    # Lo scopo, il lettore, lo stile e i legami arrivano al piano.
    assert "insegnare a fare il pane" in client.systems[0]
    assert "principianti curiosi" in client.systems[0]
    assert "ironico e asciutto" in client.systems[0]
    assert "va prima di" in client.users[0]
    # L'idea dimenticata al primo giro è stata richiesta nel secondo.
    assert sum(1 for u in client.users[1:] if f"[{n2}]" in u) == 2
    assert progress[-1][0] == progress[-1][1]

    with Session(make_engine(db)) as ses:
        outline = ses.get(Outline, report.outline_id)
    assert outline.ordering == "purpose" and outline.style_profile_id == 7
    plan = outline_plan(db, report.outline_id)
    assert plan["purpose"] == "insegnare a fare il pane" and plan["reader"] == "principianti curiosi"
    assert plan["rationale"] == "Dal concreto all'astratto."
    assert [p["title"] for p in plan["parts"]] == ["Le basi", "La pratica"]
    first = plan["parts"][0]["chapters"][0]
    assert first["chapter_id"] == tree[0]["chapter_id"] and first["ideas"] == 2
    assert plan["parts"][0]["chapters"][1]["chapter_id"] is None
    assert plan["unplaced_chapter_id"] == tree[2]["chapter_id"]
    assert outline_plan(db, 9999) is None


def test_organize_purpose_needs_purpose_reader_and_a_plan(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    _seed(db, vec)
    with pytest.raises(ValueError):
        organize_purpose(db, PurposeClient(), "", "lettori")

    class NoPlan:
        def chat_json(self, messages, *, schema, model=None):
            raise RuntimeError("modello spento")

    with pytest.raises(RuntimeError, match="modello spento"):
        organize_purpose(db, NoPlan(), "scopo", "lettore")


def test_compose_uses_purpose_reader_and_chapter_goal(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    _seed(db, vec, LINK_TEXTS, LINK_VECTORS)
    report = organize_purpose(db, PurposeClient(), "insegnare a fare il pane", "principianti")

    class Spy:
        def __init__(self) -> None:
            self.systems: list[str] = []

        def chat_json(self, messages, *, schema, model=None):
            self.systems.append(messages[0]["content"])
            return {"text": "Prosa del capitolo.", "glossary": [], "summary": "riassunto"}

    spy = Spy()
    compose_outline(db, report.outline_id, "Scrivi chiaro.", spy, reconcile=False)
    assert any("insegnare a fare il pane" in s and "principianti" in s for s in spy.systems)
    assert any("capire cosa fa il lievito" in s for s in spy.systems)
    assert any("Ora le mani in pasta." in s for s in spy.systems)


# --------------------------------------------------------------------------- #
#  4. stile applicato al riordino
# --------------------------------------------------------------------------- #
class TitleSpy:
    def __init__(self) -> None:
        self.systems: list[str] = []

    def chat(self, messages, *, model=None):
        self.systems.append(messages[0]["content"])
        return "Titolo in stile"


def test_style_reaches_chapter_titles_and_is_remembered(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    _seed(db, vec)
    style_id = save_style(db, "Ironico", "abstract", {
        "name": "Ironico", "origin": "abstract",
        "dimensions": {}, "exemplars": [], "constraints": ["ironico e asciutto"],
    })
    contract = style_contract_for(db, style_id)
    assert "ironico e asciutto" in contract
    assert style_contract_for(db, None) == "" and style_contract_for(db, 999) == ""

    spy = TitleSpy()
    report = organize_thematic(db, vec, chapters=2, client=spy, style_contract=contract,
                               style_profile_id=style_id)
    assert spy.systems and all("ironico e asciutto" in s for s in spy.systems)
    with Session(make_engine(db)) as ses:
        assert ses.get(Outline, report.outline_id).style_profile_id == style_id

    spy.systems.clear()
    retitle_outline(db, report.outline_id, spy, style_contract=contract)
    assert spy.systems and all("ironico e asciutto" in s for s in spy.systems)

    # Senza stile il comportamento resta quello di prima.
    plain = TitleSpy()
    organize_thematic(db, vec, chapters=2, client=plain)
    assert all("stile" not in s.lower() for s in plain.systems)


def test_dedup_with_an_embedding_client_reembeds_the_kept_idea(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    n1, n2, _n3, _n4 = _seed(db, vec)

    class EmbeddingDedup(DedupClient):
        def __init__(self) -> None:
            super().__init__()
            self.embedded: list[str] = []

        def embed(self, texts, *, kind="document", model=None):
            self.embedded.extend(texts)
            return [[1.0, 0.0, 0.0] for _ in texts]

    client = EmbeddingDedup()
    report = deduplicate_nodes(db, vec, client, similarity=0.8)

    assert report.merged == 1
    ids = LanceStore(vec).existing_ids("node")
    assert n2 in ids and n1 not in ids  # la tenuta è tornata con il testo nuovo
    assert any(TEXTS[0] in t and TEXTS[1] in t for t in client.embedded)


def test_rejudging_a_pair_replaces_its_link_instead_of_doubling_it(tmp_path: Path) -> None:
    db, vec = tmp_path / "p.db", tmp_path / "v.lance"
    _seed(db, vec, LINK_TEXTS, LINK_VECTORS)
    client = LinkClient()
    build_typed_links(db, vec, client, k=3, min_similarity=0.0)
    with Session(make_engine(db)) as ses:  # come dopo una fusione: i giudizi sono dimenticati
        for row in ses.exec(select(NodePairCheck)).all():
            ses.delete(row)
        ses.commit()

    build_typed_links(db, vec, client, k=3, min_similarity=0.0)
    assert len(typed_links(db)) == 3
