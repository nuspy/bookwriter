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
from bookwriter.db.project_schema import Chunk, Node, make_engine
from bookwriter.text_utils import split_sentences

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


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


@dataclass
class AtomizeReport:
    chunks_done: int
    chunks_skipped: int
    nodes_created: int

    def summary(self) -> str:
        return f"chunk atomizzati={self.chunks_done} · saltati={self.chunks_skipped} · nodi={self.nodes_created}"


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


def atomize_chunks(
    db_path: Path,
    client: SupportsChatJson,
    *,
    model: str | None = None,
    concurrency: int = 4,
    progress=None,
) -> AtomizeReport:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        chunks = ses.exec(select(Chunk)).all()
        already = _atomized_chunk_ids(ses)

    todo = [c for c in chunks if c.id not in already]

    def _call(chunk: Chunk):
        try:
            return client.chat_json(
                [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": chunk.text}],
                schema=NODES_SCHEMA,
                model=model,
            )
        except Exception:  # noqa: BLE001 - chunk fallito: verrà ritentato alla prossima esecuzione
            return None

    nodes_created = 0
    succeeded = 0
    done = 0
    # Le chiamate LLM girano in parallelo; le scritture su DB restano nel thread principale.
    for chunk, result in imap_unordered(_call, todo, concurrency):
        done += 1
        if progress:
            progress(done, len(todo))
        if not result:
            continue
        rows = [
            Node(
                document_id=chunk.document_id,
                chunk_id=chunk.id,
                raw_text=_snap_verbatim(chunk.text, str(nd.get("excerpt", ""))),
                summary=str(nd.get("summary", "")),
                type=nd.get("type", "idea") if nd.get("type") in _NODE_TYPES else "idea",
                themes=list(nd.get("themes", []) or []),
                entities=list(nd.get("entities", []) or []),
                event_date=nd.get("event_date"),
                salience=float(nd.get("salience", 0.5) or 0.5),
            )
            for nd in result.get("nodes", [])
        ]
        if rows:
            with Session(engine) as ses:
                ses.add_all(rows)
                ses.commit()
            nodes_created += len(rows)
        succeeded += 1
    return AtomizeReport(chunks_done=succeeded, chunks_skipped=len(already), nodes_created=nodes_created)
