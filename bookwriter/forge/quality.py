"""La qualità di un capitolo e di un libro: voti da 0 a 10, consapevoli del genere e dello scopo.

Nasce dalla seconda analisi editoriale del cap. 11 (``docs/REVIEW_CAPITOLO_11_V2.md``): avanzamento,
ripetizione di significato, continuità, tracciabilità delle fonti, architettura, protagonista e
indagine, uso della ricerca, metafore, misura. A questi l'autore ha aggiunto quello che giudica un
lettore professionale: stile (con il richiamo agli autori che ricorda), capacità narrativa,
attenzione del lettore, argomentazioni, leggibilità, interesse, godibilità, fluidità, coerenza,
una valutazione globale e una stima del potenziale di pubblico.

**Il genere e lo scopo cambiano il metro.** Un manuale non deve avere suspense ma chiarezza e
progressione; un testo accademico non deve essere «godibile» come un thriller ma rigoroso e
tracciabile; un romanzo non cita le fonti nel testo, ma la sua storia deve avanzare a ogni scena.
Per questo ogni criterio ha, per tipo di libro (:data:`KINDS`), un peso (0 = non si applica), una
domanda propria e il segno di critico; e il valutatore riceve lo scopo dell'autore, il lettore e
l'effetto voluto, dal quaderno.

**I voti hanno una scala dichiarata** (:data:`SCALE`) e ogni voto porta una citazione come prova e
i problemi con la correzione minima: un numero senza prova non serve a correggere niente. Alcune
misure non chiedono un modello (leggibilità: Gulpease per l'italiano, Flesch per l'inglese;
misura rispetto al budget; paragrafi simili; quota di dialogo) e arrivano al valutatore come dati.

**Il ciclo di miglioramento** (:func:`improve_chapter`): valuta; per ogni problema dei criteri
sotto soglia trova la parte che tocca — il paragrafo citato, o al più tre paragrafi indicati dal
modello — e riscrive **solo quella** (:func:`apply_fixes`); poi rivaluta il capitolo intero. La
versione nuova si tiene solo se migliora davvero, e ogni versione resta su disco
(:func:`versions`, :func:`restore`). Si ferma quando la qualità è accettata (:class:`Thresholds`),
quando un giro non migliora, o al tetto dei giri delle impostazioni: oltre il tetto ogni giro lo
autorizza l'autore, uno per volta. Un giudizio
dello stesso modello che ha scritto non è una verifica indipendente: i voti possono venire da un
altro modello (``llm.models.review``).

La **stima del potenziale di pubblico** non è una previsione: è l'opinione motivata di un lettore
professionale, con i titoli di confronto e i rischi. Si dice così anche nell'interfaccia.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bookwriter.forge import metrics as mx

# --------------------------------------------------------------------------- #
#  i tipi di libro
# --------------------------------------------------------------------------- #
KINDS: dict[str, str] = {
    "romanzo": "romanzo / narrativa",
    "racconti": "racconti",
    "memoir": "memoir / biografia",
    "divulgativo": "saggio divulgativo",
    "saggio": "saggio",
    "manuale": "manuale / guida",
    "accademico": "testo accademico",
}
NARRATIVE_KINDS = frozenset({"romanzo", "racconti", "memoir"})
_KIND_PATTERNS = [
    ("accademico", r"accadem|academic|scientific|scientif|tesi di|dissertation|paper"),
    ("manuale", r"manual|guida|handbook|how.?to|tutorial|ricettario|istruzion"),
    ("divulgativo", r"divulga|popular science|saggio divulgativo"),
    ("memoir", r"memoir|autobiogra|biografi|memorie"),
    ("racconti", r"raccont|short stor|stories|novelle"),
    ("saggio", r"saggio|essay|non.?fiction|saggistica|trattato"),
    ("romanzo", r"romanz|novel|giallo|thriller|fantasy|narrativ|fiction|noir|horror|mystery"),
]


def kind_of(form: str, narrative_hint: bool = True) -> str:
    """Il tipo di libro dalla voce «genere e forma» del quaderno."""
    testo = (form or "").lower()
    for chiave, schema in _KIND_PATTERNS:
        if re.search(schema, testo):
            return chiave
    return "romanzo" if narrative_hint else "saggio"


# --------------------------------------------------------------------------- #
#  i criteri
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Criterion:
    key: str
    group: str
    label: str
    question: str
    #: etichetta e domanda per i libri non narrativi, se diverse
    label_essay: str = ""
    question_essay: str = ""
    #: peso per tipo di libro (assente = 1; 0 = il criterio non si applica)
    weights: tuple[tuple[str, float], ...] = ()
    #: i tipi in cui il criterio è critico (sotto soglia, il capitolo non è accettato)
    critical: frozenset[str] = frozenset()
    #: conta nella media globale (la stima del potenziale no: è un'opinione a parte)
    in_global: bool = True

    def weight(self, kind: str) -> float:
        if kind in NARRATIVE_KINDS:
            # la rubrica della revisione narrativa (specifica editoriale del 25/10/2026): i suoi
            # undici criteri pesano per la loro percentuale; gli altri restano visibili con 1
            return NARRATIVE_WEIGHTS.get(self.key, dict(self.weights).get(kind, 1.0)
                                         and 1.0)
        return dict(self.weights).get(kind, 1.0)

    def applies(self, kind: str) -> bool:
        return self.weight(kind) > 0

    def label_for(self, kind: str) -> str:
        return self.label_essay if kind not in NARRATIVE_KINDS and self.label_essay else self.label

    def question_for(self, kind: str) -> str:
        return (self.question_essay if kind not in NARRATIVE_KINDS and self.question_essay
                else self.question)


#: I pesi della rubrica per romanzo, racconti e memoir (in percento: sommano 100). Vengono dalla
#: specifica editoriale della revisione (``docs/REVIEW_WORKFLOW_CLAUDE_CODE_THE_RED_THREAD_CH11_V3.md``
#: §5), scelta dall'autore il 25/10/2026 con le schede 0-10. Voce, stile e leggibilità (6%) si
#: dividono fra «stile» e «leggibilità».
NARRATIVE_WEIGHTS: dict[str, float] = {
    "causalita": 15, "cronologia": 12, "continuita": 12, "oggetti": 10, "struttura": 10,
    "suspense": 10, "densita": 8, "ripetizioni": 7, "stile": 3, "leggibilita": 3, "misura": 5,
    "fonti": 5,
}

_TUTTI = frozenset(KINDS)
_NARR = NARRATIVE_KINDS
_SAGGI = frozenset({"saggio", "divulgativo", "accademico"})
_NON_NARR_ZERO = (("saggio", 0.0), ("divulgativo", 0.0), ("manuale", 0.0), ("accademico", 0.0))

CRITERIA: list[Criterion] = [
    # ---- struttura e contenuto -----------------------------------------------------------
    Criterion("avanzamento", "struttura", "Avanzamento della storia",
              "Ogni scena fa avanzare la storia o il mistero con un cambiamento verificabile "
              "(conoscenza, rischio, possesso, rapporto, destinazione, fuga), invece di "
              "esprimere di nuovo la stessa tesi?",
              "Avanzamento dell'argomento",
              "Ogni sezione aggiunge qualcosa di nuovo all'argomento (un'idea, una prova, un "
              "passo del procedimento), invece di ripetere quanto già detto?",
              critical=_TUTTI),
    Criterion("ripetizioni", "struttura", "Ripetizioni di significato",
              "Le idee si ripetono senza novità, anche con parole o metafore diverse? Voto alto "
              "= ogni idea è detta una volta, e torna solo se cambia o produce un effetto nuovo.",
              critical=_TUTTI),
    Criterion("struttura", "struttura", "Funzione e distinzione delle scene",
              "Ogni scena modifica situazione, conoscenza, rischio, relazione o obiettivo, con "
              "obiettivo, ostacolo, decisione, conseguenza e una domanda nuova per la scena dopo? "
              "Nessuna scena duplica la funzione di un'altra (stesso ambiente, stesse "
              "informazioni, stesso effetto)?",
              "Struttura delle sezioni",
              "Le sezioni sono ordinate (premessa, sviluppo, prove o passi, conclusione), con "
              "titoli e passaggi che il lettore segue senza perdersi?",
              critical=frozenset({"manuale", "accademico"})),
    Criterion("personaggi", "struttura", "Protagonista e indagine",
              "Il protagonista agisce, sceglie e paga un prezzo? Il lettore segue il suo "
              "ragionamento (cosa osserva, come lo interpreta, cosa scarta, perché agisce così), "
              "anche quando sbaglia?",
              weights=(*_NON_NARR_ZERO, ("racconti", 0.8)),
              critical=frozenset({"romanzo"})),
    Criterion("suspense", "struttura", "Suspense, decisioni e conseguenze",
              "Il protagonista sceglie, agisce e paga un costo; le minacce diventano eventi e non "
              "restano spiegazioni?",
              weights=_NON_NARR_ZERO, critical=frozenset({"romanzo"})),
    Criterion("densita", "struttura", "Densità narrativa d'avventura",
              "Indagine, azione, ostacoli e scoperte concrete sono presenti in misura adatta al "
              "genere e al punto del libro (per un thriller d'avventura a metà libro, "
              "indicativamente un terzo del capitolo), senza blocchi di esposizione?",
              weights=_NON_NARR_ZERO),
    Criterion("coerenza", "struttura", "Coerenza del racconto",
              "Personaggi, motivazioni, tono e regole del mondo restano coerenti; quello che "
              "accade ha una causa credibile?",
              "Coerenza dell'impianto",
              "Tesi, definizioni e conclusioni restano coerenti fra loro e con lo scopo dichiarato?",
              critical=_TUTTI),
    # ---- gli eventi --------------------------------------------------------------------------
    Criterion("causalita", "eventi", "Causalità e logica degli eventi",
              "Ogni evento deriva da una causa leggibile; decisioni e conseguenze sono "
              "comprensibili; nessuno arriva in un luogo o sa una cosa senza un perché mostrato?",
              weights=_NON_NARR_ZERO, critical=_NARR),
    Criterion("cronologia", "eventi", "Cronologia e geografia",
              "Giorni, ore, fusi orari, spostamenti e distanze sono compatibili; nessuno è in due "
              "luoghi incompatibili nello stesso intervallo; le indicazioni relative («la sera "
              "dopo») si possono ancorare a una data?",
              weights=_NON_NARR_ZERO, critical=_NARR),
    Criterion("oggetti", "eventi", "Tracciabilità di oggetti e documenti",
              "Ogni oggetto, documento o prova importante ha origine, custode, posizione e "
              "passaggi di mano chiari; niente cambia custode, si duplica, sparisce o ricompare "
              "senza una transizione nel testo?",
              weights=_NON_NARR_ZERO, critical=_NARR),
    Criterion("continuita", "eventi", "Continuità degli eventi",
              "Orari, luoghi, oggetti posseduti, porte e vie di fuga, scadenze, date e stagioni, "
              "chi sa cosa: tutto compatibile? Il capitolo riparte in modo causale da dove il "
              "precedente ha lasciato il protagonista, senza abbandonare in silenzio i fili "
              "aperti e senza anticipare i capitoli dopo?",
              "Coerenza di termini, dati e rimandi",
              "Termini, cifre, nomi e rimandi interni sono coerenti nel capitolo e con i capitoli "
              "precedenti?",
              critical=_TUTTI),
    Criterion("fonti", "fatti", "Tracciabilità delle fonti",
              "I dettagli storici, scientifici o tecnici presentati come fatti hanno una "
              "provenienza tracciabile (non serve citarla nel testo di un romanzo), e le ipotesi "
              "restano ipotesi dei personaggi o canone dichiarato del romanzo?",
              "Tracciabilità delle fonti",
              "Le affermazioni di fatto hanno fonti riconoscibili, indipendenti e citate in modo "
              "corretto per il genere; ipotesi e interpretazioni sono distinte dai fatti?",
              weights=(("romanzo", 0.5), ("racconti", 0.3), ("memoir", 0.5),
                       ("accademico", 2.0), ("saggio", 1.5)),
              critical=_SAGGI),
    Criterion("ricerca", "fatti", "Ricerca messa in scena",
              "Il materiale di ricerca entra attraverso oggetti, luoghi, documenti, dialoghi e "
              "scelte, e ogni dettaglio ha una funzione (un enigma, una decisione, un rapporto di "
              "forza, un seme per dopo), invece di accumularsi come in un saggio?",
              "Selezione del materiale",
              "Il materiale è selezionato per lo scopo del capitolo, senza accumuli di dettagli "
              "che non servono all'argomento?"),
    Criterion("argomentazioni", "fatti", "Logica e motivazioni",
              "Le motivazioni dei personaggi e le deduzioni sono credibili e seguibili?",
              "Argomentazioni",
              "Le tesi sono sostenute da ragioni e prove pertinenti, le obiezioni ovvie sono "
              "considerate, i passaggi logici non saltano?",
              critical=_SAGGI),
    # ---- il lettore ----------------------------------------------------------------------
    Criterion("capacita", "lettore", "Capacità narrativa",
              "Il racconto sa costruire scene, tensione, rivelazioni, ritmo e punto di vista?",
              "Capacità espositiva",
              "L'autore sa spiegare: esempi, gradualità, sintesi, passaggi dal concreto "
              "all'astratto adatti al lettore?"),
    Criterion("attenzione", "lettore", "Tenere l'attenzione",
              "L'inizio aggancia, le domande aperte tirano avanti la lettura, la fine crea il "
              "bisogno del capitolo dopo?",
              "Tenere l'attenzione",
              "Il capitolo motiva la lettura (una domanda, un problema, un caso) e la sostiene "
              "fino alla fine?",
              weights=(("accademico", 0.5), ("manuale", 0.7)),
              critical=frozenset({"romanzo", "racconti"})),
    Criterion("interesse", "lettore", "Livello di interesse",
              "Quanto il contenuto è interessante, nuovo, sorprendente per il lettore a cui il "
              "libro si rivolge?"),
    Criterion("godibilita", "lettore", "Godibilità",
              "Quanto è piacevole da leggere: il lettore ne trae piacere, emozione, gusto?",
              weights=(("accademico", 0.4), ("manuale", 0.5))),
    Criterion("fluidita", "lettore", "Fluidità",
              "Le frasi e i passaggi scorrono, le transizioni sono naturali, niente inciampi?"),
    Criterion("leggibilita", "lettore", "Leggibilità",
              "Il testo è leggibile per il lettore indicato (lunghezza e complessità delle frasi, "
              "lessico, densità)? Tieni conto dell'indice di leggibilità misurato.",
              critical=frozenset({"manuale", "divulgativo"})),
    # ---- lo stile ------------------------------------------------------------------------
    Criterion("stile", "stile", "Stile di scrittura",
              "Voce riconoscibile, registro adatto al genere e al lettore, lessico preciso, "
              "ritmo delle frasi; a quali autori fa pensare, e in cosa?"),
    Criterion("immagini", "stile", "Metafore e immagini",
              "Le immagini sono selettive e forti nei punti giusti, senza una metafora applicata "
              "a tutto e senza formule seriali?",
              "Precisione e immagini",
              "Il linguaggio è preciso; le immagini, se ci sono, chiariscono invece di decorare?"),
    Criterion("misura", "stile", "Misura",
              "La lunghezza rispetta l'obiettivo senza tagli arbitrari né riempitivi?",
              weights=tuple((k, 0.5) for k in KINDS)),
    Criterion("potenziale", "stile", "Potenziale di pubblico (stima)",
              "Se il libro continuasse così, quanto potrebbe piacere a molti lettori del suo "
              "genere e diventare un successo? È una stima motivata, non una previsione.",
              in_global=False),
]
CRITERIA_BY_KEY = {c.key: c for c in CRITERIA}
#: I gruppi, ciascuno una chiamata (una risposta sta nell'output dei modelli locali).
GROUPS: dict[str, str] = {
    "struttura": "struttura e contenuto",
    "eventi": "eventi, cronologia, oggetti e continuità",
    "fatti": "fonti, ricerca e argomenti",
    "lettore": "esperienza del lettore",
    "stile": "stile e potenziale",
}

#: La scala dichiarata a chi dà i voti: senza, i voti salgono tutti verso l'8.
SCALE = (
    "Scala dei voti (sii severo e usa tutta la scala): 10 = capolavoro; 9 = eccellente, da grande "
    "editore senza interventi; 8 = pubblicabile da un editore professionale così com'è; 7 = buono, "
    "serve un editing leggero; 6 = sufficiente, difetti evidenti ma correggibili; 5 = debole; "
    "3-4 = molto debole; 0-2 = inutilizzabile. Una prima stesura raramente supera 7. Ogni voto "
    "ha una citazione breve dal testo come prova."
)


def band(score: float | None) -> str:
    """Il giudizio a parole di un voto: il colore non è l'unico veicolo dell'informazione."""
    if score is None:
        return "non si applica"
    if score >= 8.5:
        return "ottimo"
    if score >= 7:
        return "buono"
    if score >= 5.5:
        return "discreto"
    if score >= 4:
        return "debole"
    return "insufficiente"


# --------------------------------------------------------------------------- #
#  i risultati
# --------------------------------------------------------------------------- #
@dataclass
class Problem:
    problem: str = ""
    quote: str = ""
    fix: str = ""
    severity: str = "importante"
    #: la sezione del capitolo (0 = la prima; -1 = tutto il capitolo)
    section: int = -1

    def instruction(self, label: str) -> str:
        testo = f"{label}: {self.problem}"
        if self.quote:
            testo += f" — nel testo: «{self.quote[:200]}»"
        if self.fix:
            testo += f" — cosa fare: {self.fix}"
        return testo


@dataclass
class Score:
    key: str
    label: str = ""
    score: float | None = None  #: None = non si applica
    weight: float = 1.0
    critical: bool = False
    verdict: str = ""
    evidence: str = ""
    problems: list[Problem] = field(default_factory=list)
    #: un dato misurato senza modello (indice di leggibilità, parole su budget…)
    measure: str = ""

    @property
    def applicable(self) -> bool:
        return self.score is not None and self.weight > 0


@dataclass
class Evaluation:
    """La valutazione di un capitolo (o del libro: ``chapter`` = 0)."""

    chapter: int = 0
    title: str = ""
    kind: str = "romanzo"
    words: int = 0
    scores: list[Score] = field(default_factory=list)
    #: gli autori che lo stile ricorda: [{"author", "why"}]
    authors: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    #: per il libro: titoli di confronto, pubblico, rischi, priorità
    comparables: list[str] = field(default_factory=list)
    audience: str = ""
    risks: list[str] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)
    model: str = ""
    created: str = ""
    errors: list[str] = field(default_factory=list)
    #: l'impronta del testo valutato: se il capitolo cambia, la valutazione è vecchia
    text_hash: str = ""

    def score(self, key: str) -> Score | None:
        return next((s for s in self.scores if s.key == key), None)

    @property
    def global_score(self) -> float | None:
        validi = [s for s in self.scores
                  if s.applicable and CRITERIA_BY_KEY.get(s.key, _GLOBALE).in_global]
        peso = sum(s.weight for s in validi)
        if not peso:
            return None
        return round(sum(s.score * s.weight for s in validi) / peso, 1)

    def failing(self, thresholds: Thresholds) -> list[Score]:
        """I criteri sotto soglia: i critici sotto la soglia critica, e tutti quelli sotto la
        soglia globale (sono loro da correggere perché il globale salga)."""
        return [s for s in self.scores if s.applicable
                and CRITERIA_BY_KEY.get(s.key, _GLOBALE).in_global
                and (s.score < thresholds.min_global
                     or (s.critical and s.score < thresholds.min_critical))]

    def accepted(self, thresholds: Thresholds) -> bool:
        g = self.global_score
        if g is None or g < thresholds.min_global:
            return False
        return not any(s.critical and s.applicable and s.score < thresholds.min_critical
                       for s in self.scores)


_GLOBALE = Criterion("globale", "", "Valutazione globale", "")


@dataclass
class Thresholds:
    """Quando la qualità è accettata, e quanti giri di correzione al massimo."""

    min_global: float = 7.0
    min_critical: float = 6.0
    max_rounds: int = 3


# --------------------------------------------------------------------------- #
#  le misure senza modello
# --------------------------------------------------------------------------- #
_FRASI = re.compile(r"[.!?…]+[\"»”’)]*\s+|\n\s*\n")
_VOCALI = re.compile(r"[aeiouyàèéìòùáíóúäëïöü]+", re.IGNORECASE)


def readability(text: str, language: str = "it") -> tuple[str, float, float]:
    """L'indice di leggibilità, il suo valore e le parole per frase.

    Gulpease per l'italiano (0-100: sopra 60 facile per un lettore medio, sotto 40 difficile),
    Flesch Reading Ease per le altre lingue (0-100: sopra 60 facile, sotto 30 difficile).
    """
    prosa = mx.prose_only(text)
    parole = re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)?", prosa)
    frasi = max(1, len([f for f in _FRASI.split(prosa) if f.strip()]))
    if not parole:
        return ("Gulpease" if language == "it" else "Flesch"), 0.0, 0.0
    per_frase = len(parole) / frasi
    if language == "it":
        lettere = sum(len(p) for p in parole)
        valore = 89 + (300 * frasi - 10 * lettere) / len(parole)
        return "Gulpease", round(max(0.0, min(100.0, valore)), 1), round(per_frase, 1)
    sillabe = sum(max(1, len(_VOCALI.findall(p))) for p in parole)
    valore = 206.835 - 1.015 * per_frase - 84.6 * sillabe / len(parole)
    return "Flesch", round(max(0.0, min(100.0, valore)), 1), round(per_frase, 1)


def length_score(words: int, budget: int) -> float | None:
    """10 dentro ±10% del budget, poi scende di poco più di un punto ogni 10% in più di scarto:
    la metà del budget prende circa 5,5, il doppio 0."""
    if not budget:
        return None
    scarto = abs(words - budget) / budget
    if scarto <= 0.1:
        return 10.0
    return round(max(0.0, 10 - (scarto - 0.1) * 100 / 9), 1)


def measures_block(text: str, *, language: str, budget: int, baseline: dict | None = None,
                   protagonist: str = "") -> tuple[str, dict[str, str]]:
    """I dati misurati, per il valutatore e per le schede."""
    indice, valore, per_frase = readability(text, language)
    m = mx.measure(text, protagonist=protagonist, baseline=baseline)
    righe = [f"- leggibilità ({indice}): {valore} — {per_frase} parole per frase",
             f"- parole: {m.words}" + (f" su un obiettivo di {budget}" if budget else ""),
             f"- quota di dialogo: {round(100 * m.dialogue)}%",
             f"- coppie di paragrafi molto simili: {len(m.similar)}"]
    if m.bursts:
        righe.append("- parole molto più frequenti che nel resto del libro: "
                     + ", ".join(f"{b.term} ({b.count})" for b in m.bursts[:8]))
    if m.serial_formulas:
        righe.append("- formule retoriche in serie: " + ", ".join(
            f"{nome} ×{len(posti)}" for nome, posti in m.serial_formulas.items()))
    dati = {"leggibilita": f"{indice} {valore} · {per_frase} parole/frase",
            "misura": f"{m.words} parole" + (f" su {budget}" if budget else "")}
    return "\n".join(righe), dati


# --------------------------------------------------------------------------- #
#  la valutazione di un capitolo
# --------------------------------------------------------------------------- #
_PROBLEM = {"type": "object", "additionalProperties": False,
            "properties": {"problem": {"type": "string"}, "quote": {"type": "string"},
                           "fix": {"type": "string"},
                           "severity": {"type": "string",
                                        "enum": ["bloccante", "importante", "minore"]},
                           "section": {"type": "integer"}},
            "required": ["problem", "quote", "fix", "severity", "section"]}


def _group_schema(chiavi: list[str], autori: bool) -> dict[str, Any]:
    props: dict[str, Any] = {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"key": {"type": "string", "enum": chiavi},
                       "score": {"type": "number"}, "verdict": {"type": "string"},
                       "evidence": {"type": "string"},
                       "problems": {"type": "array", "items": _PROBLEM}},
        "required": ["key", "score", "verdict", "evidence", "problems"]}}}
    richiesti = ["items"]
    if autori:
        props["authors"] = {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"author": {"type": "string"}, "why": {"type": "string"}},
            "required": ["author", "why"]}}
        richiesti.append("authors")
    return {"type": "object", "additionalProperties": False, "properties": props,
            "required": richiesti}


def purpose_block(kind: str, notebook: dict[str, Any] | None) -> str:
    """Il tipo di libro e lo scopo, per giudicare con il metro giusto."""
    nb = notebook or {}

    def voce(k: str) -> str:
        v = nb.get(k)
        if isinstance(v, list):
            return "; ".join(str(x) for x in v if x)
        return str(v or "").strip()

    righe = [f"- Tipo di libro: {KINDS.get(kind, kind)}"]
    for etichetta, k in (("Genere e forma", "form"), ("Il libro in poche righe", "premise"),
                         ("Scopo dell'autore", "author_goal"),
                         ("Effetto voluto sul lettore", "reader_effect"),
                         ("Il lettore", "audience"),
                         ("Come si adegua al lettore", "adaptation")):
        if voce(k):
            righe.append(f"- {etichetta}: {voce(k)[:500]}")
    return "# Il libro e il suo scopo\n" + "\n".join(righe)


def _system(kind: str, gruppo: str, criteri: list[Criterion], language: str) -> str:
    voci = "\n".join(f"- {c.key} — {c.label_for(kind)}: {c.question_for(kind)}"
                     + (" (CRITICO per questo tipo di libro)" if kind in c.critical else "")
                     for c in criteri)
    return (
        f"Sei un editor professionale e valuti un capitolo di un libro ({KINDS.get(kind, kind)}): "
        f"aspetto «{GROUPS[gruppo]}». Giudica con il metro del suo tipo e del suo scopo: un "
        "manuale non deve avere suspense ma chiarezza e progressione; un testo accademico deve "
        "essere rigoroso e tracciabile prima che godibile; un romanzo non cita le fonti nel "
        "testo ma deve far avanzare la storia a ogni scena; un saggio divulgativo deve essere "
        "corretto e piacevole insieme. Le tesi e le invenzioni dell'autore sono il suo libro: "
        "non sono errori da correggere.\n\n" + SCALE + "\n\n"
        "Per ogni criterio: 'score' da 0 a 10 (anche con un decimale), 'verdict' in una riga, "
        "'evidence' una citazione breve dal testo (al massimo 25 parole), 'problems' al "
        "massimo tre, i più importanti, ciascuno con la citazione, la sezione (il numero fra "
        "parentesi quadre; 0 se riguarda tutto il capitolo), la gravità e la correzione minima "
        "concreta. Un criterio senza problemi ha 'problems' vuoto.\n"
        + ("In 'authors' due o tre autori che lo stile ricorda davvero e in cosa (ritmo, "
           "lessico, struttura, voce), anche per contrasto.\n" if gruppo == "stile" else "")
        + f"Lingua delle spiegazioni: {language}.\n\nI criteri:\n{voci}"
    )


def evaluate_text(client: Any, text: str, *, chapter: int = 0, title: str = "",
                  kind: str = "romanzo", notebook: dict[str, Any] | None = None,
                  budget: int = 0, language: str = "it", explain_language: str = "italiano",
                  previous: str = "", baseline: dict | None = None, protagonist: str = "",
                  model: str | None = None, progress=None) -> Evaluation:
    """La valutazione di un capitolo: un voto per criterio, con prove e problemi. Non solleva:
    un gruppo che non riesce si annota negli errori e i suoi criteri restano senza voto."""
    from bookwriter.forge.editor import parse

    cap = parse(text)
    sezioni = "\n\n".join(f"## [{i + 1}] {s.heading.lstrip('# ') or '(senza titolo)'}\n{s.text}"
                          for i, s in enumerate(cap.sections))
    misure, dati = measures_block(text, language=language, budget=budget, baseline=baseline,
                                  protagonist=protagonist)
    valutazione = Evaluation(chapter=chapter, title=title, kind=kind, words=mx.count_words(text),
                             model=model or "", text_hash=_hash(text),
                             created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    gruppi = list(GROUPS)
    for g_i, gruppo in enumerate(gruppi):
        if progress:
            progress(g_i, len(gruppi))
        criteri = [c for c in CRITERIA if c.group == gruppo and c.applies(kind)
                   and not (c.key == "misura")]
        if not criteri:
            continue
        utente = "\n\n".join(p for p in (
            purpose_block(kind, notebook),
            f"# Dove il capitolo prima ha lasciato il lettore\n{previous}" if previous else "",
            f"# Dati misurati (senza modello)\n{misure}",
            f"# Il capitolo {chapter}: {title}, a sezioni numerate\n{sezioni}") if p)
        try:
            r = client.chat_json(
                [{"role": "system", "content": _system(kind, gruppo, criteri, explain_language)},
                 {"role": "user", "content": utente}],
                schema=_group_schema([c.key for c in criteri], gruppo == "stile"), model=model)
        except Exception as exc:  # noqa: BLE001 - un gruppo mancato si dice, non ferma il resto
            valutazione.errors.append(f"{GROUPS[gruppo]}: {str(exc)[:160]}")
            r = {}
        _read_items(valutazione, r, criteri, kind, len(cap.sections))
        if gruppo == "stile" and isinstance(r, dict):
            valutazione.authors = [{"author": str(a.get("author") or "").strip(),
                                    "why": str(a.get("why") or "").strip()}
                                   for a in r.get("authors") or [] if isinstance(a, dict)
                                   and str(a.get("author") or "").strip()][:4]
    misura = CRITERIA_BY_KEY["misura"]
    valutazione.scores.append(Score(
        "misura", misura.label_for(kind), length_score(valutazione.words, budget),
        misura.weight(kind), kind in misura.critical,
        verdict=dati["misura"], measure=dati["misura"]))
    for s in valutazione.scores:
        if s.key == "leggibilita":
            s.measure = dati["leggibilita"]
    # l'ordine delle schede è quello dei criteri, sempre lo stesso
    ordine = {c.key: i for i, c in enumerate(CRITERIA)}
    valutazione.scores.sort(key=lambda s: ordine.get(s.key, 99))
    if progress:
        progress(len(gruppi), len(gruppi))
    return valutazione


def _read_items(valutazione: Evaluation, r: Any, criteri: list[Criterion], kind: str,
                n_sezioni: int) -> None:
    visti: set[str] = set()
    for v in (r.get("items") or []) if isinstance(r, dict) else []:
        if not isinstance(v, dict):
            continue
        c = CRITERIA_BY_KEY.get(str(v.get("key") or "").strip())
        if c is None or c not in criteri or c.key in visti:
            continue
        visti.add(c.key)
        try:
            voto = max(0.0, min(10.0, float(v.get("score"))))
        except (TypeError, ValueError):
            voto = None
        problemi = []
        for p in v.get("problems") or []:
            if not isinstance(p, dict) or not str(p.get("problem") or "").strip():
                continue
            try:
                k = int(p.get("section") or 0) - 1
            except (TypeError, ValueError):
                k = -1
            problemi.append(Problem(str(p["problem"]).strip(), str(p.get("quote") or "").strip(),
                                    str(p.get("fix") or "").strip(),
                                    str(p.get("severity") or "importante"),
                                    k if 0 <= k < n_sezioni else -1))
        valutazione.scores.append(Score(
            c.key, c.label_for(kind), None if voto is None else round(voto, 1), c.weight(kind),
            kind in c.critical, str(v.get("verdict") or "").strip(),
            str(v.get("evidence") or "").strip(), problemi[:3]))
    for c in criteri:  # i criteri senza risposta restano visibili, senza voto
        if c.key not in visti:
            valutazione.scores.append(Score(c.key, c.label_for(kind), None, c.weight(kind),
                                            kind in c.critical, verdict="non valutato"))


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- #
#  il libro intero
# --------------------------------------------------------------------------- #
_BOOK_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "items": _group_schema(["arco", "coerenza_libro", "ripetizioni_libro", "potenziale"],
                               False)["properties"]["items"],
        "summary": {"type": "string"},
        "comparables": {"type": "array", "items": {"type": "string"}},
        "audience": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "priorities": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["items", "summary", "comparables", "audience", "risks", "priorities"],
}
BOOK_CRITERIA: dict[str, tuple[str, str]] = {
    "arco": ("Arco del libro", "La storia (o l'argomento) cresce di capitolo in capitolo verso il "
             "finale, con svolte e una progressione riconoscibile?"),
    "coerenza_libro": ("Coerenza fra i capitoli", "Personaggi, fatti, tesi e tono restano "
                       "coerenti fra un capitolo e l'altro?"),
    "ripetizioni_libro": ("Ripetizioni fra i capitoli", "I capitoli rispiegano cose già dette in "
                          "quelli prima? Voto alto = ognuno porta cose nuove."),
    "potenziale": ("Potenziale di pubblico (stima)", "Quanto il libro potrebbe piacere a molti "
                   "lettori del suo genere e diventare un successo? Stima motivata, non "
                   "previsione: titoli di confronto, pubblico, rischi."),
}


def book_evaluation(client: Any, chapters: list[Evaluation], digests: list[str], *, kind: str,
                    notebook: dict[str, Any] | None, explain_language: str = "italiano",
                    model: str | None = None) -> Evaluation:
    """Il libro intero: le medie dei capitoli (pesate per parole) e i criteri del libro."""
    libro = Evaluation(chapter=0, title="Il libro intero", kind=kind,
                       words=sum(c.words for c in chapters), model=model or "",
                       created=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    for c in CRITERIA:
        voti = [(s.score, ev.words or 1) for ev in chapters
                if (s := ev.score(c.key)) is not None and s.applicable]
        if not c.applies(kind):
            continue
        media = (round(sum(v * w for v, w in voti) / sum(w for _v, w in voti), 1)
                 if voti else None)
        peggiori = sorted(((s.score, ev.chapter) for ev in chapters
                           if (s := ev.score(c.key)) is not None and s.applicable))[:3]
        libro.scores.append(Score(
            c.key, c.label_for(kind), media, c.weight(kind), kind in c.critical,
            verdict=("capitoli più deboli: " + ", ".join(f"{n} ({v})" for v, n in peggiori))
            if peggiori else ""))
    tabella = "\n".join(
        f"- Capitolo {ev.chapter} «{ev.title}»: globale {ev.global_score}; "
        + ", ".join(f"{s.key} {s.score}" for s in ev.scores if s.applicable)
        for ev in chapters)
    sistema = (
        f"Sei un editor e un lettore professionale. Valuti un libro intero "
        f"({KINDS.get(kind, kind)}) dai riassunti dei capitoli e dai voti già dati capitolo per "
        "capitolo. Giudica i criteri del libro con lo stesso metro (il suo tipo, il suo scopo, "
        "il suo lettore).\n\n" + SCALE + "\n\nI criteri:\n"
        + "\n".join(f"- {k} — {l}: {q}" for k, (l, q) in BOOK_CRITERIA.items())
        + "\n\nIn 'summary' il giudizio complessivo in poche frasi; in 'comparables' tre-cinque "
        "titoli pubblicati con cui il libro si confronta (genere, pubblico, tono); in "
        "'audience' il pubblico realistico; in 'risks' i rischi principali; in 'priorities' "
        "le cinque correzioni che migliorerebbero di più il libro, in ordine. La stima del "
        "potenziale è un'opinione motivata: dillo nel verdetto. Lingua delle spiegazioni: "
        f"{explain_language}."
    )
    utente = "\n\n".join(p for p in (
        purpose_block(kind, notebook),
        "# I capitoli in breve\n" + "\n\n".join(digests),
        f"# I voti dei capitoli\n{tabella}") if p)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_BOOK_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001
        libro.errors.append(f"libro: {str(exc)[:160]}")
        r = {}
    if isinstance(r, dict):
        for v in r.get("items") or []:
            chiave = str((v or {}).get("key") or "")
            if chiave not in BOOK_CRITERIA:
                continue
            try:
                voto = round(max(0.0, min(10.0, float(v.get("score")))), 1)
            except (TypeError, ValueError):
                voto = None
            etichetta = BOOK_CRITERIA[chiave][0]
            esistente = libro.score(chiave)
            nuovo = Score(chiave, etichetta, voto, 1.0, chiave in ("arco", "coerenza_libro"),
                          str(v.get("verdict") or ""), str(v.get("evidence") or ""))
            if esistente is not None:  # il potenziale del libro vale più della media
                libro.scores[libro.scores.index(esistente)] = nuovo
            else:
                libro.scores.append(nuovo)
        libro.summary = str(r.get("summary") or "")
        libro.comparables = [str(x) for x in r.get("comparables") or [] if str(x).strip()][:6]
        libro.audience = str(r.get("audience") or "")
        libro.risks = [str(x) for x in r.get("risks") or [] if str(x).strip()][:6]
        libro.priorities = [str(x) for x in r.get("priorities") or [] if str(x).strip()][:8]
    pot = libro.score("potenziale")
    if pot is not None:  # resta fuori dalla media in ogni caso
        pot.weight = pot.weight or 1.0
    return libro


# --------------------------------------------------------------------------- #
#  su disco: le valutazioni e le versioni
# --------------------------------------------------------------------------- #
def quality_dir(project) -> Path:
    return project.root / "quality"


def history_path(project, stem: str) -> Path:
    return quality_dir(project) / f"{stem}.json"


def evaluation_from_dict(d: dict[str, Any]) -> Evaluation:
    campi = Evaluation.__dataclass_fields__
    ev = Evaluation(**{k: v for k, v in d.items() if k in campi and k != "scores"})
    for s in d.get("scores") or []:
        if not isinstance(s, dict):
            continue
        problemi = [Problem(**{k: v for k, v in p.items() if k in Problem.__dataclass_fields__})
                    for p in s.get("problems") or [] if isinstance(p, dict)]
        ev.scores.append(Score(**{**{k: v for k, v in s.items()
                                     if k in Score.__dataclass_fields__ and k != "problems"},
                                  "problems": problemi}))
    return ev


def load_history(project, stem: str) -> list[Evaluation]:
    path = history_path(project, stem)
    if not path.exists():
        return []
    try:
        dati = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [evaluation_from_dict(d) for d in dati.get("rounds") or [] if isinstance(d, dict)]


def save_round(project, stem: str, valutazione: Evaluation, note: str = "") -> None:
    storia = load_history(project, stem)
    storia.append(valutazione)
    path = history_path(project, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    righe = [asdict(v) for v in storia[-30:]]
    if note:
        righe[-1]["note"] = note
    path.write_text(json.dumps({"rounds": righe}, ensure_ascii=False, indent=1), encoding="utf-8")


def latest(project, stem: str) -> Evaluation | None:
    storia = load_history(project, stem)
    return storia[-1] if storia else None


def book_path(project) -> Path:
    return quality_dir(project) / "libro.json"


def save_book(project, libro: Evaluation, capitoli: list[Evaluation]) -> None:
    path = book_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"book": asdict(libro), "chapters": [asdict(c) for c in capitoli]},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def load_book(project) -> tuple[Evaluation | None, list[Evaluation]]:
    path = book_path(project)
    if not path.exists():
        return None, []
    try:
        dati = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, []
    return (evaluation_from_dict(dati.get("book") or {}),
            [evaluation_from_dict(c) for c in dati.get("chapters") or [] if isinstance(c, dict)])


def versions_dir(project, stem: str) -> Path:
    return project.story_dir / "versions" / stem


def versions(project, stem: str) -> list[Path]:
    cartella = versions_dir(project, stem)
    if not cartella.exists():
        return []
    return sorted(cartella.glob("v*.md"), key=lambda p: int(re.match(r"v(\d+)", p.stem).group(1))
                  if re.match(r"v(\d+)", p.stem) else 0)


def save_version(project, stem: str, text: str, label: str) -> Path:
    """Una versione del capitolo, numerata: la prima è l'originale prima di ogni correzione."""
    cartella = versions_dir(project, stem)
    cartella.mkdir(parents=True, exist_ok=True)
    n = len(versions(project, stem))
    quando = datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005 - per chi legge la cartella
    path = cartella / f"v{n}_{quando}_{re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')}.md"
    path.write_text(text, encoding="utf-8")
    return path


