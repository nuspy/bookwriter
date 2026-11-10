"""Struttura del libro ricavata da uno **scopo** e da un **lettore** dichiarati.

Gli altri ordinamenti partono dal materiale: lo raggruppano per argomento, per data, per arco.
Un libro di saggistica però si costruisce al contrario, da cosa deve ottenere e per chi: lo
stesso mucchio di appunti diventa un manuale per principianti o un saggio per colleghi, e i
capitoli non sono gli stessi. Qui l'autore dichiara scopo e lettore e il modello propone la
struttura che serve a quel lettore per arrivare a quello scopo.

Il lavoro è diviso in due tempi, perché i modelli locali scrivono risposte brevi.

1. **Il piano** (una chiamata): parti e capitoli, ognuno con il suo obiettivo per il lettore, il
   perché sta lì e la frase di passaggio dal capitolo precedente. Il modello vede le idee (o,
   se sono troppe, i temi e le idee più rilevanti) e i legami logici più sicuri fra le idee
   (causa e sequenza, da :mod:`bookwriter.memory.links`), che dicono cosa va spiegato prima.
2. **L'assegnazione** (a lotti brevi): ogni idea va nel capitolo dove serve di più. Le idee che
   il modello non colloca non si perdono: finiscono in un capitolo «da collocare» in fondo.

Dentro ogni capitolo l'ordine segue i legami (la causa prima dell'effetto, il «prima» prima del
«dopo», l'idea prima del suo esempio) e, dove i legami tacciono, l'ordine degli appunti.

Il piano completo resta in :class:`OutlinePlan`: i capitoli previsti rimasti senza materiale sono
lacune da mostrare all'autore, e la stesura rilegge obiettivo e transizione di ogni capitolo.
Con uno stile scelto (``style_contract``) titoli, obiettivi e transizioni sono scritti in quello.
"""

from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.config import language_label
from bookwriter.db.project_schema import (
    Node,
    OutlineChapter,
    OutlinePlan,
    ensure_notes_tables,
    make_engine,
)
from bookwriter.memory.links import typed_links
from bookwriter.memory.pairs import batched
from bookwriter.organize.common import (
    OrganizeReport,
    chunk_positions,
    load_nodes,
    order_key,
    persist_outline,
)
from bookwriter.organize.thematic import style_note

#: Fino a quante idee il piano le vede una per una; oltre, vede i temi e le idee più rilevanti.
PLAN_DIRECT_IDEAS = 150
#: Quante idee rilevanti mostrare quando sono troppe per vederle tutte.
PLAN_SAMPLE_IDEAS = 100
#: Quanti legami logici (causa, sequenza) mostrare al piano come indizi d'ordine.
PLAN_LINK_HINTS = 40
#: Idee per chiamata nell'assegnazione ai capitoli.
ASSIGN_BATCH = 30
#: Titolo del capitolo che raccoglie le idee non collocate: niente esce dal libro in silenzio.
UNPLACED_TITLE = "Idee da collocare"

ORDERING = "purpose"

_CHAPTER = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "goal": {"type": "string", "description": "Cosa ottiene il lettore da questo capitolo."},
        "rationale": {"type": "string", "description": "Perché sta qui, in una frase."},
        "transition": {"type": "string", "description": "Frase di passaggio dal capitolo prima."},
    },
    "required": ["title", "goal", "rationale", "transition"],
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rationale": {"type": "string", "description": "Perché questa struttura serve a scopo e lettore."},
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "chapters": {"type": "array", "items": _CHAPTER},
                },
                "required": ["title", "chapters"],
            },
        },
    },
    "required": ["rationale", "parts"],
}

ASSIGN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"type": "integer"}, "chapter": {"type": "integer"}},
                "required": ["id", "chapter"],
            },
        }
    },
    "required": ["assignments"],
}


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


