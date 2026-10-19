"""Fase «Revisione»: il materiale trovato dalla ricerca, discusso con l'autore un punto alla volta.

La ricerca lavora per il libro: cerca materiale per scrivere meglio e fonti a sostegno di ciò che
l'autore afferma. La revisione lo mette davanti all'autore, **un punto alla volta**, in
quest'ordine: prima il **materiale trovato** da integrare («ho trovato questo, così può entrare
nel capitolo X»; la scelta di serie è che entra), poi le **idee** per espandere la tesi e il
racconto, poi le scelte sul campo e le incoerenze interne, e per ultimi i **contrasti con fatti
comprovati**, che si mostrano e si discutono ma non si impongono mai. Un libro può andare in
direzione opposta a quella che le fonti più comuni indicano, se l'autore lo vuole.

Quello che l'autore ha scritto — tesi, fatti, scene, personaggi — **resta nel libro**, sempre,
anche senza fonti e anche contro le fonti: nessun percorso automatico lo toglie, lo attenua o lo
tralascia. «Tralascia» c'è solo per una decisione esplicita dell'autore su quel punto. Gli
elementi d'invenzione non si verificano mai. L'unica regola che non si piega: **mai fonti, cifre
o citazioni inventate**.

Tutto sta in ``research/review.json`` (i punti, le conversazioni, le decisioni). Le questioni
aperte della ricerca (:class:`~bookwriter.forge.research_store.Question`: contrasti con fatti
comprovati) sono punti di tipo ``non_documentato`` e **restano oggetti loro**: la decisione su di
esse si scrive dove si è sempre scritta (``Question.outcome``). Le note che l'autore sceglie di
tenere (prove contrarie, formulazioni scartate) vanno in ``research/review_notes.md``, un file
che la stesura **non** legge.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from bookwriter.config import ResearchSettings, language_label
from bookwriter.forge import notebook as nb
from bookwriter.forge import research as rs
from bookwriter.forge import research_store as store
from bookwriter.forge.project import BookProject, ForgeState
from bookwriter.forge.spec import chapter_points
from bookwriter.llm.client import quick as _quick
from bookwriter.forge.websearch import Page, SearchError, SearchResult, gather, norm_url

# --------------------------------------------------------------------------- #
#  vocabolario
# --------------------------------------------------------------------------- #
MATERIALE, IDEA = "materiale", "idea"
#: ``tensione`` = contrasto con un fatto che sembra comprovato (il nome è quello dei dati salvati).
TENSIONE, INCOERENZA, DEBOLEZZA = "tensione", "incoerenza", "debolezza"
#: ``non_documentato`` = una questione della ricerca, cioè un contrasto segnalato.
NON_DOCUMENTATO, SCELTA_CAMPO, LIBERO = "non_documentato", "scelta_campo", "libero"
#: I tipi che genera il modello; gli altri li costruisce il codice.
MODEL_KINDS = (IDEA, INCOERENZA, DEBOLEZZA, TENSIONE)
KINDS = (MATERIALE, IDEA, TENSIONE, INCOERENZA, DEBOLEZZA, NON_DOCUMENTATO, SCELTA_CAMPO, LIBERO)
#: Ordine di presentazione: prima il materiale da integrare, poi le idee per espandere, poi il
#: resto, e per ultimi i contrasti con fatti comprovati (il controllo di correttezza è terziario).
KIND_ORDER = {MATERIALE: 0, IDEA: 1, SCELTA_CAMPO: 2, INCOERENZA: 3, DEBOLEZZA: 4, TENSIONE: 5,
              NON_DOCUMENTATO: 6, LIBERO: 7}
#: I tipi su cui basta «entra» (o «lo lascio fuori»): la formulazione è facoltativa.
KINDS_WITHOUT_TEXT = (MATERIALE, IDEA)
AREAS = ("narrativa", "argomentativa", "fonti", "struttura")

# stati di un punto
APERTO, IN_DISCUSSIONE = "aperto", "in_discussione"
CONCORDATO, IMPOSTO, FONTE, TRALASCIATO = "concordato", "imposto", "fonte", "tralasciato"
RIMANDATO, OBSOLETO = "rimandato", "obsoleto"
#: Stati in cui un punto non ha più bisogno dell'autore.
CHIUSI = (CONCORDATO, IMPOSTO, FONTE, TRALASCIATO)

# esiti di una decisione (quello che l'autore conferma)
D_CONCORDATA, D_IMPOSTA, D_FONTE, D_TRALASCIA = "concordata", "imposta", "fonte", "tralascia"
DECISION_KINDS = (D_CONCORDATA, D_IMPOSTA, D_FONTE, D_TRALASCIA)
_STATUS_OF = {D_CONCORDATA: CONCORDATO, D_IMPOSTA: IMPOSTO, D_FONTE: FONTE, D_TRALASCIA: TRALASCIATO}

#: Quanta conversazione si rimanda al modello, in caratteri.
HISTORY_CHARS = 40_000

#: Fallimenti di fila delle passate del modello dopo i quali la generazione si ferma.
CONSECUTIVE_FAILURES = rs.CONSECUTIVE_FAILURES

NOTES_FILE = "review_notes.md"


class ReviewFailed(RuntimeError):
    """La generazione dei punti non è riuscita, e l'autore legge perché."""


# --------------------------------------------------------------------------- #
#  dati
# --------------------------------------------------------------------------- #
@dataclass
class Evidence:
    """Una prova citata da un punto: sempre letterale dai dati, mai riscritta dal modello."""

    ref: str = ""  # finding | page | notebook | spec | landscape | question
    id: str = ""
    quote: str = ""
    source: str = ""
    url: str = ""
    level: str = ""


@dataclass
class Decision:
    kind: str = ""  # D_*
    text: str = ""  # la formulazione esatta che entra nel libro
    note: str = ""  # prove contrarie, formulazioni scartate: vanno negli appunti se l'autore vuole
    keep_note: bool = False
    source: str = ""  # per D_FONTE
    url: str = ""
    structure_note: str = ""  # «questa scelta tocca il capitolo N: rivedi la specifica»
    by_author: bool = False  # «Decido io…»: scritta a mano, senza passare dal modello
    at: str = ""


@dataclass
class ReviewPoint:
    id: str = ""
    kind: str = TENSIONE
    area: str = ""
    chapter: int = 0  # 0 = riguarda tutto il libro
    point: int = -1
    title: str = ""
    statement: str = ""  # cosa dice il libro / l'autore
    evidence: list[Evidence] = field(default_factory=list)
    stakes: str = ""
    options: list[str] = field(default_factory=list)
    severity: int = 2
    status: str = APERTO
    thread: list[dict[str, Any]] = field(default_factory=list)
    proposal: dict[str, Any] = field(default_factory=dict)
    decision: Decision | None = None
    #: per ``non_documentato``: l'id della questione di ricerca (la fonte di verità della decisione).
    ref: str = ""
    anchor: str = ""
    #: quanti messaggi della conversazione sono già stati letti per proporre una decisione.
    proposed_upto: int = 0

    @property
    def closed(self) -> bool:
        return self.status in CHIUSI


