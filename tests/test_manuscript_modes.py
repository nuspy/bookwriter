"""Completare e aggiornare un manoscritto: la stessa macchina a fasi, su un libro che esiste.

Il manoscritto è dell'autore: niente lo riscrive se non dove il piano lo dice. Completando, il
testo originale resta parola per parola e le parti nuove si inseriscono; aggiornando, si
riscrivono solo i paragrafi che un cambiamento tocca — anche di riflesso: spostare un evento
da New York a Milano cambia la mancia al cameriere, non solo il nome della città.
"""

from __future__ import annotations

import re
from pathlib import Path

from bookwriter.config import AppConfig
from bookwriter.forge import changes as ch
from bookwriter.forge import completion as cm
from bookwriter.forge import interview as iv
from bookwriter.forge import manuscript as ms
from bookwriter.forge import writing as wr
from bookwriter.forge.engine import open_forge
from bookwriter.projects import ProjectPaths
from tests.test_editorial import ModelloEditoriale

MANOSCRITTO = """# Il viaggio

Marta prenota il volo per New York e conta i dollari nel portafoglio.

All'aeroporto JFK la aspetta un taxi giallo, e fuori nevica: è gennaio.

# La cena

Al ristorante sulla Fifth Avenue Marta lascia il venti per cento di mancia.

Poi sale al ventesimo piano dell'albergo, quello che segue il dodicesimo senza il tredicesimo.

# L'incontro

Sull'Empire State Building Marta incontra Luca, che le consegna la lettera.
"""


