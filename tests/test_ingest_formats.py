"""Formati aggiuntivi dell'importazione degli appunti: scansioni (OCR), audio, posta, pagine web,
fogli di calcolo.

Ogni formato deve uscire come gli altri: un ``LoadedDoc`` con lo scheletro di segmenti e il
testo che ne deriva, così dedup, parser strutturale e chunking non si accorgono di nulla. I
motori pesanti si provano senza scaricare niente: l'OCR vero gira solo se il pacchetto c'è (e i
suoi modelli viaggiano col pacchetto), la trascrizione si prova con un trascrittore finto.
"""

from __future__ import annotations

import importlib.util
import mailbox
import math
import plistlib
import shutil
import struct
import sys
import wave
import zipfile
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import numpy as np
import pytest

from bookwriter.db.project_schema import init_project_db, list_documents
from bookwriter.ingest import audio, mail, ocr, sheets, web
from bookwriter.ingest.errors import LoaderError
from bookwriter.ingest.loaders import FORMAT_GROUPS, SUPPORTED_EXTENSIONS, load_document
from bookwriter.ingest.pipeline import ingest_paths, iter_files
from bookwriter.layout.model import HEADING, PAGEBREAK, PARAGRAPH

HA_OCR = importlib.util.find_spec("rapidocr") is not None
FONT = Path("C:/Windows/Fonts/arial.ttf")


def _coerente(doc) -> None:
    """Le stesse garanzie dei formati di sempre: testo = scheletro, formato = estensione."""
    assert doc.layout is not None
    assert doc.raw_text == doc.layout.to_text()
    assert doc.format == doc.path.suffix.lower().lstrip(".")
    assert doc.title == doc.path.stem


def test_estensioni_supportate_e_gruppi() -> None:
    attese = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp",
              ".mp3", ".wav", ".m4a", ".ogg", ".flac",
              ".eml", ".msg", ".mbox",
              ".html", ".htm", ".mhtml", ".mht", ".url", ".webloc",
              ".csv", ".tsv", ".xlsx", ".xls", ".ods",
              ".txt", ".md", ".pdf", ".docx", ".epub"}
    assert attese <= SUPPORTED_EXTENSIONS
    assert set().union(*map(set, FORMAT_GROUPS.values())) == SUPPORTED_EXTENSIONS


# --------------------------------------------------------------------------- #
#  OCR
# --------------------------------------------------------------------------- #
def _riga(x0, y0, x1, y1, testo, conf=0.95):
    return ocr.RigaOCR(x0, y0, x1, y1, testo, conf)


def test_ocr_righe_ricomposte_in_capoversi() -> None:
    righe = [
        _riga(100, 20, 600, 80, "Il faro"),  # grande e isolata: titolo
        _riga(100, 130, 900, 160, "La prima riga del capoverso arriva al mar-"),
        _riga(100, 165, 900, 195, "gine e continua qui fino in fondo alla"),
        _riga(100, 200, 400, 230, "frase che finisce."),
        _riga(160, 235, 900, 265, "Un rientro apre il secondo capoverso,"),
        _riga(100, 270, 500, 300, "che resta unito."),
        _riga(100, 360, 450, 390, "Dopo uno stacco"),
        _riga(460, 361, 900, 391, "sulla stessa riga."),  # stessa riga, riquadro a parte
        _riga(100, 420, 300, 450, "macchia", conf=0.2),  # sotto soglia: via
    ]
    seg = ocr.righe_a_segmenti(righe)
    assert [s.kind for s in seg] == [HEADING, PARAGRAPH, PARAGRAPH, PARAGRAPH]
    assert seg[0].text == "Il faro"
    assert seg[1].text == ("La prima riga del capoverso arriva al margine e continua qui fino "
                           "in fondo alla frase che finisce.")
    assert seg[2].text == "Un rientro apre il secondo capoverso, che resta unito."
    assert seg[3].text == "Dopo uno stacco sulla stessa riga."
    assert all("macchia" not in s.text for s in seg)


def _immagine_con_testo(righe: list[str], *, size=(1400, 90)):
    from PIL import Image, ImageDraw, ImageFont

    if not FONT.exists():
        pytest.skip("font di sistema non disponibile per disegnare il testo")
    img = Image.new("RGB", (size[0], size[1] * len(righe) + 40), "white")
    disegno = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), 40)
    for i, riga in enumerate(righe):
        disegno.text((40, 30 + i * size[1]), riga, fill="black", font=font)
    return img


