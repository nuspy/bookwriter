"""La verifica della qualità: voti da 0 a 10 con il metro del genere, correzioni mirate, versioni.

L'autore l'ha chiesta dopo la seconda analisi del cap. 11: i criteri dell'analisi editoriale più
quelli di un lettore professionale (stile e autori che ricorda, capacità narrativa, attenzione,
argomentazioni, leggibilità, interesse, godibilità, fluidità, coerenza, globale, potenziale), per
capitolo e per il libro intero, e un ciclo che corregge **un problema alla volta, solo sulla sua
parte**, rivaluta, e oltre il tetto dei giri chiede il permesso.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bookwriter.config import AppConfig
from bookwriter.forge import quality as ql
from bookwriter.forge.engine import Forge, open_quality_workspace


# --------------------------------------------------------------------------- #
#  il metro: il tipo di libro
# --------------------------------------------------------------------------- #
def test_the_kind_of_book_comes_from_the_genre() -> None:
    assert ql.kind_of("thriller esoterico") == "romanzo"
    assert ql.kind_of("saggio divulgativo sulla storia dei templari") == "divulgativo"
    assert ql.kind_of("manuale pratico di fotografia") == "manuale"
    assert ql.kind_of("tesi di dottorato in archeologia") == "accademico"
    assert ql.kind_of("racconti brevi") == "racconti"
    assert ql.kind_of("", narrative_hint=False) == "saggio"


def test_each_kind_has_its_own_yardstick() -> None:
    personaggi = ql.CRITERIA_BY_KEY["personaggi"]
    assert personaggi.applies("romanzo") and not personaggi.applies("manuale")
    assert "romanzo" in personaggi.critical
    fonti = ql.CRITERIA_BY_KEY["fonti"]
    assert "accademico" in fonti.critical and "romanzo" not in fonti.critical

    def quota(chiave: str, tipo: str) -> float:
        validi = [c for c in ql.CRITERIA if c.applies(tipo) and c.in_global]
        return ql.CRITERIA_BY_KEY[chiave].weight(tipo) / sum(c.weight(tipo) for c in validi)

    assert quota("fonti", "accademico") > quota("fonti", "romanzo")
    # la rubrica narrativa della specifica editoriale: undici criteri che fanno 100
    assert sum(ql.NARRATIVE_WEIGHTS.values()) == 100
    assert ql.CRITERIA_BY_KEY["causalita"].weight("romanzo") == 15
    assert {"causalita", "cronologia", "oggetti", "continuita"} <= {
        c.key for c in ql.CRITERIA if "romanzo" in c.critical}
    assert not ql.CRITERIA_BY_KEY["cronologia"].applies("manuale")
    avanzamento = ql.CRITERIA_BY_KEY["avanzamento"]
    assert avanzamento.label_for("romanzo") == "Avanzamento della storia"
    assert avanzamento.label_for("manuale") == "Avanzamento dell'argomento"
    assert not ql.CRITERIA_BY_KEY["potenziale"].in_global, "una stima non fa media"


def test_readability_is_measured_without_a_model() -> None:
    facile = "Il cane corre. Il gatto dorme. La casa è calda. " * 20
    difficile = ("L'interpretazione epistemologica dell'evidenza archeologica presuppone "
                 "un'articolazione metodologica considerevolmente sofisticata e "
                 "interdisciplinare, " * 6) + "."
    nome, alto, _ = ql.readability(facile, "it")
    _, basso, _ = ql.readability(difficile, "it")
    assert nome == "Gulpease" and alto > 60 > basso
    assert ql.readability(facile, "en")[0] == "Flesch"
    assert ql.length_score(7000, 7000) == 10.0 and ql.length_score(7600, 7000) == 10.0
    assert ql.length_score(3500, 7000) < 6 and ql.length_score(14000, 7000) == 0


# --------------------------------------------------------------------------- #
#  un modello finto che dà i voti
# --------------------------------------------------------------------------- #
class Valutatore:
    """Dà a ogni criterio il voto di ``voti`` (o ``base``) e un problema con citazione."""

    def __init__(self, base: float = 8.0, voti: dict[str, float] | None = None,
                 citazione: str = "") -> None:
        self.base = base
        self.voti = dict(voti or {})
        self.citazione = citazione
        self.gruppi = 0
        self.riscritture: list[str] = []
        self.localizzati = 0
        self.sistemi: list[str] = []

    def chat_json(self, messages, *, schema, model=None, **kw):
        props = schema.get("properties", {})
        self.sistemi.append(messages[0]["content"])
        if set(props) == {"section", "first", "last"}:
            self.localizzati += 1
            return {"section": 1, "first": 2, "last": 2}
        if "comparables" in props:
            return {"items": [{"key": "arco", "score": 6.5, "verdict": "cresce poco",
                               "evidence": "x", "problems": []},
                              {"key": "potenziale", "score": 5.0, "verdict": "stima: nicchia",
                               "evidence": "x", "problems": []}],
                    "summary": "Un thriller colto.", "comparables": ["Il pendolo di Foucault"],
                    "audience": "lettori di thriller storici", "risks": ["troppa teoria"],
                    "priorities": ["più scene"]}
        if "items" in props:
            self.gruppi += 1
            chiavi = props["items"]["items"]["properties"]["key"]["enum"]
            fuori = {"items": [{
                "key": k, "score": self.voti.get(k, self.base), "verdict": f"giudizio {k}",
                "evidence": "una frase",
                "problems": [{"problem": f"problema di {k}", "quote": self.citazione,
                              "fix": "correggi", "severity": "importante", "section": 1}]
                if self.voti.get(k, self.base) < 7 else []} for k in chiavi]}
            if "authors" in props:
                fuori["authors"] = [{"author": "Umberto Eco", "why": "erudizione in trama"}]
            return fuori
        return {}

    def chat(self, messages, **kw):
        self.riscritture.append(messages[-1]["content"])
        return "Nicolò sfila la pagina, la piega due volte e la infila nel taccuino."


CAPITOLO = ("# Capitolo 1: La pagina\n\n## L'archivio\n\n"
            "Nicolò entra nell'archivio alle sei e firma il registro dell'archivista.\n\n"
            "Il sistema archivia tutto, anche le persone, anche lui, anche il tempo stesso.\n\n"
            "Marie-Elise gli porge il fascicolo e torna al suo tavolo senza guardarlo.\n")


# --------------------------------------------------------------------------- #
#  la valutazione
# --------------------------------------------------------------------------- #
def test_a_chapter_gets_a_score_per_criterion_with_evidence() -> None:
    v = ql.evaluate_text(Valutatore(base=7.5, voti={"potenziale": 2.0}), CAPITOLO, chapter=1,
                         title="La pagina", kind="romanzo", budget=60, language="it")
    chiavi = [s.key for s in v.scores]
    assert {"avanzamento", "ripetizioni", "continuita", "stile", "leggibilita", "godibilita",
            "attenzione", "argomentazioni", "coerenza", "potenziale", "misura"} <= set(chiavi)
    assert chiavi.index("avanzamento") < chiavi.index("stile"), "sempre nello stesso ordine"
    assert v.score("leggibilita").measure.startswith("Gulpease")
    assert v.score("misura").score is not None
    assert v.global_score is not None and v.global_score > 7, "il potenziale non fa media"
    assert v.authors and v.authors[0]["author"] == "Umberto Eco"


def test_a_manual_is_not_judged_on_characters() -> None:
    giudice = Valutatore()
    v = ql.evaluate_text(giudice, CAPITOLO, kind="manuale", language="it")
    assert v.score("personaggi") is None
    assert "manuale non deve avere suspense" in giudice.sistemi[0]
    assert v.score("avanzamento").label == "Avanzamento dell'argomento"


def test_acceptance_needs_the_average_and_every_critical_criterion() -> None:
    soglie = ql.Thresholds(min_global=7.0, min_critical=6.0)
    buono = ql.evaluate_text(Valutatore(base=8.0), CAPITOLO, kind="romanzo")
    assert buono.accepted(soglie)
    critico_basso = ql.evaluate_text(Valutatore(base=8.0, voti={"continuita": 5.0}), CAPITOLO,
                                     kind="romanzo")
    assert critico_basso.global_score >= 7 and not critico_basso.accepted(soglie)
    assert [s.key for s in critico_basso.failing(soglie)] == ["continuita"]


# --------------------------------------------------------------------------- #
#  la correzione: un problema alla volta, solo sulla sua parte
# --------------------------------------------------------------------------- #
def test_a_problem_with_a_quote_rewrites_only_that_paragraph() -> None:
    giudice = Valutatore()
    v = ql.evaluate_text(Valutatore(base=8, voti={"ripetizioni": 5.0},
                                    citazione="Il sistema archivia tutto"), CAPITOLO,
                         kind="romanzo")
    lavori = ql.plan_fixes(v, ql.Thresholds())
    assert len(lavori) == 1 and lavori[0].criterion == "ripetizioni"
    nuovo = ql.apply_fixes(giudice, CAPITOLO, lavori, kind="romanzo", language="italiano")
    assert "Il sistema archivia tutto" not in nuovo
    assert "Nicolò sfila la pagina" in nuovo
    assert "firma il registro dell'archivista" in nuovo and "torna al suo tavolo" in nuovo, \
        "il resto del capitolo non si tocca"
    assert giudice.localizzati == 0, "la citazione basta a trovare la parte"
    assert "LA PARTE DA RISCRIVERE" in giudice.riscritture[0]
    assert "Il paragrafo prima (non riscriverlo)" in giudice.riscritture[0]


def test_a_problem_without_a_quote_asks_where_and_still_touches_one_part() -> None:
    giudice = Valutatore()
    v = ql.evaluate_text(Valutatore(base=8, voti={"attenzione": 4.0}), CAPITOLO, kind="romanzo")
    lavori = ql.plan_fixes(v, ql.Thresholds())
    nuovo = ql.apply_fixes(giudice, CAPITOLO, lavori, kind="romanzo", language="italiano")
    assert giudice.localizzati == 1
    assert "Il sistema archivia tutto" not in nuovo, "il paragrafo 2 della sezione 1"
    assert "firma il registro" in nuovo and "torna al suo tavolo" in nuovo
    assert lavori[0].done and "riscritta la parte" in lavori[0].outcome


def test_a_rewrite_that_blows_up_the_part_is_refused() -> None:
    class Gonfia(Valutatore):
        def chat(self, messages, **kw):
            return " ".join(["parola"] * 400)

    v = ql.evaluate_text(Valutatore(base=8, voti={"stile": 4.0},
                                    citazione="Il sistema archivia tutto"), CAPITOLO,
                         kind="romanzo")
    lavori = ql.plan_fixes(v, ql.Thresholds())
    nuovo = ql.apply_fixes(Gonfia(), CAPITOLO, lavori, kind="romanzo", language="italiano")
    assert "Il sistema archivia tutto" in nuovo and not lavori[0].done
    assert "scartata" in lavori[0].outcome


def _sequenza(*voti: float):
    """Una valutazione per chiamata, con il globale che segue la sequenza."""
    resto = list(voti)

    def valuta(testo: str) -> ql.Evaluation:
        voto = resto.pop(0) if resto else voti[-1]
        return ql.evaluate_text(Valutatore(base=voto, citazione="Il sistema archivia tutto"),
                                testo, kind="romanzo")
    return valuta


def test_the_loop_stops_when_the_quality_is_accepted() -> None:
    salvate: list[str] = []
    _testo, r = ql.improve_chapter(Valutatore(), CAPITOLO, _sequenza(6.0, 7.5),
                                   thresholds=ql.Thresholds(max_rounds=3), kind="romanzo",
                                   language="italiano", rounds=3,
                                   save=lambda t, v, e: salvate.append(e))
    assert r.accepted and r.before == 6.0 and r.after == 7.5
    assert salvate == ["giro 1"] and not r.needs_authorization


def test_a_round_that_does_not_improve_keeps_the_previous_version() -> None:
    salvate: list[str] = []
    testo, r = ql.improve_chapter(Valutatore(), CAPITOLO, _sequenza(6.0, 6.1),
                                  thresholds=ql.Thresholds(), kind="romanzo",
                                  language="italiano", rounds=3,
                                  save=lambda t, v, e: salvate.append(e))
    assert testo == CAPITOLO and not salvate and not r.changed
    assert "tenuta la versione prima" in r.rounds[-1] and not r.needs_authorization


def test_beyond_the_round_limit_the_author_decides() -> None:
    _testo, r = ql.improve_chapter(Valutatore(), CAPITOLO, _sequenza(5.0, 5.5, 6.0),
                                   thresholds=ql.Thresholds(), kind="romanzo",
                                   language="italiano", rounds=2)
    assert not r.accepted and r.needs_authorization, "migliora ancora: si chiede il permesso"
    _testo, zero = ql.improve_chapter(Valutatore(), CAPITOLO, _sequenza(5.0),
                                      thresholds=ql.Thresholds(), kind="romanzo",
                                      language="italiano", rounds=0)
    assert zero.rounds == [] and not zero.changed


# --------------------------------------------------------------------------- #
#  il motore: versioni, libro intero, file esterni
# --------------------------------------------------------------------------- #
def _libro(tmp_path: Path, giudice) -> Forge:
    f = Forge(tmp_path / "book", AppConfig(), client=giudice)
    f.start()
    f.state.notebook["form"] = "romanzo"
    f.state.outline = [{"number": 1, "title": "La pagina", "words": 60},
                       {"number": 2, "title": "La porta", "words": 60}]
    f.project.chapter_path(1, "La pagina").write_text(CAPITOLO, encoding="utf-8")
    f.project.chapter_path(2, "La porta").write_text(CAPITOLO.replace("La pagina", "La porta"),
                                                    encoding="utf-8")
    f.save()
    return f


def test_every_kept_version_is_saved_and_can_be_restored(tmp_path: Path, monkeypatch) -> None:
    f = _libro(tmp_path, Valutatore(base=6.0, citazione="Il sistema archivia tutto"))
    f.state.notebook["form"] = "saggio"  # un romanzo passa dalla revisione completa
    voti = iter([6.0, 7.6])
    monkeypatch.setattr(f, "_evaluate_text", lambda testo, *a, **k: ql.evaluate_text(
        Valutatore(base=next(voti, 7.6), citazione="Il sistema archivia tutto"), testo,
        kind="romanzo"))
    r = f.improve_chapter_quality(1)
    assert r.accepted
    file = f.project.chapter_path(1, "La pagina")
    versioni = ql.versions(f.project, file.stem)
    assert [v.name.split("_")[0] for v in versioni] == ["v0", "v1"]
    assert "originale" in versioni[0].name
    assert versioni[0].read_text(encoding="utf-8") == CAPITOLO
    assert "Nicolò sfila la pagina" in file.read_text(encoding="utf-8")
    ql.restore(f.project, file, versioni[0])
    assert file.read_text(encoding="utf-8") == CAPITOLO
    assert len(ql.versions(f.project, file.stem)) == 3, "quella tolta resta salvata"
    assert len(ql.load_history(f.project, file.stem)) == 2


def test_the_book_reuses_the_evaluation_of_unchanged_chapters(tmp_path: Path) -> None:
    giudice = Valutatore(base=7.2)
    f = _libro(tmp_path, giudice)
    libro, capitoli = f.evaluate_book_quality()
    gruppi = giudice.gruppi
    assert len(capitoli) == 2 and gruppi == 2 * len(ql.GROUPS)
    assert libro.score("arco").score == 6.5 and libro.comparables
    assert libro.score("potenziale").score == 5.0, "il potenziale del libro, non la media"
    f.evaluate_book_quality()
    assert giudice.gruppi == gruppi, "nessun capitolo è cambiato: niente da rivalutare"
    caricato, _ = ql.load_book(f.project)
    assert caricato.summary == "Un thriller colto."


def test_any_file_is_evaluated_on_a_copy(tmp_path: Path) -> None:
    from types import SimpleNamespace

    sorgente = tmp_path / "manoscritto.md"
    sorgente.write_text("# Primo\n\nTesto del primo capitolo.\n\n# Secondo\n\nTesto del secondo.",
                        encoding="utf-8")
    ppaths = SimpleNamespace(root=tmp_path / "progetto", db=tmp_path / "p.db",
                             vectors=tmp_path / "v")
    f = open_quality_workspace(ppaths, AppConfig(), sorgente, kind="saggio")
    assert f.project.root == tmp_path / "progetto" / "valutazioni" / "manoscritto"
    assert len(f.project.written_chapters()) == 2
    assert f.quality_kind() == "saggio"
    assert sorgente.read_text(encoding="utf-8").startswith("# Primo"), "il file non si tocca"


def test_the_thresholds_and_the_round_limit_come_from_the_settings() -> None:
    cfg = AppConfig()
    assert (cfg.quality.min_global, cfg.quality.min_critical, cfg.quality.max_rounds) == \
        (7.0, 6.0, 3)
    with pytest.raises(ValueError):
        AppConfig.model_validate({"quality": {"min_global": 11}})


def test_jev_can_point_to_a_compatible_local_service() -> None:
    from bookwriter.config import JEV_DEFAULT_URL, ResearchSettings
    from bookwriter.forge import jev

    assert ResearchSettings().jev_url == JEV_DEFAULT_URL == jev.URL
    assert jev.session(ResearchSettings(jev_enabled=True)) is None or jev.api_key_for(
        ResearchSettings()), "remoto senza chiave: spento"
    locale = jev.session(ResearchSettings(jev_enabled=True, jev_url="http://127.0.0.1:9000/v1/j",
                                          jev_model="giudice-locale", jev_parallel=2))
    assert locale is not None, "un servizio locale non chiede la chiave"
    assert locale.client.url.startswith("http://127.0.0.1") and locale.client.model == \
        "giudice-locale" and locale.parallel == 2


def test_the_measures_handle_serial_formulas() -> None:
    """La prova v4 si è fermata qui: le formule seriali sono un dizionario, non coppie."""
    testo = "\n\n".join("It was not a door. It was a verdict." for _ in range(4))
    blocco, dati = ql.measures_block(testo, language="en", budget=100)
    assert "formule retoriche in serie" in blocco and dati["misura"]
