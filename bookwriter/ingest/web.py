"""Ritagli dal web: pagine salvate (.html, .mhtml) e collegamenti (.url, .webloc).

Fra gli appunti di chi prepara un libro ci sono le pagine messe da parte: salvate dal browser
(«Salva pagina con nome» produce un .html o, in Chrome ed Edge, un unico .mhtml), oppure solo
ricordate con un collegamento trascinato sul desktop (.url su Windows, .webloc su macOS).

Di una pagina vera conta il testo principale, non l'incarto: menu, banner dei cookie, piè di
pagina. Quel lavoro lo fa già ``bookwriter.forge.pagetext.clean_html`` per la ricerca sul web, e
qui si riusa così com'è. Ma un file .html fra gli appunti può anche essere una nota esportata
(da Evernote, OneNote, un editor qualsiasi), fatta di righe brevi e di elenchi: passata alla
pulizia delle pagine web perderebbe proprio quelle righe, scambiate per menu. Per questo si
distingue prima la pagina dal documento (``sembra_pagina_web``) e solo la pagina si pulisce.

Di ogni ritaglio si tengono il titolo e l'indirizzo d'origine: senza, un appunto preso dal web
non si può più citare né ritrovare.

Un collegamento non contiene la pagina, solo l'indirizzo. Scaricarla vorrebbe dire uscire in
rete durante un'importazione, e la rete in questo programma la accende l'autore: per questo si
scarica solo se chi chiama passa una funzione ``scarica_pagina``; altrimenti il documento
contiene il titolo e l'indirizzo, e un avviso lo dice.
"""

from __future__ import annotations

import plistlib
import re
from collections.abc import Callable
from email import policy
from email.parser import BytesParser
from pathlib import Path

from bookwriter.ingest.errors import LoaderError
from bookwriter.layout.model import HEADING, PARAGRAPH, Segment

#: Pagine salvate e collegamenti.
WEB_EXTENSIONS = {".html", ".htm", ".mhtml", ".mht", ".url", ".webloc"}

ScaricaPagina = Callable[[str], str]

# Il commento che i browser lasciano in cima a una pagina salvata.
_SAVED_FROM = re.compile(r"saved from url=\(\d+\)\s*(\S+)", re.IGNORECASE)
_BLOCCHI = ["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre", "dt", "dd"]
# Segnaposto dei <br>: gli a capo del sorgente HTML sono spazi, quelli dei <br> no.
_A_CAPO = "\u2028"
_INCARTO = ("script", "nav", "footer", "header", "aside", "iframe", "form")


def _spazi(testo: str) -> str:
    return re.sub(r"\s+", " ", testo or "").strip()


def decodifica(dati: bytes) -> str:
    """HTML in byte → testo, rispettando il charset dichiarato (BeautifulSoup lo cerca)."""
    try:
        from bs4 import UnicodeDammit
    except ModuleNotFoundError:  # pragma: no cover - beautifulsoup4 è una dipendenza di base
        return dati.decode("utf-8", errors="replace")
    return UnicodeDammit(dati, is_html=True).unicode_markup or ""


def _meta(soup, *nomi: str) -> str:
    for nome in nomi:
        tag = soup.find("meta", attrs={"property": nome}) or soup.find("meta", attrs={"name": nome})
        if tag and tag.get("content"):
            return _spazi(tag["content"])
    return ""


def titolo_e_fonte(soup, html: str, url: str = "") -> tuple[str, str]:
    """Titolo della pagina e indirizzo d'origine, da dove la pagina li dichiara."""
    titolo = _meta(soup, "og:title", "twitter:title")
    if not titolo and soup.title and soup.title.string:
        titolo = _spazi(soup.title.string)
    if not titolo:
        h1 = soup.find("h1")
        titolo = _spazi(h1.get_text(" ")) if h1 else ""
    fonte = url
    if not fonte:
        canonico = soup.find("link", rel=lambda v: v and "canonical" in v)
        fonte = (canonico.get("href", "") if canonico else "") or _meta(soup, "og:url")
    if not fonte:
        trovato = _SAVED_FROM.search(html[:4000])
        fonte = trovato.group(1) if trovato else ""
    return titolo, fonte.strip()


