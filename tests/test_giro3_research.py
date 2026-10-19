"""Giro 3, la ricerca: interrogazioni centrate sul punto, e riprendere invece di rifare."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookwriter.config import AppConfig
from bookwriter.forge import notebook as nb
from bookwriter.forge import research as rs
from bookwriter.forge import research_store as store
from bookwriter.forge.engine import Forge
from bookwriter.forge.websearch import Page, SearchResult
from tests.test_forge import _pronto_a_scrivere
from tests.test_research_depth import FintoRicerca

PREMESSA = ("Un romanzo sull'acacia: Osiride, Mosè e l'Arca dell'Alleanza, i Templari e la "
            "Massoneria, fino al finale nel tempio.")


@pytest.fixture()
def web(monkeypatch):
    chiamate: list[str] = []

    def gather(query, settings, *, read=None, page=0, skip=None):
        chiamate.append(query)
        s = "-".join(query.lower().split())[:40]
        return ([SearchResult(f"T {s}", f"https://esempio.org/{s}", "e")],
                [Page(f"https://esempio.org/{s}", f"P {s}",
                      "una cerimonia con l'ayahuasca descritta da chi c'era")])

    monkeypatch.setattr(rs, "gather", gather)
    return chiamate


def _forge(tmp_path: Path, client, *, relevance: float = 0.0) -> Forge:
    cfg = AppConfig()
    cfg.research.enabled = True
    cfg.research.min_relevance = relevance
    f = Forge(tmp_path / "book", cfg, client=client)
    f.start()
    _pronto_a_scrivere(f)
    return f


class Spia(FintoRicerca):
    """Registra i messaggi utente di ogni tipo di richiesta e quante volte si chiede un punto."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.prompt: dict[str, list[str]] = {}
        self.fonti = 0
        #: numeri d'ordine (da 1) delle richieste di fonti che devono fallire
        self.fallisci: set[int] = set()
        self.fermati_dopo: int | None = None

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = set(schema.get("properties", {}))
        utente = messages[-1]["content"]
        if "search_queries" in props:
            self.prompt.setdefault("interrogazioni", []).append(utente)
            return {"search_queries": ["ayahuasca ceremony experience"]}
        if "score" in props:
            self.prompt.setdefault("pertinenza", []).append(utente)
            return {"score": 0.9, "reason": "parla di ayahuasca"}
        if "findings" in props:
            self.prompt.setdefault("estrazione", []).append(utente)
            self.fonti += 1
            if self.fonti in self.fallisci:
                raise RuntimeError("il modello non risponde")
        if props == {"queries"}:
            self.prompt.setdefault("riformulazione", []).append(utente)
        return super().chat_json(messages, schema=schema, model=model)


# --------------------------------------------------------------------------- #
#  B3 — le richieste su un punto non portano i temi degli altri capitoli
# --------------------------------------------------------------------------- #
def test_a_point_on_ayahuasca_does_not_send_the_themes_of_the_other_chapters(
    tmp_path: Path, web,
) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello, relevance=0.25)
    f.state.notebook = {**f.state.notebook, "premise": PREMESSA, "messages": [PREMESSA],
                        "title": "Archeomysteries", "form": "romanzo storico-iniziatico",
                        "setting": "Egitto dei faraoni e Trieste oggi"}
    f.state.outline = [{"number": 1, "title": "Il tunnel frattale", "part": 1, "words": 900,
                        "does": "Una notte di cerimonia.",
                        "points": ["La cerimonia dell'ayahuasca"]}]
    rs.chapter_research(f.project, f.state, modello, f.cfg.research, f.state.outline[0])
    assert set(modello.prompt) >= {"interrogazioni", "pertinenza", "estrazione"}
    for tipo in ("interrogazioni", "pertinenza", "estrazione"):
        for testo in modello.prompt[tipo]:
            for tema in ("Osiride", "Mosè", "Arca", "Templari", "Massoneria", "finale nel tempio"):
                assert tema not in testo, f"{tipo}: «{tema}» non c'entra con questo punto"
            assert "La cerimonia dell'ayahuasca" in testo
            assert "Il tunnel frattale" in testo
    # il contesto c'è, in una riga: titolo, forma e ambientazione
    contesto = modello.prompt["interrogazioni"][0]
    assert "Archeomysteries" in contesto and "romanzo storico-iniziatico" in contesto
    assert "Egitto dei faraoni" in contesto


def test_the_context_line_is_one_short_line(tmp_path: Path) -> None:
    f = _forge(tmp_path, FintoRicerca())
    f.state.notebook = {**f.state.notebook, "premise": PREMESSA, "title": "T", "form": "F" * 500,
                        "setting": "S" * 500}
    riga = rs._context_line(f.state)
    assert "\n" not in riga and len(riga) < 400 and "Osiride" not in riga


# --------------------------------------------------------------------------- #
#  F — riprendere
# --------------------------------------------------------------------------- #
def _tutti(f: Forge) -> list[store.ChapterData]:
    return store.load_all_chapters(f.project.research_dir, f.state.outline)


