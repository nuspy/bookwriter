"""Fase 3 — la ricerca: lavora PER il libro.

Cerca **materiale per scrivere meglio**: dettagli, descrizioni, esperienze e testimonianze,
l'aspetto reale di un luogo o di un oggetto usato in una scena, citazioni, idee e collegamenti
che espandono la tesi e il racconto dell'autore, e **fonti a sostegno** delle sue posizioni — di
qualunque tipo (accademiche, divulgative, forum, post, testimonianze): si etichettano, non si
scartano. Ogni elemento dice come può servire al capitolo o alla scena.

Il controllo di correttezza è **terziario**: si segnala solo il contrasto con un fatto che sembra
comprovato e che produrrebbe un errore macroscopico (una data, un luogo reale). Si mostra e se
ne discute con l'autore, che decide. Gli elementi d'invenzione — personaggi, nomi, trama,
ambientazione, titoli dei capitoli, tesi e scelte di stile — non si verificano mai; e quello che
non trova nessuna fonte, a favore o contro, **resta nel libro com'è**: la mancanza di fonti non
è un difetto e non genera rinunce.

**Prima dell'indice.** Si guardano tre-cinque libri che esistono già e i loro indici, e le
recensioni critiche: descrivono il campo, non dicono cosa il libro debba fare. Il risultato va in
``research/00_landscape.md`` e cambia la struttura **prima** che la si mostri all'autore.

**Durante la stesura.** Un file per capitolo, scritto man mano e mai ricostruito dopo.

Una sola regola non si piega: **nessuna fonte, cifra o citazione inventata**. Si cita solo ciò
che si è letto davvero: una citazione inventata somiglia in tutto a una vera ed è la cosa più
dannosa che si possa mettere in un libro stampato.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from bookwriter.config import ResearchSettings, language_label
from bookwriter.forge import notebook as nb
from bookwriter.forge import pagetext
from bookwriter.forge import research_store as store
from bookwriter.forge.project import BookProject, ForgeState
from bookwriter.forge.research_store import EVIDENCE_LEVELS  # noqa: F401 - riesportato
from bookwriter.forge import jev as jevmod
from bookwriter.forge.spec import chapter_points
from bookwriter.llm.client import quick as _quick
from bookwriter.forge.websearch import Page, SearchError, SearchResult, fetch, gather, norm_url

#: Il panorama: «opere simili da cui attingere». Per ogni opera vicina al libro, cosa ci si può
#: prendere (idee, immagini, scene, fonti citate, spunti) e una nota breve su dove il libro
#: dell'autore è diverso: posizionamento, non correzione. I panorami salvati prima (con
#: ``competitors``, ``implicit_syllabus``…) si leggono ancora: vedi ``research_store``.
LANDSCAPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "works": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "year": {"type": "string"},
                    "url": {"type": "string"},
                    "draw_from": {"type": "string"},
                    "cited_sources": {"type": "array", "items": {"type": "string"}},
                    "different": {"type": "string"},
                },
                "required": ["title", "author", "year", "url", "draw_from", "cited_sources",
                             "different"],
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "ideas": {"type": "array", "items": {"type": "string"}},
        "recent": {"type": "string"},
    },
    "required": ["works", "sources", "ideas", "recent"],
}

#: Le chiavi del panorama di una volta: ancora lette, mai più scritte.
LEGACY_LANDSCAPE_KEYS = ("competitors", "implicit_syllabus", "gaps", "novelty",
                         "outline_consequences")

SOURCES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "detail": {"type": "string"},
                    "use": {"type": "string"},
                    "level": {"type": "string"},
                    "source": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["claim", "detail", "use", "level", "source", "url"],
            },
        },
        # I contrasti con un fatto che sembra comprovato (terziario). Il nome è quello di prima,
        # perché i dati già salvati e la stesura lo conoscono: oggi vuol dire solo questo.
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["findings", "open_questions"],
}


REFORMULATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}

RESOLVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "resolutions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "question_id": {"type": "string"},
                    "resolved": {"type": "boolean"},
                    "claim": {"type": "string"},
                    "detail": {"type": "string"},
                    "level": {"type": "string"},
                    "source": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["question_id", "resolved", "claim", "detail", "level", "source",
                             "url"],
            },
        },
    },
    "required": ["resolutions"],
}


#: Quante volte si riprovano le questioni su cui un passo del modello è fallito.
RETRY_ROUNDS = 3

#: Punti non riusciti di fila (anche fra capitoli) dopo i quali la ricerca si ferma. Come per la
#: stesura (``writing.CONSECUTIVE_FAILURES``): uno o due sono il punto, tre sono il modello o il
#: server che non rispondono più, e insistere vuol dire aspettare lo stesso errore per ogni punto
#: rimasto.
CONSECUTIVE_FAILURES = 3


@dataclass
class FailureStreak:
    """Quanti punti di fila non sono riusciti. Si passa da un capitolo al successivo."""

    count: int = 0
    last: str = ""
    limit: int = CONSECUTIVE_FAILURES

    @property
    def tripped(self) -> bool:
        return self.count >= self.limit


class ResearchFailed(RuntimeError):
    """Un passo della ricerca non è riuscito e **non** va dato per fatto.

    Un panorama vuoto scritto in silenzio e una ricerca che prosegue come se niente fosse sono il
    difetto da non fare: la fase resta da fare, e chi guarda il riquadro di stato legge perché.
    """


QUERIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}


class SupportsChat(Protocol):
    def chat_json(self, messages: list[dict[str, str]], *, schema: dict,
                  model: str | None = ...) -> Any:
        ...


@dataclass
class ResearchReport:
    """Cosa ha prodotto una passata di ricerca, e cosa non è riuscita a fare."""

    path: Path | None = None
    queries: list[str] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    findings: int = 0
    warnings: list[str] = field(default_factory=list)
    #: interrotta dall'autore prima della fine: quello che c'è non è il lavoro completo.
    stopped: bool = False
    #: i dati strutturati del capitolo, quando il rapporto è di un capitolo.
    data: Any = None
    #: i punti per cui il modello non ha saputo ricavare le fonti: la ricerca non è completa.
    failures: list[str] = field(default_factory=list)
    #: la ricerca si è fermata perché troppi punti di fila non sono riusciti (vedi ``FailureStreak``).
    gave_up: bool = False

    def summary(self) -> str:
        buone = [p for p in self.pages if getattr(p, "usable", True)]
        parti = [f"{len(self.results)} risultati", f"{len(buone)} pagine lette"]
        scartate = len(self.pages) - len(buone)
        if scartate:
            parti.append(f"{scartate} scartate (non leggibili o non pertinenti)")
        if self.findings:
            parti.append(f"{self.findings} affermazioni documentate")
        testo = ", ".join(parti)
        if self.path:
            testo += f" → {self.path.name}"
        return testo + ("\n" + "\n".join(f"⚠ {w}" for w in self.warnings) if self.warnings else "")


# --------------------------------------------------------------------------- #
#  il panorama: opere simili da cui attingere
# --------------------------------------------------------------------------- #
def landscape_queries(state: ForgeState) -> list[str]:
    """Le interrogazioni di ripiego, se il modello non le sa generare: titolo, forma e temi.

    Mai la premessa troncata (una frase di venti parole tagliata a metà non è una ricerca): il
    titolo di lavoro, la forma del libro e i suoi messaggi sono parole chiave, e si dicono anche
    in inglese, la lingua in cui sta quasi tutta la letteratura di un campo.
    """
    quaderno = nb.normalise(state.notebook)
    titolo = nb.get(state, "title").strip()
    forma = nb.get(state, "form").strip() or "libro"
    temi = [t if isinstance(t, str) else str(t.get("name", "")) for t in (quaderno.get("messages") or [])]
    temi = [" ".join(t.split()[:6]) for t in temi if t.strip()][:3]
    soggetto = titolo or (temi[0] if temi else " ".join(nb.get(state, "premise").split()[:6]))
    if not soggetto:
        return []
    code = nb.language_code(state)
    richieste = [f"{soggetto} {forma}", f"libri su {soggetto}", f"{soggetto} recensioni"]
    richieste += [f"libri su {t}" for t in temi]
    if code != "en":
        richieste += [f"best books about {soggetto}", f"{soggetto} book review"]
    else:
        richieste += [f"{soggetto} book review"]
    return list(dict.fromkeys(richieste))[:8]


def generate_landscape_queries(state: ForgeState, client: SupportsChat, *,
                               model: str | None = None) -> list[str]:
    """Le interrogazioni per guardare cosa esiste già, scritte dal modello a partire dal quaderno.

    Parole chiave su argomento, temi e forma (non la premessa tagliata), da quattro a otto, nella
    lingua del libro e in inglese quando il libro non lo è. Se il modello non risponde o dà
    interrogazioni inservibili si ripiega su :func:`landscape_queries`.
    """
    lingua = nb.language_name(state)
    codice = nb.language_code(state)
    sistema = (
        "Devi trovare, con un motore di ricerca, OPERE SIMILI a un libro che si sta progettando, "
        "da cui l'autore possa attingere: libri, saggi, racconti, articoli, testimonianze che "
        "trattano temi, ambienti o esperienze vicini, con le loro idee, immagini, scene e fonti "
        "citate. Non cerchi concorrenti da battere né errori da correggere. "
        "Dal quaderno del libro ricava da QUATTRO a OTTO interrogazioni brevi, da motore di "
        "ricerca (parole chiave, non frasi discorsive e non pezzi della premessa): l'argomento, "
        "i temi, la forma del libro, termini tecnici, tipi di opere affini. "
        f"Scrivile in {lingua}"
        + ("" if codice == "en" else " e, per metà circa, in inglese (la letteratura di un campo è "
                                       "per lo più in inglese)")
        + ". Niente interrogazioni identiche fra loro."
    )
    try:
        risposta = _quick(client).chat_json(
            [{"role": "system", "content": sistema},
             {"role": "user", "content": _brief_breve(state)
              or nb.get(state, "title") or nb.get(state, "premise")}],
            schema=QUERIES_SCHEMA, model=model)
        grezze = [str(q).strip() for q in (risposta.get("queries") or []) if str(q).strip()]
    except Exception:
        grezze = []
    premessa = " ".join(nb.get(state, "premise").lower().split())
    attacco = " ".join(premessa.split()[:8])
    buone = []
    for q in dict.fromkeys(grezze):
        if attacco and len(attacco.split()) >= 6 and attacco in " ".join(q.lower().split()):
            continue  # un pezzo di premessa, non una ricerca
        if len(q.split()) > 12:
            continue
        buone.append(q)
    if len(buone) < 3:
        return landscape_queries(state)
    return buone[:8]


def landscape(
    project: BookProject,
    state: ForgeState,
    client: SupportsChat,
    settings: ResearchSettings,
    *,
    model: str | None = None,
    progress=None,
    should_stop=None,
) -> ResearchReport:
    """Guarda cosa esiste già, e ne trae le conseguenze per la struttura.

    Si può fermare fra un'interrogazione e l'altra: in quel caso non scrive il panorama (sarebbe
    fatto su un pezzo del campo) e il rapporto lo dice con ``stopped``. Non segna la ricerca come
    fatta: «fatta» è panorama e fonti di tutti i capitoli, senza interruzioni (vedi ``Forge``).
    """
    project.scaffold()
    report = ResearchReport(queries=generate_landscape_queries(state, client, model=model))
    visti: set[str] = set()
    tema = topic_for(state)  # per il panorama contano solo le parole del libro
    for i, query in enumerate(report.queries, 1):
        if should_stop and should_stop():
            report.stopped = True
            return report
        if progress:
            progress(i - 1, len(report.queries))
        try:
            risultati, pagine = gather(query, settings)
        except SearchError as exc:
            report.warnings.append(str(exc))
            continue
        for r in risultati:  # una fonte una volta sola, anche se due interrogazioni la trovano
            if norm_url(r.url) not in visti:
                visti.add(norm_url(r.url))
                report.results.append(r)
        assess_pages(pagine, tema, settings, client=client, model=model,
                     context=judge_context(state))
        letti = {norm_url(p.url) for p in report.pages}
        report.pages.extend(p for p in pagine if norm_url(p.url) not in letti)
    if progress:
        progress(len(report.queries), len(report.queries))

    if not report.results:
        report.warnings.append(
            "Nessun risultato: il panorama si scrive con quello che il modello già sa, che è "
            "meno affidabile e va trattato come tale."
        )

    dati = _landscape_from_model(state, client, report, model=model)
    if _analysis_empty(dati):
        # Un panorama vuoto non è un panorama: non si scrive, la fase resta da fare e si dice
        # perché. Scriverlo in silenzio faceva proseguire la ricerca come se fosse fatta.
        raise ResearchFailed(
            "Il panorama non è stato scritto: il modello non ne ha ricavato niente dal "
            "materiale trovato. " + (" ".join(report.warnings) if report.warnings else "")
            + " Premi «Riprendi la ricerca» per riprovare.")
    paesaggio = store.LandscapeData(
        date=date.today().isoformat(), queries=list(report.queries),
        results=[store.Hit(r.title, r.url, r.snippet) for r in report.results],
        pages=_read(report.pages),
        analysis=dati, warnings=list(report.warnings),
    )
    report.path = store.save_landscape(
        project.research_dir, paesaggio, store.render_landscape_md("", paesaggio))
    report.findings = len(dati.get("works") or dati.get("competitors") or [])
    return report


def _landscape_from_model(state: ForgeState, client: SupportsChat, report: ResearchReport,
                          *, model: str | None) -> dict[str, Any]:
    materiale = "\n\n".join(
        f"### {p.title}\n{p.url}\n({p.cleaning_line()})\n{p.excerpt(3500)}"
        for p in usable(report.pages)[:6]
    ) or "(nessuna pagina letta)"
    elenco = "\n".join(r.line() for r in report.results[:20]) or "(nessun risultato)"
    sistema = (
        "Sei un editor che cerca, per un autore, materiale da cui attingere: le OPERE SIMILI al "
        "suo libro. Dal materiale trovato ricava, per ogni opera vicina: titolo, autore, anno, "
        "indirizzo; 'draw_from' = cosa ci si può prendere per arricchire il libro dell'autore "
        "(idee, immagini, scene, ambientazioni, modi di raccontare, spunti riusabili), con i "
        "dettagli concreti che il materiale riporta; 'cited_sources' = le fonti che l'opera cita e "
        "che potrebbero servire; 'different' = una nota breve su dove il libro dell'autore è "
        "diverso (posizionamento, non correzione). Poi 'sources' (fonti, testi o siti utili "
        "emersi), 'ideas' (idee e spunti ricavati dal materiale per espandere la tesi e il "
        "racconto dell'autore) e 'recent' (cosa è uscito di recente che può servire). Non giudicare "
        "la tesi dell'autore, non dire che deve coprire certi argomenti e non suggerire di "
        "cambiarla. Non inventare titoli, autori o anni: se il materiale non lo dice, scrivi nel "
        "campo una stringa vuota (\"\") — il campo deve esserci sempre, mai omesso — e per gli "
        "elenchi un elenco vuoto ([]). Un'opera inesistente citata come simile è peggio di "
        "nessuna. "
        f"Scrivi in {language_label(state.language) or 'italiano'}."
    )
    utente = (
        f"## Il libro da fare\n{_brief_breve(state)}\n\n"
        f"## Risultati della ricerca\n{elenco}\n\n## Pagine lette\n{materiale}"
    )
    try:
        return client.chat_json(
            [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
            schema=LANDSCAPE_SCHEMA, model=model,
        )
    except Exception as exc:
        raise ResearchFailed(
            f"Il panorama non è stato scritto: il modello non ha saputo leggere il materiale ({exc})."
            " Premi «Riprendi la ricerca» per riprovare.") from exc


def _analysis_empty(dati: dict[str, Any]) -> bool:
    """L'analisi non contiene niente di utile (né opere, né fonti, né idee, né i campi di una volta)."""
    return not any(dati.get(k) for k in (
        "works", "sources", "ideas", "recent", *LEGACY_LANDSCAPE_KEYS))


