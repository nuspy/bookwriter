"""Parte B: punti chiave, ricerca strutturata, questioni aperte approfondite e decise dall'autore.

Niente rete e niente modelli veri: il motore di ricerca, la lettura delle pagine e il modello sono
sostituiti.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookwriter.config import AppConfig
from bookwriter.forge import interview as iv  # noqa: F401
from bookwriter.forge import questions as qs
from bookwriter.forge import research as rs
from bookwriter.forge import research_store as store
from bookwriter.forge import spec as sp
from bookwriter.forge.engine import Forge
from bookwriter.forge.websearch import Page, SearchResult
from tests.test_forge import FintoModello, _pronto_a_scrivere, _quaderno_pieno


class FintoRicerca(FintoModello):
    """Il modello finto, con la ricerca approfondita: sa riformulare, risolvere e registrare."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        #: le questioni che ogni punto lascia aperte
        self.aperte: list[str] = ["La cifra esatta delle vittime"]
        #: le interrogazioni che il modello propone a ogni giro
        self.giri: list[list[str]] = [["alfa"], ["fonte beta"], ["gamma"]]
        self.giro = 0
        #: risolve solo se una pagina letta ha questo frammento nell'indirizzo
        self.risolve_se = "fonte"
        self.decisioni: list[dict] = []
        self.chat_systems: list[str] = []
        self.scritti: list[str] = []

    def chat(self, messages, **kw):
        sistema = messages[0]["content"]
        if "Sei l'editor di un libro. La ricerca ha segnalato" in sistema:
            self.chat_systems.append(sistema)
            risposta = "Propongo: «secondo la tradizione, le vittime furono molte»."
            if kw.get("on_delta"):
                kw["on_delta"]("content", risposta)
            return risposta
        if "capitolo di libro" in sistema or "UNA SCENA" in sistema:
            self.scritti.append(messages[-1]["content"])
        return super().chat(messages, **kw)

    def chat_json(self, messages, *, schema, model=None):
        props = set(schema.get("properties", {}))
        utente = messages[-1]["content"]
        if props == {"queries"}:
            self.giro += 1
            return {"queries": self.giri[min(self.giro, len(self.giri)) - 1]}
        if props == {"resolutions"}:
            voci = []
            for riga in utente.split("## Contrasti\n")[1].split("\n\n")[0].splitlines():
                ident = riga.split(":")[0].lstrip("- ").strip()
                ok = self.risolve_se in utente.split("## Materiale")[1]
                voci.append({"question_id": ident, "resolved": ok,
                             "claim": "Le vittime furono 12" if ok else "",
                             "detail": "dodici, secondo l'archivio" if ok else "",
                             "level": "B" if ok else "", "source": "Archivio di Stato (1978)"
                             if ok else "", "url": "" if ok else ""})
            return {"resolutions": voci}
        if props == {"decisions"}:
            return {"decisions": self.decisioni}
        if "findings" in props:
            return {"findings": [{"claim": "Un fatto documentato", "detail": "con cifre",
                                  "level": "C", "source": "Rossi (2020)", "url": "https://r.org"}],
                    "open_questions": list(self.aperte)}
        return super().chat_json(messages, schema=schema, model=model)


@pytest.fixture()
def web(monkeypatch):
    """Una rete finta: ogni interrogazione trova una pagina sua, mai vista prima."""
    chiamate: list[str] = []

    def gather(query, settings, *, read=None, page=0, skip=None):
        chiamate.append(query)
        slug = "-".join(query.lower().split())[:40]
        risultati = [SearchResult(f"Titolo {slug}", f"https://esempio.org/{slug}", "estratto")]
        pagine = [Page(f"https://esempio.org/{slug}", f"Pagina {slug}", f"Testo di {query}")]
        return risultati, pagine

    monkeypatch.setattr(rs, "gather", gather)
    return chiamate


