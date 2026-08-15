"""Test della struttura tipografica: si traduce il testo, l'impaginazione resta.

Le tre cose che devono valere sempre: al modello arriva solo il testo, la struttura ritorna
intatta nel file finito, e il file finito si può chiedere in DOCX, PDF o Markdown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bookwriter.layout import (
    BLANK,
    HEADING,
    PAGEBREAK,
    PARAGRAPH,
    DocumentLayout,
    Segment,
    extract_layout,
    layout_to_markdown,
    write_layout,
)
from bookwriter.layout import inline
from bookwriter.layout.model import layout_from_text, unmark
from bookwriter.text_utils import normalize_text


def _libretto() -> DocumentLayout:
    return DocumentLayout(
        segments=[
            Segment(kind=HEADING, text="Diario delle vacanze", level=1),
            Segment(kind=HEADING, text="Capitolo primo", level=2),
            Segment(kind=PARAGRAPH, text="Era una notte **buia** e tempestosa."),
            Segment(kind=PAGEBREAK),
            Segment(kind=BLANK),
            Segment(kind=PARAGRAPH, text="Il giorno dopo pioveva."),
        ],
        source_format="docx",
        title="Diario",
    )


def test_only_the_text_reaches_the_model() -> None:
    """Interruzioni di pagina e pagine bianche non sono testo: non si traducono."""
    libro = _libretto()
    testo = libro.to_text()
    assert testo.count("\n\n") == 3, "quattro segmenti di testo, tre giunzioni"
    assert "Era una notte **buia** e tempestosa." in testo
    # I quattro segmenti testuali sono gli unici ad avere una posizione nel testo.
    assert sorted(libro.spans()) == [0, 1, 2, 5]


def test_the_text_given_to_the_model_is_already_normalised() -> None:
    """Se il testo del libro cambiasse passando dalla normalizzazione, gli offset scalerebbero."""
    testo = _libretto().to_text()
    assert normalize_text(testo) == testo


def test_headings_travel_with_their_marks_and_come_back_clean() -> None:
    """Nel testo il titolo porta i cancelletti (il capitolo si riconosce), nel libro no."""
    libro = _libretto()
    assert libro.to_text().startswith("# Diario delle vacanze\n\n## Capitolo primo")
    assert unmark("## Chapter One", HEADING) == "Chapter One"
    rifatto = libro.replace_texts({0: "# Holiday Diary", 1: "## Chapter One"})
    assert [s.text for s in rifatto.segments[:2]] == ["Holiday Diary", "Chapter One"]
    assert [s.level for s in rifatto.segments[:2]] == [1, 2]


def test_a_chunk_finds_the_segments_it_covered() -> None:
    """È così che il testo elaborato ritrova il proprio posto: per posizione, non a occhio."""
    libro = _libretto()
    inizio, _fine = libro.spans()[1]
    _inizio2, fine = libro.spans()[2]
    assert libro.segments_in_span(inizio, fine) == [1, 2]


def test_a_paragraph_split_in_two_becomes_two_paragraphs() -> None:
    """Se l'elaborazione divide un capoverso, il libro ne ha due — non uno con un buco dentro."""
    libro = _libretto()
    rifatto = libro.replace_texts({2: ["Prima metà.", "Seconda metà."]})
    testi = [s.text for s in rifatto.segments if s.kind == PARAGRAPH]
    assert testi == ["Prima metà.", "Seconda metà.", "Il giorno dopo pioveva."]
    # E lo stacco di pagina è ancora al suo posto, dopo i due capoversi.
    assert [s.kind for s in rifatto.segments] == [
        HEADING, HEADING, PARAGRAPH, PARAGRAPH, PAGEBREAK, BLANK, PARAGRAPH
    ]


def test_an_emptied_paragraph_leaves_no_hole() -> None:
    """Un capoverso che l'elaborazione ha fuso col precedente sparisce, non resta vuoto."""
    rifatto = _libretto().replace_texts({2: ""})
    assert all(s.text.strip() for s in rifatto.segments if s.kind == PARAGRAPH)
    assert len([s for s in rifatto.segments if s.kind == PARAGRAPH]) == 1


