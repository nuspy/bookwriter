"""Fase 5 — la produzione: la copertina, ``book.yaml``, la misura, i caratteri.

Questa fase ha una dipendenza dura, e va detta prima di arrivarci: **la copertina la fornisce
l'autore**. Non la si inventa, non la si genera senza che sia stato chiesto, e non la si lascia
per ultima — se va commissionata, il tempo serve prima.

Tre casi, e bisogna sapere quale prima di comporre qualsiasi cosa:

1. **l'immagine esiste** — si prende il file e se ne controlla subito la risoluzione, non alla fine;
2. **va fatta** — è una commissione, che vada a un illustratore o a un modello: la si concorda
   esplicitamente, e quello che esce da un modello è una proposta, non una consegna;
3. **copertina tipografica** — solo lettere su un fondo pieno. Spesso è la scelta migliore per un
   saggio, ed è l'unica del tutto sotto controllo.

Quando l'immagine è più piccola del necessario si può ingrandire — il compositore lo fa e dichiara
i punti per pollice che ne restano — ma ingrandire non crea dettaglio. Il numero si dice
all'autore, e decide lui.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from bookwriter.config import ProductionSettings
from bookwriter.forge import notebook as nb
from bookwriter.forge.bookgen.config import PAGE_PRESETS
from bookwriter.forge.project import BookProject, ForgeState

#: Pixel per millimetro a 300 punti per pollice — la risoluzione che vuole la stampa.
PX_PER_MM = 11.811

#: Spessore per foglio (due pagine), in millimetri. Lo conferma comunque lo stampatore.
PAPER_CALIPER = {
    "cream-80": 0.1250,
    "white-80": 0.1120,
    "white-90": 0.1300,
    "kdp-cream": 0.1275,
    "kdp-white": 0.1120,
}


# --------------------------------------------------------------------------- #
#  la copertina
# --------------------------------------------------------------------------- #
@dataclass
class CoverCheck:
    """Se l'immagine di copertina regge la stampa, e con che numeri."""

    required: tuple[int, int]
    actual: tuple[int, int] | None = None
    path: Path | None = None
    dpi: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.actual) and not self.warnings

    def message(self) -> str:
        largo, alto = self.required
        if self.actual is None:
            return (f"Nessuna copertina. Per la stampa serve un'immagine di almeno "
                    f"{largo}×{alto} pixel (300 punti per pollice, abbondanza compresa).")
        testo = (f"{self.path.name if self.path else ''}: {self.actual[0]}×{self.actual[1]} pixel, "
                 f"{self.dpi:.0f} punti per pollice alla misura scelta.")
        return testo + ("\n" + "\n".join(f"⚠ {w}" for w in self.warnings) if self.warnings else
                        " Va bene per la stampa.")


def cover_requirements(trim: str, *, bleed_mm: float = 3.0) -> tuple[int, int]:
    """I pixel minimi per la copertina stampata, abbondanza compresa."""
    misura = PAGE_PRESETS.get(trim, PAGE_PRESETS["a5"])
    return (
        round((misura["width"] + 2 * bleed_mm) * PX_PER_MM),
        round((misura["height"] + 2 * bleed_mm) * PX_PER_MM),
    )


def check_cover(path: Path | None, trim: str, *, bleed_mm: float = 3.0) -> CoverCheck:
    """Controlla l'immagine contro la misura scelta. Il numero si dice, non si aggira in silenzio."""
    richiesti = cover_requirements(trim, bleed_mm=bleed_mm)
    controllo = CoverCheck(required=richiesti, path=Path(path) if path else None)
    if not path or not Path(path).exists():
        return controllo
    try:
        from PIL import Image

        with Image.open(path) as immagine:
            controllo.actual = (immagine.width, immagine.height)
    except Exception as exc:
        controllo.warnings.append(f"Non si riesce a leggere l'immagine: {exc}")
        return controllo

    largo, alto = controllo.actual
    misura = PAGE_PRESETS.get(trim, PAGE_PRESETS["a5"])
    controllo.dpi = min(largo / ((misura["width"] + 2 * bleed_mm) / 25.4),
                        alto / ((misura["height"] + 2 * bleed_mm) / 25.4))
    if largo < richiesti[0] or alto < richiesti[1]:
        controllo.warnings.append(
            f"È più piccola del necessario: servono {richiesti[0]}×{richiesti[1]} pixel. "
            f"Si può ingrandire, e resterà a {controllo.dpi:.0f} punti per pollice, ma "
            "ingrandire non crea dettaglio."
        )
    proporzione = (alto / largo) if largo else 0
    attesa = misura["height"] / misura["width"]
    if proporzione and abs(proporzione - attesa) > 0.15:
        controllo.warnings.append(
            "Le proporzioni non sono quelle della pagina: il compositore la porta a tutta "
            "larghezza e mette una fascia sotto per le scritte, invece di tagliarne i lati."
        )
    return controllo


def spine_width_mm(pages: int, paper: str = "cream-80") -> float:
    """Lo spessore della costola, in millimetri. Da confermare con lo stampatore, sempre."""
    return round(pages / 2 * PAPER_CALIPER.get(paper, 0.125), 2)


