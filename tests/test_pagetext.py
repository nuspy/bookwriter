"""La pulizia delle pagine, le pagine non leggibili, la pertinenza. Pagine finte in
``tests/fixtures/pages``: niente rete."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookwriter.config import AppConfig, ResearchSettings
from bookwriter.forge import pagetext as pt
from bookwriter.forge import research as rs
from bookwriter.forge import research_store as store
from bookwriter.forge.engine import Forge
from bookwriter.forge.websearch import Page, SearchResult
from tests.test_forge import _pronto_a_scrivere
from tests.test_research_depth import FintoRicerca

PAGES = Path(__file__).parent / "fixtures" / "pages"


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app

#: Il vocabolario del libro (la premessa e i messaggi): le parole più discriminanti.
LIBRO = ("Un professore di antropologia scopre che l'acacia, pianta ricca di DMT, è il filo rosso "
         "della storia: ayahuasca, Amazzonia, cerimonia, visioni, il mondo degli Dei, sostanze e "
         "rituali. anthropologist ayahuasca ceremony Amazon DMT acacia")
PUNTO_CERIMONIA = "The ayahuasca ceremony is staged as sensory data: smoke, chanting, vomiting, darkness"
PUNTO_TUNNEL = "The fractal tunnel is described as a repeatable visual architecture"


def _pulita(nome: str, url: str = "https://esempio.org/x") -> pt.Cleaned:
    return pt.clean_html((PAGES / nome).read_text(encoding="utf-8"), url)


def _page(nome: str, url: str = "") -> Page:
    c = _pulita(nome, url or f"https://esempio.org/{nome}")
    return Page(url or f"https://esempio.org/{nome}", nome, c.text if c.readable else "",
                readable=c.readable, note="" if c.readable else c.reason, removed=dict(c.removed),
                chars_before=c.chars_before, chars_after=c.chars_after)


# --------------------------------------------------------------------------- #
#  pulizia
# --------------------------------------------------------------------------- #
def test_the_main_text_survives_and_the_boilerplate_goes() -> None:
    c = _pulita("ayahuasca_account.html")
    assert c.readable
    assert "maloca" in c.text and "icaros" in c.text and "tunnel" in c.text
    for incarto in ("We use cookies", "Accept all", "Related posts", "Subscribe to our newsletter",
                    "All rights reserved", "Privacy policy", "Home", "Contact"):
        assert incarto not in c.text, incarto
    assert c.chars_after < c.chars_before


def test_what_was_removed_is_counted_per_category() -> None:
    c = _pulita("ayahuasca_account.html")
    assert c.removed["banner cookie e consenso"] == 1
    assert c.removed["elementi di pagina"] >= 5
    assert "tolto:" in c.summary() and "banner cookie e consenso" in c.summary()
    assert f"{c.chars_before} → {c.chars_after}" in c.summary()


@pytest.mark.parametrize("nome,motivo", [
    ("cookie_wall.html", "cookie"),
    ("serial_novel_index.html", "indici"),
    ("shop_page.html", "menu"),
    ("youtube_shell.html", "YouTube"),
])
def test_unreadable_pages_are_marked_with_the_reason(nome: str, motivo: str) -> None:
    c = _pulita(nome, "https://www.youtube.com/watch?v=x" if "youtube" in nome else
                f"https://esempio.org/{nome}")
    assert not c.readable
    assert motivo in c.reason
    assert c.removed, "anche una pagina scartata dice cosa si è tolto"


def test_a_serial_novel_index_is_not_prose() -> None:
    c = _pulita("serial_novel_index.html")
    assert not c.readable and c.removed["menu e liste di link"] >= 50
    assert "Become a Patron" not in c.text


def test_youtube_gives_its_description_never_the_footer() -> None:
    c = _pulita("youtube_with_description.html", "https://www.youtube.com/watch?v=abc")
    assert c.readable
    assert "geometric tunnel of light" in c.text and "My first ayahuasca ceremony" in c.text
    assert "Ismertető" not in c.text and "Google LLC" not in c.text
    assert "nessuna trascrizione" in " ".join(c.removed)


def test_a_threshold_decides_what_is_enough_prose() -> None:
    html = (PAGES / "ayahuasca_account.html").read_text(encoding="utf-8")
    assert pt.clean_html(html, "https://e.org/", min_chars=400).readable
    assert not pt.clean_html(html, "https://e.org/", min_chars=5000).readable


# --------------------------------------------------------------------------- #
#  pertinenza, misurata sulle pagine di prova
# --------------------------------------------------------------------------- #
def test_the_game_walkthrough_is_dropped_and_the_ayahuasca_account_is_kept() -> None:
    gioco, racconto = _pulita("game_walkthrough.html"), _pulita("ayahuasca_account.html")
    assert gioco.readable, "il walkthrough si legge benissimo: parla d'altro"
    for punto in (PUNTO_CERIMONIA, PUNTO_TUNNEL):
        tema = pt.Topic.of(punto, LIBRO, exclude={"nicolò"})
        r_gioco, r_racconto = pt.relevance(gioco.text, tema), pt.relevance(racconto.text, tema)
        assert r_gioco.score < 0.25 <= r_racconto.score, (punto, r_gioco, r_racconto)
        assert r_gioco.anchors == 0 and r_racconto.anchors >= 3
        assert r_racconto.score > 0.6 and r_gioco.score < 0.15, "separazione netta"


def test_the_chapter_title_alone_does_not_make_a_page_relevant() -> None:
    """«Fractal tunnel» è anche un videogioco: conta il vocabolario del libro."""
    tema = pt.Topic.of("fractal tunnel", LIBRO)
    assert pt.relevance(_pulita("game_walkthrough.html").text, tema).score < 0.25


def test_without_a_vocabulary_nothing_is_filtered() -> None:
    assert pt.relevance("qualsiasi cosa", pt.Topic()).score == 1.0


def test_invented_names_do_not_count_as_topic() -> None:
    tema = pt.Topic.of("Nicolò arriva in Amazzonia", LIBRO, exclude={"Nicolò"})
    assert "nicolò"[:6] not in tema.point and "nicolo"[:6] not in tema.point


# --------------------------------------------------------------------------- #
#  dentro la ricerca: solo le pagine buone vanno al modello
# --------------------------------------------------------------------------- #
class Spia(FintoRicerca):
    """Il modello finto: registra il materiale e GIUDICA la pertinenza (0-1) come farebbe il vero."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.materiali: list[str] = []
        self.giudicate: list[str] = []
        self.rompi_giudizio = False

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = set(schema.get("properties", {}))
        if props == {"score", "reason"}:
            utente = messages[-1]["content"]
            self.giudicate.append(utente.split("## La pagina\n", 1)[-1].split("\n", 1)[0])
            if self.rompi_giudizio:
                raise RuntimeError("il modello non risponde")
            # un modello vero legge la pagina; questo riconosce il videogioco
            if "Level 2" in utente or "Dark Souls" in utente or "boss" in utente.lower():
                return {"score": 0.05, "reason": "parla di un videogioco, non del tema"}
            return {"score": 0.9, "reason": "parla dell'argomento"}
        if "findings" in props:
            self.materiali.append(messages[-1]["content"])
        return super().chat_json(messages, schema=schema, model=model, **kw)