def restore(project, chapter_file: Path, version: Path) -> None:
    """Rimette una versione nel capitolo, salvando prima quella attuale (niente si perde)."""
    attuale = chapter_file.read_text(encoding="utf-8") if chapter_file.exists() else ""
    if attuale:
        save_version(project, chapter_file.stem, attuale, "prima del ripristino")
    chapter_file.write_text(version.read_text(encoding="utf-8"), encoding="utf-8")


# --------------------------------------------------------------------------- #
#  la correzione: un problema alla volta, solo sulla sua parte
# --------------------------------------------------------------------------- #
#: Quanti problemi si correggono per giro, i più pesanti prima.
MAX_FIX_PROBLEMS = 8
#: Quanti paragrafi consecutivi può toccare, al più, la correzione di un problema.
MAX_PART_PARAGRAPHS = 3
#: Di quanto deve salire il voto globale perché la versione nuova si tenga: sotto, è rumore.
MIN_GAIN = 0.2
#: Di quanto può scendere un criterio critico in una versione che si tiene.
MAX_CRITICAL_DROP = 1.0
#: La forbice della lunghezza di una parte riscritta, per tipo di problema: per la continuità
#: (una fuga da mostrare, un viaggio che manca) la parte può crescere di più; per le ripetizioni
#: può scendere di più.
PART_RANGE = {"continuita": (0.8, 2.0), "ripetizioni": (0.4, 1.2), "misura": (0.8, 1.6)}
DEFAULT_PART_RANGE = (0.7, 1.4)


