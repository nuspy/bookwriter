"""Persistenza di sessioni, messaggi, intenti e domande aperte (§7.7).

Tutto è salvato **nel progetto** (``project.db``) per garantire continuità tra sessioni di lavoro
diverse. ``build_brief`` ricostruisce il "brief di ripresa" da iniettare all'avvio di una sessione.
Le funzioni ritornano id o dict (mai istanze ORM staccate).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, func, select

from bookwriter.db.project_schema import (
    BookSpec,
    Intent,
    Message,
    Node,
    OpenQuestion,
    Outline,
    OutlineChapter,
    WorkSession,
    make_engine,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- sessioni e messaggi ---------------------------------------------------
def start_session(db_path: Path, mode: str = "compose") -> int:
    with Session(make_engine(db_path)) as ses:
        row = WorkSession(mode=mode)
        ses.add(row)
        ses.commit()
        ses.refresh(row)
        return row.id


def end_session(db_path: Path, session_id: int) -> None:
    with Session(make_engine(db_path)) as ses:
        row = ses.get(WorkSession, session_id)
        if row is not None:
            row.ended_at = _now()
            ses.add(row)
            ses.commit()


def log_message(db_path: Path, session_id: int | None, role: str, content: str) -> int:
    with Session(make_engine(db_path)) as ses:
        row = Message(session_id=session_id, role=role, content=content)
        ses.add(row)
        ses.commit()
        ses.refresh(row)
        return row.id


def recent_messages(db_path: Path, limit: int = 10) -> list[dict]:
    with Session(make_engine(db_path)) as ses:
        rows = ses.exec(select(Message).order_by(Message.id.desc()).limit(limit)).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


# --- intenti / decisioni ---------------------------------------------------
def record_intent(
    db_path: Path,
    kind: str,
    value: dict,
    *,
    rationale: str | None = None,
    status: str = "open",
) -> int:
    with Session(make_engine(db_path)) as ses:
        row = Intent(kind=kind, value=value, rationale=rationale, status=status)
        ses.add(row)
        ses.commit()
        ses.refresh(row)
        return row.id


def list_intents(db_path: Path, status: str | None = None) -> list[dict]:
    with Session(make_engine(db_path)) as ses:
        stmt = select(Intent)
        if status:
            stmt = stmt.where(Intent.status == status)
        rows = ses.exec(stmt.order_by(Intent.id)).all()
    return [
        {"id": r.id, "kind": r.kind, "value": r.value, "status": r.status, "rationale": r.rationale}
        for r in rows
    ]


def set_intent_status(db_path: Path, intent_id: int, status: str) -> None:
    with Session(make_engine(db_path)) as ses:
        row = ses.get(Intent, intent_id)
        if row is not None:
            row.status = status
            row.updated_at = _now()
            ses.add(row)
            ses.commit()


# --- domande aperte --------------------------------------------------------
def add_question(db_path: Path, text: str, session_id: int | None = None) -> int:
    with Session(make_engine(db_path)) as ses:
        row = OpenQuestion(text=text, created_from_session=session_id)
        ses.add(row)
        ses.commit()
        ses.refresh(row)
        return row.id


def answer_question(db_path: Path, question_id: int, answer: str) -> None:
    with Session(make_engine(db_path)) as ses:
        row = ses.get(OpenQuestion, question_id)
        if row is not None:
            row.answer = answer
            row.status = "answered"
            ses.add(row)
            ses.commit()


def list_open_questions(db_path: Path) -> list[dict]:
    with Session(make_engine(db_path)) as ses:
        rows = ses.exec(
            select(OpenQuestion).where(OpenQuestion.status == "open").order_by(OpenQuestion.id)
        ).all()
    return [{"id": r.id, "text": r.text} for r in rows]


# --- brief di ripresa ------------------------------------------------------
def build_brief(db_path: Path) -> dict:
    """Ricostruisce lo stato del progetto per riprendere il lavoro in una nuova sessione."""
    with Session(make_engine(db_path)) as ses:
        spec = ses.get(BookSpec, 1)
        outline = ses.exec(select(Outline).order_by(Outline.id.desc())).first()
        outline_info = None
        if outline is not None:
            n_ch = ses.exec(
                select(func.count()).select_from(OutlineChapter).where(OutlineChapter.outline_id == outline.id)
            ).one()
            outline_info = {"name": outline.name, "ordering": outline.ordering, "chapters": int(n_ch)}
        node_count = ses.exec(select(func.count()).select_from(Node)).one()

    return {
        "target_words": spec.target_words if spec else None,
        "outline": outline_info,
        "node_count": int(node_count),
        "open_intents": list_intents(db_path, status="open"),
        "open_questions": list_open_questions(db_path),
        "recent_messages": recent_messages(db_path, limit=6),
    }


def format_brief(brief: dict) -> str:
    """Rende il brief come testo compatto (per la GUI/CLI e da iniettare come contesto LLM)."""
    lines = ["# Brief di ripresa"]
    lines.append(f"- Target parole: {brief.get('target_words')}")
    outline = brief.get("outline")
    lines.append(
        f"- Outline: {outline['name']} ({outline['ordering']}, {outline['chapters']} capitoli)"
        if outline else "- Outline: nessuno"
    )
    lines.append(f"- Nodi in memoria: {brief.get('node_count')}")
    intents = brief.get("open_intents") or []
    lines.append(f"- Intenti aperti ({len(intents)}):")
    for it in intents:
        lines.append(f"    · [{it['kind']}] {it['value']}")
    questions = brief.get("open_questions") or []
    lines.append(f"- Domande aperte ({len(questions)}):")
    for q in questions:
        lines.append(f"    · (#{q['id']}) {q['text']}")
    return "\n".join(lines)
