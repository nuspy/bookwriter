"""La struttura del libro intero: riordinare, tagliare, unire, dividere capitoli.

Perché un modulo a parte. La qualità (:mod:`quality`) e la revisione (:mod:`revision`) lavorano
dentro un capitolo: riscrivono, uniscono, dividono o spostano paragrafi e scene, ma non toccano il
posto del capitolo nel libro. Alcuni difetti però si vedono solo da lontano: due capitoli che
fanno lo stesso lavoro, uno che racconta un effetto prima della sua causa, un capitolo così
scarno che vive solo come appendice del precedente, uno che ne porta due. Nessuna correzione
locale li risolve, e una revisione capitolo per capitolo li rende anzi più solidi.

Il lavoro è in tre tempi, e il secondo è dell'autore:

1. **la diagnosi** (:func:`diagnose`). Il modello non legge i capitoli: legge le loro **schede**
   — cosa deve fare ognuno (la struttura), cosa fa davvero e cosa lascia al lettore (il registro
   del racconto, :mod:`ledger`), le parole, le sezioni, i voti della qualità quando sono ancora
   validi — e propone operazioni (``reorder``, ``cut``, ``merge``, ``split``), ciascuna con il
   problema che risolve, le prove, il rischio e le dipendenze che rompe. Le chiamate sono brevi:
   un libro lungo si guarda a finestre di capitoli più una passata d'insieme con le schede corte,
   perché il modello locale scrive al più qualche migliaio di token per risposta.
   Poi i **controlli deterministici** (:func:`check`), che non si fidano del modello: un nome,
   un codice o un oggetto introdotto in un capitolo e usato nei successivi è una dipendenza
   misurabile sul testo; un riordino che mette chi lo usa prima di chi lo presenta, o un taglio
   che lo perde senza una destinazione, è **bloccante**. Le date esplicite del registro danno
   l'ordine del tempo: invertirle è un avviso (un flashback può essere voluto).
2. **la decisione** (:func:`decide`). Ogni operazione è una proposta: si accetta o si rifiuta
   una per una. Un'operazione con blocker si accetta solo con una deroga motivata dell'autore,
   registrata — mai implicita, come le deroghe ai piani dei capitoli.
3. **l'applicazione** (:func:`apply`) e il **ritorno indietro** (:func:`undo`). Prima di toccare
   un file se ne copia ogni versione in ``story/struttura/<giro>/`` (capitoli, ricerca, piani,
   cronologie, storia della qualità, registro, specifica); la struttura e i file dei capitoli si
   rinumerano insieme, e la fusione scrive col modello solo il punto di giunzione — gli ultimi
   paragrafi del primo capitolo e i primi del secondo — perché tutto il resto è già scritto e
   una riscrittura intera starebbe ben oltre la misura di una risposta. I capitoli toccati
   (spostati, uniti, divisi, o con un capitolo diverso davanti) restano segnati da ricontrollare
   (:func:`recheck` nel motore: registro e qualità). L'annullamento rimette i file com'erano,
   a ritroso, e rifiuta di cancellare un lavoro fatto dopo senza il consenso esplicito.

Tutto sta in ``story/struttura_review.json``: le diagnosi (giri), le decisioni con la loro data,
le applicazioni e gli annullamenti. Non si cancella niente: un giro vecchio resta come storia.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bookwriter.forge import ledger as lg
from bookwriter.forge import metrics as mx
from bookwriter.forge import notebook as nb
from bookwriter.forge import quality as ql
from bookwriter.forge.editor import Chapter, Section, parse
from bookwriter.forge.project import BookProject, ForgeState, slugify

# --------------------------------------------------------------------------- #
#  vocabolario
# --------------------------------------------------------------------------- #
REORDER, CUT, MERGE, SPLIT = "reorder", "cut", "merge", "split"
KINDS = (REORDER, CUT, MERGE, SPLIT)

#: Come il modello (o un'interfaccia) può chiamare le operazioni: tutto si riporta alle quattro.
_KIND_ALIASES = {
    "reorder": REORDER, "move": REORDER, "riordina": REORDER, "sposta": REORDER,
    "riordino": REORDER, "spostamento": REORDER,
    "cut": CUT, "remove": CUT, "delete": CUT, "taglia": CUT, "elimina": CUT, "togli": CUT,
    "taglio": CUT,
    "merge": MERGE, "fuse": MERGE, "unisci": MERGE, "fondi": MERGE, "fusione": MERGE,
    "split": SPLIT, "divide": SPLIT, "dividi": SPLIT, "divisione": SPLIT,
}

PROPOSTA, ACCETTATA, RIFIUTATA = "proposta", "accettata", "rifiutata"
APPLICATA, ANNULLATA, SUPERATA = "applicata", "annullata", "superata"
#: Le operazioni ancora «vive»: si rinumerano quando un'altra cambia il libro.
OPEN_STATUSES = (PROPOSTA, ACCETTATA)

#: Il formato del file: cresce se cambia la forma dei dati.
FORMAT = 1

#: Un libro fino a tanti capitoli si guarda in una chiamata sola, con le schede intere.
WINDOW = 10
#: Le finestre di un libro più lungo: quanti capitoli, e di quanti si va avanti ogni volta (la
#: sovrapposizione fa vedere le coppie a cavallo di due finestre).
WINDOW_STEP = 8
#: Quante operazioni per chiamata: ognuna costa qualche centinaio di token di risposta.
MAX_OPS_PER_CALL = 6
#: Quante operazioni, al più, per diagnosi.
MAX_OPS = 20
#: Quanti paragrafi per parte, e quante parole al più, entrano nella giunzione di una fusione.
SEAM_PARAGRAPHS, SEAM_WORDS = 3, 700

_T = {"type": "string"}
_L = {"type": "array", "items": {"type": "string"}}
_N = {"type": "integer"}

OPERATION_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "chapters": {"type": "array", "items": _N},
        "after": _N, "destination": _N,
        "unique_content": _L, "new_titles": _L, "split_at": _T,
        "issue": _T, "evidence": _T, "reason": _T, "risk": _T, "breaks": _L,
    },
    "required": ["kind", "chapters", "after", "destination", "unique_content", "new_titles",
                 "split_at", "issue", "evidence", "reason", "risk", "breaks"],
}
DIAGNOSIS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"assessment": _T,
                   "operations": {"type": "array", "items": OPERATION_SCHEMA}},
    "required": ["assessment", "operations"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cut(testo: Any, n: int) -> str:
    testo = " ".join(str(testo or "").split())
    return testo if len(testo) <= n else testo[:n - 1].rstrip() + "…"


def _int(valore: Any, default: int = 0) -> int:
    try:
        return int(valore)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
#  i dati
# --------------------------------------------------------------------------- #
@dataclass
class Operation:
    """Una proposta di cambiamento della struttura, e la sua storia."""

    id: str = ""
    kind: str = REORDER
    #: i capitoli coinvolti: uno per reorder/cut/split, due per merge (nell'ordine del libro)
    chapters: list[int] = field(default_factory=list)
    #: reorder: dopo quale capitolo va (0 = in testa al libro)
    after: int = 0
    #: cut: il capitolo che accoglie il contenuto unico (0 = nessuno)
    destination: int = 0
    #: cut: quello che il capitolo ha di unico e non va perso
    unique_content: list[str] = field(default_factory=list)
    #: merge: [titolo del capitolo unito]; split: [titolo prima parte, titolo seconda parte]
    new_titles: list[str] = field(default_factory=list)
    #: split: il titolo della sezione da cui comincia la seconda parte
    split_at: str = ""
    issue: str = ""
    evidence: str = ""
    #: la ragione causale o cronologica (reorder), o la funzione duplicata (cut, merge)
    reason: str = ""
    risk: str = ""
    #: le dipendenze che rompe, secondo il modello
    breaks: list[str] = field(default_factory=list)
    #: dai controlli deterministici: bloccano l'accettazione senza una deroga
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = PROPOSTA
    #: la deroga motivata dell'autore ai blocker, se c'è
    waiver: str = ""
    note: str = ""
    #: i capitoli com'erano quando la proposta è nata (o è stata rinumerata): {number, title}
    refs: list[dict[str, Any]] = field(default_factory=list)
    #: il giro di applicazione (la cartella del salvataggio), se applicata
    run: str = ""
    #: la storia della proposta: [{"at", "what"}]
    log: list[dict[str, str]] = field(default_factory=list)

    def remember(self, what: str) -> None:
        self.log.append({"at": _now(), "what": what})

    @property
    def signature(self) -> tuple:
        """Cosa rende due proposte la stessa proposta."""
        return (self.kind, tuple(self.chapters), self.after if self.kind == REORDER else 0,
                self.split_at.strip().lower() if self.kind == SPLIT else "")

    def line(self) -> str:
        """L'operazione in una riga, per chi legge un elenco."""
        c = self.chapters
        if self.kind == REORDER and c:
            return (f"Sposta il capitolo {c[0]} "
                    + ("in testa al libro" if not self.after else f"dopo il {self.after}"))
        if self.kind == CUT and c:
            return f"Togli il capitolo {c[0]}" + (
                f" (il contenuto unico va nel {self.destination})" if self.destination else "")
        if self.kind == MERGE and len(c) >= 2:
            return f"Unisci i capitoli {c[0]} e {c[1]}"
        if self.kind == SPLIT and c:
            return f"Dividi il capitolo {c[0]}" + (
                f" alla sezione «{self.split_at}»" if self.split_at else "")
        return self.kind


@dataclass
class Round:
    """Una diagnosi: le proposte nate insieme, sulla struttura di quel momento."""

    id: str = ""
    created: str = ""
    model: str = ""
    kind: str = ""
    #: la struttura su cui è nata: [{number, title, words}]
    basis: list[dict[str, Any]] = field(default_factory=list)
    assessment: str = ""
    #: se e quanto la valutazione della qualità era usabile
    quality: str = ""
    calls: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)


@dataclass
class Run:
    """Un'applicazione (o un'integrazione): cosa ha cambiato, e dove sta l'originale."""

    id: str = ""
    op: str = ""
    kind: str = ""
    at: str = ""
    backup: str = ""  #: la cartella, relativa al libro
    #: numero vecchio → numero nuovo (None = il capitolo non c'è più)
    number_map: dict[str, Any] = field(default_factory=dict)
    renamed: list[list[str]] = field(default_factory=list)
    recheck: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    undone: str = ""


@dataclass
class StructureData:
    format: int = FORMAT
    #: cresce a ogni salvataggio: chi ha una copia vecchia lo vede
    revision: int = 0
    updated: str = ""
    rounds: list[Round] = field(default_factory=list)
    runs: list[Run] = field(default_factory=list)
    #: i capitoli da ricontrollare (registro e qualità), con la numerazione di adesso
    recheck: list[int] = field(default_factory=list)
    #: il contenuto unico dei capitoli tagliati, da inserire nel capitolo che lo accoglie:
    #: [{"op", "chapter", "from", "content"}]
    integrations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def current(self) -> Round | None:
        return self.rounds[-1] if self.rounds else None

    def operations(self) -> list[Operation]:
        return [op for r in self.rounds for op in r.operations]

    def operation(self, ident: str) -> Operation | None:
        return next((op for op in self.operations() if op.id == ident), None)

    def open(self) -> list[Operation]:
        """Le proposte ancora da decidere o da applicare, di qualsiasi giro."""
        return [op for op in self.operations() if op.status in OPEN_STATUSES]

    def last_run(self) -> Run | None:
        attivi = [r for r in self.runs if not r.undone]
        return attivi[-1] if attivi else None


def data_path(project: BookProject) -> Path:
    return project.story_dir / "struttura_review.json"


def backups_dir(project: BookProject) -> Path:
    return project.story_dir / "struttura"


def _op_from(d: dict[str, Any]) -> Operation:
    campi = set(Operation.__dataclass_fields__)
    return Operation(**{k: v for k, v in d.items() if k in campi})