def sembra_pagina_web(soup) -> bool:
    """Vero per una pagina presa dal web, falso per un documento esportato in HTML.

    Una pagina porta con sé l'incarto — script, menu, intestazioni, piè di pagina, metadati per
    i social — o è fatta in buona parte di collegamenti. Una nota esportata no.
    """
    if soup.find(list(_INCARTO)) is not None:
        return True
    if soup.find("meta", attrs={"property": re.compile(r"^og:")}) is not None:
        return True
    testo = len(_spazi(soup.get_text(" ")))
    collegato = sum(len(_spazi(a.get_text(" "))) for a in soup.find_all("a"))
    return testo > 0 and collegato > 0.3 * testo


def _livelli_dei_titoli(soup) -> dict[str, int]:
    return {_spazi(h.get_text(" ")): int(h.name[1])
            for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
            if _spazi(h.get_text(" "))}


def _blocchi_del_documento(soup) -> list[Segment]:
    """Un documento HTML letto blocco per blocco, senza togliere nulla."""
    corpo = soup.body or soup
    for tag in corpo.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    for br in corpo.find_all("br"):
        br.replace_with(_A_CAPO)
    segmenti: list[Segment] = []
    for tag in corpo.find_all(_BLOCCHI):
        if tag.find_parent(["p", "blockquote", "pre", "dd"]) is not None:
            continue  # già letto col blocco che lo contiene
        if tag.name == "li":
            # Della voce di elenco solo il suo testo: le voci annidate e i capoversi che
            # contiene si leggono per conto loro, un blocco ciascuno.
            testo = "".join(s for s in tag.find_all(string=True)
                             if s.find_parent(["li", "p"]) is tag)
        else:
            # Senza separatore: un separatore spezzerebbe le parole a metà grassetto
            # («pa<b>ro</b>la»); gli a capo dei <br> sono già diventati testo.
            testo = tag.get_text("")
        if tag.name == "pre":
            testo = testo.replace(_A_CAPO, "\n").strip()
        elif tag.name.startswith("h"):
            testo = _spazi(testo)
        else:  # gli a capo voluti (i <br> di una poesia, di un indirizzo) restano
            testo = "\n".join(r for r in (_spazi(x) for x in testo.split(_A_CAPO)) if r)
        if not testo:
            continue
        if tag.name.startswith("h") and tag.name[1:].isdigit():
            segmenti.append(Segment(kind=HEADING, text=testo, level=int(tag.name[1])))
        else:
            segmenti.append(Segment(kind=PARAGRAPH, text=f"- {testo}" if tag.name == "li" else testo,
                                    style=tag.name))
    if not segmenti:
        # Testo senza blocchi (un <div> con dei <br>): righe come capoversi.
        righe = [_spazi(r) for r in corpo.get_text("\n").replace(_A_CAPO, "\n").splitlines()]
        segmenti = [Segment(kind=PARAGRAPH, text=r) for r in righe if r]
    return segmenti


def _blocchi_della_pagina(soup, html: str, url: str) -> list[Segment]:
    """Il testo principale di una pagina web, senza incarto, con i titoli riconosciuti."""
    from bookwriter.forge.pagetext import clean_html

    livelli = _livelli_dei_titoli(soup)
    # Nessuna soglia minima: un ritaglio breve l'autore l'ha salvato apposta.
    pulito = clean_html(html, url, min_chars=0)
    segmenti: list[Segment] = []
    for blocco in pulito.text.split("\n\n"):
        blocco = blocco.strip()
        if not blocco:
            continue
        if blocco in livelli:
            segmenti.append(Segment(kind=HEADING, text=blocco, level=livelli[blocco]))
        else:
            segmenti.append(Segment(kind=PARAGRAPH, text=blocco))
    return segmenti