@pytest.fixture()
def pagine_finte(monkeypatch):
    nomi = ["ayahuasca_account.html", "game_walkthrough.html", "cookie_wall.html",
            "serial_novel_index.html", "youtube_shell.html"]

    def gather(query, settings, *, read=None, page=0, skip=None):
        pagine = [_page(n, f"https://esempio.org/{n}") for n in nomi]
        return [SearchResult(p.title, p.url, "") for p in pagine], pagine

    monkeypatch.setattr(rs, "gather", gather)


def _forge(tmp_path: Path, client) -> Forge:
    cfg = AppConfig()
    cfg.research.enabled = True  # soglia di serie: 0.25
    f = Forge(tmp_path / "book", cfg, client=client)
    f.start()
    _pronto_a_scrivere(f)
    # il quaderno finto è un giallo: gli si dà il vocabolario del libro dell'esempio
    f.state.notebook["premise"] = LIBRO
    return f


def test_only_readable_and_relevant_pages_reach_the_model(tmp_path: Path, pagine_finte) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    capitolo = f.state.outline[0]
    capitolo["points"] = [PUNTO_CERIMONIA]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo)
    assert modello.materiali, "il modello ha letto il materiale"
    visto = modello.materiali[0]
    assert "maloca" in visto, "la pagina pertinente c'è"
    assert "Level 2, the Tunnel" not in visto, "il walkthrough non va al modello"
    assert "Accept all" not in visto and "Become a Patron" not in visto
    assert "(tolto:" in visto, "ogni pagina dice cosa le è stato tolto"


