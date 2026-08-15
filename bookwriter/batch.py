"""Coda di libri: si preparano tutti i lavori, poi si va a dormire.

Tradurre o riscrivere un libro dura ore. Restare davanti al programma per lanciare il successivo
quando il primo finisce è tempo di una persona speso a fare il lavoro di un contatore. Qui i lavori
si mettono in fila — ciascuno col proprio documento, la propria lingua o il proprio stile, il
proprio file di destinazione e il proprio formato — e partono uno dopo l'altro.

Uno alla volta e non in parallelo: due libri insieme si contendono lo stesso modello e finiscono
entrambi più tardi di quanto avrebbero fatto in fila. Un lavoro che fallisce non ferma la coda: si
registra il motivo e si passa al successivo, perché la ragione della coda è tornare e trovare fatto
tutto quello che si poteva fare.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bookwriter.llm.client import LLMError
from bookwriter.rewrite import rewrite_document, write_result

# Stati di un lavoro, come li legge chi guarda la coda.
ATTESA = "in attesa"
IN_CORSO = "in corso"
FATTO = "fatto"
FALLITO = "fallito"
ANNULLATO = "annullato"
INTERROTTO = "interrotto"


@dataclass
class QueuedBook:
    """Un libro in fila, con tutto quello che serve a lavorarlo senza chiedere altro."""

    document_id: int
    title: str  # come compare nell'elenco, per farsi riconoscere
    out_path: Path  # dove finirà: nome e cartella li sceglie chi mette in coda
    fmt: str = "docx"
    mode: str = "translate"  # translate|rewrite
    contract: str = ""
    target_language: str | None = None
    style_name: str = ""
    style_profile_id: int | None = None
    ratio: float | None = None
    critic: bool = True
    state: str = ATTESA
    detail: str = ""

    @property
    def operation(self) -> str:
        """L'operazione come la chiamerebbe l'utente, non come si chiama nel motore."""
        if self.mode == "translate":
            return f"traduci in {self.target_language}" if self.target_language else "traduci"
        return f"riscrivi nello stile «{self.style_name}»" if self.style_name else "riscrivi"


# Quanto costa una lingua, in ordine di lavorazione. Non è una classifica di importanza: è il
# prezzo che il modello paga per scriverla. Le lingue che il tokenizzatore spezza in molti pezzi
# richiedono più passi per la stessa pagina — l'ungherese misurato costa il triplo dell'inglese —
# e mettere le economiche davanti significa avere più libri finiti prima, a parità di ore.
_GRUPPI_LINGUA: list[tuple[int, tuple[str, ...]]] = [
    (0, ("ingl", "engl", "franc", "french", "tedesc", "german", "deutsch",
         "spagn", "spanish", "españ", "portogh", "portug", "italian")),
    (1, ("ungher", "hungar", "magyar", "russ", "ucrain", "ukrain")),
]


def language_cost_group(language: str | None) -> int:
    """Gruppo di costo di una lingua: 0 le latine, 1 le slave e l'ungherese, 2 tutte le altre.

    Il confronto è per prefisso e senza maiuscole, così regge i modi in cui una lingua viene
    scritta a mano — «Giappanese», «Arabico», «Indi» — invece di pretendere un elenco esatto.
    """
    nome = (language or "").strip().lower()
    for gruppo, prefissi in _GRUPPI_LINGUA:
        if any(nome.startswith(p) for p in prefissi):
            return gruppo
    return 2


def sort_queue_by_language_cost(books: list[QueuedBook]) -> list[QueuedBook]:
    """Rimette la coda in ordine di costo, senza toccare i libri già consegnati.

    Dentro ogni gruppo l'ordine che avevi dato resta: si spostano i blocchi, non le singole voci.
    """
    consegnati = [b for b in books if b.state == FATTO]
    restanti = [b for b in books if b.state != FATTO]
    return consegnati + sorted(restanti, key=lambda b: language_cost_group(b.target_language))


def load_queue(db_path: Path) -> list[QueuedBook]:
    """Rilegge la coda dal progetto, nell'ordine in cui è stata composta."""
    from sqlmodel import Session, select

    from bookwriter.db.project_schema import QueuedBookRow, ensure_columns, make_engine

    if not db_path.exists():
        return []
    engine = make_engine(db_path)
    ensure_columns(engine, QueuedBookRow)
    with Session(engine) as ses:
        righe = list(ses.exec(select(QueuedBookRow).order_by(QueuedBookRow.position)).all())
    return [
        QueuedBook(
            document_id=r.document_id, title=r.title, out_path=Path(r.out_path), fmt=r.fmt,
            mode=r.mode, contract=r.contract, target_language=r.target_language,
            style_name=r.style_name, style_profile_id=r.style_profile_id,
            ratio=r.ratio, critic=bool(r.critic),
            state=r.state, detail=r.detail,
        )
        for r in righe
    ]


