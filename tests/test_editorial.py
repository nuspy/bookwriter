"""La qualità editoriale: i difetti veri di *The Red Thread*, uno per test.

L'analisi editoriale del capitolo 11 (*The Excavation*) ha trovato una tesi ripetuta per pagine,
un saggio al posto delle scene, un salto dall'Egitto alla Francia senza ponte, una bibliografia
con fonti mai lette. Ogni test qui sotto fissa uno di quei difetti e controlla che la stesura
non lo rifaccia: le misure lo vedono, il piano lo rifiuta, la scena non lo riceve, l'editor lo
toglie senza svuotare il capitolo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bookwriter.config import AppConfig, LLMConfig
from bookwriter.forge import claims as cl
from bookwriter.forge import context as cx
from bookwriter.forge import editor as ed
from bookwriter.forge import ledger as lg
from bookwriter.forge import metrics as mx
from bookwriter.forge import research as rs
from bookwriter.forge import research_store as store
from bookwriter.forge import scenes as sc
from bookwriter.forge import writing as wr
from bookwriter.forge.engine import Forge
from tests.test_forge import FintoModello, _pronto_a_scrivere

FIXTURE = Path(__file__).parent / "fixtures" / "editorial" / "ch11_public_court.md"


# --------------------------------------------------------------------------- #
#  un modello finto che sa fare piano, scene, registro e revisione
# --------------------------------------------------------------------------- #
class ModelloEditoriale(FintoModello):
    """Riconosce i compiti nuovi dallo schema o dal prompt e risponde in modo plausibile."""

    def __init__(self, *, piano: dict | None = None, scena_parole: int = 700, **kw) -> None:
        super().__init__(**kw)
        self.piano = piano
        self.scena_parole = scena_parole
        self.scene_prompt: list[tuple[str, str]] = []  # (sistema, utente)
        self.seguiti: list[str] = []
        self.gruppi: list[dict] = []
        self.extra: list[dict] = []
        self.registro = {
            "summary": "Nicolò viene trattenuto alla polizia di Dendera, poi rilasciato.",
            "end_state": {"place": "Dendera, ufficio di polizia", "time": "sera",
                          "physical": "stanco", "objects": ["registri fotografati"],
                          "knows": ["i registri sono cifrati"], "relations": [],
                          "threats": ["arresto"], "next_action": "incontrare Bassam"},
            "told": ["il fumo di resina era una tecnologia rituale"],
            "motifs": [{"motif": "fumo", "function": "indizio chimico"}],
            "inventions": [{"name": "Ufficio 7", "what": "ufficio dei registri",
                            "status": "fiction"}],
            "open_threads": ["chi ha cifrato i registri"], "danger": "arrestato",
        }
        self.n = 0

    def chat(self, messages, **kw):
        sistema = messages[0]["content"]
        if "UNA SCENA" in sistema:
            self.n += 1
            if len(messages) > 2 and messages[-2]["role"] == "assistant":
                self.seguiti.append(messages[-1]["content"])
                return wr.DONE
            self.scene_prompt.append((sistema, messages[1]["content"]))
            corpo = " ".join(f"Nicolò fa la cosa numero {self.n}-{i}." for i in range(
                self.scena_parole // 6))
            return f"## Scena {self.n}\n\n{corpo}"
        if "passaggio che li sostituisce" in sistema or "Sei il romanziere di questo libro" in sistema:
            voluto = re.search(r"[Cc]irca (\d+) parole", messages[-1]["content"])
            n = int(voluto.group(1)) if voluto else 300
            return " ".join(f"Bassam apre la porta e Nicolò decide {i}." for i in range(n // 7))
        return super().chat(messages, **kw)

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = set(schema.get("properties", {}))
        if "dramatic_question" in props:
            if self.piano:
                return self.piano
            # il piano finto riparte da dove la scheda del capitolo prima lascia il protagonista:
            # un salto di luogo senza ponte è un blocker, e la stesura non partirebbe
            piano = super().chat_json(messages, schema=schema, model=model)
            for scena in piano["scenes"]:
                scena["place"] = self.registro["end_state"]["place"]
            return piano
        if "end_state" in props:
            return self.registro
        if props == {"groups"}:
            return {"groups": self.gruppi}
        if props == {"scenes"}:
            return {"scenes": self.extra}
        if props == {"issues"} or props == {"items"} or props == {"same"}:
            return {"issues": [], "items": [], "same": []}
        return super().chat_json(messages, schema=schema, model=model)


_COSE = ["il timbro francese", "la chiave dell'archivio", "il nome del notaio", "la cassa partita",
         "il fascicolo cifrato", "la lettera di Bassam", "il biglietto del treno", "la foto"]


def _scena(ident: str, **kw) -> dict:
    base = {k: "" for k in sc.SCENE_FIELDS}
    cosa = _COSE[(int(ident) if ident.isdigit() else len(ident)) % len(_COSE)]
    base.update({"id": ident, "kind": "presente", "place": "Dendera, ufficio di polizia",
                 "day": "giovedì", "time": "9:00 a.m.", "arrival": "in auto",
                 "goal": f"recuperare {cosa}", "obstacle": "l'ispettore",
                 "clue": "un timbro francese",
                 "belief_before": "è un controllo", "belief_after": "è una trappola",
                 "characters": "Elena Sartori",
                 "decision": "Elena fotografa il timbro", "consequence": "lo seguono",
                 "next_question": "chi ha messo il timbro?",
                 "changes": [{"variable": "possesso", "before": "niente",
                              "after": f"ha {cosa} numero {ident}"}],
                 "reasoning": {"observed": "l'inchiostro è fresco", "interpretation": "è recente",
                               "alternative": "un falso vecchio", "why": "una foto basta"},
                 "claim_uses": [], "claim_ids": [], "words": 1000})
    base.update(kw)
    return base


def _piano(*scene: dict, start: str = "", bridge: dict | None = None) -> dict:
    return {"goal": "uscire dall'Egitto con la pista francese", "dramatic_question": "chi?",
            "turn": "il timbro", "start": start, "end": "a Lione",
            "bridge": bridge or {}, "threads": [], "already_known": [],
            "motifs": [], "canon": [], "continuity_risks": [], "scenes": list(scene)}


def _pack(capitolo: dict | None = None, **kw) -> cx.ContextPack:
    return cx.ContextPack(chapter=capitolo or {"number": 11, "title": "The Excavation",
                                               "does": "x", "words": 2000}, **kw)


# --------------------------------------------------------------------------- #
#  1. le misure vedono la tesi ripetuta
# --------------------------------------------------------------------------- #
def test_the_measures_flag_the_availability_loop_of_chapter_11() -> None:
    testo = FIXTURE.read_text(encoding="utf-8")
    m = mx.measure(testo, protagonist="Nicolò Sforza")
    parole = {b.term for b in m.bursts}
    assert {"line", "availability"} & parole or "available" in parole, parole
    assert m.similar, "paragrafi che dicono la stessa cosa"
    assert m.protagonist_mentions == 0
    assert m.flagged_paragraphs()
    # un testo vario non dà allarmi
    vario = "\n\n".join(f"Paragrafo {i} su un argomento diverso: {w} e {v}." for i, (w, v) in
                        enumerate(zip(["mare", "bosco", "città", "treno"],
                                      ["pane", "luce", "ferro", "vento"])))
    assert not mx.measure(vario).similar


def test_a_motif_that_is_the_word_of_the_book_is_not_an_anomaly() -> None:
    testo = FIXTURE.read_text(encoding="utf-8")
    base = mx.book_baseline([testo, testo])  # il «libro» usa quelle parole così
    assert not mx.term_bursts(testo, base), "non si vietano parole: si confronta col libro"


# --------------------------------------------------------------------------- #
#  2. il piano: scene che cambiano qualcosa, nessun salto senza ponte
# --------------------------------------------------------------------------- #
def test_the_gate_rejects_a_scene_without_decision_and_a_jump_without_bridge() -> None:
    precedente = lg.ChapterEntry(number=10, title="The Osiris Mysteries",
                                 end_state=lg.EndState(place="Dendera, ufficio di polizia"))
    pack = _pack(previous=precedente)
    piano = sc.plan_from_dict(_piano(_scena("1", place="Lyon, archivio municipale",
                                            decision=""), _scena("2")))
    difetti = sc.gate(piano, pack, 1500)
    assert any("non ha: decisione" in d for d in difetti)
    assert any("Dendera" in d and "Lyon" in d for d in difetti)
    # una riga di riassunto in 'start' non basta più: serve la catena del protagonista
    riassunto = sc.plan_from_dict(_piano(_scena("1", place="Lyon, archivio municipale"),
                                         _scena("2"), start="Due giorni dopo, in treno da "
                                         "Marsiglia: il timbro lo porta a Lione"))
    assert any("indizio → decisione" in d for d in sc.gate(riassunto, pack, 1500))
    # con il ponte detto — indizio, decisione, spostamento, nuovo obiettivo — il salto è legittimo
    ponte = sc.plan_from_dict(_piano(
        _scena("1", place="Lyon, archivio municipale"), _scena("2"),
        bridge={"from": "Dendera", "clue": "il timbro francese sul registro",
                "decision": "decide di seguire il timbro fino a Lione",
                "movement": "treno da Marsiglia, due giorni", "new_goal": "trovare il notaio"}))
    assert not sc.gate(ponte, pack, 1500)


def test_the_gate_wants_the_protagonist_living_the_present_not_an_abstraction() -> None:
    """La prima prova del cap. 11 aveva sei scene senza Nicolò: «the network files the pattern»."""
    pack = _pack(protagonist="Nicolò Sforza")
    astratto = sc.plan_from_dict(_piano(*[_scena(
        str(i), characters="Thérèse, la rete", goal="the network must file the pattern",
        decision="the pattern is filed", consequence="the pattern is filed in France")
        for i in range(1, 5)]))
    difetti = sc.gate(astratto, pack, 1500)
    assert any("non è in nessuna scena del presente" in d for d in difetti)
    assert any("in nessuna scena del presente decide" in d for d in difetti)
    # assente da tutto il presente, o senza nessuna decisione sua: la stesura non parte
    assert len([d for d in difetti if sc.severity_of(d) == "blocker"]) == 2
    assert any("ruotano attorno alle stesse parole" in d and "pattern" in d for d in difetti)
    vivo = sc.plan_from_dict(_piano(
        _scena("1", characters="Nicolò, Bassam", decision="Nicolò fotografa il timbro"),
        _scena("2", characters="Nicolò", goal="entrare nell'archivio di Lione",
               decision="Nicolò mente all'archivista", consequence="ottiene il fascicolo"),
        _scena("3", kind="episodio_storico", characters="un templare", goal="nascondere la testa",
               decision="la mura nel muro", consequence="la commenda brucia")))
    assert not sc.gate(vivo, pack, 1500)


def test_a_long_scene_is_written_in_parts_within_the_model_output() -> None:
    piano = sc.plan_from_dict(_piano(_scena("1", words=3200), _scena("2", words=900)))
    unita = sc.units(piano, 1500)
    assert [u.key for u in unita] == ["1/1", "1/2", "1/3", "2"]
    assert max(u.words for u in unita) <= 1500
    # le parti si dividono il lavoro, non le parole
    s1 = piano.scenes[0]
    assert "senza risolverlo" in unita[0].task(s1) and "decisione" in unita[2].task(s1)


# --------------------------------------------------------------------------- #
#  3. la stesura per scene
# --------------------------------------------------------------------------- #
def _forge_romanzo(tmp_path: Path, modello: ModelloEditoriale) -> Forge:
    f = Forge(tmp_path / "book", AppConfig(), client=modello)
    f.start()
    _pronto_a_scrivere(f)
    for c in f.state.outline:
        c["words"] = 2000
    return f


def test_a_novel_is_planned_and_written_scene_by_scene(tmp_path: Path) -> None:
    modello = ModelloEditoriale(piano=_piano(_scena("1"), _scena("2")), chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    risultato = f.write_next()
    testo = risultato.path.read_text(encoding="utf-8")
    assert testo.startswith("# Capitolo 1:")
    assert "## Scena" in testo
    assert sc.plan_path(f.project, 1, "Il passaggio 1").exists(), "il piano va su disco"
    assert ed.report_path(f.project, 1, "Il passaggio 1").exists(), "e il resoconto"
    # la scena riceve il suo piano, non una tesi da argomentare
    sistema, utente = modello.scene_prompt[0]
    assert "SCENA, NON SAGGIO" in sistema and "# La scena da scrivere: 1" in utente
    assert "fotografa il timbro" in utente
    # dopo il capitolo, la sua scheda nel registro
    assert lg.load(f.project).entry(1).end_state.place == "Dendera, ufficio di polizia"


def test_a_short_scene_is_never_asked_to_develop_more_of_the_same(tmp_path: Path) -> None:
    modello = ModelloEditoriale(piano=_piano(_scena("1"), _scena("2")), scena_parole=200,
                                chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    f.write_next()
    assert modello.seguiti, "una scena corta riceve un seguito…"
    for seguito in modello.seguiti:
        assert "sviluppala ancora" not in seguito
        assert wr.DONE in seguito, "…che può rispondere che non manca niente"
        assert "fotografa il timbro" in seguito, "e chiede gli elementi del piano, non parole"
    sorgente = Path(wr.__file__).read_text(encoding="utf-8")
    assert "sviluppala ancora" not in sorgente


def test_the_second_chapter_starts_from_where_the_first_left_the_protagonist(
        tmp_path: Path) -> None:
    modello = ModelloEditoriale(piano=_piano(_scena("1"), _scena("2")), chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    f.write_next()
    modello.scene_prompt.clear()
    f.write_next()
    _sistema, utente = modello.scene_prompt[0]
    assert "Dove ci ha lasciato il capitolo 1" in utente
    assert "Dendera, ufficio di polizia" in utente
    assert "Le ultime righe del capitolo precedente, alla lettera" in utente
    assert "il fumo di resina era una tecnologia rituale" in utente, "già detto: non rispiegare"


def test_each_scene_receives_only_its_own_research(tmp_path: Path) -> None:
    a = cl.Claim(id="Kaaaaaaaaaa", text="Nove cavalieri nel 1119", chapter=1, point=0,
                 verified=True, support="debole")
    b = cl.Claim(id="Kbbbbbbbbbb", text="Il processo del 1312", chapter=1, point=1,
                 verified=True, support="debole")
    pack = _pack(claims=[a, b])
    piano = sc.plan_from_dict(_piano(_scena("1", claim_ids=[a.id]), _scena("2",
                                                                         claim_ids=[b.id])))
    unita = sc.units(piano, 1500)
    prima = wr._scene_user(pack, piano, piano.scenes[0], unita[0], [])
    assert a.id in prima.split("# Il materiale di ricerca per QUESTA scena")[1]
    assert "Il processo del 1312" not in prima.split("# La scena da scrivere")[1]


def test_a_short_chapter_gets_scenes_from_the_story_not_padding(tmp_path: Path) -> None:
    modello = ModelloEditoriale(piano=_piano(_scena("1", words=500), _scena("2", words=500)),
                                scena_parole=300, chapters=2)
    modello.extra = [_scena("x", after="1", goal="convincere Bassam", words=600)]
    f = _forge_romanzo(tmp_path, modello)
    risultato = f.write_next()
    piano = sc.load(f.project, 1, "Il passaggio 1")
    assert [s.id for s in piano.scenes] == ["1", "1+1", "2"], "la scena nuova sta dopo la 1"
    assert any("convincere Bassam" in u for _s, u in modello.scene_prompt)
    assert risultato.path.exists()


def test_a_trial_never_touches_the_chapters_nor_the_ledger(tmp_path: Path) -> None:
    modello = ModelloEditoriale(piano=_piano(_scena("1"), _scena("2")), chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    f.write_next()
    f.write_next()
    prima = {p.name: p.read_bytes() for p in f.project.chapters_dir.glob("*.md")}
    scheda = lg.load(f.project).entry(2)
    risultato = f.write_trial(2)
    assert risultato.path.parent == f.project.trials_dir
    assert {p.name: p.read_bytes() for p in f.project.chapters_dir.glob("*.md")} == prima
    assert lg.load(f.project).entry(2) == scheda
    assert sc.plan_path(f.project, 2, "Il passaggio 2", trial=True).exists()


# --------------------------------------------------------------------------- #
#  4. l'editor: per paragrafi, senza svuotare il capitolo
# --------------------------------------------------------------------------- #
def test_the_editor_cuts_the_loop_by_paragraph_and_reinvests_the_space(tmp_path: Path) -> None:
    testo = FIXTURE.read_text(encoding="utf-8")
    capitolo = ed.parse(testo)
    n = len(capitolo.sections[0].paragraphs)
    modello = ModelloEditoriale()
    modello.gruppi = [{"paragraphs": list(range(1, n + 1)), "function": "la pubblicità rende "
                       "il segreto vulnerabile", "keep": 1, "action": "elimina", "why": "eco"}]
    from bookwriter.forge.project import BookProject

    progetto = BookProject(tmp_path / "book")
    progetto.scaffold()
    finale, resoconto = ed.edit_chapter(progetto, modello, testo, None,
                                        _pack({"number": 11, "title": "The Excavation",
                                               "words": 1400}))
    assert "availability" in finale.lower(), "il primo paragrafo sufficiente resta"
    assert "Bassam apre la porta" in finale, "lo spazio tolto diventa azione"
    assert resoconto.words_final >= ed.LENGTH_GUARD * resoconto.words_draft
    assert resoconto.redundancy and "tenuto §1" in resoconto.redundancy[0]
    assert ed.report_path(progetto, 11, "The Excavation").exists()


def test_a_revision_that_halves_a_scene_is_refused() -> None:
    capitolo = ed.parse("# C\n\n## Scena\n\n" + "\n\n".join(
        " ".join(["parola"] * 100) for _ in range(5)))

    class Taglia:
        def chat(self, messages, **kw):
            return "## Scena\n\nCorta."

    ok, come = ed._revise_section(Taglia(), capitolo, 0, ["ridondanza"], None, None,
                                  model=None, language="italiano", style="")
    assert not ok and "fuori dalla forbice" in come
    assert capitolo.sections[0].words == 500


# --------------------------------------------------------------------------- #
#  5. le fonti: solo pagine lette, e una volta sola
# --------------------------------------------------------------------------- #
def test_a_source_the_model_never_read_is_not_a_source() -> None:
    from bookwriter.forge.websearch import Page

    letta = Page(url="https://en.wikipedia.org/wiki/Knights_Templar", title="Knights Templar",
                 text="x")
    dati = {"findings": [
        {"claim": "Fondazione 1119", "detail": "", "level": "C", "source": "Knights Templar",
         "url": "https://en.wikipedia.org/wiki/Knights_Templar"},
        {"claim": "Sede ad Al-Aqsa nel 1120", "detail": "", "level": "C",
         "source": "history.com", "url": "https://www.history.com/topics/knights-templar-al-aqsa-1120"},
    ]}
    vere, inventata = rs._findings(dati, [letta])
    assert not vere.unverified and vere.url
    assert inventata.unverified and inventata.url == "", "l'indirizzo inventato sparisce"


def test_a_resolution_with_an_invented_url_does_not_close_the_question() -> None:
    letta = store.PageRead(url="https://a.org/p", title="Pagina A", text="testo")
    domanda = store.Question(id="q1", text="Data della sede", chapter=11, point=0)

    class Risolve:
        def chat_json(self, messages, *, schema, **kw):
            return {"resolutions": [{"question_id": "q1", "resolved": True, "claim": "1120",
                                     "detail": "", "level": "C", "source": "history.com",
                                     "url": "https://www.history.com/inventata"}]}

    from bookwriter.forge.project import ForgeState

    assert rs.resolve_with_material(ForgeState(), Risolve(), [domanda], [letta]) == {}


def test_the_bibliography_lists_only_read_sources_once() -> None:
    letta = cl.Source(id="S1", url="https://a.org/p", title="Pagina A", read=True,
                      accessed="2026-10-19")
    mai = cl.Source(id="S2", url="https://b.org/x", title="Mai letta", read=False)
    archivio = cl.ClaimStore(
        claims=[cl.Claim(id="K1", text="a", source_ids=["S1"]),
                cl.Claim(id="K2", text="b", source_ids=["S1", "S2"])],
        sources={"S1": letta, "S2": mai})
    voci = cl.bibliography(archivio, ["K1", "K2"], "en")
    assert len(voci) == 1 and "Pagina A" in voci[0] and "accessed 2026-10-19" in voci[0]


def test_two_copies_of_a_page_are_one_line_of_information() -> None:
    def parole(prefisso: str) -> str:  # parole tutte diverse, solo lettere
        return " ".join(f"{prefisso}{chr(97 + i % 26)}{chr(97 + i // 26 % 26)}" for i in range(400))

    testo = parole("w")
    gruppi, copie = cl.page_groups({"https://a.org/1": testo, "https://b.net/2": testo + " fine",
                                    "https://c.com/3": parole("z")})
    assert gruppi["https://a.org/1"] == gruppi["https://b.net/2"]
    assert gruppi["https://c.com/3"] != gruppi["https://a.org/1"]
    assert copie
    indice = cl.PageIndex()
    indice.add("https://a.org/1", testo)
    assert indice.duplicate_of("https://mirror.org/1", testo + " coda")
    assert not indice.duplicate_of("https://altro.org/1", parole("z"))


def test_mentioning_an_object_and_its_authenticity_stay_two_claims(tmp_path: Path) -> None:
    f = Forge(tmp_path / "book", AppConfig(), client=ModelloEditoriale())
    f.start()
    _pronto_a_scrivere(f)
    capitolo = f.state.outline[0]
    dati = store.ChapterData(number=1, title=capitolo["title"], points=[store.PointData(
        index=0, text="p", pages=[store.PageRead(url="https://a.org/x", title="A", text="t")],
        findings=[store.Finding(claim="Un inventario del 1307 menziona la testa barbuta",
                                source="A", url="https://a.org/x"),
                  store.Finding(claim="La testa barbuta dell'inventario del 1307 è autentica",
                                source="A", url="https://a.org/x")])])
    store.save_chapter(f.project.research_dir, dati)
    archivio = cl.build(f.project, f.state, ModelloEditoriale())  # il modello: «non sono uguali»
    assert len(archivio.claims) == 2


def test_an_author_decision_is_not_asked_again_in_another_chapter() -> None:
    decisa = store.Question(id="c3p0q1", text="La data del processo di Parigi è il 1310, "
                            "non il 1312", chapter=3, status=store.DECISA,
                            outcome={"kind": "publish", "text": "1310"})
    assert rs.same_question("Il processo di Parigi è del 1310 e non del 1312", [decisa]) is decisa
    assert rs.same_question("Le reliquie di Dendera", [decisa]) is None


def test_an_objection_that_a_thesis_is_not_attested_is_not_a_contrast() -> None:
    dati = store.ChapterData(number=11, title="t")
    punto = store.PointData(index=0, text="p")
    dati.points.append(punto)
    nuove = rs._add_questions(dati, punto, [
        "Lo scavo templare non è attestato da evidenze archeologiche",
        "Il libro data il Concilio di Vienne al 1310, le fonti al 1312"])
    assert [q.text[:20] for q in nuove] == ["Il libro data il Con"]
    assert nuove[0].category == "cronologia"


def test_objections_are_not_material_for_the_scene() -> None:
    archivio = cl.ClaimStore(claims=[
        cl.Claim(id="K1", text="La tradizione dello scavo non va presentata come fatto",
                 claim_type="contrasto", chapter=11, found_in=[11], support="debole"),
        cl.Claim(id="K2", text="Nove cavalieri presero alloggio ad Al-Aqsa", chapter=11,
                 found_in=[11], support="debole", claim_type="tradizione")])
    scelte = cl.select_for_chapter(archivio, {"number": 11, "does": "cavalieri Al-Aqsa scavo",
                                              "points": []}).chosen
    assert [c.id for c in scelte] == ["K2"]


# --------------------------------------------------------------------------- #
#  6. lo stile e il ragionamento
# --------------------------------------------------------------------------- #
def test_restyling_a_long_text_goes_by_sections_and_never_shortens() -> None:
    from bookwriter.style.engine import apply_style
    from bookwriter.style.models import StyleProfile

    class Tagliatore:
        def __init__(self) -> None:
            self.chiamate = 0

        def chat(self, messages, **kw):
            self.chiamate += 1
            return "Breve."

    testo = "\n\n".join(f"## Sezione {i}\n\n" + " ".join(["parola"] * 900) for i in range(3))
    modello = Tagliatore()
    fuori = apply_style(testo, StyleProfile(name="x"), modello, critic=False)
    assert modello.chiamate == 6, "tre sezioni, un tentativo in più ciascuna"
    assert fuori == testo, "un pezzo mutilato resta com'era"


def test_writing_has_its_own_reasoning_level() -> None:
    cfg = LLMConfig(reasoning_effort="none", reasoning_effort_writing="medium")
    assert cfg.reasoning_for() == "none"
    assert cfg.reasoning_for(writing=True) == "medium"
    assert LLMConfig(reasoning_effort="low").reasoning_for(writing=True) == "low"


def test_the_ledger_lives_in_readable_files(tmp_path: Path) -> None:
    from bookwriter.forge.project import BookProject, ForgeState

    progetto = BookProject(tmp_path / "book")
    voce = lg.record(progetto, ForgeState(), ModelloEditoriale(), 10, "The Osiris Mysteries",
                     "# Capitolo 10\n\nTesto.")
    assert voce.end_state.place.startswith("Dendera")
    dati = json.loads(lg.ledger_path(progetto).read_text(encoding="utf-8"))
    assert "10" in dati["chapters"]
    assert "Prossima azione: incontrare Bassam" in (progetto.story_dir / "ledger.md").read_text(
        encoding="utf-8")
    assert re.search(r"Ufficio 7 \(fiction\)", (progetto.story_dir / "ledger.md").read_text(
        encoding="utf-8"))


def test_a_chapter_left_with_a_critical_item_open_is_marked_for_review(tmp_path: Path) -> None:
    """Il resoconto dice «da rivedere»: l'autore deve vederlo senza aprire la cartella. Contano
    solo i capitoli scritti con lo stesso nome; le prove e i resoconti rimasti no."""
    from bookwriter.forge.project import BookProject

    progetto = BookProject(tmp_path / "book")
    progetto.chapters_dir.mkdir(parents=True)
    progetto.editorial_dir.mkdir(parents=True)
    for nome in ("01_inizio", "02_lo-scavo", "03_la-corte"):
        (progetto.chapters_dir / f"{nome}.md").write_text("# T\n\nTesto.", encoding="utf-8")
    (progetto.editorial_dir / "01_inizio.md").write_text(
        ed.EditorialReport(chapter=1, title="Inizio").markdown(), encoding="utf-8")
    (progetto.editorial_dir / "02_lo-scavo.md").write_text(
        ed.EditorialReport(chapter=2, title="Lo scavo",
                           failed_critical=["protagonista attivo", "scene e non saggio"]
                           ).markdown(), encoding="utf-8")
    (progetto.editorial_dir / "prova_03_la-corte.md").write_text(
        ed.EditorialReport(chapter=3, trial=True, failed_critical=["x"]).markdown(),
        encoding="utf-8")
    (progetto.editorial_dir / "03_vecchio-titolo.md").write_text(
        ed.EditorialReport(chapter=3, failed_critical=["y"]).markdown(), encoding="utf-8")

    assert ed.review_flags(progetto) == {2: ["protagonista attivo", "scene e non saggio"]}


def test_the_bibliography_says_who_wrote_a_page_and_when() -> None:
    """Autore e data si leggono da quello che la pagina dichiara (meta, JSON-LD, <time>), e
    arrivano fino alla pagina registrata; niente si deduce dal testo."""
    from bs4 import BeautifulSoup

    from bookwriter.forge import pagetext
    from bookwriter.forge.websearch import Page

    meta = BeautifulSoup('<meta name="citation_author" content="Rossi, Anna">'
                         '<meta name="citation_author" content="Bianchi, Luca">'
                         '<meta property="article:published_time" content="2019-03-07T10:00:00Z">',
                         "html.parser")
    assert pagetext.page_meta(meta) == ("Rossi, Anna, Bianchi, Luca", "2019-03-07")
    ld = BeautifulSoup('<script type="application/ld+json">{"@graph": [{"@type": "Article", '
                       '"author": {"@type": "Person", "name": "By Carla Verdi"}, '
                       '"datePublished": "2021-11"}]}</script>', "html.parser")
    assert pagetext.page_meta(ld) == ("Carla Verdi", "2021-11")
    tempo = BeautifulSoup('<article><time datetime="2008">2008</time></article>', "html.parser")
    assert pagetext.page_meta(tempo) == ("", "2008")
    assert pagetext.page_meta(BeautifulSoup("<p>Scritto nel 1999 da Mario.</p>",
                                            "html.parser")) == ("", "")

    html = ('<html><head><meta name="author" content="Anna Rossi">'
            '<script type="application/ld+json">{"datePublished": "2020-05-02"}</script></head>'
            "<body><main><p>" + "Una frase che si legge davvero e parla dello scavo. " * 20
            + "</p></main></body></html>")
    pulita = pagetext.clean_html(html, "https://example.org/a")
    assert (pulita.author, pulita.published) == ("Anna Rossi", "2020-05-02")
    letta = rs._read([Page(url="https://example.org/a", title="Lo scavo", text=pulita.text,
                           author=pulita.author, published=pulita.published)])[0]
    assert (letta.author, letta.published) == ("Anna Rossi", "2020-05-02")
    ritorno = store.PageRead(**{**letta.__dict__})
    assert ritorno.published == "2020-05-02"


def test_a_shared_common_word_does_not_make_two_distant_places_the_same() -> None:
    """Prova v5 del cap. 11: un ufficio di conservazione a Lione e uno a Dendera sembravano lo
    stesso posto, e il salto Egitto → Francia è passato senza ponte."""
    precedente = lg.ChapterEntry(number=10, title="The Osiris Mysteries", end_state=lg.EndState(
        place="Toward the western side of the Dendera temple complex, near the small chamber "
              "behind the conservation office. The text does not place Nicolò in a room."))
    pack = _pack(previous=precedente, protagonist="Nicolò Sforza")
    piano = sc.plan_from_dict(_piano(
        _scena("1", place="Regional conservation office in Lyon", characters="Nicolò"),
        _scena("2", place="Regional conservation office in Lyon", characters="Nicolò")))
    difetti = sc.gate(piano, pack, 1500)
    salto = [d for d in difetti if "indizio → decisione" in d]
    assert salto and sc.severity_of(salto[0]) == "blocker"
    # lo stesso posto detto con altre parole resta lo stesso posto
    qui = sc.plan_from_dict(_piano(
        _scena("1", place="Dendera, the conservation office", characters="Nicolò"),
        _scena("2", characters="Nicolò")))
    assert not [d for d in sc.gate(qui, pack, 1500) if "indizio → decisione" in d]


def test_a_protagonist_missing_from_some_scenes_only_weakens_the_plan() -> None:
    pack = _pack(protagonist="Nicolò Sforza")
    piano = sc.plan_from_dict(_piano(
        _scena("1", characters="Nicolò", decision="Nicolò fotografa il timbro"),
        _scena("2", characters="Salma", decision="Salma firma il trasferimento")))
    assenza = [d for d in sc.gate(piano, pack, 1500) if "non è in scena in: 2" in d]
    assert assenza and sc.severity_of(assenza[0]) == "major"


def test_a_repair_keeps_the_fields_the_model_left_out_and_a_lost_answer_is_retried() -> None:
    """Prova v5: la seconda riparazione è andata persa per un solo campo omesso ('danger'), e le
    riparazioni si fermavano lì."""
    piano = sc.plan_from_dict(_piano(_scena("1", danger="la polizia", decision=""),
                                     _scena("2")))

    class Riparatore:
        def __init__(self) -> None:
            self.chiamate = 0

        def chat_json(self, messages, *, schema, **kw):
            self.chiamate += 1
            assert schema["properties"]["scenes"]["items"]["required"] == ["id", "after"]
            assert schema["required"] == ["scenes"]
            if self.chiamate == 1:
                raise RuntimeError("model output failed the JSON Schema")
            return {"scenes": [{"id": "1", "after": "", "decision": "Nicolò ruba il registro"}]}

    finto = Riparatore()
    assert sc._repair(finto, [], piano, ["la scena 1 non ha: decisione"], None) is None
    nuovo = sc._repair(finto, [], piano, ["la scena 1 non ha: decisione"], None)
    s1 = nuovo.scene("1")
    assert s1.decision == "Nicolò ruba il registro"
    assert s1.danger == "la polizia" and s1.goal and s1.reasoning["observed"]


def test_the_repair_finds_the_scenes_named_in_english_by_the_continuity_check() -> None:
    piano = sc.plan_from_dict(_piano(_scena("1"), _scena("2"), _scena("3"), _scena("11.4")))
    difetti = ["[blocker] continuità del piano, scena Scene 1 → Scene 3 (same day)",
               "la scena 2 non ha: decisione", "Scenes 11.4 and 1 overlap. Then scene 2."]
    assert sc._scene_ids_in(difetti, piano) == ["1", "3", "2", "11.4"]


class Verificatore:
    """Risponde alla verifica indipendente con i giudizi dati, in ordine."""

    def __init__(self, giudizi: list[dict]) -> None:
        self.giudizi = giudizi
        self.chiamate: list[str] = []

    def chat_json(self, messages, *, schema, **kw):
        assert "verdicts" in schema["properties"]
        self.chiamate.append(messages[-1]["content"])
        return {"verdicts": self.giudizi}


def test_a_blocker_from_the_model_stays_only_if_an_independent_check_proves_it() -> None:
    """Prova v6 del cap. 11: ogni riparazione toglieva il suo blocker e il controllo ne trovava un
    altro (una metafora presa per un luogo, un giudizio di drammaturgia): il cancello non si
    apriva più."""
    pack = _pack(protagonist="Elena Sartori")
    piano = sc.plan_from_dict(_piano(
        _scena("1", time="16:50", goal="attivare lo strato francese"),
        _scena("2", time="18:15", goal="sciogliere lo strato francese")))
    difetti = [
        "[blocker] continuità del piano, scena 1 → 2: lo strato si attiva e si scioglie in 85 minuti",
        "[blocker] registro «setting_vector»: «setting side» è in 2 luoghi nello stesso momento",
        "[blocker] continuità del piano, scena 2: il giorno è sbagliato",
        "[blocker] registro «nodi»: 9 (Francia) + 5 (Mediterraneo) = 14 ≠ 16",
        "il capitolo precedente lascia il protagonista a «Dendera»: serve la catena indizio",
    ]
    finto = Verificatore([
        {"n": 1, "blocking": False, "quote_a": "", "quote_b": "", "why": "è drammaturgia"},
        {"n": 2, "blocking": False, "quote_a": "", "quote_b": "", "why": "è una metafora"},
        {"n": 3, "blocking": True, "quote_a": "attivare lo strato francese",
         "quote_b": "sciogliere lo strato francese", "why": "si contraddicono"}])
    esito = sc.verify_blockers(finto, piano, pack, difetti)
    assert [sc.severity_of(d) for d in esito] == ["major", "major", "blocker", "blocker",
                                                  "blocker"]
    assert "è drammaturgia" in esito[0] and "non confermato" in esito[0]
    assert sc.CONFIRMED in esito[2]
    assert esito[3:] == difetti[3:], "i conti e il controllo del codice non si verificano"
    assert "1. [blocker]" in finto.chiamate[0] and "4. " not in finto.chiamate[0]
    # confermato, non si chiede di nuovo
    assert sc.verify_blockers(finto, piano, pack, esito) == esito and len(finto.chiamate) == 1


def test_a_blocker_confirmed_with_quotes_not_in_the_plan_is_not_proved() -> None:
    pack = _pack()
    piano = sc.plan_from_dict(_piano(_scena("1"), _scena("2")))
    difetti = ["[blocker] continuità del piano, scena 2: la porta è chiusa e aperta"]
    finto = Verificatore([{"n": 1, "blocking": True, "quote_a": "la porta resta chiusa a chiave",
                           "quote_b": "Elena apre la porta", "why": "si contraddicono"}])
    esito = sc.verify_blockers(finto, piano, pack, difetti)
    assert sc.severity_of(esito[0]) == "major" and "citazioni non sono nel piano" in esito[0]
    # se la verifica non risponde, il blocker resta: il cancello non si apre da solo
    class Muto:
        def chat_json(self, *a, **kw):
            raise RuntimeError("server giù")
    assert sc.verify_blockers(Muto(), piano, pack, difetti) == difetti
