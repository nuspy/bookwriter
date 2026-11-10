"""Quello che la stesura di un capitolo riceve: poco, scelto, nell'ordine giusto.

Prima il capitolo riceveva tutto quello che c'era — il quaderno intero, i primi novemila caratteri
del file di ricerca (cioè il materiale del primo punto e nient'altro), una riga per capitolo
precedente — e niente di quello che serviva: dove il capitolo prima aveva lasciato il
protagonista, cosa il lettore sapeva già, quali motivi erano stati usati e come.

Il pacchetto (:class:`ContextPack`) mette in fila, per il piano e per ogni scena:

1. il capitolo: cosa deve fare, i suoi punti, la sua misura;
2. lo stato di partenza: la scheda del capitolo precedente (:mod:`ledger`) e le sue ultime righe;
3. le cose già dette al lettore, i motivi con la funzione che hanno avuto, le invenzioni da tenere
   coerenti;
4. le affermazioni di ricerca scelte per il capitolo (:func:`claims.select_for_chapter`);
5. le decisioni dell'autore;
6. il canone del libro (il quaderno), intero per il piano e ridotto per la scena.

Ogni elenco ha un tetto: quando il libro cresce, si tengono i capitoli più vicini.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bookwriter.forge import claims as cl
from bookwriter.forge import ledger as lg
from bookwriter.forge import notebook as nb
from bookwriter.forge.project import BookProject, ForgeState

#: Quante parole della fine del capitolo precedente arrivano alla lettera.
PREVIOUS_TAIL_WORDS = 800
#: I tetti degli elenchi che crescono col libro.
MAX_TOLD = 45
MAX_MOTIFS = 25
MAX_INVENTIONS = 35

#: Le voci del quaderno che la scena riceve: il canone che deve rispettare. Trama, catena degli
#: avvenimenti e scene chiave le ha già distribuite il piano, e ripeterle a ogni scena invita il
#: modello a raccontare il libro intero invece della sua scena.
SCENE_CANON = ("form", "premise", "setting", "characters", "places", "style_notes", "avoid",
               "agreed")


@dataclass
class ContextPack:
    chapter: dict[str, Any]
    narrative: bool = True
    language: str = "italiano"
    protagonist: str = ""
    #: il quaderno intero: per il piano
    notebook: str = ""
    #: il quaderno ridotto al canone: per la scena
    canon: str = ""
    previous: lg.ChapterEntry | None = None
    previous_tail: str = ""
    told: list[str] = field(default_factory=list)
    motifs: list[str] = field(default_factory=list)
    inventions: list[str] = field(default_factory=list)
    open_threads: list[str] = field(default_factory=list)
    claims: list[cl.Claim] = field(default_factory=list)
    deferred: list[cl.Claim] = field(default_factory=list)
    sources: dict[str, cl.Source] = field(default_factory=dict)
    decisions: str = ""
    review: str = ""
    memory: list[str] = field(default_factory=list)
    style: str = ""
    #: le opere simili da cui attingere (il panorama della ricerca): per il piano
    landscape: str = ""
    #: un brano originale del manoscritto (completare o aggiornare un libro): la voce da imitare
    voice: str = ""
    #: i capitoli che vengono dopo: da non anticipare
    following: list[str] = field(default_factory=list)
    #: i capitoli che vengono prima, come li vuole la struttura
    before: list[str] = field(default_factory=list)

    @property
    def number(self) -> int:
        return int(self.chapter.get("number") or 0)

    @property
    def budget(self) -> int:
        return int(self.chapter.get("words") or 0)

    def claim(self, ident: str) -> cl.Claim | None:
        return next((c for c in self.claims if c.id == ident), None)

    # ---- i blocchi del prompt -------------------------------------------
    def chapter_block(self) -> str:
        c = self.chapter
        testo = (f"# Questo capitolo\nCapitolo {c.get('number')}: {c.get('title')}\n"
                 f"Funzione nel libro: {c.get('does')}")
        if c.get("points"):
            testo += ("\n\nPunti chiave (sono FUNZIONI da ottenere con scene, non tesi da "
                      "argomentare):\n" + "\n".join(f"- {p}" for p in c["points"]))
        if self.budget:
            testo += f"\n\nMisura: circa {self.budget} parole."
        return testo

    def start_block(self) -> str:
        if self.previous is None:
            return ""
        p = self.previous
        righe = [f"# Dove ci ha lasciato il capitolo {p.number} («{p.title}»)"]
        if p.summary:
            righe.append(p.summary)
        righe += p.end_state.lines()
        if p.danger:
            righe.append(f"- Pericolo: {p.danger}")
        if self.open_threads:
            righe.append("- Fili aperti: " + "; ".join(self.open_threads))
        return "\n".join(righe)

    def previous_tail_block(self) -> str:
        if not self.previous_tail:
            return ""
        return f"# Le ultime righe del capitolo precedente, alla lettera\n{self.previous_tail}"

    def told_block(self) -> str:
        if not self.told:
            return ""
        return ("# Già detto al lettore nei capitoli precedenti: NON rispiegarlo (si può "
                "richiamare in un inciso, o trasformare con una prova nuova)\n"
                + "\n".join(f"- {t}" for t in self.told))

    def motifs_block(self) -> str:
        if not self.motifs:
            return ""
        return ("# I motivi ricorrenti e la funzione che hanno già avuto (un motivo torna solo "
                "con una funzione nuova)\n" + "\n".join(f"- {m}" for m in self.motifs))

    def inventions_block(self) -> str:
        if not self.inventions:
            return ""
        return ("# Invenzioni già introdotte (nomi, codici, uffici, oggetti): restano come sono\n"
                + "\n".join(f"- {i}" for i in self.inventions))

    def claims_block(self, ids: list[str] | None = None, *, title: str = "") -> str:
        scelte = self.claims if ids is None else [c for c in self.claims if c.id in ids]
        if not scelte:
            return ""
        intestazione = title or ("# Affermazioni di ricerca scelte per questo capitolo (materiale "
                                 "da mettere in scena, non da riassumere)")
        return intestazione + "\n" + "\n".join(cl.claim_line(c, self.sources) for c in scelte)

    def decisions_block(self) -> str:
        pezzi = []
        if self.decisions:
            pezzi.append("# Contrasti segnalati dalla ricerca: cosa ha deciso l'autore\n"
                         + self.decisions)
        if self.review:
            pezzi.append("# Decisioni dell'autore dopo la revisione (hanno la precedenza sulla "
                         "ricerca)\n" + self.review)
        return "\n\n".join(pezzi)

    def voice_block(self) -> str:
        if not self.voice:
            return ""
        return ("# La voce del libro: un brano originale dell'autore. Imitane ritmo, lunghezza "
                "delle frasi, lessico, punteggiatura e modo di dialogare; NON il contenuto, che "
                "non va ripetuto\n" + self.voice)

    def memory_block(self) -> str:
        if not self.memory:
            return ""
        return "# Materiale dell'autore da usare\n" + "\n\n".join(m[:2500] for m in self.memory[:4])

    def outline_block(self) -> str:
        pezzi = []
        if self.before:
            pezzi.append("# I capitoli prima, come li vuole la struttura\n" + "\n".join(self.before))
        if self.following:
            pezzi.append("# I capitoli dopo: NON anticiparne gli eventi\n"
                         + "\n".join(self.following))
        return "\n\n".join(pezzi)


def _tail(text: str, words: int) -> str:
    parti = text.split()
    return " ".join(parti[-words:]) if len(parti) > words else text.strip()


def build(project: BookProject, state: ForgeState, chapter: dict[str, Any], *,
          archive: cl.ClaimStore | None = None, ledger: lg.Ledger | None = None,
          decisions: str = "", review: str = "", memory: list[str] | None = None,
          style: str = "", landscape: str = "") -> ContextPack:
    """Il pacchetto di un capitolo, dai file del libro."""
    from bookwriter.forge.metrics import prose_only

    numero = int(chapter.get("number") or 0)
    registro = ledger if ledger is not None else lg.load(project)
    pacchetto = ContextPack(
        chapter=chapter, narrative=nb.is_narrative(state), language=nb.language_name(state),
        protagonist=nb.protagonist(state), notebook=nb.brief(state),
        canon=nb.render(nb.normalise(state.notebook),
                        skip=tuple(k for k in nb.KEYS if k not in SCENE_CANON)),
        decisions=decisions, review=review, memory=list(memory or []), style=style,
        landscape=landscape)

    prima = registro.before(numero)
    pacchetto.previous = prima[-1] if prima else None
    scritti = project.written_chapters()
    if pacchetto.previous is not None and pacchetto.previous.number in scritti:
        testo = scritti[pacchetto.previous.number].read_text(encoding="utf-8")
        pacchetto.previous_tail = _tail(prose_only(testo), PREVIOUS_TAIL_WORDS)
    elif numero - 1 in scritti:
        testo = scritti[numero - 1].read_text(encoding="utf-8")
        pacchetto.previous_tail = _tail(prose_only(testo), PREVIOUS_TAIL_WORDS)
    if pacchetto.previous is not None:
        pacchetto.open_threads = list(pacchetto.previous.open_threads)

    # dai capitoli più vicini ai più lontani, fino al tetto
    for voce in reversed(prima):
        for t in voce.told:
            if len(pacchetto.told) < MAX_TOLD:
                pacchetto.told.append(f"{t} (cap. {voce.number})")
        for m in voce.motifs:
            if len(pacchetto.motifs) < MAX_MOTIFS and m.get("motif"):
                pacchetto.motifs.append(f"{m['motif']}: {m.get('function', '')} "
                                        f"(cap. {voce.number})")
        for i in voce.inventions:
            if len(pacchetto.inventions) < MAX_INVENTIONS and i.get("name"):
                pacchetto.inventions.append(f"{i['name']}: {i.get('what', '')} "
                                            f"({i.get('status', '')}, cap. {voce.number})")

    if archive is not None:
        scelta = cl.select_for_chapter(archive, chapter)
        pacchetto.claims, pacchetto.deferred = scelta.chosen, scelta.deferred
        pacchetto.sources = archive.sources

    pacchetto.before = [f"- Capitolo {c.get('number')}: {c.get('title')} — {c.get('does')}"
                        for c in state.outline if int(c.get("number") or 0) < numero]
    pacchetto.following = [f"- Capitolo {c.get('number')}: {c.get('title')} — {c.get('does')}"
                           for c in state.outline if int(c.get("number") or 0) > numero][:4]
    return pacchetto
