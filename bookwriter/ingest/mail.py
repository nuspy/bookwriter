"""Appunti arrivati per posta: messaggi salvati (.eml, .msg) e caselle intere (.mbox).

Molto materiale di un libro passa per la posta: l'intervista mandata per scritto, la nota che
l'autore si spedisce da solo, lo scambio con un testimone. Di un messaggio contano chi l'ha
scritto, quando e che cosa: oggetto, mittente, data e corpo diventano testo, e il corpo perde il
rumore delle risposte — la citazione del messaggio precedente («Il giorno … ha scritto:»,
le righe con «>», l'intestazione di Outlook «Da: … Inviato: …»), la firma, il «Inviato da
iPhone». Un inoltro invece si tiene: chi inoltra un messaggio lo fa proprio per quel contenuto.

Gli allegati si elencano per nome; quelli di testo semplice (.txt, .md) si leggono anche, perché
sono spesso proprio gli appunti. Un messaggio allegato a un messaggio si legge come messaggio.

.eml e .mbox si leggono con la libreria standard; .msg (Outlook) con ``extract-msg``, extra
``ingest``.
"""

from __future__ import annotations

import mailbox
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path

from bookwriter.ingest.errors import LoaderError, dipendenza_mancante
from bookwriter.layout.model import HEADING, PARAGRAPH, Segment

#: Messaggi e caselle di posta.
MAIL_EXTENSIONS = {".eml", ".msg", ".mbox"}

#: Quanto testo si legge, al massimo, da un allegato di testo.
MAX_ALLEGATO_CARATTERI = 200_000

Progress = Callable[[int, int, str], None]

_ALLEGATI_DI_TESTO = {"text/plain", "text/markdown", "text/x-markdown"}
_SUFFISSI_DI_TESTO = {".txt", ".md", ".markdown", ".text"}


@dataclass
class Messaggio:
    """Un messaggio ridotto a quel che serve al libro."""

    oggetto: str = ""
    mittente: str = ""
    destinatari: str = ""
    data: str = ""
    corpo: str = ""
    allegati: list[str] = field(default_factory=list)
    allegati_di_testo: list[tuple[str, str]] = field(default_factory=list)
    annidati: list[Messaggio] = field(default_factory=list)


# --------------------------------------------------------------------------- #
#  il corpo, senza il rumore delle risposte
# --------------------------------------------------------------------------- #
_RISPOSTA = re.compile(
    r"^\s*(on\b.{0,200}\bwrote|il giorno\b.{0,200}\bha scritto|il\b.{0,200}\bha scritto|"
    r"le\b.{0,200}\ba écrit|am\b.{0,200}\bschrieb|el\b.{0,200}\bescribió)\s*:\s*$",
    re.IGNORECASE)
_ORIGINALE = re.compile(
    r"^\s*-{2,}\s*(original message|messaggio originale|message d'origine|"
    r"ursprüngliche nachricht|mensaje original)\s*-{2,}\s*$", re.IGNORECASE)
_INOLTRO = re.compile(
    r"(forwarded message|messaggio inoltrato|inizio messaggio inoltrato|begin forwarded message|"
    r"message transféré|weitergeleitete nachricht)", re.IGNORECASE)
_DA = re.compile(r"^\s*\**(from|da|de|von)\**\s*:", re.IGNORECASE)
_INVIATO = re.compile(r"^\s*\**(sent|inviato|date|data|envoyé|gesendet)\**\s*:", re.IGNORECASE)
_DAL_TELEFONO = re.compile(
    r"^\s*(sent from my \w+|inviato da(l mio)? (iphone|ipad|android|samsung|outlook|smartphone)|"
    r"get outlook for \w+|scarica outlook per \w+)\b.*$", re.IGNORECASE)
_SEPARATORE_OUTLOOK = re.compile(r"^\s*_{10,}\s*$")
_OGGETTO_INOLTRO = re.compile(r"^\s*(fwd?|i|inoltro|tr|wg|rv)\s*:", re.IGNORECASE)


def e_un_inoltro(oggetto: str) -> bool:
    """Vero se l'oggetto dice che il messaggio è un inoltro («Fwd:», «I:», «TR:»…)."""
    return bool(_OGGETTO_INOLTRO.match(oggetto or ""))


def _risposta(riga: str) -> bool:
    # Le intestazioni di risposta portano sempre una data: senza cifre, una frase come «Il
    # capitolo che ha scritto:» non è un'intestazione e non deve tagliare il messaggio.
    return bool(_RISPOSTA.match(riga)) and bool(re.search(r"\d", riga))


