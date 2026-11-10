"""Appunti a voce: la trascrizione dei file audio.

Un autore detta: in macchina, camminando, appena sveglio. Quei memo vocali sono appunti come gli
altri e devono poter entrare nel libro senza che qualcuno li riascolti e li ribatta.

La trascrizione la fa ``faster-whisper`` (extra ``audio``): Whisper su CTranslate2, in locale,
sulla CPU per impostazione (``DISPOSITIVO``), con il modello ``MODELLO``. Il modello non viaggia
col pacchetto: al primo uso si scarica da Hugging Face (circa 480 MB per «small») e poi resta in
cache, e da lì si lavora offline. Si sceglie «small» perché sull'italiano è il primo che non
storpia i nomi propri e la punteggiatura, restando sopportabile sulla CPU.

Un'ora di registrazione, decodificata tutta insieme, sono più di 200 MB di campioni, e la
trascrizione dura parecchi minuti senza dire a che punto è. Per questo l'audio si legge e si
trascrive **a pezzi** (``PEZZO_SECONDI``), leggendo con ``ffmpeg`` solo il pezzo che serve, e
dopo ogni pezzo si dice a che minuto si è arrivati. Il taglio fra un pezzo e l'altro non cade a un
secondo fisso, che spezzerebbe una parola a metà: cade nel punto più silenzioso degli ultimi
``MARGINE_SECONDI`` (``punto_di_taglio``). La meccanica — pezzi, tagli, montaggio delle frasi in
capoversi — è separata dal motore e si prova con un trascrittore finto, senza scaricare nulla.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from bookwriter.ingest.errors import LoaderError, dipendenza_mancante
from bookwriter.layout.model import PARAGRAPH, Segment

#: Estensioni dei file audio trascritti.
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

#: Modello di Whisper usato per la trascrizione (si scarica al primo uso, poi resta in cache).
MODELLO = "small"
#: Dimensione indicativa del download del modello, da mostrare all'autore prima di partire.
MODELLO_MB = 480
#: Dove gira la trascrizione se chi chiama non dice altro.
DISPOSITIVO = "cpu"

#: Frequenza di campionamento richiesta da Whisper.
CAMPIONAMENTO = 16000
#: Lunghezza di un pezzo di audio trascritto in una volta.
PEZZO_SECONDI = 600.0
#: Ampiezza della zona, attorno alla fine del pezzo, in cui si cerca il silenzio dove tagliare.
MARGINE_SECONDI = 30.0
#: Finestra su cui si misura il silenzio.
FINESTRA_SILENZIO = 0.4

#: Una pausa più lunga di così fra due frasi apre un capoverso nuovo.
PAUSA_CAPOVERSO = 2.0
#: Un capoverso dettato senza pause si chiude alla prima fine di frase dopo tante parole.
MAX_PAROLE_CAPOVERSO = 180

Progress = Callable[[int, int, str], None]
#: ``(campioni, lingua) -> ([(inizio, fine, testo), …], lingua_rilevata)``; tempi in secondi dal
#: principio dei campioni ricevuti.
Trascrittore = Callable[..., tuple]

_modelli: dict[tuple[str, str], object] = {}
_lock = threading.Lock()


def _senza_finestra() -> dict:
    """Su Windows ogni ``ffmpeg`` lanciato dalla GUI aprirebbe una console nera: la si evita."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _mmss(secondi: float) -> str:
    s = int(max(0, secondi))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# --------------------------------------------------------------------------- #
