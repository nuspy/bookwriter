"""Parte E: niente risultati vuoti in silenzio, fonti senza doppioni, interrogazioni sensate,
prosa senza ripetizioni, e il riquadro di stato che mostra cosa fa il modello e ascolta «Interrompi»."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from bookwriter.config import AppConfig
from bookwriter.forge import research as rs
from bookwriter.forge import research_store as store
from bookwriter.forge.engine import Forge
from bookwriter.forge.websearch import Page, SearchResult, norm_url
from tests.test_forge import FintoModello, _pronto_a_scrivere, _quaderno_pieno
from tests.test_research_depth import FintoRicerca


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _forge(tmp_path: Path, client=None, *, web: bool = True) -> Forge:
    cfg = AppConfig()
    cfg.research.enabled = web
    cfg.research.min_relevance = 0.0  # le pagine finte non hanno le parole del libro
    f = Forge(tmp_path / "book", cfg, client=client or FintoRicerca())
    f.start()
    _pronto_a_scrivere(f)
    return f


class ModelloRotto(FintoRicerca):
    """Il modello fallisce (o risponde a vuoto) sul panorama e/o sulle fonti."""

    def __init__(self, *, panorama="errore", fonti="ok", **kw) -> None:
        super().__init__(**kw)
        self.panorama, self.fonti = panorama, fonti

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = set(schema.get("properties", {}))
        if "works" in props and self.panorama != "ok":
            if self.panorama == "errore":
                raise RuntimeError("Risposta non interpretabile come JSON")
            return {"works": [], "sources": [], "ideas": [], "recent": ""}
        if "findings" in props and self.fonti == "errore":
            raise RuntimeError("il server non risponde")
        return super().chat_json(messages, schema=schema, model=model)


@pytest.fixture()
def web(monkeypatch):
    chiamate: list[str] = []

    def gather(query, settings, *, read=None, page=0, skip=None):
        chiamate.append(query)
        s = "-".join(query.lower().split())[:40]
        return ([SearchResult(f"T {s}", f"https://esempio.org/{s}", "e")],
                [Page(f"https://esempio.org/{s}", f"P {s}", "testo")])

    monkeypatch.setattr(rs, "gather", gather)
    return chiamate


# --------------------------------------------------------------------------- #
#  mai un risultato vuoto accettato in silenzio
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("modo", ["errore", "vuoto"])
def test_a_landscape_the_model_could_not_read_is_not_written(tmp_path: Path, web, modo: str) -> None:
    f = _forge(tmp_path, ModelloRotto(panorama=modo))
    with pytest.raises(rs.ResearchFailed) as errore:
        f.research_landscape()
    assert "Riprendi la ricerca" in str(errore.value)
    assert not (f.project.research_dir / "00_landscape.md").exists()
    assert f.landscape_data() is None and not f.state.research_done


def test_a_chapter_research_with_failed_points_is_not_done(tmp_path: Path, web) -> None:
    f = _forge(tmp_path, ModelloRotto(fonti="errore"))
    f.client.panorama = "ok"
    f.research_landscape()
    with pytest.raises(rs.ResearchFailed) as errore:
        f.research_chapters()
    # Il server non risponde mai: dopo tre punti di fila la ricerca si ferma e dice l'ultimo errore.
    assert "si è fermata" in str(errore.value) and "il server non risponde" in str(errore.value)
    assert not f.state.research_done
    assert f.chapter_data(1).warnings, "il guasto sta anche nei dati, non solo nel messaggio"


def test_the_gui_says_why_the_landscape_failed_and_offers_to_redo(
    qapp, temp_workspace, monkeypatch,
) -> None:
    from tests.test_gui import _forge_inline, _intervista_lampo
    from bookwriter.gui.main_window import MainWindow

    win = MainWindow()
    win.pm.create("Panorama rotto")
    modello = ModelloRotto(panorama="errore")
    monkeypatch.setattr(win, "_client", lambda s: modello)
    monkeypatch.setattr(rs, "gather", lambda *a, **k: ([], []))
    _forge_inline(win, monkeypatch)
    win._refresh_forge()
    _intervista_lampo(win)
    win._forge_advance()
    win._forge_make_spec()
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    win._forge_approve_spec()      # la ricerca parte da sola e il panorama non riesce

    forge = win._forge()
    assert not forge.state.research_done and forge.landscape_data() is None
    assert "Il panorama non è stato scritto" in win.fg_todo.text()
    assert not win.fg_status_redo.isHidden(), "«Rifai la ricerca»"
    win.close()


# --------------------------------------------------------------------------- #
#  fonti senza doppioni
# --------------------------------------------------------------------------- #
def test_norm_url_treats_the_same_page_as_one() -> None:
    uguali = ["https://www.Esempio.org/pagina/", "https://esempio.org/pagina#sezione",
              "https://esempio.org/pagina?utm_source=x&utm_medium=y", "https://esempio.org/pagina"]
    assert len({norm_url(u) for u in uguali}) == 1
    assert norm_url("https://esempio.org/a?id=1") != norm_url("https://esempio.org/a?id=2")


def test_the_landscape_lists_each_source_once(tmp_path: Path, monkeypatch) -> None:
    def gather(query, settings, *, read=None, page=0, skip=None):
        # due interrogazioni diverse trovano la stessa pagina, scritta in due modi
        return ([SearchResult("Una pagina", "https://www.esempio.org/x/", "e"),
                 SearchResult("Una pagina", "https://esempio.org/x?utm_source=q", "e")],
                [Page("https://esempio.org/x", "Una pagina", "testo")])

    monkeypatch.setattr(rs, "gather", gather)
    f = _forge(tmp_path)
    f.research_landscape()
    dati = f.landscape_data()
    assert len(dati.results) == 1 and len(dati.pages) == 1
    md = (f.project.research_dir / "00_landscape.md").read_text(encoding="utf-8")
    assert md.count("esempio.org/x") == 1


def test_the_chapter_research_lists_each_source_once(tmp_path: Path, monkeypatch) -> None:
    def gather(query, settings, *, read=None, page=0, skip=None):
        return ([SearchResult("Sempre lei", "https://esempio.org/stessa/", "e")],
                [Page("https://esempio.org/stessa", "Sempre lei", "testo")])

    monkeypatch.setattr(rs, "gather", gather)
    f = _forge(tmp_path)
    f.client.aperte = []
    rs.chapter_research(f.project, f.state, f.client, f.cfg.research, f.state.outline[0])
    for punto in f.chapter_data(1).points:
        assert len(punto.results) == 1 and len(punto.pages) == 1


# --------------------------------------------------------------------------- #
#  interrogazioni del panorama
# --------------------------------------------------------------------------- #
class ModelloInterrogazioni(FintoRicerca):
    def __init__(self, queries, **kw) -> None:
        super().__init__(**kw)
        self.queries = queries
        self.richieste: list[str] = []

    def chat_json(self, messages, *, schema, model=None, **kw):
        if set(schema.get("properties", {})) == {"queries"} and "motore di ricerca" in messages[0]["content"]:
            self.richieste.append(messages[0]["content"])
            return {"queries": self.queries}
        return super().chat_json(messages, schema=schema, model=model)


PREMESSA = ("Un professore di antropologia italiano di 45 anni scopre che l'acacia del giardino "
            "di famiglia nasconde un segreto millenario sui riti funebri")


def _con_premessa(f: Forge) -> None:
    f.state.notebook["premise"] = PREMESSA
    f.state.notebook["title"] = "L'acacia"
    f.state.notebook["form"] = "romanzo giallo"
    f.state.notebook["messages"] = ["il mito dell'albero", "antropologia dei riti funebri"]
    f.save()


def test_landscape_queries_come_from_the_model_in_the_book_language_and_english(
    tmp_path: Path, web,
) -> None:
    f = _forge(tmp_path, ModelloInterrogazioni([
        "romanzi gialli antropologia", "gialli ambientati nel mondo accademico",
        "best anthropological mystery novels", "funerary rites in fiction", "recensioni gialli antropologici"]))
    _con_premessa(f)
    report = f.research_landscape()
    assert report.queries[0] == "romanzi gialli antropologia" and len(report.queries) == 5
    assert any("best anthropological" in q for q in report.queries), "anche in inglese"
    assert not any(PREMESSA[:30].lower() in q.lower() for q in report.queries)
    assert "inglese" in f.client.richieste[0], "il modello è istruito a scrivere anche in inglese"


def test_a_truncated_premise_is_never_a_query(tmp_path: Path, web) -> None:
    f = _forge(tmp_path, ModelloInterrogazioni([
        "migliori libri su Un professore di antropologia italiano di 45 anni scopre che l'acacia",
        "gialli antropologici", "romanzi sui riti funebri", "antropologia romanzo"]))
    _con_premessa(f)
    report = f.research_landscape()
    assert not any(q.lower().startswith("migliori libri su un professore") for q in report.queries)


def test_the_fallback_queries_use_title_form_and_themes_not_the_premise(tmp_path: Path, web) -> None:
    f = _forge(tmp_path, ModelloInterrogazioni([]))          # il modello non ne dà: ripiego
    _con_premessa(f)
    queries = rs.generate_landscape_queries(f.state, f.client)
    assert 4 <= len(queries) <= 8
    assert not any("professore" in q.lower() for q in queries)
    assert any("L'acacia" in q for q in queries) and any("romanzo giallo" in q for q in queries)
    assert any("best books" in q or "book review" in q for q in queries), "anche in inglese"


# --------------------------------------------------------------------------- #
#  prosa senza ripetizioni
# --------------------------------------------------------------------------- #
class ModelloRipetitivo(FintoModello):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.chiamate_capitolo = 0
        self.sempre = False

    def chat(self, messages, **kw):
        if "capitolo di libro" in messages[0]["content"]:
            self.chiamate_capitolo += 1
            p = "Questo paragrafo è abbastanza lungo da contare come un paragrafo vero e proprio."
            if self.chiamate_capitolo == 1 or self.sempre:
                return f"# Capitolo 1: x\n\n{p}\n\nUn altro paragrafo, diverso e abbastanza lungo.\n\n{p}\n"
            return "# Capitolo 1: x\n\n" + "\n\n".join(
                f"Il paragrafo numero {i} dice una cosa diversa dagli altri e basta." for i in range(30)) + "\n"
        return super().chat(messages, **kw)


def test_a_chapter_with_repeated_paragraphs_is_rewritten_once(tmp_path: Path) -> None:
    f = Forge(tmp_path / "book", AppConfig(), client=ModelloRipetitivo(chapters=3, words=300))
    f.start()
    _pronto_a_scrivere(f)
    f.state.notebook["form"] = "saggio divulgativo"  # il percorso di una chiamata sola
    for c in f.state.outline:
        c["words"] = 300           # un capitolo breve: una chiamata sola
    risultato = f.write_next()
    assert f.client.chiamate_capitolo == 2, "rifatto una volta, non di più"
    testo = risultato.path.read_text(encoding="utf-8")
    assert "Il paragrafo numero 3" in testo


def test_if_the_rewrite_still_repeats_the_identical_paragraphs_are_removed(tmp_path: Path) -> None:
    f = Forge(tmp_path / "book", AppConfig(), client=ModelloRipetitivo(chapters=3, words=300))
    f.client.sempre = True
    f.start()
    _pronto_a_scrivere(f)
    f.state.notebook["form"] = "saggio divulgativo"  # il percorso di una chiamata sola
    for c in f.state.outline:
        c["words"] = 300
    risultato = f.write_next()
    assert f.client.chiamate_capitolo == 2
    testo = risultato.path.read_text(encoding="utf-8")
    assert testo.count("Questo paragrafo è abbastanza lungo") == 1


# --------------------------------------------------------------------------- #
#  il riquadro di stato: cosa fa il modello, e «Interrompi»
# --------------------------------------------------------------------------- #
def test_the_status_box_shows_the_reasoning_count_then_the_answer(
    qapp, temp_workspace, monkeypatch,
) -> None:
    from tests.test_gui import _forge_inline, _scheda_libro
    from bookwriter.gui.main_window import MainWindow

    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    visti: list[str] = []

    def lavoro():
        win._on_model_activity(0, 0)
        win._on_model_activity(920_000, 0)           # ~230.000 token di ragionamento
        visti.append(win._forge_doing_text())
        win._on_model_activity(920_000, 300)
        visti.append(win._forge_doing_text())

    win._forge_job(lambda: lavoro(), lambda r: None, "Prova…", activity="Sto lavorando…")
    assert "il modello sta ragionando… 230.000 token" in visti[0]
    assert "il modello sta scrivendo la risposta…" in visti[1]
    assert win._fg_activity is None
    win.close()


def test_interrupt_cancels_the_live_clients_only_for_the_forge_jobs(
    qapp, temp_workspace, monkeypatch,
) -> None:
    from tests.test_gui import _scheda_libro
    from bookwriter.gui.main_window import MainWindow

    class Finto:
        def __init__(self) -> None:
            self.annullato = False

        def cancel(self) -> None:
            self.annullato = True

    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    cliente = Finto()
    win._live_clients.append((cliente, None))
    win._request_stop()                                   # nessun lavoro della scheda in corso
    assert not cliente.annullato, "un lavoro di un'altra scheda si ferma alla fine del brano"

    def lavoro():
        win._request_stop()

    win._live_clients.append((cliente, None))
    win._forge_job(lavoro, lambda r: None, "Prova…", activity="Sto lavorando…")
    assert cliente.annullato
    win.close()


def test_a_cancelled_call_is_not_shown_as_an_error(qapp, temp_workspace, monkeypatch) -> None:
    from tests.test_gui import _scheda_libro
    from bookwriter.gui.main_window import MainWindow
    from bookwriter.llm.client import LLMCancelled

    win = MainWindow()
    _scheda_libro(win, monkeypatch)
    annullati: list[int] = []

    def lavoro():
        raise LLMCancelled()

    win._forge_job(lavoro, lambda r: None, "Prova…", activity="Sto lavorando…",
                   on_error=lambda m: annullati.append(-1), on_cancel=lambda: annullati.append(1))
    assert annullati == [1], "va al gestore dell'interruzione, non a quello degli errori"
    assert "Interrotto" in win.fg_todo.text() and "Qualcosa non è andato" not in win.fg_todo.text()
    win.close()


def test_a_section_with_repeated_paragraphs_is_rewritten_once(tmp_path: Path) -> None:
    from tests.test_long_chapters import ModelloCorto, _forge_lungo

    class SezioneRipetuta(ModelloCorto):
        def __init__(self, *a, **k) -> None:
            super().__init__(*a, **k)
            self.rifatte = 0

        def chat(self, messages, **kw):
            sistema = messages[0]["content"]
            if "Scrivi UNA SEZIONE" in sistema and "Sezione 1 di" in sistema:
                if messages[-1]["content"].startswith("Nel testo che hai scritto ci sono ripetizioni"):
                    self.rifatte += 1
                    return "## Titolo 1\n\n" + " ".join(["alfaa"] * 1400)
                p = "Un paragrafo lungo abbastanza da essere contato come ripetizione vera."
                return f"## Titolo 1\n\n{p}\n\n" + " ".join(["alfaa"] * 1400) + f"\n\n{p}"
            return super().chat(messages, **kw)

    f = _forge_lungo(tmp_path, tetto=1800)
    f.client = SezioneRipetuta(1800, chapters=3, words=900)
    risultato = f.write_next()
    assert f.client.rifatte == 1, "la sezione si rifà una volta"
    testo = risultato.path.read_text(encoding="utf-8")
    assert testo.count("Un paragrafo lungo abbastanza") == 0


# --------------------------------------------------------------------------- #
#  un modello che si blocca su un punto: si vede subito, si prosegue, non si ritenta
# --------------------------------------------------------------------------- #
class ModelloBloccato(FintoRicerca):
    """Sulle fonti il modello si blocca a ripetere (``LLMDegenerate``), come fa il vero client."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.richieste_fonti = 0

    def chat_json(self, messages, *, schema, model=None, **kw):
        if "findings" in set(schema.get("properties", {})):
            from bookwriter.llm.client import LLMDegenerate

            self.richieste_fonti += 1
            raise LLMDegenerate("Il modello «m» ha cominciato a ripetere sempre la stessa "
                                "sequenza nel testo che stava scrivendo ('abc').")
        return super().chat_json(messages, schema=schema, model=model)


