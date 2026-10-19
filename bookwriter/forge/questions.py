"""I contrasti segnalati dalla ricerca: cosa farne lo decide l'autore.

Una questione nasce in un solo caso: la ricerca ha trovato un fatto che sembra comprovato (una
data, un luogo reale…) in contrasto con quanto il libro afferma, e questo produrrebbe un errore
macroscopico. Mai per gli elementi d'invenzione, mai perché una tesi non ha fonti: quello che
l'autore scrive resta com'è, sempre, finché non decide lui di cambiarlo.

Prima la ricerca la riprende (vedi ``research.deepen_question``: giri successivi per trovare più
materiale sul punto). Quel che resta arriva qui, in una conversazione come quella
dell'intervista: l'assistente dice cosa ha trovato, e l'autore sceglie fra quattro strade:

* **una fonte**: incolla un indirizzo o allega un documento (pdf, docx, md, txt). Si legge, e se
  chiarisce davvero la cosa e dice da dove viene, la questione è risolta;
* **tenerlo com'è**: l'assistente propone come dirlo (una formulazione netta in voce d'autore,
  una che attribuisce o dichiara l'incertezza, una nota) e l'autore sceglie o corregge. La
  formulazione esatta si registra e la stesura la usa;
* **tralasciarlo**: non se ne parla nel libro. Solo se lo decide l'autore.

La conversazione è libera, ma niente si decide per sbaglio: dopo ogni scambio una passata
strutturata registra solo quello che l'autore ha detto in modo esplicito (come il quaderno).
Il documento letto non passa mai da un modello che possa metterci del suo: una questione si
risolve solo se il materiale la chiarisce **e** dà la fonte (vedi ``research.resolve_with_material``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bookwriter.config import ResearchSettings, language_label
from bookwriter.forge import research_store as store
from bookwriter.forge.project import ForgeState
from bookwriter.forge.websearch import fetch

_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

#: Quanta conversazione sulle questioni si rimanda al modello, in caratteri.
HISTORY_CHARS = 40_000

#: Le estensioni che si sanno leggere (quelle dei loader già usati per i documenti del progetto).
DOCUMENT_SUFFIXES = (".pdf", ".docx", ".md", ".markdown", ".txt", ".text")


@dataclass
class Material:
    """Quello che l'autore ha portato in un messaggio, già letto."""

    pages: list[store.PageRead] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # cosa non si è potuto leggere, e perché


def urls_in(text: str) -> list[str]:
    """Gli indirizzi scritti in un messaggio, senza la punteggiatura che li chiude."""
    trovati = [u.rstrip(".,;:!?") for u in _URL.findall(text or "")]
    return list(dict.fromkeys(trovati))


def read_material(settings: ResearchSettings, text: str, files: list[Path]) -> Material:
    """Legge gli indirizzi del messaggio e i documenti allegati.

    Gli indirizzi escono in rete, e la rete è spenta finché l'autore non la accende: lo si dice
    invece di far finta. I documenti sono locali e si leggono sempre.
    """
    from bookwriter.ingest.loaders import LoaderError, load_document

    materiale = Material()
    for url in urls_in(text):
        if not settings.enabled:
            materiale.notes.append(f"Non ho letto {url}: la ricerca sul web è spenta. "
                                   "Accendila in Impostazioni, oppure allega la pagina "
                                   "come documento.")
            continue
        pagina = fetch(url, settings)
        if pagina is None:
            materiale.notes.append(f"Non sono riuscito a leggere {url}.")
            continue
        if not pagina.readable:
            materiale.notes.append(f"{url} non è leggibile: {pagina.note}.")
            continue
        materiale.pages.append(store.PageRead(pagina.url, pagina.title,
                                              pagina.text[:store.PAGE_KEEP * 3], "autore"))
    for percorso in files:
        percorso = Path(percorso)
        if percorso.suffix.lower() not in DOCUMENT_SUFFIXES:
            materiale.notes.append(f"{percorso.name}: formato non supportato "
                                   "(pdf, docx, md, txt).")
            continue
        try:
            doc = load_document(percorso)
        except (LoaderError, OSError) as exc:
            materiale.notes.append(f"Non sono riuscito a leggere {percorso.name}: {exc}")
            continue
        if not doc.raw_text.strip():
            materiale.notes.append(f"{percorso.name} non contiene testo leggibile.")
            continue
        materiale.pages.append(store.PageRead(percorso.name, percorso.name,
                                              doc.raw_text[:store.PAGE_KEEP * 10], "autore"))
    return materiale


