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
forge_app = typer.Typer(no_args_is_help=True, help="Le fasi: dal colloquio con l'autore al libro stampabile.")
app.add_typer(project_app, name="project")
app.add_typer(memory_app, name="memory")
app.add_typer(session_app, name="session")
app.add_typer(style_app, name="style")
app.add_typer(author_app, name="author")
app.add_typer(outline_app, name="outline")
app.add_typer(forge_app, name="forge")

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
    out = write_output(
        compose_to_markdown(ppaths.db, outline_id), ppaths.exports / f"{slug}-book.{fmt}", fmt,
        language=cfg.project.language or "it", production=cfg.production,
    )
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
    out = write_output(
        compose_to_markdown(ppaths.db, org.outline_id), ppaths.exports / f"{slug}-book.{fmt}", fmt,
        language=cfg.project.language or "it", production=cfg.production,
    )
    console.print(f"[green]✓[/green] {rep.summary()}")
    console.print(f"[green]✓[/green] bozza esportata: {out}")


# --------------------------------------------------------------------------- #
#  forge (le sette fasi: dal colloquio con l'autore al libro stampabile)
# --------------------------------------------------------------------------- #
def _forge(*, with_client: bool = True):
    """Il libro del progetto attivo, pronto da lavorare."""
    from bookwriter.forge import open_forge

    pm = _pm()
    slug, ppaths = _active_or_exit(pm)
    cfg = pm.config_for(slug)
    client = LMStudioClient(cfg.llm, cfg.embedding) if with_client else None
    return open_forge(ppaths, cfg, client=client)


def _forge_style(forge, style: str | None) -> str:
    """Il contratto di stile: quello chiesto con ``--style``, altrimenti quello scelto per il
    libro (anche dalla finestra), perché conversazione e stesura parlino dello stesso stile."""
    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    if style:
        return build_style_contract(_load_style(ppaths, style))
    if forge.state.style_id:
        row = project_schema.get_style_row(ppaths.db, int(forge.state.style_id))
        if row is not None:
            return build_style_contract(StyleProfile.model_validate(row["data"]))
    return ""


@forge_app.command("init")
def forge_init() -> None:
    """Prepara la cartella del libro dentro il progetto attivo."""
    forge = _forge(with_client=False)
    forge.start()
    console.print(f"[green]✓[/green] libro preparato in {forge.project.root}")
    console.print("  MASTER_SPEC.md   la specifica contro cui lavorano tutte le fasi")
    console.print("  chapters/        un file per capitolo, salvato appena è scritto")
    console.print("  front/ back/     introduzione, avvertenza, glossario, biografia")
    console.print("  research/        le fonti, con il livello di prova di ogni affermazione")
    console.print("  output/          quello che produce la composizione")


@forge_app.command("status")
def forge_status() -> None:
    """Cosa c'è davvero su disco, contro quello che la specifica prevede."""
    forge = _forge(with_client=False)
    stato = forge.status()
    console.print(stato.render())
    console.print()
    for fase in forge.phases():
        segno = {"fatta": "[green]✓[/green]", "in corso": "[yellow]›[/yellow]"}.get(fase.state, " ")
        console.print(f" {segno} {fase.title:<22} {fase.detail}")
    raise typer.Exit(code=3 if stato.missing or stato.unreadable else 0)


@forge_app.command("interview")
def forge_interview(
    style: str = typer.Option(None, "--style", help="Profilo di stile scelto per il libro."),
) -> None:
    """Conversa con l'assistente sul libro: trama, personaggi, scopo, lettore.

    Scrivi come parleresti a un editor: l'assistente domanda, propone e discute. Di stampa non
    si parla — misura, copertina e formati si decidono con ``forge produce``.

    Una riga vuota mette in pausa (quello che è stato detto resta, e riprendendo si continua da
    lì); «/quaderno» mostra il quaderno del libro; «/fine» chiude la conversazione e lascia
    decidere il resto all'assistente.
    """
    import sys

    from bookwriter.forge import interview as iv
    from bookwriter.forge import notebook as nb

    forge = _forge()
    forge.start()
    contratto = _forge_style(forge, style)
    if forge.state.transcript:
        ultima = forge.state.transcript[-1]
        console.print(f"[dim]… si riprende da: {ultima.get('text', '')[:300]}[/dim]")
    else:
        console.print(iv.OPENING, markup=False)
    console.print("[dim]Quando hai finito scrivi /fine: chiude la conversazione e lascia "
                  "decidere il resto all'assistente.[/dim]")

    def scrivi(tipo: str, pezzo: str) -> None:
        # Il testo del modello va stampato così com'è: per rich una parentesi quadra è markup.
        if tipo == "content":
            sys.stdout.write(pezzo)
            sys.stdout.flush()

    def chiudi() -> None:
        try:
            console.print(forge.finish_interview(style_contract=contratto), markup=False)
        except (LLMError, ValueError) as exc:
            err.print(f"[red]Errore:[/red] {exc}")
            raise typer.Exit(code=1)
        console.print()
        console.print(nb.render(forge.state.notebook), markup=False)

    while True:
        console.print()
        testo = typer.prompt("Tu", default="", show_default=False).strip()
        if not testo:
            console.print("[yellow]In pausa.[/yellow] Quello che hai detto è salvato.")
            break
        if testo == "/quaderno":
            console.print(nb.render(forge.state.notebook) or "(il quaderno è ancora vuoto)",
                          markup=False)
            continue
        if testo == "/fine":
            chiudi()
            break
        console.print()
        try:
            forge.talk(testo, style_contract=contratto, on_delta=scrivi)
        except LLMError as exc:
            err.print(f"\n[red]La risposta non è arrivata:[/red] {exc}")
            continue
        console.print()
        try:
            esito = forge.update_notebook()
        except (LLMError, ValueError) as exc:
            console.print(f"[dim]Il quaderno non si è aggiornato ({exc}): riprovo al prossimo "
                          "scambio.[/dim]")
            continue
        if esito.close_requested and not forge.state.interview_closed:
            # Come /fine: l'autore l'ha chiesto a parole, e la conversazione non avvia niente.
            console.print("[dim]Hai chiesto di cominciare: chiudo la conversazione.[/dim]")
            chiudi()
            break