@dataclass
class PurposeReport(OrganizeReport):
    parts: int = 0
    gaps: list[str] = field(default_factory=list)  # capitoli previsti rimasti senza idee
    unplaced: int = 0  # idee finite in «da collocare»
    failed_batches: int = 0
    last_error: str = ""

    def summary(self) -> str:
        base = f"{super().summary()} · parti={self.parts}"
        if self.gaps:
            base += f" · capitoli senza materiale={len(self.gaps)}"
        if self.unplaced:
            base += f" · idee da collocare={self.unplaced}"
        if self.failed_batches:
            base += f" · lotti falliti={self.failed_batches}"
            if self.last_error:
                base += f" ({self.last_error})"
        return base


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _idea_line(node: Node, limit: int = 160) -> str:
    themes = ", ".join(node.themes or [])
    line = f"[{node.id}] {_clip(node.summary or node.raw_text, limit)}"
    return f"{line} (temi: {themes})" if themes else line


def _material_view(nodes: list[Node], chunk_pos: dict[int, int]) -> str:
    """Il materiale come lo vede il piano: tutte le idee, o i temi e le più rilevanti."""
    ordered = sorted(nodes, key=lambda n: order_key(n, chunk_pos))
    if len(ordered) <= PLAN_DIRECT_IDEAS:
        return f"Idee disponibili ({len(ordered)}):\n" + "\n".join(_idea_line(n) for n in ordered)
    themes = Counter(t for n in nodes for t in (n.themes or []))
    top = sorted(ordered, key=lambda n: -(n.salience or 0.0))[:PLAN_SAMPLE_IDEAS]
    top.sort(key=lambda n: order_key(n, chunk_pos))
    return (
        f"Le idee sono {len(nodes)}: troppe per elencarle tutte.\n"
        "Temi ricorrenti (numero di idee): "
        + "; ".join(f"{t} ({c})" for t, c in themes.most_common(60))
        + f"\n\nLe {len(top)} idee più rilevanti:\n"
        + "\n".join(_idea_line(n, 120) for n in top)
    )


def _link_hints(links: list[dict], nodes: dict[int, Node]) -> str:
    """I legami più sicuri di causa e sequenza, scritti per esteso: dicono cosa va prima."""
    useful = sorted(
        (link for link in links if link["relation"] in ("causa", "sequenza")),
        key=lambda link: -(link["confidence"] or 0.0),
    )[:PLAN_LINK_HINTS]
    if not useful:
        return ""
    verbs = {"causa": "è causa di", "sequenza": "va prima di"}
    lines = [
        f"- «{_clip(nodes[link['src']].summary or nodes[link['src']].raw_text, 90)}» "
        f"{verbs[link['relation']]} "
        f"«{_clip(nodes[link['dst']].summary or nodes[link['dst']].raw_text, 90)}»"
        for link in useful
    ]
    return "Legami logici già accertati fra le idee (indicano cosa spiegare prima):\n" + "\n".join(lines)


def _plan_system(purpose: str, reader: str, chapters: int | None, language: str, style_contract: str) -> str:
    lingua = language_label(language) if language else "la stessa lingua delle idee"
    quanti = f"circa {chapters} capitoli" if chapters else "il numero di capitoli che serve davvero"
    return (
        style_note(style_contract, "titoli, obiettivi, motivazioni e transizioni")
        + "Sei un editor che progetta la struttura di un libro a partire dagli appunti dell'autore.\n"
        f"SCOPO del libro: {purpose}\n"
        f"LETTORE a cui è destinato: {reader}\n"
        f"Proponi la struttura che porta QUESTO lettore a QUESTO scopo: {quanti}, raggruppati in "
        "parti solo se servono (altrimenti una sola parte con titolo vuoto). Parti da ciò che il "
        "lettore sa già e arriva a ciò che lo scopo richiede: prima i concetti che servono a "
        "capire i successivi, le cause prima degli effetti, gli esempi dopo l'idea che illustrano. "
        "Usa solo argomenti presenti nel materiale; se allo scopo manca qualcosa di essenziale, "
        "puoi prevedere un capitolo anche senza materiale, dicendolo nella motivazione. "
        "Per ogni capitolo: un titolo, l'obiettivo (cosa ottiene il lettore), la motivazione della "
        "posizione (una frase) e una frase di passaggio dal capitolo precedente (vuota per il "
        "primo). In 'rationale' spiega in 2-3 frasi perché questa struttura serve a scopo e lettore. "
        f"Scrivi tutto in {lingua}. Sii conciso: niente elenchi di idee nel piano."
    )


