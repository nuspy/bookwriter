"""Aggiornare seguendo la catena: un fatto spostato cambia chi lo sa, cosa ne segue, i viaggi.

Spostare l'incontro dall'Empire State Building alla Torre di Pisa non tocca solo i paragrafi che
nominano l'Empire State Building. Luca lo viene a sapere da una lettera nel capitolo 2 e nel
capitolo 3 vola dodici ore verso New York: il capitolo 3 non nomina il vecchio, ma ne dipende. La
catena delle dipendenze, ricavata dal registro del racconto, lo trova, lo fa leggere sapendo cosa
cercarvi e lo aggiorna con il suo «dipende da». Finito l'aggiornamento, la cronologia dei capitoli
toccati si ricostruisce sul testo nuovo; e personaggi e dialoghi sono categorie con un nome fisso,
che il documento delle modifiche raggruppa.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bookwriter.config import AppConfig
from bookwriter.forge import changes as ch
from bookwriter.forge import timeline as tl
from bookwriter.forge.engine import open_forge
from bookwriter.forge.project import ForgeState
from bookwriter.projects import ProjectPaths
from tests.test_manuscript_modes import ModelloManoscritto

MANOSCRITTO = """# La partenza

Marta fissa l'incontro con Luca all'Empire State Building, per sabato a mezzogiorno.

Poi scrive la lettera e la imbuca.

# La lettera

Luca legge la lettera di Marta e capisce che l'incontro sarà a New York, sabato.

«Ci vediamo all'Empire State Building», dice la lettera, con la grafia di Marta.

# Il volo

Luca prende il volo: dodici ore fino a New York, e arriva stanco.

In aereo dorme poco.

# Il giardino

Nel giardino di casa, la madre di Luca pota le rose.
"""

#: Le schede del registro, capitolo per capitolo: chi sa cosa, eventi.
SCHEDE = {
    "1": {"summary": "Marta fissa l'incontro con Luca.",
          "knowledge": [{"who": "Marta", "what": "l'incontro con Luca è all'Empire State "
                         "Building", "source": ""}],
          "events": [{"when": "sabato, mezzogiorno", "what": "incontro fissato all'Empire State "
                      "Building", "where": "New York"}]},
    "2": {"summary": "Luca legge la lettera di Marta.",
          "knowledge": [{"who": "Luca", "what": "l'incontro con Marta è all'Empire State "
                         "Building", "source": "la lettera di Marta"}],
          "events": []},
    "3": {"summary": "Luca vola verso l'incontro con Marta; la lettera è in tasca.",
          "knowledge": [], "events": [{"when": "venerdì", "what": "Luca vola dodici ore",
                                       "where": "in aereo"}]},
    "4": {"summary": "La madre di Luca pota le rose in giardino.", "knowledge": [],
          "events": []},
}


class ModelloCatena(ModelloManoscritto):
    """Sa ricavare le schede del registro, la catena delle dipendenze e applicare il cambiamento
    alla Torre di Pisa."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.anelli: list[dict] = []
        self.anelli_rotti = False
        self.letture: dict[str, str] = {}   # capitolo → il prompt della lettura
        self.scene: dict[str, dict] = {}

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = set(schema.get("properties", {}))
        utente = messages[-1]["content"]
        if "end_state" in props:                            # la scheda del registro
            numero = re.search(r"# Capitolo (\d+)", utente)
            scheda = SCHEDE.get(numero.group(1) if numero else "", {})
            return {**self.registro, "told": [], "inventions": [], "motifs": [],
                    "end_state": {**self.registro["end_state"], "knows": [], "objects": [],
                                  "place": "casa"}, **scheda}
        if props == {"changes"}:                            # il profilo delle differenze
            return {"changes": [{"change": "L'incontro si sposta all'Empire State Building alla "
                                           "Torre di Pisa", "kind": "luogo",
                                 "old": "Empire State Building", "new": "Torre di Pisa",
                                 "when": "",
                                 "facets": [{"category": "Personaggi e relazioni", "old": "Luca "
                                             "aspetta Marta a New York", "question": "Chi sa "
                                             "dell'incontro?", "old_terms": []},
                                            {"category": "viaggio", "old": "il volo per New York",
                                             "question": "Quanto dura il volo per Pisa?",
                                             "old_terms": ["Empire State Building"]}]}]}
        if props == {"links"}:                              # la catena delle dipendenze
            if self.anelli_rotti:
                raise RuntimeError("il server non risponde")
            return {"links": self.anelli}
        if props == {"impacts"}:
            numero = re.search(r"# Capitolo (\d+)", utente).group(1)
            self.letture[numero] = utente
            return {"impacts": self.impatti.get(numero, [])}
        if props == {"rewritten", "insert_after"}:
            da_fare = re.findall(r"^\[(\d+)\] (.+)$", utente.split("# I paragrafi da riscrivere")[1]
                                 .split("# Il paragrafo dopo")[0], re.MULTILINE)
            return {"rewritten": [t.replace("Empire State Building", "Torre di Pisa")
                                  .replace("dodici ore fino a New York",
                                           "un'ora e mezza fino a Pisa")
                                  .replace("a New York", "a Pisa") for _n, t in da_fare],
                    "insert_after": ""}
        if "locked_in_at_end" in props:                     # la cronologia di una scena
            for chiave, record in self.scene.items():
                if chiave in utente.split("# La scena", 1)[-1]:
                    return record
            return {}
        return super().chat_json(messages, schema=schema, model=model, **kw)


