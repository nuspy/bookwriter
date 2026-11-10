"""La revisione di un capitolo: diagnosi, piano, intervento, verifica — separati e tracciati.

Nasce dalla specifica editoriale del 25/10/2026 (``docs/REVIEW_WORKFLOW_CLAUDE_CODE_THE_RED_THREAD_CH11_V3
- Copy.md``), scritta sul caso della bozza v3 del cap. 11. La revisione di prima rivedeva il testo
in un solo giro e chiedeva allo stesso modello se era migliorato; qui ogni passo ha la sua
responsabilità e lascia una traccia verificabile:

0. **Situazione di partenza** (:func:`baseline`): una copia intatta, le parole per scena, il
   registro degli eventi (luoghi, ore, oggetti, porte, scadenze, chi sa cosa) e il registro degli
   oggetti; e **quali fonti dell'app sono state consultate davvero** (il capitolo prima e quello
   dopo **originali**, con versione e impronta; la scheda del registro; il quaderno). Un dato che
   l'app non ha resta ``UNVERIFIED``: non si ricava da versioni esterne.
1. **Diagnosi** (:func:`diagnose`): rilievi con un ID stabile (``CH11-TIME-001``), gravità
   (Blocker, Major, Moderate, Minor), categoria, prova nel testo, impatto sul lettore, intervento,
   test di accettazione, base (testo, canone, evidenza, inferenza, domanda aperta). Vengono dal
   registro degli eventi, dagli oggetti, dalla funzione delle scene, dalle ripetizioni (parole,
   idee, scene), dalla rubrica (:mod:`quality`), dalla misura e dalle fonti.
2. **Piano** (:func:`plan`): interventi ordinati per dipendenza — cronologia, continuità, oggetti,
   funzione delle scene, ripetizioni, prosa, misura — ciascuno con l'operazione (riscrivi, fondi,
   separa, inserisci, sposta, accorcia, preserva, decisione dell'autore), il bersaglio, l'effetto
   voluto e quello che va protetto; le cose da preservare; le domande all'autore.
3. **Intervento** (:func:`execute`): un intervento alla volta, solo sul suo bersaglio (una scena,
   o dei paragrafi), con il contesto che serve (i fatti fissati prima, gli oggetti, il canone);
   dopo i blocchi strutturali il registro degli eventi si rifà, e una regressione si registra.
4. **Verifica indipendente** (:func:`verify`): rilievo per rilievo, con lo stato (``RESOLVED``,
   ``PARTIALLY_RESOLVED``, ``UNRESOLVED``, ``REGRESSION``, ``NOT_APPLICABLE``) e la citazione che lo
   prova — un problema non è risolto perché il testo è cambiato; le regressioni nuove; le cose da
   preservare; la rubrica prima e dopo; i nove **gate** (A cronologia, B continuità, C oggetti,
   D conoscenze, E funzione delle scene, F ripetizione, G misura, H fonti, I vincoli protetti), che
   nessuna media compensa.
5. **Verdetto**: ``PASS``, ``PASS WITH WARNINGS``, ``REVISE``, ``AUTHOR INPUT REQUIRED``, ``FAIL``, e il
   prossimo intervento minimo.

Tutto va in ``revisioni/<run>/`` del lavoro: la copia di partenza, il testo rivisto, il report in
dodici sezioni, il diff, i dati (``run.json``). Il testo rivisto entra nel capitolo solo se il
verdetto non è ``FAIL`` e la rubrica non è peggiorata; la versione di prima resta fra le versioni.

I controlli possono girare su un altro modello (``llm.models.review``): chi ha scritto non è
l'unica autorità che approva.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bookwriter.forge import metrics as mx
from bookwriter.forge import quality as ql
from bookwriter.forge import timeline as tl

# --------------------------------------------------------------------------- #
#  i vocabolari
# --------------------------------------------------------------------------- #
SEVERITIES = ("Blocker", "Major", "Moderate", "Minor")
_DA_TIMELINE = {"bloccante": "Blocker", "importante": "Major", "minore": "Minor"}
STATUSES = ("OPEN", "RESOLVED", "PARTIALLY_RESOLVED", "UNRESOLVED", "REGRESSION",
            "NOT_APPLICABLE")
OPERATIONS = ("rewrite", "merge", "split", "insert", "move", "trim", "preserve",
              "author_decision")
#: L'ordine dei blocchi: prima la cronologia, poi quello che ne dipende.
BLOCKS = ("cronologia", "continuita", "oggetti", "scene", "ripetizioni", "prosa", "misura")
#: Categoria del rilievo → blocco del piano.
CATEGORY_BLOCK = {"REG": "cronologia", "TIME": "cronologia", "PLACE": "cronologia",
                  "CAUSE": "continuita",
                  "CONT": "continuita", "CANON": "continuita", "KNOW": "continuita",
                  "OBJ": "oggetti", "SCENE": "scene", "REP": "ripetizioni", "STYLE": "prosa",
                  "SRC": "prosa", "LEN": "misura"}
BASES = ("testo", "canone", "evidenza", "inferenza", "domanda_aperta")
VERDICTS = ("PASS", "PASS WITH WARNINGS", "REVISE", "AUTHOR INPUT REQUIRED", "FAIL")
GATES = {"A": "Cronologia", "B": "Continuità fra capitoli", "C": "Oggetti e documenti",
         "D": "Conoscenze dei personaggi", "E": "Funzione delle scene",
         "F": "Ripetizione concettuale", "G": "Lunghezza", "H": "Fonti", "I": "Vincoli protetti"}

#: Quanti rilievi minori e moderati si tengono (Blocker e Major tutti).
MAX_MODERATE = 15
MAX_MINOR = 10
#: Quanti interventi, al più, in un giro: oltre, è una riscrittura del capitolo.
MAX_CHANGES = 14
#: La fascia della misura: il budget ±10% (6.300–7.700 per 7.000).
LENGTH_BAND = 0.10
#: Quanti rilievi di ripetizione, al più: nella prova v4 erano 16 Major e hanno fatto tagliare
#: dappertutto; ChatGPT ne vedeva uno. Il resto si accorpa nell'ultimo.
MAX_REP = 4
#: La forbice di un intervento rispetto alla misura del suo bersaglio (prova v4: un «trim» ha
#: portato una scena da 1.191 a 481 parole, e il capitolo da 7.470 a 4.892).
OP_RANGE = {"rewrite": (0.8, 1.2), "trim": (0.7, 1.05), "merge": (0.55, 1.1),
            "split": (0.85, 1.25)}
#: Un passaggio inserito non supera queste parole.
MAX_INSERT = 400

_TIMELINE_KIND = {"tempo": "TIME", "luogo": "PLACE", "oggetto": "OBJ", "accesso": "OBJ",
                  "scadenza": "TIME", "data": "TIME", "conoscenza": "KNOW",
                  "filo aperto": "CONT", "anticipazione": "CANON", "eventi": "CONT"}
_CRITERION_CATEGORY = {"causalita": "CAUSE", "cronologia": "TIME", "continuita": "CONT",
                       "oggetti": "OBJ", "struttura": "SCENE", "suspense": "SCENE",
                       "densita": "SCENE", "avanzamento": "SCENE", "personaggi": "SCENE",
                       "ripetizioni": "REP", "misura": "LEN", "fonti": "SRC",
                       "coerenza": "CAUSE"}
_IMPACT = {
    "REG": "i conti del testo non tornano: il lettore non sa più quanti sono e dove sono",
    "TIME": "il lettore non riesce a ricostruire quando e in che ordine accadono le cose",
    "PLACE": "il lettore vede il protagonista cambiare luogo senza sapere come",
    "CAUSE": "un evento sembra accadere perché lo richiede la trama, non per una causa",
    "CONT": "il capitolo non riprende ciò che il precedente aveva lasciato aperto",
    "CANON": "il capitolo anticipa o contraddice ciò che il libro riserva ad altri capitoli",
    "KNOW": "un personaggio agisce su un'informazione che non ha ancora",
    "OBJ": "il lettore perde il filo di chi ha un oggetto o un documento",
    "SCENE": "la scena non cambia niente o ripete il lavoro di un'altra",
    "REP": "la stessa tesi torna senza aggiungere significato, e il ritmo si ferma",
    "STYLE": "la lettura si fa meno immersiva",
    "LEN": "il capitolo è fuori misura: si diluisce o si comprime la storia",
    "SRC": "un fatto esterno compare senza provenienza registrata",
}
_TEST = {
    "REG": "il validatore del registro non trova più la contraddizione (somma, conservazione o "
           "luogo) sul testo rivisto",
    "TIME": "il registro degli eventi del testo rivisto non contiene più questa contraddizione "
            "di orari, giorni o durate",
    "PLACE": "il testo rivisto mostra come il protagonista passa da un luogo all'altro "
             "(causa, mezzo, tempo)",
    "CAUSE": "nel testo rivisto l'evento ha una causa leggibile e citabile",
    "CONT": "il testo rivisto riprende lo stato finale del capitolo precedente originale",
    "CANON": "il testo rivisto non mette in scena eventi riservati ai capitoli successivi",
    "KNOW": "nel testo rivisto il personaggio apprende l'informazione prima di usarla",
    "OBJ": "nel registro degli oggetti del testo rivisto l'oggetto ha origine e passaggi di "
           "mano mostrati",
    "SCENE": "la scena rivista cambia qualcosa di concreto e non duplica un'altra scena",
    "REP": "il cluster ripetuto ricorre solo dove aggiunge significato",
    "STYLE": "il passo citato è riscritto senza il difetto indicato",
    "LEN": "il capitolo rivisto è nella fascia della misura",
    "SRC": "il fatto ha una voce nella ricerca o è segnato UNVERIFIED nel report",
}


# --------------------------------------------------------------------------- #
#  i dati
# --------------------------------------------------------------------------- #
@dataclass
class Issue:
    issue_id: str = ""
    chapter: int = 0
    source_version: str = ""
    category: str = ""
    severity: str = "Moderate"
    location: str = ""
    section: int = -1
    evidence: str = ""
    diagnosis: str = ""
    reader_impact: str = ""
    cause: str = ""
    proposed_action: str = ""
    dependencies: list[str] = field(default_factory=list)
    acceptance_test: str = ""
    status: str = "OPEN"
    resolution_evidence: str = ""
    regression_risk: str = ""
    basis: str = "testo"
    origin: str = ""

    @property
    def block(self) -> str:
        return CATEGORY_BLOCK.get(self.category, "prosa")

    def short(self) -> str:
        return f"{self.issue_id} [{self.severity}] {self.location}: {self.diagnosis}"

    def instruction(self) -> str:
        testo = f"{self.issue_id} ({self.severity}): {self.diagnosis}"
        if self.evidence:
            testo += f" — nel testo: «{self.evidence[:220]}»"
        if self.proposed_action:
            testo += f" — intervento: {self.proposed_action}"
        if self.acceptance_test:
            testo += f" — sarà risolto quando: {self.acceptance_test}"
        return testo


@dataclass
class Change:
    change_id: str = ""
    related_issue_ids: list[str] = field(default_factory=list)
    operation: str = "rewrite"
    targets: list[int] = field(default_factory=list)  #: sezioni (0 = la prima)
    first_paragraph: int = -1
    last_paragraph: int = -1
    intended_effect: str = ""
    protected_content: str = ""
    words: int = 0
    block: str = "prosa"
    done: bool = False
    before_after_summary: str = ""
    verification_result: str = ""


@dataclass
class Gate:
    key: str
    name: str
    passed: bool | None = None  #: None = non verificabile
    details: list[str] = field(default_factory=list)


@dataclass
class ObjectRecord:
    """Un oggetto, documento o prova del capitolo, dal registro degli eventi."""

    id: str
    name: str = ""
    first_section: int = -1
    last_section: int = -1
    holders: list[tuple[int, str, str]] = field(default_factory=list)  # (sezione, custode, stato)

    def line(self) -> str:
        passi = " → ".join(f"[{s + 1}] {h or '?'} ({st})" for s, h, st in self.holders)
        return f"{self.name or self.id}: {passi}"


@dataclass
class RevisionRun:
    run_id: str = ""
    chapter: int = 0
    title: str = ""
    kind: str = "romanzo"
    input_version: str = ""
    output_version: str = ""
    model: str = ""
    check_model: str = ""
    created: str = ""
    sources_consulted: list[dict[str, str]] = field(default_factory=list)
    words_before: int = 0
    words_after: int = 0
    words_per_section_before: list[tuple[str, int]] = field(default_factory=list)
    words_per_section_after: list[tuple[str, int]] = field(default_factory=list)
    budget: int = 0
    timeline_before: str = ""
    timeline_after: str = ""
    objects_before: list[str] = field(default_factory=list)
    objects_after: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    preserve: list[str] = field(default_factory=list)
    preserve_check: list[str] = field(default_factory=list)
    author_questions: list[str] = field(default_factory=list)
    rubric_before: dict[str, Any] = field(default_factory=dict)
    rubric_after: dict[str, Any] = field(default_factory=dict)
    gates: list[Gate] = field(default_factory=list)
    verdict: str = ""
    next_steps: list[str] = field(default_factory=list)
    applied_to_chapter: bool = False
    output_path: str = ""
    errors: list[str] = field(default_factory=list)
    #: la metodologia usata (file in story/metodologia/) e i valori canonici fissati
    methodology: str = ""
    canonical: str = ""

    def issue(self, ident: str) -> Issue | None:
        return next((i for i in self.issues if i.issue_id == ident), None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _version(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def _tokens(testo: str) -> set[str]:
    return {w for w in mx.content_words(testo or "") if len(w) > 3}


def _simile(a: str, b: str, soglia: float = 0.45) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= soglia


# --------------------------------------------------------------------------- #
#  0. la situazione di partenza
# --------------------------------------------------------------------------- #
def object_registry(records: list[tl.SceneRecord]) -> list[ObjectRecord]:
    """Gli oggetti del capitolo con i loro passaggi di mano, scena per scena."""
    oggetti: dict[str, ObjectRecord] = {}
    for i, r in enumerate(records):
        for o in r.objects:
            ident = o.get("id") or tl._ident(o.get("what", ""))
            if not ident:
                continue
            voce = oggetti.setdefault(ident, ObjectRecord(ident, o.get("what", ""), i, i))
            voce.last_section = i
            ultimo = voce.holders[-1] if voce.holders else None
            nuovo = (i, o.get("holder", ""), o.get("status", ""))
            if ultimo is None or (ultimo[1], ultimo[2]) != (nuovo[1], nuovo[2]):
                voce.holders.append(nuovo)
    return list(oggetti.values())


#: Gli stati che sono essi stessi un passaggio di mano mostrato.
_TRANSFERS = {"consegnato", "sottratto", "perso", "lasciato", "distrutto", "copiato",
              "fotografato"}


def object_issues(registro: list[ObjectRecord]) -> list[tuple[int, str, str]]:
    """Custodi che cambiano senza un passaggio mostrato: (sezione, oggetto, problema)."""
    fuori = []
    for o in registro:
        for (s1, h1, st1), (s2, h2, st2) in zip(o.holders, o.holders[1:]):
            if h1 and h2 and not _simile(h1, h2, 0.3) and st2 not in _TRANSFERS \
                    and st1 not in _TRANSFERS:
                fuori.append((s2, o.name or o.id,
                              f"«{o.name or o.id}» passa da {h1} (scena {s1 + 1}) a {h2} "
                              f"(scena {s2 + 1}) senza un passaggio di mano mostrato"))
    return fuori


def sources_consulted(project, chapter: int) -> list[dict[str, str]]:
    """Le fonti canoniche dell'app per la continuità: i capitoli **originali** prima e dopo (i
    file in ``chapters/``, con l'impronta), la scheda del registro, il quaderno e la struttura."""
    from bookwriter.forge import ledger as lg

    scritti = project.written_chapters()
    fuori = []
    for n, ruolo in ((chapter - 1, "capitolo precedente"), (chapter + 1, "capitolo successivo")):
        if n < 1:
            continue
        f = scritti.get(n)
        if f is None:
            fuori.append({"name": f"{ruolo} ({n})", "path": "", "version": "",
                          "status": "UNVERIFIED: non è nell'app"})
            continue
        testo = f.read_text(encoding="utf-8")
        fuori.append({"name": f"{ruolo} ({n})", "path": str(f), "version": _version(testo),
                      "status": "consultato (originale dell'app)"})
    voce = lg.load(project).entry(chapter - 1) if chapter > 1 else None
    if chapter > 1:
        fuori.append({"name": f"scheda del registro, capitolo {chapter - 1}",
                      "path": str(lg.ledger_path(project)),
                      "version": voce.source_hash if voce else "",
                      "status": "consultata" if voce and not voce.error else
                      "UNVERIFIED: scheda assente o non riuscita"})
    fuori.append({"name": "quaderno e struttura del libro", "path": str(project.state_path),
                  "version": "", "status": "consultati"})
    return fuori