def load(project: BookProject) -> StructureData:
    path = data_path(project)
    if not path.exists():
        return StructureData()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return StructureData()
    if not isinstance(raw, dict):
        return StructureData()
    dati = StructureData(format=_int(raw.get("format"), FORMAT),
                         revision=_int(raw.get("revision")), updated=str(raw.get("updated") or ""),
                         recheck=[_int(n) for n in raw.get("recheck") or [] if _int(n)],
                         integrations=[i for i in raw.get("integrations") or []
                                       if isinstance(i, dict)])
    campi_giro = set(Round.__dataclass_fields__)
    for r in raw.get("rounds") or []:
        if not isinstance(r, dict):
            continue
        giro = Round(**{k: v for k, v in r.items() if k in campi_giro and k != "operations"})
        giro.operations = [_op_from(o) for o in r.get("operations") or [] if isinstance(o, dict)]
        dati.rounds.append(giro)
    campi_run = set(Run.__dataclass_fields__)
    dati.runs = [Run(**{k: v for k, v in r.items() if k in campi_run})
                 for r in raw.get("runs") or [] if isinstance(r, dict)]
    return dati


def save(project: BookProject, dati: StructureData) -> Path:
    """Salva in modo atomico: un'interruzione non lascia mezzo file."""
    dati.revision += 1
    dati.updated = _now()
    path = data_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    provvisorio = path.with_suffix(".json.tmp")
    provvisorio.write_text(json.dumps(asdict(dati), ensure_ascii=False, indent=1),
                           encoding="utf-8")
    os.replace(provvisorio, path)
    return path


# --------------------------------------------------------------------------- #
#  i capitoli visti dall'alto
# --------------------------------------------------------------------------- #
@dataclass
class ChapterView:
    """Un capitolo come lo vede la diagnosi: la struttura, la scheda, la misura. Mai il testo
    intero al modello; il testo serve solo ai controlli deterministici."""

    number: int
    title: str
    does: str = ""
    budget: int = 0
    words: int = 0
    part: int = 1
    written: bool = False
    path: Path | None = None
    text: str = ""
    entry: Any = None  #: la scheda del registro, se c'è
    stale: bool = False  #: la scheda è di una versione precedente del testo
    sections: list[str] = field(default_factory=list)
    score: float | None = None


_SEZIONE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_NOTE = re.compile(r"(?i)^(?:Note bibliografiche|Bibliograph\w*|References|Fonti|Notes|Note)$")


def _sections(testo: str) -> list[str]:
    return [m.group(1).strip() for m in _SEZIONE.finditer(mx.prose_only(testo))
            if not _NOTE.match(m.group(1).strip())]


def _outline(state: ForgeState) -> list[dict[str, Any]]:
    return sorted((c for c in state.outline if _int(c.get("number")) > 0),
                  key=lambda c: _int(c.get("number")))


def chapter_views(project: BookProject, state: ForgeState) -> list[ChapterView]:
    """I capitoli della struttura, nell'ordine, con quello che si sa di ciascuno."""
    scritti = project.written_chapters()
    registro = lg.load(project)
    viste = []
    for c in _outline(state):
        n = _int(c.get("number"))
        v = ChapterView(number=n, title=str(c.get("title") or ""),
                        does=str(c.get("does") or ""), budget=_int(c.get("words")),
                        part=_int(c.get("part"), 1) or 1)
        if n in scritti:
            v.written, v.path = True, scritti[n]
            try:
                v.text = scritti[n].read_text(encoding="utf-8")
            except OSError:
                v.text = ""
            v.words = mx.count_words(mx.prose_only(v.text))
            v.sections = _sections(v.text)
            ultima = ql.latest(project, scritti[n].stem)
            # un voto vale solo per il testo che ha valutato
            if ultima is not None and ultima.text_hash == ql._hash(v.text):
                v.score = ultima.global_score
        voce = registro.entry(n)
        if voce is not None and not voce.error:
            v.entry = voce
            v.stale = bool(v.text) and voce.source_hash != lg.text_hash(v.text)
        viste.append(v)
    return viste


def _names(voci: list[dict[str, Any]], chiave: str) -> list[str]:
    return [str(v.get(chiave) or "").strip() for v in voci or [] if isinstance(v, dict)
            and str(v.get(chiave) or "").strip()]


def digest(v: ChapterView, *, full: bool = True) -> str:
    """La scheda di un capitolo per il modello: breve, con i fatti che servono a giudicarne il
    posto. ``full=False``: una riga o due, per guardare il libro intero in una chiamata."""
    misura = f"{v.words} parole" if v.written else "non ancora scritto"
    if v.budget:
        misura += f" (previste {v.budget})"
    testa = f"## Capitolo {v.number}: «{v.title}» — {misura}"
    if v.score is not None:
        testa += f", voto globale {v.score}"
    e = v.entry
    if not full:
        cosa = _cut(e.summary if e is not None and e.summary else v.does, 260)
        luogo = ""
        if e is not None and (e.end_state.place or e.end_state.time):
            luogo = f" Alla fine: {_cut(e.end_state.place, 60)}; {_cut(e.end_state.time, 60)}."
        return f"{testa}\n{cosa}{luogo}"
    righe = [testa]
    if v.does:
        righe.append(f"Cosa deve fare: {_cut(v.does, 300)}")
    if e is None:
        righe.append("(nessuna scheda nel registro)")
    else:
        if v.stale:
            righe.append("(scheda di una versione precedente del testo)")
        if e.summary:
            righe.append(f"Cosa fa davvero: {_cut(e.summary, 500)}")
        es = e.end_state
        if es.place or es.time:
            righe.append(f"Alla fine: {_cut(es.place, 80)}; {_cut(es.time, 80)}")
        if e.told:
            righe.append("Già detto al lettore: " + "; ".join(_cut(t, 120) for t in e.told[:8]))
        sapere = getattr(e, "knowledge", None) or []
        if sapere:
            righe.append("Chi sa cosa: " + "; ".join(
                _cut(f"{k.get('who')} — {k.get('what')}", 120) for k in sapere[:6]
                if isinstance(k, dict)))
        eventi = getattr(e, "events", None) or []
        if eventi:
            righe.append("Eventi: " + "; ".join(
                _cut(f"{x.get('when')}: {x.get('what')}", 120) for x in eventi[:6]
                if isinstance(x, dict)))
        if e.motifs:
            righe.append("Motivi: " + "; ".join(
                _cut(f"{m.get('motif')} ({m.get('function')})", 90) for m in e.motifs[:6]))
        nomi = _names(e.inventions, "name")
        if nomi:
            righe.append("Introduce: " + ", ".join(_cut(x, 50) for x in nomi[:10]))
        if e.end_state.objects:
            righe.append("Oggetti alla fine: " + ", ".join(_cut(o, 50)
                                                            for o in e.end_state.objects[:8]))
        if e.open_threads:
            righe.append("Fili aperti: " + "; ".join(_cut(t, 100) for t in e.open_threads[:5]))
    if v.sections:
        righe.append("Sezioni: " + " | ".join(_cut(s, 60) for s in v.sections[:12]))
    return "\n".join(righe)


def quality_block(project: BookProject, viste: list[ChapterView]) -> tuple[str, str]:
    """Il giudizio del libro dalla verifica della qualità, se vale ancora per questi capitoli.

    Ritorna ``(blocco per il modello, nota per l'autore)``. La valutazione del libro si usa solo
    se tutti i capitoli che ha valutato hanno ancora lo stesso testo: un giudizio su un'altra
    struttura confonderebbe la diagnosi più di quanto la aiuti.
    """
    libro, capitoli = ql.load_book(project)
    if libro is None:
        return "", "nessuna valutazione del libro: la diagnosi usa solo struttura e registro"
    testi = {v.number: v.text for v in viste if v.written}
    validi = [c for c in capitoli
              if c.chapter in testi and c.text_hash == ql._hash(testi[c.chapter])]
    if len(validi) < len(capitoli) or len(capitoli) < len(testi):
        return "", ("la valutazione del libro è di una versione precedente: si usano solo i voti "
                    "dei capitoli non cambiati")
    righe = ["# Il giudizio del libro (verifica della qualità)"]
    if libro.summary:
        righe.append(_cut(libro.summary, 800))
    for chiave in ("arco", "ripetizioni_libro", "coerenza_libro"):
        s = libro.score(chiave)
        if s is not None and s.score is not None:
            righe.append(f"- {s.label}: {s.score} — {_cut(s.verdict, 300)}")
    if libro.priorities:
        righe.append("Priorità: " + "; ".join(_cut(p, 160) for p in libro.priorities[:5]))
    if libro.risks:
        righe.append("Rischi: " + "; ".join(_cut(r, 160) for r in libro.risks[:4]))
    return "\n".join(righe), "usata la valutazione del libro"


# --------------------------------------------------------------------------- #
#  le dipendenze, misurate sul testo
# --------------------------------------------------------------------------- #
#: l'articolo davanti a un nome: una parola intera seguita da spazio («la chiave», non «lavoro»),
#: o l'elisione («l'ufficio»)
_ARTICOLI = re.compile(r"^(?:(?:il|lo|la|i|gli|le|un|uno|una|the|a|an)\s+|(?:l|un)['’]\s*)",
                       re.IGNORECASE)


def _key(nome: str) -> str:
    k = " ".join(str(nome or "").lower().split()).strip(" .,;:!?\"'«»“”()[]")
    return _ARTICOLI.sub("", k).strip()


@dataclass
class Dependencies:
    """Chi usa cosa: per ogni nome introdotto (invenzioni e oggetti del registro), i capitoli il
    cui testo lo nomina. Il primo, nell'ordine attuale, è quello che lo presenta."""

    users: dict[str, set[int]] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    #: le date esplicite dei capitoli, dal registro: numero → (anno, mese, giorno)
    dates: dict[int, tuple[int, int, int]] = field(default_factory=dict)
    date_labels: dict[int, str] = field(default_factory=dict)

    def intro(self, chiave: str) -> int:
        return min(self.users[chiave])

    def introduced_by(self, numero: int) -> list[str]:
        """I nomi che il capitolo presenta e che i capitoli dopo usano."""
        return sorted((k for k, u in self.users.items() if len(u) > 1 and min(u) == numero),
                      key=lambda k: self.labels[k].lower())


def dependencies(viste: list[ChapterView]) -> Dependencies:
    dip = Dependencies()
    testi = {v.number: v.text.lower() for v in viste if v.written and v.text}
    for v in viste:
        e = v.entry
        if e is None:
            continue
        # le invenzioni sono nomi propri del libro; gli oggetti contano solo se nominati in modo
        # specifico («il fascicolo Kappa», non «il telefono»), altrimenti ogni capitolo che dice
        # «telefono» diventerebbe una dipendenza
        nomi = _names(e.inventions, "name") + [o for o in e.end_state.objects or []
                                                if len(_key(o).split()) >= 2]
        for nome in nomi:
            k = _key(nome)
            if len(k) < 4 or k in dip.users:
                continue
            utenti = {n for n, t in testi.items() if k in t}
            utenti.add(v.number)  # chi lo registra lo usa, anche se la grafia è diversa
            dip.users[k] = utenti
            dip.labels[k] = str(nome).strip()
        quando = _date(e.end_state.time)
        etichetta = e.end_state.time
        if quando is None:
            for ev in reversed(getattr(e, "events", None) or []):
                if isinstance(ev, dict) and (quando := _date(str(ev.get("when") or ""))):
                    etichetta = str(ev.get("when") or "")
                    break
        if quando is not None:
            dip.dates[v.number] = quando
            dip.date_labels[v.number] = _cut(etichetta, 60)
    return dip


