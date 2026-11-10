"""Dal HTML di una pagina al testo che vale la pena leggere, e se vale la pena passarlo al modello.

Una pagina scaricata non è il suo testo: è una pagina. Prima di arrivare al modello deve perdere
tutto ciò che non è lettura — banner dei cookie, muri di accesso, menu, piè di pagina, «articoli
correlati», pubblicità, elenchi di link — e **dire cosa le è stato tolto** (un conteggio per
categoria), così si può controllare. Se di leggibile non resta abbastanza (un muro di cookie, una
pagina che esiste solo con JavaScript, i soli link di piè di pagina di YouTube) la pagina è
«non leggibile»: si registra con il motivo e **non va mai al modello**.

Poi la pertinenza: una pagina può essere leggibilissima e parlare d'altro (la guida di un
videogioco che si chiama come il capitolo). La giudica il **modello** (vedi
``research.judge_page``, in qualunque lingua); qui c'è solo il confronto di parole, che serve a
misurare e a un unico scarto senza modello: stessa lingua e nessuna parola in comune.

Nessuna dipendenza oltre a BeautifulSoup, che c'è già. Le soglie stanno nelle impostazioni della
ricerca (:class:`~bookwriter.config.ResearchSettings`), non qui.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# ---- cosa si toglie per nome di tag ------------------------------------------------------------
_DROP_TAGS = ("script", "style", "noscript", "template", "nav", "footer", "header", "aside",
              "form", "iframe", "svg", "button", "canvas", "video", "audio", "dialog", "select",
              "input", "textarea", "figcaption")

# ---- cosa si toglie per nome di classe o di id (a parole intere) ---------------------------------
_BOILER_TOKENS = frozenset("""
cookie cookies consent gdpr cmp onetrust banner newsletter subscribe subscription popup modal
overlay advert advertisement ads adsense sponsor sponsored promo share sharing social breadcrumb
breadcrumbs related recommended comments comment sidebar menu navbar navigation nav footer
paywall login signin signup patreon donate widget toolbar masthead skip
""".split())

# ---- righe che sono quasi sempre incarto (a prescindere da dove stanno) ---------------------------
_COOKIE_LINE = re.compile(
    r"\b(cookie|cookies|consenso|consent|gdpr|accept all|accetta (tutti|i cookie)|"
    r"manage preferences|gestisci le preferenze|we use cookies|utilizziamo i cookie|"
    r"privacy settings)\b", re.IGNORECASE)
_PROMO_LINE = re.compile(
    r"\b(become a patron|patreon|iscriviti alla newsletter|subscribe to our|sign up for|"
    r"follow us|seguici|condividi|share on|share this|tweet|pin it|buy me a coffee|donate|"
    r"advertisement|pubblicità|sponsored|related posts|articoli correlati|leggi anche|"
    r"read more:|you may also like|potrebbe interessarti|commenti|leave a comment|"
    r"lascia un commento)\b", re.IGNORECASE)
_LEGAL_LINE = re.compile(
    r"(©|\(c\)\s*\d{4}|copyright\s+\d{4}|all rights reserved|tutti i diritti riservati|"
    r"terms of (use|service)|termini e condizioni|privacy policy|informativa sulla privacy|"
    r"\bimpressum\b)", re.IGNORECASE)
_LOGIN_WALL = re.compile(
    r"(sign in to (continue|read|view)|log ?in to (continue|read|view)|accedi per (continuare|"
    r"leggere)|subscribe to (continue|read)|abbonati per (continuare|leggere)|create (a free )?"
    r"account to (continue|read)|enable javascript|attiva javascript|javascript is (required|"
    r"disabled)|your browser (does not|doesn't) support|please enable cookies)", re.IGNORECASE)

_SENTENCE = re.compile(r"[.!?…:;»”\"]\s*$|[.!?…]\s")
_WORD = re.compile(r"\w+", re.UNICODE)


@dataclass
class Cleaned:
    """Il risultato della pulizia di una pagina."""

    text: str = ""
    #: cosa è stato tolto, per categoria: {«elementi»: n, «cookie»: n, «menu e link»: n, …}
    removed: dict[str, int] = field(default_factory=dict)
    #: caratteri del testo prima e dopo la pulizia (del solo testo, non dell'HTML)
    chars_before: int = 0
    chars_after: int = 0
    #: ``False`` = non leggibile: ``reason`` dice perché e il testo non va al modello
    readable: bool = True
    reason: str = ""
    #: chi l'ha scritta e quando, dai metadati della pagina (vuoti se la pagina non lo dice)
    author: str = ""
    published: str = ""

    def summary(self) -> str:
        """Il conteggio di ciò che è stato tolto, in una riga (va accanto alla pagina)."""
        pezzi = [f"{n} {nome}" for nome, n in self.removed.items() if n]
        tolto = ", ".join(pezzi) if pezzi else "niente"
        return (f"tolto: {tolto}; testo {self.chars_before} → {self.chars_after} caratteri")


def _tokens(valore) -> list[str]:
    if not valore:
        return []
    if isinstance(valore, (list, tuple)):
        valore = " ".join(valore)
    return [t for t in re.split(r"[^a-z0-9]+", str(valore).lower()) if t]


def _is_boiler_element(tag) -> bool:
    if tag.name in ("html", "body", "article", "main"):
        return False
    attrs = tag.attrs or {}
    if str(attrs.get("role", "")).lower() in ("dialog", "alertdialog", "navigation", "banner",
                                              "contentinfo", "complementary", "search"):
        return True
    if str(attrs.get("aria-modal", "")).lower() == "true":
        return True
    toks = set(_tokens(attrs.get("class"))) | set(_tokens(attrs.get("id")))
    return bool(toks & _BOILER_TOKENS)


def _link_density(tag) -> float:
    testo = tag.get_text(" ", strip=True)
    if not testo:
        return 0.0
    link = " ".join(a.get_text(" ", strip=True) for a in tag.find_all("a"))
    return len(link) / max(1, len(testo))


def _prose_chars(testo: str) -> int:
    """Caratteri di «prosa»: righe di almeno otto parole che finiscono come una frase."""
    totale = 0
    for riga in testo.splitlines():
        if len(_WORD.findall(riga)) >= 8 and (_SENTENCE.search(riga) or len(riga) > 120):
            totale += len(riga)
    return totale


def youtube_description(html: str) -> tuple[str, str]:
    """Titolo e descrizione di un video di YouTube, dalla pagina (non serve nessuna libreria).

    La pagina è quasi vuota senza JavaScript, ma porta con sé i dati del video; la descrizione è
    in ``shortDescription``. La **trascrizione** richiede un'altra chiamata (o una libreria): non
    si fa qui.
    """
    desc = ""
    m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', html)
    if m:
        try:
            desc = json.loads(f'"{m.group(1)}"')
        except ValueError:
            desc = m.group(1)
    titolo = ""
    m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    if m:
        titolo = m.group(1)
    return titolo.strip(), desc.strip()


# ---- chi ha scritto la pagina e quando --------------------------------------------------------------
#: i meta tag con l'autore, nell'ordine in cui ci si fida (citazioni accademiche prima)
_AUTHOR_META = ("citation_author", "dc.creator", "dcterms.creator", "author", "article:author",
                "parsely-author", "sailthru.author", "byl")
#: i meta tag con la data di pubblicazione
_DATE_META = ("citation_publication_date", "citation_date", "dc.date", "dcterms.date",
              "dcterms.issued", "article:published_time", "og:published_time", "datepublished",
              "pubdate", "publish-date", "date", "sailthru.date", "parsely-pub-date")
_ISO_DATE = re.compile(r"\b(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?")


def _date(valore: str) -> str:
    """Una data leggibile da quello che scrivono le pagine: ``2019-03-07``, ``2019-03``, ``2019``."""
    m = _ISO_DATE.search(valore or "")
    if not m or not 1000 <= int(m.group(1)) <= 2100:
        return ""
    anno, mese, giorno = m.group(1), m.group(2), m.group(3)
    if mese and 1 <= int(mese) <= 12:
        if giorno and 1 <= int(giorno) <= 31:
            return f"{anno}-{int(mese):02d}-{int(giorno):02d}"
        return f"{anno}-{int(mese):02d}"
    return anno


def _author(valore: str) -> str:
    """Un nome d'autore pulito: niente «By», niente indirizzi web, niente liste interminabili."""
    testo = re.sub(r"\s+", " ", valore or "").strip()
    testo = re.sub(r"^(by|di|von|par|por)\s+", "", testo, flags=re.IGNORECASE)
    if not testo or testo.startswith(("http://", "https://", "@")) or len(testo) > 120:
        return ""
    return testo