@dataclass
class ReviewData:
    date: str = ""
    points: list[ReviewPoint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: la chat libera di fine revisione.
    free_thread: list[dict[str, Any]] = field(default_factory=list)
    free_proposal: dict[str, Any] = field(default_factory=dict)
    free_upto: int = 0

    def point(self, ident: str) -> ReviewPoint | None:
        return next((p for p in self.points if p.id == ident), None)

    def pending(self) -> list[ReviewPoint]:
        return [p for p in self.points if p.status in (APERTO, IN_DISCUSSIONE, RIMANDATO)]


def review_path(project: BookProject) -> Path:
    return project.research_dir / "review.json"


def notes_path(project: BookProject) -> Path:
    return project.research_dir / NOTES_FILE


def _point_from_dict(d: dict[str, Any]) -> ReviewPoint:
    campi = set(ReviewPoint.__dataclass_fields__)
    p = ReviewPoint(**{k: v for k, v in d.items()
                       if k in campi and k not in ("evidence", "decision")})
    ev = set(Evidence.__dataclass_fields__)
    p.evidence = [Evidence(**{k: v for k, v in e.items() if k in ev})
                  for e in d.get("evidence") or [] if isinstance(e, dict)]
    dec = d.get("decision")
    if isinstance(dec, dict) and dec.get("kind"):
        dc = set(Decision.__dataclass_fields__)
        p.decision = Decision(**{k: v for k, v in dec.items() if k in dc})
    return p


def load(project: BookProject) -> ReviewData | None:
    path = review_path(project)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    dati = ReviewData(date=str(raw.get("date") or ""),
                      warnings=[str(w) for w in raw.get("warnings") or []],
                      free_thread=[b for b in raw.get("free_thread") or [] if isinstance(b, dict)],
                      free_proposal=raw.get("free_proposal") or {},
                      free_upto=int(raw.get("free_upto") or 0))
    dati.points = [_point_from_dict(p) for p in raw.get("points") or [] if isinstance(p, dict)]
    return dati


def save(project: BookProject, dati: ReviewData) -> Path:
    project.research_dir.mkdir(parents=True, exist_ok=True)
    path = review_path(project)
    path.write_text(json.dumps(asdict(dati), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
#  utilità
# --------------------------------------------------------------------------- #
def _norm(testo: str) -> str:
    return " ".join((testo or "").lower().split())


def _anchor(kind: str, title: str) -> str:
    return hashlib.sha1(f"{kind}|{_norm(title)}".encode()).hexdigest()[:12]


def _words(testo: str) -> set[str]:
    return {w for w in re.findall(r"\w{4,}", (testo or "").lower())}


def _cut(testo: str, n: int) -> str:
    testo = " ".join((testo or "").split())
    return testo if len(testo) <= n else testo[:n - 1].rstrip() + "…"


def sort_points(punti: list[ReviewPoint]) -> list[ReviewPoint]:
    """Ordine di presentazione: tipo, poi capitolo e punto, poi gravità (solo per i pari)."""
    return sorted(punti, key=lambda p: (KIND_ORDER.get(p.kind, 9), p.chapter or 10**6,
                                        p.point, -p.severity, p.id))


def _renumber(punti: list[ReviewPoint]) -> None:
    for i, p in enumerate(punti, 1):
        if not p.id or not re.fullmatch(r"r\d{3,}", p.id):
            p.id = f"r{i:03d}"


def _next_id(punti: list[ReviewPoint]) -> str:
    massimo = max((int(p.id[1:]) for p in punti if re.fullmatch(r"r\d{3,}", p.id)), default=0)
    return f"r{massimo + 1:03d}"


# --------------------------------------------------------------------------- #
#  le prove: un catalogo con identificativi, da cui il modello può solo citare
# --------------------------------------------------------------------------- #
@dataclass
class Catalog:
    entries: dict[str, Evidence] = field(default_factory=dict)

    def add(self, ev: Evidence) -> None:
        self.entries[ev.id] = ev

    def line(self, ev: Evidence) -> str:
        livello = f" (livello {ev.level})" if ev.level else ""
        fonte = f" — fonte: {ev.source}" if ev.source else ""
        return f"[{ev.id}]{livello} {ev.quote}{fonte}"

    def text(self) -> str:
        return "\n".join(self.line(e) for e in self.entries.values())

    def resolve(self, ids: list[str]) -> list[Evidence] | None:
        """Le prove con questi id, o ``None`` se anche uno solo non esiste (punto da scartare)."""
        trovate = []
        for i in ids:
            ev = self.entries.get(str(i).strip().strip("[]"))
            if ev is None:
                return None
            trovate.append(ev)
        return trovate


def _notebook_entries(state: ForgeState) -> list[Evidence]:
    quaderno = nb.normalise(state.notebook)
    voci = []
    for chiave in ("premise", "author_goal", "messages", "plot", "events", "causality",
                   "psychology", "key_scenes", "ending", "setting"):
        valore = quaderno.get(chiave)
        testo = "; ".join(valore) if isinstance(valore, list) else str(valore or "")
        if testo.strip():
            voci.append(Evidence(ref="notebook", id=f"n:{chiave}", quote=_cut(testo, 700),
                                 source=nb.LABEL.get(chiave, chiave)))
    return voci


def _chapter_entries(data: store.ChapterData | None) -> list[Evidence]:
    if data is None:
        return []
    voci = []
    for punto in data.points:
        for j, f in enumerate(punto.findings, 1):
            if f.excluded:
                continue
            uso = f" — come può servire: {f.use}" if f.use else ""
            voci.append(Evidence(
                ref="finding", id=f"c{data.number}p{punto.index}f{j}",
                quote=_cut(f"{f.claim}: {f.detail}{uso}", 450), source=f.source, url=f.url,
                level=(f.level or "").strip().upper()[:1]))
        for k, pg in enumerate(punto.pages, 1):
            voci.append(Evidence(
                ref="page", id=f"c{data.number}p{punto.index}u{k}",
                quote=_cut(f"{pg.title}: {pg.text}", 350), source=pg.title, url=pg.url))
    return voci


# --------------------------------------------------------------------------- #
#  generazione dei punti
# --------------------------------------------------------------------------- #
POINTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"},
                    "area": {"type": "string"},
                    "title": {"type": "string"},
                    "statement": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "stakes": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "severity": {"type": "integer"},
                },
                "required": ["kind", "area", "title", "statement", "evidence_ids", "stakes",
                             "options", "severity"],
            },
        },
    },
    "required": ["points"],
}

