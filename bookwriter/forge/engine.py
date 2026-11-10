"""Il coordinatore delle fasi: quello che GUI e riga di comando chiamano davvero.

Tiene insieme tre cose che altrimenti si duplicherebbero da tutt'e due le parti: dove sta il
libro, a che punto è, e cosa manca per passare alla fase dopo. Ogni operazione salva lo stato
appena ha finito — perché anche lo stato è un file, e vale la stessa regola dei capitoli.

Le fasi non si saltano, ma non sono nemmeno una prigione: chi vuole andare avanti con una
decisione ancora scoperta può farlo, e :meth:`Forge.requirements` dice cosa sta lasciando indietro
invece di limitarsi a rifiutare.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bookwriter.config import AppConfig
from bookwriter.forge import claims as cl
from bookwriter.forge import context as cx
from bookwriter.forge import editor as ed
from bookwriter.forge import interview as iv
from bookwriter.forge import ledger as lg
from bookwriter.forge import metrics as mx
from bookwriter.forge import notebook as nb
from bookwriter.forge import production as prod
from bookwriter.forge import questions as qs
from bookwriter.forge import research_store as store
from bookwriter.forge.websearch import norm_url
from bookwriter.forge import research as rs
from bookwriter.forge import review as rv
from bookwriter.forge import scenes as sc
from bookwriter.forge import spec as sp
from bookwriter.forge import writing as wr
from bookwriter.forge.build import BuildReport, build, check, write_production_notes
from bookwriter.forge.project import (
    PHASE_IDS,
    PHASES,
    BookProject,
    Status,
    phase_title,
    status,
)

DONE, CURRENT, TODO = "fatta", "in corso", "da fare"


@dataclass
class PhaseInfo:
    id: str
    title: str
    state: str
    detail: str = ""


class Forge:
    """Un libro in lavorazione, con le sue fasi."""

    def __init__(self, root: Path, cfg: AppConfig, *, client: Any = None,
                 db_path: Path | None = None, vectors_path: Path | None = None) -> None:
        self.project = BookProject(Path(root))
        self.cfg = cfg
        self.client = client
        self.db_path = db_path
        self.vectors_path = vectors_path
        self.state = self.project.load_state()
        if not self.state.language:
            self.state.language = cfg.ui.language or "it"

    @property
    def mode(self) -> str:
        """Il lavoro: ``nuovo``, ``completa`` o ``aggiorna`` (vedi ``project.MODES``)."""
        return self.state.mode or "nuovo"

    @property
    def from_manuscript(self) -> bool:
        return self.mode in ("completa", "aggiorna")

    # ---- infrastruttura -------------------------------------------------
    @property
    def model(self) -> str:
        return self.cfg.llm.models.reasoning

    def save(self) -> None:
        self.project.save_state(self.state)

    def start(self) -> None:
        """Crea la cartella del libro e salva lo stato iniziale."""
        self.project.scaffold()
        self.save()

    def status(self) -> Status:
        return status(self.project)

    # ---- dove siamo -----------------------------------------------------
    def phases(self) -> list[PhaseInfo]:
        """Le fasi con il loro stato e una riga che dice a che punto sono."""
        st = self.status()
        fatte, dettagli = self._progress(st)
        corrente = self.state.phase
        info: list[PhaseInfo] = []
        for pid, titolo in PHASES:
            if pid in fatte:
                stato = DONE
            elif pid == corrente:
                stato = CURRENT
            else:
                stato = TODO
            info.append(PhaseInfo(id=pid, title=titolo, state=stato,
                                  detail=dettagli.get(pid, "")))
        return info

    def _progress(self, st: Status) -> tuple[set[str], dict[str, str]]:
        pieni, portanti = nb.filled(self.state.notebook)
        scambi = iv.exchanges(self.state)
        scritti, previsti = len(st.written), len(st.planned or self.state.outline)
        if self.from_manuscript:
            # i capitoli sono su disco dal manoscritto: conta quello che resta da fare
            scritti = previsti - len(wr.pending_chapters(self.project, self.state))
        fatte: set[str] = set()
        dettagli = {
            "interview": ("conversazione chiusa, il resto l'ha deciso l'assistente"
                          if self.state.interview_closed else
                          f"{scambi} {'battuta' if scambi == 1 else 'battute'} dell'autore, "
                          f"quaderno: {pieni} voci su {portanti}"
                          if scambi else "la conversazione non è cominciata"),
            "spec": ("approvata" if self.state.spec_approved else
                     "scritta, da approvare" if self.project.spec_path.exists() else "non scritta"),
            "research": (f"{len(st.research)} file in research/" if st.research else
                         "rinunciata" if self.state.research_declined else "niente raccolto"),
            "review": self._review_detail(),
            "writing": f"{scritti} capitoli su disco" + (f" di {previsti}" if previsti else "")
                       + (f", {n} da rivedere" if (n := len(ed.review_flags(self.project)))
                          else ""),
            "production": ("book.yaml pronto" if self.project.config_path.exists() else
                           "book.yaml non scritto")
                          + (f", copertina: {st.cover}" if st.cover else ", copertina mancante"),
            "build": ", ".join(st.built) if st.built else "niente composto",
            "handover": ("note di produzione scritte" if self.project.notes_path.exists()
                         else "da fare"),
        }
        if self.state.interview_closed:
            fatte.add("interview")
        if self.state.spec_approved:
            fatte.add("spec")
        if self.state.research_done or self.state.research_declined:
            fatte.add("research")
        # La revisione è facoltativa: chi l'ha lasciata, o è andato oltre senza passarci (ha
        # rinunciato alla ricerca, o ha scritto lo stesso), non ha niente in sospeso lì.
        if self.state.review_done or self.state.phase_index > PHASE_IDS.index("review"):
            fatte.add("review")
        if previsti and scritti >= previsti:
            fatte.add("writing")
        if self.state.production_ready and self.project.config_path.exists():
            fatte.add("production")
        if st.built:
            fatte.add("build")
        if self.project.notes_path.exists():
            fatte.add("handover")
        return fatte, dettagli

    def requirements(self, phase: str | None = None) -> list[str]:
        """Cosa manca per considerare finita una fase. Vuoto significa: si può passare oltre."""
        phase = phase or self.state.phase
        st = self.status()
        if phase == "interview":
            if self.from_manuscript and not self.state.manuscript.get("imported"):
                return ["Il manoscritto non è ancora caricato: è il punto di partenza."]
            if self.state.interview_closed:
                return []
            return [("La conversazione non è chiusa: quando hai detto quello che ti preme, "
                     "chiudila e il resto lo decide l'assistente.")]
        if phase == "spec":
            if not self.project.spec_path.exists():
                return ["MASTER_SPEC.md non è ancora stata scritta."]
            if not self.state.spec_approved:
                return [("La specifica non è stata approvata: la struttura si rivede adesso, "
                        "non dopo venti capitoli.")]
            return []
        if phase == "research":
            if self.state.research_declined:
                return []
            if not self.state.research_done:
                return ["La ricerca non è stata fatta né rinunciata esplicitamente."]
            # Fatta, ma con contrasti su cui l'autore non ha ancora deciso: si elencano. Chi
            # passa avanti lo stesso non perde niente e niente viene tralasciato: nel libro
            # resta com'è scritto, finché non decide (vedi ``research_store.decisions_text``).
            return [f"Contrasto da discutere (capitolo {q.chapter}): {q.text}"
                    for q in self.pending_questions()]
        if phase == "review":
            return []  # la revisione non è necessaria: saltarla non blocca niente
        if phase == "writing":
            if self.from_manuscript:
                return [f"Capitolo {c.get('number')} «{c.get('title')}»: "
                        + {"nuovo": "da scrivere", "da_completare": "da completare",
                           "da_aggiornare": "da aggiornare"}.get(str(c.get("status")), "da fare")
                        for c in wr.pending_chapters(self.project, self.state)]
            mancanti = [c for c in self.state.outline
                        if int(c.get("number") or 0) not in st.written]
            return [f"Capitolo {c.get('number')} «{c.get('title')}» non è su disco."
                    for c in mancanti]
        if phase == "production":
            manca = []
            if not self.project.config_path.exists():
                manca.append("book.yaml non è stato scritto.")
            if not st.cover:
                manca.append("Manca l'immagine di copertina: la fornisce l'autore.")
            return manca
        if phase == "build":
            return [] if st.built else ["Niente è stato ancora composto."]
        return [] if self.project.notes_path.exists() else ["Le note di produzione non ci sono."]

    def advance(self, *, force: bool = False) -> tuple[bool, str]:
        """Passa alla fase successiva. Senza ``force`` si ferma e dice cosa manca."""
        mancanze = self.requirements()
        if mancanze and not force:
            return False, "\n".join(mancanze)
        indice = self.state.phase_index
        if indice >= len(PHASE_IDS) - 1:
            return False, "Sei all'ultima fase."
        if mancanze and self.state.phase not in self.state.skipped:
            self.state.skipped.append(self.state.phase)  # chiusa a mano dall'autore
        if self.state.phase == "review":
            # Lasciare la revisione non decide niente al posto dell'autore: i punti non decisi
            # restano aperti e il contenuto del libro resta com'è. «Tralascia» c'è solo se l'ha
            # deciso lui, punto per punto.
            self.state.review_done = True
        self.state.advance(PHASE_IDS[indice + 1])
        self.save()
        return True, phase_title(self.state.phase)

    def go_to(self, phase: str) -> None:
        self.state.advance(phase)
        self.save()

    def phase_concluded(self, phase: str) -> bool:
        """La fase ha finito il suo lavoro con successo, o l'autore l'ha chiusa a mano."""
        s = self.state
        fatta = {
            "interview": s.interview_closed,
            "spec": s.spec_approved,
            "research": s.research_done or s.research_declined,
            "review": s.review_done,
        }.get(phase, False)
        return fatta or phase in s.skipped

    def can_autostart_research(self) -> bool:
        """La ricerca parte da sola solo se non è già conclusa (fatta o rinunciata) e se la fase
        prima — la specifica — è conclusa: approvata, o chiusa a mano dall'autore. Guardare la
        fase Ricerca non basta a farla partire, e una ricerca finita non riparte mai da sola
        (nemmeno se è nel formato di prima: per quella c'è «Rifai la ricerca»)."""
        s = self.state
        if s.research_done or s.research_declined or not s.outline:
            return False
        return self.phase_concluded("spec")

    def _chapters_to_research(self) -> list[dict[str, Any]]:
        """I capitoli che hanno una ricerca: tutti, o del manoscritto solo quello che si scrive —
        capitoli nuovi, parti mancanti, modifiche — non i capitoli che restano com'erano."""
        if not self.from_manuscript:
            return list(self.state.outline)
        return [c for c in self.state.outline
                if c.get("status") in ("nuovo", "da_completare", "da_aggiornare")
                and (c.get("points") or c.get("status") == "nuovo")]

    # ---- fase 1: la conversazione -----------------------------------------
    def talk(self, text: str | None, *, style_contract: str = "", opening: str = iv.OPENING,
             on_delta: iv.OnDelta | None = None) -> str:
        """Una battuta dell'autore e la risposta dell'assistente. ``text`` vuoto: riprova.

        Lo stato si salva anche se la risposta non arriva: quello che l'autore ha scritto non
        deve sparire perché il modello non ha risposto.
        """
        if self.client is None:
            raise RuntimeError("Serve un modello per conversare sul libro.")
        self.project.scaffold()
        try:
            return iv.reply(self.state, self.client, text, model=self.model,
                            style=style_contract, opening=opening, on_delta=on_delta)
        finally:
            self.save()

    def update_notebook(self) -> iv.NotebookUpdate:
        """Porta il quaderno al passo con la conversazione.

        L'esito dice anche se l'autore ha chiesto di chiudere la conversazione: a chi chiama
        decidere se farlo (``finish_interview``).
        """
        if self.client is None:
            return iv.NotebookUpdate()
        esito = iv.update_notebook(self.state, self.client, model=self.model)
        if esito.read:
            self.save()
        return esito

    def finish_interview(self, *, style_contract: str = "") -> str:
        """L'autore chiude: l'assistente decide il resto e dice cosa ha deciso."""
        if self.client is None:
            raise RuntimeError("Serve un modello per completare il quaderno.")
        decisioni = iv.finish(self.state, self.client, model=self.model, style=style_contract)
        messaggio = iv.closing_message(decisioni)
        self.state.say("assistant", messaggio)
        self.state.notebook_upto = len(self.state.transcript)
        self.save()
        return messaggio

    # ---- «Sorpresa»: una descrizione, e tutto il resto in automatico -----------
    def surprise(self, description: str = "", *, style_contract: str = "", progress=None,
                 note=None, should_stop: Callable[[], bool] | None = None,
                 formats: list[str] | None = None) -> str:
        """Il libro intero da una descrizione minima: l'assistente decide tutto il resto.

        Le fasi sono le stesse di sempre, nessuna saltata di nascosto: la descrizione è la prima
        battuta dell'autore, che chiude subito la conversazione e lascia decidere all'assistente
        (quaderno, personaggi, trama, finale); poi specifica approvata, ricerca (panorama e
        capitoli, se il web è acceso), revisione lasciata, stesura per scene con la revisione
        editoriale, produzione e composizione — senza copertina, che è dell'autore.

        Riprende da dove si era fermato: ogni passo si salva, e premendo di nuovo si continua dal
        primo che manca. Ritorna un resoconto.
        """
        if self.client is None:
            raise RuntimeError("Serve un modello per scrivere il libro.")
        if self.mode != "nuovo":
            raise ValueError("«Sorpresa» scrive un libro da zero.")
        descrizione = (description or self.state.surprise.get("description") or "").strip()
        if not descrizione:
            raise ValueError("Scrivi almeno una frase su cosa deve raccontare il libro.")
        self.project.scaffold()
        self.state.surprise = {**self.state.surprise, "description": descrizione}
        fermo = should_stop or (lambda: False)
        righe: list[str] = []

        def passo(testo: str) -> None:
            righe.append(testo)
            self.state.surprise["step"] = testo
            self.save()
            if note:
                note(testo)

        # 1. la conversazione: la descrizione, e «decidi tu il resto»
        if not self.state.interview_closed:
            if not any(b.get("role") == "user" for b in self.state.transcript):
                self.state.say("assistant", iv.OPENING)
                self.state.say("user", descrizione + "\n\n(Sorpresa: decidi tu tutto il resto.)")
                self.save()
            passo("Leggo la descrizione e decido il resto del libro…")
            self.update_notebook()
            self.finish_interview(style_contract=style_contract)
        if self.state.phase == "interview":
            self.state.advance("spec")
        if fermo():
            return "\n".join(righe + ["Fermato: si riprende da qui."])
        # 2. la specifica
        if not self.state.spec_approved:
            passo("Progetto la struttura del libro…")
            self.make_spec(style_contract=style_contract)
            self.approve_spec()
        if fermo():
            return "\n".join(righe + ["Fermato: si riprende da qui."])
        # 3. la ricerca (se il web è spento si scrive senza, e lo si dice)
        if not (self.state.research_done or self.state.research_declined):
            if self.cfg.research.enabled:
                if not (self.project.research_dir / "00_landscape.md").exists():
                    passo("Cerco cosa esiste già sul tema…")
                    self.research_landscape(progress=progress, should_stop=fermo)
                if fermo():
                    return "\n".join(righe + ["Fermato: si riprende da qui."])
                passo("Cerco le fonti dei capitoli…")
                try:
                    self.research_chapters(progress=progress, note=note, should_stop=fermo)
                except rs.ResearchFailed as exc:
                    passo(f"Ricerca incompleta ({exc}): scrivo con quello che c'è.")
                    self.state.research_done = True
            else:
                passo("Ricerca in rete spenta: scrivo senza fonti esterne.")
                self.decline_research()
        if fermo():
            return "\n".join(righe + ["Fermato: si riprende da qui."])
        # 4. la revisione è facoltativa: in una sorpresa decide l'assistente, cioè la lascia
        if not self.state.review_done:
            self.review_skip_all()
            self.state.review_done = True
        if self.state.phase_index < PHASE_IDS.index("writing"):
            self.state.advance("writing")
        self.save()
        # 5. la stesura
        if wr.pending_chapters(self.project, self.state):
            passo("Scrivo i capitoli, scena per scena…")
            report = self.write_all(style_contract=style_contract, progress=progress, note=note,
                                    should_stop=fermo)
            righe.append(report.summary())
            if report.stopped or wr.pending_chapters(self.project, self.state):
                return "\n".join(righe + ["Stesura non finita: premendo di nuovo si riprende."])
        # 6. produzione, composizione, consegna
        passo("Impagino il libro…")
        if not self.project.config_path.exists():
            self.produce(formats=formats)
        self.state.production_ready = True
        self.state.advance("build")
        resoconto = self.build(formats=formats, allow_missing_cover=True)
        self.handover(resoconto)
        self.state.surprise["done"] = True
        self.save()
        passo("Fatto: il libro è composto (senza copertina: quella è tua).")
        return "\n".join(righe)

    def set_style(self, style_id: int | None) -> None:
        """Ricorda lo stile scelto nella tendina, per la conversazione e per la stesura."""
        self.state.style_id = int(style_id or 0)
        self.save()

    # ---- fase 2: specifica ----------------------------------------------
    def make_spec(self, *, chapters: int | None = None, style_contract: str = "",
                  progress=None, should_stop=None) -> str:
        if self.client is None:
            raise RuntimeError("Serve un modello per proporre la struttura del libro.")
        if self.from_manuscript:
            return self._make_manuscript_spec(style_contract=style_contract, progress=progress,
                                              should_stop=should_stop)
        panorama = ""
        landscape = self.project.research_dir / "00_landscape.md"
        if landscape.exists():
            panorama = landscape.read_text(encoding="utf-8")[:8000]
        outline = sp.propose_outline(self.state, self.client, model=self.model,
                                     research=panorama, chapters=chapters)
        prima = list(self.state.outline)
        testo, _ = sp.write_spec(self.project, self.state, outline, style=style_contract)
        self.state.spec_approved = False
        self._structure_changed(prima)
        self.save()
        return testo

    @staticmethod
    def _outline_signature(outline: list[dict[str, Any]]) -> list[tuple]:
        """Cosa rende un capitolo «lo stesso» per la ricerca: numero, titolo e punti chiave.
        «Cosa fa», parole e parte possono cambiare senza che le fonti da cercare cambino."""
        return [(int(c.get("number") or 0), str(c.get("title") or "").strip(),
                 tuple(sp.normalise_points(c.get("points")))) for c in outline]

    def _structure_changed(self, prima: list[dict[str, Any]]) -> bool:
        """Se la struttura è cambiata la ricerca dei capitoli non vale più, e va rifatta.

        Fonti cercate per capitoli che non ci sono più (o con altri punti) non servono alla
        stesura, e darle per buone farebbe scrivere contro la ricerca sbagliata. Il panorama
        dipende dal quaderno, non dalla struttura: resta, e si riusa. Chi ha rinunciato alla
        ricerca continua a averla rinunciata. Una rigenerazione che non cambia niente, o la
        modifica del solo «cosa fa», non butta niente.
        """
        if self._outline_signature(prima) == self._outline_signature(self.state.outline):
            return False
        self.state.research_done = False
        return True

    def research_format_old(self) -> bool:
        """Ricerca «fatta» nel formato precedente: solo i ``.md``, senza i dati strutturati.

        Non ha punti, fonti esplorabili né questioni: per le funzioni nuove è come non fatta, e va
        rifatta (vedi :mod:`research_store`).
        """
        if not (self.state.research_done and self.state.outline):
            return False
        if self.mode == "nuovo" and self.landscape_data() is None:
            return True  # il panorama serve solo a chi scrive da zero
        rd = self.project.research_dir
        return not all(
            store.chapter_json_path(rd, int(c.get("number") or 0), str(c.get("title") or "")).exists()
            for c in self._chapters_to_research())

    def save_spec(self, text: str) -> Path:
        """Salva la specifica modificata a mano, rileggendone la struttura."""
        self.project.scaffold()
        self.project.spec_path.write_text(text, encoding="utf-8")
        from bookwriter.forge.project import planned_chapters

        previsti = planned_chapters(self.project.spec_path)
        prima = list(self.state.outline)
        if previsti:
            per_numero = {int(c.get("number") or 0): c for c in self.state.outline}
            punti = sp.parse_points(text)
            con_sezione = sp.has_points_section(text)
            nuovo = []
            for n, titolo in sorted(previsti.items()):
                capitolo = {**per_numero.get(n, {"words": 0, "does": "", "part": 1}),
                            "number": n, "title": titolo}
                # Una specifica vecchia, senza la sezione dei punti, li lascia come sono; una
                # che la ha li ha decisi l'autore, anche se li ha tolti tutti.
                if con_sezione:
                    capitolo["points"] = punti.get(n, [])
                nuovo.append(capitolo)
            self.state.outline = nuovo
            self._structure_changed(prima)
        self.save()
        return self.project.spec_path

    def _make_manuscript_spec(self, *, style_contract: str = "", progress=None,
                              should_stop=None) -> str:
        """La specifica di un completamento o di un aggiornamento: la struttura del manoscritto
        con quello che cambia, e il piano (cosa si aggiunge, o dove ogni modifica tocca il libro)."""
        from bookwriter.forge import changes as ch
        from bookwriter.forge import completion as cm
        from bookwriter.forge import manuscript as ms

        if not self.state.manuscript.get("imported"):
            raise ValueError("Prima carica il manoscritto.")
        import copy

        sommario = self.state.manuscript.get("summary") or ms.summary_from_ledger(self.project)
        prima = copy.deepcopy(self.state.outline)
        if self.mode == "completa":
            struttura = cm.propose(self.state, self.writer, sommario, model=self.model)
            piano = cm.completion_markdown(struttura["chapters"])
        else:
            dati = ch.plan(self.project, self.state, self.writer, sommario, model=self.model,
                           progress=progress, should_stop=should_stop)
            struttura = {"parts": [{"number": 1, "title": ""}], "chapters": self.state.outline,
                         "front_matter": [], "back_matter": [],
                         "rationale": "La struttura è quella del manoscritto: cambia il testo "
                                      "dove le modifiche lo toccano."}
            piano = ch.plan_markdown(dati, self.state.outline)
        testo, _ = sp.write_spec(self.project, self.state, struttura, style=style_contract)
        testo = testo.rstrip() + "\n\n" + piano + "\n"
        self.project.spec_path.write_text(testo, encoding="utf-8")
        self.state.spec_approved = False
        self._structure_changed(prima)
        self.save()
        return testo

    def import_manuscript(self, path: Path, *, progress=None, note=None,
                          should_stop=None) -> int:
        """Carica il manoscritto da completare o aggiornare: capitoli, registro della continuità,
        quaderno precompilato, voce dell'autore come stile del libro. Ritorna i capitoli."""
        from bookwriter.forge import manuscript as ms
        from bookwriter.ingest.loaders import load_document

        if not self.from_manuscript:
            raise ValueError("Il manoscritto si carica per completare o aggiornare un libro.")
        documento = load_document(Path(path))
        n = ms.import_text(self.project, self.state, documento.raw_text, source=str(path))
        self.save()
        if note:
            note(f"Manoscritto caricato: {n} capitoli, {self.state.manuscript.get('words')} "
                 "parole. Ne ricavo il registro capitolo per capitolo…")
        if self.client is None:
            return n
        self.ledger_rebuild(progress=progress, should_stop=should_stop)
        self.state.manuscript["summary"] = ms.summary_from_ledger(self.project)
        registro = lg.load(self.project)
        for c in self.state.outline:
            voce = registro.entry(int(c.get("number") or 0))
            if voce is not None and voce.summary and not c.get("does"):
                c["does"] = voce.summary
        if note:
            note("Ricavo dal manoscritto il quaderno del libro e la voce dell'autore…")
        campione = ms.sample_text(self.project)
        self.state.notebook = ms.notebook_from_manuscript(
            self.state, self.writer, self.state.manuscript["summary"], campione[:6000],
            model=self.model)
        self._adopt_voice(campione)
        self.save()
        return n

    def _adopt_voice(self, campione: str) -> None:
        """La voce del manoscritto diventa lo stile del libro: le parti nuove o cambiate devono
        suonare come quelle che ci sono."""
        if not (campione and self.db_path):
            return
        try:
            from bookwriter.db import project_schema
            from bookwriter.style.engine import extract_style

            titolo = nb.get(self.state, "title") or "manoscritto"
            profilo = extract_style(campione, self.writer, name=f"Voce di «{titolo}»",
                                    model=self.model)
            self.state.style_id = project_schema.save_style(
                self.db_path, profilo.name, profilo.origin, profilo.model_dump())
        except Exception:  # noqa: BLE001 - senza profilo resta il brano di esempio alla stesura
            self.state.manuscript["voice_error"] = "profilo di stile non ricavato"

    def approve_spec(self) -> None:
        if self.mode == "completa":
            from bookwriter.forge import completion as cm

            cm.renumber(self.project, self.state)
        self.state.spec_approved = True
        if self.state.phase == "spec":
            self.state.advance("research")
        self.save()

    # ---- fase 3: ricerca -------------------------------------------------
    def research_landscape(self, *, progress=None,
                           should_stop: Callable[[], bool] | None = None) -> rs.ResearchReport:
        """Il panorama. Da solo non chiude la ricerca: serve anche quella dei capitoli."""
        if self.client is None:
            raise RuntimeError("Serve un modello per leggere quello che si trova.")
        report = rs.landscape(self.project, self.state, self.client, self.cfg.research,
                              model=self.model, progress=progress, should_stop=should_stop)
        self.save()
        return report

    # ---- riprendere, rifare, ricominciare ------------------------------------------
    def research_progress(self) -> dict[str, Any]:
        """A che punto è la ricerca su disco, per dire all'autore cosa può fare.

        ``landscape`` (il panorama c'è), ``chapters`` (quanti capitoli), ``done`` (quanti
        finiti), ``partial`` (quanti iniziati e non finiti), ``started`` (c'è qualcosa), e
        ``next`` = ``{"number", "title", "point", "points", "stage"}`` del primo capitolo da
        riprendere, o ``None``.
        """
        rd = self.project.research_dir
        fatti = parziali = 0
        prossimo = None
        for capitolo in self.state.outline:
            dati = store.load_chapter(rd, int(capitolo.get("number") or 0),
                                      str(capitolo.get("title") or ""))
            tappa = rs.chapter_state(dati, capitolo)
            if tappa == "done":
                fatti += 1
                continue
            if dati is not None and dati.points and tappa != "new":
                parziali += 1
            if prossimo is None:
                punti = rs.chapter_points(capitolo)
                esistenti = dati.points if dati and tappa != "new" else []
                da_fare = next((p.index for p in esistenti if not p.done), len(esistenti))
                prossimo = {"number": int(capitolo.get("number") or 0),
                            "title": str(capitolo.get("title") or ""),
                            "point": da_fare + 1, "points": len(punti), "stage": tappa}
        landscape = self.landscape_data() is not None
        return {"landscape": landscape, "chapters": len(self.state.outline), "done": fatti,
                "partial": parziali, "next": prossimo,
                "started": landscape or fatti > 0 or parziali > 0}

    def prepare_redo(self) -> int:
        """«Rifai la ricerca»: azzera i punti fatti di **tutti** i capitoli, tenendo quanto già
        raccolto (pagine, risultati, affermazioni, decisioni dell'autore). Ritorna quanti
        capitoli ha toccato.

        I punti da rifare restano segnati su disco: se la ricerca si interrompe, «Riprendi la
        ricerca» continua il rifacimento invece di dare per buono il materiale vecchio.
        """
        toccati = 0
        for capitolo in self.state.outline:
            dati = store.load_chapter(self.project.research_dir, int(capitolo.get("number") or 0),
                                      str(capitolo.get("title") or ""))
            if dati is None or not dati.points:
                continue
            for punto in dati.points:
                punto.done = False
            dati.stage = store.STAGE_POINTS
            store.save_chapter(self.project.research_dir, dati)
            toccati += 1
        self.state.research_done = False
        self.save()
        return toccati

    def set_aside_research(self) -> Path | None:
        """«Cancella e rifai»: mette da parte tutto il materiale della ricerca e riparte da zero.

        I file non si cancellano: vanno in ``research/.superati/<data-ora>/`` (come i capitoli
        troppo corti), e si rimettono al loro posto a mano. Ritorna la cartella, o ``None`` se
        non c'era niente.
        """
        rd = self.project.research_dir
        files = [f for f in rd.iterdir() if f.is_file()] if rd.exists() else []
        self.state.research_done = False
        self.save()
        if not files:
            return None
        from datetime import datetime

        destinazione = rd / ".superati" / datetime.now().strftime("%Y%m%d-%H%M%S")
        destinazione.mkdir(parents=True, exist_ok=True)
        for f in files:
            f.replace(destinazione / f.name)
        return destinazione

    def import_blocked_pages(self) -> list[dict[str, Any]]:
        """Importa le pagine restituite in research/bloccate/ricevute/ (Interfaccia B)."""
        import logging
        from bookwriter.forge import blocked
        _log = logging.getLogger(__name__)
        esiti = blocked.import_ricevute(self.project.research_dir,
                                        min_chars=self.cfg.research.min_page_chars)
        for res in esiti:
            if res.get("status") == "ok" and res.get("page") and res.get("item"):
                item: blocked.BlockedItem = res["item"]
                page: rs.Page = res["page"]
                dati = self.chapter_data(item.chapter)
                if dati is None:
                    continue
                punto = dati.point(item.point - 1)
                if punto is None:
                    continue
                chiave = rs.norm_url(page.url)
                gia = [x for x in punto.pages if rs.norm_url(x.url) == chiave]
                if any(x.status == "" for x in gia):
                    continue  # già letta e usata: il file resta, non si rifà
                ch_dict = next((c for c in self.state.outline
                                if int(c.get("number") or 0) == item.chapter), None)
                # La pagina bloccata era già registrata (illeggibile): la nuova prende il suo posto.
                punto.pages = [x for x in punto.pages if rs.norm_url(x.url) != chiave]
                if ch_dict is not None and self.client is not None:
                    # Stessa strada delle pagine scaricate: pertinenza, poi estrazione.
                    sessione = rs.jevmod.session(self.cfg.research)
                    rs.assess_pages([page], rs.topic_for(self.state, punto.text),
                                    self.cfg.research, client=self.client, model=self.model,
                                    context=rs.judge_context(self.state, ch_dict, punto.text),
                                    jev=sessione)
                    rs._flush_jev(dati, sessione)
                letta = rs._read([page])[0]
                punto.pages.append(letta)
                if self.client is not None and ch_dict is not None and letta.status == "":
                    try:
                        rs._sources_for_new_pages(
                            self.state, self.client, self.cfg.research,
                            dati, ch_dict, punto, [page],
                            self._memory_for(ch_dict), self.model
                        )
                    except Exception as exc:
                        _log.warning("Errore estrazione fonti da pagina sbloccata: %s", exc)
                store.save_chapter(self.project.research_dir, dati)
        return esiti

    #: messaggio quando manca l'indirizzo del servizio esterno (lo ripete anche la GUI, tradotto)
    NO_EXTERNAL_SERVICE = "Imposta l'indirizzo del servizio esterno in Impostazioni → Ricerca."

    def fetch_blocked_pages(self, *, progress=None,
                            should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
        """Chiede al servizio esterno (interfaccia /v1) le pagine in coda e le importa.

        Per ogni riga ``in_coda`` chiede la pagina con ``fetch_via_flaresolverr`` (indirizzo e
        tempo massimo dalle impostazioni). L'HTML ottenuto, se si legge, viene messo in
        ``ricevute/<id>.html`` e **passa dall'importazione di sempre**
        (:meth:`import_blocked_pages`: pulizia, pertinenza, estrazione nel punto giusto, riga a
        ``ricevuta``). Se il servizio non risponde o restituisce ancora una verifica la riga resta
        ``in_coda`` (col motivo aggiornato, se la pagina ne mostra uno riconoscibile). Nessun
        errore ferma il resto.

        Solleva ``RuntimeError`` solo se l'indirizzo del servizio non è impostato. Ritorna
        ``{"total", "fetched", "still_blocked": {motivo: n}, "irrelevant", "no_answer",
        "skipped", "service_ready", "stopped"}``.
        """
        import logging
        from urllib.parse import urlparse

        from bookwriter.forge import blocked, browser_service, pagetext

        settings = self.cfg.research
        endpoint = (settings.external_fetcher_url or "").strip()
        if not endpoint:
            raise RuntimeError(self.NO_EXTERNAL_SERVICE)
        _log = logging.getLogger(__name__)

        coda = blocked.load_coda(self.project.research_dir)
        da_fare = [it for it in coda if it.status == blocked.STATUS_IN_CODA]
        esito: dict[str, Any] = {
            "total": len(da_fare), "fetched": 0, "still_blocked": {}, "irrelevant": 0,
            "no_answer": 0, "skipped": 0, "service_ready": True, "stopped": False}
        if not da_fare:
            return esito

        # Come per la ricerca: se il servizio è locale e l'avvio automatico è acceso, lo si avvia
        # (o si riusa quello già in piedi) e si aspetta che risponda.
        if settings.auto_start_browser and "127.0.0.1" in endpoint:
            porta = urlparse(endpoint).port or 8191
            esito["service_ready"] = bool(browser_service.start_trawl(port=porta, wait_ready=True))

        ricevute = blocked.ricevute_dir(self.project.research_dir)
        ricevute.mkdir(parents=True, exist_ok=True)
        salvate: list[blocked.BlockedItem] = []
        cambiate = False
        for i, voce in enumerate(da_fare):
            if should_stop and should_stop():
                esito["stopped"] = True
                break
            if progress:
                progress(i, len(da_fare))
            if any(f.stem == voce.id for f in ricevute.iterdir() if f.is_file()):
                esito["skipped"] += 1  # un file per questa pagina c'è già: lo importa l'importazione
                continue
            risposta = browser_service.fetch_via_flaresolverr(
                voce.url, endpoint=endpoint, timeout_s=settings.external_fetcher_timeout_s)
            if risposta is None:
                esito["no_answer"] += 1
                continue
            html = risposta[1]
            pulita = pagetext.clean_html(html, voce.url, min_chars=settings.min_page_chars)
            if not pulita.readable:
                motivo = blocked.detect_block_reason(voce.url, html=html,
                                                     clean_reason=pulita.reason)
                if motivo and motivo != voce.reason:
                    voce.reason, cambiate = motivo, True
                esito["still_blocked"][voce.reason] = esito["still_blocked"].get(voce.reason, 0) + 1
                continue
            (ricevute / f"{voce.id}.html").write_text(html, encoding="utf-8")
            salvate.append(voce)
        if progress:
            progress(len(da_fare), len(da_fare))
        if cambiate:
            blocked.save_coda(self.project.research_dir, self._merge_reasons(coda))
        if salvate:
            try:
                esiti = self.import_blocked_pages()
            except Exception as exc:  # noqa: BLE001 - l'importazione non ferma il resto
                _log.warning("Importazione delle pagine recuperate: %s", exc)
                esiti = []
            ok = {e["id"] for e in esiti if e.get("status") == "ok"}
            for voce in salvate:
                if voce.id not in ok:
                    # il file si legge ma l'importazione l'ha rifiutato: resta in coda
                    esito["still_blocked"][voce.reason] = esito["still_blocked"].get(
                        voce.reason, 0) + 1
                    continue
                esito["fetched"] += 1
                dati = self.chapter_data(voce.chapter)
                punto = dati.point(voce.point - 1) if dati else None
                registrata = next((x for x in (punto.pages if punto else [])
                                   if rs.norm_url(x.url) == rs.norm_url(voce.url)), None)
                if registrata is not None and registrata.status == "non_pertinente":
                    esito["irrelevant"] += 1
        return esito

    def _merge_reasons(self, modificate: list[Any]) -> list[Any]:
        """La coda com'è su disco adesso, con i motivi aggiornati dalle righe ``modificate``."""
        from bookwriter.forge import blocked

        nuovi = {it.id: it.reason for it in modificate}
        corrente = blocked.load_coda(self.project.research_dir)
        for it in corrente:
            if it.id in nuovi and it.status == blocked.STATUS_IN_CODA:
                it.reason = nuovi[it.id]
        return corrente

    def blocked_pages(self) -> list[Any]:
        """Elenco delle pagine in coda o bloccate."""
        from bookwriter.forge import blocked
        return blocked.load_coda(self.project.research_dir)

    def research_chapters(self, *, progress=None, note=None,
                          should_stop: Callable[[], bool] | None = None) -> list[rs.ResearchReport]:
        """Le fonti dei capitoli. **Riprende** da dove si era arrivati: i capitoli e i punti già
        fatti non si rifanno (vedi ``research.chapter_research``)."""
        if self.client is None:
            raise RuntimeError("Serve un modello per ricavare le fonti.")

        # Importa automaticamente le pagine ricevute prima di continuare
        try:
            self.import_blocked_pages()
        except Exception:
            pass

        from bookwriter.forge import browser_service
        if self.cfg.research.auto_start_browser and "127.0.0.1" in self.cfg.research.external_fetcher_url:
            browser_service.start_trawl(wait_ready=False)

        rapporti = []
        fallimenti: list[str] = []
        totale = len(self.state.outline)
        interrotta = False
        serie = rs.FailureStreak()
        arresa = False
        da_cercare = self._chapters_to_research()
        totale = len(da_cercare)
        for i, capitolo in enumerate(da_cercare):
            if should_stop and should_stop():
                interrotta = True
                break
            if progress:
                progress(i, totale)
            rapporti.append(rs.chapter_research(
                self.project, self.state, self.client, self.cfg.research, capitolo,
                model=self.model, memory=self._memory_for(capitolo),
                should_stop=should_stop, note=note, streak=serie,
            ))
            arresa = rapporti[-1].gave_up
            if rapporti[-1].stopped:
                interrotta = True
            fallimenti += [f"Capitolo {capitolo.get('number')}: {f}" for f in rapporti[-1].failures]
            if note:
                note(f"Capitolo {capitolo.get('number')}: {rapporti[-1].summary()}")
            if interrotta or arresa:
                break
        if progress:
            progress(totale, totale)
        # «Fatta» vuol dire panorama e fonti di tutti i capitoli, senza interruzioni: una ricerca
        # a metà non è una ricerca, e darla per fatta farebbe scrivere contro fonti che mancano.
        self.state.research_done = (not interrotta and not fallimenti
                                   and (self.from_manuscript or
                                        (self.project.research_dir / "00_landscape.md").exists()))
        self.save()
        if arresa and not interrotta:
            # Tre punti di fila non sono riusciti: la ricerca si ferma qui invece di attraversare
            # tutti i capitoli con lo stesso guasto. Quello già raccolto resta salvato.
            raise rs.ResearchFailed(
                f"La ricerca si è fermata: {serie.limit} punti di fila non sono riusciti, quindi "
                "il guasto non è del singolo punto ma del modello o del server (ultimo errore: "
                f"{serie.last}). I capitoli e i punti già raccolti sono salvati. Controlla il "
                "server dei modelli, poi premi «Riprendi la ricerca»: riparte da qui.")
        if fallimenti and not interrotta:
            # Mai una ricerca a buchi data per fatta: si dice cosa non è riuscito, e resta da fare.
            raise rs.ResearchFailed(
                "La ricerca dei capitoli non è completa: " + " · ".join(fallimenti[:4])
                + (f" (e altri {len(fallimenti) - 4})" if len(fallimenti) > 4 else "")
                + ". Premi «Riprendi la ricerca» per riprovare i punti non riusciti.")
        return rapporti

    def decline_research(self) -> str:
        """L'autore rinuncia alla ricerca. Si dice una volta cosa costa, e poi si scrive così."""
        self.state.research_declined = True
        if self.state.phase == "research":
            self.state.advance("writing")
        self.save()
        return ("Senza ricerca il libro afferma invece di dimostrare, e ogni cifra che ci finisce "
                "dentro è responsabilità dell'autore e non della letteratura. Nessuna citazione "
                "inventata, nessuna autorità presa in prestito, nessuna statistica: un libro ben "
                "argomentato e senza fonti è onesto, uno con fonti false non si recupera.")

    def _memory_for(self, chapter: dict[str, Any]) -> list[str]:
        if not (self.db_path and self.vectors_path and self.client):
            return []
        query = f"{chapter.get('title', '')} {chapter.get('does', '')}".strip()
        return rs.project_memory(self.db_path, self.vectors_path, self.client, query)

    # ---- i dati della ricerca, per chi li esplora ------------------------------
    def landscape_data(self) -> store.LandscapeData | None:
        return store.load_landscape(self.project.research_dir)

    def chapter_data(self, number: int) -> store.ChapterData | None:
        capitolo = next((c for c in self.state.outline if int(c.get("number") or 0) == number),
                        None)
        if capitolo is None:
            return None
        return store.load_chapter(self.project.research_dir, number, str(capitolo.get("title")))

    def _chapter_for(self, number: int) -> dict[str, Any]:
        capitolo = next((c for c in self.state.outline if int(c.get("number") or 0) == number),
                        None)
        if capitolo is None:
            raise ValueError(f"Il capitolo {number} non è nella struttura.")
        return capitolo

    def include_result(self, number: int, point: int, url: str) -> store.PageRead:
        """«Includi nella ricerca»: legge la pagina e la aggiunge alle fonti di quel punto."""
        if self.client is None:
            raise RuntimeError("Serve un modello per ricavare le fonti.")
        capitolo = self._chapter_for(number)
        dati = self.chapter_data(number)
        if dati is None:
            raise ValueError("La ricerca di questo capitolo non è ancora stata fatta.")
        return rs.include_result(self.project, self.state, self.client, self.cfg.research, dati,
                                 capitolo, point, url, model=self.model,
                                 memory=self._memory_for(capitolo))

    def extend_point(self, number: int, point: int, *, should_stop=None, note=None) -> int:
        """«Estendi la ricerca»: nuove interrogazioni e più risultati per un punto."""
        if self.client is None:
            raise RuntimeError("Serve un modello per ricavare le fonti.")
        capitolo = self._chapter_for(number)
        dati = self.chapter_data(number)
        if dati is None:
            raise ValueError("La ricerca di questo capitolo non è ancora stata fatta.")
        return rs.extend_point(self.project, self.state, self.client, self.cfg.research, dati,
                               capitolo, point, model=self.model,
                               memory=self._memory_for(capitolo), should_stop=should_stop,
                               note=note)

    # ---- le questioni aperte -----------------------------------------------------
    def questions(self) -> list[store.Question]:
        return store.all_questions(self.project.research_dir, self.state.outline)

    def pending_questions(self) -> list[store.Question]:
        """Quelle su cui nessuno ha ancora deciso (né risolte con una fonte)."""
        return [q for q in self.questions() if not q.closed]

    def _save_question(self, question: store.Question) -> None:
        dati = self.chapter_data(question.chapter)
        if dati is None:
            return
        for i, q in enumerate(dati.questions):
            if q.id == question.id:
                dati.questions[i] = question
        store.save_chapter(self.project.research_dir, dati)

    def decide_question(self, ident: str, kind: str, text: str = "") -> store.Question:
        """L'autore decide: ``publish`` (con la formulazione) o ``omit``."""
        domanda = next((q for q in self.questions() if q.id == ident), None)
        if domanda is None:
            raise ValueError(f"Non c'è nessuna questione {ident}.")
        if kind == store.OUT_PUBLISH and not text.strip():
            raise ValueError("Per pubblicare serve la formulazione da usare.")
        if kind not in (store.OUT_PUBLISH, store.OUT_OMIT):
            raise ValueError("La decisione è «publish» o «omit».")
        qs.decide(domanda, kind, text.strip())
        self._save_question(domanda)
        return domanda

    # ---- scartare (senza cancellare) --------------------------------------------------
    def discard_question(self, ident: str, on: bool = True) -> store.Question:
        """Scarta una questione (o la ripristina): non conta più per la stesura né per la
        revisione, ma resta sul disco."""
        domanda = next((q for q in self.questions() if q.id == ident), None)
        if domanda is None:
            raise ValueError(f"Non c'è nessuna questione {ident}.")
        domanda.discarded = on
        self._save_question(domanda)
        return domanda

    def discard_item(self, number: int, kind: str, ident: str = "", on: bool = True) -> int:
        """Scarta o ripristina un elemento della ricerca di un capitolo (senza cancellare nulla).

        ``kind``: ``question`` (``ident`` = id), ``url`` (un risultato), ``finding`` (``ident`` =
        «punto:numero», cioè la voce di materiale), ``all`` (tutto il materiale del capitolo).
        """
        if kind == "question":
            self.discard_question(ident, on)
            return 1
        if kind == "url":
            return self.discard_material(number, url=ident, on=on)
        if kind == "all":
            return self.discard_material(number, on=on)
        if kind == "finding":
            dati = self.chapter_data(number)
            punto, _, voce = ident.partition(":")
            pt = dati.point(int(punto)) if dati else None
            if pt is None or not voce.isdigit() or not 0 <= int(voce) < len(pt.findings):
                raise ValueError("Quel materiale non esiste.")
            pt.findings[int(voce)].excluded = on
            store.save_chapter(self.project.research_dir, dati)
            return 1
        raise ValueError(f"Non so scartare «{kind}».")

    def discard_old_questions(self) -> int:
        """Scarta le questioni nate prima del riorientamento (senza ``kind``) e ancora aperte.

        Quelle di allora venivano da ciò che il materiale «non documentava», anche per gli
        elementi d'invenzione. Si ripristinano una per una. Ritorna quante.
        """
        n = 0
        for q in self.questions():
            if not q.kind and not q.closed:
                q.discarded = True
                self._save_question(q)
                n += 1
        return n

    def discard_material(self, number: int, *, url: str | None = None, on: bool = True) -> int:
        """Scarta (o ripristina) un risultato di un capitolo, o tutto il materiale del capitolo.

        Lo scarto esclude l'elemento dal materiale che legge la stesura e dalla revisione, e non
        cancella niente dal disco. Ritorna quanti elementi sono cambiati.
        """
        dati = self.chapter_data(number)
        if dati is None:
            raise ValueError("La ricerca di questo capitolo non è ancora stata fatta.")
        n = dati.discard_url(url, on) if url else dati.discard_all(on)
        store.save_chapter(self.project.research_dir, dati)
        return n

    def resume_deepening(self, *, note=None, should_stop=None) -> int:
        """Riprende l'approfondimento delle questioni rimaste ferme dopo un'interruzione.

        Sono quelle ancora aperte che non hanno nessun tentativo. Ritorna quante ne ha riprese.
        """
        if self.client is None:
            raise RuntimeError("Serve un modello per approfondire.")
        if not self.cfg.research.enabled:
            raise RuntimeError("La ricerca sul web è spenta nelle impostazioni.")
        riprese = 0
        for capitolo in self.state.outline:
            dati = self.chapter_data(int(capitolo.get("number") or 0))
            if dati is None:
                continue
            ferme = [q.id for q in dati.questions
                     if not q.closed and not q.attempts
                     and q.status in (store.APERTA, store.IN_APPROFONDIMENTO)]
            if not ferme or (should_stop and should_stop()):
                continue
            rs.deepen_chapter(self.project, self.state, self.client, self.cfg.research, dati,
                              capitolo, model=self.model, should_stop=should_stop, note=note,
                              only=ferme)
            riprese += len(ferme)
        return riprese

    def resolve_with_material(self, text: str = "", files: list[Path] | None = None
                              ) -> tuple[list[store.Question], qs.Material]:
        """Legge indirizzi e documenti dell'autore e risolve le questioni che reggono."""
        if self.client is None:
            raise RuntimeError("Serve un modello per leggere il materiale.")
        materiale = qs.read_material(self.cfg.research, text, list(files or []))
        risolte: list[store.Question] = []
        if not materiale.pages:
            return risolte, materiale
        pendenti = self.pending_questions()
        trovate = rs.resolve_with_material(self.state, self.client, pendenti, materiale.pages,
                                           model=self.model)
        per_capitolo: dict[int, store.ChapterData] = {}
        for q in pendenti:
            if q.id not in trovate:
                continue
            dati = per_capitolo.get(q.chapter) or self.chapter_data(q.chapter)
            if dati is None:
                continue
            per_capitolo[q.chapter] = dati
            vera = dati.question(q.id)
            if vera is None:
                continue
            punto = dati.point(vera.point)
            if punto is not None:
                for p in materiale.pages:
                    if norm_url(p.url) not in punto.read_urls():
                        punto.pages.append(p)
            rs.apply_resolution(dati, vera, trovate[q.id])
            risolte.append(vera)
        for dati in per_capitolo.values():
            store.save_chapter(self.project.research_dir, dati)
        return risolte, materiale

    def questions_talk(self, text: str | None, files: list[Path] | None = None, *,
                       on_delta=None) -> tuple[str, list[store.Question], qs.Material]:
        """Una battuta dell'autore sulle questioni aperte, e la risposta dell'assistente.

        Prima si legge quello che l'autore ha portato (indirizzi, documenti) e si risolvono le
        questioni che il materiale regge; poi l'assistente risponde sapendolo.
        """
        if self.client is None:
            raise RuntimeError("Serve un modello per parlare delle questioni.")
        files = list(files or [])
        if files or (text and qs.urls_in(text)):
            risolte, materiale = self.resolve_with_material(text or "", files)
        else:
            risolte, materiale = [], qs.Material()
        titoli = {int(c.get("number") or 0): str(c.get("title") or "") for c in self.state.outline}
        voce = (text or "").strip()
        if files:
            voce += ("\n\n" if voce else "") + "[allegati: " + ", ".join(
                Path(f).name for f in files) + "]"
        try:
            return qs.reply(
                self.state, self.client, voce or None,
                qs.system_prompt(self.state, self.pending_questions(), titoli,
                                 resolved_now=risolte, notes=materiale.notes,
                                 web_on=self.cfg.research.enabled),
                model=self.model, on_delta=on_delta), risolte, materiale
        finally:
            self.save()

    def questions_update(self) -> list[store.Question]:
        """Dopo uno scambio: registra le decisioni che l'autore ha preso in modo esplicito."""
        if self.client is None:
            return []
        pendenti = self.pending_questions()
        decise = qs.update_decisions(self.state, self.client, pendenti, model=self.model)
        registrate = []
        for q in pendenti:
            if q.id in decise:
                azione, testo = decise[q.id]
                qs.decide(q, azione, testo)
                self._save_question(q)
                registrate.append(q)
        self.save()
        return registrate

    # ---- fase «Revisione» --------------------------------------------------------
    def _review_detail(self) -> str:
        dati = rv.load(self.project)
        if dati is None:
            return "non cominciata (facoltativa)"
        decisi = sum(1 for p in dati.points if p.closed)
        return f"{decisi} punti decisi su {len(dati.points)}"

    def review_data(self) -> rv.ReviewData | None:
        return rv.load(self.project)

    def review_generate(self, *, progress=None, note=None,
                        should_stop: Callable[[], bool] | None = None) -> rv.ReviewData:
        """Costruisce (o rifà, senza perdere le decisioni) l'elenco dei punti da discutere."""
        if self.client is None:
            raise RuntimeError("Serve un modello per preparare la revisione.")
        dati = rv.generate(self.project, self.state, self.client, model=self.model,
                           web_on=self.cfg.research.enabled, progress=progress, note=note, should_stop=should_stop)
        self.save()
        return dati

    def _review_point(self, dati: rv.ReviewData, ident: str) -> rv.ReviewPoint:
        punto = dati.point(ident)
        if punto is None:
            raise ValueError(f"Non c'è nessun punto {ident}.")
        return punto

    def _chapter_dict(self, number: int) -> dict[str, Any] | None:
        return next((c for c in self.state.outline if int(c.get("number") or 0) == number), None)

    def _review_search(self, text: str, files: list[Path], punto: rv.ReviewPoint | None
                       ) -> tuple[str, list[rv.SearchLog], list[str], list[Any]]:
        """Quello che l'autore ha portato o chiesto di cercare, prima che il modello risponda.

        Ritorna il testo per il prompt, il registro delle ricerche, le note su cosa non si è
        potuto fare e le pagine lette. I fatti ricavati entrano nel punto di capitolo come
        affermazioni con ``origin="revisione"``.
        """
        note: list[str] = []
        materiale = qs.read_material(self.cfg.research, text, files)
        note += materiale.notes
        richieste = rv.detect_intent(self.client, text, punto, model=self.model)
        dati_cap = self.chapter_data(punto.chapter) if punto and punto.chapter else None
        visti = dati_cap.all_urls() if dati_cap else set()
        log, avvisi = rv.run_requests(self.cfg.research, richieste, visti)
        note += avvisi
        pagine = [p for s in log for p in s.pages]
        for pr in materiale.pages:
            pagine.append(rv.Page(pr.url, pr.title, pr.text))
        trovate: list[store.Finding] = []
        if pagine and punto is not None:
            capitolo = self._chapter_dict(punto.chapter) if punto.chapter else None
            trovate = rv.findings_from_pages(self.state, self.client, pagine, punto, capitolo,
                                             model=self.model)
            if dati_cap is not None:
                idx = punto.point if punto.point >= 0 else 0
                pt = dati_cap.point(idx) or (dati_cap.points[0] if dati_cap.points else None)
                if pt is not None:
                    letti = pt.read_urls()
                    for pg in pagine:
                        if norm_url(pg.url) not in letti:
                            pt.pages.append(store.PageRead(pg.url, pg.title,
                                                           pg.text[:store.PAGE_KEEP], "autore"))
                    esistenti = {rv._norm(f.claim) for f in pt.findings}
                    pt.findings.extend(f for f in trovate if rv._norm(f.claim) not in esistenti)
                    store.save_chapter(self.project.research_dir, dati_cap)
        testo = rv.searched_text(log, trovate)
        for pr in materiale.pages:
            testo += f"\n### Portato dall'autore: {pr.title}\n{rv._cut(pr.text, 3000)}"
        return testo.strip(), log, note, pagine

    def review_talk(self, point_id: str, text: str | None, files: list[Path] | None = None, *,
                    on_delta=None, on_search=None) -> tuple[str, list[rv.SearchLog], list[str]]:
        """Una battuta dell'autore su un punto, e la risposta. Cerca se l'autore lo chiede."""
        if self.client is None:
            raise RuntimeError("Serve un modello per parlare dei risultati.")
        dati = rv.load(self.project)
        if dati is None:
            raise ValueError("La revisione non è stata ancora preparata.")
        punto = self._review_point(dati, point_id)
        files = list(files or [])
        voce = (text or "").strip()
        if files:
            voce += ("\n\n" if voce else "") + "[allegati: " + ", ".join(
                Path(f).name for f in files) + "]"
        if not punto.thread:
            punto.thread.append({"role": "assistant", "text": rv.opening(punto)})
        cercato, log, note, _ = ("", [], [], [])
        if voce:
            cercato, log, note, _ = self._review_search(voce, files, punto)
            for riga in [*(s_.summary() for s_ in log), *note]:
                if on_search:
                    on_search(riga)
        if voce:
            punto.thread.append({"role": "user", "text": voce})
        for s in log:
            punto.thread.append({"role": "notice", "text": s.summary()})
        for n in note:
            punto.thread.append({"role": "notice", "text": n})
        if punto.status in (rv.APERTO, rv.RIMANDATO):
            punto.status = rv.IN_DISCUSSIONE
        rv.save(self.project, dati)
        try:
            risposta = rv.reply(
                self.state, self.client, punto.thread, None,
                rv.system_prompt(self.state, punto, searched=cercato,
                                 web_on=self.cfg.research.enabled, notes=note),
                model=self.model, on_delta=on_delta)
        finally:
            rv.save(self.project, dati)
            self.save()
        return risposta, log, note

    def review_propose(self, point_id: str) -> dict[str, Any]:
        """Dopo uno scambio: cosa registrerebbe l'app. Non registra niente (vedi ``review_confirm``)."""
        if self.client is None:
            return {}
        dati = rv.load(self.project)
        if dati is None:
            return {}
        punto = self._review_point(dati, point_id)
        proposta = rv.propose(self.client, punto, thread=punto.thread, upto=punto.proposed_upto,
                              model=self.model)
        punto.proposed_upto = len(punto.thread)
        punto.proposal = proposta
        rv.save(self.project, dati)
        return proposta

    def review_confirm(self, point_id: str, kind: str, text: str = "", *, note: str = "",
                       keep_note: bool = False, source: str = "", url: str = "",
                       structure_note: str = "", by_author: bool = False) -> rv.ReviewPoint:
        """L'autore conferma una decisione (la proposta dell'app o la sua, «Decido io…»).

        È l'**unico** punto in cui una decisione si scrive. ``keep_note`` è la scelta dell'autore,
        punto per punto: se le prove contrarie e le formulazioni scartate vanno negli appunti
        (``research/review_notes.md``, che la stesura non legge); il testo del libro resta nella
        sua voce in ogni caso.
        """
        if kind not in rv.DECISION_KINDS:
            raise ValueError("La decisione è «concordata», «imposta», «fonte» o «tralascia».")
        dati = rv.load(self.project)
        if dati is None:
            raise ValueError("La revisione non è stata ancora preparata.")
        punto = self._review_point(dati, point_id)
        # Per il materiale e le idee basta «entra»: la formulazione è facoltativa.
        senza_testo = punto.kind in rv.KINDS_WITHOUT_TEXT and kind == rv.D_CONCORDATA
        if kind != rv.D_TRALASCIA and not text.strip() and not senza_testo:
            raise ValueError("Serve la formulazione da usare nel libro.")
        testo = text.strip()
        if kind == rv.D_FONTE and not (source.strip() or url.strip()):
            kind = rv.D_CONCORDATA  # una fonte senza fonte non è una fonte
        punto.decision = rv.Decision(
            kind=kind, text=testo, note=note.strip(), keep_note=bool(keep_note),
            source=source.strip(), url=url.strip(), structure_note=structure_note.strip(),
            by_author=by_author, at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        punto.status = rv._STATUS_OF[kind]
        punto.proposal = {}
        if punto.ref:
            self._review_decide_question(punto)
        elif punto.kind == rv.MATERIALE:
            # «Lo lascio fuori» toglie il materiale dal file che legge la stesura; ogni altra
            # scelta lo (ri)mette: il materiale entra, è la scelta di serie.
            self._review_exclude_material(punto, kind == rv.D_TRALASCIA)
        elif kind == rv.D_FONTE:
            self._review_add_finding(punto)
        if kind in (rv.D_CONCORDATA, rv.D_IMPOSTA) and testo:
            riga = f"{punto.title}: {testo}"
            quaderno = nb.normalise(self.state.notebook)
            if riga not in quaderno["agreed"]:
                quaderno["agreed"] = [*quaderno["agreed"], riga]
                self.state.notebook = quaderno
        if keep_note:
            rv.append_note(self.project, punto, punto.decision)
        rv.save(self.project, dati)
        self.save()
        return punto

    def _review_decide_question(self, punto: rv.ReviewPoint) -> None:
        """La decisione su una questione non documentata va dove è sempre andata."""
        d = punto.decision
        assert d is not None
        if d.kind == rv.D_TRALASCIA:
            self.decide_question(punto.ref, store.OUT_OMIT)
        elif d.kind == rv.D_FONTE:
            dati = self.chapter_data(punto.chapter)
            domanda = dati.question(punto.ref) if dati else None
            if dati is not None and domanda is not None:
                rs.apply_resolution(dati, domanda, store.Finding(
                    claim=d.text, source=d.source or d.url, url=d.url, origin="revisione"))
                store.save_chapter(self.project.research_dir, dati)
        else:
            self.decide_question(punto.ref, store.OUT_PUBLISH, d.text)

    def _review_exclude_material(self, punto: rv.ReviewPoint, escludi: bool) -> None:
        """Segna (o ripristina) come escluso il materiale di un punto di tipo ``materiale``."""
        for e in punto.evidence:
            m = re.fullmatch(r"l:work(\d+)", e.id)
            if m:
                self._exclude_landscape_work(int(m.group(1)), escludi)
        if not punto.chapter:
            return
        dati = self.chapter_data(punto.chapter)
        if dati is None:
            return
        pt = dati.point(punto.point)
        if pt is None:
            return
        for e in punto.evidence:
            m = re.fullmatch(r"c\d+p\d+f(\d+)", e.id)
            if m and 1 <= int(m.group(1)) <= len(pt.findings):
                pt.findings[int(m.group(1)) - 1].excluded = escludi
        store.save_chapter(self.project.research_dir, dati)

    def _exclude_landscape_work(self, numero: int, escludi: bool) -> None:
        paesaggio = self.landscape_data()
        if paesaggio is None:
            return
        chiave = "works" if paesaggio.analysis.get("works") else "competitors"
        opere = paesaggio.analysis.get(chiave) or []
        if 1 <= numero <= len(opere) and isinstance(opere[numero - 1], dict):
            opere[numero - 1]["excluded"] = escludi
            store.save_landscape(self.project.research_dir, paesaggio,
                                 store.render_landscape_md("", paesaggio))

    def _review_add_finding(self, punto: rv.ReviewPoint) -> None:
        d = punto.decision
        dati = self.chapter_data(punto.chapter) if punto.chapter else None
        if dati is None or d is None:
            return
        pt = dati.point(punto.point) or (dati.points[0] if dati.points else None)
        if pt is None or any(rv._norm(f.claim) == rv._norm(d.text) for f in pt.findings):
            return
        pt.findings.append(store.Finding(claim=d.text, detail="", level="",
                                         source=d.source or d.url, url=d.url,
                                         origin="revisione"))
        store.save_chapter(self.project.research_dir, dati)

    def review_clear_proposal(self, point_id: str | None = None) -> None:
        """«Non ancora»: la proposta non si registra e non si ripropone da sola."""
        dati = rv.load(self.project)
        if dati is None:
            return
        if point_id:
            self._review_point(dati, point_id).proposal = {}
        else:
            dati.free_proposal = {}
        rv.save(self.project, dati)

    def review_skip_point(self, point_id: str) -> rv.ReviewPoint:
        """«Salta il punto»: resta da decidere, senza nessun effetto sul libro."""
        dati = rv.load(self.project)
        if dati is None:
            raise ValueError("La revisione non è stata ancora preparata.")
        punto = self._review_point(dati, point_id)
        if not punto.closed:
            punto.status = rv.RIMANDATO
        punto.proposal = {}
        rv.save(self.project, dati)
        return punto

    def review_skip_all(self) -> int:
        """«Salta la revisione»: tutto quello che è aperto resta da decidere. Ritorna quanti."""
        dati = rv.load(self.project)
        quanti = 0
        if dati is not None:
            for p in dati.points:
                if p.status in (rv.APERTO, rv.IN_DISCUSSIONE):
                    p.status = rv.RIMANDATO
                    quanti += 1
            rv.save(self.project, dati)
        return quanti

    def review_perplexities(self) -> list[str]:
        return rv.perplexities(rv.load(self.project), self.project, self.state)

    def review_free_system(self, dati: rv.ReviewData | None) -> str:
        decisioni = []
        for p in (dati.points if dati else []):
            if p.decision is not None:
                decisioni.append(f"- ({p.decision.kind}) {p.title}: {p.decision.text or 'tralascia'}")
        rimasti = [p for p in (dati.points if dati else [])
                   if p.status in (rv.APERTO, rv.IN_DISCUSSIONE, rv.RIMANDATO)]
        return "\n\n".join(x for x in (
            rv.FREE_ROLE,
            "## Il libro (quaderno)\n" + rv._cut(nb.brief(self.state), 6000),
            "## Decisioni prese\n" + ("\n".join(decisioni) or "(nessuna)"),
            "## Ancora da decidere\n" + ("\n".join(f"- {p.title}" for p in rimasti) or "(niente)"),
            "## Le mie perplessità\n" + ("\n".join(f"- {c}" for c in self.review_perplexities())
                                          or "(nessuna)"),
        ) if x)

    def review_free_talk(self, text: str | None, files: list[Path] | None = None, *,
                         on_delta=None, on_search=None
                         ) -> tuple[str, list[rv.SearchLog], list[str]]:
        """La chat libera di fine revisione: l'autore dice quello che vuole, l'app risponde."""
        if self.client is None:
            raise RuntimeError("Serve un modello per parlare dei risultati.")
        dati = rv.load(self.project) or rv.ReviewData(date=rv.date.today().isoformat())
        files = list(files or [])
        voce = (text or "").strip()
        if files:
            voce += ("\n\n" if voce else "") + "[allegati: " + ", ".join(
                Path(f).name for f in files) + "]"
        cercato, log, note = "", [], []
        if voce:
            cercato, log, note, _ = self._review_search(voce, files, None)
            for riga in [*(s_.summary() for s_ in log), *note]:
                if on_search:
                    on_search(riga)
            dati.free_thread.append({"role": "user", "text": voce})
        for s in log:
            dati.free_thread.append({"role": "notice", "text": s.summary()})
        for n in note:
            dati.free_thread.append({"role": "notice", "text": n})
        rv.save(self.project, dati)
        sistema = self.review_free_system(dati)
        if cercato:
            sistema += "\n\n## Ricerca appena fatta su richiesta dell'autore\n" + cercato
        try:
            risposta = rv.reply(self.state, self.client, dati.free_thread, None, sistema,
                                model=self.model, on_delta=on_delta)
        finally:
            rv.save(self.project, dati)
        return risposta, log, note

    def review_free_propose(self) -> dict[str, Any]:
        if self.client is None:
            return {}
        dati = rv.load(self.project)
        if dati is None:
            return {}
        finto = rv.ReviewPoint(kind=rv.LIBERO, title="Chat libera",
                               statement="Una questione portata dall'autore nella chat libera.")
        proposta = rv.propose(self.client, finto, thread=dati.free_thread, upto=dati.free_upto,
                              model=self.model)
        dati.free_upto = len(dati.free_thread)
        dati.free_proposal = proposta
        rv.save(self.project, dati)
        return proposta

    def review_free_confirm(self, title: str, kind: str, text: str = "", **kw) -> rv.ReviewPoint:
        """Una decisione presa nella chat libera diventa un punto di tipo «libero», già deciso."""
        dati = rv.load(self.project) or rv.ReviewData(date=rv.date.today().isoformat())
        titolo = rv._cut(title or text, 100) or "Dalla chat libera"
        punto = rv.ReviewPoint(id=rv._next_id(dati.points), kind=rv.LIBERO, title=titolo,
                               statement="Portato dall'autore nella chat libera.",
                               anchor=rv._anchor(rv.LIBERO, f"{titolo}{len(dati.points)}"))
        dati.points.append(punto)
        dati.free_proposal = {}
        rv.save(self.project, dati)
        return self.review_confirm(punto.id, kind, text, **kw)

    def _review_for(self, chapter: dict[str, Any]) -> str:
        return rv.review_text(rv.load(self.project), int(chapter.get("number") or 0))

    def _decisions_for(self, chapter: dict[str, Any]) -> str:
        dati = store.load_chapter(self.project.research_dir, int(chapter.get("number") or 0),
                                  str(chapter.get("title") or ""))
        return store.decisions_text(dati)

    def _research_for(self, chapter: dict[str, Any]) -> str:
        from bookwriter.forge.project import slugify

        numero = int(chapter.get("number") or 0)
        path = self.project.research_dir / f"{numero:02d}_{slugify(str(chapter.get('title', '')))}.md"
        testo = path.read_text(encoding="utf-8")[:9000] if path.exists() else ""
        # Il panorama è materiale come il resto: opere simili da cui attingere, fonti e idee.
        opere = store.landscape_for_writing(self.landscape_data())
        if opere:
            testo = (testo + "\n\n" if testo else "") + (
                "## Opere simili da cui attingere (idee, immagini, scene, fonti)\n\n" + opere)
        return testo

    # ---- fase 4: stesura -------------------------------------------------
    @property
    def writer(self) -> Any:
        """Il client della stesura: ragiona al livello scelto per lei (``reasoning_effort_writing``)."""
        from bookwriter.llm.client import writing

        return writing(self.client)

    def claim_store(self, *, rebuild: bool = False, note=None) -> cl.ClaimStore | None:
        """L'archivio delle affermazioni della ricerca: si rifà se manca o se la ricerca è cambiata
        dopo di lui. ``None`` se non c'è ricerca."""
        from bookwriter.llm.client import quick

        dati = sorted(self.project.research_dir.glob("[0-9]*_*.json")) \
            if self.project.research_dir.exists() else []
        if not dati:
            return None
        percorso = cl.store_path(self.project)
        aggiornato = percorso.exists() and percorso.stat().st_mtime >= max(
            p.stat().st_mtime for p in dati)
        if aggiornato and not rebuild:
            archivio = cl.load(self.project)
            if archivio is not None:
                return archivio
        if note:
            note("Riordino la ricerca: affermazioni uniche, fonti verificate e indipendenti…")
        client = quick(self.client) if self.client is not None else None
        return cl.build(self.project, self.state, client, model=self.model)

    def ledger_rebuild(self, *, upto: int | None = None, only_stale: bool = True, progress=None,
                       note=None, should_stop=None) -> list[int]:
        """Ricava (o aggiorna) le schede del registro dai capitoli scritti."""
        if self.client is None:
            raise RuntimeError("Serve un modello per ricavare il registro dai capitoli.")
        rifatti = lg.rebuild(self.project, self.state, self.writer, model=self.model,
                             only_stale=only_stale, upto=upto, progress=progress,
                             should_stop=should_stop)
        if note and rifatti:
            note(f"Registro della continuità aggiornato: capitoli {', '.join(map(str, rifatti))}.")
        return rifatti

    # ---- la qualità: voti da 0 a 10, consapevoli del genere e dello scopo --------------
    def quality_kind(self) -> str:
        """Il tipo di libro per la verifica: quello scelto dall'autore, o dal quaderno."""
        from bookwriter.forge import quality as ql

        scelto = str(self.state.quality.get("kind") or "")
        if scelto in ql.KINDS:
            return scelto
        return ql.kind_of(nb.get(self.state, "form"), nb.is_narrative(self.state))

    def set_quality_kind(self, kind: str) -> None:
        self.state.quality = {**self.state.quality, "kind": kind}
        self.save()

    def quality_thresholds(self):
        from bookwriter.forge import quality as ql

        q = self.cfg.quality
        return ql.Thresholds(min_global=q.min_global, min_critical=q.min_critical,
                             max_rounds=q.max_rounds)

    def _quality_inputs(self, number: int) -> tuple[Path, dict[str, Any], str]:
        scritti = self.project.written_chapters()
        if number not in scritti:
            raise ValueError(f"Il capitolo {number} non è scritto.")
        capitolo = next((c for c in self.state.outline if int(c.get("number") or 0) == number),
                        {"number": number, "title": scritti[number].stem})
        precedente = lg.load(self.project).previous(number)
        prima = ""
        if precedente is not None:
            prima = "\n".join([precedente.summary, *precedente.end_state.lines()]).strip()
        return scritti[number], capitolo, prima

    def evaluate_chapter_quality(self, number: int, *, explain_language: str = "italiano",
                                 progress=None, save: bool = True):
        """La valutazione di un capitolo scritto, salvata nella sua storia (``quality/``)."""
        from bookwriter.forge import quality as ql

        if self.client is None:
            raise RuntimeError("Serve un modello per valutare il capitolo.")
        file, capitolo, prima = self._quality_inputs(number)
        testo = file.read_text(encoding="utf-8")
        valutazione = self._evaluate_text(testo, capitolo, prima, explain_language,
                                          progress=progress)
        if save:
            ql.save_round(self.project, file.stem, valutazione)
        return valutazione

    def _evaluate_text(self, testo: str, capitolo: dict[str, Any], prima: str,
                       explain_language: str, *, progress=None):
        from bookwriter.forge import quality as ql

        numero = int(capitolo.get("number") or 0)
        return ql.evaluate_text(
            self.writer, testo, chapter=numero, title=str(capitolo.get("title") or ""),
            kind=self.quality_kind(), notebook=nb.normalise(self.state.notebook),
            budget=int(capitolo.get("words") or 0), language=nb.language_code(self.state),
            explain_language=explain_language, previous=prima,
            baseline=self._baseline(numero), protagonist=nb.protagonist(self.state),
            model=self.cfg.llm.models.checker, progress=progress)

    def revise_chapter(self, number: int, *, text: str | None = None, output: Path | None = None,
                       apply: bool = True, trial: bool = False,
                       explain_language: str = "italiano", style_contract: str = "", note=None,
                       should_stop: Callable[[], bool] | None = None):
        """Un giro di revisione del capitolo (:mod:`revision`): diagnosi, piano, interventi,
        verifica indipendente, gate e verdetto, con il report in ``revisioni/<run>/``.

        ``text``: la bozza da rivedere (per una prova); senza, il capitolo in ``chapters/``.
        ``output``: dove scrivere il testo rivisto invece del capitolo. Altrimenti, con ``apply``,
        il testo rivisto entra nel capitolo solo se il verdetto non è FAIL e la rubrica non è
        peggiorata; la versione di prima resta in ``story/versions/``. La continuità si controlla
        sui capitoli **originali** dell'app (vedi ``RevisionRun.sources_consulted``).
        """
        from bookwriter.forge import quality as ql
        from bookwriter.forge import revision as rv

        if self.client is None:
            raise RuntimeError("Serve un modello per rivedere il capitolo.")
        capitolo = self._chapter_for(number)
        titolo = str(capitolo.get("title") or f"Capitolo {number}")
        file = self.project.written_chapters().get(number)
        if text is None:
            if file is None:
                raise ValueError(f"Il capitolo {number} non è scritto.")
            text = file.read_text(encoding="utf-8")
        if number > 1:
            self.ledger_rebuild(upto=number - 1, note=note)
        pacchetto = cx.build(self.project, self.state, capitolo,
                             archive=self.claim_store(note=note),
                             decisions=self._decisions_for(capitolo),
                             review=self._review_for(capitolo))
        piano = sc.load(self.project, number, titolo, trial=trial) or \
            sc.load(self.project, number, titolo)
        canone = "\n\n".join(p for p in (pacchetto.canon, pacchetto.decisions_block(),
                                           pacchetto.start_block()) if p)
        rivisto, esecuzione = rv.revise(
            self.writer, text, project=self.project, chapter=number, title=titolo,
            kind=self.quality_kind(), pack=pacchetto, piano=piano,
            budget=int(capitolo.get("words") or 0), language=nb.language_name(self.state),
            explain_language=explain_language, notebook=nb.normalise(self.state.notebook),
            canon=canone, model=self.model, check_model=self.cfg.llm.models.checker,
            style=style_contract, protagonist=nb.protagonist(self.state),
            baseline_words=self._baseline(number), should_stop=should_stop, note=note,
            state=self.state)
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rivisto, encoding="utf-8")
            esecuzione.output_path = str(output)
        elif apply and file is not None:
            prima = ql.evaluation_from_dict(esecuzione.rubric_before).global_score or 0
            dopo = ql.evaluation_from_dict(esecuzione.rubric_after).global_score or 0
            if esecuzione.verdict != "FAIL" and dopo >= prima:
                if not ql.versions(self.project, file.stem):
                    ql.save_version(self.project, file.stem, text, "originale")
                ql.save_version(self.project, file.stem, rivisto, esecuzione.run_id)
                file.write_text(rivisto, encoding="utf-8")
                esecuzione.applied_to_chapter = True
                esecuzione.output_path = str(file)
        if not esecuzione.output_path:
            esecuzione.output_path = str(rv.run_dir(self.project, esecuzione.run_id) /
                                         "rivisto.md")
        rv.save_run(self.project, esecuzione, text, rivisto)
        return esecuzione

    def improve_chapter_quality(self, number: int, *, rounds: int | None = None,
                                explain_language: str = "italiano", style_contract: str = "",
                                note=None, should_stop: Callable[[], bool] | None = None):
        """Valuta e corregge un capitolo fino alla qualità accettata o al tetto dei giri
        (``rounds``, predefinito dalle impostazioni). Ogni versione tenuta va nel capitolo e in
        ``story/versions/``; la prima è l'originale.

        Per un racconto ogni giro è una revisione completa (:meth:`revise_chapter`: diagnosi,
        piano, interventi, verifica, gate); per un saggio o un manuale, la correzione di un
        problema alla volta sulla sua parte (:func:`quality.improve_chapter`)."""
        from bookwriter.forge import editor as edt
        from bookwriter.forge import quality as ql

        if self.client is None:
            raise RuntimeError("Serve un modello per correggere il capitolo.")
        if self.quality_kind() in ql.NARRATIVE_KINDS:
            return self._improve_by_revision(number, rounds=rounds,
                                             explain_language=explain_language,
                                             style_contract=style_contract, note=note,
                                             should_stop=should_stop)
        soglie = self.quality_thresholds()
        giri = soglie.max_rounds if rounds is None else rounds
        file, capitolo, prima = self._quality_inputs(number)
        testo = file.read_text(encoding="utf-8")
        titolo = str(capitolo.get("title") or file.stem)
        piano = sc.load(self.project, number, titolo)

        def scheda(indice: int) -> str:
            corrente = edt.parse(file.read_text(encoding="utf-8"))
            scena = edt._scene_for(piano, indice, corrente)
            return scena.card() if scena is not None else ""

        def valuta(t: str):
            v = self._evaluate_text(t, capitolo, prima, explain_language)
            ql.save_round(self.project, file.stem, v)
            return v

        def salva(t: str, v, etichetta: str) -> None:
            if not ql.versions(self.project, file.stem):
                ql.save_version(self.project, file.stem, file.read_text(encoding="utf-8"),
                                "originale")
            ql.save_version(self.project, file.stem, t, etichetta)
            file.write_text(t, encoding="utf-8")

        _testo, resoconto = ql.improve_chapter(
            self.writer, testo, valuta, thresholds=soglie, kind=self.quality_kind(),
            language=nb.language_name(self.state), rounds=giri, model=self.model,
            check_model=self.cfg.llm.models.checker, scene_card=scheda if piano else None,
            style=style_contract, save=salva, should_stop=should_stop, note=note,
            chapter=number, title=titolo, current=ql.latest(self.project, file.stem))
        return resoconto

    def _improve_by_revision(self, number: int, *, rounds: int | None,
                             explain_language: str, style_contract: str, note, should_stop):
        """Giri di revisione completa finché il verdetto è PASS, fino al tetto; un giro che non
        migliora la rubrica (o finisce FAIL) ferma il ciclo, perché ripeterlo non aiuta."""
        from bookwriter.forge import quality as ql

        soglie = self.quality_thresholds()
        giri = soglie.max_rounds if rounds is None else rounds
        file, capitolo, _prima = self._quality_inputs(number)
        resoconto = ql.ImproveReport(chapter=number,
                                     title=str(capitolo.get("title") or file.stem))
        tenuto_ultimo = False
        for giro in range(1, max(0, giri) + 1):
            tenuto_ultimo = False
            if should_stop and should_stop():
                resoconto.stopped = True
                break
            if note:
                note(f"Capitolo {number}: giro di revisione {giro}…")
            esecuzione = self.revise_chapter(number, explain_language=explain_language,
                                             style_contract=style_contract, note=note,
                                             should_stop=should_stop)
            prima = ql.evaluation_from_dict(esecuzione.rubric_before)
            dopo = ql.evaluation_from_dict(esecuzione.rubric_after)
            ql.save_round(self.project, file.stem, dopo if esecuzione.applied_to_chapter
                          else prima, note=esecuzione.run_id)
            if resoconto.before is None:
                resoconto.before = prima.global_score
            resoconto.final = dopo if esecuzione.applied_to_chapter else prima
            resoconto.rounds.append(
                f"giro {giro}: {esecuzione.verdict}, {prima.global_score} → {dopo.global_score}"
                + ("" if esecuzione.applied_to_chapter else " (non applicato)")
                + f" — report: revisioni/{esecuzione.run_id}/report.md")
            resoconto.fixes += [f"{c.change_id} {c.before_after_summary}"
                                for c in esecuzione.changes]
            if not esecuzione.applied_to_chapter:
                break
            resoconto.changed = tenuto_ultimo = True
            if esecuzione.verdict in ("PASS", "PASS WITH WARNINGS"):
                break
            if esecuzione.verdict == "AUTHOR INPUT REQUIRED":
                resoconto.rounds.append("servono decisioni dell'autore: vedi il report")
                break
        resoconto.after = resoconto.final.global_score if resoconto.final else resoconto.before
        resoconto.accepted = bool(resoconto.final and resoconto.final.accepted(soglie))
        resoconto.needs_authorization = (not resoconto.accepted and not resoconto.stopped
                                         and tenuto_ultimo and len(resoconto.rounds) >= giri)
        return resoconto

    def evaluate_book_quality(self, *, explain_language: str = "italiano", progress=None,
                              note=None, should_stop: Callable[[], bool] | None = None):
        """Il libro intero: ogni capitolo (si riusa la valutazione di un capitolo non cambiato)
        e il giudizio del libro. Ritorna ``(libro, capitoli)``, salvati in ``quality/libro.json``."""
        from bookwriter.forge import quality as ql

        if self.client is None:
            raise RuntimeError("Serve un modello per valutare il libro.")
        scritti = self.project.written_chapters()
        if not scritti:
            raise ValueError("Non c'è ancora nessun capitolo scritto.")
        registro = lg.load(self.project)
        capitoli, riassunti = [], []
        for i, (numero, file) in enumerate(sorted(scritti.items())):
            if should_stop and should_stop():
                break
            if progress:
                progress(i, len(scritti))
            testo = file.read_text(encoding="utf-8")
            ultima = ql.latest(self.project, file.stem)
            if ultima is not None and ultima.text_hash == ql._hash(testo) \
                    and ultima.kind == self.quality_kind():
                valutazione = ultima
            else:
                if note:
                    note(f"Valuto il capitolo {numero}…")
                valutazione = self.evaluate_chapter_quality(numero,
                                                            explain_language=explain_language)
            capitoli.append(valutazione)
            voce = registro.entry(numero)
            riassunto = voce.summary if voce is not None and voce.summary else \
                " ".join(mx.prose_only(testo).split()[:150]) + " … " + \
                " ".join(mx.prose_only(testo).split()[-120:])
            riassunti.append(f"## Capitolo {numero}: {valutazione.title}\n{riassunto}")
        if progress:
            progress(len(scritti), len(scritti))
        if note:
            note("Valuto il libro intero…")
        libro = ql.book_evaluation(self.writer, capitoli, riassunti, kind=self.quality_kind(),
                                   notebook=nb.normalise(self.state.notebook),
                                   explain_language=explain_language,
                                   model=self.cfg.llm.models.checker)
        ql.save_book(self.project, libro, capitoli)
        return libro, capitoli

    # ---- la struttura del libro: riordinare, tagliare, unire, dividere capitoli ---------
    def structure_data(self):
        """Le diagnosi della struttura, le proposte con le decisioni e le applicazioni
        (``story/struttura_review.json``, vedi :mod:`structure_review`). Legge un file: veloce."""
        from bookwriter.forge import structure_review as sr

        return sr.load(self.project)

    def structure_review(self, *, explain_language: str = "italiano", progress=None, note=None,
                         should_stop: Callable[[], bool] | None = None):
        """Una diagnosi della struttura del libro dalle schede dei capitoli (registro, struttura,
        misure, qualità): proposte di riordino, taglio, fusione, divisione, già passate dai
        controlli deterministici. Chiama il modello (lungo). Ritorna il giro (``Round``)."""
        from bookwriter.forge import structure_review as sr

        if self.client is None:
            raise RuntimeError("Serve un modello per ragionare sulla struttura del libro.")
        return sr.diagnose(self.project, self.state, self.writer,
                           model=self.cfg.llm.models.checker, kind=self.quality_kind(),
                           explain_language=explain_language, progress=progress, note=note,
                           should_stop=should_stop)

    def structure_decide(self, op_id: str, decision: str, *, note: str = "", waiver: str = "",
                         destination: int | None = None):
        """La decisione dell'autore su una proposta: ``accept``, ``reject`` o ``reset``. Con
        blocker si accetta solo con ``waiver`` (la deroga motivata); ``destination`` sceglie il
        capitolo che accoglie il contenuto di un taglio. Veloce. Ritorna l'``Operation``."""
        from bookwriter.forge import structure_review as sr

        return sr.decide(self.project, self.state, op_id, decision, note=note, waiver=waiver,
                         destination=destination)

    def structure_apply(self, op_id: str, *, note=None):
        """Applica una proposta accettata: copia di sicurezza, struttura, file dei capitoli
        rinumerati, loro dati, registro, specifica. Una fusione chiama il modello per la sola
        giunzione (può durare); il resto è veloce. Ritorna ``ApplyResult``."""
        from bookwriter.forge import structure_review as sr

        esito = sr.apply(self.project, self.state, op_id,
                         client=self.writer if self.client is not None else None,
                         model=self.model, language=nb.language_name(self.state), note=note)
        self.save()
        return esito

    def structure_undo(self, run: str | None = None, *, force: bool = False):
        """Annulla l'ultima applicazione (a ritroso): i file tornano dalla copia di sicurezza.
        Se dopo un file è cambiato si rifiuta, a meno di ``force``. Veloce."""
        from bookwriter.forge import structure_review as sr

        esito = sr.undo(self.project, self.state, run, force=force)
        self.save()
        return esito

    def structure_integrate(self, op_id: str, *, style_contract: str = "", note=None,
                            should_stop: Callable[[], bool] | None = None):
        """Inserisce nel testo del capitolo che lo accoglie il contenuto unico di un capitolo
        tagliato (le parti mancanti, come nel completamento: il testo che c'è resta parola per
        parola). Lungo. Si annulla con :meth:`structure_undo`; un'interruzione o un errore
        rimettono il capitolo com'era."""
        import copy

        from bookwriter.forge import completion as cm
        from bookwriter.forge import structure_review as sr

        if self.client is None:
            raise RuntimeError("Serve un modello per scrivere il contenuto da integrare.")
        voce = next((i for i in sr.load(self.project).integrations if i.get("op") == op_id),
                    None)
        if voce is None:
            raise ValueError(f"Nessun contenuto da integrare per la proposta {op_id}.")
        numero = int(voce.get("chapter") or 0)
        capitolo = copy.deepcopy(self._chapter_for(numero))
        titolo = str(capitolo.get("title") or "")
        percorso = self.project.chapter_path(numero, titolo)
        if not percorso.exists():
            raise ValueError(f"Il capitolo {numero} non è scritto: il contenuto è già nei suoi "
                             "punti e si scriverà con lui.")
        file_toccati = [percorso, sc.plan_path(self.project, numero, titolo),
                        lg.ledger_path(self.project), self.project.story_dir / "ledger.md"]

        def lavoro(da_integrare: dict[str, Any]) -> list[str]:
            capitolo["points"] = [str(x) for x in da_integrare.get("content") or []]
            strumenti = self.chapter_tools(capitolo, style_contract=style_contract, note=note)
            esito = cm.complete_chapter(self.project, self.state, self.writer, capitolo,
                                        strumenti.pack, model=self.model,
                                        style_contract=style_contract,
                                        cap=self.cfg.writing.section_words,
                                        should_stop=should_stop)
            if should_stop and should_stop():
                raise RuntimeError("Integrazione interrotta: il capitolo è tornato com'era.")
            testo = esito["path"].read_text(encoding="utf-8")
            lg.record(self.project, self.state, self.writer, numero, titolo, testo,
                      model=self.model)
            return [] if esito["inserted"] else ["nessuna parte inserita: controlla il capitolo"]

        return sr.integrate(self.project, self.state, op_id, file_toccati, lavoro)

    def structure_recheck(self, *, explain_language: str = "italiano", progress=None, note=None,
                          should_stop: Callable[[], bool] | None = None) -> list[int]:
        """Il ricontrollo dopo un cambiamento di struttura: le schede vecchie del registro si
        rifanno, e la qualità dei capitoli segnati si rivaluta. Lungo. Ritorna i capitoli
        ricontrollati (escono dalla lista)."""
        from bookwriter.forge import structure_review as sr

        if self.client is None:
            raise RuntimeError("Serve un modello per ricontrollare i capitoli.")
        self.ledger_rebuild(only_stale=True, note=note, should_stop=should_stop)
        scritti = self.project.written_chapters()
        segnati = sr.load(self.project).recheck
        sr.clear_recheck(self.project, [n for n in segnati if n not in scritti])
        da_fare = [n for n in segnati if n in scritti]
        fatti: list[int] = []
        for i, numero in enumerate(da_fare):
            if should_stop and should_stop():
                break
            if progress:
                progress(i, len(da_fare))
            if note:
                note(f"Ricontrollo il capitolo {numero}…")
            self.evaluate_chapter_quality(numero, explain_language=explain_language)
            sr.clear_recheck(self.project, [numero])
            fatti.append(numero)
        if progress:
            progress(len(da_fare), len(da_fare))
        return fatti

    def _baseline(self, number: int) -> dict[str, float]:
        """La misura del libro: le densità delle parole negli altri capitoli scritti."""
        altri = [p.read_text(encoding="utf-8")
                 for n, p in self.project.written_chapters().items() if n != number]
        return mx.book_baseline(altri)

    def chapter_tools(self, chapter: dict[str, Any], *, style_contract: str = "",
                      destination: Path | None = None, note=None) -> wr.ChapterTools:
        """Quello che serve per scrivere un capitolo col piano delle scene, la revisione
        editoriale e il registro: il contesto si costruisce adesso, dopo che il capitolo prima
        ha lasciato la sua scheda."""
        numero = int(chapter.get("number") or 0)
        prova = destination is not None
        if numero > 1:
            self.ledger_rebuild(upto=numero - 1, note=note)
        archivio = self.claim_store(note=note)
        pacchetto = cx.build(self.project, self.state, chapter, archive=archivio,
                             decisions=self._decisions_for(chapter),
                             review=self._review_for(chapter), memory=self._memory_for(chapter),
                             style=style_contract,
                             landscape=store.landscape_for_writing(self.landscape_data()))
        if self.from_manuscript:
            from bookwriter.forge import manuscript as ms

            pacchetto.voice = ms.voice_sample(self.project, int(chapter.get("source") or numero))
        cliente, modello, tetto = self.writer, self.model, self.cfg.writing.section_words
        controlli = self.cfg.llm.models.checker
        base = self._baseline(numero)
        lingua = nb.language_code(self.state)

        def pianifica(pack):
            if note:
                note(f"Capitolo {numero}: progetto le scene…")
            return sc.plan(self.project, self.state, cliente, pack, cap=tetto, model=modello,
                           trial=prova, check_model=controlli)

        def fatti(titolo_scena, testo, scena, prima):
            from bookwriter.forge import timeline as tl

            inizio = list(pacchetto.previous.end_state.lines()) if pacchetto.previous else []
            registro, _problemi = tl.record_scene(cliente, titolo_scena, testo, prima, inizio,
                                                  plan_card=scena.card() if scena else "",
                                                  model=controlli)
            return registro

        def rivedi(testo, piano, pack):
            if note:
                note(f"Capitolo {numero}: revisione editoriale…")
            finale, resoconto = ed.edit_chapter(self.project, cliente, testo, piano, pack,
                                                model=modello, baseline=base,
                                                style=style_contract, trial=prova,
                                                check_model=controlli)
            if note:
                note(f"Capitolo {numero}: " + (
                    "da rivedere — " + ", ".join(resoconto.failed_critical)
                    if resoconto.needs_review else "revisione superata")
                    + f" ({resoconto.words_draft} → {resoconto.words_final} parole).")
            return finale

        def registra(n, titolo, testo):
            lg.record(self.project, self.state, cliente, n, titolo, testo, model=modello)

        def note_bibliografiche(ids):
            return cl.bibliography(archivio, ids, lingua) if archivio is not None else []

        return wr.ChapterTools(pack=pacchetto, planner=pianifica, editor=rivedi,
                               recorder=registra, notes=note_bibliografiche,
                               destination=destination,
                               facts=fatti if pacchetto.narrative else None)

    def _record_waiver(self, numero: int, motivo: str, risultato, *, trial: bool) -> None:
        """Una deroga usata davvero (il piano aveva blocker) resta nello stato del libro."""
        if not motivo:
            return
        piano = sc.load(self.project, numero, str(self._chapter_for(numero).get("title") or ""),
                        trial=trial)
        if piano is None or not piano.blockers() or piano.waiver != motivo:
            return
        self.state.waivers.append({
            "chapter": numero, "reason": motivo, "blockers": piano.blockers(),
            "date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "trial": trial})
        self.save()

    def replan_chapter(self, number: int, *, trial: bool = False) -> None:
        """Butta il piano del capitolo (e il lavoro a metà): la prossima stesura lo rifà."""
        capitolo = self._chapter_for(number)
        titolo = str(capitolo.get("title") or "")
        for path in (wr.partial_path(self.project, number, titolo, trial=trial),
                     sc.plan_path(self.project, number, titolo, trial=trial)):
            try:
                path.unlink()
            except OSError:
                pass

    def repair_plan(self, number: int, *, trial: bool = False, style_contract: str = "",
                    note=None) -> sc.Plan | None:
        """Ripara il piano del capitolo solo dove ha difetti (:func:`scenes.repair`): le scene
        sane restano. Il piano riparato prende il posto di quello nel lavoro a metà, così la
        stesura riparte da lui. ``None`` se non c'è un piano o se ci sono già scene scritte."""
        capitolo = self._chapter_for(number)
        titolo = str(capitolo.get("title") or "")
        piano = sc.load(self.project, number, titolo, trial=trial)
        lavoro = wr.partial_path(self.project, number, titolo, trial=trial)
        budget = int(capitolo.get("words") or 0)
        if piano is None or wr._load_scene_partial(lavoro, budget).get("texts"):
            return None
        strumenti = self.chapter_tools(capitolo, style_contract=style_contract,
                                       destination=self.trial_path(number) if trial else None,
                                       note=note)
        if note:
            note(f"Capitolo {number}: riparo il piano solo dove ha difetti…")
        piano = sc.repair(self.project, self.writer, strumenti.pack, piano,
                          cap=self.cfg.writing.section_words, model=self.model, trial=trial,
                          check_model=self.cfg.llm.models.checker, note=note)
        wr._save_scene_partial(lavoro, budget, piano, {})
        return piano

    def write_next(self, *, style_contract: str = "", note=None,
                   should_stop: Callable[[], bool] | None = None,
                   waiver: str = "") -> wr.ChapterResult | None:
        """Il prossimo capitolo. ``waiver``: la deroga motivata dell'autore ai blocker del piano
        (senza, un piano con blocker non si scrive e il risultato li riporta in ``blocked``)."""
        capitolo = wr.next_chapter(self.project, self.state)
        if capitolo is None:
            if self.mode == "aggiorna" and self.client is not None:
                return self._finish_update(note=note)
            return None
        if self.client is None:
            raise RuntimeError("Serve un modello per scrivere il capitolo.")
        numero = int(capitolo.get("number") or 0)
        if capitolo.get("status") in ("da_completare", "da_aggiornare"):
            return self._rework_chapter(capitolo, style_contract=style_contract, note=note,
                                        should_stop=should_stop)
        risultato = wr.draft_chapter(
            self.project, self.state, self.writer, capitolo, model=self.model,
            style_contract=style_contract, research=self._research_for(capitolo),
            memory=self._memory_for(capitolo), decisions=self._decisions_for(capitolo),
            review=self._review_for(capitolo),
            section_words=self.cfg.writing.section_words, should_stop=should_stop,
            on_section=(lambda k, n: note(("section", numero, k, n))) if note else None,
            tools=self._with_waiver(self.chapter_tools(capitolo, style_contract=style_contract,
                                                       note=note), waiver),
        )
        self._record_waiver(numero, waiver, risultato, trial=False)
        self.save()
        return risultato

    @staticmethod
    def _with_waiver(strumenti, motivo: str):
        strumenti.waiver = (motivo or "").strip()
        return strumenti

    def _rework_chapter(self, capitolo: dict[str, Any], *, style_contract: str = "", note=None,
                        should_stop=None) -> wr.ChapterResult:
        """Un capitolo del manoscritto da completare (parti nuove inserite) o da aggiornare
        (paragrafi toccati riscritti). Il resto del testo resta parola per parola."""
        from bookwriter.forge import changes as ch
        from bookwriter.forge import completion as cm

        numero = int(capitolo.get("number") or 0)
        titolo = str(capitolo.get("title") or "")
        strumenti = self.chapter_tools(capitolo, style_contract=style_contract, note=note)
        prima = mx.count_words(self.project.chapter_path(numero, titolo).read_text(
            encoding="utf-8"))
        sezione = (lambda k, n: note(("section", numero, k, n))) if note else None
        if capitolo.get("status") == "da_completare":
            esito = cm.complete_chapter(self.project, self.state, self.writer, capitolo,
                                        strumenti.pack, model=self.model,
                                        style_contract=style_contract,
                                        cap=self.cfg.writing.section_words,
                                        should_stop=should_stop, on_section=sezione)
            if note:
                note(f"Capitolo {numero}: inserite {len(esito['inserted'])} parti nuove.")
        else:
            esito = ch.apply_chapter(self.project, self.state, self.writer, capitolo,
                                     strumenti.pack, model=self.model,
                                     style_contract=style_contract, should_stop=should_stop,
                                     on_section=sezione)
            if note:
                note(f"Capitolo {numero}: {esito['changed']} paragrafi aggiornati, "
                     f"{esito['added']} passaggi nuovi"
                     + (f", {len(esito['errors'])} punti NON applicati" if esito["errors"]
                        else "") + ".")
        testo = esito["path"].read_text(encoding="utf-8")
        lg.record(self.project, self.state, self.writer, numero, titolo, testo, model=self.model)
        self.save()
        risultato = wr.ChapterResult(number=numero, title=titolo, path=esito["path"],
                                     words=mx.count_words(testo),
                                     budget=int(capitolo.get("words") or prima))
        return risultato

    def _write_all_manuscript(self, *, style_contract: str = "", progress=None, note=None,
                              should_stop=None) -> wr.WritingReport:
        """Completare o aggiornare: un capitolo alla volta finché non resta niente da fare."""
        from bookwriter.llm.client import LLMError

        report = wr.WritingReport()
        totale = len(wr.pending_chapters(self.project, self.state))
        fatti = 0
        while True:
            if should_stop and should_stop():
                report.stopped = True
                break
            if progress:
                progress(min(fatti, totale), max(totale, fatti + 1))
            try:
                risultato = self.write_next(style_contract=style_contract, note=note,
                                            should_stop=should_stop)
            except LLMError as errore:
                report.aborted = f"Fermato: {errore}"
                break
            if risultato is None:
                break
            if risultato.blocked:
                # il piano ha blocker: rifare la stessa richiesta darebbe lo stesso piano
                report.blocked = (risultato.number, risultato.title, risultato.blocked)
                break
            if risultato.stopped:
                report.stopped = True
                break
            report.written.append(risultato)
            fatti += 1
            if note:
                note(risultato.line())
        if progress:
            progress(totale, totale)
        return report

    def _finish_update(self, *, note=None) -> wr.ChapterResult | None:
        """Finito di aggiornare: i rimandi al vecchio rimasti tornano da riguardare (una volta),
        poi il documento con le modifiche segnate."""
        from bookwriter.forge import changes as ch

        residui = ch.residual_sweep(self.project, self.state)
        if residui and ch.requeue_residuals(self.project, self.state, residui):
            self.save()
            if note:
                note(f"Rimandi al vecchio rimasti: {len(residui)}. Li riguardo.")
            return self.write_next(note=note)
        if not ch.load(self.project).get("consistency_done"):
            # la cronologia dei capitoli toccati, ricostruita sul testo nuovo: quello che non
            # torna (orari, spostamenti, scadenze) si riguarda una volta
            if note:
                note("Ricostruisco la cronologia dei capitoli toccati e ne controllo la "
                     "coerenza…")
            esito = ch.consistency_check(self.project, self.state, self.writer,
                                         model=self.cfg.llm.models.checker)
            rimessi = ch.requeue_consistency(self.project, self.state)
            if note:
                note(f"Cronologia: {len(esito['issues'])} segnalazioni su "
                     f"{len(esito['chapters'])} capitoli"
                     + (f"; {rimessi} capitoli da riguardare." if rimessi else "."))
            if rimessi:
                self.save()
                return self.write_next(note=note)
        if (self.project.out_dir / "modifiche.md").exists() and not residui:
            return None
        fatti = ch.write_outputs(self.project, self.state)
        self.save()
        if note:
            note("Documento delle modifiche: " + ", ".join(p.name for p in fatti))
        return None

    def update_outputs(self, fmt: str = "docx") -> list[Path]:
        """Il documento con le modifiche segnate e il resoconto, a richiesta."""
        from bookwriter.forge import changes as ch

        return ch.write_outputs(self.project, self.state, fmt)

    def trial_path(self, number: int) -> Path:
        capitolo = self._chapter_for(number)
        return self.project.trials_dir / self.project.chapter_path(
            number, str(capitolo.get("title") or "")).name

    def write_trial(self, number: int, *, style_contract: str = "", note=None,
                    should_stop: Callable[[], bool] | None = None,
                    destination: Path | None = None,
                    editorial: bool = True, waiver: str = "") -> wr.ChapterResult:
        """Riscrive un capitolo **in prova**, con la stesura per scene e la revisione editoriale,
        in ``output/prove/`` (o in ``destination``): il capitolo in ``chapters/`` non si tocca, e
        il registro non prende la prova per il libro. Con ``editorial`` falso si ferma alla
        bozza, senza revisione editoriale: per confrontare la stesura da sola."""
        if self.client is None:
            raise RuntimeError("Serve un modello per scrivere il capitolo.")
        capitolo = self._chapter_for(number)
        destinazione = destination or self.trial_path(number)
        risultato = wr.draft_chapter(
            self.project, self.state, self.writer, capitolo, model=self.model,
            style_contract=style_contract, research=self._research_for(capitolo),
            memory=self._memory_for(capitolo), decisions=self._decisions_for(capitolo),
            review=self._review_for(capitolo),
            section_words=self.cfg.writing.section_words, should_stop=should_stop,
            on_section=(lambda k, n: note(("section", number, k, n))) if note else None,
            tools=self._with_waiver(
                self._trial_tools(capitolo, style_contract, destinazione, note, editorial),
                waiver),
        )
        self._record_waiver(number, waiver, risultato, trial=True)
        return risultato

    def _trial_tools(self, capitolo, style_contract, destinazione, note, editorial):
        strumenti = self.chapter_tools(capitolo, style_contract=style_contract,
                                       destination=destinazione, note=note)
        if not editorial:
            strumenti.editor = None
        return strumenti

    def write_benchmark(self, number: int, before: ed.EditorialReport,
                        after: ed.EditorialReport) -> Path:
        """Il confronto fra un capitolo e la sua prova, su disco accanto ai resoconti."""
        capitolo = self._chapter_for(number)
        path = self.project.editorial_dir / f"confronto_{number:02d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        variazione = mx.length_drift(before.words_draft, after.words_draft)
        path.write_text("\n".join([
            f"# Confronto — Capitolo {number}: {capitolo.get('title')}", "",
            f"Parole: {before.words_draft} → {after.words_draft} ({variazione:+.0%}); misura "
            f"prevista {capitolo.get('words')}.", "",
            "## Il capitolo scritto", "", before.metrics_before, "",
            "## La prova", "", after.metrics_before, ""]), encoding="utf-8")
        return path

    def editorial_measures(self, number: int, *, trial: bool = False) -> ed.EditorialReport:
        """Le misure di un capitolo scritto (o della sua prova), senza modello e senza toccarlo."""
        capitolo = self._chapter_for(number)
        percorso = self.trial_path(number) if trial else \
            self.project.written_chapters().get(number)
        if percorso is None or not percorso.exists():
            raise ValueError(f"Il capitolo {number} non è scritto"
                             + (" in prova." if trial else "."))
        return ed.measure_only(self.project, percorso.read_text(encoding="utf-8"), capitolo,
                               protagonist=nb.protagonist(self.state),
                               baseline=self._baseline(number))

    def write_all(self, *, style_contract: str = "", progress=None, note=None,
                  should_stop=None, waivers: dict[int, str] | None = None) -> wr.WritingReport:
        if self.client is None:
            raise RuntimeError("Serve un modello per scrivere i capitoli.")
        if self.from_manuscript:
            return self._write_all_manuscript(style_contract=style_contract, progress=progress,
                                              note=note, should_stop=should_stop)
        report = wr.write_all(
            self.project, self.state, self.writer, model=self.model,
            style_contract=style_contract, research_for=self._research_for,
            memory_for=self._memory_for, decisions_for=self._decisions_for,
            review_for=self._review_for,
            progress=progress, note=note, should_stop=should_stop,
            section_words=self.cfg.writing.section_words,
            tools_for=lambda c: self._with_waiver(
                self.chapter_tools(c, style_contract=style_contract, note=note),
                (waivers or {}).get(int(c.get("number") or 0), "")),
        )
        for n, motivo in (waivers or {}).items():
            self._record_waiver(n, motivo, None, trial=False)
        self.save()
        return report

    def short_chapters(self, threshold: float = 0.6) -> list[tuple[int, int, int]]:
        """I capitoli scritti molto sotto il loro budget: ``(numero, parole, budget)``.

        Un capitolo sotto il 60% della misura non è un capitolo un po' breve: è quasi sempre il
        modello che si è fermato, o un budget letto male.
        """
        st = self.status()
        corti = []
        for c in self.state.outline:
            numero, budget = int(c.get("number") or 0), int(c.get("words") or 0)
            if budget and numero in st.written:
                parole = st.written[numero][1]
                if parole < threshold * budget:
                    corti.append((numero, parole, budget))
        return corti

    def redo_short_chapters(self, *, style_contract: str = "", progress=None, note=None,
                            should_stop=None, threshold: float = 0.6
                            ) -> tuple[list[Path], wr.WritingReport]:
        """Rifà i capitoli troppo corti. Quelli di prima non si cancellano: si mettono da parte.

        Finiscono in ``chapters/.superati/AAAAMMGG-hhmm/`` (fuori dalla vista del compositore),
        poi si riscrive quel che manca. Ritorna i file messi da parte e il resoconto.
        """
        import shutil
        from datetime import datetime

        da_rifare = {n for n, _, _ in self.short_chapters(threshold)}
        messi_da_parte: list[Path] = []
        if da_rifare:
            cartella = self.project.chapters_dir / ".superati" / datetime.now().strftime(
                "%Y%m%d-%H%M")
            cartella.mkdir(parents=True, exist_ok=True)
            for numero, percorso in self.project.written_chapters().items():
                if numero in da_rifare:
                    destinazione = cartella / percorso.name
                    shutil.move(str(percorso), str(destinazione))
                    messi_da_parte.append(destinazione)
        report = self.write_all(style_contract=style_contract, progress=progress, note=note,
                                should_stop=should_stop)
        return messi_da_parte, report

    def write_matter(self, kind: str, *, style_contract: str = "") -> Path:
        if self.client is None:
            raise RuntimeError("Serve un modello per scrivere questa sezione.")
        path = wr.draft_matter(self.project, self.state, self.client, kind, model=self.model,
                               style_contract=style_contract)
        self.save()
        return path

    # ---- fase 5: produzione ----------------------------------------------
    def cover_check(self) -> prod.CoverCheck:
        from bookwriter.forge.build import _cover_path

        taglio = prod.trim_of(self.project, fallback=self.cfg.production.trim)
        immagine = _cover_path(self.project) if self.project.config_path.exists() else None
        return prod.check_cover(immagine, taglio)

    def set_cover(self, image: Path) -> Path:
        if not self.project.config_path.exists():
            self.produce()
        path = prod.set_cover(self.project, image)
        self.save()
        return path

    def produce(self, *, formats: list[str] | None = None) -> Path:
        from bookwriter.forge.build import _cover_path

        copertina = _cover_path(self.project) if self.project.config_path.exists() else None
        path = prod.write_book_yaml(
            self.project, self.state, self.cfg.production,
            cover=copertina.name if copertina and copertina.exists() else "",
            formats=formats,
        )
        self.save()
        return path

    # ---- fasi 6 e 7: composizione e consegna ------------------------------
    def build(self, *, formats: list[str] | None = None, allow_missing_cover: bool = False,
              copy_without_cover: bool = False, note=None) -> BuildReport:
        """Compone i formati voluti; senza copertina esce lo stesso, con un'avvertenza."""
        report = build(self.project, formats=formats, allow_missing_cover=allow_missing_cover,
                       copy_without_cover=copy_without_cover, note=note)
        self.state.built = sorted(p.name for p in report.produced.values())
        if self.state.phase in ("production", "build"):
            self.state.advance("build")
        self.save()
        return report

    def check(self) -> tuple[str, bool]:
        return check(self.project)

    def handover(self, report: BuildReport | None = None) -> Path:
        path = write_production_notes(self.project, self.state, report)
        self.state.advance("handover")
        self.save()
        return path


def open_quality_workspace(ppaths, cfg: AppConfig, source: Path, *, client: Any = None,
                           kind: str = "") -> Forge:
    """Un libro qualsiasi (md, docx, txt, pdf…) da valutare e correggere: lo si copia, diviso in
    capitoli, in ``valutazioni/<nome>/`` del progetto. Il file dell'autore non si tocca mai; le
    correzioni vanno nella copia, con le versioni."""
    from bookwriter.forge import manuscript as ms
    from bookwriter.forge.project import slugify
    from bookwriter.ingest.loaders import load_document

    radice = ppaths.root / "valutazioni" / (slugify(Path(source).stem) or "libro")
    forge = Forge(radice, cfg, client=client, db_path=ppaths.db, vectors_path=ppaths.vectors)
    if not forge.project.written_chapters():
        documento = load_document(Path(source))
        forge.state.mode = "completa"  # un manoscritto: originale/ resta intatto
        ms.import_text(forge.project, forge.state, documento.raw_text, source=str(source))
    if kind:
        forge.state.quality = {**forge.state.quality, "kind": kind}
    forge.save()
    return forge


def open_forge(ppaths, cfg: AppConfig, *, client: Any = None, mode: str = "nuovo") -> Forge:
    """Apre (o prepara) il libro del progetto attivo. ``mode``: scrivere da zero (``nuovo``, in
    ``book/``), completare (``completa/``) o aggiornare (``aggiorna/``) un manoscritto: tre lavori
    separati, ciascuno nella sua cartella, con le stesse fasi."""
    from bookwriter.forge.project import MODES

    if mode not in MODES:
        raise ValueError(f"Lavoro sconosciuto: {mode} (usa {'|'.join(MODES)})")
    radice = ppaths.book if mode == "nuovo" else ppaths.root / MODES[mode]
    forge = Forge(radice, cfg, client=client, db_path=ppaths.db, vectors_path=ppaths.vectors)
    if not forge.project.state_path.exists():
        forge.state.mode = mode
    return forge
