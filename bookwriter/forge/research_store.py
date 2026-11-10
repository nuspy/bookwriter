"""I dati della ricerca, in forma strutturata: la fonte da cui si rigenerano i file di testo.

La ricerca produce molto più di quello che la stesura legge: interrogazioni provate, risultati
trovati, pagine lette, affermazioni con il loro livello di prova, questioni rimaste aperte. Tutto
questo sta in JSON accanto ai ``.md`` di ``research/``:

* ``research/landscape.json`` — il panorama;
* ``research/NN_titolo.json`` — un capitolo, punto per punto.

I ``.md`` restano, perché li legge la stesura e perché un file di testo si apre ovunque, ma sono
una **resa**: si rigenerano dal JSON (:func:`render_chapter_md`, :func:`render_landscape_md`), e
chi li modifica a mano deve sapere che alla prossima passata si riscrivono.

Le **questioni aperte** sono oggetti con un loro percorso (:class:`Question`): nascono dalla
ricerca di un punto, passano dall'approfondimento automatico, e quelle che restano irrisolte vanno
all'autore, che le risolve con una fonte o decide come trattarle. Tutto quello che l'autore decide
si scrive qui, perché la stesura lo legga.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

#: Il tipo di fonte, come etichetta. Non è un filtro: ogni fonte, di qualunque tipo (anche un post
#: o una testimonianza), si tiene e si etichetta; il livello dice solo di che cosa si tratta.
EVIDENCE_LEVELS = {
    "A": "revisione sistematica, meta-analisi, esperimento replicato",
    "B": "studio singolo, statistica ufficiale",
    "C": "fonte secondaria o divulgativa",
    "D": "testimonianza, esperienza, aneddoto, post, forum",
    "E": "congettura, idea o modello di un autore",
}

from bookwriter.forge.websearch import norm_url  # noqa: E402

# stati di una questione
APERTA = "aperta"
IN_APPROFONDIMENTO = "in_approfondimento"
RISOLTA = "risolta"
CHIESTA = "chiesta_all_autore"
DECISA = "decisa"

#: Che tipo di contrasto ha trovato la ricerca. È un'etichetta per chi decide, non un verdetto.
CONTRAST_CATEGORIES = {
    "storica": "contraddizione con un fatto storico",
    "cronologia": "date o ordine degli eventi",
    "terminologia": "differenza di nomi o termini",
    "incertezza": "le fonti stesse non concordano",
    "fonte_debole": "la fonte del contrasto è debole",
    "fiction": "contraddizione con la storia del libro",
}

#: Gli stati in cui una questione non ha più bisogno di nessuno.
CHIUSE = (RISOLTA, DECISA)

# cosa ha deciso l'autore
OUT_SOURCE, OUT_PUBLISH, OUT_OMIT = "source", "publish", "omit"

#: Quanto testo di una pagina letta si conserva nel JSON: abbastanza per rileggerla e per
#: ricavarne affermazioni, non una copia del web.
PAGE_KEEP = 8000

#: le tappe della ricerca di un capitolo (vedi ``ChapterData.stage``)
STAGE_POINTS = "points"
STAGE_DEEPENING = "deepening"
STAGE_DONE = "done"


@dataclass
class Hit:
    """Un risultato di ricerca."""

    title: str = ""
    url: str = ""
    snippet: str = ""
    #: ``escluso`` = dominio escluso dalle impostazioni: registrato, mai scaricato.
    status: str = ""
    #: scartato dall'autore a fine ricerca: non conta per la stesura né per la revisione, ma resta
    #: sul disco e si può ripristinare.
    discarded: bool = False


@dataclass
class PageRead:
    """Una pagina (o un documento) letta per intero, con quello che se n'è tenuto."""

    url: str = ""
    title: str = ""
    text: str = ""
    #: da dove viene: ``web``, ``autore`` (indirizzo o documento forniti dall'autore).
    origin: str = "web"
    #: vuoto = va al modello; ``non_leggibile`` (muro di cookie, solo JavaScript, solo menu) o
    #: ``non_pertinente`` (parla d'altro): la pagina resta registrata ma non si passa al modello.
    status: str = ""
    #: il perché dello stato, e il conteggio di ciò che la pulizia ha tolto
    note: str = ""
    cleaning: str = ""
    #: scartata dall'autore (vedi ``Hit.discarded``)
    discarded: bool = False
    #: autore e data di pubblicazione dichiarati dalla pagina (per la bibliografia)
    author: str = ""
    published: str = ""