@pytest.mark.skipif(not HA_OCR, reason="motore OCR (rapidocr) non installato")
def test_ocr_vero_di_un_immagine_con_gli_accenti(tmp_path: Path) -> None:
    img = _immagine_con_testo(["Perché la città è già lì.", "The lighthouse keeper, Niccolò."])
    percorso = tmp_path / "appunto.png"
    img.save(percorso)
    doc = load_document(percorso)
    _coerente(doc)
    assert "città" in doc.raw_text and "Perché" in doc.raw_text
    assert "lighthouse" in doc.raw_text


def _pdf_misto(percorso: Path, testo_scansione: list[str]) -> None:
    """Pagina 1 con testo vero, pagina 2 fatta di sola immagine (una scansione)."""
    import io

    import fitz

    pdf = fitz.open()
    p1 = pdf.new_page()
    p1.insert_text((72, 100), "Pagina con testo vero, scritta al computer.", fontsize=12)
    p2 = pdf.new_page()
    buf = io.BytesIO()
    _immagine_con_testo(testo_scansione).save(buf, format="PNG")
    p2.insert_image(fitz.Rect(36, 36, 560, 200), stream=buf.getvalue())
    pdf.save(percorso)
    pdf.close()


def test_pdf_scansionato_pagina_letta_col_motore_finto(tmp_path, monkeypatch) -> None:
    percorso = tmp_path / "misto.pdf"
    _pdf_misto(percorso, ["qualunque"])
    assert ocr.pagine_scansionate(percorso) == [1]
    monkeypatch.setattr(ocr, "motore", lambda: object())
    monkeypatch.setattr(ocr, "riconosci", lambda img: [_riga(10, 10, 800, 40, "Testo della scansione.")])
    note: list[str] = []
    doc = load_document(percorso, progress=lambda a, b, n: note.append(n))
    _coerente(doc)
    vero = doc.raw_text.index("Pagina con testo vero")
    scansione = doc.raw_text.index("Testo della scansione.")
    assert vero < scansione  # la pagina letta dall'OCR resta al suo posto
    assert PAGEBREAK in [s.kind for s in doc.layout.segments]
    assert any("OCR pagina 2" in n for n in note)
    assert doc.avvisi == []


def test_pdf_scansionato_senza_motore(tmp_path, monkeypatch) -> None:
    from bookwriter.ingest.errors import dipendenza_mancante

    def manca():
        raise dipendenza_mancante("immagini e PDF scansionati (OCR)", "rapidocr", "ingest")

    monkeypatch.setattr(ocr, "motore", manca)
    misto = tmp_path / "misto.pdf"
    _pdf_misto(misto, ["qualunque"])
    doc = load_document(misto)  # il testo che c'è entra, con un avviso per la pagina mancante
    assert "Pagina con testo vero" in doc.raw_text
    assert doc.avvisi and "2" in doc.avvisi[0] and "rapidocr" in doc.avvisi[0]

    import fitz

    tutto = tmp_path / "scanner.pdf"
    pdf = fitz.open(misto)
    pdf.delete_page(0)
    pdf.save(tutto)
    pdf.close()
    with pytest.raises(LoaderError, match="ingest"):
        load_document(tutto)
    with pytest.raises(LoaderError, match="rapidocr"):
        load_document(_salva_png(tmp_path / "foto.png"))


def _salva_png(percorso: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (50, 50), "white").save(percorso)
    return percorso


@pytest.mark.skipif(not HA_OCR, reason="motore OCR (rapidocr) non installato")
def test_pdf_scansionato_vero(tmp_path: Path) -> None:
    percorso = tmp_path / "misto.pdf"
    _pdf_misto(percorso, ["Il quaderno scansionato dice: perché no."])
    doc = load_document(percorso)
    assert "quaderno scansionato" in doc.raw_text
    assert doc.raw_text.index("Pagina con testo vero") < doc.raw_text.index("quaderno")


def test_immagine_senza_testo_e_un_errore(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ocr, "motore", lambda: object())
    monkeypatch.setattr(ocr, "riconosci", lambda img: [])
    with pytest.raises(LoaderError, match="nessun testo"):
        load_document(_salva_png(tmp_path / "bianca.png"))


