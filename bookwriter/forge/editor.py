"""La revisione editoriale di un capitolo appena scritto: passaggi separati, ciascuno col suo compito.

La stessa chiamata non può inventare scene, rispettare la ricerca, tenere lo stile, raggiungere la
misura e accorgersi di essersi ripetuta: sono obiettivi in concorrenza, e vince quello più facile
da soddisfare — la misura, riempita con la stessa tesi. Qui, dopo la stesura:

1. **le misure** (:mod:`metrics`): dove si addensano le parole, quali paragrafi si somigliano,
   quali formule tornano in serie — per sapere dove guardare;
2. **il significato**, sul capitolo intero: ogni paragrafo dice che cosa afferma e con quale
   immagine, con le etichette delle scene prima; la stessa idea detta di nuovo senza novità, il
   commento che reinterpreta una scena appena mostrata, la metafora applicata a tutto e il
   dettaglio di ricerca che non serve all'azione si tolgono o si riscrivono **per paragrafo** —
   la correzione lessicale da sola non basta («danger» sparisce e il suo lavoro lo fa «filing»);
3. **la ridondanza**, scena per scena: i gruppi di paragrafi che svolgono la stessa funzione
   (tenere il primo sufficiente, fondere, sostituire con azione); lo spazio tolto si reinveste
   in indagine, collegamenti e conseguenze che il piano chiedeva e il testo non mostra;
4. **la continuità degli eventi** (:mod:`timeline`): il registro di chi è dove, a che ora, con
   quali oggetti, dietro quali porte; le contraddizioni bloccanti e importanti si correggono
   nelle scene coinvolte e **si ricontrollano** dopo la correzione;
5. **le fonti**: numeri, date e citazioni del testo confrontati col materiale assegnato, e la
   provenienza di ogni affermazione usata (tipo, sostegno, gruppi di fonti indipendenti) — solo
   un resoconto, niente nel testo; le invenzioni del racconto non si verificano mai;
6. **la griglia**, in controlli separati per categoria (racconto, indagine, stile): ogni voce
   passa o no **con una citazione** come prova; se cade una voce critica, **una** revisione
   mirata delle scene coinvolte; poi il registro degli eventi si rifà sul testo finale.

I controlli possono girare su un modello diverso da quello che ha scritto (``models.review``
nella configurazione): un giudizio dello stesso modello non è una verifica indipendente.

Su tutto, la **guardia di lunghezza**: nessun passaggio può togliere più di un decimo del testo che
riceve. Il resoconto va in ``output/editoriale/NN_titolo.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from bookwriter.forge import metrics as mx
from bookwriter.forge.project import BookProject, slugify

#: Nessun passaggio può accorciare di più il testo che riceve.
LENGTH_GUARD = 0.9
#: Una scena rivista in modo mirato deve restare in questa forbice della sua lunghezza.
REVISION_RANGE = (0.9, 1.2)
#: Quanti gruppi di ridondanza si trattano per scena, quante scene si rivedono dopo la griglia.
MAX_GROUPS = 5
MAX_REVISED_SCENES = 2
#: Quante scene si rivedono per la continuità degli eventi.
MAX_CONTINUITY_SCENES = 3
#: Quanti paragrafi si riscrivono, al massimo, per la ripetizione di significato.
MAX_MEANING_OPS = 24
#: In quanti paragrafi può tornare la stessa immagine-guida prima di diventare un sistema.
IMAGE_LIMIT = 3
#: Oltre questa parte dei paragrafi del capitolo, un'immagine non è più una metafora: è il
#: vocabolario del mondo del libro (nella prova v3 del cap. 11 «archiviazione» era in 135
#: paragrafi, come nel resto del libro). Non si riscrive tutto: solo i commenti e le descrizioni
#: dove pesa di più, al più :data:`IMAGE_OPS_PER_FAMILY`.
IMAGE_VOCABULARY_SHARE = 0.35
IMAGE_OPS_PER_FAMILY = 6
#: Il capitolo può crescere fino a qui rispetto alla sua misura, non oltre: lo spazio si
#: reinveste solo se c'è (la prova v3 è passata da 8.163 a 9.047 parole su 7.000).
GROWTH_CAP = 1.1

KEEP, DELETE, MERGE, DRAMATIZE = "tieni", "elimina", "fondi", "drammatizza"

#: Le voci della griglia, per categoria: ogni categoria è un controllo a sé (una chiamata), perché
#: un giudizio complessivo dello stesso modello che ha scritto non è una verifica. Le voci
#: critiche bloccano il capitolo. La continuità non è qui: la controlla il registro degli eventi.
GRID_GROUPS: dict[str, list[tuple[str, str, bool]]] = {
    "racconto": [
        ("funzione", "Il capitolo fa avanzare la trama e il puzzle, con un cambiamento verificabile?",
         True),
        ("struttura", "Ha obiettivo, progressione, svolta e chiusura?", False),
        ("scene", "Gli eventi sono messi in scena e non riassunti come in un saggio?", True),
        ("variabili", ("Ogni scena cambia davvero, nel testo, qualcosa di concreto (conoscenza, "
                       "rischio, possesso, rapporto, destinazione, fuga), e non la stessa cosa di "
                       "un'altra scena?"), True),
        ("ridondanza", ("Le idee si ripetono senza novità (stessa tesi in parole o metafore "
                        "diverse)? Passa se NO."), True),
        ("protagonista", ("Il protagonista agisce, sceglie e paga un prezzo (almeno una "
                          "decisione significativa)?"), True),
        ("mistero", "Il patto col lettore regge (nessuna rivelazione anticipata)?", False),
        ("chiusura", ("La fine crea una necessità concreta per il capitolo dopo, non una frase "
                      "tematica?"), True),
    ],
    "indagine": [
        ("indizi", "Le deduzioni derivano da elementi concreti, con una catena comprensibile?",
         False),
        ("ragionamento", ("Per ogni scoperta importante il lettore vede cosa il protagonista "
                          "osserva, come lo interpreta, quale alternativa scarta e perché agisce "
                          "così (es. perché prende un documento invece di fotografarlo)?"), True),
        ("dettagli", ("I dettagli storici, tecnici e amministrativi servono alla scena (un "
                      "enigma, una decisione, un rapporto di forza, un seme per dopo) e non sono "
                      "lì solo perché erano nel materiale?"), False),
    ],
    "stile": [
        ("fidarsi", ("Dopo un'azione o un dialogo, il testo evita di spiegare e reinterpretare "
                     "quello che la scena ha già mostrato?"), True),
        ("metafore", ("Le immagini sono selettive, senza una metafora-guida applicata a tutto "
                      "(documenti, porte, persone, strade)?"), False),
        ("ritmo", "Alterna scoperta, conflitto e azione, senza blocchi lunghi immobili?", False),
        ("dialogo", "Dove ci sono più personaggi, parlano con voci distinte?", False),
        ("atmosfera", "È evocativa senza diventare manierata?", False),
        ("lingua", "La lingua è idiomatica e coerente?", False),
    ],
}
#: Tutte le voci, in un elenco (per chi le cerca per chiave).
GRID: list[tuple[str, str, bool]] = [v for voci in GRID_GROUPS.values() for v in voci]


# --------------------------------------------------------------------------- #
#  il capitolo come scene e paragrafi
# --------------------------------------------------------------------------- #
@dataclass
class Section:
    heading: str  #: la riga ``## …`` (vuota per il testo prima della prima sezione)
    paragraphs: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(self.paragraphs)

    @property
    def words(self) -> int:
        return mx.count_words(self.text)


@dataclass
class Chapter:
    head: str = ""
    sections: list[Section] = field(default_factory=list)
    notes: str = ""  #: la bibliografia in fondo, intatta

    def render(self) -> str:
        pezzi = [self.head.strip()] if self.head.strip() else []
        for s in self.sections:
            blocco = "\n\n".join(([s.heading] if s.heading else []) + s.paragraphs)
            if blocco.strip():
                pezzi.append(blocco)
        if self.notes.strip():
            pezzi.append(self.notes.strip())
        return "\n\n".join(pezzi).strip() + "\n"

    @property
    def words(self) -> int:
        return sum(s.words for s in self.sections)


_NOTES = re.compile(r"(?im)^##\s+(?:Note bibliografiche|Bibliograph\w*|References|Fonti)\s*$")


def parse(text: str) -> Chapter:
    m = _NOTES.search(text or "")
    corpo, note = (text[:m.start()], text[m.start():]) if m else (text or "", "")
    cap = Chapter(notes=note)
    corrente: Section | None = None
    for blocco in re.split(r"\n\s*\n", corpo):
        b = blocco.strip()
        if not b:
            continue
        righe = b.splitlines()
        if righe[0].startswith("# ") and not cap.sections and corrente is None:
            cap.head = righe[0]
            resto = "\n".join(righe[1:]).strip()
            if resto:
                corrente = Section("")
                cap.sections.append(corrente)
                corrente.paragraphs.append(resto)
            continue
        if righe[0].startswith("## "):
            corrente = Section(righe[0])
            cap.sections.append(corrente)
            resto = "\n".join(righe[1:]).strip()
            if resto:
                corrente.paragraphs.append(resto)
            continue
        if corrente is None:
            corrente = Section("")
            cap.sections.append(corrente)
        corrente.paragraphs.append(b)
    return cap


# --------------------------------------------------------------------------- #
#  il resoconto
# --------------------------------------------------------------------------- #
@dataclass
class EditorialReport:
    chapter: int = 0
    title: str = ""
    trial: bool = False
    words_draft: int = 0
    words_final: int = 0
    budget: int = 0
    plan: str = ""
    metrics_before: str = ""
    metrics_after: str = ""
    meaning: list[str] = field(default_factory=list)
    redundancy: list[str] = field(default_factory=list)
    continuity: list[str] = field(default_factory=list)
    #: il registro degli eventi del testo finale, in tabella
    timeline: str = ""
    sources: list[str] = field(default_factory=list)
    grid: list[str] = field(default_factory=list)
    failed_critical: list[str] = field(default_factory=list)
    revised: list[str] = field(default_factory=list)
    guards: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        """Resta una voce critica della griglia non superata: il capitolo va riguardato."""
        return bool(self.failed_critical)

    def markdown(self) -> str:
        quando = datetime.now().strftime("%Y-%m-%d %H:%M")  # noqa: DTZ005 - ora locale, per chi legge
        stato = ("⚠ DA RIVEDERE: " + ", ".join(self.failed_critical)) if self.failed_critical \
            else "✓ griglia superata nelle voci critiche"
        variazione = mx.length_drift(self.words_draft, self.words_final)
        righe = [
            f"# Revisione editoriale — Capitolo {self.chapter}: {self.title}"
            + (" (prova)" if self.trial else ""), "",
            f"_{quando}_ · {stato}", "",
            f"**Parole:** bozza {self.words_draft} → finale {self.words_final} "
            f"({variazione:+.0%})" + (f" · misura prevista {self.budget}" if self.budget else ""),
            "",
        ]
        if self.plan:
            righe += ["## Il piano delle scene", "", self.plan, ""]
        righe += ["## Misure della bozza", "", self.metrics_before or "—", "",
                  "## Misure del testo finale", "", self.metrics_after or "—", ""]
        if self.timeline:
            righe += ["## Il registro degli eventi (testo finale)", "", self.timeline, ""]
        for titolo, voci in (("Ripetizione di significato", self.meaning),
                             ("Ridondanza", self.redundancy),
                             ("Continuità degli eventi", self.continuity),
                             ("Fonti", self.sources), ("Griglia di qualità", self.grid),
                             ("Revisione mirata", self.revised),
                             ("Guardia di lunghezza", self.guards),
                             ("Passaggi non riusciti", self.errors)):
            righe += [f"## {titolo}", ""]
            righe += [f"- {v}" for v in voci] if voci else ["- niente da segnalare"]
            righe.append("")
        return "\n".join(righe).rstrip() + "\n"


def report_path(project: BookProject, number: int, title: str, *, trial: bool = False):
    prefisso = "prova_" if trial else ""
    return project.editorial_dir / f"{prefisso}{number:02d}_{slugify(title)}.md"


_DA_RIVEDERE = re.compile(r"⚠ DA RIVEDERE: (.+?)\s*$", re.MULTILINE)


def review_flags(project: BookProject) -> dict[int, list[str]]:
    """I capitoli scritti che la revisione editoriale ha lasciato da riguardare, con le voci
    critiche della griglia non superate. Si leggono dai resoconti su disco, prove escluse.

    Vale solo il resoconto dello stesso file del capitolo (stesso numero e stesso titolo): un
    resoconto rimasto da un capitolo rinumerato o rifatto con un altro titolo non conta.
    """
    if not project.editorial_dir.exists():
        return {}
    capitoli = {p.stem for p in project.chapter_files()}
    segnati: dict[int, list[str]] = {}
    for path in sorted(project.editorial_dir.glob("*.md")):
        if path.stem not in capitoli or not (numero := re.match(r"^(\d+)", path.stem)):
            continue
        try:
            testa = path.read_text(encoding="utf-8")[:2000]
        except OSError:
            continue
        if voce := _DA_RIVEDERE.search(testa):
            segnati[int(numero.group(1))] = [v.strip() for v in voce.group(1).split(", ")
                                             if v.strip()]
    return segnati


# --------------------------------------------------------------------------- #
#  2. la ridondanza
# --------------------------------------------------------------------------- #
_GROUPS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"groups": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"paragraphs": {"type": "array", "items": {"type": "integer"}},
                       "function": {"type": "string"}, "keep": {"type": "integer"},
                       "action": {"type": "string"}, "why": {"type": "string"}},
        "required": ["paragraphs", "function", "keep", "action", "why"]}}},
    "required": ["groups"],
}

_REDUNDANCY_SYSTEM = (
    "Sei un editor strutturale specializzato nella ridondanza semantica. Ricevi UNA scena di un "
    "capitolo, a paragrafi numerati. Trova i gruppi di paragrafi che svolgono LA STESSA "
    "FUNZIONE senza aggiungere niente: ripetono la stessa conclusione, usano metafore diverse "
    "per dire la stessa cosa, rispiegano un indizio già interpretato, riassumono quello che è "
    "appena successo, dichiarano che il pericolo cresce senza che la situazione cambi, ripetono "
    "in serie le stesse parole-simbolo o la stessa costruzione («non era X: era Y»), descrivono "
    "come concetto quello che andrebbe messo in scena.\n\n"
    "Per ogni gruppo: 'paragraphs' (i numeri), 'function' (la funzione ripetuta, in una riga), "
    "'keep' (il numero della prima occorrenza sufficiente, da tenere) e 'action':\n"
    "- 'elimina': gli altri paragrafi del gruppo non servono, si tolgono;\n"
    "- 'fondi': gli altri contengono un dettaglio utile, si fondono nel paragrafo da tenere;\n"
    "- 'drammatizza': al posto degli altri va un passaggio di scena — azione, dialogo, "
    "decisione, conseguenza — che metta in atto quello che i paragrafi si limitano a dire;\n"
    "- 'tieni': la ripetizione è voluta e fa evolvere il motivo (allora non è un gruppo).\n"
    "Non segnalare una parola chiave solo perché ricorre: un motivo può tornare quando cambia "
    "significato. Se la scena non ha ridondanze, 'groups' è vuoto. Al massimo cinque gruppi."
)


def _numbered(paragrafi: list[str], segnalati: set[int] | None = None) -> str:
    return "\n\n".join(f"[{i + 1}]{' ⚑' if segnalati and i in segnalati else ''} {p}"
                       for i, p in enumerate(paragrafi))


def _ask_text(client: Any, sistema: str, utente: str, *, model: str | None) -> str:
    from bookwriter.forge.writing import _clean_continuation

    return _clean_continuation(client.chat(
        [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
        model=model))


def _redundancy(client: Any, sezione: Section, scena: Any, pack: Any, segnalati: set[int],
                report: EditorialReport, *, model: str | None, language: str,
                cap_corrente: Chapter | None = None) -> None:
    """Toglie le ridondanze di una scena, per paragrafo, reinvestendo lo spazio in azione."""
    if len(sezione.paragraphs) < 3:
        return
    prima = sezione.words
    contesto = []
    if scena is not None:
        contesto.append("# Il piano di questa scena\n" + scena.card())
    if pack is not None and pack.told:
        contesto.append(pack.told_block())
    utente = "\n\n".join([*contesto, "# La scena (⚑ = segnalato dalle misure)\n"
                          + _numbered(sezione.paragraphs, segnalati)])
    try:
        r = client.chat_json([{"role": "system", "content": _REDUNDANCY_SYSTEM},
                              {"role": "user", "content": utente}],
                             schema=_GROUPS_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001 - un passaggio non riuscito si dice, non ferma il libro
        report.errors.append(f"ridondanza, «{sezione.heading}»: {str(exc)[:160]}")
        return
    n = len(sezione.paragraphs)
    sostituzioni: dict[int, str] = {}
    via: set[int] = set()
    for g in (r.get("groups") or [])[:MAX_GROUPS] if isinstance(r, dict) else []:
        try:
            numeri = sorted({int(x) - 1 for x in g.get("paragraphs") or [] if 0 < int(x) <= n})
            tieni = int(g.get("keep") or 0) - 1
        except (TypeError, ValueError):
            continue
        azione = str(g.get("action") or "").strip().lower()
        if len(numeri) < 2 or azione == KEEP or tieni not in numeri:
            continue
        altri = [i for i in numeri if i != tieni and i not in via and i not in sostituzioni]
        if not altri:
            continue
        funzione = str(g.get("function") or "")
        if azione == MERGE:
            fuso = _ask_text(client, (
                "Fondi i paragrafi in UN SOLO paragrafo che tenga il primo e ne prenda soltanto i "
                "dettagli concreti nuovi, senza ripetere due volte lo stesso concetto. Stessa "
                f"voce, stessa lingua ({language}). Rispondi solo con il paragrafo."),
                "\n\n".join(sezione.paragraphs[i] for i in [tieni, *altri]), model=model)
            if fuso:
                sostituzioni[tieni] = fuso
                via.update(altri)
                report.redundancy.append(f"«{_short(sezione.heading)}» §{_ids(numeri)}: {funzione}"
                                         " → fusi")
        elif azione in (DELETE, DRAMATIZE):
            tolte = sum(mx.count_words(sezione.paragraphs[i]) for i in altri)
            # Lo spazio tolto si reinveste se il taglio è grande o se l'editor lo chiede: una
            # tesi detta tre volte diventa una cosa che succede, non un buco nel capitolo.
            budget_c = int(getattr(pack, "budget", 0) or 0)
            pieno = cap_corrente is not None and budget_c and \
                cap_corrente.words >= GROWTH_CAP * budget_c
            if (azione == DRAMATIZE or tolte > 0.1 * prima) and not pieno:
                passaggio = _ask_text(client, _dramatize_system(language), _dramatize_user(
                    sezione, tieni, altri, scena, funzione, int(tolte * 0.9)), model=model)
                if passaggio:
                    sostituzioni[altri[0]] = passaggio
                    via.update(altri[1:])
                    report.redundancy.append(
                        f"«{_short(sezione.heading)}» §{_ids(numeri)}: {funzione} → tenuto "
                        f"§{tieni + 1}, il resto sostituito da azione ({mx.count_words(passaggio)}"
                        f" parole al posto di {tolte})")
                    continue
            via.update(altri)
            report.redundancy.append(f"«{_short(sezione.heading)}» §{_ids(numeri)}: {funzione} → "
                                     f"tenuto §{tieni + 1}, tolti {len(altri)} paragrafi")
    if not sostituzioni and not via:
        return
    nuovi = [sostituzioni.get(i, p) for i, p in enumerate(sezione.paragraphs) if i not in via
             or i in sostituzioni]
    dopo = mx.count_words("\n\n".join(nuovi))
    budget = int(getattr(pack, "budget", 0) or 0)
    spazio = _room(cap_corrente, budget) if cap_corrente is not None else 10 ** 6
    if prima and dopo < LENGTH_GUARD * prima and spazio >= 60:
        report.guards.append(f"«{_short(sezione.heading)}»: la ridondanza toglieva "
                             f"{prima - dopo} parole su {prima}; reinvestite in scena")
        aggiunta = _reinvest(client, nuovi, scena, min(int(0.97 * prima) - dopo, spazio),
                             model=model, language=language)
        if aggiunta:
            nuovi.insert(max(0, len(nuovi) - 1), aggiunta)
    sezione.paragraphs = nuovi


def _dramatize_system(language: str) -> str:
    return (
        "Sei il romanziere di questo libro. Alcuni paragrafi di una scena ripetono un concetto "
        "già detto invece di farlo accadere. Scrivi il passaggio che li sostituisce: azione, "
        "dialogo, una scelta, una conseguenza concreta, presi dal piano della scena. Non "
        "ripetere il concetto, non spiegarlo: fallo succedere. Non introdurre fatti storici "
        "nuovi né fonti. Continuità perfetta con il paragrafo prima e quello dopo. "
        f"Lingua: {language}. Rispondi solo con il passaggio, in uno o più paragrafi, senza "
        "titolo."
    )


def _dramatize_user(sezione: Section, tieni: int, altri: list[int], scena: Any, funzione: str,
                    parole: int) -> str:
    primo = altri[0]
    prima = sezione.paragraphs[primo - 1] if primo > 0 else ""
    dopo_i = max(altri) + 1
    dopo = sezione.paragraphs[dopo_i] if dopo_i < len(sezione.paragraphs) else ""
    pezzi = [
        f"# Il concetto ripetuto\n{funzione}",
        f"# Il paragrafo che lo dice già (resta)\n{sezione.paragraphs[tieni]}",
        "# I paragrafi da sostituire\n" + "\n\n".join(sezione.paragraphs[i] for i in altri),
        f"# Il piano della scena\n{scena.card()}" if scena is not None else "",
        f"# Il paragrafo prima\n{prima}" if prima else "",
        f"# Il paragrafo dopo\n{dopo}" if dopo else "",
        f"# Misura\nCirca {max(80, parole)} parole.",
    ]
    return "\n\n".join(p for p in pezzi if p)


def _reinvest(client: Any, paragrafi: list[str], scena: Any, parole: int, *,
              model: str | None, language: str) -> str:
    if parole < 60:
        return ""
    elementi = ""
    if scena is not None:
        r = getattr(scena, "reasoning", {}) or {}
        elementi = "; ".join(f"{k}: {v}" for k, v in (
            ("ostacolo", scena.obstacle), ("indizio", scena.clue),
            ("come lo interpreta", r.get("interpretation", "")),
            ("alternativa che scarta", r.get("alternative", "")),
            ("perché agisce così", r.get("why", "")), ("decisione", scena.decision),
            ("conseguenza", scena.consequence),
            ("domanda nuova", getattr(scena, "next_question", ""))) if v)
    return _ask_text(client, _dramatize_system(language), "\n\n".join(p for p in (
        "# La scena com'è adesso\n" + "\n\n".join(paragrafi),
        f"# Quello che il piano chiede e la scena mostra poco\n{elementi}" if elementi else "",
        f"# Da scrivere\nUn passaggio di circa {parole} parole da inserire prima dell'ultimo "
        "paragrafo: rende chiari l'indagine (cosa il protagonista osserva, come ragiona, cosa "
        "scarta), i collegamenti e le conseguenze delle azioni, con azione e dialogo. Niente "
        "riassunti del già detto, niente commento sul significato.") if p), model=model)


def _short(heading: str) -> str:
    return heading.lstrip("# ").strip()[:60] or "senza titolo"


def _ids(numeri: list[int]) -> str:
    return ",".join(str(i + 1) for i in numeri)


# --------------------------------------------------------------------------- #
#  2b. il significato: la stessa idea, la stessa immagine, sul capitolo intero
# --------------------------------------------------------------------------- #
PARAGRAPH_KINDS = ("azione", "dialogo", "descrizione", "ragionamento", "commento")

_TAGS_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"paragraphs": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"n": {"type": "integer"},
                       "kind": {"type": "string", "enum": list(PARAGRAPH_KINDS)},
                       "idea": {"type": "string"}, "image": {"type": "string"},
                       "unused_detail": {"type": "boolean"}},
        "required": ["n", "kind", "idea", "image", "unused_detail"]}}},
    "required": ["paragraphs"],
}

_TAGS_SYSTEM = (
    "Sei un editor strutturale. Ricevi UNA scena di un capitolo a paragrafi numerati e le "
    "etichette già usate nelle scene prima. Per OGNI paragrafo:\n"
    "- 'kind': azione (succede qualcosa), dialogo, descrizione, ragionamento (il personaggio "
    "deduce da un indizio), commento (il narratore spiega o interpreta il significato di quello "
    "che è successo);\n"
    "- 'idea': in poche parole il SIGNIFICATO che il paragrafo afferma (es. «il sistema "
    "trasforma le persone in documenti»), non i fatti; se è la stessa idea di un'etichetta già "
    "usata, RIUSA ESATTAMENTE quell'etichetta; vuoto se il paragrafo racconta solo fatti;\n"
    "- 'image': la metafora-guida usata (es. «archiviazione»), riusando le etichette già viste; "
    "vuoto se non ce n'è una;\n"
    "- 'unused_detail': vero se il paragrafo accumula dettagli storici, tecnici o "
    "amministrativi che non servono all'azione della scena (né a un enigma, a una decisione, a "
    "un rapporto di forza, a un seme per dopo).\n"
    "Un paragrafo per voce, nell'ordine, con il suo numero in 'n'."
)


@dataclass
class _Tag:
    section: int
    index: int
    kind: str = ""
    idea: str = ""
    image: str = ""
    unused: bool = False


def _norm_label(testo: str) -> str:
    return " ".join(sorted(mx.content_words(testo or "")))[:80]


def _tag_sections(client: Any, cap: Chapter, piano: Any, report: EditorialReport, *,
                  model: str | None) -> list[_Tag]:
    etichette: dict[str, int] = {}
    immagini: dict[str, int] = {}
    tutte: list[_Tag] = []
    for si, sezione in enumerate(cap.sections):
        if len(sezione.paragraphs) < 2:
            continue
        scena = _scene_for(piano, si, cap)
        utente = "\n\n".join(p for p in (
            ("# Idee già affermate nelle scene prima (etichetta: volte)\n"
             + "\n".join(f"- {k}: {n}" for k, n in etichette.items())) if etichette else "",
            ("# Immagini-guida già usate (etichetta: volte)\n"
             + "\n".join(f"- {k}: {n}" for k, n in immagini.items())) if immagini else "",
            f"# Il piano di questa scena\n{scena.card()}" if scena is not None else "",
            f"# La scena\n{_numbered(sezione.paragraphs)}") if p)
        try:
            r = client.chat_json([{"role": "system", "content": _TAGS_SYSTEM},
                                  {"role": "user", "content": utente}],
                                 schema=_TAGS_SCHEMA, model=model)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"significato, «{_short(sezione.heading)}»: {str(exc)[:160]}")
            continue
        for v in (r.get("paragraphs") or []) if isinstance(r, dict) else []:
            try:
                k = int(v.get("n") or 0) - 1
            except (TypeError, ValueError):
                continue
            if not 0 <= k < len(sezione.paragraphs):
                continue
            tag = _Tag(si, k, str(v.get("kind") or ""), str(v.get("idea") or "").strip(),
                       str(v.get("image") or "").strip(), bool(v.get("unused_detail")))
            tutte.append(tag)
            if tag.idea:
                etichette[tag.idea] = etichette.get(tag.idea, 0) + 1
            if tag.image:
                immagini[tag.image] = immagini.get(tag.image, 0) + 1
    return tutte


def _meaning_ops(tags: list[_Tag]) -> dict[tuple[int, int], tuple[str, str]]:
    """Cosa fare di ciascun paragrafo: ``(sezione, indice) → (operazione, motivo)``.

    - la stessa idea già affermata, in un commento o in una descrizione → togliere l'idea;
    - un commento che segue un'azione o un dialogo e ne reinterpreta il senso già affermato →
      togliere l'idea (fidarsi della scena);
    - un'immagine-guida oltre :data:`IMAGE_LIMIT` paragrafi → dirlo senza l'immagine;
    - un accumulo di dettagli che non servono all'azione → tenere solo quelli che servono.
    """
    ops: dict[tuple[int, int], tuple[str, str]] = {}
    viste: dict[str, tuple[int, int]] = {}
    for t in tags:
        chiave = _norm_label(t.idea)
        if not chiave:
            continue
        if chiave in viste:
            prima = viste[chiave]
            if t.kind in ("commento", "descrizione", "ragionamento"):
                ops[(t.section, t.index)] = (
                    "idea", f"«{t.idea}» è già stata detta (scena {prima[0] + 1}, "
                            f"§{prima[1] + 1})")
        else:
            viste[chiave] = (t.section, t.index)
    per_sezione: dict[int, list[_Tag]] = {}
    for t in tags:
        per_sezione.setdefault(t.section, []).append(t)
    for lista in per_sezione.values():
        lista.sort(key=lambda x: x.index)
        for prima, dopo in zip(lista, lista[1:]):
            if dopo.kind == "commento" and prima.kind in ("azione", "dialogo") and \
                    (dopo.section, dopo.index) not in ops and _norm_label(dopo.idea) and \
                    _norm_label(dopo.idea) == _norm_label(prima.idea):
                ops[(dopo.section, dopo.index)] = (
                    "idea", f"il paragrafo prima ha già mostrato «{dopo.idea}»: fidarsi della "
                            "scena")
    famiglie: dict[str, list[_Tag]] = {}
    for t in tags:
        if _norm_label(t.image):
            famiglie.setdefault(_norm_label(t.image), []).append(t)
    for lista in famiglie.values():
        if len(lista) <= IMAGE_LIMIT:
            continue
        # si tengono le prime due e l'ultima (il punto d'arrivo); le altre si dicono senza —
        # ma solo nei commenti e nelle descrizioni: nell'azione e nel dialogo l'immagine è dei
        # personaggi. Se l'immagine è il vocabolario del capitolo, se ne tocca solo una parte.
        tenute = {id(lista[0]), id(lista[1]), id(lista[-1])}
        candidati = [t for t in lista if id(t) not in tenute and (t.section, t.index) not in ops
                     and t.kind in ("commento", "descrizione")]
        vocabolario = len(lista) > IMAGE_VOCABULARY_SHARE * len(tags)
        if vocabolario:
            candidati = _spread(sorted(candidati, key=lambda x: x.kind != "commento"),
                                IMAGE_OPS_PER_FAMILY)
        else:
            candidati = candidati[:IMAGE_OPS_PER_FAMILY]
        for t in candidati:
            ops[(t.section, t.index)] = (
                "immagine", f"l'immagine «{t.image}» torna in {len(lista)} paragrafi"
                + (" (è il vocabolario del capitolo: tolta solo dove commenta)"
                   if vocabolario else ""))
    for t in tags:
        if t.unused and t.kind in ("descrizione", "commento") and (t.section, t.index) not in ops:
            ops[(t.section, t.index)] = ("dettagli", "dettagli che non servono all'azione")
    return ops


_PRIORITA = {"idea": 0, "dettagli": 1, "immagine": 2}


def _spread(tags: list[_Tag], limite: int) -> list[_Tag]:
    """Al più ``limite`` paragrafi, distribuiti fra le scene a giro invece che tutti nella prima."""
    per_sezione: dict[int, list[_Tag]] = {}
    for t in tags:
        per_sezione.setdefault(t.section, []).append(t)
    fuori: list[_Tag] = []
    while len(fuori) < limite and any(per_sezione.values()):
        for k in sorted(per_sezione):
            if per_sezione[k] and len(fuori) < limite:
                fuori.append(per_sezione[k].pop(0))
    return fuori


def ordered_ops(ops: dict[tuple[int, int], tuple[str, str]]) -> list[tuple[tuple[int, int],
                                                                         tuple[str, str]]]:
    """Le operazioni nell'ordine in cui conviene farle con un tetto: prima le idee ripetute,
    poi i dettagli, poi le immagini; dentro ogni tipo, a giro fra le scene."""
    fuori = []
    for tipo in sorted(_PRIORITA, key=_PRIORITA.get):
        del_tipo = sorted((k, v) for k, v in ops.items() if v[0] == tipo)
        per_sezione: dict[int, list] = {}
        for voce in del_tipo:
            per_sezione.setdefault(voce[0][0], []).append(voce)
        while any(per_sezione.values()):
            for k in sorted(per_sezione):
                if per_sezione[k]:
                    fuori.append(per_sezione[k].pop(0))
    return fuori


def _room(cap: Chapter, budget: int) -> int:
    """Quante parole si possono ancora aggiungere al capitolo (0 se è già oltre la misura)."""
    if not budget:
        return 10 ** 6
    return max(0, int(GROWTH_CAP * budget) - cap.words)


def _meaning_rewrite(client: Any, paragrafo: str, operazione: str, motivo: str, tag: _Tag,
                     scena: Any, *, model: str | None, language: str) -> str:
    if operazione == "idea":
        compito = (f"Togli da questo paragrafo l'affermazione «{tag.idea}», che il lettore "
                   f"conosce già ({motivo}). Tieni ogni fatto, azione, oggetto, battuta di "
                   "dialogo e dettaglio concreto. Se tolta quella non resta niente, rispondi "
                   "soltanto VUOTO.")
    elif operazione == "immagine":
        compito = (f"Riscrivi questo paragrafo senza l'immagine «{tag.image}» ({motivo}): di' la "
                   "stessa cosa in modo concreto e letterale, con gli oggetti e i gesti della "
                   "scena, più o meno nella stessa lunghezza.")
    else:
        compito = ("Tieni di questo paragrafo solo i dettagli che servono all'azione della scena "
                   "(a un enigma, a una decisione, a un rapporto di forza, a un seme per dopo) e "
                   "togli quelli che sono lì solo perché erano nel materiale di ricerca.")
    sistema = (f"Sei il romanziere di questo libro e rivedi UN paragrafo. {compito} Stessa voce, "
               f"stessa lingua ({language}). Non aggiungere fatti nuovi, fonti o cifre. Rispondi "
               "solo con il paragrafo.")
    utente = "\n\n".join(p for p in (
        f"# Il piano della scena\n{scena.card()}" if scena is not None else "",
        f"# Il paragrafo\n{paragrafo}") if p)
    return _ask_text(client, sistema, utente, model=model)


def _meaning(client: Any, cap: Chapter, piano: Any, report: EditorialReport, *,
             model: str | None, language: str, budget: int = 0) -> None:
    """La ripetizione di significato sul capitolo intero, per paragrafo, con la guardia."""
    tags = _tag_sections(client, cap, piano, report, model=model)
    if not tags:
        return
    ops = _meaning_ops(tags)
    per_tag = {(t.section, t.index): t for t in tags}
    prima = {si: s.words for si, s in enumerate(cap.sections)}
    fatte = 0
    toccate: set[int] = set()
    for (si, pi), (operazione, motivo) in ordered_ops(ops):
        if fatte >= MAX_MEANING_OPS:
            report.meaning.append(f"… altre {len(ops) - fatte} segnalazioni lasciate com'erano "
                                  f"(tetto di {MAX_MEANING_OPS} per capitolo)")
            break
        sezione = cap.sections[si]
        if pi >= len(sezione.paragraphs):
            continue
        nuovo = _meaning_rewrite(client, sezione.paragraphs[pi], operazione, motivo,
                                 per_tag[(si, pi)], _scene_for(piano, si, cap), model=model,
                                 language=language)
        fatte += 1
        if not nuovo:
            continue
        if nuovo.strip().upper().startswith("VUOTO"):
            sezione.paragraphs[pi] = ""
            esito = "tolto"
        else:
            sezione.paragraphs[pi] = nuovo
            esito = "riscritto"
        toccate.add(si)
        report.meaning.append(f"«{_short(sezione.heading)}» §{pi + 1}: {motivo} → {esito}")
    for si in sorted(toccate):
        sezione = cap.sections[si]
        sezione.paragraphs = [p for p in sezione.paragraphs if p.strip()]
        spazio = _room(cap, budget)
        if prima[si] and sezione.words < LENGTH_GUARD * prima[si] and spazio >= 60:
            aggiunta = _reinvest(client, sezione.paragraphs, _scene_for(piano, si, cap),
                                 min(int(0.97 * prima[si]) - sezione.words, spazio),
                                 model=model, language=language)
            if aggiunta:
                sezione.paragraphs.insert(max(0, len(sezione.paragraphs) - 1), aggiunta)
                report.guards.append(f"«{_short(sezione.heading)}»: lo spazio tolto alle "
                                     "ripetizioni è andato a indagine e conseguenze")


# --------------------------------------------------------------------------- #
#  3. la continuità
# --------------------------------------------------------------------------- #
def _events(client: Any, cap: Chapter, piano: Any, pack: Any, *, model: str | None) -> Any:
    from bookwriter.forge import timeline as tl

    return tl.build(client, [(s.heading, s.text) for s in cap.sections], pack, piano,
                    model=model, scene_for=lambda i: _scene_for(piano, i, cap))


def _fix_events(client: Any, cap: Chapter, piano: Any, pack: Any, report: EditorialReport, *,
                model: str | None, check_model: str | None, language: str, style: str) -> None:
    """Il registro degli eventi, le scene da correggere, e il controllo dopo la correzione."""
    cronologia = _events(client, cap, piano, pack, model=check_model)
    for r in cronologia.records:
        if r.error:
            report.errors.append(f"registro degli eventi, «{_short(r.heading)}»: {r.error}")
    gravi = cronologia.serious()
    for i in cronologia.issues:
        report.continuity.append(i.line())
    if not gravi:
        return
    per_scena: dict[int, list[str]] = {}
    for i in gravi:
        per_scena.setdefault(max(0, i.section), []).append(i.instruction())
    ordine = sorted(per_scena, key=lambda k: (-len(per_scena[k]), k))[:MAX_CONTINUITY_SCENES]
    for k in ordine:
        if k >= len(cap.sections):
            continue
        ok, come = _revise_section(client, cap, k, per_scena[k], _scene_for(piano, k, cap), pack,
                                   model=model, language=language, style=style, events=True)
        report.continuity.append(f"«{_short(cap.sections[k].heading)}»: "
                                 + ("corretta" if ok else "non corretta") + f", {come}")


# --------------------------------------------------------------------------- #
#  4. le fonti
# --------------------------------------------------------------------------- #
_NUMBERS = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2}|[0-9]{1,3}(?:[.,][0-9]{3})+|[0-9]+(?:[.,][0-9]+)?"
                      r"\s?%)\b")
_QUOTES = re.compile(r"“([^”]{25,300})”|\"([^\"\n]{25,300})\"|«([^»]{25,300})»")


def _sources(text: str, piano: Any, pack: Any, report: EditorialReport) -> None:
    """Numeri, date e citazioni del testo che non stanno nel materiale assegnato né nel quaderno:
    da guardare, non da togliere. Le invenzioni del racconto non si verificano."""
    if pack is None:
        return
    ids = piano.claim_ids() if piano is not None else [c.id for c in pack.claims]
    materiale = " ".join(f"{c.text} {c.detail}" for c in pack.claims if c.id in ids)
    canone = pack.notebook + " " + pack.decisions + " " + pack.review
    corpo = mx.prose_only(text)
    for numero in sorted(set(_NUMBERS.findall(corpo))):
        if numero not in materiale and numero not in canone:
            report.sources.append(f"«{numero}» non compare nel materiale assegnato: viene dal "
                                  "modello (verificare se è un fatto storico)")
    for m in _QUOTES.finditer(corpo):
        frase = next(g for g in m.groups() if g)
        # una battuta di dialogo è racconto; una citazione attribuita a un'opera no
        attribuita = re.search(r"(?:wrote|scrisse|scrive|according to|secondo|records?|"
                               r"registr|deposition|deposizione)\W", corpo[max(0, m.start() - 120):
                                                                          m.start()],
                               re.IGNORECASE)
        if attribuita and frase[:40] not in materiale:
            report.sources.append(f"citazione attribuita senza riscontro nel materiale: "
                                  f"«{frase[:80]}…»")
    usate = [c for c in pack.claims if c.id in ids]
    fonti = getattr(pack, "sources", {}) or {}
    for c in usate:
        if not c.verified:
            report.sources.append(f"{c.id} usata con fonte non verificata (non entra in "
                                  f"bibliografia): {c.text[:90]}")
    # la provenienza, affermazione per affermazione: tipo, sostegno, linee indipendenti
    provenienza = []
    for c in usate:
        titoli = [(fonti[x].title or fonti[x].site) for x in c.source_ids if x in fonti]
        gruppi = len(set(c.groups)) or (1 if titoli else 0)
        riga = (f"{c.id} ({c.claim_type or '?'}, sostegno {c.support or '?'}; {len(titoli)} "
                f"fonti, {gruppi} linee indipendenti): {c.text[:90]}"
                + (f" — {'; '.join(t[:50] for t in titoli[:3])}" if titoli else ""))
        if c.claim_type == "fatto_storico" and gruppi <= 1 and \
                c.support in ("debole", "non_verificato", "controverso", ""):
            riga += " ⚑ un fatto storico con una sola linea di fonti deboli"
        if c.claim_type in ("ipotesi", "interpretazione", "tradizione"):
            riga += " — nel racconto: convinzione o scoperta di un personaggio, o canone dell'autore"
        provenienza.append(riga)
    if len(report.sources) > 25:
        resto = len(report.sources) - 25
        report.sources = report.sources[:25] + [f"… e altre {resto} segnalazioni"]
    if provenienza:
        report.sources += ["Provenienza delle affermazioni usate:", *provenienza]


# --------------------------------------------------------------------------- #
#  5. la griglia, e la revisione mirata
# --------------------------------------------------------------------------- #
_GRID_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"key": {"type": "string"}, "pass": {"type": "boolean"},
                       "evidence": {"type": "string"},
                       "sections": {"type": "array", "items": {"type": "integer"}},
                       "fix": {"type": "string"}},
        "required": ["key", "pass", "evidence", "sections", "fix"]}}},
    "required": ["items"],
}


def _grid(client: Any, cap: Chapter, piano: Any, pack: Any, report: EditorialReport, *,
          model: str | None, language: str) -> list[tuple[str, list[int], str]]:
    """La griglia di qualità, un controllo per categoria. Ritorna le voci critiche cadute:
    (voce, sezioni, cosa correggere)."""
    cadute: list[tuple[str, list[int], str]] = []
    for categoria, voci in GRID_GROUPS.items():
        cadute += _grid_group(client, categoria, voci, cap, piano, pack, report, model=model,
                              language=language)
    return cadute


def _grid_group(client: Any, categoria: str, griglia: list[tuple[str, str, bool]], cap: Chapter,
                piano: Any, pack: Any, report: EditorialReport, *, model: str | None,
                language: str) -> list[tuple[str, list[int], str]]:
    voci = "\n".join(f"- {k}: {d}" + (" (CRITICA)" if crit else "") for k, d, crit in griglia)
    sistema = (
        f"Sei l'editor che controlla UN aspetto di un capitolo di romanzo: {categoria}. Valuta "
        "ogni voce con 'pass' vero o falso, e in 'evidence' una CITAZIONE breve dal testo (al "
        "massimo 25 parole) che lo prova — per una voce che non passa, il passo che mostra il "
        "difetto. In 'sections' i numeri delle sezioni coinvolte (vuoto se riguarda tutto il "
        "capitolo), in 'fix' cosa andrebbe fatto, in una riga. Non dare punteggi e non giudicare "
        f"altro che queste voci: o la voce passa o no. Lingua delle spiegazioni: {language}."
        f"\n\nLe voci:\n{voci}"
    )
    sezioni = "\n\n".join(f"## [{i + 1}] {s.heading.lstrip('# ')}\n{s.text}"
                          for i, s in enumerate(cap.sections))
    piano_testo = ""
    if piano is not None:
        piano_testo = f"# Il piano\nObiettivo: {piano.goal}\nSvolta: {piano.turn}\n"
        piano_testo += "\n".join(f"{s.line()}\n{s.card()}" if categoria != "stile" else s.line()
                                 for s in piano.scenes)
    utente = "\n\n".join(p for p in (
        pack.chapter_block() if pack is not None else "",
        pack.start_block() if pack is not None and categoria == "racconto" else "",
        piano_testo,
        f"# Il capitolo, a sezioni numerate\n{sezioni}") if p)
    try:
        r = client.chat_json([{"role": "system", "content": sistema},
                              {"role": "user", "content": utente}],
                             schema=_GRID_SCHEMA, model=model)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"griglia ({categoria}): {str(exc)[:160]}")
        return []
    chiavi = {k for k, _d, _c in griglia}
    critiche = {k for k, _d, c in griglia if c}
    cadute = []
    for v in (r.get("items") or []) if isinstance(r, dict) else []:
        chiave = str(v.get("key") or "").strip().lower()
        if chiave not in chiavi:
            continue
        ok = bool(v.get("pass"))
        prova = str(v.get("evidence") or "").strip()
        report.grid.append(f"{'✓' if ok else '✗'} {categoria} · {chiave}"
                           + (f" — «{prova}»" if prova else "")
                           + ("" if ok else f" → {v.get('fix', '')}"))
        if not ok and chiave in critiche:
            sezioni_ko = [int(x) - 1 for x in (v.get("sections") or [])
                          if isinstance(x, int) and 0 < x <= len(cap.sections)]
            cadute.append((chiave, sezioni_ko, str(v.get("fix") or "")))
    return cadute


def _revise_section(client: Any, cap: Chapter, indice: int, problemi: list[str], scena: Any,
                    pack: Any, *, model: str | None, language: str, style: str,
                    events: bool = False) -> tuple[bool, str]:
    budget = int(getattr(pack, "budget", 0) or 0) if pack is not None else 0
    pieno = bool(budget) and cap.words >= GROWTH_CAP * budget
    from bookwriter.forge.writing import _NO_INVENTION, FORBIDDEN

    sezione = cap.sections[indice]
    prima = sezione.words
    if not prima:
        return False, "vuota"
    eventi = (" Per i problemi di continuità la correzione è nei fatti: mostra l'uscita da una "
              "porta chiusa, il viaggio che manca, distingui due documenti che sembravano lo "
              "stesso, metti d'accordo orari, scadenze, date e stagione — con poche righe "
              "concrete dentro la scena, senza spiegazioni al lettore.") if events else ""
    sistema = (
        "Sei il romanziere di questo libro e stai rivedendo UNA scena su indicazione "
        "dell'editor. Correggi i problemi indicati e nient'altro: tieni ogni elemento del piano, "
        "ogni fatto, ogni personaggio, la voce e lo stile; dove la scena spiegava, fai "
        "accadere; dove ripeteva, fai avanzare." + eventi
        + (" Il capitolo è già oltre la sua misura: la scena non deve allungarsi — per "
           "aggiungere, togli altrove nella stessa scena." if pieno else
           " La lunghezza resta quella (±10%).")
        + " La prima riga è il titolo della scena, identico. "
        f"Lingua: {language}. Non scrivere mai nel testo: {FORBIDDEN}.\n\n" + _NO_INVENTION
        + (f"\n\nStile richiesto:\n{style}" if style else "")
    )
    precedente = cap.sections[indice - 1].paragraphs[-1] if indice > 0 and \
        cap.sections[indice - 1].paragraphs else ""
    utente = "\n\n".join(p for p in (
        "# I problemi da correggere\n" + "\n".join(f"- {x}" for x in problemi),
        f"# Il piano della scena\n{scena.card()}" if scena is not None else "",
        pack.told_block() if pack is not None else "",
        f"# Come finisce la scena prima\n{precedente}" if precedente else "",
        f"# La scena\n{sezione.heading}\n\n{sezione.text}") if p)
    try:
        nuovo = client.chat([{"role": "system", "content": sistema},
                             {"role": "user", "content": utente}], model=model)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:160]
    rivista = parse("# x\n\n" + (nuovo or ""))
    paragrafi = [p for s in rivista.sections for p in s.paragraphs]
    dopo = mx.count_words("\n\n".join(paragrafi))
    basso, alto = REVISION_RANGE
    if pieno:
        alto = 1.05  # il capitolo è già oltre la sua misura: la correzione non lo allunga
    if not (basso * prima <= dopo <= alto * prima):
        return False, f"scartata: {dopo} parole contro {prima} (fuori dalla forbice ±10%)"
    sezione.paragraphs = paragrafi
    return True, f"{prima} → {dopo} parole"


# --------------------------------------------------------------------------- #
#  tutto insieme
# --------------------------------------------------------------------------- #
def _scene_for(piano: Any, indice: int, cap: Chapter) -> Any:
    """La scena del piano che corrisponde alla sezione: per posizione, se i conti tornano."""
    if piano is None:
        return None
    titolate = [i for i, s in enumerate(cap.sections) if s.heading]
    if len(titolate) != len(piano.scenes) or indice not in titolate:
        return None
    return piano.scenes[titolate.index(indice)]


def edit_chapter(project: BookProject, client: Any, text: str, piano: Any, pack: Any, *,
                 model: str | None = None, baseline: dict[str, float] | None = None,
                 style: str = "", trial: bool = False,
                 check_model: str | None = None) -> tuple[str, EditorialReport]:
    """La revisione editoriale di un capitolo: il testo rivisto e il resoconto (già su disco).

    Non solleva: un passaggio che non riesce si annota nel resoconto e il testo resta quello del
    passaggio prima. Se alla fine il testo è più corto di un decimo rispetto alla bozza (non
    dovrebbe: ogni passaggio ha la sua guardia), si tiene la bozza e lo si dice.

    ``check_model``: il modello dei controlli (registro degli eventi, etichette del significato,
    griglia), se l'autore ne ha indicato uno diverso da quello che scrive.
    """
    from bookwriter.forge import scenes as sc

    lingua = getattr(pack, "language", "") or "italiano"
    protagonista = getattr(pack, "protagonist", "") or ""
    capitolo = pack.chapter if pack is not None else {}
    numero = int(capitolo.get("number") or 0)
    titolo = str(capitolo.get("title") or "")
    report = EditorialReport(chapter=numero, title=titolo, trial=trial,
                             budget=int(capitolo.get("words") or 0),
                             plan=sc.plan_markdown(piano) if piano is not None else "")
    misure = mx.measure(text, protagonist=protagonista, baseline=baseline)
    report.words_draft = misure.words
    report.metrics_before = misure.markdown()

    cap = parse(text)
    controllo = check_model or model
    _meaning(client, cap, piano, report, model=controllo, language=lingua,
             budget=int(capitolo.get("words") or 0))
    # gli indici dei paragrafi segnalati, riportati alla loro sezione: misurati sul testo dopo
    # la passata del significato, che può aver tolto o aggiunto paragrafi
    segnalati = mx.measure(cap.render(), protagonist=protagonista,
                           baseline=baseline).flagged_paragraphs() if report.meaning else \
        misure.flagged_paragraphs()
    globale, per_sezione = 0, []
    for s in cap.sections:
        locali = {i for i in range(len(s.paragraphs)) if globale + i in segnalati}
        per_sezione.append(locali)
        globale += len(s.paragraphs)
    for i, s in enumerate(cap.sections):
        _redundancy(client, s, _scene_for(piano, i, cap), pack, per_sezione[i], report,
                    model=model, language=lingua, cap_corrente=cap)
    # La continuità di un racconto e la griglia del romanzo (scene, protagonista, mistero) non
    # valgono per un saggio: lì restano le misure, il significato, la ridondanza e le fonti.
    narrativo = bool(getattr(pack, "narrative", True))
    if narrativo:
        _fix_events(client, cap, piano, pack, report, model=model, check_model=controllo,
                    language=lingua, style=style)
    _sources(text, piano, pack, report)
    cadute = _grid(client, cap, piano, pack, report, model=controllo, language=lingua) \
        if narrativo else []

    if cadute:
        per_scena: dict[int, list[str]] = {}
        for voce, sezioni, cosa in cadute:
            for k in sezioni:
                per_scena.setdefault(k, []).append(f"{voce}: {cosa}")
        riviste: set[int] = set()
        for k in sorted(per_scena, key=lambda x: -len(per_scena[x]))[:MAX_REVISED_SCENES]:
            ok, come = _revise_section(client, cap, k, per_scena[k], _scene_for(piano, k, cap),
                                       pack, model=model, language=lingua, style=style)
            if ok:
                riviste.add(k)
            report.revised.append(f"«{_short(cap.sections[k].heading)}»: "
                                  + ("rivista, " if ok else "non rivista, ") + come)
        # una voce critica resta aperta se riguarda tutto il capitolo o una scena non rivista
        report.failed_critical = [v for v, sezioni, _ in cadute
                                  if not sezioni or not set(sezioni) <= riviste]

    if narrativo:
        # il registro degli eventi si rifà sul testo finale: una correzione (di continuità o di
        # griglia) può averne rotta un'altra, e un problema non corretto resta da rivedere
        finale_cron = _events(client, cap, piano, pack, model=controllo)
        report.timeline = finale_cron.markdown()
        if numero:
            from bookwriter.forge import timeline as tl

            tl.save(tl.timeline_path(project, numero, titolo, trial=trial), finale_cron)
        residui = finale_cron.serious()
        if residui:
            report.continuity.append("Dopo le correzioni restano:")
            report.continuity += [f"  {i.line()}" for i in residui]
            report.failed_critical.append("continuità degli eventi")
        else:
            report.continuity.append("Dopo le correzioni: nessun problema bloccante o importante.")

    finale = cap.render()
    dopo = mx.measure(finale, protagonist=protagonista, baseline=baseline)
    if report.words_draft and dopo.words < LENGTH_GUARD * report.words_draft:
        report.guards.append(f"la revisione portava il capitolo da {report.words_draft} a "
                             f"{dopo.words} parole: tenuta la bozza")
        finale, dopo = text, misure
    report.words_final = dopo.words
    report.metrics_after = dopo.markdown()
    if report.budget and dopo.words > GROWTH_CAP * report.budget:
        report.guards.append(f"il capitolo resta oltre la misura: {dopo.words} parole su "
                             f"{report.budget} (la bozza ne aveva {report.words_draft})")
    path = report_path(project, numero, titolo, trial=trial)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.markdown(), encoding="utf-8")
    return finale, report


def measure_only(project: BookProject, text: str, chapter: dict[str, Any], *,
                 protagonist: str = "", baseline: dict[str, float] | None = None
                 ) -> EditorialReport:
    """Solo le misure, senza modello: per guardare un capitolo esistente senza toccarlo."""
    misure = mx.measure(text, protagonist=protagonist, baseline=baseline)
    return EditorialReport(chapter=int(chapter.get("number") or 0),
                           title=str(chapter.get("title") or ""),
                           budget=int(chapter.get("words") or 0), words_draft=misure.words,
                           words_final=misure.words, metrics_before=misure.markdown(),
                           metrics_after=misure.markdown())