def test_a_point_the_model_gets_stuck_on_is_announced_at_once_and_not_retried(
    tmp_path: Path, web,
) -> None:
    modello = ModelloBloccato()
    f = _forge(tmp_path, modello)
    note: list[object] = []
    capitolo = f.state.outline[0]
    punti = len(capitolo["points"])
    rapporto = rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo,
                                   note=note.append)

    assert modello.richieste_fonti == punti, "una sola richiesta per punto: nessun ritentare"
    assert len(rapporto.failures) == punti
    avvisi = [n for n in note if isinstance(n, str) and n.startswith("⚠")]
    assert len(avvisi) == punti, "ogni punto fallito si dice subito, non a fine capitolo"
    assert "Riprendi la ricerca" in avvisi[0] and "riprendere" in avvisi[0]
    assert "testo di partenza" not in avvisi[0]
    # il contatore avanza anche sui punti falliti
    assert [n for n in note if isinstance(n, tuple) and n[0] == "point"][-1][2] == punti


def test_a_stuck_model_ends_with_a_clear_error_and_the_research_stays_to_do(
    tmp_path: Path, web,
) -> None:
    f = _forge(tmp_path, ModelloBloccato())
    f.research_landscape()
    with pytest.raises(rs.ResearchFailed) as errore:
        f.research_chapters()
    assert "si è fermata" in str(errore.value) and "Riprendi la ricerca" in str(errore.value)
    assert not f.state.research_done