GENERATE_RULES = (
    "Leggi il quaderno del libro e il materiale trovato dalla ricerca. Elenca i punti che "
    "l'autore dovrebbe poter vedere e decidere, in quest'ordine di importanza:\n"
    "(a) \"idea\": un'idea o un collegamento, ricavato dal materiale, per ESPANDERE la tesi o "
    "il racconto dell'autore (una scena che si può arricchire, un dettaglio che la rende più "
    "vera, un nesso fra due cose che il libro già dice). Mai per correggere.\n"
    "(b) \"incoerenza\": una contraddizione INTERNA al libro (fra capitoli, nella cronologia, "
    "nei nessi causali), non con la realtà.\n"
    "(c) \"debolezza\": un punto in cui il libro non regge SECONDO LE SUE STESSE premesse (un "
    "salto logico, un buco nella causalità), con l'area: narrativa, argomentativa, fonti o "
    "struttura.\n"
    "(d) \"tensione\": SOLO un contrasto con un fatto che sembra comprovato (una data, un luogo "
    "reale, un evento storico, un dato accertato), documentato da una prova che ti do, che "
    "produrrebbe un errore macroscopico per il lettore. È l'ultima cosa che conta.\n\n"
    "Non sei un revisore che difende la visione corrente, e non fai il controllo di "
    "correttezza. Una tesi originale, o in contrasto con le fonti più comuni, NON è un "
    "problema: non va segnalata. Personaggi, nomi, trama, ambientazione scelta, titoli dei "
    "capitoli, scelte di stile e tesi dell'autore sono suoi e non si verificano MAI; non "
    "segnalare che qualcosa «non è documentato» o «non compare nel materiale». Se il libro è di "
    "finzione, la verità del mondo inventato non si controlla.\n\n"
    "Per ogni punto: un titolo neutro; cosa dice il libro (citato dal quaderno o dalla "
    "specifica) o, per un'idea, in cosa consiste; gli identificativi delle prove, SOLO fra "
    "quelle che ti do (almeno uno); perché conta per il libro; da due a quattro strade "
    "possibili (fra queste sempre «tengo com'è» e «tralascio»); una gravità da 1 a 3. Non "
    "proporre un punto senza almeno un riferimento reale. Non inventare prove, fonti, cifre o "
    "citazioni. Non suggerire cosa l'autore «dovrebbe» pensare. Se non c'è niente da segnalare, "
    "elenco vuoto."
)


def _points_from_answer(risposta: Any, catalog: Catalog, *, chapter: int) -> tuple[
        list[ReviewPoint], int]:
    """I punti validi della risposta del modello, e quanti ne ha scartati.

    Un punto vale solo se ogni prova che cita esiste davvero nel catalogo: è la difesa, in
    codice e non in prompt, contro le prove inventate.
    """
    validi: list[ReviewPoint] = []
    scartati = 0
    for voce in (risposta.get("points") or []) if isinstance(risposta, dict) else []:
        if not isinstance(voce, dict):
            scartati += 1
            continue
        kind = str(voce.get("kind") or "").strip().lower()
        titolo = str(voce.get("title") or "").strip()
        dichiara = str(voce.get("statement") or "").strip()
        ids = [str(i) for i in voce.get("evidence_ids") or [] if str(i).strip()]
        prove = catalog.resolve(ids) if ids else None
        if kind not in MODEL_KINDS or not titolo or not dichiara or not prove:
            scartati += 1
            continue
        area = str(voce.get("area") or "").strip().lower()
        gravita = voce.get("severity")
        validi.append(ReviewPoint(
            kind=kind, area=area if area in AREAS else "", chapter=chapter, title=titolo,
            statement=dichiara, evidence=[Evidence(**asdict(e)) for e in prove],
            stakes=str(voce.get("stakes") or "").strip(),
            options=[str(o).strip() for o in voce.get("options") or [] if str(o).strip()][:4],
            severity=gravita if isinstance(gravita, int) and 1 <= gravita <= 3 else 2,
            anchor=_anchor(kind, titolo)))
    return validi, scartati


def _model_pass(client: Any, state: ForgeState, system: str, user: str, catalog: Catalog,
                *, chapter: int, model: str | None) -> tuple[list[ReviewPoint], int]:
    risposta = client.chat_json(
        [{"role": "system", "content": system + f"\nScrivi in "
          f"{language_label(state.language) or 'italiano'}."},
         {"role": "user", "content": user}],
        schema=POINTS_SCHEMA, model=model)
    return _points_from_answer(risposta, catalog, chapter=chapter)


def _chapter_user(state: ForgeState, chapter: dict[str, Any], data: store.ChapterData | None,
                  catalog: Catalog) -> str:
    punti = chapter_points(chapter)
    return "\n\n".join(p for p in (
        f"## Il libro (quaderno)\n{nb.brief(state)}",
        f"## Capitolo {chapter.get('number')}: {chapter.get('title')}\n"
        f"Cosa deve fare: {chapter.get('does')}\nPunti: " + "; ".join(punti),
        "## Prove che puoi citare (con identificativo)\n" + catalog.text(),
        ("## Contrasti segnalati dalla ricerca\n" + "\n".join(
            f"- {q.text}" for q in (data.questions if data else []) if not q.closed))
        if data and any(not q.closed for q in data.questions) else "",
    ) if p)


def material_points(project: BookProject, state: ForgeState) -> list[ReviewPoint]:
    """Il materiale trovato dalla ricerca come punti: «ho trovato questo, così può entrare».

    Nessun modello: sono i dati. Un punto per ogni punto del capitolo che ha materiale. La scelta
    di serie è che il materiale **entra** nel libro: è già nel file che legge la stesura, e
    decidere serve solo per dire come usarlo o per lasciarlo fuori.
    """
    punti: list[ReviewPoint] = []
    for c in state.outline:
        numero = int(c.get("number") or 0)
        dati = store.load_chapter(project.research_dir, numero, str(c.get("title") or ""))
        if dati is None:
            continue
        for punto in dati.points:
            voci = [e for e in _chapter_entries(
                store.ChapterData(number=numero, points=[punto])) if e.ref == "finding"]
            if not voci:
                continue
            righe = []
            for j, f in enumerate(punto.findings, 1):
                if f.excluded:
                    continue
                uso = f" — come può servire: {f.use}" if f.use else ""
                righe.append(f"- {f.claim}{uso}")
            punti.append(ReviewPoint(
                kind=MATERIALE, chapter=numero, point=punto.index,
                title=_cut(f"Materiale per il capitolo {numero}: {punto.text}", 120),
                statement="Ho trovato questo, e può entrare nel capitolo "
                          f"{numero} («{c.get('title')}», punto «{_cut(punto.text, 80)}»):\n"
                          + "\n".join(righe),
                evidence=voci,
                stakes="È già a disposizione di chi scrive il capitolo: entra da sé. Qui puoi "
                       "dire come usarlo, o lasciarlo fuori.",
                options=["Entra così com'è", "Entra, e dico come usarlo", "Lo lascio fuori"],
                anchor=_anchor(MATERIALE, f"{numero}|{punto.text}")))
    return punti