# --------------------------------------------------------------------------- #
#  la conversazione
# --------------------------------------------------------------------------- #
_ROLE = (
    "Sei l'editor di un libro. La ricerca ha segnalato alcuni contrasti fra quanto il libro "
    "afferma e fatti che sembravano comprovati. Non sei un revisore e non difendi nessuna "
    "versione ufficiale: il libro è dell'autore, e finché non decide lui resta com'è scritto. "
    "Trovi i contrasti sotto, con quello che è stato provato. Parli con l'autore, che decide: una "
    "conversazione libera, non un modulo; un contrasto alla volta, partendo dal primo, a meno che "
    "l'autore scelga un altro.\n\n"
    "Per ognuno spiega in breve cosa dice il libro, cosa dice la fonte trovata e cosa è stato "
    "provato, senza dire che l'autore sbaglia, e proponi le strade:\n"
    "1. L'autore ha una fonte: può incollare uno o più indirizzi web, oppure allegare un "
    "documento (pdf, docx, md, txt) con il pulsante «Allega documento…». Li leggi tu.\n"
    "2. Pubblicare comunque: proponi tu come dirlo nel testo, con almeno tre formulazioni — "
    "una NETTA, in voce d'autore, senza cautele né attribuzioni; una che attribuisce a una "
    "tradizione o a una testimonianza, o che dichiara l'incertezza; una nota sull'assenza di "
    "fonti — e l'autore sceglie o corregge. Scrivi ogni formulazione per intero, pronta da "
    "usare, e non dire quale sia «la migliore» a meno che te lo chieda.\n"
    "3. Tralasciare l'informazione: il libro non ne parla (solo se lo decide l'autore).\n\n"
    "Non inventare fonti, cifre, autori o anni, e non fingere di aver letto quello che non hai "
    "letto. Non considerare niente deciso finché l'autore non l'ha detto in modo esplicito: "
    "una proposta tua non è una decisione sua. Quando un contrasto è chiuso passa al "
    "successivo. Quando sono finiti, dillo."
)


def system_prompt(state: ForgeState, pending: list[store.Question],
                  titles: dict[int, str], *, resolved_now: list[store.Question] | None = None,
                  notes: list[str] | None = None, web_on: bool = True) -> str:
    """Il ruolo dell'assistente, con le questioni da trattare e quello che è appena successo."""
    lingua = language_label(state.language) or "italiano"
    righe = []
    for q in pending:
        provato = (f"{q.tried()} interrogazioni, {q.seen()} risultati visti"
                   if q.attempts else
                   "nessun approfondimento (ricerca sul web spenta nelle impostazioni)"
                   if not web_on else
                   "non ancora approfondito (la ricerca si è fermata prima)")
        righe.append(f"- {q.id} — capitolo {q.chapter} «{titles.get(q.chapter, '')}»: "
                     f"{q.text} [provato: {provato}]")
    parti = [_ROLE, "## I contrasti da decidere\n" + ("\n".join(righe) or "(nessuna)")]
    if resolved_now:
        parti.append("## Appena risolte con il materiale dell'autore\n" + "\n".join(
            f"- {q.text} → {q.outcome.get('source', '')}" for q in resolved_now))
    if notes:
        parti.append("## Cosa non si è potuto leggere\n" + "\n".join(f"- {n}" for n in notes))
    parti.append(f"Rispondi nella lingua in cui ti scrive l'autore; se non è chiaro, in {lingua}.")
    return "\n\n".join(parti)


def _alternate(transcript: list[dict[str, str]], limit: int) -> list[dict[str, str]]:
    """Come nell'intervista: battute alternate, a partire dall'autore, senza superare ``limit``."""
    messaggi: list[dict[str, str]] = []
    for battuta in transcript:
        ruolo = "assistant" if battuta.get("role") == "assistant" else "user"
        testo = (battuta.get("text") or "").strip()
        if not testo:
            continue
        if messaggi and messaggi[-1]["role"] == ruolo:
            messaggi[-1]["content"] += "\n\n" + testo
        else:
            messaggi.append({"role": ruolo, "content": testo})
    while messaggi and sum(len(m["content"]) for m in messaggi) > limit and len(messaggi) > 1:
        messaggi.pop(0)
    while messaggi and messaggi[0]["role"] != "user":
        messaggi.pop(0)
    return messaggi


