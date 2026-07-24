"""Finestra principale della GUI: barra progetto + pannelli sulle funzioni del core."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from sqlmodel import Session, select

from bookwriter.atomize import atomize_chunks
from bookwriter.authoring import expand_topic, start_authoring
from bookwriter.coherence import coherence_pass_compose
from bookwriter.compose import compose_outline, compose_to_markdown
from bookwriter.db import project_schema
from bookwriter.db.project_schema import Node, make_engine
from bookwriter.exporter import export_outline, export_wiki, latest_outline_id, outline_to_markdown
from bookwriter.gui.worker import Worker
from bookwriter.ingest import pipeline
from bookwriter.interact import (
    build_brief,
    format_brief,
    integrate_answer,
    list_intents,
    list_open_questions,
    propose_questions,
)
from bookwriter.llm import LMStudioClient
from bookwriter.memory import (
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
    rebuild_outline,
    subdivide_sections,
)
from bookwriter.projects import ProjectManager
from bookwriter.rewrite import assemble_markdown, preview_pairs, rewrite_document
from bookwriter.style import (
    StyleProfile,
    abstract_style,
    apply_style,
    authors_style,
    build_style_contract,
    extract_style,
)
from bookwriter.style.library import get_global_style, list_global_styles
from bookwriter.style.models import AuthorWeight


class DropListWidget(QListWidget):
    """QListWidget che accetta file/cartelle trascinati dall'esterno."""

    paths_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.toLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("bookwriter")
        self.resize(1000, 700)
        self.pm = ProjectManager()
        self.pm.ensure()
        self.pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()
        self._selected_paths: list[Path] = []

        central = QWidget()
        root = QVBoxLayout(central)
        root.addLayout(self._build_project_bar())
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_ingest(), "Ingestione")
        self.tabs.addTab(self._tab_memory(), "Memoria")
        self.tabs.addTab(self._tab_organize(), "Organizza & Export")
        self.tabs.addTab(self._tab_outline(), "Outline")
        self.tabs.addTab(self._tab_style(), "Stile")
        self.tabs.addTab(self._tab_interact(), "Interazione")
        self.tabs.addTab(self._tab_brief(), "Brief")
        self.tabs.addTab(self._tab_rewrite(), "Rewrite")
        self.tabs.addTab(self._tab_translate(), "Translate")
        self.tabs.addTab(self._tab_authoring(), "Authoring")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        root.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.statusBar().showMessage("Pronto")
        self._reload_projects()

    def _on_tab_changed(self, _index: int) -> None:
        """Aggiorna il pannello selezionato con i dati correnti."""
        if not self.pm.active_slug():
            return
        title = self.tabs.tabText(self.tabs.currentIndex())
        if title == "Memoria":
            self._refresh_mem_stats()
        elif title == "Organizza & Export":
            self._refresh_compose_lists()
        elif title == "Outline":
            self._refresh_outline_combo()
        elif title == "Stile":
            self._refresh_styles()
        elif title == "Interazione":
            self._refresh_questions(self.pm.paths(self.pm.active_slug()))
        elif title == "Brief":
            self._do_brief()
        elif title == "Rewrite":
            self._refresh_rewrite_lists()
        elif title == "Translate":
            self._refresh_translate_lists()
        elif title == "Authoring":
            self._refresh_author_lists()

    # ------------------------------------------------------------------ #
    #  infrastruttura
    # ------------------------------------------------------------------ #
    def _run_bg(self, fn: Callable, on_done: Callable, busy: str = "Elaborazione…", progressable: bool = False) -> None:
        self.statusBar().showMessage(busy)
        self.progress_bar.setRange(0, 0)  # indeterminato finché non arriva un progresso reale
        self.progress_bar.setVisible(True)
        worker = Worker(fn, progressable=progressable)
        self._workers.add(worker)

        def _finished(result: object) -> None:
            self._workers.discard(worker)
            self.statusBar().showMessage("Pronto")
            self.progress_bar.setVisible(False)
            on_done(result)

        def _error(msg: str) -> None:
            self._workers.discard(worker)
            self.statusBar().showMessage("Errore")
            self.progress_bar.setVisible(False)
            QMessageBox.critical(self, "Errore", msg)

        def _prog(done: int, total: int) -> None:
            if total > 0:
                self.progress_bar.setRange(0, total)
                self.progress_bar.setValue(done)

        worker.signals.finished.connect(_finished)
        worker.signals.error.connect(_error)
        worker.signals.progress.connect(_prog)
        self.pool.start(worker)

    def _active(self) -> tuple[str, object] | None:
        slug = self.pm.active_slug()
        if not slug:
            QMessageBox.warning(self, "Nessun progetto", "Crea o seleziona prima un progetto.")
            return None
        return slug, self.pm.paths(slug)

    def _client(self, slug: str) -> LMStudioClient:
        cfg = self.pm.config_for(slug)
        return LMStudioClient(cfg.llm, cfg.embedding)

    # ------------------------------------------------------------------ #
    #  barra progetto
    # ------------------------------------------------------------------ #
    def _build_project_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Progetto:"))
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        bar.addWidget(self.project_combo, 1)
        new_btn = QPushButton("Nuovo…")
        new_btn.clicked.connect(self._new_project)
        bar.addWidget(new_btn)
        self.project_info = QLabel("")
        bar.addWidget(self.project_info)
        return bar

    def _reload_projects(self) -> None:
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        projects = self.pm.list()
        for p in projects:
            self.project_combo.addItem(p.name, p.slug)
        active = self.pm.active_slug()
        if active:
            idx = self.project_combo.findData(active)
            if idx >= 0:
                self.project_combo.setCurrentIndex(idx)
        self.project_combo.blockSignals(False)
        self._refresh_project_info()

    def _on_project_changed(self, _index: int) -> None:
        slug = self.project_combo.currentData()
        if slug:
            self.pm.set_active(slug)
            self._refresh_project_info()

    def _refresh_project_info(self) -> None:
        slug = self.pm.active_slug()
        if not slug:
            self.project_info.setText("nessun progetto")
            return
        info = self.pm.info(slug)
        db = self.pm.paths(slug).db
        nodes = graph_stats(db)["nodes"]
        words = self._word_estimate(db)
        target = info.get("target_words")
        wtxt = f"{words}" + (f"/{target}" if target else "")
        self.project_info.setText(
            f"parole: {wtxt} · doc: {info['documents']} · chunk: {info['chunks']} · nodi: {nodes}"
        )

    def _word_estimate(self, db) -> int:
        """Parole stimate del futuro libro (somma delle parole dei nodi verbatim)."""
        with Session(make_engine(db)) as ses:
            texts = ses.exec(select(Node.raw_text)).all()
        return sum(len(t.split()) for t in texts if t)

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "Nuovo progetto", "Nome del progetto:")
        if not ok or not name.strip():
            return
        target, ok2 = QInputDialog.getInt(self, "Target parole", "Numero di parole (0 = non impostato):", 0, 0, 10_000_000, 1000)
        self.pm.create(name.strip(), target_words=target if ok2 and target > 0 else None)
        self._reload_projects()

    # ------------------------------------------------------------------ #
    #  tab: ingestione
    # ------------------------------------------------------------------ #
    def _tab_ingest(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        btns = QHBoxLayout()
        add_files = QPushButton("Aggiungi file…")
        add_files.clicked.connect(self._add_files)
        add_dir = QPushButton("Aggiungi cartella…")
        add_dir.clicked.connect(self._add_dir)
        btns.addWidget(add_files)
        btns.addWidget(add_dir)
        self.recursive_cb = QCheckBox("Ricorsivo")
        self.recursive_cb.setChecked(True)
        btns.addWidget(self.recursive_cb)
        btns.addStretch(1)
        lay.addLayout(btns)
        lay.addWidget(QLabel("Trascina qui file o cartelle, oppure usa i pulsanti:"))
        self.paths_list = DropListWidget()
        self.paths_list.paths_dropped.connect(self._add_dropped)
        lay.addWidget(self.paths_list, 1)
        run = QHBoxLayout()
        import_btn = QPushButton("Importa nella memoria")
        import_btn.clicked.connect(self._do_ingest)
        clear_btn = QPushButton("Svuota lista")
        clear_btn.clicked.connect(lambda: (self._selected_paths.clear(), self.paths_list.clear()))
        run.addWidget(import_btn)
        run.addWidget(clear_btn)
        run.addStretch(1)
        lay.addLayout(run)
        self.ingest_result = QLabel("")
        lay.addWidget(self.ingest_result)
        return w

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Scegli file")
        for f in files:
            self._selected_paths.append(Path(f))
            self.paths_list.addItem(f)

    def _add_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Scegli cartella")
        if d:
            self._selected_paths.append(Path(d))
            self.paths_list.addItem(d + "  (cartella)")

    def _add_dropped(self, paths: list[Path]) -> None:
        for p in paths:
            self._selected_paths.append(p)
            self.paths_list.addItem(f"{p}{'  (cartella)' if p.is_dir() else ''}")

    def _do_ingest(self) -> None:
        active = self._active()
        if not active or not self._selected_paths:
            return
        slug, ppaths = active
        cfg = self.pm.config_for(slug)
        paths = list(self._selected_paths)
        recursive = self.recursive_cb.isChecked()

        def job() -> str:
            report = pipeline.ingest_paths(
                paths, db_path=ppaths.db, target_tokens=cfg.chunking.target_tokens, recursive=recursive
            )
            return report.summary()

        self._run_bg(job, lambda s: (self.ingest_result.setText(s), self._refresh_project_info()), "Importazione…")

    # ------------------------------------------------------------------ #
    #  tab: memoria
    # ------------------------------------------------------------------ #
    def _tab_memory(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        for label, handler in [
            ("Embedda chunk", self._do_embed_chunks),
            ("Atomizza", self._do_atomize),
            ("Costruisci grafo", self._do_graph),
            ("Esporta archivio (wiki)", self._do_export_wiki),
            ("Aggiorna stats", lambda: self._refresh_mem_stats()),
        ]:
            b = QPushButton(label)
            b.clicked.connect(handler)
            row.addWidget(b)
        lay.addLayout(row)
        self.mem_stats = QLabel("")
        lay.addWidget(self.mem_stats)
        search_row = QHBoxLayout()
        self.search_target = QComboBox()
        self.search_target.addItems(["chunk", "nodi"])
        search_row.addWidget(self.search_target)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Ricerca semantica…")
        self.search_edit.returnPressed.connect(self._do_search)
        sbtn = QPushButton("Cerca")
        sbtn.clicked.connect(self._do_search)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(sbtn)
        lay.addLayout(search_row)
        self.search_results = QListWidget()
        lay.addWidget(self.search_results, 1)
        return w

    def _refresh_mem_stats(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        s = pipeline.memory_stats(ppaths.db)
        g = graph_stats(ppaths.db)
        self.mem_stats.setText(
            f"documenti {s['documents']} · chunk {s['chunks']} · nodi {g['nodes']} · archi {g['edges']}"
        )

    def _do_embed_chunks(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)
        self._run_bg(
            lambda p: embed_chunks(ppaths.db, ppaths.vectors, client, progress=p).summary(),
            lambda s: (self.mem_stats.setText(s), self._refresh_mem_stats()),
            "Embedding chunk…",
            progressable=True,
        )

    def _do_atomize(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        self._run_bg(
            lambda p: atomize_chunks(ppaths.db, client, model=model, progress=p).summary(),
            lambda s: (self.mem_stats.setText(s), self._refresh_mem_stats()),
            "Atomizzazione…",
            progressable=True,
        )

    def _do_graph(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)

        def job(p) -> str:
            embed_nodes(ppaths.db, ppaths.vectors, client, progress=p)
            return build_graph(ppaths.db, ppaths.vectors).summary()

        self._run_bg(job, lambda s: (self.mem_stats.setText(s), self._refresh_mem_stats()), "Costruzione grafo…", progressable=True)

    def _do_export_wiki(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        result = export_wiki(ppaths.db, ppaths.exports / "wiki")
        QMessageBox.information(self, "Archivio esportato", f"Wiki: {result['html']}\nGrafo: {result['json']}")

    def _do_search(self) -> None:
        active = self._active()
        if not active:
            return
        query = self.search_edit.text().strip()
        if not query:
            return
        slug, ppaths = active
        client = self._client(slug)
        on_nodes = self.search_target.currentText() == "nodi"

        def job():
            if on_nodes:
                return ("nodi", search_nodes(ppaths.db, ppaths.vectors, client, query, k=8))
            return ("chunk", search_chunks(ppaths.db, ppaths.vectors, client, query, k=8))

        def done(result) -> None:
            kind, hits = result
            self.search_results.clear()
            for h in hits:
                if kind == "nodi":
                    self.search_results.addItem(f"[{h['distance']:.3f}] {', '.join(h['themes'] or [])} · {h['summary']}")
                else:
                    snippet = " ".join(h.text.split())[:120]
                    self.search_results.addItem(f"[{h.distance:.3f}] {h.document_title or ''} · {snippet}…")

        self._run_bg(job, done, "Ricerca…")

    # ------------------------------------------------------------------ #
    #  tab: organizza & export
    # ------------------------------------------------------------------ #
    def _tab_organize(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Ordinamento:"))
        self.ordering_combo = QComboBox()
        self.ordering_combo.addItems(["thematic", "chronological", "narrative", "custom"])
        row.addWidget(self.ordering_combo)
        row.addWidget(QLabel("Capitoli (0=auto):"))
        self.chapters_spin = QSpinBox()
        self.chapters_spin.setRange(0, 100)
        row.addWidget(self.chapters_spin)
        self.method_combo = QComboBox()
        self.method_combo.addItems(["embedding", "community"])
        row.addWidget(self.method_combo)
        org_btn = QPushButton("Organizza")
        org_btn.clicked.connect(self._do_organize)
        row.addWidget(org_btn)
        row.addStretch(1)
        lay.addLayout(row)
        crit_row = QHBoxLayout()
        crit_row.addWidget(QLabel("Criterio (solo custom):"))
        self.criterion_edit = QLineEdit()
        self.criterion_edit.setPlaceholderText("es. per luogo, per personaggio, domanda→risposta…")
        crit_row.addWidget(self.criterion_edit, 1)
        lay.addLayout(crit_row)
        exp = QHBoxLayout()
        exp.addWidget(QLabel("Formato:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["docx", "md", "pdf"])
        exp.addWidget(self.format_combo)
        exp_btn = QPushButton("Esporta")
        exp_btn.clicked.connect(self._do_export)
        exp.addWidget(exp_btn)
        preview_btn = QPushButton("Anteprima")
        preview_btn.clicked.connect(self._do_preview)
        exp.addWidget(preview_btn)
        exp.addStretch(1)
        lay.addLayout(exp)
        comp = QHBoxLayout()
        comp.addWidget(QLabel("Stesura narrativa — stile:"))
        self.compose_style_combo = QComboBox()
        comp.addWidget(self.compose_style_combo, 1)
        comp.addWidget(QLabel("target parole:"))
        self.compose_target = QLineEdit()
        self.compose_target.setFixedWidth(90)
        comp.addWidget(self.compose_target)
        comp_btn = QPushButton("Componi (prosa) + export DOCX")
        comp_btn.clicked.connect(self._do_compose)
        comp.addWidget(comp_btn)
        coh_btn = QPushButton("Coerenza globale")
        coh_btn.clicked.connect(self._do_coherence)
        comp.addWidget(coh_btn)
        lay.addLayout(comp)
        self.outline_preview = QTextEdit()
        self.outline_preview.setReadOnly(True)
        lay.addWidget(self.outline_preview, 1)
        return w

    def _refresh_compose_lists(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        self.compose_style_combo.clear()
        self.compose_style_combo.addItem("— prosa chiara —", None)
        for r in project_schema.list_styles(ppaths.db):
            self.compose_style_combo.addItem(f"{r['name']} ({r['origin']})", r["id"])
        spec = project_schema.get_bookspec(ppaths.db)
        if spec.target_words and not self.compose_target.text().strip():
            self.compose_target.setText(str(spec.target_words))

    def _do_compose(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        oid = latest_outline_id(ppaths.db)
        if not oid:
            QMessageBox.warning(self, "Nessun outline", "Esegui prima Organizza.")
            return
        style_id = self.compose_style_combo.currentData()
        if style_id is not None:
            row = project_schema.get_style_row(ppaths.db, int(style_id))
            contract = build_style_contract(StyleProfile.model_validate(row["data"]))
        else:
            contract = "Scrivi in prosa chiara, scorrevole e coerente, con buone transizioni."
        spec = project_schema.get_bookspec(ppaths.db)
        target = spec.target_words
        if self.compose_target.text().strip():
            try:
                target = int(self.compose_target.text().strip())
            except ValueError:
                pass
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning

        def job(p):
            rep = compose_outline(
                ppaths.db, oid, contract, client,
                target_words=target, tolerance_pct=spec.tolerance_pct, model=model, progress=p,
            )
            markdown = compose_to_markdown(ppaths.db, oid)
            out = ppaths.exports / f"{slug}-book.docx"
            out.parent.mkdir(parents=True, exist_ok=True)
            import pypandoc

            pypandoc.convert_text(markdown, "docx", format="markdown", outputfile=str(out))
            return f"{rep.summary()}\nEsportato: {out}"

        self._run_bg(job, lambda s: self.outline_preview.setPlainText(s), "Stesura narrativa…", progressable=True)

    def _do_coherence(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        oid = latest_outline_id(ppaths.db)
        if not oid:
            QMessageBox.warning(self, "Nessun outline", "Esegui prima Organizza e Componi.")
            return
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.quality
        def done(r) -> None:
            lines = [f"Coerenza: {r['replacements']} sostituzioni, {len(r['mappings'])} varianti uniformate."]
            for issue in r.get("timeline_issues") or []:
                lines.append(f"⏱ timeline: {issue}")
            for rep in r.get("repetitions") or []:
                lines.append(f"↺ ripetizione: {rep}")
            self.outline_preview.setPlainText("\n".join(lines))

        self._run_bg(lambda: coherence_pass_compose(ppaths.db, oid, client, model=model), done, "Coerenza globale…")

    def _do_organize(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        method = self.method_combo.currentText()
        chapters = self.chapters_spin.value() or None
        ordering = self.ordering_combo.currentText()
        criterion = self.criterion_edit.text().strip()
        if ordering == "custom" and not criterion:
            QMessageBox.warning(self, "Manca il criterio", "Per l'ordinamento custom serve un criterio.")
            return

        def job() -> str:
            if ordering == "thematic":
                rep = organize_thematic(ppaths.db, ppaths.vectors, method=method, chapters=chapters, client=client, model=model)
            elif ordering == "chronological":
                rep = organize_chronological(ppaths.db, chapters=chapters)
            elif ordering == "narrative":
                rep = organize_narrative(ppaths.db, ppaths.vectors, client, chapters=chapters, model=model)
            else:
                rep = organize_custom(ppaths.db, client, criterion, model=model)
            return f"{rep.summary()} (ordinamento: {ordering})"

        self._run_bg(job, lambda s: self.statusBar().showMessage(s), "Organizzazione…")

    def _do_export(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        fmt = self.format_combo.currentText()
        oid = latest_outline_id(ppaths.db)
        if not oid:
            QMessageBox.warning(self, "Nessun outline", "Esegui prima Organizza.")
            return
        out = ppaths.exports / f"{slug}.{fmt}"
        self._run_bg(
            lambda: str(export_outline(ppaths.db, oid, out, fmt=fmt)),
            lambda p: QMessageBox.information(self, "Esportato", p),
            "Export…",
        )

    def _do_preview(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        oid = latest_outline_id(ppaths.db)
        if not oid:
            QMessageBox.warning(self, "Nessun outline", "Esegui prima Organizza.")
            return
        self.outline_preview.setPlainText(outline_to_markdown(ppaths.db, oid))

    # ------------------------------------------------------------------ #
    #  tab: outline (editing manuale della struttura)
    # ------------------------------------------------------------------ #
    def _tab_outline(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("Outline:"))
        self.outline_combo = QComboBox()
        self.outline_combo.currentIndexChanged.connect(self._load_outline_tree)
        top.addWidget(self.outline_combo, 1)
        reload_btn = QPushButton("Ricarica")
        reload_btn.clicked.connect(self._load_outline_tree)
        top.addWidget(reload_btn)
        lay.addLayout(top)
        lay.addWidget(QLabel("Trascina per riordinare capitoli e spostare nodi. Doppio clic per rinominare un capitolo."))
        self.outline_tree = QTreeWidget()
        self.outline_tree.setHeaderLabels(["Capitoli e nodi"])
        self.outline_tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.outline_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        lay.addWidget(self.outline_tree, 1)
        btns = QHBoxLayout()
        for label, handler in [
            ("Aggiungi capitolo", self._outline_add_chapter),
            ("Rinomina", self._outline_rename),
            ("Elimina selezionato", self._outline_delete),
            ("Suddividi in sezioni", self._do_subdivide),
            ("Salva struttura", self._outline_save),
        ]:
            b = QPushButton(label)
            b.clicked.connect(handler)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        return w

    def _refresh_outline_combo(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        self.outline_combo.blockSignals(True)
        self.outline_combo.clear()
        for o in list_outlines(ppaths.db):
            self.outline_combo.addItem(f"#{o['id']} {o['name']} ({o['ordering']})", o["id"])
        self.outline_combo.setCurrentIndex(self.outline_combo.count() - 1)
        self.outline_combo.blockSignals(False)
        self._load_outline_tree()

    def _load_outline_tree(self) -> None:
        active = self._active_silent()
        oid = self.outline_combo.currentData()
        if not active or oid is None:
            return
        _slug, ppaths = active
        self.outline_tree.clear()
        for ch in outline_tree(ppaths.db, oid):
            ci = QTreeWidgetItem([ch["title"]])
            ci.setData(0, Qt.ItemDataRole.UserRole, ("chapter", ch["chapter_id"]))
            ci.setFlags(ci.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsDragEnabled)
            self.outline_tree.addTopLevelItem(ci)
            for n in ch["nodes"]:
                ni = QTreeWidgetItem([f"#{n['node_id']} {n['summary']}"])
                ni.setData(0, Qt.ItemDataRole.UserRole, ("node", n["node_id"]))
                ni.setFlags((ni.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
                ci.addChild(ni)
            ci.setExpanded(True)

    def _outline_add_chapter(self) -> None:
        ci = QTreeWidgetItem(["Nuovo capitolo"])
        ci.setData(0, Qt.ItemDataRole.UserRole, ("chapter", -1))
        ci.setFlags(ci.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsDragEnabled)
        self.outline_tree.addTopLevelItem(ci)

    def _outline_rename(self) -> None:
        item = self.outline_tree.currentItem()
        if item is not None and item.parent() is None:
            self.outline_tree.editItem(item)

    def _outline_delete(self) -> None:
        item = self.outline_tree.currentItem()
        if item is None:
            return
        parent = item.parent()
        if parent is None:
            self.outline_tree.takeTopLevelItem(self.outline_tree.indexOfTopLevelItem(item))
        else:
            parent.removeChild(item)

    def _do_subdivide(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        oid = self.outline_combo.currentData()
        if oid is None:
            return
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        self._run_bg(
            lambda: subdivide_sections(ppaths.db, ppaths.vectors, oid, client=client, model=model),
            lambda r: QMessageBox.information(self, "Sezioni", f"Create {r['sections']} sezioni (visibili in export/stesura)."),
            "Suddivisione in sezioni…",
        )

    def _outline_save(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        oid = self.outline_combo.currentData()
        if oid is None:
            return
        chapters: list[dict] = []
        current: dict | None = None
        for i in range(self.outline_tree.topLevelItemCount()):
            item = self.outline_tree.topLevelItem(i)
            kind, ident = item.data(0, Qt.ItemDataRole.UserRole)
            if kind == "chapter":
                current = {"title": item.text(0), "node_ids": []}
                chapters.append(current)
                for j in range(item.childCount()):
                    child = item.child(j)
                    ck, cid = child.data(0, Qt.ItemDataRole.UserRole)
                    if ck == "node":
                        current["node_ids"].append(cid)
            else:  # nodo finito al livello superiore → attaccato al capitolo corrente
                if current is None:
                    current = {"title": "Capitolo", "node_ids": []}
                    chapters.append(current)
                current["node_ids"].append(ident)
        rebuild_outline(ppaths.db, oid, chapters)
        self._load_outline_tree()
        QMessageBox.information(self, "Salvato", "Struttura dell'outline aggiornata.")

    # ------------------------------------------------------------------ #
    #  tab: stile
    # ------------------------------------------------------------------ #
    def _tab_style(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        for label, handler in [
            ("Estrai da file…", self._do_style_extract),
            ("Definisci (astratto)…", self._do_style_abstract),
            ("Da autori…", self._do_style_authors),
            ("Importa globale…", self._do_style_import_global),
        ]:
            b = QPushButton(label)
            b.clicked.connect(handler)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Profili di stile (seleziona per applicare):"))
        self.styles_list = QListWidget()
        lay.addWidget(self.styles_list, 1)
        lay.addWidget(QLabel("Testo da riscrivere nello stile selezionato:"))
        self.style_input = QTextEdit()
        self.style_input.setPlaceholderText("Incolla qui il testo…")
        lay.addWidget(self.style_input, 1)
        apply_btn = QPushButton("Applica stile")
        apply_btn.clicked.connect(self._do_style_apply)
        lay.addWidget(apply_btn)
        self.style_output = QTextEdit()
        self.style_output.setReadOnly(True)
        lay.addWidget(self.style_output, 1)
        return w

    def _refresh_styles(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        self.styles_list.clear()
        for r in project_schema.list_styles(ppaths.db):
            self.styles_list.addItem(f"#{r['id']} {r['name']} ({r['origin']})")

    def _active_silent(self):
        slug = self.pm.active_slug()
        return (slug, self.pm.paths(slug)) if slug else None

    def _do_style_import_global(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        globs = list_global_styles()
        if not globs:
            QMessageBox.information(self, "Libreria vuota", "Nessuno stile nella libreria globale.")
            return
        names = [g["name"] for g in globs]
        name, ok = QInputDialog.getItem(self, "Importa stile globale", "Stile:", names, 0, False)
        if not ok or not name:
            return
        profile = get_global_style(name)
        if profile is None:
            return
        project_schema.save_style(ppaths.db, profile.name, profile.origin, profile.model_dump())
        self._refresh_styles()

    def _do_style_extract(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        file, _ = QFileDialog.getOpenFileName(self, "Testo campione")
        if not file:
            return
        name, ok = QInputDialog.getText(self, "Nome profilo", "Nome:")
        if not ok or not name.strip():
            return
        client, model = self._client(slug), self.pm.config_for(slug).llm.models.reasoning
        text = Path(file).read_text(encoding="utf-8", errors="replace")

        def job():
            prof = extract_style(text, client, name=name.strip(), model=model)
            project_schema.save_style(ppaths.db, prof.name, prof.origin, prof.model_dump())
            return prof.name

        self._run_bg(job, lambda _n: self._refresh_styles(), "Estrazione stile…")

    def _do_style_abstract(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        name, ok = QInputDialog.getText(self, "Nome profilo", "Nome:")
        if not ok or not name.strip():
            return
        desc, ok2 = QInputDialog.getMultiLineText(self, "Descrizione stile", "Descrivi lo stile desiderato:")
        if not ok2 or not desc.strip():
            return
        client, model = self._client(slug), self.pm.config_for(slug).llm.models.reasoning

        def job():
            prof = abstract_style(desc, client, name=name.strip(), model=model)
            project_schema.save_style(ppaths.db, prof.name, prof.origin, prof.model_dump())
            return prof.name

        self._run_bg(job, lambda _n: self._refresh_styles(), "Definizione stile…")

    def _do_style_authors(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        name, ok = QInputDialog.getText(self, "Nome profilo", "Nome:")
        if not ok or not name.strip():
            return
        specs_text, ok2 = QInputDialog.getMultiLineText(
            self, "Autori", "Una riga per autore: Autore:peso:dim1,dim2", "Calvino:0.7:sintassi,ritmo\nHemingway:0.3:lessico"
        )
        if not ok2 or not specs_text.strip():
            return
        specs: list[AuthorWeight] = []
        for line in specs_text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(":")
            author = parts[0].strip()
            weight = float(parts[1]) if len(parts) > 1 and parts[1] else 1.0
            dims = [d.strip() for d in parts[2].split(",")] if len(parts) > 2 and parts[2] else []
            specs.append(AuthorWeight(author=author, weight=weight, dims=dims))
        client, model = self._client(slug), self.pm.config_for(slug).llm.models.reasoning

        def job():
            prof = authors_style(specs, client, name=name.strip(), model=model)
            project_schema.save_style(ppaths.db, prof.name, prof.origin, prof.model_dump())
            return prof.name

        self._run_bg(job, lambda _n: self._refresh_styles(), "Fusione autori…")

    def _do_style_apply(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        item = self.styles_list.currentItem()
        text = self.style_input.toPlainText().strip()
        if item is None or not text:
            QMessageBox.warning(self, "Manca qualcosa", "Seleziona un profilo e inserisci un testo.")
            return
        ref = item.text().split()[0].lstrip("#")
        row = project_schema.get_style_row(ppaths.db, ref)
        if row is None:
            return
        profile = StyleProfile.model_validate(row["data"])
        client, model = self._client(slug), self.pm.config_for(slug).llm.models.reasoning
        self._run_bg(
            lambda: apply_style(text, profile, client, model=model),
            lambda out: self.style_output.setPlainText(out),
            "Applicazione stile…",
        )

    # ------------------------------------------------------------------ #
    #  tab: interazione
    # ------------------------------------------------------------------ #
    def _tab_interact(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        ask_btn = QPushButton("Proponi domande")
        ask_btn.clicked.connect(self._do_ask)
        top.addWidget(ask_btn)
        top.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(QLabel("Domande aperte (selezionane una per rispondere):"))
        self.questions_list = QListWidget()
        lay.addWidget(self.questions_list, 1)
        ans = QHBoxLayout()
        self.answer_edit = QLineEdit()
        self.answer_edit.setPlaceholderText("La tua risposta / contenuto…")
        integ = QPushButton("Integra come nodo")
        integ.clicked.connect(self._do_answer)
        ans.addWidget(self.answer_edit, 1)
        ans.addWidget(integ)
        lay.addLayout(ans)
        lay.addWidget(QLabel("Intenti / decisioni persistiti:"))
        self.intents_list = QListWidget()
        lay.addWidget(self.intents_list, 1)
        return w

    def _refresh_questions(self, ppaths) -> None:
        self.questions_list.clear()
        for q in list_open_questions(ppaths.db):
            self.questions_list.addItem(f"#{q['id']}  {q['text']}")
        self.intents_list.clear()
        for it in list_intents(ppaths.db):
            self.intents_list.addItem(f"#{it['id']} [{it['status']}] {it['kind']}: {it['value']}")

    def _do_ask(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        self._run_bg(
            lambda: propose_questions(ppaths.db, client, model=model, n=3),
            lambda _r: self._refresh_questions(ppaths),
            "Generazione domande…",
        )

    def _do_answer(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        text = self.answer_edit.text().strip()
        item = self.questions_list.currentItem()
        if not text or item is None:
            QMessageBox.warning(self, "Manca qualcosa", "Seleziona una domanda e scrivi una risposta.")
            return
        qid = int(item.text().split()[0].lstrip("#"))
        client = self._client(slug)
        self._run_bg(
            lambda: integrate_answer(ppaths.db, ppaths.vectors, client, qid, text),
            lambda nid: (self.answer_edit.clear(), self._refresh_questions(ppaths), self._refresh_project_info()),
            "Integrazione risposta…",
        )

    # ------------------------------------------------------------------ #
    #  tab: brief
    # ------------------------------------------------------------------ #
    def _tab_brief(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        btn = QPushButton("Aggiorna brief")
        btn.clicked.connect(self._do_brief)
        lay.addWidget(btn)
        self.brief_text = QTextEdit()
        self.brief_text.setReadOnly(True)
        lay.addWidget(self.brief_text, 1)
        return w

    def _do_brief(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        self.brief_text.setPlainText(format_brief(build_brief(ppaths.db)))

    # ------------------------------------------------------------------ #
    #  tab: rewrite (modalità B)
    # ------------------------------------------------------------------ #
    def _tab_rewrite(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Stile:"))
        self.rw_style_combo = QComboBox()
        row.addWidget(self.rw_style_combo, 1)
        row.addWidget(QLabel("Documento:"))
        self.rw_doc_combo = QComboBox()
        row.addWidget(self.rw_doc_combo, 1)
        row.addWidget(QLabel("Ratio:"))
        self.rw_ratio = QLineEdit()
        self.rw_ratio.setPlaceholderText("opz. es. 0.7")
        self.rw_ratio.setFixedWidth(80)
        row.addWidget(self.rw_ratio)
        lay.addLayout(row)
        btns = QHBoxLayout()
        prev_btn = QPushButton("Preview 1° capitolo")
        prev_btn.clicked.connect(self._do_rewrite_preview)
        full_btn = QPushButton("Riscrivi tutto + export DOCX")
        full_btn.clicked.connect(self._do_rewrite_full)
        btns.addWidget(prev_btn)
        btns.addWidget(full_btn)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.rw_output = QTextEdit()
        self.rw_output.setReadOnly(True)
        lay.addWidget(self.rw_output, 1)
        return w

    def _refresh_rewrite_lists(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        self.rw_style_combo.clear()
        for r in project_schema.list_styles(ppaths.db):
            self.rw_style_combo.addItem(f"{r['name']} ({r['origin']})", r["id"])
        self.rw_doc_combo.clear()
        for d in project_schema.list_documents(ppaths.db):
            self.rw_doc_combo.addItem(f"{d['title']} ({d['format']})", d["id"])

    def _rewrite_setup(self):
        active = self._active()
        if not active:
            return None
        slug, ppaths = active
        style_id = self.rw_style_combo.currentData()
        doc_id = self.rw_doc_combo.currentData()
        if style_id is None or doc_id is None:
            QMessageBox.warning(self, "Manca qualcosa", "Servono un profilo di stile e un documento.")
            return None
        row = project_schema.get_style_row(ppaths.db, int(style_id))
        contract = build_style_contract(StyleProfile.model_validate(row["data"]))
        ratio = None
        if self.rw_ratio.text().strip():
            try:
                ratio = float(self.rw_ratio.text().strip())
            except ValueError:
                ratio = None
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        return slug, ppaths, contract, int(doc_id), ratio, client, model

    def _do_rewrite_preview(self) -> None:
        setup = self._rewrite_setup()
        if not setup:
            return
        _slug, ppaths, contract, doc_id, ratio, client, model = setup

        def job():
            rep = rewrite_document(
                ppaths.db, doc_id, contract, client, kind="preview", ratio=ratio, model=model, resume=False
            )
            return preview_pairs(ppaths.db, rep.run_id)

        def done(pairs):
            blocks = []
            for i, p in enumerate(pairs, 1):
                blocks.append(f"— brano {i} ({p['chapter'] or ''}) —\nORIGINALE:\n{p['original']}\n\nRISCRITTO:\n{p['rewritten']}\n")
            self.rw_output.setPlainText("\n".join(blocks))

        self._run_bg(job, done, "Preview riscrittura…")

    def _do_rewrite_full(self) -> None:
        setup = self._rewrite_setup()
        if not setup:
            return
        slug, ppaths, contract, doc_id, ratio, client, model = setup

        def job():
            rep = rewrite_document(ppaths.db, doc_id, contract, client, kind="full", ratio=ratio, model=model)
            markdown = assemble_markdown(ppaths.db, rep.run_id)
            out = ppaths.exports / f"{slug}-rewrite.docx"
            out.parent.mkdir(parents=True, exist_ok=True)
            import pypandoc

            pypandoc.convert_text(markdown, "docx", format="markdown", outputfile=str(out))
            return str(out)

        self._run_bg(job, lambda p: self.rw_output.setPlainText(f"Esportato: {p}"), "Riscrittura completa…")

    # ------------------------------------------------------------------ #
    #  tab: translate (modalità C)
    # ------------------------------------------------------------------ #
    def _tab_translate(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Lingua:"))
        self.tr_lang = QLineEdit()
        self.tr_lang.setPlaceholderText("es. inglese")
        row.addWidget(self.tr_lang)
        row.addWidget(QLabel("Stile:"))
        self.tr_style_combo = QComboBox()
        row.addWidget(self.tr_style_combo, 1)
        row.addWidget(QLabel("Documento:"))
        self.tr_doc_combo = QComboBox()
        row.addWidget(self.tr_doc_combo, 1)
        lay.addLayout(row)
        btns = QHBoxLayout()
        prev_btn = QPushButton("Preview 1° capitolo")
        prev_btn.clicked.connect(lambda: self._do_translate(preview=True))
        full_btn = QPushButton("Traduci tutto + export DOCX")
        full_btn.clicked.connect(lambda: self._do_translate(preview=False))
        btns.addWidget(prev_btn)
        btns.addWidget(full_btn)
        btns.addStretch(1)
        lay.addLayout(btns)
        self.tr_output = QTextEdit()
        self.tr_output.setReadOnly(True)
        lay.addWidget(self.tr_output, 1)
        return w

    def _refresh_translate_lists(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        self.tr_style_combo.clear()
        self.tr_style_combo.addItem("— mantieni stile originale —", None)
        for r in project_schema.list_styles(ppaths.db):
            self.tr_style_combo.addItem(f"{r['name']} ({r['origin']})", r["id"])
        self.tr_doc_combo.clear()
        for d in project_schema.list_documents(ppaths.db):
            self.tr_doc_combo.addItem(f"{d['title']} ({d['format']})", d["id"])

    def _do_translate(self, *, preview: bool) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        lang = self.tr_lang.text().strip()
        doc_id = self.tr_doc_combo.currentData()
        if not lang or doc_id is None:
            QMessageBox.warning(self, "Manca qualcosa", "Servono la lingua e un documento.")
            return
        style_id = self.tr_style_combo.currentData()
        if style_id is not None:
            row = project_schema.get_style_row(ppaths.db, int(style_id))
            contract = build_style_contract(StyleProfile.model_validate(row["data"]))
        else:
            contract = "Mantieni fedelmente lo stile e il registro dell'originale."
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        kind = "preview" if preview else "full"

        def job():
            rep = rewrite_document(
                ppaths.db, int(doc_id), contract, client,
                kind=kind, mode="translate", target_language=lang, model=model, resume=not preview,
            )
            if preview:
                return ("preview", preview_pairs(ppaths.db, rep.run_id))
            markdown = assemble_markdown(ppaths.db, rep.run_id)
            out = ppaths.exports / f"{slug}-{lang}.docx"
            out.parent.mkdir(parents=True, exist_ok=True)
            import pypandoc

            pypandoc.convert_text(markdown, "docx", format="markdown", outputfile=str(out))
            return ("full", str(out))

        def done(result):
            kind_, payload = result
            if kind_ == "preview":
                blocks = [f"— {p['chapter'] or ''} —\nORIG:\n{p['original']}\n\nTRAD:\n{p['rewritten']}\n" for p in payload]
                self.tr_output.setPlainText("\n".join(blocks))
            else:
                self.tr_output.setPlainText(f"Esportato: {payload}")

        self._run_bg(job, done, "Traduzione…")

    # ------------------------------------------------------------------ #
    #  tab: authoring (modalità D)
    # ------------------------------------------------------------------ #
    def _tab_authoring(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("1) Brief dell'opera"))
        brief = QHBoxLayout()
        self.au_theme = QLineEdit()
        self.au_theme.setPlaceholderText("Tema")
        self.au_audience = QLineEdit()
        self.au_audience.setPlaceholderText("Pubblico")
        self.au_goal = QLineEdit()
        self.au_goal.setPlaceholderText("Obiettivo")
        self.au_target = QLineEdit()
        self.au_target.setPlaceholderText("Target parole")
        self.au_target.setFixedWidth(100)
        for wdg in (self.au_theme, self.au_audience, self.au_goal, self.au_target):
            brief.addWidget(wdg)
        start_btn = QPushButton("Avvia brief")
        start_btn.clicked.connect(self._do_author_start)
        brief.addWidget(start_btn)
        lay.addLayout(brief)

        lay.addWidget(QLabel("2) Genera idee (l'LLM crea contenuti dal nulla)"))
        exp = QHBoxLayout()
        self.au_topic = QLineEdit()
        self.au_topic.setPlaceholderText("Sotto-tema da sviluppare")
        exp.addWidget(self.au_topic, 1)
        exp.addWidget(QLabel("n:"))
        self.au_n = QSpinBox()
        self.au_n.setRange(1, 30)
        self.au_n.setValue(6)
        exp.addWidget(self.au_n)
        exp_btn = QPushButton("Genera idee")
        exp_btn.clicked.connect(self._do_author_expand)
        exp.addWidget(exp_btn)
        lay.addLayout(exp)

        lay.addWidget(QLabel("3) Genera la bozza (grafo → organizza → stesura → DOCX)"))
        draft = QHBoxLayout()
        draft.addWidget(QLabel("Stile:"))
        self.au_style_combo = QComboBox()
        draft.addWidget(self.au_style_combo, 1)
        draft_btn = QPushButton("Genera bozza completa")
        draft_btn.clicked.connect(self._do_author_draft)
        draft.addWidget(draft_btn)
        lay.addLayout(draft)
        self.au_output = QTextEdit()
        self.au_output.setReadOnly(True)
        lay.addWidget(self.au_output, 1)
        return w

    def _refresh_author_lists(self) -> None:
        active = self._active_silent()
        if not active:
            return
        _slug, ppaths = active
        self.au_style_combo.clear()
        self.au_style_combo.addItem("— prosa chiara —", None)
        for r in project_schema.list_styles(ppaths.db):
            self.au_style_combo.addItem(f"{r['name']} ({r['origin']})", r["id"])

    def _do_author_start(self) -> None:
        active = self._active()
        if not active:
            return
        _slug, ppaths = active
        theme = self.au_theme.text().strip()
        if not theme:
            QMessageBox.warning(self, "Manca il tema", "Inserisci almeno il tema.")
            return
        target = None
        if self.au_target.text().strip():
            try:
                target = int(self.au_target.text().strip())
            except ValueError:
                target = None
        start_authoring(
            ppaths.db, theme=theme,
            audience=self.au_audience.text().strip() or None,
            goal=self.au_goal.text().strip() or None,
            target_words=target,
        )
        self.au_output.setPlainText("Brief registrato. Ora genera idee sui sotto-temi, poi la bozza.")
        self._refresh_project_info()

    def _do_author_expand(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        topic = self.au_topic.text().strip()
        if not topic:
            QMessageBox.warning(self, "Manca il sotto-tema", "Inserisci un sotto-tema.")
            return
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        n = self.au_n.value()
        aud = self.au_audience.text().strip() or None
        goal = self.au_goal.text().strip() or None
        self._run_bg(
            lambda: expand_topic(ppaths.db, ppaths.vectors, client, topic=topic, n=n, audience=aud, goal=goal, model=model),
            lambda c: (self.au_output.setPlainText(f"Generate {c} idee sul tema «{topic}»."), self._refresh_project_info()),
            "Generazione idee…",
        )

    def _do_author_draft(self) -> None:
        active = self._active()
        if not active:
            return
        slug, ppaths = active
        client = self._client(slug)
        model = self.pm.config_for(slug).llm.models.reasoning
        style_id = self.au_style_combo.currentData()
        if style_id is not None:
            row = project_schema.get_style_row(ppaths.db, int(style_id))
            contract = build_style_contract(StyleProfile.model_validate(row["data"]))
        else:
            contract = "Scrivi in prosa chiara, scorrevole e coerente, con buone transizioni."
        spec = project_schema.get_bookspec(ppaths.db)

        def job():
            embed_nodes(ppaths.db, ppaths.vectors, client)
            build_graph(ppaths.db, ppaths.vectors)
            org = organize_thematic(ppaths.db, ppaths.vectors, method="embedding", name="Bozza autoriale", client=client, model=model)
            rep = compose_outline(ppaths.db, org.outline_id, contract, client, target_words=spec.target_words, tolerance_pct=spec.tolerance_pct, model=model)
            markdown = compose_to_markdown(ppaths.db, org.outline_id)
            out = ppaths.exports / f"{slug}-book.docx"
            out.parent.mkdir(parents=True, exist_ok=True)
            import pypandoc

            pypandoc.convert_text(markdown, "docx", format="markdown", outputfile=str(out))
            return f"{rep.summary()}\nEsportato: {out}"

        self._run_bg(job, lambda s: self.au_output.setPlainText(s), "Generazione bozza…")

    # ------------------------------------------------------------------ #
    def _placeholder(self, text: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label)
        return w