@forge_app.command("finish")
def forge_finish(
    style: str = typer.Option(None, "--style", help="Profilo di stile scelto per il libro."),
) -> None:
    """Chiude la conversazione: l'assistente completa il quaderno e dice cosa ha deciso."""
    from bookwriter.forge import notebook as nb

    forge = _forge()
    try:
        console.print(forge.finish_interview(style_contract=_forge_style(forge, style)),
                      markup=False)
    except (LLMError, ValueError) as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print()
    console.print(nb.render(forge.state.notebook), markup=False)


@forge_app.command("spec")
def forge_spec(
    chapters: int = typer.Option(0, "--chapters", help="Quanti capitoli; 0 = decide l'assistente."),
    approve: bool = typer.Option(False, "--approve", help="Approva subito, senza rileggerla."),
    style: str = typer.Option(None, "--style", help="Profilo di stile scelto per il libro."),
) -> None:
    """Propone la struttura e scrive ``MASTER_SPEC.md``."""
    forge = _forge()
    try:
        testo = forge.make_spec(chapters=chapters or None,
                                style_contract=_forge_style(forge, style))
    except (LLMError, ValueError) as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(testo)
    console.print(f"\n[green]✓[/green] scritta: {forge.project.spec_path}")
    if approve:
        forge.approve_spec()
        console.print("[green]✓[/green] approvata.")
    else:
        console.print("Rileggila e poi `bookwriter forge approve`. Rivedere una struttura costa "
                      "minuti, rivedere venti capitoli costa giorni.")


@forge_app.command("approve")
def forge_approve() -> None:
    """Approva la specifica: da qui in poi si scrive contro quel documento."""
    forge = _forge(with_client=False)
    if not forge.project.spec_path.exists():
        err.print("[red]La specifica non è stata scritta.[/red] Esegui prima `forge spec`.")
        raise typer.Exit(code=1)
    forge.approve_spec()
    console.print("[green]✓[/green] specifica approvata.")


research_app = typer.Typer(
    no_args_is_help=False,
    invoke_without_command=True,
    help="Ricerca: prima il panorama, poi le fonti dei capitoli.",
)
blocked_app = typer.Typer(
    no_args_is_help=True,
    help="Gestione delle pagine bloccate e della coda su cartella.",
)


@research_app.callback(invoke_without_command=True)
def forge_research(
    ctx: typer.Context,
    landscape: bool = typer.Option(True, "--landscape/--no-landscape",
                                   help="Guarda i libri che esistono già."),
    chapters: bool = typer.Option(False, "--chapters", help="Cerca le fonti di ogni capitolo."),
    decline: bool = typer.Option(False, "--decline", help="Rinuncia alla ricerca e vai avanti."),
    redo: bool = typer.Option(False, "--redo",
                              help="Rifà panorama e punti di tutti i capitoli, tenendo il "
                                   "materiale già raccolto e le tue decisioni."),
    fresh: bool = typer.Option(False, "--fresh",
                               help="Mette da parte tutto il materiale (research/.superati/) e "
                                    "ricomincia da zero."),
) -> None:
    """Ricerca: prima il panorama (che cambia la struttura), poi le fonti dei capitoli.

    Senza --redo né --fresh **riprende** da dove si era arrivati: il panorama c'è già e i
    capitoli e i punti fatti non si rifanno.
    """
    if ctx.invoked_subcommand is not None:
        return

    forge = _forge()
    if decline:
        console.print(forge.decline_research())
        return
    if redo and fresh:
        err.print("[red]--redo e --fresh si escludono: scegline uno.[/red]")
        raise typer.Exit(code=2)
    if fresh:
        console.print(f"Messo da parte in: {forge.set_aside_research() or '(niente da mettere da parte)'}")
    elif redo:
        console.print(f"Punti azzerati in {forge.prepare_redo()} capitoli (il materiale resta).")
    from bookwriter.forge.research import ResearchFailed

    try:
        if landscape and (redo or fresh or forge.landscape_data() is None):
            report = forge.research_landscape()
            console.print(report.summary())
        if chapters:
            from bookwriter.forge.research import describe_note

            for rapporto in forge.research_chapters(
                    note=lambda n: console.print(f"  {describe_note(n)}")):
                console.print(rapporto.summary())
    except ResearchFailed as exc:
        # Una ricerca a buchi non è fatta: lo si dice in chiaro e si esce con errore, invece di
        # lasciare una traccia di eccezione che non spiega cosa fare.
        from rich.markup import escape

        err.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc


@blocked_app.command("list")
def forge_blocked_list() -> None:
    """Le pagine in coda con motivo e stato."""
    from rich.table import Table

    forge = _forge(with_client=False)
    pagine = forge.blocked_pages()
    if not pagine:
        console.print("Nessuna pagina bloccata registrata.")
        return

    table = Table(title="Pagine bloccate (coda.jsonl)")
    table.add_column("ID", style="cyan")
    table.add_column("Capitolo", justify="right")
    table.add_column("Punto", justify="right")
    table.add_column("Motivo", style="yellow")
    table.add_column("Stato", style="bold")
    table.add_column("Indirizzo", style="blue")

    for p in pagine:
        table.add_row(
            p.id,
            str(p.chapter),
            str(p.point),
            p.reason,
            p.status,
            p.url,
        )
    console.print(table)


@blocked_app.command("export")
def forge_blocked_export() -> None:
    """Riscrive research/bloccate/coda.jsonl."""
    from bookwriter.forge import blocked

    forge = _forge(with_client=False)
    pagine = forge.blocked_pages()
    blocked.save_coda(forge.project.research_dir, pagine)
    coda_p = blocked.coda_file(forge.project.research_dir)
    console.print(f"[green]✓[/green] Riscritte {len(pagine)} righe in {coda_p}.")


@blocked_app.command("import")
def forge_blocked_import() -> None:
    """Importa ciò che c'è in research/bloccate/ricevute/."""
    forge = _forge(with_client=True)
    esiti = forge.import_blocked_pages()
    if not esiti:
        console.print("Nessun file trovato da importare in research/bloccate/ricevute/.")
        return

    importati = sum(1 for e in esiti if e.get("status") == "ok")
    non_ric = sum(1 for e in esiti if e.get("status") == "non_riconosciuta")
    non_leg = sum(1 for e in esiti if e.get("status") in ("non_leggibile", "vuoto"))

    for e in esiti:
        st = e.get("status")
        f = e.get("file")
        if st == "ok":
            console.print(f"[green]✓[/green] {f} importato con successo.")
        elif st in ("non_leggibile", "vuoto"):
            console.print(f"[yellow]⚠[/yellow] {f} non leggibile: {e.get('detail', '')}")
        else:
            console.print(f"[red]✗[/red] {f} non riconosciuto: {e.get('detail', '')}")

    console.print(f"\nRiepilogo: {importati} importati, {non_leg} non leggibili, {non_ric} non riconosciuti.")


@blocked_app.command("fetch")
def forge_blocked_fetch() -> None:
    """Chiede al servizio esterno (/v1) le pagine in coda e le importa."""
    forge = _forge(with_client=True)
    try:
        esito = forge.fetch_blocked_pages(
            progress=lambda fatte, tot: console.print(f"  {fatte} di {tot}") if tot else None)
    except RuntimeError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if not esito["total"]:
        console.print("Nessuna pagina in coda da recuperare.")
        return
    if not esito["service_ready"]:
        err.print("[yellow]Il servizio esterno non risulta pronto: provo lo stesso.[/yellow]")
    ancora = ", ".join(f"{n} {m}" for m, n in esito["still_blocked"].items()) or "0"
    console.print(
        f"Recuperate {esito['fetched']} di {esito['total']}. Ancora bloccate: {ancora}. "
        f"Non pertinenti: {esito['irrelevant']}. Senza risposta dal servizio: "
        f"{esito['no_answer']}. Già in ricevute/: {esito['skipped']}."
        + (" Interrotto." if esito["stopped"] else ""))


@forge_app.command("update-trawl")
def forge_update_trawl() -> None:
    """Aggiorna il microservizio TRAWL dal repository originale GitHub."""
    from bookwriter.forge import browser_service

    console.print("[cyan]==>[/cyan] Aggiornamento di TRAWL in corso...")
    try:
        res = browser_service.update_trawl()
        commit = res.get("commit") or "sconosciuto"
        date = res.get("date") or ""
        path = res.get("path") or ""
        console.print("[green]✓[/green] TRAWL aggiornato con successo!")
        console.print(f"  Commit: [bold]{commit}[/bold] ({date})")
        console.print(f"  Percorso: [dim]{path}[/dim]")
    except Exception as exc:
        err.print(f"[red]Errore durante l'aggiornamento di TRAWL:[/red] {exc}")
        raise typer.Exit(code=1) from exc


blocked_app.command("update-trawl")(forge_update_trawl)


@forge_app.command("start-trawl")
def forge_start_trawl(
    port: int = typer.Option(8191, "--port", "-p", help="Porta su cui avviare TRAWL (default 8191)."),
) -> None:
    """Avvia il microservizio TRAWL in modalità standalone."""
    import os
    import subprocess
    from bookwriter.forge import browser_service

    bun_exe = browser_service.find_bun()
    if not bun_exe:
        err.print("[red]Bun non trovato.[/red] Installalo con 'winget install Oven-sh.Bun' o da https://bun.sh")
        raise typer.Exit(code=1)

    t_dir = browser_service.trawl_dir()
    api_dir = t_dir / "apps" / "api"
    entry = api_dir / "src" / "index.ts"
    if not entry.exists():
        err.print(f"[red]TRAWL non trovato in {t_dir}.[/red] Esegui prima `forge update-trawl`.")
        raise typer.Exit(code=1)

    console.print(f"[cyan]==>[/cyan] Avvio di TRAWL in modalità standalone su [bold]http://127.0.0.1:{port}[/bold]...")
    console.print("[dim]Premi Ctrl+C per arrestarlo.[/dim]")

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["SESSION_CACHE_DRIVER"] = "memory"
    env["BROWSER_POOL_SIZE"] = "1"
    env["BROWSER_HEADFUL_POOL_SIZE"] = "0"
    env["SCRAPE_MIN_TIER"] = "1"
    env["MCP_ENABLED"] = "false"
    env["MITM_ENABLED"] = "false"

    try:
        subprocess.run([bun_exe, "--cwd", str(api_dir), "src/index.ts"], cwd=str(t_dir), env=env)
    except KeyboardInterrupt:
        console.print("\n[yellow]TRAWL arrestato.[/yellow]")


