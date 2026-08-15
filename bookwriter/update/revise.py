"""Aggiornamento di una bozza con il materiale del progetto.

Data una bozza già scritta, si confronta il suo contenuto con le sezioni disponibili nel progetto
e si propone una **revisione** fatta di quattro operazioni:

- ``keep``    il paragrafo resta com'è;
- ``add``     una sezione del progetto non è rappresentata nella bozza: va aggiunta;
- ``remove``  un paragrafo ripete qualcosa già detto altrove nella bozza: va tolto;
- ``replace`` un paragrafo e una sezione dicono la stessa cosa in modo parziale: si fondono
  (il vecchio resta segnato come sostituito, il nuovo lo rimpiazza).

L'appaiamento è per **somiglianza semantica** (embeddings), non per parole in comune, così
riconosce lo stesso contenuto anche se formulato diversamente. La fusione dei testi, quando
serve, è l'unico passaggio affidato al modello.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
from sqlmodel import Session, select

from bookwriter.db.project_schema import Node, make_engine

Operation = Literal["keep", "add", "remove", "replace"]

# Soglie sulla similarità coseno fra un paragrafo della bozza e una sezione del progetto.
_COVERED = 0.80  # oltre: la bozza già contiene quel contenuto
_PARTIAL = 0.62  # fra le due: contenuto affine ma incompleto → si integra
_RELATED = 0.35  # sotto: il contenuto non appartiene a nessun capitolo della bozza
_DUPLICATE = 0.90  # fra due paragrafi della bozza: ripetizione

NEW_CHAPTER = "Contenuti nuovi da collocare"


@dataclass
class Change:
    """Una modifica proposta su un punto preciso della bozza."""

    operation: Operation
    chapter: str
    text: str  # testo risultante (per 'remove' è il testo tolto)
    previous: str = ""  # testo sostituito (solo per 'replace')
    node_id: int | None = None
    reason: str = ""


@dataclass
class DraftChapter:
    title: str
    paragraphs: list[str] = field(default_factory=list)


@dataclass
class RevisionReport:
    changes: list[Change]
    merges_failed: int = 0  # integrazioni non riuscite: il contenuto è rimasto separato

    def counts(self) -> dict[str, int]:
        out = {"keep": 0, "add": 0, "remove": 0, "replace": 0}
        for change in self.changes:
            out[change.operation] += 1
        return out

    def summary(self) -> str:
        c = self.counts()
        base = (
            f"invariati={c['keep']} · aggiunti={c['add']} · tolti={c['remove']} "
            f"· integrati={c['replace']}"
        )
        if self.merges_failed:
            # Senza questa riga un modello irraggiungibile darebbe un risultato plausibile e
            # tutto sbagliato: contenuti accodati invece che fusi, e nessuno se ne accorge.
            base += f" · integrazioni non riuscite={self.merges_failed} (contenuto accodato a parte)"
        return base


class SupportsEmbedChat(Protocol):
    def embed(self, texts: list[str], *, kind: str = ..., model: str | None = ...) -> list[list[float]]:
        ...

    def chat_json(self, messages: list[dict[str, str]], *, schema: dict, model: str | None = ...) -> object:
        ...


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def split_draft(text: str) -> list[DraftChapter]:
    """Divide la bozza in capitoli usando i titoli Markdown; senza titoli, un capitolo solo."""
    chapters: list[DraftChapter] = []
    current = DraftChapter(title="")
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        heading = _HEADING.match(block.splitlines()[0]) if block.startswith("#") else None
        if heading:
            if current.paragraphs or current.title:
                chapters.append(current)
            current = DraftChapter(title=heading.group(2))
            resto = "\n".join(block.splitlines()[1:]).strip()
            if resto:
                current.paragraphs.append(resto)
            continue
        current.paragraphs.append(block)
    if current.paragraphs or current.title:
        chapters.append(current)
    return chapters or [DraftChapter(title="", paragraphs=[])]


def _normalize(vectors: list[list[float]]) -> np.ndarray:
    matrix = np.asarray(vectors, dtype=float)
    if matrix.size == 0:
        return matrix
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


MERGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}

_MERGE_SYSTEM = (
    "Ricevi un paragrafo di una bozza e un passaggio dell'autore che tratta lo stesso argomento "
    "in modo più completo. Restituisci UN SOLO paragrafo che li integra, conservando le parole "
    "dell'autore e senza ripetere due volte lo stesso concetto. Non aggiungere nulla di tuo, non "
    "commentare. Rispondi nella lingua dei testi ricevuti."
)


def _embed_all(
    texts: list[str], client: SupportsEmbedChat, *, batch_size: int = 32, progress=None
) -> np.ndarray:
    """Embedding a blocchi, come fa l'indicizzazione.

    Mandare in una sola richiesta tutti i paragrafi della bozza e tutte le sezioni del progetto
    (qui sono centinaia) espone a un fallimento unico che butta via l'intera analisi, e lascia
    la barra ferma per tutto il tempo.
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(client.embed(texts[start : start + batch_size], kind="document"))
        if progress:
            progress(min(start + batch_size, len(texts)), len(texts))
    return _normalize(vectors)