_MESI = {m: i for i, nomi in enumerate((
    ("gennaio", "january", "janvier", "januar"), ("febbraio", "february", "février", "februar"),
    ("marzo", "march", "mars", "märz"), ("aprile", "april", "avril"),
    ("maggio", "may", "mai"), ("giugno", "june", "juin", "juni"),
    ("luglio", "july", "juillet", "juli"), ("agosto", "august", "août"),
    ("settembre", "september", "septembre"), ("ottobre", "october", "octobre", "oktober"),
    ("novembre", "november"), ("dicembre", "december", "décembre", "dezember")), 1)
    for m in nomi}
_ISO = re.compile(r"\b(1\d{3}|20\d{2})-(\d{1,2})-(\d{1,2})\b")
_GIORNO_MESE_ANNO = re.compile(r"\b(\d{1,2})\s+([a-zà-ü]+)\s+(1\d{3}|20\d{2})\b", re.IGNORECASE)
_MESE_GIORNO_ANNO = re.compile(r"\b([a-zà-ü]+)\s+(\d{1,2}),?\s+(1\d{3}|20\d{2})\b", re.IGNORECASE)
_MESE_ANNO = re.compile(r"\b([a-zà-ü]+)\s+(?:del\s+|of\s+)?(1\d{3}|20\d{2})\b", re.IGNORECASE)
_ANNO = re.compile(r"\b(1\d{3}|20\d{2})\b")


def _date(testo: str) -> tuple[int, int, int] | None:
    """Una data esplicita in un'indicazione di tempo; None se non ce n'è (o è solo «sera»)."""
    t = str(testo or "")
    if m := _ISO.search(t):
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    if (m := _GIORNO_MESE_ANNO.search(t)) and m.group(2).lower() in _MESI:
        return int(m.group(3)), _MESI[m.group(2).lower()], int(m.group(1))
    if (m := _MESE_GIORNO_ANNO.search(t)) and m.group(1).lower() in _MESI:
        return int(m.group(3)), _MESI[m.group(1).lower()], int(m.group(2))
    if (m := _MESE_ANNO.search(t)) and m.group(1).lower() in _MESI:
        return int(m.group(2)), _MESI[m.group(1).lower()], 0
    if m := _ANNO.search(t):
        return int(m.group(1)), 0, 0
    return None


# --------------------------------------------------------------------------- #
#  l'operazione simulata: dove finisce il contenuto di ogni capitolo
# --------------------------------------------------------------------------- #
@dataclass
class Slot:
    """Un capitolo del libro dopo l'operazione."""

    base: int  #: il capitolo di adesso da cui prende posto, titolo e metadati
    content: list[int]  #: i capitoli di adesso il cui contenuto finisce qui
    part: int = 0  #: per la divisione: 1 o 2
    moved: bool = False


def validate(op: Operation, numeri: list[int], viste: list[ChapterView] | None = None
             ) -> list[str]:
    """Gli errori di forma: un'operazione che non si può nemmeno simulare."""
    errori = []
    c = op.chapters
    attesi = 2 if op.kind == MERGE else 1
    if op.kind not in KINDS:
        return [f"operazione sconosciuta: {op.kind}"]
    if len(c) != attesi or len(set(c)) != len(c):
        return [f"servono {attesi} capitoli distinti"]
    mancano = [n for n in c if n not in numeri]
    if mancano:
        return [f"capitolo {', '.join(map(str, mancano))} non nella struttura"]
    if op.kind == REORDER:
        if op.after and op.after not in numeri:
            errori.append(f"il capitolo {op.after} non è nella struttura")
        elif op.after == c[0]:
            errori.append("un capitolo non si sposta dopo sé stesso")
        else:
            i = numeri.index(c[0])
            if (numeri[i - 1] if i else 0) == op.after:
                errori.append("il capitolo è già in quella posizione")
    if op.kind == CUT and op.destination:
        if op.destination == c[0]:
            errori.append("la destinazione non può essere il capitolo tagliato")
        elif op.destination not in numeri:
            errori.append(f"la destinazione {op.destination} non è nella struttura")
    if op.kind == SPLIT and viste is not None:
        v = next((x for x in viste if x.number == c[0]), None)
        if v is None or not v.written:
            errori.append("si divide solo un capitolo già scritto")
        elif split_index(v.text, op.split_at) is None:
            errori.append(f"la sezione «{op.split_at}» non è fra quelle del capitolo "
                          "(la seconda parte comincia a una sezione che non sia la prima)")
    return errori


def transform(op: Operation, numeri: list[int]) -> list[Slot]:
    """Il libro dopo l'operazione, come sequenza di capitoli (vedi :class:`Slot`)."""
    posti = [Slot(n, [n]) for n in numeri]

    def dove(n: int) -> int:
        return next(i for i, s in enumerate(posti) if s.base == n)

    if op.kind == REORDER:
        s = posti.pop(dove(op.chapters[0]))
        s.moved = True
        posti.insert(0 if not op.after else dove(op.after) + 1, s)
    elif op.kind == CUT:
        posti.pop(dove(op.chapters[0]))
        if op.destination:
            posti[dove(op.destination)].content.append(op.chapters[0])
    elif op.kind == MERGE:
        a, b = sorted(op.chapters[:2])
        posti[dove(a)].content = [a, b]
        posti.pop(dove(b))
    elif op.kind == SPLIT:
        i = dove(op.chapters[0])
        posti[i] = Slot(op.chapters[0], [op.chapters[0]], part=1)
        posti.insert(i + 1, Slot(op.chapters[0], [op.chapters[0]], part=2))
    return posti


