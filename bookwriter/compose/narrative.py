"""Stesura narrativa (Compose → prosa, §8.1).

Per ogni capitolo dell'outline genera **prosa nuova** che intreccia i nodi (frammenti/idee) nello
stile scelto (StyleContract), rispettando un **budget di parole** ripartito dal `BookSpec`. Uno stato
di continuità (glossario, riassunto, coda) mantiene uniforme lo stile tra i capitoli. Una
**riconciliazione** avvicina ogni capitolo al suo budget. Persistente e riavviabile.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.db.project_schema import (
    ComposedChapter,
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    OutlineSection,
    make_engine,
)
from bookwriter.text_utils import split_sentences

COMPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
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
    "required": ["text", "glossary", "summary"],
}


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


@dataclass
class ComposeReport:
    outline_id: int
    chapters: int
    words: int
    target_words: int | None

    def summary(self) -> str:
        t = f"/{self.target_words}" if self.target_words else ""
        return f"outline #{self.outline_id}: capitoli={self.chapters} · parole={self.words}{t}"


def _continuity_text(continuity: dict) -> str:
    lines = []
    glossary = continuity.get("glossary") or {}
    if glossary:
        lines.append("Rese coerenti: " + "; ".join(f"{t}→{r}" for t, r in list(glossary.items())[:40]))
    if continuity.get("summary"):
        lines.append(f"Riassunto del già scritto: {continuity['summary']}")
    if continuity.get("tail"):
        lines.append(f"Ultime frasi scritte (continua senza ripeterle): {continuity['tail']}")
    return "\n".join(lines)


# Oltre questo budget un capitolo viene generato in più parti (per non superare il limite di output).
_MAX_PART_WORDS = 900


def _split_list(items: list, k: int) -> list[list]:
    n = len(items)
    k = max(1, min(k, n))
    return [items[round(i * n / k) : round((i + 1) * n / k)] for i in range(k) if items[round(i * n / k) : round((i + 1) * n / k)]]


def _generate_chapter(
    title: str,
    materials: list[str],
    budget: int | None,
    contract: str,
    continuity: dict,
    client: SupportsChatJson,
    *,
    model: str | None,
    adjust_note: str | None = None,
) -> tuple[str, dict, int]:
    """Genera un capitolo; se il budget è grande lo produce in più parti sequenziali."""
    parts = 1
    if budget and budget > _MAX_PART_WORDS and len(materials) > 1:
        parts = -(-budget // _MAX_PART_WORDS)  # ceil
    if parts <= 1:
        return _generate_part(title, materials, budget, contract, continuity, client, model=model, adjust_note=adjust_note)

    groups = _split_list(materials, parts)
    part_budget = max(1, round((budget or 0) / len(groups)))
    texts: list[str] = []
    total_words = 0
    for i, group in enumerate(groups):
        note = (
            f"Questa è la parte {i + 1} di {len(groups)} del capitolo «{title}»: prosegui il discorso "
            f"senza reintrodurre il capitolo{' e senza concluderlo' if i < len(groups) - 1 else ''}."
        )
        if adjust_note:
            note += " " + adjust_note
        text, continuity, words = _generate_part(
            title, group, part_budget, contract, continuity, client, model=model, adjust_note=note
        )
        texts.append(text)
        total_words += words
    return "\n\n".join(texts), continuity, total_words


def _generate_part(
    title: str,
    materials: list[str],
    budget: int | None,
    contract: str,
    continuity: dict,
    client: SupportsChatJson,
    *,
    model: str | None,
    adjust_note: str | None = None,
) -> tuple[str, dict, int]:
    mat = "\n".join(f"- {m}" for m in materials if m.strip())
    instruction = (
        f"Scrivi la prosa del capitolo «{title}» integrando i seguenti materiali (idee/frammenti) "
        f"nello stile descritto. Non inventare fatti non presenti nei materiali; puoi riformularli e "
        f"collegarli con transizioni."
    )
    if budget:
        instruction += f" Lunghezza: circa {budget} parole."
    if any(m.startswith("[Sezione:") for m in materials):
        instruction += " Se compaiono righe '[Sezione: X]', organizza la prosa in sotto-sezioni con intestazione Markdown '### X'."
    if adjust_note:
        instruction += " " + adjust_note
    instruction += (
        " Rispondi in JSON: 'text' (la prosa), 'glossary' (nomi/termini con la resa scelta), "
        "'summary' (riassunto cumulativo aggiornato)."
    )
    system = contract + "\n" + _continuity_text(continuity) + "\n" + instruction
    result = client.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": mat}],
        schema=COMPOSE_SCHEMA,
        model=model,
    )
    text = str(result.get("text", "")).strip()

    glossary = dict(continuity.get("glossary") or {})
    for entry in result.get("glossary", []) or []:
        term, rendering = entry.get("term"), entry.get("rendering")
        if term and rendering and term not in glossary:
            glossary[term] = rendering
    sentences = split_sentences(text)
    new_continuity = {
        "glossary": glossary,
        "summary": str(result.get("summary", continuity.get("summary", ""))).strip()[:1500],
        "tail": " ".join(sentences[-2:]) if sentences else text[-300:],
    }
    return text, new_continuity, len(text.split())


def _load_chapters(db_path: Path, outline_id: int) -> list[dict]:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        chapters = list(
            ses.exec(
                select(OutlineChapter).where(OutlineChapter.outline_id == outline_id).order_by(OutlineChapter.position)
            )
        )
        out = []
        for ch in chapters:
            sections = ses.exec(
                select(OutlineSection).where(OutlineSection.chapter_id == ch.id).order_by(OutlineSection.position)
            ).all()
            materials: list[str] = []

            def _append_items(items) -> None:
                for it in items:
                    node = ses.get(Node, it.node_id)
                    if node:
                        materials.append(node.raw_text or node.summary)

            if sections:
                for sec in sections:
                    materials.append(f"[Sezione: {sec.title}]")
                    _append_items(ses.exec(
                        select(OutlineItem).where(OutlineItem.section_id == sec.id).order_by(OutlineItem.position)
                    ))
                _append_items(ses.exec(
                    select(OutlineItem)
                    .where(OutlineItem.chapter_id == ch.id, OutlineItem.section_id.is_(None))
                    .order_by(OutlineItem.position)
                ))
            else:
                _append_items(ses.exec(
                    select(OutlineItem).where(OutlineItem.chapter_id == ch.id).order_by(OutlineItem.position)
                ))
            out.append({"id": ch.id, "position": ch.position, "title": ch.title, "materials": materials})
    return out


def compose_outline(
    db_path: Path,
    outline_id: int,
    contract: str,
    client: SupportsChatJson,
    *,
    target_words: int | None = None,
    tolerance_pct: int = 5,
    reconcile: bool = True,
    model: str | None = None,
    resume: bool = True,
    progress=None,
) -> ComposeReport:
    chapters = _load_chapters(db_path, outline_id)
    if not chapters:
        raise ValueError("Outline senza capitoli: esegui prima `organize`.")

    # Ripartizione del budget: peso = numero di materiali (nodi) del capitolo.
    weights = [max(len(c["materials"]), 1) for c in chapters]
    total_w = sum(weights)
    budgets = [round(target_words * w / total_w) if target_words else None for w in weights]

    engine = make_engine(db_path)
    if not resume:
        with Session(engine) as ses:
            for row in ses.exec(select(ComposedChapter).where(ComposedChapter.outline_id == outline_id)).all():
                ses.delete(row)
            ses.commit()

    with Session(engine) as ses:
        done_ch = {
            r.chapter_id
            for r in ses.exec(select(ComposedChapter).where(ComposedChapter.outline_id == outline_id)).all()
        }

    continuity: dict = {}
    for idx, (ch, budget) in enumerate(zip(chapters, budgets)):
        if progress:
            progress(idx, len(chapters))
        if ch["id"] in done_ch:
            continue
        text, continuity, words = _generate_chapter(
            ch["title"], ch["materials"], budget, contract, continuity, client, model=model
        )
        # Riconciliazione: se fuori tolleranza dal budget, una rigenerazione mirata.
        if reconcile and budget and abs(words - budget) > budget * tolerance_pct / 100:
            note = (
                f"La stesura precedente aveva {words} parole; servono circa {budget}: "
                + ("espandi con più dettaglio e transizioni." if words < budget else "accorcia sintetizzando.")
            )
            text, continuity, words = _generate_chapter(
                ch["title"], ch["materials"], budget, contract, continuity, client, model=model, adjust_note=note
            )
        with Session(engine) as ses:
            ses.add(
                ComposedChapter(
                    outline_id=outline_id,
                    chapter_id=ch["id"],
                    position=ch["position"],
                    title=ch["title"],
                    text=text,
                    words=words,
                )
            )
            ses.commit()

    if progress:
        progress(len(chapters), len(chapters))
    with Session(engine) as ses:
        rows = ses.exec(select(ComposedChapter).where(ComposedChapter.outline_id == outline_id)).all()
        total_words = sum(r.words for r in rows)
        n_ch = len(rows)
    return ComposeReport(outline_id=outline_id, chapters=n_ch, words=total_words, target_words=target_words)


def compose_to_markdown(db_path: Path, outline_id: int) -> str:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        outline = ses.get(Outline, outline_id)
        rows = list(
            ses.exec(
                select(ComposedChapter).where(ComposedChapter.outline_id == outline_id).order_by(ComposedChapter.position)
            )
        )
    title = (outline.name if outline else None) or "Libro"
    lines: list[str] = [f"# {title}", ""]
    for rc in rows:
        lines += [f"## {rc.title}", "", rc.text.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"
