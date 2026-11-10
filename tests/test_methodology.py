"""La specifica metodologica del 25/10/2026, sui casi veri della prova v4 del cap. 11.

L'LLM interpreta e propone (la metodologia: registri e invarianti di QUESTO testo); il canone
vincola; il codice verifica i conti; l'autore decide le ambiguità e le deroghe.
"""

from __future__ import annotations

from pathlib import Path

from bookwriter.forge import concepts as cn
from bookwriter.forge import methodology as md
from bookwriter.forge import scenes as sc
from tests.test_editorial import ModelloEditoriale, _forge_romanzo, _pack, _piano, _scena

NODI = {"id": "nodi", "name": "nodi del pattern", "purpose": "il totale è canone",
        "entity": "un nodo della rete", "unit": "nodo", "categories": ["Francia", "Mediterraneo",
                                                                        "Occidente"],
        "disjoint": True, "exhaustive": True, "total": 14, "initial": "14 nodi",
        "invariants": ["sum_to_total"], "certainty": "canone"}
SCHEDE = {"id": "schede", "name": "schede dell'indice", "purpose": "il conteggio guida la scena",
          "entity": "una scheda d'archivio", "unit": "scheda", "categories": [],
          "disjoint": False, "exhaustive": False, "total": None, "initial": "",
          "invariants": ["conservation", "sum_to_total"], "certainty": "testo"}


def _metodo(*registri: dict) -> md.Methodology:
    return md.validate_methodology({"registers": list(registri)}, md.Methodology())


def _oss(register: str, section: int = 0, **kw) -> md.Observation:
    return md.Observation(register=register, section=section, **kw)


# --------------------------------------------------------------------------- #
#  la metodologia: proposta dall'LLM, validata dal codice
# --------------------------------------------------------------------------- #
def test_the_code_refuses_a_sum_it_cannot_justify() -> None:
    metodo = _metodo(NODI, SCHEDE, {"id": "vuoto", "name": "x", "invariants": ["magia"]})
    assert [r.id for r in metodo.registers] == ["nodi", "schede"]
    schede = metodo.register("schede")
    assert schede.invariants == ["conservation"], "categorie non disgiunte: niente somma"
    assert any("somma al totale non si verifica" in v for v in metodo.validation)
    assert any("«x»: tolto" in v for v in metodo.validation)
    assert "totale canonico 14" in metodo.canon_block()


# --------------------------------------------------------------------------- #
#  il validatore: i conti della prova v4
# --------------------------------------------------------------------------- #
def test_nine_plus_five_plus_two_is_not_fourteen() -> None:
    metodo = _metodo(NODI)
    esiti = md.check(metodo, [
        _oss("nodi", 1, kind="distribution", quote="nine nodes to France, five east, two west",
             parts=[("Francia", 9), ("Mediterraneo", 5), ("Occidente", 2)]),
        _oss("nodi", 4, kind="distribution", quote="France kept six, Aragon three, Cyprus five",
             parts=[("Francia", 6), ("Aragona", 3), ("Cipro", 5)])])
    falliti = [e for e in esiti if e.failed]
    assert len(falliti) == 1 and "= 16, ma il totale è 14" in falliti[0].detail
    assert falliti[0].sections == [1] and "nine nodes" in falliti[0].evidence[0]
    assert md.failures_text(esiti)[0].startswith("[blocker] registro «nodi»")


def test_the_card_index_counts_must_follow_the_events() -> None:
    """11 schede, poi 14 senza che niente sia stato aggiunto, poi 8 messe in cassa e 11 rimaste."""
    metodo = _metodo(SCHEDE)
    esiti = md.check(metodo, [
        _oss("schede", 0, kind="count", quote="the index held eleven cards", initial=11),
        _oss("schede", 3, kind="count", quote="fourteen cards at 2:15", present=14),
        _oss("schede", 5, kind="count", quote="eight were packed at 5:10; eleven remained",
             removed=8, present=11)])
    dettagli = " / ".join(e.detail for e in esiti if e.failed)
    assert "14 presenti senza un evento che lo spieghi" in dettagli
    assert "ma il testo dice 11 presenti" in dettagli
    coerenti = md.check(metodo, [
        _oss("schede", 0, kind="count", initial=14, present=14),
        _oss("schede", 3, kind="count", removed=3, present=11)])
    assert not [e for e in coerenti if e.failed]


