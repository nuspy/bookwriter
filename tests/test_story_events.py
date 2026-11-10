"""La seconda analisi editoriale del cap. 11: scene migliori, sequenza impossibile.

ChatGPT ha confrontato la prova v2 del capitolo con la prima stesura (docs/REVIEW_CAPITOLO_11_V2.md)
e ha trovato difetti che un revisore dello stile non vede: il protagonista sottrae l'ultima pagina
di un fascicolo e più avanti un'altra «pagina finale»; resta chiuso a chiave alle 2:10 ed è in
giardino alle 2:45 senza che si veda come; la cassa parte a mezzanotte e alle undici; una «fine
primavera» convive con un ordine datato 2019-11-04; il capitolo comincia in Francia mentre il
precedente lo lasciava in Egitto con un appuntamento; cinque scene dicono la stessa tesi con la
metafora dell'archiviazione. Ogni test qui sotto fissa uno di quei difetti.
"""

from __future__ import annotations

from pathlib import Path

from bookwriter.forge import editor as ed
from bookwriter.forge import ledger as lg
from bookwriter.forge import scenes as sc
from bookwriter.forge import timeline as tl
from bookwriter.forge.project import BookProject
from tests.test_editorial import ModelloEditoriale, _forge_romanzo, _pack, _piano, _scena


def _record(**kw) -> tl.SceneRecord:
    return tl.record_from_dict({"place_start": "archivio di Lione", "place_end": "archivio di Lione",
                                **kw})


# --------------------------------------------------------------------------- #
#  il piano: ogni scena cambia una cosa diversa, il ponte è una catena del protagonista
# --------------------------------------------------------------------------- #
def test_a_scene_that_changes_nothing_or_the_same_thing_is_rejected() -> None:
    pack = _pack()
    ferma = sc.plan_from_dict(_piano(_scena("1", changes=[]), _scena("2")))
    assert any("non cambia nessuna variabile" in d for d in sc.gate(ferma, pack, 1500))
    stessa = {"variable": "conoscenza", "before": "crede di indagare",
              "after": "capisce che il sistema lo sta archiviando come un documento"}
    doppia = sc.plan_from_dict(_piano(_scena("1", changes=[stessa]), _scena("2", changes=[stessa])))
    assert any("cambiano la stessa cosa" in d for d in sc.gate(doppia, pack, 1500))
    senza_domanda = sc.plan_from_dict(_piano(_scena("1", next_question=""), _scena("2")))
    assert any("domanda nuova" in d for d in sc.gate(senza_domanda, pack, 1500))


def test_a_discovery_without_the_reasoning_is_rejected() -> None:
    """Perché strappa la pagina invece di fotografarla? Il lettore deve poterlo seguire."""
    pack = _pack()
    muta = sc.plan_from_dict(_piano(_scena("1", reasoning={"observed": "", "interpretation": "",
                                                          "alternative": "", "why": ""}),
                                    _scena("2")))
    difetti = sc.gate(muta, pack, 1500)
    assert any("non ha il ragionamento" in d and "perché agisce così" in d for d in difetti)


def test_being_transported_is_not_a_bridge_and_open_threads_need_an_answer() -> None:
    """La prova v2: «He is not invited. He is transported» — e l'appuntamento col proprietario
    del capitolo 10 sparito."""
    precedente = lg.ChapterEntry(number=10, title="The Osiris Mysteries",
                                 end_state=lg.EndState(place="Dendera, ufficio di conservazione"),
                                 open_threads=["vedere il proprietario prima del tramonto"])
    pack = _pack(previous=precedente, protagonist="Nicolò Sforza",
                 open_threads=list(precedente.open_threads))
    subito = sc.plan_from_dict(_piano(
        _scena("1", place="Île-de-France, archivio provinciale", characters="Nicolò",
               decision="Nicolò firma il registro"), _scena("2", characters="Nicolò"),
        bridge={"from": "Cairo", "clue": "nessuno", "decision": "una berlina lo carica",
                "movement": "auto, una notte", "new_goal": "l'archivio"}))
    difetti = sc.gate(subito, pack, 1500)
    assert any("dev'essere di Nicolò Sforza" in d for d in difetti)
    assert any("fili aperti" in d and "proprietario" in d for d in difetti)

    voluto = sc.plan_from_dict({**_piano(
        _scena("1", place="Île-de-France, archivio provinciale", characters="Nicolò",
               decision="Nicolò firma il registro"), _scena("2", characters="Nicolò"),
        bridge={"from": "Dendera", "clue": "il numero di conservazione sul fascicolo di Bassam",
                "decision": "Nicolò decide di seguire il numero fino in Francia",
                "movement": "volo dal Cairo a Parigi, poi treno", "new_goal": "il fascicolo"}),
        "threads": [{"thread": "vedere il proprietario prima del tramonto",
                     "handling": "risolto nella scena 1: il proprietario gli dà il numero"}]})
    assert not sc.gate(voluto, pack, 1500)