def test_bold_and_italic_survive_the_notation() -> None:
    """Il corsivo di un titolo di libro non deve sparire nella traduzione."""
    markup = inline.wrap(" molto buio ", bold=True)
    assert markup == " **molto buio** ", "gli spazi restano fuori dai marcatori"
    assert inline.parse("Era **buio** e *strano*") == [
        ("Era ", False, False), ("buio", True, False), (" e ", False, False), ("strano", False, True)
    ]
    assert inline.to_plain("Era **buio**") == "Era buio"
    assert inline.to_html("Era **buio**") == "Era <b>buio</b>"


@pytest.mark.parametrize("fmt", ["md", "docx", "pdf"])
def test_the_book_can_be_asked_in_any_of_the_three_formats(tmp_path: Path, fmt: str) -> None:
    out = write_layout(_libretto(), tmp_path / f"libro.{fmt}", fmt)
    assert out.exists() and out.stat().st_size > 0


def test_markdown_keeps_the_levels_and_the_breaks() -> None:
    md = layout_to_markdown(_libretto())
    assert md.startswith("# Diario delle vacanze")
    assert "## Capitolo primo" in md
    assert "---" in md, "l'interruzione di pagina resta visibile"


def test_a_plain_text_still_gets_a_structure() -> None:
    """Anche su un txt senza formattazione i titoli vanno riconosciuti."""
    libro = layout_from_text("# Titolo\n\nUn paragrafo.\n\n## Capitolo 1\n\nUn altro.")
    assert [(s.kind, s.level) for s in libro.segments] == [
        (HEADING, 1), (PARAGRAPH, 0), (HEADING, 2), (PARAGRAPH, 0)
    ]


# --------------------------------------------------------------------------- #
#  DOCX: il caso in cui si può conservare davvero tutto
# --------------------------------------------------------------------------- #
docx = pytest.importorskip("docx", reason="python-docx è un extra opzionale")


def _docx_di_prova(path: Path) -> Path:
    documento = docx.Document()
    documento.add_heading("Diary of My Holidays", level=1)
    paragrafo = documento.add_paragraph("It was a dark night. ")
    paragrafo.add_run("Very dark").bold = True
    paragrafo.add_run(" indeed.")
    documento.add_page_break()
    secondo = documento.add_paragraph("Second chapter opens here.")
    secondo.runs[0].italic = True
    tabella = documento.add_table(rows=1, cols=2)
    tabella.cell(0, 0).text = "Name"
    tabella.cell(0, 1).text = "Value"
    documento.save(str(path))
    return path


def test_a_docx_is_read_down_to_its_bold_and_its_page_breaks(tmp_path: Path) -> None:
    libro = extract_layout(_docx_di_prova(tmp_path / "src.docx"))
    tipi = [s.kind for s in libro.segments]
    assert HEADING in tipi and PAGEBREAK in tipi
    assert any("**Very dark**" in s.text for s in libro.segments), "il grassetto è stato letto"
    assert any(s.text.startswith("*Second chapter") for s in libro.segments), "e il corsivo pure"
    # Anche il testo dentro alla tabella è testo da tradurre.
    assert [s.text for s in libro.segments if s.ref and s.ref.startswith("t")] == ["Name", "Value"]


def test_translating_a_docx_leaves_the_book_looking_the_same(tmp_path: Path) -> None:
    """La prova del nove: dentro c'è un'altra lingua, tutto il resto è com'era."""
    origine = _docx_di_prova(tmp_path / "src.docx")
    libro = extract_layout(origine)
    tradotto = libro.replace_texts(
        {
            i: {
                "Diary of My Holidays": "Diario delle mie vacanze",
                "It was a dark night. **Very dark** indeed.": "Era una notte buia. **Molto buia** davvero.",
                "*Second chapter opens here.*": "*Qui si apre il secondo capitolo.*",
                "Name": "Nome",
                "Value": "Valore",
            }[s.text]
            for i, s in enumerate(libro.segments)
            if s.has_text
        }
    )
    uscita = write_layout(tradotto, tmp_path / "out.docx", "docx")

    riletto = docx.Document(str(uscita))
    stili = [p.style.name for p in riletto.paragraphs if p.text.strip()]
    assert stili[0].startswith("Heading"), "il titolo è rimasto un titolo"
    testi = [p.text for p in riletto.paragraphs]
    assert "Era una notte buia. Molto buia davvero." in testi
    # Il grassetto è finito sulle parole tradotte, non su quelle di partenza.
    corpo = next(p for p in riletto.paragraphs if p.text.startswith("Era una notte"))
    assert [(r.text, bool(r.bold)) for r in corpo.runs] == [
        ("Era una notte buia. ", False), ("Molto buia", True), (" davvero.", False)
    ]
    corsivo = next(p for p in riletto.paragraphs if "secondo capitolo" in p.text)
    assert all(r.italic for r in corsivo.runs if r.text)
    # Interruzione di pagina e tabella non sono passate dal modello e sono ancora lì.
    from bookwriter.layout.extract import _has_page_break

    assert any(_has_page_break(p) for p in riletto.paragraphs)
    assert [[c.text for c in r.cells] for t in riletto.tables for r in t.rows] == [["Nome", "Valore"]]


