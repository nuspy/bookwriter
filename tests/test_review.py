"""La fase «Revisione»: i risultati della ricerca discussi con l'autore, un punto alla volta.

Niente rete e niente modelli veri: il motore di ricerca e il modello sono sostituiti.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bookwriter.config import AppConfig
from bookwriter.forge import review as rv
from bookwriter.forge import research as rs
from bookwriter.forge import research_store as store
from bookwriter.forge import writing as wr
from bookwriter.forge.engine import Forge
from bookwriter.forge.project import PHASE_IDS
from bookwriter.forge.websearch import Page, SearchResult
from tests.test_forge import _pronto_a_scrivere
from tests.test_research_depth import FintoRicerca


class FintoRevisione(FintoRicerca):
    """Il modello finto della revisione: genera punti citando le prove che gli si danno."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.risolve_se = "mai-presente-nel-materiale"  # le questioni restano aperte
        self.sistemi: list[str] = []
        self.richieste: list[dict] = []  # cosa risponde il passaggio di «intento»
        self.proposta: dict = {"outcome": "nessuno", "text": "", "note": "", "source": "",
                               "url": "", "structure_note": ""}
        self.chiamate_intento = 0
        self.chiamate_punti = 0
        self.punti_rotti = False
        self.fatto_nuovo = ""
        self.cita_inventato = True

    def chat(self, messages, **kw):
        sistema = messages[0]["content"]
        if "interlocutore editoriale" in sistema:
            self.sistemi.append(sistema)
            risposta = "Capisco. Le fonti dicono una cosa, tu ne vuoi un'altra: come la scrivi?"
            if kw.get("on_delta"):
                kw["on_delta"]("content", risposta)
            return risposta
        return super().chat(messages, **kw)

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = set(schema.get("properties", {}))
        if props == {"points"}:
            self.chiamate_punti += 1
            if self.punti_rotti:
                raise RuntimeError("il server non risponde")
            utente = messages[-1]["content"]
            ids = re.findall(r"^\[([^\]]+)\]", utente, re.M)
            reali = [i for i in ids if i.startswith("c")] or ids
            punti = [{"kind": "tensione", "area": "storica",
                      "title": f"Tensione su {reali[0]}", "statement": "Il libro dice una cosa.",
                      "evidence_ids": [reali[0]], "stakes": "Conta.",
                      "options": ["Tengo com'è", "Tralascio"], "severity": 3}]
            if self.cita_inventato:
                punti.append({"kind": "debolezza", "area": "fonti", "title": "Prova inventata",
                              "statement": "x", "evidence_ids": ["c99p9f9"], "stakes": "",
                              "options": [], "severity": 1})
            return {"points": punti}
        if props == {"requests"}:
            self.chiamate_intento += 1
            return {"requests": self.richieste}
        if "outcome" in props:
            return dict(self.proposta)
        risposta = super().chat_json(messages, schema=schema, model=model, **kw)
        if "findings" in props and self.fatto_nuovo:
            risposta = {**risposta, "findings": [{**f, "claim": self.fatto_nuovo}
                                                 for f in risposta["findings"]]}
        return risposta


@pytest.fixture()
def web(monkeypatch):
    chiamate: list[str] = []

    def gather(query, settings, *, read=None, page=0, skip=None):
        chiamate.append(query)
        slug = "-".join(query.lower().split())[:40]
        return ([SearchResult(f"Titolo {slug}", f"https://esempio.org/{slug}", "estratto")],
                [Page(f"https://esempio.org/{slug}", f"Pagina {slug}", f"Testo di {query}")])

    monkeypatch.setattr(rs, "gather", gather)
    monkeypatch.setattr(rv, "gather", gather)
    return chiamate


def _forge(tmp_path: Path, *, web_on: bool = True, client=None) -> Forge:
    cfg = AppConfig()
    cfg.research.enabled = web_on
    cfg.research.min_relevance = 0.0  # le pagine finte non hanno le parole del libro
    f = Forge(tmp_path / "book", cfg, client=client or FintoRevisione())
    f.start()
    _pronto_a_scrivere(f)
    return f


def _ricercato(tmp_path: Path, **kw) -> Forge:
    """Un libro con panorama e fonti dei capitoli già fatti."""
    f = _forge(tmp_path, **kw)
    f.research_landscape()
    f.research_chapters()
    return f