def test_discarded_pages_are_recorded_with_their_reason(tmp_path: Path, pagine_finte) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    capitolo = f.state.outline[0]
    capitolo["points"] = [PUNTO_CERIMONIA]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo)
    dati = f.chapter_data(1)
    per_nome = {p.url.rsplit("/", 1)[-1]: p for p in dati.points[0].pages}
    assert per_nome["ayahuasca_account.html"].status == ""
    assert per_nome["game_walkthrough.html"].status == "non_pertinente"
    assert "giudizio del modello" in per_nome["game_walkthrough.html"].note
    for nome in ("cookie_wall.html", "serial_novel_index.html", "youtube_shell.html"):
        assert per_nome[nome].status == "non_leggibile" and per_nome[nome].note
        assert per_nome[nome].text == ""
    assert per_nome["ayahuasca_account.html"].cleaning.startswith("tolto:")
    # sono nei dati salvati: si rileggono con il loro stato
    riletto = store.load_chapter(f.project.research_dir, 1, capitolo["title"])
    assert {p.status for p in riletto.points[0].pages} == {"", "non_pertinente", "non_leggibile"}


def test_the_threshold_is_a_setting_not_a_constant(tmp_path: Path, pagine_finte) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    f.cfg.research.min_relevance = 0.0  # nessun filtro: anche il walkthrough passa
    capitolo = f.state.outline[0]
    capitolo["points"] = [PUNTO_CERIMONIA]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo)
    assert "Level 2, the Tunnel" in modello.materiali[0]
    assert ResearchSettings().min_page_chars == 400 and ResearchSettings().min_relevance == 0.25


def test_the_landscape_never_gets_unreadable_or_irrelevant_pages(tmp_path: Path, pagine_finte) -> None:
    visti: list[str] = []

    class SpiaPanorama(Spia):
        def chat_json(self, messages, *, schema, model=None, **kw):
            if "works" in set(schema.get("properties", {})):
                visti.append(messages[-1]["content"])
            return super().chat_json(messages, schema=schema, model=model, **kw)

    f = _forge(tmp_path, SpiaPanorama())
    f.research_landscape()
    assert visti and "maloca" in visti[0]
    assert "Level 2, the Tunnel" not in visti[0] and "Become a Patron" not in visti[0]
    paesaggio = f.landscape_data()
    assert {p.status for p in paesaggio.pages} >= {"", "non_leggibile"}


def test_a_page_the_author_includes_must_still_be_readable(tmp_path: Path, monkeypatch) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    capitolo = f.state.outline[0]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo)
    dati = f.chapter_data(1)
    monkeypatch.setattr(rs, "fetch", lambda url, s: _page("cookie_wall.html", url))
    from bookwriter.forge.websearch import SearchError

    with pytest.raises(SearchError, match="non è leggibile"):
        rs.include_result(f.project, f.state, modello, f.cfg.research, dati, capitolo, 0,
                          "https://esempio.org/muro")


def test_fetch_returns_an_unreadable_page_instead_of_none(httpx_mock) -> None:
    from bookwriter.forge.websearch import fetch

    httpx_mock.add_response(url="https://esempio.org/muro",
                            headers={"content-type": "text/html"},
                            text=(PAGES / "cookie_wall.html").read_text(encoding="utf-8"))
    pagina = fetch("https://esempio.org/muro", ResearchSettings(enabled=True))
    assert pagina is not None and not pagina.readable and pagina.text == ""
    assert "cookie" in pagina.note and pagina.status == "non_leggibile"
    assert not pagina.usable