# --------------------------------------------------------------------------- #
#  1. la diagnosi
# --------------------------------------------------------------------------- #
_SCENES_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"scenes": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"section": {"type": "integer"}, "wants": {"type": "string"},
                       "obstacle": {"type": "string"}, "changes": {"type": "string"},
                       "new": {"type": "string"}, "why_here": {"type": "string"},
                       "static": {"type": "boolean"}, "duplicates": {"type": "integer"},
                       "quote": {"type": "string"}},
        "required": ["section", "wants", "obstacle", "changes", "new", "why_here", "static",
                     "duplicates", "quote"]}}},
    "required": ["scenes"],
}

_CLUSTERS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"clusters": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"label": {"type": "string"},
                       "kind": {"type": "string",
                                "enum": ["motivo_progressivo", "richiamo_necessario",
                                         "ridondanza", "duplicazione"]},
                       "why": {"type": "string"}},
        "required": ["label", "kind", "why"]}}},
    "required": ["clusters"],
}


def scene_functions(client: Any, sections: list[tuple[str, str]], *, model: str | None,
                    language: str) -> list[dict[str, Any]]:
    """Gate E: per ogni scena cosa vuole chi la vive, l'ostacolo, cosa cambia, cosa c'è di nuovo,
    perché sta qui, e se ripete il lavoro di un'altra."""
    corpo = "\n\n".join(f"## [{i + 1}] {h.lstrip('# ')}\n{t}" for i, (h, t) in enumerate(sections))
    sistema = (
        "Sei un editor strutturale. Per OGNI scena del capitolo (il numero fra parentesi quadre) "
        "rispondi: 'wants' cosa vuole il protagonista o il personaggio focale; 'obstacle' cosa "
        "si oppone; 'changes' cosa cambia entro la fine (situazione, conoscenza, rischio, "
        "relazione, obiettivo); 'new' quale informazione, decisione o conseguenza nuova emerge; "
        "'why_here' perché la scena deve stare proprio qui; 'static' vero se la scena non "
        "cambia niente di concreto; 'duplicates' il numero della scena che svolge GIÀ la stessa "
        "funzione (stesso ambiente, stesse informazioni, stesso effetto), 0 se nessuna; "
        f"'quote' una frase breve che lo mostra. Lingua delle risposte: {language}."
    )
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": corpo}],
                             schema=_SCENES_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - chi chiama lo annota
        return []
    return [s for s in (r.get("scenes") or []) if isinstance(s, dict)] if isinstance(r, dict) \
        else []