blocked_app.command("start-trawl")(forge_start_trawl)


research_app.add_typer(blocked_app, name="blocked")
forge_app.add_typer(research_app, name="research")
forge_app.add_typer(blocked_app, name="blocked")



@forge_app.command("questions")
def forge_questions(
    decide: str = typer.Option(None, "--decide", help="L'id della questione su cui decidere."),
    omit: bool = typer.Option(False, "--omit", help="Con --decide: tralascia l'informazione."),
    publish: str = typer.Option(None, "--publish",
                                help="Con --decide: pubblica con questa formulazione."),
    discard: str = typer.Option(None, "--discard",
                                help="Scarta questa questione (non si cancella: si ripristina "
                                     "con --restore)."),
    restore: str = typer.Option(None, "--restore", help="Ripristina una questione scartata."),
    discard_old: bool = typer.Option(False, "--discard-old",
                                     help="Scarta tutte le questioni vecchie ancora aperte."),
    resume: bool = typer.Option(False, "--resume",
                                help="Riprende l'approfondimento delle questioni rimaste ferme."),
    source: str = typer.Option(None, "--source",
                               help="Con --decide: un indirizzo o un file (pdf, docx, md, txt) "
                                    "che documenta la questione."),
) -> None:
    """I contrasti segnalati dalla ricerca (fatti che sembrano comprovati), con il loro stato.

    La conversazione con l'assistente sta nella finestra; da qui basta poter vedere e decidere.
    """
    from pathlib import Path

    from bookwriter.forge import research_store as store

    forge = _forge(with_client=source is not None or resume)
    if discard_old:
        console.print(f"{forge.discard_old_questions()} questioni vecchie scartate.")
        return
    if discard or restore:
        try:
            q = forge.discard_question(discard or restore, on=bool(discard))
        except ValueError as exc:
            err.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        console.print(f"{q.id}: {'scartata' if q.discarded else 'ripristinata'}.")
        return
    if resume:
        try:
            console.print(f"{forge.resume_deepening()} questioni riprese.")
        except RuntimeError as exc:
            err.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2) from exc
        return
    if decide is None:
        domande = forge.questions()
        if not domande:
            console.print("Nessun contrasto segnalato: nel libro resta tutto com'è scritto.")
            return
        for q in domande:
            esito = q.outcome or {}
            dettaglio = {"source": f" → fonte: {esito.get('source', '')}",
                         "publish": f" → pubblica così: {esito.get('text', '')}",
                         "omit": " → tralascia"}.get(esito.get("kind", ""), "")
            sc = " (scartata)" if q.discarded else ""
            console.print(f"[bold]{q.id}[/bold] [{q.status}]{sc} cap. {q.chapter}: "
                          f"{q.text}{dettaglio}")
        console.print(f"\n{len(forge.pending_questions())} da decidere.")
        return
    try:
        if source is not None:
            percorso = Path(source)
            testo, files = ("", [percorso]) if percorso.exists() else (source, [])
            risolte, materiale = forge.resolve_with_material(testo, files)
            for nota in materiale.notes:
                err.print(f"[yellow]{nota}[/yellow]")
            if not any(q.id == decide for q in risolte):
                err.print("[red]Il materiale non risolve questa questione.[/red]")
                raise typer.Exit(code=1)
            console.print(f"[green]✓[/green] {decide} risolta.")
        elif omit:
            forge.decide_question(decide, store.OUT_OMIT)
            console.print(f"[green]✓[/green] {decide}: tralasciata.")
        elif publish:
            forge.decide_question(decide, store.OUT_PUBLISH, publish)
            console.print(f"[green]✓[/green] {decide}: si pubblica così.")
        else:
            err.print("[red]Con --decide serve --omit, --publish \"testo\" o --source.[/red]")
            raise typer.Exit(code=2)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)


review_app = typer.Typer(
    no_args_is_help=True,
    help="La revisione dei risultati della ricerca con l'autore, un punto alla volta (facoltativa).")
forge_app.add_typer(review_app, name="review")


def _review_point_line(p) -> str:
    stato = {"aperto": "aperto", "in_discussione": "in discussione", "concordato": "concordato",
             "imposto": "imposto dall'autore", "fonte": "fonte", "tralasciato": "tralasciato",
             "rimandato": "rimandato", "obsoleto": "obsoleto"}.get(p.status, p.status)
    cap = f" cap. {p.chapter}" if p.chapter else ""
    return f"[bold]{p.id}[/bold] [{stato}] ({p.kind}{cap}) {p.title}"


@review_app.command("generate")
def review_generate() -> None:
    """Prepara l'elenco dei punti da discutere (rifarlo non perde le decisioni già prese)."""
    from bookwriter.forge.review import ReviewFailed

    forge = _forge()
    try:
        dati = forge.review_generate(note=lambda n: err.print(f"[yellow]{n}[/yellow]"))
    except (ReviewFailed, LLMError, RuntimeError) as exc:
        from rich.markup import escape

        err.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"{len(dati.points)} punti. Usa `forge review list`.")
    for w in dati.warnings:
        err.print(f"[yellow]{w}[/yellow]")