@dataclass
class Fix:
    """Un problema da correggere e la parte del capitolo che tocca."""

    criterion: str
    label: str
    problem: Problem
    section: int = -1
    first: int = -1  #: il primo paragrafo della parte (nella sezione)
    last: int = -1
    done: bool = False
    outcome: str = ""

    def line(self) -> str:
        dove = (f"sezione {self.section + 1}, §{self.first + 1}"
                + (f"-{self.last + 1}" if self.last > self.first else "")) \
            if self.section >= 0 and self.first >= 0 else "parte non trovata"
        return f"{self.label} — {self.problem.problem} [{dove}] → {self.outcome}"


@dataclass
class ImproveReport:
    chapter: int = 0
    title: str = ""
    rounds: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    before: float | None = None
    after: float | None = None
    accepted: bool = False
    stopped: bool = False
    changed: bool = False
    #: il tetto dei giri è raggiunto e la qualità non è accettata: un giro in più va autorizzato
    needs_authorization: bool = False
    final: Evaluation | None = None

    def line(self) -> str:
        esito = ("qualità accettata" if self.accepted else "fermato" if self.stopped else
                 "tetto dei giri raggiunto" if self.needs_authorization else
                 "nessun miglioramento")
        return (f"Capitolo {self.chapter} «{self.title}»: {self.before} → {self.after} "
                f"({esito}; {len(self.rounds)} giri)")


