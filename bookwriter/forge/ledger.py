"""Il registro del racconto: quello che ogni capitolo scritto ha lasciato in eredità al successivo.

Prima di questo registro la stesura del capitolo N sapeva dei precedenti solo una riga ciascuno —
«Establishes that the Osiris Mysteries are a surviving ritual technology» — cioè quello che il
piano voleva, non quello che il testo aveva fatto. Il capitolo 11 di *The Red Thread* comincia così
a Milano, poi passa a Lione, mentre il 10 aveva lasciato il protagonista in Egitto: nessuno gli
aveva detto dove fosse.

Dopo ogni capitolo se ne ricava, dal testo, una scheda:

* **lo stato alla fine** — luogo, tempo, condizione fisica, oggetti, chi sa cosa, relazioni,
  minacce già avvenute, prossima azione: da qui riparte il capitolo dopo;
* **le cose già dette al lettore** — fatti e interpretazioni comunicati: il capitolo dopo non le
  rispiega, a meno che una prova nuova le trasformi;
* **i motivi** — l'acacia, il fumo, il filo… e quale funzione hanno avuto qui: un motivo torna se
  fa un lavoro nuovo, non per ripetersi;
* **le invenzioni** — nomi, codici, uffici, procedure introdotti dalla stesura: sono fiction e
  vanno tenuti coerenti, non verificati;
* **i fili aperti** e **il pericolo**: a che punto è la minaccia;
* **chi sa cosa** (non solo il protagonista: chi viene a sapere cosa, e da chi) e **gli eventi**
  che contano, con il quando e il dove: servono a seguire un cambiamento nei capitoli dopo
  (:mod:`bookwriter.forge.changes`), perché un fatto spostato cambia chi lo sa e cosa ne segue.

Il registro vive in ``story/ledger.json`` (più ``story/ledger.md`` per chi lo vuole leggere). Ogni
scheda porta l'impronta del testo da cui viene: se il capitolo cambia, la scheda si rifà.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bookwriter.forge import notebook as nb
from bookwriter.forge.metrics import count_words, prose_only
from bookwriter.forge.project import BookProject, ForgeState

#: I tipi di invenzione: si registrano perché restino coerenti, non perché si verifichino.
INVENTION_STATUS = ("fiction", "fatto_con_fonte", "inferenza", "da_verificare")

_TESTO = {"type": "string"}
_ELENCO = {"type": "array", "items": {"type": "string"}}

LEDGER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": _TESTO,
        "end_state": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "place": _TESTO, "time": _TESTO, "physical": _TESTO, "objects": _ELENCO,
                "knows": _ELENCO, "relations": _ELENCO, "threats": _ELENCO,
                "next_action": _TESTO,
            },
            "required": ["place", "time", "physical", "objects", "knows", "relations",
                         "threats", "next_action"],
        },
        "told": _ELENCO,
        "motifs": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False,
                      "properties": {"motif": _TESTO, "function": _TESTO},
                      "required": ["motif", "function"]},
        },
        "inventions": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False,
                      "properties": {"name": _TESTO, "what": _TESTO, "status": _TESTO},
                      "required": ["name", "what", "status"]},
        },
        "open_threads": _ELENCO,
        "danger": _TESTO,
        "knowledge": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False,
                      "properties": {"who": _TESTO, "what": _TESTO, "source": _TESTO},
                      "required": ["who", "what", "source"]},
        },
        "events": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False,
                      "properties": {"when": _TESTO, "what": _TESTO, "where": _TESTO},
                      "required": ["when", "what", "where"]},
        },
    },
    "required": ["summary", "end_state", "told", "motifs", "inventions", "open_threads",
                 "danger", "knowledge", "events"],
}

#: Quante voci di «chi sa cosa» e di eventi tiene una scheda: il registro resta breve.
MAX_KNOWLEDGE, MAX_EVENTS = 10, 8


@dataclass
class EndState:
    place: str = ""
    time: str = ""
    physical: str = ""
    objects: list[str] = field(default_factory=list)
    knows: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    threats: list[str] = field(default_factory=list)
    next_action: str = ""

    def lines(self) -> list[str]:
        righe = [f"- Luogo: {self.place or '?'}", f"- Tempo: {self.time or '?'}"]
        if self.physical:
            righe.append(f"- Condizione: {self.physical}")
        for etichetta, voci in (("Ha con sé", self.objects), ("Sa", self.knows),
                                ("Relazioni", self.relations), ("Minacce già avvenute",
                                                                self.threats)):
            if voci:
                righe.append(f"- {etichetta}: " + "; ".join(voci))
        if self.next_action:
            righe.append(f"- Prossima azione: {self.next_action}")
        return righe


@dataclass
class ChapterEntry:
    number: int = 0
    title: str = ""
    #: l'impronta del testo da cui è stata ricavata: se il capitolo cambia, la scheda è vecchia
    source_hash: str = ""
    words: int = 0
    updated: str = ""
    summary: str = ""
    end_state: EndState = field(default_factory=EndState)
    told: list[str] = field(default_factory=list)
    motifs: list[dict[str, str]] = field(default_factory=list)
    inventions: list[dict[str, str]] = field(default_factory=list)
    open_threads: list[str] = field(default_factory=list)
    danger: str = ""
    #: vuoto se l'estrazione è riuscita; altrimenti il perché (la scheda resta, ma vuota)
    error: str = ""
    #: chi viene a sapere cosa in questo capitolo, e da chi o da quale prova: {who, what, source}
    knowledge: list[dict[str, str]] = field(default_factory=list)
    #: gli eventi che contano, nell'ordine: {when, what, where}
    events: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Ledger:
    chapters: dict[int, ChapterEntry] = field(default_factory=dict)

    def entry(self, number: int) -> ChapterEntry | None:
        return self.chapters.get(int(number))

    def before(self, number: int) -> list[ChapterEntry]:
        return [self.chapters[n] for n in sorted(self.chapters) if n < int(number)]

    def previous(self, number: int) -> ChapterEntry | None:
        prima = self.before(number)
        return prima[-1] if prima else None


# --------------------------------------------------------------------------- #
#  su disco
# --------------------------------------------------------------------------- #
def ledger_path(project: BookProject) -> Path:
    return project.story_dir / "ledger.json"


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _entry_from(d: dict[str, Any]) -> ChapterEntry:
    campi = set(ChapterEntry.__dataclass_fields__)
    voce = ChapterEntry(**{k: v for k, v in d.items() if k in campi and k != "end_state"})
    stato = d.get("end_state") or {}
    voce.end_state = EndState(**{k: v for k, v in stato.items()
                                 if k in EndState.__dataclass_fields__})
    return voce


def load(project: BookProject) -> Ledger:
    path = ledger_path(project)
    if not path.exists():
        return Ledger()
    try:
        dati = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Ledger()
    return Ledger({int(k): _entry_from(v) for k, v in (dati.get("chapters") or {}).items()
                   if isinstance(v, dict)})


def save(project: BookProject, ledger: Ledger) -> Path:
    path = ledger_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    dati = {"chapters": {str(n): asdict(v) for n, v in sorted(ledger.chapters.items())}}
    provvisorio = path.with_suffix(".json.tmp")
    provvisorio.write_text(json.dumps(dati, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(provvisorio, path)
    (project.story_dir / "ledger.md").write_text(render(ledger), encoding="utf-8")
    return path


def render(ledger: Ledger) -> str:
    """Il registro in forma leggibile."""
    righe = ["# Registro del racconto", "",
             "Ricavato dai capitoli scritti: si rigenera da solo quando un capitolo cambia.", ""]
    for n in sorted(ledger.chapters):
        v = ledger.chapters[n]
        righe += [f"## Capitolo {n}: {v.title}", ""]
        if v.error:
            righe += [f"_Scheda non ricavata: {v.error}_", ""]
            continue
        if v.summary:
            righe += [v.summary, ""]
        righe += ["**Alla fine del capitolo**", *v.end_state.lines(), ""]
        if v.danger:
            righe += [f"**Pericolo:** {v.danger}", ""]
        if v.told:
            righe += ["**Già detto al lettore**", *[f"- {t}" for t in v.told], ""]
        if v.motifs:
            righe += ["**Motivi e loro funzione**",
                      *[f"- {m.get('motif')}: {m.get('function')}" for m in v.motifs], ""]
        if v.inventions:
            righe += ["**Invenzioni introdotte**",
                      *[f"- {i.get('name')} ({i.get('status')}): {i.get('what')}"
                        for i in v.inventions], ""]
        if v.open_threads:
            righe += ["**Fili aperti**", *[f"- {t}" for t in v.open_threads], ""]
        if v.events:
            righe += ["**Eventi**", *[f"- {e.get('when') or '?'}, {e.get('where') or '?'}: "
                                      f"{e.get('what')}" for e in v.events], ""]
        if v.knowledge:
            righe += ["**Chi sa cosa**",
                      *[f"- {k.get('who')}: {k.get('what')}"
                        + (f" (da {k['source']})" if k.get("source") else "")
                        for k in v.knowledge], ""]
    return "\n".join(righe).rstrip() + "\n"


# --------------------------------------------------------------------------- #
#  ricavare la scheda di un capitolo
# --------------------------------------------------------------------------- #
def _system(state: ForgeState) -> str:
    protagonista = nb.protagonist(state)
    chi = f" Il protagonista è {protagonista}." if protagonista else ""
    return (
        "Sei il curatore della continuità di un libro. Leggi un capitolo appena scritto e ne "
        "ricavi la scheda che servirà a scrivere il capitolo dopo. Registri quello che il TESTO "
        "fa davvero, non quello che il piano voleva: se il capitolo spiega una cosa senza "
        "mostrarla, è comunque una cosa già detta al lettore." + chi + "\n\n"
        "- 'summary': cosa succede, in 3-5 frasi concrete (chi fa cosa, dove).\n"
        "- 'end_state': dove si trova il protagonista alla fine (luogo preciso), quando (tempo "
        "approssimativo, ora del giorno), in che condizione fisica, che oggetti o documenti ha, "
        "cosa sa di nuovo (al massimo 8 voci), le relazioni cambiate, le minacce già avvenute "
        "contro di lui, la prossima azione che il testo lascia aperta. Se il protagonista non è "
        "in scena alla fine, l'ultimo luogo in cui il testo lo mostra, e dillo.\n"
        "- 'told': i fatti e le interpretazioni che il lettore ha ormai ricevuto, una riga "
        "ciascuno, al massimo 15: sono le cose da non rispiegare.\n"
        "- 'motifs': i motivi ricorrenti (simboli, immagini, parole chiave) e la funzione che "
        "hanno avuto in QUESTO capitolo, al massimo 8.\n"
        "- 'inventions': nomi, codici, uffici, procedure, oggetti, persone introdotti dal "
        "testo, al massimo 15, con 'status': 'fiction' (inventato dal racconto), "
        "'fatto_con_fonte' (fatto storico presentato come tale), 'inferenza' (deduzione del "
        "protagonista) o 'da_verificare'. Non giudicare se sono veri: registrali.\n"
        "- 'open_threads': le domande e le piste lasciate aperte, al massimo 6.\n"
        "- 'danger': a che punto è il pericolo per il protagonista (osservato, derubato, "
        "minacciato, arrestato, aggredito, rapito…), in una riga.\n"
        "- 'knowledge': chi (qualunque personaggio, non solo il protagonista) viene a sapere "
        "qualcosa che conta per la trama, cosa, e da chi o da quale prova ('source'), al "
        f"massimo {MAX_KNOWLEDGE}.\n"
        "- 'events': gli eventi che contano, nell'ordine, con 'when' (giorno, ora, periodo, "
        f"come li dice il testo) e 'where', al massimo {MAX_EVENTS}.\n\n"
        f"Scrivi in {nb.language_name(state)}. Righe brevi: è un registro, non un riassunto "
        "letterario."
    )


def extract(state: ForgeState, client: Any, number: int, title: str, text: str, *,
            model: str | None = None) -> ChapterEntry:
    """La scheda di un capitolo, ricavata dal suo testo. Non solleva: se il modello non ce la
    fa, la scheda resta vuota e dice perché."""
    voce = ChapterEntry(number=int(number), title=title, source_hash=text_hash(text),
                        words=count_words(prose_only(text)),
                        updated=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    try:
        risposta = client.chat_json(
            [{"role": "system", "content": _system(state)},
             {"role": "user", "content": f"# Capitolo {number}: {title}\n\n{prose_only(text)}"}],
            schema=LEDGER_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001 - la scheda vuota dice il perché
        voce.error = str(exc)[:300]
        return voce
    if not isinstance(risposta, dict) or not risposta.get("end_state"):
        voce.error = "il modello non ha restituito la scheda"
        return voce
    stato = risposta.get("end_state") or {}
    voce.summary = str(risposta.get("summary") or "").strip()
    voce.end_state = EndState(
        place=str(stato.get("place") or ""), time=str(stato.get("time") or ""),
        physical=str(stato.get("physical") or ""),
        objects=_strings(stato.get("objects")), knows=_strings(stato.get("knows")),
        relations=_strings(stato.get("relations")), threats=_strings(stato.get("threats")),
        next_action=str(stato.get("next_action") or ""))
    voce.told = _strings(risposta.get("told"))[:20]
    voce.motifs = [{"motif": str(m.get("motif") or ""), "function": str(m.get("function") or "")}
                   for m in (risposta.get("motifs") or []) if isinstance(m, dict)][:10]
    voce.inventions = [{"name": str(i.get("name") or ""), "what": str(i.get("what") or ""),
                        "status": _status(str(i.get("status") or ""))}
                       for i in (risposta.get("inventions") or []) if isinstance(i, dict)][:20]
    voce.open_threads = _strings(risposta.get("open_threads"))[:8]
    voce.danger = str(risposta.get("danger") or "").strip()
    voce.knowledge = _records(risposta.get("knowledge"), ("who", "what", "source"))[
        :MAX_KNOWLEDGE]
    voce.events = _records(risposta.get("events"), ("when", "what", "where"))[:MAX_EVENTS]
    return voce


def _records(valore: Any, chiavi: tuple[str, ...]) -> list[dict[str, str]]:
    """Voci strutturate pulite: solo le chiavi attese, stringhe, e con un 'what'."""
    fuori = []
    for v in valore or []:
        if not isinstance(v, dict):
            continue
        voce = {k: str(v.get(k) or "").strip() for k in chiavi}
        if voce.get("what"):
            fuori.append(voce)
    return fuori


def _strings(valore: Any) -> list[str]:
    return [str(v).strip() for v in (valore or []) if str(v).strip()]


def _status(valore: str) -> str:
    v = valore.strip().lower().replace(" ", "_")
    return v if v in INVENTION_STATUS else "da_verificare"


def record(project: BookProject, state: ForgeState, client: Any, number: int, title: str,
           text: str, *, model: str | None = None) -> ChapterEntry:
    """Ricava la scheda di un capitolo e la salva nel registro."""
    registro = load(project)
    voce = extract(state, client, number, title, text, model=model)
    registro.chapters[int(number)] = voce
    save(project, registro)
    return voce


def rebuild(project: BookProject, state: ForgeState, client: Any, *, model: str | None = None,
            only_stale: bool = True, upto: int | None = None, progress=None,
            should_stop=None) -> list[int]:
    """Ricava le schede dei capitoli scritti (solo quelle vecchie o mancanti, se ``only_stale``).

    ``upto``: solo i capitoli fino a quel numero (per preparare la continuità di un capitolo).
    Ritorna i numeri dei capitoli rifatti.
    """
    registro = load(project)
    scritti = project.written_chapters()
    titoli = {int(c.get("number") or 0): str(c.get("title") or "") for c in state.outline}
    da_fare = []
    for numero, percorso in sorted(scritti.items()):
        if upto is not None and numero > upto:
            continue
        testo = percorso.read_text(encoding="utf-8")
        voce = registro.entry(numero)
        if only_stale and voce and not voce.error and voce.source_hash == text_hash(testo):
            continue
        da_fare.append((numero, testo))
    rifatti = []
    for i, (numero, testo) in enumerate(da_fare):
        if should_stop and should_stop():
            break
        if progress:
            progress(i, len(da_fare))
        registro.chapters[numero] = extract(state, client, numero, titoli.get(numero, ""), testo,
                                            model=model)
        save(project, registro)  # una scheda alla volta: un'interruzione non perde le fatte
        rifatti.append(numero)
    if progress:
        progress(len(da_fare), len(da_fare))
    return rifatti