def _ask_plan(
    client: SupportsChatJson, system: str, material: str, hints: str, model: str | None
) -> tuple[str, list[dict]]:
    content = material + (f"\n\n{hints}" if hints else "")
    result = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": content}],
        schema=PLAN_SCHEMA,
        model=model,
    )
    if not isinstance(result, dict):
        result = {}
    parts: list[dict] = []
    for part in result.get("parts") or []:
        if not isinstance(part, dict):
            continue
        chapters = []
        for ch in part.get("chapters") or []:
            if not isinstance(ch, dict) or not str(ch.get("title") or "").strip():
                continue
            chapters.append({key: _clip(str(ch.get(key) or ""), 600) for key in ("title", "goal", "rationale", "transition")})
        if chapters:
            parts.append({"title": _clip(str(part.get("title") or ""), 200), "chapters": chapters})
    return _clip(str(result.get("rationale") or ""), 1500), parts


def _assign_system(purpose: str, reader: str, plan_lines: str) -> str:
    return (
        "Assegna ciascuna idea al capitolo del piano in cui serve di più, tenendo conto dello "
        f"scopo del libro («{purpose}») e del lettore («{reader}»).\n"
        f"Piano:\n{plan_lines}\n"
        "Rispondi per OGNI idea con il suo id e il NUMERO del capitolo. Usa 0 solo se l'idea non "
        "serve in nessun capitolo."
    )


def _ask_assign(
    client: SupportsChatJson, system: str, batch: list[Node], n_chapters: int, model: str | None
) -> dict[int, int]:
    result = client.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": "Idee:\n" + "\n".join(_idea_line(n, 220) for n in batch)}],
        schema=ASSIGN_SCHEMA,
        model=model,
    )
    if not isinstance(result, dict):
        result = {}
    wanted = {n.id for n in batch}
    out: dict[int, int] = {}
    for item in result.get("assignments") or []:
        if not isinstance(item, dict):
            continue
        try:
            node_id, chapter = int(item.get("id")), int(item.get("chapter"))
        except (TypeError, ValueError):
            continue
        if node_id in wanted and node_id not in out and 0 <= chapter <= n_chapters:
            out[node_id] = chapter
    return out


def order_by_links(nodes: list[Node], links: list[dict], chunk_pos: dict[int, int]) -> list[Node]:
    """Ordina le idee di un capitolo seguendo i legami, e dove tacciono l'ordine degli appunti.

    Vincoli: causa prima dell'effetto, «prima» prima del «dopo», l'idea prima del suo esempio.
    È un ordinamento topologico stabile; se i legami formano un cerchio, si scioglie dal punto
    che viene prima negli appunti, invece di fallire.
    """
    by_id = {n.id: n for n in nodes}
    after: dict[int, set[int]] = {nid: set() for nid in by_id}
    indegree: dict[int, int] = {nid: 0 for nid in by_id}
    for link in links:
        if link["relation"] in ("causa", "sequenza"):
            first, then = link["src"], link["dst"]
        elif link["relation"] == "esempio":
            first, then = link["dst"], link["src"]  # l'idea, poi il suo esempio
        else:
            continue
        if first in by_id and then in by_id and first != then and then not in after[first]:
            after[first].add(then)
            indegree[then] += 1

    def key(nid: int) -> tuple:
        return order_key(by_id[nid], chunk_pos)

    ready = [(key(nid), nid) for nid, deg in indegree.items() if deg == 0]
    heapq.heapify(ready)
    ordered: list[Node] = []
    placed: set[int] = set()
    while len(ordered) < len(by_id):
        if not ready:
            # Cerchio di legami: si parte dall'idea che negli appunti viene prima.
            nid = min((n for n in by_id if n not in placed), key=key)
            indegree[nid] = 0
            heapq.heappush(ready, (key(nid), nid))
        _, nid = heapq.heappop(ready)
        if nid in placed:
            continue
        placed.add(nid)
        ordered.append(by_id[nid])
        for nxt in after[nid]:
            if nxt in placed:
                continue
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(ready, (key(nxt), nxt))
    return ordered