def check(op: Operation, viste: list[ChapterView], dip: Dependencies | None = None
          ) -> tuple[list[str], list[str]]:
    """I controlli deterministici: ``(blocker, avvisi)``. Non chiedono niente a nessun modello.

    - un nome introdotto in un capitolo e usato dopo non può finire dopo chi lo usa
      (riordino, fusione di capitoli lontani, destinazione di un taglio troppo avanti);
    - un taglio non può perdere contenuto unico (nomi usati dopo, o quello che il modello stesso
      dice unico) senza una destinazione;
    - le date esplicite non devono invertirsi (avviso: un flashback può essere voluto).
    """
    numeri = [v.number for v in viste]
    errori = validate(op, numeri, viste)
    if errori:
        return errori, []
    dip = dip or dependencies(viste)
    blocker: list[str] = []
    avvisi: list[str] = []
    posti = transform(op, numeri)
    pos = {n: i for i, s in enumerate(posti) for n in s.content}
    toccati = set(op.chapters) | ({op.destination} if op.destination else set())

    for k, utenti in sorted(dip.users.items()):
        if len(utenti) < 2:
            continue
        intro = min(utenti)
        presenti = [u for u in utenti if u in pos]
        if intro not in pos:
            dopo = sorted(u for u in presenti if u != intro)
            if dopo:
                blocker.append(f"«{dip.labels[k]}» è presentato nel capitolo {intro}, che si "
                               f"toglie senza destinazione, ma lo usano i capitoli "
                               f"{', '.join(map(str, dopo))}")
            continue
        prima = sorted(u for u in presenti if pos[u] < pos[intro])
        if prima:
            if op.kind == CUT and intro == op.chapters[0]:
                blocker.append(f"«{dip.labels[k]}», presentato nel capitolo {intro}, finirebbe "
                               f"nel {op.destination}: troppo tardi, lo usano già i capitoli "
                               f"{', '.join(map(str, prima))}")
            else:
                blocker.append(f"«{dip.labels[k]}» è presentato nel capitolo {intro} ma il "
                               f"capitolo {', '.join(map(str, prima))} lo usa: dopo "
                               "l'operazione il lettore lo incontrerebbe prima della sua "
                               "presentazione")

    if op.kind == CUT:
        x = op.chapters[0]
        if op.unique_content and not op.destination:
            blocker.append("il capitolo ha contenuto unico (" + "; ".join(
                _cut(u, 80) for u in op.unique_content[:4]) + ") e nessuna destinazione")
        solo_qui = sorted(dip.labels[k] for k, u in dip.users.items() if u == {x})
        if solo_qui:
            avvisi.append("spariscono dal libro: " + ", ".join(solo_qui[:10]))
        v = next(v for v in viste if v.number == x)
        if v.written and v.words:
            avvisi.append(f"si tolgono {v.words} parole scritte (restano nella copia di "
                          "sicurezza)")

    if op.kind == MERGE:
        a, b = sorted(op.chapters)
        if numeri.index(b) - numeri.index(a) != 1:
            avvisi.append(f"i capitoli {a} e {b} non sono consecutivi: il contenuto del {b} "
                          "passa davanti ai capitoli in mezzo")
        va, vb = (next(v for v in viste if v.number == n) for n in (a, b))
        bilanci = sorted(v.budget for v in viste if v.budget)
        mediana = bilanci[len(bilanci) // 2] if bilanci else 0
        if mediana and va.words + vb.words > 1.8 * mediana:
            avvisi.append(f"il capitolo unito avrebbe circa {va.words + vb.words} parole, quasi "
                          f"il doppio della misura tipica ({mediana})")

    if op.kind == SPLIT:
        v = next(v for v in viste if v.number == op.chapters[0])
        i = split_index(v.text, op.split_at)
        if i is not None:
            cap = parse(v.text)
            prima = sum(s.words for s in cap.sections[:i])
            dopo = sum(s.words for s in cap.sections[i:])
            if min(prima, dopo) < 400:
                avvisi.append(f"una delle due parti avrebbe solo {min(prima, dopo)} parole")

    # la cronologia esplicita: le coppie che l'operazione inverte (il contenuto di un capitolo
    # unito o tagliato si conta dove finisce)
    ordine = [n for s in posti if s.part != 2 for n in s.content]
    datati = [n for n in ordine if n in dip.dates]
    for i, a in enumerate(datati):
        for b in datati[i + 1:]:
            if numeri.index(a) > numeri.index(b) and dip.dates[a] > dip.dates[b] \
                    and ({a, b} & toccati):
                avvisi.append(f"cronologia: il capitolo {a} ({dip.date_labels[a]}) verrebbe "
                              f"prima del {b} ({dip.date_labels[b]}) — va bene solo se è un "
                              "flashback voluto")
    return blocker, avvisi


# --------------------------------------------------------------------------- #
#  la diagnosi
# --------------------------------------------------------------------------- #
def _system(kind: str, explain_language: str, massimo: int) -> str:
    return (
        f"Sei un editor di struttura. Guardi un libro intero ({ql.KINDS.get(kind, kind)}) "
        "dall'alto, attraverso le schede dei capitoli — non il testo — e proponi soltanto le "
        "operazioni sulla STRUTTURA che risolvono un problema reale, documentato dalle schede.\n\n"
        "Le operazioni ('kind'):\n"
        "- 'reorder': spostare il capitolo indicato in 'chapters' (uno solo) subito dopo il "
        "capitolo 'after' (0 = in testa al libro), quando l'ordine attuale racconta un effetto "
        "prima della sua causa, rompe la cronologia senza motivo, o mette un capitolo dove il "
        "lettore non ha ancora quello che gli serve. In 'reason' la ragione causale o "
        "cronologica.\n"
        "- 'cut': togliere il capitolo indicato perché la sua funzione è già svolta da un altro. "
        "In 'unique_content' quello che ha di unico e non va perso (fatti, scene, nomi, indizi), "
        "in 'destination' il capitolo che lo accoglie (0 solo se non ha niente di unico).\n"
        "- 'merge': fondere i due capitoli in 'chapters' (di solito consecutivi) quando sono due "
        "metà della stessa funzione, o uno è troppo scarno per reggersi da solo; in "
        "'new_titles' il titolo del capitolo unito.\n"
        "- 'split': dividere il capitolo indicato in due quando porta due funzioni distinte ed è "
        "troppo lungo; in 'split_at' il titolo della sezione (fra quelle elencate nella scheda, "
        "non la prima) da cui comincia la seconda parte, in 'new_titles' i titoli delle due "
        "parti.\n\n"
        "Per ogni operazione: 'issue' il problema che risolve; 'evidence' le prove dalle schede "
        "(quali capitoli, quali fatti, chi sa cosa e da quando); 'risk' cosa si rischia; 'breaks' "
        "le dipendenze che rompe (chi sa cosa, oggetti, nomi introdotti, cronologia, fili "
        "aperti) — vuoto solo se non ne rompe davvero.\n\n"
        "Non proporre operazioni per gusto: un libro con una struttura sana riceve una lista "
        f"vuota. Al massimo {massimo} operazioni, le più utili prima. I campi che non "
        "riguardano l'operazione restano vuoti (0, \"\", []). Usa i numeri dei capitoli come "
        "compaiono nelle schede. In 'assessment' il giudizio sulla struttura in due o tre "
        f"frasi. Lingua delle spiegazioni: {explain_language}."
    )


def _normalise(raw: Any, numeri: list[int]) -> Operation | None:
    """Una proposta del modello, ripulita; None se non si capisce che cosa propone."""
    if not isinstance(raw, dict):
        return None
    tipo = _KIND_ALIASES.get(str(raw.get("kind") or "").strip().lower())
    if tipo is None:
        return None
    capitoli = []
    for c in raw.get("chapters") or []:
        n = _int(c)
        if n in numeri and n not in capitoli:
            capitoli.append(n)
    if tipo == MERGE:
        capitoli = sorted(capitoli[:2])
    else:
        capitoli = capitoli[:1]

    def testi(chiave: str, limite: int) -> list[str]:
        return [_cut(x, 300) for x in raw.get(chiave) or [] if str(x).strip()][:limite]

    op = Operation(kind=tipo, chapters=capitoli,
                   after=_int(raw.get("after")) if tipo == REORDER else 0,
                   destination=_int(raw.get("destination")) if tipo == CUT else 0,
                   unique_content=testi("unique_content", 8) if tipo == CUT else [],
                   new_titles=testi("new_titles", 2) if tipo in (MERGE, SPLIT) else [],
                   split_at=_cut(raw.get("split_at"), 200).lstrip("# ").strip()
                   if tipo == SPLIT else "",
                   issue=_cut(raw.get("issue"), 600), evidence=_cut(raw.get("evidence"), 900),
                   reason=_cut(raw.get("reason"), 600), risk=_cut(raw.get("risk"), 500),
                   breaks=testi("breaks", 8))
    if op.destination and op.destination not in numeri:
        op.destination = 0
    if validate(op, numeri):
        return None
    return op


def _refs(op: Operation, titoli: dict[int, str]) -> list[dict[str, Any]]:
    nums = list(op.chapters) + [n for n in (op.after, op.destination) if n]
    return [{"number": n, "title": titoli.get(n, "")} for n in dict.fromkeys(nums)]


def diagnose(project: BookProject, state: ForgeState, client: Any, *, model: str | None = None,
             kind: str = "romanzo", explain_language: str = "italiano", progress=None, note=None,
             should_stop=None) -> Round:
    """Una diagnosi della struttura: proposte nuove, controllate, salvate come un giro nuovo.

    Le proposte ancora aperte dei giri prima, se solo proposte, diventano «superate» (la nuova
    diagnosi guarda lo stesso libro) — ma solo se la diagnosi è completa; quelle accettate
    restano, e una proposta uguale a una che l'autore ha già rifiutato non si ripete. Un errore
    del modello non ferma il giro: resta scritto negli errori.
    """
    viste = chapter_views(project, state)
    if len(viste) < 2:
        raise ValueError("Per ragionare sulla struttura servono almeno due capitoli.")
    numeri = [v.number for v in viste]
    titoli = {v.number: v.title for v in viste}
    giro = Round(created=_now(), model=model or "", kind=kind,
                 basis=[{"number": v.number, "title": v.title, "words": v.words} for v in viste])
    qualita, giro.quality = quality_block(project, viste)
    scopo = ql.purpose_block(kind, nb.normalise(state.notebook))
    senza_scheda = [v.number for v in viste if v.written and v.entry is None]
    if senza_scheda:
        giro.notes.append("capitoli scritti senza scheda nel registro (prove deboli): "
                          + ", ".join(map(str, senza_scheda)))
    scritti = project.written_chapters()
    fuori = sorted(set(scritti) - set(numeri))
    if fuori:
        giro.notes.append("file di capitoli fuori dalla struttura: "
                          + ", ".join(scritti[n].name for n in fuori))

    # le chiamate: il libro intero se è corto; altrimenti una passata d'insieme e le finestre
    chiamate: list[tuple[str, str]] = []
    if len(viste) <= WINDOW:
        chiamate.append(("libro", "# Le schede dei capitoli\n\n"
                         + "\n\n".join(digest(v) for v in viste)))
    else:
        chiamate.append(("insieme", "# Il libro intero, in breve\n\nGuarda soprattutto le "
                         "operazioni a distanza: capitoli lontani che fanno lo stesso lavoro, "
                         "cause raccontate molto dopo i loro effetti.\n\n"
                         + "\n\n".join(digest(v, full=False) for v in viste)))
        inizio = 0
        while inizio < len(viste):
            finestra = viste[inizio:inizio + WINDOW]
            resto = [v for v in viste if v not in finestra]
            chiamate.append((
                f"capitoli {finestra[0].number}–{finestra[-1].number}",
                f"# Una parte del libro: capitoli {finestra[0].number}–{finestra[-1].number} "
                f"di {len(viste)}\n\nProponi solo operazioni fra questi capitoli.\n\n"
                + "\n\n".join(digest(v) for v in finestra)
                + "\n\n# Il resto del libro (solo i titoli)\n"
                + "; ".join(f"{v.number}. {v.title}" for v in resto)))
            if inizio + WINDOW >= len(viste):
                break
            inizio += WINDOW_STEP

    sistema = _system(kind, explain_language, MAX_OPS_PER_CALL)
    proposte: list[Operation] = []
    giudizi = []
    completa = True  #: tutte le chiamate fatte e riuscite: solo allora le vecchie proposte decadono
    for i, (etichetta, schede) in enumerate(chiamate):
        if should_stop and should_stop():
            giro.notes.append("diagnosi interrotta: proposte parziali")
            completa = False
            break
        if progress:
            progress(i, len(chiamate))
        if note:
            note(f"Struttura: guardo {etichetta}…")
        utente = "\n\n".join(p for p in (scopo, qualita, schede) if p)
        giro.calls += 1
        try:
            r = client.chat_json([{"role": "system", "content": sistema},
                                  {"role": "user", "content": utente}],
                                 schema=DIAGNOSIS_SCHEMA, model=model)
        except Exception as exc:  # noqa: BLE001 - un errore resta scritto, il giro continua
            giro.errors.append(f"{etichetta}: {_cut(exc, 200)}")
            completa = False
            continue
        if not isinstance(r, dict):
            giro.errors.append(f"{etichetta}: risposta non leggibile")
            completa = False
            continue
        if str(r.get("assessment") or "").strip():
            giudizi.append(_cut(r.get("assessment"), 600))
        valide = [op for op in (_normalise(g, numeri) for g in r.get("operations") or [])
                  if op is not None]
        proposte += valide[:2 * MAX_OPS_PER_CALL]  # il margine: le doppie si tolgono dopo
    if progress:
        progress(len(chiamate), len(chiamate))
    giro.assessment = "\n\n".join(giudizi)

    # le doppie, le già rifiutate, le già accettate. Il file si rilegge adesso: mentre il modello
    # pensava, l'autore può aver deciso altre proposte
    dati = load(project)
    giro.id = str(len(dati.rounds) + 1)
    rifiutate = {op.signature for op in dati.operations() if op.status == RIFIUTATA}
    accettate = {op.signature for op in dati.operations() if op.status == ACCETTATA}
    viste_firme: set[tuple] = set()
    ripetute = 0
    dip = dependencies(viste)
    for op in proposte:
        if op.signature in viste_firme or op.signature in accettate:
            continue
        if op.signature in rifiutate:
            ripetute += 1
            continue
        viste_firme.add(op.signature)
        op.id = f"{giro.id}.{len(giro.operations) + 1}"
        op.refs = _refs(op, titoli)
        op.blockers, op.warnings = check(op, viste, dip)
        op.remember("proposta")
        giro.operations.append(op)
        if len(giro.operations) >= MAX_OPS:
            break
    if ripetute:
        giro.notes.append(f"{ripetute} proposte già rifiutate dall'autore non ripetute")
    if completa:
        for op in dati.operations():
            if op.status == PROPOSTA:
                op.status = SUPERATA
                op.remember(f"superata dalla diagnosi {giro.id}")
    else:
        giro.notes.append("diagnosi incompleta: le proposte dei giri prima restano aperte")
    dati.rounds.append(giro)
    save(project, dati)
    return giro


# --------------------------------------------------------------------------- #
#  la decisione dell'autore
# --------------------------------------------------------------------------- #
ACCEPT, REJECT, RESET = "accept", "reject", "reset"


def refresh_checks(project: BookProject, state: ForgeState, dati: StructureData) -> None:
    """Rifà i controlli deterministici delle proposte aperte sul libro di adesso."""
    viste = chapter_views(project, state)
    dip = dependencies(viste)
    for op in dati.open():
        op.blockers, op.warnings = check(op, viste, dip)


def decide(project: BookProject, state: ForgeState, op_id: str, decision: str, *,
           note: str = "", waiver: str = "", destination: int | None = None) -> Operation:
    """La decisione dell'autore su una proposta: ``accept``, ``reject`` o ``reset`` (torna
    proposta). ``destination``: per un taglio, il capitolo che accoglie il contenuto unico (si
    ricontrolla). Un'operazione con blocker si accetta solo con ``waiver``, la deroga motivata.
    """
    dati = load(project)
    op = dati.operation(op_id)
    if op is None:
        raise ValueError(f"Nessuna proposta {op_id}.")
    if op.status in (APPLICATA, SUPERATA):
        raise ValueError(f"La proposta {op_id} è {op.status}: non si decide più.")
    if decision == ACCEPT and _stale(op, state):
        raise ValueError("La struttura è cambiata dopo questa proposta: rifai la diagnosi.")
    viste = chapter_views(project, state)
    if destination is not None and op.kind == CUT and int(destination) != op.destination:
        if destination and int(destination) not in {v.number for v in viste}:
            raise ValueError(f"Il capitolo {destination} non è nella struttura.")
        prima = op.destination
        op.destination = int(destination)
        titoli = {v.number: v.title for v in viste}
        op.refs = [r for r in op.refs if _int(r.get("number")) != prima
                   or _int(r.get("number")) in op.chapters]
        if op.destination:
            op.refs.append({"number": op.destination, "title": titoli.get(op.destination, "")})
        op.remember(f"destinazione scelta dall'autore: {op.destination or 'nessuna'}")
    op.blockers, op.warnings = check(op, viste)
    if note.strip():
        op.note = note.strip()
    if decision == ACCEPT:
        errori = validate(op, [v.number for v in viste], viste)
        if errori:  # un'operazione impossibile non si deroga
            raise ValueError("La proposta non si può applicare: " + "; ".join(errori))
        if op.blockers and not waiver.strip():
            raise ValueError("La proposta ha controlli bloccanti: per accettarla serve la tua "
                             "deroga motivata.\n- " + "\n- ".join(op.blockers))
        op.waiver = waiver.strip() if op.blockers else ""
        op.status = ACCETTATA
        op.remember("accettata" + (f" con deroga: {op.waiver}" if op.waiver else ""))
    elif decision == REJECT:
        op.status = RIFIUTATA
        op.remember("rifiutata" + (f": {op.note}" if op.note else ""))
    elif decision == RESET:
        op.status, op.waiver = PROPOSTA, ""
        op.remember("riportata a proposta")
    else:
        raise ValueError(f"Decisione sconosciuta: {decision}")
    save(project, dati)
    return op


def _stale(op: Operation, state: ForgeState) -> bool:
    """La proposta parla di capitoli che non sono più quelli (numero o titolo cambiati)."""
    titoli = {_int(c.get("number")): str(c.get("title") or "") for c in state.outline}
    return any(titoli.get(_int(r.get("number"))) != str(r.get("title") or "") for r in op.refs)


# --------------------------------------------------------------------------- #
#  il testo: intestazioni, fusione, divisione
# --------------------------------------------------------------------------- #
_TESTA = re.compile(r"^#\s+(?P<w>Capitolo|Chapter|Chapitre|Kapitel|Cap[ií]tulo)\s+"
                    r"(?P<n>\d+|[IVXLCDM]+)\b\s*(?P<sep>[:.\-–—]?)\s*(?P<t>.*)$", re.IGNORECASE)


def _roman(n: int) -> str:
    valori = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
              (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    fuori = ""
    for v, s in valori:
        while n >= v:
            fuori, n = fuori + s, n - v
    return fuori


def retitle(text: str, number: int, title: str, *, new_title: bool = False) -> str:
    """L'intestazione del capitolo segue il numero (e il titolo, se è cambiato). Un titolo senza
    numero («# La porta») si tocca solo se il titolo cambia; un capitolo senza intestazione resta
    com'è: non si inventa una forma che il libro non usa."""
    righe = (text or "").split("\n")
    i = next((k for k, r in enumerate(righe) if r.strip()), None)
    if i is None or not righe[i].startswith("# "):
        return text
    if m := _TESTA.match(righe[i].strip()):
        n = _roman(number) if re.fullmatch(r"[IVXLCDM]+", m.group("n")) else str(number)
        righe[i] = f"# {m.group('w')} {n}{m.group('sep') or ':'} {title}".rstrip()
    elif new_title:
        righe[i] = f"# {title}"
    return "\n".join(righe)


def _heading_like(testo: str, numero: int, titolo: str) -> str:
    """L'intestazione di un capitolo nuovo (la seconda parte di una divisione) nella forma di
    quella del capitolo da cui viene."""
    prima = next((r for r in (testo or "").splitlines() if r.strip()), "")
    if _TESTA.match(prima.strip()):
        return retitle(prima.strip(), numero, titolo, new_title=True)
    return f"# {titolo}"


def split_index(testo: str, split_at: str) -> int | None:
    """L'indice della sezione da cui comincia la seconda parte (mai la prima)."""
    voluto = _key(str(split_at or "").lstrip("# "))
    if not voluto:
        return None
    cap = parse(testo or "")
    for i, s in enumerate(cap.sections):
        if i == 0 or not s.heading:
            continue
        if _key(s.heading.lstrip("# ")) == voluto:
            return i
    for i, s in enumerate(cap.sections):  # un titolo citato a metà dal modello
        if i and s.heading and voluto in _key(s.heading.lstrip("# ")):
            return i
    return None


def split_text(testo: str, indice: int, numero: int, titoli: tuple[str, str]
               ) -> tuple[str, str]:
    """Il capitolo diviso alla sezione ``indice``. La bibliografia va in tutt'e due le parti:
    meglio una nota di troppo da togliere che una citazione senza fonte."""
    cap = parse(testo)
    testa1 = retitle(cap.head, numero, titoli[0], new_title=True) if cap.head else ""
    testa2 = _heading_like(cap.head or testo, numero + 1, titoli[1]) if cap.head else \
        f"# {titoli[1]}"
    prima = Chapter(head=testa1, sections=cap.sections[:indice], notes=cap.notes)
    seconda = Chapter(head=testa2, sections=cap.sections[indice:], notes=cap.notes)
    return prima.render(), seconda.render()


def _seam_parts(paragrafi: list[str], dalla_fine: bool) -> int:
    """Quanti paragrafi entrano nella giunzione: al più SEAM_PARAGRAPHS e SEAM_WORDS parole."""
    presi, parole = 0, 0
    sequenza = reversed(paragrafi) if dalla_fine else iter(paragrafi)
    for p in sequenza:
        w = mx.count_words(p)
        if presi and parole + w > SEAM_WORDS:
            break
        presi, parole = presi + 1, parole + w
        if presi >= SEAM_PARAGRAPHS:
            break
    return presi


def _bridge(client: Any, coda: list[str], testa: list[str], *, model: str | None,
            language: str, titoli: tuple[str, str], riassunti: tuple[str, str]) -> str | None:
    """Il punto di giunzione riscritto: la fine del primo capitolo e l'inizio del secondo
    diventano un passaggio continuo. None se il modello non risponde o risponde fuori misura."""
    parole = mx.count_words("\n\n".join(coda + testa))
    sistema = (
        "Due capitoli consecutivi di un libro diventano un capitolo solo. Riscrivi SOLTANTO il "
        "punto di giunzione: gli ultimi paragrafi del primo capitolo e i primi del secondo, "
        "qui sotto, diventano un passaggio continuo. Togli quello che serviva solo a chiudere o "
        "ad aprire un capitolo (riepiloghi, presentazioni ripetute, sospensioni che il seguito "
        "risolve subito); conserva ogni fatto, nome, oggetto, dialogo e indizio che serve al "
        "seguito. Stessa voce, stesso tempo verbale, stessa persona. Lunghezza simile: circa "
        f"{parole} parole. Rispondi solo con la prosa, a paragrafi separati da una riga vuota, "
        f"senza titoli e senza commenti. Lingua: {language}."
    )
    utente = "\n\n".join(p for p in (
        f"# Il primo capitolo: «{titoli[0]}»\n{riassunti[0]}" if riassunti[0] else "",
        f"# Il secondo capitolo: «{titoli[1]}»\n{riassunti[1]}" if riassunti[1] else "",
        "# La fine del primo capitolo\n\n" + "\n\n".join(coda),
        "# L'inizio del secondo capitolo\n\n" + "\n\n".join(testa)) if p)
    try:
        risposta = client.chat([{"role": "system", "content": sistema},
                                {"role": "user", "content": utente}], model=model)
    except Exception:  # noqa: BLE001 - senza giunzione si uniscono i testi così come sono
        return None
    testo = "\n".join(r for r in str(risposta or "").splitlines()
                      if not r.lstrip().startswith("#")).strip()
    scritte = mx.count_words(testo)
    if not testo or scritte < 0.5 * parole or scritte > 1.6 * parole + 40:
        return None
    return testo


def merge_text(client: Any, a: str, b: str, *, numero: int, titolo: str, model: str | None,
               language: str, titoli: tuple[str, str], riassunti: tuple[str, str]
               ) -> tuple[str, list[str]]:
    """Il capitolo unito: tutto il primo, tutto il secondo, e la giunzione riscritta.

    Riscrivere i due capitoli per intero starebbe ben oltre quello che un modello locale scrive
    in una risposta, e rifarebbe un testo già rivisto: si riscrive solo dove i due si toccano.
    Senza modello, o se la giunzione non torna, i testi si uniscono come sono e si dice.
    """
    avvisi: list[str] = []
    pa, pb = parse(a or ""), parse(b or "")
    testa = retitle(pa.head, numero, titolo, new_title=True) if pa.head else \
        (f"# {titolo}" if pb.head else "")
    # una bibliografia sola, in fondo: quella del primo, poi le voci nuove del secondo
    note = pa.notes.strip() or pb.notes.strip()
    if pa.notes.strip() and pb.notes.strip():
        gia = {r.strip() for r in pa.notes.splitlines() if r.strip()}
        nuove = [r for r in pb.notes.strip().splitlines()[1:] if r.strip() and r.strip() not in gia]
        if nuove:
            note += "\n\n" + "\n\n".join(nuove)
    sezioni_a = [Section(s.heading, list(s.paragraphs)) for s in pa.sections]
    sezioni_b = [Section(s.heading, list(s.paragraphs)) for s in pb.sections]
    giunzione = None
    if client is not None and sezioni_a and sezioni_b and sezioni_a[-1].paragraphs \
            and sezioni_b[0].paragraphs:
        k_a = _seam_parts(sezioni_a[-1].paragraphs, True)
        k_b = _seam_parts(sezioni_b[0].paragraphs, False)
        coda, apertura = sezioni_a[-1].paragraphs[-k_a:], sezioni_b[0].paragraphs[:k_b]
        giunzione = _bridge(client, coda, apertura, model=model, language=language,
                            titoli=titoli, riassunti=riassunti)
        if giunzione is not None:
            ultima = sezioni_a[-1]
            ultima.paragraphs = ultima.paragraphs[:-k_a] + [
                p.strip() for p in re.split(r"\n\s*\n", giunzione) if p.strip()]
            ultima.paragraphs += sezioni_b[0].paragraphs[k_b:]
            sezioni = sezioni_a + sezioni_b[1:]
    if giunzione is None:
        avvisi.append("la giunzione fra i due capitoli non è stata riscritta: i testi sono "
                      "uniti così come sono, con un cambio di sezione")
        if sezioni_b and not sezioni_b[0].heading:
            sezioni_b[0].heading = f"## {titoli[1]}"
        sezioni = sezioni_a + sezioni_b
    return Chapter(head=testa, sections=sezioni, notes=note).render(), avvisi


# --------------------------------------------------------------------------- #
#  l'applicazione: un piano di file, una copia di sicurezza, poi i cambiamenti
# --------------------------------------------------------------------------- #
@dataclass
class ApplyResult:
    op: Operation
    run: Run
    outline: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class UndoResult:
    run: Run
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:20]
    except OSError:
        return ""


class _Transazione:
    """Ogni file che si tocca, copiato prima; alla fine, l'impronta di com'è diventato."""

    def __init__(self, project: BookProject, run: str) -> None:
        self.project = project
        self.dir = backups_dir(project) / run
        self.files = self.dir / "files"
        self.existed: list[str] = []
        self.created: list[str] = []

    def rel(self, path: Path) -> str:
        return Path(path).resolve().relative_to(self.project.root.resolve()).as_posix()

    def protect(self, paths) -> None:
        for p in paths:
            r = self.rel(p)
            if r in self.existed or r in self.created:
                continue
            if Path(p).exists():
                copia = self.files / r
                copia.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, copia)
                self.existed.append(r)
            else:
                self.created.append(r)

    def hashes(self) -> dict[str, str]:
        return {r: _hash_file(self.project.root / r) for r in self.existed + self.created}

    def rollback(self) -> None:
        """Rimette tutto com'era: un lavoro fallito a metà non lascia il libro a metà."""
        _restore(self.project, self.files, self.existed, self.created)