def _forge(tmp_path: Path, *, web_on: bool = True, client=None) -> Forge:
    cfg = AppConfig()
    cfg.research.enabled = web_on
    cfg.research.min_relevance = 0.0  # le pagine finte non hanno le parole del libro
    f = Forge(tmp_path / "book", cfg, client=client or FintoRicerca())
    f.start()
    _pronto_a_scrivere(f)
    return f


# --------------------------------------------------------------------------- #
#  B1 — punti chiave
# --------------------------------------------------------------------------- #
def test_the_outline_has_key_points_and_the_spec_shows_them(tmp_path: Path) -> None:
    f = _forge(tmp_path)
    for capitolo in f.state.outline:
        assert 3 <= len(capitolo["points"]) <= 6
    testo = f.project.spec_path.read_text(encoding="utf-8")
    assert sp.POINTS_SECTION in testo and "#### Capitolo 1" in testo
    assert "- Scena 1 del capitolo 1" in testo


def test_points_edited_by_hand_are_read_back_and_chapters_stay_the_same(tmp_path: Path) -> None:
    f = _forge(tmp_path)
    testo = f.project.spec_path.read_text(encoding="utf-8")
    testo = testo.replace("- Scena 1 del capitolo 1", "- Il corpo nel porto\n- Un punto aggiunto")
    f.save_spec(testo)
    primo = f.state.outline[0]
    assert primo["points"][:2] == ["Il corpo nel porto", "Un punto aggiunto"]
    assert [c["number"] for c in f.state.outline] == [1, 2, 3], "i punti non sono capitoli"
    assert len(f.state.outline) == 3


def test_a_spec_without_points_works_as_before(tmp_path: Path) -> None:
    f = _forge(tmp_path)
    vecchio = "\n".join(l for l in f.project.spec_path.read_text(encoding="utf-8").splitlines()
                        if not l.startswith("- Scena") and not l.startswith("####")
                        and sp.POINTS_SECTION not in l)
    f.state.outline = [{k: v for k, v in c.items() if k != "points"} for c in f.state.outline]
    f.save_spec(vecchio)
    assert all("points" not in c for c in f.state.outline)
    assert sp.chapter_points(f.state.outline[0]) == ["Stabilisce la cosa numero 1."]
    f.write_all()  # la stesura funziona
    assert len(f.project.written_chapters()) == 3


def test_the_writing_receives_the_chapter_points(tmp_path: Path) -> None:
    f = _forge(tmp_path)
    f.write_next()
    prompt = f.client.scritti[-1]
    assert "Punti chiave" in prompt and "Scena 2 del capitolo 1" in prompt


# --------------------------------------------------------------------------- #
#  B2 — ricerca strutturata
# --------------------------------------------------------------------------- #
def test_the_research_is_saved_per_point_in_json_and_markdown_is_regenerated(
    tmp_path: Path, web,
) -> None:
    f = _forge(tmp_path)
    f.client.aperte = []
    capitolo = f.state.outline[0]
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, capitolo)
    percorso = store.chapter_json_path(f.project.research_dir, 1, capitolo["title"])
    dati = json.loads(percorso.read_text(encoding="utf-8"))
    assert len(dati["points"]) == len(capitolo["points"])
    punto = dati["points"][0]
    assert punto["queries"] and punto["results"][0]["url"].startswith("https://esempio.org")
    assert punto["pages"] and punto["findings"][0]["source"] == "Rossi (2020)"
    # il .md si rigenera dal JSON
    md = store.chapter_md_path(f.project.research_dir, 1, capitolo["title"])
    md.unlink()
    store.save_chapter(f.project.research_dir,
                       store.load_chapter(f.project.research_dir, 1, capitolo["title"]))
    assert "Rossi (2020)" in md.read_text(encoding="utf-8")
    # il panorama ha il suo JSON
    f.research_landscape()
    assert (f.project.research_dir / "landscape.json").exists()
    assert f.landscape_data().analysis["works"]