@review_app.command("list")
def review_list() -> None:
    """I punti, nell'ordine in cui si propongono, con il loro stato."""
    forge = _forge(with_client=False)
    dati = forge.review_data()
    if dati is None or not dati.points:
        console.print("Nessun punto: la revisione non è stata preparata (`forge review generate`).")
        return
    for p in dati.points:
        console.print(_review_point_line(p))
    console.print(f"\n{len(dati.pending())} ancora da decidere.")


@review_app.command("show")
def review_show(point_id: str = typer.Argument(..., help="L'id del punto, es. r001.")) -> None:
    """Un punto per intero: cosa dice il libro, le prove, la conversazione, la decisione."""
    from bookwriter.forge import review as rv

    forge = _forge(with_client=False)
    dati = forge.review_data()
    punto = dati.point(point_id) if dati else None
    if punto is None:
        err.print(f"[red]Non c'è nessun punto {point_id}.[/red]")
        raise typer.Exit(code=2)
    console.print(_review_point_line(punto))
    console.print(rv.opening(punto), markup=False)
    for b in punto.thread[1:]:
        ruolo = {"assistant": "Assistente", "user": "Autore", "notice": "·"}.get(b.get("role"), "?")
        console.print(f"\n{ruolo}: {b.get('text', '')}", markup=False)
    if punto.decision:
        d = punto.decision
        console.print(f"\nDecisione ({d.kind}): {d.text or 'tralascia'}", markup=False)


@review_app.command("talk")
def review_talk(
    point_id: str = typer.Argument(..., help="L'id del punto."),
    text: str = typer.Argument(..., help="Cosa dici all'assistente."),
) -> None:
    """Una battuta sul punto: stampa la risposta e la decisione che l'app registrerebbe."""
    forge = _forge()
    try:
        risposta, log, note = forge.review_talk(point_id, text)
        proposta = forge.review_propose(point_id)
    except (ValueError, LLMError, RuntimeError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    for s_ in log:
        console.print(f"· {s_.summary()}", markup=False)
    for n in note:
        err.print(f"[yellow]{n}[/yellow]")
    console.print(risposta, markup=False)
    if proposta:
        console.print(f"\nRegistrerei ({proposta['kind']}): {proposta['text'] or 'tralascia'}\n"
                      f"Per confermare: `forge review confirm {point_id} --kind "
                      f"{proposta['kind']} --text \"…\"`", markup=False)


@review_app.command("confirm")
def review_confirm(
    point_id: str = typer.Argument(..., help="L'id del punto."),
    kind: str = typer.Option(..., "--kind", help="concordata, imposta, fonte o tralascia."),
    text: str = typer.Option("", "--text", help="La formulazione da usare nel libro."),
    note: str = typer.Option("", "--note", help="Note per gli appunti (prove contrarie…)."),
    keep_note: bool = typer.Option(False, "--keep-note/--no-keep-note",
                                   help="Metti le note negli appunti (review_notes.md)."),
    source: str = typer.Option("", "--source", help="Per `fonte`: la fonte."),
    url: str = typer.Option("", "--url", help="Per `fonte`: l'indirizzo."),
) -> None:
    """Registra la decisione dell'autore su un punto (l'unico comando che la scrive)."""
    forge = _forge(with_client=False)
    try:
        p = forge.review_confirm(point_id, kind, text, note=note, keep_note=keep_note,
                                 source=source, url=url, by_author=True)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]✓[/green] {p.id}: {p.status}.")


@review_app.command("skip")
def review_skip(
    point_id: str = typer.Argument(None, help="Il punto da saltare; senza, vedi --all."),
    all_points: bool = typer.Option(False, "--all", help="Salta la revisione intera."),
) -> None:
    """Salta un punto (o tutta la revisione): resta da decidere, senza nessun effetto sul libro."""
    forge = _forge(with_client=False)
    try:
        if all_points:
            console.print(f"{forge.review_skip_all()} punti rimandati.")
        elif point_id:
            forge.review_skip_point(point_id)
            console.print(f"{point_id}: rimandato.")
        else:
            err.print("[red]Serve l'id di un punto, oppure --all.[/red]")
            raise typer.Exit(code=2)
    except ValueError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc


@review_app.command("free")
def review_free(text: str = typer.Argument(..., help="Cosa dici all'assistente.")) -> None:
    """La chat libera di fine revisione, con le perplessità dell'app."""
    forge = _forge()
    try:
        risposta, log, note = forge.review_free_talk(text)
    except (ValueError, LLMError, RuntimeError) as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc
    for s_ in log:
        console.print(f"· {s_.summary()}", markup=False)
    for n in note:
        err.print(f"[yellow]{n}[/yellow]")
    console.print(risposta, markup=False)


@review_app.command("perplexities")
def review_perplexities() -> None:
    """Le perplessità dell'app prima di scrivere (fatti verificabili, nessun giudizio)."""
    forge = _forge(with_client=False)
    cose = forge.review_perplexities()
    for c in cose:
        console.print(f"- {c}", markup=False)
    if not cose:
        console.print("Nessuna perplessità.")