def test_each_point_is_saved_as_soon_as_it_is_done(tmp_path: Path, web) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    modello.fermati_dopo = 2
    chiamate = {"n": 0}

    def ferma() -> bool:
        chiamate["n"] += 1
        return modello.fonti >= 2  # dopo il secondo punto di fonti

    rs.chapter_research(f.project, f.state, modello, f.cfg.research, f.state.outline[0],
                        should_stop=ferma)
    dati = store.load_chapter(f.project.research_dir, 1, f.state.outline[0]["title"])
    assert [p.done for p in dati.points] == [True, True]
    assert dati.stage == store.STAGE_POINTS, "il terzo punto manca: il capitolo non è finito"
    assert not any(p.text.startswith("Scena 3") for p in dati.points)


def test_resuming_does_not_redo_what_is_done(tmp_path: Path, web) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    totale = sum(len(rs.chapter_points(c)) for c in f.state.outline)  # 9 punti
    f.research_landscape()
    stop = {"on": False}
    f.research_chapters(should_stop=lambda: modello.fonti >= 4)  # interrotta a metà del cap. 2
    assert modello.fonti == 4 and not f.state.research_done
    progresso = f.research_progress()
    assert progresso["done"] == 1 and progresso["partial"] == 1
    assert progresso["next"]["number"] == 2 and progresso["next"]["point"] == 2

    f.research_chapters()  # «Riprendi»
    assert modello.fonti == totale, "ogni punto è stato cercato una sola volta"
    assert f.state.research_done
    assert all(c.stage == store.STAGE_DONE and all(p.done for p in c.points) for c in _tutti(f))
    del stop


def test_a_failed_point_is_retried_alone_on_resume(tmp_path: Path, web) -> None:
    modello = Spia()
    modello.fallisci = {2}
    f = _forge(tmp_path, modello)
    f.research_landscape()
    with pytest.raises(rs.ResearchFailed) as errore:
        f.research_chapters()
    assert "Riprendi la ricerca" in str(errore.value)
    primo = store.load_chapter(f.project.research_dir, 1, f.state.outline[0]["title"])
    assert [p.done for p in primo.points] == [True, False, True]
    assert primo.stage == store.STAGE_POINTS and not f.state.research_done
    prima = modello.fonti
    f.research_chapters()
    assert modello.fonti - prima == 1, "solo il punto non riuscito, gli altri capitoli sono fatti"
    assert f.state.research_done
    primo = store.load_chapter(f.project.research_dir, 1, f.state.outline[0]["title"])
    assert all(p.done for p in primo.points)
    assert not [w for w in primo.warnings if w.startswith("Punto 2 «")], "l'avviso non resta"


def test_redo_keeps_the_material_and_redoes_every_point(tmp_path: Path, web) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    f.research_landscape()
    f.research_chapters()
    prima = modello.fonti
    conteggi = [[len(pt.findings) for pt in c.points] for c in _tutti(f)]
    pagine = {p.url for c in _tutti(f) for pt in c.points for p in pt.pages}
    # una decisione dell'autore su una questione
    cap = _tutti(f)[0]
    cap.questions[0].status = store.DECISA
    cap.questions[0].outcome = {"kind": "omit"}
    store.save_chapter(f.project.research_dir, cap)

    assert f.prepare_redo() == 3
    assert not f.state.research_done
    assert all(not p.done and c.stage == store.STAGE_POINTS for c in _tutti(f) for p in c.points)
    assert pagine <= {p.url for c in _tutti(f) for pt in c.points for p in pt.pages}, \
        "il materiale resta anche prima di cominciare"

    f.research_chapters()
    assert modello.fonti - prima == prima, "tutti i punti rifatti"
    dopo = _tutti(f)
    assert pagine <= {p.url for c in dopo for pt in c.points for p in pt.pages}
    assert [[len(pt.findings) for pt in c.points] for c in dopo] == conteggi, "senza doppioni"
    assert dopo[0].questions[0].status == store.DECISA, "le decisioni dell'autore restano"
    assert f.state.research_done


def test_an_interrupted_redo_resumes_the_redo(tmp_path: Path, web) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    f.research_landscape()
    f.research_chapters()
    f.prepare_redo()
    base = modello.fonti
    f.research_chapters(should_stop=lambda: modello.fonti - base >= 4)
    assert not f.state.research_done
    progresso = f.research_progress()
    assert progresso["done"] == 1 and progresso["next"]["number"] == 2, \
        "i capitoli 2 e 3 non si danno per buoni: il rifacimento era in corso"
    f.research_chapters()
    assert modello.fonti - base == 9 and f.state.research_done


def test_fresh_sets_the_material_aside_and_starts_from_zero(tmp_path: Path, web) -> None:
    modello = Spia()
    f = _forge(tmp_path, modello)
    f.research_landscape()
    f.research_chapters()
    rd = f.project.research_dir
    prima = sorted(p.name for p in rd.iterdir() if p.is_file())
    assert prima

    cartella = f.set_aside_research()
    assert cartella is not None and cartella.parent.name == ".superati"
    assert sorted(p.name for p in cartella.iterdir()) == prima, "niente è stato cancellato"
    assert not [p for p in rd.iterdir() if p.is_file()]
    assert not f.state.research_done and f.landscape_data() is None

    base = modello.fonti
    f.research_landscape()
    f.research_chapters()
    assert modello.fonti - base == 9 and f.state.research_done