def test_research_without_a_narrative_function_stays_in_the_archive() -> None:
    from bookwriter.forge import claims as cl

    pack = _pack(claims=[cl.Claim(id="Ka", text="resina"), cl.Claim(id="Kb", text="ordinanza")])
    piano = sc.plan_from_dict(_piano(_scena("1", claim_uses=[
        {"claim_id": "Ka", "function": "enigma", "how": "il campione nella cassa"},
        {"claim_id": "Kb", "function": "perché c'era", "how": "un elenco di commi"}]),
        _scena("2")))
    assert piano.scenes[0].claim_ids == ["Ka", "Kb"]
    assert any("senza dire a cosa servono" in d for d in sc.gate(piano, pack, 1500))
    sc.normalise(piano, pack, 1500)
    assert piano.scenes[0].claim_ids == ["Ka"], "senza funzione resta nell'archivio"


def test_old_plans_with_only_claim_ids_still_load() -> None:
    vecchio = {"scenes": [{"id": "1", "goal": "g", "claim_ids": ["K1"], "words": 900}]}
    assert sc.plan_from_dict(vecchio).scenes[0].claim_ids == ["K1"]


# --------------------------------------------------------------------------- #
#  il registro degli eventi: i controlli che non chiedono un modello
# --------------------------------------------------------------------------- #
def test_the_same_page_taken_twice_is_caught() -> None:
    pagina = {"id": "pagina_finale", "what": "l'ultima pagina del fascicolo", "holder": "Nicolò",
              "status": "sottratto"}
    problemi = tl.check([_record(objects=[pagina]), _record(objects=[pagina])])
    assert any(p.kind == "oggetto" and p.severity == tl.BLOCKING for p in problemi)


def test_a_locked_door_needs_an_escape_on_the_page() -> None:
    chiuso = _record(place_start="stanza interna dell'archivio",
                     place_end="stanza interna dell'archivio", time_start="2:10 a.m.",
                     locked_in_at_end=True)
    giardino = _record(place_start="giardino esterno, panchina", place_end="automobile",
                       time_start="2:45 a.m.")
    problemi = tl.check([chiuso, giardino])
    assert any("fuga non è mostrata" in p.problem and p.severity == tl.BLOCKING for p in problemi)
    giardino.arrival = "Forza la finestra del bagno con il tagliacarte e scende nel cortile."
    assert not [p for p in tl.check([chiuso, giardino]) if p.kind == "luogo"]


def test_one_deadline_has_one_time() -> None:
    a = _record(deadlines=[{"id": "partenza_cassa", "what": "la cassa parte", "when": "midnight 12:00 a.m."}])
    b = _record(deadlines=[{"id": "partenza_cassa", "what": "la cassa caricata", "when": "11:00 p.m."}])
    assert any(p.kind == "scadenza" for p in tl.check([a, b]))


def test_a_date_that_does_not_go_with_the_season_is_caught() -> None:
    problemi = tl.check([_record(season="late spring", dates=["order no. 2019-11-04"])])
    assert any(p.kind == "data" and "2019-11-04" in p.problem for p in problemi)
    assert not tl.check([_record(season="late spring", dates=["2019-05-24"])])