def _forge(tmp_path: Path, modello: ModelloCatena, testo: str = MANOSCRITTO):
    paths = ProjectPaths(tmp_path / "progetto")
    paths.root.mkdir(parents=True)
    f = open_forge(paths, AppConfig(), client=modello, mode="aggiorna")
    f.start()
    file = tmp_path / "manoscritto.md"
    file.write_text(testo, encoding="utf-8")
    f.import_manuscript(file)
    f.state.notebook["changes"] = [
        "L'incontro si sposta dall'Empire State Building alla Torre di Pisa"]
    return f


ANELLI = [
    {"chapter": 2, "kind": "conoscenza", "fact": "Luca sa che l'incontro è sull'Empire State "
     "Building", "who": ["Luca"], "depends_on_chapter": 1,
     "depends_on": "Marta fissa l'incontro", "why": "Luca lo sa dalla lettera"},
    {"chapter": 3, "kind": "viaggio", "fact": "Luca vola dodici ore verso New York",
     "who": ["Luca"], "depends_on_chapter": 2, "depends_on": "Luca sa dell'incontro",
     "why": "il volo per Pisa è un altro"},
]


# --------------------------------------------------------------------------- #
#  la catena delle dipendenze
# --------------------------------------------------------------------------- #
def test_a_chapter_that_never_names_the_old_fact_is_updated_because_it_depends_on_it(
        tmp_path: Path) -> None:
    modello = ModelloCatena()
    modello.anelli = ANELLI
    f = _forge(tmp_path, modello)
    # la lettura del capitolo 2 lega il suo punto all'anello della catena che le è stato dato
    modello.impatti = {"2": [{"paragraphs": [1], "change": 1, "category": "personaggi",
                              "what": "Luca capisce che l'incontro sarà a Pisa", "need": "",
                              "action": "riscrivi", "depends": ["1.2"]}]}
    f.make_spec()
    dati = ch.load(f.project)

    # il grafo: il fatto del capitolo 1, chi lo sa nel 2, il viaggio del 3 che ne segue
    per_id = {d["id"]: d for d in dati["dependencies"]}
    volo = next(d for d in dati["dependencies"] if d["chapter"] == 3 and d["kind"] == "viaggio")
    sa = per_id[volo["parent"]]
    assert sa["chapter"] == 2 and sa["kind"] == "conoscenza" and sa["who"] == ["Luca"]
    origine = per_id[sa["parent"]]
    assert origine["chapter"] == 1 and origine["parent"] == ""
    assert volo["depth"] == 3 and volo["chain"][0].startswith("Cambiamento 1")
    assert len(volo["chain"]) == 4

    # la lettura di ogni capitolo riceve i suoi anelli da cercare
    assert "[1.3] viaggio (Luca): Luca vola dodici ore" in modello.letture["3"]
    assert "Le conseguenze a catena" not in modello.letture["4"]

    # il capitolo 3 non nomina il vecchio, ma ne dipende: diventa un impatto col suo perché
    volo_impatti = dati["chapters"]["3"]
    assert volo_impatti and volo_impatti[0]["paragraphs"] == [1]
    assert "dipende da: cap. 2 · conoscenza (Luca)" in volo_impatti[0]["what"]
    assert volo_impatti[0]["depends"] == [volo["id"]]
    # il punto che la lettura ha legato all'anello porta il suo «dipende da»
    lettera = dati["chapters"]["2"]
    assert lettera[0]["depends"] == ["1.2"] and "dipende da: cap. 1" in lettera[0]["what"]
    stati = {c["number"]: c["status"] for c in f.state.outline}
    assert stati == {1: "da_aggiornare", 2: "da_aggiornare", 3: "da_aggiornare",
                     4: "originale"}, "il giardino non dipende dall'incontro"

    # il piano lo mostra, ad albero
    spec = f.project.spec_path.read_text(encoding="utf-8")
    assert "### Le conseguenze a catena" in spec
    assert "  - cap. 2 · conoscenza (Luca)" in spec and "    - cap. 3 · viaggio (Luca)" in spec

    f.approve_spec()
    f.write_all()
    volo_testo = f.project.chapter_path(3, "Il volo").read_text(encoding="utf-8")
    assert "un'ora e mezza fino a Pisa" in volo_testo
    assert "In aereo dorme poco." in volo_testo, "il resto resta com'è"
    resoconto = (f.project.out_dir / "modifiche.md").read_text(encoding="utf-8")
    assert "## Le conseguenze a catena" in resoconto
    assert "dipende da: cap. 2" in resoconto
    # finito l'aggiornamento, la cronologia dei capitoli toccati è stata ricostruita
    assert "## La cronologia dopo le modifiche" in resoconto
    assert "Capitoli ricostruiti: 1, 2, 3." in resoconto
    assert ch.load(f.project)["consistency_done"] is True
    assert tl.timeline_path(f.project, 3, "Il volo").exists()


