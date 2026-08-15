"""Atomizzazione dei chunk in nodi-idea.

Per ogni chunk l'LLM estrae, con output strutturato (JSON Schema), un insieme di **nodi-idea**
atomici con metadati (riassunto, tipo, temi, entità, data, salienza) e un **excerpt verbatim**. Per
preservare la fedeltà (necessaria alla resa "verbatim", §8.0), l'excerpt viene **agganciato** al
testo originale del chunk: se ritrovato, si conserva il testo *alla lettera*.

Idempotente: i chunk già atomizzati (che hanno già dei nodi) vengono saltati.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.concurrency import imap_unordered
from bookwriter.config import language_label
from bookwriter.db.project_schema import Chunk, Node, OutlineItem, make_engine
from bookwriter.text_utils import has_prose, split_sentences

_NODE_TYPES = ["idea", "fatto", "citazione", "aneddoto", "argomento", "domanda"]

NODES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "excerpt": {"type": "string", "description": "Citazione verbatim dal testo."},
                    "summary": {"type": "string", "description": "Sintesi dell'idea in una frase."},
                    "type": {"type": "string", "enum": _NODE_TYPES},
                    "themes": {"type": "array", "items": {"type": "string"}},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "event_date": {"type": ["string", "null"], "description": "ISO se deducibile, altrimenti null."},
                    "salience": {"type": "number", "description": "Rilevanza 0..1."},
                },
                "required": ["excerpt", "summary", "type", "themes", "entities", "event_date", "salience"],
            },
        }
    },
    "required": ["nodes"],
}

_SYSTEM = (
    "Sei un analista che scompone un testo in idee atomiche. Per ogni idea distinta fornisci: "
    "un excerpt VERBATIM preso dal testo (copiato alla lettera, senza modifiche), una sintesi di "
    "una frase, il tipo, i temi, le entità citate, una data ISO se deducibile (altrimenti null) e "
    "una salienza 0..1. Non inventare contenuti: usa solo ciò che è nel testo."
)

# Copertura: gli excerpt sono il materiale con cui verrà scritto il libro, quindi il testo di
# partenza va percorso tutto. Excerpt più lunghi (più frasi consecutive) coprono di più senza
# moltiplicare i nodi.
_COVERAGE = (
    " Percorri il testo dall'inizio alla fine e COPRILO INTERAMENTE: ogni frase che porta contenuto "
    "deve finire dentro a un excerpt. Procedi in ordine, senza sovrapporre gli excerpt e senza "
    "saltare passaggi; un excerpt può contenere più frasi consecutive quando esprimono una sola "
    "idea. Escludi solo intestazioni, numeri di pagina e ripetizioni letterali."
    " Preferisci excerpt ampi: raggruppa 2-6 frasi consecutive che sviluppano lo stesso pensiero, "
    "invece di spezzare il testo frase per frase."
)

# Temi: diventano i titoli dei capitoli e la base dei raggruppamenti, quindi devono ripetersi
# fra nodi diversi invece di essere etichette uniche.
_THEMES = (
    " I temi sono etichette brevi (1-2 parole) in minuscolo, generali e RIPETIBILI: riusa la stessa "
    "etichetta ogni volta che torna l'argomento, non coniarne una nuova per ogni idea. Massimo 3 per "
    "nodo. Usa il tipo 'domanda' solo se l'excerpt è davvero una domanda."
)


def build_system_prompt(language: str = "") -> str:
    """Istruzioni di sistema; ``language`` (codice ISO) fissa la lingua dei metadati generati."""
    prompt = _SYSTEM + _COVERAGE + _THEMES
    if language:
        prompt += (
            f" Scrivi 'summary', 'themes' ed 'entities' in {language_label(language)}, "
            "sempre e solo in questa lingua, anche se il testo di partenza è in un'altra."
        )
    else:
        prompt += " Scrivi 'summary', 'themes' ed 'entities' nella stessa lingua del testo di partenza."
    return prompt


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


@dataclass
class AtomizeReport:
    chunks_done: int
    chunks_skipped: int
    nodes_created: int
    chunks_failed: int = 0
    chunks_without_text: int = 0  # blocchi senza prosa (indice, colophon): nessuna idea da estrarre
    nodes_dropped: int = 0  # excerpt scartati perché contenuti in uno più ampio
    last_error: str = ""
    coverage_pct: int = 0  # quota del testo di partenza finita dentro agli excerpt

    def summary(self) -> str:
        base = (
            f"chunk atomizzati={self.chunks_done} · saltati={self.chunks_skipped} "
            f"· nodi={self.nodes_created} · copertura={self.coverage_pct}%"
        )
        if self.chunks_without_text:
            base += f" · senza testo={self.chunks_without_text}"
        if self.nodes_dropped:
            base += f" · ridondanti scartati={self.nodes_dropped}"
        if self.chunks_failed:
            base += f" · falliti={self.chunks_failed}"
            if self.last_error:
                base += f" ({self.last_error})"
        return base


def _snap_verbatim(chunk_text: str, excerpt: str) -> str:
    """Aggancia l'excerpt al testo originale, così ``raw_text`` è sempre testo reale del documento.

    1) match esatto; 2) match tollerante agli spazi; 3) fallback: la **frase originale** del chunk
    con la massima sovrapposizione di parole (se l'LLM ha parafrasato).
    """
    excerpt = excerpt.strip()
    if not excerpt:
        return excerpt
    idx = chunk_text.find(excerpt)
    if idx != -1:
        return chunk_text[idx : idx + len(excerpt)]
    pattern = re.compile(r"\s+".join(re.escape(tok) for tok in excerpt.split()))
    match = pattern.search(chunk_text)
    if match:
        return match.group(0)
    # Fallback: nessuna corrispondenza letterale → scegli la frase originale più sovrapposta.
    exc_words = {w.lower() for w in re.findall(r"\w+", excerpt)}
    if not exc_words:
        return excerpt
    best, best_score = excerpt, 0
    for sentence in split_sentences(chunk_text):
        overlap = len(exc_words & {w.lower() for w in re.findall(r"\w+", sentence)})
        if overlap > best_score:
            best, best_score = sentence, overlap
    return best if best_score > 0 else excerpt


def _atomized_chunk_ids(ses: Session) -> set[int]:
    rows = ses.exec(select(Node.chunk_id).where(Node.chunk_id.is_not(None))).all()
    return {r for r in rows if r is not None}


def _coverage(chunk_text: str, excerpts: list[str]) -> tuple[int, int]:
    """Caratteri del chunk coperti dagli excerpt e caratteri totali (per misurare la resa)."""
    if not chunk_text:
        return 0, 0
    mask = bytearray(len(chunk_text))
    for excerpt in excerpts:
        if not excerpt:
            continue
        start = chunk_text.find(excerpt)
        while start != -1:
            for i in range(start, start + len(excerpt)):
                mask[i] = 1
            start = chunk_text.find(excerpt, start + 1)
    return sum(mask), len(chunk_text)


def drop_contained(rows: list[Node]) -> list[Node]:
    """Tiene solo gli excerpt **massimi** di un chunk, conservandone l'ordine.

    Chiedendo la copertura totale il modello restituisce spesso sia un passaggio lungo sia le
    singole frasi che lo compongono: il testo finirebbe due volte nel libro e le idee sarebbero
    frammentate. Scartare gli excerpt contenuti in un altro non toglie copertura.
    """
    order = {id(row): i for i, row in enumerate(rows)}
    kept: list[Node] = []
    for row in sorted(rows, key=lambda r: len(r.raw_text or ""), reverse=True):
        text = (row.raw_text or "").strip()
        if not text or any(text in k.raw_text for k in kept):
            continue
        kept.append(row)
    return sorted(kept, key=lambda r: order[id(r)])


def _clean_themes(values) -> list[str]:
    """Normalizza i temi (minuscolo, senza doppioni): devono ripetersi fra nodi diversi."""
    seen: list[str] = []
    for value in values or []:
        theme = str(value).strip().lower()
        if theme and theme not in seen:
            seen.append(theme)
    return seen


def atomize_chunks(
    db_path: Path,
    client: SupportsChatJson,
    *,
    model: str | None = None,
    language: str = "",
    concurrency: int = 4,
    progress=None,
) -> AtomizeReport:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        chunks = ses.exec(select(Chunk)).all()
        already = _atomized_chunk_ids(ses)

    todo = [c for c in chunks if c.id not in already]
    # Stesso motivo della riscrittura: da un indice o da un colophon non si estrae nessuna idea,
    # e sui punti di guida il modello ripete senza fine. Restano fuori dalle chiamate, non dal
    # conto: il rapporto dice quanti erano.
    without_text = [c for c in todo if not has_prose(c.text)]
    todo = [c for c in todo if has_prose(c.text)]
    system = build_system_prompt(language)
    errors: list[str] = []

    def _call(chunk: Chunk):
        try:
            return client.chat_json(
                [{"role": "system", "content": system}, {"role": "user", "content": chunk.text}],
                schema=NODES_SCHEMA,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 - chunk fallito: verrà ritentato alla prossima esecuzione
            errors.append(str(exc)[:200])
            return None

    nodes_created = 0
    nodes_dropped = 0
    succeeded = 0
    failed = 0
    done = 0
    covered_chars = total_chars = 0
    # Le chiamate LLM girano in parallelo; le scritture su DB restano nel thread principale.
    for chunk, result in imap_unordered(_call, todo, concurrency):
        done += 1
        if progress:
            progress(done, len(todo))
        if not result:
            failed += 1
            continue
        rows = [
            Node(
                document_id=chunk.document_id,
                chunk_id=chunk.id,
                raw_text=_snap_verbatim(chunk.text, str(nd.get("excerpt", ""))),
                summary=str(nd.get("summary", "")),
                type=nd.get("type", "idea") if nd.get("type") in _NODE_TYPES else "idea",
                themes=_clean_themes(nd.get("themes")),
                entities=list(nd.get("entities", []) or []),
                event_date=nd.get("event_date"),
                salience=float(nd.get("salience", 0.5) or 0.5),
            )
            for nd in result.get("nodes", [])
        ]
        # Il modello restituisce spesso un passaggio e le sue singole frasi: tenere entrambi
        # duplicherebbe il testo nel libro. La copertura non cambia.
        maximal = drop_contained(rows)
        nodes_dropped += len(rows) - len(maximal)
        rows = maximal
        covered, total = _coverage(chunk.text, [r.raw_text for r in rows])
        covered_chars += covered
        total_chars += total
        if rows:
            with Session(engine) as ses:
                ses.add_all(rows)
                ses.commit()
            nodes_created += len(rows)
        succeeded += 1
    return AtomizeReport(
        chunks_done=succeeded,
        chunks_skipped=len(already),
        nodes_created=nodes_created,
        chunks_failed=failed,
        chunks_without_text=len(without_text),
        nodes_dropped=nodes_dropped,
        last_error=errors[-1] if errors else "",
        coverage_pct=round(100 * covered_chars / total_chars) if total_chars else 0,
    )


def prune_contained_nodes(db_path: Path) -> int:
    """Elimina dalle idee già salvate quelle contenute in un'idea più ampia dello stesso blocco.

    Serve a recuperare le estrazioni fatte prima che il filtro fosse in linea. Non tocca le idee
    già usate in una struttura (outline) e non riduce la copertura del testo.
    """
    engine = make_engine(db_path)
    with Session(engine) as ses:
        used = {r for r in ses.exec(select(OutlineItem.node_id)).all()}
        by_chunk: dict[int, list[Node]] = {}
        for node in ses.exec(select(Node).where(Node.chunk_id.is_not(None))).all():
            by_chunk.setdefault(node.chunk_id, []).append(node)

        removed = 0
        for rows in by_chunk.values():
            keep_ids = {id(r) for r in drop_contained(rows)}
            for row in rows:
                if id(row) not in keep_ids and row.id not in used:
                    ses.delete(row)
                    removed += 1
        ses.commit()
    return removed


def merge_adjacent_nodes(
    db_path: Path, *, target_words: int = 600, min_words: int = 250
) -> tuple[int, int]:
    """Unisce le idee consecutive in **sezioni**: un discorso completo, non una frase.

    Tre scelte, tutte necessarie perché il libro non risulti un mosaico di pensieri scollegati:

    - si procede **per documento**, non per blocco: il blocco è un taglio meccanico a 1500 token
      e cade in mezzo a un ragionamento;
    - si taglia **dove cambia l'argomento** (nessun tema in comune con la sezione in corso) una
      volta superate ``min_words``, non a intervalli fissi;
    - il testo è ripreso dal blocco originale dalla prima all'ultima idea, quindi rientrano anche
      le frasi di raccordo che l'estrazione aveva lasciato fuori.

    Ritorna ``(sezioni create, idee assorbite)``. Non tocca le idee già usate in una struttura.
    """
    engine = make_engine(db_path)
    created = absorbed = 0
    with Session(engine) as ses:
        used = {r for r in ses.exec(select(OutlineItem.node_id)).all()}
        chunks = {c.id: c for c in ses.exec(select(Chunk)).all()}
        by_doc: dict[int, list[Node]] = {}
        for node in ses.exec(select(Node).where(Node.chunk_id.is_not(None))).all():
            if node.id not in used and node.chunk_id in chunks:
                by_doc.setdefault(node.document_id or 0, []).append(node)

        for rows in by_doc.values():
            rows.sort(key=_document_order(chunks))
            for group in _split_on_topic_shift(rows, target_words, min_words):
                if len(group) < 2:
                    continue
                merged = _merge_group(group, chunks)
                if merged is None:
                    continue
                ses.add(merged)
                for row in group:
                    ses.delete(row)
                created += 1
                absorbed += len(group)
        ses.commit()
    return created, absorbed


def _document_order(chunks: dict[int, Chunk]):
    """Ordine di lettura: posizione del blocco nel documento, poi posizione dentro al blocco."""

    def key(node: Node) -> tuple[int, int, int]:
        chunk = chunks[node.chunk_id]
        return (chunk.book_pos, chunk.text.find(node.raw_text), node.id or 0)

    return key


def _split_on_topic_shift(rows: list[Node], target_words: int, min_words: int) -> list[list[Node]]:
    """Sezioni consecutive: si chiude al cambio di argomento, o al più tardi al budget."""
    groups: list[list[Node]] = []
    current: list[Node] = []
    themes: set[str] = set()
    words = 0
    for row in rows:
        row_themes = {t.lower() for t in (row.themes or [])}
        topic_changed = bool(current) and bool(themes) and bool(row_themes) and not (themes & row_themes)
        if current and (words >= target_words or (topic_changed and words >= min_words)):
            groups.append(current)
            current, themes, words = [], set(), 0
        current.append(row)
        themes |= row_themes
        words += len(row.raw_text.split())
    if current:
        groups.append(current)
    return groups


def _merge_group(group: list[Node], chunks: dict[int, Chunk]) -> Node | None:
    """Costruisce la sezione: testo continuo del documento, metadati uniti.

    Il gruppo può attraversare più blocchi: si prende una fetta per blocco e si ricuce, togliendo
    la frase che i blocchi adiacenti condividono per costruzione (sovrapposizione del chunking).
    """
    pieces: list[str] = []
    for chunk_id in dict.fromkeys(row.chunk_id for row in group):
        rows = [r for r in group if r.chunk_id == chunk_id]
        source = chunks[chunk_id].text
        start = source.find(rows[0].raw_text)
        last = rows[-1].raw_text
        end = source.find(last)
        if start < 0 or end < 0:
            continue
        piece = source[start : end + len(last)].strip()
        if not piece:
            continue
        if pieces:
            piece = _drop_shared_head(pieces[-1], piece)
        if piece:
            pieces.append(piece)
    text = "\n\n".join(pieces).strip()
    if not text:
        return None
    themes = _clean_themes([t for row in group for t in (row.themes or [])])
    entities: list[str] = []
    for row in group:
        for entity in row.entities or []:
            if entity not in entities:
                entities.append(entity)
    return Node(
        document_id=group[0].document_id,
        chunk_id=group[0].chunk_id,
        raw_text=text,
        summary=" ".join(row.summary for row in group if row.summary)[:2000],
        type=group[0].type,
        themes=themes[:6],
        entities=entities[:15],
        event_date=next((row.event_date for row in group if row.event_date), None),
        salience=max((row.salience or 0.0) for row in group),
    )


def _drop_shared_head(previous: str, piece: str) -> str:
    """Toglie dall'inizio di ``piece`` le frasi già presenti in fondo a ``previous``."""
    sentences = split_sentences(piece)
    while sentences and sentences[0] and sentences[0] in previous:
        piece = piece[len(piece) - len(piece.split(sentences[0], 1)[-1]) :].lstrip()
        sentences = sentences[1:]
    return piece.strip()