def test_the_cli_says_why_the_research_failed_instead_of_a_traceback(
    tmp_path: Path, web, monkeypatch,
) -> None:
    from typer.testing import CliRunner

    from bookwriter import cli

    f = _forge(tmp_path, ModelloBloccato())
    f.research_landscape()
    monkeypatch.setattr(cli, "_forge", lambda **k: f)
    esito = CliRunner().invoke(cli.app, ["forge", "research", "--no-landscape", "--chapters"])
    assert esito.exit_code == 1
    assert "si è fermata" in (esito.output + str(esito.stderr_bytes or b"", "utf-8", "ignore"))


# --------------------------------------------------------------------------- #
#  tre punti di fila falliti: la ricerca si ferma, e quello che c'è resta
# --------------------------------------------------------------------------- #
class ModelloAFasi(FintoRicerca):
    """Le richieste di fonti riescono o falliscono secondo una sequenza (True = riesce)."""

    def __init__(self, sequenza, **kw) -> None:
        super().__init__(**kw)
        self.sequenza, self.chiamate_fonti = list(sequenza), 0

    def chat_json(self, messages, *, schema, model=None, **kw):
        if "findings" in set(schema.get("properties", {})):
            ok = self.sequenza[min(self.chiamate_fonti, len(self.sequenza) - 1)]
            self.chiamate_fonti += 1
            if not ok:
                raise RuntimeError("il server non risponde")
        return super().chat_json(messages, schema=schema, model=model)