class ModelloManoscritto(ModelloEditoriale):
    """Sa leggere un manoscritto, progettarne il completamento e seguirne le modifiche."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.completamento: dict = {}
        self.inserimenti: list[dict] = []
        self.impatti: dict[str, list[dict]] = {}
        self.riscritture: list[str] = []

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = set(schema.get("properties", {}))
        utente = messages[-1]["content"]
        if "characters" in props and "changes" in props:   # il quaderno dal manoscritto
            return {"title": "Il viaggio di Marta", "form": "romanzo", "premise": "Marta "
                    "consegna una lettera", "characters": [{"name": "Marta", "role":
                                                            "protagonista"}]}
        if props == {"chapters", "rationale"}:
            return self.completamento
        if "dramatic_question" in props and not self.piano:
            # la protagonista di questo manoscritto vive e decide il presente
            piano = super().chat_json(messages, schema=schema, model=model, **kw)
            for scena in piano["scenes"]:
                scena["characters"] = "Marta"
                scena["decision"] = f"Marta decide: {scena['decision']}"
            return piano
        if props == {"scenes"} and "after" in str(schema):
            return {"scenes": self.inserimenti}
        if props == {"changes"}:                            # il profilo delle differenze
            return {"changes": [{"change": "New York diventa Milano", "kind": "luogo",
                                 "old": "New York", "new": "Milano", "when": "gennaio",
                                 "facets": [{"category": "mance", "old": "20%",
                                             "question": "Quanto si lascia di mancia a Milano?",
                                             "old_terms": ["Fifth Avenue", "dollari"]}]}]}
        if props == {"impacts"}:
            titolo = re.search(r"# Capitolo (\d+)", utente)
            return {"impacts": self.impatti.get(titolo.group(1) if titolo else "", [])}
        if props == {"rewritten", "insert_after"}:
            da_fare = re.findall(r"^\[(\d+)\] (.+)$", utente.split("# I paragrafi da riscrivere")[1]
                                 .split("# Il paragrafo dopo")[0], re.MULTILINE)
            self.riscritture.append(utente)
            return {"rewritten": [testo.replace("Fifth Avenue", "via Montenapoleone")
                                  .replace("il venti per cento di mancia", "due euro sul tavolo")
                                  .replace("dollari", "euro") for _n, testo in da_fare],
                    "insert_after": ""}
        return super().chat_json(messages, schema=schema, model=model, **kw)

    def chat(self, messages, **kw):
        sistema = messages[0]["content"]
        if "UNA SCENA" in sistema and "Scrivi la parte che manca" in messages[1]["content"]:
            return "Luca esita, poi le racconta da dove viene la lettera."
        return super().chat(messages, **kw)


def _forge(tmp_path: Path, mode: str, modello: ModelloManoscritto):
    paths = ProjectPaths(tmp_path / "progetto")
    paths.root.mkdir(parents=True)
    f = open_forge(paths, AppConfig(), client=modello, mode=mode)
    f.start()
    file = tmp_path / "manoscritto.md"
    file.write_text(MANOSCRITTO, encoding="utf-8")
    f.import_manuscript(file)
    return f


# --------------------------------------------------------------------------- #
#  il manoscritto
# --------------------------------------------------------------------------- #
def test_a_manuscript_is_split_into_chapters_by_its_own_headings() -> None:
    assert [c.title for c in ms.split_manuscript(MANOSCRITTO)] == ["Il viaggio", "La cena",
                                                                   "L'incontro"]
    senza_markdown = "Capitolo 1: Il porto\n\nTesto uno.\n\nCapitolo 2: Il molo\n\nTesto due."
    assert [c.title for c in ms.split_manuscript(senza_markdown)] == ["Il porto", "Il molo"]
    assert len(ms.split_manuscript("Un solo blocco di testo.")) == 1


def test_each_job_lives_in_its_own_folder(tmp_path: Path) -> None:
    paths = ProjectPaths(tmp_path / "p")
    paths.root.mkdir()
    for modo, cartella in (("nuovo", "book"), ("completa", "completa"), ("aggiorna", "aggiorna")):
        f = open_forge(paths, AppConfig(), mode=modo)
        assert f.project.root == paths.root / cartella and f.mode == modo


def test_importing_keeps_the_original_and_prefills_what_the_interview_would_ask(
        tmp_path: Path) -> None:
    f = _forge(tmp_path, "completa", ModelloManoscritto())
    assert len(ms.originals(f.project)) == 3
    assert [c["status"] for c in f.state.outline] == ["originale"] * 3
    assert f.state.notebook["title"] == "Il viaggio di Marta", "il quaderno parte dal testo"
    assert "Capitolo 1" in f.state.manuscript["summary"], "l'intervista sa cosa c'è"
    assert iv.opening_for(f.state) == iv.OPENING_COMPLETE
    assert "completare un manoscritto" in iv.system_prompt(f.state)
    assert not f.requirements("interview") or "conversazione" in f.requirements("interview")[0]


# --------------------------------------------------------------------------- #
#  completare
# --------------------------------------------------------------------------- #
def test_the_completion_plan_never_reorders_or_drops_the_original_chapters() -> None:
    attuale = [{"number": i, "title": f"C{i}", "source": i, "words": 1000, "status": "originale"}
               for i in (1, 2, 3)]
    proposta = {"chapters": [
        {"source": 2, "title": "C2", "does": "", "words": 1000, "points": [], "missing": []},
        {"source": 0, "title": "Nuovo dopo il 2", "does": "x", "words": 0, "points": ["a"],
         "missing": []},
        {"source": 1, "title": "C1", "does": "", "words": 1000, "points": [],
         "missing": ["la lettera arriva"]},
    ]}
    struttura = cm.merge_outline(proposta, attuale)["chapters"]
    assert [(c["title"], c["status"]) for c in struttura] == [
        ("C1", "da_completare"), ("C2", "originale"), ("Nuovo dopo il 2", "nuovo"),
        ("C3", "originale")]
    assert [c["number"] for c in struttura] == [1, 2, 3, 4]
    assert struttura[2]["words"] == 1000, "un capitolo nuovo misura come gli altri"


def test_completing_adds_chapters_and_missing_parts_and_keeps_the_original_word_for_word(
        tmp_path: Path) -> None:
    modello = ModelloManoscritto(piano=None)
    modello.completamento = {"chapters": [
        {"source": 1, "title": "Il viaggio", "does": "", "words": 0, "points": [], "missing": []},
        {"source": 0, "title": "La notte", "does": "Marta non dorme", "words": 600,
         "points": ["Marta rilegge la lettera"], "missing": []},
        {"source": 2, "title": "La cena", "does": "", "words": 0, "points": [], "missing": []},
        {"source": 3, "title": "L'incontro", "does": "", "words": 0, "points": [],
         "missing": ["Luca racconta da dove viene la lettera"]}], "rationale": "r"}
    modello.inserimenti = [{**{k: "" for k in ("id", "kind", "pov", "place", "time",
                                               "characters", "obstacle", "stakes", "clue",
                                               "belief_before", "belief_after", "decision",
                                               "consequence", "danger", "bridge")},
                            "goal": "Luca racconta", "claim_ids": [], "words": 300, "after": 1}]
    f = _forge(tmp_path, "completa", modello)
    f.make_spec()
    f.approve_spec()
    assert [c["title"] for c in f.state.outline] == ["Il viaggio", "La notte", "La cena",
                                                     "L'incontro"]
    assert f.project.chapter_path(3, "La cena").exists(), "i file seguono il numero nuovo"
    assert ms.original_path(f.project, 2, "La cena").exists(), "l'originale no"
    assert [c["number"] for c in wr.pending_chapters(f.project, f.state)] == [2, 4]

    report = f.write_all()
    assert len(report.written) == 2 and not wr.pending_chapters(f.project, f.state)
    nuovo = f.project.chapter_path(2, "La notte").read_text(encoding="utf-8")
    assert nuovo.startswith("# Capitolo 2: La notte")
    completato = f.project.chapter_path(4, "L'incontro").read_text(encoding="utf-8")
    originale = ms.original_path(f.project, 3, "L'incontro").read_text(encoding="utf-8")
    frase = "Sull'Empire State Building Marta incontra Luca, che le consegna la lettera."
    assert frase in originale and frase in completato, "il testo originale resta parola per parola"
    assert completato.index(frase) < completato.index("Luca esita"), "la parte nuova va dopo il §1"
    # la voce del manoscritto arriva alla stesura delle parti nuove
    assert any("La voce del libro" in u for _s, u in modello.scene_prompt)


# --------------------------------------------------------------------------- #
#  aggiornare
# --------------------------------------------------------------------------- #
def test_an_update_follows_the_change_into_its_consequences_and_marks_every_edit(
        tmp_path: Path) -> None:
    modello = ModelloManoscritto()
    f = _forge(tmp_path, "aggiorna", modello)
    assert iv.opening_for(f.state) == iv.OPENING_UPDATE
    f.state.notebook["changes"] = ["La storia si sposta da New York a Milano"]
    modello.impatti = {"2": [{"paragraphs": [1], "change": 1, "category": "mance",
                              "what": "la mancia diventa di pochi euro",
                              "need": "Quanto si lascia di mancia a Milano?",
                              "action": "riscrivi"}]}
    f.make_spec()
    stati = {c["number"]: c["status"] for c in f.state.outline}
    # il capitolo 1 non l'ha segnalato la lettura, ma nomina «dollari»: lo trova la parola
    assert stati == {1: "da_aggiornare", 2: "da_aggiornare", 3: "originale"}
    assert f.state.outline[1]["points"] == ["Quanto si lascia di mancia a Milano?"]
    assert "Piano delle modifiche" in f.project.spec_path.read_text(encoding="utf-8")
    f.approve_spec()

    f.write_all()
    cena = f.project.chapter_path(2, "La cena").read_text(encoding="utf-8")
    assert "due euro sul tavolo" in cena and "via Montenapoleone" in cena
    assert "quello che segue il dodicesimo" in cena, "il paragrafo non toccato resta com'è"
    viaggio = f.project.chapter_path(1, "Il viaggio").read_text(encoding="utf-8")
    assert "dollari" not in viaggio
    assert "dollari" in ms.original_path(f.project, 1, "Il viaggio").read_text(encoding="utf-8")
    # il documento con le modifiche segnate e il resoconto
    for nome in ("modifiche_segnate.docx", "modifiche_segnate.html", "modifiche.md"):
        assert (f.project.out_dir / nome).exists(), nome
    rapporto = ch.revision_report(f.project, f.state)
    assert rapporto.counts()["replace"] >= 2
    assert "la mancia diventa di pochi euro" in (f.project.out_dir / "modifiche.md").read_text(
        encoding="utf-8")


def test_old_words_left_after_the_update_are_looked_at_again_once(tmp_path: Path) -> None:
    modello = ModelloManoscritto()
    f = _forge(tmp_path, "aggiorna", modello)
    f.state.notebook["changes"] = ["New York diventa Milano"]
    f.make_spec()
    # una riscrittura che dimentica la parola vecchia
    percorso = f.project.chapter_path(3, "L'incontro")
    percorso.write_text(percorso.read_text(encoding="utf-8") + "\nI dollari restano.\n",
                        encoding="utf-8")
    residui = ch.residual_sweep(f.project, f.state)
    assert any(r["chapter"] == 3 and r["term"] == "dollari" for r in residui)
    assert ch.requeue_residuals(f.project, f.state, residui) >= 1
    assert f.state.outline[2]["status"] == "da_aggiornare"
    assert ch.requeue_residuals(f.project, f.state, residui) == 0, "una volta sola"


def test_the_difference_profile_asks_the_research_instead_of_asserting() -> None:
    from bookwriter.forge.project import ForgeState

    s = ForgeState(mode="aggiorna")
    s.notebook = {"changes": ["L'albergo si sposta da Milano a Hong Kong"]}
    visti = []

    class Guarda:
        def chat_json(self, messages, *, schema, **kw):
            visti.append(messages[0]["content"])
            return {"changes": []}

    profilo = ch.profile(s, Guarda(), "riassunto")
    assert profilo[0]["change"].startswith("L'albergo"), "senza profilo resta il cambiamento"
    for cosa in ("superstizioni", "mancia", "lato della strada", "emisfero", "meteo"):
        assert cosa in visti[0], cosa
    assert "non la risposta" in visti[0], "le risposte le danno le fonti"


# --------------------------------------------------------------------------- #
#  «Sorpresa»: una descrizione, e tutto il resto in automatico
# --------------------------------------------------------------------------- #
def test_a_surprise_goes_from_one_paragraph_to_a_composed_book(tmp_path: Path) -> None:
    from tests.test_forge import FintoModello

    paths = ProjectPaths(tmp_path / "sorpresa")
    paths.root.mkdir(parents=True)
    f = open_forge(paths, AppConfig(), client=FintoModello(chapters=2, words=600))
    note: list[str] = []
    resoconto = f.surprise("Un giallo a Trieste, d'inverno, con la bora.",
                           note=lambda n: note.append(str(n)), formats=["md"])
    assert "Fatto" in resoconto
    assert f.state.interview_closed and f.state.spec_approved
    assert f.state.research_declined, "senza web si scrive senza ricerca, e lo si dice"
    assert len(f.project.written_chapters()) == 2
    assert f.state.surprise["done"] and f.state.built
    assert f.state.transcript[1]["text"].startswith("Un giallo a Trieste")
    assert any("Scrivo i capitoli" in n for n in note)
    # premere di nuovo non rifà niente
    assert "Fatto" in f.surprise()
