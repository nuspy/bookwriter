"""Fase 4 — la stesura: un capitolo, un file, scritto su disco prima del successivo.

> **La regola zero.** Ogni capitolo finisce in ``<libro>/chapters/NN_nome.md`` nel momento in cui
> è scritto, prima che cominci il successivo. Non nella risposta, non in un foglio di appunti,
> non in una cache sotto una cartella temporanea: nella cartella del libro, con uno strumento che
> scrive file davvero.

Non è pignoleria. Un libro sono trenta o cinquanta capitoli e molte sessioni di lavoro; il
contesto di un modello non ci arriva. Il guasto è silenzioso ed è totale: si scrivono dodici
capitoli, la sessione finisce o viene riassunta, e non esiste più niente — non per il modello,
non per un altro assistente, non per l'autore. Non c'è niente da recuperare, perché una bozza è
un file e nessun file è stato scritto.

Per questo qui si scrive prima e si racconta dopo, e per questo lo stato lo dà
:func:`bookwriter.forge.project.status`, che guarda il disco.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from bookwriter.forge import notebook as nb
from bookwriter.forge.metrics import count_words
from bookwriter.forge.project import BookProject, ForgeState
from bookwriter.llm import jsoncheck
from bookwriter.llm.client import LLMError

#: Quanto ci si può discostare dal budget di parole prima di rifare il capitolo.
TOLERANCE = 0.15

#: Capitoli non riusciti di fila dopo i quali si smette di provare. Uno o due sono il capitolo;
#: tre sono il modello che non risponde più, e insistere significa attendere il timeout pieno
#: per ciascuno dei rimanenti per riportare lo stesso errore.
CONSECUTIVE_FAILURES = 3

#: Quello che non deve mai arrivare nel manoscritto. Il compositore ne toglie una parte, ma il
#: modo affidabile per non stamparlo è non scriverlo.
FORBIDDEN = (
    "conteggi di parole, tabelle di avanzamento, «capitolo completato», TODO, note all'autore, "
    "commenti sul lavoro fatto"
)


#: Le due regole di fondo, uguali per un capitolo scritto d'un fiato e per una sua sezione.
_NO_INVENTION = (
    "Il contenuto del libro è dell'autore: le sue tesi, i suoi fatti, le sue scene, i suoi "
    "personaggi e le sue scelte (il quaderno e la struttura) si scrivono nella sua voce, per "
    "intero, anche se la ricerca non li documenta o li contraddice, senza cautele, "
    "attenuazioni, smentite o «secondo alcuni» che lui non ha chiesto. Che una cosa non abbia "
    "fonti non è un difetto e non è un motivo per toglierla. Il materiale della ricerca serve a "
    "arricchire quel contenuto: mettilo in scena attraverso oggetti, documenti, luoghi, gesti e "
    "dialoghi, scegliendo quello che serve qui; non è un elenco da riassumere né da esaurire. "
    "Una cosa sola non si inventa mai: fonti, cifre, nomi di autori, titoli e "
    "citazioni attribuiti a una fonte vengono solo da ciò che la ricerca ha letto davvero; se ti "
    "servirebbe una cifra o una citazione che non hai, scrivi la frase senza. Le decisioni "
    "dell'autore (sezioni apposite, se ci sono) si scrivono come lui le ha formulate, in voce "
    "d'autore e con precedenza sulla ricerca, anche in contrasto con le fonti trovate e senza "
    "attribuirle a fonti che non le dicono; «tralascia» non si menziona affatto; «fonte "
    "trovata» si usa citando la fonte. Un contrasto segnalato e non ancora deciso non cambia il "
    "testo: il libro dice quello che l'autore ha scritto."
)
_NOTEBOOK_RULE = (
    "Il libro è descritto dal suo quaderno, nato dalla conversazione con l'autore: "
    "personaggi, luoghi, catena degli avvenimenti, nessi causali, scene chiave e finale sono "
    "decisi. Il capitolo li rispetta e non li contraddice: un personaggio ha l'aspetto, la "
    "cultura e il carattere che il quaderno gli dà, e un fatto accade per le ragioni che il "
    "quaderno dice. Linguaggio e registro seguono il lettore indicato."
)


class SupportsChat(Protocol):
    def chat(self, messages: list[dict[str, str]], *, model: str | None = ...,
             extra: dict[str, Any] | None = ...) -> str:
        ...


@dataclass
class ChapterResult:
    number: int
    title: str
    path: object = None
    words: int = 0
    budget: int = 0
    retried: bool = False
    sections: int = 0  #: da quante sezioni è stato scritto (0 = una chiamata sola)
    stopped: bool = False  #: fermato a metà: le sezioni scritte restano nel lavoro
    #: il piano ha blocker e nessuna deroga: niente è stato scritto (vedi ``Plan.blockers``)
    blocked: list[str] = field(default_factory=list)
    plan_path: str = ""

    @property
    def drift(self) -> float:
        return abs(self.words - self.budget) / self.budget if self.budget else 0.0

    def line(self) -> str:
        scostamento = f" ({self.words} parole su {self.budget})" if self.budget else \
                      f" ({self.words} parole)"
        avviso = "  ⚠ fuori budget" if self.budget and self.drift > TOLERANCE else ""
        return f"{self.number}. {self.title}{scostamento}{avviso}"


@dataclass
class ChapterTools:
    """Quello che la stesura riceve da chi la coordina, oltre al capitolo.

    ``pack`` è il contesto del capitolo (:mod:`context`); ``planner`` ne fa il piano delle scene
    (:func:`scenes.plan`); ``editor`` passa il capitolo scritto alla revisione editoriale e ne
    restituisce il testo (:mod:`editor`); ``recorder`` ne ricava la scheda per il registro della
    continuità (:mod:`ledger`); ``notes`` dà le voci di bibliografia delle affermazioni usate.
    ``destination``: dove scrivere il capitolo invece che in ``chapters/`` (una prova: allora
    il registro non si aggiorna, perché la prova non è il libro). ``facts`` registra i fatti di
    una scena appena scritta (:func:`timeline.record_scene`): le scene dopo li ricevono come
    fatti fissati, così una pagina sottratta nella scena 1 non si sottrae di nuovo nella 4.
    """

    pack: Any
    planner: Callable[[Any], Any] | None = None
    editor: Callable[[str, Any, Any], str] | None = None
    recorder: Callable[[int, str, str], None] | None = None
    notes: Callable[[list[str]], list[str]] | None = None
    destination: Path | None = None
    facts: Callable[[str, str, Any, list], Any] | None = None
    #: la deroga dell'autore ai blocker del piano (il motivo); vuota = i blocker fermano
    waiver: str = ""

    @property
    def trial(self) -> bool:
        return self.destination is not None


@dataclass
class WritingReport:
    written: list[ChapterResult] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    #: I capitoli che il modello non è riuscito a scrivere: (numero, titolo, motivo).
    failed: list[tuple[int, str, str]] = field(default_factory=list)
    stopped: bool = False
    #: Perché il giro si è interrotto da solo, se è successo. Vuoto se è andato fino in fondo.
    aborted: str = ""
    #: il capitolo fermato dai blocker del suo piano: (numero, titolo, blocker)
    blocked: tuple[int, str, list[str]] | None = None

    def summary(self) -> str:
        parole = sum(c.words for c in self.written)
        righe = [
            f"{len(self.written)} capitoli scritti su disco, {parole} parole."
            if self.written else "Nessun capitolo scritto."
        ]
        if self.stopped:
            righe[0] += " Fermato su richiesta: quello che è stato scritto resta."
        righe.extend(c.line() for c in self.written)
        if self.failed:
            # I non riusciti vanno detti per nome: senza, chi legge vede «18 capitoli scritti»
            # su venti e non sa quali due manchino né perché.
            righe.append("")
            righe.append(f"NON RIUSCITI: {len(self.failed)}")
            righe.extend(f"{n}. {titolo} — {motivo}" for n, titolo, motivo in self.failed)
            righe.append("Il file non è stato scritto: ripremendo si riparte da questi.")
        if self.aborted:
            righe.append("")
            righe.append(self.aborted)
        if self.blocked:
            n, titolo, difetti = self.blocked
            righe += ["", f"FERMATO AL CAPITOLO {n}. {titolo}: il piano ha difetti bloccanti",
                      *[f"- {d}" for d in difetti],
                      "Ripianifica il capitolo, o procedi con una deroga motivata."]
        if self.skipped:
            righe.append(f"Non tentati: {self.skipped}")
        return "\n".join(righe)


# --------------------------------------------------------------------------- #
#  cosa scrivere adesso
# --------------------------------------------------------------------------- #
def pending_chapters(project: BookProject, state: ForgeState) -> list[dict[str, Any]]:
    """I capitoli previsti dalla specifica che **non sono su disco**. Guarda i file, non lo stato.

    Completando o aggiornando un manoscritto i capitoli sono su disco fin dall'inizio: allora
    resta da fare quello che la struttura dice (capitoli nuovi senza file, capitoli da completare
    o da aggiornare).
    """
    scritti = project.written_chapters()
    modo = getattr(state, "mode", "nuovo")
    if modo == "completa":
        from bookwriter.forge import completion

        return completion.pending(state, set(scritti))
    if modo == "aggiorna":
        from bookwriter.forge import changes

        return changes.pending(state)
    return [c for c in state.outline if int(c.get("number") or 0) not in scritti]


def next_chapter(project: BookProject, state: ForgeState) -> dict[str, Any] | None:
    aperti = pending_chapters(project, state)
    return aperti[0] if aperti else None


# --------------------------------------------------------------------------- #
#  la stesura di un capitolo
# --------------------------------------------------------------------------- #
def draft_chapter(
    project: BookProject,
    state: ForgeState,
    client: SupportsChat,
    chapter: dict[str, Any],
    *,
    model: str | None = None,
    style_contract: str = "",
    research: str = "",
    memory: list[str] | None = None,
    decisions: str = "",
    review: str = "",
    section_words: int = 1500,
    should_stop: Callable[[], bool] | None = None,
    on_section: Callable[[int, int], None] | None = None,
    tools: ChapterTools | None = None,
) -> ChapterResult:
    """Scrive un capitolo e **lo salva subito**. Ritorna cosa è stato scritto e quanto è lungo.

    Con ``tools`` (vedi :class:`ChapterTools`) un libro narrativo si scrive **per scene**, da un
    piano (:func:`_draft_by_scenes`), e ogni capitolo passa dalla revisione editoriale e lascia
    la sua scheda nel registro della continuità.

    ``decisions`` è cosa fare di ogni contrasto segnalato dalla ricerca: la fonte trovata, la
    formulazione scelta dall'autore, «tralascia» solo se l'ha deciso lui, o niente: il testo
    resta com'è. Vedi ``research_store.decisions_text``.

    Un capitolo il cui budget supera ``section_words`` si scrive **a sezioni**: una risposta del
    modello non basta per settemila parole (se ne ferma attorno alle duemila), e chiedere tutto
    in una volta produce un capitolo di un terzo della misura che sembra finito. Vedi
    :func:`_draft_by_sections`.
    """
    project.scaffold()
    numero = int(chapter.get("number") or 0)
    titolo = str(chapter.get("title") or f"Capitolo {numero}")
    budget = int(chapter.get("words") or 0)

    if tools is not None and getattr(tools.pack, "narrative", False):
        return _draft_by_scenes(
            project, state, client, chapter, tools=tools, model=model,
            style_contract=style_contract, section_words=section_words,
            should_stop=should_stop, on_section=on_section)
    if tools is not None and getattr(tools.pack, "claims", None):
        # anche un saggio riceve le affermazioni scelte, non il taglio cieco del file di ricerca
        research = tools.pack.claims_block()
    if budget and section_words and budget > section_words:
        return _draft_by_sections(
            project, state, client, chapter, model=model, style_contract=style_contract,
            research=research, memory=memory or [], decisions=decisions, review=review,
            section_words=section_words, should_stop=should_stop, on_section=on_section,
            tools=tools)

    testo = _generate(state, client, chapter, model=model, style_contract=style_contract,
                      research=research, memory=memory or [], decisions=decisions,
                      review=review)
    testo = _clean(testo, numero, titolo)
    ripetizioni = jsoncheck.repeated_text(testo)
    if ripetizioni:
        # Ripetizioni nella prosa: si rifà una volta, dicendo cosa ripeteva; se ripete ancora si
        # tolgono i paragrafi identici.
        rifatto = _clean(_generate(
            state, client, chapter, model=model, style_contract=style_contract, research=research,
            memory=memory or [], decisions=decisions, review=review,
            avviso="Il tentativo precedente conteneva ripetizioni (" + "; ".join(ripetizioni[:3])
            + "): scrivi il capitolo senza ripetere paragrafi né frasi."), numero, titolo)
        if _count(rifatto) < LENGTH_GUARD * _count(testo):
            rifatto = testo  # il rifacimento ha tagliato contenuto: si tiene il primo
        testo = rifatto if not jsoncheck.repeated_text(rifatto) else jsoncheck.dedupe_paragraphs(rifatto)
    risultato = ChapterResult(number=numero, title=titolo, words=_count(testo), budget=budget)

    if budget and abs(risultato.words - budget) / budget > TOLERANCE:
        rifatto = _reconcile(state, client, testo, chapter, model=model,
                             style_contract=style_contract)
        if rifatto:
            candidato = _clean(rifatto, numero, titolo)
            parole = _count(candidato)
            # Si tiene la versione più vicina al budget: un secondo tentativo peggiore va buttato.
            if abs(parole - budget) < abs(risultato.words - budget):
                testo, risultato.words, risultato.retried = candidato, parole, True

    return _finish(project, numero, titolo, testo, risultato, tools, None)


def _finish(project: BookProject, numero: int, titolo: str, testo: str, risultato: ChapterResult,
            tools: ChapterTools | None, piano: Any) -> ChapterResult:
    """La revisione editoriale, il file, la scheda nel registro: in quest'ordine.

    Prima si scrive. Sempre. Quello che sta solo in memoria non esiste — e per questo la bozza
    è già nel lavoro (``.forge/partial``) quando parte la revisione: se la revisione si ferma, la
    bozza c'è ancora.
    """
    if tools is not None and tools.editor is not None:
        testo = tools.editor(testo, piano, tools.pack)
    if tools is not None and tools.destination is not None:
        path = tools.destination
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = project.chapter_path(numero, titolo)
    path.write_text(testo, encoding="utf-8")
    risultato.path = path
    risultato.words = _count(testo)
    if tools is not None and tools.recorder is not None and not tools.trial:
        tools.recorder(numero, titolo, testo)
    return risultato


def _generate(state: ForgeState, client: SupportsChat, chapter: dict[str, Any], *,
              model: str | None, style_contract: str, research: str,
              memory: list[str], decisions: str = "", avviso: str = "",
              review: str = "") -> str:
    from bookwriter.forge.spec import _brief

    numero = chapter.get("number")
    lingua = nb.language_name(state)
    budget = int(chapter.get("words") or 0)
    misura = f"Circa {budget} parole, con uno scarto massimo del 15%." if budget else ""

    sistema = (
        "Scrivi un capitolo di libro, in prosa finita e pubblicabile. Non un abbozzo, non uno "
        "schema, non un elenco di punti da sviluppare: il capitolo come andrà stampato.\n\n"
        f"Lingua: {lingua}. {misura}\n\n"
        "Forma del file:\n"
        f"- la prima riga è esattamente `# Capitolo {numero}: {chapter.get('title')}`\n"
        "- le sezioni interne sono titoli di secondo livello (`##`), da 400 a 900 parole ciascuna\n"
        "- al massimo tre livelli di titolo: un quarto vuol dire che erano due capitoli\n"
        "- le fonti del capitolo, se ce ne sono, vanno in fondo sotto `## Note bibliografiche`\n\n"
        f"Non scrivere mai nel testo: {FORBIDDEN}.\n\n"
        + _NO_INVENTION + "\n\n" + _NOTEBOOK_RULE
    )
    if style_contract:
        sistema += f"\n\nStile richiesto:\n{style_contract}"

    precedenti = "\n".join(
        f"- Capitolo {c.get('number')}: {c.get('title')} — {c.get('does')}"
        for c in state.outline if int(c.get("number") or 0) < int(numero or 0)
    )
    utente = "\n\n".join(p for p in (
        f"# Il quaderno del libro\n{_brief(state)}",
        f"# Cosa hanno già stabilito i capitoli precedenti\n{precedenti}" if precedenti else "",
        (f"# Questo capitolo\nCapitolo {numero}: {chapter.get('title')}\n"
        f"Cosa deve fare: {chapter.get('does')}"
         + (("\n\nPunti chiave, nell'ordine (ciascuno va portato nel capitolo):\n"
             + "\n".join(f"- {p}" for p in chapter["points"]))
            if chapter.get("points") else "")),
        f"# Materiale trovato dalla ricerca per questo capitolo (da usare)\n{research}"
        if research else "",
        (f"# Contrasti segnalati dalla ricerca: cosa ha deciso l'autore\n{decisions}" if decisions else ""),
        (f"# Decisioni dell'autore dopo la revisione (hanno la precedenza sulla ricerca)\n{review}"
         if review else ""),
        "# Materiale dell'autore da usare\n" + "\n\n".join(m[:2500] for m in memory[:4])
        if memory else "",
        f"# Attenzione\n{avviso}" if avviso else "",
    ) if p)
    return client.chat(
        [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
        model=model,
    )


# --------------------------------------------------------------------------- #
#  un capitolo lungo si scrive a sezioni
# --------------------------------------------------------------------------- #
#: Una sezione più corta di questa frazione del suo obiettivo si prolunga, una volta sola — ma
#: solo se il suo compito non è svolto: il modello può rispondere ``DONE``.
SHORT_SECTION = 0.6

#: Una riscrittura (per togliere ripetizioni, per stile, in revisione) si tiene solo se conserva
#: almeno questa parte della lunghezza: oltre, ha tagliato contenuto, non ripetizioni.
LENGTH_GUARD = 0.9

#: La risposta con cui il modello dice che non c'è niente da aggiungere.
DONE = "FATTO"

#: Il seguito di una risposta tagliata dal server: si riprende la frase, non si ricomincia.
CONTINUE_CUT = (
    "La risposta è stata interrotta. Riprendi ESATTAMENTE dalla parola in cui si è fermata e "
    "porta a termine il compito, senza ripetere nulla, senza titolo e senza commenti.")

#: Quante parole del capitolo già scritto si rimandano alla lettera alla sezione dopo.
CONTEXT_WORDS = 2000

SECTIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "does": {"type": "string"},
                    "words": {"type": "integer"},
                },
                "required": ["title", "does", "words"],
            },
        },
    },
    "required": ["sections"],
}


class WritingStopped(Exception):
    """L'autore ha fermato la stesura a metà capitolo: le sezioni scritte restano nel lavoro."""