# --------------------------------------------------------------------------- #
#  book.yaml
# --------------------------------------------------------------------------- #
def _yaml_str(value: str) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def trim_of(project: BookProject, *, fallback: str = "a5") -> str:
    """La misura già scritta in ``book.yaml``; ``fallback`` se il file non c'è ancora."""
    try:
        import yaml

        data = yaml.safe_load(project.config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return fallback
    preset = str(((data.get("format") or {}).get("preset")) or "")
    return preset if preset in PAGE_PRESETS else fallback


def render_book_yaml(state: ForgeState, production: ProductionSettings | None = None,
                     *, cover: str = "", formats: list[str] | None = None) -> str:
    """``book.yaml``, commentato perché si possa correggere a mano.

    Le decisioni sull'oggetto — misura, marchio, autore stampato, diritti — vengono dalle
    impostazioni di produzione e dalla tendina della misura, non dalla conversazione: di stampa
    con l'assistente non si parla. Dal quaderno arrivano solo il titolo, la lingua e la
    descrizione.
    """
    production = production or ProductionSettings()
    taglio = production.trim if production.trim in PAGE_PRESETS else "a5"
    # Un titolo ci vuole comunque: senza, la copertina e i titoli correnti stampano «Untitled»,
    # che è peggio di un titolo provvisorio perché sembra una scelta.
    premessa = nb.get(state, "premise")
    titolo = nb.get(state, "title") or " ".join(premessa.split()[:8]) or "Senza titolo"
    lingua = nb.language_code(state)
    # Nel dubbio escono tutti e tre: un file in più non fa danno, un ebook che manca
    # all'ultimo momento sì. I formati si scelgono nella fase di composizione.
    formats = formats or ["pdf", "docx", "epub", "md"]

    return f"""# Configurazione del libro.
# Tutto quello che il compositore deve sapere di questo titolo e di nessun altro.
# Scritto dal quaderno del libro e dalle impostazioni di produzione: correggilo a mano quando
# serve, vince questo file.

book:
  title: {_yaml_str(titolo)}
  subtitle: ""
  author: {_yaml_str(production.author)}
  language: {_yaml_str(lingua)}          # decide sillabazione, virgolette e nomi delle sezioni
  publisher: {_yaml_str(production.publisher)}
  year: {_yaml_str(date.today().year)}
  edition: "Prima edizione"
  isbn: ""
  rights: {_yaml_str(production.rights)}
  description: {_yaml_str(premessa)}
  dedication: ""
  epigraph: ""

# --------------------------------------------------------------- l'oggetto --
format:
  preset: {taglio}
  bleed_mm: 3               # abbondanza della copertina; 3 mm è lo standard, chiedi allo stampatore
  chapters_open_recto: true # ogni capitolo comincia su una pagina di destra
  pad_to_even: true         # la brossura non sa fare un numero dispari di facciate

typography:
  serif: {_yaml_str(production.serif)}   # vuoto = la prima famiglia installata fra le preferite
  sans: {_yaml_str(production.sans)}
  first_line_indent_mm: 5
  hyphenation: {production.hyphenation or 'auto'}
  justify: {str(production.justify).lower()}

# ---------------------------------------------------------------- sorgenti --
source:
  root: "."
  pattern: "chapters/*.md"
  exclude: ["output/**", "front/**", "back/**", "research/**", "MASTER_SPEC.md"]
  chapter_regex: '^#\\s+(?:Capitolo|Chapter|Cap\\.?)\\s*(\\d+)\\s*[:\\-—–]\\s*(.*)$'
  part_regex: '^#\\s+(?:PARTE|PART)\\s+([IVXLC]+)\\s*[—\\-:–]?\\s*(.*)$'
  title_split: "—"
  strip_production_noise: true
  extract_chapter_references: true

parts: []
front_matter: []            # vuoto = si scoprono da front/*.md, in ordine di lettura
back_matter: []             # vuoto = si scoprono da back/*.md; la bibliografia si genera

# -------------------------------------------------------------- copertina --
cover:
  image: {_yaml_str(cover)}   # la fornisce l'autore: non si inventa
  compose_title: true
  include_in_pdf: true

# ----------------------------------------------------------------- uscita --
output:
  directory: "output"
  basename: ""
  formats: [{', '.join(formats)}]
  pdf_with_cover: true      # copertina come pagina uno, più il suo verso bianco
  pdf_interior_copy: true   # e il solo interno a parte, che è quello che vuole lo stampatore
"""


def write_book_yaml(project: BookProject, state: ForgeState,
                    production: ProductionSettings | None = None, *, cover: str = "",
                    formats: list[str] | None = None) -> Path:
    """Scrive ``book.yaml``. Sovrascrive: è generato dalle risposte, che sono la fonte."""
    project.scaffold()
    project.config_path.write_text(
        render_book_yaml(state, production, cover=cover, formats=formats), encoding="utf-8"
    )
    state.production_ready = True
    return project.config_path


def set_cover(project: BookProject, image: Path) -> Path:
    """Registra l'immagine di copertina in ``book.yaml``, copiandola accanto al libro."""
    image = Path(image)
    if not image.exists():
        raise FileNotFoundError(f"L'immagine di copertina non esiste: {image}")
    project.scaffold()
    destinazione = project.root / f"cover{image.suffix.lower()}"
    if image.resolve() != destinazione.resolve():
        destinazione.write_bytes(image.read_bytes())
    testo = project.config_path.read_text(encoding="utf-8") if project.config_path.exists() else ""
    if "image:" in testo:
        testo = re.sub(r"(?m)^(\s*image:).*$", rf"\1 {_yaml_str(destinazione.name)}", testo, count=1)
        project.config_path.write_text(testo, encoding="utf-8")
    return destinazione