def _context_line(state: ForgeState) -> str:
    """Una riga di contesto per le richieste **su un punto**: titolo, forma e ambientazione.

    Mai la premessa né i messaggi del libro: elencano i temi di **tutti** i capitoli, e un
    modello che li legge cerca l'Osiride del capitolo 7 mentre lavora a una cerimonia del
    capitolo 1 (misurato: le interrogazioni di un punto sull'ayahuasca parlavano di Osiride,
    Mosè e Massoneria). Per il punto contano il capitolo e il punto; il libro serve solo a
    sapere di che genere di libro si tratta e dove è ambientato.
    """
    parti = []
    titolo = " ".join(nb.get(state, "title").split())
    if titolo:
        parti.append(f"«{titolo}»")
    forma = " ".join(nb.get(state, "form").split())
    if forma:
        parti.append(forma[:160])
    ambiente = " ".join(nb.get(state, "setting").split())
    if ambiente:
        parti.append("ambientazione: " + ambiente[:160])
    return "; ".join(parti)


def _brief_breve(state: ForgeState) -> str:
    voci = [
        ("Il libro", "premise"), ("Forma", "form"), ("Cosa vuole ottenere l'autore", "author_goal"),
        ("Messaggi", "messages"), ("Lettore", "audience"),
    ]
    return "\n".join(f"- **{e}**: {nb.get(state, k)}" for e, k in voci if nb.get(state, k))