@forge_app.command("write")
def forge_write(
    all_chapters: bool = typer.Option(False, "--all", help="Scrivi tutti i capitoli che mancano."),
    style: str = typer.Option(None, "--style", help="Profilo di stile da applicare."),
    redo_short: bool = typer.Option(
        False, "--redo-short",
        help="Rifà i capitoli sotto il 60% del loro budget di parole: quelli di prima si mettono "
             "da parte in chapters/.superati/, non si cancellano."),
    chapter: int = typer.Option(None, "--chapter", help="Con --trial: il capitolo da riscrivere."),
    trial: bool = typer.Option(
        False, "--trial",
        help="Riscrive il capitolo --chapter in prova, in output/prove/: il capitolo in chapters/ "
             "non si tocca."),
    waive: str = typer.Option(
        "", "--waive", help="Deroga motivata ai difetti bloccanti del piano (resta registrata). "
                            "Senza, un piano con blocker non si scrive."),
    replan: bool = typer.Option(False, "--replan",
                                help="Butta il piano del capitolo e lo rifà prima di scrivere."),
) -> None:
    """Scrive i capitoli. Ognuno finisce su disco appena è pronto, prima del successivo."""
    forge = _forge()
    contratto = _forge_style(forge, style)
    if trial or chapter is not None:
        if not (trial and chapter):
            err.print("[red]Errore:[/red] --chapter e --trial vanno insieme: una prova di un "
                      "capitolo. I capitoli veri si scrivono senza.")
            raise typer.Exit(code=2)

    def racconta(n) -> None:
        if isinstance(n, tuple) and n and n[0] == "section":
            console.print(f"  capitolo {n[1]}: sezione {n[2]} di {n[3]}")
        else:
            console.print(f"  {n}")

    def bloccato(risultato) -> bool:
        if risultato is None or not getattr(risultato, "blocked", None):
            return False
        console.print(f"[red]Fermato:[/red] il piano del capitolo {risultato.number} ha difetti "
                      "bloccanti:", markup=True)
        for d in risultato.blocked:
            console.print(f"  - {d}", markup=False)
        console.print("Ripianifica con --replan, o procedi con --waive \"motivo\".")
        return True

    try:
        if replan:
            from bookwriter.forge import writing as fw

            prossimo = fw.next_chapter(forge.project, forge.state)
            numero = chapter or (int(prossimo.get("number") or 0) if prossimo else 0)
            if numero:
                forge.replan_chapter(numero, trial=trial)
        if trial:
            risultato = forge.write_trial(chapter, style_contract=contratto, note=racconta,
                                          waiver=waive)
            if bloccato(risultato):
                raise typer.Exit(code=3)
            if risultato.stopped:
                console.print("Fermato: le scene scritte restano nel lavoro, si riprende da lì.")
            else:
                console.print(f"[green]✓[/green] prova: {risultato.line()} → {risultato.path}")
            return
        if redo_short:
            corti = forge.short_chapters()
            if not corti:
                console.print("Nessun capitolo è molto sotto il suo budget di parole.")
                return
            for numero, parole, budget in corti:
                console.print(f"  capitolo {numero}: {parole} parole su {budget}")
            messi, report = forge.redo_short_chapters(style_contract=contratto, note=racconta)
            for percorso in messi:
                console.print(f"  messo da parte: {percorso}")
            console.print(report.summary())
        elif all_chapters:
            report = forge.write_all(style_contract=contratto, note=racconta)
            console.print(report.summary())
            if report.blocked:
                raise typer.Exit(code=3)
        else:
            risultato = forge.write_next(style_contract=contratto, note=racconta, waiver=waive)
            if bloccato(risultato):
                raise typer.Exit(code=3)
            if risultato is None:
                console.print("Tutti i capitoli previsti sono già su disco.")
            else:
                console.print(f"[green]✓[/green] {risultato.line()} → {risultato.path}")
    except (LLMError, ValueError) as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)


@forge_app.command("revise")
def forge_revise(
    chapter: int = typer.Option(..., "--chapter", help="Il capitolo da rivedere."),
    file: Path = typer.Option(None, "--file", help="La bozza da rivedere (una prova); senza, il "
                                                   "capitolo in chapters/."),
    out: Path = typer.Option(None, "--out", help="Dove scrivere il testo rivisto (senza: nel "
                                                 "capitolo, se il verdetto lo permette)."),
    style: str = typer.Option(None, "--style", help="Profilo di stile da applicare."),
) -> None:
    """Un giro di revisione: diagnosi, piano, interventi, verifica indipendente, gate, verdetto.
    Il report va in revisioni/<run>/report.md."""
    forge = _forge()
    contratto = _forge_style(forge, style)
    testo = file.read_text(encoding="utf-8") if file else None
    try:
        esecuzione = forge.revise_chapter(chapter, text=testo, output=out, trial=file is not None,
                                          style_contract=contratto,
                                          note=lambda n: console.print(f"  {n}", markup=False))
    except (LLMError, ValueError, RuntimeError) as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"Verdetto: {esecuzione.verdict} · testo: {esecuzione.output_path}", markup=False)


@forge_app.command("surprise")
def forge_surprise(
    description: str = typer.Argument("", help="Cosa deve raccontare il libro, in poche frasi. "
                                                "Vuoto: riprende la sorpresa interrotta."),
    style: str = typer.Option(None, "--style", help="Profilo di stile da applicare."),
) -> None:
    """«Sorpresa»: una descrizione minima, e tutto il resto lo decide l'assistente — dal quaderno
    al libro composto."""
    forge = _forge()
    contratto = _forge_style(forge, style)

    def racconta(n) -> None:
        if isinstance(n, tuple) and n and n[0] == "section":
            console.print(f"  capitolo {n[1]}: scena {n[2]} di {n[3]}")
        elif not isinstance(n, tuple):
            console.print(f"  {n}", markup=False)

    try:
        console.print(forge.surprise(description, style_contract=contratto, note=racconta),
                      markup=False)
    except (LLMError, ValueError, RuntimeError) as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)