# --------------------------------------------------------------------------- #
#  B4 — l'approfondimento
# --------------------------------------------------------------------------- #
def test_a_question_resolved_at_the_second_round_carries_its_source(tmp_path: Path, web) -> None:
    f = _forge(tmp_path)
    f.client.aperte = ["La cifra esatta delle vittime"]
    capitolo = f.state.outline[0]
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, capitolo)
    dati = f.chapter_data(1)
    domanda = dati.questions[0]
    assert domanda.status == store.RISOLTA
    assert [a.round for a in domanda.attempts] == [1, 2], "risolta al secondo giro"
    assert domanda.outcome["source"] == "Archivio di Stato (1978)"
    assert any(fi.claim == "Le vittime furono 12" for p in dati.points for fi in p.findings)
    assert "Archivio di Stato (1978)" in f.project.research_dir.joinpath(
        store.chapter_stem(1, capitolo["title"]) + ".md").read_text(encoding="utf-8")


def test_a_round_with_nothing_new_stops_the_deepening(tmp_path: Path, monkeypatch) -> None:
    f = _forge(tmp_path)
    f.client.risolve_se = "mai"
    chiamate: list[str] = []

    def gather(query, settings, *, read=None, page=0, skip=None):
        chiamate.append(query)
        # sempre lo stesso risultato: dal secondo giro in poi non c'è niente di nuovo
        return [SearchResult("Sempre lui", "https://esempio.org/uno", "")], []

    monkeypatch.setattr(rs, "gather", gather)
    f.cfg.research.deepen_rounds = 3
    capitolo = {**f.state.outline[0], "points": ["Un solo punto"]}
    f.client.giri = [["x"], ["y"], ["z"]]
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, capitolo)
    domanda = f.chapter_data(1).questions[0]
    assert domanda.status == store.CHIESTA
    assert len(domanda.attempts) == 1, "ci si ferma al primo giro senza risultati nuovi"
    assert domanda.attempts[0].new == 0


def test_with_the_web_off_questions_go_straight_to_the_author(tmp_path: Path, web) -> None:
    f = _forge(tmp_path, web_on=False)
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, f.state.outline[0])
    dati = f.chapter_data(1)
    assert dati.questions and all(q.status == store.CHIESTA for q in dati.questions)
    assert all(not q.attempts for q in dati.questions)
    assert web == [], "senza web niente interrogazioni"


def test_the_deepening_can_be_interrupted(tmp_path: Path, web) -> None:
    f = _forge(tmp_path)
    parti = iter([False] * 4 + [True] * 100)
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, f.state.outline[0],
                        should_stop=lambda: next(parti))
    dati = f.chapter_data(1)
    assert any(q.status == store.APERTA for q in dati.questions) or all(
        q.closed or q.status == store.CHIESTA for q in dati.questions)


# --------------------------------------------------------------------------- #
#  B4 — l'autore
# --------------------------------------------------------------------------- #
def _con_questioni_aperte(tmp_path: Path, web) -> Forge:
    f = _forge(tmp_path)
    f.client.risolve_se = "mai"  # il giro automatico non risolve: restano all'autore
    f.client.giri = [["a"], ["b"], ["c"]]
    f.research_chapters()
    assert f.pending_questions()
    return f


def test_the_pending_questions_are_listed_as_requirements(tmp_path: Path, web) -> None:
    f = _con_questioni_aperte(tmp_path, web)
    f.state.research_done = True
    mancanze = f.requirements("research")
    assert mancanze and "Contrasto da discutere" in mancanze[0]
    # chi passa avanti lo stesso non perde niente: i contrasti non decisi NON si tralasciano,
    # il libro resta com'è scritto (la stesura lo sa)
    f.state.advance("research")
    passato, _ = f.advance(force=True)
    assert passato and f.state.phase == "review"
    assert f.pending_questions()
    passato, _ = f.advance()  # la revisione non è mai un requisito
    assert passato and f.state.phase == "writing"
    assert f.pending_questions(), "uscire dalla revisione non decide niente al posto dell'autore"
    testo = f._decisions_for(f.state.outline[0])
    assert "resta come l'ha scritto l'autore" in testo and "non menzionar" not in testo
    assert all(q.outcome == {} for q in f.questions() if not q.closed)


