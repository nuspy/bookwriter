"""Cosa si può fare adesso, nella scheda «Scrivi da zero»: lo stato dei comandi e le parole.

Un solo punto decide due cose, guardando il libro e il disco e nient'altro:

* di ogni comando di fase, se è **consigliato** (è il passo atteso), **disponibile** (si può
  fare ma non è quello che ci si aspetta) o **superato** (non ha più senso: grigio e spento, con
  il motivo nel suggerimento);
* la frase che dice all'autore cosa tocca a lui.

Le due cose stanno insieme perché devono dire la stessa cosa: il colore del pulsante e la frase
nel riquadro di stato non possono contraddirsi. Il colore non basta da solo (chi non distingue i
colori deve poter capire lo stesso), per questo la frase nomina sempre il pulsante da premere.
"""

from __future__ import annotations

from dataclasses import dataclass

from bookwriter.forge import editor as ed
from bookwriter.forge import interview as iv
from bookwriter.forge import writing as wr
from bookwriter.forge import notebook as nb
from bookwriter.forge import spec as sp
from bookwriter.forge.project import slugify
from bookwriter.gui.i18n import t

CONSIGLIATO, DISPONIBILE, SUPERATO = "consigliato", "disponibile", "superato"


@dataclass(frozen=True)
class Command:
    """Lo stato di un comando di fase; ``why`` spiega perché è superato."""

    state: str
    why: str = ""


# --------------------------------------------------------------------------- #
#  il pulsante che porta avanti
# --------------------------------------------------------------------------- #
def advance_label(phase: str) -> str | None:
    """Il testo del pulsante di avanzamento: dice dove porta. ``None`` all'ultima fase."""
    if phase == "interview":
        return t("Passa alla specifica")
    if phase == "spec":
        return t("Passa alla ricerca")
    if phase == "research":
        return t("Passa alla revisione")
    if phase == "review":
        return t("Passa alla stesura")
    if phase == "writing":
        return t("Passa alla produzione")
    if phase == "production":
        return t("Passa alla composizione")
    if phase == "build":
        return t("Passa ai controlli e alla consegna")
    return None


# --------------------------------------------------------------------------- #
#  i fatti, letti dal disco una volta sola
# --------------------------------------------------------------------------- #
@dataclass
class Facts:
    phase: str
    exchanges: int
    interview_closed: bool
    spec_exists: bool
    spec_approved: bool
    has_outline: bool
    landscape_done: bool
    chapters_sourced: bool
    research_done: bool
    research_declined: bool
    written: int
    planned: int
    config_exists: bool
    cover: bool
    built: bool
    notes: bool
    requirements_met: bool
    #: le questioni aperte su cui l'autore deve ancora decidere
    questions_pending: int = 0
    #: le parole che il quaderno dichiara per il libro (None se non lo dice), e quelle che la
    #: struttura prevede: se divergono, l'autore deve saperlo prima di approvare.
    declared_words: int | None = None
    outline_words: int = 0
    n_chapters: int = 0
    #: capitoli scritti molto sotto il loro budget (numero, parole, budget)
    short_chapters: tuple = ()
    #: i punti della revisione e quanti sono ancora da decidere (la revisione è facoltativa)
    review_points: int = 0
    review_pending: int = 0
    #: la ricerca c'è ma nel formato precedente (solo .md): per le funzioni nuove non è fatta
    research_old_format: bool = False
    #: dove riprenderebbe la ricerca: ``(numero del capitolo, punto, punti del capitolo)``, o None
    research_next: tuple | None = None
    #: il lavoro (``nuovo``, ``completa``, ``aggiorna``) e se il manoscritto è caricato
    mode: str = "nuovo"
    manuscript_loaded: bool = False
    #: capitoli che la revisione editoriale ha lasciato da riguardare (voci critiche aperte)
    to_review: int = 0


def _review_points(forge) -> list:
    dati = forge.review_data()
    return list(dati.points) if dati else []