# --------------------------------------------------------------------------- #
#  le fonti dei capitoli: punto per punto
# --------------------------------------------------------------------------- #
def describe_note(nota: object) -> str:
    """Una nota di avanzamento in parole: le note strutturate (tuple) per la riga di comando."""
    if isinstance(nota, tuple) and nota and nota[0] == "point":
        return f"Capitolo {nota[1]}, punto {nota[2]} di {nota[3]}"
    if isinstance(nota, tuple) and nota and nota[0] == "deepen":
        return f"Capitolo {nota[1]}: approfondisco una questione (giro {nota[2]} di {nota[3]})"
    return str(nota)


def _norm(testo: str) -> str:
    return " ".join((testo or "").lower().split())


def _hits(risultati: list[SearchResult]) -> list[store.Hit]:
    return [store.Hit(r.title, r.url, r.snippet, "escluso" if getattr(r, "excluded", False) else "")
            for r in risultati]


def _read(pagine: list[Page], origin: str = "web") -> list[store.PageRead]:
    return [store.PageRead(p.url, p.title, p.text[:store.PAGE_KEEP], origin, p.status, p.note,
                           p.cleaning_line()) for p in pagine]


def _page_objects(pagine: list[store.PageRead]) -> list[Page]:
    return [Page(url=p.url, title=p.title, text=p.text,
                 readable=p.status != "non_leggibile", relevant=p.status != "non_pertinente",
                 note=p.note, cleaning=p.cleaning) for p in pagine]


def usable(pagine: list[Page]) -> list[Page]:
    """Le pagine che possono andare al modello: leggibili e pertinenti. Le altre restano
    registrate nei dati della ricerca, con il motivo, e non si passano mai."""
    return [p for p in pagine if p.usable]


def topic_for(state: ForgeState, point_text: str = "") -> pagetext.Topic:
    """Di cosa deve parlare una pagina: le parole del punto e quelle del libro.

    Il titolo del capitolo non conta (un «tunnel frattale» è anche un videogioco) e nemmeno i
    nomi inventati dal libro: non stanno sul web.
    """
    libro = " ".join(filter(None, (
        nb.get(state, "premise"), nb.get(state, "author_goal"), nb.get(state, "setting"),
        nb.get(state, "form"), nb.get(state, "messages"))))
    return pagetext.Topic.of(point_text, libro, exclude=invented_terms(state))


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"score": {"type": "number"}, "reason": {"type": "string"}},
    "required": ["score", "reason"],
}

JUDGE_RULES = (
    "Giudichi se una pagina web SERVE a scrivere un punto di un libro. La pagina può essere in "
    "qualunque lingua, e il libro in un'altra. Serve se parla dell'argomento del punto o del "
    "libro e può dare materiale utile — descrizioni, esperienze, dettagli, fonti, idee — anche in "
    "parte. Non serve se parla d'altro: un omonimo (un videogioco, un prodotto, un'altra "
    "persona), un argomento diverso, un indice o una pagina commerciale. Non giudicare la "
    "verità né la tesi dell'autore: conta solo se la pagina parla dell'argomento. 'score' da 0 a "
    "1: 0 = parla d'altro; 0,5 = tocca l'argomento ma è poco utile; 1 = molto utile. 'reason' = "
    "una riga che dice di cosa parla la pagina e perché serve o no."
)


def judge_page(client: SupportsChat, page: Page, context: str, *, model: str | None = None
               ) -> tuple[float, str] | None:
    """Il giudizio del modello sulla pertinenza di una pagina: ``(punteggio 0-1, motivo)``.

    ``None`` se il modello non risponde o risponde male: nel dubbio la pagina **non** si scarta.
    """
    utente = (f"## Il punto da scrivere\n{context}\n\n## La pagina\n{page.title}\n{page.url}\n"
              f"{page.excerpt(2500)}")
    try:
        risposta = _quick(client).chat_json(
            [{"role": "system", "content": JUDGE_RULES}, {"role": "user", "content": utente}],
            schema=JUDGE_SCHEMA, model=model)
        punteggio = float(risposta["score"])
    except Exception:  # noqa: BLE001 - un giudizio mancato non toglie una pagina
        return None
    return max(0.0, min(1.0, punteggio)), str(risposta.get("reason") or "").strip()


def judge_context(state: ForgeState, chapter: dict[str, Any] | None = None,
                  point_text: str = "") -> str:
    parti = [f"Il libro: {_context_line(state)}"] if _context_line(state) else []
    if chapter:
        parti.append(f"Capitolo {chapter.get('number')}: {chapter.get('title')} — "
                     f"{chapter.get('does')}")
    if point_text:
        parti.append(f"Punto: {point_text}")
    return "\n".join(parti)


def assess_pages(pagine: list[Page], topic: pagetext.Topic, settings: ResearchSettings, *,
                 client: SupportsChat | None = None, model: str | None = None,
                 context: str = "", jev: jevmod.JevSession | None = None) -> None:
    """Segna come non pertinenti le pagine che non servono al punto.

    Lo giudica **Jev** se è acceso (``research.jev_enabled``: solo l'estratto della pagina e il
    punto, soglia ``jev_min_score``), altrimenti, o se Jev non risponde, il **modello** locale
    (una breve chiamata per pagina pulita, in qualunque lingua; soglia ``min_relevance``). Prima,
    un solo scarto senza modello, per il caso più sicuro: stessa lingua e nessuna parola in
    comune. Un giudizio mancato non scarta la pagina.
    """
    soglia = float(getattr(settings, "min_relevance", 0.0) or 0.0)
    sessione = jev if jev is not None else jevmod.session(settings)
    if not soglia and sessione is None:
        return
    for p in pagine:
        if not p.readable:
            continue
        if soglia and pagetext.zero_overlap(p.text, topic, topic.text):
            p.relevant, p.relevance = False, 0.0
            p.note = "non pertinente: nemmeno una parola in comune con il punto e con il libro"
            continue
        if sessione is not None:
            try:
                punteggio, confidenza = sessione.judge(context, p.text)
            except jevmod.JevError:
                sessione.meter.fell_back()  # Jev non risponde: si torna al modello locale
            else:
                p.relevance = punteggio / jevmod.SCALE_MAX
                if punteggio < sessione.min_score:
                    p.relevant = False
                    p.note = (f"non pertinente (giudizio Jev {punteggio:.2f}, "
                              f"confidenza {confidenza:.2f})")
                continue
        if client is None or not soglia:
            continue
        giudizio = judge_page(client, p, context, model=model)
        if giudizio is None:
            continue
        p.relevance = giudizio[0]
        if giudizio[0] < soglia:
            p.relevant = False
            p.note = (f"non pertinente (giudizio del modello {giudizio[0]:.2f}, sotto "
                      f"{soglia:.2f}): {giudizio[1]}")


def _add_unique(destinazione: list[store.Hit], nuovi: list[store.Hit]) -> list[store.Hit]:
    """Aggiunge i risultati mai visti e ritorna quelli aggiunti."""
    visti = {norm_url(h.url) for h in destinazione}
    aggiunti = []
    for h in nuovi:
        if h.url and norm_url(h.url) not in visti:
            visti.add(norm_url(h.url))
            destinazione.append(h)
            aggiunti.append(h)
    return aggiunti