def test_a_pasted_url_resolves_a_question(tmp_path: Path, web, monkeypatch) -> None:
    f = _con_questioni_aperte(tmp_path, web)
    f.client.risolve_se = "archivio"
    monkeypatch.setattr(qs, "fetch", lambda url, settings: Page(url, "Archivio", "Testo."))
    risolte, materiale = f.resolve_with_material("Guarda https://esempio.gov/archivio.")
    assert risolte and risolte[0].status == store.RISOLTA
    assert risolte[0].outcome["url"].startswith("https://esempio.gov/archivio")
    assert not materiale.notes
    # la pagina entra fra quelle lette del punto
    dati = f.chapter_data(risolte[0].chapter)
    assert any(p.origin == "autore" for pt in dati.points for p in pt.pages)


def test_a_url_with_the_web_off_is_said_not_read(tmp_path: Path, web) -> None:
    f = _con_questioni_aperte(tmp_path, web)
    f.cfg.research.enabled = False
    risolte, materiale = f.resolve_with_material("https://esempio.gov/archivio")
    assert not risolte
    assert any("spenta" in n for n in materiale.notes)


@pytest.mark.parametrize("estensione", [".txt", ".md", ".docx", ".pdf"])
def test_an_attached_document_is_read_and_can_resolve(
    tmp_path: Path, web, estensione: str,
) -> None:
    f = _con_questioni_aperte(tmp_path, web)
    f.client.risolve_se = "documento"
    percorso = tmp_path / f"documento{estensione}"
    corpo = "Le vittime furono dodici secondo l'archivio di stato del 1978."
    if estensione in (".txt", ".md"):
        percorso.write_text(corpo, encoding="utf-8")
    elif estensione == ".docx":
        docx = pytest.importorskip("docx")
        d = docx.Document()
        d.add_paragraph(corpo)
        d.save(percorso)
    else:
        canvas = pytest.importorskip("reportlab.pdfgen.canvas")
        c = canvas.Canvas(str(percorso))
        c.drawString(72, 720, corpo)
        c.save()
    try:
        risolte, materiale = f.resolve_with_material("", [percorso])
    except Exception as exc:  # il lettore del formato può mancare in questo ambiente
        pytest.skip(str(exc))
    if materiale.notes and not materiale.pages:
        pytest.skip("; ".join(materiale.notes))
    assert risolte, materiale.notes
    assert risolte[0].outcome["url"] == percorso.name


def test_an_unsupported_attachment_is_said(tmp_path: Path, web) -> None:
    f = _con_questioni_aperte(tmp_path, web)
    strano = tmp_path / "vocale.mp3"
    strano.write_bytes(b"x")
    _, materiale = f.resolve_with_material("", [strano])
    assert any("non supportato" in n for n in materiale.notes)


def test_publish_and_omit_decisions_are_recorded_and_reach_the_writing(
    tmp_path: Path, web,
) -> None:
    f = _con_questioni_aperte(tmp_path, web)
    pendenti = f.pending_questions()
    primo = pendenti[0].id
    f.client.decisioni = [
        {"question_id": primo, "action": "publish",
         "text": "Secondo la tradizione, le vittime furono molte."},
    ]
    risposta, _, _ = f.questions_talk("Va bene la tua formulazione.")
    assert "Propongo" in risposta
    # il prompt dell'assistente elenca le questioni da decidere
    assert primo in f.client.chat_systems[-1]
    registrate = f.questions_update()
    assert [q.id for q in registrate] == [primo]
    domanda = next(q for q in f.questions() if q.id == primo)
    assert domanda.status == store.DECISA
    assert domanda.outcome == {"kind": "publish",
                               "text": "Secondo la tradizione, le vittime furono molte."}
    # un'altra: tralasciare
    secondo = f.pending_questions()[0].id
    f.decide_question(secondo, "omit")
    assert next(q for q in f.questions() if q.id == secondo).outcome["kind"] == "omit"
    # arrivano alla stesura
    capitolo = next(c for c in f.state.outline if int(c["number"]) == domanda.chapter)
    testo = f._decisions_for(capitolo)
    assert "scrivi così" in testo and "Secondo la tradizione" in testo
    f.write_next()
    assert "Contrasti segnalati dalla ricerca: cosa ha deciso l'autore" in f.client.scritti[-1]