def repetition_clusters(client: Any, cap: Any, piano: Any, sections: list[tuple[str, str]], *,
                        text: str, model: str | None, language: str
                        ) -> tuple[list[tuple[str, str, list[tuple[int, int]], str]], list[str]]:
    """Gate F, tre livelli: le parole (misure), le idee (etichette di significato), le scene.
    Ritorna i cluster classificati ``(etichetta, tipo, posizioni, perché)`` e gli errori."""
    from bookwriter.forge import editor as ed

    resoconto = ed.EditorialReport()
    tags = ed._tag_sections(client, cap, piano, resoconto, model=model)
    idee: dict[str, list[tuple[int, int]]] = {}
    nomi: dict[str, str] = {}
    for t in tags:
        chiave = ed._norm_label(t.idea)
        if chiave:
            idee.setdefault(chiave, []).append((t.section, t.index))
            nomi.setdefault(chiave, t.idea)
    misure = mx.measure(text)
    candidati = [(nomi[k], pos) for k, pos in sorted(idee.items(), key=lambda kv: -len(kv[1]))
                 if len(pos) >= 3][:12]
    parole = [(f"parola «{b.term}» ({b.count} volte)", []) for b in misure.bursts[:5]]
    if not candidati and not parole:
        return [], resoconto.errors
    elenco = "\n".join(
        f"- {etichetta}: {len(pos)} paragrafi, scene {sorted({s + 1 for s, _ in pos})}"
        for etichetta, pos in candidati) + "\n" + "\n".join(f"- {e}" for e, _ in parole)
    sistema = (
        "Classifica ogni gruppo ripetuto di un capitolo di romanzo: 'motivo_progressivo' (torna "
        "e ogni volta aggiunge significato, una prova, un costo o una conseguenza), "
        "'richiamo_necessario' (serve alla comprensione), 'ridondanza' (dice di nuovo la stessa "
        "tesi: da comprimere), 'duplicazione' (scene diverse producono lo stesso effetto: da "
        "sostituire con un evento nuovo). Un motivo centrale del libro non è ridondante se "
        "progredisce. 'label' è l'etichetta esatta del gruppo; 'why' in una riga. Lingua: "
        f"{language}."
    )
    corpo = "\n\n".join(f"## [{i + 1}] {h.lstrip('# ')}\n{t}" for i, (h, t) in enumerate(sections))
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": f"# I gruppi\n{elenco}\n\n# Il capitolo\n"
                                                         f"{corpo}"}],
                             schema=_CLUSTERS_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001
        return [], [*resoconto.errors, f"ripetizioni: {str(exc)[:160]}"]
    posizioni = {e: pos for e, pos in candidati + parole}
    fuori = []
    for c in (r.get("clusters") or []) if isinstance(r, dict) else []:
        if not isinstance(c, dict):
            continue
        etichetta = str(c.get("label") or "")
        pos = posizioni.get(etichetta) or next(
            (p for e, p in posizioni.items() if _simile(e, etichetta, 0.5)), [])
        fuori.append((etichetta, str(c.get("kind") or ""), pos, str(c.get("why") or "")))
    return fuori, resoconto.errors


class _Ids:
    """Gli ID stabili dei rilievi: CH11-TIME-001, CH11-TIME-002…"""

    def __init__(self, chapter: int) -> None:
        self.chapter = chapter
        self.contatori: dict[str, int] = {}

    def next(self, categoria: str) -> str:
        self.contatori[categoria] = self.contatori.get(categoria, 0) + 1
        return f"CH{self.chapter:02d}-{categoria}-{self.contatori[categoria]:03d}"


def _location(sections: list[tuple[str, str]], k: int) -> str:
    if 0 <= k < len(sections):
        return f"scena {k + 1} «{sections[k][0].lstrip('# ').strip()[:60]}»"
    return "tutto il capitolo"


def diagnose(client: Any, text: str, *, chapter: int, kind: str, pack: Any, piano: Any,
             budget: int, language: str, explain_language: str, notebook: dict | None,
             model: str | None, check_model: str | None, protagonist: str = "",
             baseline: dict | None = None, note=None, metodo: Any = None
             ) -> tuple[list[Issue], tl.Timeline, list[ObjectRecord], ql.Evaluation, list[str]]:
    """I rilievi del capitolo, dai rilevatori separati. Ritorna anche il registro degli eventi,
    il registro degli oggetti e la rubrica (per la situazione di partenza)."""
    from bookwriter.forge import editor as ed

    narrativo = kind in ql.NARRATIVE_KINDS
    cap = ed.parse(text)
    sezioni = [(s.heading, s.text) for s in cap.sections]
    versione = _version(text)
    ids = _Ids(chapter)
    errori: list[str] = []
    grezzi: list[Issue] = []

    def aggiungi(categoria: str, severita: str, sezione: int, diagnosi: str, *, prova: str = "",
                 azione: str = "", base: str = "testo", origine: str = "", causa: str = "") -> None:
        grezzi.append(Issue(
            chapter=chapter, source_version=versione, category=categoria, severity=severita,
            location=_location(sezioni, sezione), section=sezione, evidence=prova,
            diagnosis=diagnosi, reader_impact=_IMPACT.get(categoria, ""), cause=causa,
            proposed_action=azione, acceptance_test=_TEST.get(categoria, ""),
            regression_risk=("cambiare un orario o un luogo può contraddire le scene vicine"
                             if categoria in ("TIME", "PLACE") else
                             "fondere o tagliare può perdere un indizio o un passaggio di mano"
                             if categoria in ("SCENE", "REP", "LEN") else ""),
            basis=base, origin=origine))

    cronologia = tl.Timeline()
    registro: list[ObjectRecord] = []
    if narrativo:
        if note:
            note("Diagnosi: il registro degli eventi, scena per scena…")
        cronologia = tl.build(client, sezioni, pack, piano, model=check_model,
                              scene_for=lambda i: ed._scene_for(piano, i, cap))
        for r in cronologia.records:
            if r.error:
                errori.append(f"registro degli eventi, «{r.heading[:40]}»: {r.error}")
        for i in cronologia.issues:
            base = "canone" if i.kind in ("filo aperto", "anticipazione") else "testo"
            gravita = _DA_TIMELINE.get(i.severity, "Moderate")
            if i.kind == "anticipazione" and gravita == "Major":
                gravita = "Moderate"  # un giudizio sulla struttura del libro, non un conto
            aggiungi(_TIMELINE_KIND.get(i.kind, "CONT"), gravita, i.section, i.problem,
                     prova=i.quote, azione=i.fix, base=base, origine=f"registro ({i.origin})")
        registro = object_registry(cronologia.records)
        for sezione, _nome, problema in object_issues(registro):
            aggiungi("OBJ", "Major", sezione, problema, azione="mostra il passaggio di mano "
                     "o tieni l'oggetto dov'era", origine="registro degli oggetti")
        if note:
            note("Diagnosi: la funzione delle scene…")
        for f in scene_functions(client, sezioni, model=check_model, language=explain_language):
            try:
                k = int(f.get("section") or 0) - 1
                doppia = int(f.get("duplicates") or 0) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= k < len(sezioni):
                continue
            if 0 <= doppia < len(sezioni) and doppia != k:
                aggiungi("SCENE", "Major", k,
                         f"la scena ripete la funzione della scena {doppia + 1}: "
                         f"{f.get('changes', '')}", prova=str(f.get("quote") or ""),
                         azione=f"fondi con la scena {doppia + 1} o dalle un evento nuovo e "
                         "irreversibile", origine="funzione delle scene")
            elif f.get("static"):
                aggiungi("SCENE", "Major", k, "la scena non cambia niente di concreto",
                         prova=str(f.get("quote") or ""), azione="dalle una conseguenza: una "
                         "perdita, una scelta, un cambio di custodia, una prova", origine=
                         "funzione delle scene")
    if note:
        note("Diagnosi: le ripetizioni (parole, idee, scene)…")
    cluster, err = repetition_clusters(client, cap, piano, sezioni, text=text, model=check_model,
                                       language=explain_language)
    errori += err
    for etichetta, tipo, posizioni, perche in cluster:
        if tipo not in ("ridondanza", "duplicazione"):
            continue
        if etichetta.startswith("parola «"):
            # una parola che torna è un segnale, non una prova: moderato, e una volta sola
            aggiungi("REP", "Moderate", -1, f"{etichetta}: {perche}", origine="ripetizioni",
                     azione="dove la parola non aggiunge significato, dillo in un altro modo "
                     "o togli la frase; mai tagliare scene per una parola")
            continue
        sezione = posizioni[1][0] if len(posizioni) > 1 else (posizioni[0][0] if posizioni
                                                               else -1)
        aggiungi("REP", "Major" if tipo == "duplicazione" or len(posizioni) >= 5 else "Moderate",
                 sezione, f"{etichetta} ({tipo}, {len(posizioni)} ricorrenze): {perche}",
                 azione="comprimi dove non aggiunge nulla; trasforma le ricorrenze in una catena "
                 "di eventi (indizio, conflitto, costo, scelta, conseguenza)",
                 origine="ripetizioni")
    if note:
        note("Diagnosi: la rubrica 0-10…")
    rubrica = ql.evaluate_text(
        client, text, chapter=chapter, kind=kind, notebook=notebook, budget=budget,
        language=language, explain_language=explain_language,
        previous="\n".join(tl._start_lines(pack)), baseline=baseline, protagonist=protagonist,
        model=check_model)
    errori += rubrica.errors
    for s in rubrica.scores:
        if not s.applicable or s.score >= 7 or not ql.CRITERIA_BY_KEY.get(s.key, ql._GLOBALE) \
                .in_global:
            continue
        categoria = _CRITERION_CATEGORY.get(s.key, "STYLE")
        severita = "Major" if s.critical and s.score < 5 else "Moderate"
        for p in s.problems:
            aggiungi(categoria, severita, p.section, f"{s.label}: {p.problem}", prova=p.quote,
                     azione=p.fix, origine=f"rubrica ({s.key} {s.score})")
    if budget:
        parole = mx.count_words(text)
        basso, alto = int(budget * (1 - LENGTH_BAND)), int(budget * (1 + LENGTH_BAND))
        if not basso <= parole <= alto:
            aggiungi("LEN", "Major", -1,
                     f"{parole} parole fuori dalla fascia {basso}–{alto} (obiettivo {budget})",
                     azione="ridisegna le scene ridondanti invece di tagliare in coda",
                     origine="misura")
    if pack is not None:
        resoconto = ed.EditorialReport()
        ed._sources(text, piano, pack, resoconto)
        for riga in resoconto.sources:
            if "non compare nel materiale" in riga or "citazione attribuita" in riga:
                aggiungi("SRC", "Minor", -1, f"UNVERIFIED: {riga}", origine="fonti",
                         azione="registra la provenienza in research/ o non darlo per certo")
    if narrativo and metodo is not None and metodo.registers:
        if note:
            note("Diagnosi: i registri della metodologia (i conti del testo)…")
        from bookwriter.forge import methodology as md

        osservazioni, err = md.extract(client, metodo, sezioni, model=check_model)
        errori += err
        for e in md.check(metodo, osservazioni):
            if e.failed:
                aggiungi("REG", "Blocker", e.sections[0] if e.sections else -1,
                         f"registro «{e.register}»: {e.detail}",
                         prova=e.evidence[0] if e.evidence else "",
                         azione="fissa prima il valore canonico, poi correggi ogni passo che "
                                "lo contraddice", origine="metodologia (validatore)")
    rilievi = _consolidate(grezzi, ids)
    return rilievi, cronologia, registro, rubrica, errori