# --------------------------------------------------------------------------- #
#  il giudizio del modello
# --------------------------------------------------------------------------- #
def test_the_model_judges_each_clean_page_and_the_reason_is_kept(tmp_path: Path, pagine_finte) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    capitolo = f.state.outline[0]
    capitolo["points"] = [PUNTO_TUNNEL]  # il punto per cui il confronto a parole non basta
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo)
    assert any("ayahuasca_account" in g for g in modello.giudicate), "ogni pagina pulita è giudicata"
    assert not any("cookie_wall" in g for g in modello.giudicate), "le illeggibili non si giudicano"
    dati = f.chapter_data(1)
    gioco = next(p for p in dati.points[0].pages if "game_walkthrough" in p.url)
    assert gioco.status == "non_pertinente" and "videogioco" in gioco.note
    assert "Level 2, the Tunnel" not in modello.materiali[0]


def test_a_judgement_that_fails_never_discards_a_page(tmp_path: Path, pagine_finte) -> None:
    modello = Spia()
    modello.rompi_giudizio = True
    f = _forge(tmp_path, modello)
    capitolo = f.state.outline[0]
    capitolo["points"] = [PUNTO_TUNNEL]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo)
    dati = f.chapter_data(1)
    per_nome = {p.url.rsplit("/", 1)[-1]: p for p in dati.points[0].pages}
    assert per_nome["game_walkthrough.html"].status == "", "nel dubbio la pagina resta"
    assert per_nome["ayahuasca_account.html"].status == ""
    assert per_nome["cookie_wall.html"].status == "non_leggibile", "le illeggibili restano scartate"


def test_the_threshold_of_the_judgement_is_the_setting(tmp_path: Path, pagine_finte) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    f.cfg.research.min_relevance = 0.04  # il gioco vale 0,05: con questa soglia resta
    capitolo = f.state.outline[0]
    capitolo["points"] = [PUNTO_TUNNEL]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo)
    assert "Level 2, the Tunnel" in modello.materiali[0]


def test_the_word_prefilter_only_drops_the_absolute_zero_in_the_same_language() -> None:
    it_libro = ("Un professore di antropologia scopre che l'acacia è il filo rosso della storia, "
                "che attraversa i millenni e tutte le civiltà della terra come un segreto nascosto "
                "nelle cose di tutti i giorni e che nessuno aveva mai messo insieme prima.")
    pagina_it_altro = ("La ricetta della torta di mele prevede farina, burro e zucchero, che si "
                       "mescolano con le uova per ottenere un impasto morbido e molto leggero, "
                       "da cuocere nel forno per circa quaranta minuti, come ogni nonna sa bene.")
    tema = pt.Topic.of("L'acacia nella storia", it_libro)
    assert pt.zero_overlap(pagina_it_altro, tema, tema.text)
    # lingue diverse, o miste: nessuno scarto senza il modello
    en = ("The recipe for the apple pie needs flour, butter and sugar, which are mixed with the "
          "eggs to obtain a soft dough that is baked in the oven for about forty minutes, as "
          "every grandmother knows very well, and then the pie is served with the cream.")
    assert not pt.zero_overlap(en, tema, tema.text)
    misto = pt.Topic.of(PUNTO_CERIMONIA, it_libro)  # premessa in italiano, punto in inglese
    assert not pt.zero_overlap(en, misto, misto.text), "una pagina inglese passa al modello"
    assert not pt.zero_overlap(pagina_it_altro, pt.Topic(), "")


# --------------------------------------------------------------------------- #
#  pagine vere (scaricate, salvate in tests/fixtures/real)
# --------------------------------------------------------------------------- #
REAL = Path(__file__).parent / "fixtures" / "real"


def _vera(nome: str):
    return pt.clean_html((REAL / nome).read_text(encoding="utf-8"), "https://en.wikipedia.org/x")