def read_facts(forge) -> Facts:
    """Raccoglie dal libro e dal disco quel che serve a decidere. Nessun altro lo rilegge."""
    st = forge.status()
    s = forge.state
    prossimo = None
    if s.phase == "research" and not s.research_done and s.outline:
        proseguo = forge.research_progress().get("next")
        if proseguo:
            prossimo = (proseguo["number"], proseguo["point"], proseguo["points"])
    p = forge.project
    previsti = len(st.planned or s.outline)
    fonti = bool(s.outline) and all(
        (p.research_dir / f"{int(c.get('number') or 0):02d}_{slugify(str(c.get('title', '')))}.md")
        .exists() for c in s.outline)
    return Facts(
        phase=s.phase,
        exchanges=iv.exchanges(s),
        interview_closed=s.interview_closed,
        spec_exists=p.spec_path.exists(),
        spec_approved=s.spec_approved,
        has_outline=bool(s.outline),
        landscape_done=(p.research_dir / "00_landscape.md").exists(),
        chapters_sourced=fonti,
        research_done=s.research_done,
        research_declined=s.research_declined,
        written=(previsti - len(wr.pending_chapters(forge.project, s))
                 if getattr(forge, "from_manuscript", False) else len(st.written)),
        planned=previsti,
        config_exists=p.config_path.exists(),
        cover=bool(st.cover),
        built=bool(st.built),
        notes=p.notes_path.exists(),
        requirements_met=not forge.requirements(),
        questions_pending=len(forge.pending_questions()) if s.research_done else 0,
        review_points=len(_review_points(forge)),
        review_pending=sum(1 for p in _review_points(forge)
                           if p.status in ("aperto", "in_discussione", "rimandato")),
        declared_words=sp.parse_words(nb.get(s, "length"), len(s.outline) or None),
        outline_words=sum(int(c.get("words") or 0) for c in s.outline),
        n_chapters=len(s.outline),
        short_chapters=tuple((n, w, b) for n, w, b in forge.short_chapters()),
        research_old_format=forge.research_format_old(),
        research_next=prossimo,
        mode=getattr(s, "mode", "nuovo") or "nuovo",
        manuscript_loaded=bool((getattr(s, "manuscript", None) or {}).get("imported")),
        to_review=len(ed.review_flags(p)),
    )