def partial_path(project: BookProject, number: int, title: str, *, trial: bool = False):
    """Dove vivono le sezioni di un capitolo non ancora finito.

    Fuori da ``chapters/``: un capitolo a metà non deve risultare scritto, né per ``status`` né
    per chi guarda i file. Quando le sezioni sono tutte, il capitolo vero si scrive e questo file
    sparisce. Una prova ha il suo file, perché non si confonda col capitolo vero.
    """
    from bookwriter.forge.project import slugify

    prefisso = "prova_" if trial else ""
    return project.root / ".forge" / "partial" / f"{prefisso}{number:02d}_{slugify(title)}.json"


def _load_partial(path, budget: int) -> tuple[list[dict[str, Any]], list[str]]:
    import json

    try:
        dati = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], []
    if int(dati.get("budget") or 0) != budget or not dati.get("plan"):
        return [], []  # il budget è cambiato: il vecchio piano non vale
    testi = [str(t) for t in (dati.get("texts") or [])][: len(dati["plan"])]
    return list(dati["plan"]), testi


def _save_partial(path, budget: int, plan: list[dict[str, Any]], testi: list[str]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"budget": budget, "plan": plan, "texts": testi},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def plan_sections(state: ForgeState, client: SupportsChat, chapter: dict[str, Any], budget: int,
                  section_words: int, *, model: str | None = None) -> list[dict[str, Any]]:
    """Il piano delle sezioni di un capitolo lungo: ``[{"title", "does", "words"}]``.

    Dai punti chiave del capitolo, se ce ne sono abbastanza; altrimenti lo chiede al modello
    (con i punti come traccia). La somma delle parole è portata vicino al budget.
    """
    from bookwriter.forge.spec import normalise_points

    quante = max(2, -(-budget // max(1, section_words)))
    punti = normalise_points(chapter.get("points"))
    if len(punti) >= quante:
        return _spread([{"title": "", "does": p, "words": 0} for p in punti], budget)
    traccia = ("\nPunti chiave del capitolo, da distribuire nelle sezioni:\n"
               + "\n".join(f"- {p}" for p in punti)) if punti else ""
    sistema = (
        f"Dividi un capitolo di libro in {quante} sezioni (anche una in più o in meno se il "
        "contenuto lo chiede) che si leggano in sequenza, ciascuna con un titolo breve, una riga "
        "che dice cosa fa e quante parole ha. La somma delle parole è il budget del capitolo. "
        f"Scrivi in {nb.language_name(state)}."
    )
    utente = (f"Capitolo {chapter.get('number')}: {chapter.get('title')}\n"
              f"Cosa deve fare: {chapter.get('does')}\nBudget: {budget} parole{traccia}")
    try:
        risposta = client.chat_json(
            [{"role": "system", "content": sistema}, {"role": "user", "content": utente}],
            schema=SECTIONS_SCHEMA, model=model)
        grezze = [s for s in (risposta.get("sections") or []) if isinstance(s, dict)]
    except Exception:
        grezze = []
    if not grezze:
        grezze = [{"title": "", "does": str(chapter.get("does") or ""), "words": 0}
                  for _ in range(quante)]
    return _spread([{"title": str(s.get("title") or "").strip(),
                     "does": str(s.get("does") or "").strip(),
                     "words": int(s.get("words") or 0)} for s in grezze], budget)


def _spread(sezioni: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Porta la somma delle parole al budget, proporzionalmente (pari, se nessuna dice quante)."""
    peso = [max(0, int(s.get("words") or 0)) for s in sezioni]
    if not sum(peso):
        peso = [1] * len(sezioni)
    totale = sum(peso)
    for s, p in zip(sezioni, peso, strict=True):
        s["words"] = max(300, int(round(budget * p / totale / 50.0) * 50))
    return sezioni


def _tail_words(testi: list[str], words: int) -> tuple[str, list[str]]:
    """Le ultime ``words`` parole del capitolo, alla lettera, e un cenno alle sezioni prima.

    Il cenno è il titolo e l'attacco di ogni sezione che sta tutta prima della coda: poco, ma
    quanto basta perché la sezione che segue sappia cosa è già stato detto.
    """
    intero = "\n\n".join(testi)
    posizioni = [m.start() for m in re.finditer(r"\S+", intero)]
    if len(posizioni) <= words:
        return intero, []
    taglio = posizioni[-words]
    coda = intero[taglio:]
    prima, consumato = [], 0
    for testo in testi:
        fine = consumato + len(testo) + 2
        if fine <= taglio:
            titolo = next((r.lstrip("# ").strip() for r in testo.splitlines()
                           if r.strip().startswith("#")), "")
            attacco = " ".join(" ".join(r for r in testo.splitlines()
                                        if not r.strip().startswith("#")).split()[:60])
            prima.append(f"- {titolo}: {attacco}…" if titolo else f"- {attacco}…")
        consumato = fine
    return coda, prima


def _section_system(state: ForgeState, chapter: dict[str, Any], plan: list[dict[str, Any]],
                    indice: int, style_contract: str) -> str:
    numero = chapter.get("number")
    ultima = indice == len(plan) - 1
    parole = plan[indice]["words"]
    sistema = (
        "Scrivi UNA SEZIONE di un capitolo di libro, in prosa finita e pubblicabile: non un "
        "abbozzo, non uno schema, non un riassunto. Una sezione lunga e sviluppata, con scene, "
        "esempi e passaggi concreti.\n\n"
        f"Lingua: {nb.language_name(state)}. Sezione {indice + 1} di {len(plan)} del capitolo "
        f"{numero} «{chapter.get('title')}». Circa {parole} parole: è un'indicazione, la sezione "
        "finisce quando ha svolto il suo compito; non allungarla ripetendo o riformulando.\n\n"
        "Forma:\n"
        "- la prima riga è `## ` seguito da un titolo breve e adatto per questa sezione (non la "
        "frase del piano), di secondo livello; non ripetere il titolo del capitolo e non usare "
        "un titolo di primo livello\n"
        "- al massimo un livello di titolo sotto di quello (`###`)\n"
        + ("- è l'ultima sezione: chiude il capitolo; le fonti usate nel capitolo vanno in fondo "
           "sotto `## Note bibliografiche`\n" if ultima else
           "- non chiudere il capitolo: la sezione dopo riprende da dove finisci\n")
        + "\nContinuità: il testo già scritto di questo capitolo ti è dato sotto. Prosegui da "
        "lì senza riassumere quello che è già stato detto, senza ripetere frasi o immagini, senza "
        "ripresentare personaggi e luoghi già presentati. Se la sezione prima si interrompe a "
        "metà di una scena o di un ragionamento, prosegui quella.\n\n"
        f"Non scrivere mai nel testo: {FORBIDDEN}.\n\n" + _NO_INVENTION + "\n\n" + _NOTEBOOK_RULE
    )
    if style_contract:
        sistema += f"\n\nStile richiesto:\n{style_contract}"
    return sistema


def _section_user(state: ForgeState, chapter: dict[str, Any], plan: list[dict[str, Any]],
                  indice: int, testi: list[str], research: str, memory: list[str],
                  decisions: str, review: str = "") -> str:
    from bookwriter.forge.spec import _brief

    numero = chapter.get("number")
    precedenti = "\n".join(
        f"- Capitolo {c.get('number')}: {c.get('title')} — {c.get('does')}"
        for c in state.outline if int(c.get("number") or 0) < int(numero or 0))
    piano = "\n".join(
        f"{i + 1}. {s['title'] or s['does']} — {s['does']} (~{s['words']} parole)"
        + ("   ← QUESTA" if i == indice else "") for i, s in enumerate(plan))
    coda, prima = _tail_words(testi, CONTEXT_WORDS)
    pezzi = [
        f"# Il quaderno del libro\n{_brief(state)}",
        f"# Cosa hanno già stabilito i capitoli precedenti\n{precedenti}" if precedenti else "",
        (f"# Questo capitolo\nCapitolo {numero}: {chapter.get('title')}\n"
         f"Cosa deve fare: {chapter.get('does')}"
         + (("\n\nPunti chiave, nell'ordine (ciascuno va portato nel capitolo):\n"
             + "\n".join(f"- {p}" for p in chapter["points"]))
            if chapter.get("points") else "")
         + f"\n\nPiano delle sezioni:\n{piano}"),
        f"# Materiale trovato dalla ricerca per questo capitolo (da usare)\n{research}"
        if research else "",
        (f"# Contrasti segnalati dalla ricerca: cosa ha deciso l'autore\n{decisions}" if decisions else ""),
        (f"# Decisioni dell'autore dopo la revisione (hanno la precedenza sulla ricerca)\n{review}"
         if review else ""),
        ("# Materiale dell'autore da usare\n" + "\n\n".join(m[:2500] for m in memory[:4])
         if memory else ""),
        ("# Le sezioni prima (in breve)\n" + "\n".join(prima)) if prima else "",
        (f"# Il capitolo finora, alla lettera\n{coda}" if coda else
         "# Il capitolo finora\n(questa è la prima sezione)"),
        (f"# Da scrivere adesso\nSezione {indice + 1} di {len(plan)}: "
         f"{plan[indice]['does']}"),
    ]
    return "\n\n".join(p for p in pezzi if p)


def _clean_section(testo: str, titolo_capitolo: str) -> str:
    """Toglie l'incarto del modello e garantisce che la sezione cominci con un titolo ``##``."""
    testo = (testo or "").strip()
    if match := _FENCE.match(testo):
        testo = match.group(1).strip()
    righe = testo.splitlines()
    while righe and not righe[0].strip():
        righe.pop(0)
    if righe and re.match(r"^#\s+", righe[0].strip()):
        # un titolo di primo livello (il capitolo, ripetuto): si abbassa a sezione
        righe[0] = "#" + righe[0].strip()
    if (righe and titolo_capitolo
            and righe[0].strip().lower().lstrip("# ").startswith(titolo_capitolo.lower())):
        righe.pop(0)
        while righe and not righe[0].strip():
            righe.pop(0)
    if righe and not righe[0].strip().startswith("## "):
        righe[:0] = ["## " + (titolo_capitolo or "Sezione"), ""]
    return "\n".join(righe).strip()


def _write_section(state: ForgeState, client: SupportsChat, chapter: dict[str, Any],
                   plan: list[dict[str, Any]], indice: int, testi: list[str], *, model,
                   style_contract: str, research: str, memory: list[str],
                   decisions: str, review: str = "") -> str:
    """Una sezione: una chiamata, e una sola continuazione se viene corta o tagliata."""
    sistema = _section_system(state, chapter, plan, indice, style_contract)
    utente = _section_user(state, chapter, plan, indice, testi, research, memory, decisions,
                           review)
    titolo = str(chapter.get("title") or "")
    messaggi = [{"role": "system", "content": sistema}, {"role": "user", "content": utente}]
    testo = _clean_section(client.chat(messaggi, model=model), titolo)
    obiettivo = plan[indice]["words"]
    tagliata = getattr(client, "last_finish_reason", "") == "length"
    if tagliata or _count(testo) < SHORT_SECTION * obiettivo:
        # Si prosegue una sezione interrotta o rimasta a metà del suo compito, mai una sezione
        # finita: chiedere «ancora» a un testo che ha già detto quello che doveva produce la
        # stessa tesi riformulata (il capitolo 11 di *The Red Thread* è fatto così).
        seguito = client.chat(
            [*messaggi, {"role": "assistant", "content": testo},
             {"role": "user", "content": CONTINUE_CUT if tagliata else (
                 "La sezione è più breve del previsto. Se il suo compito («"
                 + str(plan[indice]["does"]) + "») non è ancora svolto, prosegui da dove ti sei "
                 "fermato e svolgilo con casi, esempi o passaggi NUOVI, senza titolo e senza "
                 "ripetere nulla di quanto hai scritto. Se è già svolto, rispondi soltanto "
                 f"{DONE}.")}],
            model=model)
        seguito = _clean_continuation(seguito)
        if seguito:
            testo = testo + "\n\n" + seguito
    # Un modello che si avvita ripete paragrafi o frasi: si controlla ciò che ha scritto, e la
    # sezione si rifà UNA volta, dicendogli cosa ripeteva. Se ripete ancora, si tolgono i
    # paragrafi identici: meglio una sezione un po' più corta che due volte lo stesso testo.
    return _fix_repetitions(client, messaggi, testo, titolo, model=model)


def _fix_repetitions(client: SupportsChat, messaggi: list[dict[str, str]], testo: str,
                     titolo: str, *, model, clean: Callable[[str], str] | None = None) -> str:
    """Un testo che ripete paragrafi o frasi identici si rifà UNA volta, mostrando al modello il
    testo e le ripetizioni. La riscrittura si tiene solo se non ha perso più di un decimo: una
    sezione dimezzata è un danno peggiore di una frase ripetuta, e allora si tolgono soltanto i
    paragrafi identici."""
    ripetizioni = jsoncheck.repeated_text(testo)
    if not ripetizioni:
        return testo
    pulisci = clean or (lambda t: _clean_section(t, titolo))
    rifatta = pulisci(client.chat(
        [*messaggi, {"role": "assistant", "content": testo},
         {"role": "user", "content": (
             "Nel testo che hai scritto ci sono ripetizioni (" + "; ".join(ripetizioni[:3])
             + "). Riscrivilo dall'inizio togliendole: dove un paragrafo ripeteva l'altro, metti "
             "al suo posto quello che il compito chiede e non è ancora scritto. Stessa forma, "
             "stessa lunghezza.")}],
        model=model))
    migliore = rifatta if _count(rifatta) >= LENGTH_GUARD * _count(testo) else testo
    return migliore if not jsoncheck.repeated_text(migliore) else jsoncheck.dedupe_paragraphs(migliore)


def _draft_by_sections(project, state, client, chapter, *, model, style_contract, research,
                       memory, decisions, section_words, should_stop, on_section, review="",
                       tools=None):
    numero = int(chapter.get("number") or 0)
    titolo = str(chapter.get("title") or f"Capitolo {numero}")
    budget = int(chapter.get("words") or 0)
    percorso = partial_path(project, numero, titolo, trial=bool(tools and tools.trial))
    piano, testi = _load_partial(percorso, budget)
    if not piano:
        piano = plan_sections(state, client, chapter, budget, section_words, model=model)
        testi = []
    risultato = ChapterResult(number=numero, title=titolo, budget=budget, sections=len(piano))
    for i in range(len(testi), len(piano)):
        if should_stop and should_stop():
            _save_partial(percorso, budget, piano, testi)
            risultato.stopped = True
            risultato.words = _count("\n\n".join(testi))
            return risultato
        if on_section:
            on_section(i + 1, len(piano))
        testi.append(_write_section(state, client, chapter, piano, i, testi, model=model,
                                    style_contract=style_contract, research=research,
                                    memory=memory or [], decisions=decisions,
                                    review=review))
        # Ogni sezione va su disco appena è scritta, ma nel lavoro: il capitolo non è finito.
        _save_partial(percorso, budget, piano, testi)
    testo = _clean(f"# Capitolo {numero}: {titolo}\n\n" + "\n\n".join(testi), numero, titolo)
    risultato.words = _count(testo)
    _finish(project, numero, titolo, testo, risultato, tools, None)  # solo adesso è scritto
    try:
        percorso.unlink()
    except OSError:
        pass
    return risultato


# --------------------------------------------------------------------------- #
#  un racconto si scrive per scene
# --------------------------------------------------------------------------- #
#: Una scena più corta di questa frazione della sua misura riceve UN seguito — solo se le manca
#: qualcosa del piano, e il modello può rispondere che non manca niente.
SHORT_SCENE = 0.6

#: Sotto questa parte della misura, il capitolo finito riceve una o due scene in più dal piano.
SHORT_CHAPTER = 0.9


def _scene_system(pack: Any, unita: Any, style_contract: str) -> str:
    chi = pack.protagonist or "il protagonista"
    primo = unita.titled
    sistema = (
        "Sei il romanziere che scrive questo libro. Scrivi UNA SCENA (o la parte di una scena) "
        "di un capitolo, in prosa finita e pubblicabile: non un abbozzo, non un riassunto, non "
        "un saggio.\n\n"
        f"Lingua: {pack.language}. Circa {unita.words} parole: è un'indicazione, la scena finisce "
        "quando ha fatto il suo lavoro; non allungarla con spiegazioni o riformulazioni.\n\n"
        "Gerarchia delle regole, dalla più forte: 1) il quaderno dell'autore e le sue decisioni; "
        "2) la continuità con quanto già scritto; 3) il piano delle scene; 4) il materiale di "
        "ricerca; 5) le preferenze di stile. Se due regole si contraddicono vale la più forte.\n\n"
        "SCENA, NON SAGGIO. " + chi[0].upper() + chi[1:] + " (o i personaggi dell'episodio "
        "storico) percepisce, cerca, sceglie, sbaglia, agisce e paga le conseguenze. Ogni "
        "informazione importante arriva attraverso un documento, un oggetto, un gesto, un luogo, "
        "un dialogo, un conflitto o una deduzione legata a un indizio concreto: la spiegazione "
        "può seguire una scoperta, mai sostituirla. I personaggi parlano: dove in scena ce n'è "
        "più di uno, fallo vivere anche nel dialogo, con voci distinte.\n\n"
        "NIENTE RIPETIZIONI. Non ripetere una tesi già comunicata con sinonimi o metafore "
        "nuove. Non spiegare di nuovo un collegamento che il lettore ha già capito (l'elenco "
        "«già detto» qui sotto), a meno che una prova nuova lo trasformi. Un motivo ricorrente "
        "torna solo se cambia significato o produce un effetto nuovo. Non chiudere i paragrafi "
        "con un aforisma che riassume il tema; non usare in serie la formula «non era X: era Y». "
        "Immagini forti nei punti di svolta, non in ogni paragrafo; verbi concreti, nomi "
        "precisi.\n\n"
        "FIDATI DELLA SCENA. Quando un'azione o un dialogo ha mostrato una cosa, non spiegarla "
        "dopo: nessun paragrafo che interpreta, reinterpreta o riassume quello che il lettore ha "
        "appena visto. Ogni paragrafo aggiunge un fatto, una scelta, un ostacolo, un indizio o "
        "una conseguenza; se ribadisce soltanto un significato già compreso, non scriverlo.\n\n"
        "UNA METAFORA NON È UN SISTEMA. Un'immagine forte vale una volta, nel punto giusto: non "
        "applicarla a tutto (documenti, porte, persone, strade, automobili) e non costruire la "
        "scena attorno a una sola parola-simbolo.\n\n"
        "IL RAGIONAMENTO SI VEDE. Quando il protagonista scopre o decide, il lettore segue il "
        "percorso: cosa osserva (un dettaglio concreto), come lo interpreta, quale alternativa "
        "considera, perché agisce proprio così — in poche righe, dentro l'azione, anche se la "
        "conclusione si rivelerà sbagliata.\n\n"
        "MISTERO. Le ipotesi esoteriche sono possibilità vive del racconto: nessuna cautela "
        "scettica che la scena non chieda. I dubbi, se servono, li portano i personaggi.\n\n"
        "ORARI E VIAGGI. Giorni, ore, mezzi e durate sono SOLO quelli delle ancore del piano: "
        "non inventare altri orari, voli, treni, numeri di volo o durate; dove il piano non ne "
        "dà, non scriverne di precisi. Un viaggio dura quello che dice il piano.\n\n"
        "EPISODI STORICI. Un episodio di un'altra epoca si riconosce subito: la prima riga dice "
        "il luogo e l'anno, e niente dell'epoca del presente vi entra (istituzioni, parole, "
        "oggetti).\n\n"
        "CONTINUITÀ. Luoghi, tempi, oggetti, conoscenze e relazioni cambiano solo con una causa "
        "mostrata. I fatti già fissati dalle scene prima (orari, oggetti, porte, scadenze) "
        "restano veri: un oggetto già sottratto non si sottrae di nuovo, una porta chiusa si "
        "apre o si supera mostrando come, una scadenza ha l'ora già detta, una data va con la "
        "stagione (un codice non deve sembrare una data). Non anticipare gli eventi dei capitoli "
        "successivi.\n\n"
        "FONTI. Le affermazioni di ricerca assegnate alla scena sono materiale da mettere in "
        "scena, non paragrafi da trascrivere, ciascuna per la funzione che il piano le dà (un "
        "enigma, una decisione, un rapporto di forza, un seme per dopo, l'ambiente): un dettaglio "
        "che non serve a quella funzione non entra, anche se è nel materiale. Il tipo di "
        "ciascuna conta: un fatto storico con fonte letta lo può dire il narratore; un'ipotesi, "
        "un'interpretazione o una tradizione trovata in rete entra come scoperta, convinzione o "
        "documento di un personaggio, oppure come canone del romanzo se il quaderno o le "
        "decisioni dell'autore la adottano — mai come dato storico con una data o una cifra che "
        "le fonti non hanno. Non scrivere la bibliografia: la aggiunge il programma.\n\n"
        "Forma: "
        + ("la prima riga è `## ` seguito da un titolo breve e concreto per la scena (non la "
           "frase del piano), poi la prosa" if primo else
           "nessun titolo: prosegui la stessa scena da dove si ferma il testo")
        + "; al massimo un livello di titolo sotto (`###`). Non chiudere il capitolo se non è "
        "l'ultima scena.\n\n"
        f"Non scrivere mai nel testo: {FORBIDDEN}.\n\n" + _NO_INVENTION + "\n\n" + _NOTEBOOK_RULE
    )
    if style_contract:
        sistema += f"\n\nStile richiesto:\n{style_contract}"
    return sistema


def _claim_uses_block(scena: Any) -> str:
    usi = [u for u in getattr(scena, "claim_uses", []) or [] if u.get("claim_id")]
    if not usi:
        return ""
    return ("# A cosa serve ciascuna in questa scena (solo per questo)\n"
            + "\n".join(f"- [{u['claim_id']}] {u.get('function', '')}: {u.get('how', '')}"
                        for u in usi))


def _anchors_block(piano: Any, scena: Any) -> str:
    """Le ancore del capitolo, gli oggetti con i passaggi di mano, il passo del motivo di questa
    scena: i vincoli che la scena non può cambiare."""
    righe = [f"- {s.id}{' ← QUESTA' if s.id == scena.id else ''}: "
             + ", ".join(x for x in (s.day, s.time, s.place) if x)
             + (f"; arrivo: {s.arrival}" if s.arrival else "")
             for s in piano.scenes if s.day or s.time]
    pezzi = []
    if righe:
        pezzi.append("# Le ancore del capitolo (giorno, ora, luogo, viaggio): le SOLE che usi\n"
                     + "\n".join(righe))
    oggetti = getattr(piano, "objects", None) or []
    if oggetti:
        pezzi.append("# Gli oggetti e i documenti: chi li ha (niente cambia mano senza un "
                     "passaggio in scena)\n" + "\n".join(
                         f"- {o.get('object')}: viene da {o.get('origin') or '?'}; all'inizio "
                         f"{o.get('holder_start') or '?'}; passaggi: {o.get('transfers') or '—'}; "
                         f"alla fine {o.get('holder_end') or '?'}" for o in oggetti))
    registri = getattr(piano, "registers_block", "")
    if registri:
        pezzi.append(registri)
    passi = [m for m in getattr(piano, "motif_chain", None) or [] if m.get("scene") == scena.id]
    if passi:
        pezzi.append("# Il motivo in questa scena (solo questo passo; non spiegare la tesi)\n"
                     + "\n".join(f"- {m.get('motif')}: {m.get('step')} — {m.get('what')}"
                                 for m in passi))
    return "\n\n".join(pezzi)


def _bridge_block(pack: Any, piano: Any) -> str:
    """Il ponte dal capitolo prima e i fili aperti: li mette in scena la prima scena."""
    righe = []
    if piano.bridge_text():
        righe.append("- Il passaggio da mettere in scena all'inizio (non in una riga di "
                     f"riassunto): {piano.bridge_text()}")
    for t in piano.threads:
        if t.get("thread") and t.get("handling"):
            righe.append(f"- Filo aperto «{t['thread']}»: {t['handling']}")
    if not righe:
        return ""
    return ("# Dal capitolo prima a questo: la catena indizio → decisione del protagonista → "
            "spostamento → nuovo obiettivo, e cosa ne è dei fili aperti\n" + "\n".join(righe))


def _scene_user(pack: Any, piano: Any, scena: Any, unita: Any, testi: list[str],
                next_opening: str = "", facts: str = "") -> str:
    coda, prima = _tail_words(testi, CONTEXT_WORDS)
    canone = pack.canon
    if piano.canon:
        canone += "\n\n## Regole del quaderno che questo capitolo deve rispettare\n" + "\n".join(
            f"- {r}" for r in piano.canon)
    testa = [
        f"# Il libro: il canone da rispettare\n{canone}",
        pack.voice_block(),
        pack.chapter_block(),
        (f"# Il capitolo, dal suo piano\n- Obiettivo: {piano.goal}\n- Domanda drammatica: "
         f"{piano.dramatic_question}\n- Svolta: {piano.turn}\n- Partenza: {piano.start}\n"
         f"- Arrivo: {piano.end}"),
        "# Il piano delle scene\n" + piano.outline(scena.id),
        f"# La scena da scrivere: {scena.id}\n{scena.card()}",
        _anchors_block(piano, scena),
        pack.claims_block(scena.claim_ids, title="# Il materiale di ricerca per QUESTA scena "
                          "(solo questo: il resto è di altre scene)") if scena.claim_ids else "",
        _claim_uses_block(scena) if scena.claim_ids else "",
        pack.decisions_block(),
        pack.told_block(),
        ("# Già stabilito in questo capitolo dal piano: non rispiegarlo\n"
         + "\n".join(f"- {t}" for t in piano.already_known)) if piano.already_known else "",
        ("# I motivi che questo capitolo riprende, con la loro funzione nuova\n"
         + "\n".join(f"- {m.get('motif')}: {m.get('new_function')}" for m in piano.motifs))
        if piano.motifs else pack.motifs_block(),
        pack.inventions_block(),
        pack.memory_block(),
    ]
    if not testi:
        testa += [pack.start_block(), pack.previous_tail_block(), _bridge_block(pack, piano)]
    testa += [
        facts,
        ("# Le scene prima (in breve)\n" + "\n".join(prima)) if prima else "",
        (f"# Il capitolo finora, alla lettera\n{coda}" if coda else
         "# Il capitolo finora\n(è la prima scena del capitolo)"),
        (f"# Quello che viene subito dopo (non riscriverlo: la scena deve portare lì)\n"
         f"{next_opening}") if next_opening else "",
        f"# Da scrivere adesso\nScena {scena.id}. {unita.task(scena)}",
    ]
    return "\n\n".join(p for p in testa if p)


def _clean_unit(testo: str, first: bool, titolo_capitolo: str) -> str:
    """Una scena comincia con il suo titolo ``##``; la parte successiva di una scena non ne ha."""
    if first:
        return _clean_section(testo, titolo_capitolo)
    return _clean_continuation(testo)


def _missing_elements(scena: Any) -> str:
    voci = [("ostacolo", scena.obstacle), ("indizio", scena.clue), ("decisione", scena.decision),
            ("conseguenza", scena.consequence), ("ponte verso la scena dopo", scena.bridge)]
    return "; ".join(f"{k}: {v}" for k, v in voci if v)


def _write_unit(client: SupportsChat, pack: Any, piano: Any, scena: Any, unita: Any,
                testi: list[str], *, model, style_contract: str, titolo: str,
                next_opening: str = "", facts: str = "") -> str:
    """Una chiamata di scrittura: una scena o una sua parte. Un seguito solo se la risposta è
    stata tagliata, o se è corta e le manca qualcosa del piano."""
    messaggi = [{"role": "system", "content": _scene_system(pack, unita, style_contract)},
                {"role": "user", "content": _scene_user(pack, piano, scena, unita, testi,
                                                        next_opening, facts)}]
    primo = unita.titled
    testo = _clean_unit(client.chat(messaggi, model=model), primo, titolo)
    tagliata = getattr(client, "last_finish_reason", "") == "length"
    if tagliata or _count(testo) < SHORT_SCENE * unita.words:
        seguito = client.chat(
            [*messaggi, {"role": "assistant", "content": testo},
             {"role": "user", "content": CONTINUE_CUT if tagliata else (
                 "La scena è più breve del previsto. Se nel testo manca qualcosa di quello che "
                 f"il piano le chiede ({_missing_elements(scena)}), mettilo in scena adesso, in "
                 "azione e dialogo, proseguendo da dove ti sei fermato, senza titolo e senza "
                 "ripetere niente. Se c'è già tutto, rispondi soltanto " + DONE + ".")}],
            model=model)
        seguito = _clean_continuation(seguito)
        if seguito:
            testo = testo + "\n\n" + seguito
    testo = _condense(client, testo, unita, primo, titolo, pack, model=model)
    return _fix_repetitions(client, messaggi, testo, titolo, model=model,
                            clean=lambda t: _clean_unit(t, primo, titolo))


#: Una scena più lunga di così rispetto alla sua misura si condensa (la bozza v3 del cap. 11 era
#: del 17% oltre la misura, con una scena al 50%).
LONG_SCENE = 1.3


def _condense(client: SupportsChat, testo: str, unita: Any, primo: bool, titolo: str, pack: Any,
              *, model) -> str:
    """Una scena troppo lunga torna alla sua misura togliendo spiegazioni, commenti e ripetizioni,
    mai eventi, dialoghi, indizi, oggetti, orari o decisioni. Si tiene solo se resta nella
    forbice della misura."""
    parole = _count(testo)
    if not unita.words or parole <= LONG_SCENE * unita.words:
        return testo
    sistema = (
        f"Riduci questa scena a circa {unita.words} parole. Togli spiegazioni, commenti del "
        "narratore, riassunti e ripetizioni; tieni OGNI evento, battuta di dialogo che conta, "
        "indizio, oggetto, orario, luogo, decisione e conseguenza, e la voce. "
        + ("La prima riga è il titolo, identico. " if primo else "Senza titolo. ")
        + f"Lingua: {pack.language}. Rispondi solo con la scena."
    )
    try:
        corto = _clean_unit(client.chat([{"role": "system", "content": sistema},
                                         {"role": "user", "content": testo}], model=model),
                            primo, titolo)
    except Exception:  # noqa: BLE001 - senza condensare la scena resta com'è
        return testo
    if 0.8 * unita.words <= _count(corto) <= 1.15 * unita.words:
        return corto
    return testo


def _load_scene_partial(path, budget: int) -> dict[str, Any]:
    import json

    try:
        dati = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if dati.get("kind") != "scenes" or int(dati.get("budget") or 0) != budget:
        return {}
    return dati


def _save_scene_partial(path, budget: int, piano: Any, testi: dict[str, str],
                        extended: bool = False, facts: dict[str, Any] | None = None) -> None:
    import json
    from dataclasses import asdict

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "scenes", "budget": budget, "plan": asdict(piano),
                                "texts": testi, "extended": extended, "facts": facts or {}},
                               ensure_ascii=False, indent=1), encoding="utf-8")