@dataclass
class Finding:
    """Un'affermazione documentabile."""

    claim: str = ""
    detail: str = ""
    level: str = ""
    source: str = ""
    url: str = ""
    #: da dove viene: vuoto = dalla ricerca; ``revisione`` = portata dalla discussione con l'autore.
    origin: str = ""
    #: come può servire al capitolo o alla scena (vuoto nei dati salvati prima del riorientamento).
    use: str = ""
    #: l'autore ha deciso, in revisione, che questo materiale non entra: la stesura non lo vede.
    excluded: bool = False
    #: il modello ha citato una fonte che fra le pagine lette non c'è: l'indirizzo era suo, non
    #: della ricerca. L'affermazione resta come materiale; la fonte non si cita.
    unverified: bool = False


@dataclass
class Attempt:
    """Un tentativo di risolvere una questione: la ricerca fatta e quel che ha portato."""

    round: int = 0
    queries: list[str] = field(default_factory=list)
    found: int = 0  # risultati visti
    new: int = 0  # di cui mai visti prima


@dataclass
class Question:
    """Un contrasto segnalato dalla ricerca (vedi ``kind``): cosa farne lo decide l'autore."""

    id: str = ""
    text: str = ""
    chapter: int = 0
    point: int = 0
    status: str = APERTA
    attempts: list[Attempt] = field(default_factory=list)
    #: l'esito: ``{"kind": "source"|"publish"|"omit", "text": ..., "source": ..., "url": ...}``.
    outcome: dict[str, str] = field(default_factory=dict)
    #: che tipo di contrasto è (vedi ``CONTRAST_CATEGORIES``): lo dice la card della decisione.
    category: str = ""
    #: ``contrasto`` = la ricerca ha trovato un fatto che sembra comprovato in contrasto con
    #: quanto l'autore scrive. Vuoto nei dati salvati prima del riorientamento (allora nascevano da
    #: ciò che il materiale «non documentava», anche per gli elementi d'invenzione).
    kind: str = ""
    #: scartata dall'autore: non è più una questione (non conta per la stesura né per la
    #: revisione), ma resta sul disco e si può ripristinare.
    discarded: bool = False
    #: l'ultimo passo del modello che è fallito su questa questione (riformulare le interrogazioni,
    #: leggere il materiale), o vuoto. Non vuol dire «senza fonte»: vuol dire «da riprovare».
    step_failed: str = ""

    @property
    def closed(self) -> bool:
        return self.status in CHIUSE or self.discarded

    def tried(self) -> int:
        return sum(len(a.queries) for a in self.attempts)

    def seen(self) -> int:
        return sum(a.found for a in self.attempts)


@dataclass
class PointData:
    """La ricerca di un punto del capitolo."""

    index: int = 0
    text: str = ""
    queries: list[str] = field(default_factory=list)
    results: list[Hit] = field(default_factory=list)
    pages: list[PageRead] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    #: il punto è stato cercato e letto fino in fondo. Un punto non riuscito, o rimasto a metà per
    #: un'interruzione, resta ``False``: «Riprendi la ricerca» riparte da lì.
    done: bool = False

    def urls(self) -> set[str]:
        return ({norm_url(h.url) for h in self.results} | {norm_url(p.url) for p in self.pages})

    def read_urls(self) -> set[str]:
        return {norm_url(p.url) for p in self.pages}