def pulisci_corpo(testo: str, *, inoltro: bool = False) -> str:
    """Il corpo del messaggio senza la citazione del precedente, la firma e le righe di rito.

    Si taglia alla prima intestazione di risposta (Gmail, Apple Mail, Thunderbird, Outlook, in
    più lingue); le righe citate con «>» si tolgono ovunque, anche nelle risposte intercalate,
    dove resta il testo di chi risponde. Un'intestazione «Da:/Data:» preceduta dalla riga di un
    inoltro non è una risposta e non si taglia. Se dopo la pulizia non resta niente (un messaggio
    fatto solo di citazione) si tiene il corpo originale: meglio il rumore che il vuoto.

    Con ``inoltro=True`` (l'oggetto lo dice) le intestazioni «Da:/Inviato:» non tagliano: Outlook
    inoltra con la stessa intestazione con cui risponde, e lì sotto c'è proprio il contenuto.
    """
    righe = (testo or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    tenute: list[str] = []
    ultima_piena = ""
    for i, riga in enumerate(righe):
        doppia = f"{riga.strip()} {righe[i + 1].strip()}" if i + 1 < len(righe) else riga
        if _risposta(riga) or (not riga.strip().endswith(":") and _risposta(doppia)):
            break
        if _ORIGINALE.match(riga):
            break
        inoltrato = inoltro or bool(_INOLTRO.search(ultima_piena))
        if _SEPARATORE_OUTLOOK.match(riga) and not inoltrato:
            seguito = [r for r in righe[i + 1:i + 4] if r.strip()]
            if seguito and _DA.match(seguito[0]):
                break
        if _DA.match(riga) and not inoltrato and any(_INVIATO.match(r) for r in righe[i + 1:i + 5]):
            break
        if riga.rstrip() in ("--", "-- "):
            break  # il delimitatore di firma standard
        if riga.lstrip().startswith(">"):
            continue
        if _DAL_TELEFONO.match(riga):
            continue
        tenute.append(riga)
        if riga.strip():
            ultima_piena = riga
    pulito = re.sub(r"\n{3,}", "\n\n", "\n".join(tenute)).strip()
    return pulito or (testo or "").strip()


# --------------------------------------------------------------------------- #
#  dai formati al messaggio
# --------------------------------------------------------------------------- #
def _data_leggibile(valore) -> str:
    if valore is None:
        return ""
    if hasattr(valore, "strftime"):
        return valore.strftime("%Y-%m-%d %H:%M")
    testo = str(valore).strip()
    try:
        return parsedate_to_datetime(testo).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, IndexError):
        return testo


def _testo_html(html: str) -> str:
    from bookwriter.ingest.web import segmenti_da_html

    _titolo, segmenti = segmenti_da_html(html)
    return "\n\n".join(s.text for s in segmenti if s.kind == PARAGRAPH and s.style != "source")


def _contenuto(parte) -> str:
    if parte.get_content_maintype() == "text" and not parte.get_content_charset():
        # Testo senza charset dichiarato: la libreria lo leggerebbe come ASCII e gli accenti
        # diventerebbero punti interrogativi. Quasi sempre è UTF-8; se non lo è, Windows.
        dati = parte.get_payload(decode=True) or b""
        try:
            return dati.decode("utf-8")
        except UnicodeDecodeError:
            return dati.decode("cp1252", errors="replace")
    try:
        contenuto = parte.get_content()
    except (LookupError, ValueError, AttributeError):
        dati = parte.get_payload(decode=True) or b""
        contenuto = dati.decode(parte.get_content_charset() or "utf-8", errors="replace")
    if isinstance(contenuto, bytes):
        contenuto = contenuto.decode("utf-8", errors="replace")
    return str(contenuto)


def messaggio_da_email(msg: EmailMessage) -> Messaggio:
    """Un messaggio MIME (``policy.default``) → :class:`Messaggio`."""
    m = Messaggio(
        oggetto=str(msg.get("Subject") or "").strip(),
        mittente=str(msg.get("From") or "").strip(),
        destinatari=str(msg.get("To") or "").strip(),
        data=_data_leggibile(msg.get("Date")),
    )
    corpo = msg.get_body(preferencelist=("plain", "html"))
    if corpo is not None:
        testo = _contenuto(corpo)
        m.corpo = _testo_html(testo) if corpo.get_content_type() == "text/html" else testo
    for allegato in msg.iter_attachments():
        tipo = allegato.get_content_type()
        if tipo == "message/rfc822":
            interno = allegato.get_payload()
            interno = interno[0] if isinstance(interno, list) and interno else interno
            if isinstance(interno, EmailMessage):
                m.annidati.append(messaggio_da_email(interno))
                continue
        nome = allegato.get_filename() or ""
        if nome:
            m.allegati.append(nome)
        if tipo in _ALLEGATI_DI_TESTO or Path(nome).suffix.lower() in _SUFFISSI_DI_TESTO:
            testo = _contenuto(allegato).strip()
            if testo:
                m.allegati_di_testo.append((nome or "allegato", testo[:MAX_ALLEGATO_CARATTERI]))
    return m


