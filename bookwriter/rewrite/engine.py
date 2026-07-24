"""Motore di riscrittura deterministico (§8.5).

Legge i chunk **in ordine assoluto** (``book_pos``) e riscrive ciascuno mantenendo il contenuto ma
adottando uno stile (contratto). L'uniformità è garantita dal **ContinuityState** che scorre di
chunk in chunk: glossario coerente (nomi/termini), riassunto progressivo, coda di contesto. Lo stato
è persistito sulla run → ripartenza deterministica. Serve sia a Rewrite (B) sia a Translate (C).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    Chunk,
    Document,
    RewriteChunk,
    RewriteRun,
    make_engine,
)
from bookwriter.text_utils import split_sentences

REWRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rewritten": {"type": "string"},
        "glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"term": {"type": "string"}, "rendering": {"type": "string"}},
                "required": ["term", "rendering"],
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["rewritten", "glossary", "summary"],
}

_CRITIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "conforms": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["conforms", "issues"],
}


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


@dataclass
class RewriteReport:
    run_id: int
    processed: int
    total: int
    status: str

    def summary(self) -> str:
        return f"run #{self.run_id}: elaborati {self.processed}/{self.total} chunk ({self.status})"


def _continuity_text(continuity: dict) -> str:
    lines = []
    glossary = continuity.get("glossary") or {}
    if glossary:
        items = "; ".join(f"{t}→{r}" for t, r in list(glossary.items())[:40])
        lines.append(f"Rese coerenti da mantenere: {items}")
    if continuity.get("summary"):
        lines.append(f"Riassunto del già elaborato: {continuity['summary']}")
    if continuity.get("tail"):
        lines.append(f"Ultime frasi elaborate (continua senza ripeterle): {continuity['tail']}")
    return "\n".join(lines)


def _instruction(mode: str, target_language: str | None, ratio: float | None) -> str:
    if mode == "translate":
        base = f"Traduci il testo in {target_language}, mantenendo lo stile descritto e il contenuto."
    else:
        base = "Riscrivi il testo nello stile descritto, mantenendo fedele il CONTENUTO (nessuna informazione aggiunta o tolta)."
    base += " Usa il glossario per la coerenza di nomi e termini. Assicura continuità con le ultime frasi."
    if ratio:
        base += f" Punta a circa {round(ratio * 100)}% della lunghezza originale."
    base += (
        " Rispondi in JSON: 'rewritten' (il testo), 'glossary' (nuovi nomi/termini con la resa scelta), "
        "'summary' (riassunto cumulativo aggiornato, incluso questo brano)."
    )
    return base


def _style_conforms(contract: str, text: str, target_language: str | None, client: SupportsChatJson, model: str | None) -> tuple[bool, list[str]]:
    lang = f" Deve essere scritto in {target_language}." if target_language else ""
    verdict = client.chat_json(
        [
            {"role": "system", "content": f"Verifica se il TESTO rispetta il contratto di stile.{lang} Elenca gli scostamenti concreti."},
            {"role": "user", "content": contract + "\n\nTESTO:\n" + text},
        ],
        schema=_CRITIC_SCHEMA,
        model=model,
    )
    return bool(verdict.get("conforms", True)), list(verdict.get("issues", []) or [])


def _rewrite_one(
    chunk_text: str,
    contract: str,
    continuity: dict,
    client: SupportsChatJson,
    *,
    mode: str,
    target_language: str | None,
    ratio: float | None,
    model: str | None,
    critic: bool = True,
    critic_model: str | None = None,
) -> tuple[str, dict]:
    system = contract + "\n" + _continuity_text(continuity) + "\n" + _instruction(mode, target_language, ratio)
    result = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": chunk_text}],
        schema=REWRITE_SCHEMA,
        model=model,
    )
    rewritten = str(result.get("rewritten", "")).strip()

    # Critic loop: una rigenerazione mirata se il testo non rispetta lo stile.
    if critic and rewritten:
        conforms, issues = _style_conforms(contract, rewritten, target_language, client, critic_model or model)
        if not conforms and issues:
            fix = " Correggi questi scostamenti dallo stile: " + "; ".join(issues)
            result = client.chat_json(
                [{"role": "system", "content": system + fix}, {"role": "user", "content": chunk_text}],
                schema=REWRITE_SCHEMA,
                model=model,
            )
            rewritten = str(result.get("rewritten", "")).strip()

    # Aggiorna la continuità.
    glossary = dict(continuity.get("glossary") or {})
    for entry in result.get("glossary", []) or []:
        term, rendering = entry.get("term"), entry.get("rendering")
        if term and rendering and term not in glossary:  # prima resa vince → coerenza
            glossary[term] = rendering
    sentences = split_sentences(rewritten)
    tail = " ".join(sentences[-2:]) if sentences else rewritten[-300:]
    new_continuity = {
        "glossary": glossary,
        "summary": str(result.get("summary", continuity.get("summary", ""))).strip()[:1500],
        "tail": tail,
    }
    return rewritten, new_continuity


def _first_chapter(chunks: list[Chunk]) -> list[Chunk]:
    if not chunks:
        return []
    first_ref = chunks[0].chapter_ref
    if first_ref is None:
        return chunks[:2]
    out = []
    for c in chunks:
        if c.chapter_ref == first_ref:
            out.append(c)
        elif out:
            break
    return out


def rewrite_document(
    db_path: Path,
    document_id: int,
    contract: str,
    client: SupportsChatJson,
    *,
    style_profile_id: int | None = None,
    mode: str = "rewrite",
    target_language: str | None = None,
    ratio: float | None = None,
    kind: str = "full",
    model: str | None = None,
    critic: bool = True,
    critic_model: str | None = None,
    resume: bool = True,
    progress=None,
) -> RewriteReport:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        chunks = list(
            ses.exec(select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.book_pos))
        )
    if not chunks:
        raise ValueError("Documento senza chunk: importalo prima.")

    targets = _first_chapter(chunks) if kind == "preview" else chunks

    # Recupera o crea la run.
    with Session(engine) as ses:
        run = None
        if resume and kind == "full":
            run = ses.exec(
                select(RewriteRun)
                .where(RewriteRun.document_id == document_id)
                .where(RewriteRun.kind == "full")
                .where(RewriteRun.mode == mode)
                .where(RewriteRun.status == "running")
                .order_by(RewriteRun.id.desc())
            ).first()
        if run is None:
            run = RewriteRun(
                document_id=document_id,
                style_profile_id=style_profile_id,
                kind=kind,
                mode=mode,
                target_language=target_language,
                ratio=ratio,
                status="running",
                continuity={},
            )
            ses.add(run)
            ses.commit()
            ses.refresh(run)
        run_id = run.id
        continuity = dict(run.continuity or {})
        done_ids = {
            rc.chunk_id
            for rc in ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id)).all()
        }

    todo = [c for c in targets if c.id not in done_ids]
    for idx, chunk in enumerate(todo):
        if progress:
            progress(idx, len(todo))
        rewritten, continuity = _rewrite_one(
            chunk.text, contract, continuity, client,
            mode=mode, target_language=target_language, ratio=ratio, model=model,
            critic=critic, critic_model=critic_model,
        )
        with Session(engine) as ses:
            ses.add(
                RewriteChunk(
                    run_id=run_id,
                    chunk_id=chunk.id,
                    book_pos=chunk.book_pos,
                    chapter_ref=chunk.chapter_ref,
                    text=rewritten,
                )
            )
            db_run = ses.get(RewriteRun, run_id)
            db_run.continuity = continuity
            ses.add(db_run)
            ses.commit()

    processed = len(done_ids) + len(todo)
    status = "done" if (kind == "full" and processed >= len(targets)) else "running"
    with Session(engine) as ses:
        db_run = ses.get(RewriteRun, run_id)
        db_run.status = status
        ses.add(db_run)
        ses.commit()
    if progress:
        progress(len(todo), len(todo))
    return RewriteReport(run_id=run_id, processed=processed, total=len(targets), status=status)


def assemble_markdown(db_path: Path, run_id: int) -> str:
    """Ricompone il testo elaborato per ``book_pos``.

    Non re-inietta i titoli di capitolo: l'intestazione è già contenuta nel testo del primo chunk
    di ogni capitolo (ed è quindi resa nella lingua/stile di destinazione), evitando duplicati.
    """
    engine = make_engine(db_path)
    with Session(engine) as ses:
        run = ses.get(RewriteRun, run_id)
        doc = ses.get(Document, run.document_id) if run else None
        rows = list(
            ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id).order_by(RewriteChunk.book_pos))
        )
    title = (doc.title if doc else None) or "Libro"
    lines: list[str] = [f"# {title}", ""]
    for rc in rows:
        if rc.text.strip():
            lines += [rc.text.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def preview_pairs(db_path: Path, run_id: int) -> list[dict]:
    """Coppie originale↔riscritto per la preview del primo capitolo."""
    engine = make_engine(db_path)
    with Session(engine) as ses:
        rows = list(
            ses.exec(select(RewriteChunk).where(RewriteChunk.run_id == run_id).order_by(RewriteChunk.book_pos))
        )
        pairs = []
        for rc in rows:
            original = ses.get(Chunk, rc.chunk_id)
            pairs.append(
                {
                    "chapter": rc.chapter_ref,
                    "original": original.text if original else "",
                    "rewritten": rc.text,
                }
            )
    return pairs