def test_times_go_forward_even_across_midnight() -> None:
    sera = _record(time_start="6:40 p.m.", time_end="8:00 p.m.")
    notte = _record(time_start="2:10 a.m.")
    assert not [p for p in tl.check([sera, notte]) if p.kind == "tempo"]
    indietro = _record(time_start="3:00 p.m.")
    assert any(p.kind == "tempo" for p in tl.check([sera, indietro]))


def test_moving_from_the_previous_chapter_needs_to_be_shown() -> None:
    francia = _record(place_start="archivio provinciale in Île-de-France")
    assert any(p.kind == "luogo" for p in tl.check([francia], "Dendera, tempio, lato ovest"))


# --------------------------------------------------------------------------- #
#  durante la stesura: le scene dopo ricevono i fatti fissati
# --------------------------------------------------------------------------- #
class ModelloEventi(ModelloEditoriale):
    """Registra i fatti di ogni scena: la prima sottrae la pagina finale."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.registri = 0
        self.problemi_scena: list[dict] = []
        self.etichette: dict[int, list[dict]] = {}
        self.piani_controllati: list[str] = []

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = set(schema.get("properties", {}))
        if "locked_in_at_end" in props:
            self.registri += 1
            primo = self.registri == 1
            return {"flashback": False, "day": "martedì", "time_start": "6:40 p.m." if primo else
                    "7:30 p.m.", "time_end": "", "place_start": "Dendera, ufficio di polizia",
                    "place_end": "Dendera, ufficio di polizia", "arrival": "", "present": ["Nicolò"],
                    "events": [], "objects": [{"id": "pagina_finale",
                                               "what": "l'ultima pagina del fascicolo",
                                               "holder": "Nicolò", "status": "sottratto"}]
                    if primo else [], "access": [], "locked_in_at_end": False,
                    "deadlines": [], "dates": [], "season": "",
                    "issues": self.problemi_scena if not primo else []}
        if props == {"paragraphs"}:
            return {"paragraphs": self.etichette.get(self.registri, [])}
        if props == {"issues"} and "PIANO delle scene" in messages[0]["content"]:
            self.piani_controllati.append(messages[1]["content"])
            return {"issues": []}
        if props == {"threads", "anticipations"}:
            return {"threads": [], "anticipations": []}
        return super().chat_json(messages, schema=schema, model=model, **kw)


def test_a_later_scene_knows_the_page_was_already_taken(tmp_path: Path) -> None:
    modello = ModelloEventi(piano=_piano(_scena("1"), _scena("2")), chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    f.write_next()
    _sistema, primo = modello.scene_prompt[0]
    sistema, secondo = modello.scene_prompt[1]
    assert "Fatti già fissati" not in primo
    assert "Fatti già fissati in questo capitolo" in secondo
    assert "l'ultima pagina del fascicolo: sottratto" in secondo
    assert "FIDATI DELLA SCENA" in sistema and "UNA METAFORA NON È UN SISTEMA" in sistema
    assert "IL RAGIONAMENTO SI VEDE" in sistema
    assert modello.piani_controllati, "il piano passa il controllo di continuità prima di scrivere"
    # il registro del testo finale va su disco, accanto al piano
    assert tl.timeline_path(f.project, 1, "Il passaggio 1").exists()


# --------------------------------------------------------------------------- #
#  la revisione: il significato sul capitolo intero, la continuità ricontrollata
# --------------------------------------------------------------------------- #
def test_the_same_idea_said_again_is_cut_and_an_image_is_not_a_system() -> None:
    tags = [ed._Tag(0, 0, "azione", "la porta archivia Nicolò", "archiviazione"),
            ed._Tag(0, 1, "commento", "la porta archivia Nicolò", "archiviazione"),
            ed._Tag(1, 0, "descrizione", "La porta archivia Nicolò", "archiviazione"),
            ed._Tag(1, 1, "commento", "", "archiviazione"),
            ed._Tag(1, 2, "dialogo", "", "archiviazione"),
            ed._Tag(2, 0, "azione", "", "archiviazione"),
            *[ed._Tag(3, i, "azione", "", "") for i in range(8)],
            ed._Tag(2, 1, "descrizione", "", "", unused=True)]
    ops = ed._meaning_ops(tags)
    assert ops[(0, 1)][0] == "idea", "il commento ripete quello che la scena ha mostrato"
    assert ops[(1, 0)][0] == "idea", "la stessa idea, anche in un'altra scena"
    assert ops[(1, 1)][0] == "immagine", "la quarta volta l'immagine diventa un sistema"
    assert (1, 2) not in ops, "nel dialogo l'immagine è del personaggio"
    assert (2, 0) not in ops, "l'ultima resta: è il punto d'arrivo"
    assert ops[(2, 1)][0] == "dettagli"
    assert (0, 0) not in ops, "la prima volta si tiene"


def test_the_book_vocabulary_is_not_rewritten_everywhere_and_the_cap_is_spread() -> None:
    """La prova v3: «archiviazione» in 135 paragrafi, e le 24 riscritture tutte nella scena 1."""
    tags = [ed._Tag(s, i, "commento" if i % 2 else "azione", "", "archiviazione")
            for s in range(6) for i in range(20)]
    tags += [ed._Tag(4, 30, "commento", "il sistema archivia le persone", ""),
             ed._Tag(5, 30, "commento", "il sistema archivia le persone", "")]
    ops = ed._meaning_ops(tags)
    immagini = [k for k, v in ops.items() if v[0] == "immagine"]
    assert len(immagini) == ed.IMAGE_OPS_PER_FAMILY, "il vocabolario del libro resta"
    assert len({k[0] for k in immagini}) == 6, "a giro fra le scene, non tutte nella prima"
    ordine = ed.ordered_ops(ops)
    assert ordine[0][1][0] == "idea", "prima le idee ripetute"


def test_the_editor_never_grows_a_chapter_beyond_its_measure() -> None:
    capitolo = ed.parse("# C\n\n## Scena\n\n" + "\n\n".join(
        " ".join(["parola"] * 100) for _ in range(5)))

    class Allunga:
        def chat(self, messages, **kw):
            return "## Scena\n\n" + " ".join(["parola"] * 560)

    ok, come = ed._revise_section(Allunga(), capitolo, 0, ["continuità"], None,
                                  _pack({"number": 1, "title": "C", "words": 450}),
                                  model=None, language="italiano", style="")
    assert not ok and "fuori dalla forbice" in come, "oltre misura la scena non si allunga"
    assert ed._room(capitolo, 450) == 0 and ed._room(capitolo, 1000) == 600


def test_the_editor_rewrites_repeated_meaning_without_emptying_the_scene(tmp_path: Path) -> None:
    testo = ("# Capitolo 11: The Excavation\n\n## La porta\n\n"
             + "\n\n".join(f"Nicolò spinge la porta numero {i} e trova il registro {i}." * 6
                           for i in range(4)))
    modello = ModelloEventi()
    modello.etichette = {0: [{"n": 1, "kind": "azione", "idea": "il sistema archivia le persone",
                              "image": "", "unused_detail": False},
                             {"n": 2, "kind": "commento", "idea": "il sistema archivia le persone",
                              "image": "", "unused_detail": False}]}
    progetto = BookProject(tmp_path / "book")
    progetto.scaffold()
    finale, resoconto = ed.edit_chapter(progetto, modello, testo, None,
                                        _pack({"number": 11, "title": "The Excavation",
                                               "words": 400}))
    assert resoconto.meaning
    assert "è già stata detta (scena 1, §1) → riscritto" in resoconto.meaning[0]
    assert resoconto.words_final >= ed.LENGTH_GUARD * resoconto.words_draft


def test_an_event_contradiction_is_fixed_and_checked_again(tmp_path: Path) -> None:
    """Il problema va alla scena, la scena si rivede, il registro si rifà: se il problema resta,
    il capitolo è da rivedere."""
    testo = ("# Capitolo 11: The Excavation\n\n## Lo scaffale\n\n"
             + "\n\n".join(f"Nicolò legge il foglio {i} sotto la lampada." * 8 for i in range(3))
             + "\n\n## La porta\n\n"
             + "\n\n".join(f"Nicolò prova la maniglia {i} e ascolta." * 8 for i in range(3)))
    modello = ModelloEventi()
    modello.problemi_scena = [{"severity": "bloccante", "kind": "oggetto",
                               "problem": "sottrae di nuovo la pagina finale",
                               "quote": "strappa l'ultima pagina", "fix": "è già sua"}]
    progetto = BookProject(tmp_path / "book")
    progetto.scaffold()
    _finale, resoconto = ed.edit_chapter(progetto, modello, testo, None,
                                         _pack({"number": 11, "title": "The Excavation",
                                                "words": 400}))
    righe = "\n".join(resoconto.continuity)
    assert "sottrae di nuovo la pagina finale" in righe
    assert "«La porta»" in righe, "la scena coinvolta passa alla revisione"
    assert "Dopo le correzioni restano" in righe, "il finto modello la segnala ancora"
    assert "continuità degli eventi" in resoconto.failed_critical
    assert resoconto.needs_review
    assert "| scena | quando |" in resoconto.timeline


def test_the_grid_is_three_separate_checks() -> None:
    assert set(ed.GRID_GROUPS) == {"racconto", "indagine", "stile"}
    chiavi = {k for k, _d, _c in ed.GRID}
    assert {"variabili", "ragionamento", "fidarsi", "metafore", "dettagli"} <= chiavi
    assert "continuita" not in chiavi, "la continuità la controlla il registro degli eventi"


def test_a_plan_is_repaired_scene_by_scene_not_rewritten_whole(tmp_path: Path) -> None:
    """Nella prova v3 il piano intero richiesto due volte tornava con gli stessi difetti: la
    riparazione chiede solo le scene da cambiare e le mette al loro posto."""
    class Pianificatore(ModelloEditoriale):
        def __init__(self) -> None:
            super().__init__(piano=_piano(_scena("1", changes=[]), _scena("2")))
            self.riparazioni: list[str] = []

        def chat_json(self, messages, *, schema, model=None, **kw):
            if "remove" in schema.get("properties", {}):
                self.riparazioni.append(messages[-1]["content"])
                return {"start": "", "bridge": {"from": "", "clue": "", "decision": "",
                                                "movement": "", "new_goal": ""},
                        "threads": [], "remove": [],
                        "scenes": [{**_scena("1", goal="aprire la cassa"), "after": ""}]}
            return super().chat_json(messages, schema=schema, model=model, **kw)

    modello = Pianificatore()
    progetto = BookProject(tmp_path / "book")
    progetto.scaffold()
    piano = sc.plan(progetto, None, modello, _pack(), cap=1500)
    assert modello.riparazioni and "Le scene coinvolte: 1" in modello.riparazioni[0]
    assert piano.origin == "riparato" and not piano.defects
    assert piano.scenes[0].goal == "aprire la cassa" and piano.scenes[1].id == "2"


# --------------------------------------------------------------------------- #
#  la stesura v4: ancore, apertura nel presente, scene distinte, motivo in catena, misura
# --------------------------------------------------------------------------- #
def test_the_plan_anchors_time_and_travel_and_opens_in_the_present() -> None:
    precedente = lg.ChapterEntry(number=10, title="The Osiris Mysteries",
                                 end_state=lg.EndState(place="Dendera, ufficio di polizia"))
    pack = _pack(previous=precedente)
    storico = sc.plan_from_dict(_piano(_scena("1", kind="episodio_storico", day="1120"),
                                       _scena("2")))
    assert any("comincia con un episodio storico" in d for d in sc.gate(storico, pack, 1500))
    viaggio = sc.plan_from_dict(_piano(
        _scena("1", time="7:30 p.m.", day="giovedì"),
        _scena("2", place="Borgogna, archivio", time="10:14 p.m.", day="giovedì",
               arrival="volo Cairo-Parigi, 5 ore, poi treno, 2 ore")))
    difetti = sc.gate(viaggio, pack, 1500)
    assert any("dura 7 ore" in d for d in difetti), difetti
    senza = sc.plan_from_dict(_piano(_scena("1"), _scena("2", time="", day="")))
    assert any("non hanno l'ancora" in d for d in sc.gate(senza, _pack(), 1500))


def test_two_scenes_doing_the_same_work_in_the_same_place_are_rejected() -> None:
    doppie = sc.plan_from_dict(_piano(
        _scena("1", place="archivio di Digione", goal="vedere le fiale nel cassetto"),
        _scena("2", place="archivio di Digione", goal="vedere le fiale nel cassetto")))
    assert any("stesso luogo con lo stesso obiettivo" in d for d in sc.gate(doppie, _pack(), 1500))


def test_the_motif_moves_along_a_chain_not_in_circles() -> None:
    piano = sc.plan_from_dict({**_piano(_scena("1"), _scena("2")), "motif_chain": [
        {"scene": "1", "motif": "filing", "step": "indizio", "what": "il registro"},
        {"scene": "2", "motif": "filing", "step": "indizio", "what": "di nuovo il registro"}]})
    assert any("ripete il passo indizio" in d for d in sc.gate(piano, _pack(), 1500))


def test_each_scene_gets_the_anchors_the_objects_and_its_motif_step(tmp_path: Path) -> None:
    piano = {**_piano(_scena("1", time="7:30 p.m."), _scena("2", time="9:00 p.m.")),
             "objects": [{"object": "il pacchetto di cera", "origin": "capitolo 10",
                          "holder_start": "Salma", "transfers": "nessuno",
                          "holder_end": "Salma"}],
             "motif_chain": [{"scene": "2", "motif": "filing", "step": "costo",
                              "what": "il registro espone Salma"}]}
    modello = ModelloEventi(piano=piano, chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    f.write_next()
    sistema, utente = modello.scene_prompt[1]
    assert "Le ancore del capitolo" in utente and "9:00 p.m." in utente
    assert "il pacchetto di cera: viene da capitolo 10; all'inizio Salma" in utente
    assert "filing: costo — il registro espone Salma" in utente
    assert "ORARI E VIAGGI" in sistema and "EPISODI STORICI" in sistema


def test_a_scene_far_over_its_measure_is_condensed_never_cut_blindly() -> None:
    from bookwriter.forge import writing as wr

    class Condensa:
        def __init__(self, parole: int) -> None:
            self.parole = parole
            self.richieste: list[str] = []

        def chat(self, messages, **kw):
            self.richieste.append(messages[0]["content"])
            return "## Scena\n\n" + " ".join(["evento"] * self.parole)

    unita = sc.Unit("1", words=1000)
    lungo = "## Scena\n\n" + " ".join(["parola"] * 1600)
    giusto = wr._condense(Condensa(950), lungo, unita, True, "T", _pack(), model=None)
    assert wr._count(giusto) < 1100
    troppo = wr._condense(Condensa(300), lungo, unita, True, "T", _pack(), model=None)
    assert troppo == lungo, "una condensazione che svuota la scena si scarta"
    nella_misura = "## Scena\n\n" + " ".join(["parola"] * 1200)
    assert wr._condense(Condensa(950), nella_misura, unita, True, "T", _pack(),
                        model=None) == nella_misura


def test_closed_lists_never_reach_the_server_and_strays_are_mapped() -> None:
    """La prova v4: Basalt ha rifiutato il piano intero per un «conseguenza» nel campo della
    funzione della ricerca (nove generazioni, nessun piano). Le liste chiuse restano scritte
    nella descrizione; i valori fuori lista si riconducono o si scartano nel codice."""
    from bookwriter.llm.client import relax_enums

    leggero = relax_enums(sc.PLAN_SCHEMA)
    assert '"enum"' not in str(leggero).replace("'", '"')
    funzione = leggero["properties"]["scenes"]["items"]["properties"]["claim_uses"]["items"][
        "properties"]["function"]
    assert funzione["type"] == "string" and "Uno fra: enigma, decisione" in funzione["description"]
    scena = sc._scene({"id": "1", "changes": [{"variable": "Knowledge", "before": "a",
                                               "after": "b"}],
                       "claim_uses": [{"claim_id": "K1", "function": "conseguenza", "how": "x"}]})
    assert scena.changes[0]["variable"] == "conoscenza"
    assert scena.claim_uses[0]["function"] == "decisione"
