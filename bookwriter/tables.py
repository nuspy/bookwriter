"""Riparazione delle tabelle Markdown rovinate, e loro riformattazione.

Una tabella Markdown regge su una riga sola: la separatrice sotto l'intestazione, `|---|---|`.
Toglierla — o sostituirla con qualcos'altro — non produce una tabella brutta: produce **testo
normale con delle barre verticali dentro**, perché nessun lettore di Markdown la riconosce più.

Qui si rimette a posto quella riga, si allineano le colonne perché il file resti leggibile anche
in un editor semplice, e le intestazioni si mettono in grassetto — che nei lettori poveri è
l'unica cosa che distingue la prima riga dalle altre.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# La separatrice com'è finita dopo la normalizzazione: celle di soli puntini di sospensione.
_ROVINATA = re.compile(r"^\s*\|(?:\s*…\s*\|)+\s*$")
# La separatrice sana: trattini con eventuali due punti per l'allineamento.
_SANA = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$")


def _celle(riga: str) -> list[str]:
    """Le celle di una riga di tabella, senza le barre esterne."""
    nuda = riga.strip()
    if nuda.startswith(("- ", "* ", "+ ")):
        nuda = nuda[2:].strip()  # riga travestita da voce di elenco
    if nuda.startswith("|"):
        nuda = nuda[1:]
    if nuda.endswith("|"):
        nuda = nuda[:-1]
    return [c.strip() for c in nuda.split("|")]


def _è_riga_tabella(riga: str) -> bool:
    return riga.strip().startswith("|") and riga.strip().endswith("|") and riga.count("|") >= 2


def _è_riga_travestita(riga: str, colonne: int) -> bool:
    """Riga di tabella che il modello ha reso come voce di elenco: «- cella | cella |».

    Succede quando la tabella arriva senza separatrice: il modello non la riconosce più come
    tabella e la restituisce come elenco, spezzandola a metà. Si riassorbe solo se ha lo stesso
    numero di celle dell'intestazione, altrimenti un elenco puntato con dentro una barra
    verticale verrebbe inghiottito per sbaglio.
    """
    nuda = riga.strip()
    if not nuda.startswith(("- ", "* ", "+ ")) or "|" not in nuda:
        return False
    return len(_celle("| " + nuda[2:].rstrip("| ") + " |")) == colonne


def _solo_puntini(riga: str) -> bool:
    """Riga fatta di sole celle vuote o di puntini: è il resto di una separatrice rovinata."""
    celle = _celle(riga)
    return bool(celle) and all(c in ("…", "...", "") for c in celle)


def _in_grassetto(cella: str) -> str:
    nuda = cella.strip()
    if not nuda or nuda.startswith("**") or nuda in ("…", "-"):
        return nuda
    return f"**{nuda}**"


@dataclass
class Riparazione:
    tabelle: int = 0  # tabelle trovate
    separatrici: int = 0  # righe separatrici rimesse a posto
    intestazioni: int = 0  # intestazioni portate in grassetto

    def __bool__(self) -> bool:
        return bool(self.separatrici or self.intestazioni)

    def summary(self) -> str:
        if not self:
            return f"{self.tabelle} tabelle, nessuna da riparare"
        parti = [f"{self.tabelle} tabelle"]
        if self.separatrici:
            parti.append(f"{self.separatrici} separatrici rimesse")
        if self.intestazioni:
            parti.append(f"{self.intestazioni} intestazioni in grassetto")
        return " · ".join(parti)


def repair(markdown: str, *, bold_headers: bool = True, align: bool = True) -> tuple[str, Riparazione]:
    """Rimette in piedi le tabelle del testo. Ritorna il testo corretto e cosa è stato fatto.

    Una tabella si riconosce da un blocco di righe consecutive che cominciano e finiscono con la
    barra. La separatrice viene rifatta con tante colonne quante ne ha l'intestazione — non
    quante ne aveva la riga rovinata, che può averne perse per strada.
    """
    esito = Riparazione()
    righe = markdown.split("\n")
    fuori: list[str] = []
    i = 0
    while i < len(righe):
        if not _è_riga_tabella(righe[i]):
            fuori.append(righe[i])
            i += 1
            continue
        blocco = []
        colonne = len(_celle(righe[i]))
        while i < len(righe) and (
            _è_riga_tabella(righe[i]) or _è_riga_travestita(righe[i], colonne)
        ):
            blocco.append(righe[i])
            i += 1
        fuori.extend(_ripara_blocco(blocco, esito, bold_headers=bold_headers, align=align))
    return "\n".join(fuori), esito


def repair_layout(layout, *, bold_headers: bool = True, align: bool = True) -> Riparazione:
    """Ripara le tabelle dentro i segmenti di un libro, sul posto. Ritorna cosa ha fatto.

    Una tabella sta tutta dentro un segmento — righe consecutive senza righe vuote in mezzo —
    quindi si può riparare segmento per segmento senza toccare la struttura del libro.
    """
    esito = Riparazione()
    for segmento in layout.segments:
        if "|" not in segmento.text:
            continue
        nuovo, parziale = repair(segmento.text, bold_headers=bold_headers, align=align)
        segmento.text = nuovo
        esito.tabelle += parziale.tabelle
        esito.separatrici += parziale.separatrici
        esito.intestazioni += parziale.intestazioni
    return esito


def _ripara_blocco(
    blocco: list[str], esito: Riparazione, *, bold_headers: bool, align: bool
) -> list[str]:
    if len(blocco) < 2:
        return blocco  # una riga sola non è una tabella
    esito.tabelle += 1

    intestazione = _celle(blocco[0])
    colonne = len(intestazione)
    seconda = blocco[1].strip()
    era_separatrice = bool(_SANA.match(seconda)) and "-" in seconda
    era_rovinata = bool(_ROVINATA.match(seconda))

    corpo = blocco[2:] if (era_separatrice or era_rovinata) else blocco[1:]
    if era_rovinata:
        esito.separatrici += 1
    elif not era_separatrice:
        # Nessuna separatrice: la tabella non è mai stata valida. Se ne mette una.
        esito.separatrici += 1

    if bold_headers:
        nuova = [_in_grassetto(c) for c in intestazione]
        if nuova != intestazione:
            esito.intestazioni += 1
        intestazione = nuova

    # I resti della separatrice rovinata non sono dati: se ne va anche quello che è avanzato
    # dopo che una separatrice sana era già stata ricostruita.
    scartate = sum(1 for r in corpo if _solo_puntini(r))
    if scartate:
        esito.separatrici += 1
    corpo = [r for r in corpo if not _solo_puntini(r)]
    righe_corpo = [_celle(r) for r in corpo]
    # Tutte le righe con lo stesso numero di celle dell'intestazione: le tabelle a cui manca una
    # cella si leggono male in ogni lettore.
    righe_corpo = [(r + [""] * colonne)[:colonne] for r in righe_corpo]

    if align:
        larghezze = [
            max(len(intestazione[c]), *(len(r[c]) for r in righe_corpo)) if righe_corpo
            else len(intestazione[c])
            for c in range(colonne)
        ]
        def stendi(celle: list[str]) -> str:
            return "| " + " | ".join(c.ljust(larghezze[n]) for n, c in enumerate(celle)) + " |"
        separatrice = "|" + "|".join("-" * (l + 2) for l in larghezze) + "|"
    else:
        def stendi(celle: list[str]) -> str:
            return "| " + " | ".join(celle) + " |"
        separatrice = "|" + "|".join(["---"] * colonne) + "|"

    return [stendi(intestazione), separatrice] + [stendi(r) for r in righe_corpo]