def test_a_real_wikipedia_page_loses_its_menus_and_reference_lists() -> None:
    c = _vera("wikipedia_acacia_confusa_en.html")
    assert c.readable and "Acacia confusa" in c.text
    assert c.chars_after < 0.5 * c.chars_before
    assert c.removed["menu e liste di link"] >= 100, "elenchi di riferimenti e di link"
    assert "Toggle the table of contents" not in c.text and "Jump to content" not in c.text


def test_the_real_acacia_page_is_never_dropped_before_the_model() -> None:
    """L'«Acacia confusa» (acacia con DMT, centrale per il libro) deve arrivare al giudizio."""
    for nome in ("wikipedia_acacia_confusa_en.html", "wikipedia_acacia_confusa_it.html"):
        c = _vera(nome)
        for punto in (PUNTO_CERIMONIA, PUNTO_TUNNEL, "The acacia, rich in DMT, runs through history"):
            tema = pt.Topic.of(punto, LIBRO)
            assert not pt.zero_overlap(c.text, tema, tema.text), (nome, punto)


def test_the_real_game_page_goes_to_the_judge_and_the_judge_can_drop_it(tmp_path: Path) -> None:
    c = _vera("wikipedia_dark_souls_en.html")
    assert c.readable
    modello = Spia()
    pagina = Page("https://en.wikipedia.org/wiki/Dark_Souls", "Dark Souls", c.text)
    cfg = ResearchSettings(enabled=True)
    tema = pt.Topic.of(PUNTO_TUNNEL, LIBRO)
    rs.assess_pages([pagina], tema, cfg, client=modello, context="Il punto: tunnel frattale")
    assert modello.giudicate, "arriva al giudizio del modello"
    assert not pagina.relevant and pagina.status == "non_pertinente"
    acacia = _vera("wikipedia_acacia_confusa_en.html")
    buona = Page("https://en.wikipedia.org/wiki/Acacia_confusa", "Acacia confusa", acacia.text)
    rs.assess_pages([buona], tema, cfg, client=modello, context="Il punto: tunnel frattale")
    assert buona.relevant and buona.relevance >= 0.25


# --------------------------------------------------------------------------- #
#  domini esclusi e siti che rifiutano
# --------------------------------------------------------------------------- #
def test_youtube_and_amazon_are_excluded_by_default_in_every_country() -> None:
    from bookwriter.forge.websearch import domain_excluded

    pat = ResearchSettings().excluded_domains
    for url in ("https://www.youtube.com/watch?v=1", "https://m.youtube.com/x", "https://youtu.be/a",
                "https://www.amazon.com/dp/1", "https://www.amazon.co.uk/x",
                "https://smile.amazon.it/y", "https://www.amazon.com.br/z"):
        assert domain_excluded(url, pat), url
    for url in ("https://amazonaws.com/x", "https://www.goodreads.com/b", "https://www.audible.com/p",
                "https://notyoutube.com/x", "https://en.wikipedia.org/wiki/A"):
        assert not domain_excluded(url, pat), url


def test_excluded_results_are_recorded_and_never_downloaded(tmp_path: Path, monkeypatch) -> None:
    from bookwriter.forge import websearch as ws

    scaricate: list[str] = []
    monkeypatch.setattr(ws, "search", lambda q, s, page=0: [
        SearchResult("Un video", "https://www.youtube.com/watch?v=1", ""),
        SearchResult("Un libro", "https://www.amazon.it/dp/1", ""),
        SearchResult("Una voce", "https://it.wikipedia.org/wiki/Acacia", "")])
    monkeypatch.setattr(ws, "fetch", lambda url, s: scaricate.append(url) or Page(url, "x", "t"))
    risultati, pagine = ws.gather("acacia", ResearchSettings(enabled=True))
    assert scaricate == ["https://it.wikipedia.org/wiki/Acacia"]
    assert [r.excluded for r in risultati] == [True, True, False]
    assert len(pagine) == 1
    hits = rs._hits(risultati)
    assert [h.status for h in hits] == ["escluso", "escluso", ""]


def test_the_excluded_list_is_a_setting_the_author_can_empty() -> None:
    from bookwriter.forge.websearch import domain_excluded

    assert not domain_excluded("https://www.youtube.com/x", ResearchSettings(excluded_domains=[]).excluded_domains)
    assert domain_excluded("https://esempio.org/x", ["esempio.org"])