def _punto(f: Forge, kind: str) -> rv.ReviewPoint:
    return next(p for p in f.review_data().points if p.kind == kind)


# --------------------------------------------------------------------------- #
#  le fasi
# --------------------------------------------------------------------------- #
def test_review_is_the_eighth_phase_between_research_and_writing() -> None:
    assert PHASE_IDS.index("review") == PHASE_IDS.index("research") + 1
    assert PHASE_IDS.index("writing") == PHASE_IDS.index("review") + 1
    assert len(PHASE_IDS) == 8


def test_the_review_is_never_a_requirement(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.state.advance("review")
    assert f.requirements("review") == []
    passato, _ = f.advance()
    assert passato and f.state.phase == "writing" and f.state.review_done


def test_skipping_the_review_changes_nothing_in_the_writing(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    capitolo = f.state.outline[0]
    assert f._review_for(capitolo) == ""  # nessuna revisione: nessun blocco
    f.review_generate()
    f.review_skip_all()
    assert f._review_for(capitolo) == "", "saltare non aggiunge niente al prompt di stesura"


# --------------------------------------------------------------------------- #
#  i punti
# --------------------------------------------------------------------------- #
def test_points_are_anchored_in_real_evidence_and_invented_ones_are_dropped(
    tmp_path: Path, web,
) -> None:
    f = _ricercato(tmp_path)
    dati = f.review_generate()
    titoli = [p.title for p in dati.points]
    assert not any("inventata" in t for t in titoli), "la prova c99p9f9 non esiste: punto scartato"
    p = next(p for p in dati.points if p.kind == rv.TENSIONE)
    assert p.evidence and p.evidence[0].id.startswith("c")
    assert p.evidence[0].source == "Rossi (2020)", "la prova è letterale dai dati, non riscritta"
    assert any("scartate" in w for w in dati.warnings), "e si dice che sono state scartate"


def test_every_kind_is_built_and_ordered(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    dati = f.review_generate()
    tipi = [p.kind for p in dati.points]
    assert rv.MATERIALE in tipi and rv.TENSIONE in tipi and rv.NON_DOCUMENTATO in tipi
    ordine = [rv.KIND_ORDER[k] for k in tipi]
    assert ordine == sorted(ordine)
    assert tipi[0] == rv.MATERIALE, "primo il materiale da integrare"
    assert tipi[-1] == rv.NON_DOCUMENTATO, "ultimi i contrasti con fatti comprovati"
    assert rv.KIND_ORDER[rv.MATERIALE] < rv.KIND_ORDER[rv.IDEA] < rv.KIND_ORDER[rv.TENSIONE]


def test_a_pending_question_becomes_a_point_that_points_back_to_it(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    q = f.pending_questions()
    assert q
    p = _punto(f.review_generate() and f, rv.NON_DOCUMENTATO)
    assert p.ref == q[0].id and "cifra esatta" in p.title


def test_landscape_works_become_material_to_draw_from_and_no_more_field_points(
    tmp_path: Path, web,
) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    opere = [p for p in f.review_data().points
             if p.kind == rv.MATERIALE and p.evidence[0].ref == "landscape"]
    assert opere and "Un libro che c'è" in opere[0].title
    assert "La scena del molo" in opere[0].statement
    assert not any(p.kind == "scelta_campo" for p in f.review_data().points)
    assert not any("di solito tratta" in p.title for p in f.review_data().points)
    # scelta di serie: entra. «Lo lascio fuori» toglie l'opera dal materiale per la stesura.
    assert "Un libro che c'è" in f._research_for(f.state.outline[0])
    f.review_confirm(opere[0].id, "tralascia")
    assert "Un libro che c'è" not in f._research_for(f.state.outline[0])


def test_regenerating_keeps_the_conversation_and_the_decision(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_talk(p.id, "La lascio com'è.")
    f.review_confirm(p.id, "imposta", "Il testo come lo voglio io.")
    f.review_generate()
    ancora = f.review_data().point(p.id)
    assert ancora is not None and ancora.status == rv.IMPOSTO
    assert ancora.decision and ancora.decision.text == "Il testo come lo voglio io."
    assert len(ancora.thread) >= 2


def test_generation_stops_after_three_failed_passes_and_keeps_the_deterministic_points(
    tmp_path: Path, web,
) -> None:
    modello = FintoRevisione(chapters=6)
    f = _ricercato(tmp_path, client=modello)
    modello.punti_rotti = True
    with pytest.raises(rv.ReviewFailed) as errore:
        f.review_generate()
    assert "si è fermata" in str(errore.value)
    assert modello.chiamate_punti == rv.CONSECUTIVE_FAILURES
    assert f.review_data().points, "quel che il codice sa da solo (questioni, campo) è salvato"


# --------------------------------------------------------------------------- #
#  la discussione e la decisione
# --------------------------------------------------------------------------- #
def test_the_prompt_makes_the_model_an_interlocutor_not_a_corrector(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_talk(p.id, "Voglio tenerla così.")
    sistema = f.client.sistemi[-1]
    assert "non sei il suo correttore" in sistema
    assert "Il consenso non è la verità" in sistema
    assert "Mai inventare fonti" in sistema
    assert "accettala: non insistere" in sistema
    assert "UNA volta" in sistema
    assert "NETTA in voce d'autore" in sistema
    assert p.evidence[0].quote in sistema, "le prove arrivano letterali"
    for vietato in ("mainstream", "consenso scientifico", "correggi la tesi"):
        assert vietato not in sistema.lower()


def test_a_reply_is_saved_in_the_thread_and_the_point_is_in_discussion(
    tmp_path: Path, web,
) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    pezzi: list[str] = []
    risposta, log, note = f.review_talk(
        p.id, "Non sono d'accordo.", on_delta=lambda t, x: pezzi.append(x) if t == "content" else 0)
    assert "".join(pezzi) == risposta and not log and not note
    ripreso = Forge(f.project.root, AppConfig()).review_data().point(p.id)
    assert [b["role"] for b in ripreso.thread] == ["assistant", "user", "assistant"]
    assert ripreso.thread[0]["text"].startswith("**"), "l'apertura è dei dati, non del modello"
    assert ripreso.status == rv.IN_DISCUSSIONE


def test_a_proposal_registers_nothing_only_a_confirmation_does(tmp_path: Path, web) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_talk(p.id, "Tienila come l'ho scritta.")
    modello.proposta = {"outcome": "imposta", "text": "Il testo dell'autore.", "note": "Le fonti "
                        "dicono altro.", "source": "", "url": "", "structure_note": ""}
    proposta = f.review_propose(p.id)
    assert proposta["kind"] == "imposta" and proposta["text"] == "Il testo dell'autore."
    ancora = f.review_data().point(p.id)
    assert ancora.decision is None and ancora.status == rv.IN_DISCUSSIONE
    f.review_confirm(p.id, "imposta", proposta["text"], note=proposta["note"], keep_note=False)
    fatto = f.review_data().point(p.id)
    assert fatto.status == rv.IMPOSTO and fatto.decision.kind == "imposta" and not fatto.proposal


def test_a_proposal_without_an_explicit_outcome_is_nothing(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_talk(p.id, "Mmh, non so.")
    assert f.review_propose(p.id) == {}


def test_notes_go_to_the_notes_file_only_when_the_author_chooses_and_never_to_the_writing(
    tmp_path: Path, web,
) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    a, b = [p for p in f.review_data().points if p.kind == rv.TENSIONE][0], None
    f.review_confirm(a.id, "imposta", "Voce dell'autore.", note="Le fonti dicono il contrario.",
                     keep_note=True)
    note = (f.project.research_dir / rv.NOTES_FILE).read_text(encoding="utf-8")
    assert "Le fonti dicono il contrario." in note and "Voce dell'autore." in note
    # la stesura non vede mai le note
    capitolo = f.state.outline[0]
    assert "Le fonti dicono il contrario." not in f._review_for(capitolo)
    assert "Le fonti dicono il contrario." not in f._research_for(capitolo)

    altri = [p for p in f.review_data().points if p.kind == rv.MATERIALE]
    f.review_confirm(altri[0].id, "tralascia", keep_note=False)
    assert altri[0].title not in (f.project.research_dir / rv.NOTES_FILE).read_text(
        encoding="utf-8"), "senza la scelta dell'autore, niente va negli appunti"
    assert b is None


def test_a_decision_reaches_the_writing_prompt_with_precedence(tmp_path: Path, web) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_confirm(p.id, "imposta", "Hiram morì al terzo colpo, e basta.")
    capitolo = next(c for c in f.state.outline if int(c["number"]) == p.chapter)
    blocco = f._review_for(capitolo)
    assert "Hiram morì al terzo colpo, e basta." in blocco
    assert "in voce d'autore, anche se in contrasto con le fonti" in blocco
    # e arriva davvero al prompt del modello che scrive
    f.write_next()
    scritto = "\n".join(modello.scritti)
    assert "Decisioni dell'autore dopo la revisione (hanno la precedenza sulla ricerca)" in scritto
    assert "Hiram morì al terzo colpo, e basta." in scritto
    assert "una cosa sola non si inventa mai" in wr._NO_INVENTION.lower()


def test_a_book_wide_decision_reaches_every_chapter(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    dati = f.review_data()
    libro = rv.ReviewPoint(id="r900", kind=rv.INCOERENZA, chapter=0, title="Cronologia",
                           anchor="x")
    dati.points.append(libro)
    rv.save(f.project, dati)
    f.review_confirm("r900", "concordata", "L'anno è il 1860, ovunque.")
    for capitolo in f.state.outline:
        assert "L'anno è il 1860, ovunque." in f._review_for(capitolo)
        assert "Valide per tutto il libro" in f._review_for(capitolo)


def test_a_concordata_decision_is_added_to_the_notebook_agreed(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.MATERIALE)
    f.review_confirm(p.id, "concordata", "Il libro non ne parla, è voluto.")
    assert any("è voluto" in v for v in f.state.notebook["agreed"])


def test_a_structure_note_is_only_flagged_never_applied(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.MATERIALE)
    prima = list(f.state.outline)
    f.review_confirm(p.id, "concordata", "Lo aggiungo.", structure_note="Tocca il capitolo 2.")
    assert f.state.outline == prima and f.state.research_done
    assert any("Tocca il capitolo 2." in c for c in f.review_perplexities())


def test_confirming_needs_a_formulation_unless_the_point_is_left_out(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    with pytest.raises(ValueError):
        f.review_confirm(p.id, "concordata", "  ")
    with pytest.raises(ValueError):
        f.review_confirm(p.id, "boh", "x")
    f.review_confirm(p.id, "tralascia")
    assert "non menzionare" in f._review_for(next(
        c for c in f.state.outline if int(c["number"]) == p.chapter))


def test_a_source_decision_without_a_source_is_not_a_source(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    assert f.review_confirm(p.id, "fonte", "Una cosa.").decision.kind == "concordata"


# --------------------------------------------------------------------------- #
#  le questioni: una sola fonte di verità
# --------------------------------------------------------------------------- #
def test_deciding_a_question_point_writes_where_questions_always_wrote(
    tmp_path: Path, web,
) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.NON_DOCUMENTATO)
    f.review_confirm(p.id, "concordata", "Secondo la tradizione, furono molte.")
    q = next(q for q in f.questions() if q.id == p.ref)
    assert q.status == store.DECISA and q.outcome["kind"] == store.OUT_PUBLISH
    assert "Secondo la tradizione, furono molte." in f._decisions_for(f.state.outline[q.chapter - 1])
    assert q.id not in [x.id for x in f.pending_questions()]
    # e non si duplica nel blocco della revisione
    assert "Secondo la tradizione" not in f._review_for(f.state.outline[q.chapter - 1])


def test_leaving_a_question_out_and_giving_a_source(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.NON_DOCUMENTATO)
    f.review_confirm(p.id, "fonte", "Le vittime furono 12.", source="Archivio di Stato (1978)",
                     url="https://archivio.example/12")
    q = next(q for q in f.questions() if q.id == p.ref)
    assert q.status == store.RISOLTA and q.outcome["source"] == "Archivio di Stato (1978)"


def test_leaving_the_review_never_drops_anything_undecided(tmp_path: Path, web) -> None:
    """Uscire dalla revisione non decide niente: il contenuto dell'autore resta com'è."""
    f = _ricercato(tmp_path)
    f.review_generate()
    f.state.advance("review")
    prima = [(q.id, q.status, dict(q.outcome)) for q in f.questions()]
    assert f.pending_questions()
    f.advance()
    assert f.state.phase == "writing"
    assert f.pending_questions(), "i contrasti non decisi restano aperti"
    assert prima == [(q.id, q.status, dict(q.outcome)) for q in f.questions()]
    testo = f._decisions_for(f.state.outline[0])
    assert "resta come l'ha scritto l'autore" in testo and "non menzionar" not in testo
    assert not hasattr(f, "omit_pending_questions"), "il percorso automatico non esiste più"


def test_a_statement_without_sources_stays_in_the_writing(tmp_path: Path, web) -> None:
    """Un'affermazione che nessuna fonte tocca resta: nessun percorso la toglie o la attenua."""
    modello = FintoRevisione()
    modello.aperte = []  # nessun contrasto, nessuna fonte: niente da segnalare
    f = _ricercato(tmp_path, client=modello)
    capitolo = f.state.outline[0]
    assert f._decisions_for(capitolo) == ""
    f.write_next()
    scritto = "\n".join(modello.scritti)
    assert "tralasc" not in scritto.lower()
    assert "Che una cosa non abbia fonti non è un difetto" in wr._NO_INVENTION
    assert "senza cautele" in wr._NO_INVENTION and "anche se la ricerca non li" in wr._NO_INVENTION


def test_a_position_with_contrary_sources_stays_until_the_author_decides(
    tmp_path: Path, web,
) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    f.review_generate()
    q = f.pending_questions()[0]
    cap = next(c for c in f.state.outline if int(c["number"]) == q.chapter)
    assert q.text in f._decisions_for(cap)
    assert "nessuna decisione ancora" in f._decisions_for(cap)
    f.review_skip_all()
    f.state.advance("review")
    f.advance()
    assert q.id in [x.id for x in f.pending_questions()]
    assert "resta come l'ha scritto l'autore" in f._decisions_for(cap)
    # «tralascia» solo per decisione esplicita
    f.decide_question(q.id, store.OUT_OMIT)
    assert "non menzionarlo" in f._decisions_for(cap)


# --------------------------------------------------------------------------- #
#  saltare
# --------------------------------------------------------------------------- #
def test_skipping_a_point_has_no_effect_on_the_book(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_skip_point(p.id)
    assert f.review_data().point(p.id).status == rv.RIMANDATO
    assert f._review_for(f.state.outline[0]) == ""
    assert any("non sono stati decisi" in c for c in f.review_perplexities())


def test_skipping_never_closes_a_decided_point(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_confirm(p.id, "tralascia")
    f.review_skip_point(p.id)
    assert f.review_data().point(p.id).status == rv.TRALASCIATO


# --------------------------------------------------------------------------- #
#  le ricerche chieste dall'autore
# --------------------------------------------------------------------------- #
def test_a_search_asked_in_the_chat_uses_the_web_and_leaves_a_trace(tmp_path: Path, web) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    modello.richieste = [{"kind": "cerca", "queries": ["acacia dmt riferimenti"], "claim": ""}]
    modello.fatto_nuovo = "Un fatto emerso dalla ricerca chiesta in chat"
    prima = len(web)
    risposta, log, note = f.review_talk(p.id, "Cerca riferimenti su quanto dico.")
    assert len(web) == prima + 1 and web[-1] == "acacia dmt riferimenti"
    assert log and "Cerco «acacia dmt riferimenti»" in log[0].summary()
    assert "Ricerca appena fatta su richiesta dell'autore" in modello.sistemi[-1]
    assert "Testo di acacia dmt riferimenti" in modello.sistemi[-1]
    dati = f.review_data().point(p.id)
    assert any(b["role"] == "notice" and "Cerco" in b["text"] for b in dati.thread)
    # le pagine e i fatti entrano nel punto di capitolo, con la loro origine
    cap = f.chapter_data(p.chapter)
    assert any(pg.origin == "autore" for pt in cap.points for pg in pt.pages)
    assert any(fi.origin == "revisione" for pt in cap.points for fi in pt.findings)


def test_verify_searches_both_for_and_against(tmp_path: Path, web) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    modello.richieste = [{"kind": "verifica", "claim": "Hiram morì al terzo colpo",
                          "queries": ["Hiram terzo colpo fonti", "Hiram terzo colpo contraddice"]}]
    _, log, _ = f.review_talk(p.id, "Verifica che Hiram morì al terzo colpo.")
    assert [s.query for s in log] == ["Hiram terzo colpo fonti", "Hiram terzo colpo contraddice"]


def test_a_message_without_a_search_request_costs_no_intent_call(tmp_path: Path, web) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_talk(p.id, "Resta com'è, grazie.")
    assert modello.chiamate_intento == 0
    f.review_talk(p.id, "Verifica per favore.")
    assert modello.chiamate_intento == 1


def test_with_the_web_off_the_assistant_says_so_and_does_not_pretend(tmp_path: Path, web) -> None:
    modello = FintoRevisione()
    f = _forge(tmp_path, web_on=False, client=modello)
    f.research_landscape()
    f.research_chapters()
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    modello.richieste = [{"kind": "cerca", "queries": ["qualcosa"], "claim": ""}]
    prima = len(web)
    _, log, note = f.review_talk(p.id, "Cerca qualcosa su questo.")
    assert len(web) == prima and not log
    assert note and "spenta" in note[0]
    assert "La ricerca sul web è spenta" in modello.sistemi[-1]


def test_a_search_never_filters_sources_by_authority() -> None:
    """Nessun filtro sulle fonti: si etichettano, non si scartano (e vale anche per la ricerca)."""
    import inspect

    sorgente = inspect.getsource(rs._sources_from_model)
    assert "secondarie autorevoli ammesse" not in sorgente
    assert "non si scartano, si etichettano" in sorgente
    assert "NON verificare il libro" in sorgente


# --------------------------------------------------------------------------- #
#  chat libera e perplessità
# --------------------------------------------------------------------------- #
def test_the_free_chat_knows_decisions_and_perplexities(tmp_path: Path, web) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    f.review_generate()
    p = _punto(f, rv.TENSIONE)
    f.review_confirm(p.id, "tralascia")
    risposta, _, _ = f.review_free_talk("Ho un dubbio sul finale.")
    assert risposta
    sistema = modello.sistemi[-1]
    assert "Decisioni prese" in sistema and p.title in sistema
    assert "Le mie perplessità" in sistema and "Ancora da decidere" in sistema


def test_a_decision_taken_in_the_free_chat_becomes_a_decided_free_point(
    tmp_path: Path, web,
) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    f.review_generate()
    f.review_free_talk("Il finale lo voglio più duro.")
    modello.proposta = {"outcome": "concordata", "text": "Il finale è più duro.", "note": "",
                        "source": "", "url": "", "structure_note": ""}
    proposta = f.review_free_propose()
    assert proposta["kind"] == "concordata"
    assert not any(p.kind == rv.LIBERO for p in f.review_data().points), "ancora niente"
    punto = f.review_free_confirm("Il finale", "concordata", "Il finale è più duro.")
    assert punto.kind == rv.LIBERO and punto.status == rv.CONCORDATO
    assert "Il finale è più duro." in f._review_for(f.state.outline[0])


def test_perplexities_are_facts_about_the_data(tmp_path: Path, web) -> None:
    f = _ricercato(tmp_path)
    assert f.review_perplexities() == [] or all(isinstance(c, str) for c in f.review_perplexities())
    # un capitolo senza ricerca è una perplessità vera
    cap = f.state.outline[-1]
    store.chapter_json_path(f.project.research_dir, int(cap["number"]), cap["title"]).unlink()
    assert any(f"capitolo {cap['number']} non ha una ricerca" in c.lower()
               for c in f.review_perplexities())


# --------------------------------------------------------------------------- #
#  i prompt inclinati al consenso
# --------------------------------------------------------------------------- #
def test_prompts_no_longer_lean_on_consensus() -> None:
    import inspect

    from bookwriter.forge import questions as qs

    visti: list[str] = []

    class Spia:
        def chat_json(self, messages, *, schema, model=None):
            visti.append(messages[0]["content"])
            return {"works": [], "sources": [], "ideas": [], "recent": ""}

    from bookwriter.forge.project import ForgeState

    try:
        rs._landscape_from_model(ForgeState(), Spia(), rs.ResearchReport(), model=None)
    except Exception:  # noqa: BLE001 - qui conta solo il prompt
        pass
    assert visti and "OPERE SIMILI" in visti[0]
    assert "programma implicito" not in visti[0] and "competente" not in visti[0]
    assert "Non giudicare la tesi dell'autore" in visti[0]
    assert "ragione deliberata" not in inspect.getsource(store.render_landscape_md)
    assert "NETTA" in qs._ROLE and "dubitativa" not in qs._ROLE


# --------------------------------------------------------------------------- #
#  riga di comando
# --------------------------------------------------------------------------- #
def test_the_cli_lists_shows_confirms_and_skips(tmp_path: Path, web, monkeypatch) -> None:
    from typer.testing import CliRunner

    from bookwriter import cli

    f = _ricercato(tmp_path)
    f.review_generate()
    monkeypatch.setattr(cli, "_forge", lambda **k: f)
    runner = CliRunner()
    elenco = runner.invoke(cli.app, ["forge", "review", "list"])
    assert elenco.exit_code == 0 and "tensione" in elenco.output and "ancora da decidere" in elenco.output
    p = _punto(f, rv.TENSIONE)
    mostra = runner.invoke(cli.app, ["forge", "review", "show", p.id])
    assert mostra.exit_code == 0 and p.title in mostra.output
    conferma = runner.invoke(cli.app, ["forge", "review", "confirm", p.id, "--kind", "imposta",
                                       "--text", "Così.", "--keep-note", "--note", "Prove contrarie."])
    assert conferma.exit_code == 0
    assert (f.project.research_dir / rv.NOTES_FILE).exists()
    salta = runner.invoke(cli.app, ["forge", "review", "skip", "--all"])
    assert salta.exit_code == 0
    errore = runner.invoke(cli.app, ["forge", "review", "show", "r999"])
    assert errore.exit_code == 2


# --------------------------------------------------------------------------- #
#  la direzione: la ricerca lavora PER il libro
# --------------------------------------------------------------------------- #
class ModelloContrasti(FintoRicerca):
    """Il modello segnala, fra i contrasti, anche il nome del personaggio di fantasia."""

    def chat_json(self, messages, *, schema, model=None, **kw):
        risposta = super().chat_json(messages, schema=schema, model=model, **kw)
        if "findings" in set(schema.get("properties", {})):
            risposta = {**risposta, "open_questions": [
                "Il nome Elena Sartori non compare nel materiale fornito",
                "Il titolo del capitolo Il passaggio 1 non compare nel materiale",
                "Il libro dice che la battaglia fu nel 1750, ma le fonti dicono 1850",
            ]}
        return risposta


def test_a_made_up_character_or_a_chapter_title_never_becomes_a_question(
    tmp_path: Path, web,
) -> None:
    f = _forge(tmp_path, client=ModelloContrasti())
    capitolo = f.state.outline[0]
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, capitolo)
    testi = [q.text for q in f.chapter_data(1).questions]
    assert not any("Elena Sartori" in t for t in testi), "il personaggio di fantasia"
    assert not any("Il passaggio 1" in t for t in testi), "il titolo del capitolo"
    assert any("1750" in t for t in testi), "il contrasto con un fatto comprovato resta"
    assert all(q.kind == "contrasto" for q in f.chapter_data(1).questions)


def test_invented_terms_come_from_the_notebook_and_the_outline(tmp_path: Path) -> None:
    f = _forge(tmp_path)
    termini = rs.invented_terms(f.state)
    assert "elena sartori" in termini and "sartori" in termini
    assert "il passaggio 1" in termini


def test_a_thesis_gets_searches_that_support_it_not_checks(tmp_path: Path, web) -> None:
    """Le interrogazioni cercano esperienze, descrizioni, fonti a sostegno: niente «studi dati»."""
    f = _forge(tmp_path)
    capitolo = f.state.outline[0]
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, capitolo)
    for query in web:
        assert "studi ricerca dati" not in query and "verifica" not in query.lower()
    assert any("esperienze" in q and "descrizioni" in q for q in web)
    assert any("fonti a sostegno" in q for q in web)


def test_the_queries_for_a_point_can_come_from_the_model(tmp_path: Path, web) -> None:
    class ConQuery(FintoRicerca):
        def chat_json(self, messages, *, schema, model=None, **kw):
            if set(schema.get("properties", {})) == {"search_queries"}:
                assert "NON devi verificare" in messages[0]["content"]
                return {"search_queries": ["ayahuasca ceremony fractal tunnel account",
                                           "DMT visions geometric patterns testimonies"]}
            return super().chat_json(messages, schema=schema, model=model, **kw)

    f = _forge(tmp_path, client=ConQuery())
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, f.state.outline[0])
    assert "ayahuasca ceremony fractal tunnel account" in web


def test_extraction_asks_for_usable_material_with_how_it_can_serve(tmp_path: Path) -> None:
    visti: list[str] = []

    class Spia(FintoRicerca):
        def chat_json(self, messages, *, schema, model=None, **kw):
            if "findings" in set(schema.get("properties", {})):
                visti.append(messages[0]["content"])
            return super().chat_json(messages, schema=schema, model=model, **kw)

    f = _forge(tmp_path, client=Spia())
    pr = rs.ResearchReport(pages=[Page("https://e.org/a", "A", "Testo")])
    rs._sources_from_model(f.state, f.client, pr, f.state.outline[0], [], model=None)
    sistema = visti[0]
    assert "'use' (come può servire" in sistema and "NON verificare il libro" in sistema
    assert "non scrivere mai che qualcosa «non è documentato»" in sistema
    assert "non si scartano, si etichettano" in sistema
    assert "grounding" not in sistema.lower() and "livello di prova (A meta" not in sistema


def test_saved_material_renders_how_it_can_serve_and_old_data_still_reads(tmp_path: Path) -> None:
    dati = store.ChapterData(number=1, title="T", points=[store.PointData(
        index=0, text="P", findings=[
            store.Finding(claim="Un dettaglio", detail="x", use="per la scena al molo", level="D",
                          source="Un blog", url="https://b.org"),
            store.Finding(claim="Vecchio", detail="y", level="B", source="IPCC")])])
    testo = store.render_chapter_md(dati)
    assert "Come può servire:** per la scena al molo" in testo and "Tipo di fonte:** D" in testo
    assert "IPCC" in testo and "Livello di prova" not in testo
    # un file salvato prima (senza `use`, `excluded`, `kind`) si legge ancora
    vecchio = {"number": 1, "title": "T", "points": [{"index": 0, "text": "P", "findings": [
        {"claim": "c", "detail": "d", "level": "B", "source": "s", "url": ""}]}],
        "questions": [{"id": "c1p0q1", "text": "q", "chapter": 1, "point": 0}]}
    letto = store.chapter_from_dict(vecchio)
    assert letto.points[0].findings[0].use == "" and not letto.points[0].findings[0].excluded
    assert letto.questions[0].kind == ""


def test_an_old_landscape_is_still_read_and_rendered_without_prescribing(tmp_path: Path) -> None:
    vecchio = store.LandscapeData(date="2026-10-17", queries=["q"], analysis={
        "competitors": [{"title": "Un libro", "author": "B", "year": "2020",
                         "does": "Copre le basi", "misses": "Nessun esempio", "url": ""}],
        "implicit_syllabus": ["le basi"], "gaps": ["gli esempi"], "recent": "",
        "novelty": "Il tuo taglio", "outline_consequences": ["aggiungere casi"]})
    md = store.render_landscape_md("", vecchio)
    assert "Un libro" in md and "Copre le basi" in md
    assert "deve" not in md.lower() or "non dice cosa il libro debba" in md
    assert "Da cui attingere" in md
    assert [c["title"] for c in store.landscape_works(vecchio)] == ["Un libro"]
    assert "Un libro" in store.landscape_for_writing(vecchio)
    assert "Nessun esempio" not in store.landscape_for_writing(vecchio), "niente critiche"


def test_the_landscape_reaches_the_writing_as_material(tmp_path: Path, web) -> None:
    modello = FintoRevisione()
    f = _ricercato(tmp_path, client=modello)
    capitolo = f.state.outline[0]
    assert "Opere simili da cui attingere" in f._research_for(capitolo)
    piani: list[str] = []
    originale = modello.chat_json

    def guarda(messages, *, schema, **kw):
        if "dramatic_question" in schema.get("properties", {}):
            piani.append(messages[-1]["content"])
        return originale(messages, schema=schema, **kw)

    modello.chat_json = guarda
    f.write_next()
    # in un racconto le opere simili servono al piano delle scene; il materiale del capitolo
    # arriva alla scena a cui è assegnato
    assert any("Un libro che c'è" in testo for testo in piani)
    assert any("Il materiale di ricerca per QUESTA scena" in testo for testo in modello.scritti)


def test_the_unapproached_questions_message_does_not_blame_the_web(tmp_path: Path, web) -> None:
    """«Non ho approfondito, il web è spento» solo se il web è davvero spento nelle impostazioni."""
    f = _ricercato(tmp_path)
    for q in f.questions():
        q.attempts = []
        f._save_question(q)
    punti = rv.non_documented_points(f.project, f.state, web_on=True)
    assert all("spenta" not in p.statement for p in punti)
    assert any("non ancora approfondito" in p.statement for p in punti)
    spenti = rv.non_documented_points(f.project, f.state, web_on=False)
    assert any("spenta nelle impostazioni" in p.statement for p in spenti)