def test_each_kind_of_run_keeps_its_own_character(tmp_path: Path) -> None:
    """Il corsivo aveva corpo e colore suoi: deve ritrovarli, non ereditare quelli del testo."""
    from docx.shared import Pt, RGBColor

    origine = tmp_path / "colori.docx"
    documento = docx.Document()
    paragrafo = documento.add_paragraph("Testo normale e poi ")
    citazione = paragrafo.add_run("un titolo di libro")
    citazione.italic = True
    citazione.font.size = Pt(14)
    citazione.font.color.rgb = RGBColor(0x33, 0x33, 0x99)
    paragrafo.add_run(" che chiude.")
    documento.save(str(origine))

    libro = extract_layout(origine)
    indice = next(i for i, s in enumerate(libro.segments) if s.has_text)
    tradotto = libro.replace_texts({indice: "Plain text and then *a book title* to close."})
    uscita = write_layout(tradotto, tmp_path / "out.docx", "docx")

    run = next(
        r for r in docx.Document(str(uscita)).paragraphs[0].runs if r.text == "a book title"
    )
    assert run.italic and run.font.size == Pt(14)
    assert str(run.font.color.rgb) == "333399"


def test_a_docx_can_also_be_handed_back_as_pdf_or_markdown(tmp_path: Path) -> None:
    """Il formato di uscita è una scelta, non una conseguenza del formato di partenza."""
    libro = extract_layout(_docx_di_prova(tmp_path / "src.docx"))
    for fmt in ("pdf", "md"):
        out = write_layout(libro, tmp_path / f"out.{fmt}", fmt)
        assert out.exists() and out.stat().st_size > 0
    testo = (tmp_path / "out.md").read_text(encoding="utf-8")
    assert "**Very dark**" in testo and "---" in testo


# --------------------------------------------------------------------------- #
#  impaginazione del PDF che non è testo
# --------------------------------------------------------------------------- #
def _riga(testo: str, *, corpo: float = 10.0, larghezza: float = 400.0, blocco: int = 0):
    from bookwriter.layout.extract import _Riga

    return _Riga(text=testo, plain=testo, size=corpo, bold=False, width=larghezza, block=blocco)


def test_running_titles_that_change_per_chapter_are_recognised() -> None:
    """Il titolo corrente cambia a ogni capitolo: nessuno arriva alla soglia sull'intero libro."""
    from bookwriter.layout.extract import titoli_correnti_di_bordo

    pagine = []
    for capitolo, titolo in (("uno", "L'OFFERTA"), ("due", "IL PROTOCOLLO")):
        for n in range(4):
            pagine.append([
                _riga(titolo),
                _riga(f"Testo del capitolo {capitolo}, pagina {n}, che prosegue il racconto."),
            ])
    ricorrenti = titoli_correnti_di_bordo(pagine)

    assert set(ricorrenti) == {"L'OFFERTA", "IL PROTOCOLLO"}
    assert not any("Testo del capitolo" in t for t in ricorrenti), "la prosa non è impaginazione"


def test_a_recurring_line_inside_the_page_is_not_a_running_title() -> None:
    """Un ritornello in mezzo alla pagina è del racconto: si tocca solo quello al bordo."""
    from bookwriter.layout.extract import titoli_correnti_di_bordo

    pagine = [
        [_riga(f"Apertura diversa numero {n}"), _riga("«E allora?»"), _riga(f"Chiusura {n}")]
        for n in range(6)
    ]
    assert titoli_correnti_di_bordo(pagine) == {}