def non_documented_points(project: BookProject, state: ForgeState, *,
                          web_on: bool = True) -> list[ReviewPoint]:
    """Le questioni ancora aperte della ricerca (contrasti segnalati) come punti. Nessun modello.

    Nel libro restano com'è scritto finché l'autore non decide.
    """
    titoli = {int(c.get("number") or 0): str(c.get("title") or "") for c in state.outline}
    punti = []
    for q in store.pending_questions(project.research_dir, state.outline):
        if q.attempts:
            provato = f"{q.tried()} interrogazioni, {q.seen()} risultati visti"
        elif not web_on:
            provato = "ricerca sul web spenta nelle impostazioni: nessun approfondimento"
        else:
            provato = "non ancora approfondito (la ricerca si è fermata prima)"
        punti.append(ReviewPoint(
            kind=NON_DOCUMENTATO, chapter=q.chapter, point=q.point, ref=q.id,
            title=_cut(q.text, 120),
            statement=f"La ricerca ha segnalato questo per il capitolo {q.chapter} "
                      f"«{titoli.get(q.chapter, '')}»: {q.text} ({provato}). Nel libro resta "
                      "com'è scritto finché non decidi tu.",
            evidence=[Evidence(ref="question", id=q.id, quote=q.text)],
            stakes="Puoi tenerlo com'è, portare una fonte, o dire come scriverlo.",
            options=["Lo tengo com'è", "Ho una fonte: la porto", "Lo scrivo così…",
                     "Lo tralascio"],
            anchor=_anchor(NON_DOCUMENTATO, q.text)))
    return punti


def landscape_material_points(project: BookProject, state: ForgeState) -> list[ReviewPoint]:
    """Le opere simili del panorama come materiale da integrare (scelta di serie: entra).

    Nessun modello: sono i dati del panorama. Un punto per opera. Sostituiscono i vecchi punti
    «il campo di solito tratta X e il libro no», che dicevano cosa il libro dovesse fare.
    """
    dati = store.load_landscape(project.research_dir)
    if dati is None:
        return []
    chiave = "works" if dati.analysis.get("works") else "competitors"
    punti = []
    for i, c in enumerate(dati.analysis.get(chiave) or [], 1):
        if not isinstance(c, dict) or c.get("excluded") or not str(c.get("title", "")).strip():
            continue
        righe = store._work_lines(c)
        punti.append(ReviewPoint(
            kind=MATERIALE, chapter=0, title=_cut(f"Materiale da un'opera simile: {c['title']}", 120),
            statement="Ho trovato un'opera vicina al tuo libro da cui puoi attingere:\n"
                      + "\n".join(righe),
            evidence=[Evidence(ref="landscape", id=f"l:work{i}", quote=_cut("\n".join(righe), 450),
                               source="Opere simili da cui attingere", url=str(c.get("url", "")))],
            stakes="È già a disposizione di chi scrive: entra da sé. Qui puoi dire come usarlo, o "
                   "lasciarlo fuori.",
            options=["Entra così com'è", "Entra, e dico come usarlo", "Lo lascio fuori"],
            anchor=_anchor(MATERIALE, f"l|{c['title']}")))
    return punti


def _merge(vecchi: list[ReviewPoint], nuovi: list[ReviewPoint]) -> list[ReviewPoint]:
    """Rifare la generazione non butta il lavoro fatto.

    Un punto nuovo con la stessa ancora di uno vecchio **eredita** conversazione e decisione.
    I punti vecchi con una decisione che non si ritrovano restano, come ``obsoleto`` (visibili,
    mai cancellati); quelli vecchi senza decisione e senza conversazione si sostituiscono.
    """
    per_ancora = {p.anchor: p for p in vecchi if p.anchor}
    usati: set[str] = set()
    fusi: list[ReviewPoint] = []
    for n in nuovi:
        v = per_ancora.get(n.anchor)
        if v is not None:
            usati.add(v.id)
            n.id, n.status, n.thread, n.proposal = v.id, v.status, v.thread, v.proposal
            n.decision, n.proposed_upto = v.decision, v.proposed_upto
        fusi.append(n)
    for v in vecchi:
        if v.id in usati or v.kind == LIBERO:
            if v.kind == LIBERO:
                fusi.append(v)
            continue
        if v.decision is not None or v.thread:
            v.status = OBSOLETO if not v.closed else v.status
            fusi.append(v)
    _renumber_new(fusi)
    return fusi


def _renumber_new(punti: list[ReviewPoint]) -> None:
    """Dà un id ai punti che non ne hanno, senza toccare quelli che ce l'hanno già."""
    presenti = [p for p in punti if p.id]
    for p in punti:
        if not p.id:
            p.id = _next_id(presenti)
            presenti.append(p)