# --------------------------------------------------------------------------- #
#  audio
# --------------------------------------------------------------------------- #
SR = audio.CAMPIONAMENTO


class SorgenteFinta:
    """Audio sintetico: rumore, con un silenzio nel punto in cui ci si aspetta il taglio."""

    def __init__(self, secondi: float, silenzi: list[float]):
        rng = np.random.default_rng(0)
        self.campioni = (rng.standard_normal(int(secondi * SR)) * 0.3).astype(np.float32)
        for t in silenzi:
            a = int(t * SR)
            self.campioni[a - SR // 2:a + SR // 2] = 0.0
        self.durata = secondi
        self.letture: list[tuple[float, float]] = []

    def pezzo(self, inizio, durata):
        self.letture.append((inizio, durata))
        a = round(inizio * SR)
        return self.campioni[a:a + int(durata * SR)]


def test_punto_di_taglio_cade_nel_silenzio() -> None:
    sorgente = SorgenteFinta(60, silenzi=[47.0])
    taglio = audio.punto_di_taglio(sorgente.campioni, da=30, a=60)
    assert abs(taglio / SR - 47.0) < 0.5


def test_trascrizione_a_pezzi_con_trascrittore_finto(monkeypatch) -> None:
    monkeypatch.setattr(audio, "PEZZO_SECONDI", 60.0)
    monkeypatch.setattr(audio, "MARGINE_SECONDI", 10.0)
    sorgente = SorgenteFinta(150, silenzi=[57.0, 118.0])
    chiamate: list[tuple[float, str | None]] = []

    def trascrittore(campioni, lingua):
        n = len(chiamate)
        chiamate.append((len(campioni) / SR, lingua))
        # una frase a 1 s dall'inizio del pezzo, una a 20 s (dopo una pausa lunga)
        return [(1.0, 3.0, f"Frase uno del pezzo {n}."), (20.0, 22.0, f"Frase due del pezzo {n}.")], "it"

    avanzamenti: list[tuple[int, int, str]] = []
    seg = audio.trascrivi(Path("finto.wav"), trascrittore=trascrittore, sorgente=sorgente,
                          progress=lambda a, b, n: avanzamenti.append((a, b, n)))
    # tre pezzi, tagliati nei silenzi e non a 60 s fissi
    assert len(chiamate) == 3
    assert abs(chiamate[0][0] - 57.0) < 0.5
    assert abs(sum(c[0] for c in chiamate) - 150.0) < 0.01
    # la lingua riconosciuta sul primo pezzo passa ai successivi
    assert [c[1] for c in chiamate] == [None, "it", "it"]
    # tempi assoluti nei riferimenti dei capoversi
    assert seg[0].ref == "t=0:01"
    assert any(s.ref == "t=0:58" for s in seg)  # 57 s di taglio + 1 s nel secondo pezzo
    assert all(s.kind == PARAGRAPH for s in seg)
    assert len(seg) == 6  # ogni pausa di 17 s apre un capoverso
    # avanzamento crescente, fino in fondo
    fatti = [a for a, _b, _n in avanzamenti]
    assert fatti == sorted(fatti) and avanzamenti[-1][0] == avanzamenti[-1][1] == 150


def test_frasi_a_capoversi() -> None:
    frasi = [(0.0, 2.0, "Prima frase."), (2.2, 4.0, "Seconda frase."), (4.1, 5.0, "Seconda frase."),
             (9.0, 10.0, "Dopo la pausa.")]
    seg = audio.frasi_a_segmenti(frasi)
    assert [s.text for s in seg] == ["Prima frase. Seconda frase.", "Dopo la pausa."]
    assert [s.ref for s in seg] == ["t=0:00", "t=0:09"]


def test_audio_senza_faster_whisper(monkeypatch, tmp_path) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(LoaderError, match=r"\[audio\]"):
        audio.trascrittore_whisper()


def _wav(percorso: Path, secondi: float = 2.0) -> Path:
    with wave.open(str(percorso), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 22050)))
                               for i in range(int(secondi * 22050))))
    return percorso


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg non disponibile")
def test_audio_vero_letto_con_ffmpeg_e_trascrittore_finto(tmp_path, monkeypatch) -> None:
    percorso = _wav(tmp_path / "memo.wav")
    sorgente = audio.SorgenteAudio(percorso)
    assert abs(sorgente.durata - 2.0) < 0.1
    assert abs(len(sorgente.pezzo(0.5, 1.0)) - SR) < SR * 0.02  # ricampionato a 16 kHz

    monkeypatch.setattr(audio, "trascrittore_whisper",
                        lambda dispositivo=None: (lambda c, l: ([(0.0, 1.5, "Ciao dal memo.")], "it")))
    doc = load_document(percorso)
    _coerente(doc)
    assert doc.raw_text == "Ciao dal memo."


