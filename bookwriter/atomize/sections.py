"""Segmentazione in **sezioni** e loro etichettatura.

Rovescia l'impostazione dell'atomizzazione: il modello non produce più il testo, lo **descrive**.

- Il taglio è **deterministico**: si uniscono paragrafi consecutivi del testo originale fino a
  raggiungere una lunghezza obiettivo, senza mai spezzare un paragrafo. Ne segue, per costruzione,
  copertura totale, nessuna sovrapposizione, nessuna frase persa e fedeltà letterale garantita.
- L'LLM riceve una sezione e restituisce solo **titolo, sintesi, temi, entità**: poche decine di
  token invece di migliaia. Non può inventare, riscrivere né frammentare il testo.

Il risultato è una riga ``Node`` per sezione, la stessa entità usata dal resto del programma.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.concurrency import imap_unordered
from bookwriter.config import language_label
from bookwriter.db.project_schema import Chunk, Node, make_engine
from bookwriter.text_utils import has_prose, split_sentences

LABEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "description": "Titolo breve della sezione (max 8 parole)."},
        "summary": {"type": "string", "description": "Di cosa parla la sezione, in 1-2 frasi."},
        "themes": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
        "event_date": {"type": ["string", "null"], "description": "ISO se deducibile, altrimenti null."},
        "salience": {"type": "number", "description": "Rilevanza 0..1 per il libro."},
    },
    "required": ["title", "summary", "themes", "entities", "event_date", "salience"],
}

_SYSTEM = (
    "Ricevi una sezione di un testo. NON riscriverla, NON citarla e NON elencarne le frasi: "
    "il testo resta com'è. Il tuo compito è solo descriverla, per poterla ritrovare e collocare "
    "in un libro. Rispondi con: un titolo breve, una sintesi di 1-2 frasi, i temi trattati, le "
    "persone e i luoghi nominati, una data ISO se il testo ne indica una (altrimenti null) e una "
    "rilevanza fra 0 e 1. I temi sono etichette brevi (1-2 parole) in minuscolo, generali e "
    "RIPETIBILI: riusa la stessa etichetta ogni volta che torna l'argomento. Massimo 4 per sezione."
)


def build_label_prompt(language: str = "") -> str:
    """Istruzioni di sistema; ``language`` fissa la lingua di titolo, sintesi e temi."""
    if language:
        return _SYSTEM + (
            f" Scrivi titolo, sintesi, temi ed entità in {language_label(language)}, sempre e solo "
            "in questa lingua, anche se il testo è in un'altra."
        )
    return _SYSTEM + " Scrivi titolo, sintesi, temi ed entità nella stessa lingua del testo."


class SupportsChatJson(Protocol):
    def chat_json(
        self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...,
        reasoning_effort: str = ...,
    ) -> Any:
        ...


@dataclass
class SectionReport:
    sections: int
    labelled: int
    failed: int = 0
    last_error: str = ""
    coverage_pct: int = 100  # per costruzione: il taglio non scarta testo

    def summary(self) -> str:
        base = f"sezioni={self.sections} · descritte={self.labelled} · copertura={self.coverage_pct}%"
        if self.failed:
            base += f" · non descritte={self.failed}"
            if self.last_error:
                base += f" ({self.last_error})"
        return base


def split_into_sections(text: str, target_words: int = 400) -> list[str]:
    """Unisce paragrafi consecutivi fino a ``target_words``, senza mai spezzarne uno.

    I paragrafi sono i blocchi separati da righe vuote; se il testo non ne ha, si ripiega sulle
    righe e infine sul testo intero. Il risultato ricopre l'originale senza buchi né doppioni.
    """
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if len(blocks) <= 1:
        blocks = [b.strip() for b in text.splitlines() if b.strip()] or ([text.strip()] if text.strip() else [])
    sections: list[str] = []
    current: list[str] = []
    words = 0
    for block in blocks:
        current.append(block)
        words += len(block.split())
        if words >= target_words:
            sections.append("\n\n".join(current))
            current, words = [], 0
    if current:
        # Una coda molto breve si attacca alla sezione precedente invece di restare orfana.
        tail = "\n\n".join(current)
        if sections and words < target_words // 3:
            sections[-1] += "\n\n" + tail
        else:
            sections.append(tail)
    return sections


def _dedupe_overlap(previous_text: str, text: str) -> str:
    """Toglie l'inizio già presente in fondo al blocco precedente (sovrapposizione del chunking)."""
    sentences = split_sentences(text)
    removed = 0
    while sentences and sentences[0] and sentences[0] in previous_text:
        removed += len(sentences[0])
        sentences = sentences[1:]
    return text[removed:].lstrip() if removed else text