# --------------------------------------------------------------------------- #
#  lo stato dei comandi
# --------------------------------------------------------------------------- #
def command_states(forge, facts: Facts | None = None) -> dict[str, Command]:
    """Lo stato di tutti i comandi di fase, per chiave (``«fase.comando»``)."""
    f = facts or read_facts(forge)
    tutte = f.planned > 0 and f.written >= f.planned
    ricerca_finita = f.research_done or f.research_declined

    def consigliato_se(cond: bool) -> Command:
        return Command(CONSIGLIATO if cond else DISPONIBILE)

    stati: dict[str, Command] = {}

    # --- intervista
    stati["interview.finish"] = (
        Command(SUPERATO, t("La conversazione è già chiusa. Puoi riprendere a scrivere e "
                            "chiuderla di nuovo.")) if f.interview_closed else Command(DISPONIBILE))

    # --- specifica
    # Dopo l'approvazione «Proponi» resta disponibile: rigenerare è previsto (toglie
    # l'approvazione) e chi ci ripensa non deve restare chiuso fuori.
    stati["spec.make"] = consigliato_se(not f.spec_exists)
    stati["spec.edit"] = (Command(DISPONIBILE) if f.spec_exists else
                          Command(SUPERATO, t("La specifica non è ancora stata scritta.")))
    if not f.spec_exists:
        stati["spec.approve"] = Command(SUPERATO, t("La specifica non è ancora stata scritta."))
    elif f.spec_approved:
        stati["spec.approve"] = Command(SUPERATO, t("La specifica è già approvata."))
    else:
        stati["spec.approve"] = Command(CONSIGLIATO)

    # --- ricerca
    stati["research.landscape"] = consigliato_se(not f.landscape_done and not ricerca_finita)
    if not f.has_outline:
        stati["research.chapters"] = Command(
            SUPERATO, t("Prima serve la specifica con l'elenco dei capitoli."))
    else:
        stati["research.chapters"] = consigliato_se(
            f.landscape_done and not f.chapters_sourced and not f.research_declined)
    if f.research_done:
        stati["research.decline"] = Command(SUPERATO, t("La ricerca è già fatta."))
    elif f.research_declined:
        stati["research.decline"] = Command(SUPERATO, t("Hai già deciso di scrivere senza ricerca."))
    else:
        stati["research.decline"] = Command(DISPONIBILE)

    # --- revisione (facoltativa: niente qui è mai un requisito)
    stati["review.generate"] = consigliato_se(f.review_points == 0 and f.research_done)
    stati["review.skip"] = Command(DISPONIBILE)
    stati["review.free"] = (Command(DISPONIBILE) if f.review_points
                            else Command(SUPERATO, t("Prima prepara i punti da discutere.")))

    # --- stesura
    fatto = Command(SUPERATO, t("Tutti i capitoli previsti sono già su disco."))
    stati["writing.next"] = fatto if tutte else Command(DISPONIBILE)
    stati["writing.all"] = fatto if tutte else Command(CONSIGLIATO)
    stati["writing.matter"] = Command(DISPONIBILE)
    stati["writing.redo_short"] = (
        consigliato_se(True) if f.short_chapters else
        Command(SUPERATO, t("Nessun capitolo è molto sotto il suo budget di parole.")))
    stati["interview.surprise"] = (
        Command(SUPERATO, t("Il libro è già fatto.")) if f.built else Command(DISPONIBILE))
    stati["interview.manuscript"] = (
        Command(SUPERATO, t("Il manoscritto è già caricato.")) if f.manuscript_loaded
        else Command(CONSIGLIATO))
    stati["writing.update_doc"] = (Command(DISPONIBILE) if f.mode == "aggiorna" and f.written
                                   else Command(SUPERATO, t("Prima applica le modifiche.")))
    nessuno = Command(SUPERATO, t("Prima scrivi almeno un capitolo."))
    stati["writing.trial"] = Command(DISPONIBILE) if f.written else nessuno
    stati["writing.ledger"] = Command(DISPONIBILE) if f.written else nessuno
    stati["writing.reports"] = (Command(CONSIGLIATO) if f.to_review else
                                Command(DISPONIBILE) if f.written else nessuno)

    # --- produzione
    stati["production.cover"] = consigliato_se(not f.cover)
    stati["production.write"] = consigliato_se(f.cover and not f.config_exists)

    # --- composizione
    stati["build.build"] = consigliato_se(not f.built)

    # --- controlli e consegna
    stati["handover.check"] = Command(DISPONIBILE)
    stati["handover.notes"] = consigliato_se(not f.notes)

    # --- avanzamento: consigliato quando la fase è chiusa
    stati["advance"] = consigliato_se(
        f.requirements_met and (f.phase != "review" or f.review_points == 0
                                or f.review_pending == 0))
    return stati