def _ld_items(soup) -> list[dict]:
    """Gli oggetti JSON-LD della pagina (anche dentro ``@graph`` o in una lista)."""
    oggetti: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            dati = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        coda = dati if isinstance(dati, list) else [dati]
        while coda:
            voce = coda.pop(0)
            if isinstance(voce, dict):
                oggetti.append(voce)
                if isinstance(voce.get("@graph"), list):
                    coda.extend(voce["@graph"])
            elif isinstance(voce, list):
                coda.extend(voce)
    return oggetti


def _ld_author(valore) -> str:
    if isinstance(valore, list):
        nomi = [n for n in (_ld_author(v) for v in valore[:3]) if n]
        return ", ".join(nomi)
    if isinstance(valore, dict):
        return _author(str(valore.get("name") or ""))
    return _author(str(valore or ""))


def page_meta(soup) -> tuple[str, str]:
    """Autore e data di pubblicazione di una pagina, da quello che la pagina dichiara.

    Si legge solo ciò che la pagina dice di sé (meta tag, JSON-LD, ``<time>``): niente si deduce
    dal testo, perché una bibliografia con una data inventata è peggio di una senza data.
    """
    meta: dict[str, list[str]] = {}
    for tag in soup.find_all("meta"):
        nome = str(tag.get("name") or tag.get("property") or tag.get("itemprop") or "").lower()
        contenuto = str(tag.get("content") or "").strip()
        if nome and contenuto:
            meta.setdefault(nome, []).append(contenuto)
    autore = ""
    for chiave in _AUTHOR_META:
        valori = [a for a in (_author(v) for v in meta.get(chiave, [])) if a]
        if valori:
            autore = ", ".join(dict.fromkeys(valori[:3]))
            break
    data = next((d for chiave in _DATE_META for d in map(_date, meta.get(chiave, [])) if d), "")
    if not (autore and data):
        for voce in _ld_items(soup):
            if not autore and voce.get("author"):
                autore = _ld_author(voce["author"])
            if not data:
                data = _date(str(voce.get("datePublished") or voce.get("dateCreated") or ""))
            if autore and data:
                break
    if not data:
        tempo = soup.find("time", attrs={"datetime": True})
        if tempo is not None:
            data = _date(str(tempo.get("datetime")))
    return autore, data