def _consolidate(grezzi: list[Issue], ids: _Ids) -> list[Issue]:
    """Toglie i doppioni (stessa scena, stessa categoria, stesso problema), tiene la gravità più
    alta, assegna gli ID, e limita moderati e minori."""
    ordine = {s: i for i, s in enumerate(SEVERITIES)}
    tenuti: list[Issue] = []
    for r in sorted(grezzi, key=lambda x: ordine.get(x.severity, 9)):
        doppio = next((t for t in tenuti if t.section == r.section and
                       CATEGORY_BLOCK.get(t.category) == CATEGORY_BLOCK.get(r.category)
                       and _simile(t.diagnosis, r.diagnosis)), None)
        if doppio is not None:
            if r.evidence and not doppio.evidence:
                doppio.evidence = r.evidence
            continue
        tenuti.append(r)
    ripetizioni = [r for r in tenuti if r.category == "REP"]
    if len(ripetizioni) > MAX_REP:
        resto = ripetizioni[MAX_REP - 1:]
        accorpato = resto[0]
        accorpato.diagnosis = (f"{len(resto)} gruppi di ripetizione: "
                               + "; ".join(r.diagnosis[:80] for r in resto[:6]))
        accorpato.severity = "Moderate"
        tenuti = [r for r in tenuti if r not in resto[1:]]
    moderati = [r for r in tenuti if r.severity == "Moderate"][:MAX_MODERATE]
    minori = [r for r in tenuti if r.severity == "Minor"][:MAX_MINOR]
    scelti = [r for r in tenuti if r.severity in ("Blocker", "Major")] + moderati + minori
    for r in sorted(scelti, key=lambda x: (BLOCKS.index(x.block), ordine.get(x.severity, 9))):
        r.issue_id = ids.next(r.category)
    return sorted(scelti, key=lambda x: x.issue_id)


# --------------------------------------------------------------------------- #
#  2. il piano
# --------------------------------------------------------------------------- #
_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "changes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"issue_ids": {"type": "array", "items": {"type": "string"}},
                           "operation": {"type": "string", "enum": list(OPERATIONS)},
                           "targets": {"type": "array", "items": {"type": "integer"}},
                           "first_paragraph": {"type": "integer"},
                           "last_paragraph": {"type": "integer"},
                           "intended_effect": {"type": "string"},
                           "protected_content": {"type": "string"},
                           "words": {"type": "integer"}},
            "required": ["issue_ids", "operation", "targets", "first_paragraph",
                         "last_paragraph", "intended_effect", "protected_content", "words"]}},
        "preserve": {"type": "array", "items": {"type": "string"}},
        "author_questions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"issue_ids": {"type": "array", "items": {"type": "string"}},
                           "question": {"type": "string"}},
            "required": ["issue_ids", "question"]}},
    },
    "required": ["changes", "preserve", "author_questions"],
}


def plan(client: Any, issues: list[Issue], sections: list[tuple[str, str]], *, budget: int,
         strengths: list[str], canon: str, model: str | None, language: str
         ) -> tuple[list[Change], list[str], list[str], list[str]]:
    """Il piano degli interventi: ``(interventi, da preservare, domande all'autore, errori)``.

    Il modello propone; il codice controlla che ogni Blocker e Major abbia un intervento o una
    domanda all'autore (se no, aggiunge una riscrittura mirata della sua scena), che i bersagli
    esistano, e mette gli interventi nell'ordine dei blocchi.
    """
    elenco = "\n".join(f"- {i.instruction()} [{i.location}; base: {i.basis}]" for i in issues)
    scene = "\n".join(f"[{k + 1}] {h.lstrip('# ')} — {mx.count_words(t)} parole "
                      f"({len([p for p in re.split(chr(10) * 2, t) if p.strip()])} paragrafi)"
                      for k, (h, t) in enumerate(sections))
    sistema = (
        "Sei l'editor che pianifica la revisione di un capitolo di romanzo. NON riscrivi: decidi "
        "gli interventi. Ogni intervento risolve uno o più rilievi ('issue_ids') con UNA "
        "operazione: 'rewrite' (riscrivere una scena o dei paragrafi), 'merge' (fondere due "
        "scene che fanno lo stesso lavoro: 'targets' le due scene), 'split' (separare funzioni "
        "confuse), 'insert' (aggiungere un ponte, un'azione, un passaggio mancante: dopo il "
        "paragrafo 'last_paragraph' della scena), 'move' (spostare la scena 'targets[0]' prima "
        "della scena 'targets[1]'), 'trim' (togliere ridondanza senza perdere contenuto "
        "necessario), 'author_decision' (serve una scelta dell'autore sul canone: non si "
        "inventa). 'targets' i numeri delle scene; 'first_paragraph'/'last_paragraph' i "
        "paragrafi (0 = la scena intera); 'words' le parole del risultato. Ordine: prima "
        "cronologia e causalità, poi continuità col capitolo precedente, poi oggetti e "
        "documenti, poi funzione delle scene, poi ripetizioni, poi prosa, infine misura. Non "
        "riscrivere il capitolo intero per un difetto locale; non tagliare per stare nella "
        f"misura: sostituisci il materiale debole con narrazione più efficace. Al più "
        f"{MAX_CHANGES} interventi. In 'preserve' le cose che funzionano e non vanno perse "
        "(indizi, immagini, progressi), in 'author_questions' le decisioni di canone che non "
        "si possono dedurre. La misura del capitolo è "
        + (f"{budget} parole (fascia {int(budget * 0.9)}–{int(budget * 1.1)})." if budget else
           "libera.") + f" Lingua: {language}."
    )
    utente = "\n\n".join(p for p in (
        f"# Il canone (quaderno, decisioni, capitolo precedente)\n{canon}" if canon else "",
        f"# Le scene\n{scene}",
        f"# I rilievi\n{elenco}",
        ("# Che cosa funziona già (dalla rubrica)\n" + "\n".join(f"- {s}" for s in strengths))
        if strengths else "") if p)
    errori: list[str] = []
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_PLAN_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001
        errori.append(f"piano: {str(exc)[:160]}")
        r = {}
    noti = {i.issue_id for i in issues}
    interventi: list[Change] = []
    for c in ((r.get("changes") or []) if isinstance(r, dict) else [])[:MAX_CHANGES]:
        if not isinstance(c, dict) or c.get("operation") not in OPERATIONS:
            continue
        bersagli = [int(t) - 1 for t in c.get("targets") or []
                    if isinstance(t, int) and 0 < t <= len(sections)]
        rilievi = [x for x in c.get("issue_ids") or [] if x in noti]
        if c["operation"] != "preserve" and not bersagli:
            continue
        if c["operation"] in ("merge", "move") and len(bersagli) < 2:
            continue
        blocchi = [CATEGORY_BLOCK.get(next(i.category for i in issues if i.issue_id == x),
                                      "prosa") for x in rilievi]
        interventi.append(Change(
            related_issue_ids=rilievi, operation=c["operation"], targets=bersagli,
            first_paragraph=int(c.get("first_paragraph") or 0) - 1,
            last_paragraph=int(c.get("last_paragraph") or 0) - 1,
            intended_effect=str(c.get("intended_effect") or ""),
            protected_content=str(c.get("protected_content") or ""),
            words=max(0, int(c.get("words") or 0)),
            block=min(blocchi, key=BLOCKS.index) if blocchi else "prosa"))
    domande = []
    in_domanda: set[str] = set()
    for q in (r.get("author_questions") or []) if isinstance(r, dict) else []:
        if isinstance(q, dict) and str(q.get("question") or "").strip():
            legati = [x for x in q.get("issue_ids") or [] if x in noti]
            in_domanda.update(legati)
            domande.append(str(q["question"]).strip()
                           + (f" ({', '.join(legati)})" if legati else ""))
    # ogni Blocker e Major ha un intervento o una domanda: se no, una riscrittura mirata
    coperti = {x for c in interventi for x in c.related_issue_ids} | in_domanda
    for i in issues:
        if i.severity in ("Blocker", "Major") and i.issue_id not in coperti:
            if i.section < 0:
                if i.category == "LEN":
                    continue  # la misura si sistema con gli altri interventi, non da sola
                domande.append(f"{i.issue_id}: {i.diagnosis} — nessuna scena da toccare: "
                               "serve una decisione")
                continue
            interventi.append(Change(related_issue_ids=[i.issue_id], operation="rewrite",
                                     targets=[i.section], intended_effect=i.proposed_action,
                                     block=i.block))
    interventi = _one_per_scene(interventi, sections)
    interventi.sort(key=lambda c: (BLOCKS.index(c.block), c.targets[:1]))
    for k, c in enumerate(interventi, 1):
        c.change_id = f"CHG-{k:03d}"
    preservare = [str(x).strip() for x in (r.get("preserve") or []) if str(x).strip()] \
        if isinstance(r, dict) else []
    return interventi, preservare[:12], domande, errori