# --------------------------------------------------------------------------- #
#  posta
# --------------------------------------------------------------------------- #
def _email(oggetto="Appunti sul faro", corpo="", **kw) -> EmailMessage:
    m = EmailMessage()
    m["Subject"] = oggetto
    m["From"] = "Mario Rossi <mario@example.com>"
    m["To"] = "autore@example.com"
    m["Date"] = "Tue, 14 Oct 2025 10:30:00 +0200"
    m.set_content(corpo)
    return m


def test_eml_con_risposta_citata_firma_e_allegati(tmp_path: Path) -> None:
    corpo = ("Ecco i ricordi del guardiano.\n\nIl faro fu spento nel 1943.\n\n"
             "Inviato da iPhone\n\n"
             "Il giorno lun 13 ott 2025 alle ore 09:00 Autore <autore@example.com> ha scritto:\n"
             "> Mi mandi i ricordi?\n> Grazie\n")
    m = _email(corpo=corpo)
    m.add_attachment("Nota allegata: il guardiano si chiamava Niccolò.".encode(),
                     maintype="text", subtype="plain", filename="nota.txt")
    m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="mappa.pdf")
    percorso = tmp_path / "messaggio.eml"
    percorso.write_bytes(bytes(m))

    doc = load_document(percorso)
    _coerente(doc)
    assert doc.layout.segments[0].kind == HEADING
    assert doc.layout.segments[0].text == "Appunti sul faro"
    assert "Da: Mario Rossi <mario@example.com>" in doc.raw_text
    assert "Data: 2025-10-14 10:30" in doc.raw_text
    assert "Il faro fu spento nel 1943." in doc.raw_text
    assert "Mi mandi" not in doc.raw_text and "ha scritto" not in doc.raw_text
    assert "iPhone" not in doc.raw_text
    assert "Allegati: nota.txt, mappa.pdf" in doc.raw_text
    assert "il guardiano si chiamava Niccolò" in doc.raw_text


def test_pulizia_del_corpo() -> None:
    outlook = ("Va bene così.\n\n________________________________\n"
               "Da: Autore <a@x.it>\nInviato: lunedì 13 ottobre 2025 09:00\nA: Mario\n"
               "Oggetto: domanda\n\nTesto vecchio")
    assert mail.pulisci_corpo(outlook) == "Va bene così."
    inoltro = ("Guarda qui sotto.\n\n---------- Forwarded message ---------\nFrom: Teste <t@x.it>\n"
               "Date: Mon, 13 Oct 2025\nSubject: ricordi\n\nIl racconto del testimone.")
    assert "Il racconto del testimone." in mail.pulisci_corpo(inoltro)
    # Outlook inoltra con la stessa intestazione con cui risponde: lo dice l'oggetto.
    assert "Testo vecchio" in mail.pulisci_corpo(outlook, inoltro=True)
    assert mail.e_un_inoltro("I: ricordi") and mail.e_un_inoltro("Fwd: x") and not mail.e_un_inoltro("Idee")
    # Una frase qualunque che finisce con «ha scritto:» non è un'intestazione di risposta.
    frase = "Il capitolo che Marta ha scritto:\nè il migliore."
    assert mail.pulisci_corpo(frase) == frase
    # Tutto citato: meglio il rumore del vuoto.
    assert mail.pulisci_corpo("> solo citazione") == "> solo citazione"
    # Firma standard.
    assert mail.pulisci_corpo("Testo.\n-- \nMario Rossi\nTel. 123") == "Testo."


def test_eml_solo_html(tmp_path: Path) -> None:
    m = EmailMessage()
    m["Subject"] = "Solo HTML"
    m.set_content("<html><body><p>Il <b>faro</b> di notte.</p></body></html>", subtype="html")
    percorso = tmp_path / "html.eml"
    percorso.write_bytes(bytes(m))
    doc = load_document(percorso)
    assert "Il faro di notte." in doc.raw_text
    assert "<p>" not in doc.raw_text


