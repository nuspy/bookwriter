"""Deduplicazione semantica delle idee estratte dagli appunti.

Gli appunti ripetono: la stessa idea torna in due quaderni, in una mail e in una nota a margine,
con parole diverse. L'importazione riconosce solo i file identici e la sezionatura solo le frasi
che due blocchi condividono per costruzione; tutto il resto arriva al libro due, tre volte.

Il lavoro procede in tre tempi.

1. **Candidati dai vettori.** Per ogni idea si guardano i vicini più simili (sopra
   :data:`DUP_SIMILARITY`). È un filtro largo e da solo non basta: due idee possono stare
   vicinissime perché parlano dello stesso fatto ma dirne cose diverse, o opposte.
2. **Conferma del modello**, a lotti di poche coppie (:data:`DUP_BATCH`), con una domanda sola:
   dicono la *stessa* cosa? «Il testo riferisce che X» non è «X è vero»; un esempio di X non è X;
   due tesi opposte sullo stesso tema non sono un doppione. Nel dubbio, sono idee diverse.
3. **Fusione con traccia.** Delle due se ne tiene una (la più completa, secondo il modello); se
   l'altra porta dettagli in più, il suo testo originale viene accodato a quella tenuta, alla
   lettera. Temi, entità e date si uniscono. L'idea assorbita esce dall'elenco ma la sua copia
   completa resta in :class:`NodeMerge`, con il motivo e lo stato precedente dell'altra:
   :func:`restore_merge` la rimette com'era. Nessun contenuto sparisce senza lasciare traccia.

Le idee già usate in una struttura non vengono assorbite (si tiene semmai quella), come fa la
potatura delle idee contenute: una struttura non deve perdere voci sotto i piedi dell'autore.
Le coppie giudicate restano registrate, quindi rieseguire chiede solo le coppie nuove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.concurrency import imap_unordered
from bookwriter.config import language_label
from bookwriter.db.project_schema import (
    Edge,
    Node,
    NodeMerge,
    OutlineItem,
    ensure_notes_tables,
    make_engine,
)
from bookwriter.memory.embedder import embed_nodes
from bookwriter.memory.pairs import (
    batched,
    forget_pairs,
    judged_pairs,
    neighbour_pairs,
    node_brief,
    record_pairs,
)
from bookwriter.memory.vectors import LanceStore

#: Similarità coseno minima perché due idee siano candidate doppioni. Alta di proposito: sotto
#: questa soglia le coppie sono quasi sempre idee diverse sullo stesso tema, e ognuna costa una
#: domanda al modello.
DUP_SIMILARITY = 0.85
#: Quanti vicini per idea si considerano.
DUP_NEIGHBOURS = 5
#: Coppie per chiamata: i testi possono essere lunghi, la risposta deve restare corta.
DUP_BATCH = 10

_KIND = "duplicate"
_SAME, _DISTINCT = "stessa", "diversa"

DEDUP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pair": {"type": "integer", "description": "Numero della coppia."},
                    "same_idea": {"type": "boolean"},
                    "keep": {"type": "string", "enum": ["A", "B"],
                             "description": "La versione più completa o meglio formulata."},
                    "adds_details": {"type": "boolean",
                                     "description": "L'altra contiene dettagli assenti in quella tenuta."},
                    "reason": {"type": "string", "description": "Una frase."},
                },
                "required": ["pair", "same_idea", "keep", "adds_details", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}

_SYSTEM = (
    "Ricevi coppie di idee estratte dagli appunti di un autore. Per ciascuna coppia decidi se A e "
    "B esprimono LA STESSA IDEA, cioè affermano la stessa cosa e nel libro una delle due sarebbe "
    "una ripetizione dell'altra. Non bastano lo stesso argomento, le stesse persone o le stesse "
    "parole. Non sono la stessa idea: un'affermazione e la notizia che qualcuno la sostiene "
    "(«si dice che X» non è «X è vero»); una tesi e un suo esempio; due tesi diverse o opposte "
    "sullo stesso tema; un fatto e la sua causa o la sua conseguenza; due episodi distinti che si "
    "somigliano. Nel dubbio rispondi same_idea=false. Se sono la stessa idea indica in 'keep' la "
    "versione più completa e meglio formulata, e in 'adds_details' se l'altra contiene comunque "
    "dettagli concreti (nomi, date, cifre, passaggi) che in quella tenuta mancano. Per le coppie "
    "diverse metti keep='A' e adds_details=false. Rispondi per OGNI coppia, con il suo numero, e "
    "una motivazione di una frase."
)


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


@dataclass
class DedupReport:
    candidates: int  # coppie sopra la soglia di similarità
    already_judged: int  # coppie giudicate in esecuzioni precedenti
    judged: int  # coppie giudicate ora dal modello
    confirmed: int  # coppie che il modello ha detto «stessa idea»
    merged: int  # idee assorbite in questa esecuzione
    details_kept: int = 0  # fusioni in cui il testo dell'assorbita è stato accodato
    skipped_in_use: int = 0  # coppie lasciate stare perché entrambe usate in una struttura
    postponed: int = 0  # coppie confermate rimandate (un'idea era già coinvolta in un'altra fusione)
    without_vector: int = 0  # idee senza vettore, quindi senza candidati
    failed_batches: int = 0
    last_error: str = ""
    merges: list[dict] = field(default_factory=list)  # {merge_id, kept, merged, reason}

    def summary(self) -> str:
        base = (
            f"coppie candidate={self.candidates} · giudicate ora={self.judged} "
            f"· doppioni confermati={self.confirmed} · idee fuse={self.merged}"
        )
        if self.details_kept:
            base += f" · con dettagli conservati={self.details_kept}"
        if self.skipped_in_use:
            base += f" · lasciate (in uso)={self.skipped_in_use}"
        if self.postponed:
            base += f" · rimandate={self.postponed}"
        if self.without_vector:
            base += f" · senza vettore={self.without_vector}"
        if self.failed_batches:
            base += f" · lotti falliti={self.failed_batches}"
            if self.last_error:
                base += f" ({self.last_error})"
        return base


def _system_prompt(language: str) -> str:
    if language:
        return _SYSTEM + f" Scrivi la motivazione in {language_label(language)}."
    return _SYSTEM + " Scrivi la motivazione nella lingua delle idee."


def _ask_batch(
    client: SupportsChatJson,
    batch: list[tuple[tuple[int, int], float]],
    nodes: dict[int, Node],
    system: str,
    model: str | None,
) -> dict[tuple[int, int], dict]:
    """Una chiamata per un lotto di coppie. Ritorna il verdetto normalizzato per coppia."""
    lines = []
    for number, ((a, b), _sim) in enumerate(batch, start=1):
        lines.append(f"Coppia {number}\nA: {node_brief(nodes[a])}\nB: {node_brief(nodes[b])}")
    result = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(lines)}],
        schema=DEDUP_SCHEMA,
        model=model,
    )
    out: dict[tuple[int, int], dict] = {}
    for item in (result.get("verdicts") if isinstance(result, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        try:
            number = int(item.get("pair"))
        except (TypeError, ValueError):
            continue
        if not 1 <= number <= len(batch) or batch[number - 1][0] in out:
            continue
        keep = str(item.get("keep") or "A").strip().upper()[:1]
        out[batch[number - 1][0]] = {
            "same": item.get("same_idea") is True,
            "keep": "B" if keep == "B" else "A",
            "adds_details": item.get("adds_details") is True,
            "reason": " ".join(str(item.get("reason") or "").split())[:400],
        }
    return out


def _snapshot(node: Node) -> dict:
    return node.model_dump()


def _union(first: list[str] | None, second: list[str] | None, limit: int) -> list[str]:
    out: list[str] = []
    for value in list(first or []) + list(second or []):
        if value and value not in out:
            out.append(value)
    return out[:limit]


def _merge_pair(
    ses: Session, kept: Node, gone: Node, *, similarity: float, reason: str, append_text: bool
) -> NodeMerge:
    """Fonde ``gone`` in ``kept`` dentro la sessione aperta e lascia la traccia della fusione."""
    record = NodeMerge(
        kept_node_id=kept.id,
        merged_node_id=gone.id,
        similarity=round(similarity, 4),
        reason=reason,
        merged_snapshot=_snapshot(gone),
        kept_before=_snapshot(kept),
    )
    gone_text = (gone.raw_text or "").strip()
    if append_text and gone_text and gone_text not in (kept.raw_text or ""):
        kept.raw_text = f"{(kept.raw_text or '').rstrip()}\n\n{gone_text}"
        record.details_added = True
    kept.themes = _union(kept.themes, gone.themes, 6)
    kept.entities = _union(kept.entities, gone.entities, 15)
    if not kept.event_date and gone.event_date:
        kept.event_date = gone.event_date
        kept.date_confidence = gone.date_confidence
    kept.salience = max(kept.salience or 0.0, gone.salience or 0.0)
    ses.add(kept)
    ses.add(record)

    # Gli archi dell'idea assorbita: quelli da vettori si rifanno col grafo; gli altri (legami
    # logici, collegamenti a mano) valgono per la stessa idea e passano a quella tenuta.
    edges = ses.exec(
        select(Edge).where((Edge.src_node == gone.id) | (Edge.dst_node == gone.id))
    ).all()
    present = {
        (e.src_node, e.dst_node, e.type)
        for e in ses.exec(
            select(Edge).where((Edge.src_node == kept.id) | (Edge.dst_node == kept.id))
        ).all()
    }
    for edge in edges:
        if edge.source == "vector":
            ses.delete(edge)
            continue
        src = kept.id if edge.src_node == gone.id else edge.src_node
        dst = kept.id if edge.dst_node == gone.id else edge.dst_node
        if src == dst or (src, dst, edge.type) in present:
            ses.delete(edge)
            continue
        edge.src_node, edge.dst_node = src, dst
        present.add((src, dst, edge.type))
        ses.add(edge)
    # Le fusioni che l'assorbita aveva fatto a sua volta passano alla tenuta, che ora ne porta
    # il testo: annullarle deve agire sull'idea giusta.
    for older in ses.exec(select(NodeMerge).where(NodeMerge.kept_node_id == gone.id)).all():
        older.kept_node_id = kept.id
        ses.add(older)
    # I giudizi sull'assorbita non servono più. Quelli sulla tenuta: «diversa da X» resta vero
    # (era diversa da X, e lo era anche l'assorbita), i legami logici invece vanno rifatti
    # perché il suo testo ora dice di più.
    forget_pairs(ses, {gone.id})
    forget_pairs(ses, {kept.id}, ("link",))
    ses.delete(gone)
    ses.flush()
    return record


def deduplicate_nodes(
    db_path: Path,
    vectors_path: Path,
    client: SupportsChatJson,
    *,
    model: str | None = None,
    language: str = "",
    similarity: float = DUP_SIMILARITY,
    k: int = DUP_NEIGHBOURS,
    batch_size: int = DUP_BATCH,
    concurrency: int = 4,
    embed_model_id: str = "",
    progress=None,
) -> DedupReport:
    """Trova le idee che dicono la stessa cosa e le fonde, lasciando traccia di ogni fusione.

    Se ``client`` sa anche calcolare embedding, prima si completano i vettori mancanti e alla
    fine si rifanno quelli delle idee cambiate; altrimenti si usano i vettori presenti e il
    rapporto dice quante idee ne erano senza. ``progress(done, total)`` conta i lotti.
    """
    engine = make_engine(db_path)
    ensure_notes_tables(engine)
    can_embed = callable(getattr(client, "embed", None))
    if can_embed:
        embed_nodes(db_path, vectors_path, client, model_id=embed_model_id)  # type: ignore[arg-type]

    with Session(engine) as ses:
        nodes = {n.id: n for n in ses.exec(select(Node)).all()}
    valid = set(nodes)
    with_vector = LanceStore(vectors_path).existing_ids("node") & valid
    candidates = neighbour_pairs(vectors_path, valid, k=k, min_similarity=similarity)
    known = judged_pairs(db_path, _KIND)
    todo = sorted(
        ((pair, sim) for pair, sim in candidates.items() if pair not in known),
        key=lambda x: -x[1],
    )
    report = DedupReport(
        candidates=len(candidates),
        already_judged=len(candidates) - len(todo),
        judged=0,
        confirmed=0,
        merged=0,
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

    verdicts: dict[tuple[int, int], dict] = {}
    for done, (batch, result) in enumerate(imap_unordered(_call, batches, concurrency), start=1):
        if progress:
            progress(done, len(batches))
        if result is None:
            report.failed_batches += 1
            continue
        verdicts.update(result)
    report.judged = len(verdicts)
    report.last_error = errors[-1] if errors else ""

    # Le coppie «diverse» si registrano subito: non vanno richieste mai più.
    record_pairs(db_path, _KIND, {p: _DISTINCT for p, v in verdicts.items() if not v["same"]})
    confirmed = sorted(
        ((pair, candidates[pair], v) for pair, v in verdicts.items() if v["same"]),
        key=lambda x: -x[1],
    )
    report.confirmed = len(confirmed)
    if not confirmed:
        return report

    with Session(engine) as ses:
        used = set(ses.exec(select(OutlineItem.node_id)).all())
    absorbed: set[int] = set()
    keepers: set[int] = set()
    changed: set[int] = set()
    store = LanceStore(vectors_path)
    for (a, b), sim, verdict in confirmed:
        keep_id, gone_id = (b, a) if verdict["keep"] == "B" else (a, b)
        append_text = verdict["adds_details"]
        if gone_id in used and keep_id not in used:
            # Si tiene l'idea già in una struttura. L'altra era la più completa secondo il
            # modello: il suo testo si accoda, così non si perde nulla.
            keep_id, gone_id, append_text = gone_id, keep_id, True
        if gone_id in used:
            report.skipped_in_use += 1
            continue
        if {keep_id, gone_id} & absorbed or gone_id in keepers:
            # Catene (A=B, B=C) non si chiudono alla cieca: la coppia torna alla prossima
            # esecuzione, quando l'idea tenuta avrà già il suo testo definitivo.
            report.postponed += 1
            continue
        with Session(engine) as ses:
            kept, gone = ses.get(Node, keep_id), ses.get(Node, gone_id)
            if kept is None or gone is None:
                continue
            record = _merge_pair(
                ses, kept, gone, similarity=sim, reason=verdict["reason"], append_text=append_text
            )
            ses.commit()
            report.merges.append(
                {"merge_id": record.id, "kept": keep_id, "merged": gone_id, "reason": verdict["reason"]}
            )
            if record.details_added:
                report.details_kept += 1
        absorbed.add(gone_id)
        keepers.add(keep_id)
        changed.update({keep_id, gone_id})
        report.merged += 1

    # Vettori: l'assorbita esce dai vicini, la tenuta (testo e temi cambiati) va rifatta.
    store.delete("node", sorted(changed))
    if can_embed and keepers:
        try:
            embed_nodes(db_path, vectors_path, client, model_id=embed_model_id)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - si rifanno al prossimo «Collega le idee»
            report.last_error = str(exc)[:200]
    return report


def list_merges(db_path: Path, *, node_id: int | None = None, include_restored: bool = False) -> list[dict]:
    """Le fusioni registrate, dalla più recente: cosa è stato assorbito, in cosa e perché.

    Con ``node_id`` si limitano a quelle in cui quell'idea è la tenuta: sono le sue «fonti».
    """
    engine = make_engine(db_path)
    ensure_notes_tables(engine)
    with Session(engine) as ses:
        query = select(NodeMerge).order_by(NodeMerge.id.desc())
        if node_id is not None:
            query = query.where(NodeMerge.kept_node_id == node_id)
        if not include_restored:
            query = query.where(NodeMerge.status == "merged")
        rows = ses.exec(query).all()
    out = []
    for row in rows:
        snap = row.merged_snapshot or {}
        out.append({
            "merge_id": row.id,
            "kept_node_id": row.kept_node_id,
            "merged_node_id": row.merged_node_id,
            "merged_summary": snap.get("summary", ""),
            "merged_text": snap.get("raw_text", ""),
            "merged_document_id": snap.get("document_id"),
            "merged_chunk_id": snap.get("chunk_id"),
            "similarity": row.similarity,
            "reason": row.reason,
            "details_added": row.details_added,
            "status": row.status,
            "created_at": row.created_at,
        })
    return out


def restore_merge(db_path: Path, merge_id: int, vectors_path: Path | None = None) -> int:
    """Annulla una fusione: l'idea assorbita torna com'era. Ritorna l'id con cui è tornata.

    Riprende il suo id originale se è libero. All'idea tenuta si toglie il testo che le era
    stato accodato; temi ed entità aggiunti restano (non tolgono nulla a nessuno). La coppia
    viene registrata come «diversa», perché chi annulla ha deciso che non è un doppione.
    """
    engine = make_engine(db_path)
    ensure_notes_tables(engine)
    with Session(engine) as ses:
        record = ses.get(NodeMerge, merge_id)
        if record is None or record.status != "merged":
            raise ValueError(f"Fusione #{merge_id} inesistente o già annullata.")
        data = dict(record.merged_snapshot or {})
        if ses.get(Node, record.merged_node_id) is not None:
            data.pop("id", None)
        restored = Node.model_validate(data)
        ses.add(restored)
        kept = ses.get(Node, record.kept_node_id)
        gone_text = (data.get("raw_text") or "").strip()
        if kept is not None and record.details_added and gone_text:
            suffix = f"\n\n{gone_text}"
            if (kept.raw_text or "").endswith(suffix):
                kept.raw_text = kept.raw_text[: -len(suffix)]
                ses.add(kept)
        record.status = "restored"
        kept_id = record.kept_node_id
        ses.add(record)
        ses.commit()
        ses.refresh(restored)
        restored_id = restored.id
    record_pairs(db_path, _KIND, {(kept_id, restored_id): _DISTINCT})
    if vectors_path is not None:
        LanceStore(vectors_path).delete("node", [kept_id, restored_id])
    return restored_id