def reply(state: ForgeState, client: Any, text: str | None, system: str, *,
          model: str | None = None, on_delta: Callable[[str, str], None] | None = None) -> str:
    """Registra quello che ha scritto l'autore e ritorna la risposta dell'assistente.

    ``text`` vuoto vuol dire «riprova». Se la risposta viene fermata a metà, quello che è
    arrivato resta nella conversazione (lo ha letto).
    """
    from bookwriter.forge.interview import Stopped

    if text and text.strip():
        state.questions_transcript.append({"role": "user", "text": text.strip()})
    if not state.questions_transcript or state.questions_transcript[-1].get("role") != "user":
        raise ValueError("Non c'è niente a cui rispondere.")
    messaggi = _alternate(state.questions_transcript, HISTORY_CHARS)
    arrivato: list[str] = []

    def ascolta(tipo: str, pezzo: str) -> None:
        if on_delta is not None:
            on_delta(tipo, pezzo)
        if tipo == "restart":
            arrivato.clear()
        elif tipo == "content":
            arrivato.append(pezzo)

    try:
        risposta = client.chat([{"role": "system", "content": system}, *messaggi],
                               model=model, on_delta=ascolta).strip()
    except Stopped:
        parziale = "".join(arrivato).strip()
        if parziale:
            state.questions_transcript.append({"role": "assistant", "text": parziale + " …"})
        raise
    state.questions_transcript.append({"role": "assistant", "text": risposta})
    return risposta


# --------------------------------------------------------------------------- #
#  registrare le decisioni
# --------------------------------------------------------------------------- #
DECISIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "question_id": {"type": "string"},
                    "action": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["question_id", "action", "text"],
            },
        },
    },
    "required": ["decisions"],
}

_DECISION_RULES = (
    "Registri le decisioni che un autore ha preso parlando con il suo editor su affermazioni del "
    "libro non documentate. Ti do le questioni ancora da decidere e l'ultimo tratto della "
    "conversazione. Per ogni questione sulla quale l'AUTORE ha deciso in modo esplicito:\n"
    "- action \"publish\": ha accettato (o scritto lui) una formulazione da pubblicare così "
    "com'è; in 'text' la formulazione ESATTA che ha approvato, per intero;\n"
    "- action \"omit\": ha detto di tralasciare l'informazione; 'text' è la stringa vuota "
    "(\"\"), ma presente.\n"
    "Non vale una proposta dell'editor a cui l'autore non ha ancora risposto, né un'ipotesi, "
    "né una domanda: nel dubbio non mettere la questione. Non mettere le questioni sulle quali "
    "non si è deciso niente. Se non c'è nessuna decisione, restituisci un elenco vuoto."
)


def update_decisions(state: ForgeState, client: Any, pending: list[store.Question], *,
                     model: str | None = None) -> dict[str, tuple[str, str]]:
    """Cosa ha deciso l'autore nell'ultimo scambio: ``{id: (azione, testo)}``.

    Si legge da dove si era rimasti (``state.questions_upto``), partendo dalla battuta che
    precede: è lì che di solito sta la proposta a cui l'autore dice di sì.
    """
    dove = max(0, min(state.questions_upto, len(state.questions_transcript)) - 1)
    tratto = state.questions_transcript[dove:]
    if not pending or not any(b.get("role") == "user" for b in tratto):
        state.questions_upto = len(state.questions_transcript)
        return {}
    conversazione = "\n\n".join(
        f"{'EDITOR' if b.get('role') == 'assistant' else 'AUTORE'}: {b.get('text', '').strip()}"
        for b in tratto)
    elenco = "\n".join(f"- {q.id}: {q.text}" for q in pending)
    risposta = client.chat_json(
        [{"role": "system", "content": _DECISION_RULES},
         {"role": "user", "content": f"## Questioni da decidere\n{elenco}\n\n"
                                     f"## L'ultimo tratto della conversazione\n{conversazione}"}],
        schema=DECISIONS_SCHEMA, model=model)
    state.questions_upto = len(state.questions_transcript)
    validi = {q.id for q in pending}
    decise: dict[str, tuple[str, str]] = {}
    for voce in (risposta.get("decisions") or []) if isinstance(risposta, dict) else []:
        if not isinstance(voce, dict):
            continue
        ident, azione = str(voce.get("question_id") or ""), str(voce.get("action") or "").lower()
        testo = str(voce.get("text") or "").strip()
        if ident not in validi:
            continue
        if azione == "publish" and testo:
            decise[ident] = (store.OUT_PUBLISH, testo)
        elif azione == "omit":
            decise[ident] = (store.OUT_OMIT, "")
    return decise


def decide(question: store.Question, kind: str, text: str = "") -> None:
    """Registra la decisione dell'autore su una questione."""
    question.status = store.DECISA
    question.outcome = {"kind": kind, "text": text}