def _one_per_scene(interventi: list[Change], sections: list[tuple[str, str]]) -> list[Change]:
    """Gli interventi sulla stessa scena diventano UNA riscrittura che li raccoglie tutti: nella
    prova v4 «The Filing» è stata riscritta sette volte di fila, e ogni passaggio la accorciava
    e la spostava un po'. Fusioni e spostamenti restano come sono."""
    per_scena: dict[int, list[Change]] = {}
    fuori: list[Change] = []
    for c in interventi:
        if c.operation in ("rewrite", "trim", "insert") and len(c.targets) == 1:
            per_scena.setdefault(c.targets[0], []).append(c)
        else:
            fuori.append(c)
    for k, lista in per_scena.items():
        if len(lista) == 1:
            fuori.append(lista[0])
            continue
        parole = mx.count_words(sections[k][1]) if 0 <= k < len(sections) else 0
        fuori.append(Change(
            related_issue_ids=list(dict.fromkeys(x for c in lista for x in c.related_issue_ids)),
            operation="rewrite", targets=[k],
            intended_effect=" · ".join(c.intended_effect for c in lista if c.intended_effect),
            protected_content=" · ".join(c.protected_content for c in lista
                                         if c.protected_content),
            words=parole, block=min((c.block for c in lista), key=BLOCKS.index)))
    return fuori


# --------------------------------------------------------------------------- #
#  3. l'intervento
# --------------------------------------------------------------------------- #
_CANON_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"decisions": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"register": {"type": "string"}, "canonical": {"type": "string"},
                       "basis": {"type": "string"}, "author_question": {"type": "string"}},
        "required": ["register", "canonical", "basis", "author_question"]}}},
    "required": ["decisions"],
}


def canonical_values(client: Any, issues: list[Issue], metodo: Any, canon: str, *,
                     model: str | None, language: str) -> tuple[str, list[str]]:
    """Prima i dati, poi la prosa (review v4, §6): per ogni registro che non torna, il valore
    canonico che il testo deve avere, ricavato dal canone e dal testo — o una domanda all'autore
    se non si deduce. Ritorna il blocco per chi riscrive e le domande."""
    conti = [i for i in issues if i.category == "REG"]
    if not conti or metodo is None:
        return "", []
    registri = "\n".join(f"- [{r.id}] {r.name}: {r.entity}; categorie {', '.join(r.categories)}"
                         + (f"; totale canonico {r.total}" if r.total is not None else "")
                         + (f"; all'inizio {r.initial}" if r.initial else "")
                         for r in metodo.registers)
    sistema = (
        "Prima di correggere la prosa si fissano i dati. Per ogni registro con un conto che non "
        "torna, scrivi in 'canonical' i valori che il testo deve avere da qui in poi (il totale, "
        "la distribuzione, la sequenza degli stati con gli orari), presi dal canone e dal testo; "
        "in 'basis' su che cosa ti basi. Se il valore non si deduce senza una scelta di trama, "
        "lascia 'canonical' vuoto e scrivi la domanda per l'autore in 'author_question'. Non "
        f"inventare. Lingua: {language}."
    )
    utente = "\n\n".join(p for p in (
        f"# Il canone\n{canon}" if canon else "", f"# I registri\n{registri}",
        "# I conti che non tornano\n" + "\n".join(f"- {i.instruction()}" for i in conti)) if p)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_CANON_SCHEMA, model=model)
    except Exception:  # noqa: BLE001 - senza valori canonici i conti diventano domande
        return "", [f"{i.issue_id}: {i.diagnosis} — serve il valore canonico" for i in conti]
    righe, domande = [], []
    for d in (r.get("decisions") or []) if isinstance(r, dict) else []:
        if not isinstance(d, dict):
            continue
        if str(d.get("canonical") or "").strip():
            righe.append(f"- {d.get('register')}: {d['canonical']} (base: {d.get('basis', '')})")
        elif str(d.get("author_question") or "").strip():
            domande.append(f"registro {d.get('register')}: {d['author_question']}")
    blocco = ("# I valori canonici, fissati prima della prosa: ogni passo li rispetta\n"
              + "\n".join(righe)) if righe else ""
    return blocco, domande


def _context_block(cap: Any, k: int, records: list[tl.SceneRecord], registro: list[ObjectRecord],
                   start: list[str]) -> str:
    prima = [r for r in records[:k] if not r.error]
    pezzi = [tl.facts_block(prima, start) if prima else
             ("# Dove il capitolo precedente ha lasciato il protagonista\n" + "\n".join(start)
              if start else "")]
    if registro:
        pezzi.append("# Gli oggetti e i documenti del capitolo, con i passaggi di mano\n"
                     + "\n".join(f"- {o.line()}" for o in registro[:20]))
    return "\n\n".join(p for p in pezzi if p)


def _ask(client: Any, sistema: str, utente: str, model: str | None) -> str:
    from bookwriter.forge.writing import _clean_continuation

    return _clean_continuation(client.chat([{"role": "system", "content": sistema},
                                            {"role": "user", "content": utente}], model=model))