def generate(project: BookProject, state: ForgeState, client: Any, *, model: str | None = None,
             web_on: bool = True, progress: Callable[[int, int], None] | None = None,
             should_stop: Callable[[], bool] | None = None,
             note: Callable[[Any], None] | None = None) -> ReviewData:
    """Costruisce l'elenco dei punti e lo salva. Si può interrompere: quel che c'è resta."""
    project.scaffold()
    vecchio = load(project)
    nuovi: list[ReviewPoint] = []
    avvisi: list[str] = []
    capitoli = state.outline
    totale = len(capitoli) + 1
    fallimenti = 0
    ultimo_errore = ""
    arrestata = False

    def passata(i: int, system: str, user: str, catalog: Catalog, chapter: int,
                etichetta: str) -> None:
        nonlocal fallimenti, ultimo_errore
        try:
            validi, scartati = _model_pass(client, state, system, user, catalog,
                                           chapter=chapter, model=model)
        except Exception as exc:  # noqa: BLE001 - un guasto del modello non è un punto vuoto
            fallimenti += 1
            ultimo_errore = str(exc)
            avvisi.append(f"{etichetta}: il modello non ha saputo leggere ({exc})")
            if note:
                note(f"⚠ {avvisi[-1]}")
            return
        fallimenti = 0
        nuovi.extend(validi)
        if scartati:
            avvisi.append(f"{etichetta}: {scartati} osservazioni scartate perché senza prove "
                          "reali (non si propone un punto che non ha un riferimento).")

    notebook_ev = _notebook_entries(state)
    for i, capitolo in enumerate(capitoli):
        if should_stop and should_stop():
            break
        if progress:
            progress(i, totale)
        numero = int(capitolo.get("number") or 0)
        dati = store.load_chapter(project.research_dir, numero, str(capitolo.get("title") or ""))
        catalogo = Catalog()
        for ev in [*notebook_ev, *_chapter_entries(dati)]:
            catalogo.add(ev)
        passata(i, GENERATE_RULES, _chapter_user(state, capitolo, dati, catalogo), catalogo,
                numero, f"Capitolo {numero}")
        if fallimenti >= CONSECUTIVE_FAILURES:
            arrestata = True
            break
    else:
        if not (should_stop and should_stop()):
            if progress:
                progress(len(capitoli), totale)
            catalogo = Catalog()
            for ev in notebook_ev:
                catalogo.add(ev)
            struttura = []
            for capitolo in capitoli:
                numero = int(capitolo.get("number") or 0)
                ev = Evidence(ref="spec", id=f"s:{numero}", source="Specifica",
                              quote=_cut(f"Capitolo {numero} «{capitolo.get('title')}»: "
                                         f"{capitolo.get('does')}. Punti: "
                                         + "; ".join(chapter_points(capitolo)), 500))
                catalogo.add(ev)
                struttura.append(ev)
                dati = store.load_chapter(project.research_dir, numero,
                                          str(capitolo.get("title") or ""))
                for e in _chapter_entries(dati):
                    if e.ref == "finding":
                        catalogo.add(e)
            utente = "\n\n".join((
                f"## Il libro (quaderno)\n{nb.brief(state)}",
                "## Prove che puoi citare (con identificativo)\n" + catalogo.text(),
                "## Cosa cercare in questo passaggio\nSolo punti che riguardano il libro nel suo "
                "insieme: incoerenze fra capitoli, nella cronologia o nei nessi causali; cifre "
                "diverse per la stessa cosa; debolezze dell'impianto. Quelli di un solo capitolo "
                "sono già stati trattati."))
            passata(len(capitoli), GENERATE_RULES, utente, catalogo, 0, "Il libro nel suo insieme")

    if arrestata:
        avvisi.append(
            f"La generazione si è fermata: {CONSECUTIVE_FAILURES} passaggi di fila non sono "
            f"riusciti (ultimo errore: {ultimo_errore}). I punti già trovati sono salvati; "
            "controlla il server dei modelli e riprova.")

    # i tipi costruiti dal codice, poi i duplicati (stessa ancora) fuori
    nuovi.extend(material_points(project, state))
    nuovi.extend(non_documented_points(project, state, web_on=web_on))
    nuovi.extend(landscape_material_points(project, state))
    visti: set[str] = set()
    unici = []
    for p in nuovi:
        if p.anchor in visti:
            continue
        visti.add(p.anchor)
        unici.append(p)
    dati_rev = ReviewData(date=date.today().isoformat(), warnings=avvisi)
    if vecchio is not None:
        dati_rev.free_thread, dati_rev.free_proposal = vecchio.free_thread, vecchio.free_proposal
        dati_rev.free_upto = vecchio.free_upto
        unici = _merge(vecchio.points, unici)
    else:
        _renumber_new(unici)
    dati_rev.points = sort_points(unici)
    save(project, dati_rev)
    if arrestata:
        raise ReviewFailed(avvisi[-1])
    return dati_rev


# --------------------------------------------------------------------------- #
#  la discussione di un punto
# --------------------------------------------------------------------------- #
ROLE = (
    "Sei l'interlocutore editoriale di un autore. Il libro è suo, e le decisioni sono sue: tu non "
    "sei il suo correttore e non difendi nessuna visione ufficiale, comune o prevalente di "
    "nessun argomento. Il tuo compito è mettergli davanti, con onestà, ciò che sta in questo "
    "punto, ragionarne insieme a lui e aiutarlo a decidere cosa fare. Un libro può andare in "
    "direzione opposta a quella che le fonti più comuni indicano, se l'autore lo vuole: non è un "
    "errore da evitare.\n\n"
    "La ricerca lavora PER il libro: ha cercato materiale per scrivere meglio e fonti a sostegno di "
    "ciò che l'autore afferma. Il controllo di correttezza è l'ultima cosa che conta. Quello che "
    "l'autore ha scritto resta nel libro, anche senza fonti e anche contro le fonti; personaggi, "
    "nomi, trama, ambientazione, titoli e tesi sono suoi e non li verifichi mai.\n\n"
    "Il punto da discutere (titolo, cosa dice il libro, le prove con fonte e livello, cosa è in "
    "gioco, le strade possibili) ti è dato sotto, letterale: non riscriverlo e non aggiungervi "
    "prove tue. Se è materiale trovato o un'idea, presentali come cose che possono ENTRARE nel "
    "libro: cosa c'è, come può servire alla scena o al capitolo, e chiedi se entra e come; non "
    "li presentare come problemi.\n\n"
    "Regole:\n"
    "1. Riferisci cosa dicono le prove e che tipo di fonte sono (livello A-E: è un'etichetta, "
    "non un voto). Non usare «giusto» o «sbagliato» per le tesi dell'autore: se una fonte è in "
    "contrasto con ciò che scrive, dillo come «in contrasto con la fonte trovata», non come un "
    "errore. Il consenso non è la verità e il dissenso non è l'errore.\n"
    "2. Se non è chiaro, chiedi in che registro è l'affermazione: un fatto che il libro pretende "
    "vero nel mondo, una tesi dell'autore, o un elemento di finzione. In un libro di finzione la "
    "verifica è la coerenza interna e la plausibilità che l'autore sceglie di dichiarare.\n"
    "3. Mai inventare fonti, cifre, autori, anni o citazioni; mai fingere di aver letto ciò che "
    "non hai letto. Se non sai, dillo.\n"
    "4. Se l'autore chiede di verificare, riporta le prove a favore E quelle contro, con le "
    "fonti, senza scegliere tu il vincitore.\n"
    "5. Se le prove contrastano con ciò che l'autore vuole dire, diglielo UNA volta, con la "
    "fonte. Non ripeterlo. Se l'autore conferma la sua posizione, accettala: non insistere, non "
    "attenuarla, non proporre di cambiarla. Passa a come formularla.\n"
    "6. Non fare la morale e non aggiungere avvertenze sull'argomento. L'autore sa di cosa "
    "scrive.\n"
    "7. Quando proponi come scrivere il punto, dai almeno tre strade: una NETTA in voce d'autore "
    "(senza cautele né attribuzioni a tradizioni), una che attribuisce o dichiara l'incertezza, e "
    "«tralascia». Scrivi ogni formulazione per intero, pronta da usare. Non indicare quale sia "
    "«la migliore» a meno che l'autore lo chieda.\n"
    "8. Una domanda per volta, breve. È un colloquio, non una requisitoria.\n"
    "9. Non considerare niente deciso finché l'autore non l'ha detto in modo esplicito: una "
    "proposta tua non è una sua decisione. Quando pensi che il punto sia maturo, di' in una riga "
    "cosa registreresti e chiedigli di confermare.\n"
    "10. Non proporre di modificare la tesi per renderla «più difendibile» a meno che sia l'autore "
    "a chiederlo."
)