def test_three_failed_points_in_a_row_stop_the_research_and_keep_what_is_done(
    tmp_path: Path, web,
) -> None:
    modello = ModelloAFasi([True, False])  # il primo punto riesce, poi nulla
    modello.aperte = []
    f = _forge(tmp_path, modello)
    f.research_landscape()
    with pytest.raises(rs.ResearchFailed) as errore:
        f.research_chapters()
    assert "si è fermata" in str(errore.value) and "Riprendi la ricerca" in str(errore.value)
    assert "salvati" in str(errore.value)
    assert modello.chiamate_fonti == 1 + rs.CONSECUTIVE_FAILURES, "si smette alla terza, non oltre"
    dati = f.chapter_data(1)
    assert dati is not None and dati.points[0].findings, "il punto riuscito resta salvato"
    assert f.chapter_data(len(f.state.outline)) is None, "gli ultimi capitoli non partono"
    assert not f.state.research_done


def test_a_success_resets_the_streak(tmp_path: Path, web) -> None:
    modello = ModelloAFasi([False, False, True] * 40)
    modello.aperte = []
    f = _forge(tmp_path, modello)
    f.research_landscape()
    with pytest.raises(rs.ResearchFailed) as errore:
        f.research_chapters()
    # Mai tre insieme: la ricerca arriva in fondo e dice cosa non è riuscito (non «si è fermata»).
    assert "non è completa" in str(errore.value)
    assert "si è fermata" not in str(errore.value)