def _findings(dati: dict[str, Any]) -> list[store.Finding]:
    trovate = []
    for f in dati.get("findings") or []:
        if isinstance(f, dict):
            trovate.append(store.Finding(
                claim=str(f.get("claim") or ""), detail=str(f.get("detail") or ""),
                level=str(f.get("level") or ""), source=str(f.get("source") or ""),
                url=str(f.get("url") or ""), use=str(f.get("use") or "")))
    return trovate


def invented_terms(state: ForgeState) -> set[str]:
    """Le parole degli elementi d'invenzione del libro: nomi dei personaggi, titoli, capitoli.

    Non si verificano mai: una «questione» che li nomina è un errore di chi l'ha scritta (è
    successo: il nome di un personaggio di fantasia fra i punti «non documentati»).
    """
    termini: set[str] = set()
    quaderno = nb.normalise(state.notebook)
    for persona in quaderno.get("characters") or []:
        nome = _norm(str(persona.get("name", "")))
        if nome:
            termini.add(nome)
            termini.update(w for w in re.findall(r"\w{4,}", nome))
    titolo = _norm(nb.get(state, "title"))
    if titolo:
        termini.add(titolo)
    for c in state.outline:
        t = _norm(str(c.get("title") or ""))
        if len(t) >= 4:
            termini.add(t)
    return termini


def _is_invention(testo: str, termini: set[str]) -> bool:
    corpo = _norm(testo)
    return any(t and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", corpo) for t in termini)


def _add_questions(data: store.ChapterData, punto: store.PointData,
                   testi: list[Any], termini: set[str] | None = None) -> list[store.Question]:
    """I contrasti segnalati per un punto diventano oggetti, senza doppioni nel capitolo.

    Quelli che nominano un elemento d'invenzione del libro si scartano: non sono contrasti con
    un fatto, e l'autore non deve mai vederli.
    """
    esistenti = {_norm(q.text) for q in data.questions}
    nuove = []
    for testo in testi:
        testo = str(testo or "").strip()
        if not testo or _norm(testo) in esistenti:
            continue
        if termini and _is_invention(testo, termini):
            continue
        esistenti.add(_norm(testo))
        ident = f"c{data.number}p{punto.index}q{len(data.questions) + 1}"
        domanda = store.Question(id=ident, text=testo, chapter=data.number, point=punto.index,
                                 kind="contrasto")
        data.questions.append(domanda)
        nuove.append(domanda)
    return nuove


QUERIES_FOR_POINT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"search_queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["search_queries"],
}