def plan_fixes(valutazione: Evaluation, thresholds: Thresholds) -> list[Fix]:
    """I problemi da correggere, dai criteri sotto soglia, i più pesanti prima."""
    sotto = sorted(valutazione.failing(thresholds),
                   key=lambda s: -(s.weight * (thresholds.min_global - s.score)
                                   + (2 if s.critical else 0)))
    gravi = {"bloccante": 0, "importante": 1, "minore": 2}
    lavori = [Fix(s.key, s.label, p, p.section) for s in sotto
              for p in sorted(s.problems, key=lambda x: gravi.get(x.severity, 1))]
    return lavori[:MAX_FIX_PROBLEMS]


def _norm(testo: str) -> str:
    return re.sub(r"\W+", " ", (testo or "").lower()).strip()


def locate_quote(cap: Any, quote: str, section: int = -1) -> tuple[int, int] | None:
    """La sezione e il paragrafo che contengono la citazione (anche in parte)."""
    q = _norm(quote)
    if len(q) < 12:
        return None
    sezioni = [section] if 0 <= section < len(cap.sections) else range(len(cap.sections))
    for pezzo in (q, q[:60], q[-60:]):
        for si in sezioni:
            for pi, par in enumerate(cap.sections[si].paragraphs):
                if pezzo and pezzo in _norm(par):
                    return si, pi
    return None