@dataclass
class ChapterData:
    """La ricerca di un capitolo: i suoi punti e le sue questioni."""

    number: int = 0
    title: str = ""
    does: str = ""
    date: str = ""
    points: list[PointData] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: a che punto è la ricerca del capitolo: ``points`` (si stanno cercando i punti),
    #: ``deepening`` (i punti sono fatti, si approfondiscono le questioni) o ``done``.
    #: Serve a «Riprendi la ricerca»: i capitoli ``done`` non si toccano.
    stage: str = "points"
    #: quanto si è speso con Jev per questo capitolo: ``calls``, ``input_tokens``,
    #: ``output_tokens``, ``seconds``, ``failures``, ``fallbacks`` (vuoto se Jev non è stato usato).
    jev: dict[str, Any] = field(default_factory=dict)

    def point(self, index: int) -> PointData | None:
        return next((p for p in self.points if p.index == index), None)

    def question(self, ident: str) -> Question | None:
        return next((q for q in self.questions if q.id == ident), None)

    def pending(self) -> list[Question]:
        return [q for q in self.questions if not q.closed]

    def all_urls(self) -> set[str]:
        visti: set[str] = set()
        for p in self.points:
            visti |= p.urls()
        return visti

    def discard_url(self, url: str, on: bool = True) -> int:
        """Scarta (o ripristina) un risultato: la riga, la pagina letta e il materiale che ne viene.

        Non cancella niente dal disco. Ritorna quanti elementi sono cambiati.
        """
        chiave, n = norm_url(url), 0
        for p in self.points:
            for h in p.results:
                if norm_url(h.url) == chiave and h.discarded != on:
                    h.discarded, n = on, n + 1
            for pg in p.pages:
                if norm_url(pg.url) == chiave and pg.discarded != on:
                    pg.discarded, n = on, n + 1
            for f in p.findings:
                if f.url and norm_url(f.url) == chiave and f.excluded != on:
                    f.excluded, n = on, n + 1
        return n

    def discard_all(self, on: bool = True) -> int:
        """Scarta (o ripristina) tutto il materiale trovato del capitolo."""
        n = 0
        for p in self.points:
            for h in p.results:
                if h.discarded != on:
                    h.discarded, n = on, n + 1
            for pg in p.pages:
                if pg.discarded != on:
                    pg.discarded, n = on, n + 1
            for f in p.findings:
                if f.excluded != on:
                    f.excluded, n = on, n + 1
        return n


@dataclass
class LandscapeData:
    """Il panorama: cosa esiste già."""

    date: str = ""
    queries: list[str] = field(default_factory=list)
    results: list[Hit] = field(default_factory=list)
    pages: list[PageRead] = field(default_factory=list)
    #: quello che il modello ne ha ricavato (concorrenti, programma implicito, lacune…).
    analysis: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  JSON <-> oggetti
# --------------------------------------------------------------------------- #
def _fields(cls) -> set[str]:
    return set(cls.__dataclass_fields__)


def _build(cls, data: Any):
    if not isinstance(data, dict):
        return cls()
    return cls(**{k: v for k, v in data.items() if k in _fields(cls)})


def _list(cls, items: Any) -> list:
    return [_build(cls, i) for i in (items or []) if isinstance(i, dict)]


def chapter_from_dict(data: dict[str, Any]) -> ChapterData:
    capitolo = _build(ChapterData, {k: v for k, v in data.items()
                                    if k not in ("points", "questions")})
    capitolo.points = []
    for p in data.get("points") or []:
        if not isinstance(p, dict):
            continue
        punto = _build(PointData, {k: v for k, v in p.items()
                                   if k not in ("results", "pages", "findings")})
        punto.results = _list(Hit, p.get("results"))
        punto.pages = _list(PageRead, p.get("pages"))
        punto.findings = _list(Finding, p.get("findings"))
        capitolo.points.append(punto)
    capitolo.questions = []
    for q in data.get("questions") or []:
        if not isinstance(q, dict):
            continue
        domanda = _build(Question, {k: v for k, v in q.items() if k != "attempts"})
        domanda.attempts = _list(Attempt, q.get("attempts"))
        capitolo.questions.append(domanda)
    if "stage" not in data:
        _infer_stage(capitolo)
    return capitolo