@forge_app.command("ledger")
def forge_ledger(
    rebuild: bool = typer.Option(False, "--rebuild",
                                 help="Rifà tutte le schede, anche quelle aggiornate."),
    upto: int = typer.Option(None, "--upto", help="Solo i capitoli fino a questo numero."),
) -> None:
    """Il registro della continuità: ricava le schede dai capitoli scritti (story/ledger.md)."""
    from bookwriter.forge import ledger as lg

    forge = _forge()
    try:
        rifatti = forge.ledger_rebuild(upto=upto, only_stale=not rebuild,
                                       progress=lambda i, n: console.print(f"  scheda {i + 1} di {n}")
                                       if i < n else None)
    except (LLMError, RuntimeError) as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    registro = lg.load(forge.project)
    for n in sorted(registro.chapters):
        v = registro.chapters[n]
        stato = f"errore: {v.error}" if v.error else (v.end_state.place or "?")
        console.print(f"  {n}. {v.title} — fine: {stato}", markup=False)
    console.print(f"[green]✓[/green] {len(rifatti)} schede ricavate → "
                  f"{lg.ledger_path(forge.project).with_suffix('.md')}")


@forge_app.command("claims")
def forge_claims(
    rebuild: bool = typer.Option(False, "--rebuild", help="Rifà l'archivio anche se è aggiornato."),
) -> None:
    """L'archivio delle affermazioni della ricerca: uniche, con fonti verificate e indipendenti."""
    from collections import Counter

    forge = _forge()
    archivio = forge.claim_store(rebuild=rebuild, note=lambda m: console.print(f"  {m}"))
    if archivio is None:
        console.print("Nessuna ricerca da riordinare.")
        return
    for chiave, valore in archivio.stats.items():
        console.print(f"  {chiave}: {valore}")
    sostegno = Counter(c.support for c in archivio.claims)
    console.print("  sostegno: " + ", ".join(f"{k} {v}" for k, v in sostegno.most_common()))


@forge_app.command("editorial")
def forge_editorial(
    chapter: int = typer.Option(..., "--chapter", help="Il capitolo da misurare."),
    trial: bool = typer.Option(False, "--trial", help="Misura la prova invece del capitolo."),
) -> None:
    """Le misure editoriali di un capitolo (ripetizioni, addensamenti, dialogo), senza modello."""
    forge = _forge(with_client=False)
    try:
        resoconto = forge.editorial_measures(chapter, trial=trial)
    except ValueError as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(resoconto.metrics_before, markup=False)


@forge_app.command("benchmark")
def forge_benchmark(
    chapter: int = typer.Option(..., "--chapter", help="Il capitolo da confrontare con la sua prova."),
) -> None:
    """Il capitolo scritto e la sua prova, misurati fianco a fianco (output/editoriale/)."""
    forge = _forge(with_client=False)
    try:
        prima = forge.editorial_measures(chapter)
        dopo = forge.editorial_measures(chapter, trial=True)
    except ValueError as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    path = forge.write_benchmark(chapter, prima, dopo)
    console.print("## Capitolo scritto", markup=False)
    console.print(prima.metrics_before, markup=False)
    console.print("\n## Prova", markup=False)
    console.print(dopo.metrics_before, markup=False)
    console.print(f"[green]✓[/green] confronto → {path}")


@forge_app.command("matter")
def forge_matter(
    kind: str = typer.Argument(..., help="introduzione|prefazione|avvertenza|glossario|"
                                         "biografia|ringraziamenti"),
    style: str = typer.Option(None, "--style", help="Profilo di stile da applicare."),
) -> None:
    """Scrive una pagina iniziale o finale."""
    forge = _forge()
    try:
        path = forge.write_matter(kind, style_contract=_forge_style(forge, style))
    except (ValueError, LLMError) as exc:
        err.print(f"[red]Errore:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] scritta: {path}")


@forge_app.command("produce")
def forge_produce(
    trim: str = typer.Option(None, "--trim", help="a5|b-format|5x8|5.5x8.5|6x9|7x10|a4."),
    cover: Path = typer.Option(None, "--cover", help="L'immagine di copertina, che fornisce l'autore."),
) -> None:
    """Scrive ``book.yaml`` e controlla la copertina contro la misura scelta."""
    forge = _forge(with_client=False)
    if trim:
        forge.cfg.production.trim = trim
    path = forge.produce()
    console.print(f"[green]✓[/green] scritto: {path}")
    if cover:
        try:
            forge.set_cover(cover)
        except FileNotFoundError as exc:
            err.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=2)
    console.print(forge.cover_check().message())


@forge_app.command("build")
def forge_build(
    only: str = typer.Option(None, "--only", help="pdf,docx,epub,md — separati da virgola."),
    without_cover: bool = typer.Option(
        False, "--without-cover",
        help="Oltre alla versione con copertina, anche una copia senza copertina per ogni "
             "formato che ne porta una (PDF interno, EPUB)."),
    allow_missing_cover: bool = typer.Option(
        False, "--allow-missing-cover",
        help="Non ha più effetto: senza copertina si esporta comunque, con un'avvertenza. "
             "Accettata per compatibilità."),
) -> None:
    """Compone ed esporta, poi esegue i controlli di stampa."""
    from bookwriter.forge.build import BuildError, CoverMissing

    forge = _forge(with_client=False)
    formati = [f.strip().lower() for f in only.split(",")] if only else None
    try:
        report = forge.build(formats=formati, copy_without_cover=without_cover,
                             note=lambda n: console.print(f"  {n}"))
    except CoverMissing as exc:
        err.print(f"[red]Manca la copertina:[/red] {exc}")
        raise typer.Exit(code=2)
    except BuildError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(report.summary())
    raise typer.Exit(code=0 if report.check_ok else 3)