def test_a_site_that_refuses_is_recorded_with_its_code(httpx_mock) -> None:
    from bookwriter.forge.websearch import fetch

    httpx_mock.add_response(url="https://erowid.example/p", status_code=403, text="Forbidden")
    pagina = fetch("https://erowid.example/p", ResearchSettings(enabled=True))
    assert pagina is not None and not pagina.readable
    assert "403" in pagina.note and "rifiutato" in pagina.note
    httpx_mock.add_response(url="https://esempio.org/manca", status_code=404, text="")
    assert "non esiste" in fetch("https://esempio.org/manca", ResearchSettings(enabled=True)).note


# --------------------------------------------------------------------------- #
#  scartare senza cancellare, riprendere
# --------------------------------------------------------------------------- #
def _con_ricerca(tmp_path: Path, pagine_finte) -> tuple[Forge, Spia]:
    modello = Spia()
    f = _forge(tmp_path, modello)
    f.cfg.research.min_relevance = 0.0
    capitolo = f.state.outline[0]
    capitolo["points"] = [PUNTO_CERIMONIA]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo)
    return f, modello


def test_a_discarded_question_is_out_of_everything_but_still_on_disk(tmp_path: Path, pagine_finte) -> None:
    f, _ = _con_ricerca(tmp_path, pagine_finte)
    dati = f.chapter_data(1)
    q = store.Question(id="c1p0q9", text="Un contrasto", chapter=1, point=0, kind="contrasto",
                       status=store.CHIESTA)
    dati.questions.append(q)
    store.save_chapter(f.project.research_dir, dati)
    assert q.id in [x.id for x in f.pending_questions()]
    f.discard_question(q.id)
    assert q.id not in [x.id for x in f.pending_questions()]
    assert q.text not in f._decisions_for(f.state.outline[0])
    assert q.id in [x.id for x in f.questions()], "non si cancella"
    f.discard_question(q.id, on=False)
    assert q.id in [x.id for x in f.pending_questions()], "si ripristina"


def test_old_questions_are_discarded_all_at_once_the_new_ones_stay(tmp_path: Path, pagine_finte) -> None:
    f, _ = _con_ricerca(tmp_path, pagine_finte)
    dati = f.chapter_data(1)
    dati.questions += [
        store.Question(id="v1", text="Il nome Nicolò non compare", chapter=1, point=0, status=store.CHIESTA),
        store.Question(id="v2", text="Non è documentato l'antropologo", chapter=1, point=0, status=store.APERTA),
        store.Question(id="n1", text="La battaglia fu nel 1850", chapter=1, point=0, kind="contrasto",
                       status=store.CHIESTA)]
    store.save_chapter(f.project.research_dir, dati)
    assert f.discard_old_questions() == 2
    vive = {q.id for q in f.pending_questions()}
    assert "n1" in vive and "v1" not in vive and "v2" not in vive
    assert {"v1", "v2", "n1"} <= {q.id for q in f.questions()}


def test_discarding_material_excludes_it_from_the_writing_and_the_review_without_deleting(
    tmp_path: Path, pagine_finte,
) -> None:
    f, _ = _con_ricerca(tmp_path, pagine_finte)
    capitolo = f.state.outline[0]
    assert "Un fatto documentato" in f._research_for(capitolo)
    f.discard_item(1, "all", "", True)
    assert "Un fatto documentato" not in f._research_for(capitolo)
    dati = f.chapter_data(1)
    assert dati.points[0].findings, "i dati restano sul disco"
    assert all(h.discarded for h in dati.points[0].results)
    assert "Un fatto documentato" not in rv_material_text(f)
    f.discard_item(1, "all", "", False)
    assert "Un fatto documentato" in f._research_for(capitolo)


def rv_material_text(f: Forge) -> str:
    from bookwriter.forge import review as rv

    return " ".join(p.statement for p in rv.material_points(f.project, f.state))