def _restore(project: BookProject, copie: Path, existed: list[str], created: list[str]
             ) -> tuple[list[str], list[str]]:
    """Toglie i file creati e rimette quelli che c'erano dalla copia. Ritorna (rimessi, tolti)."""
    rimessi, tolti = [], []
    radice = project.root.resolve()
    for r in created:
        p = project.root / r
        if p.exists():
            p.unlink()
            tolti.append(r)
            # le cartelle nate con lui (le versioni di un capitolo rinominato), se restano vuote
            cartella = p.parent.resolve()
            while cartella.parent != radice and cartella != radice and cartella.exists() \
                    and not any(cartella.iterdir()):
                cartella.rmdir()
                cartella = cartella.parent
    for r in existed:
        copia = copie / r
        if copia.exists():
            destinazione = project.root / r
            destinazione.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(copia, destinazione)
            rimessi.append(r)
    return rimessi, tolti


def _artefacts(project: BookProject, numero: int, titolo: str, stem: str) -> dict[str, Path]:
    """I file che appartengono a un capitolo, oltre al capitolo: ricerca, piano delle scene,
    cronologia (per numero e titolo della struttura), storia della qualità (per il nome del
    file del capitolo)."""
    base = f"{numero:02d}_{slugify(titolo)}"
    trovati = {
        "research_md": project.research_dir / f"{base}.md",
        "research_json": project.research_dir / f"{base}.json",
        "plan": project.plans_dir / f"{base}.json",
        "timeline": project.story_dir / "timeline" / f"{base}.json",
    }
    if stem:
        trovati["quality"] = ql.history_path(project, stem)
    return {k: p for k, p in trovati.items() if p.exists()}