_LOCATE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"section": {"type": "integer"}, "first": {"type": "integer"},
                   "last": {"type": "integer"}},
    "required": ["section", "first", "last"],
}


def locate_part(client: Any, cap: Any, fix: Fix, *, model: str | None = None
                ) -> tuple[int, int, int] | None:
    """La parte (sezione, primo e ultimo paragrafo) che un problema tocca.

    Prima la citazione; se non c'è o non si trova, il modello indica al più
    :data:`MAX_PART_PARAGRAPHS` paragrafi consecutivi dove correggerlo serve di più.
    """
    trovato = locate_quote(cap, fix.problem.quote, fix.section)
    if trovato is not None:
        return trovato[0], trovato[1], trovato[1]
    elenco = "\n\n".join(
        f"## [{si + 1}] {s.heading.lstrip('# ')}\n" + "\n".join(
            f"[{si + 1}.{pi + 1}] {p[:160]}…" for pi, p in enumerate(s.paragraphs))
        for si, s in enumerate(cap.sections))
    sistema = (
        "Un editor ha trovato un problema in un capitolo. Indica DOVE correggerlo: la sezione e "
        f"al più {MAX_PART_PARAGRAPHS} paragrafi consecutivi ('first' e 'last', numerati come "
        "[sezione.paragrafo], solo il numero del paragrafo) dove la correzione serve di più. "
        "Una parte sola, la più piccola che basta."
    )
    utente = f"# Il problema\n{fix.problem.instruction(fix.label)}\n\n# Il capitolo\n{elenco}"
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_LOCATE_SCHEMA, model=model)
        si = int(r.get("section") or 0) - 1
        primo, ultimo = int(r.get("first") or 0) - 1, int(r.get("last") or 0) - 1
    except Exception:  # noqa: BLE001 - senza una parte definita il problema non si tocca
        return None
    if not 0 <= si < len(cap.sections):
        return None
    n = len(cap.sections[si].paragraphs)
    if not 0 <= primo < n:
        return None
    ultimo = max(primo, min(ultimo, n - 1, primo + MAX_PART_PARAGRAPHS - 1))
    return si, primo, ultimo