def test_who_learns_the_fact_carries_it_into_later_chapters_without_forcing_a_rewrite(
        tmp_path: Path) -> None:
    modello = ModelloCatena()          # il modello non trova anelli: restano quelli del registro
    f = _forge(tmp_path, modello)
    f.make_spec()
    dati = ch.load(f.project)
    regole = [d for d in dati["dependencies"] if d["origin"] == "regola"]
    # le schede che nominano il vecchio: chi lo sa, quale evento
    assert any(d["chapter"] == 2 and d["kind"] == "conoscenza" and "Luca" in d["who"]
               for d in regole)
    # Luca lo porta nel capitolo 3 (nome e cosa sa in comune), non nel giardino (solo il nome)
    portato = [d for d in regole if d["chapter"] == 3]
    assert portato and portato[0]["parent"], "dipende da chi l'ha saputo"
    assert not any(d["chapter"] == 4 for d in dati["dependencies"])
    # un anello del solo registro che la lettura non ha trovato si guarda, non si riscrive
    impatti = dati["chapters"]["3"]
    assert impatti and all(i["action"] == "verifica" and not i["paragraphs"] for i in impatti)
    assert f.state.outline[2]["status"] == "originale"
    assert "la lettura non l'ha trovato nel testo" in f.project.spec_path.read_text(
        encoding="utf-8")


def test_a_failed_dependency_search_is_recorded_and_the_plan_goes_on(tmp_path: Path) -> None:
    modello = ModelloCatena()
    modello.anelli_rotti = True
    f = _forge(tmp_path, modello)
    f.make_spec()
    dati = ch.load(f.project)
    assert dati["dependency_errors"] and "non risponde" in dati["dependency_errors"][0]["error"]
    assert dati["chapters"], "la mappa dell'impatto c'è lo stesso"
    assert any(d["origin"] == "regola" for d in dati["dependencies"])
    assert "dipendenze non cercate" in f.project.spec_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