#  la sorgente: durata e lettura a pezzi
# --------------------------------------------------------------------------- #
class SorgenteAudio:
    """Il file audio, letto un pezzo per volta a 16 kHz mono.

    Con ``ffmpeg`` sul PATH si legge solo il pezzo che serve (memoria costante qualunque sia la
    durata). Senza, si decodifica l'intero file una volta con PyAV (che arriva con
    ``faster-whisper``) e si affetta: funziona, ma un file di ore occupa molta memoria.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._ffmpeg = shutil.which("ffmpeg")
        self._tutto = None
        self.durata = self._misura()

    def _misura(self) -> float:
        try:
            import av

            with av.open(str(self.path)) as contenitore:
                if contenitore.duration:
                    return float(contenitore.duration) / 1_000_000  # AV_TIME_BASE
        except ModuleNotFoundError:
            pass
        except Exception as exc:  # file rovinato: lo dice il messaggio
            raise LoaderError(f"File audio non leggibile: {self.path.name} ({exc})") from exc
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            esito = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(self.path)],
                capture_output=True, text=True, check=False, **_senza_finestra())
            try:
                return float(esito.stdout.strip())
            except ValueError:
                pass
        return 0.0  # durata ignota: si legge fino in fondo lo stesso

    def pezzo(self, inizio: float, durata: float):
        """I campioni (float32, 16 kHz, mono) da ``inizio`` per ``durata`` secondi."""
        import numpy as np

        if self._ffmpeg:
            esito = subprocess.run(
                [self._ffmpeg, "-nostdin", "-v", "error", "-ss", f"{inizio:.3f}",
                 "-t", f"{durata:.3f}", "-i", str(self.path), "-ac", "1",
                 "-ar", str(CAMPIONAMENTO), "-f", "s16le", "-"],
                capture_output=True, check=False, **_senza_finestra())
            if esito.returncode != 0:
                errore = esito.stderr.decode("utf-8", "replace").strip().splitlines()
                raise LoaderError(f"ffmpeg non riesce a leggere {self.path.name}: "
                                  f"{errore[-1] if errore else 'errore sconosciuto'}")
            return np.frombuffer(esito.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        if self._tutto is None:
            try:
                from faster_whisper.audio import decode_audio
            except ModuleNotFoundError as exc:
                raise dipendenza_mancante("file audio", "faster-whisper", "audio") from exc
            self._tutto = decode_audio(str(self.path), sampling_rate=CAMPIONAMENTO)
        a = int(inizio * CAMPIONAMENTO)
        return self._tutto[a:a + int(durata * CAMPIONAMENTO)]


# --------------------------------------------------------------------------- #
#  dove tagliare
# --------------------------------------------------------------------------- #
def punto_di_taglio(campioni, *, da: float, a: float, finestra: float = FINESTRA_SILENZIO,
                    campionamento: int = CAMPIONAMENTO) -> int:
    """L'indice del campione, fra ``da`` e ``a`` secondi, al centro della finestra più silenziosa.

    Il silenzio si misura come energia media (RMS) su finestre di ``finestra`` secondi: una
    pausa fra due frasi è lì, una parola non lo è mai. Se l'intervallo è vuoto si taglia in ``a``.
    """
    import numpy as np

    n = len(campioni)
    passo = max(1, int(finestra * campionamento))
    inizio = max(0, int(da * campionamento))
    fine = min(n, int(a * campionamento))
    if fine - inizio < passo:
        return min(n, max(1, fine))
    zona = np.asarray(campioni[inizio:fine], dtype=np.float32)
    k = len(zona) // passo
    energia = np.sqrt(np.mean(zona[: k * passo].reshape(k, passo) ** 2, axis=1))
    migliore = int(np.argmin(energia))
    return inizio + migliore * passo + passo // 2


# --------------------------------------------------------------------------- #
#  dalle frasi ai capoversi
# --------------------------------------------------------------------------- #
_FINE_FRASE = (".", "!", "?", "…")


def frasi_a_segmenti(frasi: list[tuple[float, float, str]]) -> list[Segment]:
    """Frasi con i loro tempi → capoversi.

    Un capoverso si apre dopo una pausa lunga (``PAUSA_CAPOVERSO``): chi detta si ferma quando
    cambia pensiero. Se non si ferma mai, il capoverso si chiude alla prima fine di frase dopo
    ``MAX_PAROLE_CAPOVERSO`` parole, perché un blocco di dieci minuti senza respiro non serve a
    nessuno. Ogni capoverso porta in ``ref`` il minuto in cui comincia («t=12:34»): è il modo di
    ritrovarlo nella registrazione. Le frasi ripetute identiche una dopo l'altra (un difetto
    noto di Whisper sui silenzi) si tengono una volta sola.
    """
    segmenti: list[Segment] = []
    corrente: list[str] = []
    inizio_capoverso = 0.0
    fine_prec: float | None = None
    parole = 0

    def chiudi() -> None:
        nonlocal parole
        if corrente:
            segmenti.append(Segment(kind=PARAGRAPH, text=" ".join(corrente),
                                    ref=f"t={_mmss(inizio_capoverso)}"))
        corrente.clear()
        parole = 0

    for t0, t1, testo in frasi:
        testo = " ".join(testo.split())
        if not testo or (corrente and testo == corrente[-1]):
            continue
        pausa = fine_prec is not None and t0 - fine_prec > PAUSA_CAPOVERSO
        troppo = parole >= MAX_PAROLE_CAPOVERSO and corrente and corrente[-1].endswith(_FINE_FRASE)
        if corrente and (pausa or troppo):
            chiudi()
        if not corrente:
            inizio_capoverso = t0
        corrente.append(testo)
        parole += len(testo.split())
        fine_prec = t1
    chiudi()
    return segmenti


# --------------------------------------------------------------------------- #
#  il motore
# --------------------------------------------------------------------------- #
def trascrittore_whisper(dispositivo: str | None = None, modello: str = MODELLO) -> Trascrittore:
    """Il trascrittore vero: faster-whisper, caricato una volta per modello e dispositivo."""
    try:
        from faster_whisper import WhisperModel
    except ModuleNotFoundError as exc:
        raise dipendenza_mancante("file audio", "faster-whisper", "audio") from exc
    dev = dispositivo or DISPOSITIVO
    chiave = (modello, dev)
    with _lock:
        if chiave not in _modelli:
            try:
                _modelli[chiave] = WhisperModel(
                    modello, device=dev, compute_type="int8" if dev == "cpu" else "default")
            except Exception as exc:  # rete assente, dispositivo non valido…
                raise LoaderError(
                    f"Non riesco a caricare il modello di trascrizione «{modello}» su {dev}: "
                    f"{exc}. Al primo uso il modello si scarica (circa {MODELLO_MB} MB): "
                    "serve la rete una volta sola.") from exc
        whisper = _modelli[chiave]

    def trascrivi(campioni, lingua):
        segmenti, info = whisper.transcribe(
            campioni, language=lingua, vad_filter=True, beam_size=5,
            # Senza il testo precedente come guida Whisper non si incastra a ripetere una frase.
            condition_on_previous_text=False)
        frasi = [(float(s.start), float(s.end), s.text.strip()) for s in segmenti]
        return frasi, lingua or getattr(info, "language", None)

    return trascrivi


def trascrivi(path: Path, *, trascrittore: Trascrittore | None = None,
              sorgente: SorgenteAudio | None = None, dispositivo: str | None = None,
              progress: Progress | None = None) -> list[Segment]:
    """Trascrive il file a pezzi e restituisce i capoversi.

    La lingua la riconosce Whisper sul primo pezzo e resta quella per i successivi: un pezzo
    che comincia con mezzo minuto di silenzio o di rumore non deve cambiare lingua al resto.
    ``progress(secondi_fatti, secondi_totali, nota)`` dice a che punto si è.
    """
    if trascrittore is None:
        if progress:
            progress(0, 1, f"carico il modello di trascrizione «{MODELLO}» (al primo uso si "
                           f"scarica, circa {MODELLO_MB} MB)")
        trascrittore = trascrittore_whisper(dispositivo)
    src = sorgente or SorgenteAudio(path)
    totale = float(src.durata or 0.0)
    lettura = PEZZO_SECONDI + MARGINE_SECONDI
    frasi: list[tuple[float, float, str]] = []
    lingua: str | None = None
    inizio = 0.0
    while True:
        if progress:
            fatto = min(inizio, totale) if totale else inizio
            progress(int(fatto), int(max(totale, fatto)),
                     f"trascrizione {_mmss(fatto)}" + (f" di {_mmss(totale)}" if totale else ""))
        campioni = src.pezzo(inizio, lettura)
        if len(campioni) == 0:
            break
        ultimo = len(campioni) < int(lettura * CAMPIONAMENTO) - CAMPIONAMENTO // 10
        taglio = len(campioni) if ultimo else punto_di_taglio(
            campioni, da=PEZZO_SECONDI - MARGINE_SECONDI, a=lettura)
        trovate, lingua = trascrittore(campioni[:taglio], lingua)
        frasi.extend((inizio + a, inizio + b, testo) for a, b, testo in trovate)
        inizio += taglio / CAMPIONAMENTO
        if ultimo:
            break
    if progress:
        fine = max(totale, inizio)
        progress(int(fine), int(fine), "trascrizione finita")
    return frasi_a_segmenti(frasi)
