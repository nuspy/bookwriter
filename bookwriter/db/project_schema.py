"""Schema per-progetto (``<progetto>/project.db``).

Definisce le entità della memoria condivise dalle due modalità (Compose e Rewrite). Le colonne
vettoriali NON stanno qui: gli embedding dei nodi vivono in LanceDB (``vectors.lance``), collegati
per ``id``. Alcune tabelle sono predisposte per le fasi successive ma create fin da subito, così lo
schema è stabile.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Column, Engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import Field, Session, SQLModel, create_engine, select


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Document(SQLModel, table=True):
    __tablename__ = "document"

    id: int | None = Field(default=None, primary_key=True)
    path: str
    format: str
    sha256: str = Field(index=True)
    title: str | None = None
    char_count: int = 0
    imported_at: str = Field(default_factory=_now_iso)
    # Documento di lavoro: un libro aperto per tradurlo o riscriverlo non è materiale del
    # progetto, è il pezzo su cui si sta lavorando. Resta finché serve e sparisce alla
    # riapertura, così l'elenco dei testi importati continua a dire una cosa sola: le fonti.
    transient: bool = Field(default=False, index=True)
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))


class Chunk(SQLModel, table=True):
    """Segmento deterministico di un documento (condiviso da Compose e Rewrite)."""

    __tablename__ = "chunk"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    book_pos: int = Field(index=True)
    chapter_ref: str | None = None
    span_start: int = 0
    span_end: int = 0
    text: str = ""
    token_count: int = 0
    prev_id: int | None = None
    next_id: int | None = None
    sha256: str = Field(index=True)


class Node(SQLModel, table=True):
    """Idea atomica (modalità Compose). L'embedding è in LanceDB, join per ``id``."""

    __tablename__ = "node"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int | None = Field(default=None, foreign_key="document.id", index=True)
    chunk_id: int | None = Field(default=None, foreign_key="chunk.id")
    raw_text: str = ""
    summary: str = ""
    type: str = "idea"  # idea|fatto|citazione|aneddoto|argomento|domanda
    themes: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    entities: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    event_date: str | None = None
    date_confidence: float = 0.0
    salience: float = 0.5
    status: str = "active"
    source: str = "ingest"  # ingest|dialogue (nodo creato dall'interazione, §7.6)
    created_at: str = Field(default_factory=_now_iso)


class Edge(SQLModel, table=True):
    """Relazione tra nodi: abilita i diversi ordinamenti."""

    __tablename__ = "edge"

    id: int | None = Field(default=None, primary_key=True)
    src_node: int = Field(foreign_key="node.id", index=True)
    dst_node: int = Field(foreign_key="node.id", index=True)
    type: str = "thematic"  # thematic|temporal|causal|elaborates|contradicts|example_of|references
    weight: float = 0.5
    rationale: str | None = None
    source: str = "llm"  # llm|manual


class Job(SQLModel, table=True):
    """Checkpoint di fasi pesanti (ingestione/atomizzazione/embedding/riscrittura)."""

    __tablename__ = "job"

    id: int | None = Field(default=None, primary_key=True)
    kind: str
    state: str = "pending"  # pending|running|done|failed
    progress: float = 0.0
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: str = Field(default_factory=_now_iso)


class BookSpec(SQLModel, table=True):
    """Specifica del libro: target di lunghezza in parole (singleton, id=1). Vedi PLAN §7.5."""

    __tablename__ = "bookspec"

    id: int | None = Field(default=1, primary_key=True)
    target_words: int | None = None
    tolerance_pct: int = 5
    distribution: str = "proportional"  # proportional|equal|manual
    per_chapter_words: dict = Field(default_factory=dict, sa_column=Column(JSON))
    updated_at: str = Field(default_factory=_now_iso)


class WorkSession(SQLModel, table=True):
    """Una sessione di lavoro (per la continuità multi-sessione, §7.7)."""

    __tablename__ = "session"

    id: int | None = Field(default=None, primary_key=True)
    mode: str = "compose"  # compose|rewrite|translate|author
    started_at: str = Field(default_factory=_now_iso)
    ended_at: str | None = None


class Message(SQLModel, table=True):
    """Traccia del dialogo, per ricostruire il contesto in sessioni successive."""

    __tablename__ = "message"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int | None = Field(default=None, foreign_key="session.id", index=True)
    role: str = "user"  # user|assistant
    content: str = ""
    created_at: str = Field(default_factory=_now_iso)