#  personaggi e dialoghi
# --------------------------------------------------------------------------- #
def test_characters_and_dialogues_are_named_categories_the_profile_always_has() -> None:
    assert ch.category_key("Personaggi e relazioni") == ch.CHARACTERS
    assert ch.category_key("dialogue") == ch.DIALOGUES
    assert ch.category_key("Battute") == ch.DIALOGUES
    assert ch.category_key("persone incontrate (mestieri)") == "persone incontrate (mestieri)"
    assert ch.category_key("clima") == "clima"

    s = ForgeState(mode="aggiorna")
    s.notebook = {"changes": ["L'albergo si sposta da Milano a Hong Kong"]}
    visti = []

    class Guarda:
        def chat_json(self, messages, *, schema, **kw):
            visti.append(messages[0]["content"])
            return {"changes": [{"change": "Milano → Hong Kong", "kind": "luogo",
                                 "old": "Milano", "new": "Hong Kong", "when": "",
                                 "facets": [{"category": "Dialoghi", "old": "«ciao»",
                                             "question": "Come si saluta?", "old_terms": []}]}]}

    profilo = ch.profile(s, Guarda(), "riassunto")
    categorie = [f["category"] for f in profilo[0]["facets"]]
    assert categorie.count(ch.DIALOGUES) == 1, "quella del modello, col nome fisso"
    assert ch.CHARACTERS in categorie, "quella che il modello non ha scritto c'è comunque"
    assert f"'{ch.CHARACTERS}'" in visti[0] and f"'{ch.DIALOGUES}'" in visti[0]


def test_the_change_document_groups_edits_by_characters_and_dialogues(tmp_path: Path) -> None:
    modello = ModelloCatena()
    modello.anelli = ANELLI
    f = _forge(tmp_path, modello)
    modello.impatti = {"2": [{"paragraphs": [1], "change": 1, "category": "Personaggi",
                              "what": "Luca capisce che l'incontro sarà a Pisa", "need": "",
                              "action": "riscrivi", "depends": []}]}
    f.make_spec()
    dati = ch.load(f.project)
    # la battuta che nomina il vecchio, non segnalata dalla lettura, è un punto «dialoghi»
    battuta = [i for i in dati["chapters"]["2"] if i["paragraphs"] == [2]]
    assert battuta and battuta[0]["category"] == ch.DIALOGUES
    assert dati["chapters"]["2"][0]["category"] == ch.CHARACTERS, "nome canonico"
    spec = f.project.spec_path.read_text(encoding="utf-8")
    assert "### Personaggi" in spec and "### Dialoghi" in spec
    f.approve_spec()
    f.write_all()
    gruppi = ch.by_category(ch.load(f.project))
    assert list(gruppi)[:2] == [ch.CHARACTERS, ch.DIALOGUES]
    assert any(v["chapter"] == 2 and v["paragraphs"] == [2] for v in gruppi[ch.DIALOGUES])
    assert any("Luca capisce" in v["what"] for v in gruppi[ch.CHARACTERS])
    resoconto = (f.project.out_dir / "modifiche.md").read_text(encoding="utf-8")
    personaggi = resoconto.split("### Personaggi", 1)[1].split("###", 1)[0]
    assert "Luca capisce che l'incontro sarà a Pisa" in personaggi
    dialoghi = resoconto.split("### Dialoghi", 1)[1].split("##", 1)[0]
    assert "cap. 2 §2" in dialoghi


# --------------------------------------------------------------------------- #
#  la cronologia ricostruita dopo le modifiche
# --------------------------------------------------------------------------- #
CON_SCENE = """# La partenza

## Sera

Marta aspetta il treno delle 21:00 alla stazione.

Il treno parte.

## Mattina

Alle 10:00 Marta è ancora alla stazione.
"""