def _paragraphs(testo: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", testo or "") if p.strip()]


def execute(client: Any, text: str, changes: list[Change], issues: list[Issue], *,
            records: list[tl.SceneRecord], registro: list[ObjectRecord], start: list[str],
            canon: str, preserve: list[str], language: str, style: str, kind: str,
            model: str | None, should_stop=None, note=None, checkpoint=None,
            budget: int = 0) -> str:
    """Esegue gli interventi nell'ordine del piano, ciascuno solo sul suo bersaglio.

    I bersagli sono le scene della bozza di partenza, tenute per identità: dopo una fusione o
    uno spostamento un intervento colpisce ancora la scena giusta. ``checkpoint(testo)``, se c'è,
    si chiama una volta finiti i blocchi strutturali (cronologia, continuità, oggetti, scene) e
    ritorna ``(registro degli eventi, registro degli oggetti)`` aggiornati per gli interventi dopo.
    """
    from bookwriter.forge import editor as ed
    from bookwriter.forge.writing import _NO_INVENTION, FORBIDDEN

    cap = ed.parse(text)
    # le sezioni si spostano (fusioni, spostamenti) e si sostituiscono (riscritture): si tengono
    # per identità, e una sezione sostituita rimanda a quella che l'ha presa il posto
    originali = list(cap.sections)
    sostituta: dict[int, Any] = {}

    def attuale(sez: Any) -> Any | None:
        visti = set()
        while id(sez) in sostituta and id(sez) not in visti:
            visti.add(id(sez))
            sez = sostituta[id(sez)]
        return sez if any(sez is x for x in cap.sections) else None

    def posto(sez: Any) -> int:
        return next(i for i, x in enumerate(cap.sections) if x is sez)
    chi = "il romanziere" if kind in ql.NARRATIVE_KINDS else "l'autore"
    regole = (
        f"Sei {chi} di questo libro e fai UN intervento di revisione deciso dall'editor. "
        "Tocca solo quello che ti viene dato; tieni fatti, personaggi, indizi, oggetti e chi li "
        "ha, la voce e lo stile; le cose da preservare restano. Un problema si risolve nei "
        "fatti della scena (un passaggio mostrato, una causa, una scelta, una conseguenza), "
        "non con una spiegazione al lettore. Non inventare decisioni di canone: se manca un "
        f"dato, lascia la cosa come era. Lingua: {language}. Non scrivere mai: {FORBIDDEN}.\n\n"
        + _NO_INVENTION + (f"\n\nStile richiesto:\n{style}" if style else ""))
    controllato = checkpoint is None
    for n, c in enumerate(changes, 1):
        if should_stop and should_stop():
            break
        if not controllato and c.block not in BLOCKS[:4]:
            controllato = True
            if any(x.done for x in changes[:n - 1]):
                records, registro = checkpoint(cap.render())
        if c.operation in ("preserve", "author_decision"):
            c.before_after_summary = "nessuna modifica (" + (
                "da preservare" if c.operation == "preserve" else "decisione dell'autore") + ")"
            continue
        if note:
            note(("change", n, len(changes)))
        bersagli = []
        for t in c.targets:
            if 0 <= t < len(originali):
                x = attuale(originali[t])
                if x is not None and not any(x is y for y in bersagli):
                    bersagli.append(x)
        if not bersagli:
            c.before_after_summary = "bersaglio non più presente (fuso o spostato prima)"
            continue
        sezione = bersagli[0]
        k = posto(sezione)
        rilievi = "\n".join(f"- {i.instruction()}" for i in issues
                            if i.issue_id in c.related_issue_ids)
        contesto = _context_block(cap, k, records, registro, start)
        protetto = "\n".join(f"- {p}" for p in [c.protected_content, *preserve] if p)
        comune = "\n\n".join(p for p in (
            f"# I rilievi da risolvere\n{rilievi}" if rilievi else "",
            f"# L'effetto voluto\n{c.intended_effect}" if c.intended_effect else "",
            f"# Da proteggere\n{protetto}" if protetto else "",
            f"# Il canone\n{canon}" if canon else "", contesto) if p)
        prima = sum(s.words for s in bersagli)
        if c.operation == "move":
            cap.sections.pop(k)
            dove = posto(bersagli[1]) if len(bersagli) > 1 else k
            cap.sections.insert(dove, sezione)
            c.done = True
            c.before_after_summary = f"scena spostata prima di «{ed._short(bersagli[1].heading)}»"
            continue
        if c.operation == "merge" and len(bersagli) > 1:
            compito = (f"Fondi queste {len(bersagli)} scene in UNA scena che abbia una funzione "
                       "sola e chiara: tieni i dettagli più forti di ciascuna, togli quello che "
                       "si ripete, e falla finire con un evento nuovo e irreversibile. La prima "
                       "riga è `## ` con un titolo breve.")
            testo_in = "\n\n".join(f"{s.heading}\n\n{s.text}" for s in bersagli)
        elif c.operation == "split":
            compito = ("Separa questa scena in DUE scene, ciascuna con una funzione chiara e il "
                       "suo titolo `## `.")
            testo_in = f"{sezione.heading}\n\n{sezione.text}"
        elif c.operation == "insert":
            dopo = max(0, min(c.last_paragraph, len(sezione.paragraphs) - 1))
            compito = ("Scrivi il passaggio che manca, da inserire esattamente fra il paragrafo "
                       "prima e quello dopo: senza titolo, senza ripeterli.")
            testo_in = ("# Il paragrafo prima\n" + sezione.paragraphs[dopo]
                        + ("\n\n# Il paragrafo dopo\n" + sezione.paragraphs[dopo + 1]
                           if dopo + 1 < len(sezione.paragraphs) else ""))
        else:  # rewrite, trim
            primo = c.first_paragraph if 0 <= c.first_paragraph < len(sezione.paragraphs) \
                else -1
            if primo >= 0:
                ultimo = max(primo, min(c.last_paragraph, len(sezione.paragraphs) - 1))
                parte = "\n\n".join(sezione.paragraphs[primo:ultimo + 1])
                compito = ("Riscrivi SOLO questi paragrafi della scena (senza titolo): devono "
                           "saldarsi a quello che c'è prima e dopo."
                           + (" Togli la ridondanza senza perdere contenuto necessario."
                              if c.operation == "trim" else ""))
                testo_in = (f"# La scena intera (per il contesto, non riscriverla)\n"
                            f"{sezione.heading}\n\n{sezione.text}\n\n# I PARAGRAFI DA "
                            f"RISCRIVERE\n{parte}")
            else:
                compito = ("Riscrivi la scena intera; la prima riga è il suo titolo, identico."
                           + (" Togli la ridondanza senza perdere contenuto necessario."
                              if c.operation == "trim" else ""))
                testo_in = f"{sezione.heading}\n\n{sezione.text}"
        parole = c.words or prima
        utente = "\n\n".join(p for p in (comune, testo_in,
                                         f"# Da fare\n{compito} Circa {parole} parole.") if p)
        try:
            nuovo = _ask(client, regole, utente, model)
        except Exception as exc:  # noqa: BLE001 - un intervento mancato si dice
            c.before_after_summary = f"non eseguito: {str(exc)[:120]}"
            continue
        if not nuovo.strip():
            c.before_after_summary = "non eseguito: risposta vuota"
            continue
        dopo_testo = ed.parse("# x\n\n" + nuovo)
        uscita = mx.count_words("\n\n".join(p for x in dopo_testo.sections for p in x.paragraphs))
        if c.operation == "insert":
            ammesso = uscita <= MAX_INSERT
        else:
            base = c.words if c.operation in ("merge", "split") and c.words else prima
            basso, alto = OP_RANGE.get(c.operation, (0.8, 1.2))
            if c.first_paragraph >= 0 and c.operation in ("rewrite", "trim"):
                base = mx.count_words("\n\n".join(
                    sezione.paragraphs[c.first_paragraph:max(c.first_paragraph,
                                                             c.last_paragraph) + 1]))
            ammesso = basso * base <= uscita <= alto * max(base, 40)
        if ammesso and budget and c.operation in ("trim", "merge"):
            # il capitolo non scende sotto la sua fascia per un taglio
            ammesso = cap.words - prima + uscita >= (1 - LENGTH_BAND) * budget
        if not ammesso:
            c.before_after_summary = (f"scartato: {uscita} parole fuori dalla forbice di "
                                      f"«{c.operation}» (bersaglio {prima})")
            continue
        if c.operation in ("merge", "split") or (c.operation in ("rewrite", "trim")
                                                 and c.first_paragraph < 0):
            nuove = [s for s in dopo_testo.sections if s.paragraphs]
            if not nuove:
                c.before_after_summary = "non eseguito: nessuna scena nella risposta"
                continue
            if not nuove[0].heading:
                nuove[0].heading = sezione.heading
            for vecchia in bersagli:
                cap.sections.pop(posto(vecchia))
                sostituta[id(vecchia)] = nuove[0]
            for j, nuova in enumerate(nuove):
                cap.sections.insert(min(k, len(cap.sections)) + j, nuova)
            dopo_n = sum(s.words for s in nuove)
        elif c.operation == "insert":
            pezzi = [p for s in dopo_testo.sections for p in s.paragraphs]
            dopo = max(0, min(c.last_paragraph, len(sezione.paragraphs) - 1))
            sezione.paragraphs[dopo + 1:dopo + 1] = pezzi
            dopo_n = mx.count_words("\n\n".join(pezzi))
        else:
            pezzi = [p for s in dopo_testo.sections for p in s.paragraphs]
            primo = c.first_paragraph
            ultimo = max(primo, min(c.last_paragraph, len(sezione.paragraphs) - 1))
            sezione.paragraphs[primo:ultimo + 1] = pezzi
            dopo_n = mx.count_words("\n\n".join(pezzi))
        c.done = True
        c.before_after_summary = (f"{c.operation} su «{ed._short(sezione.heading)}»: "
                                  f"{prima if c.operation != 'insert' else 0} → {dopo_n} parole")
    return cap.render()


# --------------------------------------------------------------------------- #
#  4. la verifica
# --------------------------------------------------------------------------- #
_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"results": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"issue_id": {"type": "string"},
                       "status": {"type": "string", "enum": list(STATUSES[1:])},
                       "evidence": {"type": "string"}},
        "required": ["issue_id", "status", "evidence"]}}},
    "required": ["results"],
}
_PRESERVE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"item": {"type": "string"}, "kept": {"type": "boolean"},
                       "evidence": {"type": "string"}},
        "required": ["item", "kept", "evidence"]}}},
    "required": ["items"],
}
#: Quanti rilievi per chiamata di verifica.
VERIFY_BATCH = 10


def verify_issues(client: Any, issues: list[Issue], text: str, *, model: str | None,
                  language: str) -> list[str]:
    """Lo stato di ogni rilievo nel testo rivisto, con la citazione. Ritorna gli errori."""
    from bookwriter.forge import editor as ed

    cap = ed.parse(text)
    corpo = "\n\n".join(f"## [{i + 1}] {s.heading.lstrip('# ')}\n{s.text}"
                        for i, s in enumerate(cap.sections))
    sistema = (
        "Sei il verificatore indipendente di una revisione. Per OGNI rilievo dato, leggi il "
        "testo RIVISTO e decidi: RESOLVED (risolto: in 'evidence' la citazione del testo "
        "rivisto che lo prova), PARTIALLY_RESOLVED (migliorato ma resta un problema: quale), "
        "UNRESOLVED (non risolto: dove si vede ancora), REGRESSION (è peggiorato o la correzione "
        "ha creato un problema nuovo: quale), NOT_APPLICABLE (non si applica più: perché). Un "
        "rilievo NON è risolto solo perché il testo è cambiato: serve la prova. Usa il test di "
        f"accettazione di ciascuno. Lingua: {language}."
    )
    errori = []
    for k in range(0, len(issues), VERIFY_BATCH):
        lotto = issues[k:k + VERIFY_BATCH]
        elenco = "\n".join(f"- {i.issue_id}: {i.diagnosis} — prova originale: «{i.evidence[:200]}»"
                           f" — test: {i.acceptance_test}" for i in lotto)
        try:
            r = client.chat_json([{"role": "system", "content": sistema},
                                  {"role": "user", "content": f"# I rilievi\n{elenco}\n\n"
                                                             f"# Il testo rivisto\n{corpo}"}],
                                 schema=_VERIFY_SCHEMA, model=model)
        except Exception as exc:  # noqa: BLE001
            errori.append(f"verifica: {str(exc)[:160]}")
            continue
        for v in (r.get("results") or []) if isinstance(r, dict) else []:
            rilievo = next((i for i in lotto if i.issue_id == v.get("issue_id")), None)
            if rilievo is None or v.get("status") not in STATUSES:
                continue
            stato = v["status"]
            prova = str(v.get("evidence") or "").strip()
            if stato == "RESOLVED" and not prova:
                stato = "PARTIALLY_RESOLVED"  # senza prova non è risolto
            rilievo.status, rilievo.resolution_evidence = stato, prova
    for i in issues:
        if i.status == "OPEN":
            i.status = "UNRESOLVED"
            i.resolution_evidence = i.resolution_evidence or "non verificato (la verifica non " \
                                                             "ha risposto per questo rilievo)"
    return errori


def cross_check(issues: list[Issue], dopo: tl.Timeline) -> list[tl.Issue]:
    """Il registro degli eventi del testo rivisto contro i rilievi: un problema del registro che
    c'è ancora non è risolto (qualunque cosa dica il verificatore); uno nuovo è una regressione.
    Ritorna i problemi nuovi."""
    nuovi = []
    for p in dopo.serious():
        simile = next((i for i in issues if i.origin.startswith("registro")
                       and _simile(i.diagnosis, p.problem, 0.35)), None)
        if simile is not None:
            if simile.status == "RESOLVED":
                simile.status = "UNRESOLVED"
                simile.resolution_evidence = ("il registro degli eventi del testo rivisto lo "
                                              f"trova ancora: {p.problem}")
        else:
            nuovi.append(p)
    return nuovi


def check_preserved(client: Any, preserve: list[str], text: str, *, model: str | None,
                    language: str) -> list[str]:
    if not preserve:
        return []
    sistema = ("Per ogni elemento da preservare, di' se è ancora nel testo rivisto ('kept') e "
               f"cita il passo ('evidence'). Lingua: {language}.")
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": "# Da preservare\n" + "\n".join(
                                  f"- {p}" for p in preserve) + f"\n\n# Il testo rivisto\n{text}"}],
                             schema=_PRESERVE_SCHEMA, model=model)
    except Exception:  # noqa: BLE001
        return [f"{p}: non verificato" for p in preserve]
    fuori = []
    for v in (r.get("items") or []) if isinstance(r, dict) else []:
        if isinstance(v, dict) and v.get("item"):
            fuori.append(f"{'✓' if v.get('kept') else '✗ PERSO'} {v['item']}"
                         + (f" — «{str(v.get('evidence') or '')[:120]}»" if v.get("evidence")
                            else ""))
    return fuori