def save_queue(db_path: Path, books: list[QueuedBook]) -> None:
    """Riscrive la coda del progetto così com'è adesso: ordine, destinazioni e stati."""
    from sqlmodel import Session, select

    from bookwriter.db.project_schema import QueuedBookRow, ensure_columns, make_engine

    engine = make_engine(db_path)
    ensure_columns(engine, QueuedBookRow)
    with Session(engine) as ses:
        for vecchia in ses.exec(select(QueuedBookRow)).all():
            ses.delete(vecchia)
        for posizione, libro in enumerate(books):
            ses.add(QueuedBookRow(
                position=posizione, document_id=libro.document_id, title=libro.title,
                out_path=str(libro.out_path), fmt=libro.fmt, mode=libro.mode,
                contract=libro.contract, target_language=libro.target_language,
                style_name=libro.style_name, style_profile_id=libro.style_profile_id,
                ratio=libro.ratio, critic=libro.critic,
                state=libro.state, detail=libro.detail,
            ))
        ses.commit()


@dataclass
class BatchReport:
    books: list[QueuedBook] = field(default_factory=list)
    stopped: bool = False

    @property
    def done(self) -> int:
        return sum(1 for b in self.books if b.state == FATTO)

    @property
    def failed(self) -> int:
        return sum(1 for b in self.books if b.state == FALLITO)

    def summary(self) -> str:
        parti = [f"{self.done}/{len(self.books)} libri completati"]
        if self.failed:
            parti.append(f"{self.failed} non riusciti")
        interrotti = sum(1 for b in self.books if b.state == INTERROTTO)
        if interrotti:
            parti.append(f"{interrotti} interrotti a metà (riprendono da dove erano)")
        annullati = sum(1 for b in self.books if b.state == ANNULLATO)
        if annullati:
            parti.append(f"{annullati} non avviati")
        if self.stopped:
            parti.append("coda fermata")
        return " · ".join(parti)


def run_queue(
    books: list[QueuedBook],
    db_path: Path,
    client: Any,
    *,
    model: str | None = None,
    critic_model: str | None = None,
    progress: Callable[[int, int, int, int], None] | None = None,
    on_change: Callable[[int, QueuedBook], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> BatchReport:
    """Lavora la coda dall'inizio alla fine.

    ``progress(indice, totale, fatti, da_fare)`` riporta a che punto è la coda e a che punto è il
    libro corrente; ``on_change(indice, libro)`` avvisa a ogni cambio di stato, così chi guarda
    vede la fila avanzare.

    Due modi di fermarsi, perché sono due intenzioni diverse. ``should_stop`` è consultato **fra
    un libro e l'altro**: il libro in lavorazione arriva in fondo e il suo file viene scritto —
    è il «finisci questo e basta» di chi ha finito il tempo. ``should_abort`` è consultato **a
    ogni passaggio**: si smette subito, il file non si scrive, ma i brani già elaborati restano
    salvati e la ripresa parte da lì.
    """
    should_abort = should_abort or should_stop
    report = BatchReport(books=books)

    def annuncia(indice: int, libro: QueuedBook) -> None:
        if on_change:
            on_change(indice, libro)

    # Il prossimo libro si sceglie **a ogni giro dalla fila viva**, non da una copia presa
    # all'avvio: chi riordina la coda mentre lavora si aspetta che l'ordine nuovo valga da
    # subito, non alla prossima sessione. Per la stessa ragione la lista va modificata sul
    # posto da chi la riordina, non sostituita.
    while True:
        libro = next((b for b in books if b.state == ATTESA), None)
        if libro is None:
            break
        indice = books.index(libro)
        if (should_stop and should_stop()) or (should_abort and should_abort()):
            report.stopped = True
            for n, rimasto in enumerate(books):
                if rimasto.state == ATTESA:
                    rimasto.state, rimasto.detail = ANNULLATO, "coda fermata prima di arrivarci"
                    annuncia(n, rimasto)
            break

        libro.state, libro.detail = IN_CORSO, ""
        annuncia(indice, libro)

        def avanza(fatti: int, da_fare: int, _i: int = indice, _libro: QueuedBook = libro) -> None:
            # L'avanzamento del libro sta nella sua riga: la barra della coda si muove di un punto
            # ogni venticinque minuti, e da fuori è indistinguibile da un blocco.
            _libro.detail = f"{fatti}/{da_fare} brani"
            if progress:
                progress(_i, len(books), fatti, da_fare)
            annuncia(_i, _libro)

        try:
            rapporto = rewrite_document(
                db_path,
                libro.document_id,
                libro.contract,
                client,
                mode=libro.mode,
                target_language=libro.target_language,
                style_profile_id=libro.style_profile_id,
                ratio=libro.ratio,
                kind="full",
                model=model,
                critic=libro.critic,
                critic_model=critic_model,
                progress=avanza,
                should_stop=should_abort,
            )
            if rapporto.stopped:
                # Interrotto a metà: il file non si scrive, perché un libro monco che si presenta
                # come finito è peggio di nessun libro. Quello che è stato fatto resta salvato.
                libro.state = INTERROTTO
                libro.detail = f"{rapporto.processed}/{rapporto.total} passaggi fatti · riparte da qui"
                annuncia(indice, libro)
                continue
            _percorso, nota = write_result(db_path, rapporto.run_id, libro.out_path, libro.fmt)
        except (LLMError, ValueError, RuntimeError, OSError) as exc:
            # Un libro che non riesce non è una ragione per non fare gli altri: si dichiara e si
            # tira avanti. È il motivo per cui esiste la coda.
            libro.state, libro.detail = FALLITO, str(exc)
        else:
            libro.state = FATTO
            libro.detail = f"{rapporto.summary()}{f' · {nota}' if nota else ''}"
        annuncia(indice, libro)

    if progress:
        progress(len(books), len(books), 1, 1)
    return report