def test_mbox_piu_messaggi(tmp_path: Path) -> None:
    percorso = tmp_path / "casella.mbox"
    casella = mailbox.mbox(str(percorso))
    casella.add(_email("Primo", "Corpo del primo."))
    casella.add(_email("Secondo", "Corpo del secondo."))
    casella.flush()
    casella.close()
    note: list[str] = []
    doc = load_document(percorso, progress=lambda a, b, n: note.append(n))
    _coerente(doc)
    titoli = [s.text for s in doc.layout.segments if s.kind == HEADING]
    assert titoli == ["Primo", "Secondo"]
    assert doc.raw_text.index("Corpo del primo.") < doc.raw_text.index("Corpo del secondo.")
    assert note


class _AllegatoMsg:
    def __init__(self, nome, dati):
        self.longFilename = nome
        self.data = dati


class _MsgFinto:
    subject = "Riunione"
    sender = "Lucia <lucia@example.com>"
    to = "autore@example.com"
    date = datetime(2025, 10, 1, 18, 5)  # noqa: DTZ001 - come la dà extract-msg
    body = "Ci vediamo al faro.\n\nDa: Autore\nInviato: ieri\nA: Lucia\n\nvecchio"
    htmlBody = None
    attachments = (
        _AllegatoMsg("scaletta.txt", b"1. arrivo\n2. tempesta"),
        _AllegatoMsg("foto.jpg", b"\xff\xd8"),
    )


def test_msg_outlook_letto_dalla_struttura_di_extract_msg() -> None:
    m = mail.messaggio_da_msg(_MsgFinto())
    seg = mail.messaggio_a_segmenti(m)
    testo = "\n\n".join(s.text for s in seg)
    assert seg[0].text == "Riunione"
    assert "Data: 2025-10-01 18:05" in testo
    assert "Ci vediamo al faro." in testo and "vecchio" not in testo
    assert "Allegati: scaletta.txt, foto.jpg" in testo
    assert "2. tempesta" in testo


def test_msg_senza_extract_msg(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "extract_msg", None)
    percorso = tmp_path / "x.msg"
    percorso.write_bytes(b"\xd0\xcf\x11\xe0")
    with pytest.raises(LoaderError, match="extract-msg"):
        load_document(percorso)


# --------------------------------------------------------------------------- #
#  pagine web
# --------------------------------------------------------------------------- #
PAGINA = """<!DOCTYPE html>
<!-- saved from url=(0035)https://example.com/articolo-faro -->
<html><head><title>Il faro di Punta Nera</title>
<link rel="canonical" href="https://example.com/faro"></head>
<body><nav><ul><li><a href="/">Home</a></li><li><a href="/x">Chi siamo</a></li></ul></nav>
<script>var x = 1;</script>
<article><h1>Il faro di Punta Nera</h1>
<p>Il faro fu acceso per la prima volta nel 1871 e il guardiano viveva nella torre con la famiglia.</p>
<h2>La tempesta</h2>
<p>Nel 1943 una tempesta distrusse la lanterna; la riaccesero solo dopo la guerra.</p></article>
<footer><p>© 2025 Example. Tutti i diritti riservati.</p></footer></body></html>"""


def test_pagina_web_salvata(tmp_path: Path) -> None:
    percorso = tmp_path / "faro.html"
    percorso.write_text(PAGINA, encoding="utf-8")
    doc = load_document(percorso)
    _coerente(doc)
    seg = doc.layout.segments
    assert seg[0].kind == HEADING and seg[0].text == "Il faro di Punta Nera"
    assert "Fonte: https://example.com/faro" in doc.raw_text
    assert "## La tempesta" in doc.raw_text  # il titolo interno resta un titolo
    assert "1871" in doc.raw_text and "1943" in doc.raw_text
    assert "Chi siamo" not in doc.raw_text and "diritti riservati" not in doc.raw_text
    assert "var x" not in doc.raw_text


def test_nota_esportata_in_html_non_perde_le_righe_brevi(tmp_path: Path) -> None:
    nota = ("<html><body><h2>Da fare</h2><ul><li>Faro</li><li>Mappa</li><li>Nomi</li>"
            "<li>Date</li></ul><p>Prima riga<br>seconda riga</p></body></html>")
    percorso = tmp_path / "nota.htm"
    percorso.write_text(nota, encoding="utf-8")
    doc = load_document(percorso)
    for voce in ("- Faro", "- Mappa", "- Nomi", "- Date"):
        assert voce in doc.raw_text
    assert "Prima riga\nseconda riga" in doc.raw_text