# --------------------------------------------------------------------------- #
#  le parole
# --------------------------------------------------------------------------- #
def next_step(forge, *, web_on: bool, activity_kind: str = "", facts: Facts | None = None) -> str:
    """Cosa tocca all'autore, in una o due frasi, in funzione di fase, stato e lavoro in corso.

    ``activity_kind`` è ``""`` quando l'app è ferma; altrimenti ``"chat"`` (l'autore può
    continuare a scrivere), ``"research"``, ``"stoppable"`` (si può interrompere) o ``"plain"``.
    """
    f = facts or read_facts(forge)
    avviso_web = ""
    if f.phase == "research" and not web_on:
        avviso_web = " " + t("Il web è spento: la ricerca usa solo quello che il modello sa e il "
                             "materiale del progetto.")

    if activity_kind == "chat":
        return t("Puoi continuare a scrivere: i messaggi partono appena l'assistente ha finito.")
    if activity_kind == "research":
        return t("Aspetta: la ricerca è in corso. Puoi fermarla con “Interrompi”.") + avviso_web
    if activity_kind == "stoppable":
        return t("Aspetta che finisca. Puoi fermare il lavoro con “Interrompi”: quello già "
                 "fatto resta salvato.")
    if activity_kind:
        return t("Aspetta che finisca: non serve premere altro.")

    if f.phase == "interview":
        if f.mode != "nuovo" and not f.manuscript_loaded:
            return t("Comincia dal manoscritto: premi “Carica il manoscritto…” e scegli il "
                     "file del libro.")
        if f.interview_closed:
            return t("Conversazione chiusa: premi “Passa alla specifica”.")
        if not f.exchanges:
            return t("Racconta il libro all'assistente: scrivi nel campo della conversazione.")
        return t("Tocca a te: rispondi, aggiungi precisazioni, oppure premi “Ho finito: decidi "
                 "tu il resto”.")
    if f.phase == "spec":
        if not f.spec_exists:
            return t("Scegli il numero dei capitoli (o lascia “decidi tu”) e premi “Proponi la "
                     "struttura e scrivi la specifica”.")
        if not f.spec_approved:
            testo = t("Rileggi la specifica, modificala se vuoi, poi premi “Approva la specifica”.")
            if f.n_chapters and f.outline_words:
                obiettivo = f.declared_words or f.outline_words
                testo += " " + t("Obiettivo: {0} parole — circa {1} per capitolo.").format(
                    sp.fmt_words(obiettivo), sp.fmt_words(obiettivo // f.n_chapters))
                if f.declared_words and abs(f.outline_words - f.declared_words) >                         0.2 * f.declared_words:
                    testo += " " + t("Attenzione: la struttura prevede {0} parole, ma il "
                                     "quaderno ne dichiara {1}.").format(
                        sp.fmt_words(f.outline_words), sp.fmt_words(f.declared_words))
            return testo
        return t("Specifica approvata: premi “Passa alla ricerca”.")
    if f.phase == "research":
        if f.research_declined:
            base = t("Hai scelto di scrivere senza ricerca: premi “Passa alla stesura”.")
        elif f.research_done and f.research_old_format:
            base = t("La ricerca è nel formato precedente: premi “Riprendi la ricerca” per avere "
                     "punti, fonti esplorabili e questioni aperte.")
        elif f.research_done and f.questions_pending:
            base = (t("1 contrasto da discutere: apri “Questioni aperte”. Nel libro resta "
                      "com'è scritto finché non decidi.")
                    if f.questions_pending == 1 else
                    t("{0} contrasti da discutere: apri “Questioni aperte”. Nel libro restano "
                      "come sono scritti finché non decidi.").format(f.questions_pending))
            if not web_on:
                base += " " + t("Il web è spento nelle impostazioni: non ho approfondito.")
        elif f.research_done:
            base = t("Ricerca finita: guarda i risultati, poi “Passa alla revisione” (è "
                     "facoltativa) o direttamente alla stesura dalla barra delle fasi.")
        elif f.landscape_done or f.chapters_sourced:
            base = t("La ricerca è incompleta (interrotta o non riuscita): premi “Riprendi la "
                     "ricerca” per continuare da dove si era fermata. “Rifai la ricerca” la "
                     "ripete tenendo quello che hai; “Cancella e rifai” riparte da zero.")
            if f.research_next:
                base += " " + t("Riprenderebbe dal capitolo {0}, punto {1} di {2}.").format(
                    *f.research_next)
        else:
            base = t("La ricerca non è ancora fatta: premi “Guarda cosa esiste già” e poi "
                     "“Cerca le fonti dei capitoli”.")
        return base + avviso_web
    if f.phase == "review":
        if not f.review_points:
            return t("La revisione è facoltativa: premi “Prepara i punti da discutere” per "
                     "rivedere con l'assistente i risultati della ricerca, oppure passa subito "
                     "alla stesura.")
        if f.review_pending:
            return (t("1 punto da decidere: scegli un punto nell'elenco, oppure passa alla "
                      "stesura (i punti lasciati non cambiano niente).")
                    if f.review_pending == 1 else
                    t("{0} punti da decidere: scegli un punto nell'elenco, oppure passa alla "
                      "stesura (i punti lasciati non cambiano niente).").format(f.review_pending))
        return t("Hai trattato tutti i punti: premi “Passa alla stesura”, oppure resta a "
                 "parlare nella chat libera.")
    if f.phase == "writing":
        if f.planned and f.written >= f.planned:
            base = t("Tutti i capitoli sono su disco: premi “Passa alla produzione”.")
        else:
            base = t("Scrivi i capitoli, uno alla volta o tutti insieme. Su disco: {0} di "
                     "{1}.").format(f.written, f.planned)
        if f.short_chapters:
            base += " " + (
                t("1 capitolo è molto sotto il budget di parole: premi “Riscrivi i capitoli "
                  "troppo corti”.") if len(f.short_chapters) == 1 else
                t("{0} capitoli sono molto sotto il budget di parole: premi “Riscrivi i "
                  "capitoli troppo corti”.").format(len(f.short_chapters)))
        return base
    if f.phase == "production":
        if not f.cover:
            return t("Scegli l'immagine di copertina, poi premi “Scrivi book.yaml”.")
        if not f.config_exists:
            return t("Premi “Scrivi book.yaml”: raccoglie le decisioni prese finora.")
        return t("Produzione pronta: premi “Passa alla composizione”.")
    if f.phase == "build":
        if not f.built:
            base = t("Scegli i formati e premi “Componi ed esporta”.")
            if not f.cover:
                base += " " + t("Senza immagine di copertina escono comunque, senza copertina: "
                                "per averla scegline una nella fase di produzione.")
            return base
        return t("Libro composto: premi “Passa ai controlli e alla consegna”.")
    if not f.notes:
        return t("Rifai i controlli di stampa, poi premi “Scrivi le note di produzione”.")
    return t("Tutto fatto: il libro è pronto. Apri la cartella del libro per prendere i file.")


def elapsed_text(seconds: int) -> str:
    """``12 s`` oppure ``3 min 05 s``: una durata che si legge a colpo d'occhio."""
    if seconds < 60:
        return t("{0} s").format(seconds)
    return t("{0} min {1} s").format(seconds // 60, f"{seconds % 60:02d}")


# --------------------------------------------------------------------------- #
#  i colori
# --------------------------------------------------------------------------- #
#: (sfondo, testo, bordo) per tema e stato. Valori fissi, ma verificati: ogni coppia
#: testo/sfondo supera il rapporto 4,5:1 (vedi il test). Il tema si legge dalla tavolozza.
COLORS: dict[str, dict[str, tuple[str, str, str]]] = {
    "light": {
        CONSIGLIATO: ("#bfe0ff", "#062a4d", "#1d6fb8"),
        DISPONIBILE: ("#e8eef7", "#17263b", "#5b7da8"),
        SUPERATO: ("#ececec", "#595959", "#c4c4c4"),
    },
    "dark": {
        CONSIGLIATO: ("#1e4a78", "#ffffff", "#6fb4f2"),
        DISPONIBILE: ("#2b3647", "#e6edf5", "#6f87a8"),
        SUPERATO: ("#333333", "#b8b8b8", "#4d4d4d"),
    },
}
#: Il bordo di un pulsante che ha il fuoco: abbastanza lontano dallo sfondo di ogni stato.
FOCUS = {"light": "#7a2e00", "dark": "#ffd166"}


def command_qss(state: str, dark: bool) -> str:
    """Il foglio di stile di un comando di fase nello stato dato."""
    tema = "dark" if dark else "light"
    sfondo, testo, bordo = COLORS[tema][state]
    spessore = 2 if state == CONSIGLIATO else 1
    peso = "bold" if state == CONSIGLIATO else "normal"
    corsivo = "italic" if state == SUPERATO else "normal"
    corpo = (f"background:{sfondo};color:{testo};border:{spessore}px solid {bordo};"
             f"border-radius:10px;padding:6px 12px;min-height:30px;font-weight:{peso};"
             f"font-style:{corsivo}")
    return (f"QPushButton,QPushButton:disabled{{{corpo}}}"
            f"QPushButton:focus{{border:3px solid {FOCUS[tema]}}}")


def contrast_ratio(a: str, b: str) -> float:
    """Rapporto di contrasto WCAG fra due colori ``#rrggbb``."""
    def luminanza(colore: str) -> float:
        canali = [int(colore[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canali]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    la, lb = luminanza(a), luminanza(b)
    chiaro, scuro = max(la, lb), min(la, lb)
    return (chiaro + 0.05) / (scuro + 0.05)


__all__ = ["CONSIGLIATO", "DISPONIBILE", "SUPERATO", "Command", "Facts",
           "advance_label", "command_qss", "command_states", "contrast_ratio", "elapsed_text",
           "next_step", "read_facts"]