def test_the_page_number_goes_and_the_text_stays() -> None:
    from bookwriter.layout.extract import _senza_numero_di_pagina

    righe = [_riga("42"), _riga("Il viaggiatore arrivò alla porta."), _riga("XIV")]
    rimaste = _senza_numero_di_pagina(righe)
    assert [r.plain for r in rimaste] == ["Il viaggiatore arrivò alla porta."]
    # Una data o un orario non sono numeri di pagina.
    intatte = _senza_numero_di_pagina([_riga("Ore 19:00"), _riga("Cominciò a piovere.")])
    assert len(intatte) == 2


# --------------------------------------------------------------------------- #
#  caratteri: ogni scrittura deve avere il suo, o il libro esce a quadratini
# --------------------------------------------------------------------------- #
def test_every_script_of_the_queue_has_a_font() -> None:
    """Le lingue in lavorazione devono poter essere stampate, non solo salvate in testo."""
    from bookwriter.layout import fonts

    campioni = {
        "russo": "Была тёмная ночь",
        "cinese": "地狱之上是漆黑的夜晚",
        "giapponese": "地獄の上は暗く嵐の夜だった",
        "hindi": "नरक के ऊपर अंधेरी रात",
        "ebraico": "היה לילה חשוך",
        "arabo": "كانت ليلة مظلمة",
        "thai": "คืนนั้นมืดมิด",
        "ungherese": "tűz, kőház, őrült",
        "geroglifici": "\U000130AA\U0001330F\U000132AE",
        "traslitterazione": "wbn rꜥ m ꜣḫt ḥtp ḏd",
    }
    for lingua, testo in campioni.items():
        assert not fonts.missing_scripts(testo), f"{lingua}: nessun carattere per {fonts.missing_scripts(testo)}"


def test_latin_after_another_script_does_not_vanish() -> None:
    """Il difetto peggiore: dopo una parola in geroglifico spariva il resto della frase.

    Il latino veniva contato fra i segni «che seguono il vicino», così tutta la coda della frase
    finiva nel carattere egizio, che le lettere latine non le ha: testo perso senza un avviso.
    """
    from bookwriter.layout import fonts

    testo = "Il dio \U0001320B\U00013207\U00013216 (imn) si legge «Amon»."
    tratti = fonts._tratti(testo)
    ricomposto = "".join(t for _s, t in tratti)
    assert ricomposto == testo, "nessun carattere si perde nella divisione in tratti"
    scritture = {s for s, _t in tratti}
    assert "egyptian" in scritture and "latin" in scritture
    # E le lettere latine non devono finire nel carattere dei geroglifici.
    egizio = "".join(t for s, t in tratti if s == "egyptian")
    assert "imn" not in egizio and "Amon" not in egizio


def test_punctuation_goes_where_it_can_be_drawn() -> None:
    """Una parentesi affidata al carattere dei geroglifici sparirebbe: quel font non ce l'ha."""
    from bookwriter.layout import fonts

    tratti = fonts._tratti("\U0001320B (imn)")
    per_scrittura = {s: t for s, t in tratti}
    assert "(" not in per_scrittura.get("egyptian", ""), "la parentesi passa al latino"
    # Il punto ideografico invece resta col cinese, che sa disegnarlo.
    tratti_cinesi = fonts._tratti("地狱之上。")
    assert all(s == "han" for s, _t in tratti_cinesi)


def test_the_pdf_embeds_a_font_for_each_script(tmp_path: Path) -> None:
    """Prova sul file vero: i caratteri devono finire dentro al PDF, non essere solo scelti."""
    fitz = pytest.importorskip("fitz", reason="PyMuPDF serve a rileggere il PDF")

    libro = DocumentLayout(segments=[
        Segment(kind=PARAGRAPH, text="Italiano con ő ungherese"),
        Segment(kind=PARAGRAPH, text="地狱之上"),
        Segment(kind=PARAGRAPH, text="\U000130AA\U0001330F\U000132AE"),
        Segment(kind=PARAGRAPH, text="wbn rꜥ m ꜣḫt"),
    ], title="Prova")
    out = write_layout(libro, tmp_path / "scritture.pdf", "pdf")

    documento = fitz.open(out)
    incorporati = " ".join(f[3] for f in documento[0].get_fonts())
    documento.close()
    assert "EgyptianHieroglyphs" in incorporati, "manca il carattere dei geroglifici"
    assert any(n in incorporati for n in ("YaHei", "YuGothic", "SimSun")), "manca quello cinese"
    assert "Arial" in incorporati, "manca quello del latino esteso"