def _merge(paragraph: str, section: str, client: SupportsEmbedChat, model: str | None) -> str:
    try:
        result = client.chat_json(
            [
                {"role": "system", "content": _MERGE_SYSTEM},
                {"role": "user", "content": f"BOZZA:\n{paragraph}\n\nPASSAGGIO:\n{section}"},
            ],
            schema=MERGE_SCHEMA,
            model=model,
        )
        merged = str((result or {}).get("text", "")).strip()  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 - fusione fallita: si tiene l'aggiunta separata
        merged = ""
    return merged


def revise_draft(
    db_path: Path,
    draft_text: str,
    client: SupportsEmbedChat,
    *,
    model: str | None = None,
    integrate: bool = True,
    progress=None,
) -> RevisionReport:
    """Confronta la bozza col materiale del progetto e ne restituisce la versione rivista.

    L'esito è il libro **intero** nell'ordine di lettura: i paragrafi invariati, quelli tolti e
    quelli sostituiti al loro posto, le aggiunte in coda al capitolo di pertinenza e, per il
    materiale che non trova casa da nessuna parte, un capitolo nuovo.
    """
    chapters = split_draft(draft_text)
    indice: list[tuple[int, str]] = []  # (capitolo, paragrafo) in ordine di lettura
    for ci, ch in enumerate(chapters):
        indice.extend((ci, par) for par in ch.paragraphs)
    testi_par = [par for _ci, par in indice]

    with Session(make_engine(db_path)) as ses:
        sections = [n for n in ses.exec(select(Node)).all() if (n.raw_text or "").strip()]
    if not sections:
        raise ValueError("Nessuna sezione nel progetto: importa e dividi i testi prima.")

    # Il lavoro si misura in testi da rappresentare più integrazioni da scrivere. Il totale
    # cresce quando si scopre quante sono queste ultime: meglio un totale che si aggiorna di una
    # barra ferma a due terzi per tutto il tempo in cui il modello sta davvero lavorando.
    fatti = 0
    totale = len(testi_par) + len(sections)

    def _riporta(extra: int = 0) -> None:
        """Avanzamento assoluto sul totale noto (il totale cresce quando si scoprono le fusioni)."""
        if progress:
            progress(min(fatti + extra, totale), totale)

    passo = lambda done, _tot: _riporta(done)  # noqa: E731 - adatta il progresso per fase
    vec_par = _embed_all(testi_par, client, progress=passo) if testi_par else np.zeros((0, 1))
    fatti += len(testi_par)
    vec_sez = _embed_all([n.raw_text for n in sections], client, progress=passo)
    fatti += len(sections)
    _riporta()

    # 1) Ripetizioni interne alla bozza: si tiene la prima occorrenza.
    ridondanti: dict[int, str] = {}
    if len(testi_par) > 1:
        simili = vec_par @ vec_par.T
        for i in range(len(testi_par)):
            for j in range(i):
                if j not in ridondanti and simili[i, j] >= _DUPLICATE:
                    ridondanti[i] = chapters[indice[j][0]].title
                    break

    # 2) Copertura del materiale: cosa manca, cosa è solo abbozzato.
    copertura = vec_sez @ vec_par.T if len(testi_par) else np.zeros((len(sections), 1))
    integrazioni: dict[int, Change] = {}     # paragrafo → versione integrata
    aggiunte: dict[int, list[Change]] = {}   # capitolo → contenuti da accodare
    nuovi: list[Change] = []                 # materiale senza capitolo di pertinenza
    da_fondere: list[tuple[int, Node, str, float]] = []  # (paragrafo, sezione, capitolo, affinità)
    prenotati: set[int] = set()

    def _accoda(node: Node, capitolo: str, punteggio: float, migliore: int) -> None:
        """Il contenuto non si fonde con nessun paragrafo: va aggiunto, in coda al suo capitolo."""
        change = Change(
            operation="add", chapter=capitolo, text=node.raw_text, node_id=node.id,
            reason="contenuto del progetto assente dalla bozza",
        )
        if punteggio >= _RELATED and migliore >= 0:
            aggiunte.setdefault(indice[migliore][0], []).append(change)
        else:
            change.chapter = NEW_CHAPTER
            change.reason = "contenuto che non trova posto in nessun capitolo della bozza"
            nuovi.append(change)

    for idx, node in enumerate(sections):
        migliore = int(np.argmax(copertura[idx])) if copertura.shape[1] else -1
        punteggio = float(copertura[idx, migliore]) if migliore >= 0 else 0.0
        if punteggio >= _COVERED:
            continue  # la bozza lo dice già
        capitolo = chapters[indice[migliore][0]].title if migliore >= 0 else ""
        if (
            integrate
            and punteggio >= _PARTIAL
            and migliore >= 0
            and migliore not in ridondanti
            and migliore not in prenotati
        ):
            # Prima si decide tutto, poi si scrive: così il numero di integrazioni è noto in
            # anticipo e l'attesa può essere raccontata invece che subita.
            prenotati.add(migliore)
            da_fondere.append((migliore, node, capitolo, punteggio))
            continue
        _accoda(node, capitolo, punteggio, migliore)

    # 2-bis) Le integrazioni: l'unico passaggio affidato al modello, e il più lungo.
    totale += len(da_fondere)
    merges_failed = 0
    for par_idx, node, capitolo, punteggio in da_fondere:
        fusione = _merge(testi_par[par_idx], node.raw_text, client, model)
        if fusione:
            integrazioni[par_idx] = Change(
                operation="replace", chapter=capitolo, text=fusione,
                previous=testi_par[par_idx], node_id=node.id,
                reason="il passaggio della bozza era incompleto rispetto al materiale",
            )
        else:
            merges_failed += 1
            _accoda(node, capitolo, punteggio, par_idx)
        fatti += 1
        _riporta()

    # 3) Assemblaggio nell'ordine di lettura.
    changes: list[Change] = []
    for ci, ch in enumerate(chapters):
        for pi, (capitolo_idx, paragrafo) in enumerate(indice):
            if capitolo_idx != ci:
                continue
            if pi in ridondanti:
                changes.append(Change(
                    operation="remove", chapter=ch.title, text=paragrafo,
                    reason=f"ripete un passaggio già presente nel capitolo «{ridondanti[pi] or '—'}»",
                ))
            elif pi in integrazioni:
                changes.append(integrazioni[pi])
            else:
                changes.append(Change(operation="keep", chapter=ch.title, text=paragrafo))
        changes.extend(aggiunte.get(ci, []))
    changes.extend(nuovi)

    _riporta()
    return RevisionReport(changes=changes, merges_failed=merges_failed)
