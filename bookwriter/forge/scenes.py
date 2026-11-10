"""Il piano delle scene di un capitolo, prima di una sola riga di prosa.

Un capitolo scritto «per punti» riceveva, sezione per sezione, una tesi da argomentare («The 1312
persecution shows what happens when the secret becomes public») e una quota di 1.400 parole da
riempire: il modello, finita la tesi, la ripeteva. Una scena non si può ripetere allo stesso
modo, perché ha una sua meta: qualcuno vuole qualcosa, qualcosa glielo impedisce, sceglie, e la
situazione dopo è un'altra.

Il piano (:func:`plan`) dice per ogni scena dove e quando, chi, cosa vuole il protagonista, cosa
lo ostacola, quale indizio trova, cosa crede prima e dopo, cosa decide, cosa ne segue, a che punto
è il pericolo, come si passa alla scena dopo, quali affermazioni di ricerca mette in scena e
quante parole. Il controllo (:func:`gate`) lo rifiuta se una scena non cambia niente o se il
capitolo riparte in un altro luogo senza dire come ci si è arrivati; un piano rifiutato si ripara
**una** volta, poi si scrive con i difetti segnati nel resoconto.

Dalla seconda analisi editoriale del cap. 11 (scene migliori, ma cinque variazioni della stessa
tesi, un salto dall'Egitto alla Francia senza causa, una pagina sottratta due volte, una porta
chiusa da cui il protagonista esce senza che si veda come), ogni scena dichiara anche:

- **cosa cambia**, come variabile concreta (conoscenza, rischio, possesso, rapporto,
  destinazione, fuga) con il prima e il dopo: due scene che cambiano la stessa cosa nello stesso
  modo sono la stessa scena;
- **la domanda nuova** che porta alla scena dopo;
- **il ragionamento** di una scoperta: cosa il protagonista osserva, come lo interpreta, quale
  alternativa scarta, perché agisce così;
- **a cosa serve** ogni informazione di ricerca che usa (un enigma, una decisione, un rapporto di
  forza, un seme per dopo, l'ambiente): quella senza funzione resta nell'archivio.

E il capitolo dichiara **il ponte** dal capitolo prima (indizio → decisione del protagonista →
spostamento → nuovo obiettivo) e cosa ne è dei suoi **fili aperti**. Oltre al controllo
deterministico, un controllo di continuità del piano (:func:`timeline.check_plan`) guarda orari,
scadenze, porte e oggetti prima di scrivere una riga.

La misura di una scena è legata a quanto il modello sa scrivere in una risposta
(``writing.section_words``): una scena più lunga si scrive in più parti (:func:`units`), ciascuna
con il suo pezzo di lavoro, invece di chiedere «ancora» a una scena finita.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from bookwriter.forge import metrics as mx
from bookwriter.forge.context import ContextPack
from bookwriter.forge.metrics import content_words
from bookwriter.forge.project import BookProject, ForgeState, slugify

PRESENT, HISTORICAL = "presente", "episodio_storico"

#: Quante volte si ripara un piano che il controllo rifiuta.
MAX_REPAIRS = 3

#: La gravità dei difetti del controllo deterministico, dal testo del difetto (specifica
#: metodologica del 25/10/2026, §5): un blocker rende impossibile una stesura coerente, un
#: maggiore la indebolisce, un minore è locale. Il primo schema che corrisponde decide.
_SEVERITA_REGOLE: tuple[tuple[str, str], ...] = (
    ("non ha restituito un piano", "blocker"),
    # un presente senza il protagonista è un altro libro (prove v1 e v5 del cap. 11: sei scene
    # della «rete», Nicolò mai nominato, e la stesura partita lo stesso)
    ("non è in nessuna scena del presente", "blocker"),
    ("in nessuna scena del presente decide", "blocker"),
    ("viene prima della", "blocker"),
    ("ore ma fra le ancore", "blocker"),
    ("serve la catena indizio", "blocker"),
    ("comincia con un episodio storico", "major"),
    ("non hanno l'ancora", "major"),
    ("fili aperti", "major"),
    ("stesso luogo con lo stesso obiettivo", "major"),
    ("cambiano la stessa cosa", "major"),
    ("non cambia nessuna variabile", "major"),
    ("non è in scena", "major"),
    ("non ha:", "major"),
    ("non risolvibil", "major"),
    ("senza dire a cosa servono", "minor"),
    ("non ha il ragionamento", "minor"),
    ("domanda nuova", "minor"),
    ("ruotano attorno alle stesse parole", "minor"),
    ("di norma ne servono", "minor"),
    ("ripete il passo", "minor"),
    ("riusa", "minor"),
)
_TAG = re.compile(r"^\[(\w+)\]\s*")


def severity_of(difetto: str) -> str:
    """La gravità di un difetto: dall'etichetta ``[blocker]`` messa dal controllo di continuità,
    o dalle regole del controllo deterministico; altrimenti maggiore."""
    from bookwriter.forge import concepts as cn

    m = _TAG.match(difetto or "")
    if m:
        g = cn.normalise("gravita", m.group(1))
        if g:
            return g
    testo = (difetto or "").lower()
    return next((g for chiave, g in _SEVERITA_REGOLE if chiave in testo), "major")


def risk(difetti: list[str]) -> tuple[int, int, int]:
    """Il rischio residuo per gravità: (blocker, maggiori, minori). Si confronta in quest'ordine:
    togliere dieci minori non compensa un blocker nuovo."""
    g = [severity_of(d) for d in difetti]
    return g.count("blocker"), g.count("major"), g.count("minor")

_T = {"type": "string"}
_L = {"type": "array", "items": {"type": "string"}}

SCENE_FIELDS = ("id", "kind", "pov", "place", "day", "time", "arrival", "characters", "goal",
                "obstacle", "stakes", "clue", "belief_before", "belief_after", "decision",
                "consequence", "danger", "bridge", "next_question")

#: I passi di un motivo che torna: ogni ricorrenza fa un lavoro diverso (specifica editoriale del
#: 25/10/2026, §10 priorità 5: «non ripetere la tesi in ogni scena; trasformarla in una catena»).
MOTIF_STEPS = ("indizio", "conflitto", "costo", "scelta", "conseguenza")

#: Le variabili narrative che una scena può cambiare: se non ne cambia nessuna, non è una scena.
VARIABLES = ("conoscenza", "rischio", "possesso", "rapporto", "destinazione", "fuga")
#: A cosa serve un'informazione di ricerca nella scena. Quella che non serve a niente di questo
#: resta nell'archivio: c'era nel materiale, non è una ragione per metterla nel capitolo.
CLAIM_FUNCTIONS = ("enigma", "decisione", "rapporto_di_forza", "semina", "ambientazione")
#: Due scene che cambiano la stessa variabile con un «dopo» così simile sono la stessa scena.
SAME_CHANGE = 0.6

_CHANGE = {"type": "object", "additionalProperties": False,
           "properties": {"variable": {"type": "string", "enum": list(VARIABLES)},
                          "before": _T, "after": _T},
           "required": ["variable", "before", "after"]}
_REASONING = {"type": "object", "additionalProperties": False,
              "properties": {"observed": _T, "interpretation": _T, "alternative": _T, "why": _T},
              "required": ["observed", "interpretation", "alternative", "why"]}
_CLAIM_USE = {"type": "object", "additionalProperties": False,
              "properties": {"claim_id": _T,
                             "function": {"type": "string", "enum": list(CLAIM_FUNCTIONS)},
                             "how": _T},
              "required": ["claim_id", "function", "how"]}
_SCENE_PROPS = {**{k: _T for k in SCENE_FIELDS}, "changes": {"type": "array", "items": _CHANGE},
                "reasoning": _REASONING, "claim_uses": {"type": "array", "items": _CLAIM_USE},
                "words": {"type": "integer"}}
_SCENE_REQUIRED = [*SCENE_FIELDS, "changes", "reasoning", "claim_uses", "words"]

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "goal": _T, "dramatic_question": _T, "turn": _T, "start": _T, "end": _T,
        "bridge": {"type": "object", "additionalProperties": False,
                   "properties": {"from": _T, "clue": _T, "decision": _T, "movement": _T,
                                  "new_goal": _T},
                   "required": ["from", "clue", "decision", "movement", "new_goal"]},
        "threads": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"thread": _T, "handling": _T},
            "required": ["thread", "handling"]}},
        "objects": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"object": _T, "origin": _T, "holder_start": _T, "transfers": _T,
                           "holder_end": _T},
            "required": ["object", "origin", "holder_start", "transfers", "holder_end"]}},
        "motif_chain": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"scene": _T, "motif": _T,
                           "step": {"type": "string", "enum": list(MOTIF_STEPS)}, "what": _T},
            "required": ["scene", "motif", "step", "what"]}},
        "already_known": _L,
        "motifs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"motif": _T, "new_function": _T},
            "required": ["motif", "new_function"]}},
        "canon": _L,
        "continuity_risks": _L,
        "scenes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": _SCENE_PROPS, "required": _SCENE_REQUIRED}},
    },
    "required": ["goal", "dramatic_question", "turn", "start", "end", "bridge", "threads",
                 "objects", "motif_chain", "already_known", "motifs", "canon",
                 "continuity_risks", "scenes"],
}


@dataclass
class Scene:
    id: str = ""
    kind: str = PRESENT
    pov: str = ""
    place: str = ""
    #: l'ancora della scena: il giorno (o la data) e l'ora; per un episodio storico, l'anno
    day: str = ""
    time: str = ""
    #: come il protagonista arriva qui dalla scena del presente prima: da dove, mezzo, durata
    arrival: str = ""
    characters: str = ""
    goal: str = ""
    obstacle: str = ""
    stakes: str = ""
    clue: str = ""
    belief_before: str = ""
    belief_after: str = ""
    decision: str = ""
    consequence: str = ""
    danger: str = ""
    bridge: str = ""
    #: la domanda o il problema nuovo che porta alla scena dopo
    next_question: str = ""
    #: cosa cambia: ``[{"variable", "before", "after"}]`` (vedi :data:`VARIABLES`)
    changes: list[dict[str, str]] = field(default_factory=list)
    #: il ragionamento di una scoperta: ``{"observed", "interpretation", "alternative", "why"}``
    reasoning: dict[str, str] = field(default_factory=dict)
    #: a cosa serve ogni informazione di ricerca: ``[{"claim_id", "function", "how"}]``
    claim_uses: list[dict[str, str]] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    words: int = 0
    #: aggiunta dopo la stesura perché il capitolo era corto: si scrive dopo la scena ``after``
    after: str = ""

    def valid_changes(self) -> list[dict[str, str]]:
        """I cambiamenti veri: una variabile nota, un prima e un dopo, diversi."""
        return [c for c in self.changes
                if c.get("variable") in VARIABLES and str(c.get("after") or "").strip()
                and str(c.get("before") or "").strip().lower()
                != str(c.get("after") or "").strip().lower()]

    def use_of(self, claim_id: str) -> dict[str, str] | None:
        return next((u for u in self.claim_uses if u.get("claim_id") == claim_id), None)

    def line(self) -> str:
        """La scena in una riga, per il piano che accompagna ogni chiamata."""
        dove = ", ".join(p for p in (self.place, self.day, self.time) if p)
        tipo = " [episodio storico]" if self.kind == HISTORICAL else ""
        return (f"{self.id}.{tipo} {dove} — {self.goal}; ostacolo: {self.obstacle}; decisione: "
                f"{self.decision}; conseguenza: {self.consequence} (~{self.words} parole)")

    def card(self) -> str:
        """La scheda completa della scena da scrivere."""
        voci = [
            ("Tipo", "episodio storico, narrato come scena" if self.kind == HISTORICAL else
             "presente del racconto"),
            ("Punto di vista", self.pov), ("Luogo", self.place), ("Giorno", self.day),
            ("Ora", self.time), ("Come ci arriva (mezzo e durata)", self.arrival),
            ("Personaggi in scena", self.characters), ("Obiettivo", self.goal),
            ("Ostacolo", self.obstacle), ("Posta in gioco", self.stakes),
            ("Indizio o informazione", self.clue), ("Crede prima", self.belief_before),
            ("Crede dopo", self.belief_after), ("Decisione", self.decision),
            ("Conseguenza", self.consequence), ("Pericolo", self.danger),
            ("Ponte verso la scena dopo", self.bridge),
            ("Domanda nuova per la scena dopo", self.next_question),
        ]
        righe = [f"- {k}: {v}" for k, v in voci if v]
        cambi = self.valid_changes()
        if cambi:
            righe.append("- Cosa cambia (deve accadere nel testo, non essere detto): " + "; ".join(
                f"{c['variable']}: da «{c.get('before', '')}» a «{c['after']}»" for c in cambi))
        r = self.reasoning or {}
        if any(str(r.get(k) or "").strip() for k in ("observed", "interpretation", "why")):
            righe.append("- Il ragionamento che il lettore deve poter seguire: "
                         + "; ".join(f"{nome}: {r[k]}" for nome, k in (
                             ("osserva", "observed"), ("interpreta", "interpretation"),
                             ("alternativa che scarta", "alternative"),
                             ("perché agisce così", "why")) if str(r.get(k) or "").strip()))
        return "\n".join(righe)


@dataclass
class Plan:
    chapter: int = 0
    title: str = ""
    goal: str = ""
    dramatic_question: str = ""
    turn: str = ""
    start: str = ""
    end: str = ""
    #: dal capitolo prima a questo: ``{"from", "clue", "decision", "movement", "new_goal"}``
    bridge: dict[str, str] = field(default_factory=dict)
    #: i fili aperti del capitolo prima e cosa ne fa questo: ``[{"thread", "handling"}]``
    threads: list[dict[str, str]] = field(default_factory=list)
    #: gli oggetti e i documenti che contano: origine, custode all'inizio, passaggi, alla fine
    objects: list[dict[str, str]] = field(default_factory=list)
    #: le ricorrenze del motivo principale, ciascuna con il suo passo (vedi MOTIF_STEPS)
    motif_chain: list[dict[str, str]] = field(default_factory=list)
    already_known: list[str] = field(default_factory=list)
    motifs: list[dict[str, str]] = field(default_factory=list)
    canon: list[str] = field(default_factory=list)
    continuity_risks: list[str] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    #: i difetti che il controllo ha trovato e che la riparazione non ha tolto
    defects: list[str] = field(default_factory=list)
    #: gli ID di ricerca storpiati ricondotti al registro: [{"from", "to", "why"}]
    resolutions: list[dict[str, str]] = field(default_factory=list)
    #: la deroga dell'autore ai blocker rimasti (il motivo); vuota = nessuna deroga
    waiver: str = ""
    #: i registri della metodologia (totali, categorie, stati iniziali): vincoli per chi scrive
    registers_block: str = ""
    #: ``modello`` (piano del planner), ``riparato``, ``dai_punti`` (il modello non ha risposto)
    origin: str = "modello"
    trial: bool = False
    created: str = ""

    def scene(self, ident: str) -> Scene | None:
        return next((s for s in self.scenes if s.id == ident), None)

    def blockers(self) -> list[str]:
        """I difetti bloccanti rimasti: con questi la stesura non parte (salvo deroga)."""
        return [d for d in self.defects if severity_of(d) == "blocker"]

    def claim_ids(self) -> list[str]:
        visti: list[str] = []
        for s in self.scenes:
            visti += [k for k in s.claim_ids if k not in visti]
        return visti

    def outline(self, current: str = "") -> str:
        return "\n".join(s.line() + ("   ← QUESTA" if s.id == current else "")
                         for s in self.scenes)

    def bridge_text(self) -> str:
        """Il ponte dal capitolo prima, in chiaro (vuoto se il piano non ne ha bisogno)."""
        b = self.bridge or {}
        voci = [(nome, str(b.get(k) or "").strip()) for nome, k in (
            ("da dove", "from"), ("l'indizio", "clue"), ("la decisione", "decision"),
            ("lo spostamento", "movement"), ("il nuovo obiettivo", "new_goal"))]
        return "; ".join(f"{nome}: {v}" for nome, v in voci if v)


# --------------------------------------------------------------------------- #
#  su disco
# --------------------------------------------------------------------------- #
def plan_path(project: BookProject, number: int, title: str, *, trial: bool = False):
    prefisso = "prova_" if trial else ""
    return project.plans_dir / f"{prefisso}{number:02d}_{slugify(title)}.json"


def plan_from_dict(d: dict[str, Any]) -> Plan:
    campi = set(Plan.__dataclass_fields__)
    piano = Plan(**{k: v for k, v in d.items() if k in campi and k != "scenes"})
    piano.bridge = {k: str((d.get("bridge") or {}).get(k) or "").strip()
                    for k in ("from", "clue", "decision", "movement", "new_goal")} \
        if isinstance(d.get("bridge"), dict) else {}
    piano.threads = _dicts(d.get("threads"), ("thread", "handling"))
    piano.objects = _dicts(d.get("objects"), ("object", "origin", "holder_start", "transfers",
                                              "holder_end"))
    piano.motif_chain = _dicts(d.get("motif_chain"), ("scene", "motif", "step", "what"))
    from bookwriter.forge import concepts as cn

    for m in piano.motif_chain:
        m["step"] = cn.normalise("passo_motivo", m["step"]) or m["step"].lower()
    piano.scenes = [_scene(s) for s in d.get("scenes") or [] if isinstance(s, dict)]
    return piano


def save(project: BookProject, piano: Plan) -> None:
    path = plan_path(project, piano.chapter, piano.title, trial=piano.trial)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(piano), ensure_ascii=False, indent=1), encoding="utf-8")


def load(project: BookProject, number: int, title: str, *, trial: bool = False) -> Plan | None:
    path = plan_path(project, number, title, trial=trial)
    if not path.exists():
        return None
    try:
        return plan_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _dicts(valore: Any, chiavi: tuple[str, ...]) -> list[dict[str, str]]:
    return [{k: str(v.get(k) or "").strip() for k in chiavi}
            for v in (valore or []) if isinstance(v, dict)]


def _scene(d: dict[str, Any]) -> Scene:
    s = Scene(**{k: (str(v).strip() if k in SCENE_FIELDS else v)
                 for k, v in d.items() if k in Scene.__dataclass_fields__})
    s.kind = HISTORICAL if "stor" in (s.kind or "").lower() or "hist" in (s.kind or "").lower() \
        else PRESENT
    from bookwriter.forge import concepts as cn

    s.changes = _dicts(d.get("changes"), ("variable", "before", "after"))
    for c in s.changes:
        c["variable"] = cn.normalise("variabile", c["variable"]) or c["variable"].lower()
    r = d.get("reasoning") if isinstance(d.get("reasoning"), dict) else {}
    s.reasoning = {k: str(r.get(k) or "").strip()
                   for k in ("observed", "interpretation", "alternative", "why")}
    s.claim_uses = _dicts(d.get("claim_uses"), ("claim_id", "function", "how"))
    for u in s.claim_uses:
        u["function"] = cn.normalise("funzione_ricerca", u["function"]) or u["function"].lower()
    # i piani di prima avevano solo gli id; quelli nuovi li ricavano dagli usi
    ids = [u["claim_id"] for u in s.claim_uses if u["claim_id"]] or \
        [str(k).strip() for k in (d.get("claim_ids") or [])]
    s.claim_ids = list(dict.fromkeys(k for k in ids if k))
    try:
        s.words = int(d.get("words") or 0)
    except (TypeError, ValueError):
        s.words = 0
    return s


# --------------------------------------------------------------------------- #
#  il planner
# --------------------------------------------------------------------------- #
def _system(pack: ContextPack, cap: int) -> str:
    chi = pack.protagonist or "il protagonista"
    return (
        "Sei il narrative planner di un romanzo. NON scrivi ancora il capitolo: ne progetti le "
        "scene. Usa solo le informazioni che ti do; scegli quelle che servono alla funzione del "
        "capitolo e lascia il resto: non tentare di includere tutto il materiale di ricerca.\n\n"
        "Una scena è un'unità di azione: qualcuno (di solito " + chi + ") vuole qualcosa in un "
        "luogo e in un tempo precisi, qualcosa o qualcuno glielo impedisce, trova o perde "
        "qualcosa di concreto (un documento, un oggetto, una frase detta da qualcuno), capisce "
        "qualcosa di diverso da prima, decide, e la decisione ha una conseguenza. Una scena che "
        "non cambia niente — né la situazione, né quello che il protagonista sa, né il "
        "pericolo — non è una scena: va riprogettata. Le scene storiche (episodi antichi) "
        "sono scene vere, con personaggi, azione, spazio, tempo, tensione e conseguenza, non "
        "riassunti di storia.\n\n"
        "I «punti chiave» del capitolo sono FUNZIONI da ottenere con le scene, non tesi da "
        "dimostrare: se un punto dice «X è presentato come fatto», progetta la scena in cui il "
        "protagonista trova la prova di X e cosa ne fa. Un punto può prendere una scena, una "
        "parte di scena o un dettaglio.\n\n"
        "Il presente del romanzo è la storia di " + chi + ": è in scena (in 'characters') in "
        "ogni scena del presente, ed è lui a volere, scegliere e pagare. Un'istituzione, una "
        "rete, un «pattern» o un'idea non sono personaggi: non hanno obiettivi né decisioni. "
        "L'obiettivo di una scena è quello che una persona vuole ottenere lì, con un verbo "
        "d'azione (trovare, convincere, entrare, fuggire, rubare, capire chi…).\n\n"
        "Ogni scena cambia almeno una VARIABILE concreta ('changes'): conoscenza (cosa sa il "
        "protagonista), rischio, possesso (un oggetto, un documento), rapporto (con un "
        "personaggio), destinazione (dove deve andare), fuga (le vie d'uscita). Prima e dopo "
        "diversi e concreti. Due scene che cambiano la stessa cosa nello stesso modo sono la "
        "stessa scena: riprogettane una. In 'next_question' la domanda o il problema NUOVO che "
        "la scena lascia e che porta alla scena dopo.\n\n"
        "Il ragionamento ('reasoning'): quando il protagonista scopre o decide qualcosa, il "
        "lettore deve poter seguire il percorso, anche se la conclusione è sbagliata: cosa "
        "osserva (un dettaglio concreto), come lo interpreta, quale alternativa considera e "
        "scarta, perché agisce proprio così (es. perché prende la pagina invece di "
        "fotografarla). Breve, ma senza buchi.\n\n"
        "Continuità fra capitoli ('bridge'): parti dallo STATO FINALE del capitolo precedente "
        "(luogo, ora, chi c'è, cosa ha in mano, la prossima azione). Se questo capitolo comincia "
        "altrove, il passaggio è una catena causale del protagonista: l'indizio che trova lì "
        "('clue'), la decisione che PRENDE LUI ('decision', non «viene portato»), lo "
        "spostamento ('movement': mezzo, tempo), il nuovo obiettivo ('new_goal'). Il ponte si "
        "mette in scena (all'inizio della prima scena o in una scena breve), non si riassume in "
        "una riga. In 'threads' ogni filo aperto del capitolo precedente e cosa ne fa questo "
        "capitolo: risolto (in quale scena), rimandato (perché, e il lettore lo sa) o chiuso; "
        "nessun filo abbandonato in silenzio (un appuntamento, una persona, una promessa).\n\n"
        "Tempo, spazio, oggetti: orari e giorni in ordine; ogni scadenza ha un'ora sola per "
        "tutto il capitolo; un oggetto sottratto non si sottrae di nuovo (se sono due pagine "
        "diverse, dillo e distinguile); se il protagonista resta chiuso in un luogo, la scena "
        "dopo mostra come esce; date e stagione coerenti. Non anticipare gli eventi dei "
        "capitoli successivi; non rivelare ciò che la struttura tiene nascosto.\n\n"
        "ANCORE. Ogni scena del presente ha 'day' (giorno o data) e 'time' (l'ora d'inizio, "
        "es. '6:40 p.m.'): sono le sole ancore che la stesura userà, e devono essere possibili "
        "— niente due luoghi lontani nello stesso intervallo. Se il protagonista cambia luogo "
        "dalla scena del presente prima, 'arrival' dice da dove, con che mezzo e quanto dura "
        "(es. 'volo Cairo-Parigi, 5 ore, poi treno per Digione, 2 ore'): fra le due ancore deve "
        "passare almeno quel tempo. Per un episodio storico 'day' è l'anno (o la data) e "
        "'time' il momento.\n\n"
        "APERTURA. Se c'è un capitolo precedente, la PRIMA scena è nel presente e riparte da "
        "dove quel capitolo ha lasciato il protagonista; gli episodi storici vengono dopo.\n\n"
        "OGGETTI. In 'objects' gli oggetti e i documenti che contano nel capitolo (prove, "
        "campioni, quaderni, copie, chiavi): da dove vengono ('origin', anche dal capitolo "
        "prima), chi li ha all'inizio, i passaggi di mano ('transfers': in quale scena, da chi "
        "a chi, come) e chi li ha alla fine. Niente cambia mano senza un passaggio in scena.\n\n"
        "MOTIVO. Se il motivo principale del libro torna in più scene, 'motif_chain' dà a ogni "
        "ricorrenza un passo diverso: indizio, conflitto, costo, scelta, conseguenza — mai due "
        "volte lo stesso passo, mai la stessa tesi detta di nuovo.\n\n"
        "Immagini: una metafora-guida forte si usa nei punti di svolta, non si applica a tutto "
        "(persone, strade, automobili): il capitolo non è costruito su una sola parola.\n\n"
        "Novità: in 'already_known' elenca ciò che il lettore sa già e il capitolo non deve "
        "rispiegare. In 'motifs' i motivi che riprendi e la funzione NUOVA che hanno qui. Ogni "
        "affermazione di ricerca che usi va in 'claim_uses' della scena che la mette in scena "
        "(l'id fra parentesi quadre, es. K1a2b3c4d5) con la sua FUNZIONE: 'enigma' (serve a "
        "risolvere o porre un enigma), 'decisione' (cambia una scelta), 'rapporto_di_forza' "
        "(sposta chi comanda), 'semina' (un elemento che il lettore riconoscerà più avanti), "
        "'ambientazione' (rende vero il luogo o l'epoca), e in 'how' come entra nella scena. Un "
        "dettaglio che non ha nessuna di queste funzioni non entra: resta nell'archivio. Una "
        "già usata in un capitolo precedente solo se qui fa un lavoro nuovo. In 'canon' le "
        "regole del quaderno che questo capitolo deve rispettare (al massimo 12, copiate "
        "brevi).\n\n"
        f"Misura: la somma delle parole delle scene è la misura del capitolo. Una scena di "
        f"più di {cap} parole dividila in due scene consecutive (es. '3a', '3b'), ciascuna "
        "con la sua meta. Di norma da 5 a 8 scene.\n\n"
        "Ogni campo è UNA riga breve e concreta, senza frasi fatte. 'kind' è 'presente' o "
        f"'episodio_storico'. Scrivi in {pack.language}."
    )


def _user(pack: ContextPack) -> str:
    pezzi = [
        f"# Il quaderno del libro\n{pack.notebook}",
        (f"# Opere simili da cui attingere (idee, immagini, scene)\n{pack.landscape}"
         if pack.landscape else ""),
        pack.outline_block(),
        pack.chapter_block(),
        pack.start_block(),
        pack.previous_tail_block(),
        pack.told_block(),
        pack.motifs_block(),
        pack.inventions_block(),
        pack.claims_block(),
        pack.decisions_block(),
        pack.memory_block(),
    ]
    return "\n\n".join(p for p in pezzi if p)


def plan(project: BookProject, state: ForgeState, client: Any, pack: ContextPack, *,
         cap: int, model: str | None = None, trial: bool = False,
         check_model: str | None = None) -> Plan:
    """Il piano delle scene: dal modello, controllato, riparato una volta se serve. Non solleva:
    se il modello non risponde, il piano si ricava dai punti del capitolo e lo dice.

    Il controllo è doppio: quello deterministico (:func:`gate`) e quello di continuità del piano
    (:func:`timeline.check_plan`, con ``check_model`` se l'autore ne ha indicato uno per i
    controlli), che guarda orari, scadenze, porte, oggetti, il ponte e i fili aperti.
    """
    metodo = None
    messaggi = _messages(pack, cap)
    piano, perche = _ask(client, messaggi, pack, model)
    if piano is not None and pack.narrative and state is not None:
        metodo = _plan_methodology(project, state, client, piano, pack,
                                   model=check_model or model, trial=trial)
    if piano is not None:
        controlla = _checker(client, pack, cap, metodo, model=model, check_model=check_model)
        piano, piano.defects = _improve(client, messaggi, piano, controlla(piano), controlla,
                                        model)
    else:
        piano = from_points(pack)
        piano.defects = ["[blocker] il modello non ha restituito un piano: scene ricavate dai "
                         "punti, senza ancore, oggetti, ponte né catena del motivo"
                         + (f" (motivo: {perche})" if perche else "")]
    normalise(piano, pack, cap)
    if metodo is not None:
        piano.registers_block = metodo.canon_block()
    piano.chapter, piano.title = pack.number, str(pack.chapter.get("title") or "")
    piano.trial = trial
    piano.created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save(project, piano)
    return piano


def _messages(pack: ContextPack, cap: int) -> list[dict[str, str]]:
    return [{"role": "system", "content": _system(pack, cap)},
            {"role": "user", "content": _user(pack)}]


def _checker(client: Any, pack: ContextPack, cap: int, metodo: Any, *, model: str | None,
             check_model: str | None):
    """Il controllo di un piano: deterministico, di continuità e dei conti dei registri."""
    from bookwriter.forge import timeline as tl

    def controlla(p: Plan) -> list[str]:
        resolve_claim_ids(p, pack)
        return gate(p, pack, cap) + verify_blockers(
            client, p, pack, tl.check_plan(client, p, pack, model=check_model or model)
            + _register_defects(client, p, metodo, check_model or model),
            model=check_model or model)

    return controlla


#: Il segno di un blocker che la verifica indipendente ha confermato: non si verifica di nuovo.
CONFIRMED = "(confermato dalla verifica indipendente)"

_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"n": {"type": "integer"}, "blocking": {"type": "boolean"},
                       "quote_a": _T, "quote_b": _T, "why": _T},
        "required": ["n", "blocking", "quote_a", "quote_b", "why"]}}},
    "required": ["verdicts"],
}


def _needs_verification(difetto: str) -> bool:
    """I blocker che vengono dal giudizio di un modello: la continuità del piano e i luoghi dei
    registri (un'entità «in due posti» può essere una metafora presa alla lettera). I conti dei
    registri (somme, conservazione) e i difetti del controllo deterministico no."""
    if severity_of(difetto) != "blocker" or CONFIRMED in difetto:
        return False
    return "continuità del piano" in difetto or "luoghi nello stesso momento" in difetto


def _norm(testo: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (testo or "").lower()).split())


def _cited(citazione: str, testo: str) -> bool:
    """Una citazione è nel testo se c'è alla lettera, a meno di maiuscole, punteggiatura e spazi;
    o se un suo tratto lungo almeno l'80% c'è così."""
    from difflib import SequenceMatcher

    c, t = _norm(citazione), _norm(testo)
    if len(c) < 8:
        return False
    if c in t:
        return True
    m = SequenceMatcher(None, c, t, autojunk=False).find_longest_match(0, len(c), 0, len(t))
    return m.size >= 0.8 * len(c)


def verify_blockers(client: Any, piano: Plan, pack: ContextPack, difetti: list[str], *,
                    model: str | None = None) -> list[str]:
    """La verifica indipendente dei blocker segnalati da un modello. Un controllo fatto da un
    modello trova quasi sempre un «blocker» in qualunque piano: nella prova v6 del cap. 11 ogni
    riparazione toglieva il suo e il controllo ne trovava uno nuovo (un giudizio di drammaturgia,
    una metafora presa per un luogo), e il cancello non si apriva più. Qui un secondo giudizio,
    senza il primo come autorità, dice se il difetto rende davvero impossibile una stesura
    coerente e lo prova citando alla lettera i due passi che si contraddicono. Il codice controlla
    che le citazioni ci siano. Confermato, resta blocker; altrimenti diventa maggiore: non si
    butta, la riparazione se ne occupa ancora."""
    dubbi = [i for i, d in enumerate(difetti) if _needs_verification(d)]
    if not dubbi or client is None:
        return difetti
    testo = "\n\n".join(x for x in (pack.start_block(), _plan_text(piano)) if x)
    sistema = (
        "Sei un revisore indipendente. Un controllo automatico ha segnalato come BLOCCANTI "
        "alcuni problemi nel piano delle scene di un capitolo; spesso esagera. Per ciascuno "
        "decidi se è davvero bloccante ('blocking'): due fatti del piano o dello stato di "
        "partenza che non possono essere veri insieme, una cronologia impossibile, la "
        "continuità con il capitolo precedente rotta, il canone violato. NON è bloccante: un "
        "giudizio di drammaturgia (un obiettivo difficile, poca tensione, una scena debole), "
        "una metafora o un'immagine presa alla lettera, un'immagine ripetuta, una cosa che la "
        "stesura risolve con una frase. Se è bloccante, copia ALLA LETTERA in 'quote_a' e "
        "'quote_b' i due passi (dal piano o dallo stato di partenza) che si contraddicono; "
        "altrimenti lasciali vuoti. In 'why' una riga. Rispondi per ogni problema col suo "
        "numero 'n'.")
    elenco = "\n".join(f"{k}. {difetti[i]}" for k, i in enumerate(dubbi, 1))
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": f"# Il piano\n{testo}\n\n"
                                                          f"# I problemi segnalati\n{elenco}"}],
                             schema=_VERIFY_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - senza verifica il blocker resta com'era
        return difetti
    giudizi = {int(v.get("n") or 0): v for v in (r.get("verdicts") or [])
               if isinstance(v, dict)} if isinstance(r, dict) else {}
    fuori = list(difetti)
    for k, i in enumerate(dubbi, 1):
        v = giudizi.get(k)
        if v is None:
            continue  # senza giudizio resta com'era
        provato = bool(v.get("blocking")) and _cited(str(v.get("quote_a") or ""), testo)             and _cited(str(v.get("quote_b") or ""), testo)
        corpo = _TAG.sub("", difetti[i])
        perche = str(v.get("why") or "").strip()
        if provato:
            fuori[i] = f"[blocker] {corpo} {CONFIRMED}"
        else:
            motivo = perche if not v.get("blocking") else "le citazioni non sono nel piano"
            fuori[i] = (f"[major] {corpo} (segnalato come bloccante, non confermato dalla "
                        f"verifica: {motivo})")
    return fuori


def _improve(client: Any, messaggi: list[dict[str, str]], piano: Plan, difetti: list[str],
             controlla, model: str | None, note=None) -> tuple[Plan, list[str]]:
    """Si ripara finché restano blocker o maggiori. Una riparazione si tiene se il rischio
    residuo scende (prima i blocker, poi i maggiori, poi i minori): il numero dei difetti non
    conta, perché un controllo fatto da un modello ne trova di più o di meno a ogni giro anche a
    parità di piano (prova v4: riparazioni utili scartate così)."""
    def conto(r: tuple[int, int, int]) -> str:
        return f"{r[0]} bloccanti, {r[1]} maggiori, {r[2]} minori"

    for giro in range(1, MAX_REPAIRS + 1):
        gravi = [d for d in difetti if severity_of(d) in ("blocker", "major")]
        if not gravi:
            break
        riparato = _repair(client, messaggi, piano, gravi, model)
        if riparato is None:
            # una risposta persa (schema, rete) non chiude le riparazioni: si riprova
            if note:
                note(f"Riparazione {giro} di {MAX_REPAIRS}: il modello non ha risposto")
            continue
        riparato.origin = "riparato"
        nuovi = controlla(riparato)
        tenuta = risk(nuovi) < risk(difetti)
        if note:
            nuovi_bloccanti = [d for d in nuovi if severity_of(d) == "blocker"]
            note(f"Riparazione {giro} di {MAX_REPAIRS}: da {conto(risk(difetti))} a "
                 f"{conto(risk(nuovi))}: " + ("tenuta" if tenuta else "scartata")
                 + "".join(f"\n  - {d[:240]}" for d in nuovi_bloccanti))
        if tenuta:
            piano, difetti = riparato, nuovi
    return piano, difetti


def repair(project: BookProject, client: Any, pack: ContextPack, piano: Plan, *, cap: int,
           model: str | None = None, trial: bool = False,
           check_model: str | None = None, note=None) -> Plan:
    """Ripara un piano già fatto solo dove ha difetti: le scene sane restano com'erano, e si
    rifanno soltanto quelle che i difetti nominano (con il ponte e i fili, se servono). È la via
    per un piano bloccato: ripianificare da capo costa il piano intero e ne porta uno nuovo, con
    difetti nuovi (prova v6 del cap. 11)."""
    from bookwriter.forge import methodology as md

    metodo = md.load_latest(project, pack.number, "piano", trial=trial) if pack.narrative         else None
    controlla = _checker(client, pack, cap, metodo, model=model, check_model=check_model)
    # i blocker di un piano salvato prima della verifica indipendente si verificano adesso
    difetti = verify_blockers(client, piano, pack, list(piano.defects),
                              model=check_model or model) if piano.defects else controlla(piano)
    piano, difetti = _improve(client, _messages(pack, cap), piano, difetti, controlla, model,
                              note=note)
    piano.defects = difetti
    piano.waiver = ""
    normalise(piano, pack, cap)
    if metodo is not None:
        piano.registers_block = metodo.canon_block()
    piano.chapter, piano.title = pack.number, str(pack.chapter.get("title") or "")
    piano.trial = trial
    save(project, piano)
    return piano


#: La riparazione chiede solo quello che cambia: il piano intero (decine di KB di JSON) sta al
#: limite di quello che un modello locale scrive in una risposta, e nella prova v3 del cap. 11
#: due riparazioni su due tornavano con gli stessi difetti.
REPAIR_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "start": _T,
        "bridge": PLAN_SCHEMA["properties"]["bridge"],
        "threads": PLAN_SCHEMA["properties"]["threads"],
        # Delle scene si chiedono solo id e posto: i campi omessi restano quelli della scena
        # corretta. Il server controlla lo schema dopo la generazione e un solo campo mancante
        # faceva perdere l'intera riparazione (prova v5: «'danger' is a required property»).
        "scenes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {**_SCENE_PROPS, "after": _T},
            "required": ["id", "after"]}},
        "remove": _L,
    },
    # ponte, inizio, fili e scene da togliere servono solo se cambiano: chiederli comunque
    # faceva perdere la riparazione a chi li ometteva (prova v6b: «'bridge' is a required
    # property», sette minuti persi)
    "required": ["scenes"],
}


def _scene_ids_in(difetti: list[str], piano: Plan) -> list[str]:
    """Le scene che i difetti nominano («la scena 3», «le scene 11e e 11f», «Scene 1»)."""
    ids = {sc_.id for sc_ in piano.scenes}
    trovati: list[str] = []
    for d in difetti:
        # anche in inglese, come li scrive il controllo di continuità («Scene 1 → Scene 3»)
        for m in re.finditer(r"scen[ae]s?\s+([\w+.]*\d[\w+.]*)"
                             r"(?:\s+(?:e|and)\s+([\w+.]*\d[\w+.]*))?", d, re.IGNORECASE):
            for g in (x.rstrip(".") for x in m.groups() if x):
                if g in ids and g not in trovati:
                    trovati.append(g)
    return trovati


def _repair(client: Any, messaggi: list[dict[str, str]], piano: Plan, difetti: list[str],
            model: str | None) -> Plan | None:
    """Ripara il piano chiedendo solo le scene da cambiare (intere, stesso id), quelle da togliere,
    e il ponte o i fili aperti se vanno cambiati; poi le mette al loro posto."""
    coinvolte = _scene_ids_in(difetti, piano)
    richiesta = (
        "Il piano ha questi difetti:\n" + "\n".join(f"- {d}" for d in difetti)
        + ("\n\nLe scene coinvolte: " + ", ".join(coinvolte) + "." if coinvolte else "")
        + "\n\nCorreggili riprogettando le scene che li hanno (non basta cambiare le parole). "
        "Rispondi SOLO con quello che cambia: in 'scenes' le scene nuove o corrette, ciascuna "
        "INTERA e con il suo id (lo stesso id per correggere una scena; un id nuovo per "
        "aggiungerne una, con in 'after' l'id della scena dopo cui va); in 'remove' gli id "
        "delle scene da togliere; 'start', 'bridge' e 'threads' solo se vanno cambiati, "
        "altrimenti vuoti. Le scene che non nomini restano come sono.")
    domanda = [*messaggi,
               {"role": "assistant", "content": json.dumps(_as_answer(piano),
                                                           ensure_ascii=False)},
               {"role": "user", "content": richiesta}]
    try:
        r = client.chat_json(domanda, schema=REPAIR_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - senza riparazione resta il piano com'era
        return None
    if not isinstance(r, dict):
        return None
    nuovo = plan_from_dict(_as_answer(piano))
    nuovo.origin = "riparato"
    vecchie = {str(d.get("id")): d for d in _as_answer(piano)["scenes"]}
    togli = {str(x).strip() for x in r.get("remove") or []}
    nuovo.scenes = [x for x in nuovo.scenes if x.id not in togli]
    for d in r.get("scenes") or []:
        if not isinstance(d, dict) or not str(d.get("id") or "").strip():
            continue
        dati = {k: v for k, v in d.items() if v not in ("", None, [], {})}
        scena = _scene({**vecchie.get(str(d["id"]).strip(), {}), **dati})
        dopo = str(d.get("after") or "").strip()
        esistente = next((i for i, x in enumerate(nuovo.scenes) if x.id == scena.id), None)
        if esistente is not None:
            nuovo.scenes[esistente] = scena
        else:
            posto = next((i + 1 for i, x in enumerate(nuovo.scenes) if x.id == dopo),
                         len(nuovo.scenes))
            nuovo.scenes.insert(posto, scena)
    if str(r.get("start") or "").strip():
        nuovo.start = str(r["start"]).strip()
    ponte = r.get("bridge") if isinstance(r.get("bridge"), dict) else {}
    if any(str(v or "").strip() for v in ponte.values()):
        nuovo.bridge = {k: str(ponte.get(k) or "").strip()
                        for k in ("from", "clue", "decision", "movement", "new_goal")}
    fili = [{"thread": str(t.get("thread") or "").strip(),
             "handling": str(t.get("handling") or "").strip()}
            for t in r.get("threads") or [] if isinstance(t, dict)]
    if fili:
        nuovo.threads = fili
    return nuovo if nuovo.scenes else None


def _plan_text(piano: Plan) -> str:
    """Il piano per chi lo legge come testo: scene con le loro schede, oggetti, catena."""
    righe = [plan_markdown(piano), ""]
    for s in piano.scenes:
        righe += [f"## Scena {s.id}", s.card(), ""]
    for o in piano.objects:
        righe.append(f"- oggetto {o.get('object')}: {o.get('origin')}; {o.get('transfers')}")
    return "\n".join(righe)


def _plan_methodology(project: BookProject, state: ForgeState, client: Any, piano: Plan,
                      pack: ContextPack, *, model: str | None, trial: bool):
    """La metodologia del capitolo, generata sul primo piano e salvata versionata: quali entità
    tracciare (schede, nodi, copie…) e con quali invarianti."""
    from bookwriter.forge import methodology as md

    try:
        lavoro = md.work_profile(state)
        progetto = md.project_profile(project, state, client, model=model)
        metodo = md.generate(client, _plan_text(piano), scope="piano", chapter=pack.number,
                             work=lavoro, project=progetto,
                             objective="pianificare il capitolo senza contraddizioni nei conti, "
                                       "negli stati e nella cronologia",
                             canon=pack.start_block(), model=model)
        md.save(project, metodo, trial=trial)
        return metodo
    except Exception:  # noqa: BLE001 - senza metodologia restano gli altri controlli
        return None


def _register_defects(client: Any, piano: Plan, metodo: Any, model: str | None) -> list[str]:
    """I conti del piano contro gli invarianti dei registri (il codice verifica)."""
    if metodo is None or not metodo.registers:
        return []
    from bookwriter.forge import methodology as md

    osservazioni, _errori = md.extract(client, metodo, [("Il piano", _plan_text(piano))],
                                       model=model)
    return md.failures_text(md.check(metodo, osservazioni))


def _edits(a: str, b: str) -> int:
    """La distanza di modifica fra due ID (Levenshtein)."""
    prima = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        corrente = [i]
        for j, cb in enumerate(b, 1):
            corrente.append(min(prima[j] + 1, corrente[j - 1] + 1, prima[j - 1] + (ca != cb)))
        prima = corrente
    return prima[-1]


def resolve_claim_ids(piano: Plan, pack: ContextPack) -> None:
    """Gli ID di ricerca che non esistono si risolvono solo se il registro del capitolo conferma
    un candidato unico e vicino (al più due caratteri di differenza), con parole in comune fra
    l'uso dichiarato e l'affermazione; altrimenti restano non risolti e il controllo lo dice.
    La risoluzione si registra nel piano: niente si corregge in silenzio."""
    noti = {c.id: c for c in pack.claims}
    for s in piano.scenes:
        for u in s.claim_uses or [{"claim_id": k} for k in s.claim_ids]:
            k = u.get("claim_id", "")
            if not k or k in noti:
                continue
            vicini = [c for i, c in noti.items() if _edits(k, i) <= 2]
            contesto = _tokens(f"{u.get('how', '')} {s.goal} {s.clue}")
            if len(vicini) == 1 and (not contesto or contesto & _tokens(vicini[0].text)
                                     or not u.get("how")):
                giusto = vicini[0].id
                piano.resolutions.append({"from": k, "to": giusto, "why": (
                    f"scena {s.id}: unico ID del registro a distanza {_edits(k, giusto)}")})
                u["claim_id"] = giusto
                s.claim_ids = [giusto if x == k else x for x in s.claim_ids]
    for s in piano.scenes:
        s.claim_ids = list(dict.fromkeys(s.claim_ids))


def _ask(client: Any, messaggi: list[dict[str, str]], pack: ContextPack,
         model: str | None) -> tuple[Plan | None, str]:
    """Il piano, o None con il perché: chi chiama ripiega sui punti, ma il motivo finisce nel
    difetto bloccante, così l'autore sa se è caduta la connessione o il modello ha risposto male."""
    try:
        r = client.chat_json(messaggi, schema=PLAN_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001 - chi chiama ripiega sui punti
        return None, f"{type(exc).__name__}: {str(exc)[:300]}"
    if not isinstance(r, dict) or not r.get("scenes"):
        return None, "la risposta non contiene scene"
    piano = plan_from_dict({k: v for k, v in r.items() if k in Plan.__dataclass_fields__})
    if not piano.scenes:
        return None, "nessuna scena utilizzabile nella risposta"
    return piano, ""


def _as_answer(piano: Plan) -> dict[str, Any]:
    d = asdict(piano)
    for chiave in ("chapter", "title", "defects", "origin", "trial", "created", "resolutions",
                   "waiver", "registers_block"):
        d.pop(chiave, None)
    for s in d["scenes"]:
        s.pop("after", None)
        s.pop("claim_ids", None)
    return d


def from_points(pack: ContextPack) -> Plan:
    """Il piano di ripiego: una scena per punto (o per la funzione del capitolo)."""
    punti = [str(p) for p in (pack.chapter.get("points") or [])] or \
        [str(pack.chapter.get("does") or "")]
    scene = [Scene(id=str(i + 1), goal=p) for i, p in enumerate(punti)]
    # il materiale va alla scena del punto per cui è stato trovato
    for c in pack.claims:
        if c.chapter == pack.number and 0 <= c.point < len(scene):
            scene[c.point].claim_ids.append(c.id)
    return Plan(goal=str(pack.chapter.get("does") or ""), origin="dai_punti", scenes=scene)


# --------------------------------------------------------------------------- #
#  il controllo
# --------------------------------------------------------------------------- #
def _tokens(testo: str) -> set[str]:
    return {w for w in content_words(testo) if len(w) > 3}


def _nomi_di_luogo(testo: str, escludi: set[str]) -> set[str]:
    """I nomi propri di un luogo («Lyon», «Dendera»): le parole maiuscole, senza i nomi del
    protagonista; a inizio frase solo se non sono parole vuote («The», «Toward»)."""
    nomi: set[str] = set()
    for frase in re.split(r"[.!?;:()\[\]]+", testo or ""):
        parole = [w for w in mx.words(frase) if len(w) > 2]
        if parole and parole[0][0].isupper() and mx.content_words(parole[0]):
            nomi.add(parole[0].lower())
        nomi |= {w.lower() for w in parole[1:] if w[0].isupper()}
    return nomi - escludi


def _stesso_luogo(a: str, b: str, pack: ContextPack) -> bool:
    """Due luoghi sono lo stesso se condividono un nome proprio; le parole comuni contano solo
    quando uno dei due non ne ha. Prova v5 del cap. 11: «Regional conservation office in Lyon» e
    «… behind the conservation office» a Dendera sembravano lo stesso posto, e il ponte non è
    stato chiesto."""
    escludi = {p.lower() for p in mx.name_tokens(pack.protagonist)} if pack.protagonist else set()
    na, nb = _nomi_di_luogo(a, escludi), _nomi_di_luogo(b, escludi)
    if na and nb:
        return bool(na & nb)
    return bool(_tokens(a) & _tokens(b))


def nomi_protagonista(pack: ContextPack) -> set[str]:
    return {mx._stem(p) for p in mx.name_tokens(pack.protagonist)} if pack.protagonist else set()


def gate(piano: Plan, pack: ContextPack, cap: int) -> list[str]:
    """I difetti del piano che si possono vedere senza leggere la prosa."""
    difetti: list[str] = []
    if not 2 <= len(piano.scenes) <= 14:
        difetti.append(f"il capitolo ha {len(piano.scenes)} scene: di norma ne servono da 5 a 8")
    for s in piano.scenes:
        mancano = [nome for nome, v in (("obiettivo", s.goal), ("ostacolo", s.obstacle),
                                        ("decisione", s.decision), ("conseguenza", s.consequence))
                   if not v.strip()]
        if mancano:
            difetti.append(f"la scena {s.id} non ha: {', '.join(mancano)}")
        elif (s.belief_before.strip().lower() == s.belief_after.strip().lower()
              and not s.clue.strip()):
            difetti.append(f"la scena {s.id} non cambia quello che il protagonista sa: indica "
                           "l'indizio e cosa crede dopo")
    presenti = [s for s in piano.scenes if s.kind == PRESENT]
    if pack.protagonist and pack.narrative:
        nomi = {p.lower() for p in mx.name_tokens(pack.protagonist)}
        assenti = [s.id for s in presenti
                   if not nomi & {w.lower() for w in mx.words(f"{s.characters} {s.pov} {s.goal}")}]
        if assenti and len(assenti) == len(presenti):
            difetti.append(f"{pack.protagonist} non è in nessuna scena del presente "
                           f"({', '.join(assenti)}). Il presente del romanzo è la sua storia: "
                           "mettilo in 'characters' e dagli l'obiettivo e la decisione")
        elif assenti:
            difetti.append(f"{pack.protagonist} non è in scena in: {', '.join(assenti)}. Il "
                           "presente del romanzo è la sua storia: mettilo in 'characters' e dagli "
                           "l'obiettivo e la decisione")
        if presenti and not any(nomi & {w.lower() for w in mx.words(s.decision)}
                                for s in presenti):
            difetti.append(f"in nessuna scena del presente decide {pack.protagonist}: almeno una "
                           "decisione che costa dev'essere sua")
    # un piano che gira attorno a una parola invece che a eventi («the pattern is filed…»)
    campi = [f"{s.goal} {s.decision} {s.consequence}" for s in piano.scenes]
    if len(campi) >= 3:
        conti = Counter(w for c in campi for w in {mx._stem(x) for x in mx.content_words(c)})
        ossessive = [w for w, n in conti.most_common() if n >= max(3, (len(campi) * 2 + 2) // 3)
                     and not nomi_protagonista(pack) & {w}]
        if ossessive:
            difetti.append("obiettivi, decisioni e conseguenze ruotano attorno alle stesse parole "
                           f"({', '.join(ossessive[:5])}) invece che a eventi diversi: "
                           "ogni scena deve cambiare qualcosa di concreto e diverso")
    difetti += _change_defects(piano)
    difetti += _reasoning_defects(piano, pack)
    difetti += _anchor_defects(piano, pack)
    if pack.previous is not None and presenti and pack.previous.end_state.place:
        prima = presenti[0]
        if not _stesso_luogo(prima.place, pack.previous.end_state.place, pack):
            ponte = piano.bridge or {}
            mancano = [nome for nome, k in (("indizio", "clue"), ("decisione", "decision"),
                                            ("spostamento", "movement"),
                                            ("nuovo obiettivo", "new_goal"))
                       if not str(ponte.get(k) or "").strip()]
            nomi = {p.lower() for p in mx.name_tokens(pack.protagonist)} if pack.protagonist \
                else set()
            sua = not nomi or bool(nomi & {w.lower() for w in mx.words(ponte.get("decision", ""))})
            if mancano or not sua:
                difetti.append(
                    f"il capitolo precedente lascia il protagonista a "
                    f"«{pack.previous.end_state.place}» e la prima scena del presente è a "
                    f"«{prima.place}»: in 'bridge' serve la catena indizio → decisione del "
                    "protagonista → spostamento → nuovo obiettivo"
                    + (f" (manca: {', '.join(mancano)})" if mancano else
                       f" (la decisione dev'essere di {pack.protagonist}, non subita)"))
    if pack.open_threads:
        trattati = [t for t in piano.threads if t.get("thread") and t.get("handling")]
        if len(trattati) < min(len(pack.open_threads), 8):
            difetti.append("i fili aperti del capitolo precedente vanno trattati uno per uno in "
                           "'threads' (risolto in quale scena, rimandato perché, chiuso): "
                           + "; ".join(pack.open_threads[:8]))
    conosciute = {c.id for c in pack.claims}
    for s in piano.scenes:
        ignote = [k for k in s.claim_ids if k not in conosciute]
        if ignote:
            difetti.append(f"la scena {s.id} cita affermazioni inesistenti: {', '.join(ignote)} "
                           "(ID non risolvibili nel registro: usa gli ID esatti fra parentesi "
                           "quadre)")
        senza = [u.get("claim_id", "") for u in s.claim_uses
                 if u.get("claim_id") in conosciute and u.get("function") not in CLAIM_FUNCTIONS]
        if senza:
            difetti.append(f"la scena {s.id} usa {', '.join(senza)} senza dire a cosa servono "
                           f"({', '.join(CLAIM_FUNCTIONS)}): senza funzione restano fuori")
    usate_prima = {c.id for c in pack.claims if any(n < pack.number for n in c.chapter_usage)}
    riprese = {m.get("motif", "").lower() for m in piano.motifs}
    for s in piano.scenes:
        for k in s.claim_ids:
            if k in usate_prima and not riprese:
                difetti.append(f"la scena {s.id} riusa {k}, già usata prima: dichiara in "
                               "'motifs' la funzione nuova")
                break
    return difetti


def _change_defects(piano: Plan) -> list[str]:
    """Scene che non cambiano niente di concreto, o che cambiano la stessa cosa di un'altra."""
    difetti: list[str] = []
    visti: list[tuple[str, str, set[str]]] = []  # (scena, variabile, parole del «dopo»)
    for i, s in enumerate(piano.scenes):
        cambi = s.valid_changes()
        if not cambi:
            difetti.append(f"la scena {s.id} non cambia nessuna variabile concreta "
                           f"({', '.join(VARIABLES)}): dichiara in 'changes' il prima e il dopo")
        for c in cambi:
            parole = _tokens(c["after"])
            for altra, variabile, dopo in visti:
                unione = parole | dopo
                if variabile == c["variable"] and unione and \
                        len(parole & dopo) / len(unione) >= SAME_CHANGE:
                    difetti.append(f"le scene {altra} e {s.id} cambiano la stessa cosa nello stesso "
                                   f"modo ({variabile}: «{c['after'][:80]}»): sono la stessa "
                                   "scena detta due volte, riprogettane una")
                    break
            visti.append((s.id, c["variable"], parole))
        if i < len(piano.scenes) - 1 and not s.next_question.strip():
            difetti.append(f"la scena {s.id} non lascia una domanda nuova ('next_question') che "
                           "porti alla scena dopo")
    return difetti


_DURATA = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:ore|ora|hours?|hrs?|h)\b", re.IGNORECASE)


def _anchor_defects(piano: Plan, pack: ContextPack) -> list[str]:
    """Le ancore del presente: giorno e ora per ogni scena, orari in ordine, viaggi possibili,
    apertura nel presente, nessuna scena doppia, il motivo in catena."""
    from bookwriter.forge import timeline as tl

    if not pack.narrative:
        return []
    difetti: list[str] = []
    presenti = [s for s in piano.scenes if s.kind == PRESENT]
    if pack.previous is not None and piano.scenes and piano.scenes[0].kind != PRESENT:
        difetti.append(f"il capitolo comincia con un episodio storico ({piano.scenes[0].id}): la "
                       "prima scena è nel presente e riparte da dove il capitolo precedente ha "
                       "lasciato il protagonista; l'episodio storico viene dopo")
    senza = [s.id for s in presenti if not s.day.strip() or not s.time.strip()]
    if senza:
        difetti.append(f"le scene {', '.join(senza)} non hanno l'ancora ('day' e 'time'): senza, "
                       "la stesura inventa orari che si contraddicono")
    for prima, dopo in zip(presenti, presenti[1:]):
        if not (_tokens(prima.place) & _tokens(dopo.place)) and not dopo.arrival.strip():
            difetti.append(f"fra la scena {prima.id} ({prima.place}) e la {dopo.id} "
                           f"({dopo.place}) il protagonista cambia luogo: 'arrival' dica da dove, "
                           "con che mezzo e quanto dura")
        m1, m2 = tl._minutes(prima.time), tl._minutes(dopo.time)
        stesso = prima.day.strip().lower() == dopo.day.strip().lower()
        if m1 is not None and m2 is not None and stesso:
            notte = m1 >= 17 * 60 and m2 <= 7 * 60
            intervallo = (m2 - m1) % (24 * 60) if notte else m2 - m1
            if intervallo < 0:
                difetti.append(f"la scena {dopo.id} ({dopo.time}) viene prima della {prima.id} "
                               f"({prima.time}) nello stesso giorno: metti gli orari in ordine")
            durata = _DURATA.findall(dopo.arrival)
            ore = sum(float(x.replace(",", ".")) for x in durata)
            if ore and intervallo >= 0 and intervallo < ore * 60:
                difetti.append(f"il viaggio per la scena {dopo.id} dura {ore:g} ore ma fra le "
                               f"ancore ({prima.time} → {dopo.time}) ne passano "
                               f"{intervallo // 60}: sposta l'ancora o cambia il viaggio")
    for i, a in enumerate(presenti):
        for b in presenti[i + 1:]:
            luoghi = _tokens(a.place) & _tokens(b.place)
            scopi = _tokens(a.goal) | _tokens(b.goal)
            if luoghi and scopi and len(_tokens(a.goal) & _tokens(b.goal)) / len(scopi) >= 0.5:
                difetti.append(f"le scene {a.id} e {b.id} si svolgono nello stesso luogo con lo "
                               "stesso obiettivo: fondile, o dai alla seconda un evento nuovo e "
                               "irreversibile")
    from bookwriter.forge import concepts as cn

    passi: dict[str, list[str]] = {}
    for m in piano.motif_chain:
        passi.setdefault(m.get("motif", "").lower(), []).append(
            cn.normalise("passo_motivo", m.get("step", "")) or m.get("step", ""))
    for motivo, lista in passi.items():
        doppi = sorted({x for x in lista if x and lista.count(x) > 1})
        if doppi:
            difetti.append(f"il motivo «{motivo}» ripete il passo {', '.join(doppi)}: ogni "
                           "ricorrenza fa un lavoro diverso (indizio, conflitto, costo, scelta, "
                           "conseguenza)")
    return difetti


def _reasoning_defects(piano: Plan, pack: ContextPack) -> list[str]:
    """Una scoperta del presente senza il ragionamento che il lettore deve poter seguire."""
    if not pack.narrative:
        return []
    difetti = []
    for s in piano.scenes:
        if s.kind != PRESENT or not s.clue.strip():
            continue
        r = s.reasoning or {}
        mancano = [nome for nome, k in (("cosa osserva", "observed"),
                                        ("come lo interpreta", "interpretation"),
                                        ("perché agisce così", "why"))
                   if not str(r.get(k) or "").strip()]
        if mancano:
            difetti.append(f"la scoperta della scena {s.id} ({s.clue[:60]}) non ha il ragionamento: "
                           f"manca {', '.join(mancano)}")
    return difetti


def normalise(piano: Plan, pack: ContextPack, cap: int) -> None:
    """Le correzioni che non chiedono un modello: id unici, affermazioni esistenti e con una
    funzione, la somma delle parole portata alla misura del capitolo, nessuna scena oltre il
    tetto per chiamata."""
    conosciute = {c.id for c in pack.claims}
    visti: set[str] = set()
    for i, s in enumerate(piano.scenes):
        if not s.id or s.id in visti:
            s.id = str(i + 1)
        visti.add(s.id)
        if s.claim_uses:
            # un'informazione senza funzione resta nell'archivio, non nel capitolo
            s.claim_uses = [u for u in s.claim_uses if u.get("claim_id") in conosciute
                            and u.get("function") in CLAIM_FUNCTIONS]
            s.claim_ids = list(dict.fromkeys(u["claim_id"] for u in s.claim_uses))
        s.claim_ids = [k for k in s.claim_ids if k in conosciute]
    budget = pack.budget
    if budget and piano.scenes:
        pesi = [max(1, s.words) for s in piano.scenes]
        if not any(s.words for s in piano.scenes):
            pesi = [1] * len(piano.scenes)
        totale = sum(pesi)
        for s, p in zip(piano.scenes, pesi, strict=True):
            s.words = max(250, int(round(budget * p / totale / 50.0) * 50))


# --------------------------------------------------------------------------- #
#  le unità di scrittura
# --------------------------------------------------------------------------- #
@dataclass
class Unit:
    """Una chiamata di scrittura: una scena intera o una sua parte."""

    scene: str
    part: int = 1
    parts: int = 1
    words: int = 0
    #: una parte nuova da inserire in un testo che esiste (completare un capitolo): senza
    #: titolo, e deve saldarsi a quello che c'è prima e dopo
    insert: bool = False

    @property
    def key(self) -> str:
        return self.scene if self.parts == 1 else f"{self.scene}/{self.part}"

    @property
    def titled(self) -> bool:
        """Se l'unità comincia con il titolo ``##`` della scena."""
        return self.part == 1 and not self.insert

    def task(self, scena: Scene) -> str:
        """Il lavoro di questa parte: le parti di una scena non si dividono le parole, si
        dividono il lavoro."""
        if self.insert:
            return ("Scrivi la parte che manca a questo capitolo, che si inserisce nel testo "
                    "esistente fra «Il capitolo finora» e «Quello che viene subito dopo»: deve "
                    "partire esattamente da dove finisce il testo prima e portare a quello "
                    "dopo, senza ripeterli e senza titolo.")
        if self.parts == 1:
            return ("Scrivi la scena intera: dall'ingresso nel luogo all'obiettivo, l'ostacolo, "
                    "l'indizio, la decisione e la conseguenza, fino al ponte verso la scena dopo.")
        if self.part == 1:
            return (f"Parte 1 di {self.parts} della scena: il luogo, chi c'è, l'obiettivo e "
                    f"l'ostacolo ({scena.obstacle}). Fermati quando l'ostacolo è davanti al "
                    "protagonista, senza risolverlo.")
        if self.part == self.parts:
            return (f"Parte {self.part} di {self.parts}, l'ultima: la decisione "
                    f"({scena.decision}), la conseguenza ({scena.consequence}) e il ponte verso la "
                    "scena dopo. Prosegui dalla parte precedente, senza titolo.")
        return (f"Parte {self.part} di {self.parts}: lo scontro con l'ostacolo e l'indizio "
                f"({scena.clue}), senza ancora la decisione. Prosegui dalla parte precedente, "
                "senza titolo.")


def units(piano: Plan, cap: int) -> list[Unit]:
    """Le chiamate di scrittura del capitolo, nell'ordine: ogni scena oltre il tetto in parti."""
    fuori: list[Unit] = []
    for s in piano.scenes:
        n = max(1, -(-s.words // max(1, cap)))
        for k in range(1, n + 1):
            fuori.append(Unit(s.id, k, n, max(200, int(s.words / n))))
    return fuori


# --------------------------------------------------------------------------- #
#  un capitolo rimasto corto: scene in più, non parole in più
# --------------------------------------------------------------------------- #
EXTRA_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {**_SCENE_PROPS, "after": _T},
        "required": [*_SCENE_REQUIRED, "after"]}}},
    "required": ["scenes"],
}