@forge_app.command("check")
def forge_check() -> None:
    """Rifà i controlli sui file già prodotti. Si riporta tutto: errori e avvertenze."""
    forge = _forge(with_client=False)
    testo, ok = forge.check()
    console.print(testo)
    console.print("\nQuesti controlli non sostituiscono Kindle Previewer 3 per l'ebook né una "
                  "copia stampata per la carta.")
    raise typer.Exit(code=0 if ok else 3)


@forge_app.command("notes")
def forge_notes() -> None:
    """Scrive ``output/PRODUCTION_NOTES.md``: cosa è fatto e cosa resta aperto."""
    forge = _forge(with_client=False)
    path = forge.handover()
    console.print(f"[green]✓[/green] scritte: {path}")
    console.print(path.read_text(encoding="utf-8"))


@forge_app.command("save")
def forge_save(
    name: str = typer.Argument("", help="Come chiamare il salvataggio."),
) -> None:
    """Mette da parte il libro com'è adesso, in un file solo. Il lavoro non si interrompe."""
    from bookwriter.forge import saves as sv

    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    forge = _forge(with_client=False)
    try:
        info = sv.save_work(forge.project, ppaths.saves, name)
    except sv.SaveError as exc:
        from rich.markup import escape

        err.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]✓[/green] {info.label()}")
    console.print(f"  {info.path}")


@forge_app.command("saves")
def forge_saves() -> None:
    """Elenca i libri messi da parte, dal più recente."""
    from bookwriter.forge import saves as sv

    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    elenco = sv.list_saves(ppaths.saves)
    if not elenco:
        console.print("[dim]Nessun salvataggio. Mettine da parte uno con `bookwriter forge save`.[/dim]")
        return
    for indice, info in enumerate(elenco, 1):
        console.print(f" {indice:>2}. {info.label()}")
        console.print(f"     [dim]{info.path.name}[/dim]")


@forge_app.command("restore")
def forge_restore(
    which: str = typer.Argument(..., help="Numero nell'elenco di `forge saves`, o nome del file."),
) -> None:
    """Rimette in lavorazione un salvataggio. Quello di adesso viene messo da parte prima."""
    from bookwriter.forge import saves as sv

    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    forge = _forge(with_client=False)
    elenco = sv.list_saves(ppaths.saves)
    scelto = None
    if which.isdigit() and 1 <= int(which) <= len(elenco):
        scelto = elenco[int(which) - 1].path
    else:
        scelto = next((s.path for s in elenco if which in s.path.name or which == s.name), None)
    if scelto is None:
        err.print(f"[red]Nessun salvataggio corrisponde a «{which}». Vedi `bookwriter forge saves`.[/red]")
        raise typer.Exit(code=1)
    try:
        info = sv.restore_save(scelto, forge.project, backup_dir=ppaths.saves)
    except sv.SaveError as exc:
        from rich.markup import escape

        err.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]✓[/green] ripreso: {info.label()}")
    console.print("  Quello che stava in lavorazione è stato messo da parte: `bookwriter forge saves`.")


@forge_app.command("reset")
def forge_reset(
    delete: bool = typer.Option(False, "--delete", help="Butta via il libro invece di salvarlo."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non chiedere conferma."),
) -> None:
    """Azzera le sette fasi e riparte dall'intervista.

    Senza ``--delete`` il libro di adesso viene messo da parte e resta ripescabile con
    ``forge restore``: azzerare è una cosa che si fa spesso, perdere il lavoro no.
    """
    from bookwriter.forge import saves as sv

    pm = _pm()
    _slug, ppaths = _active_or_exit(pm)
    forge = _forge(with_client=False)
    if not sv.has_work(forge.project):
        sv.clear_work(forge.project)
        console.print("[green]✓[/green] si riparte dall'intervista: `bookwriter forge interview`")
        return
    stato = forge.status()
    from bookwriter.forge.project import phase_title

    console.print(f"Nel libro di adesso: {len(stato.written)} capitoli, "
                  f"{sv._migliaia(stato.words)} parole, fase «{phase_title(forge.state.phase)}»")
    if not yes:
        domanda = ("Butto via tutto questo, senza possibilità di recuperarlo?" if delete
                   else "Lo metto da parte e riparto dall'intervista?")
        if not typer.confirm(domanda, default=not delete):
            raise typer.Exit(code=1)
    messo_da_parte = None
    if not delete:
        try:
            messo_da_parte = sv.save_work(forge.project, ppaths.saves, stato.title)
        except sv.SaveError as exc:
            # La richiesta era «conservalo»: se non riesco, non cancello.
            err.print(f"[red]Non l'ho messo da parte, quindi non l'ho cancellato: {exc}[/red]")
            raise typer.Exit(code=1) from exc
    try:
        sv.clear_work(forge.project)
    except sv.SaveError as exc:
        from rich.markup import escape

        err.print(f"[red]{escape(str(exc))}[/red]")
        raise typer.Exit(code=1) from exc
    if messo_da_parte is not None:
        console.print(f"[green]✓[/green] messo da parte: {messo_da_parte.path.name}")
    console.print("Si riparte dall'intervista: `bookwriter forge interview`")


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
