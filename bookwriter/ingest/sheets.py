"""Appunti in tabella: fogli di calcolo (.xlsx, .xls, .ods) e file di dati (.csv, .tsv).

Una cronologia degli eventi, l'elenco dei personaggi con età e luoghi, le fonti con autore e
anno: molti autori tengono questo materiale in un foglio di calcolo. Il modello però legge testo,
e una riga di tabella fuori dalla sua intestazione non dice niente («1943 · Roma · 12»). Per
questo ogni riga diventa un capoverso fatto di coppie «intestazione: valore», una per riga di
testo, e ogni foglio un titolo col suo nome: la tabella resta leggibile anche spezzata in chunk.

Le celle vuote si saltano (nessun «Note: » a vuoto), i numeri interi restano interi («12», non
«12.0»), le date si scrivono nel formato ISO. Se la prima riga non ha l'aria di
un'intestazione (contiene numeri, o celle vuote) le colonne si chiamano «Colonna 1», «Colonna 2».

.csv/.tsv e .ods si leggono con la libreria standard (l'.ods è un archivio zip di XML); .xlsx
richiede ``openpyxl`` e .xls ``xlrd``, entrambi nell'extra ``ingest``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path
from xml.etree import ElementTree

from bookwriter.ingest.errors import LoaderError, dipendenza_mancante
from bookwriter.layout.model import HEADING, PARAGRAPH, Segment

#: Fogli di calcolo e file di dati tabellari.
SHEET_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xlsm", ".xls", ".ods"}

#: Un foglio con più colonne di così si legge fino a qui: oltre, quasi sempre è formattazione.
MAX_COLONNE = 200

Progress = Callable[[int, int, str], None]
Foglio = tuple[str, list[list[object]]]  # (nome, righe di celle)


# --------------------------------------------------------------------------- #
#  dalle celle al testo
# --------------------------------------------------------------------------- #
def cella_a_testo(valore: object) -> str:
    """Il valore di una cella come lo scriverebbe una persona."""
    if valore is None:
        return ""
    if isinstance(valore, bool):
        return "sì" if valore else "no"
    if isinstance(valore, float):
        if math.isnan(valore):
            return ""
        return str(int(valore)) if valore.is_integer() else repr(valore)
    if isinstance(valore, dt.datetime):
        if (valore.hour, valore.minute, valore.second) == (0, 0, 0):
            return valore.date().isoformat()
        return valore.strftime("%Y-%m-%d %H:%M")
    if isinstance(valore, (dt.date, dt.time)):
        return valore.isoformat()
    return " ".join(str(valore).split()) if "\n" not in str(valore) else str(valore).strip()


def _numerica(testo: str) -> bool:
    try:
        float(testo.replace(",", ".").replace(" ", ""))
    except ValueError:
        return False
    return True


def _sembra_intestazione(riga: list[str], resto: list[list[str]]) -> bool:
    piene = [c for c in riga if c]
    # Qualche colonna senza nome si tollera (diventa «Colonna N»); mezza riga vuota no.
    if not resto or len(piene) < 2 or len(piene) < 0.6 * len(riga):
        return False
    return not any(_numerica(c) for c in piene) and len(set(piene)) == len(piene)


def righe_a_segmenti(righe: Iterable[Iterable[object]], *, nome: str | None = None,
                     livello: int = 2) -> list[Segment]:
    """Le righe di un foglio → titolo del foglio e un capoverso per riga.

    Con l'intestazione: «Colonna: valore», una coppia per riga di testo. Senza (una sola
    colonna, o una prima riga che non lo sembra): i valori della riga separati da « · ».
    """
    pulite: list[list[str]] = []
    for riga in righe:
        celle = [cella_a_testo(c) for c in list(riga)[:MAX_COLONNE]]
        while celle and not celle[-1]:
            celle.pop()
        if any(celle):
            pulite.append(celle)
    segmenti: list[Segment] = []
    if nome:
        segmenti.append(Segment(kind=HEADING, text=nome, level=livello))
    if not pulite:
        return segmenti
    larghezza = max(len(r) for r in pulite)
    intestazione: list[str] | None = None
    if larghezza > 1 and _sembra_intestazione(pulite[0] + [""] * (larghezza - len(pulite[0])),
                                               pulite[1:]):
        intestazione = pulite[0]
        pulite = pulite[1:]
    for riga in pulite:
        if intestazione is None:
            testo = " · ".join(c for c in riga if c)
        else:
            coppie = []
            for i, valore in enumerate(riga):
                if not valore:
                    continue
                chiave = intestazione[i] if i < len(intestazione) and intestazione[i] else f"Colonna {i + 1}"
                coppie.append(f"{chiave}: {valore}")
            testo = "\n".join(coppie)
        if testo:
            segmenti.append(Segment(kind=PARAGRAPH, text=testo, style="row"))
    return segmenti


def fogli_a_segmenti(fogli: list[Foglio], *, progress: Progress | None = None) -> list[Segment]:
    segmenti: list[Segment] = []
    for n, (nome, righe) in enumerate(fogli):
        if progress:
            progress(n, len(fogli), f"foglio «{nome}»")
        segmenti.extend(righe_a_segmenti(righe, nome=nome or f"Foglio {n + 1}"))
    return segmenti


# --------------------------------------------------------------------------- #
#  csv e tsv
# --------------------------------------------------------------------------- #
def _decodifica(dati: bytes) -> str:
    """UTF-8 (con o senza BOM) se lo è; altrimenti cp1252, quello di Excel italiano."""
    try:
        return dati.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dati.decode("cp1252", errors="replace")


def leggi_csv(path: Path) -> list[list[str]]:
    """Le righe di un .csv o .tsv. Il separatore del .csv si indovina (Excel italiano usa «;»)."""
    testo = _decodifica(path.read_bytes())
    if path.suffix.lower() == ".tsv":
        separatore = "\t"
    else:
        try:
            separatore = csv.Sniffer().sniff(testo[:65536], delimiters=",;\t|").delimiter
        except csv.Error:
            separatore = ","
    try:
        return list(csv.reader(io.StringIO(testo, newline=""), delimiter=separatore))
    except csv.Error as exc:  # una cella enorme, virgolette mai chiuse
        raise LoaderError(f"File {path.suffix.lower()} non leggibile: {path.name} ({exc})") from exc


# --------------------------------------------------------------------------- #
#  xlsx, xls, ods
# --------------------------------------------------------------------------- #
def leggi_xlsx(path: Path) -> list[Foglio]:
    try:
        import openpyxl
    except ModuleNotFoundError as exc:
        raise dipendenza_mancante("fogli Excel (.xlsx)", "openpyxl", "ingest") from exc
    try:
        # data_only: il valore calcolato delle formule, non la formula.
        libro = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # file rovinato o protetto da password
        raise LoaderError(f"Foglio Excel non leggibile: {path.name} ({exc})") from exc
    try:
        return [(foglio.title, [list(r) for r in foglio.iter_rows(values_only=True)])
                for foglio in libro.worksheets]
    finally:
        libro.close()


def leggi_xls(path: Path) -> list[Foglio]:
    try:
        import xlrd
    except ModuleNotFoundError as exc:
        raise dipendenza_mancante("fogli Excel 97-2003 (.xls)", "xlrd", "ingest") from exc
    try:
        libro = xlrd.open_workbook(str(path), on_demand=True)
    except Exception as exc:  # file rovinato o protetto da password
        raise LoaderError(f"Foglio Excel non leggibile: {path.name} ({exc})") from exc
    fogli: list[Foglio] = []
    try:
        for indice in range(libro.nsheets):
            foglio = libro.sheet_by_index(indice)
            righe = []
            for r in range(foglio.nrows):
                riga = []
                for c in range(min(foglio.ncols, MAX_COLONNE)):
                    cella = foglio.cell(r, c)
                    if cella.ctype == xlrd.XL_CELL_DATE:
                        try:
                            riga.append(xlrd.xldate_as_datetime(cella.value, libro.datemode))
                            continue
                        except (ValueError, OverflowError):
                            pass
                    if cella.ctype == xlrd.XL_CELL_BOOLEAN:
                        riga.append(bool(cella.value))
                    elif cella.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                        riga.append(None)
                    else:
                        riga.append(cella.value)
                righe.append(riga)
            fogli.append((foglio.name, righe))
            libro.unload_sheet(indice)
    finally:
        libro.release_resources()
    return fogli


_NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}
_T = f"{{{_NS['table']}}}"
_O = f"{{{_NS['office']}}}"
_X = f"{{{_NS['text']}}}"
# Le righe e le colonne vuote in fondo a un foglio ODS sono dichiarate «ripetute» fino al bordo
# (un milione di righe): oltre questa soglia una ripetizione non è contenuto.
_MAX_RIPETIZIONI = 1000


def _valore_ods(cella) -> object:
    tipo = cella.get(f"{_O}value-type")
    if tipo in ("float", "percentage", "currency"):
        try:
            return float(cella.get(f"{_O}value"))
        except (TypeError, ValueError):
            pass
    elif tipo == "boolean":
        return cella.get(f"{_O}boolean-value") == "true"
    elif tipo == "date":
        valore = cella.get(f"{_O}date-value") or ""
        try:
            return dt.datetime.fromisoformat(valore)
        except ValueError:
            return valore
    # Testo: un <text:p> per riga di testo nella cella.
    righe = [_testo_ods(p) for p in cella.findall(f"{_X}p")]
    return "\n".join(righe) or None


def _testo_ods(el) -> str:
    """Il testo di un paragrafo ODS: <text:s/> sono spazi, <text:span> pezzi formattati."""
    pezzi = [el.text or ""]
    for figlio in el:
        if figlio.tag == f"{_X}s":
            pezzi.append(" " * int(figlio.get(f"{_X}c", "1")))
        elif figlio.tag == f"{_X}tab":
            pezzi.append("\t")
        elif figlio.tag == f"{_X}line-break":
            pezzi.append("\n")
        else:
            pezzi.append(_testo_ods(figlio))
        pezzi.append(figlio.tail or "")
    return "".join(pezzi)


def leggi_ods(path: Path) -> list[Foglio]:
    """Un foglio OpenDocument letto dal suo ``content.xml``, senza dipendenze."""
    try:
        with zipfile.ZipFile(path) as archivio:
            xml = archivio.read("content.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise LoaderError(f"Foglio ODS non leggibile: {path.name} ({exc})") from exc
    radice = ElementTree.fromstring(xml)
    fogli: list[Foglio] = []
    for tabella in radice.iter(f"{_T}table"):
        righe: list[list[object]] = []
        for riga in tabella.iter(f"{_T}table-row"):
            celle: list[object] = []
            for cella in riga:
                if cella.tag not in (f"{_T}table-cell", f"{_T}covered-table-cell"):
                    continue
                valore = _valore_ods(cella) if cella.tag == f"{_T}table-cell" else None
                volte = int(cella.get(f"{_T}number-columns-repeated", "1"))
                celle.extend([valore] * min(volte, _MAX_RIPETIZIONI if valore is not None else 1))
                if len(celle) >= MAX_COLONNE:
                    break
            volte = int(riga.get(f"{_T}number-rows-repeated", "1"))
            vuota = not any(c not in (None, "") for c in celle)
            righe.extend([celle] * (1 if vuota else min(volte, _MAX_RIPETIZIONI)))
        fogli.append((tabella.get(f"{_T}name") or "", righe))
    return fogli


def leggi_foglio(path: Path, *, progress: Progress | None = None) -> list[Segment]:
    """Qualsiasi formato tabellare → segmenti. Il .csv non ha fogli: niente titolo di foglio."""
    ext = path.suffix.lower()
    if ext in (".csv", ".tsv"):
        return righe_a_segmenti(leggi_csv(path))
    if ext in (".xlsx", ".xlsm"):
        fogli = leggi_xlsx(path)
    elif ext == ".xls":
        fogli = leggi_xls(path)
    elif ext == ".ods":
        fogli = leggi_ods(path)
    else:  # pragma: no cover - lo esclude il chiamante
        raise LoaderError(f"Formato non supportato: {ext} ({path.name})")
    return fogli_a_segmenti(fogli, progress=progress)