def point_failed(data: ChapterData, point: PointData) -> bool:
    """Se questo punto è rimasto indietro: il modello non ha saputo leggerne le fonti (c'è l'avviso
    «Punto N «…»: …» fra quelli del capitolo)."""
    prefisso = f"Punto {point.index + 1} «"
    return any(w.startswith(prefisso) for w in data.warnings)


def stuck_questions(data: ChapterData) -> list[Question]:
    """Le questioni aperte rimaste ferme: nessun tentativo di approfondimento, o un passo del
    modello fallito. Non quelle che l'autore ha già in mano (``chiesta``) né quelle scartate."""
    return [q for q in data.questions
            if not q.closed and (q.step_failed or (
                not q.attempts and q.status in (APERTA, IN_APPROFONDIMENTO)))]


def _infer_stage(data: ChapterData) -> None:
    """Un capitolo salvato prima delle tappe: si deduce da quello che c'è.

    Un punto è fatto se non ha l'avviso di fallimento; il capitolo è ``points`` se qualcuno non lo
    è, ``deepening`` se restano questioni ferme, altrimenti ``done``. Quanti punti la struttura ne
    prevede lo sa chi chiama (vedi ``research.chapter_state``).
    """
    for punto in data.points:
        punto.done = not point_failed(data, punto)
    if not all(p.done for p in data.points):
        data.stage = STAGE_POINTS
    elif stuck_questions(data):
        data.stage = STAGE_DEEPENING
    else:
        data.stage = STAGE_DONE


def landscape_from_dict(data: dict[str, Any]) -> LandscapeData:
    paesaggio = _build(LandscapeData, {k: v for k, v in data.items()
                                       if k not in ("results", "pages")})
    paesaggio.results = _list(Hit, data.get("results"))
    paesaggio.pages = _list(PageRead, data.get("pages"))
    return paesaggio


# --------------------------------------------------------------------------- #
#  su disco
# --------------------------------------------------------------------------- #
def chapter_stem(number: int, title: str) -> str:
    from bookwriter.forge.project import slugify

    return f"{number:02d}_{slugify(title)}"


def chapter_json_path(research_dir: Path, number: int, title: str) -> Path:
    return research_dir / f"{chapter_stem(number, title)}.json"


def chapter_md_path(research_dir: Path, number: int, title: str) -> Path:
    return research_dir / f"{chapter_stem(number, title)}.md"


def landscape_json_path(research_dir: Path) -> Path:
    return research_dir / "landscape.json"


def _write_atomic(path: Path, text: str) -> None:
    """Scrive in un file accanto e lo mette al posto dell'altro in un colpo solo: chi legge nello
    stesso momento (l'esploratore della finestra) vede il file di prima o quello nuovo, mai
    metà."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save_chapter(research_dir: Path, data: ChapterData) -> Path:
    """Scrive il JSON del capitolo e rigenera il suo ``.md``. Prima si scrive, poi si racconta."""
    research_dir.mkdir(parents=True, exist_ok=True)
    path = chapter_json_path(research_dir, data.number, data.title)
    _write_atomic(path, json.dumps(asdict(data), ensure_ascii=False, indent=2))
    _write_atomic(chapter_md_path(research_dir, data.number, data.title),
                  render_chapter_md(data))
    return path


def load_chapter(research_dir: Path, number: int, title: str) -> ChapterData | None:
    path = chapter_json_path(research_dir, number, title)
    if not path.exists():
        return None
    try:
        return chapter_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def load_all_chapters(research_dir: Path, outline: list[dict[str, Any]]) -> list[ChapterData]:
    """La ricerca dei capitoli della struttura, nell'ordine, quelli che ci sono."""
    trovati = []
    for c in outline:
        dati = load_chapter(research_dir, int(c.get("number") or 0), str(c.get("title") or ""))
        if dati is not None:
            trovati.append(dati)
    return trovati