def rewrite_part(client: Any, cap: Any, fix: Fix, *, kind: str, language: str,
                 model: str | None = None, scene_card: str = "", style: str = "") -> str:
    """Riscrive SOLO la parte del problema; il resto del capitolo non lo vede cambiare."""
    from bookwriter.forge.editor import _ask_text
    from bookwriter.forge.writing import _NO_INVENTION, FORBIDDEN

    sezione = cap.sections[fix.section]
    parte = "\n\n".join(sezione.paragraphs[fix.first:fix.last + 1])
    prima = sezione.paragraphs[fix.first - 1] if fix.first > 0 else ""
    dopo = sezione.paragraphs[fix.last + 1] if fix.last + 1 < len(sezione.paragraphs) else ""
    chi = "il romanziere" if kind in NARRATIVE_KINDS else "l'autore"
    sistema = (
        f"Sei {chi} di questo libro ({KINDS.get(kind, kind)}). Correggi UN problema indicato "
        "dall'editor riscrivendo SOLO la parte data: niente di quello che sta prima o dopo, "
        "nessun titolo. Correggi quel problema e nient'altro; tieni fatti, personaggi, voce e "
        "stile; la parte deve saldarsi al paragrafo prima e a quello dopo. Se il problema chiede "
        "di mostrare qualcosa che manca (un passaggio, un'uscita, un ragionamento), aggiungilo "
        "qui in poche righe concrete. Se chiede di togliere una ripetizione, togli e non "
        f"riempire. Lingua: {language}. Non scrivere mai: {FORBIDDEN}.\n\n" + _NO_INVENTION
        + (f"\n\nStile richiesto:\n{style}" if style else "")
    )
    utente = "\n\n".join(p for p in (
        f"# Il problema\n{fix.problem.instruction(fix.label)}",
        f"# Il piano di questa scena\n{scene_card}" if scene_card else "",
        f"# Il paragrafo prima (non riscriverlo)\n{prima}" if prima else "",
        f"# LA PARTE DA RISCRIVERE\n{parte}",
        f"# Il paragrafo dopo (non riscriverlo)\n{dopo}" if dopo else "",
        "# Rispondi solo con la parte riscritta, in uno o più paragrafi.") if p)
    return _ask_text(client, sistema, utente, model=model)