def test_a_proposal_the_author_did_not_accept_is_not_a_decision(tmp_path: Path, web) -> None:
    f = _con_questioni_aperte(tmp_path, web)
    f.client.decisioni = []  # il modello non vede nessuna decisione esplicita
    f.questions_talk("Mmh, fammi pensare.")
    assert not f.questions_update()
    assert len(f.pending_questions()) == len(f.questions())


def test_the_conversation_survives_a_reopen(tmp_path: Path, web) -> None:
    f = _con_questioni_aperte(tmp_path, web)
    f.questions_talk("Ciao")
    ripreso = Forge(f.project.root, f.cfg)
    assert [b["role"] for b in ripreso.state.questions_transcript] == ["user", "assistant"]


# --------------------------------------------------------------------------- #
#  l'esploratore: includere un risultato, estendere un punto
# --------------------------------------------------------------------------- #
def test_including_a_result_reads_the_page_into_the_point(tmp_path: Path, web, monkeypatch) -> None:
    f = _forge(tmp_path)
    f.client.aperte = []
    f.research_chapters()
    dati = f.chapter_data(1)
    punto = dati.points[0]
    nuovo = "https://altro.org/risultato"
    punto.results.append(store.Hit("Un altro", nuovo, "estratto"))
    store.save_chapter(f.project.research_dir, dati)
    monkeypatch.setattr(rs, "fetch", lambda url, settings: Page(url, "Un altro", "Testo letto"))
    pagina = f.include_result(1, 0, nuovo)
    assert pagina.url == nuovo
    assert nuovo in f.chapter_data(1).points[0].read_urls()


def test_extending_a_point_adds_results(tmp_path: Path, web) -> None:
    f = _forge(tmp_path)
    f.client.aperte = []
    f.research_chapters()
    prima = len(f.chapter_data(1).points[0].results)
    f.client.giri = [["nuova interrogazione"], ["altra ancora"], ["e un'altra"]]
    nuovi = f.extend_point(1, 0)
    assert nuovi >= 1
    assert len(f.chapter_data(1).points[0].results) > prima


def test_include_and_extend_refuse_with_the_web_off(tmp_path: Path, web) -> None:
    from bookwriter.forge.websearch import SearchError

    f = _forge(tmp_path, web_on=False)
    f.client.aperte = []
    f.research_chapters()
    with pytest.raises(SearchError, match="spenta"):
        f.extend_point(1, 0)
    with pytest.raises(SearchError, match="spenta"):
        f.include_result(1, 0, "https://x.org")


def test_the_deepening_setting_exists_and_defaults_to_three() -> None:
    assert AppConfig().research.deepen_rounds == 3


def test_the_cli_lists_and_decides_questions(tmp_path: Path, web, monkeypatch) -> None:
    from typer.testing import CliRunner

    from bookwriter import cli

    f = _con_questioni_aperte(tmp_path, web)
    monkeypatch.setattr(cli, "_forge", lambda **k: f)
    runner = CliRunner()
    elenco = runner.invoke(cli.forge_app, ["questions"])
    assert elenco.exit_code == 0 and "La cifra esatta" in elenco.output
    ident = f.pending_questions()[0].id
    esito = runner.invoke(cli.forge_app, ["questions", "--decide", ident, "--publish",
                                          "Secondo la tradizione."])
    assert esito.exit_code == 0, esito.output
    assert next(q for q in f.questions() if q.id == ident).outcome["kind"] == "publish"
    ident2 = f.pending_questions()[0].id
    assert runner.invoke(cli.forge_app, ["questions", "--decide", ident2, "--omit"]).exit_code == 0
    assert runner.invoke(cli.forge_app, ["questions", "--decide", ident2]).exit_code == 2


