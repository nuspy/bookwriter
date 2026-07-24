"""Motore di interazione / co-scrittura (§7.6): analisi lacune, domande, completamento.

- ``analyze_coverage``: fotografa la copertura tematica dei nodi (temi dominanti/sottili).
- ``propose_questions``: l'LLM genera domande mirate a colmare le lacune; sono **persistite** come
  domande aperte e come intento ``content-gap``.
- ``integrate_answer``: la risposta dell'utente diventa un **nuovo nodo** (``source=dialogue``),
  embeddato in memoria → la conoscenza cresce durante il dialogo.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.db.project_schema import Node, make_engine
from bookwriter.interact.store import add_question, answer_question, log_message, record_intent
from bookwriter.memory.vectors import LanceStore

QUESTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["questions"],
}


class SupportsChatEmbed(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...

    def embed(self, texts: list[str], *, kind: str = ..., model: str | None = ...) -> list[list[float]]:
        ...


def analyze_coverage(db_path: Path) -> dict:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        nodes = ses.exec(select(Node)).all()
    themes = Counter(t for n in nodes for t in (n.themes or []))
    thin = sorted(t for t, c in themes.items() if c <= 1)
    return {
        "node_count": len(nodes),
        "theme_counts": dict(themes.most_common()),
        "top_themes": [t for t, _ in themes.most_common(8)],
        "thin_themes": thin,
        "sample_summaries": [n.summary for n in nodes[:12] if n.summary],
    }


def propose_questions(
    db_path: Path,
    client: SupportsChatEmbed,
    *,
    model: str | None = None,
    n: int = 3,
    session_id: int | None = None,
) -> list[dict]:
    """Genera e persiste domande per definire/completare i contenuti."""
    coverage = analyze_coverage(db_path)
    context = (
        f"Temi principali: {', '.join(coverage['top_themes']) or 'nessuno'}.\n"
        f"Temi con pochi contenuti: {', '.join(coverage['thin_themes']) or 'nessuno'}.\n"
        f"Esempi di idee presenti: {' | '.join(coverage['sample_summaries'][:8])}."
    )
    result = client.chat_json(
        [
            {
                "role": "system",
                "content": (
                    f"Sei un editor che aiuta a completare un libro. In base alla copertura dei "
                    f"contenuti, proponi esattamente {n} domande mirate all'autore per colmare "
                    f"lacune o approfondire temi deboli. Domande concrete, una frase ciascuna, in italiano."
                ),
            },
            {"role": "user", "content": context},
        ],
        schema=QUESTIONS_SCHEMA,
        model=model,
    )
    questions = [q.strip() for q in result.get("questions", []) if q and q.strip()][:n]

    persisted: list[dict] = []
    for q in questions:
        qid = add_question(db_path, q, session_id=session_id)
        persisted.append({"id": qid, "text": q})
    if questions:
        record_intent(
            db_path,
            "content-gap",
            {"thin_themes": coverage["thin_themes"], "questions": questions},
            rationale="Domande generate per colmare lacune di copertura.",
        )
        log_message(db_path, session_id, "assistant", "\n".join(f"- {q}" for q in questions))
    return persisted


def integrate_answer(
    db_path: Path,
    vectors_path: Path,
    client: SupportsChatEmbed,
    question_id: int | None,
    answer_text: str,
    *,
    themes: list[str] | None = None,
    session_id: int | None = None,
) -> int:
    """Trasforma la risposta dell'utente in un nuovo nodo (source=dialogue) e lo embedda."""
    answer_text = answer_text.strip()
    summary = answer_text[:160]
    engine = make_engine(db_path)
    with Session(engine) as ses:
        node = Node(
            raw_text=answer_text,
            summary=summary,
            type="idea",
            themes=themes or [],
            source="dialogue",
        )
        ses.add(node)
        ses.commit()
        ses.refresh(node)
        node_id = node.id

    representation = f"{summary} {' '.join(themes or [])} {answer_text}".strip()
    vector = client.embed([representation], kind="document")[0]
    LanceStore(vectors_path).add("node", [{"id": node_id, "text": answer_text, "vector": vector}])

    if question_id is not None:
        answer_question(db_path, question_id, answer_text)
    log_message(db_path, session_id, "user", answer_text)
    return node_id