def apply_fixes(client: Any, text: str, fixes: list[Fix], *, kind: str, language: str,
                model: str | None = None, check_model: str | None = None,
                scene_card=None, style: str = "", should_stop=None, note=None) -> str:
    """Corregge i problemi uno alla volta, ciascuno solo sulla sua parte. Ritorna il testo."""
    from bookwriter.forge.editor import parse

    cap = parse(text)
    for i, f in enumerate(fixes):
        if should_stop and should_stop():
            break
        if note:
            note(("fix", i + 1, len(fixes)))
        posto = locate_part(client, cap, f, model=check_model or model)
        if posto is None:
            f.outcome = "parte non trovata: non toccato"
            continue
        f.section, f.first, f.last = posto
        prima = mx.count_words("\n\n".join(cap.sections[f.section].paragraphs[f.first:f.last + 1]))
        nuovo = rewrite_part(client, cap, f, kind=kind, language=language, model=model,
                             scene_card=scene_card(f.section) if scene_card else "", style=style)
        paragrafi = [p.strip() for p in re.split(r"\n\s*\n", nuovo or "") if p.strip()
                     and not p.strip().startswith("#")]
        dopo = mx.count_words("\n\n".join(paragrafi))
        basso, alto = PART_RANGE.get(f.criterion, DEFAULT_PART_RANGE)
        if not paragrafi or not (basso * prima <= dopo <= alto * max(prima, 40)):
            f.outcome = f"riscrittura scartata ({dopo} parole contro {prima})"
            continue
        sezione = cap.sections[f.section]
        sezione.paragraphs[f.first:f.last + 1] = paragrafi
        f.done = True
        f.outcome = f"riscritta la parte ({prima} → {dopo} parole)"
    return cap.render()