def test_an_entity_is_in_one_place_at_a_time_and_unknowns_stay_unknown() -> None:
    metodo = _metodo({**NODI, "id": "pacchetto", "name": "pacchetto di cera",
                      "invariants": ["single_place"], "total": None})
    esiti = md.check(metodo, [
        _oss("pacchetto", 2, kind="location", entity="il pacchetto", place="Dendera",
             time="sera"),
        _oss("pacchetto", 2, kind="location", entity="il pacchetto", place="Parigi",
             time="sera")])
    assert any(e.failed and "2 luoghi" in e.detail for e in esiti)
    ambiguo = md.check(_metodo(NODI), [_oss("nodi", 0, kind="distribution",
                                             parts=[("Francia", None), ("Cipro", 5)])])
    assert [e.outcome for e in ambiguo] == ["ambiguous"], "un valore ignoto non si inventa"


# --------------------------------------------------------------------------- #
#  i concetti in più lingue, gli ID di ricerca, la gravità
# --------------------------------------------------------------------------- #
def test_concepts_are_recognised_in_any_language() -> None:
    assert cn.normalise("passo_motivo", "consequence") == "conseguenza"
    assert cn.normalise("passo_motivo", "Choice") == "scelta"
    assert cn.normalise("variabile", "Knowledge") == "conoscenza"
    assert cn.normalise("gravita", "Blocker") == "blocker"
    assert cn.normalise("passo_motivo", "una parola qualsiasi") == "", "non si indovina"
    piano = sc.plan_from_dict({**_piano(_scena("1"), _scena("2")), "motif_chain": [
        {"scene": "1", "motif": "filing", "step": "consequence", "what": "x"},
        {"scene": "2", "motif": "filing", "step": "conseguenza", "what": "y"}]})
    assert any("ripete il passo conseguenza" in d for d in sc.gate(piano, _pack(), 1500))


def test_a_mangled_research_id_is_resolved_only_by_the_registry() -> None:
    from bookwriter.forge import claims as cl

    pack = _pack(claims=[cl.Claim(id="Kb7689cb55d", text="la sentenza del 1312"),
                         cl.Claim(id="Kca0191a0ee", text="il trasferimento ai Templari")])
    piano = sc.plan_from_dict(_piano(_scena("1", claim_uses=[
        {"claim_id": "Kbc7689cb55d", "function": "enigma", "how": "la sentenza del 1312"}])))
    sc.resolve_claim_ids(piano, pack)
    assert piano.scenes[0].claim_ids == ["Kb7689cb55d"]
    assert piano.resolutions[0]["from"] == "Kbc7689cb55d" and "unico ID" in \
        piano.resolutions[0]["why"]
    ambiguo = _pack(claims=[cl.Claim(id="K1234567890", text="a"),
                            cl.Claim(id="K1234567899", text="b")])
    p2 = sc.plan_from_dict(_piano(_scena("1", claim_uses=[
        {"claim_id": "K123456789X", "function": "enigma", "how": ""}])))
    sc.resolve_claim_ids(p2, ambiguo)
    assert p2.scenes[0].claim_ids == ["K123456789X"] and not p2.resolutions, \
        "due candidati: non si sceglie in silenzio"
    assert any("non risolvibili" in d for d in sc.gate(p2, ambiguo, 1500))


def test_a_repair_is_judged_by_severity_not_by_count() -> None:
    prima = ["[blocker] continuità del piano: 9 + 5 + 2 = 16", "domanda nuova manca"]
    dopo = ["[minor] x", "[minor] y", "[minor] z", "[major] w"]
    assert sc.risk(dopo) < sc.risk(prima), "più difetti, ma nessun blocker: è meglio"
    assert sc.severity_of("il viaggio per la scena 2 dura 7 ore ma fra le ancore") == "blocker"
    assert sc.severity_of("la scena 1 non lascia una domanda nuova") == "minor"


