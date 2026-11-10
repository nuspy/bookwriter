"""Legami logici tipizzati fra le idee: causa, sequenza, contraddizione, esempio.

Il grafo tematico (:mod:`bookwriter.memory.graph`) dice quali idee stanno vicine, non come stanno
fra loro. Per costruire un libro serve sapere di più: che un'idea **spiega** l'altra (causa), che
va **prima** perché senza di lei la seconda non si capisce (sequenza), che le due **si smentiscono**
(contraddizione, da sciogliere o da raccontare), che una è il **caso concreto** dell'altra
(esempio). Sono questi legami a decidere l'ordine dentro un capitolo e a dare all'autore i punti
in cui il suo materiale si contraddice.

Come per la deduplica, i vettori propongono le coppie (i vicini tematici di ogni idea) e il
modello le giudica a lotti brevi, con verso, fiducia e una frase di motivazione. Si giudica ciò
che le idee **affermano**, non gli argomenti che toccano: due idee che citano lo stesso fatto non
sono per questo causa l'una dell'altra. Nel dubbio il legame è «nessuna», e non si scrive.

Gli archi vanno nella tabella ``edge`` con il tipo già previsto dallo schema (vedi
:data:`EDGE_TYPES`) e ``source="llm"``; la motivazione in ``rationale``, la fiducia in ``weight``.
Ogni coppia giudicata, anche senza legame, resta registrata: rieseguire chiede solo le nuove.
Con ``rebuild=True`` si rifà tutto da capo. Gli archi a mano (``source="manual"``) non si toccano.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.concurrency import imap_unordered
from bookwriter.config import language_label
from bookwriter.db.project_schema import Edge, Node, NodePairCheck, ensure_notes_tables, make_engine
from bookwriter.memory.embedder import embed_nodes
from bookwriter.memory.pairs import batched, judged_pairs, neighbour_pairs, node_brief, record_pairs
from bookwriter.memory.vectors import LanceStore

#: Relazione chiesta al modello → tipo d'arco dello schema. Il verso dell'arco è sempre
#: ``src → dst`` = «causa → effetto», «prima → dopo», «esempio → idea illustrata».
EDGE_TYPES: dict[str, str] = {
    "causa": "causal",
    "sequenza": "temporal",
    "contraddizione": "contradicts",
    "esempio": "example_of",
}
RELATIONS = (*EDGE_TYPES, "nessuna")
TYPED_EDGE_TYPES = tuple(EDGE_TYPES.values())

#: Vicini per idea fra cui cercare legami: pochi, perché ogni coppia è una domanda al modello.
LINK_NEIGHBOURS = 4
#: Similarità minima di una coppia candidata. Più bassa della deduplica: causa ed esempio
#: legano spesso idee che si somigliano poco nelle parole.
LINK_MIN_SIMILARITY = 0.45
#: Sotto questa fiducia il legame non si scrive.
LINK_MIN_CONFIDENCE = 0.5
#: Coppie per chiamata.
LINK_BATCH = 12

_KIND = "link"

LINKS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pair": {"type": "integer"},
                    "relation": {"type": "string", "enum": list(RELATIONS)},
                    "direction": {"type": "string", "enum": ["A->B", "B->A"]},
                    "confidence": {"type": "number", "description": "0..1"},
                    "reason": {"type": "string", "description": "Una frase."},
                },
                "required": ["pair", "relation", "direction", "confidence", "reason"],
            },
        }
    },
    "required": ["links"],
}

_SYSTEM = (
    "Ricevi coppie di idee tratte dagli appunti di un autore. Per ogni coppia indica quale legame "
    "logico c'è fra A e B, scegliendone UNO solo:\n"
    "- causa: una delle due è la causa, la ragione o la spiegazione dell'altra;\n"
    "- sequenza: una deve venire prima dell'altra, nel tempo o nel ragionamento (per capire la "
    "seconda serve aver letto la prima);\n"
    "- contraddizione: affermano cose incompatibili fra loro;\n"
    "- esempio: una è un caso concreto, un episodio o un'illustrazione dell'altra;\n"
    "- nessuna: nessuno dei legami precedenti vale con sicurezza (anche se parlano dello stesso "
    "argomento).\n"
    "Giudica ciò che le idee AFFERMANO, non gli argomenti che toccano: citare lo stesso fatto non "
    "crea un legame; un'idea che riferisce un'opinione o una notizia («si dice che X») non afferma "
    "X. Nel dubbio rispondi «nessuna». Verso: 'A->B' se è A la causa, A viene prima, o A è "
    "l'esempio; 'B->A' nel caso opposto; per contraddizione e nessuna usa 'A->B'. 'confidence' è "
    "la tua sicurezza fra 0 e 1. Rispondi per OGNI coppia, con il suo numero, e una motivazione "
    "di una frase."
)


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


@dataclass
class LinkReport:
    candidates: int
    already_judged: int
    judged: int
    links: int  # archi tipizzati scritti in questa esecuzione
    by_type: dict[str, int] = field(default_factory=dict)  # per relazione (causa, sequenza…)
    without_vector: int = 0
    failed_batches: int = 0
    last_error: str = ""

    def summary(self) -> str:
        kinds = ", ".join(f"{k}={v}" for k, v in sorted(self.by_type.items())) or "nessuno"
        base = (
            f"coppie candidate={self.candidates} · giudicate ora={self.judged} "
            f"· legami scritti={self.links} ({kinds})"
        )
        if self.without_vector:
            base += f" · senza vettore={self.without_vector}"
        if self.failed_batches:
            base += f" · lotti falliti={self.failed_batches}"
            if self.last_error:
                base += f" ({self.last_error})"
        return base


def normalize_relation(value: object) -> str:
    """Riconduce la risposta a una delle :data:`RELATIONS` (il server non vincola l'enum)."""
    text = str(value or "").strip().lower()
    if text.startswith(("caus", "cause", "ragion")):
        return "causa"
    if text.startswith(("seq", "success", "tempor", "preced", "prima", "order")):
        return "sequenza"
    if text.startswith(("contrad", "contrast", "incompat")):
        return "contraddizione"
    if text.startswith(("esemp", "exampl", "illustr", "caso")):
        return "esempio"
    return "nessuna"


def _confidence(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1.0:  # qualche modello risponde in percentuale
        number /= 100.0
    return max(0.0, min(1.0, number))


def _system_prompt(language: str) -> str:
    if language:
        return _SYSTEM + f" Scrivi la motivazione in {language_label(language)}."
    return _SYSTEM + " Scrivi la motivazione nella lingua delle idee."


def _ask_batch(
    client: SupportsChatJson,
    batch: list[tuple[int, int]],
    nodes: dict[int, Node],
    system: str,
    model: str | None,
) -> dict[tuple[int, int], dict]:
    lines = [
        f"Coppia {number}\nA: {node_brief(nodes[a], 700)}\nB: {node_brief(nodes[b], 700)}"
        for number, (a, b) in enumerate(batch, start=1)
    ]
    result = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(lines)}],
        schema=LINKS_SCHEMA,
        model=model,
    )
    out: dict[tuple[int, int], dict] = {}
    for item in (result.get("links") if isinstance(result, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("pair"))
        except (TypeError, ValueError):
            continue
        if not 1 <= number <= len(batch) or batch[number - 1] in out:
            continue
        a, b = batch[number - 1]
        relation = normalize_relation(item.get("relation"))
        reverse = str(item.get("direction") or "").replace(" ", "").upper().startswith("B")
        src, dst = (b, a) if reverse and relation != "contraddizione" else (a, b)
        out[(a, b)] = {
            "relation": relation,
            "src": src,
            "dst": dst,
            "confidence": _confidence(item.get("confidence")),
            "reason": " ".join(str(item.get("reason") or "").split())[:400],
        }
    return out


def build_typed_links(
    db_path: Path,
    vectors_path: Path,
    client: SupportsChatJson,
    *,
    model: str | None = None,
    language: str = "",
    k: int = LINK_NEIGHBOURS,
    min_similarity: float = LINK_MIN_SIMILARITY,
    min_confidence: float = LINK_MIN_CONFIDENCE,
    batch_size: int = LINK_BATCH,
    concurrency: int = 4,
    rebuild: bool = False,
    embed_model_id: str = "",
    progress=None,
) -> LinkReport:
    """Chiede al modello il legame logico fra le idee vicine e lo scrive come arco tipizzato.

    Idempotente: le coppie già giudicate non si richiedono e i loro archi restano. Con
    ``rebuild=True`` si cancellano archi e giudizi prodotti da qui e si rifà tutto. Se
    ``client`` calcola anche embedding, i vettori mancanti si completano prima di cominciare.
    """
    engine = make_engine(db_path)
    ensure_notes_tables(engine)
    if callable(getattr(client, "embed", None)):
        embed_nodes(db_path, vectors_path, client, model_id=embed_model_id)  # type: ignore[arg-type]

    if rebuild:
        with Session(engine) as ses:
            for edge in ses.exec(
                select(Edge).where(Edge.source == "llm", Edge.type.in_(TYPED_EDGE_TYPES))
            ).all():
                ses.delete(edge)
            for row in ses.exec(select(NodePairCheck).where(NodePairCheck.kind == _KIND)).all():
                ses.delete(row)
            ses.commit()

    with Session(engine) as ses:
        nodes = {n.id: n for n in ses.exec(select(Node)).all()}
    valid = set(nodes)
    with_vector = LanceStore(vectors_path).existing_ids("node") & valid
    candidates = neighbour_pairs(vectors_path, valid, k=k, min_similarity=min_similarity)
    known = judged_pairs(db_path, _KIND)
    todo = sorted((p for p in candidates if p not in known), key=lambda p: -candidates[p])
    report = LinkReport(
        candidates=len(candidates),
        already_judged=len(candidates) - len(todo),
        judged=0,
        links=0,
        without_vector=len(valid - with_vector),
    )

    system = _system_prompt(language)
    batches = batched(todo, batch_size)
    errors: list[str] = []

    def _call(batch):
        try:
            return _ask_batch(client, batch, nodes, system, model)
        except Exception as exc:  # noqa: BLE001 - lotto fallito: le sue coppie si richiedono dopo
            errors.append(str(exc)[:200])
            return None

    counts: Counter[str] = Counter()
    for done, (batch, result) in enumerate(imap_unordered(_call, batches, concurrency), start=1):
        if result is None:
            report.failed_batches += 1
        else:
            # Ogni lotto si salva appena arriva: un lavoro interrotto non perde ciò che ha pagato.
            verdicts: dict[tuple[int, int], str] = {}
            with Session(engine) as ses:
                for pair, verdict in result.items():
                    relation = verdict["relation"]
                    written = relation != "nessuna" and verdict["confidence"] >= min_confidence
                    verdicts[pair] = relation if written else "nessuna"
                    # Un giudizio nuovo sulla stessa coppia sostituisce il vecchio (succede
                    # quando un'idea cambia, per esempio dopo una fusione).
                    a, b = pair
                    for old in ses.exec(select(Edge).where(
                        Edge.source == "llm", Edge.type.in_(TYPED_EDGE_TYPES),
                        ((Edge.src_node == a) & (Edge.dst_node == b))
                        | ((Edge.src_node == b) & (Edge.dst_node == a)),
                    )).all():
                        ses.delete(old)
                    if not written:
                        continue
                    ses.add(Edge(
                        src_node=verdict["src"], dst_node=verdict["dst"],
                        type=EDGE_TYPES[relation], weight=round(verdict["confidence"], 3),
                        rationale=verdict["reason"] or None, source="llm",
                    ))
                    counts[relation] += 1
                ses.commit()
            record_pairs(db_path, _KIND, verdicts)
            report.judged += len(result)
        if progress:
            progress(done, len(batches))
    report.links = sum(counts.values())
    report.by_type = dict(counts)
    report.last_error = errors[-1] if errors else ""
    return report


def typed_links(db_path: Path, *, node_ids: set[int] | None = None) -> list[dict]:
    """I legami tipizzati presenti, per mostrarli o per ordinare: relazione, verso, motivo.

    Comprende quelli scritti a mano con gli stessi tipi. ``relation`` è il nome italiano della
    relazione (causa, sequenza…), ``type`` quello dello schema.
    """
    names = {v: k for k, v in EDGE_TYPES.items()}
    with Session(make_engine(db_path)) as ses:
        rows = ses.exec(select(Edge).where(Edge.type.in_(TYPED_EDGE_TYPES))).all()
    out = []
    for edge in rows:
        if node_ids is not None and not (edge.src_node in node_ids and edge.dst_node in node_ids):
            continue
        out.append({
            "src": edge.src_node, "dst": edge.dst_node, "type": edge.type,
            "relation": names.get(edge.type, edge.type), "confidence": edge.weight,
            "reason": edge.rationale or "", "source": edge.source,
        })
    return out


def contradictions(db_path: Path) -> list[dict]:
    """Le coppie che si smentiscono, con le due sintesi: da far vedere all'autore."""
    links = [link for link in typed_links(db_path) if link["relation"] == "contraddizione"]
    if not links:
        return []
    ids = {link["src"] for link in links} | {link["dst"] for link in links}
    with Session(make_engine(db_path)) as ses:
        nodes = {n.id: n for n in ses.exec(select(Node).where(Node.id.in_(ids))).all()}
    return [
        {**link, "src_summary": (nodes[link["src"]].summary or nodes[link["src"]].raw_text)[:200],
         "dst_summary": (nodes[link["dst"]].summary or nodes[link["dst"]].raw_text)[:200]}
        for link in links
        if link["src"] in nodes and link["dst"] in nodes
    ]
