"""Il libro come cartella su disco, e lo stato delle fasi.

Una cartella, non una tabella. Un libro dura più sessioni di lavoro e a volte più programmi: chi
lo riprende domani — o un altro assistente, o l'autore da solo — deve trovare la specifica, i
capitoli e le fonti in file che si aprono e si leggono, senza il database e senza di noi.

    <progetto>/book/
      book.yaml            misura, metadati, copertina, formati di uscita
      MASTER_SPEC.md       la specifica: pubblico, tesi, struttura, voce, prove
      chapters/            un file per capitolo, chiamato in modo che si ordini
      front/  back/        introduzione, avvertenza, glossario, biografia
      research/            le fonti, con il livello di prova di ogni affermazione
      output/              tutto quello che produce la composizione
      .forge/state.json    a che punto siamo, la conversazione e il quaderno del libro

``status()`` legge il disco. È l'unico resoconto di cui fidarsi: il ricordo di aver scritto un
capitolo non è la prova che il file esista.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).parent / "assets"
TEMPLATES = ASSETS / "templates"
REFERENCES = ASSETS / "references"

#: Le fasi, nell'ordine (la revisione è la ottava: dopo la ricerca, mai obbligatoria). L'ordine non è decorativo: la misura del taglio e il canale di
#: pubblicazione decidono la forma di ogni file, e indovinarli significa rifare il lavoro.
PHASES: list[tuple[str, str]] = [
    ("interview", "Intervista"),
    ("spec", "Specifica"),
    ("research", "Ricerca"),
    ("review", "Revisione"),
    ("writing", "Stesura"),
    ("production", "Produzione"),
    ("build", "Composizione"),
    ("handover", "Controlli e consegna"),
]
PHASE_IDS = [p for p, _ in PHASES]


#: I lavori che la stessa macchina a fasi sa fare, con la cartella in cui vive ciascuno dentro il
#: progetto: un libro da zero, il completamento di un manoscritto, il suo aggiornamento.
MODES: dict[str, str] = {"nuovo": "book", "completa": "completa", "aggiorna": "aggiorna"}

#: Come si chiamano le fasi quando il lavoro parte da un manoscritto.
_MODE_TITLES: dict[str, dict[str, str]] = {
    "completa": {"interview": "Manoscritto e intervista", "spec": "Piano del completamento",
                 "writing": "Stesura delle parti nuove"},
    "aggiorna": {"interview": "Manoscritto e intervista", "spec": "Piano delle modifiche",
                 "writing": "Applica le modifiche"},
}


def phase_title(phase: str, mode: str = "nuovo") -> str:
    return _MODE_TITLES.get(mode, {}).get(phase) or dict(PHASES).get(phase, phase)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
#  stato
# --------------------------------------------------------------------------- #
@dataclass
class ForgeState:
    """A che punto è il libro, e tutto quello che l'autore ha già deciso.

    Vive in ``.forge/state.json`` accanto al libro, non nel database del progetto, per la stessa
    ragione per cui ci vivono i capitoli: dev'essere leggibile senza aprire niente.
    """

    phase: str = "interview"
    #: che lavoro è: ``nuovo`` (scrivere da zero), ``completa`` (completare un manoscritto) o
    #: ``aggiorna`` (cambiare un manoscritto: luoghi, epoche, eventi…). Vedi :data:`MODES`.
    mode: str = "nuovo"
    #: il manoscritto di partenza, per «completa» e «aggiorna»: ``{"file", "chapters", "words",
    #: "imported"}``. I suoi capitoli originali restano intatti in ``originale/``.
    manuscript: dict[str, Any] = field(default_factory=dict)
    #: «Sorpresa»: l'autore ha dato solo una descrizione minima e ha lasciato decidere tutto il
    #: resto. Il testo della descrizione, e a che passo è arrivato il lavoro automatico.
    surprise: dict[str, Any] = field(default_factory=dict)
    #: la conversazione con l'autore: ``[{"role": "assistant"|"user", "text": ...}]``.
    transcript: list[dict[str, str]] = field(default_factory=list)
    #: il quaderno del libro: quello che è emerso dalla conversazione (vedi :mod:`notebook`).
    notebook: dict[str, Any] = field(default_factory=dict)
    #: fino a quale battuta della conversazione il quaderno è aggiornato.
    notebook_upto: int = 0
    #: le risposte della vecchia intervista a domande, nei libri cominciati con quella. Non si
    #: scrivono più; si leggono perché un libro a metà stesura non perda quel che sapeva.
    answers: dict[str, str] = field(default_factory=dict)
    #: lo stile scelto nella tendina (id del profilo; 0 = prosa chiara). Sta qui perché la
    #: conversazione e la stesura devono parlare dello stesso stile anche fra una sessione e l'altra.
    style_id: int = 0
    #: l'autore ha chiuso la conversazione e l'assistente ha deciso il resto.
    interview_closed: bool = False
    spec_approved: bool = False
    #: la struttura approvata: ``[{"number", "title", "does", "words", "part"}]``.
    outline: list[dict[str, Any]] = field(default_factory=list)
    research_done: bool = False
    research_declined: bool = False
    #: l'autore ha lasciato la fase «Revisione» (discussa o saltata): non è mai un requisito.
    review_done: bool = False
    #: le fasi che l'autore ha chiuso a mano senza che fossero finite («passa avanti lo
    #: stesso»): valgono come chiuse per far partire da sola la fase dopo.
    skipped: list[str] = field(default_factory=list)
    #: la verifica della qualità: il tipo di libro scelto dall'autore (vuoto = dal quaderno)
    quality: dict[str, Any] = field(default_factory=dict)
    #: le deroghe dell'autore ai blocker di un piano: [{"chapter", "reason", "blockers",
    #: "date", "trial"}] — registrate e recuperabili, mai implicite
    waivers: list[dict[str, Any]] = field(default_factory=list)
    production_ready: bool = False
    built: list[str] = field(default_factory=list)
    #: cosa resta aperto alla consegna (ISBN, licenze dei caratteri, costola, quarta).
    open_items: list[str] = field(default_factory=list)
    language: str = "it"  # lingua in cui l'autore sta parlando con l'assistente
    #: la conversazione sulle questioni aperte della ricerca (vedi :mod:`questions`).
    questions_transcript: list[dict[str, str]] = field(default_factory=list)
    #: fino a quale battuta di quella conversazione le decisioni sono registrate.
    questions_upto: int = 0
    updated_at: str = field(default_factory=_now)

    def say(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.transcript.append({"role": role, "text": text})

    def advance(self, phase: str) -> None:
        if phase in PHASE_IDS:
            self.phase = phase

    @property
    def phase_index(self) -> int:
        return PHASE_IDS.index(self.phase) if self.phase in PHASE_IDS else 0


# --------------------------------------------------------------------------- #
#  la cartella
# --------------------------------------------------------------------------- #
@dataclass
class BookProject:
    """La cartella del libro e le operazioni che la riguardano."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    # ---- percorsi ------------------------------------------------------
    @property
    def chapters_dir(self) -> Path:
        return self.root / "chapters"

    @property
    def front_dir(self) -> Path:
        return self.root / "front"

    @property
    def back_dir(self) -> Path:
        return self.root / "back"

    @property
    def research_dir(self) -> Path:
        return self.root / "research"

    @property
    def out_dir(self) -> Path:
        return self.root / "output"

    @property
    def original_dir(self) -> Path:
        """I capitoli del manoscritto di partenza, come sono arrivati: non si toccano mai."""
        return self.root / "originale"

    @property
    def story_dir(self) -> Path:
        """Il registro del racconto: continuità, cose già dette, motivi, piani delle scene."""
        return self.root / "story"

    @property
    def plans_dir(self) -> Path:
        return self.story_dir / "plans"

    @property
    def editorial_dir(self) -> Path:
        """I resoconti della revisione editoriale, uno per capitolo."""
        return self.out_dir / "editoriale"

    @property
    def trials_dir(self) -> Path:
        """I capitoli riscritti in prova: fuori da ``chapters/``, non li compone nessuno."""
        return self.out_dir / "prove"

    @property
    def spec_path(self) -> Path:
        return self.root / "MASTER_SPEC.md"

    @property
    def config_path(self) -> Path:
        return self.root / "book.yaml"

    @property
    def state_path(self) -> Path:
        return self.root / ".forge" / "state.json"

    @property
    def notes_path(self) -> Path:
        return self.out_dir / "PRODUCTION_NOTES.md"

    @property
    def exists(self) -> bool:
        return self.state_path.exists() or self.config_path.exists()

    # ---- impianto ------------------------------------------------------
    def scaffold(self) -> None:
        """Crea le cartelle. Non sovrascrive niente di già scritto.

        ``book.yaml`` non si copia dal modello: la sua presenza è il segno che la fase di
        produzione è stata fatta, e un file pieno di segnaposti direbbe il contrario. Il modello
        resta in ``assets/templates`` per chi vuole partire da lì a mano.
        """
        for d in (self.chapters_dir, self.front_dir, self.back_dir,
                  self.research_dir, self.out_dir, self.state_path.parent):
            d.mkdir(parents=True, exist_ok=True)

    # ---- stato ---------------------------------------------------------
    def load_state(self) -> ForgeState:
        if not self.state_path.exists():
            return ForgeState()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ForgeState()
        campi = {f for f in ForgeState.__dataclass_fields__}
        return ForgeState(**{k: v for k, v in data.items() if k in campi})

    def save_state(self, state: ForgeState) -> None:
        state.updated_at = _now()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- capitoli su disco ---------------------------------------------
    def chapter_files(self) -> list[Path]:
        if not self.chapters_dir.exists():
            return []
        return sorted(self.chapters_dir.glob("*.md"), key=_sort_key)

    def chapter_path(self, number: int, title: str) -> Path:
        return self.chapters_dir / f"{number:02d}_{slugify(title)}.md"

    def written_chapters(self) -> dict[int, Path]:
        """Numero → file, letto dal disco. Il numero viene dal nome, che per questo si ordina."""
        trovati: dict[int, Path] = {}
        for path in self.chapter_files():
            match = re.match(r"^(\d+)", path.stem)
            if match:
                trovati.setdefault(int(match.group(1)), path)
        return trovati