def organize_purpose(
    db_path: Path,
    client: SupportsChatJson,
    purpose: str,
    reader: str,
    *,
    name: str = "Struttura per scopo e lettore",
    chapters: int | None = None,
    model: str | None = None,
    language: str = "",
    style_contract: str = "",
    style_profile_id: int | None = None,
    batch_size: int = ASSIGN_BATCH,
    progress=None,
) -> PurposeReport:
    """Propone parti e capitoli per lo scopo e il lettore dati e vi colloca le idee.

    Una chiamata per il piano, poi una ogni ``batch_size`` idee per l'assegnazione (più un
    secondo giro per le idee che il modello ha saltato). ``progress(done, total)`` conta le
    chiamate. Se il piano non arriva, solleva ``RuntimeError``: senza piano non c'è struttura.
    """
    purpose, reader = (purpose or "").strip(), (reader or "").strip()
    if not purpose or not reader:
        raise ValueError("Servono sia lo scopo del libro sia il lettore a cui è destinato.")
    nodes = load_nodes(db_path)
    if not nodes:
        raise ValueError("Nessun nodo: esegui prima `atomize`.")
    engine = make_engine(db_path)
    ensure_notes_tables(engine)
    chunk_pos = chunk_positions(db_path)
    node_by_id = {n.id: n for n in nodes}
    links = typed_links(db_path, node_ids=set(node_by_id))

    batches = batched(sorted(nodes, key=lambda n: order_key(n, chunk_pos)), batch_size)
    total = 1 + len(batches)
    if progress:
        progress(0, total)

    system = _plan_system(purpose, reader, chapters, language, style_contract)
    try:
        rationale, parts = _ask_plan(
            client, system, _material_view(nodes, chunk_pos), _link_hints(links, node_by_id), model
        )
    except Exception as exc:  # senza piano non si può proseguire: lo si dice chiaro
        raise RuntimeError(f"Il modello non ha proposto una struttura: {str(exc)[:200]}") from exc
    flat = [(pi, ch) for pi, part in enumerate(parts) for ch in part["chapters"]]
    if not flat:
        raise RuntimeError("Il modello non ha proposto nessun capitolo: riprova o cambia modello.")
    done = 1
    if progress:
        progress(done, total)

    plan_lines = "\n".join(
        f"{i}. {ch['title']} — {ch['goal']}" for i, (_pi, ch) in enumerate(flat, start=1)
    )
    assign_system = _assign_system(purpose, reader, plan_lines)
    assigned: dict[int, int] = {}
    errors: list[str] = []
    failed = 0

    def _run(batch_list: list[list[Node]]) -> None:
        nonlocal done, failed
        for batch in batch_list:
            try:
                assigned.update(_ask_assign(client, assign_system, batch, len(flat), model))
            except Exception as exc:  # noqa: BLE001 - lotto fallito: le sue idee vanno «da collocare»
                errors.append(str(exc)[:200])
                failed += 1
            done += 1
            if progress:
                progress(done, max(total, done))

    _run(batches)
    missing = [n for n in nodes if n.id not in assigned]
    if missing:
        # Secondo giro per le idee saltate: capita che il modello ne dimentichi qualcuna.
        retry = batched(sorted(missing, key=lambda n: order_key(n, chunk_pos)), batch_size)
        total += len(retry)
        _run(retry)

    members: list[list[Node]] = [[] for _ in flat]
    unplaced: list[Node] = []
    for node in nodes:
        chapter = assigned.get(node.id, 0)
        (members[chapter - 1] if chapter else unplaced).append(node)

    groups: list[tuple[str, list[Node]]] = []
    kept_index: list[int] = []
    for index, ((_pi, ch), chapter_nodes) in enumerate(zip(flat, members)):
        if chapter_nodes:
            ids = {n.id for n in chapter_nodes}
            local = [link for link in links if link["src"] in ids and link["dst"] in ids]
            groups.append((ch["title"], order_by_links(chapter_nodes, local, chunk_pos)))
            kept_index.append(index)
    if unplaced:
        groups.append((UNPLACED_TITLE, sorted(unplaced, key=lambda n: order_key(n, chunk_pos))))

    report = persist_outline(
        db_path, name, ORDERING, groups, style_profile_id=style_profile_id
    )
    with Session(engine) as ses:
        chapter_ids = list(ses.exec(
            select(OutlineChapter.id)
            .where(OutlineChapter.outline_id == report.outline_id)
            .order_by(OutlineChapter.position)
        ))
    id_of = dict(zip(kept_index, chapter_ids))
    data_parts = []
    for pi, part in enumerate(parts):
        data_parts.append({"title": part["title"], "chapters": []})
    for index, (pi, ch) in enumerate(flat):
        data_parts[pi]["chapters"].append({
            **ch, "chapter_id": id_of.get(index), "ideas": len(members[index]),
        })
    gaps = [ch["title"] for index, (_pi, ch) in enumerate(flat) if not members[index]]
    data = {
        "rationale": rationale,
        "parts": data_parts,
        "gaps": gaps,
        "unplaced_chapter_id": chapter_ids[-1] if unplaced and chapter_ids else None,
    }
    with Session(engine) as ses:
        ses.add(OutlinePlan(
            outline_id=report.outline_id, purpose=purpose, reader=reader,
            style_profile_id=style_profile_id, data=data,
        ))
        ses.commit()
    if progress:
        progress(total, total)
    return PurposeReport(
        outline_id=report.outline_id,
        chapters=report.chapters,
        items=report.items,
        parts=len(parts),
        gaps=gaps,
        unplaced=len(unplaced),
        failed_batches=failed,
        last_error=errors[-1] if errors else "",
    )