def _versions(project: BookProject, stem: str) -> list[Path]:
    cartella = ql.versions_dir(project, stem) if stem else None
    if cartella is None or not cartella.exists():
        return []
    return sorted(p for p in cartella.rglob("*") if p.is_file())


def _renumber_json(raw: bytes, vecchio: int, nuovo: int, titolo_vecchio: str, titolo: str
                   ) -> bytes:
    """Un file di dati di un capitolo che cambia numero (o titolo): i campi che lo nominano."""
    try:
        dati = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw
    if not isinstance(dati, dict):
        return raw
    for chiave in ("number", "chapter"):
        if _int(dati.get(chiave)) == vecchio and not isinstance(dati.get(chiave), bool):
            dati[chiave] = nuovo
    if titolo != titolo_vecchio and str(dati.get("title") or "") == titolo_vecchio:
        dati["title"] = titolo
    for q in dati.get("questions") or []:
        if isinstance(q, dict) and _int(q.get("chapter")) == vecchio:
            q["chapter"] = nuovo
    return json.dumps(dati, ensure_ascii=False, indent=2).encode("utf-8")


def _quality_json(raw: bytes, vecchio_hash: str, nuovo_hash: str, numero: int, titolo: str,
                  conserva: bool) -> bytes:
    """La storia della qualità di un capitolo che cambia solo numero: l'ultima valutazione vale
    ancora (stesso testo, stesso capitolo davanti) e si aggiorna l'impronta; altrimenti resta
    vecchia e il capitolo si rivaluta."""
    try:
        dati = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return raw
    giri = dati.get("rounds") if isinstance(dati, dict) else None
    if not giri or not isinstance(giri[-1], dict):
        return raw
    ultimo = giri[-1]
    if conserva and ultimo.get("text_hash") == vecchio_hash:
        ultimo["text_hash"] = nuovo_hash
    ultimo["chapter"], ultimo["title"] = numero, titolo
    return json.dumps(dati, ensure_ascii=False, indent=1).encode("utf-8")


def _spec_table(testo: str, outline: list[dict[str, Any]]) -> str | None:
    """La tabella della struttura in ``MASTER_SPEC.md`` riscritta con la struttura nuova (e i
    punti chiave). Il resto della specifica resta parola per parola. None se la tabella non si
    trova: allora la specifica non si tocca e lo si dice."""
    from bookwriter.forge import spec as sp

    righe = testo.splitlines()
    inizio = next((i for i, r in enumerate(righe) if r.strip().startswith("| # |")), None)
    if inizio is None:
        return None
    # i titoli delle parti, sopra la prima tabella, fanno parte della struttura
    while inizio > 0 and (righe[inizio - 1].strip().startswith("### Parte")
                          or not righe[inizio - 1].strip()):
        inizio -= 1
    while inizio < len(righe) and not righe[inizio].strip():
        inizio += 1
    fine = next((i for i in range(inizio, len(righe))
                 if righe[i].strip().startswith("**Pagine iniziali:**")), None)
    if fine is None:
        fine = next((i + 1 for i in range(inizio, len(righe))
                     if righe[i].strip().startswith("**Totale previsto:**")), None)
    if fine is None:
        return None
    parti: dict[int, str] = {}
    for r in righe[inizio:fine]:
        if m := re.match(r"^###\s+Parte\s+(\d+)\s*(?:—\s*(.*))?$", r.strip()):
            parti[int(m.group(1))] = (m.group(2) or "").strip()

    def cella(x: Any) -> str:
        return " ".join(str(x or "").replace("|", "/").split())

    nuove: list[str] = []
    numeri_parti = sorted({_int(c.get("part"), 1) or 1 for c in outline})
    for p in numeri_parti:
        if len(parti) > 1 or parti.get(p):
            nuove += [f"### Parte {p} — {parti.get(p, '')}".rstrip(" —"), ""]
        nuove += ["| # | Capitolo | Cosa fa | Parole |", "|---|---|---|---|"]
        nuove += [f"| {c.get('number')} | {cella(c.get('title'))} | {cella(c.get('does'))} | "
                  f"{_int(c.get('words'))} |"
                  for c in outline if (_int(c.get("part"), 1) or 1) == p]
        nuove.append("")
    totale = next((r for r in righe[inizio:fine] if r.strip().startswith("**Totale previsto")),
                  None)
    if totale is not None:
        obiettivo = re.search(r"\(obiettivo [^)]*\)", totale)
        nuove.append(f"**Totale previsto:** "
                     f"{sp.fmt_words(sum(_int(c.get('words')) for c in outline))} parole"
                     + (f" {obiettivo.group(0)}" if obiettivo else ""))
    if any(c.get("points") for c in outline):
        nuove += ["", f"### {sp.POINTS_SECTION}", "",
                  "Le scene, i passaggi o gli argomenti che ogni capitolo porta. Si cercano le",
                  "fonti e si scrive punto per punto: un punto per riga, modificabili a mano.", ""]
        for c in outline:
            if c.get("points"):
                nuove += [f"#### Capitolo {c.get('number')} — {c.get('title')}", ""]
                nuove += [f"- {p}" for p in c["points"]]
                nuove.append("")
    while nuove and not nuove[-1].strip():
        nuove.pop()
    if fine < len(righe) and righe[fine].strip():
        nuove.append("")
    return "\n".join(righe[:inizio] + nuove + righe[fine:]) + ("\n" if testo.endswith("\n")
                                                              else "")


def _remap(numero: int, mappa: dict[int, int | None]) -> int | None:
    return mappa.get(numero, numero)


@dataclass
class _Libro:
    """Il libro com'è prima dell'operazione, letto una volta: struttura, testi, registro."""

    project: BookProject
    outline: list[dict[str, Any]]
    numeri: list[int]
    per_numero: dict[int, dict[str, Any]]
    scritti: dict[int, Path]
    testi: dict[int, str]
    registro: Any


def _read_book(project: BookProject, state: ForgeState) -> _Libro:
    outline = copy.deepcopy(_outline(state))
    numeri = [_int(c.get("number")) for c in outline]
    scritti = project.written_chapters()
    fuori = sorted(set(scritti) - set(numeri))
    if fuori:
        raise ValueError("Ci sono file di capitoli fuori dalla struttura ("
                         + ", ".join(scritti[n].name for n in fuori)
                         + "): allinea struttura e capitoli prima di cambiarla.")
    if numeri != list(range(1, len(numeri) + 1)):
        raise ValueError("La struttura non è numerata 1, 2, 3…: allineala prima di cambiarla.")
    return _Libro(project=project, outline=outline, numeri=numeri,
                  per_numero={_int(c.get("number")): c for c in outline}, scritti=scritti,
                  testi={n: scritti[n].read_text(encoding="utf-8") for n in numeri
                         if n in scritti},
                  registro=lg.load(project))


def _split_words(testo: str, indice: int, parte: int) -> float:
    """La quota delle parole del capitolo che va in una delle due parti."""
    quante = [x.words for x in parse(testo).sections]
    return (sum(quante[:indice]) if parte == 1 else sum(quante[indice:])) / max(1, sum(quante))


def _new_outline(op: Operation, posti: list[Slot], libro: _Libro
                 ) -> tuple[list[dict[str, Any]], dict[int, int | None], set[int]]:
    """La struttura dopo l'operazione, la mappa dei numeri (vecchio → nuovo; None = non c'è
    più) e i capitoli il cui titolo è cambiato. I campi che l'operazione non riguarda restano."""
    nuova: list[dict[str, Any]] = []
    mappa: dict[int, int | None] = {n: None for n in libro.numeri}
    titolo_cambiato: set[int] = set()
    for i, s in enumerate(posti, 1):
        base = libro.per_numero[s.base]
        voce = copy.deepcopy(base)
        voce["number"] = i
        if s.part in (0, 1):
            mappa[s.base] = i
        if s.moved:  # la parte del libro è quella del capitolo che ora lo precede
            vicino = posti[i - 2] if i > 1 else (posti[1] if len(posti) > 1 else None)
            if vicino is not None and "part" in libro.per_numero[vicino.base]:
                voce["part"] = libro.per_numero[vicino.base]["part"]
        if op.kind == MERGE and len(s.content) == 2:
            a, b = (libro.per_numero[n] for n in s.content)
            voce["title"] = (op.new_titles[0] if op.new_titles else "") or str(a.get("title"))
            voce["does"] = " ".join(x for x in (str(a.get("does") or "").strip(),
                                                str(b.get("does") or "").strip()) if x)
            voce["words"] = _int(a.get("words")) + _int(b.get("words"))
            voce["points"] = list(a.get("points") or []) + list(b.get("points") or [])
            mappa[s.content[1]] = i
        elif op.kind == CUT and len(s.content) == 2:
            voce["points"] = list(voce.get("points") or []) + _unique(op, libro)
        elif s.part:
            titoli = (op.new_titles + ["", ""])[:2]
            testo = libro.testi.get(s.base, "")
            indice = split_index(testo, op.split_at) or 1
            if s.part == 1:
                voce["title"] = titoli[0] or str(base.get("title"))
            else:
                sezione = parse(testo).sections[indice].heading.lstrip("# ").strip()
                voce["title"] = titoli[1] or sezione or f"{base.get('title')} (II)"
                voce["does"] = f"Seconda parte di «{base.get('title')}»" + (
                    f": {sezione}" if sezione else "")
                voce["points"] = []
                voce.pop("source", None)
            if testo and _int(base.get("words")):
                voce["words"] = round(_int(base.get("words")) * _split_words(testo, indice,
                                                                             s.part))
        if str(voce.get("title")) != str(base.get("title")):
            titolo_cambiato.add(i)
        nuova.append(voce)
    return nuova, mappa, titolo_cambiato


def _unique(op: Operation, libro: _Libro) -> list[str]:
    """Il contenuto unico di un capitolo tagliato, come punti del capitolo che lo accoglie."""
    titolo = libro.per_numero[op.chapters[0]].get("title")
    return [f"(dal capitolo tolto «{titolo}») {u}" for u in op.unique_content] or [
        f"il contenuto unico del capitolo tolto «{titolo}»"]


def _new_texts(op: Operation, posti: list[Slot], libro: _Libro, nuova: list[dict[str, Any]],
               titolo_cambiato: set[int], *, client: Any, model: str | None, language: str,
               note=None) -> tuple[dict[int, str], list[str]]:
    """I testi dei capitoli dopo l'operazione, per numero nuovo. Solo la fusione chiama il
    modello, e solo per la giunzione."""
    testi, avvisi = {}, []
    for i, s in enumerate(posti, 1):
        titolo = str(nuova[i - 1].get("title") or "")
        if op.kind == MERGE and len(s.content) == 2:
            a, b = s.content
            if a in libro.testi and b in libro.testi:
                if note:
                    note(f"Struttura: unisco i capitoli {a} e {b} (giunzione)…")
                ea, eb = libro.registro.entry(a), libro.registro.entry(b)
                testi[i], altri = merge_text(
                    client, libro.testi[a], libro.testi[b], numero=i, titolo=titolo,
                    model=model, language=language,
                    titoli=(str(libro.per_numero[a].get("title")),
                            str(libro.per_numero[b].get("title"))),
                    riassunti=(ea.summary if ea else "", eb.summary if eb else ""))
                avvisi += altri
            elif a in libro.testi or b in libro.testi:
                solo = a if a in libro.testi else b
                testi[i] = retitle(libro.testi[solo], i, titolo, new_title=True)
                avvisi.append(f"del capitolo unito era scritto solo il {solo}: l'altra metà è "
                              "nei punti della struttura, da scrivere")
        elif s.part and s.base in libro.testi:
            indice = split_index(libro.testi[s.base], op.split_at)
            if indice is None:
                raise ValueError(f"La sezione «{op.split_at}» non c'è più nel capitolo.")
            primo = i if s.part == 1 else i - 1
            uno, due = split_text(libro.testi[s.base], indice, primo,
                                  (str(nuova[primo - 1].get("title")),
                                   str(nuova[primo].get("title"))))
            testi[i] = uno if s.part == 1 else due
            if s.part == 1 and parse(libro.testi[s.base]).notes.strip():
                avvisi.append("la bibliografia del capitolo diviso è in tutt'e due le parti: "
                              "togli da ciascuna le voci che non cita")
        elif s.base in libro.testi:
            testi[i] = retitle(libro.testi[s.base], i, titolo, new_title=i in titolo_cambiato)
        if "file" in nuova[i - 1] and i in testi:
            nuova[i - 1]["file"] = libro.project.chapter_path(i, titolo).name
    return testi, avvisi


