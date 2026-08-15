"""Stesura narrativa (Compose → prosa, §8.1).

Per ogni capitolo dell'outline genera **prosa nuova** che intreccia i nodi (frammenti/idee) nello
stile scelto (StyleContract), rispettando un **budget di parole** ripartito dal `BookSpec`. Uno stato
di continuità (glossario, riassunto, coda) mantiene uniforme lo stile tra i capitoli. Una
**riconciliazione** avvicina ogni capitolo al suo budget. Persistente e riavviabile.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.config import language_label
from bookwriter.db.project_schema import (
    ComposedChapter,
    Node,
    Outline,
    OutlineChapter,
    OutlineItem,
    OutlineSection,
    ensure_columns,
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
    def chat_json(
        self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...,
        temperature: float = ..., max_tokens: int | None = ...,
    ) -> Any:
        ...


# A temperatura zero il modello, davanti a materiali simili, ripete le stesse formule capitolo
# dopo capitolo: per la prosa serve varietà.
PROSE_TEMPERATURE = 0.8


@dataclass
class ComposeReport:
    outline_id: int
    chapters: int
    words: int
    target_words: int | None
    rewritten: int = 0  # capitoli riscritti in questa esecuzione (gli altri erano già validi)

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

# Due modi opposti di comporre. «libera» lascia riscrivere: serve quando si vuole imporre uno
# stile altrui. «fedele» conserva le formulazioni dell'autore e limita l'intervento a ordinare,
# collegare e togliere le ripetizioni: è il modo per non perdere la propria voce.
FIDELITY_FREE = "libera"
FIDELITY_FAITHFUL = "fedele"

_INSTRUCTION_FREE = (
    "Non inventare fatti non presenti nei materiali; puoi riformularli e collegarli con transizioni."
)
# Ridurre conservando la voce è un lavoro editoriale: si toglie e si accorpa, non si riscrive.
_INSTRUCTION_REDUCE = (
    " I materiali contano circa {disponibili} parole e il capitolo ne deve avere circa {richieste}: "
    "va tenuto all'incirca il {quota}%. Riduci per SOTTRAZIONE, mai per parafrasi. Nell'ordine: "
    "1) togli i passaggi che ripetono un concetto già espresso altrove nel capitolo; "
    "2) accorpa i passaggi vicini che dicono la stessa cosa, tenendo la formulazione migliore "
    "dell'autore invece di fonderne due in una frase nuova; "
    "3) taglia esempi ridondanti, incisi e dettagli marginali rispetto al filo del capitolo; "
    "4) solo se ancora non basta, accorcia i passaggi meno centrali eliminando parole, non "
    "sostituendole. Quello che resta deve essere riconoscibile come scritto dall'autore."
)

_INSTRUCTION_FAITHFUL = (
    "MASSIMA ADERENZA ALL'ORIGINALE. Usa il più possibile le parole esatte dei materiali: "
    "conserva frasi, espressioni, immagini e registro così come sono. Il tuo compito non è "
    "riscrivere ma COMPORRE: ordina i materiali in un discorso continuo, uniscili con transizioni "
    "brevi e sobrie, accorpa i passaggi che dicono la stessa cosa tenendo la formulazione migliore, "
    "elimina le ripetizioni e trasforma eventuali elenchi in prosa. Non introdurre lessico, "
    "metafore o costruzioni che non siano già nei materiali. Non aggiungere commenti né conclusioni tue."
)
_MAX_RECONCILE_ATTEMPTS = 3


def estimated_parts(budget: int | None, materials: list) -> int:
    """Quante parti servono per un capitolo: è l'unità di lavoro percepibile dall'utente."""
    if budget and budget > _MAX_PART_WORDS and len(materials) > 1:
        return max(1, min(-(-budget // _MAX_PART_WORDS), len(materials)))
    return 1


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
    language: str = "",
    fidelity: str = FIDELITY_FREE,
    on_part=None,
) -> tuple[str, dict, int]:
    """Genera un capitolo; se il budget è grande lo produce in più parti sequenziali."""
    parts = 1
    if budget and budget > _MAX_PART_WORDS and len(materials) > 1:
        parts = -(-budget // _MAX_PART_WORDS)  # ceil
    if parts <= 1:
        risultato = _generate_part(
            title, materials, budget, contract, continuity, client,
            model=model, adjust_note=adjust_note, language=language, fidelity=fidelity,
        )
        if on_part:
            on_part()
        return risultato

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
            title, group, part_budget, contract, continuity, client,
            model=model, adjust_note=note, language=language, fidelity=fidelity,
        )
        texts.append(text)
        total_words += words
        if on_part:
            on_part()
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
    language: str = "",
    fidelity: str = FIDELITY_FREE,
) -> tuple[str, dict, int]:
    mat = "\n".join(f"- {m}" for m in materials if m.strip())
    fedele = fidelity == FIDELITY_FAITHFUL
    apertura = (
        f"Componi il capitolo «{title}» a partire dai seguenti materiali (passaggi dell'autore)."
        if fedele
        else f"Scrivi la prosa del capitolo «{title}» integrando i seguenti materiali "
             f"(idee/frammenti) nello stile descritto."
    )
    instruction = apertura + " " + (_INSTRUCTION_FAITHFUL if fedele else _INSTRUCTION_FREE)
    disponibili = sum(len(m.split()) for m in materials if not m.startswith("[Sezione:"))
    if fedele and budget and disponibili > budget * 1.15:
        instruction += _INSTRUCTION_REDUCE.format(
            disponibili=disponibili, richieste=budget, quota=round(100 * budget / disponibili),
        )
    if language:
        instruction += f" Scrivi il capitolo in {language_label(language)}."
    if budget:
        instruction += (
            f" Lunghezza richiesta: almeno {budget} parole. Sviluppa i materiali distendendoli, "
            "non riassumerli: se il testo risulta più corto, mancano parti del racconto."
        )
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
        temperature=PROSE_TEMPERATURE,
        # Nessun tetto ai token: sui modelli che ragionano il pensiero consuma il budget
        # prima ancora di iniziare a scrivere, e la risposta torna vuota o troncata.
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
    language: str = "",
    fidelity: str = FIDELITY_FREE,
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
    ensure_columns(engine, ComposedChapter)
    # Stile, lunghezza e lingua identificano la stesura: se cambiano, riusare i capitoli
    # già scritti darebbe lo stesso libro di prima con un nome diverso.
    signature = hashlib.sha256(
        f"{contract}|{target_words}|{tolerance_pct}|{language}|{fidelity}".encode()
    ).hexdigest()[:16]
    if not resume:
        with Session(engine) as ses:
            for row in ses.exec(select(ComposedChapter).where(ComposedChapter.outline_id == outline_id)).all():
                ses.delete(row)
            ses.commit()

    with Session(engine) as ses:
        done_ch = {
            r.chapter_id
            for r in ses.exec(select(ComposedChapter).where(ComposedChapter.outline_id == outline_id)).all()
            if r.signature == signature
        }

    # L'avanzamento si misura in parti: un capitolo da 8.000 parole ne vale dieci, e contarlo
    # come una sola unità lascerebbe la barra ferma a zero per venti minuti.
    total_parts = sum(estimated_parts(b, ch["materials"]) for ch, b in zip(chapters, budgets)) or 1
    done_parts = 0

    def _part_done() -> None:
        nonlocal done_parts, total_parts
        done_parts += 1
        # La riconciliazione aggiunge parti non prevedibili: il totale cresce invece di mentire.
        total_parts = max(total_parts, done_parts + 1)
        if progress:
            progress(done_parts, total_parts)

    continuity: dict = {}
    rewritten = 0
    for ch, budget in zip(chapters, budgets):
        if ch["id"] in done_ch:
            # Capitolo già valido per questi parametri: le sue parti sono comunque fatte.
            for _ in range(estimated_parts(budget, ch["materials"])):
                _part_done()
            continue
        text, continuity, words = _generate_chapter(
            ch["title"], ch["materials"], budget, contract, continuity, client,
            model=model, language=language, fidelity=fidelity, on_part=_part_done,
        )
        # Riconciliazione: si insiste finché il capitolo rientra nella tolleranza. Un solo
        # tentativo non basta, perché i modelli tendono sistematicamente a scrivere meno.
        for _ in range(_MAX_RECONCILE_ATTEMPTS):
            if not (reconcile and budget) or abs(words - budget) <= budget * tolerance_pct / 100:
                break
            note = (
                f"La stesura precedente aveva {words} parole; ne servono circa {budget}: "
                + (
                    "espandi con più dettaglio, scene e transizioni, senza aggiungere fatti nuovi."
                    if words < budget
                    else "accorcia sintetizzando."
                )
            )
            candidate, candidate_continuity, candidate_words = _generate_chapter(
                ch["title"], ch["materials"], budget, contract, continuity, client,
                model=model, adjust_note=note, language=language, fidelity=fidelity,
                on_part=_part_done,
            )
            # Si tiene il tentativo solo se avvicina davvero il bersaglio.
            if abs(candidate_words - budget) < abs(words - budget):
                text, continuity, words = candidate, candidate_continuity, candidate_words
        with Session(engine) as ses:
            # Sostituisce la stesura precedente dello stesso capitolo: due esecuzioni non
            # devono far comparire il capitolo due volte nel libro.
            for old in ses.exec(
                select(ComposedChapter).where(
                    ComposedChapter.outline_id == outline_id,
                    ComposedChapter.chapter_id == ch["id"],
                )
            ).all():
                ses.delete(old)
            ses.add(
                ComposedChapter(
                    outline_id=outline_id,
                    chapter_id=ch["id"],
                    position=ch["position"],
                    title=ch["title"],
                    text=text,
                    words=words,
                    signature=signature,
                )
            )
            ses.commit()
        rewritten += 1

    if progress:
        progress(total_parts, total_parts)
    with Session(engine) as ses:
        rows = ses.exec(select(ComposedChapter).where(ComposedChapter.outline_id == outline_id)).all()
        total_words = sum(r.words for r in rows)
        n_ch = len(rows)
    return ComposeReport(
        outline_id=outline_id, chapters=n_ch, words=total_words,
        target_words=target_words, rewritten=rewritten,
    )


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
