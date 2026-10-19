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
from dataclasses import dataclass, field
from typing import Any, Protocol

from bookwriter.forge import notebook as nb
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
    "arricchire quel contenuto: usalo attivamente (dettagli, descrizioni, esperienze, citazioni "
    "con la loro fonte). Una cosa sola non si inventa mai: fonti, cifre, nomi di autori, titoli e "
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

    @property
    def drift(self) -> float:
        return abs(self.words - self.budget) / self.budget if self.budget else 0.0

    def line(self) -> str:
        scostamento = f" ({self.words} parole su {self.budget})" if self.budget else \
                      f" ({self.words} parole)"
        avviso = "  ⚠ fuori budget" if self.budget and self.drift > TOLERANCE else ""
        return f"{self.number}. {self.title}{scostamento}{avviso}"


@dataclass
class WritingReport:
    written: list[ChapterResult] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    #: I capitoli che il modello non è riuscito a scrivere: (numero, titolo, motivo).
    failed: list[tuple[int, str, str]] = field(default_factory=list)
    stopped: bool = False
    #: Perché il giro si è interrotto da solo, se è successo. Vuoto se è andato fino in fondo.
    aborted: str = ""

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
        if self.skipped:
            righe.append(f"Non tentati: {self.skipped}")
        return "\n".join(righe)


# --------------------------------------------------------------------------- #
#  cosa scrivere adesso
# --------------------------------------------------------------------------- #
def pending_chapters(project: BookProject, state: ForgeState) -> list[dict[str, Any]]:
    """I capitoli previsti dalla specifica che **non sono su disco**. Guarda i file, non lo stato."""
    scritti = project.written_chapters()
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
) -> ChapterResult:
    """Scrive un capitolo e **lo salva subito**. Ritorna cosa è stato scritto e quanto è lungo.

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

    if budget and section_words and budget > section_words:
        return _draft_by_sections(
            project, state, client, chapter, model=model, style_contract=style_contract,
            research=research, memory=memory or [], decisions=decisions, review=review,
            section_words=section_words, should_stop=should_stop, on_section=on_section)

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

    # Prima si scrive. Sempre. Quello che sta solo in memoria non esiste.
    path = project.chapter_path(numero, titolo)
    path.write_text(testo, encoding="utf-8")
    risultato.path = path
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
#: Una sezione più corta di questa frazione del suo obiettivo si prolunga, una volta sola.
SHORT_SECTION = 0.7

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


def partial_path(project: BookProject, number: int, title: str):
    """Dove vivono le sezioni di un capitolo non ancora finito.

    Fuori da ``chapters/``: un capitolo a metà non deve risultare scritto, né per ``status`` né
    per chi guarda i file. Quando le sezioni sono tutte, il capitolo vero si scrive e questo file
    sparisce.
    """
    from bookwriter.forge.project import slugify

    return project.root / ".forge" / "partial" / f"{number:02d}_{slugify(title)}.json"


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
        f"{numero} «{chapter.get('title')}». Circa {parole} parole: non meno di "
        f"{int(parole * 0.8)}.\n\n"
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
        mancano = max(200, obiettivo - _count(testo))
        seguito = client.chat(
            [*messaggi, {"role": "assistant", "content": testo},
             {"role": "user", "content": (
                 f"Prosegui ESATTAMENTE da dove ti sei interrotto, per altre circa {mancano} "
                 "parole: senza ripetere nulla di quanto hai scritto, senza un nuovo titolo di "
                 "sezione e senza salutare o commentare. Se la sezione è già completa nei "
                 "contenuti, sviluppala ancora con esempi e passaggi concreti.")}],
            model=model)
        seguito = (seguito or "").strip()
        if match := _FENCE.match(seguito):
            seguito = match.group(1).strip()
        if seguito:
            testo = testo + "\n\n" + seguito
    # Un modello che si avvita ripete paragrafi o frasi: si controlla ciò che ha scritto, e la
    # sezione si rifà UNA volta, dicendogli cosa ripeteva. Se ripete ancora, si tolgono i
    # paragrafi identici: meglio una sezione un po' più corta che due volte lo stesso testo.
    ripetizioni = jsoncheck.repeated_text(testo)
    if ripetizioni:
        rifatta = client.chat(
            [*messaggi, {"role": "user", "content": (
                "Nel testo di questa sezione ci sono ripetizioni (" + "; ".join(ripetizioni[:3])
                + "). Riscrivi la sezione dall'inizio, senza ripetere paragrafi né frasi, con la "
                "stessa forma e circa la stessa lunghezza.")}],
            model=model)
        rifatta = _clean_section(rifatta, titolo)
        migliore = rifatta if _count(rifatta) >= 0.5 * _count(testo) else testo
        testo = migliore if not jsoncheck.repeated_text(migliore) else jsoncheck.dedupe_paragraphs(migliore)
    return testo


def _draft_by_sections(project, state, client, chapter, *, model, style_contract, research,
                       memory, decisions, section_words, should_stop, on_section, review=""):
    numero = int(chapter.get("number") or 0)
    titolo = str(chapter.get("title") or f"Capitolo {numero}")
    budget = int(chapter.get("words") or 0)
    percorso = partial_path(project, numero, titolo)
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
    path = project.chapter_path(numero, titolo)
    path.write_text(testo, encoding="utf-8")  # solo adesso il capitolo è scritto
    risultato.path = path
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


_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _count(testo: str) -> int:
    """Le parole del capitolo, senza i titoli: è il testo che il lettore legge."""
    corpo = "\n".join(r for r in (testo or "").splitlines() if not r.strip().startswith("#"))
    return len(_WORD.findall(corpo))


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