def save_landscape(research_dir: Path, data: LandscapeData, md: str) -> Path:
    research_dir.mkdir(parents=True, exist_ok=True)
    _write_atomic(landscape_json_path(research_dir),
                  json.dumps(asdict(data), ensure_ascii=False, indent=2))
    path = research_dir / "00_landscape.md"
    _write_atomic(path, md)
    return path


def load_landscape(research_dir: Path) -> LandscapeData | None:
    path = landscape_json_path(research_dir)
    if not path.exists():
        return None
    try:
        return landscape_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
#  le questioni di tutto il libro
# --------------------------------------------------------------------------- #
def all_questions(research_dir: Path, outline: list[dict[str, Any]]) -> list[Question]:
    return [q for c in load_all_chapters(research_dir, outline) for q in c.questions]


def pending_questions(research_dir: Path, outline: list[dict[str, Any]]) -> list[Question]:
    return [q for q in all_questions(research_dir, outline) if not q.closed]


def decisions_text(data: ChapterData | None) -> str:
    """Per la stesura: cosa fare di ogni contrasto segnalato dalla ricerca.

    «Fonte trovata» con la fonte, «pubblica così» con la formulazione scelta, «tralascia» solo se
    l'autore l'ha deciso. **Senza decisione il contenuto resta com'è**: quello che l'autore scrive
    non lo toglie, non lo attenua e non lo smentisce nessun percorso automatico, nemmeno quando
    non ha fonti o ne ha di contrarie. Cosa farne si discute con lui (fase «Revisione»).
    """
    if data is None or not data.questions:
        return ""
    righe = []
    for q in data.questions:
        if q.discarded:
            continue
        esito = q.outcome or {}
        if q.status == RISOLTA or esito.get("kind") == OUT_SOURCE:
            fonte = esito.get("source") or esito.get("url") or "vedi la ricerca"
            riga = f"- «{q.text}» — fonte trovata: {fonte}"
            if esito.get("text"):
                riga += f" ({esito['text']})"
        elif esito.get("kind") == OUT_PUBLISH:
            riga = (f"- «{q.text}» — scrivi così (formulazione scelta dall'autore, in voce "
                    f"d'autore): {esito.get('text', '')}")
        elif esito.get("kind") == OUT_OMIT:
            riga = f"- «{q.text}» — l'autore ha deciso di non menzionarlo"
        else:
            riga = (f"- «{q.text}» — nessuna decisione ancora: nel libro resta come l'ha scritto "
                    "l'autore, nella sua voce, senza cautele né smentite")
        righe.append(riga)
    return "\n".join(righe)


