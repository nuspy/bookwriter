"""Caricamento del testo grezzo dai vari formati.

txt/md sono nativi (nessuna dipendenza). pdf/docx/epub usano dipendenze opzionali (extra
``ingest``), importate in modo lazy con messaggio d'errore utile se mancano. I loader strutturati
(docx/epub) emettono i titoli come righe in stile Markdown (``# Titolo``) così il parser strutturale
li riconosce in modo uniforme.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bookwriter.text_utils import normalize_text

SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".text", ".pdf", ".docx", ".epub"}


@dataclass
class LoadedDoc:
    path: Path
    format: str  # estensione senza punto (txt, md, pdf, docx, epub)
    raw_text: str  # testo normalizzato
    title: str | None = None


class LoaderError(RuntimeError):
    pass


def _missing_dep(fmt: str, package: str) -> LoaderError:
    return LoaderError(
        f"Per importare file {fmt} serve la dipendenza '{package}'. "
        f"Installa gli extra: pip install -e \".[ingest]\""
    )


def load_document(path: str | Path) -> LoadedDoc:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".txt", ".text", ".md", ".markdown"):
        raw = path.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        raw = _load_pdf(path)
    elif ext == ".docx":
        raw = _load_docx(path)
    elif ext == ".epub":
        raw = _load_epub(path)
    else:
        raise LoaderError(f"Formato non supportato: {ext} ({path.name})")
    fmt = ext.lstrip(".")
    return LoadedDoc(path=path, format=fmt, raw_text=normalize_text(raw), title=path.stem)


def _load_pdf(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except ModuleNotFoundError as exc:  # pragma: no cover - dipende dall'ambiente
        raise _missing_dep("PDF", "pymupdf") from exc
    parts: list[str] = []
    with fitz.open(path) as doc:
        for page in doc:
            parts.append(page.get_text("text"))
    return "\n\n".join(parts)


def _load_docx(path: Path) -> str:
    try:
        import docx  # python-docx
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise _missing_dep("DOCX", "python-docx") from exc
    document = docx.Document(str(path))
    lines: list[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            lines.append("")
            continue
        style = (para.style.name or "").lower() if para.style else ""
        if style.startswith("heading") or style == "title":
            level = "".join(c for c in style if c.isdigit())
            hashes = "#" * (int(level) if level else 1)
            lines.append(f"{hashes} {text}")
        else:
            lines.append(text)
    return "\n\n".join(lines)


def _load_epub(path: Path) -> str:
    try:
        from bs4 import BeautifulSoup
        from ebooklib import ITEM_DOCUMENT, epub
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise _missing_dep("EPUB", "ebooklib e beautifulsoup4") from exc
    book = epub.read_epub(str(path))
    parts: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            level = int(tag.name[1])
            tag.replace_with(f"\n\n{'#' * level} {tag.get_text(strip=True)}\n\n")
        text = soup.get_text("\n")
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts)