class Intent(SQLModel, table=True):
    """Intento/decisione persistito (ordinamento, stile, scopo, obiettivo, lacuna, todo…)."""

    __tablename__ = "intent"

    id: int | None = Field(default=None, primary_key=True)
    kind: str  # ordering|style|scope|audience|goal|content-gap|todo
    value: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "open"  # open|resolved|dropped
    rationale: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class OpenQuestion(SQLModel, table=True):
    """Domanda posta dall'LLM per definire/completare i contenuti (§7.6)."""

    __tablename__ = "open_question"

    id: int | None = Field(default=None, primary_key=True)
    text: str
    status: str = "open"  # open|answered
    answer: str | None = None
    created_from_session: int | None = None
    created_at: str = Field(default_factory=_now_iso)


class StyleProfileRow(SQLModel, table=True):
    """Profilo di stile persistito (JSON completo). Vedi PLAN §6."""

    __tablename__ = "style_profile"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    origin: str = "extracted"  # extracted|abstract|authors
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: str = Field(default_factory=_now_iso)


class ComposedChapter(SQLModel, table=True):
    """Capitolo generato in prosa (stesura narrativa, §8.1). Ricomposto per ``position``."""

    __tablename__ = "composed_chapter"

    id: int | None = Field(default=None, primary_key=True)
    outline_id: int = Field(foreign_key="outline.id", index=True)
    chapter_id: int = Field(foreign_key="outline_chapter.id")
    position: int = 0
    title: str = ""
    text: str = ""
    words: int = 0
    # Impronta dei parametri usati (stile, lunghezza, lingua): se cambiano, il capitolo
    # va riscritto invece di essere riusato.
    signature: str = ""
    created_at: str = Field(default_factory=_now_iso)


class RewriteRun(SQLModel, table=True):
    """Una riscrittura (modalità B/C): checkpoint + stato di continuità. Vedi PLAN §8.5."""

    __tablename__ = "rewrite_run"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    style_profile_id: int | None = None
    kind: str = "full"  # full|preview
    mode: str = "rewrite"  # rewrite|translate
    target_language: str | None = None  # per la traduzione (§8.6)
    ratio: float | None = None  # rapporto di lunghezza sul testo originale
    status: str = "running"  # running|done
    continuity: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: str = Field(default_factory=_now_iso)


class RewriteChunk(SQLModel, table=True):
    """Output riscritto di un chunk dentro una run (ricomposizione per ``book_pos``)."""

    __tablename__ = "rewrite_chunk"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="rewrite_run.id", index=True)
    chunk_id: int = Field(foreign_key="chunk.id")
    book_pos: int = Field(index=True)
    chapter_ref: str | None = None
    text: str = ""
    created_at: str = Field(default_factory=_now_iso)


class QueuedBookRow(SQLModel, table=True):
    """Un libro in fila per essere tradotto o riscritto (§ coda di lavorazione).

    Sta nel database e non nella memoria della finestra perché una coda di lavori che durano ore
    deve sopravvivere alla chiusura del programma: chi la compone la sera si aspetta di ritrovarla
    la mattina, non di ricomporla.
    """

    __tablename__ = "queued_book"

    id: int | None = Field(default=None, primary_key=True)
    position: int = Field(default=0, index=True)
    document_id: int = Field(foreign_key="document.id", index=True)
    title: str = ""
    out_path: str = ""
    fmt: str = "docx"
    mode: str = "translate"  # translate|rewrite
    contract: str = ""
    target_language: str | None = None
    style_name: str = ""
    style_profile_id: int | None = None
    ratio: float | None = None
    critic: bool = True
    state: str = "in attesa"
    detail: str = ""
    created_at: str = Field(default_factory=_now_iso)


class Outline(SQLModel, table=True):
    """Struttura del libro: percorso sui nodi. Vedi PLAN §7."""

    __tablename__ = "outline"

    id: int | None = Field(default=None, primary_key=True)
    name: str = "Outline"
    ordering: str = "thematic"  # thematic|chronological|narrative|custom
    render_mode: str = "verbatim"  # verbatim|narrative (§8.0)
    style_profile_id: int | None = None
    status: str = "draft"  # draft|approved
    created_at: str = Field(default_factory=_now_iso)


class OutlineChapter(SQLModel, table=True):
    __tablename__ = "outline_chapter"

    id: int | None = Field(default=None, primary_key=True)
    outline_id: int = Field(foreign_key="outline.id", index=True)
    position: int = 0
    title: str = ""


class OutlineSection(SQLModel, table=True):
    """Sezione (sotto-capitolo) opzionale. Capitolo → sezioni → nodi (§7)."""

    __tablename__ = "outline_section"

    id: int | None = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="outline_chapter.id", index=True)
    position: int = 0
    title: str = ""