FREE_ROLE = (
    "Sei l'interlocutore editoriale di un autore, alla fine della revisione dei risultati della "
    "ricerca. Il libro è suo e le decisioni sono sue: non sei il suo correttore e non difendi "
    "nessuna visione ufficiale di nessun argomento. Ti do sotto le decisioni prese e quello che "
    "è rimasto in sospeso, con le perplessità che avresti scrivendo. Parli liberamente con "
    "l'autore, che può portare altri dubbi, chiedere di verificare qualcosa o decidere sui punti "
    "rimandati. Valgono le stesse regole di sempre: riferisci le prove con la loro forza senza "
    "dire «giusto» o «sbagliato» per le sue tesi, accetta la sua posizione se la conferma, mai "
    "fonti o cifre inventate, nessuna morale, una domanda per volta. Non considerare deciso "
    "niente finché non l'ha detto lui in modo esplicito."
)


def evidence_block(point: ReviewPoint) -> str:
    righe = []
    for e in point.evidence:
        livello = f" (livello {e.level})" if e.level else ""
        fonte = f" — fonte: {e.source}" if e.source else ""
        url = f" {e.url}" if e.url else ""
        righe.append(f"- [{e.id}]{livello} {e.quote}{fonte}{url}")
    return "\n".join(righe) or "(nessuna prova citata)"


def point_block(point: ReviewPoint) -> str:
    return "\n".join(p for p in (
        f"## Il punto: {point.title}",
        f"Tipo: {point.kind}" + (f" ({point.area})" if point.area else "")
        + (f" — capitolo {point.chapter}" if point.chapter else ""),
        f"Cosa dice il libro: {point.statement}",
        "Prove (letterali dai dati della ricerca):\n" + evidence_block(point),
        f"Cosa è in gioco: {point.stakes}" if point.stakes else "",
        ("Strade possibili:\n" + "\n".join(f"- {o}" for o in point.options))
        if point.options else "",
    ) if p)


def system_prompt(state: ForgeState, point: ReviewPoint, *, searched: str = "",
                  web_on: bool = True, notes: list[str] | None = None) -> str:
    lingua = language_label(state.language) or "italiano"
    parti = [ROLE, point_block(point), f"## Il libro (quaderno)\n{_cut(nb.brief(state), 6000)}"]
    if searched:
        parti.append("## Ricerca appena fatta su richiesta dell'autore\n" + searched)
    if notes:
        parti.append("## Cosa non si è potuto fare\n" + "\n".join(f"- {n}" for n in notes))
    if not web_on:
        parti.append("La ricerca sul web è spenta: se l'autore ne chiede una, digli che si può "
                     "accendere nelle Impostazioni; puoi usare il materiale del progetto e i "
                     "documenti che allega.")
    parti.append(f"Rispondi nella lingua in cui ti scrive l'autore; se non è chiaro, in {lingua}.")
    return "\n\n".join(parti)


def _alternate(thread: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    messaggi: list[dict[str, str]] = []
    for b in thread:
        if b.get("role") == "notice":  # gli avvisi dell'app (ricerche fatte…) non sono battute
            continue
        ruolo = "assistant" if b.get("role") == "assistant" else "user"
        testo = (b.get("text") or "").strip()
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


def opening(point: ReviewPoint) -> str:
    """La battuta con cui l'app presenta il punto: dati letterali, nessun modello."""
    righe = [f"**{point.title}**", "", point.statement, ""]
    if point.evidence:
        righe.append("Cosa dicono i dati:")
        for e in point.evidence:
            livello = f" (livello {e.level})" if e.level else ""
            righe.append(f"- {e.quote}{livello}" + (f" — {e.source}" if e.source else ""))
        righe.append("")
    if point.stakes:
        righe += [point.stakes, ""]
    if point.options:
        righe.append("Le strade che vedo:")
        righe += [f"- {o}" for o in point.options]
        righe.append("")
    righe.append("Cosa vuoi fare?")
    return "\n".join(righe)


def reply(state: ForgeState, client: Any, thread: list[dict[str, Any]], text: str | None,
          system: str, *, model: str | None = None,
          on_delta: Callable[[str, str], None] | None = None) -> str:
    """Registra la battuta dell'autore e ritorna la risposta. ``text`` vuoto = «riprova»."""
    from bookwriter.forge.interview import Stopped

    if text and text.strip():
        thread.append({"role": "user", "text": text.strip()})
    ultima = next((b for b in reversed(thread) if b.get("role") != "notice"), None)
    if ultima is None or ultima.get("role") != "user":
        raise ValueError("Non c'è niente a cui rispondere.")
    messaggi = _alternate(thread, HISTORY_CHARS)
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
            thread.append({"role": "assistant", "text": parziale + " …"})
        raise
    thread.append({"role": "assistant", "text": risposta})
    return risposta


# --------------------------------------------------------------------------- #
#  ricerca su richiesta dell'autore, nella chat
# --------------------------------------------------------------------------- #
INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string"},
                    "queries": {"type": "array", "items": {"type": "string"}},
                    "claim": {"type": "string"},
                },
                "required": ["kind", "queries", "claim"],
            },
        },
    },
    "required": ["requests"],
}

INTENT_RULES = (
    "Dal messaggio dell'autore dì se chiede di fare una ricerca: cercare riferimenti (kind "
    "\"cerca\"), verificare un'affermazione (\"verifica\"), scoprire come funziona o è andata una "
    "cosa (\"scopri\"). Per ogni richiesta restituisci le interrogazioni brevi da motore di "
    "ricerca (parole chiave, nella lingua più adatta alla fonte) e, se c'è, l'affermazione da "
    "verificare. Per \"verifica\" scrivi due interrogazioni: una cerca ciò che la sostiene, una "
    "ciò che la contraddice. Se non chiede niente, elenco vuoto. Gli indirizzi web li legge un "
    "altro passaggio: non metterli fra le interrogazioni."
)

#: Un filtro economico prima del passaggio del modello: la chiamata di «intento» costa un
#: ragionamento intero, e la maggior parte dei messaggi non chiede nessuna ricerca.
_SEARCH_HINT = re.compile(
    r"cerc|verific|scopri|trova|controll|riferiment|fonti|fonte|documenta|approfond|"
    r"search|look ?up|find|verify|check|source|reference|investigate|"
    r"cherch|vérifi|trouv|recherch|suche|prüf|finde|quelle|busca|verific|encontr|fuente|"
    r"поиск|найд|проверь|查|搜|検索|확인|खोज", re.IGNORECASE)