def slugify(text: str) -> str:
    """Pezzo di nome file: minuscolo, senza segni, con i trattini al posto degli spazi."""
    pulito = re.sub(r"[^\w\s-]", "", (text or "").strip().lower(), flags=re.UNICODE)
    return re.sub(r"[\s_]+", "-", pulito).strip("-") or "capitolo"


def _sort_key(path: Path):
    numeri = [int(n) for n in re.findall(r"\d+", path.stem)]
    return (numeri, path.stem.lower())


# --------------------------------------------------------------------------- #
#  stato reale del libro
# --------------------------------------------------------------------------- #
@dataclass
class Status:
    """Cosa c'è davvero su disco, contro quello che la specifica prevede."""

    root: Path
    title: str = ""
    planned: dict[int, str] = field(default_factory=dict)
    written: dict[int, tuple[Path, int, str]] = field(default_factory=dict)  # n → (file, parole, titolo)
    unreadable: list[tuple[Path, str]] = field(default_factory=list)
    front: list[str] = field(default_factory=list)
    back: list[str] = field(default_factory=list)
    research: list[str] = field(default_factory=list)
    cover: str = ""
    built: list[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(w for _, w, _ in self.written.values())

    @property
    def missing(self) -> list[int]:
        totale = max(len(self.planned), max(self.written, default=0))
        return [n for n in range(1, totale + 1) if n not in self.written]

    @property
    def thin(self) -> list[int]:
        """Capitoli sotto le 400 parole: quasi sempre un file aperto e mai finito."""
        return sorted(n for n, (_, w, _) in self.written.items() if w < 400)

    def render(self) -> str:
        """Il resoconto in chiaro. Si riporta com'è, buchi compresi."""
        righe = [
            f"cartella   {self.root}",
            f"titolo     {self.title or '(senza titolo)'}",
            f"su disco   {len(self.written)} capitoli"
            + (f" di {len(self.planned)} previsti" if self.planned else ""),
            f"parole     {self.words:,}".replace(",", "."),
        ]
        if self.written:
            righe.append(f"intervallo capitoli {min(self.written)}–{max(self.written)}")
        if self.thin:
            righe.append(f"scarni     {self.thin} — sotto le 400 parole, controlla che siano finiti")
        if self.missing:
            righe.append("")
            righe.append(f"MANCANTI   {len(self.missing)} capitoli non sono su disco: {self.missing}")
            for n in self.missing[:10]:
                righe.append(f"           {n}. {self.planned.get(n, '(non è nemmeno nella specifica)')}")
            if len(self.missing) > 10:
                righe.append(f"           … e altri {len(self.missing) - 10}")
            righe.append("")
            righe.append("Quello che credi di aver scritto e compare qui sopra non è stato salvato.")
            righe.append("Riscrivilo adesso, dalla bozza se ce l'hai ancora, altrimenti dalla specifica.")
        for path, errore in self.unreadable:
            righe.append(f"ILLEGGIBILE {path.name}: {errore}")
        righe.append(f"iniziali   {', '.join(self.front) or '(nessuna)'}")
        righe.append(f"finali     {', '.join(self.back) or '(nessuna)'}")
        righe.append(f"ricerca    {', '.join(self.research) or '(niente raccolto)'}")
        righe.append("copertina  " + (self.cover or "MANCA — chiedila all'autore"))
        righe.append(f"prodotti   {', '.join(self.built) or '(niente esportato)'}")
        return "\n".join(righe)


_SPEC_TABLE = re.compile(r"^\s*\|\s*(\d{1,3})\s*\|\s*([^|]+?)\s*\|")
_SPEC_INLINE = re.compile(r"(?:^|·\s*)(\d{1,3})\.\s+([^·\n]{3,70})")


def planned_chapters(spec_path: Path) -> dict[int, str]:
    """I capitoli previsti dalla specifica, dalla tabella o dal testo corrente.

    Si leggono entrambe le forme perché chi scrive la specifica usa quella che gli sta comoda, e
    perché la specifica la può correggere l'autore a mano, in un editor, senza passare di qui.
    """
    if not spec_path.exists():
        return {}
    righe = spec_path.read_text(encoding="utf-8").splitlines()
    # La tabella, quando c'è, comanda: un elenco numerato nella trama («1. Marco arriva in
    # città») non è un capitolo, e letto per primo ne prenderebbe il posto.
    previsti: dict[int, str] = {}
    for riga in righe:
        if match := _SPEC_TABLE.match(riga):
            previsti.setdefault(int(match.group(1)), match.group(2).strip())
    if previsti:
        return previsti
    for riga in righe:
        for match in _SPEC_INLINE.finditer(riga):
            previsti.setdefault(int(match.group(1)), match.group(2).strip(" *_"))
    return previsti


def status(project: BookProject) -> Status:
    """Cosa c'è su disco. Non chiede niente a nessuno: guarda."""
    st = Status(root=project.root)
    st.planned = planned_chapters(project.spec_path)

    for path in project.chapter_files():
        try:
            testo = path.read_text(encoding="utf-8")
        except OSError as exc:
            st.unreadable.append((path, str(exc)))
            continue
        numero = int(match.group(1)) if (match := re.match(r"^(\d+)", path.stem)) else 0
        titolo = ""
        for riga in testo.splitlines():
            if riga.strip().startswith("# "):
                titolo = riga.strip()[2:].strip()
                break
        st.written[numero] = (path, len(testo.split()), titolo)

    for etichetta, cartella in (("front", project.front_dir), ("back", project.back_dir)):
        nomi = sorted(p.name for p in cartella.glob("*.md")) if cartella.exists() else []
        setattr(st, etichetta, nomi)
    st.research = (sorted(p.name for p in project.research_dir.glob("*.md"))
                   if project.research_dir.exists() else [])

    if project.config_path.exists():
        st.title, copertina = _title_and_cover(project)
        st.cover = copertina
    if project.out_dir.exists():
        st.built = sorted(p.name for p in project.out_dir.glob("*")
                          if p.suffix.lower() in (".pdf", ".docx", ".epub"))
    return st


def _title_and_cover(project: BookProject) -> tuple[str, str]:
    """Titolo e copertina da ``book.yaml``, senza far fallire lo stato se il file è rotto."""
    try:
        import yaml

        data = yaml.safe_load(project.config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return "", ""
    libro = data.get("book", data) or {}
    titolo = str(libro.get("title") or "")
    immagine = str(((data.get("cover") or {}).get("image")) or "")
    if not immagine:
        return titolo, ""
    path = Path(immagine)
    path = path if path.is_absolute() else project.root / path
    return titolo, path.name if path.exists() else ""
