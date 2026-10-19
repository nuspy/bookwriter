"""Dal Markdown al volume impaginato: il ponte verso il compositore.

Il compositore (``bookwriter.forge.bookgen``) non lavora su testo ma su un modello di libro —
parti, capitoli, blocchi tipografici — e da quello sa scrivere un PDF alla misura di stampa, un
DOCX con stili e indice vivo e un EPUB 3. Qui c'è il passaggio che gli porta il Markdown prodotto
dal resto del programma: la stesura da un indice, una riscrittura, una traduzione, un capitolo
scritto dall'assistente.

Il senso è averne **una sola** di catena. Prima le schermate che producevano un libro nuovo
passavano da un impaginatore rudimentale (A4, tre livelli di titolo, nessuna sillabazione);
adesso passano tutte di qui, e ottengono la stessa cura: misura reale, margini asimmetrici,
capitoli in recto, titoli correnti, indice, bibliografia unita in fondo.

Resta fuori una cosa sola, e per un motivo: ``bookwriter.layout`` riproduce l'impaginazione di un
libro **già esistente** — sostituisce il testo dentro il DOCX di partenza lasciando intatto tutto
il resto. Comporre un libro nuovo e conservare l'impaginazione di uno vecchio sono due mestieri
diversi, e il secondo non si fa con un compositore.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bookwriter.forge.bookgen import config as bookgen_config
from bookwriter.forge.bookgen.assemble import build_bibliography
from bookwriter.forge.bookgen.model import Book, Chapter, Part, Section
from bookwriter.forge.bookgen.parse import (
    REFERENCE_HEADING,
    clean_inline,
    dedupe_references,
    parse_blocks,
    split_references,
)

#: Formati che il compositore sa scrivere. ``md`` esce così com'è: è la sorgente, non una resa.
SUPPORTED_FORMATS = ("docx", "pdf", "epub", "md")

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# «Capitolo 3: Titolo», «Chapter 3 — Titolo»: il numero scritto nel titolo vince su quello
# dedotto dalla posizione, perché è quello che l'autore ha in mente.
_NUMBERED = re.compile(
    r"^(?:Capitolo|Capítulo|Chapter|Chapitre|Kapitel|Cap\.?|Chap\.?)\s*(\d+)\s*[:\-—–]?\s*(.*)$",
    re.IGNORECASE,
)


@dataclass
class Composed:
    """Cosa è uscito dalla composizione, per poterlo raccontare a chi ha premuto il pulsante."""

    path: Path
    fmt: str
    pages: int | None = None
    chapters: int = 0
    words: int = 0
    fonts: list[str] | None = None

    def summary(self) -> str:
        parti = []
        if self.chapters:
            parti.append(f"{self.chapters} capitoli")
        if self.words:
            parti.append(f"{self.words} parole")
        if self.pages:
            parti.append(f"{self.pages} pagine")
        return f"{self.path.name}: " + ", ".join(parti) if parti else self.path.name


# --------------------------------------------------------------------------- #
#  configurazione del volume
# --------------------------------------------------------------------------- #
def book_config(
    root: Path,
    *,
    title: str = "",
    subtitle: str = "",
    author: str = "",
    language: str = "it",
    trim: str = "a5",
    overrides: dict[str, Any] | None = None,
) -> bookgen_config.Config:
    """Configurazione di composizione per un libro che non ha una cartella di progetto sua.

    Serve alle schermate che esportano al volo: c'è un Markdown e un percorso di uscita, ma
    nessun ``book.yaml``. Le decisioni tipografiche restano quelle del compositore — misura,
    corpo derivato dalla giustezza, sillabazione dalla lingua — e chi ne sa di più le sovrascrive
    con ``overrides``, che ha la stessa forma del file.
    """
    data: dict[str, Any] = {
        "book": {
            "title": title or "Senza titolo",
            "subtitle": subtitle,
            "author": author,
            "language": (language or "it")[:2].lower(),
        },
        "format": {"preset": (trim or "a5").lower()},
        "output": {"formats": ["pdf", "docx", "epub"], "pdf_with_cover": False},
    }
    if overrides:
        data = _deep_merge(data, overrides)
    return bookgen_config.from_mapping(data, root=Path(root))


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------- #
#  dal Markdown al modello di libro
# --------------------------------------------------------------------------- #
def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    """``(riga, livello, testo)`` di ogni titolo, saltando i blocchi di codice."""
    trovati: list[tuple[int, int, str]] = []
    dentro_codice = False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("```"):
            dentro_codice = not dentro_codice
            continue
        if dentro_codice:
            continue
        match = _HEADING.match(stripped)
        if match:
            trovati.append((i, len(match.group(1)), match.group(2).strip()))
    return trovati


def _split_title_and_chapters(lines: list[str]) -> tuple[str, int, list[tuple[int, str]]]:
    """Titolo del libro, livello dei capitoli e posizione di ciascuno.

    Una regola sola, prevedibile: se il livello più alto compare **una volta** è il titolo del
    libro e i capitoli stanno al livello successivo; se compare più volte, quelli sono i capitoli
    e il titolo del libro va chiesto altrove.

    Le note bibliografiche non aprono mai un capitolo, a qualunque livello siano scritte:
    appartengono al capitolo che le precede, ed è da lì che il compositore le raccoglie per
    farne la bibliografia in fondo al volume.
    """
    heads = [h for h in _headings(lines) if not REFERENCE_HEADING.match("#" * h[1] + " " + h[2])]
    if not heads:
        return "", 0, []

    top = min(livello for _, livello, _ in heads)
    al_vertice = [h for h in heads if h[1] == top]
    if len(al_vertice) > 1:
        return "", top, [(i, testo) for i, livello, testo in heads if livello == top]

    titolo = al_vertice[0][2]
    piu_sotto = sorted({livello for _, livello, _ in heads if livello > top})
    if not piu_sotto:
        # Un titolo solo e nient'altro: tutto il testo è un capitolo unico.
        return "", top, [(al_vertice[0][0], titolo)]
    capitoli = piu_sotto[0]
    return titolo, capitoli, [(i, testo) for i, livello, testo in heads if livello == capitoli]


def _dedent_headings(lines: list[str], shift: int) -> list[str]:
    """Riporta i titoli interni al capitolo al livello che il compositore si aspetta.

    Nel Markdown di bookwriter una sezione dentro un capitolo è un ``###``; nel modello del
    compositore la prima suddivisione di un capitolo è un ``##``. Senza questo scalino ogni
    sezione scenderebbe di un rango e la gerarchia stampata direbbe il falso.
    """
    if shift <= 0:
        return lines
    fuori: list[str] = []
    dentro_codice = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("```"):
            dentro_codice = not dentro_codice
            fuori.append(raw)
            continue
        match = None if dentro_codice else _HEADING.match(stripped)
        if match:
            livello = max(2, len(match.group(1)) - shift)
            fuori.append("#" * livello + " " + match.group(2))
        else:
            fuori.append(raw)
    return fuori


def book_from_markdown(
    markdown: str,
    cfg: bookgen_config.Config,
    *,
    front: list[Section] | None = None,
    back: list[Section] | None = None,
) -> Book:
    """Costruisce il modello di libro dal Markdown, senza passare dal disco."""
    lines = markdown.splitlines()
    titolo, livello, posizioni = _split_title_and_chapters(lines)
    shift = max(0, livello - 1)

    front = list(front or [])
    back = list(back or [])
    capitoli: list[Chapter] = []

    if not posizioni:
        corpo, riferimenti = split_references(lines, cfg)
        capitoli.append(
            Chapter(number=1, title=cfg.title, blocks=parse_blocks(corpo, cfg),
                    references=riferimenti)
        )
    else:
        # Quel che precede il primo capitolo (titolo escluso) è un'apertura, non un capitolo.
        apertura = [l for i, l in enumerate(lines[: posizioni[0][0]]) if not _HEADING.match(l.strip())]
        blocchi_apertura = parse_blocks(apertura, cfg)
        if blocchi_apertura:
            front.insert(0, Section(ident="introduction",
                                    title=cfg.label("introduction", "Introduzione"),
                                    blocks=blocchi_apertura))

        confini = [i for i, _ in posizioni] + [len(lines)]
        for indice, (inizio, testo) in enumerate(posizioni, start=1):
            fine = confini[indice]
            numero, titolo_cap, sottotitolo = _chapter_title(testo, indice, cfg)
            corpo = _dedent_headings(lines[inizio + 1 : fine], shift)
            corpo, riferimenti = split_references(corpo, cfg)
            capitoli.append(
                Chapter(number=numero, title=titolo_cap, subtitle=sottotitolo,
                        blocks=parse_blocks(corpo, cfg), references=riferimenti)
            )

    riferimenti = dedupe_references([r for c in capitoli for r in c.references])
    if riferimenti and not any(s.ident == "bibliography" for s in back):
        back.append(Section(ident="bibliography",
                            title=cfg.label("bibliography", "Bibliografia"),
                            blocks=build_bibliography(riferimenti, cfg)))

    if titolo and cfg.title in ("", "Senza titolo", "Untitled"):
        cfg.title = clean_inline(titolo, cfg.language)

    return Book(
        title=cfg.title, subtitle=cfg.subtitle, author=cfg.author, language=cfg.language,
        parts=[Part(number=1, roman="", title="", chapters=capitoli)],
        front=front, back=back, references=riferimenti, config=cfg,
    )


def _chapter_title(testo: str, posizione: int, cfg: bookgen_config.Config) -> tuple[int, str, str]:
    """Numero, titolo e sottotitolo di un capitolo, dal testo del suo titolo."""
    testo = clean_inline(testo, cfg.language)
    numero = posizione
    match = _NUMBERED.match(testo)
    if match and match.group(2).strip():
        numero, testo = int(match.group(1)), match.group(2).strip()
    separatore = cfg.source.title_split
    if separatore and separatore in testo:
        titolo, sottotitolo = (p.strip() for p in testo.split(separatore, 1))
        return numero, titolo, sottotitolo
    return numero, testo, ""


# --------------------------------------------------------------------------- #
#  resa
# --------------------------------------------------------------------------- #
def render_book(
    book: Book,
    out_path: Path,
    fmt: str,
    cfg: bookgen_config.Config,
    *,
    cover: Path | None = None,
    interior_copy: Path | None = None,
) -> Composed:
    """Scrive il libro nel formato richiesto. ``md`` qui non arriva: è la sorgente."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    risultato = Composed(path=out_path, fmt=fmt,
                         chapters=len(book.chapters), words=book.word_count)

    if fmt == "pdf":
        from bookwriter.forge.bookgen.pdf import build_pdf

        info = build_pdf(book, out_path, cfg, cover=cover, interior_copy=interior_copy)
        risultato.pages = info.get("pages")
        risultato.fonts = list(info.get("fonts") or [])
    elif fmt == "docx":
        from bookwriter.forge.bookgen.docx_out import build_docx
        from bookwriter.forge.bookgen.typography import register_fonts, theme_from_config

        theme = register_fonts(theme_from_config(cfg), cfg)
        build_docx(book, out_path, cfg, serif=theme.serif_name, sans=theme.sans_name)
        risultato.fonts = list(theme.font_report)
    elif fmt == "epub":
        from bookwriter.forge.bookgen.epub import build_epub

        build_epub(book, out_path, cfg, cover=cover)
    else:
        raise ValueError(f"Formato non supportato: {fmt} (usa {'|'.join(SUPPORTED_FORMATS)})")
    return risultato


def compose_markdown(
    markdown: str,
    out_path: Path,
    fmt: str,
    *,
    title: str = "",
    subtitle: str = "",
    author: str = "",
    language: str = "it",
    trim: str = "a5",
    cover: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Composed:
    """Markdown → libro impaginato. È il punto unico usato da tutte le modalità."""
    out_path = Path(out_path)
    if fmt == "md":
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        return Composed(path=out_path, fmt="md")

    cfg = book_config(
        out_path.parent, title=title, subtitle=subtitle, author=author,
        language=language, trim=trim, overrides=overrides,
    )
    book = book_from_markdown(markdown, cfg)
    return render_book(book, out_path, fmt, cfg, cover=cover)


__all__ = [
    "SUPPORTED_FORMATS",
    "Composed",
    "book_config",
    "book_from_markdown",
    "compose_markdown",
    "render_book",
]