def _assemble(piano: Any, testi: dict[str, str], cap: int) -> list[str]:
    from bookwriter.forge import scenes as sc

    return [testi[u.key] for u in sc.units(piano, cap) if testi.get(u.key)]


def _draft_by_scenes(project: BookProject, state: ForgeState, client: SupportsChat,
                     chapter: dict[str, Any], *, tools: ChapterTools, model, style_contract: str,
                     section_words: int, should_stop, on_section) -> ChapterResult:
    """Il capitolo di un racconto: il piano delle scene, poi una chiamata per scena (o per parte
    di scena, se la scena supera quello che il modello scrive in una risposta).

    Il piano e ogni scena scritta vanno nel lavoro (``.forge/partial``) appena ci sono: una
    stesura interrotta riparte dalla scena che manca, con lo stesso piano.
    """
    from bookwriter.forge import scenes as sc

    numero = int(chapter.get("number") or 0)
    titolo = str(chapter.get("title") or f"Capitolo {numero}")
    budget = int(chapter.get("words") or 0)
    cap = max(400, section_words or 1500)
    pack = tools.pack
    percorso = partial_path(project, numero, titolo, trial=tools.trial)
    lavoro = _load_scene_partial(percorso, budget)
    from bookwriter.forge import timeline as tl

    fatti: dict[str, Any] = {}
    if lavoro.get("plan"):
        piano = sc.plan_from_dict(lavoro["plan"])
        testi: dict[str, str] = {str(k): str(v) for k, v in (lavoro.get("texts") or {}).items()}
        fatti = dict(lavoro.get("facts") or {})
    else:
        piano = tools.planner(pack) if tools.planner else sc.from_points(pack)
        testi = {}
        _save_scene_partial(percorso, budget, piano, testi)
    unita = sc.units(piano, cap)
    risultato = ChapterResult(number=numero, title=titolo, budget=budget, sections=len(unita))
    # Il cancello prima della stesura (specifica metodologica del 25/10/2026, priorità 0): un
    # piano con blocker non si scrive, salvo deroga motivata dell'autore, che resta nel piano.
    bloccanti = piano.blockers()
    if bloccanti and not (tools.waiver or piano.waiver):
        risultato.blocked = bloccanti
        risultato.plan_path = str(sc.plan_path(project, numero, titolo, trial=tools.trial))
        return risultato
    if bloccanti and tools.waiver and not piano.waiver:
        piano.waiver = tools.waiver
        sc.save(project, piano)

    estesa_gia = {"si": bool(lavoro.get("extended"))}

    def salva(estesa: bool = False) -> None:
        estesa_gia["si"] = estesa_gia["si"] or estesa
        _save_scene_partial(percorso, budget, piano, testi, estesa_gia["si"], fatti)

    def fissati(scena_id: str) -> str:
        """I fatti delle scene già scritte prima di questa, nell'ordine del piano."""
        if tools.facts is None:
            return ""
        ordine = [x.id for x in piano.scenes]
        prima = ordine[:ordine.index(scena_id)] if scena_id in ordine else []
        registri = [tl.record_from_dict(fatti[k]) for k in prima if k in fatti]
        inizio = list(pack.previous.end_state.lines()) if getattr(pack, "previous", None) else []
        return tl.facts_block(registri, inizio)

    def registra(scena) -> None:
        """Finita una scena (tutte le sue parti), se ne registrano i fatti per le scene dopo."""
        if tools.facts is None or scena is None or scena.id in fatti:
            return
        parti = [u for u in sc.units(piano, cap) if u.scene == scena.id]
        if not all(u.key in testi for u in parti):
            return
        testo = "\n\n".join(testi[u.key] for u in parti)
        ordine = [x.id for x in piano.scenes]
        prima = [tl.record_from_dict(fatti[k]) for k in ordine[:ordine.index(scena.id)]
                 if k in fatti]
        registro = tools.facts(testo.split("\n", 1)[0], testo, scena, prima)
        if registro is not None and not getattr(registro, "error", ""):
            fatti[scena.id] = asdict(registro)

    def scrivi(lista, inizio: int = 0, prossima: dict[str, str] | None = None) -> bool:
        for i, u in enumerate(lista):
            if u.key in testi:
                continue
            if should_stop and should_stop():
                salva()
                return False
            if on_section:
                on_section(inizio + i + 1, inizio + len(lista))
            scena = piano.scene(u.scene)
            # il testo che viene prima di questa unità, nell'ordine del piano
            ordine = [x.key for x in sc.units(piano, cap)]
            prima = [testi[k] for k in ordine[:ordine.index(u.key)] if k in testi]
            testi[u.key] = _write_unit(client, pack, piano, scena, u, prima, model=model,
                                       style_contract=style_contract, titolo=titolo,
                                       next_opening=(prossima or {}).get(u.key, ""),
                                       facts=fissati(u.scene))
            registra(scena)
            salva()
        return True

    if not scrivi(unita):
        risultato.stopped = True
        risultato.words = _count("\n\n".join(_assemble(piano, testi, cap)))
        return risultato

    # Un capitolo rimasto corto riceve scene in più dal racconto, non parole in più.
    scritto = "\n\n".join(_assemble(piano, testi, cap))
    if budget and _count(scritto) < SHORT_CHAPTER * budget and not lavoro.get("extended"):
        nuove = sc.extra_beats(client, piano, pack, budget - _count(scritto), scritto,
                               model=model)
        prossime: dict[str, str] = {}
        for s in nuove:
            posto = next(i for i, x in enumerate(piano.scenes) if x.id == s.after)
            piano.scenes.insert(posto + 1, s)
            dopo = piano.scenes[posto + 2] if posto + 2 < len(piano.scenes) else None
            if dopo is not None:
                inizio_dopo = next((testi[u.key] for u in sc.units(piano, cap)
                                    if u.scene == dopo.id and u.key in testi), "")
                prossime[s.id] = " ".join(inizio_dopo.split()[:150])
        if nuove:
            sc.save(project, piano)
        # le scene in più si chiedono una volta sola, anche se la stesura si interrompe
        salva(estesa=True)
        if nuove:
            nuove_unita = [u for u in sc.units(piano, cap) if u.scene in {s.id for s in nuove}]
            risultato.sections += len(nuove_unita)
            if not scrivi(nuove_unita, len(unita), prossime):
                risultato.stopped = True
                return risultato

    corpo = "\n\n".join(_assemble(piano, testi, cap))
    note = tools.notes(piano.claim_ids()) if tools.notes else []
    if note:
        corpo += "\n\n## Note bibliografiche\n\n" + "\n\n".join(note)
    testo = _clean(f"# Capitolo {numero}: {titolo}\n\n" + corpo, numero, titolo)
    risultato.words = _count(testo)
    _finish(project, numero, titolo, testo, risultato, tools, piano)
    try:
        percorso.unlink()
    except OSError:
        pass
    return risultato