def gates(run: RevisionRun, dopo: tl.Timeline, nuovi: list[tl.Issue], narrativo: bool) -> list[Gate]:
    """I nove gate, pass/fail, sul testo rivisto."""
    aperti = [i for i in run.issues if i.status in ("UNRESOLVED", "REGRESSION",
                                                    "PARTIALLY_RESOLVED")]

    def gate(chiave: str, categorie: set[str], extra: list[str] | None = None,
             applicabile: bool = True) -> Gate:
        if not applicabile:
            return Gate(chiave, GATES[chiave], None, ["non si applica a questo tipo di libro"])
        problemi = [f"{i.issue_id} {i.status}: {i.diagnosis[:120]}" for i in aperti
                    if i.category in categorie and i.severity in ("Blocker", "Major")]
        problemi += extra or []
        return Gate(chiave, GATES[chiave], not problemi, problemi)

    nuovi_per = {k: [f"nuovo: {p.problem[:120]}" for p in nuovi
                     if _TIMELINE_KIND.get(p.kind, "CONT") in cat]
                 for k, cat in (("A", {"TIME", "PLACE"}), ("B", {"CONT", "CANON"}),
                                ("C", {"OBJ"}), ("D", {"KNOW"}))}
    g = [gate("A", {"TIME", "PLACE"}, nuovi_per["A"], narrativo),
         gate("B", {"CONT", "CANON", "CAUSE"}, nuovi_per["B"], narrativo),
         gate("C", {"OBJ"}, nuovi_per["C"], narrativo),
         gate("D", {"KNOW"}, nuovi_per["D"], narrativo),
         gate("E", {"SCENE"}, applicabile=narrativo),
         gate("F", {"REP"})]
    if run.budget:
        basso, alto = int(run.budget * (1 - LENGTH_BAND)), int(run.budget * (1 + LENGTH_BAND))
        dentro = basso <= run.words_after <= alto
        g.append(Gate("G", GATES["G"], dentro,
                      [] if dentro else [f"{run.words_after} parole, fascia {basso}–{alto} "
                                         f"(prima {run.words_before})"]))
    else:
        g.append(Gate("G", GATES["G"], None, ["nessuna misura stabilita"]))
    fonti = [f"{i.issue_id}: {i.diagnosis[:120]}" for i in run.issues
             if i.category == "SRC" and i.status in ("UNRESOLVED", "REGRESSION")]
    g.append(Gate("H", GATES["H"], not fonti or None, fonti))
    persi = [p for p in run.preserve_check if p.startswith("✗")]
    protetti = persi + [f"{i.issue_id}: {i.diagnosis[:120]}" for i in run.issues
                        if i.status == "REGRESSION"]
    g.append(Gate("I", GATES["I"], not protetti, protetti))
    return g


def verdict(run: RevisionRun) -> tuple[str, list[str]]:
    """Il verdetto e il prossimo intervento minimo."""
    aperti = [i for i in run.issues if i.status in ("UNRESOLVED", "REGRESSION",
                                                    "PARTIALLY_RESOLVED")]
    regressioni = [i for i in run.issues if i.status == "REGRESSION"]
    bloccanti = [i for i in aperti if i.severity == "Blocker"]
    falliti = [g for g in run.gates if g.passed is False]
    prossimi = [f"{i.issue_id} ({i.severity}, {i.status}): {i.proposed_action or i.diagnosis}"
                for i in sorted(aperti, key=lambda x: SEVERITIES.index(x.severity))[:10]]
    if regressioni and any(i.severity in ("Blocker", "Major") for i in regressioni) or \
            any(g.key == "I" for g in falliti):
        return "FAIL", prossimi
    if run.author_questions and (bloccanti or falliti):
        return "AUTHOR INPUT REQUIRED", prossimi
    if bloccanti or falliti:
        return "REVISE", prossimi
    if aperti:
        return "PASS WITH WARNINGS", prossimi
    return "PASS", prossimi


# --------------------------------------------------------------------------- #
#  il report e i file
# --------------------------------------------------------------------------- #
def run_dir(project, run_id: str) -> Path:
    return project.root / "revisioni" / run_id


def diff_text(prima: str, dopo: str) -> str:
    righe = difflib.unified_diff(prima.splitlines(), dopo.splitlines(), "baseline", "rivisto",
                                 lineterm="", n=1)
    return "\n".join(righe)


def _rubric_table(prima: dict, dopo: dict) -> list[str]:
    if not prima:
        return ["—"]
    pv = ql.evaluation_from_dict(prima)
    dv = ql.evaluation_from_dict(dopo) if dopo else None
    righe = ["| criterio | peso | prima | dopo |", "|---|---:|---:|---:|"]
    for s in pv.scores:
        if not s.applicable and s.score is None:
            continue
        d = dv.score(s.key) if dv else None
        righe.append(f"| {s.label}{' (critico)' if s.critical else ''} | {s.weight:g} | "
                     f"{'—' if s.score is None else s.score} | "
                     f"{'—' if d is None or d.score is None else d.score} |")
    righe.append(f"| **globale** | | **{pv.global_score}** | "
                 f"**{dv.global_score if dv else '—'}** |")
    return righe


def report_markdown(run: RevisionRun) -> str:
    """Il report in dodici sezioni (specifica §8)."""
    r = run
    e = lambda x: (x or "").replace("|", "/")  # noqa: E731
    out = [f"# Revisione — Capitolo {r.chapter}: {r.title}", "",
           f"**Verdetto: {r.verdict}**", "",
           "## 1. Identità dell'esecuzione", "",
           f"- run: `{r.run_id}` · {r.created}",
           f"- versione in ingresso: `{r.input_version}` · in uscita: `{r.output_version}`",
           f"- modello che scrive: {r.model or '—'} · modello dei controlli: "
           f"{r.check_model or r.model or '—'}",
           f"- tipo di libro: {ql.KINDS.get(r.kind, r.kind)}",
           f"- testo rivisto: {r.output_path or '—'}"
           + (" (messo nel capitolo; la versione di prima è fra le versioni)"
              if r.applied_to_chapter else ""), "",
           "### Fonti canoniche dell'app consultate", ""]
    out += [f"- {s['name']}: {s['status']}" + (f" — `{Path(s['path']).name}`" if s.get("path")
                                               else "") + (f" · versione `{s['version']}`"
                                                           if s.get("version") else "")
            for s in r.sources_consulted] or ["- —"]
    out += ["", "## 2. Baseline", "",
            f"- parole: {r.words_before}" + (f" (obiettivo {r.budget}, fascia "
                                             f"{int(r.budget * 0.9)}–{int(r.budget * 1.1)})"
                                             if r.budget else ""),
            "- parole per scena: " + "; ".join(f"{h} {n}" for h, n in r.words_per_section_before),
            "", "### Il registro degli eventi", "", r.timeline_before or "—", "",
            "### Gli oggetti e i documenti", ""]
    out += [f"- {o}" for o in r.objects_before] or ["- —"]
    out += ["", "## 3. Verdetto iniziale", ""]
    conta = {s: sum(1 for i in r.issues if i.severity == s and i.origin != "regressione")
             for s in SEVERITIES}
    out.append("- rilievi: " + ", ".join(f"{n} {s}" for s, n in conta.items()))
    out += [""] + _rubric_table(r.rubric_before, {}) + ["", "### I rilievi", "",
                                                         "| ID | gravità | dove | rilievo | base |",
                                                         "|---|---|---|---|---|"]
    out += [f"| {i.issue_id} | {i.severity} | {e(i.location)} | {e(i.diagnosis[:160])} | "
            f"{i.basis} |" for i in r.issues if i.origin != "regressione"]
    if r.methodology:
        out += ["", f"Metodologia: `story/metodologia/{r.methodology}.json`"]
    out += ["", "## 4. Piano d'intervento", ""]
    if r.canonical:
        out += [r.canonical, ""]
    for c in r.changes:
        out.append(f"- **{c.change_id}** [{c.block}] {c.operation} → scene "
                   f"{', '.join(str(t + 1) for t in c.targets) or '—'}"
                   + (f" §{c.first_paragraph + 1}-{c.last_paragraph + 1}"
                      if c.first_paragraph >= 0 else "")
                   + f" · rilievi {', '.join(c.related_issue_ids) or '—'} · {c.intended_effect}")
    out += ["", "## 5. Registro delle modifiche", ""]
    out += [f"- {c.change_id}: {c.before_after_summary or 'non eseguito'}"
            + (f" — {c.verification_result}" if c.verification_result else "")
            for c in r.changes] or ["- —"]
    out += ["", "## 6. Elementi preservati", ""]
    out += [f"- {p}" for p in r.preserve_check or r.preserve] or ["- —"]
    out += ["", "## 7. Verifica post-revisione", "", "| ID | stato | prova |", "|---|---|---|"]
    out += [f"| {i.issue_id} | {i.status} | {e(i.resolution_evidence[:200])} |"
            for i in r.issues if i.origin != "regressione"]
    out += ["", "### I gate", ""]
    for g in r.gates:
        esito = "PASS" if g.passed else "FAIL" if g.passed is False else "n/d"
        out.append(f"- **{g.key} · {g.name}: {esito}**"
                   + ("".join(f"\n  - {d}" for d in g.details) if g.details else ""))
    out += ["", "## 8. Regressioni", ""]
    out += [f"- {i.issue_id} [{i.severity}] {i.location}: {i.diagnosis}"
            for i in r.issues if i.status == "REGRESSION"] or ["- nessuna trovata"]
    out += ["", "## 9. Questioni aperte e decisioni dell'autore", ""]
    out += [f"- {q}" for q in r.author_questions] or ["- nessuna"]
    out += [f"- UNVERIFIED: {s['name']}" for s in r.sources_consulted
            if s.get("status", "").startswith("UNVERIFIED")]
    out += ["", "## 10. Confronto prima/dopo", "",
            f"- parole: {r.words_before} → {r.words_after}"
            + (f" ({(r.words_after - r.words_before) / max(1, r.words_before):+.0%})"),
            "- parole per scena dopo: " + "; ".join(f"{h} {n}" for h, n in
                                                    r.words_per_section_after), ""]
    out += _rubric_table(r.rubric_before, r.rubric_after)
    out += ["", "### Il registro degli eventi dopo", "", r.timeline_after or "—", "",
            "### Gli oggetti dopo", ""]
    out += [f"- {o}" for o in r.objects_after] or ["- —"]
    out += ["", "## 11. Verdetto finale", "", f"**{r.verdict}**", "",
            "## 12. Prossimo intervento minimo", ""]
    out += [f"- {p}" for p in r.next_steps] or ["- nessuno"]
    if r.errors:
        out += ["", "## Passaggi non riusciti", ""] + [f"- {x}" for x in r.errors]
    return "\n".join(out).rstrip() + "\n"