@dataclass
class SearchRequest:
    kind: str = "cerca"
    queries: list[str] = field(default_factory=list)
    claim: str = ""


@dataclass
class SearchLog:
    """Cosa è stato cercato e cosa ne è venuto: lo vede l'autore prima della risposta."""

    query: str = ""
    results: list[SearchResult] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return f"Cerco «{self.query}»: {self.error}"
        return (f"Cerco «{self.query}»: {len(self.results)} risultati, "
                f"{len(self.pages)} pagine lette.")


def looks_like_search(text: str) -> bool:
    return bool(text and _SEARCH_HINT.search(text))


def detect_intent(client: Any, text: str, point: ReviewPoint | None, *,
                  model: str | None = None) -> list[SearchRequest]:
    """Se l'autore chiede una ricerca, quale. Un guasto del modello vale «nessuna richiesta»."""
    if not looks_like_search(text):
        return []
    contesto = f"Il punto in discussione: {point.title}\n\n" if point else ""
    try:
        risposta = _quick(client).chat_json(
            [{"role": "system", "content": INTENT_RULES},
             {"role": "user", "content": f"{contesto}Messaggio dell'autore:\n{text}"}],
            schema=INTENT_SCHEMA, model=model)
    except Exception:  # noqa: BLE001
        return []
    richieste = []
    for r in (risposta.get("requests") or []) if isinstance(risposta, dict) else []:
        if not isinstance(r, dict):
            continue
        qs = [str(q).strip() for q in r.get("queries") or [] if str(q).strip()][:4]
        if qs:
            richieste.append(SearchRequest(str(r.get("kind") or "cerca"), qs,
                                           str(r.get("claim") or "")))
    return richieste


def run_requests(settings: ResearchSettings, requests: list[SearchRequest],
                 seen: set[str]) -> tuple[list[SearchLog], list[str]]:
    """Esegue le richieste. Rete spenta: lo dice e basta (``notes``). Riuso di ``gather``.

    Nessun filtro sulle fonti: si leggono e si etichettano più avanti, non si scartano perché
    non sono «autorevoli». Nessun tetto imposto dall'app: valgono ``max_results`` e ``max_pages``
    delle impostazioni.
    """
    log: list[SearchLog] = []
    note: list[str] = []
    if not requests:
        return log, note
    if not settings.enabled:
        note.append("La ricerca sul web è spenta: accendila nelle Impostazioni per far cercare "
                    "all'assistente. Posso usare il materiale del progetto e i documenti che "
                    "allegi.")
        return log, note
    visti = set(seen)
    for r in requests:
        for q in r.queries:
            try:
                risultati, pagine = gather(q, settings, skip=visti)
            except SearchError as exc:
                log.append(SearchLog(query=q, error=str(exc)))
                continue
            visti |= {norm_url(p.url) for p in pagine} | {norm_url(x.url) for x in risultati}
            log.append(SearchLog(query=q, results=risultati, pages=pagine))
    return log, note


def searched_text(log: list[SearchLog], findings: list[store.Finding]) -> str:
    righe = []
    for s in log:
        righe.append(f"### «{s.query}»" + (f" — errore: {s.error}" if s.error else ""))
        righe += [r.line() for r in s.results[:12]]
        for p in s.pages:
            righe.append(f"Pagina letta: {p.title} — {p.url}\n{_cut(p.text, 1500)}")
    if findings:
        righe.append("### Affermazioni ricavate dalle pagine (con livello di prova)")
        righe += [f"- ({f.level or '?'}) {f.claim}: {f.detail} — fonte: {f.source} {f.url}"
                  for f in findings]
    return "\n".join(righe)


def findings_from_pages(state: ForgeState, client: Any, pages: list[Page],
                        point: ReviewPoint, chapter: dict[str, Any] | None, *,
                        model: str | None = None) -> list[store.Finding]:
    """Le affermazioni documentabili delle pagine lette (stesso passaggio della ricerca)."""
    if not pages:
        return []
    voce = {**(chapter or {"number": point.chapter or "", "title": "", "does": point.statement}),
            "point": point.title}
    try:
        dati = rs._sources_from_model(state, client, rs.ResearchReport(pages=list(pages)), voce,
                                      [], model=model)
    except rs.ResearchFailed:
        return []
    trovate = rs._findings(dati)
    for f in trovate:
        f.origin = "revisione"
    return trovate


# --------------------------------------------------------------------------- #
#  proporre e registrare una decisione
# --------------------------------------------------------------------------- #
PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "outcome": {"type": "string"},
        "text": {"type": "string"},
        "note": {"type": "string"},
        "source": {"type": "string"},
        "url": {"type": "string"},
        "structure_note": {"type": "string"},
    },
    "required": ["outcome", "text", "note", "source", "url", "structure_note"],
}

PROPOSAL_RULES = (
    "Ti do un punto e la sua conversazione fra un autore e il suo editor. Se l'AUTORE ha detto in "
    "modo esplicito cosa vuole fare, restituisci:\n"
    "- outcome: \"concordata\" (autore ed editor hanno trovato una formulazione; per un punto di "
    "materiale o un'idea, l'autore lo vuole nel libro), \"imposta\" "
    "(l'autore ha detto di tenere la sua posizione NONOSTANTE le prove), \"fonte\" (l'autore ha "
    "portato o chiesto una fonte che regge), \"tralascia\" (il libro non ne parla), oppure "
    "\"nessuno\";\n"
    "- text: la formulazione ESATTA da usare nel libro, per intero, nella voce dell'autore, senza "
    "cautele che lui non ha chiesto (stringa vuota \"\" per \"tralascia\", e anche per un punto di "
    "materiale o un'idea che l'autore vuole così com'è);\n"
    "- note: una o due righe sulle prove contrarie o sulle formulazioni scartate, per gli "
    "appunti dell'autore (non entrano nel libro);\n"
    "- source e url: solo per \"fonte\", e solo se stanno nella conversazione o nelle pagine "
    "lette: mai inventati;\n"
    "- structure_note: se la scelta obbliga a cambiare i capitoli o i punti della struttura, una "
    "riga che lo dice; altrimenti la stringa vuota (\"\"). Tutti i campi ci sono sempre: i valori "
    "non noti sono stringhe vuote.\n"
    "Non vale una proposta dell'editor a cui l'autore non ha risposto, né un'ipotesi, né una "
    "domanda. \"imposta\" vale solo se l'autore ha detto chiaramente di tenere la sua "
    "posizione. Nel dubbio: \"nessuno\". Non correggere la tesi dell'autore."
)