def messaggio_da_msg(msg) -> Messaggio:
    """Un messaggio di Outlook letto con ``extract-msg`` → :class:`Messaggio`."""
    corpo = getattr(msg, "body", None) or ""
    if not str(corpo).strip():
        html = getattr(msg, "htmlBody", None) or b""
        corpo = _testo_html(html.decode("utf-8", "replace") if isinstance(html, bytes) else html)
    m = Messaggio(
        oggetto=str(getattr(msg, "subject", "") or "").strip(),
        mittente=str(getattr(msg, "sender", "") or "").strip(),
        destinatari=str(getattr(msg, "to", "") or "").strip(),
        data=_data_leggibile(getattr(msg, "date", None)),
        corpo=str(corpo),
    )
    for allegato in getattr(msg, "attachments", None) or []:
        dati = getattr(allegato, "data", None)
        if dati is not None and hasattr(dati, "subject") and hasattr(dati, "body"):
            m.annidati.append(messaggio_da_msg(dati))  # un .msg allegato a un .msg
            continue
        nome = (getattr(allegato, "longFilename", None) or getattr(allegato, "shortFilename", None)
                or getattr(allegato, "name", None) or "")
        if nome:
            m.allegati.append(str(nome))
        if Path(str(nome)).suffix.lower() in _SUFFISSI_DI_TESTO and isinstance(dati, bytes):
            testo = dati.decode("utf-8", errors="replace").strip()
            if testo:
                m.allegati_di_testo.append((str(nome), testo[:MAX_ALLEGATO_CARATTERI]))
    return m


# --------------------------------------------------------------------------- #
#  dal messaggio ai segmenti
# --------------------------------------------------------------------------- #
def _capoversi(testo: str, **campi) -> list[Segment]:
    return [Segment(kind=PARAGRAPH, text=b.strip(), **campi)
            for b in re.split(r"\n\s*\n", testo or "") if b.strip()]


def messaggio_a_segmenti(m: Messaggio, *, livello: int = 2) -> list[Segment]:
    """Oggetto come titolo, poi chi/quando, il corpo ripulito, gli allegati."""
    segmenti = [Segment(kind=HEADING, text=m.oggetto or "(senza oggetto)", level=livello)]
    intestazione = [f"{nome}: {valore}" for nome, valore in
                    (("Da", m.mittente), ("A", m.destinatari), ("Data", m.data)) if valore]
    if intestazione:
        segmenti.append(Segment(kind=PARAGRAPH, text="\n".join(intestazione), style="mail-header"))
    segmenti.extend(_capoversi(pulisci_corpo(m.corpo, inoltro=e_un_inoltro(m.oggetto))))
    if m.allegati:
        segmenti.append(Segment(kind=PARAGRAPH, text="Allegati: " + ", ".join(m.allegati),
                                style="mail-attachments"))
    for nome, testo in m.allegati_di_testo:
        segmenti.append(Segment(kind=HEADING, text=f"Allegato: {nome}", level=min(livello + 1, 6)))
        segmenti.extend(_capoversi(testo))
    for interno in m.annidati:
        segmenti.extend(messaggio_a_segmenti(interno, livello=min(livello + 1, 6)))
    return segmenti


# --------------------------------------------------------------------------- #
#  i file
# --------------------------------------------------------------------------- #
def _analizza(dati: bytes) -> EmailMessage:
    return BytesParser(policy=policy.default).parsebytes(dati)


def leggi_eml(path: Path) -> tuple[str, list[Segment]]:
    m = messaggio_da_email(_analizza(path.read_bytes()))
    return m.oggetto or path.stem, messaggio_a_segmenti(m)


def leggi_msg(path: Path) -> tuple[str, list[Segment]]:
    try:
        import extract_msg
    except ModuleNotFoundError as exc:
        raise dipendenza_mancante("messaggi di Outlook (.msg)", "extract-msg", "ingest") from exc
    try:
        msg = extract_msg.openMsg(str(path))
    except Exception as exc:  # il pacchetto ha molte eccezioni sue
        raise LoaderError(f"Messaggio di Outlook non leggibile: {path.name} ({exc})") from exc
    try:
        m = messaggio_da_msg(msg)
    finally:
        chiudi = getattr(msg, "close", None)
        if chiudi:
            chiudi()
    return m.oggetto or path.stem, messaggio_a_segmenti(m)


def leggi_mbox(path: Path, *, progress: Progress | None = None
               ) -> tuple[str, list[Segment], list[str]]:
    """Una casella intera: un messaggio dopo l'altro, nell'ordine del file."""
    casella = mailbox.mbox(str(path), factory=None, create=False)
    segmenti: list[Segment] = []
    avvisi: list[str] = []
    try:
        chiavi = list(casella.keys())
        for n, chiave in enumerate(chiavi):
            if progress and n % 20 == 0:
                progress(n, len(chiavi), f"messaggio {n + 1} di {len(chiavi)}")
            try:
                m = messaggio_da_email(_analizza(casella.get_bytes(chiave)))
            except Exception as exc:  # noqa: BLE001 - un messaggio rotto non ferma la casella
                avvisi.append(f"{path.name}: messaggio {n + 1} non leggibile ({exc})")
                continue
            segmenti.extend(messaggio_a_segmenti(m))
    finally:
        casella.close()
    if progress:
        progress(len(chiavi), len(chiavi), "posta letta")
    return path.stem, segmenti, avvisi