# --------------------------------------------------------------------------- #
#  il cancello prima della stesura
# --------------------------------------------------------------------------- #
class ModelloConti(ModelloEditoriale):
    """Propone un registro dei nodi e, nel piano, una distribuzione che non torna."""

    def __init__(self, conti_giusti: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.conti_giusti = conti_giusti
        self.metodologie = 0

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = schema.get("properties", {})
        if "registers" in props and "criteria" in props:
            self.metodologie += 1
            return {"criteria": [], "registers": [NODI], "motifs": [], "sources": [],
                    "risks": [], "tests": [], "blocking": [], "author_questions": []}
        if "observations" in props:
            parti = [{"category": "Francia", "quantity": 9 if not self.conti_giusti else 7},
                     {"category": "Mediterraneo", "quantity": 5},
                     {"category": "Occidente", "quantity": 2}]
            return {"observations": [{
                "register": "nodi", "kind": "distribution", "quote": "nine to France",
                "time": "", "parts": parti, "total": None, "initial": None, "present": None,
                "removed": None, "added": None, "entity": "", "place": "",
                "certainty": "known"}]}
        if "rules" in props:
            return {"rules": [{"text": "circa 7.000 parole per capitolo", "kind": "vincolo",
                               "source": "quaderno"}]}
        return super().chat_json(messages, schema=schema, model=model, **kw)


def test_a_plan_whose_counts_do_not_add_up_is_not_written(tmp_path: Path) -> None:
    modello = ModelloConti(chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    risultato = f.write_next()
    assert risultato.blocked and any("9 (Francia) + 5 (Mediterraneo) + 2 (Occidente) = 16" in d
                                     for d in risultato.blocked)
    assert not f.project.written_chapters(), "niente è stato scritto"
    assert modello.metodologie == 1, "la metodologia si genera una volta per capitolo"
    assert md.load_latest(f.project, 1, "piano").registers[0].total == 14
    assert md.project_profile_path(f.project).exists()
    # la deroga dell'autore sblocca, e resta registrata
    scritto = f.write_next(waiver="i nodi si contano diversamente nel capitolo 12")
    assert not scritto.blocked and scritto.path.exists()
    assert f.state.waivers and f.state.waivers[0]["chapter"] == 1
    assert "nodi" in f.state.waivers[0]["reason"]
    assert sc.load(f.project, 1, "Il passaggio 1").waiver


def test_with_counts_that_add_up_the_writer_gets_the_registers(tmp_path: Path) -> None:
    modello = ModelloConti(conti_giusti=True, chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    risultato = f.write_next()
    assert not risultato.blocked and risultato.path.exists()
    _sistema, utente = modello.scene_prompt[0]
    assert "I registri del capitolo" in utente and "totale canonico 14" in utente


def test_replanning_throws_the_blocked_plan_away(tmp_path: Path) -> None:
    modello = ModelloConti(chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    assert f.write_next().blocked
    modello.conti_giusti = True
    assert f.write_next().blocked, "il piano bloccato resta finché non si rifà"
    f.replan_chapter(1)
    assert not f.write_next().blocked


class ModelloRiparatore(ModelloConti):
    """Come ModelloConti, e sa riparare una scena del piano; conta i piani fatti da capo."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.piani = 0
        self.riparazioni = 0
        self.sa_riparare = False  # durante il primo piano le riparazioni non bastano

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = schema.get("properties", {})
        if "dramatic_question" in props:
            self.piani += 1
        if "remove" in props:
            self.riparazioni += 1
            self.conti_giusti = self.sa_riparare
            return {"start": "", "bridge": {}, "threads": [], "remove": [],
                    "scenes": [{"id": "1", "after": "",
                                "decision": "Elena manda sette nodi in Francia"}]}
        return super().chat_json(messages, schema=schema, model=model, **kw)


def test_a_blocked_plan_is_repaired_only_where_it_is_wrong(tmp_path: Path) -> None:
    """Ripianificare da capo porta un piano nuovo con difetti nuovi (prova v6 del cap. 11): la
    riparazione tocca solo le scene coi difetti, e la stesura riparte da quel piano."""
    modello = ModelloRiparatore(chapters=2)
    f = _forge_romanzo(tmp_path, modello)
    risultato = f.write_next()
    assert risultato.blocked
    prima = sc.load(f.project, 1, "Il passaggio 1")
    riparazioni = modello.riparazioni
    modello.sa_riparare = True
    piano = f.repair_plan(1)
    assert modello.riparazioni > riparazioni and modello.piani == 1, "nessun piano da capo"
    assert not piano.blockers()
    assert piano.scene("1").decision == "Elena manda sette nodi in Francia"
    assert piano.scene("1").goal == prima.scene("1").goal, "i campi non nominati restano"
    assert [s.goal for s in piano.scenes[1:]] == [s.goal for s in prima.scenes[1:]]
    scritto = f.write_next()
    assert not scritto.blocked and scritto.path.exists()
    assert modello.piani == 1, "la stesura parte dal piano riparato"
    assert not f.state.waivers, "nessuna deroga: il piano è stato riparato"
