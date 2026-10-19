"""Gestione delle pagine bloccate e della coda su cartella (Interfaccia B).

Come descritto in docs/browser_integration.md, le pagine non leggibili a causa di
verifiche (CAPTCHA, challenge anti-bot), muri di consenso o codici HTTP di rifiuto
vengono registrate con il loro motivo specifico e possono essere risolte da un servizio
esterno (Interfaccia A) o inserite nella coda su file (Interfaccia B) in
research/bloccate/coda.jsonl per poi essere importate da research/bloccate/ricevute/.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any

from bookwriter.forge import pagetext
from bookwriter.forge.websearch import Page, norm_url

logger = logging.getLogger(__name__)

STATUS_IN_CODA = "in_coda"
STATUS_RICEVUTA = "ricevuta"
STATUS_NON_RICONOSCIUTA = "non_riconosciuta"

# Motivi di blocco riconosciuti
REASON_CAPTCHA = "captcha"
REASON_CHALLENGE = "challenge"
REASON_HTTP_403 = "http_403"
REASON_HTTP_429 = "http_429"
REASON_HTTP_503 = "http_503"
REASON_COOKIE_WALL = "cookie_wall"
REASON_LOGIN_WALL = "login_wall"
REASON_JAVASCRIPT_ONLY = "javascript_only"

RECOGNIZED_REASONS = {
    REASON_CAPTCHA,
    REASON_CHALLENGE,
    REASON_HTTP_403,
    REASON_HTTP_429,
    REASON_HTTP_503,
    REASON_COOKIE_WALL,
    REASON_LOGIN_WALL,
    REASON_JAVASCRIPT_ONLY,
}

_CAPTCHA_PAT = re.compile(r"(recaptcha|hcaptcha|turnstile|cf-turnstile|g-recaptcha)", re.IGNORECASE)
_CHALLENGE_PAT = re.compile(
    r"(just a moment|checking your browser|ddos-guard|attention required|cloudflare ray id|"
    r"unusual traffic|verify you are human|verifying that you are human|security check)",
    re.IGNORECASE,
)
_COOKIE_PAT = re.compile(r"(muro di cookie|consenso|cookie|consent)", re.IGNORECASE)
_LOGIN_PAT = re.compile(r"(muro di accesso|sign in|log in|abbonati|subscribe to)", re.IGNORECASE)
_JS_PAT = re.compile(r"(richiede javascript|javascript_only|javascript is required|enable javascript)", re.IGNORECASE)


def detect_block_reason(url: str, html: str | None = None,
                        http_code: int | None = None,
                        clean_reason: str = "") -> str | None:
    """Riconosce se una pagina è bloccata e determina il codice di motivo corrispondente.

    Restituisce una delle stringhe standard (captcha, challenge, http_403, ...)
    oppure None se non si tratta di un blocco gestibile.
    """
    if http_code == 403:
        return REASON_HTTP_403
    if http_code == 429:
        return REASON_HTTP_429
    if http_code == 503:
        return REASON_HTTP_503
    if http_code and http_code in (401, 520, 521, 522, 523, 524):
        return f"http_{http_code}"

    corpus = f"{clean_reason} {html[:4000] if html else ''}"

    if _CAPTCHA_PAT.search(corpus):
        return REASON_CAPTCHA
    if _CHALLENGE_PAT.search(corpus):
        return REASON_CHALLENGE
    if _COOKIE_PAT.search(clean_reason):
        return REASON_COOKIE_WALL
    if _LOGIN_PAT.search(clean_reason):
        return REASON_LOGIN_WALL
    if _JS_PAT.search(clean_reason):
        return REASON_JAVASCRIPT_ONLY

    # Se clean_reason cita esplicitamente JavaScript
    if "javascript" in clean_reason.lower():
        return REASON_JAVASCRIPT_ONLY

    return None


def make_page_id(chapter: int, point: int, url: str) -> str:
    """Genera un identificativo stabile per la riga della coda (es. c01p2-4f9a1c)."""
    normed = norm_url(url)
    h = hashlib.sha256(normed.encode("utf-8")).hexdigest()[:6]
    return f"c{chapter:02d}p{point}-{h}"


@dataclass
class BlockedItem:
    id: str
    url: str
    chapter: int
    point: int
    query: str
    reason: str
    status: str = STATUS_IN_CODA
    queued_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BlockedItem:
        return cls(
            id=str(data.get("id", "")),
            url=str(data.get("url", "")),
            chapter=int(data.get("chapter", 0)),
            point=int(data.get("point", 0)),
            query=str(data.get("query", "")),
            reason=str(data.get("reason", "")),
            status=str(data.get("status", STATUS_IN_CODA)),
            queued_at=str(data.get("queued_at", "")),
        )


def coda_file(research_dir: Path) -> Path:
    return research_dir / "bloccate" / "coda.jsonl"


def ricevute_dir(research_dir: Path) -> Path:
    return research_dir / "bloccate" / "ricevute"


def load_coda(research_dir: Path) -> list[BlockedItem]:
    """Carica tutte le righe da research/bloccate/coda.jsonl."""
    fpath = coda_file(research_dir)
    if not fpath.is_file():
        return []
    items: list[BlockedItem] = []
    try:
        with fpath.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    items.append(BlockedItem.from_dict(data))
                except Exception:
                    continue
    except Exception as exc:
        logger.warning("Impossibile leggere %s: %s", fpath, exc)
    return items


def save_coda(research_dir: Path, items: list[BlockedItem]) -> None:
    """Salva le righe in research/bloccate/coda.jsonl."""
    fpath = coda_file(research_dir)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    with fpath.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it.to_dict(), ensure_ascii=False) + "\n")


def enqueue_blocked_page(research_dir: Path, url: str, chapter: int, point: int,
                         query: str, reason: str) -> BlockedItem:
    """Aggiunge una pagina bloccata alla coda se non è già presente."""
    items = load_coda(research_dir)
    normed = norm_url(url)
    for it in items:
        if norm_url(it.url) == normed and it.chapter == chapter and it.point == point:
            return it  # già in coda

    pid = make_page_id(chapter, point, url)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    item = BlockedItem(
        id=pid,
        url=url,
        chapter=chapter,
        point=point,
        query=query,
        reason=reason,
        status=STATUS_IN_CODA,
        queued_at=now,
    )
    items.append(item)
    save_coda(research_dir, items)

    # Assicura anche l'esistenza della cartella ricevute
    ricevute_dir(research_dir).mkdir(parents=True, exist_ok=True)
    return item


def import_ricevute(research_dir: Path, min_chars: int = 400) -> list[dict[str, Any]]:
    """Controlla research/bloccate/ricevute/ e importa i file per gli id in coda.

    Restituisce un resoconto delle operazioni effettuate:
    [{"id": ..., "file": ..., "status": "ok"|"non_riconosciuta"|"non_leggibile", "page": Page | None, "item": BlockedItem | None}]
    """
    r_dir = ricevute_dir(research_dir)
    if not r_dir.is_dir():
        return []

    coda = load_coda(research_dir)
    coda_by_id = {it.id: it for it in coda}

    resoconto: list[dict[str, Any]] = []
    modificati = False

    for fpath in sorted(r_dir.iterdir()):
        if not fpath.is_file():
            continue

        item_id = fpath.stem
        ext = fpath.suffix.lower()

        if ext not in (".html", ".htm", ".txt", ".md", ".pdf"):
            resoconto.append({
                "file": fpath.name,
                "id": item_id,
                "status": STATUS_NON_RICONOSCIUTA,
                "detail": f"Estensione non supportata: {ext}",
            })
            continue

        item = coda_by_id.get(item_id)
        if not item:
            resoconto.append({
                "file": fpath.name,
                "id": item_id,
                "status": STATUS_NON_RICONOSCIUTA,
                "detail": "Nessun id corrispondente in coda.jsonl",
            })
            continue

        # Legge il contenuto
        raw_text = ""
        is_html = ext in (".html", ".htm")
        if ext == ".pdf":
            try:
                from bookwriter.ingest.loaders import load_document
                raw_text = load_document(fpath).raw_text
            except Exception as exc:
                logger.warning("Errore lettura PDF %s: %s", fpath, exc)
                raw_text = ""
        else:
            try:
                raw_text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                logger.warning("Errore lettura testo %s: %s", fpath, exc)
                raw_text = ""

        if not raw_text.strip():
            resoconto.append({
                "file": fpath.name,
                "id": item_id,
                "item": item,
                "status": "vuoto",
                "detail": "Il file è vuoto",
            })
            continue

        if is_html:
            cleaned = pagetext.clean_html(raw_text, item.url, min_chars=min_chars)
            page = Page(
                url=item.url,
                title=item.url,
                text=cleaned.text if cleaned.readable else "",
                readable=cleaned.readable,
                note="" if cleaned.readable else cleaned.reason,
                removed=dict(cleaned.removed),
                chars_before=cleaned.chars_before,
                chars_after=cleaned.chars_after,
            )
        else:
            page = Page(
                url=item.url,
                title=item.url,
                text=raw_text.strip(),
                readable=len(raw_text.strip()) >= min_chars,
                note="" if len(raw_text.strip()) >= min_chars else f"troppo poco testo ({len(raw_text.strip())} caratteri)",
                chars_before=len(raw_text),
                chars_after=len(raw_text.strip()),
            )

        if not page.readable:
            # È ancora bloccata o non leggibile
            resoconto.append({
                "file": fpath.name,
                "id": item_id,
                "item": item,
                "status": "non_leggibile",
                "detail": page.note,
                "page": page,
            })
            continue

        # Aggiorna lo stato nella coda
        item.status = STATUS_RICEVUTA
        modificati = True
        resoconto.append({
            "file": fpath.name,
            "id": item_id,
            "item": item,
            "status": "ok",
            "page": page,
        })

    if modificati:
        save_coda(research_dir, coda)

    return resoconto