def test_fresh_with_nothing_to_set_aside_says_so(tmp_path: Path) -> None:
    f = _forge(tmp_path, FintoRicerca())
    assert f.set_aside_research() is None


def test_a_chapter_saved_before_the_stages_is_read_by_what_it_contains(tmp_path: Path) -> None:
    rd = tmp_path / "r"
    rd.mkdir()

    def capitolo(punti_ok: int, avviso: str = "", ferma: bool = False) -> dict:
        return {
            "number": 1, "title": "Uno", "does": "", "date": "", "warnings": [avviso] if avviso else [],
            "points": [{"index": i, "text": f"p{i}", "queries": [], "results": [], "pages": [],
                        "findings": []} for i in range(3)][:punti_ok],
            "questions": ([{"id": "q1", "text": "x", "chapter": 1, "point": 0, "status": "aperta",
                            "attempts": [], "outcome": {}}] if ferma else []),
        }

    chiuso = store.chapter_from_dict(capitolo(3))
    assert chiuso.stage == store.STAGE_DONE and all(p.done for p in chiuso.points)
    fermo = store.chapter_from_dict(capitolo(3, ferma=True))
    assert fermo.stage == store.STAGE_DEEPENING, "restano questioni ferme da approfondire"
    fallito = store.chapter_from_dict(capitolo(3, avviso="Punto 2 «p1»: il modello non risponde"))
    assert fallito.stage == store.STAGE_POINTS
    assert [p.done for p in fallito.points] == [True, False, True]
    # e un capitolo con meno punti di quelli della struttura riparte dai mancanti
    capitolo_outline = {"number": 1, "title": "Uno", "points": ["p0", "p1", "p2"]}
    corto = store.chapter_from_dict(capitolo(2))
    assert rs.chapter_state(corto, capitolo_outline) == "points"
    assert rs.chapter_state(chiuso, capitolo_outline) == "done"
    assert rs.chapter_state(fermo, capitolo_outline) == "deepening"
    altri = {"number": 1, "title": "Uno", "points": ["diverso", "p1", "p2"]}
    assert rs.chapter_state(chiuso, altri) == "new", "i punti sono cambiati: non vale"


def test_chapter_files_are_written_whole_or_not_at_all(tmp_path: Path, monkeypatch) -> None:
    """Chi legge mentre si salva (l'esploratore) non deve trovare mezzo file."""
    dati = store.ChapterData(number=1, title="Uno", points=[store.PointData(index=0, text="a")])
    store.save_chapter(tmp_path, dati)
    percorso = store.chapter_json_path(tmp_path, 1, "Uno")
    assert json.loads(percorso.read_text(encoding="utf-8"))["stage"] == store.STAGE_POINTS
    assert not list(tmp_path.glob("*.tmp"))
    import os as _os

    def scoppia(src, dst):
        raise OSError("disco pieno")

    monkeypatch.setattr(store.os, "replace", scoppia)
    dati.title = "Uno"
    dati.points.append(store.PointData(index=1, text="b"))
    with pytest.raises(OSError):
        store.save_chapter(tmp_path, dati)
    assert len(json.loads(percorso.read_text(encoding="utf-8"))["points"]) == 1, \
        "il file di prima è intatto"
    del _os


def test_the_cli_resumes_redoes_or_starts_over(tmp_path: Path, web, monkeypatch) -> None:
    from typer.testing import CliRunner

    from bookwriter import cli

    modello = Spia()
    f = _forge(tmp_path, modello)
    monkeypatch.setattr(cli, "_forge", lambda **k: f)
    esegui = CliRunner()

    primo = esegui.invoke(cli.forge_app, ["research", "--chapters"])
    assert primo.exit_code == 0, primo.output
    fatte = modello.fonti
    assert fatte == 9 and f.state.research_done

    # di nuovo: riprende, e non c'è niente da rifare (il panorama c'è, i capitoli sono finiti)
    secondo = esegui.invoke(cli.forge_app, ["research", "--chapters"])
    assert secondo.exit_code == 0 and modello.fonti == fatte

    rifatto = esegui.invoke(cli.forge_app, ["research", "--chapters", "--redo"])
    assert rifatto.exit_code == 0 and modello.fonti == 2 * fatte
    assert "Punti azzerati in 3 capitoli" in rifatto.output

    da_zero = esegui.invoke(cli.forge_app, ["research", "--chapters", "--fresh"])
    assert da_zero.exit_code == 0 and modello.fonti == 3 * fatte
    assert ".superati" in da_zero.output
    assert list(f.project.research_dir.glob(".superati/*/01_*.json"))

    assert esegui.invoke(cli.forge_app, ["research", "--redo", "--fresh"]).exit_code == 2