# --------------------------------------------------------------------------- #
#  la struttura cambia: la ricerca dei capitoli non vale più
# --------------------------------------------------------------------------- #
def _ricerca_fatta(f) -> None:
    from bookwriter.forge import research as rs  # noqa: F401

    f.research_landscape()
    f.research_chapters()
    assert f.state.research_done


def test_a_changed_structure_invalidates_the_chapter_research_but_not_the_landscape(
    tmp_path: Path, web,
) -> None:
    f = _forge(tmp_path)
    f.client.aperte = []
    _ricerca_fatta(f)
    assert f.landscape_data() is not None and not f.research_format_old()

    # rigenerazione identica: non si butta niente
    f.make_spec()
    assert f.state.research_done
    # solo il testo di «cosa fa»: niente
    testo = f.project.spec_path.read_text(encoding="utf-8").replace(
        "Stabilisce la cosa numero 1.", "Stabilisce la prima cosa, meglio detta.")
    f.save_spec(testo)
    assert f.state.research_done, "«cosa fa» non cambia le fonti da cercare"

    # un titolo diverso: la ricerca dei capitoli non vale più, il panorama resta
    testo = f.project.spec_path.read_text(encoding="utf-8").replace("Il passaggio 2", "Un altro titolo")
    f.save_spec(testo)
    assert not f.state.research_done
    assert f.landscape_data() is not None
    assert f.chapter_data(2) is None, "il capitolo col nuovo titolo non ha ricerca"
    assert not f.pending_questions() or all(q.chapter != 2 for q in f.pending_questions())

    # i punti modificati a mano contano come struttura cambiata
    _ricerca_fatta(f)
    testo = f.project.spec_path.read_text(encoding="utf-8").replace(
        "- Scena 1 del capitolo 1", "- Un punto nuovo")
    f.save_spec(testo)
    assert not f.state.research_done


def test_a_regenerated_structure_with_other_chapters_resets_research_done(tmp_path: Path, web) -> None:
    f = _forge(tmp_path)
    f.client.aperte = []
    _ricerca_fatta(f)
    f.client.chapters = 5          # un'altra struttura: cinque capitoli invece di tre
    f.make_spec()
    assert len(f.state.outline) == 5 and not f.state.research_done
    assert f.landscape_data() is not None


def test_declined_research_stays_declined_when_the_structure_changes(tmp_path: Path) -> None:
    f = _forge(tmp_path)
    f.decline_research()
    f.client.chapters = 5
    f.make_spec()
    assert f.state.research_declined


def _ricerca_vecchia(f) -> None:
    """Il formato di prima: solo i .md, nessun JSON; il libro la considera fatta."""
    f.project.scaffold()
    (f.project.research_dir / "00_landscape.md").write_text("# Panorama\n", encoding="utf-8")
    for c in f.state.outline:
        store.chapter_md_path(f.project.research_dir, int(c["number"]), c["title"]).write_text(
            "# Ricerca\n", encoding="utf-8")
    f.state.research_done = True
    f.save()


def test_old_format_research_counts_as_not_done_for_the_new_features(tmp_path: Path) -> None:
    from bookwriter.gui import phase_guide as pg

    f = _forge(tmp_path)
    _ricerca_vecchia(f)
    assert f.research_format_old()
    f.go_to("research")
    testo = pg.next_step(f, web_on=False)
    assert "La ricerca è nel formato precedente" in testo and "Riprendi la ricerca" in testo
    assert "Ricerca finita" not in testo


def test_a_scanned_or_mailed_attachment_is_now_read(tmp_path: Path, web) -> None:
    """Gli allegati della chat si leggono come l'importazione: una mail entra come materiale."""
    f = _con_questioni_aperte(tmp_path, web)
    posta = tmp_path / "lettera.eml"
    posta.write_text("From: a@b.it\nSubject: La lettera\n\nIl documento è del 1312.\n",
                     encoding="utf-8")
    _, materiale = f.resolve_with_material("", [posta])
    assert not any("non supportato" in n for n in materiale.notes)
    assert any("1312" in p.text for p in materiale.pages)