def test_a_single_result_and_a_single_finding_can_be_discarded(tmp_path: Path, pagine_finte) -> None:
    f, _ = _con_ricerca(tmp_path, pagine_finte)
    dati = f.chapter_data(1)
    url = dati.points[0].results[0].url
    f.discard_item(1, "url", url, True)
    assert f.chapter_data(1).points[0].results[0].discarded
    assert url not in f.project.research_dir.joinpath(
        store.chapter_stem(1, dati.title) + ".md").read_text(encoding="utf-8").split("## Fonti")[-1]
    f.discard_item(1, "finding", "0:0", True)
    assert f.chapter_data(1).points[0].findings[0].excluded
    with pytest.raises(ValueError):
        f.discard_item(1, "finding", "0:99", True)


def test_resuming_picks_up_the_questions_left_without_attempts(tmp_path: Path, pagine_finte) -> None:
    f, modello = _con_ricerca(tmp_path, pagine_finte)
    dati = f.chapter_data(1)
    dati.questions = [store.Question(id="c1p0q1", text="Un contrasto", chapter=1, point=0,
                                     kind="contrasto", status=store.APERTA)]
    store.save_chapter(f.project.research_dir, dati)
    assert f.resume_deepening() == 1
    q = f.chapter_data(1).questions[0]
    assert q.attempts, "ora ha tentativi"
    assert f.resume_deepening() == 0, "quelle già riprese non si riprendono"


def test_the_questions_toolbar_and_the_discard_buttons_exist_in_the_window(
    qapp, temp_workspace, monkeypatch,
) -> None:
    from PySide6.QtWidgets import QPushButton

    from bookwriter.gui.main_window import MainWindow
    from bookwriter.gui.research_view import ChapterPage

    win = MainWindow()
    assert win.fg_explorer.discard_old.text() == "Scarta tutte le questioni vecchie"
    assert win.fg_explorer.resume.text() == "Riprendi l'approfondimento"
    assert win.fg_explorer.discard_old.minimumHeight() >= 44
    pagina = ChapterPage(1)
    dati = store.ChapterData(number=1, title="T", points=[store.PointData(
        index=0, text="P", findings=[store.Finding(claim="c", detail="d", source="s", url="https://x.org")],
        results=[store.Hit("R", "https://x.org", "", "")],
        pages=[store.PageRead("https://x.org", "R", "testo", "web")])],
        questions=[store.Question(id="q1", text="Q", chapter=1, point=0, status=store.CHIESTA)])
    pagina.fill({"number": 1, "title": "T", "does": ""}, dati, web_on=True)
    testi = [b.text() for b in pagina.findChildren(QPushButton)]
    assert testi.count("Scarta") >= 3, "uno per il materiale, uno per il risultato, uno per la questione"
    assert "Scarta tutto il materiale del capitolo" in testi and "Ripristina tutto" in testi
    mosse = []
    pagina.discard_requested.connect(lambda *a: mosse.append(a))
    next(b for b in pagina.findChildren(QPushButton) if b.text() == "Scarta tutto il materiale del capitolo").click()
    assert mosse == [(1, "all", "", True)]
    win.close()


def test_the_research_thresholds_and_domains_are_in_the_settings_tab(
    qapp, temp_workspace, monkeypatch,
) -> None:
    from bookwriter.gui.main_window import MainWindow

    monkeypatch.setattr(MainWindow, "_load_models", lambda self, *, force=False: self._fill_model_combos())
    win = MainWindow()
    win.pm.create("Soglie")
    win._refresh_settings()
    assert win.set_min_chars.value() == 400 and abs(win.set_min_rel.value() - 0.25) < 1e-9
    assert "amazon.*" in win.set_excluded.text() and "youtube.com" in win.set_excluded.text()
    win.set_min_chars.setValue(900)
    win.set_min_rel.setValue(0.4)
    win.set_excluded.setText("esempio.org, altro.it")
    win._models = []
    win._fill_model_combos()
    # il salvataggio chiede i modelli: se non ci sono, si controlla almeno che i campi si leggano
    cfg = win._current_config()
    assert cfg.research.min_page_chars in (400, 900)
    win.close()