def _reconcile(state: ForgeState, client: SupportsChat, testo: str, chapter: dict[str, Any],
               *, model: str | None, style_contract: str) -> str:
    """Un solo ritentativo per riportare il capitolo dentro il budget. Uno, non tre."""
    budget = int(chapter.get("words") or 0)
    attuali = _count(testo)
    verso = ("Allungalo sviluppando gli argomenti già presenti, con esempi e passaggi che "
             "mancano: non aggiungere argomenti nuovi e non ripeterti."
             if attuali < budget else
             "Accorcialo togliendo le ripetizioni, le premesse e i giri di frase: non togliere "
             "argomenti né esempi.")
    sistema = (
        f"Riscrivi il capitolo portandolo a circa {budget} parole (adesso ne ha {attuali}). "
        f"{verso} Mantieni la stessa struttura, lo stesso titolo e le stesse note bibliografiche. "
        f"Lingua: {nb.language_name(state)}. "
        f"Non scrivere mai nel testo: {FORBIDDEN}."
    )
    if style_contract:
        sistema += f"\n\nStile richiesto:\n{style_contract}"
    try:
        return client.chat(
            [{"role": "system", "content": sistema}, {"role": "user", "content": testo}],
            model=model,
        )
    except Exception:
        return ""


_FENCE = re.compile(r"^\s*```(?:markdown|md)?\s*\n(.*)\n```\s*$", re.DOTALL)