class OutlineItem(SQLModel, table=True):
    """Riferimento a un nodo dentro un capitolo (l'ordine è ``position``)."""

    __tablename__ = "outline_item"

    id: int | None = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="outline_chapter.id", index=True)
    section_id: int | None = Field(default=None, foreign_key="outline_section.id")
    position: int = 0
    node_id: int = Field(foreign_key="node.id")


class TokenUsage(SQLModel, table=True):
    """Token consumati da una singola esecuzione (per attività e modello). Vedi §12.5."""

    __tablename__ = "token_usage"

    id: int | None = Field(default=None, primary_key=True)
    activity: str = Field(index=True)  # etichetta dell'operazione, come la vede l'utente
    model: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Prezzi applicati (dollari per milione di token) e costo risultante: congelati al momento
    # della registrazione, così lo storico resta valido anche se il listino cambia.
    price_in: float = 0.0
    price_out: float = 0.0
    cost: float = 0.0
    created_at: str = Field(default_factory=_now_iso)


_PROJECT_TABLES = [
    Document.__table__,  # type: ignore[attr-defined]
    Chunk.__table__,  # type: ignore[attr-defined]
    Node.__table__,  # type: ignore[attr-defined]
    Edge.__table__,  # type: ignore[attr-defined]
    Job.__table__,  # type: ignore[attr-defined]
    BookSpec.__table__,  # type: ignore[attr-defined]
    Outline.__table__,  # type: ignore[attr-defined]
    OutlineChapter.__table__,  # type: ignore[attr-defined]
    OutlineSection.__table__,  # type: ignore[attr-defined]
    OutlineItem.__table__,  # type: ignore[attr-defined]
    WorkSession.__table__,  # type: ignore[attr-defined]
    Message.__table__,  # type: ignore[attr-defined]
    Intent.__table__,  # type: ignore[attr-defined]
    OpenQuestion.__table__,  # type: ignore[attr-defined]
    StyleProfileRow.__table__,  # type: ignore[attr-defined]
    ComposedChapter.__table__,  # type: ignore[attr-defined]
    RewriteRun.__table__,  # type: ignore[attr-defined]
    RewriteChunk.__table__,  # type: ignore[attr-defined]
    QueuedBookRow.__table__,  # type: ignore[attr-defined]
    TokenUsage.__table__,  # type: ignore[attr-defined]
]