def save_run(project, run: RevisionRun, prima: str, dopo: str) -> Path:
    cartella = run_dir(project, run.run_id)
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "baseline.md").write_text(prima, encoding="utf-8")
    (cartella / "rivisto.md").write_text(dopo, encoding="utf-8")
    (cartella / "diff.md").write_text("```diff\n" + diff_text(prima, dopo) + "\n```\n",
                                      encoding="utf-8")
    (cartella / "run.json").write_text(json.dumps(asdict(run), ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    (cartella / "report.md").write_text(report_markdown(run), encoding="utf-8")
    return cartella


# --------------------------------------------------------------------------- #
#  tutto insieme
# --------------------------------------------------------------------------- #
def revise(client: Any, text: str, *, project, chapter: int, title: str, kind: str, pack: Any,
           piano: Any, budget: int, language: str, explain_language: str,
           notebook: dict | None, canon: str, model: str | None, check_model: str | None,
           style: str = "", protagonist: str = "", baseline_words: dict | None = None,
           should_stop=None, note=None, state: Any = None) -> tuple[str, RevisionRun]:
    """Un giro di revisione completo. Ritorna il testo rivisto e la sua esecuzione (già su disco,
    in ``revisioni/<run>/``). Non tocca il capitolo: lo decide chi chiama."""
    from bookwriter.forge import editor as ed

    narrativo = kind in ql.NARRATIVE_KINDS
    adesso = datetime.now()  # noqa: DTZ005 - per il nome della cartella
    run = RevisionRun(run_id=f"ch{chapter:02d}_{adesso.strftime('%Y%m%d-%H%M%S')}",
                      chapter=chapter, title=title, kind=kind, input_version=_version(text),
                      model=model or "", check_model=check_model or "", created=_now(),
                      budget=budget, words_before=mx.count_words(text))
    run.sources_consulted = sources_consulted(project, chapter)
    cap = ed.parse(text)
    run.words_per_section_before = [(ed._short(s.heading), s.words) for s in cap.sections]
    # 0b. la metodologia di questo testo: quali entità contare, con quali invarianti
    metodo = None
    if narrativo and state is not None:
        from bookwriter.forge import methodology as md

        if note:
            note("Metodologia: che cosa va controllato in questo capitolo…")
        try:
            metodo = md.generate(client, text, scope="testo", chapter=chapter,
                                 work=md.work_profile(state),
                                 project=md.project_profile(project, state, client,
                                                            model=check_model),
                                 objective="rivedere il capitolo correggendo prima i conti, la "
                                           "cronologia e la continuità, poi la prosa",
                                 canon=canon, model=check_model)
            md.save(project, metodo)
            run.methodology = f"{chapter:02d}_testo_v{metodo.version}"
        except Exception as exc:  # noqa: BLE001
            run.errors.append(f"metodologia: {str(exc)[:160]}")
    # 1. diagnosi (e la situazione di partenza che ne viene)
    rilievi, cronologia, registro, rubrica, errori = diagnose(
        client, text, chapter=chapter, kind=kind, pack=pack, piano=piano, budget=budget,
        language=language, explain_language=explain_language, notebook=notebook, model=model,
        check_model=check_model, protagonist=protagonist, baseline=baseline_words, note=note,
        metodo=metodo)
    run.issues, run.errors = rilievi, list(errori)
    run.timeline_before = cronologia.markdown() if cronologia.records else ""
    run.objects_before = [o.line() for o in registro]
    run.rubric_before = asdict(rubrica)
    if should_stop and should_stop():
        run.verdict = "REVISE"
        run.output_path = ""
        save_run(project, run, text, text)
        return text, run
    # 2. piano
    if note:
        note("Piano degli interventi…")
    forti = [f"{s.label}: «{s.evidence}»" for s in rubrica.scores
             if s.applicable and s.score >= 7 and s.evidence][:8]
    interventi, preservare, domande, err = plan(
        client, rilievi, [(s.heading, s.text) for s in cap.sections], budget=budget,
        strengths=forti, canon=canon, model=check_model, language=explain_language)
    run.changes, run.preserve, run.author_questions = interventi, preservare, domande
    run.errors += err
    # prima i dati: i valori canonici dei registri che non tornano
    canonici, domande_conti = canonical_values(client, rilievi, metodo, canon,
                                               model=check_model, language=explain_language)
    run.canonical = canonici
    run.author_questions += domande_conti
    if metodo is not None:
        canon = "\n\n".join(p for p in (canon, metodo.canon_block(), canonici) if p)
    # 3. intervento, per blocchi; dopo i blocchi strutturali si ricontrolla la cronologia
    start = tl._start_lines(pack)

    def controllo(testo: str):
        if note:
            note("Ricontrollo la cronologia dopo gli interventi strutturali…")
        intermedio = tl.build(client, [(s.heading, s.text) for s in ed.parse(testo).sections],
                              pack, None, model=check_model)
        for p in intermedio.serious()[:6]:
            run.errors.append(f"dopo i blocchi strutturali resta: {p.problem[:160]}")
        return intermedio.records, object_registry(intermedio.records)

    rivisto = execute(client, text, interventi, rilievi, records=cronologia.records,
                      registro=registro, start=start, canon=canon, preserve=preservare,
                      language=language, style=style, kind=kind, model=model,
                      should_stop=should_stop, note=note,
                      checkpoint=controllo if narrativo else None, budget=budget)
    # 4. verifica indipendente
    if note:
        note("Verifica indipendente, rilievo per rilievo…")
    run.errors += verify_issues(client, rilievi, rivisto, model=check_model,
                                language=explain_language)
    dopo_cron = tl.Timeline()
    nuovi: list[tl.Issue] = []
    if narrativo:
        dopo_cron = tl.build(client, [(s.heading, s.text) for s in ed.parse(rivisto).sections],
                             pack, None, model=check_model)
        nuovi = cross_check(rilievi, dopo_cron)
        ids = _Ids(chapter)
        ids.contatori = {cat: sum(1 for i in rilievi if i.category == cat)
                         for cat in {i.category for i in rilievi}}
        for p in nuovi:
            categoria = _TIMELINE_KIND.get(p.kind, "CONT")
            run.issues.append(Issue(
                issue_id=ids.next(categoria), chapter=chapter, source_version=_version(rivisto),
                category=categoria, severity=_DA_TIMELINE.get(p.severity, "Moderate"),
                location=_location([(s.heading, s.text) for s in ed.parse(rivisto).sections],
                                   p.section),
                section=p.section, evidence=p.quote, diagnosis=p.problem,
                reader_impact=_IMPACT.get(categoria, ""), proposed_action=p.fix,
                acceptance_test=_TEST.get(categoria, ""), status="REGRESSION",
                origin="regressione"))
    run.preserve_check = check_preserved(client, preservare, rivisto, model=check_model,
                                         language=explain_language)
    dopo_rubrica = ql.evaluate_text(
        client, rivisto, chapter=chapter, kind=kind, notebook=notebook, budget=budget,
        language=language, explain_language=explain_language,
        previous="\n".join(start), baseline=baseline_words, protagonist=protagonist,
        model=check_model)
    run.rubric_after = asdict(dopo_rubrica)
    run.words_after = mx.count_words(rivisto)
    run.words_per_section_after = [(ed._short(s.heading), s.words)
                                   for s in ed.parse(rivisto).sections]
    run.timeline_after = dopo_cron.markdown() if dopo_cron.records else ""
    run.objects_after = [o.line() for o in object_registry(dopo_cron.records)]
    for c in run.changes:
        stati = [run.issue(x).status for x in c.related_issue_ids if run.issue(x)]
        c.verification_result = ", ".join(f"{x} {s}" for x, s in zip(c.related_issue_ids, stati))
    run.gates = gates(run, dopo_cron, nuovi, narrativo)
    run.verdict, run.next_steps = verdict(run)
    run.output_version = _version(rivisto)
    save_run(project, run, text, rivisto)
    return rivisto, run


def load_run(project, run_id: str) -> RevisionRun | None:
    path = run_dir(project, run_id) / "run.json"
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run = RevisionRun(**{k: v for k, v in d.items() if k in RevisionRun.__dataclass_fields__
                         and k not in ("issues", "changes", "gates")})
    run.issues = [Issue(**i) for i in d.get("issues") or []]
    run.changes = [Change(**c) for c in d.get("changes") or []]
    run.gates = [Gate(**g) for g in d.get("gates") or []]
    return run


def latest_run(project, chapter: int) -> RevisionRun | None:
    cartella = project.root / "revisioni"
    if not cartella.exists():
        return None
    nomi = sorted(p.name for p in cartella.glob(f"ch{chapter:02d}_*") if p.is_dir())
    return load_run(project, nomi[-1]) if nomi else None