# --------------------------------------------------------------------------- #
#  i passi del modello falliti nell'approfondimento si riprovano, poi si dicono
# --------------------------------------------------------------------------- #
class ModelloChePerdeColpi(FintoRicerca):
    """Riformulare fallisce le prime ``guasti`` volte; non risolve mai (nessuna fonte buona)."""

    def __init__(self, guasti: int, **kw) -> None:
        super().__init__(**kw)
        self.risolve_se = "mai-presente-nel-materiale"
        self.guasti, self.riformulazioni = guasti, 0

    def chat_json(self, messages, *, schema, model=None, **kw):
        if set(schema.get("properties", {})) == {"queries"}:
            self.riformulazioni += 1
            if self.riformulazioni <= self.guasti:
                raise RuntimeError("risposta non interpretabile")
        return super().chat_json(messages, schema=schema, model=model)


def _un_capitolo(f: Forge, modello, note: list) -> store.ChapterData:
    f.state.outline = f.state.outline[:1]
    capitolo = f.state.outline[0]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, capitolo, note=note.append)
    return f.chapter_data(1)


def test_a_question_whose_model_step_failed_is_retried_up_to_three_times_then_reported(
    tmp_path: Path, web,
) -> None:
    modello = ModelloChePerdeColpi(guasti=10_000)
    f = _forge(tmp_path, modello)
    note: list = []
    dati = _un_capitolo(f, modello, note)

    testi = [n for n in note if isinstance(n, str)]
    assert any("tentativo 1 di 3" in t for t in testi) and any("tentativo 3 di 3" in t for t in testi)
    assert not any("tentativo 4" in t for t in testi), "al massimo tre giri"
    assert any("dopo 3 tentativi" in w for w in dati.warnings), "e alla fine si avvisa, con l'elenco"
    assert "cifra esatta" in "".join(dati.warnings)
    assert all(q.step_failed for q in dati.questions if not q.closed)


def test_a_retry_that_works_clears_the_failure_and_says_nothing(tmp_path: Path, web) -> None:
    modello = ModelloChePerdeColpi(guasti=1)  # sbaglia una volta sola
    f = _forge(tmp_path, modello)
    note: list = []
    dati = _un_capitolo(f, modello, note)

    assert not any("dopo 3 tentativi" in w for w in dati.warnings)
    assert all(not q.step_failed for q in dati.questions)
    assert any("tentativo 1 di 3" in t for t in note if isinstance(t, str))
    assert not any("tentativo 2 di 3" in t for t in note if isinstance(t, str)), \
        "riuscito al primo giro: non si insiste"


def test_a_healthy_model_makes_no_retry(tmp_path: Path, web) -> None:
    modello = ModelloChePerdeColpi(guasti=0)
    f = _forge(tmp_path, modello)
    note: list = []
    dati = _un_capitolo(f, modello, note)
    assert not any("tentativo" in t for t in note if isinstance(t, str))
    assert not any("dopo 3 tentativi" in w for w in dati.warnings)