def plan_sections(db_path: Path, target_words: int = 400) -> list[tuple[Chunk, str]]:
    """Sezioni previste per l'intero progetto, in ordine di lettura. Non tocca il database.

    Il taglio avviene dentro al blocco, ma le code troppo corte vengono riassorbite dalla sezione
    precedente **dello stesso documento**: il blocco è un confine tecnico, non un fine discorso.
    """
    engine = make_engine(db_path)
    with Session(engine) as ses:
        chunks = list(ses.exec(select(Chunk).order_by(Chunk.document_id, Chunk.book_pos)))

    planned: list[tuple[Chunk, str]] = []
    previous_doc, previous_text = None, ""
    # Soglia sotto la quale una sezione è troppo corta per stare da sola; mai oltre l'obiettivo,
    # altrimenti con obiettivi piccoli finirebbe per riassorbire tutto in un'unica sezione.
    minimum = min(max(target_words // 2, 60), target_words)
    for chunk in chunks:
        text = chunk.text if chunk.document_id != previous_doc else _dedupe_overlap(previous_text, chunk.text)
        previous_doc, previous_text = chunk.document_id, chunk.text
        for section in split_into_sections(text, target_words):
            attachable = (
                planned
                and planned[-1][0].document_id == chunk.document_id
                and len(section.split()) < minimum
            )
            if attachable:
                head, body = planned[-1]
                planned[-1] = (head, body + "\n\n" + section)
            else:
                planned.append((chunk, section))
    return planned


def build_sections(
    db_path: Path,
    client: SupportsChatJson,
    *,
    model: str | None = None,
    language: str = "",
    target_words: int = 400,
    concurrency: int = 4,
    progress=None,
) -> SectionReport:
    """Crea le sezioni e le fa descrivere dal modello. Salta i blocchi già lavorati."""
    engine = make_engine(db_path)
    with Session(engine) as ses:
        already = {r for r in ses.exec(select(Node.chunk_id)).all() if r is not None}
    todo = [
        (chunk, text)
        for chunk, text in plan_sections(db_path, target_words)
        # Una sezione senza prosa (indice, colophon, pagine di numeri) non ha niente da farsi
        # descrivere: la chiamata non produrrebbe nulla e sui punti di guida manda in loop.
        if chunk.id not in already and has_prose(text)
    ]
    system = build_label_prompt(language)
    errors: list[str] = []

    def _describe(item: tuple[Chunk, str]):
        try:
            return client.chat_json(
                [{"role": "system", "content": system}, {"role": "user", "content": item[1]}],
                schema=LABEL_SCHEMA,
                model=model,
                # Ragionamento pieno: da queste etichette nascono i raggruppamenti e i titoli
                # dei capitoli, cioè la struttura del libro. Non è un compito meccanico.
            )
        except Exception as exc:  # noqa: BLE001 - sezione fallita: si ritenta alla prossima esecuzione
            errors.append(str(exc)[:200])
            return None

    labelled = failed = done = 0
    for (chunk, text), label in imap_unordered(_describe, todo, concurrency):
        done += 1
        if progress:
            progress(done, len(todo))
        if not label:
            failed += 1
            continue
        themes = []
        for value in label.get("themes") or []:
            theme = str(value).strip().lower()
            if theme and theme not in themes:
                themes.append(theme)
        with Session(engine) as ses:
            ses.add(
                Node(
                    document_id=chunk.document_id,
                    chunk_id=chunk.id,
                    raw_text=text,
                    summary=f"{str(label.get('title', '')).strip()} — {str(label.get('summary', '')).strip()}".strip(" —"),
                    type="argomento",
                    themes=themes[:4],
                    entities=[str(e).strip() for e in (label.get("entities") or [])][:12],
                    event_date=label.get("event_date"),
                    salience=float(label.get("salience", 0.6) or 0.6),
                )
            )
            ses.commit()
        labelled += 1
    return SectionReport(
        sections=len(todo),
        labelled=labelled,
        failed=failed,
        last_error=errors[-1] if errors else "",
    )