def is_youtube(url: str) -> bool:
    host = urlparse(url or "").netloc.lower()
    return host.endswith("youtube.com") or host == "youtu.be" or host.endswith("youtube-nocookie.com")


def clean_html(html: str, url: str = "", *, min_chars: int = 400) -> Cleaned:
    """Il testo principale di una pagina, il conteggio di ciò che è stato tolto, e se si legge.

    ``min_chars`` è la quantità minima di prosa che deve restare perché la pagina sia leggibile.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    prima = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    out = Cleaned(chars_before=len(prima))
    out.author, out.published = page_meta(soup)  # prima della pulizia: i metadati sono negli script
    rimossi = {"elementi di pagina": 0, "banner cookie e consenso": 0, "menu e liste di link": 0,
               "righe di promozione": 0, "righe legali e di piè di pagina": 0}

    # YouTube: la pagina esiste solo con JavaScript. Si prende la descrizione, se c'è.
    if is_youtube(url):
        titolo, desc = youtube_description(html)
        if len(desc) >= min_chars // 2:
            out.text = (f"{titolo}\n\n" if titolo else "") + desc
            out.removed = {"pagina YouTube (solo la descrizione del video; nessuna trascrizione)": 1}
            out.chars_after = len(out.text)
            return out
        out.readable, out.reason = False, ("pagina YouTube senza descrizione utile: il video non "
                                           "si legge senza la trascrizione")
        out.removed = {"pagina YouTube": 1}
        out.chars_after = 0
        return out

    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()
        rimossi["elementi di pagina"] += 1
    tutti = list(soup.find_all(True))
    for tag in tutti:
        if getattr(tag, "attrs", None) is None or tag.decomposed:
            continue
        if _is_boiler_element(tag):
            testo = tag.get_text(" ", strip=True)
            toks = set(_tokens(tag.attrs.get("class"))) | set(_tokens(tag.attrs.get("id")))
            consenso = bool(toks & {"cookie", "cookies", "consent", "gdpr", "cmp", "onetrust"}
                            or str(tag.attrs.get("role", "")).lower() in ("dialog", "alertdialog"))
            # Un contenitore che è quasi tutta la pagina non è incarto, si è sbagliato nome:
            # eccetto un banner di consenso, che può esserlo davvero (il muro di cookie).
            if not consenso and len(testo) > 0.6 * max(1, len(prima)):
                continue
            rimossi["banner cookie e consenso" if consenso else "elementi di pagina"] += 1
            tag.decompose()

    # Il contenitore principale: article / main, altrimenti il blocco con più prosa.
    corpo = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
    if corpo is None:
        migliore, punteggio = None, 0
        for c in soup.find_all(["div", "section", "td"]):
            p = sum(len(x.get_text(" ", strip=True)) for x in c.find_all("p")
                    if len(x.get_text(" ", strip=True)) >= 40)
            p *= 1 - min(0.9, _link_density(c))
            if p > punteggio:
                migliore, punteggio = c, p
        corpo = migliore or soup.body or soup

    # Blocco per blocco: via le liste di link, i menu, le righe di incarto.
    blocchi: list[str] = []
    for el in corpo.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre", "td", "dd"]):
        if el.find(["p", "li", "blockquote"]) and el.name in ("td", "dd"):
            continue  # il contenitore di altri blocchi: li leggiamo uno per uno
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if not t:
            continue
        parole = len(_WORD.findall(t))
        if _COOKIE_LINE.search(t) and parole < 60:
            rimossi["banner cookie e consenso"] += 1
            continue
        if _PROMO_LINE.search(t) and parole < 30:
            rimossi["righe di promozione"] += 1
            continue
        if _LEGAL_LINE.search(t) and parole < 40:
            rimossi["righe legali e di piè di pagina"] += 1
            continue
        if _LOGIN_WALL.search(t) and parole < 40:
            rimossi["banner cookie e consenso"] += 1
            continue
        if el.name == "li" and (_link_density(el) > 0.6 or parole < 4):
            rimossi["menu e liste di link"] += 1
            continue
        if el.name in ("p", "td", "dd") and _link_density(el) > 0.8 and parole < 25:
            rimossi["menu e liste di link"] += 1
            continue
        blocchi.append(t)

    # Serie di righe cortissime senza punteggiatura (menu rimasti senza etichetta, indici di
    # capitoli): non sono prosa. Si tolgono a gruppi di quattro o più.
    pulite: list[str] = []
    corte: list[str] = []

    def chiudi() -> None:
        if len(corte) >= 4:
            rimossi["menu e liste di link"] += len(corte)
        else:
            pulite.extend(corte)
        corte.clear()

    for b in blocchi:
        if len(b) < 45 and not _SENTENCE.search(b):
            corte.append(b)
        else:
            chiudi()
            pulite.append(b)
    chiudi()
    # senza doppioni consecutivi (titolo ripetuto, didascalie)
    unici = [b for i, b in enumerate(pulite) if i == 0 or b != pulite[i - 1]]
    out.text = "\n\n".join(unici).strip()
    out.removed = {k: v for k, v in rimossi.items() if v}
    out.chars_after = len(out.text)

    # La pagina si legge?
    prosa = _prose_chars(out.text)
    if _LOGIN_WALL.search(prima) and prosa < 4 * min_chars:
        out.readable, out.reason = False, "muro di accesso o pagina che richiede JavaScript"
    elif prosa < min_chars:
        out.readable = False
        cookie = out.removed.get("banner cookie e consenso", 0)
        menu = out.removed.get("menu e liste di link", 0)
        if cookie and cookie >= menu and prosa < min_chars // 2:
            out.reason = "muro di cookie o di consenso: sotto non c'è testo da leggere"
        elif menu >= 3 and prosa < min_chars // 2:
            out.reason = "solo menu, indici e liste di link: nessun testo da leggere"
        elif out.chars_before and out.chars_after < 0.2 * out.chars_before:
            out.reason = "solo menu, piè di pagina e link: nessun testo da leggere"
        else:
            out.reason = (f"troppo poco testo da leggere ({prosa} caratteri di prosa, ne servono "
                          f"almeno {min_chars})")
    return out


# ---- pertinenza --------------------------------------------------------------------------------
_STOP = frozenset("""
the and for with that this from have has had was were are not but his her its their there these
those into than then them they what when where which while will would about after before over
under also been being more most some such only very just like each other many much per via
della dello delle degli dell alla alle allo agli nel nella nelle nei negli che con per una uno
sono come anche più dopo prima quando dove quale quali tra fra suo sua suoi sue loro essere
stato stata stati state viene vengono questo questa questi queste quello quella quelli quelle
dalla dalle dallo dagli sul sulla sulle sui sugli capitolo scena punto libro
""".split())


def terms(testo: str) -> set[str]:
    """Le parole che contano di un testo: minuscole, senza parole vuote, a radice di sei lettere."""
    return {w[:6] for w in (m.lower() for m in _WORD.findall(testo or ""))
            if len(w) >= 4 and w not in _STOP and not w.isdigit()}


@dataclass
class Topic:
    """Di cosa deve parlare una pagina: le parole del punto e quelle del libro."""

    point: set[str] = field(default_factory=set)
    book: set[str] = field(default_factory=set)
    text: str = ""  # i testi da cui vengono, per riconoscerne la lingua

    @classmethod
    def of(cls, point_text: str, book_text: str, *, exclude: set[str] | None = None) -> Topic:
        """``exclude``: parole da non contare (i nomi inventati dal libro non stanno sul web)."""
        togli = {w.lower()[:6] for w in (exclude or set())}
        return cls(point=terms(point_text) - togli, book=terms(book_text) - togli,
                   text=f"{point_text} {book_text}")


@dataclass
class Relevance:
    score: float = 1.0
    anchors: int = 0
    point_cov: float = 0.0
    reason: str = ""


_IT = frozenset("""
il lo la le gli un una dei delle degli nel nella che con per sono non come anche più della dello
alla questo questa essere stato loro tra fra molto ogni quando dove
""".split())
_EN = frozenset("""
the and of to in is that for with as are was were this from by not but have has which their
when where also more most been being into than then they
""".split())


def guess_lang(testo: str) -> str:
    """``it``, ``en`` o vuoto (non si sa): conta le parole funzionali di ciascuna lingua."""
    parole = [w.lower() for w in _WORD.findall(testo or "")][:2000]
    if len(parole) < 25:
        return ""
    it = sum(1 for w in parole if w in _IT)
    en = sum(1 for w in parole if w in _EN)
    if it > 2 * en and it >= 8:
        return "it"
    if en > 2 * it and en >= 8:
        return "en"
    return ""


def zero_overlap(page_text: str, topic: Topic, topic_text: str) -> bool:
    """Vero solo nel caso più sicuro: la pagina e l'argomento sono nella stessa lingua e non hanno
    **nemmeno una parola** in comune. In ogni altro caso (lingue diverse, o miste come in un libro
    con la premessa in italiano e i punti in inglese) il giudizio spetta al modello: un confronto
    di parole fra lingue diverse scarterebbe pagine buone.
    """
    if not topic.point and not topic.book:
        return False
    lingua_pagina, lingua_tema = guess_lang(page_text), guess_lang(topic_text)
    if not lingua_pagina or lingua_pagina != lingua_tema:
        return False
    parole = terms(page_text)
    return not ((topic.point | topic.book) & parole)


def relevance(page_text: str, topic: Topic) -> Relevance:
    """Quanto una pagina parla dell'argomento: 0 (d'altro) … 1.

    Due misure, entrambe sulle parole: quante di quelle **del libro** (la premessa, i messaggi:
    ayahuasca, DMT, acacia…) la pagina contiene — sono le più discriminanti — e quante di quelle
    **del punto**. Il titolo di un capitolo da solo non basta: «fractal tunnel» è anche un
    videogioco. Se non c'è nessun vocabolario (un libro appena cominciato) non si filtra.
    """
    if not topic.point and not topic.book:
        return Relevance(1.0, reason="nessun vocabolario con cui confrontare")
    parole = terms(page_text)
    ancore = topic.book & parole
    solo_punto = topic.point - topic.book
    cov = (len(solo_punto & parole) / len(solo_punto)) if solo_punto else 0.0
    ancora_cov = min(1.0, len(ancore) / 3) if topic.book else 0.0
    # Un punto di due parole («fractal tunnel») non basta a dire di cosa parla una pagina: la sua
    # copertura pesa in proporzione a quanto è ricco il vocabolario del punto.
    fiducia = min(1.0, len(solo_punto) / 4)
    if topic.book:
        score = 0.65 * ancora_cov + 0.35 * cov * fiducia
    else:
        score = cov * fiducia
    motivo = (f"{len(ancore)} parole del libro e {len(solo_punto & parole)} su "
              f"{len(solo_punto)} del punto")
    return Relevance(round(score, 3), len(ancore), round(cov, 3), motivo)
