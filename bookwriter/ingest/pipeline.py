"""Pipeline di ingestione: file singoli o intere directory → Document + Chunk in project.db.

Caratteristiche: scansione ricorsiva, filtri include/exclude, **dedup per sha256** (re-ingestione
idempotente/incrementale), linking prev/next dei chunk, riepilogo con importati/saltati/falliti.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from sqlmodel import Session, func, select

from bookwriter.db.project_schema import Chunk as ChunkRow
from bookwriter.db.project_schema import Document, make_engine
from bookwriter.ingest.chunker import chunk_document
from bookwriter.ingest.loaders import SUPPORTED_EXTENSIONS, load_document
from bookwriter.ingest.structure import parse_structure


@dataclass
class IngestReport:
    imported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # duplicati (già presenti)
    failed: list[tuple[str, str]] = field(default_factory=list)
    chunks: int = 0

    def summary(self) -> str:
        return (
            f"importati={len(self.imported)} · saltati(dup)={len(self.skipped)} · "
            f"falliti={len(self.failed)} · chunk={self.chunks}"
        )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _matches(name: str, patterns: list[str] | None) -> bool:
    return any(fnmatch(name, pat) for pat in patterns) if patterns else False


def iter_files(
    paths: list[Path],
    *,
    recursive: bool,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> list[Path]:
    """Espande file e directory in un elenco ordinato e deduplicato di file supportati."""
    found: list[Path] = []
    seen: set[Path] = set()

    def consider(f: Path) -> None:
        if not f.is_file() or f in seen:
            return
        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return
        if include and not _matches(f.name, include):
            return
        if exclude and _matches(f.name, exclude):
            return
        seen.add(f)
        found.append(f)

    for p in paths:
        if p.is_dir():
            for f in sorted(p.rglob("*") if recursive else p.glob("*")):
                consider(f)
        else:
            consider(p)
    return found


def ingest_paths(
    paths: list[Path],
    *,
    db_path: Path,
    target_tokens: int = 1500,
    recursive: bool = False,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    transient: bool = False,
    progress=None,
) -> IngestReport:
    """Importa i documenti dai path indicati. ``progress`` è un callback opzionale ``(done, total, name)``.

    Con ``transient=True`` i documenti entrano come materiale di lavoro: restano usabili per
    tutta la sessione ma non fanno parte delle fonti del progetto e spariscono alla riapertura.
    """
    files = iter_files(paths, recursive=recursive, include=include, exclude=exclude)
    report = IngestReport()
    engine = make_engine(db_path)

    with Session(engine) as ses:
        existing = set(ses.exec(select(Document.sha256)).all())
        for idx, path in enumerate(files):
            if progress:
                progress(idx, len(files), path.name)
            try:
                digest = _sha256_bytes(path.read_bytes())
                if digest in existing:
                    report.skipped.append(str(path))
                    if not transient:
                        # Lo stesso file già presente come documento di lavoro, ora importato
                        # davvero: da qui in poi è una fonte del progetto e non va più cancellato.
                        for row in ses.exec(
                            select(Document).where(Document.sha256 == digest)
                        ).all():
                            if row.transient:
                                row.transient = False
                                ses.add(row)
                        ses.commit()
                    continue
                doc = load_document(path)
                blocks = parse_structure(doc.raw_text)
                chunks = chunk_document(doc.raw_text, blocks, target_tokens=target_tokens)

                row = Document(
                    path=str(path),
                    format=doc.format,
                    sha256=digest,
                    title=doc.title,
                    char_count=len(doc.raw_text),
                    transient=transient,
                    # Lo scheletro viaggia col documento: senza, alla riconsegna non si saprebbe
                    # più dove stavano titoli, stacchi di pagina e corsivi.
                    meta={"layout": doc.layout.to_dict()} if doc.layout else {},
                )
                ses.add(row)
                ses.commit()
                ses.refresh(row)

                chunk_rows = [
                    ChunkRow(
                        document_id=row.id,
                        book_pos=c.book_pos,
                        chapter_ref=c.chapter_ref,
                        span_start=c.span_start,
                        span_end=c.span_end,
                        text=c.text,
                        token_count=c.token_count,
                        sha256=_sha256_text(c.text),
                    )
                    for c in chunks
                ]
                ses.add_all(chunk_rows)
                ses.commit()
                for cr in chunk_rows:
                    ses.refresh(cr)
                for k, cr in enumerate(chunk_rows):
                    cr.prev_id = chunk_rows[k - 1].id if k > 0 else None
                    cr.next_id = chunk_rows[k + 1].id if k < len(chunk_rows) - 1 else None
                ses.add_all(chunk_rows)
                ses.commit()

                existing.add(digest)
                report.imported.append(str(path))
                report.chunks += len(chunk_rows)
            except Exception as exc:  # noqa: BLE001 - registriamo e proseguiamo col file successivo
                report.failed.append((str(path), str(exc)))
        if progress:
            progress(len(files), len(files), "")
    return report


def memory_stats(db_path: Path) -> dict:
    """Conteggi della memoria: documenti, chunk, token, ripartizione per formato."""
    engine = make_engine(db_path)
    with Session(engine) as ses:
        n_docs = ses.exec(select(func.count()).select_from(Document)).one()
        n_chunks = ses.exec(select(func.count()).select_from(ChunkRow)).one()
        total_tokens = ses.exec(select(func.coalesce(func.sum(ChunkRow.token_count), 0))).one()
        by_format = dict(
            ses.exec(select(Document.format, func.count()).group_by(Document.format)).all()
        )
    return {
        "documents": int(n_docs),
        "chunks": int(n_chunks),
        "total_tokens_est": int(total_tokens),
        "by_format": by_format,
    }