def extra_beats(client: Any, piano: Plan, pack: ContextPack, missing: int, written: str, *,
                model: str | None = None) -> list[Scene]:
    """Una o due scene da aggiungere a un capitolo rimasto sotto misura.

    Non «sviluppa ancora» niente: chiede un ostacolo in più, un dialogo che metta in conflitto
    due personaggi, una conseguenza che il piano prevedeva e il testo ha liquidato in una frase.
    """
    sistema = (
        "Un capitolo di romanzo è stato scritto secondo il suo piano ma è rimasto corto di circa "
        f"{missing} parole. NON si allunga quello che c'è: si aggiungono una o due scene brevi "
        "che il racconto rende necessarie — un ostacolo in più sulla strada del protagonista, un "
        "dialogo in cui due personaggi si scontrano su qualcosa di concreto, la conseguenza di "
        "una decisione che il testo ha liquidato in una frase, il ragionamento che porta a una "
        "scoperta. Ogni scena ha obiettivo, ostacolo, decisione e conseguenza, cambia una "
        "variabile concreta che le altre scene non cambiano già ('changes'), non ripete niente "
        "di ciò che il capitolo ha già detto, rispetta orari, oggetti e porte del capitolo, e in "
        "'after' dice l'id della scena dopo la quale va inserita. Non anticipare i capitoli "
        f"successivi. Scrivi in {pack.language}."
    )
    utente = "\n\n".join(p for p in (
        pack.chapter_block(),
        "# Il piano\n" + piano.outline(),
        f"# Il capitolo scritto, alla lettera\n{written}",
        pack.outline_block(),
    ) if p)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=EXTRA_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - senza scene in più il capitolo resta com'è, e lo si dice
        return []
    nuove = []
    for i, d in enumerate((r.get("scenes") or [])[:2] if isinstance(r, dict) else []):
        s = _scene(d)
        s.after = str(d.get("after") or "").strip()
        if not s.goal or piano.scene(s.after) is None:
            continue
        s.id = f"{s.after}+{i + 1}"
        s.claim_uses = [u for u in s.claim_uses if pack.claim(u.get("claim_id", ""))
                        and u.get("function") in CLAIM_FUNCTIONS]
        s.claim_ids = [k for k in s.claim_ids if pack.claim(k)
                       and (not s.claim_uses or s.use_of(k) is not None)]
        s.words = max(300, min(s.words or missing // 2, missing))
        nuove.append(s)
    return nuove


def scene_words(testo: str) -> int:
    from bookwriter.forge.metrics import count_words

    return count_words(testo)


def plan_markdown(piano: Plan) -> str:
    """Il piano in forma leggibile, per il resoconto editoriale."""
    righe = [f"**Obiettivo:** {piano.goal}", f"**Domanda drammatica:** {piano.dramatic_question}",
             f"**Svolta:** {piano.turn}", f"**Partenza:** {piano.start}",
             f"**Arrivo:** {piano.end}"]
    if piano.bridge_text():
        righe.append(f"**Ponte dal capitolo prima:** {piano.bridge_text()}")
    for t in piano.threads:
        righe.append(f"**Filo aperto** «{t.get('thread', '')}»: {t.get('handling', '')}")
    righe.append("")
    for s in piano.scenes:
        righe += [f"- **{s.id}** — {s.line()[len(s.id) + 2:]}"]
        cambi = s.valid_changes()
        if cambi:
            righe.append("  - cambia: " + "; ".join(f"{c['variable']} → {c['after']}"
                                                     for c in cambi))
        if s.next_question:
            righe.append(f"  - domanda nuova: {s.next_question}")
    if piano.defects:
        righe += ["", "**Difetti del piano rimasti dopo la riparazione:**",
                  *[f"- {d}" for d in piano.defects]]
    return "\n".join(r for r in righe if r is not None)