def _to_recheck(posti: list[Slot], libro: _Libro, testi: dict[int, str]) -> set[int]:
    """I capitoli da ricontrollare: cambiati nel contenuto, spostati, o con un capitolo diverso
    davanti (la continuità e la qualità si giudicano anche da quello che viene prima)."""
    davanti_prima = {n: (((libro.numeri[k - 1],), 0) if k else ())
                     for k, n in enumerate(libro.numeri)}
    fuori = set()
    for i, s in enumerate(posti, 1):
        davanti = ((tuple(posti[i - 2].content), posti[i - 2].part) if i > 1 else ())
        if len(s.content) > 1 or s.part or s.moved or davanti != davanti_prima[s.base]:
            fuori.add(i)
    return {i for i in fuori if i in testi}


def _file_plan(op: Operation, posti: list[Slot], libro: _Libro, nuova: list[dict[str, Any]],
               testi: dict[int, str], ricontrolla: set[int]
               ) -> tuple[dict[Path, str | bytes], set[Path], list[list[str]]]:
    """Cosa si scrive e cosa si toglie: i capitoli, e i dati di ogni capitolo (ricerca, piano,
    cronologia, storia della qualità, versioni) che lo seguono, si fondono o si tolgono.

    Tutto si legge qui, prima di scrivere: due capitoli che si scambiano il nome non si
    sovrascrivono a vicenda. I dati di un capitolo tagliato restano solo nella copia di sicurezza.
    """
    project = libro.project
    scrivi: dict[Path, str | bytes] = {}
    togli: set[Path] = {libro.scritti[n] for n in libro.numeri if n in libro.scritti}
    rinomine: list[list[str]] = []
    for i, testo in testi.items():
        scrivi[project.chapter_path(i, str(nuova[i - 1].get("title") or ""))] = testo

    for n in libro.numeri:
        vecchio = libro.per_numero[n]
        titolo_vecchio = str(vecchio.get("title") or "")
        stem = libro.scritti[n].stem if n in libro.scritti else ""
        dati_cap = _artefacts(project, n, titolo_vecchio, stem)
        versioni = _versions(project, stem)
        togli.update(dati_cap.values())
        togli.update(versioni)
        destinazioni = [i for i, s in enumerate(posti, 1)
                        if s.base == n or (op.kind == MERGE and n in s.content)]
        if op.kind == CUT and n == op.chapters[0]:
            destinazioni = []
        for i in destinazioni:
            s = posti[i - 1]
            titolo = str(nuova[i - 1].get("title") or "")
            base_nuova = f"{i:02d}_{slugify(titolo)}"
            stem_nuovo = project.chapter_path(i, titolo).stem
            secondo = op.kind == MERGE and n != s.content[0]  # la seconda metà di una fusione
            principale = not secondo and s.part != 2  # chi eredita storia e versioni
            for chiave, p in dati_cap.items():
                if chiave in ("plan", "timeline") and op.kind in (MERGE, SPLIT) \
                        and n in op.chapters:
                    continue  # descrivono le scene di un capitolo che non c'è più così
                raw = p.read_bytes()
                if chiave == "research_md":
                    dest = project.research_dir / f"{base_nuova}.md"
                    if secondo:  # la ricerca in prosa delle due metà, una dopo l'altra
                        prima = scrivi.get(dest, b"")
                        raw = (prima if isinstance(prima, bytes) else prima.encode("utf-8")) + (
                            b"\n\n" if prima else b"") + raw
                elif chiave == "research_json":
                    if secondo:
                        continue  # la ricerca strutturata resta quella della prima metà
                    dest = project.research_dir / f"{base_nuova}.json"
                    raw = _renumber_json(raw, n, i, titolo_vecchio, titolo)
                elif chiave in ("plan", "timeline"):
                    cartella = project.plans_dir if chiave == "plan" else \
                        project.story_dir / "timeline"
                    dest = cartella / f"{base_nuova}.json"
                    raw = _renumber_json(raw, n, i, titolo_vecchio, titolo)
                else:  # la storia della qualità
                    if not principale:
                        continue
                    dest = ql.history_path(project, stem_nuovo)
                    raw = _quality_json(raw, ql._hash(libro.testi.get(n, "")),
                                        ql._hash(testi.get(i, "")), i, titolo,
                                        i not in ricontrolla)
                scrivi[dest] = raw
            if principale:
                for p in versioni:
                    relativo = p.relative_to(ql.versions_dir(project, stem))
                    scrivi[ql.versions_dir(project, stem_nuovo) / relativo] = p.read_bytes()
        if n in libro.scritti:
            dopo = [project.chapter_path(i, str(nuova[i - 1].get("title") or "")).name
                    for i in destinazioni if i in testi]
            if dopo != [libro.scritti[n].name]:
                rinomine.append([libro.scritti[n].name, *dopo])

    for p in list(scrivi):  # i file che restano identici non si toccano
        if p in togli and p.exists() and _same(p, scrivi[p]):
            del scrivi[p]
            togli.discard(p)
    return scrivi, togli, rinomine


def _same(path: Path, contenuto: str | bytes) -> bool:
    try:
        if isinstance(contenuto, bytes):
            return path.read_bytes() == contenuto
        return path.read_text(encoding="utf-8") == contenuto
    except OSError:
        return False


def _new_ledger(op: Operation, posti: list[Slot], libro: _Libro, nuova: list[dict[str, Any]],
                testi: dict[int, str]) -> Any:
    """Il registro rinumerato. La scheda si ricava dal solo testo del capitolo: un capitolo che
    cambia solo numero la conserva (e la sua impronta segue l'intestazione nuova); un capitolo
    unito o diviso ha una scheda provvisoria, la somma delle due, segnata da rifare."""
    registro = lg.Ledger()
    for i, s in enumerate(posti, 1):
        contenuto = s.content[:1] if op.kind == CUT else s.content
        voci = [v for v in (libro.registro.entry(n) for n in contenuto) if v is not None]
        if not voci:
            continue
        voce = copy.deepcopy(voci[0])
        voce.number, voce.title = i, str(nuova[i - 1].get("title") or "")
        if len(contenuto) > 1 or s.part:
            for altra in voci[1:]:
                voce.summary = " ".join(x for x in (voce.summary, altra.summary) if x)
                voce.told = list(voce.told) + list(altra.told)
                voce.motifs = list(voce.motifs) + list(altra.motifs)
                voce.inventions = list(voce.inventions) + list(altra.inventions)
                voce.open_threads, voce.danger = list(altra.open_threads), altra.danger
                voce.end_state = copy.deepcopy(altra.end_state)
                for campo in ("knowledge", "events"):
                    if hasattr(voce, campo):
                        setattr(voce, campo, list(getattr(voce, campo) or [])
                                + list(getattr(altra, campo, None) or []))
            voce.source_hash = ""  # da rifare
        elif s.base in libro.testi and i in testi \
                and voce.source_hash == lg.text_hash(libro.testi[s.base]):
            voce.source_hash = lg.text_hash(testi[i])
        registro.chapters[i] = voce
    return registro


def _review_json(project: BookProject, op: Operation, mappa: dict[int, int | None]
                 ) -> tuple[Path, str | None, str]:
    """I punti della revisione della ricerca (``research/review.json``) con i numeri nuovi; i
    punti di un capitolo tagliato vanno al capitolo che ne accoglie il contenuto, o al libro."""
    path = project.research_dir / "review.json"  # vedi review.review_path
    if not path.exists():
        return path, None, ""
    try:
        grezza = json.loads(path.read_text(encoding="utf-8"))
        for p in grezza.get("points") or []:
            if isinstance(p, dict) and _int(p.get("chapter")):
                nuovo = _remap(_int(p.get("chapter")), mappa)
                if nuovo is None and op.kind == CUT and op.destination:
                    nuovo = mappa.get(op.destination)
                p["chapter"] = nuovo or 0
        return path, json.dumps(grezza, ensure_ascii=False, indent=2), ""
    except (OSError, ValueError, AttributeError):
        return path, None, ("la revisione della ricerca (research/review.json) non si legge: i "
                            "suoi numeri di capitolo non sono stati aggiornati")


def _remap_others(dati: StructureData, op: Operation, libro: _Libro,
                  mappa: dict[int, int | None], nuova: list[dict[str, Any]]) -> None:
    """Le altre proposte aperte parlano dei numeri di prima: si rinumerano, e quelle che
    toccavano un capitolo che non c'è più (o che è stato diviso) diventano superate."""
    titoli = {_int(c.get("number")): str(c.get("title") or "") for c in nuova}
    for altra in dati.open():
        if altra.id == op.id:
            continue
        rimappati = [_remap(n, mappa) for n in altra.chapters]
        if any(n is None for n in rimappati) or len(set(rimappati)) != len(rimappati) or \
                (op.kind == SPLIT and op.chapters[0] in altra.chapters):
            altra.status = SUPERATA
            altra.remember(f"superata dall'applicazione di {op.id}: i suoi capitoli sono cambiati")
            continue
        altra.chapters = [int(n) for n in rimappati]
        if altra.after:
            dopo = _remap(altra.after, mappa)
            if dopo is None:  # il capitolo dopo cui andava non c'è più: quello prima di lui
                k = libro.numeri.index(altra.after)
                dopo = next((mappa[m] for m in reversed(libro.numeri[:k]) if mappa.get(m)), 0)
            altra.after = int(dopo or 0)
        if altra.destination:
            altra.destination = int(_remap(altra.destination, mappa) or 0)
        altra.refs = _refs(altra, titoli)
        altra.remember(f"rinumerata dopo l'applicazione di {op.id}")


def _manifest_base(run: Run, state: ForgeState, dati: StructureData, op_id: str
                   ) -> dict[str, Any]:
    """Quello che serve all'annullamento oltre ai file: la struttura, le deroghe, i ricontrolli,
    le integrazioni e la numerazione delle altre proposte com'erano."""
    return {
        "run": run.id, "op": op_id, "kind": run.kind, "at": run.at,
        "outline_before": copy.deepcopy(state.outline),
        "waivers_before": copy.deepcopy(state.waivers),
        "recheck_before": list(dati.recheck),
        "integrations_before": copy.deepcopy(dati.integrations),
        "ops_before": {o.id: {"chapters": list(o.chapters), "after": o.after,
                              "destination": o.destination, "status": o.status,
                              "refs": copy.deepcopy(o.refs), "blockers": list(o.blockers),
                              "warnings": list(o.warnings)}
                       for o in dati.open() if o.id != op_id},
    }


def _write_manifest(tx: _Transazione, manifest: dict[str, Any]) -> None:
    manifest["existed"], manifest["created"] = tx.existed, tx.created
    manifest["after_hashes"] = tx.hashes()
    tx.dir.mkdir(parents=True, exist_ok=True)
    (tx.dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                          encoding="utf-8")


def _run_id(op_id: str, kind: str) -> str:
    quando = datetime.now().strftime("%Y%m%d-%H%M%S-%f")  # noqa: DTZ005 - per chi legge la cartella
    return f"{quando}_{op_id}_{kind}"