# --------------------------------------------------------------------------- #
#  le rese in testo
# --------------------------------------------------------------------------- #
def render_chapter_md(data: ChapterData) -> str:
    """Il ``.md`` del capitolo: il materiale che legge la stesura. Si rigenera dal JSON."""
    oggi = data.date or date.today().isoformat()
    righe = [
        f"# Ricerca — capitolo {data.number}: {data.title}",
        "",
        f"*Raccolta il {oggi}.*",
        "",
    ]
    if data.jev.get("calls") or data.jev.get("failures"):
        j = data.jev
        riga = (f"*Giudizi rapidi con Jev: {int(j.get('calls', 0))} chiamate, "
                f"{int(j.get('input_tokens', 0))} token in ingresso, "
                f"{float(j.get('seconds', 0)):.1f} s")
        if j.get("failures") or j.get("fallbacks"):
            riga += (f"; {int(j.get('failures', 0))} richieste non riuscite, "
                     f"{int(j.get('fallbacks', 0))} volte si è tornati al modello locale")
        righe += [riga + ".*", ""]
    righe += [
        "## Materiale per il capitolo",
        "",
        "Dettagli, descrizioni, esperienze, citazioni e fonti trovati per questo capitolo. Servono "
        "a scrivere meglio: usali. Il livello di prova è solo un'etichetta di che tipo di fonte è, "
        "non un motivo per scartare.",
        "",
    ]
    trovate = [(p, f) for p in data.points for f in p.findings if not f.excluded]
    if trovate:
        for punto, f in trovate:
            livello = (f.level or "").strip().upper()[:1]
            righe += [f"### {f.claim}", "", f"{f.detail}", ""]
            if f.use:
                righe += [f"- **Come può servire:** {f.use}"]
            if len(data.points) > 1:
                righe += [f"- **Punto:** {punto.text}"]
            if livello:
                righe += [f"- **Tipo di fonte:** {livello} — "
                          f"{EVIDENCE_LEVELS.get(livello, 'non classificato')}"]
            righe += [
                f"- **Fonte:** {f.source}" + (f" — {f.url}" if f.url else "")
                + (" *(non verificata: non è fra le pagine lette, non citarla)*"
                   if f.unverified else ""),
                f"- **Consultata:** {oggi}",
            ]
            if f.origin == "revisione":
                righe.append("- **Origine:** aggiunta nella revisione con l'autore")
            righe.append("")
    else:
        righe += ["*(nessun materiale trovato: il capitolo si scrive comunque, dalla tesi e dalla "
                  "storia dell'autore, che restano com'è. Nessuna fonte non è un difetto.)*", ""]

    aperte = [q for q in data.questions if not q.closed]
    if aperte:
        righe += ["## Contrasti da discutere con l'autore", "",
                  "La ricerca ha trovato qualcosa che sembra in contrasto con quanto il libro "
                  "afferma. **Nel libro resta com'è stato scritto** finché l'autore non decide "
                  "diversamente.", ""]
        righe += [f"- {q.text}" + (f" *({CONTRAST_CATEGORIES.get(q.category, q.category)})*"
                                   if q.category else "") for q in aperte]
        righe.append("")

    decise = [q for q in data.questions if q.status == DECISA]
    if decise:
        righe += ["## Decisioni dell'autore", ""]
        for q in decise:
            esito = q.outcome or {}
            if esito.get("kind") == OUT_PUBLISH:
                righe.append(f"- «{q.text}» — scrivi così: {esito.get('text', '')}")
            else:
                righe.append(f"- «{q.text}» — l'autore ha deciso di non menzionarlo")
        righe.append("")

    righe += ["## Fonti consultate", ""]
    visti: set[str] = set()
    for p in data.points:
        for h in p.results:
            if h.discarded or h.status == "escluso":
                continue
            if norm_url(h.url) not in visti:
                visti.add(norm_url(h.url))
                righe.append(f"- [{h.title}]({h.url})")
    if not visti:
        righe.append("*(nessuna)*")
    if data.warnings:
        righe += ["", "## Avvertenze", ""] + [f"- {w}" for w in data.warnings]
    return "\n".join(righe) + "\n"


def _work_lines(c: dict[str, Any]) -> list[str]:
    """Un'opera simile, in righe. Legge anche i panorami salvati prima (``does``/``misses``)."""
    titolo = str(c.get("title", "")).strip()
    chi = ", ".join(x for x in (str(c.get("author", "")).strip(), str(c.get("year", "")).strip())
                    if x)
    testa = f"- **{titolo or '(senza titolo)'}**" + (f" ({chi})" if chi else "")
    if c.get("url"):
        testa += f" — {c['url']}"
    righe = [testa]
    if c.get("draw_from") or c.get("does"):
        righe.append(f"  - Da cui attingere: {c.get('draw_from') or c.get('does')}")
    citate = [str(x) for x in (c.get("cited_sources") or []) if str(x).strip()]
    if citate:
        righe.append("  - Fonti che cita: " + "; ".join(citate))
    if c.get("different"):
        righe.append(f"  - Dove il tuo libro è diverso: {c['different']}")
    return righe


def landscape_works(data: LandscapeData | None) -> list[dict[str, Any]]:
    """Le opere simili del panorama, nuove o di una volta, senza quelle che l'autore ha escluso."""
    if data is None:
        return []
    opere = data.analysis.get("works") or data.analysis.get("competitors") or []
    return [c for c in opere if isinstance(c, dict) and not c.get("excluded")]