def test_mhtml(tmp_path: Path) -> None:
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("related", type="text/html")
    msg["Subject"] = "Il faro di Punta Nera"
    msg["Snapshot-Content-Location"] = "https://example.com/faro-mhtml"
    parte = MIMEText(PAGINA, "html", "utf-8")
    parte["Content-Location"] = "https://example.com/faro-mhtml"
    msg.attach(parte)
    percorso = tmp_path / "faro.mhtml"
    percorso.write_bytes(msg.as_bytes())
    doc = load_document(percorso)
    _coerente(doc)
    assert "Fonte: https://example.com/faro-mhtml" in doc.raw_text
    assert "1871" in doc.raw_text


def test_collegamenti_url_e_webloc(tmp_path: Path) -> None:
    url = tmp_path / "Articolo sul faro.url"
    url.write_text("[{000214A0-0000-0000-C000-000000000046}]\nProp3=19,11\n"
                   "[InternetShortcut]\nIDList=\nURL=https://example.com/faro\n", encoding="utf-8")
    doc = load_document(url)
    _coerente(doc)
    assert "Fonte: https://example.com/faro" in doc.raw_text
    assert doc.avvisi and "non è stata scaricata" in doc.avvisi[0]

    webloc = tmp_path / "faro.webloc"
    with webloc.open("wb") as f:
        plistlib.dump({"URL": "https://example.com/webloc"}, f)
    assert web.indirizzo_del_collegamento(webloc) == "https://example.com/webloc"

    scaricati: list[str] = []
    doc = load_document(webloc, scarica_pagina=lambda u: scaricati.append(u) or PAGINA)
    assert scaricati == ["https://example.com/webloc"]
    assert "1871" in doc.raw_text and doc.avvisi == []


# --------------------------------------------------------------------------- #
#  fogli di calcolo
# --------------------------------------------------------------------------- #
def test_csv_italiano_con_punto_e_virgola(tmp_path: Path) -> None:
    percorso = tmp_path / "personaggi.csv"
    percorso.write_bytes("Nome;Ruolo;Anno\nNiccolò;guardiano;1943\nMarta;;1950\n".encode("cp1252"))
    doc = load_document(percorso)
    _coerente(doc)
    assert "Nome: Niccolò\nRuolo: guardiano\nAnno: 1943" in doc.raw_text
    assert "Nome: Marta\nAnno: 1950" in doc.raw_text  # la cella vuota non compare


def test_tsv_senza_intestazione(tmp_path: Path) -> None:
    percorso = tmp_path / "date.tsv"
    percorso.write_text("1871\taccensione\n1943\ttempesta\n", encoding="utf-8")
    doc = load_document(percorso)
    assert "1871 · accensione" in doc.raw_text and "1943 · tempesta" in doc.raw_text


