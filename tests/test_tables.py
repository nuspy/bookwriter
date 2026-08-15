"""Test della riparazione delle tabelle Markdown."""

from __future__ import annotations

from bookwriter.tables import repair
from bookwriter.text_utils import normalize_text


def test_the_separator_of_a_table_is_not_a_dot_leader() -> None:
    """Cinque trattini in una tabella tengono in piedi la tabella; in un indice sono un fregio."""
    tabella = "| Jel | Jelentése |\n| ----- | ------- |\n| Emelkedő | Nem fejezte be |"
    assert normalize_text(tabella) == tabella, "la separatrice non si tocca"

    indice = "Capitolo 1 . . . . . . . . 12"
    assert "…" in normalize_text(indice), "i punti di guida invece sì"
    trattini = "Capitolo 2 --------- 34"
    assert "…" in normalize_text(trattini)


def test_a_ruined_separator_is_put_back() -> None:
    """È il difetto trovato nei libri: la riga separatrice ridotta a puntini."""
    rovinata = "| A | B | C |\n| … | … | … |\n| 1 | 2 | 3 |"
    fuori, esito = repair(rovinata, align=False)

    righe = fuori.split("\n")
    assert righe[1] == "|---|---|---|", "la separatrice è tornata quella giusta"
    assert esito.separatrici == 1 and esito.tabelle == 1
    assert "| 1 | 2 | 3 |" in fuori, "il contenuto non si tocca"


def test_headers_come_out_in_bold() -> None:
    fuori, esito = repair("| Jel | Jelentése |\n| … | … |\n| a | b |", align=False)
    assert fuori.startswith("| **Jel** | **Jelentése** |")
    assert esito.intestazioni == 1
    # Chi era già in grassetto non ne prende un secondo.
    ancora, _ = repair(fuori, align=False)
    assert "****" not in ancora


def test_columns_are_aligned_so_the_file_stays_readable() -> None:
    """Il file lo si apre anche in un editor semplice: le colonne allineate si leggono."""
    fuori, _ = repair("| Jel | Jelentése |\n| … | … |\n| Emelkedő hangnem | Nem |")
    righe = [r for r in fuori.split("\n") if r.startswith("|")]
    assert len({len(r) for r in righe}) == 1, "tutte le righe della stessa larghezza"
    assert "**Jel**             " in righe[0] or righe[0].count(" ") > 4


def test_a_row_missing_a_cell_is_completed() -> None:
    """Una riga con meno celle dell'intestazione rompe la tabella in molti lettori."""
    fuori, _ = repair("| A | B | C |\n| … | … | … |\n| 1 | 2 |", align=False)
    assert "| 1 | 2 |  |" in fuori


def test_a_table_that_never_had_a_separator_gets_one() -> None:
    fuori, esito = repair("| A | B |\n| 1 | 2 |", align=False)
    assert fuori.split("\n")[1] == "|---|---|"
    assert esito.separatrici == 1
    assert "| 1 | 2 |" in fuori


def test_a_healthy_table_is_left_alone() -> None:
    sana = "| **A** | **B** |\n|-------|-------|\n| 1     | 2     |"
    fuori, esito = repair(sana)
    assert not esito, "niente da riparare"
    assert fuori == sana


def test_text_around_the_tables_is_untouched() -> None:
    testo = ("## Titolo\n\nUn paragrafo con | una barra | in mezzo alla frase.\n\n"
             "| A | B |\n| … | … |\n| 1 | 2 |\n\nAltro paragrafo.")
    fuori, esito = repair(testo, align=False)
    assert "## Titolo" in fuori and "Altro paragrafo." in fuori
    assert "Un paragrafo con | una barra | in mezzo alla frase." in fuori
    assert esito.tabelle == 1


def test_leftover_dots_after_a_good_separator_are_not_data() -> None:
    """Capitato davvero: separatrice sana ricostruita, e la vecchia rimasta sotto come riga."""
    testo = "| A | B |\n|---|---|\n| … | … |\n| 1 | 2 |"
    fuori, esito = repair(testo, bold_headers=False, align=False)
    assert "| … | … |" not in fuori
    assert "| 1 | 2 |" in fuori and esito.separatrici == 1


def test_a_table_row_turned_into_a_bullet_is_taken_back() -> None:
    """Senza separatrice il modello non vede più la tabella e la rende come elenco, spezzandola."""
    testo = ("| Hiba | Következmény |\n| … | … |\n"
             "| Túl kiszámított | Mesterségesnek tűnik |\n"
             "- A jelek túlelemzése | Szorongás az interakció során |\n"
             "- Elvek helyett mondatok | Képtelenség alkalmazkodni |")
    fuori, esito = repair(testo, bold_headers=False, align=False)

    righe = [r for r in fuori.split("\n") if r.strip()]
    assert all(r.startswith("|") for r in righe), "tutte le righe tornano nella tabella"
    assert "A jelek túlelemzése" in fuori and "Szorongás" in fuori
    assert esito.tabelle == 1


def test_a_real_bullet_list_is_not_swallowed() -> None:
    """Un elenco vero con una barra dentro non deve finire inghiottito nella tabella sopra."""
    testo = ("| A | B |\n| … | … |\n| 1 | 2 |\n"
             "- una voce con | una barra | e un'altra | ancora |")
    fuori, _ = repair(testo, bold_headers=False, align=False)
    assert "- una voce con | una barra |" in fuori, "ha 4 celle contro 2: resta un elenco"
