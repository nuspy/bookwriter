"""Motore di authoring (§8.7): dal brief alla generazione di contenuti da zero.

- ``start_authoring`` — registra il brief (tema, pubblico, obiettivo, target parole) come **intenti**
  persistiti + `BookSpec` + una sessione, così l'opera si costruisce in più sessioni (§7.7).
- ``expand_topic`` — l'LLM **genera nodi-idea originali** su un tema; è il modo per popolare la
  memoria partendo da nulla. I nodi (``source=dialogue``) confluiscono poi in organize → compose.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session

from bookwriter.db.project_schema import Node, make_engine, set_bookspec
from bookwriter.interact.store import record_intent, start_session
from bookwriter.memory.vectors import LanceStore

IDEAS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "summary": {"type": "string"},
                    "themes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "summary", "themes"],
            },
        }
    },
    "required": ["ideas"],
}


class SupportsChatEmbed(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...

    def embed(self, texts: list[str], *, kind: str = ..., model: str | None = ...) -> list[list[float]]:
        ...


@dataclass
class BriefInfo:
    session_id: int
    theme: str


def start_authoring(
    db_path: Path,
    *,
    theme: str,
    audience: str | None = None,
    goal: str | None = None,
    target_words: int | None = None,
) -> BriefInfo:
    """Registra il brief autoriale (intenti persistiti + BookSpec) e apre una sessione."""
    session_id = start_session(db_path, mode="author")
    record_intent(db_path, "scope", {"theme": theme}, rationale="Tema dell'opera (authoring).")
    if audience:
        record_intent(db_path, "audience", {"who": audience})
    if goal:
        record_intent(db_path, "goal", {"goal": goal})
    if target_words:
        set_bookspec(db_path, target_words=target_words)
    return BriefInfo(session_id=session_id, theme=theme)


def expand_topic(
    db_path: Path,
    vectors_path: Path,
    client: SupportsChatEmbed,
    *,
    topic: str,
    n: int = 6,
    audience: str | None = None,
    goal: str | None = None,
    model: str | None = None,
) -> int:
    """L'LLM genera ``n`` nodi-idea originali sul tema e li aggiunge alla memoria (embeddati)."""
    context = f"Tema: {topic}."
    if audience:
        context += f" Pubblico: {audience}."
    if goal:
        context += f" Obiettivo: {goal}."
    result = client.chat_json(
        [
            {
                "role": "system",
                "content": (
                    f"Sei un autore. Genera esattamente {n} idee/paragrafi ORIGINALI sul tema, "
                    f"ciascuno con 'text' (contenuto sviluppato), 'summary' (una frase) e 'themes'. "
                    f"Idee distinte e non ripetitive, coerenti col pubblico e l'obiettivo."
                ),
            },
            {"role": "user", "content": context},
        ],
        schema=IDEAS_SCHEMA,
        model=model,
    )
    ideas = result.get("ideas", []) or []
    if not ideas:
        return 0

    engine = make_engine(db_path)
    node_ids: list[int] = []
    reps: list[str] = []
    with Session(engine) as ses:
        for idea in ideas:
            text = str(idea.get("text", "")).strip()
            if not text:
                continue
            themes = list(idea.get("themes", []) or [])
            node = Node(
                raw_text=text,
                summary=str(idea.get("summary", "")).strip(),
                type="idea",
                themes=themes,
                source="dialogue",
            )
            ses.add(node)
            ses.commit()
            ses.refresh(node)
            node_ids.append(node.id)
            reps.append(f"{node.summary} {' '.join(themes)} {text}".strip())

    if node_ids:
        vectors = client.embed(reps, kind="document")
        LanceStore(vectors_path).add(
            "node",
            [{"id": nid, "text": rep, "vector": vec} for nid, rep, vec in zip(node_ids, reps, vectors)],
        )
    return len(node_ids)
