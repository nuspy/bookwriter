"""Salvataggi del libro in lavorazione: metterlo da parte, riprenderlo, ricominciare da capo.

Le sette fasi scrivono su disco a ogni passo, e questa è la ragione per cui il lavoro non si
perde mai da solo. Ma la stessa cosa rende impossibile abbandonarlo: riaprendo il programma il
libro è ancora lì, a metà, e l'unico modo per ricominciare sarebbe cancellare cartelle a mano.

Qui il libro si mette da parte prima di azzerarlo. Un salvataggio è **un file solo** — uno zip
della cartella ``book/`` — perché così si copia su una chiavetta o si allega a un messaggio
senza perdere pezzi per strada. Dentro, accanto allo stato, ci va un manifesto
(``.forge/save.json``) che dice di che libro si tratta e a che punto era: l'elenco dei
salvataggi si legge da lì, senza scompattare niente.

Il ripristino non è mai distruttivo: prima di sovrascrivere la cartella viva mette da parte
quello che c'è. Cancellare resta una cosa che si sceglie, mai un effetto collaterale.
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bookwriter.forge.project import BookProject, phase_title, slugify, status

#: Il manifesto vive dentro l'archivio, accanto allo stato: un salvataggio che si copia via
#: resta un oggetto solo, e non c'è un secondo file che si può perdere.
MANIFEST_NAME = ".forge/save.json"

#: Si cancella una cartella solo se è davvero un libro. Senza questa verifica, una radice di
#: progetto configurata male trasformerebbe «ricomincia da capo» nella cancellazione dei
#: documenti di chi la usa.
BOOK_MARKERS = (".forge", "chapters", "research", "front", "back", "output",
                "MASTER_SPEC.md", "book.yaml")


class SaveError(RuntimeError):
    """Il salvataggio o il ripristino non si può fare, e il perché si dice all'autore."""


@dataclass
class SaveInfo:
    """Un salvataggio sul disco, descritto quanto basta per riconoscerlo in un elenco."""

    path: Path
    name: str
    created_at: str
    phase: str = ""
    title: str = ""
    chapters: int = 0
    words: int = 0
    bytes: int = 0

    def label(self) -> str:
        """Una riga per l'elenco: nome, quando, a che punto era, quanto pesa."""
        pezzi = [self.name, _leggibile(self.created_at)]
        if self.phase:
            pezzi.append(phase_title(self.phase))
        if self.chapters:
            pezzi.append(f"{self.chapters} capitoli, {_migliaia(self.words)} parole")
        pezzi.append(_peso(self.bytes))
        return " · ".join(p for p in pezzi if p)


# --------------------------------------------------------------------------- #
#  mettere da parte
# --------------------------------------------------------------------------- #
def save_work(project: BookProject, saves_dir: Path, name: str = "") -> SaveInfo:
    """Mette da parte la cartella del libro così com'è, e restituisce il salvataggio scritto.

    Ci va dentro tutto, output compreso: un salvataggio serve a ritrovare il lavoro com'era, e
    decidere al posto dell'autore quali dei suoi file sono superflui non è un risparmio, è una
    perdita. Quanto pesa si vede nell'elenco, così la scelta resta sua.
    """
    if not has_work(project):
        raise SaveError("Non c'è ancora niente da salvare: il libro è vuoto.")
    st = status(project)
    nome = (name or st.title or "libro senza titolo").strip()
    quando = datetime.now(timezone.utc)
    info = SaveInfo(
        path=_free_path(saves_dir, nome, quando),
        name=nome,
        created_at=quando.isoformat(timespec="seconds"),
        phase=project.load_state().phase,
        title=st.title,
        chapters=len(st.written),
        words=st.words,
    )
    manifesto = json.dumps(
        {"name": info.name, "created_at": info.created_at, "phase": info.phase,
         "title": info.title, "chapters": info.chapters, "words": info.words},
        ensure_ascii=False, indent=2,
    )
    saves_dir.mkdir(parents=True, exist_ok=True)
    # Si scrive accanto e si rinomina alla fine: un archivio interrotto a metà non deve
    # presentarsi nell'elenco come se fosse un salvataggio buono.
    parziale = info.path.with_suffix(".zip.parziale")
    try:
        with zipfile.ZipFile(parziale, "w", zipfile.ZIP_DEFLATED) as archivio:
            for file in sorted(project.root.rglob("*")):
                dentro = file.relative_to(project.root).as_posix()
                # Un libro ripreso da un salvataggio si porta dietro il manifesto di allora.
                # Scriverlo insieme a quello nuovo darebbe un archivio con due voci uguali, e
                # l'elenco leggerebbe la prima: il salvataggio di oggi descritto da ieri.
                if file.is_file() and dentro != MANIFEST_NAME:
                    archivio.write(file, dentro)
            archivio.writestr(MANIFEST_NAME, manifesto)
        parziale.replace(info.path)
    except OSError as exc:
        parziale.unlink(missing_ok=True)
        raise SaveError(f"Non sono riuscito a scrivere il salvataggio: {exc}") from exc
    info.bytes = info.path.stat().st_size
    return info


def has_work(project: BookProject) -> bool:
    """Vero se nella cartella c'è qualcosa da perdere.

    Le cartelle vuote che ``scaffold()`` crea all'inizio non contano: aprire la scheda e non
    fare niente non è lavoro, e chiedere «vuoi salvare?» a chi non ha scritto una riga insegna
    solo a rispondere di no senza leggere.
    """
    if not project.root.exists():
        return False
    return any(f.is_file() for f in project.root.rglob("*"))


