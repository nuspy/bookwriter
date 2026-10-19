"""Fasi 6 e 7 — la composizione, i controlli, la consegna.

La composizione produce, nella cartella ``output/`` del libro:

- un PDF alla misura esatta, con la copertina come pagina uno e il suo verso bianco;
- il **solo interno**, che è quello che vogliono davvero lo stampatore e KDP;
- i file di copertina: con abbondanza per la stampa, rifilato per la pagina, e nella misura che
  chiedono i negozi;
- un DOCX con stili veri, un indice che si aggiorna da solo e i titoli correnti agganciati agli
  stili;
- un EPUB 3 che passa i requisiti di KDP.

Alcuni comportamenti sembrano arbitrari e non lo sono: i capitoli aprono su una pagina di destra,
il numero di facciate viene portato a pari (la brossura non sa farne un numero dispari), e la
copertina rilegata dentro il PDF è seguita da una pagina bianca — con una sola pagina in più ogni
destra diventerebbe una sinistra e i margini specchiati cadrebbero dalla parte sbagliata della
piega.

I controlli girano da soli dopo ogni composizione. Non sostituiscono Kindle Previewer per
l'ebook né una prova stampata per la carta, e questo va detto ogni volta. **Un resoconto pulito
su un file che non è stato controllato è peggio di nessun resoconto.**
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import re
from datetime import date
from pathlib import Path

from bookwriter.forge.project import BookProject, ForgeState, status

ALL_FORMATS = ("pdf", "docx", "epub", "md")


class BuildError(RuntimeError):
    """La composizione non si può fare, e il motivo va detto."""


class CoverMissing(BuildError):
    """Manca l'immagine di copertina.

    La composizione non la solleva più: senza copertina si esporta lo stesso e lo si dice nelle
    avvertenze. Resta per chi la importava; la regola «la copertina non si inventa» non cambia.
    """


@dataclass
class BuildReport:
    """Cosa è uscito dalla composizione, e cosa hanno detto i controlli."""

    produced: dict[str, Path] = field(default_factory=dict)
    pages: int = 0
    words: int = 0
    chapters: int = 0
    fonts: list[str] = field(default_factory=list)
    cover_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: cose da sapere che non sono problemi (per esempio: «il DOCX non porta mai copertina»).
    notes: list[str] = field(default_factory=list)
    #: la copertina è mancata e i file sono usciti senza.
    no_cover: bool = False
    check: str = ""
    check_ok: bool = True

    def summary(self) -> str:
        righe = [f"{self.chapters} capitoli, {self.words} parole"
                 + (f", {self.pages} pagine" if self.pages else "")]
        for tipo, path in self.produced.items():
            peso = path.stat().st_size / 1024 if path.exists() else 0
            righe.append(f"  {tipo.upper():5} {path.name}  ({peso:.0f} KB)")
        for path in self.cover_files:
            righe.append(f"  COVER {path.name}")
        for carattere in self.fonts:
            righe.append(f"  carattere {carattere}")
        for avviso in self.warnings:
            righe.append(f"  ⚠ {avviso}")
        for nota in self.notes:
            righe.append(f"  ℹ {nota}")
        if self.check:
            righe += ["", "Controlli di stampa:", self.check]
        return "\n".join(righe)


def build(
    project: BookProject,
    *,
    formats: list[str] | None = None,
    allow_missing_cover: bool = False,
    copy_without_cover: bool = False,
    run_check: bool = True,
    note: Callable[[object], None] | None = None,
) -> BuildReport:
    """Compone ed esporta i formati voluti.

    Senza immagine di copertina esporta lo stesso, senza copertina, e lo dice nelle avvertenze:
    la copertina la fornisce l'autore e non si inventa. ``allow_missing_cover`` non ha più
    effetto (resta per compatibilità).

    ``copy_without_cover``: oltre alla versione con copertina, una senza, per ogni formato che
    una copertina la porta. Il PDF ha il suo interno senza copertina (``_interior.pdf``) e
    l'EPUB una copia ``_nocover``; il DOCX e il Markdown non portano mai copertina, quindi la
    loro versione normale è già quella senza e non si duplica niente.
    """
    from bookwriter.forge.bookgen import build_book, load_config, stats

    if not project.config_path.exists():
        raise BuildError(
            "Manca book.yaml: la fase di produzione non è stata fatta. Serve almeno la misura "
            "del taglio e la lingua del libro."
        )
    if not project.chapter_files():
        raise BuildError("Non c'è nessun capitolo su disco in chapters/: non c'è niente da comporre.")

    cfg = load_config(project.root)
    libro = build_book(cfg)
    numeri = stats(libro)
    report = BuildReport(words=int(numeri["words"]), chapters=int(numeri["chapters"]))
    voluti = [f.lower() for f in (formats or cfg.output.formats)]
    base = cfg.output.basename
    out = cfg.out_dir
    pagina_copertina = None

    # --- copertina ---------------------------------------------------------
    from bookwriter.forge.bookgen.cover import CoverMissing as _CoverMissing
    from bookwriter.forge.bookgen.cover import build_covers

    try:
        if note:
            note("copertina…")
        rapporto = build_covers(cfg)
        report.cover_files = list(rapporto.files)
        report.warnings.extend(rapporto.warnings)
        pagina_copertina = next((p for p in rapporto.files
                                 if p.name.endswith("_cover_page.jpg")), None)
    except _CoverMissing:
        report.no_cover = True
        report.warnings.append(
            "Composto senza copertina: non ne ho inventata una. Scegli l'immagine di copertina "
            "nella fase di produzione e ricomponi per avere anche la versione con copertina."
        )

    # --- PDF ---------------------------------------------------------------
    if "pdf" in voluti:
        from bookwriter.forge.bookgen.pdf import build_pdf

        if note:
            note("PDF…")
        path = out / f"{base}_{int(cfg.page.width_mm)}x{int(cfg.page.height_mm)}.pdf"
        con_copertina = bool(pagina_copertina and cfg.output.pdf_with_cover)
        interno = (out / f"{base}_interior.pdf"
                   if con_copertina and (cfg.output.pdf_interior_copy or copy_without_cover)
                   else None)
        info = build_pdf(libro, path, cfg,
                         cover=pagina_copertina if cfg.output.pdf_with_cover else None,
                         interior_copy=interno)
        report.produced["pdf"] = path
        report.pages = int(info.get("pages") or 0)
        report.fonts = list(info.get("fonts") or [])
        if interno:
            report.produced["pdf_interior"] = interno
        elif copy_without_cover and not con_copertina:
            report.notes.append("PDF: è già senza copertina, non ne produco una seconda copia.")

    # --- DOCX --------------------------------------------------------------
    if "docx" in voluti:
        from bookwriter.forge.bookgen.docx_out import build_docx
        from bookwriter.forge.bookgen.typography import register_fonts, theme_from_config

        if note:
            note("DOCX…")
        tema = register_fonts(theme_from_config(cfg), cfg)
        path = out / f"{base}.docx"
        build_docx(libro, path, cfg, serif=tema.serif_name, sans=tema.sans_name)
        report.produced["docx"] = path
        report.fonts = report.fonts or list(tema.font_report)
        if copy_without_cover:
            report.notes.append("DOCX: non porta mai la copertina, quindi la versione normale è "
                                "già senza.")

    # --- EPUB --------------------------------------------------------------
    if "epub" in voluti:
        from bookwriter.forge.bookgen.epub import build_epub

        if note:
            note("EPUB…")
        path = out / f"{base}_ebook.epub"
        copertina_ebook = out / f"{base}_cover_ebook.jpg"
        build_epub(libro, path, cfg,
                   cover=copertina_ebook if copertina_ebook.exists() else None)
        report.produced["epub"] = path
        if copy_without_cover:
            if copertina_ebook.exists():
                senza = out / f"{base}_ebook_nocover.epub"
                build_epub(libro, senza, cfg, cover=None)
                report.produced["epub_nocover"] = senza
            else:
                report.notes.append("EPUB: è già senza copertina, non ne produco una seconda "
                                    "copia.")

    # --- Markdown ----------------------------------------------------------
    if "md" in voluti:
        if note:
            note("Markdown…")
        path = out / f"{base}.md"
        out.mkdir(parents=True, exist_ok=True)
        path.write_text(book_markdown(project, cfg), encoding="utf-8")
        report.produced["md"] = path
        if copy_without_cover:
            report.notes.append("Markdown: non porta mai la copertina, quindi la versione "
                                "normale è già senza.")

    if run_check and report.produced:
        if note:
            note("controlli…")
        report.check, report.check_ok = _run_check(cfg, report.produced)
    return report


def book_markdown(project: BookProject, cfg) -> str:
    """Il libro intero in un solo Markdown: pagine iniziali, capitoli in ordine, pagine finali.

    Rilegge i file del libro, che sono già Markdown: non c'è niente da tradurre e niente da
    duplicare. Le pagine iniziali e finali seguono lo stesso ordine che usa la composizione.
    """
    from bookwriter.forge.bookgen.assemble import BACK_ORDER, FRONT_ORDER, _ident_from_name

    def ordinati(cartella: Path, ordine: list[str]) -> list[Path]:
        if not cartella.exists():
            return []
        file = sorted(cartella.glob("*.md"))
        if all(re.match(r"^\d+", p.stem) for p in file):
            return file  # i prefissi numerici vincono
        def posto(p: Path) -> int:
            ident = _ident_from_name(p.stem) or p.stem
            return ordine.index(ident) if ident in ordine else len(ordine)
        return sorted(file, key=posto)

    testata = [f"# {cfg.title}"]
    if cfg.subtitle:
        testata.append(f"*{cfg.subtitle}*")
    if cfg.author:
        testata.append(f"*{cfg.author}*")
    pezzi = ["\n\n".join(testata)]
    for path in (ordinati(project.front_dir, FRONT_ORDER) + project.chapter_files()
                 + ordinati(project.back_dir, BACK_ORDER)):
        testo = path.read_text(encoding="utf-8").strip()
        if testo:
            pezzi.append(testo)
    return "\n\n".join(pezzi) + "\n"


def check(project: BookProject) -> tuple[str, bool]:
    """Rifà i controlli sui file già prodotti. Si riporta tutto: errori e avvertenze."""
    from bookwriter.forge.bookgen import load_config

    cfg = load_config(project.root)
    bersagli = {
        p.stem: p for p in sorted(cfg.out_dir.glob("*"))
        if p.suffix.lower() in (".pdf", ".epub", ".docx") and "cover" not in p.stem
    }
    if not bersagli:
        return f"Non c'è niente da controllare in {cfg.out_dir}.", False
    return _run_check(cfg, bersagli)


def _run_check(cfg, bersagli: dict[str, Path]) -> tuple[str, bool]:
    from bookwriter.forge.bookgen.check import check_all

    rapporto = check_all(cfg, dict(bersagli))
    return rapporto.render(), rapporto.ok()


# --------------------------------------------------------------------------- #
#  la consegna
# --------------------------------------------------------------------------- #
#: Le quattro cose che restano quasi sempre aperte. Si elencano, non si nascondono.
STANDING_ITEMS = [
    ("ISBN", ("Segnaposto finché non ne registri uno tuo, oppure lascialo vuoto e usa quello "
             "gratuito del negozio. Va sulla pagina dei diritti e nei metadati dell'ebook.")),
    ("Licenze dei caratteri", ("I caratteri di sistema vanno benissimo per comporre e non sempre "
                              "per vendere. Per un libro in commercio metti caratteri liberi — "
                              "EB Garamond o Libre Baskerville per il testo, Lato o Source Sans "
                              "per i titoli — in `typography` dentro book.yaml.")),
    ("Costola e quarta di copertina", ("Lo spessore della costola dipende dalla carta dello "
                                      "stampatore e va confermato con lui sul numero di pagine "
                                      "finale. Il testo della quarta lo scrive l'autore.")),
    ("Prove vere", ("Questi controlli non sostituiscono Kindle Previewer 3 per l'ebook né una "
                   "copia stampata per la carta. Falle entrambe prima di pubblicare.")),
]


def write_production_notes(project: BookProject, state: ForgeState,
                           report: BuildReport | None = None) -> Path:
    """``output/PRODUCTION_NOTES.md``: cosa è stato fatto e cosa resta aperto, detto chiaro."""
    project.out_dir.mkdir(parents=True, exist_ok=True)
    st = status(project)
    righe = [
        f"# Note di produzione — {st.title or 'libro senza titolo'}",
        "",
        f"*{date.today().isoformat()}*",
        "",
        "## Cosa c'è",
        "",
        "```",
        st.render(),
        "```",
        "",
    ]
    if report:
        righe += ["## Cosa è stato prodotto", "", "```", report.summary(), "```", ""]
        if not report.check_ok:
            righe += [("> I controlli hanno segnalato degli errori. Sono qui sopra per intero: "
                      "vanno risolti prima di mandare i file da qualche parte."), ""]

    righe += ["## Cosa resta aperto", ""]
    for titolo, testo in STANDING_ITEMS:
        righe += [f"### {titolo}", "", testo, ""]

    from bookwriter.forge.production import check_cover, trim_of

    taglio = trim_of(project)
    copertina = check_cover(_cover_path(project), taglio)
    righe += ["### Copertina", "", copertina.message(), ""]

    if state.open_items:
        righe += ["### Deciso dall'autore, ancora in sospeso", ""]
        righe += [f"- {voce}" for voce in state.open_items]
        righe.append("")
    project.notes_path.write_text("\n".join(righe), encoding="utf-8")
    return project.notes_path


def _cover_path(project: BookProject) -> Path | None:
    try:
        import yaml

        data = yaml.safe_load(project.config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    immagine = str(((data.get("cover") or {}).get("image")) or "")
    if not immagine:
        return None
    path = Path(immagine)
    return path if path.is_absolute() else project.root / path