def make_engine(db_path: Path) -> Engine:
    # NullPool: nessuna connessione trattenuta nel pool → il file SQLite non resta lockato
    # (necessario su Windows per poter cancellare/spostare i progetti).
    # timeout: davanti a un lock si aspetta invece di fallire subito, perché il contatore dei
    # token scrive mentre un lavoro lungo sta scrivendo i propri risultati.
    engine = create_engine(
        f"sqlite:///{db_path}", poolclass=NullPool, connect_args={"timeout": 30.0}
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - configurazione driver
        cursor = dbapi_connection.cursor()
        try:
            # Prima l'attesa: così anche il passaggio a WAL può aspettare invece di fallire.
            cursor.execute("PRAGMA busy_timeout=30000")
            # WAL: un lettore non blocca uno scrittore e viceversa. Richiede accesso esclusivo
            # al momento del passaggio: se un altro processo tiene il file, si prosegue con la
            # modalità attuale invece di impedire la connessione.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        finally:
            cursor.close()

    return engine


def init_project_db(db_path: Path) -> Engine:
    """Crea (se assenti) solo le tabelle di progetto nel DB indicato."""
    engine = make_engine(db_path)
    SQLModel.metadata.create_all(engine, tables=_PROJECT_TABLES)
    # I progetti nati prima non hanno le tabelle e le colonne aggiunte dopo: si allineano qui,
    # senza migrazioni da eseguire a mano.
    ensure_columns(engine, Document)
    ensure_columns(engine, QueuedBookRow)
    return engine


# --- helper BookSpec (target di parole) ------------------------------------
def get_bookspec(db_path: Path) -> BookSpec:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        spec = ses.get(BookSpec, 1)
        return spec or BookSpec(id=1)


def set_bookspec(
    db_path: Path,
    *,
    target_words: int | None = None,
    tolerance_pct: int | None = None,
    distribution: str | None = None,
) -> BookSpec:
    engine = make_engine(db_path)
    with Session(engine) as ses:
        spec = ses.get(BookSpec, 1)
        if spec is None:
            spec = BookSpec(id=1)
        if target_words is not None:
            spec.target_words = target_words
        if tolerance_pct is not None:
            spec.tolerance_pct = tolerance_pct
        if distribution is not None:
            spec.distribution = distribution
        spec.updated_at = _now_iso()
        ses.add(spec)
        ses.commit()
        ses.refresh(spec)
        return spec


# --- helper Job (checkpoint con progresso) ---------------------------------
def start_job(db_path: Path, kind: str, payload: dict | None = None) -> int:
    with Session(make_engine(db_path)) as ses:
        job = Job(kind=kind, state="running", progress=0.0, payload=payload or {})
        ses.add(job)
        ses.commit()
        ses.refresh(job)
        return job.id


def update_job(db_path: Path, job_id: int, progress: float) -> None:
    with Session(make_engine(db_path)) as ses:
        job = ses.get(Job, job_id)
        if job is not None:
            job.progress = progress
            job.updated_at = _now_iso()
            ses.add(job)
            ses.commit()


def finish_job(db_path: Path, job_id: int, state: str = "done") -> None:
    with Session(make_engine(db_path)) as ses:
        job = ses.get(Job, job_id)
        if job is not None:
            job.state = state
            job.progress = 1.0 if state == "done" else job.progress
            job.updated_at = _now_iso()
            ses.add(job)
            ses.commit()


def list_jobs(db_path: Path, limit: int = 20) -> list[dict]:
    with Session(make_engine(db_path)) as ses:
        rows = ses.exec(select(Job).order_by(Job.id.desc()).limit(limit)).all()
    return [
        {"id": r.id, "kind": r.kind, "state": r.state, "progress": r.progress, "updated_at": r.updated_at}
        for r in rows
    ]


# --- helper Document -------------------------------------------------------
def list_documents(db_path: Path) -> list[dict]:
    # Solo le colonne che servono: ``meta`` porta lo scheletro tipografico del libro, che per un
    # romanzo è mezzo megabyte, e questo elenco si ricostruisce a ogni cambio di schermata.
    with Session(make_engine(db_path)) as ses:
        rows = ses.exec(
            select(Document.id, Document.title, Document.format, Document.path, Document.transient)
            .order_by(Document.id)
        ).all()
    return [
        {"id": r[0], "title": r[1], "format": r[2], "path": r[3], "transient": bool(r[4])}
        for r in rows
    ]


def get_document_layout(db_path: Path, document_id: int):
    """Lo scheletro tipografico salvato all'importazione, o ``None`` se non c'è.

    Manca per i documenti importati prima che il programma sapesse conservare la struttura: in
    quel caso chi chiama ricade sulla resa in Markdown, com'era prima.
    """
    from bookwriter.layout import DocumentLayout

    with Session(make_engine(db_path)) as ses:
        row = ses.get(Document, document_id)
        dati = (row.meta or {}).get("layout") if row else None
    return DocumentLayout.from_dict(dati) if dati else None


def purge_transient_documents(db_path: Path) -> int:
    """Cancella i documenti di lavoro e tutto ciò che ne discende. Ritorna quanti ne ha tolti.

    Va chiamata all'apertura del progetto: è lì che «l'importazione non è persistita» diventa
    vero. Sparisce anche la riscrittura in corso su quei documenti, perché senza il testo di
    partenza non sarebbe comunque riprendibile.

    Fanno eccezione i libri **ancora in coda**: un lavoro messo in fila e non finito è una cosa
    che l'utente ha chiesto e aspetta. Sparirà alla riapertura successiva a quella in cui viene
    consegnato o tolto dalla coda.
    """
    if not db_path.exists():
        return 0
    engine = make_engine(db_path)
    ensure_columns(engine, Document)
    ensure_columns(engine, QueuedBookRow)
    with Session(engine) as ses:
        in_coda = {
            r for r in ses.exec(
                select(QueuedBookRow.document_id).where(QueuedBookRow.state != "fatto")
            ).all()
        }
        ids = [
            i for i in ses.exec(select(Document.id).where(Document.transient == True)).all()  # noqa: E712
            if i not in in_coda
        ]
        if not ids:
            return 0
        run_ids = list(
            ses.exec(select(RewriteRun.id).where(RewriteRun.document_id.in_(ids))).all()
        )
        if run_ids:
            for rc in ses.exec(select(RewriteChunk).where(RewriteChunk.run_id.in_(run_ids))).all():
                ses.delete(rc)
            for run in ses.exec(select(RewriteRun).where(RewriteRun.id.in_(run_ids))).all():
                ses.delete(run)
        for node in ses.exec(select(Node).where(Node.document_id.in_(ids))).all():
            ses.delete(node)
        for chunk in ses.exec(select(Chunk).where(Chunk.document_id.in_(ids))).all():
            ses.delete(chunk)
        # Voci di coda già consegnate su documenti che se ne vanno: senza il testo di partenza
        # non vogliono dire più niente, e resterebbero in elenco come lavori impossibili.
        for voce in ses.exec(select(QueuedBookRow).where(QueuedBookRow.document_id.in_(ids))).all():
            ses.delete(voce)
        for doc in ses.exec(select(Document).where(Document.id.in_(ids))).all():
            ses.delete(doc)
        ses.commit()
    return len(ids)


# --- helper StyleProfile ---------------------------------------------------
def save_style(db_path: Path, name: str, origin: str, data: dict) -> int:
    with Session(make_engine(db_path)) as ses:
        row = StyleProfileRow(name=name, origin=origin, data=data)
        ses.add(row)
        ses.commit()
        ses.refresh(row)
        return row.id


def list_styles(db_path: Path) -> list[dict]:
    with Session(make_engine(db_path)) as ses:
        rows = ses.exec(select(StyleProfileRow).order_by(StyleProfileRow.id)).all()
    return [{"id": r.id, "name": r.name, "origin": r.origin} for r in rows]


def get_style_row(db_path: Path, ref: int | str) -> dict | None:
    with Session(make_engine(db_path)) as ses:
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            row = ses.get(StyleProfileRow, int(ref))
        else:
            row = ses.exec(
                select(StyleProfileRow).where(StyleProfileRow.name == ref).order_by(StyleProfileRow.id.desc())
            ).first()
        if row is None:
            return None
        return {"id": row.id, "name": row.name, "origin": row.origin, "data": row.data}


# --- helper consumo token ---------------------------------------------------
def ensure_columns(engine: Engine, model: type[SQLModel]) -> None:
    """Crea la tabella e le colonne mancanti: i progetti nati prima non vanno ricreati."""
    table = model.__table__  # type: ignore[attr-defined]
    table.create(engine, checkfirst=True)
    with engine.connect() as conn:
        present = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
        for column in table.columns:
            if column.name not in present:
                sql_type = column.type.compile(engine.dialect)
                conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {sql_type}")
        conn.commit()


def _ensure_usage_table(engine: Engine) -> None:
    ensure_columns(engine, TokenUsage)


def save_usage(
    db_path: Path,
    *,
    row_id: int | None,
    activity: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    calls: int,
    price_in: float = 0.0,
    price_out: float = 0.0,
) -> int | None:
    """Salva i token di un'esecuzione: crea la riga la prima volta, poi la aggiorna.

    Aggiornare la stessa riga permette di seguire i consumi mentre il lavoro è in corso
    senza moltiplicare le righe a ogni rilevazione.
    """
    if not (prompt_tokens or completion_tokens or calls):
        return row_id
    engine = make_engine(db_path)
    _ensure_usage_table(engine)
    with Session(engine) as ses:
        row = ses.get(TokenUsage, row_id) if row_id else None
        if row is None:
            row = TokenUsage(activity=activity, model=model)
        row.calls = calls
        row.prompt_tokens = prompt_tokens
        row.completion_tokens = completion_tokens
        row.total_tokens = prompt_tokens + completion_tokens
        row.price_in = price_in
        row.price_out = price_out
        row.cost = (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000
        ses.add(row)
        ses.commit()
        ses.refresh(row)
        return row.id


def usage_by_activity(db_path: Path) -> list[dict]:
    """Consumi aggregati per attività, dal più recente al più vecchio per ultima esecuzione."""
    if not db_path.exists():
        return []
    engine = make_engine(db_path)
    _ensure_usage_table(engine)
    with Session(engine) as ses:
        rows = ses.exec(select(TokenUsage)).all()
    grouped: dict[str, dict] = {}
    for row in rows:
        agg = grouped.setdefault(
            row.activity,
            {"activity": row.activity, "runs": 0, "calls": 0, "prompt_tokens": 0,
             "completion_tokens": 0, "total_tokens": 0, "cost": 0.0, "models": [], "last": ""},
        )
        agg["runs"] += 1
        agg["calls"] += row.calls
        agg["prompt_tokens"] += row.prompt_tokens
        agg["completion_tokens"] += row.completion_tokens
        agg["total_tokens"] += row.total_tokens
        agg["cost"] += row.cost or 0.0
        if row.model and row.model not in agg["models"]:
            agg["models"].append(row.model)
        agg["last"] = max(agg["last"], row.created_at)
    return sorted(grouped.values(), key=lambda a: a["last"], reverse=True)