def apply(project: BookProject, state: ForgeState, op_id: str, *, client: Any = None,
          model: str | None = None, language: str = "italiano", note=None) -> ApplyResult:
    """Applica una proposta accettata. Prima si calcola tutto in memoria (struttura, testi, dati
    dei capitoli, registro, specifica); poi si copia ogni file che si toccherà in
    ``story/struttura/<giro>/``; poi si scrive.

    ``client``: serve solo alla fusione, per la giunzione; senza, i due testi si uniscono come
    sono (e si dice). Lo stato (``state.outline``) cambia in memoria: lo salva chi chiama.
    """
    dati = load(project)
    op = dati.operation(op_id)
    if op is None:
        raise ValueError(f"Nessuna proposta {op_id}.")
    if op.status != ACCETTATA:
        raise ValueError(f"La proposta {op_id} è «{op.status}»: si applica solo una proposta "
                         "accettata.")
    if _stale(op, state):
        raise ValueError("La struttura è cambiata dopo questa proposta: rifai la diagnosi o "
                         "rivedi la proposta.")
    libro = _read_book(project, state)
    viste = chapter_views(project, state)
    errori = validate(op, libro.numeri, viste)
    if errori:
        raise ValueError("La proposta non si può applicare: " + "; ".join(errori))
    op.blockers, op.warnings = check(op, viste)
    if op.blockers and not op.waiver:
        save(project, dati)
        raise ValueError("Controlli bloccanti nuovi dopo l'accettazione: decidi di nuovo.\n- "
                         + "\n- ".join(op.blockers))

    posti = transform(op, libro.numeri)
    nuova, mappa, titolo_cambiato = _new_outline(op, posti, libro)
    testi, avvisi = _new_texts(op, posti, libro, nuova, titolo_cambiato, client=client,
                               model=model, language=language, note=note)
    if op.kind == MERGE:  # la giunzione può aver preso tempo: le decisioni si rileggono
        avvertimenti = op.warnings
        dati = load(project)
        op = dati.operation(op_id)
        if op is None or op.status != ACCETTATA:
            raise ValueError(f"La proposta {op_id} non è più accettata: niente è cambiato.")
        op.warnings = avvertimenti
    avvisi = list(op.warnings) + avvisi
    ricontrolla = _to_recheck(posti, libro, testi)
    scrivi, togli, rinomine = _file_plan(op, posti, libro, nuova, testi, ricontrolla)
    registro = _new_ledger(op, posti, libro, nuova, testi)
    revisione_path, revisione, avviso = _review_json(project, op, mappa)
    if avviso:
        avvisi.append(avviso)
    specifica = None
    if project.spec_path.exists():
        specifica = _spec_table(project.spec_path.read_text(encoding="utf-8"), nuova)
        if specifica is None:
            avvisi.append("la tabella dei capitoli non si trova in MASTER_SPEC.md: la "
                          "specifica non è stata aggiornata")

    # ---- la copia di sicurezza, poi i cambiamenti ---------------------------------------
    run = Run(id=_run_id(op.id, op.kind), op=op.id, kind=op.kind, at=_now())
    tx = _Transazione(project, run.id)
    run.backup = tx.rel(tx.dir)
    manifest = _manifest_base(run, state, dati, op.id)
    tx.protect(sorted(set(scrivi) | togli))
    tx.protect([lg.ledger_path(project), project.story_dir / "ledger.md"])
    if revisione is not None:
        tx.protect([revisione_path])
    if specifica is not None:
        tx.protect([project.spec_path])
    tx.dir.mkdir(parents=True, exist_ok=True)
    try:
        for p in sorted(togli - set(scrivi)):
            if p.exists():
                p.unlink()
        for p, contenuto in scrivi.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(contenuto, bytes):
                p.write_bytes(contenuto)
            else:
                p.write_text(contenuto, encoding="utf-8")
        lg.save(project, registro)
        if revisione is not None:
            revisione_path.write_text(revisione, encoding="utf-8")
        if specifica is not None:
            project.spec_path.write_text(specifica, encoding="utf-8")
    except BaseException:
        tx.rollback()  # un errore a metà (disco, file aperto altrove) non lascia mezzo libro
        raise
    for n in libro.numeri:  # le cartelle delle versioni rimaste vuote
        if n in libro.scritti:
            cartella = ql.versions_dir(project, libro.scritti[n].stem)
            if cartella.exists() and not any(cartella.iterdir()):
                cartella.rmdir()

    # ---- lo stato, le altre proposte, il resoconto ---------------------------------------
    state.outline = nuova
    for w in state.waivers:
        if isinstance(w, dict) and _int(w.get("chapter")):
            w["chapter"] = _remap(_int(w.get("chapter")), mappa) or 0
    _remap_others(dati, op, libro, mappa, nuova)
    dati.recheck = sorted({m for n in dati.recheck if (m := _remap(n, mappa))} | ricontrolla)
    for voce in dati.integrations:
        voce["chapter"] = _remap(_int(voce.get("chapter")), mappa) or 0
    if op.kind == CUT and op.destination:
        dest = mappa.get(op.destination)
        if dest and dest in testi:
            dati.integrations.append({
                "op": op.id, "chapter": dest,
                "from": str(libro.per_numero[op.chapters[0]].get("title") or ""),
                "content": _unique(op, libro)})
            dati.recheck = sorted(set(dati.recheck) | {dest})
            avvisi.append(f"il contenuto unico è nei punti del capitolo {dest}: inseriscilo nel "
                          "testo con «integra», o a mano")
        elif dest:
            avvisi.append(f"il capitolo {dest}, che accoglie il contenuto unico, non è ancora "
                          "scritto: il contenuto è nei suoi punti e si scriverà con lui")
    run.number_map = {str(k): v for k, v in mappa.items()}
    run.renamed, run.recheck, run.warnings = rinomine, sorted(ricontrolla), avvisi
    op.status, op.run = APPLICATA, run.id
    op.remember(f"applicata (copia di sicurezza in {run.backup})")
    dati.runs.append(run)
    if dati.open():
        viste = chapter_views(project, ForgeState(outline=nuova))
        dip = dependencies(viste)
        for o in dati.open():
            o.blockers, o.warnings = check(o, viste, dip)
    _write_manifest(tx, manifest)
    save(project, dati)
    return ApplyResult(op=op, run=run, outline=nuova)


# --------------------------------------------------------------------------- #
#  l'annullamento
# --------------------------------------------------------------------------- #
def changed_since(project: BookProject, run: Run) -> list[str]:
    """I file che qualcuno ha cambiato dopo l'applicazione: annullare li riporterebbe indietro."""
    manifest = _manifest(project, run)
    return sorted(r for r, h in (manifest.get("after_hashes") or {}).items()
                  if _hash_file(project.root / r) != h)


def _manifest(project: BookProject, run: Run) -> dict[str, Any]:
    path = backups_dir(project) / run.id / "manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def undo(project: BookProject, state: ForgeState, run_id: str | None = None, *,
         force: bool = False) -> UndoResult:
    """Annulla l'ultima applicazione (o integrazione) non ancora annullata: i file tornano
    com'erano, quelli creati si tolgono, la struttura e le proposte tornano a prima.

    Si annulla a ritroso, una alla volta: un'applicazione più vecchia poggia su quelle dopo. Se
    dopo l'applicazione un file è cambiato (una revisione, una stesura), annullare cancellerebbe
    quel lavoro: si rifiuta, a meno di ``force``. Lo stato cambia in memoria: lo salva chi chiama.
    """
    dati = load(project)
    ultimo = dati.last_run()
    if ultimo is None:
        raise ValueError("Non c'è niente da annullare.")
    if run_id and run_id != ultimo.id:
        raise ValueError(f"Si annulla a ritroso: prima l'ultima applicazione ({ultimo.id}).")
    manifest = _manifest(project, ultimo)
    if not manifest:
        raise ValueError(f"La copia di sicurezza di {ultimo.id} non si trova: non si può "
                         "annullare.")
    cambiati = changed_since(project, ultimo)
    if cambiati and not force:
        raise ValueError("Dopo l'applicazione sono cambiati questi file; annullare li "
                         "riporterebbe indietro:\n- " + "\n- ".join(cambiati))
    esito = UndoResult(run=ultimo)
    esito.restored, esito.removed = _restore(project, backups_dir(project) / ultimo.id / "files",
                                             list(manifest.get("existed") or []),
                                             list(manifest.get("created") or []))
    if "outline_before" in manifest:
        state.outline = manifest["outline_before"]
    if "waivers_before" in manifest:
        state.waivers = manifest["waivers_before"]
    dati.recheck = list(manifest.get("recheck_before") or [])
    dati.integrations = list(manifest.get("integrations_before") or [])
    for ident, campi in (manifest.get("ops_before") or {}).items():
        o = dati.operation(ident)
        if o is None:
            continue
        for k, v in campi.items():
            # una decisione presa dopo l'applicazione resta; una proposta che l'applicazione
            # aveva reso superata torna com'era
            if k != "status" or o.status == SUPERATA:
                setattr(o, k, v)
        o.remember(f"riportata com'era prima di {ultimo.id}")
    op = dati.operation(ultimo.op)
    if op is not None and ultimo.kind in KINDS:
        op.status, op.run = ANNULLATA, ""
        op.remember(f"annullata: i file sono tornati com'erano prima di {ultimo.id}")
    elif op is not None:
        op.remember(f"integrazione annullata ({ultimo.id})")
    ultimo.undone = _now()
    save(project, dati)
    return esito


# --------------------------------------------------------------------------- #
#  l'integrazione del contenuto unico di un capitolo tagliato
# --------------------------------------------------------------------------- #
def integrate(project: BookProject, state: ForgeState, op_id: str, paths: list[Path],
              worker) -> Run:
    """Inserisce nel testo del capitolo che lo accoglie il contenuto unico di un capitolo
    tagliato, dentro una transazione come quella di :func:`apply`: prima la copia di ``paths``
    (i file che ``worker`` toccherà), poi il lavoro, e se il lavoro fallisce tutto torna com'era.

    ``worker(voce)`` riceve ``{"op", "chapter", "from", "content"}``, scrive, e ritorna gli avvisi.
    Si annulla con :func:`undo`, come un'applicazione.
    """
    dati = load(project)
    voce = next((i for i in dati.integrations if i.get("op") == op_id), None)
    if voce is None:
        raise ValueError(f"Nessun contenuto da integrare per la proposta {op_id}.")
    run = Run(id=_run_id(op_id, "integrate"), op=op_id, kind="integrate", at=_now())
    tx = _Transazione(project, run.id)
    run.backup = tx.rel(tx.dir)
    manifest = _manifest_base(run, state, dati, op_id)
    tx.protect(paths)
    tx.dir.mkdir(parents=True, exist_ok=True)
    try:
        avvisi = list(worker(dict(voce)) or [])
    except BaseException:
        tx.rollback()
        raise
    dati = load(project)  # il lavoro può essere stato lungo: si rilegge prima di scrivere
    voce = next((i for i in dati.integrations if i.get("op") == op_id), voce)
    if voce in dati.integrations:
        dati.integrations.remove(voce)
    run.recheck = [_int(voce.get("chapter"))]
    dati.recheck = sorted(set(dati.recheck) | set(run.recheck))
    run.warnings = avvisi
    dati.runs.append(run)
    op = dati.operation(op_id)
    if op is not None:
        op.remember(f"contenuto unico inserito nel capitolo {run.recheck[0]} (copia di "
                    f"sicurezza in {run.backup})")
    _write_manifest(tx, manifest)
    save(project, dati)
    return run


def clear_recheck(project: BookProject, numbers: list[int]) -> None:
    """Toglie dalla lista da ricontrollare i capitoli ricontrollati."""
    dati = load(project)
    dati.recheck = [n for n in dati.recheck if n not in set(numbers)]
    save(project, dati)