def segmenti_da_html(html: str, url: str = "") -> tuple[str, list[Segment]]:
    """HTML → (titolo, segmenti): il titolo in testa, l'indirizzo d'origine, poi il testo."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    titolo, fonte = titolo_e_fonte(soup, html, url)
    corpo: list[Segment] = []
    if sembra_pagina_web(soup):
        corpo = _blocchi_della_pagina(soup, html, fonte)
    if not corpo:  # documento, oppure pagina da cui la pulizia non ha salvato nulla
        corpo = _blocchi_del_documento(BeautifulSoup(html or "", "html.parser"))
    segmenti: list[Segment] = []
    # In testa il titolo, poi la fonte: se la pagina comincia già col suo titolo, è quello.
    if corpo and corpo[0].kind == HEADING and corpo[0].text == titolo:
        segmenti.append(corpo.pop(0))
    elif titolo:
        segmenti.append(Segment(kind=HEADING, text=titolo, level=1))
    if fonte:
        segmenti.append(Segment(kind=PARAGRAPH, text=f"Fonte: {fonte}", style="source"))
    segmenti.extend(corpo)
    return titolo, segmenti


# --------------------------------------------------------------------------- #
#  i file
# --------------------------------------------------------------------------- #
def leggi_html(path: Path) -> tuple[str, list[Segment]]:
    return segmenti_da_html(decodifica(path.read_bytes()))


def leggi_mhtml(path: Path) -> tuple[str, list[Segment]]:
    """Un .mhtml è un messaggio MIME: la pagina è la prima parte text/html, le immagini e i fogli
    di stile le altre. L'indirizzo d'origine sta nelle intestazioni."""
    messaggio = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
    fonte = str(messaggio.get("Snapshot-Content-Location") or "")
    for parte in messaggio.walk():
        if parte.get_content_type() != "text/html":
            continue
        try:
            html = parte.get_content()
        except (LookupError, ValueError):
            html = decodifica(parte.get_payload(decode=True) or b"")
        fonte = fonte or str(parte.get("Content-Location") or "")
        titolo, segmenti = segmenti_da_html(html, fonte)
        return titolo or str(messaggio.get("Subject") or ""), segmenti
    raise LoaderError(f"{path.name}: nessuna pagina HTML dentro l'archivio MHTML.")


def indirizzo_del_collegamento(path: Path) -> str:
    """L'indirizzo contenuto in un collegamento .url (Windows) o .webloc (macOS)."""
    if path.suffix.lower() == ".webloc":
        try:
            with path.open("rb") as f:
                dati = plistlib.load(f)  # XML o binario, li legge entrambi
        except (plistlib.InvalidFileException, ValueError, OSError) as exc:
            raise LoaderError(f"Collegamento non leggibile: {path.name} ({exc})") from exc
        return str(dati.get("URL", "")).strip() if isinstance(dati, dict) else ""
    testo = path.read_bytes().decode("utf-8", errors="replace")
    sezione = re.search(r"\[InternetShortcut\](.*?)(?:^\[|\Z)", testo,
                        re.DOTALL | re.MULTILINE | re.IGNORECASE)
    trovato = re.search(r"^\s*URL\s*=\s*(\S+)", sezione.group(1) if sezione else testo,
                        re.MULTILINE | re.IGNORECASE)
    return trovato.group(1).strip() if trovato else ""


def leggi_collegamento(path: Path, *, scarica_pagina: ScaricaPagina | None = None
                       ) -> tuple[str, list[Segment], list[str]]:
    """(titolo, segmenti, avvisi) per un collegamento: la pagina, se si può scaricarla."""
    url = indirizzo_del_collegamento(path)
    if not url:
        raise LoaderError(f"{path.name}: il collegamento non contiene un indirizzo.")
    if scarica_pagina is not None:
        try:
            html = scarica_pagina(url)
        except Exception as exc:  # noqa: BLE001 - la rete fallisce in mille modi
            html, errore = "", str(exc)
        else:
            errore = ""
        if html:
            titolo, segmenti = segmenti_da_html(html, url)
            if any(s.kind == PARAGRAPH and s.style != "source" for s in segmenti):
                return titolo or path.stem, segmenti, []
        avviso = f"{path.name}: non sono riuscito a scaricare {url}" + (f" ({errore})" if errore else "")
    else:
        avviso = (f"{path.name}: è un collegamento a {url}; la pagina non è stata scaricata, "
                  "ho importato solo titolo e indirizzo.")
    segmenti = [Segment(kind=HEADING, text=path.stem, level=1),
                Segment(kind=PARAGRAPH, text=f"Fonte: {url}", style="source")]
    return path.stem, segmenti, [avviso]
