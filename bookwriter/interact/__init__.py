"""Interazione e co-scrittura: persistenza di sessioni/intenti e motore di completamento."""

from bookwriter.interact.engine import analyze_coverage, integrate_answer, propose_questions
from bookwriter.interact.store import (
    add_question,
    answer_question,
    build_brief,
    format_brief,
    list_intents,
    list_open_questions,
    log_message,
    record_intent,
    start_session,
)

__all__ = [
    "add_question",
    "analyze_coverage",
    "answer_question",
    "build_brief",
    "format_brief",
    "integrate_answer",
    "list_intents",
    "list_open_questions",
    "log_message",
    "propose_questions",
    "record_intent",
    "start_session",
]