def _clean(testo: str, numero: int, titolo: str) -> str:
    """Toglie l'incarto del modello e garantisce il titolo che il compositore si aspetta."""
    testo = (testo or "").strip()
    if match := _FENCE.match(testo):
        testo = match.group(1).strip()
    righe = testo.splitlines()
    prima = next((i for i, r in enumerate(righe) if r.strip()), None)
    intestazione = f"# Capitolo {numero}: {titolo}"
    if prima is None:
        return intestazione + "\n"
    if righe[prima].strip().startswith("# "):
        righe[prima] = intestazione if not re.match(
            r"^#\s+(?:Capitolo|Chapter)\s*\d+", righe[prima].strip()) else righe[prima].rstrip()
    else:
        righe.insert(prima, intestazione)
        righe.insert(prima + 1, "")
    return "\n".join(righe).strip() + "\n"


def _count(testo: str) -> int:
    """Le parole del capitolo, senza i titoli: è il testo che il lettore legge."""
    return count_words(testo)


def _clean_continuation(seguito: str) -> str:
    """Il seguito di una sezione o di una scena: senza incarto, vuoto se il modello ha detto che
    non c'era niente da aggiungere."""
    seguito = (seguito or "").strip()
    if match := _FENCE.match(seguito):
        seguito = match.group(1).strip()
    if re.fullmatch(r"\W*(?:FATTO|DONE)\W*", seguito, re.IGNORECASE):
        return ""
    righe = seguito.splitlines()
    while righe and righe[0].strip().startswith("#"):  # una continuazione non ha titolo
        righe.pop(0)
    return "\n".join(righe).strip()