# --------------------------------------------------------------------------- #
#  rileggere
# --------------------------------------------------------------------------- #
def list_saves(saves_dir: Path) -> list[SaveInfo]:
    """I salvataggi presenti, dal più recente. Un archivio illeggibile si salta, non fa fallire."""
    if not Path(saves_dir).exists():
        return []
    trovati = []
    for path in sorted(Path(saves_dir).glob("*.zip")):
        info = read_save(path)
        if info is not None:
            trovati.append(info)
    # Il momento è registrato al secondo: due salvataggi fatti di seguito lo condividono, e
    # senza un secondo criterio l'elenco cadrebbe sull'ordine alfabetico dei nomi.
    return sorted(trovati, key=lambda s: (s.created_at, s.path.stat().st_mtime), reverse=True)


def read_save(path: Path) -> SaveInfo | None:
    """Cosa c'è dentro un archivio, senza scompattarlo. ``None`` se non è un salvataggio."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archivio:
            nomi = archivio.namelist()
            dati = {}
            if MANIFEST_NAME in nomi:
                dati = json.loads(archivio.read(MANIFEST_NAME).decode("utf-8"))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(dati, dict):
        dati = {}
    # Un archivio senza manifesto resta recuperabile: il nome del file e i capitoli che
    # contiene bastano a riconoscerlo, ed è meglio di una riga che non compare.
    capitoli = [n for n in nomi if n.startswith("chapters/") and n.endswith(".md")]
    return SaveInfo(
        path=path,
        name=str(dati.get("name") or path.stem),
        created_at=str(dati.get("created_at")
                       or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                       .isoformat(timespec="seconds")),
        phase=str(dati.get("phase") or ""),
        title=str(dati.get("title") or ""),
        chapters=int(dati.get("chapters") or len(capitoli)),
        words=int(dati.get("words") or 0),
        bytes=path.stat().st_size,
    )


# --------------------------------------------------------------------------- #
#  riprendere
# --------------------------------------------------------------------------- #
def restore_save(path: Path, project: BookProject, *, backup_dir: Path | None = None) -> SaveInfo:
    """Rimette in lavorazione un salvataggio, al posto di quello che c'è ora.

    Quello che c'è ora viene messo da parte per primo, sempre: riprendere un salvataggio è una
    cosa che si fa per rimediare a un errore, e non deve poterne causare un altro.
    """
    info = read_save(path)
    if info is None:
        raise SaveError(f"Non è un salvataggio leggibile: {Path(path).name}")
    with zipfile.ZipFile(path) as archivio:
        nomi = archivio.namelist()
        for nome in nomi:
            _check_member(nome)
        if backup_dir is not None and has_work(project):
            save_work(project, Path(backup_dir), f"prima di riprendere «{info.name}»")
        clear_work(project)
        project.root.mkdir(parents=True, exist_ok=True)
        archivio.extractall(project.root)
    return info


def _check_member(nome: str) -> None:
    """Un nome dentro l'archivio non deve poter scrivere fuori dalla cartella del libro.

    Uno zip può contenere ``../../.bashrc`` o un percorso assoluto: sono file che arrivano da
    fuori, e ``extractall`` da solo non basta a fidarsi.
    """
    pulito = nome.replace("\\", "/")
    if pulito.startswith("/") or re.match(r"^[A-Za-z]:", pulito):
        raise SaveError(f"L'archivio contiene un percorso assoluto: {nome}")
    if any(pezzo == ".." for pezzo in pulito.split("/")):
        raise SaveError(f"L'archivio contiene un percorso che esce dalla cartella: {nome}")


# --------------------------------------------------------------------------- #
#  ricominciare
# --------------------------------------------------------------------------- #
def clear_work(project: BookProject) -> None:
    """Cancella la cartella del libro. Il resto del progetto — documenti, database — non si tocca."""
    root = project.root
    if not root.exists():
        return
    if not root.is_dir():
        raise SaveError(f"Non è una cartella: {root}")
    contenuto = list(root.iterdir())
    if contenuto and not any(f.name in BOOK_MARKERS for f in contenuto):
        raise SaveError(
            f"In {root} non c'è un libro: non la cancello. Controlla la cartella del progetto."
        )
    shutil.rmtree(root)


# --------------------------------------------------------------------------- #
#  dettagli
# --------------------------------------------------------------------------- #
def _free_path(saves_dir: Path, nome: str, quando: datetime) -> Path:
    """Un percorso libero. La data nel nome serve a ritrovarli in ordine anche fuori dal programma."""
    base = f"{quando.strftime('%Y%m%d-%H%M%S')}-{slugify(nome)[:60]}"
    path = Path(saves_dir) / f"{base}.zip"
    contatore = 2
    while path.exists():
        path = Path(saves_dir) / f"{base}-{contatore}.zip"
        contatore += 1
    return path


def _migliaia(numero: int) -> str:
    """Il punto separa le migliaia. Va applicato al solo numero: una sostituzione sull'intera
    frase si mangia anche le virgole che separano le parole («1 capitoli. 603 parole»)."""
    return f"{numero:,}".replace(",", ".")


def _leggibile(iso: str) -> str:
    """La data come la scriverebbe una persona, ora locale: un ISO in UTC non si legge a colpo d'occhio."""
    try:
        quando = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if quando.tzinfo is not None:
        quando = quando.astimezone()
    return quando.strftime("%d/%m/%Y %H:%M")


def _peso(bytes_: int) -> str:
    if bytes_ <= 0:
        return ""
    if bytes_ < 1024 * 1024:
        return f"{max(1, round(bytes_ / 1024))} kB"
    return f"{bytes_ / (1024 * 1024):.1f} MB".replace(".", ",")