def outline_plan(db_path: Path, outline_id: int) -> dict | None:
    """Il piano ragionato di una struttura, o ``None`` se non è nata da scopo e lettore.

    ``{purpose, reader, style_profile_id, rationale, parts: [{title, chapters: [{chapter_id,
    title, goal, rationale, transition, ideas}]}], gaps, unplaced_chapter_id}``.
    """
    engine = make_engine(db_path)
    ensure_notes_tables(engine)
    with Session(engine) as ses:
        row = ses.exec(
            select(OutlinePlan).where(OutlinePlan.outline_id == outline_id).order_by(OutlinePlan.id.desc())
        ).first()
    if row is None:
        return None
    data = dict(row.data or {})
    return {
        "purpose": row.purpose,
        "reader": row.reader,
        "style_profile_id": row.style_profile_id,
        "rationale": data.get("rationale", ""),
        "parts": data.get("parts", []),
        "gaps": data.get("gaps", []),
        "unplaced_chapter_id": data.get("unplaced_chapter_id"),
    }


def chapter_guidance(db_path: Path, outline_id: int) -> dict:
    """Per la stesura: scopo, lettore e, per capitolo, obiettivo e transizione.

    ``{"purpose", "reader", "by_id": {chapter_id: {...}}, "by_title": {titolo: {...}}}``, o un
    dizionario vuoto se la struttura non ha un piano. Il titolo serve da ripiego: modificare la
    struttura a mano ricrea i capitoli con id nuovi, ma di solito con gli stessi titoli.
    """
    plan = outline_plan(db_path, outline_id)
    if not plan:
        return {}
    by_id: dict[int, dict] = {}
    by_title: dict[str, dict] = {}
    for part in plan["parts"]:
        for ch in part.get("chapters", []):
            entry = {"goal": ch.get("goal", ""), "transition": ch.get("transition", ""),
                     "part": part.get("title", "")}
            if ch.get("chapter_id") is not None:
                by_id[int(ch["chapter_id"])] = entry
            by_title.setdefault(ch.get("title", ""), entry)
    return {"purpose": plan["purpose"], "reader": plan["reader"], "by_id": by_id, "by_title": by_title}