# --------------------------------------------------------------------------- #
#  tutti i capitoli
# --------------------------------------------------------------------------- #
def write_all(
    project: BookProject,
    state: ForgeState,
    client: SupportsChat,
    *,
    model: str | None = None,
    style_contract: str = "",
    research_for: Callable[[dict[str, Any]], str] | None = None,
    memory_for: Callable[[dict[str, Any]], list[str]] | None = None,
    decisions_for: Callable[[dict[str, Any]], str] | None = None,
    review_for: Callable[[dict[str, Any]], str] | None = None,
    progress: Callable[[int, int], None] | None = None,
    note: Callable[[object], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    section_words: int = 1500,
    tools_for: Callable[[dict[str, Any]], ChapterTools] | None = None,
) -> WritingReport:
    """Scrive tutti i capitoli che mancano, uno alla volta, salvando ciascuno appena è finito.

    Si può fermare fra un capitolo e l'altro: quello che è già su disco resta, e riprendendo si
    riparte dal primo file che manca — non da dove si crede di essere arrivati.
    """
    report = WritingReport()
    da_fare = pending_chapters(project, state)
    totale = len(da_fare)
    di_fila = 0  # fallimenti consecutivi, per distinguere un capitolo difficile da un guasto
    for i, capitolo in enumerate(da_fare):
        if should_stop and should_stop():
            report.stopped = True
            report.skipped = [int(c.get("number") or 0) for c in da_fare[i:]]
            break
        if progress:
            progress(i, totale)
        numero = int(capitolo.get("number") or 0)
        titolo = str(capitolo.get("title") or f"Capitolo {numero}")
        try:
            risultato = draft_chapter(
                project, state, client, capitolo, model=model, style_contract=style_contract,
                research=research_for(capitolo) if research_for else "",
                memory=memory_for(capitolo) if memory_for else None,
                decisions=decisions_for(capitolo) if decisions_for else "",
                review=review_for(capitolo) if review_for else "",
                section_words=section_words, should_stop=should_stop,
                on_section=(lambda k, n, c=numero: note(("section", c, k, n))) if note else None,
                tools=tools_for(capitolo) if tools_for else None,
            )
        except LLMError as errore:
            # Un capitolo che non riesce non deve portarsi dietro gli altri: su un libro da venti
            # capitoli, perdere il giro all'undicesimo significa non tentare nemmeno i nove che
            # sarebbero venuti bene. Il file non è stato scritto, quindi al prossimo giro questo
            # capitolo torna fra quelli da fare: niente da segnare, basta guardare il disco.
            report.failed.append((numero, titolo, str(errore)))
            di_fila += 1
            if note:
                note(f"{numero}. {titolo} — non riuscito: {errore}")
            if di_fila >= CONSECUTIVE_FAILURES:
                # Tre di fila senza nessuno riuscito in mezzo non è il capitolo: è il modello o
                # il server. Insistere sui rimanenti costa un'attesa piena per ciascuno — con i
                # tempi di serie, ore — per riportare lo stesso errore venti volte.
                report.aborted = (
                    f"Fermato dopo {di_fila} capitoli non riusciti di fila: non è il capitolo, "
                    "è il modello o il server. Controlla che risponda e ripremi: si riparte da "
                    "quelli che mancano."
                )
                report.skipped = [int(c.get("number") or 0) for c in da_fare[i + 1:]]
                break
            continue
        if risultato.blocked:
            # Il piano ha blocker: non si scrive e non si passa al capitolo dopo, che ne dipende.
            report.blocked = (numero, titolo, risultato.blocked)
            report.skipped = [int(c.get("number") or 0) for c in da_fare[i:]]
            if note:
                note(f"{numero}. {titolo} — fermato: il piano ha difetti bloccanti")
            break
        if risultato.stopped:
            # Fermato a metà capitolo: non è scritto, e le sezioni fatte restano nel lavoro.
            report.stopped = True
            report.skipped = [int(c.get("number") or 0) for c in da_fare[i:]]
            break
        di_fila = 0
        report.written.append(risultato)
        if note:
            note(risultato.line())
    if progress:
        progress(totale, totale)
    return report


# --------------------------------------------------------------------------- #
#  pagine iniziali e finali
# --------------------------------------------------------------------------- #
#: Le sezioni che il libro può avere davanti e dietro, con il nome del file e cosa sono.
MATTER = {
    "introduzione": ("front", "L'introduzione: comincia il libro, fa parte del suo argomento."),
    "prefazione": ("front", ("La prefazione: racconta come il libro è venuto al mondo. "
                             "Non è l'introduzione.")),
    "avvertenza": ("front", ("L'avvertenza: il limite di quello che il libro può fare. In materia "
                             "di salute, diritto, denaro o psiche non è un ornamento.")),
    "glossario": ("back", "Il glossario: i termini che il libro conia o prende in prestito."),
    "biografia": ("back", ("La biografia dell'autore, 100-150 parole: chi è, cosa lo qualifica "
                           "per QUESTO libro, un dettaglio umano, e cosa deve fare il lettore "
                           "dopo.")),
    "ringraziamenti": ("back", "I ringraziamenti, coi nomi scritti come li vogliono loro."),
}


def draft_matter(
    project: BookProject,
    state: ForgeState,
    client: SupportsChat,
    kind: str,
    *,
    model: str | None = None,
    style_contract: str = "",
):
    """Scrive una pagina iniziale o finale e la salva subito, come per i capitoli."""
    from bookwriter.forge.project import slugify
    from bookwriter.forge.spec import _brief

    if kind not in MATTER:
        raise ValueError(f"Sezione sconosciuta: {kind} (usa {'|'.join(MATTER)})")
    dove, descrizione = MATTER[kind]
    project.scaffold()
    cartella = project.front_dir if dove == "front" else project.back_dir

    sistema = (
        f"Scrivi questa sezione di un libro, in prosa finita. {descrizione} "
        f"La prima riga è il titolo della sezione, di primo livello (`# ...`). "
        f"Lingua: {nb.language_name(state)}. "
        f"Non scrivere mai nel testo: {FORBIDDEN}."
    )
    if style_contract:
        sistema += f"\n\nStile richiesto:\n{style_contract}"
    struttura = "\n".join(f"- Capitolo {c.get('number')}: {c.get('title')} — {c.get('does')}"
                          for c in state.outline)
    testo = client.chat(
        [{"role": "system", "content": sistema},
         {"role": "user", "content": f"# Il quaderno del libro\n{_brief(state)}\n\n"
                                     f"# La struttura\n{struttura}"}],
        model=model,
    ).strip()
    if match := _FENCE.match(testo):
        testo = match.group(1).strip()
    path = cartella / f"{slugify(kind)}.md"
    path.write_text(testo + "\n", encoding="utf-8")
    return path