def propose(client: Any, point: ReviewPoint, *, thread: list[dict[str, Any]],
            upto: int, model: str | None = None) -> dict[str, Any]:
    """Cosa registrerebbe l'app dopo l'ultimo scambio. **Non registra niente**: la scheda di
    conferma la mostra, e solo il clic dell'autore decide."""
    dove = max(0, min(upto, len(thread)) - 1)
    tratto = thread[dove:]
    if not any(b.get("role") == "user" for b in tratto):
        return {}
    conversazione = "\n\n".join(
        f"{'EDITOR' if b.get('role') == 'assistant' else 'AUTORE'}: {(b.get('text') or '').strip()}"
        for b in tratto)
    risposta = client.chat_json(
        [{"role": "system", "content": PROPOSAL_RULES},
         {"role": "user", "content": point_block(point) + "\n\n## L'ultimo tratto della "
                                     "conversazione\n" + conversazione}],
        schema=PROPOSAL_SCHEMA, model=model)
    if not isinstance(risposta, dict):
        return {}
    esito = str(risposta.get("outcome") or "").strip().lower()
    if esito not in DECISION_KINDS:
        return {}
    testo = str(risposta.get("text") or "").strip()
    senza_testo = point.kind in KINDS_WITHOUT_TEXT and esito == D_CONCORDATA
    if esito != D_TRALASCIA and not testo and not senza_testo:
        return {}
    return {"kind": esito, "text": testo, "note": str(risposta.get("note") or "").strip(),
            "source": str(risposta.get("source") or "").strip(),
            "url": str(risposta.get("url") or "").strip(),
            "structure_note": str(risposta.get("structure_note") or "").strip()}


def note_text(point: ReviewPoint, decision: Decision) -> str:
    """Le note di un punto per gli appunti: prove contrarie e formulazioni scartate, letterali."""
    righe = [f"## {point.title}", "",
             f"*Punto {point.id} ({point.kind}), decisione: {decision.kind}, "
             f"{date.today().isoformat()}.*", ""]
    if point.statement:
        righe += [f"Il libro diceva: {point.statement}", ""]
    if point.evidence:
        righe.append("Prove:")
        righe += [f"- {e.quote}" + (f" (livello {e.level})" if e.level else "")
                  + (f" — {e.source}" if e.source else "") + (f" {e.url}" if e.url else "")
                  for e in point.evidence]
        righe.append("")
    if decision.note:
        righe += [decision.note, ""]
    righe += [f"Nel libro: {decision.text or '(niente)'}", ""]
    return "\n".join(righe)


def append_note(project: BookProject, point: ReviewPoint, decision: Decision) -> Path:
    """Mette il punto negli appunti (``review_notes.md``), che la stesura non legge."""
    path = notes_path(project)
    testa = ("# Appunti della revisione\n\nProve contrarie, formulazioni scartate e fonti dei "
             "punti discussi con l'autore. **Non entrano nel libro**: la stesura non legge "
             "questo file.\n\n")
    esistente = path.read_text(encoding="utf-8") if path.exists() else testa
    blocco = note_text(point, decision)
    if f"*Punto {point.id} (" in esistente:  # già scritto: si rifà, non si duplica
        prima = esistente.split(f"## {point.title}")[0]
        resto = esistente.split(f"## {point.title}", 1)[1]
        fine = resto.find("\n## ")
        esistente = prima.rstrip() + "\n\n" + (resto[fine + 1:] if fine >= 0 else "")
    project.research_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(esistente.rstrip() + "\n\n" + blocco, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
#  dalla decisione alla stesura
# --------------------------------------------------------------------------- #
def review_text(data: ReviewData | None, chapter: int, *, skip_refs: bool = True) -> str:
    """Le decisioni della revisione per un capitolo, in forma di prompt per la stesura.

    Le decisioni sulle questioni non sono qui: stanno già in ``decisions_text`` (la loro fonte di
    verità è ``Question.outcome``). Le note che l'autore ha tenuto per sé non ci sono mai. Un punto
    non deciso non compare: il libro resta com'è. «Lo lascio fuori» di un materiale o di un'idea
    non scrive niente (il materiale esce dal file della ricerca), «tralascia» di un contrasto sì.
    """
    if data is None:
        return ""
    righe_cap, righe_libro = [], []
    for p in data.points:
        d = p.decision
        if d is None or p.status not in CHIUSI or (skip_refs and p.ref):
            continue
        if p.chapter not in (0, chapter):
            continue
        if p.kind in KINDS_WITHOUT_TEXT:
            if d.kind == D_TRALASCIA:
                continue
            cosa = "il materiale" if p.kind == MATERIALE else "l'idea"
            riga = (f"- «{p.title}» — l'autore vuole {cosa} nel libro: usalo"
                    + (f" così: {d.text}" if d.text else f". {p.statement}"))
        elif d.kind == D_TRALASCIA:
            riga = f"- «{p.title}» — non menzionare."
        elif d.kind == D_IMPOSTA:
            riga = (f"- «{p.title}» — scrivi così, in voce d'autore, anche se in contrasto con le "
                    f"fonti trovate (decisione dell'autore): {d.text}")
        elif d.kind == D_FONTE:
            riga = (f"- «{p.title}» — fonte trovata ({d.source or d.url or 'vedi la ricerca'}): "
                    f"{d.text}")
        else:
            riga = f"- «{p.title}» — scrivi così (concordato con l'autore): {d.text}"
        (righe_cap if p.chapter else righe_libro).append(riga)
    blocchi = []
    if righe_libro:
        blocchi.append("Valide per tutto il libro:\n" + "\n".join(righe_libro))
    if righe_cap:
        blocchi.append("Per questo capitolo:\n" + "\n".join(righe_cap))
    return "\n\n".join(blocchi)


def perplexities(data: ReviewData | None, project: BookProject, state: ForgeState) -> list[str]:
    """Le perplessità dell'app prima di scrivere: solo fatti verificabili sui dati."""
    cose: list[str] = []
    if data is not None:
        rimandati = [p for p in data.points if p.status in (APERTO, IN_DISCUSSIONE, RIMANDATO)]
        if rimandati:
            cose.append(f"{len(rimandati)} punti non sono stati decisi: "
                        + "; ".join(f"«{_cut(p.title, 70)}»" for p in rimandati[:6])
                        + ("…" if len(rimandati) > 6 else "."))
        for p in data.points:
            if p.decision is not None and p.decision.structure_note:
                cose.append(f"La decisione su «{_cut(p.title, 70)}» tocca la struttura: "
                            f"{p.decision.structure_note}")
    for c in state.outline:
        numero = int(c.get("number") or 0)
        dati = store.load_chapter(project.research_dir, numero, str(c.get("title") or ""))
        if dati is None:
            cose.append(f"Il capitolo {numero} non ha una ricerca.")
        elif not any(pt.findings for pt in dati.points):
            cose.append(f"Per il capitolo {numero} «{c.get('title')}» la ricerca non ha trovato "
                        "materiale: si scrive comunque, da quello che hai raccontato. Se vuoi, "
                        "posso cercarne altro.")
    return cose
