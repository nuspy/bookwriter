"""Ricerca sul web e lettura delle pagine trovate.

È l'unico punto del programma che esce su internet, ed è **spento** finché non lo si accende
(``research.enabled`` nelle impostazioni). Chi lavora su una macchina staccata dalla rete, o su
materiale che non deve uscire di lì, non deve scoprire il traffico dopo averlo fatto.

Quattro motori. Il primo non chiede niente a nessuno e basta per cominciare; gli altri vogliono
una chiave e in cambio danno risultati migliori e nessun blocco:

- **DuckDuckGo** — la pagina dei risultati, letta come la leggerebbe un browser. Nessuna chiave.
  In compenso, se si esagera con le richieste, risponde con una pagina vuota: è il suo modo di
  dire di rallentare, e va preso per quello che è invece di ritentare.
- **Brave Search** e **Tavily** — API con chiave, dalla variabile d'ambiente o dalle impostazioni.
- **SearXNG** — la propria istanza, per chi ne ha una.

Quello che si scarica non è il testo di una pagina: è una pagina. Le parti che non sono lettura —
navigazione, piè di pagina, script — si tolgono qui, perché il resto del programma non deve
sapere che l'HTML esiste.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from bookwriter.config import ResearchSettings
from bookwriter.forge import pagetext

#: Da dove viene la chiave, per motore, quando non è scritta nelle impostazioni.
KEY_ENV = {"brave": "BRAVE_API_KEY", "tavily": "TAVILY_API_KEY"}

#: Pausa minima fra due interrogazioni allo stesso motore. Non è cortesia astratta: è quello che
#: separa una ricerca che funziona da una che comincia a ricevere pagine vuote.
_MIN_INTERVAL_S = 1.2
_last_call: dict[str, float] = {}


_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ref_src$)", re.IGNORECASE)


def norm_url(url: str) -> str:
    """L'indirizzo confrontabile: stessa pagina, stessa chiave.

    Minuscolo il sito e senza «www.», senza «/» finale, senza frammento e senza i parametri di
    tracciamento (``utm_*``…). Due interrogazioni che trovano la stessa pagina con l'indirizzo
    scritto in modo un po' diverso danno una fonte sola, non due.
    """
    try:
        p = urlparse((url or "").strip())
    except ValueError:
        return (url or "").strip()
    if not p.netloc:
        return (url or "").strip()
    host = p.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    query = "&".join(sorted(q for q in p.query.split("&") if q and not _TRACKING.match(q.split("=")[0])))
    base = f"{p.scheme.lower()}://{host}{p.path.rstrip('/')}"
    return base + (f"?{query}" if query else "")


class SearchError(RuntimeError):
    """La ricerca non è andata a buon fine, e va detto invece di restituire una lista vuota."""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""
    #: dominio escluso dalle impostazioni: il risultato si registra ma la pagina non si scarica
    excluded: bool = False

    def line(self) -> str:
        return f"- **{self.title}** — {self.url}\n  {self.snippet}".rstrip()


@dataclass
class Page:
    """Una pagina letta: quello che c'è dentro, e da dove viene.

    ``readable`` e ``relevant`` dicono se può andare al modello: una pagina non leggibile (un muro
    di cookie, una pagina che esiste solo con JavaScript) o non pertinente (parla d'altro) resta
    registrata, con il motivo in ``note``, ma non si passa mai al modello. ``removed`` è il
    conteggio di ciò che la pulizia ha tolto.
    """

    url: str
    title: str
    text: str
    readable: bool = True
    relevant: bool = True
    note: str = ""
    removed: dict[str, int] = field(default_factory=dict)
    chars_before: int = 0
    chars_after: int = 0
    relevance: float = 1.0
    #: la riga di conteggio già calcolata (quando la pagina viene da dati salvati)
    cleaning: str = ""
    #: motivo specifico del blocco se non leggibile (captcha, challenge, http_403, etc.)
    block_reason: str = ""

    @property
    def usable(self) -> bool:
        return self.readable and self.relevant

    @property
    def status(self) -> str:
        """Vuoto se la pagina va al modello; altrimenti perché no."""
        return "" if self.readable and self.relevant else (
            "non_leggibile" if not self.readable else "non_pertinente")

    def cleaning_line(self) -> str:
        """Cosa è stato tolto, in una riga: la si mette accanto alla pagina."""
        if self.cleaning:
            return self.cleaning
        if not self.removed and not self.chars_before:
            return ""
        pezzi = ", ".join(f"{n} {nome}" for nome, n in self.removed.items() if n) or "niente"
        return f"tolto: {pezzi}; testo {self.chars_before} → {self.chars_after} caratteri"

    def excerpt(self, chars: int = 6000) -> str:
        return self.text[:chars]


# --------------------------------------------------------------------------- #
#  ricerca
# --------------------------------------------------------------------------- #
def api_key_for(settings: ResearchSettings) -> str:
    """La chiave in uso: quella scritta nelle impostazioni, o la variabile d'ambiente."""
    if settings.api_key.strip():
        return settings.api_key.strip()
    return (os.environ.get(KEY_ENV.get(settings.engine, ""), "") or "").strip()


def _throttle(engine: str) -> None:
    scorso = _last_call.get(engine, 0.0)
    attesa = _MIN_INTERVAL_S - (time.monotonic() - scorso)
    if attesa > 0:
        time.sleep(attesa)
    _last_call[engine] = time.monotonic()


def search(query: str, settings: ResearchSettings, *, page: int = 0) -> list[SearchResult]:
    """Interroga il motore configurato. Solleva ``SearchError`` se non si può fare.

    ``page`` (da 0) chiede i risultati successivi ai primi, per i motori che li danno (Brave e
    SearXNG); gli altri ne restituiscono sempre la prima pagina, e chi approfondisce compensa
    alzando ``max_results``.
    """
    if not settings.enabled:
        raise SearchError(
            "La ricerca sul web è spenta. Accendila in Impostazioni → Ricerca, oppure procedi "
            "senza: il libro affermerà invece di dimostrare."
        )
    query = (query or "").strip()
    if not query:
        return []
    motore = settings.engine or "duckduckgo"
    _throttle(motore)
    intestazioni = {"User-Agent": settings.user_agent, "Accept-Language": "it,en;q=0.8"}
    with httpx.Client(timeout=settings.timeout_s, headers=intestazioni,
                      follow_redirects=True) as client:
        if motore == "brave":
            return _brave(client, query, settings, page)
        if motore == "tavily":
            return _tavily(client, query, settings)
        if motore == "searxng":
            return _searxng(client, query, settings, page)
        return _duckduckgo(client, query, settings)


def _duckduckgo(client: httpx.Client, query: str,
                settings: ResearchSettings) -> list[SearchResult]:
    try:
        resp = client.post("https://html.duckduckgo.com/html/", data={"q": query})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SearchError(f"DuckDuckGo non ha risposto: {exc}") from exc

    soup = _soup(resp.text)
    trovati: list[SearchResult] = []
    for blocco in soup.select("div.result, div.web-result")[: settings.max_results * 2]:
        link = blocco.select_one("a.result__a")
        if link is None or not link.get("href"):
            continue
        frammento = blocco.select_one(".result__snippet")
        trovati.append(SearchResult(
            title=link.get_text(" ", strip=True),
            url=_unwrap_ddg(str(link["href"])),
            snippet=frammento.get_text(" ", strip=True) if frammento else "",
            source="duckduckgo",
        ))
        if len(trovati) >= settings.max_results:
            break
    if not trovati and "anomaly" in resp.text.lower():
        raise SearchError(
            "DuckDuckGo ha risposto con una pagina vuota: sta chiedendo di rallentare. "
            "Aspetta qualche minuto, oppure passa a un motore con chiave."
        )
    return trovati


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo incarta i link in un proprio rimando: quello che serve è l'indirizzo vero."""
    if "duckduckgo.com/l/" not in href:
        return href if href.startswith("http") else f"https:{href}"
    query = urlparse(href if href.startswith("http") else f"https:{href}").query
    dentro = parse_qs(query).get("uddg")
    return unquote(dentro[0]) if dentro else href


def _brave(client: httpx.Client, query: str, settings: ResearchSettings,
           page: int = 0) -> list[SearchResult]:
    chiave = api_key_for(settings)
    if not chiave:
        raise SearchError("Brave Search vuole una chiave: mettila nelle impostazioni o in "
                          "BRAVE_API_KEY.")
    try:
        resp = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": settings.max_results, "offset": page},
            headers={"X-Subscription-Token": chiave, "Accept": "application/json"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SearchError(f"Brave Search non ha risposto: {exc}") from exc
    voci = (resp.json().get("web") or {}).get("results") or []
    return [
        SearchResult(title=v.get("title", ""), url=v.get("url", ""),
                     snippet=_plain(v.get("description", "")), source="brave")
        for v in voci[: settings.max_results]
    ]


def _tavily(client: httpx.Client, query: str, settings: ResearchSettings) -> list[SearchResult]:
    chiave = api_key_for(settings)
    if not chiave:
        raise SearchError("Tavily vuole una chiave: mettila nelle impostazioni o in TAVILY_API_KEY.")
    try:
        resp = client.post(
            "https://api.tavily.com/search",
            json={"api_key": chiave, "query": query, "max_results": settings.max_results,
                  "search_depth": "advanced"},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SearchError(f"Tavily non ha risposto: {exc}") from exc
    return [
        SearchResult(title=v.get("title", ""), url=v.get("url", ""),
                     snippet=_plain(v.get("content", ""))[:400], source="tavily")
        for v in (resp.json().get("results") or [])[: settings.max_results]
    ]


def _searxng(client: httpx.Client, query: str, settings: ResearchSettings,
             page: int = 0) -> list[SearchResult]:
    base = settings.instance_url.strip().rstrip("/")
    if not base:
        raise SearchError("SearXNG vuole l'indirizzo della tua istanza nelle impostazioni.")
    try:
        resp = client.get(f"{base}/search",
                          params={"q": query, "format": "json", "language": "it",
                                  "pageno": page + 1})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SearchError(f"L'istanza SearXNG non ha risposto: {exc}") from exc
    return [
        SearchResult(title=v.get("title", ""), url=v.get("url", ""),
                     snippet=_plain(v.get("content", "")), source="searxng")
        for v in (resp.json().get("results") or [])[: settings.max_results]
    ]


# --------------------------------------------------------------------------- #
#  lettura delle pagine
# --------------------------------------------------------------------------- #
def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text or "")).strip()


def fetch(url: str, settings: ResearchSettings) -> Page | None:
    """Scarica una pagina e ne restituisce il testo principale, pulito (vedi :mod:`pagetext`).

    ``None`` se non si è potuta scaricare. Una pagina scaricata ma inservibile (muro di cookie,
    solo JavaScript, solo menu, captcha) torna con ``readable=False`` e il motivo in ``note``
    e ``block_reason``: è un dato della ricerca, e non va al modello.

    Se la pagina risulta bloccata e ``settings.external_fetcher_url`` è configurato, tenta
    la risoluzione tramite il servizio esterno compatibile con FlareSolverr (/v1).
    """
    if not settings.enabled or not url.startswith("http"):
        return None

    from bookwriter.forge import blocked, browser_service

    html = ""
    http_error_code: int | None = None
    http_error_msg = ""

    try:
        with httpx.Client(timeout=settings.timeout_s,
                          headers={"User-Agent": settings.user_agent},
                          follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            if "html" not in resp.headers.get("content-type", "text/html"):
                return None
            html = resp.text
    except httpx.HTTPStatusError as exc:
        http_error_code = exc.response.status_code
        motivo = ("il sito ha rifiutato la richiesta" if http_error_code in (401, 403, 429) else
                  "la pagina non esiste" if http_error_code in (404, 410) else "il sito ha dato un errore")
        http_error_msg = f"{motivo} (HTTP {http_error_code})"
    except (httpx.HTTPError, UnicodeDecodeError):
        return None

    # Se c'è stato un rifiuto HTTP (401, 403, 429, 503...) proviamo con il solver esterno
    if http_error_code is not None:
        block_reason = blocked.detect_block_reason(url, http_code=http_error_code) or f"http_{http_error_code}"
        if settings.external_fetcher_url and http_error_code in (401, 403, 429, 503):
            res = browser_service.fetch_via_flaresolverr(
                url,
                endpoint=settings.external_fetcher_url,
                timeout_s=settings.external_fetcher_timeout_s,
            )
            if res is not None:
                _, ext_html = res
                soup_ext = _soup(ext_html)
                titolo_ext = soup_ext.title.get_text(strip=True) if soup_ext.title else url
                pulita_ext = pagetext.clean_html(ext_html, url, min_chars=settings.min_page_chars)
                if pulita_ext.readable:
                    return Page(url=url, title=titolo_ext, text=pulita_ext.text,
                                readable=True, note="", removed=dict(pulita_ext.removed),
                                chars_before=pulita_ext.chars_before, chars_after=pulita_ext.chars_after)
        return Page(url=url, title=url, text="", readable=False, note=http_error_msg,
                    block_reason=block_reason)

    soup = _soup(html)
    titolo = soup.title.get_text(strip=True) if soup.title else url
    pulita = pagetext.clean_html(html, url, min_chars=settings.min_page_chars)

    if pulita.readable:
        return Page(url=url, title=titolo, text=pulita.text,
                    readable=True, note="",
                    removed=dict(pulita.removed), chars_before=pulita.chars_before,
                    chars_after=pulita.chars_after)

    # La pagina scaricata non è leggibile: verifichiamo se è un blocco
    block_reason = blocked.detect_block_reason(url, html=html, clean_reason=pulita.reason)

    # Se è bloccata e il solver esterno è attivo, proviamo a risolverla con il browser
    if block_reason and settings.external_fetcher_url:
        res = browser_service.fetch_via_flaresolverr(
            url,
            endpoint=settings.external_fetcher_url,
            timeout_s=settings.external_fetcher_timeout_s,
        )
        if res is not None:
            _, ext_html = res
            soup_ext = _soup(ext_html)
            titolo_ext = soup_ext.title.get_text(strip=True) if soup_ext.title else url
            pulita_ext = pagetext.clean_html(ext_html, url, min_chars=settings.min_page_chars)
            if pulita_ext.readable:
                return Page(url=url, title=titolo_ext, text=pulita_ext.text,
                            readable=True, note="", removed=dict(pulita_ext.removed),
                            chars_before=pulita_ext.chars_before, chars_after=pulita_ext.chars_after)

    return Page(url=url, title=titolo, text="",
                readable=False, note=pulita.reason,
                block_reason=block_reason or "",
                removed=dict(pulita.removed), chars_before=pulita.chars_before,
                chars_after=pulita.chars_after)


def domain_excluded(url: str, patterns) -> bool:
    """Il dominio di ``url`` è fra quelli esclusi? ``amazon.*`` vale per ogni paese e sottodominio
    (``amazon.it``, ``smile.amazon.co.uk``) e non per ``amazonaws.com``; ``youtube.com`` vale per
    ``m.youtube.com``."""
    host = urlparse(url or "").netloc.lower().split(":")[0]
    for p in patterns or []:
        p = str(p).strip().lower().lstrip(".")
        if not p:
            continue
        regex = r"(^|\.)" + re.escape(p).replace(r"\*", r"[a-z0-9.\-]+") + "$"
        if re.search(regex, host):
            return True
    return False


def gather(query: str, settings: ResearchSettings, *, read: int | None = None,
           page: int = 0, skip: set[str] | None = None
           ) -> tuple[list[SearchResult], list[Page]]:
    """Cerca e legge: i risultati, e per intero le prime pagine fra quelle trovate.

    ``skip`` sono gli indirizzi già visti: i risultati si restituiscono tutti, ma le pagine già
    lette non si scaricano di nuovo.
    """
    risultati = search(query, settings, page=page)
    for r in risultati:
        r.excluded = domain_excluded(r.url, settings.excluded_domains)
    quante = settings.max_pages if read is None else read
    visti = {norm_url(u) for u in (skip or ())}
    da_leggere = [r for r in risultati
                  if norm_url(r.url) not in visti and not r.excluded][:quante]
    pagine = [p for p in (fetch(r.url, settings) for r in da_leggere) if p]
    return risultati, pagine
