"""Il libro mentre nasce: quello che è già stato scritto, prima che il file esista.

Un libro lungo si lavora per ore. Finché non finisce, il file non c'è — di proposito, perché un
file a metà col nome di quello finito si scambia troppo facilmente per il libro vero. Ma il testo
c'è già, brano per brano, nel progetto: da qui si legge senza aspettare.

Vale per tutte le lavorazioni che procedono a pezzi: traduzione, riscrittura, stesura in prosa e
bozza autoriale. Ciascuna salva quello che finisce, e quello che è salvato si può guardare.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, func, select

from bookwriter.db.project_schema import (
    Chunk,
    ComposedChapter,
    Document,
    Outline,
    RewriteChunk,
    RewriteRun,
    make_engine,
)


@dataclass
class Sorgente:
    """Una lavorazione di cui si può leggere il risultato parziale."""

    kind: str  # rewrite|compose
    ident: int  # id della lavorazione o dell'outline
    label: str  # come la legge l'utente
    done: int  # pezzi già scritti
    total: int  # pezzi previsti (0 se non si sa)
    running: bool

    @property
    def progress(self) -> str:
        if not self.total:
            return f"{self.done} pezzi scritti"
        return f"{self.done}/{self.total} ({round(100 * self.done / self.total)}%)"


def sources(db_path: Path) -> list[Sorgente]:
    """Tutte le lavorazioni leggibili, le più recenti per prime, quella in corso in testa."""
    if not db_path.exists():
        return []
    with Session(make_engine(db_path)) as ses:
        fuori: list[Sorgente] = []
        for run in ses.exec(select(RewriteRun).order_by(RewriteRun.id.desc())).all():
            fatti = ses.exec(
                select(func.count()).select_from(RewriteChunk).where(RewriteChunk.run_id == run.id)
            ).one()
            if not fatti:
                continue
            totale = ses.exec(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == run.document_id)
            ).one()
            doc = ses.get(Document, run.document_id)
            titolo = (doc.title if doc else None) or f"documento #{run.document_id}"
            verso = f" → {run.target_language}" if run.target_language else ""
            azione = "traduzione" if run.mode == "translate" else "riscrittura"
            fuori.append(Sorgente(
                kind="rewrite", ident=run.id, done=fatti, total=totale,
                running=run.status == "running",
                label=f"{azione}: {titolo}{verso}",
            ))
        for outline in ses.exec(select(Outline).order_by(Outline.id.desc())).all():
            scritti = ses.exec(
                select(func.count()).select_from(ComposedChapter)
                .where(ComposedChapter.outline_id == outline.id)
            ).one()
            if not scritti:
                continue
            fuori.append(Sorgente(
                kind="compose", ident=outline.id, done=scritti, total=0, running=False,
                label=f"stesura in prosa: {outline.name}",
            ))
    return sorted(fuori, key=lambda s: (not s.running, -s.ident))


def text_of(db_path: Path, sorgente: Sorgente) -> str:
    """Il testo scritto finora da quella lavorazione, in Markdown."""
    if sorgente.kind == "compose":
        from bookwriter.compose import compose_to_markdown

        return compose_to_markdown(db_path, sorgente.ident)
    from bookwriter.rewrite import assemble_markdown

    return assemble_markdown(db_path, sorgente.ident)


# --------------------------------------------------------------------------- #
#  resa leggibile
# --------------------------------------------------------------------------- #
_TITOLO = re.compile(r"^(#{1,6})\s+(.*)$")
_GRASSETTO = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_CORSIVO = re.compile(r"(?<!\*)\*([^*]+?)\*(?!\*)")


def _riga_html(riga: str) -> str:
    import html as _html

    testo = _html.escape(riga)
    testo = _GRASSETTO.sub(r"<b>\1</b>", testo)
    testo = _CORSIVO.sub(r"<i>\1</i>", testo)
    return testo


def to_html(markdown: str, *, evidenzia_coda: int = 0) -> str:
    """Markdown → HTML semplice, per leggerlo come un libro invece che come un file.

    ``evidenzia_coda`` marca gli ultimi N caratteri: è la parte arrivata da poco, e trovarla a
    colpo d'occhio è l'unica ragione per stare a guardare un libro che si scrive da solo.
    """
    confine = max(0, len(markdown) - evidenzia_coda) if evidenzia_coda else len(markdown) + 1
    fuori: list[str] = []
    posizione = 0
    aperta_nuova = False
    for riga in markdown.splitlines():
        if posizione >= confine and not aperta_nuova:
            fuori.append('<div style="background:#fff8e1">')
            aperta_nuova = True
        posizione += len(riga) + 1
        nuda = riga.strip()
        if not nuda:
            continue
        titolo = _TITOLO.match(nuda)
        if titolo:
            livello = min(len(titolo.group(1)), 6)
            corpo = _riga_html(titolo.group(2))
            fuori.append(f"<h{livello}>{corpo}</h{livello}>")
        elif nuda == "---":
            fuori.append("<hr>")
        elif nuda.startswith(("- ", "* ", "+ ")):
            fuori.append(f"<div style='margin-left:1.2em'>• {_riga_html(nuda[2:])}</div>")
        elif nuda.startswith("|"):
            fuori.append(f"<div style='font-family:monospace;font-size:90%'>{_riga_html(nuda)}</div>")
        else:
            fuori.append(f"<p>{_riga_html(nuda)}</p>")
    if aperta_nuova:
        fuori.append("</div>")
    return (
        "<html><body style=\"font-family:Georgia,serif; line-height:1.5; max-width:46em\">"
        + "\n".join(fuori)
        + "</body></html>"
    )