def point_queries(state: ForgeState, client: SupportsChat, chapter: dict[str, Any], testo: str,
                  *, model: str | None = None) -> list[str]:
    """Le interrogazioni per trovare materiale per un punto, scritte dal modello.

    Orientate a **scrivere meglio** e a trovare fonti **a sostegno**, non a verificare: se non
    risponde, o risponde male, si ripiega su :func:`_point_queries`.
    """
    sistema = (
        "Devi trovare, con un motore di ricerca, materiale per SCRIVERE MEGLIO un punto di un "
        "libro: esperienze e testimonianze di chi ha vissuto cose simili, descrizioni concrete e "
        "dettagli sensoriali o d'epoca, come appare davvero un luogo o un oggetto che sta in una "
        "scena, fonti e testi che SOSTENGONO la tesi dell'autore (di qualunque tipo: "
        "accademiche, divulgative, forum, post, testimonianze), idee e collegamenti che la "
        "espandono. NON devi verificare se è vero né cercare smentite, e non cercare i nomi o gli "
        "eventi inventati dall'autore: cerca ciò che serve a raccontarli. Da tre a cinque "
        "interrogazioni brevi, da motore di ricerca (parole chiave, non frasi), nella lingua più "
        "adatta alle fonti (spesso l'inglese). "
        f"Scrivi in {language_label(state.language) or 'italiano'} solo le parti che non sono "
        "interrogazioni."
    )
    contesto = _context_line(state)
    utente = ((f"## Il libro (solo come contesto: cerca per il punto, non per il libro)\n"
               f"{contesto}\n\n" if contesto else "")
              + f"## Capitolo {chapter.get('number')}: {chapter.get('title')}\n"
              f"Cosa deve fare: {chapter.get('does')}\n"
              f"Punto: {testo}")
    try:
        risposta = _quick(client).chat_json(
            [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
            schema=QUERIES_FOR_POINT_SCHEMA, model=model)
        grezze = [str(q).strip() for q in (risposta.get("search_queries") or [])
                  if str(q).strip()] if isinstance(risposta, dict) else []
    except Exception:  # noqa: BLE001 - un guasto qui non ferma la ricerca: si ripiega
        grezze = []
    buone = [q for q in dict.fromkeys(grezze) if len(q.split()) <= 14]
    return buone[:5]


def _point_queries(titolo: str, testo: str, many: bool) -> list[str]:
    """Le interrogazioni di ripiego: materiale per scrivere e fonti a sostegno, non verifiche."""
    soggetto = f"{titolo} {testo}".strip() if many else testo
    return [q for q in (f"{soggetto} esperienze testimonianze descrizioni",
                        f"{soggetto} fonti a sostegno approfondimenti") if soggetto]


def chapter_state(data: store.ChapterData | None, chapter: dict[str, Any]) -> str:
    """A che punto è la ricerca di un capitolo, secondo quello che c'è su disco.

    ``new`` (niente, o fatta per altri punti: la struttura è cambiata), ``points`` (mancano
    punti, o qualcuno non è riuscito), ``deepening`` (i punti ci sono, restano questioni da
    approfondire) o ``done``. «Riprendi la ricerca» tocca tutto tranne ``done``.
    """
    punti = chapter_points(chapter)
    if data is None or not data.points:
        return "new"
    fatti = [_norm(p.text) for p in data.points]
    if fatti != [_norm(t) for t in punti][:len(fatti)]:
        return "new"
    if len(data.points) < len(punti) or not all(p.done for p in data.points):
        return "points"
    if data.stage == store.STAGE_DONE:
        return "done"
    return "deepening"


def chapter_research(
    project: BookProject,
    state: ForgeState,
    client: SupportsChat,
    settings: ResearchSettings,
    chapter: dict[str, Any],
    *,
    model: str | None = None,
    memory: list[str] | None = None,
    should_stop=None,
    note=None,
    streak: FailureStreak | None = None,
) -> ResearchReport:
    """Raccoglie e documenta quello che serve a un capitolo, **punto per punto**.

    Ogni punto chiave del capitolo ha le sue interrogazioni, i suoi risultati, le sue pagine
    lette e le sue affermazioni. Quello che non si riesce a documentare diventa una
    :class:`~bookwriter.forge.research_store.Question`: si approfondisce da soli (finché rende) e
    quel che resta va all'autore. Il JSON si scrive **dopo ogni punto** (e dopo ogni questione
    approfondita); il ``.md`` che legge la stesura si rigenera da lì.

    **Riprende** da dove si era arrivati: se il capitolo è già su disco, i punti fatti
    (``PointData.done``) non si rifanno, un punto rimasto a metà o non riuscito si riprende
    tenendo le pagine già lette, e un capitolo ``done`` non si tocca. Per rifare un capitolo da
    capo si mette da parte il suo materiale prima (``ForgeEngine.set_aside_research``); per
    rifarlo tenendo il materiale si azzerano i ``done`` (``ForgeEngine.prepare_redo``).

    ``memory`` sono i brani del materiale già importato nel progetto che parlano di questo
    capitolo: valgono quanto una fonte esterna, e spesso di più, perché sono dell'autore.
    """
    project.scaffold()
    numero = int(chapter.get("number") or 0)
    titolo = str(chapter.get("title") or "")
    punti = chapter_points(chapter)
    report = ResearchReport(queries=[])

    termini = invented_terms(state)  # gli elementi d'invenzione non si verificano mai

    # Quel che l'autore aveva già deciso su questo capitolo non si butta rifacendo la ricerca.
    vecchio = store.load_chapter(project.research_dir, numero, titolo)
    tappa = chapter_state(vecchio, chapter)
    if tappa == "done":
        report.data = vecchio
        report.queries = [q for p in vecchio.points for q in p.queries]
        report.results = [SearchResult(h.title, h.url, h.snippet) for p in vecchio.points
                          for h in p.results]
        report.pages = _page_objects([pg for p in vecchio.points for pg in p.pages])
        report.findings = sum(len(p.findings) for p in vecchio.points)
        report.warnings = list(vecchio.warnings)
        report.path = store.chapter_md_path(project.research_dir, numero, titolo)
        if note:
            note(("point", numero, len(punti), len(punti)))
        return report
    if tappa == "new":
        data = store.ChapterData(number=numero, title=titolo, does=str(chapter.get("does") or ""),
                                 date=date.today().isoformat())
    else:
        data = vecchio
        data.does = str(chapter.get("does") or data.does)
    decise = {_norm(q.text): q for q in (vecchio.questions if vecchio else [])
              if q.status == store.DECISA}
    data.stage = store.STAGE_POINTS
    sessione = jevmod.session(settings)  # None se Jev è spento o manca la chiave
    #: il testo intero delle pagine appena lette: su disco se ne tengono solo i primi
    #: ``PAGE_KEEP`` caratteri, ma Jev sceglie le sezioni migliori dall'intero.
    interi: dict[str, str] = {}

    for indice, testo in enumerate(punti):
        if should_stop and should_stop():
            report.stopped = True
            break
        punto = data.point(indice)
        if punto is not None and punto.done:
            if note:
                note(("point", numero, indice + 1, len(punti)))
            continue
        if punto is None:
            punto = store.PointData(index=indice, text=testo)
            data.points.append(punto)
        if settings.enabled:
            punto.queries = (point_queries(state, client, chapter, testo, model=model)
                             or _point_queries(titolo, testo, len(punti) > 1 or bool(
                                 chapter.get("points"))))
            for query in punto.queries:
                try:
                    risultati, pagine = gather(query, settings,
                                               read=max(1, settings.max_pages // 2),
                                               skip=data.all_urls())
                except SearchError as exc:
                    if str(exc) not in data.warnings:
                        data.warnings.append(str(exc))
                    continue
                assess_pages(pagine, topic_for(state, testo), settings, client=client,
                             model=model, context=judge_context(state, chapter, testo),
                             jev=sessione)
                _add_unique(punto.results, _hits(risultati))
                nuove = [p for p in _read(pagine) if norm_url(p.url) not in punto.read_urls()]
                punto.pages.extend(nuove)
                interi.update({norm_url(p.url): p.text for p in pagine})
                from bookwriter.forge import blocked
                for pag in pagine:
                    if not pag.readable and getattr(pag, "block_reason", ""):
                        blocked.enqueue_blocked_page(
                            project.research_dir,
                            url=pag.url,
                            chapter=numero,
                            point=indice + 1,
                            query=query,
                            reason=pag.block_reason,
                        )
        else:
            if indice == 0 and (msg := "Ricerca in rete spenta nelle impostazioni: per questo "
                                "capitolo si usa solo il materiale del progetto.") \
                    not in data.warnings:
                data.warnings.append(msg)
        # Tutte le pagine buone del punto, anche quelle lette in un giro precedente.
        pagine_punto: list[Page] = usable(_page_objects(punto.pages))
        for pg in pagine_punto:
            intero = interi.get(norm_url(pg.url), "")
            if len(intero) > len(pg.text):
                pg.text = intero

        pr = ResearchReport(queries=list(punto.queries), pages=pagine_punto)
        voce = {**chapter, "point": testo if chapter.get("points") else "",
                "does": (testo if chapter.get("points") else chapter.get("does"))}
        prefisso = f"Punto {indice + 1} «"
        try:
            dati = _sources_from_model(state, client, pr, voce, memory or [], model=model,
                                       jev=sessione)
        except ResearchFailed as exc:
            # Un punto che il modello non ha saputo leggere non è un punto senza fonti: si dice,
            # e la ricerca non conta come fatta. Gli altri punti proseguono.
            motivo = f"Punto {indice + 1} «{testo[:50]}»: {exc}"
            data.warnings[:] = [w for w in data.warnings if not w.startswith(prefisso)]
            data.warnings.append(motivo)
            report.failures.append(motivo)
            if streak is not None:
                streak.count += 1
                streak.last = motivo
            if note:
                # Subito, non a fine capitolo: un punto che il modello non legge può costare
                # minuti, e chi guarda il riquadro vedrebbe il contatore fermo senza sapere perché.
                note(("point", numero, indice + 1, len(punti)))
                note(f"⚠ Capitolo {numero}, {motivo}. Proseguo col punto successivo; alla fine "
                     "la ricerca resterà da riprendere («Riprendi la ricerca»).")
            _flush_jev(data, sessione)
            store.save_chapter(project.research_dir, data)
            if streak is not None and streak.tripped:
                # Tre di fila: non è il punto, è il modello o il server. Si smette qui; quello che
                # è già stato raccolto è salvato e resta.
                report.gave_up = True
                break
            continue
        if streak is not None:
            streak.count = 0
        data.warnings[:] = [w for w in data.warnings if not w.startswith(prefisso)]
        data.warnings.extend(w for w in pr.warnings if w not in data.warnings)
        visti = {_norm(f.claim) for f in punto.findings}
        punto.findings.extend(f for f in _findings(dati) if _norm(f.claim) not in visti)
        for domanda in _add_questions(data, punto, list(dati.get("open_questions") or []),
                                      termini):
            if (anterior := decise.get(_norm(domanda.text))) is not None:
                domanda.status, domanda.outcome = anterior.status, dict(anterior.outcome)
        punto.done = True
        _flush_jev(data, sessione)
        store.save_chapter(project.research_dir, data)  # un punto alla volta: nulla va perso
        if note:
            note(("point", numero, indice + 1, len(punti)))

    report.queries = [q for p in data.points for q in p.queries]
    tutti = len(data.points) == len(punti) and all(p.done for p in data.points)
    if tutti:
        data.stage = store.STAGE_DEEPENING
    _flush_jev(data, sessione)
    store.save_chapter(project.research_dir, data)  # prima si scrive: il resto può fermarsi

    if not report.stopped and not report.gave_up:
        if settings.enabled:
            ferme = [q.id for q in store.stuck_questions(data)]
            if ferme:
                deepen_chapter(project, state, client, settings, data, chapter, model=model,
                               should_stop=should_stop, note=note, only=ferme)
            report.stopped = bool(should_stop and should_stop())
        else:
            # Senza web non c'è dove approfondire: passano all'autore, che può portare lui le fonti.
            for q in data.questions:
                if not q.closed:
                    q.status = store.CHIESTA
        if tutti and not report.stopped:
            data.stage = store.STAGE_DONE
    store.save_chapter(project.research_dir, data)

    report.results = [SearchResult(h.title, h.url, h.snippet) for p in data.points
                      for h in p.results]
    report.pages = _page_objects([pg for p in data.points for pg in p.pages])
    report.findings = sum(len(p.findings) for p in data.points)
    report.warnings = list(data.warnings)
    report.data = data
    report.path = store.chapter_md_path(project.research_dir, numero, titolo)
    _append_sources_index(project, {"findings": [asdict_finding(f) for p in data.points
                                                 for f in p.findings]})
    return report


def asdict_finding(f: store.Finding) -> dict[str, str]:
    return {"claim": f.claim, "detail": f.detail, "level": f.level, "source": f.source,
            "url": f.url}


def _excerpt(page: Page, limit: int, jev: jevmod.JevSession | None, context: str) -> str:
    """Quanto del testo della pagina legge il modello per l'estrazione: l'inizio, oppure, con Jev
    e una pagina più lunga del limite, le sezioni più utili al punto (intere, nell'ordine)."""
    if jev is None or len(page.text) <= limit:
        return page.excerpt(limit)
    return jev.best_excerpt(context, page.text, limit)


def _flush_jev(data: store.ChapterData, sessione: jevmod.JevSession | None) -> None:
    """Porta nei dati del capitolo quanto si è speso con Jev (chiamate, token, secondi)."""
    if sessione is None:
        return
    m = sessione.meter
    if not (m.calls or m.failures or m.fallbacks):
        return
    m.merge_into(data.jev)
    sessione.meter = jevmod.JevMeter()


def _sources_from_model(state: ForgeState, client: SupportsChat, report: ResearchReport,
                        chapter: dict[str, Any], memory: list[str],
                        *, model: str | None,
                        jev: jevmod.JevSession | None = None) -> dict[str, Any]:
    contesto_jev = judge_context(state, chapter, str(chapter.get("point") or "")) if jev else ""
    materiale = "\n\n".join(
        f"### {p.title}\n{p.url}\n({p.cleaning_line()})\n{_excerpt(p, 3000, jev, contesto_jev)}"
        for p in usable(report.pages)[:5]
    )
    interno = "\n\n".join(f"### Dal materiale del progetto\n{t[:2000]}" for t in memory[:5])
    sistema = (
        "Hai davanti il materiale trovato dalla ricerca per un capitolo (o per un suo punto) di "
        "un libro che l'autore sta scrivendo. Il tuo compito è tirarne fuori tutto ciò che si può "
        "USARE nel libro: dettagli concreti e sensoriali; descrizioni di luoghi, oggetti, riti, "
        "persone; esperienze e testimonianze di chi le ha vissute; aneddoti; citazioni; dati e "
        "cifre; idee e collegamenti che espandono la tesi o il racconto dell'autore; fonti che "
        "SOSTENGONO le sue posizioni, di qualunque tipo (accademiche, divulgative, forum, post, "
        "testimonianze: non si scartano, si etichettano). Per ogni elemento: 'claim' (di cosa si "
        "tratta, in una riga), 'detail' (il dettaglio, con le parole e le cifre esatte del "
        "materiale), 'use' (come può servire a questo capitolo o a questa scena), 'level' (il "
        "tipo di fonte: A revisione sistematica · B studio singolo o statistica ufficiale · C "
        "fonte secondaria o divulgativa · D testimonianza, esperienza, post · E congettura o idea "
        "di un autore: è solo un'etichetta, non un voto), 'source' (l'autore o il sito, e l'anno "
        "se c'è) e 'url'.\n\n"
        "NON verificare il libro. Personaggi, nomi, trama, ambientazione, titoli dei capitoli, "
        "tesi e scelte di stile sono dell'autore e non si controllano mai: non è un problema "
        "che il materiale non li nomini, e non scrivere mai che qualcosa «non è documentato». "
        "Se per questo punto il materiale non offre niente di utile, 'findings' è un elenco "
        "vuoto: va bene.\n\n"
        "'open_questions' serve SOLO a questo: il materiale contiene un fatto che sembra "
        "comprovato (una data, un luogo reale, un evento storico, un dato scientifico accertato) "
        "in contrasto con quanto il libro afferma, e che produrrebbe un errore macroscopico per "
        "il lettore. Una riga per contrasto: cosa afferma il libro, cosa dice la fonte. Non "
        "serve per tesi controverse, per elementi di fantasia o per ciò che non trovi: in quei "
        "casi elenco vuoto.\n\n"
        "NON inventare fonti, cifre, autori, anni o citazioni: cita solo ciò che il materiale "
        "dice davvero. Una citazione inventata somiglia in tutto a una vera ed è la cosa più "
        "dannosa che si possa mettere in un libro stampato. "
        f"Scrivi in {language_label(state.language) or 'italiano'}."
    )
    punto = str(chapter.get("point") or "").strip()
    utente = (
        (f"## Il libro (è dell'autore: non si controlla; solo contesto)\n"
           f"{_context_line(state)}\n\n" if _context_line(state) else "")
        + f"## Capitolo {chapter.get('number')}: {chapter.get('title')}\n"
        f"Cosa deve fare: {chapter.get('does')}\n"
        + (f"Punto di cui ci occupiamo ora: {punto}\n" if punto else "")
        + f"\n## Materiale trovato\n{materiale or '(nessuna pagina letta)'}\n\n"
        f"{interno}"
    )
    try:
        return client.chat_json(
            [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
            schema=SOURCES_SCHEMA, model=model,
        )
    except Exception as exc:
        raise ResearchFailed(f"il modello non ha saputo ricavare le fonti ({exc})") from exc


# --------------------------------------------------------------------------- #
#  le questioni aperte: approfondire prima di disturbare l'autore
# --------------------------------------------------------------------------- #
def reformulate(state: ForgeState, client: SupportsChat, question: store.Question,
                chapter: dict[str, Any], tried: list[str], *, round: int,
                model: str | None = None, errors: list[str] | None = None) -> list[str]:
    """Interrogazioni nuove per una questione: parole chiave diverse, sinonimi, termini tecnici e,
    quando serve, un'altra lingua (l'inglese, per le fonti internazionali)."""
    sistema = (
        "Una prima ricerca non ha trovato abbastanza su questo punto di un libro. Devi trovare "
        "PIÙ MATERIALE per scriverlo meglio: fonti che lo sostengono, esperienze e testimonianze, "
        "descrizioni, dettagli. Se il punto è un contrasto con un fatto, cerca anche le fonti sul "
        "fatto stesso. Proponi da due a quattro interrogazioni per un motore di ricerca, "
        "DIVERSE da quelle già provate: parole chiave differenti, sinonimi, termini tecnici, il "
        "nome del fenomeno invece di quello comune, e — se le fonti plausibili sono "
        "internazionali, o se il libro è in inglese — anche in inglese. Brevi, da motore di "
        "ricerca, non frasi discorsive. Non ripetere le già provate."
    )
    utente = (
        f"## Il punto su cui cercare altro\n{question.text}\n\n"
        f"## Il capitolo\n{chapter.get('title')}: {chapter.get('does')}\n\n"
        f"## Già provate\n" + ("\n".join(f"- {q}" for q in tried) or "(niente)")
        + f"\n\n## Giro numero\n{round}\n\n"
        f"Lingua del libro: {language_label(state.language) or 'italiano'}."
    )
    try:
        risposta = _quick(client).chat_json(
            [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
            schema=REFORMULATE_SCHEMA, model=model,
        )
        nuove = [str(q).strip() for q in (risposta.get("queries") or []) if str(q).strip()]
    except Exception as exc:
        nuove = []
        if errors is not None:  # non si ingoia: chi chiama sa che il passo è da riprovare
            errors.append(f"riformulare le interrogazioni: {exc}")
    nuove = [q for q in dict.fromkeys(nuove) if _norm(q) not in {_norm(t) for t in tried}]
    if not nuove:
        # Il modello non ha risposto: una variante meccanica è meglio di niente, e se non porta
        # risultati nuovi il giro si ferma da sé.
        base = question.text.strip()
        nuove = [f"{base} fonte originale", f"{base} source"][:2 if round > 1 else 1]
        nuove = [q for q in nuove if _norm(q) not in {_norm(t) for t in tried}]
    return nuove[:4]


def _relevant(testo: str, questions: list[store.Question], chars: int) -> str:
    """I passaggi di un testo lungo che più somigliano alle questioni, in ordine di lettura.

    Un documento dell'autore può avere centinaia di pagine: mandare al modello le prime
    righe vorrebbe dire non vederci mai la cifra cercata.
    """
    if len(testo) <= chars:
        return testo
    parole = {w for q in questions for w in re.findall(r"\w{4,}", q.text.lower())}
    brani = [b.strip() for b in re.split(r"\n\s*\n", testo) if b.strip()]
    punteggio = [(sum(1 for w in set(re.findall(r"\w{4,}", b.lower())) if w in parole), i)
                 for i, b in enumerate(brani)]
    scelti, usati = [], 0
    for _, i in sorted(punteggio, key=lambda x: (-x[0], x[1])):
        if usati + len(brani[i]) > chars and scelti:
            continue
        scelti.append(i)
        usati += len(brani[i])
        if usati >= chars:
            break
    return "\n\n".join(brani[i][:chars] for i in sorted(scelti))


def resolve_with_material(state: ForgeState, client: SupportsChat,
                          questions: list[store.Question], pages: list[store.PageRead],
                          *, model: str | None = None,
                          errors: list[str] | None = None) -> dict[str, store.Finding]:
    """Quali questioni il materiale risolve davvero, e con quale fonte.

    Una questione si risolve solo se il materiale dice la cosa **e** dice da dove viene: una
    fonte che regge ha un autore, un'organizzazione o un titolo, e un anno. Il modello non può
    metterne una sua: se non c'è, la questione resta com'è.
    """
    if not questions or not pages:
        return {}
    materiale = "\n\n".join(
        f"### [{i}] {p.title}\n{p.url}\n{_relevant(p.text, questions, 3500)}"
        for i, p in enumerate(pages[:6], 1))
    elenco = "\n".join(f"- {q.id}: {q.text}" for q in questions)
    sistema = (
        "Ti do alcuni contrasti segnalati dalla ricerca fra quanto un libro afferma e fatti che "
        "sembravano comprovati, e del materiale appena trovato. Non sei un revisore: non dai "
        "ragione né torto al libro. Per ciascun contrasto dì se il materiale lo CHIARISCE con una "
        "fonte vera (per esempio: il fatto è diverso da come sembrava, oppure c'è una fonte che "
        "sostiene la posizione del libro). Deve permettere di risalire alla fonte (autore, ente o "
        "titolo, e anno). Se sì: resolved=true, con l'affermazione, il dettaglio con le parole e "
        "le cifre esatte, il tipo di fonte (A revisione sistematica · B studio singolo o "
        "statistica ufficiale · C fonte secondaria o divulgativa · D testimonianza, esperienza, "
        "post · E congettura o idea di un autore), la fonte e l'indirizzo della pagina. Se il "
        "materiale non basta, o dice altro: resolved=false e gli altri campi a stringa vuota "
        "(\"\"), mai omessi. NON inventare fonti, cifre, autori o anni: una citazione "
        "inventata è la cosa più dannosa che si possa mettere in un libro stampato. Restituisci "
        "una voce per ogni contrasto. "
        f"Scrivi in {language_label(state.language) or 'italiano'}."
    )
    utente = f"## Contrasti\n{elenco}\n\n## Materiale\n{materiale}"
    try:
        risposta = client.chat_json(
            [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
            schema=RESOLVE_SCHEMA, model=model,
        )
    except Exception as exc:
        if errors is not None:  # non si ingoia: chi chiama sa che il passo è da riprovare
            errors.append(f"leggere il materiale: {exc}")
        return {}
    validi = {q.id for q in questions}
    indirizzi = {p.url for p in pages}
    risolte: dict[str, store.Finding] = {}
    for voce in (risposta.get("resolutions") or []) if isinstance(risposta, dict) else []:
        if not isinstance(voce, dict) or voce.get("resolved") is not True:
            continue
        ident = str(voce.get("question_id") or "")
        fonte = str(voce.get("source") or "").strip()
        if ident not in validi or not fonte:
            continue  # niente fonte, niente risoluzione
        url = str(voce.get("url") or "").strip()
        if not url or (url not in indirizzi and not url.startswith("http")):
            url = pages[0].url
        risolte[ident] = store.Finding(
            claim=str(voce.get("claim") or "").strip(), detail=str(voce.get("detail") or ""),
            level=str(voce.get("level") or ""), source=fonte, url=url)
    return risolte


def apply_resolution(data: store.ChapterData, question: store.Question,
                     finding: store.Finding) -> None:
    """Una questione risolta: l'affermazione entra nel punto e la questione si chiude con la fonte."""
    punto = data.point(question.point) or (data.points[0] if data.points else None)
    if punto is not None:
        if not finding.claim:
            finding.claim = question.text
        if not any(_norm(f.claim) == _norm(finding.claim) for f in punto.findings):
            punto.findings.append(finding)
    question.status = store.RISOLTA
    question.outcome = {"kind": store.OUT_SOURCE, "text": finding.claim,
                        "source": finding.source, "url": finding.url}


def deepen_question(
    state: ForgeState,
    client: SupportsChat,
    settings: ResearchSettings,
    data: store.ChapterData,
    question: store.Question,
    chapter: dict[str, Any],
    *,
    model: str | None = None,
    should_stop=None,
    note=None,
) -> None:
    """Approfondisce un punto segnalato: giri di ricerca per trovare **più materiale** su di esso.

    A ogni giro più risultati e più pagine del precedente. Si ferma quando la fonte si trova,
    quando i giri sono finiti (``settings.deepen_rounds``) o quando un giro non porta **nessun
    risultato nuovo**: insistere sullo stesso motore con le stesse risposte è lavoro senza ritorno.
    Quello che resta irrisolto passa all'autore. Non limita il ragionamento del modello.
    """
    punto = data.point(question.point) or (data.points[0] if data.points else None)
    if punto is None:
        question.status = store.CHIESTA
        return
    provate = [q for a in question.attempts for q in a.queries] + list(punto.queries)
    errori: list[str] = []  # i passi del modello che sono falliti: da riprovare, non da ingoiare
    question.step_failed = ""
    for giro in range(1, max(0, int(settings.deepen_rounds)) + 1):
        if should_stop and should_stop():
            question.status = store.APERTA  # interrotta: resta da approfondire
            question.step_failed = "; ".join(dict.fromkeys(errori))
            return
        question.status = store.IN_APPROFONDIMENTO
        if note:
            note(("deepen", data.number, giro, settings.deepen_rounds))
        interrogazioni = reformulate(state, client, question, chapter, provate, round=giro,
                                     model=model, errors=errori)
        provate.extend(interrogazioni)
        largo = settings.model_copy(update={
            "max_results": settings.max_results * (1 + giro),
            "max_pages": settings.max_pages + 2 * giro,
        })
        visti = data.all_urls()
        tentativo = store.Attempt(round=giro, queries=list(interrogazioni))
        nuove_pagine: list[store.PageRead] = []
        for interrogazione in interrogazioni:
            try:
                risultati, pagine = gather(interrogazione, largo, read=largo.max_pages,
                                           page=giro - 1, skip=visti)
            except SearchError as exc:
                if str(exc) not in data.warnings:
                    data.warnings.append(str(exc))
                continue
            tentativo.found += len(risultati)
            aggiunti = _add_unique(punto.results, _hits(risultati))
            tentativo.new += len(aggiunti)
            sessione = jevmod.session(settings)
            assess_pages(pagine, topic_for(state, punto.text), settings, client=client,
                         model=model, context=judge_context(state, chapter, punto.text),
                         jev=sessione)
            _flush_jev(data, sessione)
            for p in _read(pagine):
                if norm_url(p.url) not in punto.read_urls():
                    punto.pages.append(p)
                    if p.status == "":
                        nuove_pagine.append(p)
        punto.queries.extend(q for q in interrogazioni if q not in punto.queries)
        question.attempts.append(tentativo)
        if not tentativo.new:
            break  # nessun risultato nuovo: un altro giro non porterebbe niente
        risolte = resolve_with_material(state, client, [question], nuove_pagine, model=model,
                                        errors=errori)
        if question.id in risolte:
            apply_resolution(data, question, risolte[question.id])
            return
    question.status = store.CHIESTA
    question.step_failed = "; ".join(dict.fromkeys(errori))


def deepen_chapter(project: BookProject, state: ForgeState, client: SupportsChat,
                   settings: ResearchSettings, data: store.ChapterData, chapter: dict[str, Any],
                   *, model: str | None = None, should_stop=None, note=None,
                   only: list[str] | None = None) -> None:
    """Approfondisce le questioni ancora da trattare di un capitolo, e salva a ogni questione.

    Le questioni su cui un passo del modello è **fallito** (non riuscito a riformulare le
    interrogazioni o a leggere il materiale) non si lasciano passare per «senza fonte»: si
    riprovano, solo quelle, fino a ``RETRY_ROUNDS`` volte; quelle che restano si dicono (avvisi
    del capitolo e ``note``), con l'elenco, e passano all'autore.
    """
    for domanda in list(data.questions):
        if domanda.closed or domanda.status == store.CHIESTA:
            continue
        if only is not None and domanda.id not in only:
            continue
        if should_stop and should_stop():
            return
        deepen_question(state, client, settings, data, domanda, chapter, model=model,
                        should_stop=should_stop, note=note)
        store.save_chapter(project.research_dir, data)

    def da_riprovare() -> list[store.Question]:
        return [q for q in data.questions if q.step_failed and not q.closed
                and (only is None or q.id in only)]

    for ciclo in range(1, RETRY_ROUNDS + 1):
        fallite = da_riprovare()
        if not fallite or (should_stop and should_stop()):
            break
        if note:
            note(f"⚠ Capitolo {data.number}: il modello ha sbagliato su {len(fallite)} "
                 f"{'questione' if len(fallite) == 1 else 'questioni'}; riprovo solo "
                 f"{'quella' if len(fallite) == 1 else 'quelle'} (tentativo {ciclo} di "
                 f"{RETRY_ROUNDS}).")
        for domanda in fallite:
            if should_stop and should_stop():
                break
            domanda.status = store.APERTA
            deepen_question(state, client, settings, data, domanda, chapter, model=model,
                            should_stop=should_stop, note=note)
            store.save_chapter(project.research_dir, data)

    rimaste = da_riprovare()
    if rimaste and not (should_stop and should_stop()):
        elenco = "; ".join(f"«{q.text[:60]}» ({q.step_failed[:80]})" for q in rimaste)
        avviso = (f"Capitolo {data.number}: dopo {RETRY_ROUNDS} tentativi il modello non è "
                  f"riuscito a trattare {len(rimaste)} "
                  f"{'questione' if len(rimaste) == 1 else 'questioni'}: {elenco}. "
                  "Non vuol dire che manchi la fonte: passano a te.")
        if avviso not in data.warnings:
            data.warnings.append(avviso)
        if note:
            note("⚠ " + avviso)
        store.save_chapter(project.research_dir, data)


# --------------------------------------------------------------------------- #
#  l'esploratore: includere un risultato, estendere un punto
# --------------------------------------------------------------------------- #
def include_result(project: BookProject, state: ForgeState, client: SupportsChat,
                   settings: ResearchSettings, data: store.ChapterData, chapter: dict[str, Any],
                   point_index: int, url: str, *, model: str | None = None,
                   memory: list[str] | None = None) -> store.PageRead:
    """Legge la pagina di un risultato e la aggiunge alle fonti del punto.

    Ne ricava le affermazioni come per ogni altra pagina; se resta qualcosa di non
    documentabile, diventa una questione del punto. Solleva ``SearchError`` se la pagina non si
    può leggere (web spento, indirizzo che non risponde).
    """
    punto = data.point(point_index)
    if punto is None:
        raise SearchError("Il punto non esiste in questo capitolo.")
    if not settings.enabled:
        raise SearchError("La ricerca sul web è spenta: accendila in Impostazioni per leggere "
                          "le pagine.")
    if norm_url(url) in punto.read_urls():
        esistente = next(p for p in punto.pages if norm_url(p.url) == norm_url(url))
        if esistente.status == "":
            return esistente
        if esistente.status == "non_leggibile":
            raise SearchError(f"Quella pagina non è leggibile: {esistente.note}.")
        # Non pertinente: l'ha scartata il programma, ma se l'autore la include vale la sua scelta.
        esistente.status, esistente.note = "", ""
        _sources_for_new_pages(state, client, settings, data, chapter, punto,
                               [Page(esistente.url, esistente.title, esistente.text)], memory,
                               model)
        store.save_chapter(project.research_dir, data)
        return esistente
    pagina = fetch(url, settings)
    if pagina is None:
        raise SearchError("Non sono riuscito a leggere quella pagina.")
    if not pagina.readable:
        raise SearchError(f"Quella pagina non è leggibile: {pagina.note}.")
    letta = _read([pagina])[0]
    punto.pages.append(letta)
    _sources_for_new_pages(state, client, settings, data, chapter, punto, [pagina], memory,
                           model)
    store.save_chapter(project.research_dir, data)
    return letta


def _sources_for_new_pages(state, client, settings, data, chapter, punto, pagine, memory,
                           model) -> None:
    pr = ResearchReport(queries=list(punto.queries), pages=usable(list(pagine)))
    voce = {**chapter, "point": punto.text if chapter.get("points") else "",
            "does": punto.text if chapter.get("points") else chapter.get("does")}
    sessione = jevmod.session(settings)
    try:
        dati = _sources_from_model(state, client, pr, voce, memory or [], model=model,
                                   jev=sessione)
    finally:
        _flush_jev(data, sessione)
    data.warnings.extend(w for w in pr.warnings if w not in data.warnings)
    esistenti = {_norm(f.claim) for f in punto.findings}
    for f in _findings(dati):
        if _norm(f.claim) not in esistenti:
            punto.findings.append(f)
    for domanda in _add_questions(data, punto, list(dati.get("open_questions") or []),
                                  invented_terms(state)):
        domanda.status = store.APERTA


def extend_point(project: BookProject, state: ForgeState, client: SupportsChat,
                 settings: ResearchSettings, data: store.ChapterData, chapter: dict[str, Any],
                 point_index: int, *, model: str | None = None, memory: list[str] | None = None,
                 should_stop=None, note=None) -> int:
    """Estende la ricerca di un punto: interrogazioni nuove, più risultati. Ritorna i nuovi risultati."""
    punto = data.point(point_index)
    if punto is None:
        raise SearchError("Il punto non esiste in questo capitolo.")
    if not settings.enabled:
        raise SearchError("La ricerca sul web è spenta: accendila in Impostazioni per estendere "
                          "la ricerca.")
    giro = 1 + len(punto.queries) // 4  # più volte si estende, più si va lontano
    finta = store.Question(id="", text=punto.text, chapter=data.number, point=point_index)
    interrogazioni = reformulate(state, client, finta, chapter, list(punto.queries), round=giro,
                                 model=model)
    largo = settings.model_copy(update={"max_results": settings.max_results * 2,
                                        "max_pages": settings.max_pages + 2})
    visti = data.all_urls()
    nuove_pagine: list[Page] = []
    nuovi = 0
    for interrogazione in interrogazioni:
        if should_stop and should_stop():
            break
        try:
            risultati, pagine = gather(interrogazione, largo, read=largo.max_pages, skip=visti)
        except SearchError as exc:
            if str(exc) not in data.warnings:
                data.warnings.append(str(exc))
            continue
        nuovi += len(_add_unique(punto.results, _hits(risultati)))
        sessione = jevmod.session(settings)
        assess_pages(pagine, topic_for(state, punto.text), settings, client=client, model=model,
                     context=judge_context(state, chapter, punto.text), jev=sessione)
        _flush_jev(data, sessione)
        for p in _read(pagine):
            if norm_url(p.url) not in punto.read_urls():
                punto.pages.append(p)
                if p.status == "":
                    intero = next((x.text for x in pagine if norm_url(x.url) == norm_url(p.url)),
                                  p.text)
                    nuove_pagine.append(Page(p.url, p.title, max(intero, p.text, key=len)))
        from bookwriter.forge import blocked
        for pag in pagine:
            if not pag.readable and getattr(pag, "block_reason", ""):
                blocked.enqueue_blocked_page(
                    project.research_dir,
                    url=pag.url,
                    chapter=int(chapter.get("number", 0)),
                    point=punto.index + 1,
                    query=interrogazione,
                    reason=pag.block_reason,
                )
    punto.queries.extend(q for q in interrogazioni if q not in punto.queries)
    if nuove_pagine:
        _sources_for_new_pages(state, client, settings, data, chapter, punto, nuove_pagine,
                               memory, model)
        deepen_chapter(project, state, client, settings, data, chapter, model=model,
                       should_stop=should_stop, note=note,
                       only=[q.id for q in data.questions if q.status == store.APERTA])
    store.save_chapter(project.research_dir, data)
    return nuovi


def _append_sources_index(project: BookProject, dati: dict[str, Any]) -> None:
    """Tiene la bibliografia di lavoro: una voce per fonte, senza doppioni."""
    path = project.research_dir / "sources.md"
    esistenti = path.read_text(encoding="utf-8") if path.exists() else (
        "# Fonti\n\nLa bibliografia di lavoro: può contenere anche fonti consultate e non citate.\n"
    )
    nuove = []
    for f in dati.get("findings") or []:
        fonte = str(f.get("source") or "").strip()
        if not fonte or fonte in esistenti:
            continue
        url = str(f.get("url") or "").strip()
        nuove.append(f"- {fonte}" + (f" {url}" if url else ""))
    if nuove:
        path.write_text(esistenti.rstrip() + "\n" + "\n".join(dict.fromkeys(nuove)) + "\n",
                        encoding="utf-8")


def _slug(text: str) -> str:
    from bookwriter.forge.project import slugify

    return slugify(text)


def project_memory(db_path: Path, vectors_path: Path, client, query: str, *, k: int = 5
                   ) -> list[str]:
    """I brani del materiale già importato che parlano di un argomento.

    L'autore che ha caricato appunti, trascrizioni o una vecchia edizione ha già fatto metà
    della ricerca: cercarla altrove sarebbe uno spreco e un rischio.
    """
    try:
        from bookwriter.memory import search_chunks

        return [h.text for h in search_chunks(db_path, vectors_path, client, query, k=k)]
    except Exception:
        return []