def test_xlsx_fogli_come_sezioni(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    libro = openpyxl.Workbook()
    foglio = libro.active
    foglio.title = "Cronologia"
    foglio.append(["Data", "Evento", "Vittime"])
    foglio.append([datetime(1943, 11, 2),  # noqa: DTZ001 - Excel non ha fusi orari
                   "Tempesta", 3.0])
    foglio.append([datetime(1871, 5, 1, 20, 30),  # noqa: DTZ001
                   "Prima accensione", None])
    secondo = libro.create_sheet("Luoghi")
    secondo.append(["Luogo", "Note"])
    secondo.append(["Punta Nera", "scogli"])
    percorso = tmp_path / "libro.xlsx"
    libro.save(percorso)

    doc = load_document(percorso)
    _coerente(doc)
    titoli = [s.text for s in doc.layout.segments if s.kind == HEADING]
    assert titoli == ["Cronologia", "Luoghi"]
    assert "Data: 1943-11-02\nEvento: Tempesta\nVittime: 3" in doc.raw_text
    assert "Data: 1871-05-01 20:30\nEvento: Prima accensione" in doc.raw_text
    assert "Luogo: Punta Nera\nNote: scogli" in doc.raw_text


def _ods(percorso: Path) -> Path:
    contenuto = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">
<office:body><office:spreadsheet>
<table:table table:name="Personaggi">
 <table:table-row>
  <table:table-cell office:value-type="string"><text:p>Nome</text:p></table:table-cell>
  <table:table-cell office:value-type="string"><text:p>Età</text:p></table:table-cell>
  <table:table-cell table:number-columns-repeated="16000"/>
 </table:table-row>
 <table:table-row>
  <table:table-cell office:value-type="string"><text:p>Niccolò<text:s text:c="2"/>il <text:span>vecchio</text:span></text:p></table:table-cell>
  <table:table-cell office:value-type="float" office:value="71"><text:p>71</text:p></table:table-cell>
 </table:table-row>
 <table:table-row table:number-rows-repeated="1048570"><table:table-cell table:number-columns-repeated="16000"/></table:table-row>
</table:table>
</office:spreadsheet></office:body></office:document-content>"""
    with zipfile.ZipFile(percorso, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", contenuto)
    return percorso


def test_ods_senza_dipendenze(tmp_path: Path) -> None:
    doc = load_document(_ods(tmp_path / "personaggi.ods"))
    _coerente(doc)
    assert doc.layout.segments[0].text == "Personaggi"
    assert "Nome: Niccolò il vecchio\nEtà: 71" in doc.raw_text  # <text:s>, <text:span>
    assert doc.raw_text.count("Nome:") == 1  # le righe vuote ripetute fino al bordo non contano


def test_fogli_senza_librerie(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    monkeypatch.setitem(sys.modules, "xlrd", None)
    for nome, pacchetto in (("a.xlsx", "openpyxl"), ("a.xls", "xlrd")):
        percorso = tmp_path / nome
        percorso.write_bytes(b"x")
        with pytest.raises(LoaderError, match=pacchetto):
            load_document(percorso)


def test_celle_scritte_come_le_scriverebbe_una_persona() -> None:
    assert sheets.cella_a_testo(12.0) == "12"
    assert sheets.cella_a_testo(12.5) == "12.5"
    assert sheets.cella_a_testo(True) == "sì"
    assert sheets.cella_a_testo(None) == ""
    assert sheets.cella_a_testo(float("nan")) == ""


# --------------------------------------------------------------------------- #
#  pipeline
# --------------------------------------------------------------------------- #
def test_pipeline_importa_i_formati_nuovi(tmp_path: Path) -> None:
    appunti = tmp_path / "appunti"
    appunti.mkdir()
    (appunti / "faro.html").write_text(PAGINA, encoding="utf-8")
    (appunti / "personaggi.csv").write_text("Nome,Ruolo\nNiccolò,guardiano\n", encoding="utf-8")
    (appunti / "messaggio.eml").write_bytes(bytes(_email(corpo="Il faro fu spento nel 1943.")))
    (appunti / "link.url").write_text("[InternetShortcut]\nURL=https://example.com/x\n",
                                      encoding="utf-8")
    (appunti / "ignora.xyz").write_text("no", encoding="utf-8")
    trovati = {p.name for p in iter_files([appunti], recursive=False)}
    assert trovati == {"faro.html", "personaggi.csv", "messaggio.eml", "link.url"}

    db = tmp_path / "project.db"
    init_project_db(db)
    note: list[str] = []
    report = ingest_paths([appunti], db_path=db, target_tokens=200,
                          progress=lambda a, b, n: note.append(n))
    assert report.failed == []
    assert len(report.imported) == 4 and report.chunks >= 4
    assert len(report.warnings) == 1 and "link.url" in report.warnings[0][0]
    assert "avvisi=1" in report.summary()
    formati = {d["format"] for d in list_documents(db)}
    assert formati == {"html", "csv", "eml", "url"}
    # re-importazione idempotente anche per i formati nuovi
    assert ingest_paths([appunti], db_path=db).imported == []


def test_pipeline_note_di_avanzamento_dentro_il_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(ocr, "motore", lambda: object())
    monkeypatch.setattr(ocr, "riconosci", lambda img: [_riga(10, 10, 800, 40, "Scritto a mano.")])
    foto = _salva_png(tmp_path / "foto.png")
    db = tmp_path / "project.db"
    init_project_db(db)
    note: list[tuple[int, int, str]] = []
    report = ingest_paths([foto], db_path=db, progress=lambda a, b, n: note.append((a, b, n)))
    assert report.imported and report.failed == []
    assert any(n.startswith("foto.png — OCR") and a == 0 and b == 1 for a, b, n in note)
