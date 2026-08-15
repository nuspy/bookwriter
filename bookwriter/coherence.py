"""Passaggio globale di coerenza: uniforma nomi/termini su tutto il libro (§8.1).

L'LLM individua le **varianti incoerenti** dello stesso referente (nomi propri, termini) e la loro
forma canonica; l'applicazione è poi **deterministica** (find/replace a confine di parola), quindi
affidabile ed economica. Opera sui capitoli composti (Compose) o sui chunk riscritti (Rewrite/Translate).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session, select

from bookwriter.db.project_schema import ComposedChapter, RewriteChunk, make_engine

COHERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "canonical": {"type": "string"},
                    "variants": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["canonical", "variants"],
            },
        },
        "timeline_issues": {"type": "array", "items": {"type": "string"}},
        "repetitions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["mappings", "timeline_issues", "repetitions"],
}


class SupportsChatJson(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> Any:
        ...


_SYSTEM = (
    "Analizza la coerenza del testo di un libro. Restituisci: 'mappings' (nomi propri/termini "
    "resi in modo INCOERENTE: 'canonical' + 'variants' da uniformare, solo vere varianti dello "
    "stesso referente), 'timeline_issues' (incoerenze cronologiche o di continuità), "
    "'repetitions' (concetti/frasi ripetuti in modo ridondante)."
)

# Ampiezza della finestra di lettura. Sta larga dentro il contesto dei modelli in uso e tiene
# insieme abbastanza pagine da far emergere le varianti di uno stesso nome.
_WINDOW_CHARS = 60_000


def _windows(text: str, size: int) -> list[str]:
    """Divide il testo in finestre di lettura, tagliando su un a capo per non spezzare le frasi."""
    if len(text) <= size:
        return [text] if text.strip() else []
    parti: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            taglio = text.rfind("\n", start + size // 2, end)
            if taglio > start:
                end = taglio
        pezzo = text[start:end]
        if pezzo.strip():
            parti.append(pezzo)
        start = end
    return parti


def analyze_coherence(text: str, client: SupportsChatJson, *, model: str | None = None) -> dict:
    """Analizza la coerenza del libro: mappa nomi/termini + problemi di timeline + ripetizioni.

    Il testo viene percorso **tutto**, a finestre. Analizzarne solo l'inizio (com'era prima:
    i primi 8.000 caratteri, cioè un paio di pagine) e poi applicare le sostituzioni trovate
    all'intero volume significa uniformare il libro sulla base dell'uno per cento del libro:
    le incoerenze del resto restano, e le sostituzioni decise su un campione così stretto
    finiscono comunque su ogni pagina.
    """
    mapping: dict[str, str] = {}
    timeline: list[str] = []
    repetitions: list[str] = []
    for finestra in _windows(text, _WINDOW_CHARS):
        result = client.chat_json(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": finestra}],
            schema=COHERENCE_SCHEMA,
            model=model,
        )
        for m in result.get("mappings", []):
            canonical = str(m.get("canonical", "")).strip()
            if not canonical:
                continue
            for variant in m.get("variants", []) or []:
                variant = str(variant).strip()
                # La prima resa vince, come per il glossario: due finestre che scelgono forme
                # canoniche diverse per lo stesso nome si annullerebbero a vicenda.
                if variant and variant != canonical and variant not in mapping:
                    mapping[variant] = canonical
        for issue in result.get("timeline_issues") or []:
            if str(issue) not in timeline:
                timeline.append(str(issue))
        for rep in result.get("repetitions") or []:
            if str(rep) not in repetitions:
                repetitions.append(str(rep))
    return {"mappings": mapping, "timeline_issues": timeline, "repetitions": repetitions}


def extract_canonical_map(text: str, client: SupportsChatJson, *, model: str | None = None) -> dict[str, str]:
    """Ritorna solo la mappa ``variante -> forma canonica`` (comodità)."""
    return analyze_coherence(text, client, model=model)["mappings"]


def apply_canonical_map(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Sostituisce le varianti con la forma canonica a confine di parola. Ritorna (testo, n_sostituzioni)."""
    count = 0
    for variant, canonical in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        text, n = re.subn(rf"\b{re.escape(variant)}\b", canonical, text)
        count += n
    return text, count


def _coherence_over(db_path: Path, model_cls, ids_texts: list[tuple[int, str]], client: SupportsChatJson, model: str | None) -> dict:
    if not ids_texts:
        return {"mappings": {}, "replacements": 0, "timeline_issues": [], "repetitions": []}
    full_text = "\n\n".join(t for _, t in ids_texts)
    analysis = analyze_coherence(full_text, client, model=model)
    mapping = analysis["mappings"]
    total = 0
    if mapping:
        engine = make_engine(db_path)
        with Session(engine) as ses:
            for rid, text in ids_texts:
                new_text, n = apply_canonical_map(text, mapping)
                if n:
                    obj = ses.get(model_cls, rid)
                    obj.text = new_text
                    if hasattr(obj, "words"):
                        obj.words = len(new_text.split())
                    ses.add(obj)
                    total += n
            ses.commit()
    return {
        "mappings": mapping,
        "replacements": total,
        "timeline_issues": analysis["timeline_issues"],
        "repetitions": analysis["repetitions"],
    }


def coherence_pass_compose(db_path: Path, outline_id: int, client: SupportsChatJson, *, model: str | None = None) -> dict:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        rows = ses.exec(
            select(ComposedChapter).where(ComposedChapter.outline_id == outline_id).order_by(ComposedChapter.position)
        ).all()
        ids_texts = [(r.id, r.text) for r in rows]
    return _coherence_over(db_path, ComposedChapter, ids_texts, client, model)


def coherence_pass_rewrite(db_path: Path, run_id: int, client: SupportsChatJson, *, model: str | None = None) -> dict:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        rows = ses.exec(
            select(RewriteChunk).where(RewriteChunk.run_id == run_id).order_by(RewriteChunk.book_pos)
        ).all()
        ids_texts = [(r.id, r.text) for r in rows]
    return _coherence_over(db_path, RewriteChunk, ids_texts, client, model)
