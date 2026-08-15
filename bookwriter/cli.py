"""Interfaccia a riga di comando (Fase 0): ``doctor`` e gestione ``project``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from bookwriter.atomize import atomize_chunks
from bookwriter.authoring import expand_topic, start_authoring
from bookwriter.coherence import coherence_pass_compose, coherence_pass_rewrite
from bookwriter.compose import compose_outline, compose_to_markdown
from bookwriter.config import load_global_config
from bookwriter.db import project_schema
from bookwriter.exporter import export_outline, export_wiki, latest_outline_id, write_output
from bookwriter.ingest import pipeline
from bookwriter.interact import (
    build_brief,
    format_brief,
    integrate_answer,
    list_intents,
    propose_questions,
)
from bookwriter.llm import LLMError, LMStudioClient
from bookwriter.memory import (
    LanceStore,
    build_graph,
    embed_chunks,
    embed_nodes,
    graph_stats,
    search_chunks,
    search_nodes,
)
from bookwriter.organize import (
    list_outlines,
    organize_chronological,
    organize_custom,
    organize_narrative,
    organize_thematic,
    outline_tree,
    subdivide_sections,
)
from bookwriter.projects import ProjectError, ProjectManager, ProjectPaths
from bookwriter.rewrite import preview_pairs, rewrite_document, write_result
from bookwriter.style import (
    StyleProfile,
    abstract_style,
    apply_style,
    authors_style,
    build_style_contract,
    extract_style,
)
from bookwriter.style.library import get_global_style, list_global_styles, save_global_style
from bookwriter.style.models import AuthorWeight

app = typer.Typer(no_args_is_help=True, add_completion=False, help="bookwriter — frammenti/libri → libro/archivio con LLM locale.")
project_app = typer.Typer(no_args_is_help=True, help="Gestione dei progetti.")
memory_app = typer.Typer(no_args_is_help=True, help="Ispezione della memoria (documenti/chunk).")
session_app = typer.Typer(no_args_is_help=True, help="Sessioni e continuità (brief, intenti).")
style_app = typer.Typer(no_args_is_help=True, help="Profili di stile (estrai, definisci, autori, applica).")
author_app = typer.Typer(no_args_is_help=True, help="Authoring interattivo: dal tema alla bozza.")
outline_app = typer.Typer(no_args_is_help=True, help="Ispezione degli outline (struttura).")
app.add_typer(project_app, name="project")
app.add_typer(memory_app, name="memory")
app.add_typer(session_app, name="session")
app.add_typer(style_app, name="style")
app.add_typer(author_app, name="author")
app.add_typer(outline_app, name="outline")

console = Console()
err = Console(stderr=True)


def _active_or_exit(pm: ProjectManager) -> tuple[str, ProjectPaths]:
    slug = pm.active_slug()
    if not slug:
        err.print("[red]Nessun progetto attivo. Crea o seleziona un progetto.[/red]")
        raise typer.Exit(code=1)
    return slug, pm.paths(slug)


def _job_progress(prog, task, db_path: Path, job_id: int):
    """Callback di avanzamento che aggiorna la barra e persiste il progresso della Job (ogni 5%)."""
    state = {"pct": -1}

    def cb(done: int, total: int) -> None:
        prog.update(task, total=max(total, 1), completed=done)
        pct = int(100 * done / max(total, 1))
        if pct != state["pct"] and pct % 5 == 0:
            project_schema.update_job(db_path, job_id, done / max(total, 1))
            state["pct"] = pct

    return cb


# --------------------------------------------------------------------------- #
#  doctor
# --------------------------------------------------------------------------- #
@app.command()
def doctor(probe: bool = typer.Option(True, help="Esegue un mini test di chat ed embedding.")) -> None:
    """Verifica LM Studio: modelli caricati, ruoli configurati, test funzionale."""
    cfg = load_global_config()
    console.print(f"[bold]LM Studio[/bold]: {cfg.llm.base_url}")
    client = LMStudioClient(cfg.llm, cfg.embedding)
    try:
        models = client.list_models()
    except LLMError as exc:
        err.print(f"[red]Impossibile contattare LM Studio:[/red] {exc}")
        raise typer.Exit(code=1)

    table = Table(title="Modelli")
    for col in ("stato", "tipo", "ctx", "id"):
        table.add_column(col)
    for m in models:
        state = "[green]loaded[/green]" if m.loaded else "[dim]not-loaded[/dim]"
        ctx = str(m.max_context_length or "?")
        table.add_row(state, m.type, ctx, m.id)
    console.print(table)

    ids = {m.id for m in models}
    loaded = {m.id for m in models if m.loaded}
    _check_role("reasoning", cfg.llm.models.reasoning, ids, loaded)
    _check_role("embedding", cfg.llm.models.embedding, ids, loaded)
    if cfg.llm.models.heavy:
        _check_role("heavy", cfg.llm.models.heavy, ids, loaded)

    if probe:
        _probe(client, cfg.llm.models.reasoning, cfg.llm.models.embedding)


def _check_role(role: str, model: str, ids: set[str], loaded: set[str]) -> None:
    if model not in ids:
        err.print(f"[yellow]! ruolo {role}[/yellow]: '{model}' non presente in LM Studio.")
    elif model not in loaded:
        console.print(f"[yellow]•[/yellow] ruolo {role}: '{model}' presente ma non caricato (verrà caricato al primo uso).")
    else:
        console.print(f"[green]✓[/green] ruolo {role}: '{model}' caricato.")


def _probe(client: LMStudioClient, chat_model: str, embed_model: str) -> None:
    console.print("\n[bold]Test funzionale[/bold]")
    try:
        reply = client.chat(
            [{"role": "user", "content": "Rispondi con una sola parola: pronto"}],
            model=chat_model,
        )
        console.print(f"[green]✓[/green] chat: {reply.strip()[:60]!r}")
    except LLMError as exc:
        err.print(f"[red]✗ chat:[/red] {exc}")
    try:
        vec = client.embed(["prova"], model=embed_model)
        dim = len(vec[0]) if vec else 0
        console.print(f"[green]✓[/green] embedding: dim={dim}")
    except LLMError as exc:
        err.print(f"[red]✗ embedding:[/red] {exc}")


# --------------------------------------------------------------------------- #
#  project
# --------------------------------------------------------------------------- #
def _pm() -> ProjectManager:
    pm = ProjectManager()
    pm.ensure()
    return pm


@project_app.command("new")
def project_new(
    name: str = typer.Argument(..., help="Nome del progetto."),
    target_words: int = typer.Option(None, "--target-words", help="Target di parole del libro (BookSpec)."),
) -> None:
    """Crea un nuovo progetto e lo rende attivo."""
    pm = _pm()
    project = pm.create(name, target_words=target_words)
    console.print(f"[green]✓[/green] creato e attivato: [bold]{project.name}[/bold] (slug: {project.slug})")
    console.print(f"  cartella: {pm.paths(project.slug).root}")
    if target_words:
        console.print(f"  target parole: {target_words:,}".replace(",", "."))


@project_app.command("list")
def project_list() -> None:
    """Elenca i progetti."""
    pm = _pm()
    projects = pm.list()
    if not projects:
        console.print("[dim]Nessun progetto. Creane uno con: bookwriter project new \"<nome>\"[/dim]")
        return
    active = pm.active_slug()
    table = Table(title="Progetti")
    for col in ("", "slug", "nome", "creato"):
        table.add_column(col)
    for p in projects:
        mark = "[green]●[/green]" if p.slug == active else " "
        table.add_row(mark, p.slug, p.name, p.created_at)
    console.print(table)


@project_app.command("switch")
def project_switch(slug: str = typer.Argument(..., help="Slug del progetto.")) -> None:
    """Rende attivo un progetto."""
    pm = _pm()
    try:
        pm.set_active(slug)
    except ProjectError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] progetto attivo: {slug}")


@project_app.command("info")
def project_info(slug: str = typer.Argument(None, help="Slug (default: progetto attivo).")) -> None:
    """Mostra i dettagli di un progetto."""
    pm = _pm()
    slug = slug or pm.active_slug()
    if not slug:
        err.print("[red]Nessun progetto attivo. Specifica uno slug o creane uno.[/red]")
        raise typer.Exit(code=1)
    try:
        data = pm.info(slug)
    except ProjectError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    for key, val in data.items():
        console.print(f"  [bold]{key}[/bold]: {val}")


@project_app.command("delete")
def project_delete(
    slug: str = typer.Argument(..., help="Slug del progetto."),
    keep_files: bool = typer.Option(False, "--keep-files", help="Non cancellare i file su disco."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non chiedere conferma."),
) -> None:
    """Elimina un progetto (e i suoi file, salvo --keep-files)."""
    pm = _pm()
    if pm.get(slug) is None:
        err.print(f"[red]Progetto '{slug}' inesistente.[/red]")
        raise typer.Exit(code=1)
    if not yes:
        confirm = typer.confirm(f"Eliminare il progetto '{slug}'{'' if keep_files else ' e i suoi file'}?")
        if not confirm:
            console.print("Annullato.")
            raise typer.Exit(code=0)
    pm.delete(slug, purge_files=not keep_files)
    console.print(f"[green]✓[/green] eliminato: {slug}")


# --------------------------------------------------------------------------- #
#  ingest
# --------------------------------------------------------------------------- #
@app.command()
def ingest(
    paths: list[Path] = typer.Argument(..., help="File e/o directory da importare."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Scansiona le directory ricorsivamente."),
    include: str = typer.Option(None, "--include", help="Glob dei nomi da includere, separati da virgola."),
    exclude: str = typer.Option(None, "--exclude", help="Glob dei nomi da escludere, separati da virgola."),
) -> None:
    """Importa file o intere directory nella memoria del progetto attivo (dedup + chunking)."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(_slug)
    inc = [s.strip() for s in include.split(",")] if include else None
    exc = [s.strip() for s in exclude.split(",")] if exclude else None

    from rich.progress import BarColumn, Progress, TextColumn

    with Progress(TextColumn("[progress.description]{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), console=console) as prog:
        task = prog.add_task("ingest", total=1)

        def on_progress(done: int, total: int, name: str) -> None:
            prog.update(task, total=max(total, 1), completed=done, description=f"ingest: {name[:40]}")

        report = pipeline.ingest_paths(
            paths,
            db_path=ppaths.db,
            target_tokens=cfg.chunking.target_tokens,
            recursive=recursive,
            include=inc,
            exclude=exc,
            progress=on_progress,
        )
    console.print(f"[green]✓[/green] {report.summary()}")
    for path, msg in report.failed:
        err.print(f"  [red]falliti[/red] {path}: {msg}")


# --------------------------------------------------------------------------- #
#  memory
# --------------------------------------------------------------------------- #
@memory_app.command("stats")
def memory_stats_cmd() -> None:
    """Mostra i conteggi della memoria del progetto attivo."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    stats = pipeline.memory_stats(ppaths.db)
    console.print(f"[bold]Memoria[/bold] ({_slug})")
    console.print(f"  documenti: {stats['documents']}")
    console.print(f"  chunk: {stats['chunks']}")
    console.print(f"  token stimati: {stats['total_tokens_est']:,}".replace(",", "."))
    if stats["by_format"]:
        fmts = ", ".join(f"{k}:{v}" for k, v in stats["by_format"].items())
        console.print(f"  per formato: {fmts}")
    embedded = LanceStore(ppaths.vectors).count("chunk") if ppaths.vectors.exists() else 0
    pending = max(stats["chunks"] - embedded, 0)
    console.print(f"  chunk embeddati: {embedded}" + (f" ([yellow]{pending} da embeddare[/yellow])" if pending else ""))
    gstats = graph_stats(ppaths.db)
    console.print(f"  nodi: {gstats['nodes']} · archi: {gstats['edges']}")


@memory_app.command("embed")
def memory_embed_cmd(batch_size: int = typer.Option(32, "--batch-size", help="Dimensione batch embedding.")) -> None:
    """Calcola gli embedding dei chunk non ancora indicizzati (idempotente)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)

    from rich.progress import BarColumn, Progress, TextColumn

    job_id = project_schema.start_job(ppaths.db, "embed")
    with Progress(TextColumn("[progress.description]{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), console=console) as prog:
        task = prog.add_task("embedding", total=1)
        on_progress = _job_progress(prog, task, ppaths.db, job_id)
        try:
            report = embed_chunks(
                ppaths.db, ppaths.vectors, client,
                batch_size=batch_size, concurrency=cfg.llm.concurrency, progress=on_progress,
            )
        except LLMError as exc:
            project_schema.finish_job(ppaths.db, job_id, "failed")
            err.print(f"[red]Errore embedding:[/red] {exc}")
            raise typer.Exit(code=1)
    project_schema.finish_job(ppaths.db, job_id)
    console.print(f"[green]✓[/green] {report.summary()}")


@memory_app.command("graph")
def memory_graph_cmd(
    k: int = typer.Option(8, "-k", help="Vicini per nodo."),
    min_weight: float = typer.Option(0.15, "--min-weight", help="Peso minimo per creare un arco."),
) -> None:
    """Embedda i nodi e costruisce il grafo tematico top-k (idempotente)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    try:
        emb = embed_nodes(ppaths.db, ppaths.vectors, client, concurrency=cfg.llm.concurrency)
    except LLMError as exc:
        err.print(f"[red]Errore embedding nodi:[/red] {exc}")
        raise typer.Exit(code=1)
    report = build_graph(ppaths.db, ppaths.vectors, k=k, min_weight=min_weight)
    console.print(f"[green]✓[/green] nodi embeddati (+{emb.embedded}); grafo: {report.summary()}")


@memory_app.command("search")
def memory_search_cmd(
    query: str = typer.Argument(..., help="Testo della ricerca semantica."),
    k: int = typer.Option(6, "-k", help="Numero di risultati."),
    nodes: bool = typer.Option(False, "--nodes", help="Cerca nei nodi invece che nei chunk."),
) -> None:
    """Ricerca semantica sui chunk (o sui nodi con --nodes) del progetto attivo."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    try:
        if nodes:
            hits = search_nodes(ppaths.db, ppaths.vectors, client, query, k=k)
        else:
            hits = search_chunks(ppaths.db, ppaths.vectors, client, query, k=k)
    except LLMError as exc:
        err.print(f"[red]Errore ricerca:[/red] {exc}")
        raise typer.Exit(code=1)
    if not hits:
        console.print("[dim]Nessun risultato. Hai eseguito embedding/atomize+grafo?[/dim]")
        return
    for i, h in enumerate(hits, 1):
        if nodes:
            dist = f"{h['distance']:.3f}" if h["distance"] is not None else "?"
            console.print(f"[bold]{i}.[/bold] [cyan]dist={dist}[/cyan] [dim]{', '.join(h['themes'] or [])}[/dim]")
            console.print(f"   {h['summary']}")
        else:
            dist = f"{h.distance:.3f}" if h.distance is not None else "?"
            snippet = " ".join(h.text.split())[:140]
            console.print(f"[bold]{i}.[/bold] [cyan]dist={dist}[/cyan] [dim]{h.document_title or ''} · {h.chapter_ref or ''}[/dim]")
            console.print(f"   {snippet}…")


# --------------------------------------------------------------------------- #
#  spec (target di parole)
# --------------------------------------------------------------------------- #
@app.command()
def spec(
    target_words: int = typer.Option(None, "--target-words", help="Target di parole del libro."),
    tolerance: int = typer.Option(None, "--tolerance", help="Tolleranza percentuale (default 5)."),
    distribution: str = typer.Option(None, "--distribution", help="proportional|equal|manual."),
) -> None:
    """Imposta o mostra la specifica del libro (BookSpec) del progetto attivo."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    if target_words is None and tolerance is None and distribution is None:
        current = project_schema.get_bookspec(ppaths.db)
        console.print(f"[bold]BookSpec[/bold] ({_slug})")
        console.print(f"  target parole: {current.target_words}")
        console.print(f"  tolleranza %: {current.tolerance_pct}")
        console.print(f"  distribuzione: {current.distribution}")
        return
    updated = project_schema.set_bookspec(
        ppaths.db, target_words=target_words, tolerance_pct=tolerance, distribution=distribution
    )
    console.print(f"[green]✓[/green] BookSpec aggiornato: target={updated.target_words}, tol={updated.tolerance_pct}%, dist={updated.distribution}")


# --------------------------------------------------------------------------- #
#  atomize (chunk -> nodi)
# --------------------------------------------------------------------------- #
@app.command()
def atomize() -> None:
    """Scompone i chunk del progetto attivo in nodi-idea con metadati (via LLM)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)

    from rich.progress import BarColumn, Progress, TextColumn

    job_id = project_schema.start_job(ppaths.db, "atomize")
    with Progress(TextColumn("[progress.description]{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), console=console) as prog:
        task = prog.add_task("atomize", total=1)
        on_progress = _job_progress(prog, task, ppaths.db, job_id)
        try:
            report = atomize_chunks(
                ppaths.db, client, model=cfg.llm.models.reasoning,
                concurrency=cfg.llm.concurrency, progress=on_progress,
            )
        except LLMError as exc:
            project_schema.finish_job(ppaths.db, job_id, "failed")
            err.print(f"[red]Errore atomizzazione:[/red] {exc}")
            raise typer.Exit(code=1)
    project_schema.finish_job(ppaths.db, job_id)
    console.print(f"[green]✓[/green] {report.summary()} · token: {client.usage['total_tokens']:,}".replace(",", "."))


# --------------------------------------------------------------------------- #
#  organize (nodi -> outline tematica)
# --------------------------------------------------------------------------- #
@app.command()
def organize(
    ordering: str = typer.Option("thematic", "--ordering", help="thematic|chronological|narrative|custom."),
    name: str = typer.Option(None, "--name", help="Nome dell'outline (default: dall'ordinamento)."),
    method: str = typer.Option("embedding", "--method", help="embedding|community (solo tematico)."),
    chapters: int = typer.Option(None, "--chapters", help="Numero di capitoli (default: automatico)."),
    criterion: str = typer.Option(None, "--criterion", help="Criterio in linguaggio naturale (solo --ordering custom)."),
    llm_titles: bool = typer.Option(True, "--llm-titles/--no-llm-titles", help="Titoli capitolo dall'LLM (tematico)."),
) -> None:
    """Raggruppa/ordina i nodi in capitoli secondo l'ordinamento scelto, creando un outline."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    model = cfg.llm.models.reasoning
    default_names = {
        "thematic": "Outline tematico", "chronological": "Outline cronologico",
        "narrative": "Outline narrativo", "custom": "Outline personalizzato",
    }
    name = name or default_names.get(ordering, "Outline")
    try:
        if ordering == "thematic":
            client = LMStudioClient(cfg.llm, cfg.embedding) if llm_titles else None
            report = organize_thematic(ppaths.db, ppaths.vectors, method=method, chapters=chapters, name=name, client=client, model=model)
        elif ordering == "chronological":
            report = organize_chronological(ppaths.db, name=name, chapters=chapters)
        elif ordering == "narrative":
            report = organize_narrative(ppaths.db, ppaths.vectors, LMStudioClient(cfg.llm, cfg.embedding), name=name, chapters=chapters, model=model)
        elif ordering == "custom":
            if not criterion:
                err.print("[red]--ordering custom richiede --criterion \"...\".[/red]")
                raise typer.Exit(code=1)
            report = organize_custom(ppaths.db, LMStudioClient(cfg.llm, cfg.embedding), criterion, name=name, model=model)
        else:
            err.print(f"[red]Ordinamento sconosciuto: {ordering}[/red]")
            raise typer.Exit(code=1)
    except (ValueError, LLMError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] {report.summary()} (ordinamento: {ordering})")
    console.print("  esporta con: [bold]bookwriter export --format docx[/bold] o genera prosa con [bold]compose[/bold]")


# --------------------------------------------------------------------------- #
#  export
# --------------------------------------------------------------------------- #
@app.command()
def export(
    fmt: str = typer.Option("docx", "--format", "-f", help="md|docx|pdf|wiki."),
    outline: int = typer.Option(None, "--outline", help="Id outline (default: l'ultimo)."),
    out: Path = typer.Option(None, "--out", help="Percorso di output (default: cartella exports del progetto)."),
) -> None:
    """Esporta l'outline (verbatim) in md/docx/pdf, oppure l'archivio wiki (nodi+grafo)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    if fmt == "wiki":
        result = export_wiki(ppaths.db, out or (ppaths.exports / "wiki"))
        console.print(f"[green]✓[/green] archivio: {result['html']}")
        console.print(f"  dati grafo: {result['json']}")
        return
    outline_id = outline or latest_outline_id(ppaths.db)
    if not outline_id:
        err.print("[red]Nessun outline. Esegui prima `bookwriter organize`.[/red]")
        raise typer.Exit(code=1)
    out_path = out or (ppaths.exports / f"{slug}.{fmt}")
    try:
        written = export_outline(ppaths.db, outline_id, out_path, fmt=fmt)
    except (ValueError, RuntimeError) as exc:
        err.print(f"[red]Errore export:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] esportato: {written}")


# --------------------------------------------------------------------------- #
#  session (continuità / intenti)
# --------------------------------------------------------------------------- #
@session_app.command("brief")
def session_brief_cmd() -> None:
    """Mostra il brief di ripresa del progetto attivo (per continuare in una nuova sessione)."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    console.print(format_brief(build_brief(ppaths.db)))


@session_app.command("intents")
def session_intents_cmd(
    status: str = typer.Option(None, "--status", help="Filtra per stato: open|resolved|dropped."),
) -> None:
    """Elenca gli intenti/decisioni persistiti."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    intents = list_intents(ppaths.db, status=status)
    if not intents:
        console.print("[dim]Nessun intento registrato.[/dim]")
        return
    for it in intents:
        console.print(f"  #{it['id']} [{it['status']}] [bold]{it['kind']}[/bold]: {it['value']}")


# --------------------------------------------------------------------------- #
#  ask / answer (interazione: completare i contenuti)
# --------------------------------------------------------------------------- #
@app.command()
def ask(n: int = typer.Option(3, "-n", help="Numero di domande.")) -> None:
    """L'LLM analizza le lacune e propone domande per completare i contenuti (persistite)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    try:
        questions = propose_questions(ppaths.db, client, model=cfg.llm.models.reasoning, n=n)
    except LLMError as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    if not questions:
        console.print("[dim]Nessuna domanda generata.[/dim]")
        return
    console.print("[bold]Domande per completare i contenuti:[/bold]")
    for q in questions:
        console.print(f"  #{q['id']} {q['text']}")
    console.print("  rispondi con: [bold]bookwriter answer <id> \"<testo>\"[/bold]")


@app.command()
def answer(
    question_id: int = typer.Argument(..., help="Id della domanda (da `ask`)."),
    text: str = typer.Argument(..., help="La risposta/contenuto da aggiungere."),
) -> None:
    """Integra la risposta come nuovo nodo in memoria (source=dialogue) e la embedda."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    try:
        node_id = integrate_answer(ppaths.db, ppaths.vectors, client, question_id, text)
    except LLMError as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] nuovo nodo #{node_id} aggiunto dalla risposta (source=dialogue).")


# --------------------------------------------------------------------------- #
#  style
# --------------------------------------------------------------------------- #
def _save_and_report(ppaths: ProjectPaths, profile: StyleProfile, use_global: bool = False) -> None:
    if use_global:
        path = save_global_style(profile)
        console.print(f"[green]✓[/green] stile globale '{profile.name}' ({profile.origin}) salvato: {path}")
    else:
        sid = project_schema.save_style(ppaths.db, profile.name, profile.origin, profile.model_dump())
        console.print(f"[green]✓[/green] profilo di stile #{sid} '{profile.name}' ({profile.origin}) salvato.")


def _load_style(ppaths: ProjectPaths, ref: str) -> StyleProfile:
    row = project_schema.get_style_row(ppaths.db, ref)
    if row is not None:
        return StyleProfile.model_validate(row["data"])
    glob = get_global_style(ref)  # fallback: libreria globale
    if glob is not None:
        return glob
    err.print(f"[red]Stile '{ref}' inesistente (né nel progetto né nella libreria globale).[/red]")
    raise typer.Exit(code=1)


@style_app.command("extract")
def style_extract(
    path: Path = typer.Argument(..., help="File di testo campione."),
    name: str = typer.Option(..., "--name", help="Nome del profilo."),
    use_global: bool = typer.Option(False, "--global", help="Salva nella libreria globale."),
) -> None:
    """Estrae un profilo di stile da un testo campione."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    text = path.read_text(encoding="utf-8", errors="replace")
    profile = extract_style(text, client, name=name, model=cfg.llm.models.reasoning)
    _save_and_report(ppaths, profile, use_global)


@style_app.command("abstract")
def style_abstract(
    description: str = typer.Argument(..., help="Descrizione dello stile desiderato."),
    name: str = typer.Option(..., "--name", help="Nome del profilo."),
    use_global: bool = typer.Option(False, "--global", help="Salva nella libreria globale."),
) -> None:
    """Definisce un profilo di stile da una descrizione in linguaggio naturale."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    profile = abstract_style(description, client, name=name, model=cfg.llm.models.reasoning)
    _save_and_report(ppaths, profile, use_global)


@style_app.command("authors")
def style_authors(
    spec: list[str] = typer.Option(..., "--spec", help="Autore:peso:dim1,dim2 (ripetibile)."),
    name: str = typer.Option(..., "--name", help="Nome del profilo."),
    use_global: bool = typer.Option(False, "--global", help="Salva nella libreria globale."),
) -> None:
    """Crea un profilo imitando uno o più autori con pesi e ambiti (dimensioni)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    specs: list[AuthorWeight] = []
    for s in spec:
        parts = s.split(":")
        author = parts[0].strip()
        weight = float(parts[1]) if len(parts) > 1 and parts[1] else 1.0
        dims = [d.strip() for d in parts[2].split(",")] if len(parts) > 2 and parts[2] else []
        specs.append(AuthorWeight(author=author, weight=weight, dims=dims))
    profile = authors_style(specs, client, name=name, model=cfg.llm.models.reasoning)
    _save_and_report(ppaths, profile, use_global)


@style_app.command("list")
def style_list(
    use_global: bool = typer.Option(False, "--global", help="Elenca la libreria globale."),
) -> None:
    """Elenca i profili di stile del progetto (o della libreria globale con --global)."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    if use_global:
        rows = list_global_styles()
        if not rows:
            console.print("[dim]Libreria globale vuota.[/dim]")
            return
        for r in rows:
            console.print(f"  [bold]{r['name']}[/bold] ({r['origin']}) · slug: {r['slug']}")
        return
    rows = project_schema.list_styles(ppaths.db)
    if not rows:
        console.print("[dim]Nessun profilo di stile.[/dim]")
        return
    for r in rows:
        console.print(f"  #{r['id']} [bold]{r['name']}[/bold] ({r['origin']})")


@style_app.command("import")
def style_import(name: str = typer.Argument(..., help="Nome o slug dello stile globale.")) -> None:
    """Importa uno stile dalla libreria globale nel progetto attivo."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    profile = get_global_style(name)
    if profile is None:
        err.print(f"[red]Stile globale '{name}' inesistente.[/red]")
        raise typer.Exit(code=1)
    sid = project_schema.save_style(ppaths.db, profile.name, profile.origin, profile.model_dump())
    console.print(f"[green]✓[/green] importato nel progetto come #{sid}: '{profile.name}'.")


@style_app.command("show")
def style_show(ref: str = typer.Argument(..., help="Id o nome del profilo.")) -> None:
    """Mostra le dimensioni di un profilo di stile."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    profile = _load_style(ppaths, ref)
    console.print(f"[bold]{profile.name}[/bold] ({profile.origin})")
    for dim, desc in profile.dimensions.items():
        console.print(f"  [cyan]{dim}[/cyan]: {desc}")
    if profile.constraints:
        console.print(f"  [yellow]vincoli[/yellow]: {'; '.join(profile.constraints)}")


@style_app.command("apply")
def style_apply(
    ref: str = typer.Argument(..., help="Id o nome del profilo."),
    text: str = typer.Option(None, "--text", help="Testo da riscrivere."),
    file: Path = typer.Option(None, "--file", help="File di testo da riscrivere."),
) -> None:
    """Riscrive un testo adottando lo stile del profilo (con critic loop)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    if not text and not file:
        err.print("[red]Fornisci --text o --file.[/red]")
        raise typer.Exit(code=1)
    source = text or file.read_text(encoding="utf-8", errors="replace")
    profile = _load_style(ppaths, ref)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    out = apply_style(source, profile, client, model=cfg.llm.models.reasoning)
    console.print(out)


# --------------------------------------------------------------------------- #
#  compose (Compose narrativo: prosa dai nodi seguendo l'outline)
# --------------------------------------------------------------------------- #
@app.command()
def compose(
    style: str = typer.Option(None, "--style", help="Profilo di stile (opzionale; default: prosa chiara)."),
    outline: int = typer.Option(None, "--outline", help="Id outline (default: l'ultimo)."),
    target_words: int = typer.Option(None, "--target-words", help="Override del target (default: BookSpec)."),
    reconcile: bool = typer.Option(True, "--reconcile/--no-reconcile", help="Avvicina i capitoli al budget."),
    coherence: bool = typer.Option(False, "--coherence", help="Passaggio globale di coerenza dopo la stesura."),
    fmt: str = typer.Option("docx", "--format", "-f", help="md|docx|pdf."),
    fresh: bool = typer.Option(False, "--fresh", help="Rigenera da capo."),
) -> None:
    """Scrive il libro in prosa (stesura narrativa) dai nodi dell'outline, nello stile e nel target parole."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    outline_id = outline or latest_outline_id(ppaths.db)
    if not outline_id:
        err.print("[red]Nessun outline. Esegui prima `bookwriter organize`.[/red]")
        raise typer.Exit(code=1)
    if style:
        contract = build_style_contract(_load_style(ppaths, style))
    else:
        contract = "Scrivi in prosa chiara, scorrevole e coerente, con buone transizioni."
    spec = project_schema.get_bookspec(ppaths.db)
    target = target_words or spec.target_words
    client = LMStudioClient(cfg.llm, cfg.embedding)

    from rich.progress import BarColumn, Progress, TextColumn

    with Progress(TextColumn("[progress.description]{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), console=console) as prog:
        task = prog.add_task("compose", total=1)

        def on_progress(done: int, total: int) -> None:
            prog.update(task, total=max(total, 1), completed=done)

        try:
            report = compose_outline(
                ppaths.db, outline_id, contract, client,
                target_words=target, tolerance_pct=spec.tolerance_pct, reconcile=reconcile,
                model=cfg.llm.models.reasoning, resume=not fresh, progress=on_progress,
            )
        except (ValueError, LLMError) as exc:
            err.print(f"[red]Errore stesura:[/red] {exc}")
            raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] {report.summary()}")
    if coherence:
        coh = coherence_pass_compose(ppaths.db, outline_id, client, model=cfg.llm.models.quality)
        console.print(f"[green]✓[/green] coerenza: {coh['replacements']} sostituzioni, {len(coh['mappings'])} varianti uniformate")
    out = write_output(compose_to_markdown(ppaths.db, outline_id), ppaths.exports / f"{slug}-book.{fmt}", fmt)
    console.print(f"[green]✓[/green] libro (prosa) esportato: {out}")


# --------------------------------------------------------------------------- #
#  rewrite (modalità B: riscrittura di un libro in un nuovo stile)
# --------------------------------------------------------------------------- #
def _resolve_document(ppaths: ProjectPaths, document: int | None) -> int:
    docs = project_schema.list_documents(ppaths.db)
    if not docs:
        err.print("[red]Nessun documento importato. Usa `bookwriter ingest`.[/red]")
        raise typer.Exit(code=1)
    if document is not None:
        return document
    if len(docs) > 1:
        console.print("[yellow]Più documenti presenti; uso il primo. Specifica --document per sceglierne un altro:[/yellow]")
        for d in docs:
            console.print(f"  #{d['id']} {d['title']} ({d['format']})")
    return docs[0]["id"]


def _run_book_transform(
    slug: str,
    ppaths: ProjectPaths,
    cfg,
    *,
    contract: str,
    document: int | None,
    preview: bool,
    ratio: float | None,
    fmt: str,
    fresh: bool,
    mode: str,
    target_language: str | None,
    out_stem: str,
) -> None:
    """Logica condivisa da `rewrite` (B) e `translate` (C)."""
    document_id = _resolve_document(ppaths, document)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    kind = "preview" if preview else "full"

    from rich.progress import BarColumn, Progress, TextColumn

    with Progress(TextColumn("[progress.description]{task.description}"), BarColumn(), TextColumn("{task.completed}/{task.total}"), console=console) as prog:
        task = prog.add_task(mode, total=1)

        def on_progress(done: int, total: int) -> None:
            prog.update(task, total=max(total, 1), completed=done)

        try:
            report = rewrite_document(
                ppaths.db, document_id, contract, client,
                ratio=ratio, kind=kind, mode=mode, target_language=target_language,
                model=cfg.llm.models.reasoning, critic_model=cfg.llm.models.quality,
                resume=not fresh, progress=on_progress,
            )
        except (ValueError, LLMError) as exc:
            err.print(f"[red]Errore:[/red] {exc}")
            raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] {report.summary()}")
    if preview:
        console.print("\n[bold]Preview primo capitolo (originale → risultato):[/bold]")
        for i, pair in enumerate(preview_pairs(ppaths.db, report.run_id), 1):
            console.print(f"\n[dim]— brano {i} ({pair['chapter'] or ''}) —[/dim]")
            console.print(f"[dim]ORIG:[/dim] {' '.join(pair['original'].split())[:300]}")
            console.print(f"[bold]NUOVO:[/bold] {' '.join(pair['rewritten'].split())[:300]}")
        console.print("\n  se il risultato va bene, esegui senza --preview per l'intero libro.")
    else:
        out, nota = write_result(ppaths.db, report.run_id, ppaths.exports / f"{out_stem}.{fmt}", fmt)
        console.print(f"[green]✓[/green] esportato: {out}")
        if nota:
            console.print(f"  [yellow]{nota}[/yellow]")


@app.command()
def rewrite(
    style: str = typer.Option(..., "--style", help="Id o nome del profilo di stile."),
    document: int = typer.Option(None, "--document", help="Id documento (default: il primo)."),
    preview: bool = typer.Option(False, "--preview", help="Riscrive solo il primo capitolo (confronto originale↔riscritto)."),
    ratio: float = typer.Option(None, "--ratio", help="Rapporto di lunghezza sul testo originale (es. 0.7)."),
    fmt: str = typer.Option("docx", "--format", "-f", help="md|docx|pdf (run completa)."),
    fresh: bool = typer.Option(False, "--fresh", help="Non riprendere una run esistente."),
) -> None:
    """Riscrive un libro in un nuovo stile (deterministico, chunk-per-chunk, con continuità)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    contract = build_style_contract(_load_style(ppaths, style))
    _run_book_transform(
        slug, ppaths, pm.config_for(slug), contract=contract, document=document, preview=preview,
        ratio=ratio, fmt=fmt, fresh=fresh, mode="rewrite", target_language=None, out_stem=f"{slug}-rewrite",
    )


@app.command()
def translate(
    to: str = typer.Option(..., "--to", help="Lingua di destinazione (es. inglese, francese)."),
    style: str = typer.Option(None, "--style", help="Profilo di stile (opzionale; default: mantieni lo stile originale)."),
    document: int = typer.Option(None, "--document", help="Id documento (default: il primo)."),
    preview: bool = typer.Option(False, "--preview", help="Traduce solo il primo capitolo (confronto)."),
    ratio: float = typer.Option(None, "--ratio", help="Rapporto di lunghezza sull'originale."),
    fmt: str = typer.Option("docx", "--format", "-f", help="md|docx|pdf (run completa)."),
    fresh: bool = typer.Option(False, "--fresh", help="Non riprendere una run esistente."),
) -> None:
    """Traduce un libro mantenendo lo stile e la coerenza terminologica (glossario bilingue)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    if style:
        contract = build_style_contract(_load_style(ppaths, style))
    else:
        contract = "Mantieni fedelmente lo stile e il registro dell'originale."
    _run_book_transform(
        slug, ppaths, pm.config_for(slug), contract=contract, document=document, preview=preview,
        ratio=ratio, fmt=fmt, fresh=fresh, mode="translate", target_language=to, out_stem=f"{slug}-{to}",
    )


# --------------------------------------------------------------------------- #
#  author (modalità D: authoring interattivo)
# --------------------------------------------------------------------------- #
@author_app.command("start")
def author_start(
    theme: str = typer.Option(..., "--theme", help="Tema/argomento dell'opera."),
    audience: str = typer.Option(None, "--audience", help="Pubblico di riferimento."),
    goal: str = typer.Option(None, "--goal", help="Obiettivo del libro."),
    target_words: int = typer.Option(None, "--target-words", help="Target di parole."),
) -> None:
    """Avvia un progetto autoriale registrando il brief (intenti persistiti + target)."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    info = start_authoring(ppaths.db, theme=theme, audience=audience, goal=goal, target_words=target_words)
    console.print(f"[green]✓[/green] brief registrato (sessione #{info.session_id}). Tema: {theme}")
    console.print("  ora: [bold]bookwriter author expand --topic \"...\"[/bold] per generare idee.")


@author_app.command("expand")
def author_expand(
    topic: str = typer.Option(..., "--topic", help="Tema/sotto-tema da sviluppare."),
    n: int = typer.Option(6, "-n", help="Numero di idee da generare."),
    audience: str = typer.Option(None, "--audience", help="Pubblico (contesto)."),
    goal: str = typer.Option(None, "--goal", help="Obiettivo (contesto)."),
) -> None:
    """L'LLM genera idee originali sul tema e le aggiunge alla memoria (da zero)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    try:
        count = expand_topic(
            ppaths.db, ppaths.vectors, client,
            topic=topic, n=n, audience=audience, goal=goal, model=cfg.llm.models.reasoning,
        )
    except LLMError as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] generate {count} idee (nodi). Ripeti su altri sotto-temi o passa a `author draft`.")


@author_app.command("draft")
def author_draft(
    style: str = typer.Option(None, "--style", help="Profilo di stile (opzionale)."),
    chapters: int = typer.Option(None, "--chapters", help="Numero di capitoli (default: automatico)."),
    fmt: str = typer.Option("docx", "--format", "-f", help="md|docx|pdf."),
) -> None:
    """Genera la bozza: grafo → organizzazione tematica → stesura narrativa → export."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    model = cfg.llm.models.reasoning
    try:
        embed_nodes(ppaths.db, ppaths.vectors, client)
        build_graph(ppaths.db, ppaths.vectors)
        org = organize_thematic(
            ppaths.db, ppaths.vectors, method="embedding", chapters=chapters,
            name="Bozza autoriale", client=client, model=model,
        )
        contract = build_style_contract(_load_style(ppaths, style)) if style else "Scrivi in prosa chiara, scorrevole e coerente."
        spec = project_schema.get_bookspec(ppaths.db)
        rep = compose_outline(
            ppaths.db, org.outline_id, contract, client,
            target_words=spec.target_words, tolerance_pct=spec.tolerance_pct, model=model,
        )
    except (ValueError, LLMError) as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    out = write_output(compose_to_markdown(ppaths.db, org.outline_id), ppaths.exports / f"{slug}-book.{fmt}", fmt)
    console.print(f"[green]✓[/green] {rep.summary()}")
    console.print(f"[green]✓[/green] bozza esportata: {out}")


# --------------------------------------------------------------------------- #
#  outline (ispezione struttura; editing avanzato nella GUI)
# --------------------------------------------------------------------------- #
@outline_app.command("list")
def outline_list() -> None:
    """Elenca gli outline del progetto."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    rows = list_outlines(ppaths.db)
    if not rows:
        console.print("[dim]Nessun outline. Esegui `organize`.[/dim]")
        return
    for r in rows:
        console.print(f"  #{r['id']} [bold]{r['name']}[/bold] · {r['ordering']} · {r['render_mode']} · {r['status']}")


@outline_app.command("sections")
def outline_sections(
    outline: int = typer.Option(None, "--outline", help="Id outline (default: l'ultimo)."),
    max_per_chapter: int = typer.Option(4, "--max", help="Massimo sezioni per capitolo (0 = automatico)."),
    llm_titles: bool = typer.Option(True, "--llm-titles/--no-llm-titles", help="Titoli sezione dall'LLM."),
) -> None:
    """Suddivide i capitoli in sezioni (sotto-clustering dei nodi)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    outline_id = outline or latest_outline_id(ppaths.db)
    if not outline_id:
        err.print("[red]Nessun outline. Esegui prima `organize`.[/red]")
        raise typer.Exit(code=1)
    client = LMStudioClient(cfg.llm, cfg.embedding) if llm_titles else None
    report = subdivide_sections(
        ppaths.db, ppaths.vectors, outline_id,
        client=client, model=cfg.llm.models.reasoning, max_per_chapter=max_per_chapter or None,
    )
    console.print(f"[green]✓[/green] create {report['sections']} sezioni.")


@outline_app.command("show")
def outline_show(outline: int = typer.Argument(None, help="Id outline (default: l'ultimo).")) -> None:
    """Mostra la struttura (capitoli e nodi) di un outline."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    outline_id = outline or latest_outline_id(ppaths.db)
    if not outline_id:
        err.print("[red]Nessun outline.[/red]")
        raise typer.Exit(code=1)
    for ch in outline_tree(ppaths.db, outline_id):
        console.print(f"[bold]{ch['title']}[/bold] ({len(ch['nodes'])} nodi)")
        for n in ch["nodes"]:
            console.print(f"    · #{n['node_id']} {n['summary']}")


@app.command()
def coherence(
    outline: int = typer.Option(None, "--outline", help="Id outline composto (default: l'ultimo)."),
    run: int = typer.Option(None, "--run", help="Id run di riscrittura/traduzione (invece dell'outline)."),
) -> None:
    """Uniforma nomi/termini su tutto il libro (glossario canonico + applicazione deterministica)."""
    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding)
    model = cfg.llm.models.quality  # usa il modello 'heavy' se impostato
    try:
        if run is not None:
            report = coherence_pass_rewrite(ppaths.db, run, client, model=model)
        else:
            outline_id = outline or latest_outline_id(ppaths.db)
            if not outline_id:
                err.print("[red]Nessun outline composto. Esegui prima `compose`.[/red]")
                raise typer.Exit(code=1)
            report = coherence_pass_compose(ppaths.db, outline_id, client, model=model)
    except LLMError as exc:
        err.print(f"[red]Errore coerenza:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] coerenza: {report['replacements']} sostituzioni, {len(report['mappings'])} varianti uniformate")
    for issue in report.get("timeline_issues") or []:
        console.print(f"  [yellow]⏱ timeline:[/yellow] {issue}")
    for rep in report.get("repetitions") or []:
        console.print(f"  [yellow]↺ ripetizione:[/yellow] {rep}")


@app.command()
def jobs() -> None:
    """Mostra lo storico delle operazioni pesanti (atomize/embed) con stato e progresso."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    rows = project_schema.list_jobs(ppaths.db)
    if not rows:
        console.print("[dim]Nessuna operazione registrata.[/dim]")
        return
    for r in rows:
        console.print(f"  #{r['id']} [bold]{r['kind']}[/bold] · {r['state']} · {int(r['progress'] * 100)}% · {r['updated_at']}")


# --------------------------------------------------------------------------- #
#  gui
# --------------------------------------------------------------------------- #
@app.command()
def gui() -> None:
    """Avvia l'interfaccia grafica desktop (PySide6)."""
    try:
        from bookwriter.gui import run
    except ModuleNotFoundError as exc:
        err.print(f"[red]GUI non disponibile:[/red] {exc}. Installa: pip install -e \".[gui]\"")
        raise typer.Exit(code=1)
    raise typer.Exit(code=run())


if __name__ == "__main__":
    app()