def improve_chapter(client: Any, text: str, evaluate, *, thresholds: Thresholds, kind: str,
                    language: str, rounds: int, model: str | None = None,
                    check_model: str | None = None, scene_card=None, style: str = "",
                    save=None, should_stop=None, note=None, chapter: int = 0, title: str = "",
                    current: Evaluation | None = None) -> tuple[str, ImproveReport]:
    """Il ciclo: valuta → correggi i problemi, uno per parte → rivaluta. Fino a ``rounds`` giri.

    ``evaluate(testo)`` dà la valutazione; ``save(testo, valutazione, etichetta)`` tiene la
    versione nuova (su disco e nel capitolo). Una versione si tiene solo se il globale sale di
    almeno :data:`MIN_GAIN` e nessun criterio critico scende di più di :data:`MAX_CRITICAL_DROP`;
    altrimenti il ciclo si ferma, perché chiedere di nuovo la stessa cosa non migliora.
    """
    resoconto = ImproveReport(chapter=chapter, title=title)
    valutazione = current if current is not None and current.text_hash == _hash(text) \
        else evaluate(text)
    resoconto.before = valutazione.global_score
    tenuto_ultimo = False
    for giro in range(1, max(0, rounds) + 1):
        tenuto_ultimo = False
        if valutazione.accepted(thresholds):
            break
        if should_stop and should_stop():
            resoconto.stopped = True
            break
        lavori = plan_fixes(valutazione, thresholds)
        if not lavori:
            resoconto.rounds.append(f"giro {giro}: sotto soglia ma senza problemi localizzati")
            break
        if note:
            note(f"Capitolo {chapter}: giro {giro}, {len(lavori)} problemi da correggere…")
        nuovo = apply_fixes(client, text, lavori, kind=kind, language=language, model=model,
                            check_model=check_model, scene_card=scene_card, style=style,
                            should_stop=should_stop, note=note)
        resoconto.fixes += [f"giro {giro}: {f.line()}" for f in lavori]
        if not any(f.done for f in lavori):
            resoconto.rounds.append(f"giro {giro}: nessuna correzione applicata")
            break
        if mx.count_words(nuovo) < 0.9 * mx.count_words(text):
            resoconto.rounds.append(f"giro {giro}: le correzioni accorciavano troppo il capitolo"
                                    " — scartate")
            break
        rivalutata = evaluate(nuovo)
        guadagno = (rivalutata.global_score or 0) - (valutazione.global_score or 0)
        cadute = [s.label for s in valutazione.scores if s.critical and s.applicable
                  and (n := rivalutata.score(s.key)) is not None and n.applicable
                  and n.score < s.score - MAX_CRITICAL_DROP]
        if guadagno < MIN_GAIN or cadute:
            resoconto.rounds.append(
                f"giro {giro}: {valutazione.global_score} → {rivalutata.global_score}, tenuta la "
                "versione prima" + (f" (scendono: {', '.join(cadute)})" if cadute else ""))
            break
        text, valutazione = nuovo, rivalutata
        resoconto.changed = tenuto_ultimo = True
        resoconto.rounds.append(f"giro {giro}: {resoconto.before if giro == 1 else ''}"
                                f"→ {valutazione.global_score}")
        if save:
            save(text, valutazione, f"giro {giro}")
    resoconto.after = valutazione.global_score
    resoconto.accepted = valutazione.accepted(thresholds)
    resoconto.final = valutazione
    # il tetto è raggiunto mentre i giri miglioravano ancora: un giro in più ha senso, ma lo
    # decide l'autore
    resoconto.needs_authorization = (not resoconto.accepted and not resoconto.stopped
                                     and tenuto_ultimo and len(resoconto.rounds) >= rounds)
    return text, resoconto