def render_landscape_md(state_brief: str, data: LandscapeData) -> str:
    """Il ``.md`` del panorama, dal JSON: «opere simili da cui attingere»."""
    dati = data.analysis or {}
    oggi = data.date or date.today().isoformat()
    righe = [
        "# Opere simili da cui attingere",
        "",
        f"*Raccolto il {oggi}. Interrogazioni: " + "; ".join(data.queries) + "*",
        "",
        "Idee, immagini, scene, fonti e spunti trovati in opere vicine al libro. Servono ad "
        "arricchirlo: non dicono cosa il libro debba fare.",
        "",
        "## Le opere",
        "",
    ]
    opere = landscape_works(data)
    if opere:
        for c in opere:
            righe += _work_lines(c)
    else:
        righe.append("*(nessuna individuata con certezza — non se ne inventano)*")

    if fonti := [str(x) for x in (dati.get("sources") or []) if str(x).strip()]:
        righe += ["", "## Fonti e testi utili", ""] + [f"- {x}" for x in fonti]
    if idee := [str(x) for x in (dati.get("ideas") or []) if str(x).strip()]:
        righe += ["", "## Idee e spunti per il libro", ""] + [f"- {x}" for x in idee]
    if recente := str(dati.get("recent") or "").strip():
        righe += ["", "## Cosa è uscito di recente", "", recente]

    # I panorami salvati prima del riorientamento hanno altri campi: si leggono ancora, con parole
    # che descrivono e non prescrivono.
    if dati.get("implicit_syllabus"):
        righe += ["", "## Gli argomenti ricorrenti nel campo (panorama precedente)", "",
                  "Cosa trattano la maggior parte dei libri trovati. Descrive il campo, non dice "
                  "cosa il libro debba fare.", ""]
        righe += [f"- {v}" for v in dati["implicit_syllabus"]]
    if dati.get("gaps"):
        righe += ["", "## Cosa rimproverano i lettori (panorama precedente)", ""]
        righe += [f"- {v}" for v in dati["gaps"]]
    if novita := str(dati.get("novelty") or "").strip():
        righe += ["", "## Dove il taglio dell'autore è diverso (panorama precedente)", "", novita]
    if dati.get("outline_consequences"):
        righe += ["", "## Spunti per la struttura (panorama precedente)", ""]
        righe += [f"- {v}" for v in dati["outline_consequences"]]

    righe += ["", "---", "", "## Fonti consultate", ""]
    visti: set[str] = set()
    fonti_c = []
    for r in data.results:  # una fonte una volta sola, anche se due interrogazioni la trovano
        if norm_url(r.url) not in visti:
            visti.add(norm_url(r.url))
            fonti_c.append(f"- [{r.title}]({r.url}) — consultato il {oggi}")
    righe += fonti_c or ["*(nessuna)*"]
    if data.warnings:
        righe += ["", "## Avvertenze", ""] + [f"- {w}" for w in data.warnings]
    return "\n".join(righe) + "\n"


def landscape_for_writing(data: LandscapeData | None, limit: int = 4000) -> str:
    """Il panorama come materiale per chi scrive: opere simili, fonti, idee. Niente critiche.

    È la parte che la stesura legge (insieme al materiale del capitolo): serve ad attingere. Le
    opere che l'autore ha escluso in revisione non ci sono.
    """
    if data is None:
        return ""
    righe: list[str] = []
    for c in landscape_works(data):
        righe += _work_lines(c)
    dati = data.analysis or {}
    if fonti := [str(x) for x in (dati.get("sources") or []) if str(x).strip()]:
        righe += ["", "Fonti e testi utili:"] + [f"- {x}" for x in fonti]
    if idee := [str(x) for x in (dati.get("ideas") or []) if str(x).strip()]:
        righe += ["", "Idee e spunti:"] + [f"- {x}" for x in idee]
    testo = "\n".join(righe).strip()
    return testo if len(testo) <= limit else testo[:limit].rsplit("\n", 1)[0]