def test_after_the_update_the_timeline_is_rebuilt_and_what_does_not_add_up_is_requeued_once(
        tmp_path: Path) -> None:
    modello = ModelloCatena()
    f = _forge(tmp_path, modello)
    f.project.chapter_path(1, "La partenza").write_text(CON_SCENE, encoding="utf-8")
    dati = ch.load(f.project)
    dati["applied"] = {"1": [{"paragraphs": [1], "reasons": ["il treno"], "added": False}]}
    ch.save(f.project, dati)
    # stesso giorno, e la seconda scena comincia prima della fine della prima
    modello.scene = {
        "## Sera": {"day": "sabato", "time_start": "21:00", "time_end": "23:00",
                    "place_start": "stazione", "place_end": "stazione"},
        "## Mattina": {"day": "sabato", "time_start": "10:00", "time_end": "10:30",
                       "place_start": "stazione", "place_end": "stazione"},
    }
    esito = ch.consistency_check(f.project, f.state, modello)
    assert esito["chapters"] == [1]
    tempo = [p for p in esito["issues"] if p["kind"] == "tempo"]
    assert tempo and tempo[0]["origin"] == "regola" and tempo[0]["section"] == 1
    salvata = tl.load(tl.timeline_path(f.project, 1, "La partenza"))
    assert salvata is not None and salvata.records[0].time_start == "21:00"
    assert ch.load(f.project)["consistency"]["issues"], "il resoconto sta in impact.json"

    assert ch.requeue_consistency(f.project, f.state) == 1
    dati = ch.load(f.project)
    punto = dati["chapters"]["1"][0]
    assert punto["category"] == ch.CHRONOLOGY and punto["paragraphs"] == [3], \
        "il primo paragrafo della scena che non torna"
    assert "correzione minima" in punto["what"]
    assert f.state.outline[0]["status"] == "da_aggiornare"
    assert dati["applied_history"]["1"], "quello che era stato applicato resta nel documento"
    assert ch.requeue_consistency(f.project, f.state) == 0, "una volta sola"
    assert "## La cronologia dopo le modifiche" in "\n".join(ch.consistency_markdown(dati))


def test_deadlines_and_stolen_objects_are_checked_across_chapters() -> None:
    uno = tl.record_from_dict({"deadlines": [{"id": "treno", "what": "il treno", "when": "21:00"}],
                               "objects": [{"id": "lettera", "what": "la lettera",
                                            "holder": "Marta", "status": "sottratto"}]})
    due = tl.record_from_dict({"deadlines": [{"id": "treno", "what": "il treno", "when": "23:30"}],
                               "objects": [{"id": "lettera", "what": "la lettera",
                                            "holder": "Luca", "status": "sottratto"}]})
    problemi = tl.check_book([(2, [due]), (1, [uno])])
    assert {(n, p.kind) for n, p in problemi} == {(2, "scadenza"), (2, "oggetto")}
    assert all(p.origin == "regola" for _n, p in problemi)
    assert tl.check_book([(1, [uno])]) == []


def test_the_ledger_records_who_knows_what_and_the_events(tmp_path: Path) -> None:
    from bookwriter.forge import ledger as lg

    modello = ModelloCatena()
    f = _forge(tmp_path, modello)
    voce = lg.load(f.project).entry(2)
    assert voce.knowledge == [{"who": "Luca", "what": "l'incontro con Marta è all'Empire State "
                               "Building", "source": "la lettera di Marta"}]
    assert "**Chi sa cosa**" in (f.project.story_dir / "ledger.md").read_text(encoding="utf-8")
    # un registro scritto prima di questi campi si legge ancora
    percorso = lg.ledger_path(f.project)
    vecchio = json.loads(percorso.read_text(encoding="utf-8"))
    for v in vecchio["chapters"].values():
        v.pop("knowledge"), v.pop("events")
    percorso.write_text(json.dumps(vecchio), encoding="utf-8")
    assert lg.load(f.project).entry(2).knowledge == []
