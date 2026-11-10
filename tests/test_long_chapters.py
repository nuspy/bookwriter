"""Parte C: il budget di parole si legge giusto, e un capitolo lungo si scrive a sezioni.

Il caso vero: «15 capitoli base, ~7.000 parole ciascuno (~105.000 parole totali)» finiva in capitoli
da cinquecento parole, perché il primo numero vicino a «parole» era preso per il totale, e perché
una risposta del modello non basta per settemila parole.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from bookwriter.config import AppConfig, EmbeddingConfig, LLMConfig
from bookwriter.forge import spec as sp
from bookwriter.forge import writing as wr
from bookwriter.forge.engine import Forge
from bookwriter.llm.client import LMStudioClient
from tests.test_forge import FintoModello, _pronto_a_scrivere, _quaderno_pieno

FRASE_UTENTE = ("15 capitoli base, ~7.000 parole ciascuno (~105.000 parole totali); eventuale "
                "espansione a 16-18 capitoli")


# --------------------------------------------------------------------------- #
#  C1 — il budget
# --------------------------------------------------------------------------- #
def test_the_exact_sentence_of_the_author_means_105000_words() -> None:
    assert sp.parse_words(FRASE_UTENTE) == 105_000
    b = sp.parse_budget(FRASE_UTENTE)
    assert (b.total, b.per_chapter, b.chapters) == (105_000, 7_000, 15)


@pytest.mark.parametrize("frase, capitoli, atteso", [
    ("55.000 parole ≈ 190 pagine", None, 55_000),                 # i casi di sempre
    ("circa 80000 parole", None, 80_000),
    ("100 pagine", None, 100),
    ("~7.000 parole ciascuno, 12 capitoli", None, 84_000),         # per capitolo × capitoli nominati
    ("7.000 parole a capitolo", 15, 105_000),                      # × capitoli chiesti
    ("circa 5,000 words per chapter, 10 chapters", None, 50_000),
    ("each chapter 4.000 words", 8, 32_000),
    ("105.000 parole in tutto", None, 105_000),                    # totali espliciti
    ("105,000 words in total", None, 105_000),
    ("105 000 parole complessive", None, 105_000),
    ("105k parole totali", None, 105_000),
    ("105 mila parole totali", None, 105_000),
    ("totale: 105.000 parole, 7.000 per capitolo", None, 105_000),
    ("7'000 parole ciascuno, in tutto 98'000 parole", None, 98_000),
])
def test_word_budget_readings(frase: str, capitoli: int | None, atteso: int) -> None:
    assert sp.parse_words(frase, capitoli) == atteso


def test_a_chapter_length_without_a_chapter_count_is_not_the_total() -> None:
    assert sp.parse_words("~7.000 parole ciascuno") is None
    assert sp.parse_budget("~7.000 parole ciascuno").per_chapter == 7_000


def _con_lunghezza(tmp_path: Path, frase: str, **kw) -> Forge:
    f = Forge(tmp_path / "book", AppConfig(), client=FintoModello(**kw))
    f.start()
    _quaderno_pieno(f.state)
    f.state.notebook["length"] = frase
    return f


def test_the_outline_respects_the_declared_chapter_length(tmp_path: Path) -> None:
    f = _con_lunghezza(tmp_path, FRASE_UTENTE, chapters=15, words=480)
    f.make_spec()
    assert len(f.state.outline) == 15
    assert all(abs(c["words"] - 7000) <= 2100 for c in f.state.outline), \
        [c["words"] for c in f.state.outline]
    assert 90_000 <= sum(c["words"] for c in f.state.outline) <= 120_000
    testo = f.project.spec_path.read_text(encoding="utf-8")
    assert "**Obiettivo:** 105.000 parole — circa 7.000 per capitolo" in testo


def test_a_per_chapter_length_pulls_a_short_outline_back_up(tmp_path: Path) -> None:
    f = _con_lunghezza(tmp_path, "circa 6.000 parole per capitolo", chapters=4, words=500)
    f.make_spec(chapters=4)
    assert [c["words"] for c in f.state.outline] == [6000] * 4


def test_the_spec_goal_and_the_gap_with_the_notebook_are_visible(tmp_path: Path) -> None:
    from bookwriter.gui import phase_guide as pg

    f = _con_lunghezza(tmp_path, FRASE_UTENTE, chapters=15, words=7000)
    f.make_spec()
    f.go_to("spec")
    testo = pg.next_step(f, web_on=False)
    assert "Obiettivo: 105.000 parole — circa 7.000 per capitolo." in testo
    assert "Attenzione" not in testo
    # una struttura che si discosta di oltre il 20%: lo dice
    for c in f.state.outline:
        c["words"] = 1000
    testo = pg.next_step(f, web_on=False)
    assert "Attenzione: la struttura prevede 15.000 parole, ma il quaderno ne dichiara 105.000" \
        in testo


# --------------------------------------------------------------------------- #
#  C2 — capitoli a sezioni
# --------------------------------------------------------------------------- #
class ModelloCorto(FintoModello):
    """Scrive al massimo ``tetto`` parole per chiamata, come un modello che si ferma da solo."""

    def __init__(self, tetto: int = 1800, **kw) -> None:
        super().__init__(**kw)
        self.tetto = tetto
        self.sezioni: list[tuple[int, bool]] = []  # (numero di sezione, è una continuazione)
        self.prompt_sezioni: list[str] = []
        self.last_finish_reason = "stop"

    def chat(self, messages, **kw):
        sistema = messages[0]["content"]
        if "Scrivi UNA SEZIONE" not in sistema:
            return super().chat(messages, **kw)
        indice = int(re.search(r"Sezione (\d+) di \d+", sistema).group(1))
        voluto = int(re.search(r"Circa (\d+) parole", sistema).group(1))
        # una continuazione rimanda il testo già scritto come risposta dell'assistente
        continuazione = len(messages) > 2 and messages[-2]["role"] == "assistant"
        self.sezioni.append((indice, continuazione))
        self.prompt_sezioni.append(messages[1]["content"])
        n = min(self.tetto, voluto)
        # la continuazione dice altro: due blocchi identici sarebbero una ripetizione vera
        parola = f"beta{chr(96 + indice)}" if continuazione else f"alfa{chr(96 + indice)}"
        corpo = " ".join([parola] * n)
        return corpo if continuazione else f"## Titolo {indice}\n\n{corpo}"


def _forge_lungo(tmp_path: Path, *, tetto: int, budget: int = 7000, sezione: int = 1500,
                 capitoli: int = 3) -> Forge:
    cfg = AppConfig()
    cfg.writing.section_words = sezione
    f = Forge(tmp_path / "book", cfg, client=ModelloCorto(tetto, chapters=capitoli, words=900))
    f.start()
    _pronto_a_scrivere(f)
    # il percorso a sezioni è quello dei libri che argomentano: un racconto si scrive per scene
    f.state.notebook["form"] = "saggio divulgativo"
    for c in f.state.outline:
        c["words"] = budget
    return f


def test_a_long_chapter_is_written_in_sections_and_reaches_its_budget(tmp_path: Path) -> None:
    f = _forge_lungo(tmp_path, tetto=1800)
    risultato = f.write_next()
    assert risultato.sections >= 5 and not risultato.stopped
    assert risultato.words >= 0.85 * 7000, risultato.words
    testo = risultato.path.read_text(encoding="utf-8")
    assert testo.startswith("# Capitolo 1:")
    posti = [testo.index(f"## Titolo {i}") for i in range(1, risultato.sections + 1)]
    assert posti == sorted(posti), "le sezioni sono nell'ordine del piano"
    # ogni sezione riceve il testo già scritto del capitolo
    assert "alfaa" in f.client.prompt_sezioni[1]
    assert "Piano delle sezioni" in f.client.prompt_sezioni[0]
    assert not wr.partial_path(f.project, 1, "Il passaggio 1").exists()


def test_a_short_section_gets_exactly_one_continuation(tmp_path: Path) -> None:
    f = _forge_lungo(tmp_path, tetto=800)   # ogni sezione vorrebbe ~1.400 e ne esce 800
    risultato = f.write_next()
    continuazioni = [i for i, cont in f.client.sezioni if cont]
    iniziali = [i for i, cont in f.client.sezioni if not cont]
    assert continuazioni == iniziali, "una continuazione per sezione corta, non di più"
    assert len(continuazioni) == risultato.sections
    assert risultato.words >= 0.85 * 7000


def test_a_cut_section_is_continued_even_if_long_enough(tmp_path: Path) -> None:
    f = _forge_lungo(tmp_path, tetto=5000)  # lunghe quanto basta: nessuna continuazione...
    f.client.last_finish_reason = "length"  # ...ma il server dice che è stata tagliata
    f.write_next()
    assert any(cont for _, cont in f.client.sezioni)


def test_the_chapter_is_not_written_until_every_section_is(tmp_path: Path) -> None:
    f = _forge_lungo(tmp_path, tetto=1800)
    conto = {"n": 0}

    def ferma() -> bool:
        conto["n"] += 1
        return conto["n"] > 3       # si ferma prima della quarta sezione

    risultato = f.write_next(should_stop=ferma)
    assert risultato.stopped and risultato.path is None
    assert not f.project.written_chapters(), "un capitolo a metà non risulta scritto"
    assert not list(f.project.chapters_dir.glob("*.md"))
    parziale = wr.partial_path(f.project, 1, "Il passaggio 1")
    assert parziale.exists()
    fatte = [i for i, cont in f.client.sezioni if not cont]
    assert fatte == [1, 2, 3]

    # la ripresa riparte dalla sezione mancante e non riscrive le precedenti
    f.client.sezioni.clear()
    risultato = f.write_next()
    assert not risultato.stopped and risultato.path.exists()
    ripartite = [i for i, cont in f.client.sezioni if not cont]
    assert ripartite[0] == 4 and 1 not in ripartite
    testo = risultato.path.read_text(encoding="utf-8")
    assert testo.index("## Titolo 1") < testo.index("## Titolo 4")
    assert "alfaa" in testo, "le sezioni di prima sono nel capitolo"
    assert not parziale.exists()


def test_write_all_stops_in_the_middle_of_a_chapter_and_says_so(tmp_path: Path) -> None:
    f = _forge_lungo(tmp_path, tetto=1800)
    notizie: list[object] = []
    conto = {"n": 0}

    def ferma() -> bool:
        conto["n"] += 1
        return conto["n"] > 2

    report = f.write_all(should_stop=ferma, note=notizie.append)
    assert report.stopped and not report.written
    assert any(isinstance(n, tuple) and n[0] == "section" for n in notizie)
    assert not f.project.written_chapters()


def test_small_budgets_keep_the_single_call(tmp_path: Path) -> None:
    f = _forge_lungo(tmp_path, tetto=1800, budget=900)
    risultato = f.write_next()
    assert risultato.sections == 0 and risultato.path.exists()
    assert not f.client.sezioni


def test_the_plan_uses_the_key_points_when_there_are_enough(tmp_path: Path) -> None:
    f = _forge_lungo(tmp_path, tetto=1800, budget=3000, sezione=1000)   # 3 sezioni, 3 punti
    f.write_next()
    assert "Scena 1 del capitolo 1" in f.client.prompt_sezioni[0]
    assert len(f.client.sezioni) == 3


def test_the_chat_client_exposes_the_finish_reason(httpx_mock: HTTPXMock) -> None:
    import time

    c = LMStudioClient(LLMConfig(), EmbeddingConfig())
    c._caricati, c._caricati_letti_a = {c.config.models.reasoning: None}, time.monotonic()
    httpx_mock.add_response(
        url="http://localhost:1234/v1/chat/completions",
        json={"choices": [{"message": {"content": "tagliata"}, "finish_reason": "length"}]})
    assert c.chat([{"role": "user", "content": "ciao"}]) == "tagliata"
    assert c.last_finish_reason == "length"


# --------------------------------------------------------------------------- #
#  C3 — rifare i capitoli troppo corti
# --------------------------------------------------------------------------- #
def test_short_chapters_are_moved_aside_and_rewritten(tmp_path: Path) -> None:
    cfg = AppConfig()
    f = Forge(tmp_path / "book", cfg, client=FintoModello(chapters=3, words=900))
    f.start()
    _pronto_a_scrivere(f)
    f.write_all()
    percorsi = f.project.written_chapters()
    corto = percorsi[2]
    corto.write_text("# Capitolo 2: Il passaggio 2\n\n" + " ".join(["poche"] * 100) + "\n",
                     encoding="utf-8")
    intatto = percorsi[1].read_text(encoding="utf-8")
    assert [n for n, _, _ in f.short_chapters()] == [2]

    messi, report = f.redo_short_chapters()

    assert len(messi) == 1 and messi[0].exists(), "spostato, non cancellato"
    assert ".superati" in messi[0].parts
    assert "poche" in messi[0].read_text(encoding="utf-8")
    assert [c.number for c in report.written] == [2], "solo quello sotto soglia"
    assert f.project.written_chapters()[1].read_text(encoding="utf-8") == intatto
    assert not f.short_chapters()
    # la cartella di riserva non è vista come capitolo
    assert sorted(f.project.written_chapters()) == [1, 2, 3]


def test_the_writing_phase_says_when_chapters_are_far_below_budget(tmp_path: Path) -> None:
    from bookwriter.gui import phase_guide as pg

    f = Forge(tmp_path / "book", AppConfig(), client=FintoModello(chapters=3, words=900))
    f.start()
    _pronto_a_scrivere(f)
    f.write_all()
    f.go_to("writing")
    f.project.written_chapters()[1].write_text("# Capitolo 1: x\n\npoche parole\n", encoding="utf-8")
    assert "Riscrivi i capitoli troppo corti" in pg.next_step(f, web_on=False)
